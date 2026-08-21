"""Repair missing Bitget 1-minute OHLCV rows using public market candles only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import numpy as np

from ohlcv_catalog import OhlcvCatalog
from ohlcv_store import OhlcvStore, timeframe_to_interval_ms
from tools.bitget_public_execution_inputs import (
    RequestFn,
    _default_request,
    _fetch_candle_history_parallel,
    build_public_requester,
    _fetch_candle_history,
    _iso_timestamp,
    _parse_timestamp,
    _sha256,
)


PUBLIC_DOC = "https://www.bitget.com/api-doc/uta/public/Get-History-Candle-Data"


def _missing_ranges(valid: np.ndarray, *, start_ms: int, interval_ms: int) -> list[tuple[int, int]]:
    missing = np.flatnonzero(~np.asarray(valid, dtype=np.bool_))
    if len(missing) == 0:
        return []
    ranges: list[tuple[int, int]] = []
    range_start = range_end = int(missing[0])
    for index in missing[1:]:
        index = int(index)
        if index != range_end + 1:
            ranges.append(
                (
                    start_ms + range_start * interval_ms,
                    start_ms + range_end * interval_ms,
                )
            )
            range_start = index
        range_end = index
    ranges.append(
        (start_ms + range_start * interval_ms, start_ms + range_end * interval_ms)
    )
    return ranges


def _market_rows_to_arrays(rows: list[list[Any]]) -> tuple[np.ndarray, np.ndarray]:
    timestamps: list[int] = []
    values: list[list[float]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            timestamp = int(row[0])
            # v2 OHLCV stores HLCV, while Bitget returns OHLCV.
            high, low, close, volume = (float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        except (TypeError, ValueError):
            continue
        if not np.isfinite([high, low, close, volume]).all() or close <= 0.0:
            continue
        timestamps.append(timestamp)
        values.append([high, low, close, max(0.0, volume)])
    if not timestamps:
        return np.empty(0, dtype=np.int64), np.empty((0, 4), dtype=np.float32)
    order = np.argsort(np.asarray(timestamps, dtype=np.int64), kind="stable")
    ts = np.asarray(timestamps, dtype=np.int64)[order]
    vals = np.asarray(values, dtype=np.float32)[order]
    unique, unique_indices = np.unique(ts, return_index=True)
    return unique, vals[unique_indices]


def repair_public_gaps(
    *,
    root: str | Path,
    exchange: str = "bitget",
    symbol: str = "BTC/USDT:USDT",
    api_symbol: str = "BTCUSDT",
    start: str,
    end: str,
    max_pages: int = 100,
    parallel_workers: int = 1,
    delay_seconds: float = 0.0,
    request: RequestFn = _default_request,
) -> dict[str, Any]:
    """Fill only invalid cache rows; never synthesize or overwrite valid rows."""

    interval_ms = timeframe_to_interval_ms("1m")
    start_ms = _parse_timestamp(start)
    end_ms = _parse_timestamp(end, end_of_day=True)
    end_ms -= end_ms % interval_ms
    if start_ms % interval_ms or start_ms > end_ms:
        raise ValueError("start/end must describe an increasing minute-aligned range")

    root_path = Path(root).expanduser().resolve()
    catalog = OhlcvCatalog(root_path / "catalog.sqlite")
    store = OhlcvStore(root_path, catalog)
    before = store.read_range(exchange, "1m", symbol, start_ms, end_ms)
    missing_before = _missing_ranges(before.valid, start_ms=start_ms, interval_ms=interval_ms)
    repaired_rows = 0
    requests: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for gap_start, gap_end in missing_before:
        if parallel_workers > 1 and delay_seconds == 0.0:
            rows, raw_pages = _fetch_candle_history_parallel(
                request_factory=build_public_requester,
                category="USDT-FUTURES",
                symbol=api_symbol,
                candle_type="market",
                interval="1m",
                start_ms=gap_start,
                end_ms=gap_end,
                max_pages=max_pages,
                max_workers=parallel_workers,
            )
        else:
            rows, raw_pages = _fetch_candle_history(
                request,
                category="USDT-FUTURES",
                symbol=api_symbol,
                candle_type="market",
                interval="1m",
                start_ms=gap_start,
                end_ms=gap_end,
                max_pages=max_pages,
                delay_seconds=delay_seconds,
            )
        ts, values = _market_rows_to_arrays(rows)
        wanted = np.arange(gap_start, gap_end + interval_ms, interval_ms, dtype=np.int64)
        wanted_set = set(int(value) for value in wanted)
        keep = np.asarray([int(value) in wanted_set for value in ts], dtype=np.bool_)
        ts = ts[keep]
        values = values[keep]
        existing = set(int(value) for value in ts)
        missing_after_fetch = [int(value) for value in wanted if int(value) not in existing]
        if len(ts):
            store.write_rows(exchange, "1m", symbol, ts, values)
            repaired_rows += len(ts)
        requests.append(
            {
                "start_utc": _iso_timestamp(gap_start),
                "end_utc": _iso_timestamp(gap_end),
                "rows_returned": len(rows),
                "rows_written": len(ts),
                "raw_pages_sha256": _sha256(raw_pages),
                "missing_rows_after_fetch": len(missing_after_fetch),
            }
        )
        if missing_after_fetch:
            unresolved.append(
                {
                    "start_utc": _iso_timestamp(missing_after_fetch[0]),
                    "end_utc": _iso_timestamp(missing_after_fetch[-1]),
                    "rows": len(missing_after_fetch),
                }
            )

    after = store.read_range(exchange, "1m", symbol, start_ms, end_ms)
    missing_after = _missing_ranges(after.valid, start_ms=start_ms, interval_ms=interval_ms)
    # A fetch log can describe a wider leading-unavailable interval than the
    # actual invalid cache rows. Clear only catalog portions that are now
    # demonstrably valid; never hide an unresolved data gap in metadata.
    for gap in catalog.get_gaps(exchange, "1m", symbol, start_ms, end_ms):
        gap_start = max(start_ms, int(gap.start_ts))
        gap_end = min(end_ms, int(gap.end_ts))
        if gap_start > gap_end:
            continue
        gap_snapshot = store.read_range(exchange, "1m", symbol, gap_start, gap_end)
        if np.all(gap_snapshot.valid):
            catalog.clear_gap_range(
                exchange=exchange,
                timeframe="1m",
                symbol=symbol,
                start_ts=gap_start,
                end_ts=gap_end,
            )

    return {
        "schema_version": "bitget-public-ohlcv-repair-v1",
        "status": "repaired" if not missing_after else "blocked",
        "collected_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "exchange": exchange,
        "symbol": symbol,
        "api_symbol": api_symbol,
        "timeframe": "1m",
        "range": {"start_utc": _iso_timestamp(start_ms), "end_utc": _iso_timestamp(end_ms)},
        "read_only_external_operations": True,
        "trading_operations_count": 0,
        "before_missing_rows": int(np.count_nonzero(~before.valid)),
        "repaired_rows": repaired_rows,
        "after_missing_rows": int(np.count_nonzero(~after.valid)),
        "missing_ranges_before": [
            {"start_utc": _iso_timestamp(start), "end_utc": _iso_timestamp(end)}
            for start, end in missing_before
        ],
        "missing_ranges_after": [
            {"start_utc": _iso_timestamp(start), "end_utc": _iso_timestamp(end)}
            for start, end in missing_after
        ],
        "unresolved_ranges": unresolved,
        "requests": requests,
        "documentation": PUBLIC_DOC,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("caches/ohlcvs"))
    parser.add_argument("--exchange", default="bitget")
    parser.add_argument("--symbol", default="BTC/USDT:USDT")
    parser.add_argument("--api-symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True, help="UTC date or ISO timestamp")
    parser.add_argument("--end", required=True, help="UTC date or ISO timestamp")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--parallel-workers", type=int, default=4)
    parser.add_argument("--delay-seconds", type=float, default=0.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_ohlcv_repair.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_pages < 1 or not 1 <= args.parallel_workers <= 8 or args.delay_seconds < 0.0:
        raise ValueError(
            "max-pages must be positive, parallel-workers must be between 1 and 8, "
            "and delay-seconds must be non-negative"
        )
    report = repair_public_gaps(
        root=args.root,
        exchange=args.exchange,
        symbol=args.symbol,
        api_symbol=args.api_symbol,
        start=args.start,
        end=args.end,
        max_pages=args.max_pages,
        parallel_workers=args.parallel_workers,
        delay_seconds=args.delay_seconds,
        request=build_public_requester(),
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": report["status"], "repaired_rows": report["repaired_rows"], "after_missing_rows": report["after_missing_rows"]}, sort_keys=True))
    return 0 if report["status"] == "repaired" else 2


if __name__ == "__main__":
    raise SystemExit(main())
