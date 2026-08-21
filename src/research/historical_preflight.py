"""Preflight the fixed BTC/ETH historical-selection protocol.

This command is deliberately a gate, not an optimizer.  It inspects the
recorded profiles, execution inputs, data-quality metrics, and roadmap status
before any expensive candidate search is allowed to start.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.data_passport import build_data_passport
from research.funding_quality import parse_timestamp_ms, validate_funding_rows


SCHEMA_VERSION = "crypto-historical-preflight-v1"
PLAN_DATE = "2026-08-17"
CANONICAL_OHLCV_CACHE_DIR = "caches/ohlcvs"
EXPECTED_OHLCV_EXCHANGE = "bitget"
EXPECTED_OHLCV_TIMEFRAME = "1m"
EXPECTED_OHLCV_START_UTC = "2024-07-01T00:00:00+00:00"
EXPECTED_OHLCV_END_UTC = f"{PLAN_DATE}T00:00:00+00:00"
EXPECTED_FUNDING_START_UTC = EXPECTED_OHLCV_START_UTC
EXPECTED_FUNDING_END_UTC = EXPECTED_OHLCV_END_UTC
EXPECTED_FUNDING_INTERVAL_HOURS = 8
PUBLIC_MARKET_PROFILE_TEMPLATE = "bitget_{prefix}_market_profile_public_only_2026-08-17.json"
PUBLIC_MARKET_PROFILE_FILENAMES = {
    "BTC": "bitget_btc_market_profile_public_only_2026-08-17.json",
    "ETH": "bitget_eth_market_profile_public_only_2026-08-17.json",
}
EXECUTION_INPUT_FILENAMES = {
    "BTC": (
        "bitget_btc_public_execution_inputs_2024-07-01_to_2026-08-17-"
        "repaired-pagination-v2.json"
    ),
    "ETH": "bitget_eth_public_execution_inputs_2024-07-01_to_2026-08-17.json",
}
DATA_METRICS_FILENAMES = {
    "BTC": "data_metrics_public_snapshot_2026-08-17.json",
    "ETH": "eth_data_metrics_public_snapshot_2026-08-17.json",
}
OHLCV_REPAIR_FILENAMES = {
    "BTC": "bitget_btc_ohlcv_repair_2024-07-01_to_2026-08-17-v4.json",
    "ETH": "bitget_eth_ohlcv_repair_2024-07-01_to_2026-08-17-v2.json",
}
OHLCV_QUALITY_FILENAMES = {
    "BTC": "bitget_btc_ohlcv_quality_2024-07-01_to_2026-08-17-v1.json",
    "ETH": "bitget_eth_ohlcv_quality_2024-07-01_to_2026-08-17-v1.json",
}
OHLCV_SOURCE_EVIDENCE_FIELDS = {
    "public_execution_inputs": "execution_inputs",
    "ohlcv_repair_report": "ohlcv_repair",
    "ohlcv_quality_report": "ohlcv_quality",
}
LOCAL_ONLY_REMOTE_FETCH_POLICY = "local_only"
BASE_MAKER_FEE_RATE = 0.0002
STRESS_MAKER_FEE_RATE = 0.0004
STRESS_MAKER_FEE_LABEL = "stress_maker_fee_0_0004"
WALK_FORWARD_WINDOWS = (
    {
        "train_start": "2024-07-01",
        "train_end": "2025-06-30",
        "validation_start": "2025-07-01",
        "validation_end": "2025-09-30",
    },
    {
        "train_start": "2024-10-01",
        "train_end": "2025-09-30",
        "validation_start": "2025-10-01",
        "validation_end": "2025-12-31",
    },
    {
        "train_start": "2025-01-01",
        "train_end": "2025-12-31",
        "validation_start": "2026-01-01",
        "validation_end": "2026-03-31",
    },
    {
        "train_start": "2025-04-01",
        "train_end": "2026-03-31",
        "validation_start": "2026-04-01",
        "validation_end": "2026-06-30",
    },
)
PROTOCOL = {
    "walk_forward_windows": WALK_FORWARD_WINDOWS,
    "holdout_start": "2026-07-01",
    "holdout_end": PLAN_DATE,
    "sides": ["long", "short", "combined"],
    "independent_optimizer_runs": 5,
    "max_changed_parameters_per_run": 8,
    "neighbor_offsets_pct": [-0.10, -0.05, 0.05, 0.10],
    "initial_capital_usdt": 1000.0,
    "reinvestment": False,
    "max_drawdown": 0.20,
    "terminal_drawdown": 0.25,
    "min_closed_cycles_per_window": 20,
    "min_stress_equity_ratio": 0.75,
    "min_neighbor_profitable_ratio": 0.80,
}


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"missing file: {path}"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"cannot read JSON {path}: {type(exc).__name__}"
    if not isinstance(value, dict):
        return None, f"JSON root is not an object: {path}"
    return value, None


def _timestamp_ms(value: Any) -> int | None:
    return parse_timestamp_ms(value)


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _funding_coverage(
    execution_inputs: Mapping[str, Any], funding_override: Mapping[str, Any] | None = None
) -> float | None:
    quality = execution_inputs.get("quality")
    if not isinstance(quality, Mapping):
        return None
    funding = quality.get("funding")
    if funding_override is not None:
        funding = funding_override
    if not isinstance(funding, Mapping):
        return None
    start = _timestamp_ms(quality.get("requested_start_utc"))
    end = _timestamp_ms(quality.get("requested_end_utc"))
    try:
        interval_hours = int(funding.get("expected_interval_hours", 8))
        rows = int(funding.get("actual_rows", funding.get("rows", 0)))
    except (TypeError, ValueError):
        return None
    if start is None or end is None or interval_hours <= 0:
        return None
    expected = ((end - start) // (interval_hours * 3_600_000)) + 1
    return rows / expected if expected > 0 else None


def _validated_funding_quality(
    execution_inputs: Mapping[str, Any],
    *,
    source_sha256s: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    quality = execution_inputs.get("quality")
    quality = quality if isinstance(quality, Mapping) else {}
    funding = quality.get("funding")
    funding = funding if isinstance(funding, Mapping) else {}
    rows = execution_inputs.get("funding_history")
    start = _timestamp_ms(quality.get("requested_start_utc"))
    end = _timestamp_ms(quality.get("requested_end_utc"))
    window_declared_valid = start is not None and end is not None
    raw_interval_hours = funding.get(
        "expected_interval_hours", EXPECTED_FUNDING_INTERVAL_HOURS
    )
    try:
        if isinstance(raw_interval_hours, bool):
            raise ValueError
        if isinstance(raw_interval_hours, float):
            if not raw_interval_hours.is_integer():
                raise ValueError
            interval_hours = int(raw_interval_hours)
        elif isinstance(raw_interval_hours, int):
            interval_hours = raw_interval_hours
        elif isinstance(raw_interval_hours, str) and raw_interval_hours.strip().isdigit():
            interval_hours = int(raw_interval_hours.strip())
        else:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        interval_hours = EXPECTED_FUNDING_INTERVAL_HOURS
        window_declared_valid = False
    interval_declared_valid = interval_hours > 0
    if not interval_declared_valid:
        interval_hours = EXPECTED_FUNDING_INTERVAL_HOURS
    passport_start = (
        start
        if start is not None
        else _timestamp_ms(EXPECTED_FUNDING_START_UTC)
    )
    passport_end = end if end is not None else _timestamp_ms(EXPECTED_FUNDING_END_UTC)
    funding_rows = rows if isinstance(rows, list) else []
    if start is not None and end is not None and interval_hours > 0:
        derived = validate_funding_rows(
            funding_rows,
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
            "expected_symbol": str(execution_inputs.get("symbol") or "").strip().upper() or None,
            "missing_symbol_rows": 0,
            "foreign_symbol_rows": 0,
            "missing_rows": 0,
            "unexpected_in_range_rows": 0,
            "interval_gaps": 0,
            "complete_between_observed_bounds": False,
            "complete_requested_window": False,
        }
    mark_rows = execution_inputs.get("mark_price_history")
    mark_timestamps: list[int] = []
    if isinstance(mark_rows, list):
        for row in mark_rows:
            raw_timestamp = (
                row.get("timestamp_ms")
                if isinstance(row, Mapping)
                else row[0]
                if isinstance(row, (list, tuple)) and row
                else None
            )
            timestamp = _timestamp_ms(raw_timestamp)
            if timestamp is not None:
                mark_timestamps.append(timestamp)
    mark_quality = quality.get("mark")
    mark_quality = mark_quality if isinstance(mark_quality, Mapping) else {}
    mark_coverage_override = (
        mark_quality.get("coverage_ratio")
        if not mark_timestamps and "coverage_ratio" in mark_quality
        else None
    )
    passport = build_data_passport(
        funding_rows,
        symbol=str(execution_inputs.get("symbol") or ""),
        start_ms=passport_start,
        end_ms=passport_end,
        cadence_interval_ms=interval_hours * 3_600_000,
        mark_timestamps_ms=mark_timestamps if mark_timestamps else None,
        mark_before_settlement_coverage_override=mark_coverage_override,
        source_sha256s=source_sha256s,
        exploratory=False,
    )
    derived["funding_history_present"] = isinstance(rows, list)
    derived["funding_cadence_is_eight_hours"] = interval_hours == 8
    derived["data_passport"] = passport.as_dict()
    derived["passport_window_declared_valid"] = window_declared_valid
    derived["passport_interval_declared_valid"] = interval_declared_valid
    derived["mark_before_settlement_rows"] = passport.mark_before_settlement_rows
    derived["mark_before_settlement_coverage"] = (
        passport.mark_before_settlement_coverage
    )
    derived["mark_missing_timestamps_ms"] = list(passport.mark_missing_timestamps_ms)
    derived["missing_timestamps_ms"] = list(passport.missing_timestamps_ms)
    derived["complete_requested_window"] = passport.complete_requested_window
    return derived


def _profile_checks(profile: Mapping[str, Any], *, asset: str | None = None) -> dict[str, Any]:
    """Validate the public market contract used by historical research.

    Account mode, leverage, withdrawals, and IP allowlisting belong to the
    later account-readiness check.  Historical data must use a public market
    profile and must not accidentally depend on a private account snapshot.
    """

    instrument = profile.get("instrument")
    mark_and_index = profile.get("mark_and_index")
    tiers = profile.get("margin_tiers")
    hashes = profile.get("source_response_sha256")
    instrument = instrument if isinstance(instrument, Mapping) else {}
    mark_and_index = mark_and_index if isinstance(mark_and_index, Mapping) else {}

    symbol_matches_asset = True
    if asset is not None:
        symbol_matches_asset = str(profile.get("symbol", "")).upper() == f"{asset.upper()}USDT"

    return {
        "schema_public_market_profile": profile.get("schema_version")
        == "bitget-public-market-profile-v1",
        "read_only_only": profile.get("read_only_only") is True,
        "trading_operations_zero": profile.get("trading_operations_count") == 0,
        "exchange_bitget": str(profile.get("exchange", "")).lower() == "bitget",
        "symbol_matches_asset": symbol_matches_asset,
        "instrument_online": instrument.get("status") == "online",
        "instrument_perpetual": instrument.get("contract_type") == "perpetual",
        "quote_usdt": str(instrument.get("quote_coin", "")).upper() == "USDT",
        "maker_fee_present": _finite_nonnegative(instrument.get("maker_fee_rate")),
        "taker_fee_present": _finite_nonnegative(instrument.get("taker_fee_rate")),
        "funding_interval_eight_hours": instrument.get("funding_interval_hours") == 8,
        "mark_price_present": _finite_positive(mark_and_index.get("mark_price")),
        "index_price_present": _finite_positive(mark_and_index.get("index_price")),
        "margin_tiers_present": isinstance(tiers, list) and bool(tiers),
        "response_hashes_present": isinstance(hashes, Mapping) and bool(hashes),
    }


def _finite_nonnegative(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and abs(number) != float("inf") and number >= 0.0


def _finite_positive(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and abs(number) != float("inf") and number > 0.0


def public_market_fees(profile: Mapping[str, Any]) -> dict[str, float]:
    """Return fee rates only from a validated public market profile."""

    instrument = profile.get("instrument")
    if not isinstance(instrument, Mapping):
        raise ValueError("public market profile has no instrument contract")
    maker = instrument.get("maker_fee_rate")
    taker = instrument.get("taker_fee_rate")
    if not _finite_nonnegative(maker) or not _finite_nonnegative(taker):
        raise ValueError("public market profile has invalid fee rates")
    return {"maker_fee_rate": float(maker), "taker_fee_rate": float(taker)}


def public_market_fee_provenance(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Describe the base fee and the opt-in fee stress separately.

    The base rate is read from the public instrument profile.  The higher
    ``0.0004`` rate is never a fallback and is not enabled by this preflight;
    it is an explicitly named stress case for a later experiment.
    """

    fees = public_market_fees(profile)
    return {
        "base": {
            "maker_fee_rate": fees["maker_fee_rate"],
            "taker_fee_rate": fees["taker_fee_rate"],
            "source": "public_market_profile",
            "enabled_by_default": True,
            "status": "passed",
        },
        "stress": {
            "label": STRESS_MAKER_FEE_LABEL,
            "maker_fee_rate": STRESS_MAKER_FEE_RATE,
            "enabled_by_default": False,
            "source": "explicit_stress_only",
            "status": "available",
        },
    }


