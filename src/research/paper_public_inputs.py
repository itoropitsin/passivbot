"""Read-only public Bitget execution inputs for the local paper ledger.

The feed is deliberately an input boundary, not a downloader.  It accepts
only saved public Bitget artifacts, keeps the raw-file SHA-256, and refuses to
derive a funding mark from a later observation.  Missing source data stays
missing so a paper run cannot turn an incomplete feed into a successful cost
reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from research.data_passport import (
    DataPassport,
    build_data_passport,
    canonical_symbol,
)


SCHEMA_VERSION = "crypto-paper-public-input-feed-v1"
SUPPORTED_INPUT_SCHEMAS = frozenset(
    {
        "bitget-public-execution-inputs-v1",
        "bitget-market-snapshot-v1",
        "bitget-market-snapshot-v3",
    }
)


class PublicExecutionInputError(ValueError):
    """Raised when a saved public input cannot be used safely."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _number(value: Any, *, name: str, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise PublicExecutionInputError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PublicExecutionInputError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0.0):
        requirement = "positive and finite" if positive else "finite"
        raise PublicExecutionInputError(f"{name} must be {requirement}")
    return result


def _timestamp(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise PublicExecutionInputError(f"{name} must be a Unix timestamp")
    try:
        raw = str(value).strip()
        if raw.isdigit():
            result = int(raw)
        else:
            if raw.endswith(("Z", "z")):
                raw = raw[:-1] + "+00:00"
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise PublicExecutionInputError(
                    f"{name} must include an explicit timezone"
                )
            result = int(parsed.timestamp() * 1000)
    except PublicExecutionInputError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise PublicExecutionInputError(f"{name} must be a Unix timestamp") from exc
    if result <= 0:
        raise PublicExecutionInputError(f"{name} must be positive")
    return result


def _canonical_symbol(value: Any) -> str:
    try:
        return canonical_symbol(value)
    except ValueError as exc:
        raise PublicExecutionInputError(str(exc)) from exc


def _assert_read_only(root: Mapping[str, Any], *, line: int | None = None) -> None:
    prefix = f"line {line}: " if line is not None else ""
    if root.get("read_only_only") is not True:
        raise PublicExecutionInputError(prefix + "public input is not marked read_only_only")
    if root.get("trading_operations_count") != 0:
        raise PublicExecutionInputError(prefix + "public input reports trading operations")
    if str(root.get("exchange") or "").strip().lower() != "bitget":
        raise PublicExecutionInputError(prefix + "public input exchange must be bitget")


def _row_price(rows: list[tuple[int, float]], timestamp_ms: int) -> float | None:
    """Return the latest price strictly before the funding timestamp."""

    candidates = [price for timestamp, price in rows if timestamp < timestamp_ms]
    return candidates[-1] if candidates else None


def _candle_rows(value: Any, *, name: str) -> list[tuple[int, float]]:
    if not isinstance(value, list):
        raise PublicExecutionInputError(f"{name} must be a list")
    normalized: list[tuple[int, float]] = []
    for index, row in enumerate(value):
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            raise PublicExecutionInputError(f"{name}[{index}] must contain a candle")
        timestamp = _timestamp(row[0], name=f"{name}[{index}].timestamp")
        close = _number(row[4], name=f"{name}[{index}].close", positive=True)
        normalized.append((timestamp, close))
    normalized.sort(key=lambda item: item[0])
    if any(current[0] == previous[0] for previous, current in zip(normalized, normalized[1:])):
        raise PublicExecutionInputError(f"{name} contains duplicate timestamps")
    return normalized


def _feed_contract(
    metadata: Mapping[str, Any] | None,
    *,
    requested_start: Any | None,
    requested_end: Any | None,
    cadence_interval_hours: int | None,
    exploratory: bool,
) -> tuple[Any | None, Any | None, int, bool]:
    """Resolve an explicit feed window without using observed bounds."""

    if not isinstance(exploratory, bool):
        raise PublicExecutionInputError("feed exploratory must be a boolean")
    root = metadata if isinstance(metadata, Mapping) else {}
    quality = root.get("quality")
    quality = quality if isinstance(quality, Mapping) else {}
    funding = quality.get("funding")
    funding = funding if isinstance(funding, Mapping) else {}

    start = requested_start
    if start is None:
        start = root.get("requested_start_utc", quality.get("requested_start_utc"))
    end = requested_end
    if end is None:
        end = root.get("requested_end_utc", quality.get("requested_end_utc"))

    raw_hours: Any = cadence_interval_hours
    if raw_hours is None:
        raw_hours = funding.get("expected_interval_hours", root.get("cadence_interval_hours", 8))
    try:
        if isinstance(raw_hours, bool):
            raise ValueError
        if isinstance(raw_hours, float):
            if not math.isfinite(raw_hours) or not raw_hours.is_integer():
                raise ValueError
            interval_hours = int(raw_hours)
        elif isinstance(raw_hours, int):
            interval_hours = raw_hours
        elif isinstance(raw_hours, str) and raw_hours.strip().isdigit():
            interval_hours = int(raw_hours.strip())
        else:
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise PublicExecutionInputError(
            "funding cadence interval must be a positive integer number of hours"
        ) from exc
    if interval_hours <= 0:
        raise PublicExecutionInputError(
            "funding cadence interval must be a positive integer number of hours"
        )

    explicitly_exploratory = (
        exploratory is True
        or root.get("exploratory") is True
        or root.get("mode") == "exploratory"
    )
    return start, end, interval_hours, explicitly_exploratory


def _build_feed_passport(
    rows: list[Mapping[str, Any]],
    *,
    mark_timestamps_ms: list[int],
    source_hash: str,
    requested_symbol: str,
    metadata: Mapping[str, Any] | None,
    requested_start: Any | None,
    requested_end: Any | None,
    cadence_interval_hours: int | None,
    exploratory: bool,
) -> tuple[DataPassport, Any | None, Any | None, bool]:
    start, end, interval_hours, explicitly_exploratory = _feed_contract(
        metadata,
        requested_start=requested_start,
        requested_end=requested_end,
        cadence_interval_hours=cadence_interval_hours,
        exploratory=exploratory,
    )
    try:
        passport = build_data_passport(
            rows,
            symbol=requested_symbol,
            start_ms=start,
            end_ms=end,
            cadence_interval_ms=interval_hours * 3_600_000,
            mark_timestamps_ms=mark_timestamps_ms,
            source_sha256s={"public_input": source_hash},
            exploratory=explicitly_exploratory,
        )
    except (TypeError, ValueError) as exc:
        raise PublicExecutionInputError(str(exc)) from exc
    return passport, start, end, explicitly_exploratory


def _settlements_from_rows(
    rows: list[Mapping[str, Any]],
    *,
    source: str,
    source_hash: str,
    requested_symbol: str,
    mark_rows: list[tuple[int, float]],
    start: Any | None,
    end: Any | None,
) -> list[PublicFundingSettlement]:
    """Keep only unambiguous rows with a strict prior mark for paper use."""

    start_ms = None if start is None else _timestamp(start, name="requested start")
    end_ms = None if end is None else _timestamp(end, name="requested end")
    rates_by_timestamp: dict[int, float | None] = {}
    for index, row in enumerate(rows):
        try:
            row_symbol = _canonical_symbol(row.get("symbol"))
            timestamp = _timestamp(
                row.get("timestamp_ms", row.get("fundingRateTimestamp")),
                name=f"funding_history[{index}].timestamp",
            )
            rate = _number(
                row.get("funding_rate", row.get("fundingRate")),
                name=f"funding_history[{index}].funding_rate",
            )
        except (KeyError, PublicExecutionInputError):
            continue
        if row_symbol != requested_symbol:
            continue
        if start_ms is not None and end_ms is not None and not (
            start_ms <= timestamp <= end_ms
        ):
            continue
        previous = rates_by_timestamp.get(timestamp)
        if previous is None and timestamp in rates_by_timestamp:
            continue
        if previous is not None and not math.isclose(
            previous, rate, rel_tol=0.0, abs_tol=1e-15
        ):
            rates_by_timestamp[timestamp] = None
            continue
        rates_by_timestamp[timestamp] = rate

    settlements: list[PublicFundingSettlement] = []
    for timestamp, rate in sorted(rates_by_timestamp.items()):
        if rate is None:
            continue
        mark = _row_price(mark_rows, timestamp)
        if mark is None:
            continue
        settlements.append(
            PublicFundingSettlement(
                settlement_id=f"{requested_symbol}:{timestamp}",
                symbol=requested_symbol,
                funding_rate=rate,
                timestamp_ms=timestamp,
                mark_price=mark,
                source=source,
                source_hash=source_hash,
            )
        )
    return settlements


@dataclass(frozen=True)
class PublicFundingSettlement:
    """One funding settlement with a mark observed before its timestamp."""

    settlement_id: str
    symbol: str
    funding_rate: float
    timestamp_ms: int
    mark_price: float
    source: str
    source_hash: str

    def as_paper_settlement(self) -> dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "symbol": self.symbol,
            "rate": self.funding_rate,
            "timestamp": self.timestamp_ms,
            "mark_price": self.mark_price,
            "source": self.source,
            "source_hash": self.source_hash,
        }


