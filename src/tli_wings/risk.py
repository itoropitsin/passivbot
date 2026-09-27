"""Python boundary for the TLI portfolio guard implemented in Rust.

The caller must provide a complete snapshot.  This module never fills missing
values and never falls back to a Python copy of the decision logic.  Live and
backtest callers therefore use the same Rust function and the same JSON shape.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .runtime_policy import RuntimePolicy, validate_runtime_policy


class TliRiskError(ValueError):
    """Raised when the Rust risk boundary cannot evaluate a snapshot."""


def _policy_payload(policy: RuntimePolicy | Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_runtime_policy(policy)
    return validated.as_dict()


def _rust_module():
    try:
        import passivbot_rust
    except ImportError as exc:  # pragma: no cover - depends on installation
        raise TliRiskError(
            "the v8.1 Rust extension is unavailable; risk decisions are blocked"
        ) from exc
    evaluator = getattr(passivbot_rust, "evaluate_tli_portfolio_risk", None)
    if not callable(evaluator):
        raise TliRiskError(
            "the v8.1 Rust extension has no TLI risk evaluator; risk decisions are blocked"
        )
    return evaluator


def evaluate_tli_portfolio_risk(
    snapshot: Mapping[str, Any],
    policy: RuntimePolicy | Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate a complete snapshot through the shared Rust risk guard.

    Exposure, used margin, and daily loss are USDT amounts.  The policy's
    ratios are applied against its declared sizing balance cap in Rust.
    """

    if not isinstance(snapshot, Mapping):
        raise TliRiskError("TLI portfolio snapshot must be an object")
    try:
        snapshot_json = json.dumps(
            snapshot,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        policy_json = json.dumps(
            _policy_payload(policy),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TliRiskError(f"TLI risk input is not valid JSON: {exc}") from exc

    try:
        output = _rust_module()(snapshot_json, policy_json)
        decision = json.loads(output)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TliRiskError(f"TLI Rust risk evaluation failed: {exc}") from exc
    if not isinstance(decision, dict) or not isinstance(
        decision.get("actions"), list
    ) or not isinstance(decision.get("allow_new_entries"), bool):
        raise TliRiskError("TLI Rust risk evaluator returned an invalid decision")
    return decision


def empty_portfolio_snapshot() -> dict[str, Any]:
    """Return the explicit zero-action smoke input used by local preflight."""

    return {
        "positions": [],
        "used_margin_usdt": 0.0,
        "active_grids": 0,
        "daily_loss_usdt": 0.0,
        "actions": [],
    }


evaluate_risk = evaluate_tli_portfolio_risk


__all__ = [
    "TliRiskError",
    "empty_portfolio_snapshot",
    "evaluate_risk",
    "evaluate_tli_portfolio_risk",
]
