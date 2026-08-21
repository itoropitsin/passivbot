"""Record public Bitget market snapshots for later execution calibration.

Only public market GET endpoints are used.  The recorder never reads API keys
and has no account, order, or transfer code path.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from tools.bitget_public_execution_inputs import _default_request


SCHEMA_VERSION = "bitget-market-snapshot-v3"
DOCUMENTATION = {
    "tickers": "https://www.bitget.com/api-doc/uta/public/Tickers",
    "orderbook": "https://www.bitget.com/api-doc/uta/public/OrderBook",
    "current_funding": "https://www.bitget.com/api-doc/uta/public/Get-Current-Funding-Rate",
    "funding_history": "https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate",
}
RequestFn = Callable[[str, Mapping[str, str]], Mapping[str, Any]]


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rows(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], Mapping) else {}
    return data if isinstance(data, Mapping) else {}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _levels(value: Any) -> list[list[float]]:
    result: list[list[float]] = []
    if not isinstance(value, list):
        return result
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price = _number(row[0])
        quantity = _number(row[1])
        if price is None or quantity is None or price <= 0.0 or quantity < 0.0:
            continue
        result.append([price, quantity])
    return result


def collect_market_snapshot(
    *,
    symbol: str = "BTCUSDT",
    category: str = "USDT-FUTURES",
    depth: int = 20,
    request: RequestFn = _default_request,
    captured_at_ms: int | None = None,
) -> dict[str, Any]:
    """Collect and validate one public ticker/order-book snapshot."""

    if depth < 1 or depth > 1000:
        raise ValueError("depth must be between 1 and 1000")
    ticker = dict(
        request(
            "/api/v3/market/tickers",
            {"category": category, "symbol": symbol},
        )
    )
    orderbook = dict(
        request(
            "/api/v3/market/orderbook",
            {"category": category, "symbol": symbol, "limit": str(depth)},
        )
    )
    # The ticker contains a useful snapshot value, but the dedicated public
    # endpoint also returns the funding cadence and the next settlement time.
    # Keeping both responses makes a later execution replay auditable when the
    # two values differ during a refresh boundary.
    current_funding = dict(
        request(
            "/api/v3/market/current-fund-rate",
            {"category": category, "symbol": symbol},
        )
    )
    funding_history = dict(
        request(
            "/api/v3/market/history-fund-rate",
            {"category": category, "symbol": symbol, "limit": "1", "cursor": "1"},
        )
    )
    ticker_row = _rows(ticker)
    orderbook_row = _rows(orderbook)
    funding_row = _rows(current_funding)
    funding_history_data = funding_history.get("data")
    funding_history_rows = (
        funding_history_data.get("resultList")
        if isinstance(funding_history_data, Mapping)
        else None
    )
    settlement_row = (
        funding_history_rows[0]
        if isinstance(funding_history_rows, list)
        and funding_history_rows
        and isinstance(funding_history_rows[0], Mapping)
        else {}
    )
    bid = _number(ticker_row.get("bid1Price"))
    ask = _number(ticker_row.get("ask1Price"))
    if bid is None or ask is None or bid <= 0.0 or ask <= 0.0 or bid > ask:
        raise ValueError("Bitget ticker returned invalid bid/ask")
    bids = _levels(orderbook_row.get("b"))
    asks = _levels(orderbook_row.get("a"))
    if not bids or not asks:
        raise ValueError("Bitget order book returned no valid bid/ask levels")
    funding_rate = _number(funding_row.get("fundingRate"))
    funding_interval_hours = _number(funding_row.get("fundingRateInterval"))
    next_funding_time_ms = _number(funding_row.get("nextUpdate"))
    if funding_rate is None or funding_interval_hours is None or next_funding_time_ms is None:
        raise ValueError("Bitget current funding response is incomplete")
    if funding_interval_hours <= 0.0 or next_funding_time_ms <= 0.0:
        raise ValueError("Bitget current funding response has invalid cadence")
    settlement_rate = _number(settlement_row.get("fundingRate"))
    settlement_time_ms = _number(settlement_row.get("fundingRateTimestamp"))
    if settlement_rate is None or settlement_time_ms is None or settlement_time_ms <= 0.0:
        raise ValueError("Bitget funding history response is incomplete")
    timestamp_ms = captured_at_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    source_timestamp_ms = _number(orderbook_row.get("ts")) or _number(ticker_row.get("ts"))
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": datetime.fromtimestamp(
            timestamp_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "captured_at_ms": timestamp_ms,
        "source_timestamp_ms": int(source_timestamp_ms) if source_timestamp_ms else None,
        "exchange": "bitget",
        "symbol": symbol,
        "category": category,
        "read_only_only": True,
        "trading_operations_count": 0,
        "ticker": {
            "last": _number(ticker_row.get("lastPrice")),
            "bid": bid,
            "ask": ask,
            "bid_size": _number(ticker_row.get("bid1Size")),
            "ask_size": _number(ticker_row.get("ask1Size")),
            "mark": _number(ticker_row.get("markPrice")),
            "index": _number(ticker_row.get("indexPrice")),
            "funding_rate": _number(ticker_row.get("fundingRate")),
        },
        "funding": {
            "rate": funding_rate,
            "interval_hours": funding_interval_hours,
            "next_funding_time_ms": int(next_funding_time_ms),
            "min_rate": _number(funding_row.get("minFundingRate")),
            "max_rate": _number(funding_row.get("maxFundingRate")),
            "latest_settlement_rate": settlement_rate,
            "latest_settlement_time_ms": int(settlement_time_ms),
        },
        "orderbook": {
            "timestamp_ms": int(_number(orderbook_row.get("ts")) or timestamp_ms),
            "bids": bids,
            "asks": asks,
        },
        "response_sha256": {
            "ticker": _canonical_hash(ticker),
            "orderbook": _canonical_hash(orderbook),
            "current_funding": _canonical_hash(current_funding),
            "funding_history": _canonical_hash(funding_history),
        },
        # Keep the source payload beside the normalized fields.  The hashes
        # then prove that a later replay used exactly the captured response.
        "source_responses": {
            "ticker": ticker,
            "orderbook": orderbook,
            "current_funding": current_funding,
            "funding_history": funding_history,
        },
        "documentation": DOCUMENTATION,
    }
    return snapshot


def verify_market_log(
    path: Path,
    *,
    max_gap_seconds: float | None = None,
    require_funding_continuity: bool = False,
    use_source_timestamp: bool = False,
) -> dict[str, Any]:
    """Verify a JSONL market log and return a content-addressed audit result.

    ``max_gap_seconds`` is intentionally opt-in: a short one-shot snapshot is
    valid input, while a long-running recorder can require a bounded gap when
    the log is checked by a daily job.  ``use_source_timestamp`` makes that
    bound apply to exchange timestamps rather than local capture time; this is
    required for a strategy shadow because local polling can look healthy while
    the market source itself is stale. Funding continuity is checked only
    between observed settlement timestamps; this never invents a missing row.
    """

    target = path.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(target)
    digest = hashlib.sha256()
    errors: list[dict[str, Any]] = []
    row_count = 0
    first_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None
    captured_timestamps: list[int] = []
    source_timestamps: list[int] = []
    last_source_timestamp_ms: int | None = None
    funding_settlement_timestamps: set[int] = set()
    funding_interval_hours: set[float] = set()
    observed_symbols: set[str] = set()
    observed_categories: set[str] = set()
    with target.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            row_count += 1
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                errors.append({"line": line_number, "reason": f"invalid_json: {exc.msg}"})
                continue
            if not isinstance(row, Mapping):
                errors.append({"line": line_number, "reason": "row_is_not_an_object"})
                continue
            symbol = row.get("symbol")
            category = row.get("category")
            if symbol is not None:
                observed_symbols.add(str(symbol))
            if category is not None:
                observed_categories.add(str(category))
            if row.get("read_only_only") is not True:
                errors.append({"line": line_number, "reason": "read_only_only_false"})
            if row.get("trading_operations_count") != 0:
                errors.append({"line": line_number, "reason": "trading_operations_present"})
            if row.get("error"):
                errors.append({"line": line_number, "reason": "recorder_error", "detail": row["error"]})
            if row.get("schema_version") == SCHEMA_VERSION:
                funding = row.get("funding")
                if not isinstance(funding, Mapping):
                    errors.append({"line": line_number, "reason": "funding_snapshot_missing"})
                else:
                    for field in (
                        "rate",
                        "interval_hours",
                        "next_funding_time_ms",
                        "latest_settlement_rate",
                        "latest_settlement_time_ms",
                    ):
                        if funding.get(field) is None:
                            errors.append({"line": line_number, "reason": f"funding_{field}_missing"})
            captured = row.get("captured_at_ms")
            if captured is not None:
                try:
                    timestamp_ms = int(captured)
                except (TypeError, ValueError):
                    errors.append({"line": line_number, "reason": "invalid_captured_at_ms"})
                else:
                    first_timestamp_ms = timestamp_ms if first_timestamp_ms is None else first_timestamp_ms
                    if last_timestamp_ms is not None and timestamp_ms < last_timestamp_ms:
                        errors.append({"line": line_number, "reason": "timestamps_decrease"})
                    last_timestamp_ms = timestamp_ms
                    captured_timestamps.append(timestamp_ms)
            source_value = row.get("source_timestamp_ms")
            if use_source_timestamp and source_value is None:
                errors.append({"line": line_number, "reason": "source_timestamp_missing"})
            elif source_value is None:
                orderbook = row.get("orderbook")
                ticker = row.get("ticker")
                if isinstance(orderbook, Mapping):
                    source_value = orderbook.get("timestamp_ms")
                if source_value is None and isinstance(ticker, Mapping):
                    source_value = ticker.get("timestamp_ms") or ticker.get("ts")
            if source_value is not None:
                try:
                    if isinstance(source_value, bool):
                        raise ValueError("boolean timestamp")
                    source_timestamp_ms = int(source_value)
                    if isinstance(source_value, float) and source_value != source_timestamp_ms:
                        raise ValueError("fractional timestamp")
                except (TypeError, ValueError, OverflowError):
                    errors.append({"line": line_number, "reason": "invalid_source_timestamp_ms"})
                else:
                    if use_source_timestamp and source_timestamp_ms < 100_000_000_000:
                        errors.append(
                            {
                                "line": line_number,
                                "reason": "source_timestamp_not_milliseconds",
                            }
                        )
                    elif source_timestamp_ms > 0:
                        if (
                            last_source_timestamp_ms is not None
                            and source_timestamp_ms < last_source_timestamp_ms
                        ):
                            errors.append(
                                {
                                    "line": line_number,
                                    "reason": "source_timestamps_decrease",
                                }
                            )
                        last_source_timestamp_ms = source_timestamp_ms
                        source_timestamps.append(source_timestamp_ms)
            hashes = row.get("response_sha256")
            sources = row.get("source_responses")
            if isinstance(hashes, Mapping) and isinstance(sources, Mapping):
                for name in ("ticker", "orderbook", "current_funding", "funding_history"):
                    if name in sources and hashes.get(name) != _canonical_hash(sources[name]):
                        errors.append({"line": line_number, "reason": f"{name}_hash_mismatch"})
                if row.get("schema_version") == SCHEMA_VERSION:
                    for name in ("ticker", "orderbook", "current_funding", "funding_history"):
                        if name not in sources or name not in hashes:
                            errors.append({"line": line_number, "reason": f"{name}_response_missing"})
            elif row.get("schema_version") == SCHEMA_VERSION:
                errors.append({"line": line_number, "reason": "source_response_hashes_missing"})

            funding = row.get("funding")
            if isinstance(funding, Mapping):
                settlement = _number(funding.get("latest_settlement_time_ms"))
                interval_hours = _number(funding.get("interval_hours"))
                if settlement is not None and settlement >= 0.0:
                    funding_settlement_timestamps.add(int(settlement))
                if interval_hours is not None and interval_hours > 0.0:
                    funding_interval_hours.add(interval_hours)

    sorted_timestamps = sorted(set(captured_timestamps))
    capture_gaps_ms = [
        current - previous
        for previous, current in zip(sorted_timestamps, sorted_timestamps[1:])
    ]
    sorted_source_timestamps = sorted(set(source_timestamps))
    source_gaps_ms = [
        current - previous
        for previous, current in zip(sorted_source_timestamps, sorted_source_timestamps[1:])
    ]
    selected_gaps_ms = source_gaps_ms if use_source_timestamp else capture_gaps_ms
    max_capture_gap_ms = max(capture_gaps_ms, default=0)
    max_source_gap_ms = max(source_gaps_ms, default=0)
    max_gap_ms = max(selected_gaps_ms, default=0)
    gap_limit_ms: int | None = None
    if max_gap_seconds is not None:
        if not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0.0:
            raise ValueError("max_gap_seconds must be finite and > 0")
        gap_limit_ms = int(max_gap_seconds * 1000)
        if max_gap_ms > gap_limit_ms:
            errors.append(
                {
                    "reason": "source_gap_exceeds_limit" if use_source_timestamp else "capture_gap_exceeds_limit",
                    "max_gap_seconds": max_gap_ms / 1000.0,
                    "limit_seconds": max_gap_seconds,
                }
            )

    sorted_settlements = sorted(funding_settlement_timestamps)
    funding_interval_ms: int | None = None
    funding_missing_rows = 0
    funding_interval_gaps = 0
    funding_continuity_status = "not_checked"
    if len(funding_interval_hours) == 1:
        funding_interval_ms = int(next(iter(funding_interval_hours)) * 60 * 60 * 1000)
    elif len(funding_interval_hours) > 1:
        funding_continuity_status = "blocked"
        errors.append({"reason": "funding_interval_inconsistent"})
    if sorted_settlements and funding_interval_ms:
        first_settlement = sorted_settlements[0]
        last_settlement = sorted_settlements[-1]
        span = last_settlement - first_settlement
        if span % funding_interval_ms != 0:
            funding_continuity_status = "blocked"
            errors.append({"reason": "funding_settlement_alignment_invalid"})
        else:
            expected_settlements = set(
                range(first_settlement, last_settlement + 1, funding_interval_ms)
            )
            missing_settlements = expected_settlements - set(sorted_settlements)
            funding_missing_rows = len(missing_settlements)
            funding_interval_gaps = sum(
                current - previous != funding_interval_ms
                for previous, current in zip(sorted_settlements, sorted_settlements[1:])
            )
            funding_continuity_status = "passed" if not missing_settlements else "failed"
            if require_funding_continuity and missing_settlements:
                errors.append(
                    {
                        "reason": "funding_settlement_gap",
                        "missing_rows": len(missing_settlements),
                    }
                )
    elif require_funding_continuity:
        funding_continuity_status = "blocked"
        errors.append({"reason": "funding_settlement_history_missing"})

    status = "passed" if not errors and row_count > 0 else "failed"
    return {
        "schema_version": "bitget-market-log-audit-v2",
        "path": str(target),
        "sha256": digest.hexdigest(),
        "row_count": row_count,
        "first_timestamp_ms": first_timestamp_ms,
        "last_timestamp_ms": last_timestamp_ms,
        "observed_symbols": sorted(observed_symbols),
        "observed_categories": sorted(observed_categories),
        "max_capture_gap_seconds": max_capture_gap_ms / 1000.0,
        "max_source_gap_seconds": max_source_gap_ms / 1000.0,
        "max_gap_limit_seconds": max_gap_seconds,
        "gap_timestamp_kind": "source" if use_source_timestamp else "capture",
        "capture_gap_count": sum(gap > (gap_limit_ms or 0) for gap in capture_gaps_ms)
        if gap_limit_ms is not None
        else 0,
        "source_gap_count": sum(gap > (gap_limit_ms or 0) for gap in source_gaps_ms)
        if gap_limit_ms is not None
        else 0,
        "funding_settlement_timestamps": sorted_settlements,
        "funding_interval_hours": sorted(funding_interval_hours),
        "funding_missing_rows_between_observed_bounds": funding_missing_rows,
        "funding_interval_gaps": funding_interval_gaps,
        "funding_continuity_status": funding_continuity_status,
        "error_count": len(errors),
        "errors": errors,
        "status": status,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def write_market_log_integrity(
    path: Path,
    output: Path | None = None,
    *,
    max_gap_seconds: float | None = None,
    require_funding_continuity: bool = False,
    use_source_timestamp: bool = False,
) -> dict[str, Any]:
    result = verify_market_log(
        path,
        max_gap_seconds=max_gap_seconds,
        require_funding_continuity=require_funding_continuity,
        use_source_timestamp=use_source_timestamp,
    )
    destination = (output or path.with_suffix(path.suffix + ".integrity.json")).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result["integrity_output"] = str(destination)
    return result


def record_market_data(
    *,
    output: Path,
    symbol: str,
    category: str,
    depth: int,
    interval_seconds: float,
    duration_seconds: float | None,
    max_samples: int | None,
    request: RequestFn = _default_request,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Append snapshots until duration or sample limit is reached."""

    if interval_seconds <= 0.0 or not math.isfinite(interval_seconds):
        raise ValueError("interval_seconds must be finite and > 0")
    if duration_seconds is not None and duration_seconds < 0.0:
        raise ValueError("duration_seconds must be >= 0")
    if max_samples is not None and max_samples < 1:
        raise ValueError("max_samples must be positive")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    started = clock()
    samples = 0
    errors = 0
    with output.open("a", encoding="utf-8") as handle:
        while True:
            # A zero duration is a useful, explicit one-shot mode.  Apply the
            # stop conditions after the first attempted snapshot so callers
            # can use --duration-seconds 0 to capture one sample immediately.
            if samples > 0 and duration_seconds is not None and clock() - started >= duration_seconds:
                break
            if samples > 0 and max_samples is not None and samples >= max_samples:
                break
            try:
                event = collect_market_snapshot(
                    symbol=symbol,
                    category=category,
                    depth=depth,
                    request=request,
                )
                samples += 1
            except Exception as exc:
                errors += 1
                event = {
                    "schema_version": SCHEMA_VERSION,
                    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                    "exchange": "bitget",
                    "symbol": symbol,
                    "category": category,
                    "read_only_only": True,
                    "trading_operations_count": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            if duration_seconds == 0.0 or max_samples == samples:
                break
            sleep(interval_seconds)
    return {
        "output": str(output),
        "samples": samples,
        "errors": errors,
        "read_only_only": True,
        "trading_operations_count": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify an existing JSONL log without calling Bitget",
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--category", default="USDT-FUTURES")
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis_reports/crypto_bot_roadmap/inputs/bitget_market_snapshots.jsonl"),
    )
    parser.add_argument("--integrity-output", type=Path)
    parser.add_argument(
        "--max-gap-seconds",
        type=float,
        help="fail verification when two captures are farther apart than this",
    )
    parser.add_argument(
        "--require-funding-continuity",
        action="store_true",
        help="fail verification when observed funding settlements have a gap",
    )
    parser.add_argument(
        "--use-source-timestamp",
        action="store_true",
        help="apply --max-gap-seconds to exchange source timestamps instead of local capture time",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verify:
        result = write_market_log_integrity(
            args.output,
            args.integrity_output,
            max_gap_seconds=args.max_gap_seconds,
            require_funding_continuity=args.require_funding_continuity,
            use_source_timestamp=args.use_source_timestamp,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "passed" else 1
    result = record_market_data(
        output=args.output,
        symbol=args.symbol,
        category=args.category,
        depth=args.depth,
        interval_seconds=args.interval_seconds,
        duration_seconds=args.duration_seconds,
        max_samples=args.max_samples,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