@dataclass(frozen=True)
class PublicExecutionInputFeed:
    """Validated, immutable view of one saved public input file."""

    path: str
    source_hash: str
    source_schema: str
    symbol: str
    settlements: tuple[PublicFundingSettlement, ...]
    passport: DataPassport

    def settlements_up_to(self, timestamp_ms: int | None = None) -> tuple[PublicFundingSettlement, ...]:
        if timestamp_ms is None:
            return self.settlements
        cutoff = _timestamp(timestamp_ms, name="feed cutoff")
        return tuple(item for item in self.settlements if item.timestamp_ms <= cutoff)

    def as_status(self) -> dict[str, Any]:
        passport_status = self.passport.as_dict()
        return {
            "schema_version": SCHEMA_VERSION,
            "path": self.path,
            "source_hash": self.source_hash,
            "source_schema": self.source_schema,
            "symbol": self.symbol,
            "settlement_count": len(self.settlements),
            "settlement_timestamps_ms": [item.timestamp_ms for item in self.settlements],
            "read_only_only": True,
            "trading_operations_count": 0,
            "passport": passport_status,
            "data_passport": passport_status,
            "requested_start_utc": passport_status["start_utc"],
            "requested_end_utc": passport_status["end_utc"],
            "requested_window": passport_status["requested_window"],
            "cadence_rule": passport_status["cadence_rule"],
            "expected_rows": passport_status["expected_rows"],
            "actual_rows": passport_status["actual_rows"],
            "missing_rows": passport_status["missing_rows"],
            "missing_timestamps_ms": passport_status["missing_timestamps_ms"],
            "duplicate_rows": passport_status["duplicate_rows"],
            "foreign_rows": passport_status["foreign_rows"],
            "future_rows": passport_status["future_rows"],
            "mark_before_settlement_rows": passport_status[
                "mark_before_settlement_rows"
            ],
            "mark_before_settlement_coverage": passport_status[
                "mark_before_settlement_coverage"
            ],
            "source_sha256s": passport_status["source_sha256s"],
            "exploratory": self.passport.exploratory,
            "qualifying": self.passport.qualifying,
        }


