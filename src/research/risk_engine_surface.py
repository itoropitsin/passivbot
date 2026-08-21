"""Deterministic read-only coverage for the shared crypto risk boundary.

The existing actual probe checks one captured Backtest step.  This module
keeps a small, named matrix of risk states and sends every valid case through
the real Python and compiled Rust adapters.  It is local evidence only: it
does not contact an exchange and cannot admit a candidate or authorize live
trading.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from research.risk_adapters import (
    compare_adapters,
    python_live_decision,
    rust_tester_decision,
)
from research.risk_parity import SnapshotValidationError, fixture_case


SYMBOL = "BTC/USDT:USDT"


@dataclass(frozen=True)
class RiskSurfaceScenario:
    name: str
    snapshot: dict[str, Any]
    request: dict[str, Any] | None
    expected_decision: str | None
    expected_reason: str | None
    expected_blocked_field: str | None = None


def _initial() -> tuple[dict[str, Any], dict[str, Any]]:
    case = fixture_case("initial_entry")
    return deepcopy(case["snapshot"]), deepcopy(case["request"])


def _rename(request: dict[str, Any], order_id: str) -> dict[str, Any]:
    request = deepcopy(request)
    request["order_identity"] = dict(request["order_identity"])
    request["order_identity"]["order_id"] = order_id
    request["order_identity"]["client_order_id"] = order_id
    return request


def build_risk_surface_scenarios() -> tuple[RiskSurfaceScenario, ...]:
    """Return the immutable named matrix used by tests and the probe CLI."""

    snapshot, request = _initial()
    scenarios: list[RiskSurfaceScenario] = [
        RiskSurfaceScenario(
            "initial_entry", snapshot, request, "allow", "entry_within_risk_limits"
        )
    ]

    for name, mutation, reason in (
        ("daily_loss_stop", lambda value: value.update(ratio=0.02), "daily_loss_stop_reached"),
        ("hsl_orange", lambda value: value.update(state="orange"), "hsl_blocks_new_entry"),
        ("hsl_red", lambda value: value.update(state="red"), "hsl_blocks_new_entry"),
        (
            "hsl_terminal_no_restart",
            lambda value: value.update(state="terminal_stop_no_restart"),
            "hsl_blocks_new_entry",
        ),
    ):
        current_snapshot, current_request = _initial()
        if name == "daily_loss_stop":
            mutation(current_snapshot["daily_loss"])
        else:
            mutation(current_snapshot["hsl"]["long"])
        scenarios.append(
            RiskSurfaceScenario(
                name,
                current_snapshot,
                _rename(current_request, name),
                "deny",
                reason,
            )
        )

    current_snapshot, current_request = _initial()
    current_snapshot["leverage"]["long"] = 3.01
    scenarios.append(
        RiskSurfaceScenario(
            "leverage_above_roadmap_max",
            current_snapshot,
            _rename(current_request, "leverage-above-max"),
            "deny",
            "leverage_above_roadmap_max",
        )
    )

    max_exposure = fixture_case("max_notional")
    scenarios.append(
        RiskSurfaceScenario(
            "max_total_exposure",
            deepcopy(max_exposure["snapshot"]),
            _rename(max_exposure["request"], "max-exposure"),
            "deny",
            "max_total_exposure_exceeded",
        )
    )

    margin_snapshot, margin_request = _initial()
    margin_snapshot["positions"]["long"] = [
        {
            "symbol": SYMBOL,
            "qty": 7.0,
            "notional": 700.0,
            "entry_price": 100.0,
            "mark_price": 100.0,
            "liquidation_price": 50.0,
        }
    ]
    margin_snapshot["leverage"]["long"] = 2.0
    margin_request["qty"] = 0.2
    scenarios.append(
        RiskSurfaceScenario(
            "max_used_margin",
            margin_snapshot,
            _rename(margin_request, "max-margin"),
            "deny",
            "max_used_margin_exceeded",
        )
    )

    entry_buffer = fixture_case("liquidation_buffer")
    scenarios.append(
        RiskSurfaceScenario(
            "entry_low_liquidation_buffer",
            deepcopy(entry_buffer["snapshot"]),
            _rename(entry_buffer["request"], "entry-low-buffer"),
            "deny",
            "liquidation_buffer_below_entry_minimum",
        )
    )

    low_buffer = fixture_case("panic_close")
    low_buffer["snapshot"]["hsl"]["long"]["state"] = "normal"
    low_buffer["snapshot"]["daily_loss"]["ratio"] = 0.0
    low_buffer["snapshot"]["positions"]["long"][0]["liquidation_price"] = 85.0
    low_buffer["request"]["order_type"] = "limit"
    low_buffer["request"]["qty"] = 0.1
    low_buffer["request"]["price"] = 100.0
    low_buffer["request"]["reduce_only"] = True
    low_buffer["request"]["panic_close"] = False
    scenarios.append(
        RiskSurfaceScenario(
            "reduce_only_low_liquidation_buffer",
            deepcopy(low_buffer["snapshot"]),
            _rename(low_buffer["request"], "reduce-only-low-buffer"),
            "deny",
            "liquidation_buffer_below_reduce_only_minimum",
        )
    )

    ordinary_market_snapshot, ordinary_market_request = _initial()
    ordinary_market_request["order_type"] = "market"
    ordinary_market_request["panic_close"] = False
    scenarios.append(
        RiskSurfaceScenario(
            "ordinary_market_order",
            ordinary_market_snapshot,
            _rename(ordinary_market_request, "ordinary-market"),
            "deny",
            "ordinary_market_order_not_allowed",
        )
    )

    short_snapshot, short_request = _initial()
    short_request["order_identity"] = dict(short_request["order_identity"])
    short_request["order_identity"].update(
        {"order_id": "short-entry", "client_order_id": "short-entry", "position_side": "short", "order_side": "sell"}
    )
    scenarios.append(
        RiskSurfaceScenario(
            "short_initial_entry",
            short_snapshot,
            short_request,
            "allow",
            "entry_within_risk_limits",
        )
    )

    panic = fixture_case("panic_close")
    panic["snapshot"]["daily_loss"]["ratio"] = 0.02
    panic["snapshot"]["hsl"]["long"]["state"] = "red"
    scenarios.append(
        RiskSurfaceScenario(
            "panic_close_overrides_entry_stops",
            deepcopy(panic["snapshot"]),
            _rename(panic["request"], "panic-overrides-stops"),
            "allow",
            "panic_close_allowed",
        )
    )

    missing_tiers, missing_request = _initial()
    missing_tiers.pop("margin_tiers")
    scenarios.append(
        RiskSurfaceScenario(
            "missing_margin_tiers",
            missing_tiers,
            _rename(missing_request, "missing-tiers"),
            None,
            None,
            expected_blocked_field="margin_tiers",
        )
    )
    return tuple(scenarios)


def evaluate_risk_surface_scenario(scenario: RiskSurfaceScenario) -> dict[str, Any]:
    """Evaluate one case and return a machine-readable local verdict."""

    if scenario.expected_blocked_field:
        try:
            compare_adapters(scenario.snapshot, scenario.request or {})
        except SnapshotValidationError as exc:
            passed = scenario.expected_blocked_field in str(exc)
            return {
                "name": scenario.name,
                "status": "passed" if passed else "failed",
                "expected": {"blocked_field": scenario.expected_blocked_field},
                "observed": {"error": str(exc)},
                "trading_operations_count": 0,
            }
        return {
            "name": scenario.name,
            "status": "failed",
            "expected": {"blocked_field": scenario.expected_blocked_field},
            "observed": {"error": "missing required surface was accepted"},
            "trading_operations_count": 0,
        }

    try:
        comparison = compare_adapters(scenario.snapshot, scenario.request or {})
        left_decision = python_live_decision(scenario.snapshot, scenario.request or {})
        right_decision = rust_tester_decision(scenario.snapshot, scenario.request or {})
    except Exception as exc:  # pragma: no cover - evidence keeps unexpected failures visible
        return {
            "name": scenario.name,
            "status": "failed",
            "expected": {
                "decision": scenario.expected_decision,
                "reason": scenario.expected_reason,
            },
            "observed": {"error": f"{type(exc).__name__}: {exc}"},
            "trading_operations_count": 0,
        }
    passed = (
        comparison.get("parity") == "passed"
        and comparison.get("decision") == scenario.expected_decision
        and left_decision.get("decision") == scenario.expected_decision
        and right_decision.get("decision") == scenario.expected_decision
        and left_decision.get("reason") == scenario.expected_reason
        and right_decision.get("reason") == scenario.expected_reason
        and comparison.get("candidate_admission") is False
        and comparison.get("trading_operations_count") == 0
    )
    return {
        "name": scenario.name,
        "status": "passed" if passed else "failed",
        "expected": {
            "decision": scenario.expected_decision,
            "reason": scenario.expected_reason,
        },
        "observed": {
            "parity": comparison.get("parity"),
            "decision": comparison.get("decision"),
            "reason": comparison.get("reason"),
            "left_reason": left_decision.get("reason"),
            "right_reason": right_decision.get("reason"),
            "snapshot_hash": comparison.get("snapshot_hash"),
            "differences": comparison.get("differences", []),
        },
        "trading_operations_count": comparison.get("trading_operations_count", 0),
    }


def run_risk_surface_probe() -> dict[str, Any]:
    """Run every named case through both real local adapters."""

    results = [
        evaluate_risk_surface_scenario(scenario)
        for scenario in build_risk_surface_scenarios()
    ]
    failed = [item for item in results if item["status"] != "passed"]
    return {
        "schema_version": "risk-engine-surface-probe-v1",
        "source_mode": "research_policy",
        "adapter": "research.risk_adapters.python_live_decision",
        "fixture_mode": True,
        "status": "passed" if not failed else "failed",
        "scenario_count": len(results),
        "passed_count": len(results) - len(failed),
        "failed_count": len(failed),
        "read_only": True,
        "trading_operations_count": 0,
        "candidate_admission": False,
        "scenarios": results,
    }


__all__ = [
    "RiskSurfaceScenario",
    "build_risk_surface_scenarios",
    "evaluate_risk_surface_scenario",
    "run_risk_surface_probe",
]
