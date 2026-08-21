"""Derive paper/live gate counters from the immutable monitor event log."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "crypto-runtime-evidence-v2"
DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000

# A first/last event interval is not evidence of a continuously observed
# runtime.  Every UTC day in the observed interval must have an observation
# and a heartbeat.  The small interval tolerance allows normal scheduling
# jitter, but does not allow an unobserved day to be hidden by a 90% ratio.
MIN_CALENDAR_COVERAGE_RATIO = 1.0
MIN_HEARTBEAT_COVERAGE_RATIO = 1.0
MAX_HEARTBEAT_GAP_HOURS = 26.0

# ``account.balance`` is emitted by the current monitor during authoritative
# refreshes.  The explicit heartbeat spellings make the reader forward
# compatible with a dedicated runtime heartbeat without treating arbitrary
# user events as proof of liveness.
HEARTBEAT_KINDS = frozenset(
    {
        "heartbeat",
        "health.heartbeat",
        "monitor.heartbeat",
        "runtime.heartbeat",
        "bot.start",
        "bot.ready",
        "bot.stop",
        "account.balance",
    }
)
COSTS_PROOF_KINDS = frozenset(
    {
        "costs.reconciled",
        "reconciliation.costs",
        "runtime.costs_verified",
    }
)
TYPED_MARKET_KINDS = frozenset(
    {
        "market.snapshot",
        "market.quote",
        "market.mark_index",
    }
)
TYPED_FUNDING_KINDS = frozenset(
    {
        "funding.payment",
        "funding.applied",
        "funding.settled",
    }
)
TYPED_MARGIN_KINDS = frozenset(
    {
        "margin.snapshot",
        "risk.snapshot",
        "account.margin",
    }
)
TYPED_RECONCILIATION_KINDS = frozenset(
    {
        "reconciliation.check",
        "reconciliation.snapshot",
        "reconcile.check",
    }
)
TYPED_EVIDENCE_SCHEMA_VERSION = "crypto-runtime-typed-evidence-v1"
OBSERVATION_KINDS = frozenset(
    {
        *HEARTBEAT_KINDS,
        "order.opened",
        "order.filled",
        "order.canceled",
        "order.cancelled",
        "strategy.cycle_started",
        "strategy.cycle_closed",
    }
)

# Values used for runtime-gate metrics which this module cannot prove from the
# event log.  They are finite on purpose: run artifacts are written with
# allow_nan=False, and each value fails the corresponding admission comparison
# instead of silently accepting a claim from metrics.json.
UNPROVEN_RUNTIME_METRICS: dict[str, Any] = {
    "uptime_ratio": 0.0,
    "max_restart_recovery_seconds": 61.0,
    "net_result": 0.0,
    "max_drawdown": 1.0,
    "liquidations": 1,
    "reconciliation_mismatches": 1,
    "max_recovery_days": 91.0,
    "gross_exposure_ratio": 0.81,
    "used_margin_ratio": 0.36,
    "min_liquidation_distance": 0.14,
    "execution_slippage_median_bps": 2.1,
    "execution_slippage_p95_bps": 10.1,
    "execution_slippage_max_bps": 25.1,
    "max_daily_loss": 0.0200001,
    "fee_reconciliation_abs_usdt": 0.0100001,
    "funding_reconciliation_abs_usdt": 0.0100001,
    "execution_p95_bps": 10.1,
    "order_confirmation_p95_seconds": 5.1,
}

# These are intentionally conservative until the event schema carries an
# explicit, independently checked proof for each claim.  Returning them from
# runtime_gate_metrics is important: research_gates.py merges that result
# after reading metrics.json, so a caller cannot restore an optimistic value.
UNPROVEN_RUNTIME_FLAGS: dict[str, Any] = {
    "all_costs_present": False,
    "typed_evidence_complete": False,
    "typed_fees_present": False,
    "typed_funding_present": False,
    "typed_slippage_present": False,
    "typed_mark_index_present": False,
    "typed_margin_present": False,
    "typed_reconciliation_present": False,
    "hsl_emergency_triggers": 1,
    "terminal_stop_reached": True,
}


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def _timestamp(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        number = int(value)
        return number if number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _event_id(payload: Mapping[str, Any]) -> str | None:
    for key in ("custom_id", "client_order_id", "id"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("payload")
    return value if isinstance(value, Mapping) else {}


def _cycle_id(row: Mapping[str, Any]) -> str | None:
    """Return the stable trade-cycle id, with a legacy cycle fallback."""

    payload = _payload(row)
    for key in ("trade_cycle_id", "cycle_id"):
        value = payload.get(key)
        if value is None or isinstance(value, bool):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _utc_day_bucket(timestamp_ms: int) -> int:
    """Return a stable UTC calendar-day bucket for a millisecond timestamp."""

    # Integer division avoids platform-specific datetime range limits while
    # retaining the UTC calendar semantics needed for coverage.
    return int(timestamp_ms // DAY_MS)


def _is_heartbeat(row: Mapping[str, Any]) -> bool:
    kind = str(row.get("kind", "")).strip().lower()
    if kind in HEARTBEAT_KINDS:
        return True
    payload = _payload(row)
    return payload.get("heartbeat") is True


def _is_observation(row: Mapping[str, Any]) -> bool:
    kind = str(row.get("kind", "")).strip().lower()
    if kind in OBSERVATION_KINDS or _is_heartbeat(row):
        return True
    # Risk, HSL and reconciliation events are useful observations of a
    # running process, but never count as heartbeat on their own.
    return kind.startswith(("risk.", "hsl.", "reconcile.", "reconciliation."))


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _safe_float(value: Any, fallback: float) -> float:
    number = _finite_number(value)
    return float(number) if number is not None else float(fallback)


def _safe_int(value: Any, fallback: int) -> int:
    number = _finite_number(value)
    return int(number) if number is not None else int(fallback)


def _nested_number(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        number = _finite_number(value)
        if number is not None:
            return number
        if isinstance(value, Mapping):
            for nested_key in ("current", "value", "amount"):
                number = _finite_number(value.get(nested_key))
                if number is not None:
                    return number
    return None


def _typed_section(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = payload.get(name)
    return value if isinstance(value, Mapping) else payload


def _typed_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _typed_currency(section: Mapping[str, Any], payload: Mapping[str, Any]) -> str | None:
    value = section.get("currency")
    if value is None:
        value = payload.get("quote_currency", payload.get("settle_currency"))
    text = _typed_text(value)
    return text.upper() if text else None


def _typed_fee(payload: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    raw = payload.get("fee", payload.get("fees"))
    if not isinstance(raw, Mapping):
        return None, "fee must be a typed object with amount/currency"
    amount = _nested_number(raw, "amount", "cost", "value")
    currency = _typed_currency(raw, payload)
    if amount is None:
        return None, "fee amount is missing or not finite"
    if amount < 0.0:
        return None, "fee amount must not be negative"
    if currency is None:
        return None, "fee currency is missing"
    rate = _nested_number(raw, "rate", "fee_rate")
    if rate is not None and not math.isfinite(rate):
        return None, "fee rate is not finite"
    return {
        "amount": float(amount),
        "currency": currency,
        "rate": float(rate) if rate is not None else None,
    }, None


def _typed_market_prices(payload: Mapping[str, Any]) -> tuple[dict[str, float] | None, str | None]:
    section = _typed_section(payload, "market")
    mark = _nested_number(section, "mark_price", "markPrice", "mark")
    index = _nested_number(section, "index_price", "indexPrice", "index")
    if mark is None:
        mark = _nested_number(payload, "mark_price", "markPrice", "mark")
    if index is None:
        index = _nested_number(payload, "index_price", "indexPrice", "index")
    if mark is None or mark <= 0.0:
        return None, "mark price is missing or not positive"
    if index is None or index <= 0.0:
        return None, "index price is missing or not positive"
    return {"mark_price": float(mark), "index_price": float(index)}, None


def _typed_slippage(payload: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    section = _typed_section(payload, "slippage")
    execution = _typed_section(payload, "execution")
    executed = _nested_number(
        execution,
        "executed_price",
        "execution_price",
        "fill_price",
        "price",
    )
    if executed is None:
        executed = _nested_number(payload, "executed_price", "execution_price", "fill_price", "price")
    reference = _nested_number(
        section,
        "reference_price",
        "benchmark_price",
        "expected_price",
    )
    if reference is None:
        reference = _nested_number(
            execution,
            "reference_price",
            "benchmark_price",
            "expected_price",
        )
    if reference is None:
        reference = _nested_number(
            payload,
            "reference_price",
            "benchmark_price",
            "expected_price",
        )
    direct_bps = _nested_number(section, "bps", "slippage_bps", "adverse_bps")
    if direct_bps is None:
        direct_bps = _nested_number(payload, "slippage_bps", "adverse_slippage_bps")
    if direct_bps is not None and (not math.isfinite(direct_bps) or direct_bps < 0.0):
        return None, "slippage bps must be finite and non-negative"
    if executed is not None and executed <= 0.0:
        return None, "executed price must be positive"
    if reference is not None and reference <= 0.0:
        return None, "slippage reference price must be positive"

    computed_bps: float | None = None
    side = str(payload.get("side") or "").strip().lower()
    if executed is not None and reference is not None and side in {"buy", "sell"}:
        signed = (executed / reference - 1.0) * 10_000.0
        if side == "sell":
            signed = -signed
        computed_bps = max(0.0, float(signed))
        if direct_bps is not None and not math.isclose(
            computed_bps,
            float(direct_bps),
            rel_tol=1e-6,
            abs_tol=1e-6,
        ):
            return None, "slippage bps disagrees with reference and executed prices"
    elif direct_bps is not None:
        computed_bps = float(direct_bps)

    if computed_bps is None:
        return None, "typed slippage requires prices or slippage bps"
    return {
        "bps": computed_bps,
        "reference_price": float(reference) if reference is not None else None,
        "executed_price": float(executed) if executed is not None else None,
        "derived_from_prices": bool(executed is not None and reference is not None),
    }, None


def _typed_funding(payload: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    section = _typed_section(payload, "funding")
    amount = _nested_number(section, "amount", "payment", "funding_payment", "cost")
    rate = _nested_number(section, "rate", "funding_rate")
    notional = _nested_number(section, "notional", "position_notional", "notional_value")
    currency = _typed_currency(section, payload)
    if amount is None:
        return None, "funding amount is missing or not finite"
    if rate is None:
        return None, "funding rate is missing or not finite"
    if notional is None or notional < 0.0:
        return None, "funding notional is missing or invalid"
    if currency is None:
        return None, "funding currency is missing"
    expected = abs(float(notional) * float(rate))
    if not math.isclose(
        abs(float(amount)),
        expected,
        rel_tol=1e-5,
        abs_tol=1e-8,
    ):
        return None, "funding amount does not match notional times rate"
    return {
        "amount": float(amount),
        "rate": float(rate),
        "notional": float(notional),
        "currency": currency,
    }, None


def _typed_margin(payload: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    section = _typed_section(payload, "margin")
    equity = _nested_number(section, "equity", "account_equity")
    gross = _nested_number(section, "gross_exposure", "gross_notional", "exposure")
    used = _nested_number(section, "used_margin", "margin_used")
    distance = _nested_number(
        section,
        "liquidation_distance",
        "liquidation_buffer",
        "distance_to_liquidation",
    )
    if equity is None or equity <= 0.0:
        return None, "margin equity is missing or not positive"
    if gross is None or gross < 0.0:
        return None, "gross exposure is missing or invalid"
    if used is None or used < 0.0:
        return None, "used margin is missing or invalid"
    if distance is None or distance < 0.0 or distance > 1.0:
        return None, "liquidation distance must be between zero and one"
    gross_ratio = float(gross) / float(equity)
    used_ratio = float(used) / float(equity)
    reported_gross_ratio = _nested_number(section, "gross_exposure_ratio", "exposure_ratio")
    reported_used_ratio = _nested_number(section, "used_margin_ratio", "margin_ratio")
    if reported_gross_ratio is not None and not math.isclose(
        gross_ratio, float(reported_gross_ratio), rel_tol=1e-6, abs_tol=1e-8
    ):
        return None, "gross exposure ratio disagrees with typed amounts"
    if reported_used_ratio is not None and not math.isclose(
        used_ratio, float(reported_used_ratio), rel_tol=1e-6, abs_tol=1e-8
    ):
        return None, "used margin ratio disagrees with typed amounts"
    return {
        "equity": float(equity),
        "gross_exposure": float(gross),
        "used_margin": float(used),
        "gross_exposure_ratio": gross_ratio,
        "used_margin_ratio": used_ratio,
        "liquidation_distance": float(distance),
    }, None


def _typed_reconciliation(payload: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    section = _typed_section(payload, "reconciliation")
    mismatch = _nested_number(section, "mismatch_count", "mismatches")
    if mismatch is None or mismatch < 0.0 or not float(mismatch).is_integer():
        return None, "reconciliation mismatch_count must be a non-negative integer"
    checks = section.get("checks", payload.get("checks"))
    if not isinstance(checks, list) or not checks:
        return None, "reconciliation requires a non-empty checks list"
    derived_mismatches = 0
    for check in checks:
        if not isinstance(check, Mapping):
            return None, "reconciliation checks must be objects"
        if not _typed_text(check.get("name")):
            return None, "reconciliation check name is missing"
        if isinstance(check.get("match"), bool):
            derived_mismatches += int(not check["match"])
        elif isinstance(check.get("mismatch"), bool):
            derived_mismatches += int(check["mismatch"])
        else:
            return None, "reconciliation check requires match or mismatch"
    if int(mismatch) != derived_mismatches:
        return None, "reconciliation mismatch_count disagrees with checks"
    source = _typed_text(section.get("source", payload.get("source")))
    if source is None:
        return None, "reconciliation source is missing"
    return {
        "mismatch_count": int(mismatch),
        "check_count": len(checks),
        "source": source,
    }, None


def _typed_cost_reconciliation(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    section = _typed_section(payload, "costs")
    if payload.get("all_costs_present") is not True and section.get("all_costs_present") is not True:
        return None, "cost reconciliation must explicitly set all_costs_present"
    fee_total = _nested_number(section, "fee_total_usdt", "fees_total_usdt", "fee_total")
    funding_total = _nested_number(
        section,
        "funding_total_usdt",
        "funding_total",
    )
    fee_difference = _nested_number(
        section,
        "fee_reconciliation_abs_usdt",
        "fee_reconciliation",
    )
    funding_difference = _nested_number(
        section,
        "funding_reconciliation_abs_usdt",
        "funding_reconciliation",
    )
    slippage_difference = _nested_number(
        section,
        "slippage_reconciliation_abs_bps",
        "slippage_reconciliation_bps",
    )
    if fee_total is None or funding_total is None:
        return None, "cost reconciliation requires fee and funding totals"
    if fee_difference is None or fee_difference < 0.0:
        return None, "fee reconciliation difference is missing or invalid"
    if funding_difference is None or funding_difference < 0.0:
        return None, "funding reconciliation difference is missing or invalid"
    if slippage_difference is None or slippage_difference < 0.0:
        return None, "slippage reconciliation difference is missing or invalid"
    return {
        "fee_total_usdt": float(fee_total),
        "funding_total_usdt": float(funding_total),
        "fee_reconciliation_abs_usdt": float(fee_difference),
        "funding_reconciliation_abs_usdt": float(funding_difference),
        "slippage_reconciliation_abs_bps": float(slippage_difference),
    }, None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * float(percentile)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _typed_runtime_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate the typed accounting/market event contract.

    This is deliberately separate from the legacy runtime counters. Older
    logs remain readable, but no paper/live admission metric is promoted from
    them until every required typed surface is present and cross-checked.
    """

    errors: list[str] = []
    fills = [row for row in rows if str(row.get("kind", "")).strip().lower() == "order.filled"]
    fees: list[dict[str, Any]] = []
    slippages: list[dict[str, Any]] = []
    market_prices: list[dict[str, Any]] = []
    funding: list[dict[str, Any]] = []
    margins: list[dict[str, Any]] = []
    reconciliations: list[dict[str, Any]] = []
    cost_reconciliations: list[dict[str, Any]] = []

    for index, row in enumerate(fills):
        payload = _payload(row)
        fee, error = _typed_fee(payload)
        if error:
            errors.append(f"fill[{index}] {error}")
        else:
            assert fee is not None
            fees.append(fee)
        slippage, error = _typed_slippage(payload)
        if error:
            errors.append(f"fill[{index}] {error}")
        else:
            assert slippage is not None
            slippages.append(slippage)
        if any(key in payload for key in ("mark_price", "markPrice", "index_price", "indexPrice", "market")):
            prices, error = _typed_market_prices(payload)
            if error:
                errors.append(f"fill[{index}] {error}")
            else:
                assert prices is not None
                market_prices.append(prices)

    for row in rows:
        kind = str(row.get("kind", "")).strip().lower()
        payload = _payload(row)
        if kind in TYPED_MARKET_KINDS:
            prices, error = _typed_market_prices(payload)
            if error:
                errors.append(f"{kind} {error}")
            else:
                assert prices is not None
                market_prices.append(prices)
        if kind in TYPED_FUNDING_KINDS:
            item, error = _typed_funding(payload)
            if error:
                errors.append(f"{kind} {error}")
            else:
                assert item is not None
                funding.append(item)
        if kind in TYPED_MARGIN_KINDS:
            item, error = _typed_margin(payload)
            if error:
                errors.append(f"{kind} {error}")
            else:
                assert item is not None
                margins.append(item)
        if kind in TYPED_RECONCILIATION_KINDS:
            item, error = _typed_reconciliation(payload)
            if error:
                errors.append(f"{kind} {error}")
            else:
                assert item is not None
                reconciliations.append(item)
        if kind in COSTS_PROOF_KINDS:
            item, error = _typed_cost_reconciliation(payload)
            if error:
                errors.append(f"{kind} {error}")
            else:
                assert item is not None
                cost_reconciliations.append(item)

    if not fills:
        errors.append("typed fees/slippage require at least one order.filled event")
    if len(fees) != len(fills):
        errors.append("every order.filled event must contain a typed fee")
    if len(slippages) != len(fills):
        errors.append("every order.filled event must contain typed slippage")
    if not market_prices:
        errors.append("typed mark/index evidence is missing")
    if not funding:
        errors.append("typed funding evidence is missing")
    if not margins:
        errors.append("typed margin evidence is missing")
    if not reconciliations:
        errors.append("typed reconciliation evidence is missing")
    if not cost_reconciliations:
        errors.append("typed cost reconciliation evidence is missing")

    latest_costs = cost_reconciliations[-1] if cost_reconciliations else None
    fee_total = sum(item["amount"] for item in fees if item["currency"] == "USDT")
    funding_total = sum(item["amount"] for item in funding if item["currency"] == "USDT")
    if fees and len([item for item in fees if item["currency"] == "USDT"]) != len(fees):
        errors.append("typed fees must use USDT for the crypto paper contract")
    if funding and len([item for item in funding if item["currency"] == "USDT"]) != len(funding):
        errors.append("typed funding must use USDT for the crypto paper contract")
    if latest_costs is not None:
        if not math.isclose(
            latest_costs["fee_total_usdt"], fee_total, rel_tol=1e-6, abs_tol=1e-8
        ):
            errors.append("cost reconciliation fee total disagrees with fill events")
        if not math.isclose(
            latest_costs["funding_total_usdt"], funding_total, rel_tol=1e-6, abs_tol=1e-8
        ):
            errors.append("cost reconciliation funding total disagrees with funding events")
    slippage_values = [item["bps"] for item in slippages]
    reconciliation_max = max(
        (item["mismatch_count"] for item in reconciliations),
        default=1,
    )
    return {
        "schema_version": TYPED_EVIDENCE_SCHEMA_VERSION,
        "status": "passed" if not errors else "blocked",
        "complete": not errors,
        "errors": errors,
        "fills": len(fills),
        "typed_fee_count": len(fees),
        "typed_slippage_count": len(slippages),
        "market_snapshot_count": len(market_prices),
        "funding_event_count": len(funding),
        "margin_snapshot_count": len(margins),
        "reconciliation_event_count": len(reconciliations),
        "cost_reconciliation_count": len(cost_reconciliations),
        "fee_total_usdt": fee_total if fees and not errors else None,
        "funding_total_usdt": funding_total if funding and not errors else None,
        "fee_reconciliation_abs_usdt": latest_costs["fee_reconciliation_abs_usdt"] if latest_costs and not errors else None,
        "funding_reconciliation_abs_usdt": latest_costs["funding_reconciliation_abs_usdt"] if latest_costs and not errors else None,
        "slippage_reconciliation_abs_bps": latest_costs["slippage_reconciliation_abs_bps"] if latest_costs and not errors else None,
        "slippage_median_bps": _percentile(slippage_values, 0.50) if not errors else None,
        "slippage_p95_bps": _percentile(slippage_values, 0.95) if not errors else None,
        "slippage_max_bps": max(slippage_values, default=None) if not errors else None,
        "max_gross_exposure_ratio": max((item["gross_exposure_ratio"] for item in margins), default=None) if not errors else None,
        "max_used_margin_ratio": max((item["used_margin_ratio"] for item in margins), default=None) if not errors else None,
        "min_liquidation_distance": min((item["liquidation_distance"] for item in margins), default=None) if not errors else None,
        "max_reconciliation_mismatches": reconciliation_max if not errors else 1,
        "mark_index_basis_max_bps": max(
            abs(item["mark_price"] / item["index_price"] - 1.0) * 10_000.0
            for item in market_prices
        ) if market_prices else None,
    }