def _load_json_or_jsonl(path: Path, raw: bytes) -> tuple[str, list[dict[str, Any]]]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PublicExecutionInputError(
                    f"line {line_number}: invalid JSON"
                ) from exc
            if not isinstance(row, dict):
                raise PublicExecutionInputError(f"line {line_number}: row must be an object")
            rows.append(row)
        if not rows:
            raise PublicExecutionInputError("public input file is empty")
        return "jsonl", rows
    except UnicodeDecodeError as exc:
        raise PublicExecutionInputError("public input file must be UTF-8") from exc
    if isinstance(value, dict):
        return "json", [value]
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return "json", list(value)
    raise PublicExecutionInputError("public input JSON root must be an object or object list")


def _from_execution_inputs(
    root: Mapping[str, Any],
    *,
    source: str,
    source_hash: str,
    requested_symbol: str,
    requested_start: Any | None,
    requested_end: Any | None,
    cadence_interval_hours: int | None,
    exploratory: bool,
) -> tuple[str, list[PublicFundingSettlement], DataPassport]:
    _assert_read_only(root)
    schema = str(root.get("schema_version") or "")
    if schema not in SUPPORTED_INPUT_SCHEMAS:
        raise PublicExecutionInputError(f"unsupported public input schema {schema!r}")
    if schema != "bitget-public-execution-inputs-v1":
        raise PublicExecutionInputError("snapshot rows must be handled as JSONL input")
    symbol = _canonical_symbol(root.get("symbol"))
    if symbol != requested_symbol:
        raise PublicExecutionInputError(
            f"public input symbol {symbol!r} does not match {requested_symbol!r}"
        )
    funding_rows = root.get("funding_history")
    if not isinstance(funding_rows, list):
        raise PublicExecutionInputError("funding_history must be a list")
    typed_funding_rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(funding_rows):
        if not isinstance(row, Mapping):
            raise PublicExecutionInputError(f"funding_history[{index}] must be an object")
        _timestamp(
            row.get("timestamp_ms", row.get("fundingRateTimestamp")),
            name=f"funding_history[{index}].timestamp",
        )
        _number(
            row.get("funding_rate", row.get("fundingRate")),
            name=f"funding_history[{index}].funding_rate",
        )
        typed_funding_rows.append(row)
    mark_rows = _candle_rows(root.get("mark_price_history"), name="mark_price_history")
    passport, start, end, _ = _build_feed_passport(
        typed_funding_rows,
        mark_timestamps_ms=[timestamp for timestamp, _ in mark_rows],
        source_hash=source_hash,
        requested_symbol=requested_symbol,
        metadata=root,
        requested_start=requested_start,
        requested_end=requested_end,
        cadence_interval_hours=cadence_interval_hours,
        exploratory=exploratory,
    )
    settlements = _settlements_from_rows(
        typed_funding_rows,
        source=source,
        source_hash=source_hash,
        requested_symbol=requested_symbol,
        mark_rows=mark_rows,
        start=start,
        end=end,
    )
    return schema, settlements, passport


