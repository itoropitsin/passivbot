"""Audit the stored Bitget OHLCV cache without downloading or changing it.

The public-data report checks the provenance and coverage of the repair job.
This command checks the actual v2 cache that a backtest will read: every
requested minute must be present, finite, price-ordered, and backed by a
verified chunk checksum.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import numpy as np

from ohlcv_catalog import OhlcvCatalog
from ohlcv_store import OhlcvStore, timeframe_to_interval_ms


SCHEMA_VERSION = "bitget-ohlcv-quality-v1"


def _timestamp_ms(value: Any, *, end_of_day: bool = False) -> int:
    raw = str(value).strip()
    if raw.isdigit():
        return int(raw)
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        raw = raw + ("T23:59:59.999999+00:00" if end_of_day else "T00:00:00+00:00")
    elif raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc).isoformat()


def _missing_ranges(valid: np.ndarray, timestamps: np.ndarray) -> list[dict[str, Any]]:
    missing = np.flatnonzero(~np.asarray(valid, dtype=np.bool_))
    if missing.size == 0:
        return []
    ranges: list[dict[str, Any]] = []
    start = previous = int(missing[0])
    for raw_index in missing[1:]:
        index = int(raw_index)
        if index != previous + 1:
            ranges.append(
                {
                    "start_utc": _iso(int(timestamps[start])),
                    "end_utc": _iso(int(timestamps[previous])),
                    "rows": previous - start + 1,
                }
            )
            start = index
        previous = index
    ranges.append(
        {
            "start_utc": _iso(int(timestamps[start])),
            "end_utc": _iso(int(timestamps[previous])),
            "rows": previous - start + 1,
        }
    )
    return ranges


def audit_ohlcv_cache(
    *,
    root: str | Path,
    exchange: str,
    timeframe: str,
    symbol: str,
    start: str,
    end: str,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Return a fail-closed quality report for one aligned cache range."""

    interval_ms = timeframe_to_interval_ms(timeframe)
    start_ms = _timestamp_ms(start)
    end_ms = _timestamp_ms(end, end_of_day=True)
    end_ms -= end_ms % interval_ms
    if start_ms % interval_ms or end_ms < start_ms:
        raise ValueError(f"start/end must describe an increasing {timeframe}-aligned range")

    root_path = Path(root).expanduser().resolve()
    catalog = OhlcvCatalog(root_path / "catalog.sqlite")
    store = OhlcvStore(root_path, catalog)
    expected_rows = ((end_ms - start_ms) // interval_ms) + 1
    checksum_error: str | None = None
    try:
        result = store.read_range(exchange, timeframe, symbol, start_ms, end_ms)
        chunks = list(store.iter_overlapping_chunks(exchange, timeframe, symbol, start_ms, end_ms))
    except (OSError, ValueError) as exc:
        checksum_error = f"{type(exc).__name__}: {exc}"
        result = None
        chunks = []

    if result is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "exchange": exchange,
            "timeframe": timeframe,
            "symbol": symbol,
            "cache_path": str(root_path),
            "remote_fetch_policy": "local_only",
            "remote_fetch_count": 0,
            "range": {"start_utc": _iso(start_ms), "end_utc": _iso(end_ms)},
            "expected_rows": expected_rows,
            "valid_rows": 0,
            "missing_rows": expected_rows,
            "missing_ranges": [{"start_utc": _iso(start_ms), "end_utc": _iso(end_ms), "rows": expected_rows}],
            "checksum_verified_chunks": 0,
            "checksum_error": checksum_error,
            "read_only_external_operations": True,
            "trading_operations_count": 0,
        }

    timestamps = result.timestamps
    values = result.values
    valid = np.asarray(result.valid, dtype=np.bool_)
    valid_values = values[valid]
    finite = np.isfinite(valid_values).all(axis=1) if valid_values.size else np.empty(0, dtype=bool)
    finite_values = valid_values[finite]
    nonpositive_price_rows = (
        int(
            np.count_nonzero(
                (finite_values[:, 0] <= 0)
                | (finite_values[:, 1] <= 0)
                | (finite_values[:, 2] <= 0)
            )
        )
        if finite_values.size
        else 0
    )
    price_order_rows = (
        int(
            np.count_nonzero(
                (finite_values[:, 0] < finite_values[:, 1])
                | (finite_values[:, 2] < finite_values[:, 1])
                | (finite_values[:, 2] > finite_values[:, 0])
            )
        )
        if finite_values.size
        else 0
    )
    negative_volume_rows = (
        int(np.count_nonzero(finite_values[:, 3] < 0)) if finite_values.size else 0
    )
    nonfinite_rows = int(np.count_nonzero(~finite))
    valid_rows = int(np.count_nonzero(valid))
    missing_rows = expected_rows - valid_rows
    timestamps_unique = int(np.unique(timestamps).size) == expected_rows
    future_rows = 0
    as_of_ms: int | None = None
    if as_of is not None:
        as_of_ms = _timestamp_ms(as_of)
        future_rows = int(np.count_nonzero(timestamps > as_of_ms))

    persistent_gaps = [
        gap
        for gap in catalog.get_gaps(exchange, timeframe, symbol, start_ms, end_ms)
        if gap.persistent
    ]
    checksum_error = None
    verified_chunks = 0
    try:
        for chunk in chunks:
            store.verify_chunk_checksum(chunk)
            verified_chunks += 1
    except (OSError, ValueError) as exc:
        checksum_error = f"{type(exc).__name__}: {exc}"

    checks = {
        "coverage_complete": valid_rows == expected_rows,
        "timestamps_unique": timestamps_unique,
        "values_finite": nonfinite_rows == 0,
        "prices_positive": nonpositive_price_rows == 0,
        "prices_ordered": price_order_rows == 0,
        "volume_nonnegative": negative_volume_rows == 0,
        "no_future_rows": future_rows == 0,
        "no_persistent_gaps": not persistent_gaps,
        "chunk_checksums_verified": checksum_error is None and verified_chunks == len(chunks),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if all(checks.values()) else "blocked",
        "exchange": exchange,
        "timeframe": timeframe,
        "symbol": symbol,
        "cache_path": str(root_path),
        "remote_fetch_policy": "local_only",
        "remote_fetch_count": 0,
        "range": {"start_utc": _iso(start_ms), "end_utc": _iso(end_ms)},
        "as_of_utc": _iso(as_of_ms) if as_of_ms is not None else None,
        "expected_rows": expected_rows,
        "valid_rows": valid_rows,
        "missing_rows": missing_rows,
        "missing_ranges": _missing_ranges(valid, timestamps),
        "value_quality": {
            "nonfinite_rows": nonfinite_rows,
            "nonpositive_price_rows": nonpositive_price_rows,
            "price_order_rows": price_order_rows,
            "negative_volume_rows": negative_volume_rows,
        },
        "future_rows": future_rows,
        "persistent_gaps": [
            {
                "start_utc": _iso(gap.start_ts),
                "end_utc": _iso(gap.end_ts),
                "reason": gap.reason,
            }
            for gap in persistent_gaps
        ],
        "chunks": len(chunks),
        "checksum_verified_chunks": verified_chunks,
        "checksum_error": checksum_error,
        "checks": checks,
        "read_only_external_operations": True,
        "trading_operations_count": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("caches/ohlcvs"))
    parser.add_argument("--exchange", default="bitget")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit_ohlcv_cache(
        root=args.root,
        exchange=args.exchange,
        timeframe=args.timeframe,
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        as_of=args.as_of,
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": report["status"]}, sort_keys=True))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
