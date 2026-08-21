"""Convert actual live/tester risk evidence into the parity decision schema.

This module is deliberately an adapter, not a second risk policy. The Python
live exporter has already run the production gate, and the Rust record has
already come from the real Backtest gate. The adapter only validates the
snapshot, derives the common evidence fields, and fails closed on incomplete
or blocked records.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

from research.risk_parity import (
    compare_decisions,
    make_decision,
    normalize_decision,
    normalize_snapshot,
    snapshot_hash,
)


class RiskTraceError(ValueError):
    """Raised when an engine trace cannot become safe parity evidence."""


def _required_text(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RiskTraceError(f"trace.{field} is required")
    return value.strip()


def _finite_number(record: Mapping[str, Any], field: str) -> float:
    value = record.get(field)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RiskTraceError(f"trace.{field} must be numeric") from exc
    if not math.isfinite(result):
        raise RiskTraceError(f"trace.{field} must be finite")
    return result


def _required_bool(record: Mapping[str, Any], field: str) -> bool:
    value = record.get(field)
    if not isinstance(value, bool):
        raise RiskTraceError(f"trace.{field} must be boolean")
    return value


def _normalise_trace_snapshot(
    record: Mapping[str, Any], snapshot: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    raw = snapshot if snapshot is not None else record.get("snapshot")
    if not isinstance(raw, Mapping):
        raise RiskTraceError("trace snapshot is missing")
    if raw.get("snapshot_status") == "blocked":
        raise RiskTraceError("trace snapshot is blocked")
    try:
        return normalize_snapshot(raw)
    except (TypeError, ValueError) as exc:
        raise RiskTraceError(f"trace snapshot is invalid: {exc}") from exc


def _common_risk_metrics(
    record: Mapping[str, Any], snapshot: Mapping[str, Any], side: str
) -> dict[str, Any]:
    hsl = snapshot.get("hsl", {}).get(side)
    if not isinstance(hsl, Mapping) or not isinstance(hsl.get("state"), str):
        raise RiskTraceError(f"snapshot.hsl.{side}.state is missing")
    return {
        "requested_notional": _finite_number(record, "requested_notional"),
        "daily_loss_ratio": snapshot["daily_loss"]["ratio"],
        "hsl_state": hsl["state"],
    }


def _semantic_identity_key(identity: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return identity fields excluding the occurrence suffix in order_id."""

    return (
        identity.get("symbol"),
        identity.get("position_side"),
        identity.get("order_side"),
        identity.get("strategy_order_type"),
        identity.get("execution_type"),
        bool(identity.get("reduce_only")),
        bool(identity.get("panic_close")),
        str(identity.get("qty")),
        str(identity.get("price")),
    )