def _from_snapshot_rows(
    rows: list[Mapping[str, Any]],
    *,
    source: str,
    source_hash: str,
    requested_symbol: str,
    requested_start: Any | None,
    requested_end: Any | None,
    cadence_interval_hours: int | None,
    exploratory: bool,
) -> tuple[str, list[PublicFundingSettlement], DataPassport]:
    funding_rows: list[dict[str, Any]] = []
    market_rows: list[tuple[int, float]] = []
    schemas: set[str] = set()
    for line_number, row in enumerate(rows, start=1):
        _assert_read_only(row, line=line_number)
        schema = str(row.get("schema_version") or "")
        if schema not in {"bitget-market-snapshot-v1", "bitget-market-snapshot-v3"}:
            raise PublicExecutionInputError(
                f"line {line_number}: unsupported snapshot schema {schema!r}"
            )
        schemas.add(schema)
        symbol = _canonical_symbol(row.get("symbol"))
        captured_at = _timestamp(row.get("captured_at_ms"), name=f"line {line_number}.captured_at_ms")
        funding = row.get("funding")
        ticker = row.get("ticker")
        if (
            symbol == requested_symbol
            and isinstance(ticker, Mapping)
            and ticker.get("mark") not in (None, "")
        ):
            market_rows.append(
                (
                    captured_at,
                    _number(ticker.get("mark"), name=f"line {line_number}.ticker.mark", positive=True),
                )
            )
        if not isinstance(funding, Mapping):
            continue
        raw_settlement_ts = funding.get("latest_settlement_time_ms")
        raw_rate = funding.get("latest_settlement_rate")
        if raw_settlement_ts in (None, "") or raw_rate in (None, ""):
            continue
        settlement_ts = _timestamp(
            raw_settlement_ts, name=f"line {line_number}.latest_settlement_time_ms"
        )
        # The recorder may observe the settlement after it happened.  A row
        # captured after the settlement cannot provide its historical mark.
        if captured_at <= settlement_ts:
            continue
        rate = _number(
            raw_rate, name=f"line {line_number}.latest_settlement_rate"
        )
        funding_rows.append(
            {
                "symbol": symbol,
                "timestamp_ms": settlement_ts,
                "funding_rate": rate,
            }
        )

    market_rows.sort(key=lambda item: item[0])
    metadata = rows[0] if rows else None
    passport, start, end, _ = _build_feed_passport(
        funding_rows,
        mark_timestamps_ms=[timestamp for timestamp, _ in market_rows],
        source_hash=source_hash,
        requested_symbol=requested_symbol,
        metadata=metadata,
        requested_start=requested_start,
        requested_end=requested_end,
        cadence_interval_hours=cadence_interval_hours,
        exploratory=exploratory,
    )
    settlements = _settlements_from_rows(
        funding_rows,
        source=source,
        source_hash=source_hash,
        requested_symbol=requested_symbol,
        mark_rows=market_rows,
        start=start,
        end=end,
    )
    return (
        "bitget-market-snapshot-v3"
        if "bitget-market-snapshot-v3" in schemas
        else "bitget-market-snapshot-v1",
        settlements,
        passport,
    )


