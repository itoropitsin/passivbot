"""Apply the Rust TLI risk decision to an already planned v8 order batch."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .risk import evaluate_tli_portfolio_risk
from .runtime_policy import RuntimePolicy


class TliOrderGateError(ValueError):
    """Raised when an order batch lacks the explicit data needed for a decision."""


_REQUIRED_RISK_FIELDS = frozenset(
    {
        "side",
        "kind",
        "exposure_usdt",
        "margin_usdt",
        "liquidation_distance_pct",
        "leverage",
        "opens_grid",
    }
)


def _risk_action(order: Mapping[str, Any]) -> dict[str, Any]:
    risk = order.get("risk")
    if not isinstance(risk, Mapping):
        raise TliOrderGateError(
            "every planned order must carry an explicit risk object; no risk is inferred"
        )
    missing = sorted(_REQUIRED_RISK_FIELDS - risk.keys())
    if missing:
        raise TliOrderGateError(
            "planned order risk is incomplete: " + ", ".join(missing)
        )
    symbol = order.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise TliOrderGateError("every planned order must carry a symbol")
    return {
        "symbol": symbol,
        "side": risk["side"],
        "kind": risk["kind"],
        "exposure_usdt": risk["exposure_usdt"],
        "margin_usdt": risk["margin_usdt"],
        "liquidation_distance_pct": risk["liquidation_distance_pct"],
        "leverage": risk["leverage"],
        "opens_grid": risk["opens_grid"],
    }


def gate_planned_orders(
    orders: Iterable[Mapping[str, Any]],
    snapshot: Mapping[str, Any],
    policy: RuntimePolicy | Mapping[str, Any],
) -> dict[str, Any]:
    """Run the common Rust gate and return allowed and blocked order copies.

    The caller invokes this after the native v8 planner and before the exchange
    connector.  Unknown risk data is an error, while a rejected entry is simply
    omitted and protective actions remain in the allowed list.
    """

    materialized = list(orders)
    if any(not isinstance(order, Mapping) for order in materialized):
        raise TliOrderGateError("planned orders must be objects")
    actions = [_risk_action(order) for order in materialized]
    risk_snapshot = dict(snapshot)
    risk_snapshot["actions"] = actions
    decision = evaluate_tli_portfolio_risk(risk_snapshot, policy)
    if len(decision["actions"]) != len(materialized):
        raise TliOrderGateError("Rust risk decision length differs from order batch")
    allowed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for order, action_decision in zip(materialized, decision["actions"]):
        copy = dict(order)
        copy["tli_risk"] = action_decision
        (allowed if action_decision["allowed"] else blocked).append(copy)
    return {
        "allow_new_entries": decision["allow_new_entries"],
        "allowed_orders": allowed,
        "blocked_orders": blocked,
        "decision": decision,
    }


__all__ = ["TliOrderGateError", "gate_planned_orders"]