def _blocked_fee_provenance(reason: str) -> dict[str, Any]:
    return {
        "base": {
            "maker_fee_rate": None,
            "taker_fee_rate": None,
            "source": "public_market_profile",
            "enabled_by_default": True,
            "status": "blocked",
            "reason": reason,
        },
        "stress": {
            "label": STRESS_MAKER_FEE_LABEL,
            "maker_fee_rate": STRESS_MAKER_FEE_RATE,
            "enabled_by_default": False,
            "source": "explicit_stress_only",
            "status": "available",
        },
    }


def _funding_failure_reason(
    funding: Mapping[str, Any], coverage_ratio: float | None
) -> str | None:
    """Return the first explicit reason the funding gate is closed.

    Observed-bounds completeness is deliberately not sufficient: a history
    that is complete only from its first to last saved row may still omit most
    of the requested training window.
    """

    if funding.get("funding_history_present") is not True:
        return "funding_history_rows_unavailable"
    if funding.get("funding_cadence_is_eight_hours") is not True:
        return "funding_cadence_not_eight_hours"
    if coverage_ratio is None:
        return "coverage_unavailable"
    if coverage_ratio < 1.0:
        return "coverage_below_100_percent"
    if float(funding.get("mark_before_settlement_coverage", 0.0) or 0.0) < 1.0:
        return "mark_before_settlement_gap"
    if funding.get("complete_requested_window") is not True:
        return "requested_window_not_confirmed"

    def count(name: str) -> int | None:
        value = funding.get(name, 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError, OverflowError):
            return None

    for name, reason in (
        ("missing_symbol_rows", "missing_funding_symbols"),
        ("foreign_symbol_rows", "foreign_funding_symbols"),
        ("missing_rows", "missing_funding_rows"),
        ("future_rows", "future_funding_rows"),
        ("outside_requested_range", "funding_rows_outside_requested_range"),
        ("invalid_rows", "invalid_funding_rows"),
        ("duplicate_rows", "duplicate_funding_rows"),
        ("interval_gaps", "funding_interval_gaps"),
    ):
        value = count(name)
        if value is None:
            return f"invalid_{name}"
        if value != 0:
            return reason
    return None