def _gap_hours(timestamps: Iterable[int], *, first_ts: int | None, last_ts: int | None) -> float:
    anchors = {int(value) for value in timestamps}
    if first_ts is not None:
        anchors.add(int(first_ts))
    if last_ts is not None:
        anchors.add(int(last_ts))
    ordered = sorted(anchors)
    if len(ordered) < 2:
        return 0.0
    return max(b - a for a, b in zip(ordered, ordered[1:])) / HOUR_MS


def _calendar_evidence(
    rows: list[dict[str, Any]],
    *,
    first_ts: int | None,
    last_ts: int | None,
) -> dict[str, Any]:
    observation_rows = [row for row in rows if _is_observation(row)]
    heartbeat_rows = [row for row in rows if _is_heartbeat(row)]
    observation_days = {
        _utc_day_bucket(int(row["ts"]))
        for row in observation_rows
        if _timestamp(row.get("ts")) is not None
    }
    heartbeat_days = {
        _utc_day_bucket(int(row["ts"]))
        for row in heartbeat_rows
        if _timestamp(row.get("ts")) is not None
    }
    if first_ts is None or last_ts is None:
        calendar_span_days = 0
    else:
        calendar_span_days = _utc_day_bucket(last_ts) - _utc_day_bucket(first_ts) + 1
    calendar_span_days = max(0, int(calendar_span_days))
    expected_day_buckets = (
        set(range(_utc_day_bucket(first_ts), _utc_day_bucket(last_ts) + 1))
        if first_ts is not None and last_ts is not None
        else set()
    )
    missing_observation_days = expected_day_buckets - observation_days
    missing_heartbeat_days = expected_day_buckets - heartbeat_days
    required_days = (
        max(1, int(math.ceil(calendar_span_days * MIN_CALENDAR_COVERAGE_RATIO)))
        if calendar_span_days
        else 0
    )
    observation_coverage_ratio = (
        len(observation_days) / calendar_span_days if calendar_span_days else 0.0
    )
    heartbeat_coverage_ratio = (
        len(heartbeat_days) / calendar_span_days if calendar_span_days else 0.0
    )
    heartbeat_timestamps = [int(row["ts"]) for row in heartbeat_rows]
    max_gap_hours = _gap_hours(
        heartbeat_timestamps,
        first_ts=first_ts,
        last_ts=last_ts,
    )
    has_rows = bool(rows) and calendar_span_days > 0
    calendar_coverage_passed = bool(
        has_rows
        and len(observation_days) >= required_days
        and observation_coverage_ratio >= MIN_CALENDAR_COVERAGE_RATIO
        and not missing_observation_days
    )
    heartbeat_passed = bool(
        has_rows
        and len(heartbeat_days) >= required_days
        and heartbeat_coverage_ratio >= MIN_HEARTBEAT_COVERAGE_RATIO
        and len(heartbeat_rows) >= required_days
        and max_gap_hours <= MAX_HEARTBEAT_GAP_HOURS
        and not missing_heartbeat_days
    )
    return {
        "calendar_span_days": calendar_span_days,
        "required_covered_days": required_days,
        "observation_count": len(observation_rows),
        "observation_calendar_days": len(observation_days),
        "missing_observation_days": len(missing_observation_days),
        "calendar_coverage_ratio": observation_coverage_ratio,
        "calendar_coverage_passed": calendar_coverage_passed,
        "heartbeat_count": len(heartbeat_rows),
        "heartbeat_calendar_days": len(heartbeat_days),
        "missing_heartbeat_days": len(missing_heartbeat_days),
        "heartbeat_coverage_ratio": heartbeat_coverage_ratio,
        "max_heartbeat_gap_hours": max_gap_hours,
        "heartbeat_gap_limit_hours": MAX_HEARTBEAT_GAP_HOURS,
        "heartbeat_passed": heartbeat_passed,
        "continuous_coverage_passed": bool(calendar_coverage_passed and heartbeat_passed),
    }


