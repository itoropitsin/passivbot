from __future__ import annotations

import pytest

from research.risk_engine_surface import (
    build_risk_surface_scenarios,
    evaluate_risk_surface_scenario,
    run_risk_surface_probe,
)


@pytest.mark.parametrize(
    "scenario",
    build_risk_surface_scenarios(),
    ids=lambda scenario: scenario.name,
)
def test_named_risk_surface_scenario_is_fail_closed_and_parity_checked(scenario):
    result = evaluate_risk_surface_scenario(scenario)
    assert result["status"] == "passed", result
    assert result["trading_operations_count"] == 0


def test_risk_surface_probe_reports_complete_local_evidence():
    result = run_risk_surface_probe()
    assert result["status"] == "passed", result
    assert result["scenario_count"] == 14
    assert result["passed_count"] == 14
    assert result["failed_count"] == 0
    assert result["read_only"] is True
    assert result["candidate_admission"] is False
    assert result["trading_operations_count"] == 0
