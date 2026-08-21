"""Promote a validated external funding archive into a new execution input.

The command is the only supported bridge from an external archive to the
canonical public execution-input file.  It validates the raw archive first,
preserves the existing market/mark/index data, records every relevant hash,
and never overwrites an existing file.  An incomplete archive produces only a
blocked validation report and cannot create promoted execution inputs.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.bitget_funding_archive import (
    NORMALIZED_SCHEMA_VERSION,
    build_validation,
)


PROMOTION_SCHEMA_VERSION = "bitget-funding-promotion-v1"
EXECUTION_INPUT_SCHEMA_VERSION = "bitget-public-execution-inputs-v1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write_new_json(path: Path, value: Mapping[str, Any]) -> str:
    path = path.expanduser().resolve()
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    path.write_bytes(payload)
    return _sha256_bytes(payload)


def _assert_execution_input_identity(
    execution_inputs: Mapping[str, Any], *, symbol: str
) -> None:
    requested_symbol = str(symbol).strip().upper()
    if execution_inputs.get("schema_version") != EXECUTION_INPUT_SCHEMA_VERSION:
        raise ValueError("execution input has an unsupported schema")
    if str(execution_inputs.get("exchange") or "").strip().lower() != "bitget":
        raise ValueError("execution input exchange must be bitget")
    if str(execution_inputs.get("symbol") or "").strip().upper() != requested_symbol:
        raise ValueError("execution input symbol does not match the archive symbol")
    if execution_inputs.get("read_only_only") is not True:
        raise ValueError("execution input must be marked read_only_only")
    if execution_inputs.get("trading_operations_count") != 0:
        raise ValueError("execution input reports trading operations")


def _normalized_payload(
    *,
    report: Mapping[str, Any],
    normalized: list[dict[str, Any]],
) -> dict[str, Any]:
    source = report["source"]
    payload = {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "exchange": report["exchange"],
        "symbol": report["symbol"],
        "requested_start_utc": report["requested_start_utc"],
        "requested_end_utc": report["requested_end_utc"],
        "metadata": source["metadata"],
        "source_sha256": source["sha256"],
        "funding_history": normalized,
    }
    calculated_hash = _sha256_bytes(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    expected_hash = source.get("normalized_payload_sha256")
    if calculated_hash != expected_hash:
        raise ValueError("normalized archive hash does not match validation report")
    payload["normalized_payload_sha256"] = calculated_hash
    return payload


def promote_funding_archive(
    *,
    archive: str | Path,
    execution_inputs: str | Path,
    symbol: str,
    start: str,
    end: str,
    output: str | Path,
    validation_output: str | Path,
    normalized_output: str | Path | None = None,
    exchange: str = "bitget",
    interval_hours: int = 8,
    source_kind: str = "external_archive",
    source_url: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    archive_path = Path(archive).expanduser().resolve()
    execution_path = Path(execution_inputs).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    validation_path = Path(validation_output).expanduser().resolve()
    normalized_path = (
        Path(normalized_output).expanduser().resolve()
        if normalized_output is not None
        else None
    )
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    if not execution_path.is_file():
        raise FileNotFoundError(execution_path)

    execution = _read_object(execution_path)
    requested_symbol = str(symbol).strip().upper()
    if not requested_symbol:
        raise ValueError("symbol must not be empty")
    _assert_execution_input_identity(execution, symbol=requested_symbol)

    report, normalized = build_validation(
        archive=archive_path,
        symbol=requested_symbol,
        exchange=exchange,
        start=start,
        end=end,
        interval_hours=interval_hours,
        source_kind=source_kind,
        source_url=source_url,
        metadata=metadata,
    )
    validation_hash = _write_new_json(validation_path, report)
    result: dict[str, Any] = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "status": "blocked" if report["status"] != "passed" else "passed",
        "validation_report_path": str(validation_path),
        "validation_report_sha256": validation_hash,
        "archive_path": str(archive_path),
        "archive_sha256": report["source"]["sha256"],
        "execution_inputs_path": str(execution_path),
        "output_path": None,
        "normalized_output_path": None,
        "trading_operations_count": 0,
        "read_only_external_operations": True,
    }
    if report["status"] != "passed":
        return result

    normalized_payload = _normalized_payload(report=report, normalized=normalized)
    normalized_hash = None
    if normalized_path is not None:
        normalized_hash = _write_new_json(normalized_path, normalized_payload)

    promoted = deepcopy(execution)
    promoted["funding_history"] = normalized
    quality = promoted.get("quality")
    quality = dict(quality) if isinstance(quality, Mapping) else {}
    quality["requested_start_utc"] = report["requested_start_utc"]
    quality["requested_end_utc"] = report["requested_end_utc"]
    quality["funding"] = dict(report["quality"])
    pagination = quality.get("pagination")
    pagination = dict(pagination) if isinstance(pagination, Mapping) else {}
    pagination["funding_history"] = {
        "source_kind": "validated_external_archive",
        "external_archive": True,
        "source_row_count": len(normalized),
        "page_count": None,
        "page_row_counts": [],
        "last_page_empty": None,
    }
    quality["pagination"] = pagination
    promoted["quality"] = quality
    promoted["status"] = "promoted_validated_external_funding"
    promoted["funding_archive_provenance"] = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "source_kind": source_kind,
        "source_url": source_url,
        "raw_archive_path": str(archive_path),
        "raw_archive_sha256": report["source"]["sha256"],
        "normalized_payload_sha256": report["source"]["normalized_payload_sha256"],
        "normalized_output_path": str(normalized_path) if normalized_path else None,
        "normalized_output_sha256": normalized_hash,
        "validation_report_path": str(validation_path),
        "validation_report_sha256": validation_hash,
        "validated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "metadata": report["source"]["metadata"],
        "eligible_for_historical_selection": True,
    }
    output_hash = _write_new_json(output_path, promoted)
    result.update(
        {
            "output_path": str(output_path),
            "output_sha256": output_hash,
            "normalized_output_path": str(normalized_path) if normalized_path else None,
            "normalized_output_sha256": normalized_hash,
            "promoted_funding_rows": len(normalized),
        }
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--execution-inputs", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", default="bitget")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--interval-hours", type=int, default=8)
    parser.add_argument("--source-kind", default="external_archive")
    parser.add_argument("--source-url")
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--validation-output", type=Path, required=True)
    parser.add_argument("--normalized-output", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    metadata = None
    if args.metadata:
        metadata = _read_object(args.metadata.expanduser().resolve())
    result = promote_funding_archive(
        archive=args.archive,
        execution_inputs=args.execution_inputs,
        symbol=args.symbol,
        exchange=args.exchange,
        start=args.start,
        end=args.end,
        interval_hours=args.interval_hours,
        source_kind=args.source_kind,
        source_url=args.source_url,
        metadata=metadata,
        validation_output=args.validation_output,
        normalized_output=args.normalized_output,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXECUTION_INPUT_SCHEMA_VERSION",
    "PROMOTION_SCHEMA_VERSION",
    "promote_funding_archive",
]