def _financial_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive only financial/runtime claims backed by typed event payloads."""

    uptime_values: list[float] = []
    recovery_values: list[float] = []
    net_result_values: list[float] = []
    equity_values: list[float] = []
    reconciliation_values: list[int] = []

    for row in rows:
        payload = _payload(row)
        kind = str(row.get("kind", "")).strip().lower()

        uptime = _nested_number(payload, "uptime_ratio", "uptime")
        if uptime is not None and 0.0 <= uptime <= 1.0:
            uptime_values.append(uptime)

        recovery_seconds = _nested_number(
            payload,
            "restart_recovery_seconds",
            "recovery_seconds",
        )
        recovery_ms = _nested_number(payload, "restart_recovery_ms", "recovery_ms")
        if recovery_seconds is not None and recovery_seconds >= 0.0:
            recovery_values.append(recovery_seconds)
        elif recovery_ms is not None and recovery_ms >= 0.0:
            recovery_values.append(recovery_ms / 1000.0)

        net_result = _nested_number(
            payload,
            "net_result",
            "realized_pnl",
            "realized_pnl_cumsum",
        )
        if net_result is not None:
            net_result_values.append(net_result)

        equity = _nested_number(payload, "equity")
        if equity is not None and equity > 0.0:
            equity_values.append(equity)

        reconciliation_value = _nested_number(
            payload,
            "reconciliation_mismatches",
            "mismatch_count",
            "mismatches",
        )
        if reconciliation_value is None:
            reconciliation_value = _nested_number(
                _typed_section(payload, "reconciliation"),
                "reconciliation_mismatches",
                "mismatch_count",
                "mismatches",
            )
        if reconciliation_value is not None and reconciliation_value >= 0.0:
            if kind.startswith(("reconcile.", "reconciliation.")) or "reconcil" in kind:
                reconciliation_values.append(int(reconciliation_value))
        elif (
            kind.startswith(("reconcile.", "reconciliation."))
            and payload.get("consistent") is True
        ):
            reconciliation_values.append(0)

    max_drawdown = None
    if len(equity_values) >= 2:
        peak = equity_values[0]
        drawdowns: list[float] = []
        for equity in equity_values:
            peak = max(peak, equity)
            drawdowns.append(max(0.0, (peak - equity) / peak))
        max_drawdown = max(drawdowns)

    return {
        "uptime_proven": bool(uptime_values),
        "derived_uptime_ratio": min(uptime_values) if uptime_values else None,
        "recovery_proven": bool(recovery_values),
        "derived_max_restart_recovery_seconds": max(recovery_values) if recovery_values else None,
        "pnl_proven": len(net_result_values) >= 1,
        "derived_net_result": net_result_values[-1] if net_result_values else None,
        "drawdown_proven": max_drawdown is not None,
        "derived_max_drawdown": max_drawdown,
        "reconciliation_proven": bool(reconciliation_values),
        "derived_reconciliation_mismatches": max(reconciliation_values) if reconciliation_values else None,
    }


def _runtime_event_flags(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive negative runtime claims from explicit event kinds only.

    The absence of an event is useful for a zero-count claim only after the
    event log itself has passed the integrity and continuity checks.  HSL
    safety is stricter: without an explicit HSL state/event surface it stays
    unproven and therefore fail-closed.
    """

    liquidation_count = 0
    hsl_event_count = 0
    hsl_emergency_count = 0
    terminal_stop_reached = False
    costs_proven = False
    fee_reconciliation_abs_usdt: float | None = None
    funding_reconciliation_abs_usdt: float | None = None

    for row in rows:
        kind = str(row.get("kind", "")).strip().lower()
        payload = _payload(row)
        payload_text = " ".join(
            str(payload.get(key, "")).strip().lower()
            for key in ("reason", "action", "state", "tier", "event", "status")
        )
        event_text = f"{kind} {payload_text}"

        if "liquidat" in event_text and "liquidation_distance" not in event_text:
            liquidation_count += 1

        if kind.startswith("hsl.") or payload.get("hsl") is not None:
            hsl_event_count += 1
            if any(
                marker in event_text
                for marker in ("emergency", "panic", "terminal", "red")
            ):
                hsl_emergency_count += 1

        if any(
            marker in event_text
            for marker in (
                "terminal_stop",
                "terminal stop",
                "no_restart",
                "no restart",
            )
        ):
            terminal_stop_reached = True

        if kind in COSTS_PROOF_KINDS and payload.get("all_costs_present") is True:
            fee_value = _nested_number(
                payload,
                "fee_reconciliation_abs_usdt",
                "fee_reconciliation",
            )
            funding_value = _nested_number(
                payload,
                "funding_reconciliation_abs_usdt",
                "funding_reconciliation",
            )
            if (
                fee_value is not None
                and fee_value >= 0.0
                and funding_value is not None
                and funding_value >= 0.0
            ):
                costs_proven = True
                fee_reconciliation_abs_usdt = fee_value
                funding_reconciliation_abs_usdt = funding_value

    return {
        "liquidation_events": liquidation_count,
        "hsl_event_count": hsl_event_count,
        "hsl_emergency_triggers": (
            hsl_emergency_count if hsl_event_count else None
        ),
        "terminal_stop_reached": terminal_stop_reached,
        "costs_proven": costs_proven,
        "fee_reconciliation_abs_usdt": fee_reconciliation_abs_usdt,
        "funding_reconciliation_abs_usdt": funding_reconciliation_abs_usdt,
    }


