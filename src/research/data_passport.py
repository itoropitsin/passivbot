"""Immutable, fail-closed passport for funding input completeness.

The passport is the shared admission contract for historical preflight and
qualifying paper input.  It records facts derived from rows and source files;
declared counters or observed bounds cannot make an incomplete window qualify.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any


SCHEMA_VERSION = "crypto-data-passport-v1"


def _timestamp_ms(value: Any) -> int | None:
    """Parse a non-negative Unix millisecond or timezone-aware ISO timestamp."""

    if isinstance(value, bool):
        return None
    try:
        raw = str(value).strip()
        if raw.isdigit():
            timestamp = int(raw)
        else:
            if raw.endswith(("Z", "z")):
                raw = raw[:-1] + "+00:00"
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            timestamp = int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if timestamp >= 0 else None


def _required_timestamp(value: Any, *, name: str) -> int:
    timestamp = _timestamp_ms(value)
    if timestamp is None:
        raise ValueError(f"{name} must be a Unix timestamp or timezone-aware ISO value")
    return timestamp


def _strict_integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"{name} must be an integer")
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(f"{name} must be an integer")


def _utc_iso(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def canonical_symbol(value: Any) -> str:
    """Normalize spot/swap symbol spellings to the exchange funding symbol."""

    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("symbol must not be empty")
    if "/" in text:
        text = text.split("/", 1)[0]
    if ":" in text:
        text = text.split(":", 1)[0]
    return text if text.endswith("USDT") else f"{text}USDT"


def _row_timestamp(row: Any) -> int | None:
    if isinstance(row, Mapping):
        for key in (
            "timestamp_ms",
            "fundingRateTimestamp",
            "fundingTime",
            "timestamp",
            "ts",
        ):
            if key in row:
                timestamp = _timestamp_ms(row.get(key))
                if timestamp is not None:
                    return timestamp
        return None
    if isinstance(row, (list, tuple)) and row:
        return _timestamp_ms(row[0])
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
    if isinstance(value, bool):
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return rate if math.isfinite(rate) else None


def _row_symbol(row: Any) -> str | None:
    if not isinstance(row, Mapping) or "symbol" not in row:
        return None
    value = row.get("symbol")
    if value is None or isinstance(value, bool):
        return None
    try:
        return canonical_symbol(value)
    except ValueError:
        return None


def _is_synthetic(row: Any) -> bool:
    if not isinstance(row, Mapping):
        return False
    for key in ("synthetic", "is_synthetic", "synthetic_row"):
        if key not in row:
            continue
        value = row.get(key)
        if value is None or value is False:
            continue
        if isinstance(value, str) and value.strip().lower() in {
            "",
            "0",
            "false",
            "no",
            "real",
        }:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
            continue
        return True
    return False


def _normalize_source_hashes(
    source_sha256s: Mapping[str, Any] | Iterable[Any] | None,
) -> tuple[tuple[str, str], ...]:
    if source_sha256s is None:
        return ()
    if isinstance(source_sha256s, Mapping):
        items = source_sha256s.items()
    else:
        items = ((f"source_{index}", value) for index, value in enumerate(source_sha256s))
    normalized: list[tuple[str, str]] = []
    for name, value in items:
        label = str(name).strip()
        digest = str(value).strip().lower()
        if not label:
            raise ValueError("data passport source hash label must not be empty")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"data passport source hash {label!r} must be SHA-256")
        normalized.append((label, digest))
    normalized.sort()
    if len({label for label, _ in normalized}) != len(normalized):
        raise ValueError("data passport source hash labels must be unique")
    return tuple(normalized)


@dataclass(frozen=True)
class DataPassport:
    """Frozen, serializable facts used by both historical and qualifying paper."""

    schema_version: str
    symbol: str
    start_utc: str | None
    end_utc: str | None
    cadence_rule: str
    cadence_interval_ms: int
    expected_rows: int
    actual_rows: int
    missing_timestamps_ms: tuple[int, ...]
    duplicate_timestamps_ms: tuple[int, ...]
    duplicate_rows: int
    foreign_timestamps_ms: tuple[int, ...]
    foreign_symbols: tuple[str, ...]
    foreign_rows: int
    missing_symbol_rows: int
    future_timestamps_ms: tuple[int, ...]
    future_rows: int
    outside_requested_range_timestamps_ms: tuple[int, ...]
    outside_requested_range_rows: int
    unexpected_in_range_timestamps_ms: tuple[int, ...]
    invalid_rows: int
    synthetic_rows: int
    mark_missing_timestamps_ms: tuple[int, ...]
    mark_before_settlement_rows: int
    settlement_rows: int
    mark_before_settlement_coverage: float
    source_sha256s: tuple[tuple[str, str], ...]
    complete_requested_window: bool
    window_defined: bool
    exploratory: bool
    qualifying: bool

    @property
    def requested_start_utc(self) -> str | None:
        return self.start_utc

    @property
    def requested_end_utc(self) -> str | None:
        return self.end_utc

    @property
    def missing_rows(self) -> int:
        return len(self.missing_timestamps_ms)

    @property
    def source_missing_rows(self) -> int:
        return len(
            set(self.missing_timestamps_ms).difference(
                self.mark_missing_timestamps_ms
            )
        )

    @property
    def mark_missing_rows(self) -> int:
        return len(self.mark_missing_timestamps_ms)

    @property
    def unexpected_in_range_rows(self) -> int:
        return len(self.unexpected_in_range_timestamps_ms)

    @property
    def gap_timestamps_ms(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                set(self.missing_timestamps_ms)
                | set(self.mark_missing_timestamps_ms)
            )
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON representation without mutable internals."""

        missing = tuple(self.missing_timestamps_ms)
        mark_missing = tuple(self.mark_missing_timestamps_ms)
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "requested_window": {
                "start_utc": self.start_utc,
                "end_utc": self.end_utc,
            },
            "cadence_rule": self.cadence_rule,
            "cadence_interval_ms": self.cadence_interval_ms,
            "expected_rows": self.expected_rows,
            "actual_rows": self.actual_rows,
            "settlement_rows": self.settlement_rows,
            "missing_rows": self.missing_rows,
            "missing_timestamps_ms": list(missing),
            "source_missing_rows": self.source_missing_rows,
            "duplicate_rows": self.duplicate_rows,
            "duplicate_timestamps_ms": list(self.duplicate_timestamps_ms),
            "foreign_rows": self.foreign_rows,
            "foreign_timestamps_ms": list(self.foreign_timestamps_ms),
            "foreign_symbols": list(self.foreign_symbols),
            "missing_symbol_rows": self.missing_symbol_rows,
            "future_rows": self.future_rows,
            "future_timestamps_ms": list(self.future_timestamps_ms),
            "outside_requested_range_rows": self.outside_requested_range_rows,
            "outside_requested_range_timestamps_ms": list(
                self.outside_requested_range_timestamps_ms
            ),
            "unexpected_in_range_rows": self.unexpected_in_range_rows,
            "unexpected_in_range_timestamps_ms": list(
                self.unexpected_in_range_timestamps_ms
            ),
            "invalid_rows": self.invalid_rows,
            "synthetic_rows": self.synthetic_rows,
            "mark_missing_rows": self.mark_missing_rows,
            "mark_missing_timestamps_ms": list(mark_missing),
            "gap_timestamps_ms": list(self.gap_timestamps_ms),
            "mark_before_settlement_rows": self.mark_before_settlement_rows,
            "mark_before_settlement_coverage": self.mark_before_settlement_coverage,
            "source_sha256s": {
                label: digest for label, digest in self.source_sha256s
            },
            "complete_requested_window": self.complete_requested_window,
            "window_defined": self.window_defined,
            "exploratory": self.exploratory,
            "qualifying": self.qualifying,
        }


