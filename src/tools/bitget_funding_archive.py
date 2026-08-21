"""Validate an externally supplied Bitget funding-rate archive.

The Bitget public endpoint currently exposes only a recent horizon.  This
small importer lets a researcher validate an independently obtained archive
without silently filling gaps or changing the execution-input artifact.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from research.funding_quality import (
    is_synthetic_funding_row,
    parse_timestamp_ms,
    validate_funding_rows,
)


SCHEMA_VERSION = "bitget-funding-archive-validation-v2"
NORMALIZED_SCHEMA_VERSION = "bitget-funding-archive-v2"
REQUIRED_SETTLEMENT_SEMANTICS = "applied_settlement"
EXPECTED_SETTLEMENT_INTERVAL_HOURS = 8


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _timestamp_ms(value: Any) -> int | None:
    return parse_timestamp_ms(value)


def _parse_rate(value: Any) -> float | None:
    try:
        rate = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return rate if math.isfinite(rate) else None


def _extract_rows(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, Mapping):
        return []
    for key in ("funding_history", "rows", "resultList"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            return candidate
    data = value.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, Mapping):
        return _extract_rows(data)
    return []


def _extract_archive_metadata(value: Any) -> dict[str, Any]:
    """Read only explicit envelope metadata; never infer it from data rows."""

    if not isinstance(value, Mapping):
        return {}
    metadata: dict[str, Any] = {}
    for key in ("metadata", "provenance", "evidence", "archive_metadata"):
        candidate = value.get(key)
        if isinstance(candidate, Mapping):
            metadata.update(candidate)
    for key in (
        "rate_semantics",
        "funding_rate_semantics",
        "source_provider",
        "provider",
        "source_reference",
        "source_url",
        "source_exchange",
        "exchange",
        "source_symbol",
        "symbol",
        "window_start_utc",
        "window_end_utc",
        "requested_start_utc",
        "requested_end_utc",
        "start_utc",
        "end_utc",
        "cadence_hours",
        "interval_hours",
        "funding_interval_hours",
        "single_symbol_declaration",
    ):
        if key in value:
            metadata.setdefault(key, value[key])
    return metadata


def _read_rows(path: Path) -> tuple[list[Any], str, dict[str, Any]]:
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        text = raw.decode("utf-8-sig")
        return list(csv.DictReader(text.splitlines(), delimiter=delimiter)), "csv", {}

    text = raw.decode("utf-8")
    try:
        document = json.loads(text)
        return _extract_rows(document), "json", _extract_archive_metadata(document)
    except json.JSONDecodeError:
        rows: list[Any] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}: {exc}") from exc
        return rows, "jsonl", {}


def _normalise_symbol(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().upper()


def _row_symbol_state(row: Any) -> tuple[str, str | None]:
    """Return ``explicit``, ``missing``, ``ambiguous`` or ``foreign`` later."""

    if not isinstance(row, Mapping) or "symbol" not in row:
        return "missing", None
    raw_symbol = row.get("symbol")
    symbol = _normalise_symbol(raw_symbol)
    if symbol is None:
        return "ambiguous", None
    return "explicit", symbol


def _single_symbol_declaration(
    metadata: Mapping[str, Any], *, requested_symbol: str
) -> tuple[bool, str | None]:
    declaration = metadata.get("single_symbol_declaration")
    if not isinstance(declaration, Mapping):
        return False, "single_symbol_declaration is missing"
    declared_symbol = _normalise_symbol(declaration.get("symbol"))
    if declared_symbol != requested_symbol:
        return False, "single_symbol_declaration.symbol does not match requested symbol"
    if declaration.get("scope") != "all_rows" or declaration.get("all_rows") is not True:
        return False, "single_symbol_declaration must set scope=all_rows and all_rows=true"
    return True, None


def _explicit_utc_timestamp(value: Any) -> int | None:
    """Parse metadata timestamps only when their UTC zone is explicit."""

    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        return _timestamp_ms(raw)
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        return None
    return _timestamp_ms(raw)


def _metadata_value(metadata: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in metadata:
            return metadata.get(key)
    return None


def _metadata_checks(
    metadata: Mapping[str, Any],
    *,
    exchange: str,
    requested_symbol: str,
    start_ms: int,
    end_ms: int,
    interval_hours: int,
) -> dict[str, bool]:
    semantics = str(
        _metadata_value(metadata, "rate_semantics", "funding_rate_semantics") or ""
    ).strip().lower()
    provider = _metadata_value(metadata, "source_provider", "provider")
    source_reference = _metadata_value(
        metadata, "source_reference", "source_url", "citation"
    )
    source_exchange = _metadata_value(metadata, "source_exchange", "exchange")
    source_symbol = _metadata_value(metadata, "source_symbol", "symbol")
    declared_start = _explicit_utc_timestamp(
        _metadata_value(
            metadata,
            "window_start_utc",
            "requested_start_utc",
            "start_utc",
        )
    )
    declared_end = _explicit_utc_timestamp(
        _metadata_value(
            metadata,
            "window_end_utc",
            "requested_end_utc",
            "end_utc",
        )
    )
    cadence = _metadata_value(
        metadata, "cadence_hours", "interval_hours", "funding_interval_hours"
    )
    try:
        cadence_hours = int(cadence)
    except (TypeError, ValueError, OverflowError):
        cadence_hours = None
    return {
        "rate_semantics_applied_settlement": semantics == REQUIRED_SETTLEMENT_SEMANTICS,
        "source_provider_declared": isinstance(provider, str) and bool(provider.strip()),
        "source_reference_declared": isinstance(source_reference, str)
        and bool(source_reference.strip()),
        "source_exchange_matches_requested": str(source_exchange or "").strip().lower()
        == str(exchange).strip().lower(),
        "source_symbol_matches_requested": _normalise_symbol(source_symbol)
        == requested_symbol,
        "window_start_explicit_utc": declared_start == start_ms,
        "window_end_explicit_utc": declared_end == end_ms,
        "cadence_is_eight_hours": cadence_hours == EXPECTED_SETTLEMENT_INTERVAL_HOURS
        and interval_hours == EXPECTED_SETTLEMENT_INTERVAL_HOURS,
    }


def _normalise_row(
    row: Any, *, default_symbol: str, allow_implicit_symbol: bool
) -> dict[str, Any] | None:
    if is_synthetic_funding_row(row):
        return None
    if isinstance(row, Mapping):
        timestamp_value = next(
            (row.get(key) for key in ("timestamp_ms", "fundingRateTimestamp", "fundingTime", "timestamp", "ts") if key in row),
            None,
        )
        rate_value = next(
            (row.get(key) for key in ("funding_rate", "fundingRate", "rate", "value") if key in row),
            None,
        )
        if "symbol" not in row or row.get("symbol") in (None, ""):
            if not allow_implicit_symbol:
                return None
            symbol = default_symbol
        else:
            symbol = row.get("symbol")
    elif isinstance(row, (list, tuple)) and len(row) >= 2:
        if not allow_implicit_symbol:
            return None
        timestamp_value, rate_value = row[0], row[1]
        symbol = default_symbol
    else:
        return None
    normalized_symbol = _normalise_symbol(symbol)
    if normalized_symbol is None:
        return None
    timestamp = _timestamp_ms(timestamp_value)
    rate = _parse_rate(rate_value)
    if timestamp is None or rate is None:
        return None
    return {
        "symbol": normalized_symbol,
        "funding_rate": rate,
        "timestamp_ms": timestamp,
    }


def build_validation(
    *,
    archive: str | Path,
    symbol: str,
    exchange: str = "bitget",
    start: str,
    end: str,
    interval_hours: int = 8,
    source_kind: str = "external_archive",
    source_url: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate rows and return the report plus valid normalized rows.

    The quality result is calculated from row contents, never from a claimed
    row count in the source file.
    """
    path = Path(archive).expanduser().resolve()
    rows, input_format, archive_metadata = _read_rows(path)
    start_ms = _timestamp_ms(start)
    end_ms = _timestamp_ms(end)
    if start_ms is None or end_ms is None:
        raise ValueError("start and end must be ISO timestamps or Unix milliseconds")
    if end_ms < start_ms:
        raise ValueError("end must not be before start")

    requested_symbol = str(symbol).upper().strip()
    if not requested_symbol:
        raise ValueError("symbol must not be empty")
    effective_metadata = dict(archive_metadata)
    if metadata is not None:
        effective_metadata.update(dict(metadata))
    if source_url is not None:
        effective_metadata.setdefault("source_url", source_url)
    single_symbol_declared, single_symbol_error = _single_symbol_declaration(
        effective_metadata, requested_symbol=requested_symbol
    )
    metadata_checks = _metadata_checks(
        effective_metadata,
        exchange=exchange,
        requested_symbol=requested_symbol,
        start_ms=start_ms,
        end_ms=end_ms,
        interval_hours=interval_hours,
    )
    rows_for_validation: list[Any] = []
    normalized: list[dict[str, Any]] = []
    foreign_symbol_rows = 0
    missing_symbol_rows = 0
    ambiguous_symbol_rows = 0
    for row in rows:
        symbol_state, row_symbol = _row_symbol_state(row)
        if symbol_state == "explicit" and row_symbol != requested_symbol:
            foreign_symbol_rows += 1
            continue
        if symbol_state == "ambiguous":
            ambiguous_symbol_rows += 1
            continue
        if symbol_state == "missing" and not single_symbol_declared:
            missing_symbol_rows += 1
            continue
        rows_for_validation.append(row)
        normalized_row = _normalise_row(
            row,
            default_symbol=requested_symbol,
            allow_implicit_symbol=single_symbol_declared,
        )
        if normalized_row is not None:
            normalized.append(normalized_row)
    quality = validate_funding_rows(
        rows_for_validation,
        start_ms=start_ms,
        end_ms=end_ms,
        interval_hours=interval_hours,
    )
    quality["requested_symbol"] = requested_symbol
    quality["foreign_symbol_rows"] = foreign_symbol_rows
    quality["missing_symbol_rows"] = missing_symbol_rows
    quality["ambiguous_symbol_rows"] = ambiguous_symbol_rows
    source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    complete_and_symbol_safe = (
        quality["complete_requested_window"]
        and foreign_symbol_rows == 0
        and missing_symbol_rows == 0
        and ambiguous_symbol_rows == 0
    )
    normalized_payload = {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "exchange": str(exchange).lower(),
        "symbol": requested_symbol,
        "requested_start_utc": datetime.fromtimestamp(
            start_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "requested_end_utc": datetime.fromtimestamp(
            end_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "metadata": effective_metadata,
        "source_sha256": source_sha256,
        "funding_history": normalized,
    }
    normalized_sha256 = _canonical_sha256(normalized_payload)
    metadata_complete = all(metadata_checks.values())
    notes = [
        "No rows were synthesized, interpolated, or copied from another exchange.",
        "A complete observed subrange is not sufficient; the requested window must be complete.",
        "Every external archive row must identify the requested symbol unless an explicit all_rows single-symbol declaration is present.",
        "A full archive must declare applied settlement semantics, source provenance, the exact UTC window, and an 8-hour cadence.",
        "Rows marked synthetic are invalid evidence and cannot open the historical gate.",
    ]
    if single_symbol_error:
        notes.append(single_symbol_error)
    if not metadata_complete:
        notes.append("Archive provenance metadata is incomplete; historical eligibility is blocked.")
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed"
        if complete_and_symbol_safe and metadata_complete
        else "blocked",
        "eligible_for_historical_selection": bool(
            str(exchange).lower() == "bitget"
            and requested_symbol
            and complete_and_symbol_safe
            and metadata_complete
        ),
        "exchange": str(exchange).lower(),
        "symbol": requested_symbol,
        "requested_start_utc": datetime.fromtimestamp(
            start_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "requested_end_utc": datetime.fromtimestamp(
            end_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "source": {
            "path": str(path),
            "kind": str(source_kind),
            "url": source_url,
            "input_format": input_format,
            "sha256": source_sha256,
            "raw_sha256": source_sha256,
            "normalized_payload_sha256": normalized_sha256,
            "raw_rows": len(rows),
            "metadata": effective_metadata,
        },
        "quality": quality,
        "archive_contract": {
            "single_symbol_declaration_valid": single_symbol_declared,
            "metadata_checks": metadata_checks,
            "metadata_complete": metadata_complete,
            "normalized_payload_sha256": normalized_sha256,
        },
        "notes": notes,
    }
    return report, normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", default="bitget")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--interval-hours", type=int, default=8)
    parser.add_argument("--source-kind", default="external_archive")
    parser.add_argument("--source-url")
    parser.add_argument(
        "--metadata",
        type=Path,
        help="JSON object with explicit archive provenance and settlement metadata",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--normalized-output",
        type=Path,
        help="optional canonical JSON copy of valid rows; no rows are added",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    metadata = None
    if args.metadata:
        metadata_value = json.loads(
            args.metadata.expanduser().resolve().read_text(encoding="utf-8")
        )
        if not isinstance(metadata_value, Mapping):
            raise ValueError("--metadata JSON root must be an object")
        metadata = dict(metadata_value)
    report, normalized = build_validation(
        archive=args.archive,
        symbol=args.symbol,
        exchange=args.exchange,
        start=args.start,
        end=args.end,
        interval_hours=args.interval_hours,
        source_kind=args.source_kind,
        source_url=args.source_url,
        metadata=metadata,
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.normalized_output:
        normalized_output = args.normalized_output.expanduser().resolve()
        normalized_output.parent.mkdir(parents=True, exist_ok=True)
        normalized_output.write_text(
            json.dumps(
                {
                    "schema_version": NORMALIZED_SCHEMA_VERSION,
                    "exchange": report["exchange"],
                    "symbol": report["symbol"],
                    "requested_start_utc": report["requested_start_utc"],
                    "requested_end_utc": report["requested_end_utc"],
                    "source_sha256": report["source"]["sha256"],
                    "normalized_payload_sha256": report["source"][
                        "normalized_payload_sha256"
                    ],
                    "metadata": report["source"]["metadata"],
                    "funding_history": normalized,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {"output": str(output), "status": report["status"]},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