def _is_close_fill(row: Mapping[str, Any]) -> bool:
    payload = row.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    order_type = str(payload.get("pb_order_type", "")).lower()
    return bool(payload.get("reduce_only")) or "close" in order_type


def _has_event_identity(row: Mapping[str, Any]) -> bool:
    """Require the identity fields that make an event auditable and replayable."""

    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        return False
    strategy_id = payload.get("strategy_id")
    config_hash = payload.get("config_hash")
    cycle_id = payload.get("cycle_id")
    return (
        strategy_id is not None
        and bool(str(strategy_id).strip())
        and config_hash is not None
        and bool(str(config_hash).strip())
        and cycle_id is not None
        and not isinstance(cycle_id, bool)
        and bool(str(cycle_id).strip())
    )


def _count_unique(values: Iterable[str]) -> tuple[int, int]:
    seen: set[str] = set()
    duplicate_count = 0
    total = 0
    for value in values:
        total += 1
        if value in seen:
            duplicate_count += 1
        seen.add(value)
    return total, duplicate_count


def read_event_log(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read all event rows and return rows plus line-level evidence errors."""

    target = Path(path).expanduser().resolve()
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    if not target.exists() or not target.is_file():
        return rows, [f"missing event log: {target}"]
    try:
        with _open(target) as handle:
            for line_number, raw_line in enumerate(handle, 1):
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    errors.append(f"line {line_number}: invalid JSON ({type(exc).__name__})")
                    continue
                if not isinstance(value, dict):
                    errors.append(f"line {line_number}: event is not an object")
                    continue
                if _timestamp(value.get("ts")) is None:
                    errors.append(f"line {line_number}: missing or invalid ts")
                if not str(value.get("kind", "")).strip():
                    errors.append(f"line {line_number}: missing kind")
                rows.append(value)
    except (OSError, gzip.BadGzipFile) as exc:
        errors.append(f"cannot read event log: {type(exc).__name__}")
    return rows, errors


def summarize_runtime_events(path: str | Path) -> dict[str, Any]:
    """Create counters used by runtime gates without trusting metrics.json."""

    target = Path(path).expanduser().resolve()
    rows, errors = read_event_log(target)
    timestamps = [int(row["ts"]) for row in rows if _timestamp(row.get("ts")) is not None]
    kinds = [str(row.get("kind", "")) for row in rows]
    opened = [row for row in rows if row.get("kind") == "order.opened"]
    fills = [row for row in rows if row.get("kind") == "order.filled"]
    close_fills = [row for row in fills if _is_close_fill(row)]
    opened_ids = [
        event_id
        for row in opened
        if isinstance(row.get("payload"), Mapping)
        for event_id in [_event_id(row["payload"])]
        if event_id is not None
    ]
    _opened_id_total, duplicate_order_count = _count_unique(opened_ids)
    opened_id_count = len(set(opened_ids))
    closed_cycle_rows = [row for row in rows if row.get("kind") == "strategy.cycle_closed"]
    closed_cycle_ids = [
        cycle_id
        for row in closed_cycle_rows
        for cycle_id in [_cycle_id(row)]
        if cycle_id is not None
    ]
    _closed_cycle_total, _duplicate_cycle_count = _count_unique(closed_cycle_ids)
    unique_closed_cycle_count = len(set(closed_cycle_ids))
    cycle_identity_missing_events = len(closed_cycle_rows) - len(closed_cycle_ids)
    unique_event_fingerprints = {
        hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        for row in rows
    }
    unknown_position_count = sum(
        1
        for row in rows
        if row.get("kind") in {
            "position.unknown",
            "reconcile.unknown_position",
            "risk.unknown_position",
        }
    )
    first_ts = min(timestamps) if timestamps else None
    last_ts = max(timestamps) if timestamps else None
    identity_missing_events = sum(1 for row in rows if not _has_event_identity(row))
    elapsed_days = (
        (last_ts - first_ts) / DAY_MS
        if first_ts is not None and last_ts is not None
        else 0.0
    )
    calendar = _calendar_evidence(rows, first_ts=first_ts, last_ts=last_ts)
    financial = _financial_evidence(rows)
    runtime_flags = _runtime_event_flags(rows)
    typed_evidence = _typed_runtime_evidence(rows)
    file_sha256 = None
    try:
        file_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        errors.append("cannot hash event log")
    base_valid = bool(rows) and not errors and identity_missing_events == 0
    blockers: list[str] = []
    if not base_valid:
        blockers.append("event_log_integrity")
    if not calendar["calendar_coverage_passed"]:
        blockers.append("calendar_coverage")
    if not calendar["heartbeat_passed"]:
        blockers.append("heartbeat_observations")
    if cycle_identity_missing_events:
        blockers.append("cycle_identity")
    for metric_name in ("uptime", "recovery", "pnl", "drawdown", "reconciliation"):
        if not financial[f"{metric_name}_proven"]:
            blockers.append(f"{metric_name}_evidence")
    observability_evidence_complete = not blockers
    if typed_evidence["status"] != "passed":
        blockers.append("typed_evidence")
    runtime_evidence_complete = not blockers

    return {
        "schema_version": SCHEMA_VERSION,
        "path": str(target),
        "file_sha256": file_sha256,
        "status": "passed" if runtime_evidence_complete else "blocked",
        "verdict": {
            "status": "passed" if runtime_evidence_complete else "blocked",
            "blockers": blockers,
        },
        "runtime_evidence_complete": runtime_evidence_complete,
        "observability_evidence_complete": observability_evidence_complete,
        "errors": errors,
        "identity_complete": bool(rows) and identity_missing_events == 0,
        "identity_missing_events": identity_missing_events,
        "event_count": len(rows),
        "unique_event_count": len(unique_event_fingerprints),
        "first_event_ts_ms": first_ts,
        "last_event_ts_ms": last_ts,
        "elapsed_days": elapsed_days,
        "bot_start_events": sum(1 for kind in kinds if kind == "bot.start"),
        "bot_ready_events": sum(1 for kind in kinds if kind == "bot.ready"),
        "bot_stop_events": sum(1 for kind in kinds if kind == "bot.stop"),
        # Keep the legacy raw count, but expose the auditable unique count for
        # admission.  Duplicate order IDs must never satisfy a minimum-order
        # requirement.
        "planned_orders": len(opened),
        "unique_planned_orders": opened_id_count,
        "planned_order_identity_missing": len(opened) - len(opened_ids),
        "fills": len(fills),
        "unique_fills": len(
            {
                event_id
                for row in fills
                for event_id in [_event_id(_payload(row))]
                if event_id is not None
            }
        ),
        "close_fills": len(close_fills),
        # ``closed_cycles`` is now a unique business-cycle count.  The raw
        # event count remains available for diagnostics.
        "closed_cycles": unique_closed_cycle_count,
        "closed_cycle_events": len(closed_cycle_rows),
        "cycle_identity_missing_events": cycle_identity_missing_events,
        "duplicate_orders": duplicate_order_count,
        "unknown_positions": unknown_position_count,
        **runtime_flags,
        **calendar,
        **financial,
        "typed_evidence": typed_evidence,
    }


def runtime_gate_metrics(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return admission metrics proven by the event log.

    ``research_gates`` merges this mapping after loading ``metrics.json``.
    Therefore every metric checked by a paper/live gate must be returned here:
    values derived from the log are authoritative, while values without an
    event-backed proof are finite fail-closed values.  This prevents a caller
    from promoting a runtime by editing only ``metrics.json``.
    """

    observation_valid = bool(
        evidence.get("schema_version") == SCHEMA_VERSION
        and evidence.get("observability_evidence_complete") is True
        and evidence.get("continuous_coverage_passed") is True
        and evidence.get("missing_observation_days", 1) == 0
        and evidence.get("missing_heartbeat_days", 1) == 0
        and _safe_float(
            evidence.get("max_heartbeat_gap_hours", MAX_HEARTBEAT_GAP_HOURS + 1),
            MAX_HEARTBEAT_GAP_HOURS + 1,
        )
        <= MAX_HEARTBEAT_GAP_HOURS
    )
    valid = bool(
        observation_valid
        and evidence.get("status") == "passed"
        and isinstance(evidence.get("typed_evidence"), Mapping)
        and evidence["typed_evidence"].get("status") == "passed"
        and evidence["typed_evidence"].get("complete") is True
    )
    typed = evidence.get("typed_evidence")
    typed = typed if isinstance(typed, Mapping) else {}
    metrics: dict[str, Any] = {
        # Start with every value which is not currently derivable from the
        # event schema.  These entries intentionally overwrite any same-named
        # value supplied in metrics.json at the call site.
        **UNPROVEN_RUNTIME_METRICS,
        **UNPROVEN_RUNTIME_FLAGS,
        "events_present": valid,
        "runtime_evidence_status": evidence.get("status", "blocked"),
        "runtime_evidence_complete": valid,
        "days": _safe_float(evidence.get("elapsed_days", 0.0), 0.0) if observation_valid else 0.0,
        "planned_orders": _safe_int(evidence.get("unique_planned_orders", 0), 0) if observation_valid else 0,
        "fills": _safe_int(evidence.get("unique_fills", evidence.get("fills", 0)), 0) if observation_valid else 0,
        "closed_cycles": _safe_int(evidence.get("closed_cycles", 0), 0) if observation_valid else 0,
        "duplicate_orders": _safe_int(evidence.get("duplicate_orders", 0), 0) if observation_valid else 1,
        "unknown_positions": _safe_int(evidence.get("unknown_positions", 0), 0) if observation_valid else 1,
        "calendar_coverage_ratio": _safe_float(
            evidence.get("calendar_coverage_ratio", 0.0), 0.0
        ),
        "heartbeat_coverage_ratio": _safe_float(
            evidence.get("heartbeat_coverage_ratio", 0.0), 0.0
        ),
        "heartbeat_count": _safe_int(evidence.get("heartbeat_count", 0), 0),
        "max_heartbeat_gap_hours": _safe_float(
            evidence.get("max_heartbeat_gap_hours", 0.0), 0.0
        ),
        "required_covered_days": _safe_int(evidence.get("required_covered_days", 0), 0),
        "missing_observation_days": _safe_int(
            evidence.get("missing_observation_days", 0), 0
        ),
        "missing_heartbeat_days": _safe_int(
            evidence.get("missing_heartbeat_days", 0), 0
        ),
        "continuous_coverage_passed": bool(
            evidence.get("continuous_coverage_passed", False)
        ),
        "unique_event_count": int(evidence.get("unique_event_count", 0)),
        "typed_evidence_status": typed.get("status", "blocked"),
        "typed_evidence_errors": list(typed.get("errors", []))
        if isinstance(typed.get("errors", []), list)
        else ["typed evidence errors are not a list"],
    }
    if observation_valid:
        metrics.update(
            {
                "uptime_ratio": evidence.get("derived_uptime_ratio", 0.0),
                "max_restart_recovery_seconds": evidence.get(
                    "derived_max_restart_recovery_seconds", 61.0
                ),
                "net_result": evidence.get("derived_net_result", 0.0),
                "max_drawdown": evidence.get("derived_max_drawdown", 1.0),
                "reconciliation_mismatches": evidence.get(
                    "derived_reconciliation_mismatches", 1
                ),
                "liquidations": _safe_int(evidence.get("liquidation_events", 0), 0),
                "terminal_stop_reached": bool(
                    evidence.get("terminal_stop_reached", False)
                ),
            }
        )
        if evidence.get("costs_proven") is True and valid:
            metrics["all_costs_present"] = True
            fee_reconciliation = _finite_number(evidence.get("fee_reconciliation_abs_usdt"))
            funding_reconciliation = _finite_number(evidence.get("funding_reconciliation_abs_usdt"))
            if fee_reconciliation is not None and fee_reconciliation >= 0.0:
                metrics["fee_reconciliation_abs_usdt"] = fee_reconciliation
            if funding_reconciliation is not None and funding_reconciliation >= 0.0:
                metrics["funding_reconciliation_abs_usdt"] = funding_reconciliation
        metrics.update(
            {
                "typed_evidence_complete": bool(valid),
                "typed_fees_present": bool(valid),
                "typed_funding_present": bool(valid),
                "typed_slippage_present": bool(valid),
                "typed_mark_index_present": bool(valid),
                "typed_margin_present": bool(valid),
                "typed_reconciliation_present": bool(valid),
                "all_costs_present": bool(valid),
                "fee_reconciliation_abs_usdt": typed.get(
                    "fee_reconciliation_abs_usdt", UNPROVEN_RUNTIME_METRICS["fee_reconciliation_abs_usdt"]
                ) if valid else UNPROVEN_RUNTIME_METRICS["fee_reconciliation_abs_usdt"],
                "funding_reconciliation_abs_usdt": typed.get(
                    "funding_reconciliation_abs_usdt", UNPROVEN_RUNTIME_METRICS["funding_reconciliation_abs_usdt"]
                ) if valid else UNPROVEN_RUNTIME_METRICS["funding_reconciliation_abs_usdt"],
                "execution_slippage_median_bps": typed.get(
                    "slippage_median_bps", UNPROVEN_RUNTIME_METRICS["execution_slippage_median_bps"]
                ) if valid else UNPROVEN_RUNTIME_METRICS["execution_slippage_median_bps"],
                "execution_slippage_p95_bps": typed.get(
                    "slippage_p95_bps", UNPROVEN_RUNTIME_METRICS["execution_slippage_p95_bps"]
                ) if valid else UNPROVEN_RUNTIME_METRICS["execution_slippage_p95_bps"],
                "execution_slippage_max_bps": typed.get(
                    "slippage_max_bps", UNPROVEN_RUNTIME_METRICS["execution_slippage_max_bps"]
                ) if valid else UNPROVEN_RUNTIME_METRICS["execution_slippage_max_bps"],
                "execution_p95_bps": typed.get(
                    "slippage_p95_bps", UNPROVEN_RUNTIME_METRICS["execution_p95_bps"]
                ) if valid else UNPROVEN_RUNTIME_METRICS["execution_p95_bps"],
                "gross_exposure_ratio": typed.get(
                    "max_gross_exposure_ratio", UNPROVEN_RUNTIME_METRICS["gross_exposure_ratio"]
                ) if valid else UNPROVEN_RUNTIME_METRICS["gross_exposure_ratio"],
                "used_margin_ratio": typed.get(
                    "max_used_margin_ratio", UNPROVEN_RUNTIME_METRICS["used_margin_ratio"]
                ) if valid else UNPROVEN_RUNTIME_METRICS["used_margin_ratio"],
                "min_liquidation_distance": typed.get(
                    "min_liquidation_distance", UNPROVEN_RUNTIME_METRICS["min_liquidation_distance"]
                ) if valid else UNPROVEN_RUNTIME_METRICS["min_liquidation_distance"],
            }
        )
        hsl_triggers = evidence.get("hsl_emergency_triggers")
        if hsl_triggers is not None:
            try:
                hsl_triggers_int = int(hsl_triggers)
            except (TypeError, ValueError, OverflowError):
                hsl_triggers_int = 1
            metrics["hsl_emergency_triggers"] = max(0, hsl_triggers_int)
    return metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    evidence = summarize_runtime_events(args.events)
    evidence["derived_gate_metrics"] = runtime_gate_metrics(evidence)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "status": evidence["status"]}, sort_keys=True))
    return 0 if evidence["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
