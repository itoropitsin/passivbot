from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tli_wings.risk import TliRiskError, evaluate_tli_portfolio_risk
from tli_wings.runtime_policy import load_runtime_policy


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs" / "tli" / "runtime_policy.json"


def _snapshot(*, exposure: float = 0.0, kind: str = "initial_entry") -> dict:
    return {
        "positions": [],
        "used_margin_usdt": 0.0,
        "active_grids": 0,
        "daily_loss_usdt": 0.0,
        "actions": [
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "kind": kind,
                "exposure_usdt": exposure,
                "margin_usdt": 1.0,
                "liquidation_distance_pct": 0.30,
                "leverage": 3.0,
                "opens_grid": True,
            }
        ],
    }


def test_live_and_backtest_boundary_uses_the_same_rust_decision():
    policy = load_runtime_policy(POLICY_PATH)
    accepted = evaluate_tli_portfolio_risk(_snapshot(exposure=30.0), policy)
    rejected = evaluate_tli_portfolio_risk(_snapshot(exposure=30.01), policy)

    assert accepted["allow_new_entries"] is True
    assert accepted["actions"][0]["allowed"] is True
    assert rejected["allow_new_entries"] is False
    assert "initial_entry_limit" in rejected["actions"][0]["reasons"]


def test_protective_action_remains_visible_when_entry_is_rejected():
    policy = load_runtime_policy(POLICY_PATH)
    snapshot = _snapshot(exposure=30.01)
    snapshot["actions"].append(
        {
            "symbol": "BTCUSDT",
            "side": "long",
            "kind": "panic",
            "exposure_usdt": 1000.0,
            "margin_usdt": 1000.0,
            "liquidation_distance_pct": 0.15,
            "leverage": 3.0,
            "opens_grid": False,
        }
    )

    decision = evaluate_tli_portfolio_risk(snapshot, policy)

    assert decision["allow_new_entries"] is False
    assert decision["actions"][1]["allowed"] is True


def test_risk_boundary_rejects_unknown_or_missing_values():
    policy = load_runtime_policy(POLICY_PATH)
    snapshot = _snapshot(exposure=1.0)
    snapshot["actions"][0]["unexpected"] = 1
    with pytest.raises(TliRiskError, match="risk evaluation failed"):
        evaluate_tli_portfolio_risk(snapshot, policy)

    invalid = copy.deepcopy(snapshot)
    invalid["actions"][0].pop("leverage")
    with pytest.raises(TliRiskError, match="risk evaluation failed"):
        evaluate_tli_portfolio_risk(invalid, policy)