def _execution_checks(
    execution_inputs: Mapping[str, Any],
    *,
    expected_symbol: str | None = None,
    source_sha256s: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    quality = execution_inputs.get("quality")
    quality = quality if isinstance(quality, Mapping) else {}
    mark = quality.get("mark") if isinstance(quality.get("mark"), Mapping) else {}
    index = quality.get("index") if isinstance(quality.get("index"), Mapping) else {}
    funding = _validated_funding_quality(
        execution_inputs,
        source_sha256s=source_sha256s,
    )
    funding_coverage = _funding_coverage(execution_inputs, funding)
    funding_failure_reason = _funding_failure_reason(funding, funding_coverage)
    passport = funding.get("data_passport")
    expected = str(expected_symbol or "").strip().upper()
    actual_symbol = str(execution_inputs.get("symbol") or "").strip().upper()
    passport_window_matches_protocol = (
        isinstance(passport, Mapping)
        and passport.get("symbol") == (expected or actual_symbol)
        and _timestamp_ms(passport.get("start_utc")) == _timestamp_ms(
            EXPECTED_FUNDING_START_UTC
        )
        and _timestamp_ms(passport.get("end_utc")) == _timestamp_ms(
            EXPECTED_FUNDING_END_UTC
        )
        and passport.get("cadence_interval_ms")
        == EXPECTED_FUNDING_INTERVAL_HOURS * 3_600_000
    )
    funding_passport_qualifying = bool(
        isinstance(passport, Mapping)
        and passport.get("qualifying") is True
        and passport_window_matches_protocol
        and funding.get("passport_window_declared_valid") is True
        and funding.get("passport_interval_declared_valid") is True
    )
    execution_identity_valid = (
        execution_inputs.get("schema_version") == "bitget-public-execution-inputs-v1"
        and str(execution_inputs.get("exchange") or "").strip().lower() == "bitget"
        and execution_inputs.get("read_only_only") is True
        and execution_inputs.get("trading_operations_count") == 0
        and (not expected or actual_symbol == expected)
    )
    funding_symbol_identity_valid = (
        bool(expected)
        and funding.get("expected_symbol") == expected
        and funding.get("missing_symbol_rows", 0) == 0
        and funding.get("foreign_symbol_rows", 0) == 0
    )
    return {
        "execution_identity_valid": execution_identity_valid,
        "funding_symbol_identity_valid": funding_symbol_identity_valid,
        "funding_coverage_ratio": funding_coverage,
        "funding_complete": funding_failure_reason is None,
        "funding_fail_closed": funding_failure_reason is not None
        or not funding_passport_qualifying,
        "funding_failure_reason": funding_failure_reason,
        "funding_quality": funding,
        "data_passport": passport,
        "passport": passport,
        "funding_passport_qualifying": funding_passport_qualifying,
        "funding_passport_window_matches_protocol": passport_window_matches_protocol,
        "mark_coverage": float(mark.get("coverage_ratio", 0.0) or 0.0),
        "index_coverage": float(index.get("coverage_ratio", 0.0) or 0.0),
        "mark_complete": float(mark.get("coverage_ratio", 0.0) or 0.0) >= 0.9995,
        "index_complete": float(index.get("coverage_ratio", 0.0) or 0.0) >= 0.9995,
        "no_future_rows": quality.get("no_future_market_rows") is True
        and funding.get("future_rows", 0) == 0
        and isinstance(passport, Mapping)
        and passport.get("future_rows") == 0,
        "no_invalid_rows": quality.get("no_nan_or_invalid_mark_rows") is True
        and quality.get("no_nan_or_invalid_index_rows") is True,
    }


def _ohlcv_checks(repair_report: Mapping[str, Any]) -> dict[str, Any]:
    after_missing = repair_report.get("after_missing_rows")
    return {
        "status_repaired": repair_report.get("status") == "repaired",
        "no_missing_rows": after_missing is not None and int(after_missing) == 0,
        "trading_operations_zero": repair_report.get("trading_operations_count") == 0,
    }


def _normalise_declared_path(value: Any, *, root: Path) -> str | None:
    if value in (None, ""):
        return None
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return str(candidate.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(candidate.resolve())


def _expected_ohlcv_identity(asset: str) -> dict[str, str]:
    normalized = str(asset).upper()
    return {
        "exchange": EXPECTED_OHLCV_EXCHANGE,
        "symbol": f"{normalized}/USDT:USDT",
        "timeframe": EXPECTED_OHLCV_TIMEFRAME,
        "start_utc": EXPECTED_OHLCV_START_UTC,
        "end_utc": EXPECTED_OHLCV_END_UTC,
    }


def _ohlcv_semantic_checks(
    quality_report: Mapping[str, Any], *, asset: str
) -> dict[str, Any]:
    """Bind the selected quality report to this asset and the fixed protocol.

    A quality report is evidence for one concrete market/range.  Its status and
    row checks are not sufficient when the report can be swapped for another
    symbol or period, so every identity field is checked independently.
    """

    expected = _expected_ohlcv_identity(asset)
    report_range = quality_report.get("range")
    report_range = report_range if isinstance(report_range, Mapping) else {}
    return {
        "exchange_matches_expected": str(quality_report.get("exchange", "")).lower()
        == expected["exchange"],
        "symbol_matches_expected": quality_report.get("symbol") == expected["symbol"],
        "timeframe_matches_expected": quality_report.get("timeframe")
        == expected["timeframe"],
        "range_start_matches_expected": _timestamp_ms(report_range.get("start_utc"))
        == _timestamp_ms(expected["start_utc"]),
        "range_end_matches_expected": _timestamp_ms(report_range.get("end_utc"))
        == _timestamp_ms(expected["end_utc"]),
    }


def _remote_fetch_declarations(
    quality_report: Mapping[str, Any], data_metrics: Mapping[str, Any] | None = None
) -> tuple[list[Any], list[Any]]:
    """Collect every remote-fetch declaration without inventing defaults."""

    policies: list[Any] = []
    counts: list[Any] = []
    for source in (quality_report, data_metrics or {}):
        if "remote_fetch_policy" in source:
            policies.append(source.get("remote_fetch_policy"))
        if "remote_fetch_count" in source:
            counts.append(source.get("remote_fetch_count"))
    return policies, counts


def _quality_report_declarations(
    quality_report: Mapping[str, Any],
) -> tuple[Any, list[Any], list[Any]]:
    """Return provenance declared by the selected quality report itself.

    ``data_metrics`` may point at the quality report, but it is a summary and
    is not allowed to fill in missing provenance fields from that report.
    Keeping the declarations tied to one selected artifact prevents a stale
    report and a different cache from being combined into a passing result.
    """

    declared_cache_path = None
    for field in ("cache_path", "ohlcv_cache_path"):
        if field in quality_report:
            declared_cache_path = quality_report.get(field)
            break
    policies, counts = _remote_fetch_declarations(quality_report)
    return declared_cache_path, policies, counts


def _is_explicit_zero_fetch_count(value: Any) -> bool:
    """Accept only an unambiguous JSON zero for a fetch count."""

    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == 0
    return isinstance(value, str) and value.strip() == "0"


def _remote_fetch_count_is_zero(values: Sequence[Any]) -> bool:
    if not values:
        return False
    return all(_is_explicit_zero_fetch_count(value) for value in values)


def _ohlcv_quality_diagnostics(
    quality_report: Mapping[str, Any],
    checks: Mapping[str, Any],
    *,
    data_metrics: Mapping[str, Any] | None = None,
    asset: str | None = None,
) -> list[str]:
    """Explain missing or conflicting cache provenance without weakening it."""

    declared_cache_path, quality_policies, quality_counts = _quality_report_declarations(
        quality_report
    )
    all_policies, all_counts = _remote_fetch_declarations(quality_report, data_metrics)
    diagnostics: list[str] = []
    if asset is not None:
        expected = _expected_ohlcv_identity(asset)
        semantic_messages = {
            "exchange_matches_expected": (
                "exchange does not match expected " f"{expected['exchange']}"
            ),
            "symbol_matches_expected": (
                "symbol does not match expected " f"{expected['symbol']}"
            ),
            "timeframe_matches_expected": (
                "timeframe does not match expected " f"{expected['timeframe']}"
            ),
            "range_start_matches_expected": (
                "range start does not match expected " f"{expected['start_utc']}"
            ),
            "range_end_matches_expected": (
                "range end does not match expected " f"{expected['end_utc']}"
            ),
        }
        for name, message in semantic_messages.items():
            if checks.get(name) is not True:
                diagnostics.append(f"selected quality report {message}")
    if declared_cache_path in (None, ""):
        diagnostics.append(
            "selected quality report has no explicit cache_path declaration"
        )
    elif checks.get("canonical_cache_path") is not True:
        diagnostics.append(
            "selected quality report cache_path does not resolve to the canonical "
            f"{CANONICAL_OHLCV_CACHE_DIR} directory"
        )
    if not quality_policies:
        diagnostics.append(
            "selected quality report has no explicit remote_fetch_policy declaration"
        )
    elif not all(policy == LOCAL_ONLY_REMOTE_FETCH_POLICY for policy in all_policies):
        diagnostics.append(
            "remote_fetch_policy is missing, conflicting, or not local_only across "
            "the selected quality evidence"
        )
    if not quality_counts:
        diagnostics.append(
            "selected quality report has no explicit remote_fetch_count declaration"
        )
    elif not _remote_fetch_count_is_zero(all_counts):
        diagnostics.append(
            "remote_fetch_count is missing, conflicting, malformed, or nonzero across "
            "the selected quality evidence"
        )
    if checks.get("canonical_cache_exists") is not True:
        diagnostics.append(
            f"canonical cache directory is missing: {CANONICAL_OHLCV_CACHE_DIR}"
        )
    return diagnostics


def _ohlcv_quality_checks(
    quality_report: Mapping[str, Any],
    *,
    root: Path,
    data_metrics: Mapping[str, Any] | None = None,
    asset: str | None = None,
) -> dict[str, Any]:
    checks = quality_report.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    metrics = data_metrics if isinstance(data_metrics, Mapping) else {}
    declared_cache_path, quality_remote_fetch_policies, quality_remote_fetch_counts = (
        _quality_report_declarations(quality_report)
    )
    remote_fetch_policies, remote_fetch_counts = _remote_fetch_declarations(
        quality_report, metrics
    )
    cache_dir = root / CANONICAL_OHLCV_CACHE_DIR
    result = {
        "status_passed": quality_report.get("status") == "passed",
        "coverage_complete": checks.get("coverage_complete") is True,
        "values_valid": all(
            checks.get(name) is True
            for name in ("values_finite", "prices_positive", "prices_ordered", "volume_nonnegative")
        ),
        "checksums_verified": checks.get("chunk_checksums_verified") is True,
        "no_future_rows": checks.get("no_future_rows") is True,
        "trading_operations_zero": quality_report.get("trading_operations_count") == 0,
        "canonical_cache_path": _normalise_declared_path(declared_cache_path, root=root)
        == CANONICAL_OHLCV_CACHE_DIR,
        "canonical_cache_exists": cache_dir.is_dir(),
        "remote_fetch_policy_declared_local_only": bool(quality_remote_fetch_policies)
        and all(
            policy == LOCAL_ONLY_REMOTE_FETCH_POLICY for policy in remote_fetch_policies
        ),
        "remote_fetch_count_zero": _remote_fetch_count_is_zero(quality_remote_fetch_counts)
        and _remote_fetch_count_is_zero(remote_fetch_counts),
    }
    if asset is not None:
        result.update(_ohlcv_semantic_checks(quality_report, asset=asset))
    return result


def _status_checks(status: Mapping[str, Any]) -> dict[str, Any]:
    stages = status.get("stages") if isinstance(status.get("stages"), Mapping) else {}
    # Historical research uses the public risk engine and independent risk
    # scenarios.  The old combined risk_contract includes private account
    # evidence and belongs to the later live path; it must not block public
    # historical work.
    required = ("baseline", "market_profile", "data", "risk_engine", "risk_grid")
    return {
        stage: isinstance(stages.get(stage), Mapping)
        and stages[stage].get("status") == "passed"
        for stage in required
    }


def _resolve_repo_evidence_path(
    value: Any, *, root: Path, field: str
) -> tuple[Path | None, str | None]:
    """Resolve a source-evidence path without reading outside ``root``."""

    if not isinstance(value, (str, Path)) or not str(value).strip():
        return None, f"source_evidence.{field} is empty"
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return (
            None,
            f"source_evidence.{field} cannot be resolved: {type(exc).__name__}",
        )
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return (
            None,
            f"source_evidence.{field} points outside the repository root: {value}",
        )
    return resolved, None


def _asset_paths_with_diagnostics(
    root: Path,
    asset: str,
    *,
    data_metrics: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Path], list[str]]:
    normalized = asset.upper()
    if normalized not in PUBLIC_MARKET_PROFILE_FILENAMES:
        raise ValueError(f"historical preflight supports only BTC and ETH, got {asset!r}")
    inputs = root / "analysis_reports" / "crypto_bot_roadmap" / "inputs"
    reports = root / "analysis_reports" / "crypto_bot_roadmap"
    paths = {
        # There is intentionally no fallback to ``*_market_profile_read_only``:
        # that file is an account/UTA probe and cannot authorize historical
        # market selection.  A missing public profile must remain blocked.
        "profile": inputs / PUBLIC_MARKET_PROFILE_FILENAMES[normalized],
        "execution_inputs": inputs / EXECUTION_INPUT_FILENAMES[normalized],
        "data_metrics": reports / DATA_METRICS_FILENAMES[normalized],
        "ohlcv_repair": inputs / OHLCV_REPAIR_FILENAMES[normalized],
        "ohlcv_quality": inputs / OHLCV_QUALITY_FILENAMES[normalized],
        "ohlcv_cache_dir": root / CANONICAL_OHLCV_CACHE_DIR,
    }
    diagnostics: list[str] = []
    evidence = data_metrics.get("source_evidence") if isinstance(data_metrics, Mapping) else None
    if isinstance(evidence, Mapping):
        for field, path_key in OHLCV_SOURCE_EVIDENCE_FIELDS.items():
            if field not in evidence:
                continue
            selected, error = _resolve_repo_evidence_path(
                evidence.get(field), root=root, field=field
            )
            if error:
                diagnostics.append(error)
            elif selected is not None:
                paths[path_key] = selected
    return paths, diagnostics


def _asset_paths(
    root: Path,
    asset: str,
    *,
    data_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Return deterministic paths, preferring paths anchored by source evidence."""

    paths, _ = _asset_paths_with_diagnostics(root, asset, data_metrics=data_metrics)
    return paths


def build_preflight(
    *,
    root: str | Path,
    assets: Sequence[str] = ("BTC", "ETH"),
    status_path: str | Path | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).expanduser().resolve()
    resolved_status = (
        Path(status_path).expanduser().resolve()
        if status_path is not None
        else repo_root / "analysis_reports" / "crypto_bot_roadmap" / "status.json"
    )
    status, status_error = _read_json(resolved_status)
    status = status or {}
    status_checks = _status_checks(status)
    results: dict[str, Any] = {}
    for raw_asset in assets:
        asset = str(raw_asset).upper()
        initial_paths = _asset_paths(repo_root, asset)
        metrics, metrics_error = _read_json(initial_paths["data_metrics"])
        paths, fixture_discovery_errors = _asset_paths_with_diagnostics(
            repo_root, asset, data_metrics=metrics
        )
        profile, profile_error = _read_json(paths["profile"])
        execution, execution_error = _read_json(paths["execution_inputs"])
        ohlcv, ohlcv_error = _read_json(paths["ohlcv_repair"])
        ohlcv_quality, ohlcv_quality_error = _read_json(paths["ohlcv_quality"])
        profile_checks = _profile_checks(profile or {}, asset=asset) if profile else {}
        execution_inputs_sha256 = _sha256_file(paths["execution_inputs"])
        execution_checks = (
            _execution_checks(
                execution or {},
                expected_symbol=f"{asset}USDT",
                source_sha256s=(
                    {"execution_inputs": execution_inputs_sha256}
                    if execution_inputs_sha256
                    else None
                ),
            )
            if execution
            else {}
        )
        metrics_execution_inputs_sha256 = (
            metrics.get("execution_inputs_sha256")
            if isinstance(metrics, Mapping)
            else None
        )
        execution_input_hash_matches_metrics = bool(
            execution_inputs_sha256
            and isinstance(metrics_execution_inputs_sha256, str)
            and metrics_execution_inputs_sha256 == execution_inputs_sha256
        )
        ohlcv_checks = _ohlcv_checks(ohlcv or {}) if ohlcv else {}
        ohlcv_quality_checks = (
            _ohlcv_quality_checks(
                ohlcv_quality or {},
                root=repo_root,
                data_metrics=metrics,
                asset=asset,
            )
            if ohlcv_quality
            else {}
        )
        if profile:
            try:
                fee_provenance = public_market_fee_provenance(profile)
            except ValueError as exc:
                fee_provenance = _blocked_fee_provenance(str(exc))
        else:
            fee_provenance = _blocked_fee_provenance("public market profile is unavailable")
        reasons: list[str] = []
        if status_error:
            reasons.append(status_error)
        reasons.extend(
            f"OHLCV fixture discovery failed: {error}"
            for error in fixture_discovery_errors
        )
        for error in (profile_error, execution_error, metrics_error, ohlcv_error, ohlcv_quality_error):
            if error:
                reasons.append(error)
        for stage, passed in status_checks.items():
            if not passed:
                reasons.append(f"roadmap stage {stage} is not passed")
        if profile and not all(profile_checks.values()):
            reasons.extend(
                f"profile check failed: {name}"
                for name, passed in profile_checks.items()
                if not passed
            )
        execution_required_checks = (
            "execution_identity_valid",
            "funding_symbol_identity_valid",
            "funding_complete",
            "funding_passport_qualifying",
            "funding_passport_window_matches_protocol",
            "mark_complete",
            "index_complete",
            "no_future_rows",
            "no_invalid_rows",
        )
        if execution and not all(
            execution_checks.get(name) is True
            for name in execution_required_checks
        ):
            reasons.extend(
                f"execution input check failed: {name}"
                for name in execution_required_checks
                if execution_checks.get(name) is not True
            )
            if execution_checks.get("funding_complete") is not True:
                reasons.append(
                    "funding gate closed: "
                    f"{execution_checks.get('funding_failure_reason') or 'incomplete'}"
                )
        if metrics:
            if not execution_input_hash_matches_metrics:
                reasons.append(
                    "data metrics do not match the current execution-input file hash"
                )
            if metrics.get("history_through_plan_date") is not True:
                reasons.append("data metrics do not reach the plan date")
            if float(metrics.get("history_coverage_ratio", 0.0) or 0.0) < 0.9995:
                reasons.append("data metrics have insufficient market coverage")
            if metrics.get("funding_history_complete") is not True:
                reasons.append("data metrics do not confirm complete funding history")
        if ohlcv and not all(ohlcv_checks.values()):
            reasons.extend(
                f"OHLCV repair check failed: {name}"
                for name, passed in ohlcv_checks.items()
                if not passed
            )
        if ohlcv_quality and not all(ohlcv_quality_checks.values()):
            reasons.extend(
                f"OHLCV cache quality check failed: {name}"
                for name, passed in ohlcv_quality_checks.items()
                if not passed
            )
            reasons.extend(
                f"OHLCV cache provenance diagnostic: {diagnostic}"
                for diagnostic in _ohlcv_quality_diagnostics(
                    ohlcv_quality,
                    ohlcv_quality_checks,
                    data_metrics=metrics,
                    asset=asset,
                )
            )
        results[asset] = {
            "symbol": f"{asset}USDT",
            "ready": not reasons,
            "reasons": sorted(set(reasons)),
            "paths": {name: str(path) for name, path in paths.items()},
            "profile_checks": profile_checks,
            "execution_checks": execution_checks,
            "execution_inputs_sha256": execution_inputs_sha256,
            "metrics_execution_inputs_sha256": metrics_execution_inputs_sha256,
            "execution_input_hash_matches_metrics": execution_input_hash_matches_metrics,
            "data_passport": execution_checks.get("data_passport"),
            "passport": execution_checks.get("data_passport"),
            "ohlcv_checks": ohlcv_checks,
            "ohlcv_quality_checks": ohlcv_quality_checks,
            "ohlcv_quality_diagnostics": (
                _ohlcv_quality_diagnostics(
                    ohlcv_quality,
                    ohlcv_quality_checks,
                    data_metrics=metrics,
                    asset=asset,
                )
                if ohlcv_quality
                else []
            ),
            "fixture_discovery_errors": fixture_discovery_errors,
            "fee_provenance": fee_provenance,
            "data_metrics": metrics or {},
            "legacy_candidate_policy": "excluded_from_four_window_protocol",
        }
    ready = bool(results) and all(item["ready"] for item in results.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_date": PLAN_DATE,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "passed" if ready else "blocked",
        "protocol": PROTOCOL,
        "roadmap_status_path": str(resolved_status),
        "roadmap_stage_checks": status_checks,
        "legacy_candidate_policy": "excluded_from_four_window_protocol",
        "assets": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--asset", action="append", dest="assets")
    parser.add_argument(
        "--status",
        type=Path,
        help="roadmap status JSON; defaults to analysis_reports/crypto_bot_roadmap/status.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "analysis_reports/crypto_bot_roadmap/preflight/"
            "historical_preflight_2026-08-17.json"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repo_root.expanduser().resolve()
    result = build_preflight(
        root=root,
        assets=tuple(args.assets or ("BTC", "ETH")),
        status_path=args.status,
    )
    output = args.output.expanduser()
    if not output.is_absolute():
        output = root / output
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "status": result["status"]}, sort_keys=True))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
