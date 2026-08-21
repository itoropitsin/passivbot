"""Build a truthful data-quality report from public Bitget input artifacts.

This command does not download data and does not repair or synthesize rows.  It
only derives the metrics used by the historical-selection preflight from the
already saved execution-input and OHLCV-repair reports.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from research.funding_quality import parse_timestamp_ms, validate_funding_rows


SCHEMA_VERSION = "bitget-public-data-metrics-v1"
PLAN_BOUNDARY_UTC = "2026-08-17T00:00:00+00:00"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp_ms(value: Any) -> int | None:
    return parse_timestamp_ms(value)


def _iso(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _timeframe_ms(value: Any) -> int | None:
    raw = str(value).strip().lower()
    try:
        if raw.endswith("m"):
            return int(raw[:-1]) * 60_000
        if raw.endswith("h"):
            return int(raw[:-1]) * 60 * 60_000
        if raw.endswith("d"):
            return int(raw[:-1]) * 24 * 60 * 60_000
    except ValueError:
        return None
    return None


def _repair_coverage(repair: Mapping[str, Any]) -> tuple[int, int, int, float]:
    before_missing = max(0, _number(repair.get("before_missing_rows")))
    repaired_rows = max(0, _number(repair.get("repaired_rows")))
    after_missing = max(0, _number(repair.get("after_missing_rows")))
    repair_range = repair.get("range")
    repair_range = repair_range if isinstance(repair_range, Mapping) else {}
    start = _timestamp_ms(repair_range.get("start_utc"))
    end = _timestamp_ms(repair_range.get("end_utc"))
    interval = _timeframe_ms(repair.get("timeframe"))
    if start is not None and end is not None and interval and end >= start:
        expected = ((end - start) // interval) + 1
    else:
        expected = before_missing + repaired_rows
    if expected == 0:
        return expected, 0, after_missing, 1.0
    actual = max(0, expected - after_missing)
    return expected, actual, after_missing, actual / expected


def _funding_coverage(
    quality: Mapping[str, Any], funding_override: Mapping[str, Any] | None = None
) -> tuple[int, int, float | None]:
    funding = quality.get("funding")
    if funding_override is not None:
        funding = funding_override
    if not isinstance(funding, Mapping):
        return 0, 0, None
    start = _timestamp_ms(quality.get("requested_start_utc"))
    end = _timestamp_ms(quality.get("requested_end_utc"))
    interval_hours = _number(funding.get("expected_interval_hours"), 8)
    rows = max(0, _number(funding.get("actual_rows", funding.get("rows"))))
    if start is None or end is None or interval_hours <= 0 or end < start:
        return 0, rows, None
    interval_ms = interval_hours * 60 * 60 * 1000
    expected = ((end - start) // interval_ms) + 1
    return expected, rows, rows / expected if expected else None


def _validated_funding_quality(
    execution_inputs: Mapping[str, Any], quality: Mapping[str, Any]
) -> dict[str, Any]:
    rows = execution_inputs.get("funding_history")
    start = _timestamp_ms(quality.get("requested_start_utc"))
    end = _timestamp_ms(quality.get("requested_end_utc"))
    funding = quality.get("funding")
    funding = funding if isinstance(funding, Mapping) else {}
    try:
        interval_hours = int(funding.get("expected_interval_hours", 8))
    except (TypeError, ValueError):
        interval_hours = 8
    derived: dict[str, Any]
    if start is not None and end is not None and interval_hours > 0:
        derived = validate_funding_rows(
            rows if isinstance(rows, list) else [],
            start_ms=start,
            end_ms=end,
            interval_hours=interval_hours,
            expected_symbol=execution_inputs.get("symbol"),
        )
    else:
        derived = {
            "expected_interval_hours": interval_hours,
            "expected_rows": 0,
            "rows": 0,
            "actual_rows": 0,
            "first_timestamp_ms": None,
            "last_timestamp_ms": None,
            "future_rows": 0,
            "outside_requested_range": 0,
            "invalid_rows": 0,
            "duplicate_rows": 0,
            "missing_rows": 0,
            "unexpected_in_range_rows": 0,
            "interval_gaps": 0,
            "complete_between_observed_bounds": False,
            "complete_requested_window": False,
        }
    derived["funding_history_present"] = isinstance(rows, list)
    derived["funding_cadence_is_eight_hours"] = interval_hours == 8
    return derived


def build_metrics(
    *,
    asset: str,
    execution_inputs: Mapping[str, Any],
    ohlcv_repair: Mapping[str, Any],
    execution_inputs_path: str,
    ohlcv_repair_path: str,
    execution_model_test_count: int = 0,
    identical_run_count: int = 1,
    ohlcv_quality: Mapping[str, Any] | None = None,
    ohlcv_quality_path: str | None = None,
) -> dict[str, Any]:
    quality = execution_inputs.get("quality")
    quality = quality if isinstance(quality, Mapping) else {}
    mark = quality.get("mark")
    mark = mark if isinstance(mark, Mapping) else {}
    index = quality.get("index")
    index = index if isinstance(index, Mapping) else {}
    funding = _validated_funding_quality(execution_inputs, quality)
    pagination = quality.get("pagination")
    pagination = pagination if isinstance(pagination, Mapping) else {}
    funding_pagination = pagination.get("funding_history")
    funding_pagination = (
        funding_pagination if isinstance(funding_pagination, Mapping) else {}
    )
    expected_funding, actual_funding, funding_ratio = _funding_coverage(
        quality, funding
    )
    expected_market, actual_market, missing_market, market_ratio = _repair_coverage(
        ohlcv_repair
    )
    request_end = _timestamp_ms(quality.get("requested_end_utc"))
    plan_boundary = _timestamp_ms(PLAN_BOUNDARY_UTC)
    mark_future = _number(mark.get("future_rows"))
    index_future = _number(index.get("future_rows"))
    funding_future = _number(funding.get("future_rows"))
    mark_invalid = _number(mark.get("invalid_rows")) + _number(mark.get("nan_values"))
    index_invalid = _number(index.get("invalid_rows")) + _number(index.get("nan_values"))
    snapshots = execution_inputs.get("snapshots")
    snapshots = snapshots if isinstance(snapshots, Mapping) else {}
    repair_status = ohlcv_repair.get("status")
    synthetic_rows = _number(ohlcv_repair.get("synthetic_rows"))
    ohlcv_quality = ohlcv_quality if isinstance(ohlcv_quality, Mapping) else {}
    ohlcv_value_quality = ohlcv_quality.get("value_quality")
    ohlcv_value_quality = ohlcv_value_quality if isinstance(ohlcv_value_quality, Mapping) else {}
    ohlcv_quality_invalid_rows = sum(
        _number(ohlcv_value_quality.get(name))
        for name in (
            "nonfinite_rows",
            "nonpositive_price_rows",
            "price_order_rows",
            "negative_volume_rows",
        )
    )
    position_tier_snapshot = snapshots.get("position_tiers")
    position_tiers_captured = (
        isinstance(position_tier_snapshot, Mapping)
        and isinstance(position_tier_snapshot.get("data"), list)
        and bool(position_tier_snapshot.get("data"))
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "asset": str(asset).upper(),
        "exchange": execution_inputs.get("exchange", "bitget"),
        "symbol": execution_inputs.get("symbol"),
        "history_through_plan_date": (
            request_end is not None
            and plan_boundary is not None
            and request_end >= plan_boundary
            and request_end <= plan_boundary
        ),
        "history_last_available_utc": quality.get("requested_end_utc"),
        "history_coverage_ratio": market_ratio,
        "history_expected_rows": expected_market,
        "history_actual_rows": actual_market,
        "history_missing_rows": missing_market,
        "unmarked_synthetic_rows": synthetic_rows,
        "nan_rows": mark_invalid + index_invalid,
        "future_data_violations": mark_future + index_future + funding_future,
        "funding_history_complete": (
            funding.get("funding_history_present") is True
            and funding.get("funding_cadence_is_eight_hours") is True
            and funding.get("missing_symbol_rows", 0) == 0
            and funding.get("foreign_symbol_rows", 0) == 0
            and funding_ratio is not None
            and funding_ratio >= 1.0
            and (
                funding.get("complete_requested_window") is True
                or (
                    "complete_requested_window" not in funding
                    and funding.get("complete_between_observed_bounds") is True
                )
            )
            and funding_future == 0
        ),
        "funding_known_coverage_ratio": funding_ratio,
        "funding_expected_rows": expected_funding,
        "funding_actual_rows": actual_funding,
        "funding_missing_rows": _number(funding.get("missing_rows")),
        "funding_duplicate_rows": _number(funding.get("duplicate_rows")),
        "funding_invalid_rows": _number(funding.get("invalid_rows")),
        "funding_complete_requested_window": funding.get("complete_requested_window"),
        "funding_expected_symbol": funding.get("expected_symbol"),
        "funding_missing_symbol_rows": _number(funding.get("missing_symbol_rows")),
        "funding_foreign_symbol_rows": _number(funding.get("foreign_symbol_rows")),
        "funding_pagination": dict(funding_pagination),
        "mark_price_history_complete": (
            float(mark.get("coverage_ratio", 0.0) or 0.0) >= 0.9995
            and mark_invalid == 0
            and mark_future == 0
        ),
        "position_tiers_captured": position_tiers_captured,
        "execution_model_implemented": execution_model_test_count > 0,
        "execution_model_test_count": execution_model_test_count,
        "identical_run_count": identical_run_count,
        "repair_status": repair_status,
        "ohlcv_cache_quality_complete": ohlcv_quality.get("status") == "passed",
        "ohlcv_cache_missing_rows": _number(ohlcv_quality.get("missing_rows")),
        "ohlcv_cache_invalid_rows": ohlcv_quality_invalid_rows,
        "ohlcv_cache_future_rows": _number(ohlcv_quality.get("future_rows")),
        "ohlcv_cache_checksums_complete": (
            isinstance(ohlcv_quality.get("checks"), Mapping)
            and ohlcv_quality["checks"].get("chunk_checksums_verified") is True
        ),
        "read_only_external_operations": (
            execution_inputs.get("trading_operations_count") == 0
            and ohlcv_repair.get("trading_operations_count") == 0
        ),
        "execution_inputs_sha256": _sha256_file(Path(execution_inputs_path)),
        "source_evidence": {
            "public_execution_inputs": execution_inputs_path,
            "public_execution_inputs_sha256": _sha256_file(Path(execution_inputs_path)),
            "ohlcv_repair_report": ohlcv_repair_path,
            "ohlcv_quality_report": ohlcv_quality_path,
            "execution_input_schema": execution_inputs.get("schema_version"),
            "execution_input_exchange": execution_inputs.get("exchange"),
            "execution_input_symbol": execution_inputs.get("symbol"),
            "funding_archive_provenance": execution_inputs.get(
                "funding_archive_provenance"
            ),
            "ohlcv_repair_schema": ohlcv_repair.get("schema_version"),
            "ohlcv_quality_schema": ohlcv_quality.get("schema_version"),
            "funding_note": (
                f"{actual_funding} of {expected_funding} expected funding rows are "
                "present; missing rows are not synthesized."
            ),
            "funding_pagination_note": (
                "The saved funding pages are "
                f"{funding_pagination.get('page_row_counts', [])}; "
                "an empty final page is retained as horizon evidence."
            ),
            "market_note": (
                f"{actual_market} of {expected_market} OHLCV rows are present; "
                f"repair status is {repair_status!r}."
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--execution-inputs", type=Path, required=True)
    parser.add_argument("--ohlcv-repair", type=Path, required=True)
    parser.add_argument("--execution-model-test-count", type=int, default=0)
    parser.add_argument("--identical-run-count", type=int, default=1)
    parser.add_argument("--ohlcv-quality", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    execution_path = args.execution_inputs.expanduser().resolve()
    repair_path = args.ohlcv_repair.expanduser().resolve()
    quality_path = args.ohlcv_quality.expanduser().resolve() if args.ohlcv_quality else None
    output = args.output.expanduser().resolve()
    result = build_metrics(
        asset=args.asset,
        execution_inputs=_read_json(execution_path),
        ohlcv_repair=_read_json(repair_path),
        execution_inputs_path=str(execution_path),
        ohlcv_repair_path=str(repair_path),
        execution_model_test_count=max(0, args.execution_model_test_count),
        identical_run_count=max(0, args.identical_run_count),
        ohlcv_quality=_read_json(quality_path) if quality_path else None,
        ohlcv_quality_path=str(quality_path) if quality_path else None,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "asset": args.asset.upper()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