def build_data_passport(
    rows: Iterable[Any],
    *,
    symbol: str,
    start_ms: Any | None,
    end_ms: Any | None,
    cadence_interval_ms: int,
    mark_timestamps_ms: Iterable[Any] | None = None,
    mark_before_settlement_coverage_override: float | None = None,
    source_sha256s: Mapping[str, Any] | Iterable[Any] | None = None,
    exploratory: bool = False,
) -> DataPassport:
    """Build one passport from source rows and independently supplied marks.

    ``actual_rows`` counts unique, finite, expected-cadence settlements for the
    requested symbol.  A settlement with no strictly earlier mark remains a
    mark gap and therefore appears in ``missing_timestamps_ms`` as well as the
    dedicated mark-gap fields.
    """

    if not isinstance(exploratory, bool):
        raise ValueError("data passport exploratory must be a boolean")
    requested_symbol = canonical_symbol(symbol)
    try:
        interval_ms = _strict_integer(
            cadence_interval_ms, name="data passport cadence_interval_ms"
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("data passport cadence_interval_ms must be positive") from exc
    if interval_ms <= 0:
        raise ValueError("data passport cadence_interval_ms must be positive")

    parsed_start = None if start_ms is None else _required_timestamp(start_ms, name="start")
    parsed_end = None if end_ms is None else _required_timestamp(end_ms, name="end")
    if (parsed_start is None) != (parsed_end is None):
        raise ValueError("data passport start and end must be provided together")
    if parsed_start is not None and parsed_end is not None and parsed_end < parsed_start:
        raise ValueError("data passport end must not precede start")

    expected_timestamps = (
        set(range(parsed_start, parsed_end + 1, interval_ms))
        if parsed_start is not None and parsed_end is not None
        else set()
    )
    alignment_valid = (
        parsed_start is not None
        and parsed_end is not None
        and parsed_start % interval_ms == 0
        and (parsed_end - parsed_start) % interval_ms == 0
    )

    materialized = list(rows)
    timestamp_counts: dict[int, int] = {}
    valid_records: list[tuple[int, str | None]] = []
    invalid_rows = 0
    synthetic_rows = 0
    missing_symbol_rows = 0
    foreign_rows = 0
    foreign_timestamps: set[int] = set()
    foreign_symbols: set[str] = set()

    for row in materialized:
        timestamp = _row_timestamp(row)
        rate = _row_rate(row)
        if _is_synthetic(row):
            synthetic_rows += 1
        if timestamp is None or rate is None:
            invalid_rows += 1
            continue
        timestamp_counts[timestamp] = timestamp_counts.get(timestamp, 0) + 1
        row_symbol = _row_symbol(row)
        if row_symbol is None:
            missing_symbol_rows += 1
        elif row_symbol != requested_symbol:
            foreign_rows += 1
            foreign_timestamps.add(timestamp)
            foreign_symbols.add(row_symbol)
        valid_records.append((timestamp, row_symbol))

    duplicate_timestamps = tuple(
        sorted(timestamp for timestamp, count in timestamp_counts.items() if count > 1)
    )
    duplicate_rows = sum(
        count - 1 for count in timestamp_counts.values() if count > 1
    )

    valid_symbol_timestamps = {
        timestamp
        for timestamp, row_symbol in valid_records
        if row_symbol == requested_symbol
    }
    if expected_timestamps:
        in_range_timestamps = {
            timestamp
            for timestamp in valid_symbol_timestamps
            if parsed_start <= timestamp <= parsed_end
        }
        valid_expected_timestamps = in_range_timestamps & expected_timestamps
        source_missing_timestamps = expected_timestamps - valid_expected_timestamps
        unexpected_timestamps = in_range_timestamps - expected_timestamps
        outside_timestamps = {
            timestamp
            for timestamp in timestamp_counts
            if timestamp < parsed_start or timestamp > parsed_end
        }
        future_timestamps = {
            timestamp for timestamp in timestamp_counts if timestamp > parsed_end
        }
    else:
        valid_expected_timestamps = valid_symbol_timestamps
        source_missing_timestamps = set()
        unexpected_timestamps = set()
        outside_timestamps = set()
        future_timestamps = set()

    if mark_timestamps_ms is None:
        mark_timestamps: set[int] | None = None
    else:
        mark_timestamps = set()
        for value in mark_timestamps_ms:
            timestamp = _timestamp_ms(value)
            if timestamp is not None:
                mark_timestamps.add(timestamp)

    mark_missing_timestamps: set[int] = set()
    if mark_timestamps is not None:
        mark_missing_timestamps = {
            timestamp
            for timestamp in valid_expected_timestamps
            if not any(mark_timestamp < timestamp for mark_timestamp in mark_timestamps)
        }

    if mark_before_settlement_coverage_override is not None:
        try:
            coverage = float(mark_before_settlement_coverage_override)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "mark_before_settlement_coverage_override must be finite"
            ) from exc
        if not math.isfinite(coverage) or coverage < 0.0 or coverage > 1.0:
            raise ValueError(
                "mark_before_settlement_coverage_override must be between 0 and 1"
            )
        mark_before_rows = min(
            len(valid_expected_timestamps),
            int(round(len(valid_expected_timestamps) * coverage)),
        )
    elif mark_timestamps is None:
        coverage = 0.0
        mark_before_rows = 0
    else:
        mark_before_rows = len(valid_expected_timestamps) - len(mark_missing_timestamps)
        coverage = (
            mark_before_rows / len(valid_expected_timestamps)
            if valid_expected_timestamps
            else 0.0
        )

    missing_timestamps = tuple(
        sorted(set(source_missing_timestamps) | mark_missing_timestamps)
    )
    source_hash_pairs = _normalize_source_hashes(source_sha256s)
    complete_requested_window = bool(
        parsed_start is not None
        and parsed_end is not None
        and alignment_valid
        and invalid_rows == 0
        and synthetic_rows == 0
        and duplicate_rows == 0
        and missing_symbol_rows == 0
        and foreign_rows == 0
        and not outside_timestamps
        and not future_timestamps
        and not unexpected_timestamps
        and not source_missing_timestamps
        and not mark_missing_timestamps
        and len(valid_expected_timestamps) == len(expected_timestamps)
        and coverage >= 1.0
    )
    qualifying = bool(
        complete_requested_window
        and bool(source_hash_pairs)
        and not exploratory
    )

    return DataPassport(
        schema_version=SCHEMA_VERSION,
        symbol=requested_symbol,
        start_utc=_utc_iso(parsed_start),
        end_utc=_utc_iso(parsed_end),
        cadence_rule=f"fixed_interval_{interval_ms // 3_600_000}h"
        if interval_ms % 3_600_000 == 0
        else f"fixed_interval_{interval_ms}ms",
        cadence_interval_ms=interval_ms,
        expected_rows=len(expected_timestamps),
        actual_rows=len(valid_expected_timestamps),
        missing_timestamps_ms=missing_timestamps,
        duplicate_timestamps_ms=duplicate_timestamps,
        duplicate_rows=duplicate_rows,
        foreign_timestamps_ms=tuple(sorted(foreign_timestamps)),
        foreign_symbols=tuple(sorted(foreign_symbols)),
        foreign_rows=foreign_rows,
        missing_symbol_rows=missing_symbol_rows,
        future_timestamps_ms=tuple(sorted(future_timestamps)),
        future_rows=len(future_timestamps),
        outside_requested_range_timestamps_ms=tuple(sorted(outside_timestamps)),
        outside_requested_range_rows=len(outside_timestamps),
        unexpected_in_range_timestamps_ms=tuple(sorted(unexpected_timestamps)),
        invalid_rows=invalid_rows,
        synthetic_rows=synthetic_rows,
        mark_missing_timestamps_ms=tuple(sorted(mark_missing_timestamps)),
        mark_before_settlement_rows=mark_before_rows,
        settlement_rows=len(valid_expected_timestamps),
        mark_before_settlement_coverage=coverage,
        source_sha256s=source_hash_pairs,
        complete_requested_window=complete_requested_window,
        window_defined=parsed_start is not None and parsed_end is not None,
        exploratory=bool(exploratory),
        qualifying=qualifying,
    )


__all__ = [
    "DataPassport",
    "SCHEMA_VERSION",
    "build_data_passport",
    "canonical_symbol",
]