def load_public_execution_feed(
    path: str | Path,
    *,
    symbol: str,
    start_utc: Any | None = None,
    end_utc: Any | None = None,
    cadence_interval_hours: int | None = None,
    exploratory: bool = False,
    requested_start_utc: Any | None = None,
    requested_end_utc: Any | None = None,
    cadence_hours: int | None = None,
) -> PublicExecutionInputFeed:
    """Load and validate one saved public execution input artifact."""

    if start_utc is not None and requested_start_utc is not None:
        raise ValueError("feed start_utc and requested_start_utc are mutually exclusive")
    if end_utc is not None and requested_end_utc is not None:
        raise ValueError("feed end_utc and requested_end_utc are mutually exclusive")
    if cadence_interval_hours is not None and cadence_hours is not None:
        raise ValueError(
            "feed cadence_interval_hours and cadence_hours are mutually exclusive"
        )
    start_utc = start_utc if start_utc is not None else requested_start_utc
    end_utc = end_utc if end_utc is not None else requested_end_utc
    cadence_interval_hours = (
        cadence_interval_hours
        if cadence_interval_hours is not None
        else cadence_hours
    )
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    raw = target.read_bytes()
    source_hash = _sha256_bytes(raw)
    source = f"bitget_public_execution_file:{target.name}"
    requested_symbol = _canonical_symbol(symbol)
    input_kind, rows = _load_json_or_jsonl(target, raw)
    if input_kind == "json" and len(rows) == 1:
        root = rows[0]
        schema, settlements, passport = _from_execution_inputs(
            root,
            source=source,
            source_hash=source_hash,
            requested_symbol=requested_symbol,
            requested_start=start_utc,
            requested_end=end_utc,
            cadence_interval_hours=cadence_interval_hours,
            exploratory=exploratory,
        )
    else:
        schema, settlements, passport = _from_snapshot_rows(
            rows,
            source=source,
            source_hash=source_hash,
            requested_symbol=requested_symbol,
            requested_start=start_utc,
            requested_end=end_utc,
            cadence_interval_hours=cadence_interval_hours,
            exploratory=exploratory,
        )
    return PublicExecutionInputFeed(
        path=str(target),
        source_hash=source_hash,
        source_schema=schema,
        symbol=requested_symbol,
        settlements=tuple(settlements),
        passport=passport,
    )


__all__ = [
    "PublicExecutionInputError",
    "PublicExecutionInputFeed",
    "PublicFundingSettlement",
    "SCHEMA_VERSION",
    "load_public_execution_feed",
]