def rust_trace_record_to_decision(
    record: Mapping[str, Any], *, snapshot: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Convert one real Rust ``RiskGateTraceRecord`` to ``risk-decision-v1``."""

    if not isinstance(record, Mapping):
        raise RiskTraceError("Rust trace record must be an object")
    decision = _required_text(record, "decision")
    if decision not in {"allow", "deny"}:
        raise RiskTraceError("trace.decision must be allow or deny")
    position_side = _required_text(record, "position_side").lower()
    order_side = _required_text(record, "order_side").lower()
    if position_side not in {"long", "short"}:
        raise RiskTraceError("trace.position_side must be long or short")
    if order_side not in {"buy", "sell"}:
        raise RiskTraceError("trace.order_side must be buy or sell")
    reason = _required_text(record, "reason")
    expected_reason = (
        "portfolio_risk_gate_allow"
        if decision == "allow"
        else "portfolio_risk_gate_deny"
    )
    if reason != expected_reason:
        raise RiskTraceError(
            f"trace.reason must be {expected_reason!r}, got {reason!r}"
        )
    canonical_snapshot = _normalise_trace_snapshot(record, snapshot)
    strategy_order_type = _required_text(record, "order_type").lower()
    execution_type = _required_text(record, "execution_type").lower()
    if execution_type not in {"limit", "market"}:
        raise RiskTraceError("trace.execution_type must be limit or market")
    reduce_only = _required_bool(record, "reduce_only")
    panic_close = _required_bool(record, "panic_close")
    identity = {
        "order_id": _required_text(record, "order_id"),
        "symbol": _required_text(record, "symbol"),
        "position_side": position_side,
        "order_side": order_side,
        "strategy_order_type": strategy_order_type,
        "execution_type": execution_type,
        "qty": abs(_finite_number(record, "qty")),
        "price": _finite_number(record, "price"),
        "reduce_only": reduce_only,
        "panic_close": panic_close,
    }
    return make_decision(
        decision=decision,
        reason=reason,
        side=position_side,
        order_identity=identity,
        risk_metrics=_common_risk_metrics(record, canonical_snapshot, position_side),
        snapshot_hash_value=snapshot_hash(canonical_snapshot),
        engine="rust-backtest",
    )


def live_export_to_decisions(export: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate actual Python live export and return its snapshot and decisions."""

    if not isinstance(export, Mapping) or export.get("status") != "passed":
        reason = export.get("reason", "live export is blocked") if isinstance(export, Mapping) else "live export is invalid"
        raise RiskTraceError(str(reason))
    raw_snapshot = export.get("snapshot")
    if not isinstance(raw_snapshot, Mapping):
        raise RiskTraceError("live export snapshot is missing")
    canonical_snapshot = normalize_snapshot(raw_snapshot)
    digest = snapshot_hash(canonical_snapshot)
    raw_decisions = export.get("decisions")
    if not isinstance(raw_decisions, Sequence) or isinstance(raw_decisions, (str, bytes)):
        raise RiskTraceError("live export decisions must be a list")
    decisions: list[dict[str, Any]] = []
    for index, raw_decision in enumerate(raw_decisions):
        try:
            decision = normalize_decision(raw_decision)
        except (TypeError, ValueError) as exc:
            raise RiskTraceError(f"live decision {index} is invalid: {exc}") from exc
        if decision["snapshot_hash"] != digest:
            raise RiskTraceError(f"live decision {index} is bound to another snapshot")
        decisions.append(decision)
    return canonical_snapshot, decisions


def compare_live_export_with_rust_trace(
    live_export: Mapping[str, Any],
    rust_record: Mapping[str, Any],
    *,
    decision_index: int = 0,
) -> dict[str, Any]:
    """Compare one actual live decision with one actual Rust trace record."""

    snapshot, live_decisions = live_export_to_decisions(live_export)
    if decision_index < 0 or decision_index >= len(live_decisions):
        raise RiskTraceError("live decision index is outside the exported decision list")
    rust_decision = rust_trace_record_to_decision(rust_record)
    return compare_decisions(live_decisions[decision_index], rust_decision, snapshot=snapshot)


def compare_live_export_with_rust_trace_records(
    live_export: Mapping[str, Any],
    rust_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare every decision from one actual engine state.

    A single trace record is useful for a smoke check, but it is not enough to
    establish parity: one engine could allow the first order and disagree on
    the next grid level.  This function matches records by the shared order
    identity, rejects duplicate or missing identities, and then compares the
    complete set fail-closed.
    """

    snapshot, live_decisions = live_export_to_decisions(live_export)
    if not isinstance(rust_records, Sequence) or isinstance(rust_records, (str, bytes)):
        raise RiskTraceError("Rust trace records must be a list")
    if not rust_records:
        raise RiskTraceError("Rust trace records must not be empty")

    live_by_id: dict[str, dict[str, Any]] = {}
    live_semantics: set[tuple[Any, ...]] = set()
    for decision in live_decisions:
        order_id = decision["order_identity"]["order_id"]
        if order_id in live_by_id:
            raise RiskTraceError(f"duplicate live order identity: {order_id}")
        semantic_key = _semantic_identity_key(decision["order_identity"])
        if semantic_key in live_semantics:
            raise RiskTraceError(
                "duplicate live semantic order identity; occurrence canonicalization is required"
            )
        live_semantics.add(semantic_key)
        live_by_id[order_id] = decision

    rust_by_id: dict[str, dict[str, Any]] = {}
    rust_semantics: set[tuple[Any, ...]] = set()
    for index, record in enumerate(rust_records):
        if not isinstance(record, Mapping):
            raise RiskTraceError(f"Rust trace record {index} is not an object")
        try:
            trace_snapshot = _normalise_trace_snapshot(record)
        except RiskTraceError as exc:
            raise RiskTraceError(f"Rust trace record {index} is invalid: {exc}") from exc
        if snapshot_hash(trace_snapshot) != snapshot_hash(snapshot):
            raise RiskTraceError(
                f"Rust trace record {index} is bound to a different snapshot"
            )
        decision = rust_trace_record_to_decision(record)
        order_id = decision["order_identity"]["order_id"]
        if order_id in rust_by_id:
            raise RiskTraceError(f"duplicate Rust order identity: {order_id}")
        semantic_key = _semantic_identity_key(decision["order_identity"])
        if semantic_key in rust_semantics:
            raise RiskTraceError(
                "duplicate Rust semantic order identity; occurrence canonicalization is required"
            )
        rust_semantics.add(semantic_key)
        rust_by_id[order_id] = decision

    missing_from_rust = sorted(set(live_by_id) - set(rust_by_id))
    missing_from_live = sorted(set(rust_by_id) - set(live_by_id))
    if missing_from_rust or missing_from_live:
        parts = []
        if missing_from_rust:
            parts.append("missing Rust decisions: " + ", ".join(missing_from_rust))
        if missing_from_live:
            parts.append("missing live decisions: " + ", ".join(missing_from_live))
        raise RiskTraceError("; ".join(parts))

    comparisons = []
    for order_id in sorted(live_by_id):
        comparisons.append(
            {
                "order_id": order_id,
                "comparison": compare_decisions(
                    live_by_id[order_id], rust_by_id[order_id], snapshot=snapshot
                ),
            }
        )
    failed = [item for item in comparisons if item["comparison"]["parity"] != "passed"]
    all_decisions = [item["comparison"]["decision"] for item in comparisons]
    first = comparisons[0]["comparison"]
    differences = []
    for item in failed:
        order_id = item["order_id"]
        for difference in item["comparison"].get("differences", []):
            differences.append(f"{order_id}:{difference}")
        if not item["comparison"].get("differences"):
            differences.append(order_id)
    return {
        "schema_version": first["schema_version"],
        "parity": "failed" if failed else "passed",
        "fail_closed": bool(failed),
        "decision": "deny" if failed or "deny" in all_decisions else "allow",
        "reason": (
            "one or more engine decisions differ; parity fails closed"
            if failed
            else "all engine decisions match"
        ),
        "side": first["side"] if len({item["comparison"]["side"] for item in comparisons}) == 1 else None,
        "order_identity": first["order_identity"] if len(comparisons) == 1 else None,
        "risk_metrics": {
            "record_count": len(comparisons),
            "failed_count": len(failed),
        },
        "snapshot_hash": snapshot_hash(snapshot),
        "differences": differences,
        "left_engine": "python-live-gate",
        "right_engine": "rust-backtest",
        "source_mode": "actual_engine_trace_set",
        "comparisons": comparisons,
        "candidate_admission": False,
        "trading_operations_count": 0,
    }


__all__ = [
    "RiskTraceError",
    "compare_live_export_with_rust_trace",
    "compare_live_export_with_rust_trace_records",
    "live_export_to_decisions",
    "rust_trace_record_to_decision",
]
