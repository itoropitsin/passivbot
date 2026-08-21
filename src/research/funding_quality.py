"""Validate funding-rate rows against an explicit requested time window."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Mapping


def parse_timestamp_ms(value: Any) -> int | None:
    """Parse milliseconds or an ISO timestamp with an explicit UTC zone."""

    try:
        if isinstance(value, bool):
            return None
        raw = str(value).strip()
        if raw.isdigit():
            timestamp = int(raw)
        else:
            if raw.endswith(("Z", "z")):
                raw = raw[:-1] + "+00:00"
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return None
            timestamp = int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if timestamp >= 0 else None


def _row_timestamp(row: Any) -> int | None:
    if isinstance(row, Mapping):
        for key in (
            "timestamp_ms",
            "fundingRateTimestamp",
            "fundingTime",
            "timestamp",
            "ts",
        ):
            timestamp = parse_timestamp_ms(row.get(key))
            if timestamp is not None:
                return timestamp
    elif isinstance(row, (list, tuple)) and row:
        return parse_timestamp_ms(row[0])
    return None


def _row_rate(row: Any) -> float | None:
    value: Any = None
    if isinstance(row, Mapping):
        for key in ("funding_rate", "fundingRate", "rate", "value"):
            if key in row:
                value = row.get(key)
                break
    elif isinstance(row, (list, tuple)) and len(row) > 1:
        value = row[1]
    try:
        rate = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return rate if math.isfinite(rate) else None


def _row_symbol(row: Any) -> str | None:
    if not isinstance(row, Mapping):
        return None
    value = row.get("symbol")
    if value is None or isinstance(value, bool):
        return None
    symbol = str(value).strip().upper()
    return symbol or None


def is_synthetic_funding_row(row: Any) -> bool:
    """Return whether a row explicitly marks itself as synthetic.

    Historical funding is evidence, so an explicit synthetic marker is never
    treated as harmless metadata.  Unknown non-empty marker values are also
    rejected rather than guessed to be real data.
    """

    if not isinstance(row, Mapping):
        return False
    for key in ("synthetic", "is_synthetic", "synthetic_row"):
        if key not in row:
            continue
        value = row.get(key)
        if value is None or value is False:
            continue
        if isinstance(value, str) and value.strip().lower() in {"", "0", "false", "no", "real"}:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
            continue
        return True
    return False


def normalize_funding_rows(rows: Iterable[Any]) -> tuple[list[dict[str, Any]], int]:
    """Normalize valid rows and count rows that cannot be validated.

    The returned rows contain only values used by the funding-quality
    contract.  No declared source counters participate in this operation.
    """

    normalized: list[dict[str, Any]] = []
    invalid_rows = 0
    for row in rows:
        if is_synthetic_funding_row(row):
            invalid_rows += 1
            continue
        timestamp = _row_timestamp(row)
        rate = _row_rate(row)
        if timestamp is None or rate is None:
            invalid_rows += 1
            continue
        normalized.append(
            {
                "timestamp_ms": timestamp,
                "funding_rate": rate,
            }
        )
    return normalized, invalid_rows


def validate_funding_rows(
    rows: Iterable[Any],
    *,
    start_ms: int,
    end_ms: int,
    interval_hours: int,
    expected_symbol: str | None = None,
) -> dict[str, Any]:
    """Return quality facts derived from the rows, never from claimed counters.

    The requested range is inclusive.  A complete archive must contain exactly
    one finite rate at every expected interval, with no rows outside the range.
    """

    interval_ms = int(interval_hours) * 60 * 60 * 1000
    if interval_ms <= 0:
        raise ValueError("interval_hours must be positive")
    if end_ms < start_ms:
        raise ValueError("end_ms must not be before start_ms")

    materialized_rows = list(rows)
    synthetic_rows = sum(is_synthetic_funding_row(row) for row in materialized_rows)
    normalized, invalid_rows = normalize_funding_rows(materialized_rows)
    timestamps = [int(row["timestamp_ms"]) for row in normalized]

    requested_symbol = (
        str(expected_symbol).strip().upper() if expected_symbol is not None else None
    )
    missing_symbol_rows = 0
    foreign_symbol_rows = 0
    if requested_symbol:
        for row in materialized_rows:
            row_symbol = _row_symbol(row)
            if row_symbol is None:
                missing_symbol_rows += 1
            elif row_symbol != requested_symbol:
                foreign_symbol_rows += 1

    timestamp_set = set(timestamps)
    duplicate_rows = len(timestamps) - len(timestamp_set)
    window_alignment_valid = (
        start_ms % interval_ms == 0
        and (end_ms - start_ms) % interval_ms == 0
    )
    expected_timestamps = set(range(start_ms, end_ms + 1, interval_ms))
    in_range = {timestamp for timestamp in timestamp_set if start_ms <= timestamp <= end_ms}
    missing_timestamps = expected_timestamps - in_range
    unexpected_in_range = in_range - expected_timestamps
    outside_requested_range = sum(
        timestamp < start_ms or timestamp > end_ms for timestamp in timestamp_set
    )
    ordered = sorted(timestamp_set)
    interval_gaps = sum(
        current - previous != interval_ms
        for previous, current in zip(ordered, ordered[1:])
    )
    future_rows = sum(timestamp > end_ms for timestamp in timestamp_set)
    complete_requested_window = (
        invalid_rows == 0
        and synthetic_rows == 0
        and window_alignment_valid
        and duplicate_rows == 0
        and missing_symbol_rows == 0
        and foreign_symbol_rows == 0
        and outside_requested_range == 0
        and not missing_timestamps
        and not unexpected_in_range
        and len(in_range) == len(expected_timestamps)
    )
    return {
        "expected_interval_hours": int(interval_hours),
        "expected_rows": len(expected_timestamps),
        "rows": len(timestamp_set),
        "actual_rows": len(in_range),
        "first_timestamp_ms": min(timestamp_set) if timestamp_set else None,
        "last_timestamp_ms": max(timestamp_set) if timestamp_set else None,
        "future_rows": future_rows,
        "outside_requested_range": outside_requested_range,
        "invalid_rows": invalid_rows,
        "synthetic_rows": synthetic_rows,
        "window_alignment_valid": window_alignment_valid,
        "duplicate_rows": duplicate_rows,
        "expected_symbol": requested_symbol,
        "missing_symbol_rows": missing_symbol_rows,
        "foreign_symbol_rows": foreign_symbol_rows,
        "missing_rows": len(missing_timestamps),
        "unexpected_in_range_rows": len(unexpected_in_range),
        "interval_gaps": interval_gaps,
        "complete_between_observed_bounds": bool(ordered) and interval_gaps == 0,
        "complete_requested_window": complete_requested_window,
    }


__all__ = [
    "is_synthetic_funding_row",
    "normalize_funding_rows",
    "parse_timestamp_ms",
    "validate_funding_rows",
]
