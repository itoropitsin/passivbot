from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import tools.crypto_risk_grid as crypto_risk_grid
from tools.crypto_risk_grid import (
    SCENARIO_COVERAGE,
    SCENARIO_SPECS,
    validate_risk_grid_config,
    validate_roadmap_config,
)


def _roadmap_config() -> dict:
    return {
        "backtest": {
            "execution_model": {
                "enabled": True,
                "funding_enabled": True,
                "funding_missing_data": "blocked",
                "mark_price_basis": "mark",
                "adverse_same_candle_ordering": "worst_case",
                "partial_fill_ratios": [0.5, 1.0],
                "gap_stress_pct": 0.01,
            }
        },
        "live": {
            "portfolio_risk": {
                "enabled": True,
                "max_initial_entry_sizing_balance_pct": 0.03,
                "require_explicit_live_authorization": True,
            },
            "hedge_mode": True,
            "margin_mode_preference": "isolated",
            "leverage": 3,
            "market_orders_allowed": False,
        },
        "bot": {
            "long": {"entry_grid_max_levels": 8, "entry_initial_qty_pct": 0.03},
            "short": {"entry_grid_max_levels": 8, "entry_initial_qty_pct": 0.03},
        },
    }


ROADMAP_CONFIG = crypto_risk_grid.ROADMAP_CONFIG_PATH
ROADMAP_PROFILE = crypto_risk_grid.ROADMAP_PROFILE_PATH


def test_crypto_risk_grid_declares_both_executable_and_missing_scenarios():
    names = [name for name, _command, _toolchain in SCENARIO_SPECS]
    assert len(names) == 15
    assert "no_liquidations" in names
    assert "partial_fills" in names
    assert "funding_input_alignment" in names
    assert "symmetric_long_short_safety" in names
    assert "gap_and_multi_level_same_candle" in names
    assert "synchronized_btc_eth_safety" in names
    coverage_names = [item["name"] for item in SCENARIO_COVERAGE]
    assert len(coverage_names) == 14
    required_checks = {
        check_name
        for item in SCENARIO_COVERAGE
        for check_name in item["required_checks"]
    }
    assert required_checks <= set(names)
    assert all(item["required_checks"] for item in SCENARIO_COVERAGE)
    assert all(item["evidence"] for item in SCENARIO_COVERAGE)


def test_valid_roadmap_config_passes_but_does_not_admit_candidate():
    result = validate_roadmap_config(_roadmap_config())

    assert result["status"] == "passed"
    assert result["eligible_for_roadmap"] is True
    assert result["candidate_admission"] is False
    assert result["failed_checks"] == []
    assert result["trading_operations_count"] == 0
    assert result["read_only_external_operations"] is True


@pytest.mark.parametrize(
    ("path", "value", "check_id"),
    [
        (("live", "portfolio_risk", "enabled"), False, "portfolio_risk_enabled"),
        (
            ("live", "portfolio_risk", "max_initial_entry_sizing_balance_pct"),
            0.030001,
            "max_initial_entry_sizing_pct_at_most_0_03",
        ),
        (("live", "hedge_mode"), False, "hedge_mode_enabled"),
        (("live", "margin_mode_preference"), "cross", "isolated_margin_mode"),
        (("live", "leverage"), 10, "leverage_at_most_3"),
        (
            ("bot", "long", "entry_initial_qty_pct"),
            0.031,
            "long_entry_initial_qty_pct_at_most_0_03",
        ),
        (
            ("bot", "long", "entry_grid_max_levels"),
            9,
            "long_entry_grid_levels_at_most_8",
        ),
        (
            ("bot", "short", "entry_grid_max_levels"),
            500,
            "short_entry_grid_levels_at_most_8",
        ),
    ],
)
def test_roadmap_preflight_blocks_each_disallowed_configuration(
    path: tuple[str, ...], value: object, check_id: str
):
    config = _roadmap_config()
    target = config
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    result = validate_risk_grid_config(config, mode="roadmap")

    assert result["status"] == "failed"
    assert result["eligible_for_roadmap"] is False
    assert result["candidate_admission"] is False
    assert check_id in result["failed_checks"]


def test_test_mode_keeps_old_educational_config_out_of_roadmap():
    educational_config = {
        "live": {
            "portfolio_risk": {"enabled": False},
            "hedge_mode": False,
            "margin_mode_preference": "cross",
            "leverage": 10,
        },
        "bot": {
            "long": {"entry_grid_max_levels": 500},
            "short": {"entry_grid_max_levels": 500},
        },
    }

    result = validate_risk_grid_config(educational_config, mode="test")

    assert result["status"] == "test_only"
    assert result["config_class"] == "test_strategy"
    assert result["eligible_for_roadmap"] is False
    assert result["candidate_admission"] is False
    assert result["checks"] == []


def test_roadmap_fixture_is_the_input_not_the_old_validation_config():
    assert ROADMAP_CONFIG.is_file()
    assert ROADMAP_PROFILE.is_file()

    roadmap_config = json.loads(ROADMAP_CONFIG.read_text(encoding="utf-8"))
    roadmap_profile = json.loads(ROADMAP_PROFILE.read_text(encoding="utf-8"))
    config_result = validate_roadmap_config(roadmap_config)
    profile_result = crypto_risk_grid.validate_roadmap_profile(roadmap_profile)

    assert config_result["status"] == "passed", config_result
    assert profile_result["status"] == "passed", profile_result
    assert config_result["checks"]
    assert profile_result["checks"]

    old_validation_config = Path(
        "configs/validation/bitget_hedge_short_smoke_2026-06.json"
    )
    old_result = validate_roadmap_config(
        json.loads(old_validation_config.read_text(encoding="utf-8"))
    )
    assert old_result["status"] == "failed"
    assert "isolated_margin_mode" in old_result["failed_checks"]


def test_account_profile_fixture_is_not_accepted_as_public_market_profile():
    account_profile = json.loads(
        (
            Path("analysis_reports/crypto_bot_roadmap/inputs/")
            / "bitget_btc_market_profile_read_only.json"
        ).read_text(encoding="utf-8")
    )

    result = crypto_risk_grid.validate_roadmap_profile(account_profile)

    assert result["status"] == "failed"
    assert "public_market_profile_schema" in result["failed_checks"]
    assert "profile_source_hashes_present" in result["failed_checks"]


def test_roadmap_run_defaults_to_config_and_profile_and_records_all_evidence(
    tmp_path, monkeypatch
):
    def fake_run_check(name, command, toolchain, *, timeout_seconds):
        return {
            "name": name,
            "passed": True,
            "exit_code": 0,
            "command": list(command),
            "toolchain": list(toolchain),
            "output_sha256": f"{name}-evidence",
            "output_tail": "deterministic test evidence",
        }

    monkeypatch.setattr(crypto_risk_grid, "_run_check", fake_run_check)
    output_path = tmp_path / "risk-grid-result.json"
    result = crypto_risk_grid.run(
        ["--config-mode", "roadmap", "--output", str(output_path)]
    )

    assert result["config_preflight"]["status"] == "passed"
    assert result["profile_preflight"]["status"] == "passed"
    assert result["config_path"] == str(ROADMAP_CONFIG.resolve())
    assert result["config_preflight"]["sha256"]
    assert len(result["checks"]) == len(SCENARIO_SPECS)
    assert len(result["scenario_coverage"]) == 14
    assert result["scenario_summary"] == {
        "passed": 14,
        "failed": 0,
        "blocked": 0,
        "not_run": 0,
        "total": 14,
    }
    assert result["status"] == "passed"
    assert result["scenario_coverage_complete"] is True
    assert all(
        check["input_evidence"]["config"]["sha256"] for check in result["checks"]
    )
    assert all(item["evidence_complete"] for item in result["scenario_coverage"])


def _coverage_item(name: str) -> dict:
    return next(item for item in SCENARIO_COVERAGE if item["name"] == name)


def test_gap_and_multi_level_same_candle_has_engine_evidence():
    config = json.loads(ROADMAP_CONFIG.read_text(encoding="utf-8"))
    profile = json.loads(ROADMAP_PROFILE.read_text(encoding="utf-8"))
    assert validate_roadmap_config(config)["status"] == "passed"
    assert crypto_risk_grid.validate_roadmap_profile(profile)["status"] == "passed"
    execution_model = config["backtest"]["execution_model"]

    assert execution_model["enabled"] is True
    assert execution_model["adverse_same_candle_ordering"] == "worst_case"
    assert execution_model["gap_stress_pct"] > 0
    assert config["bot"]["long"]["entry_grid_max_levels"] == 8
    scenario = _coverage_item("gap_and_multi_level_same_candle")
    assert scenario["coverage_status"] == "covered"
    assert scenario["missing"] == ()
    assert "backtest.rs" in scenario["evidence"][0]


def test_synchronized_btc_eth_safety_has_engine_evidence():
    config = json.loads(ROADMAP_CONFIG.read_text(encoding="utf-8"))
    profile = json.loads(ROADMAP_PROFILE.read_text(encoding="utf-8"))
    assert validate_roadmap_config(config)["status"] == "passed"
    assert crypto_risk_grid.validate_roadmap_profile(profile)["status"] == "passed"
    assert config["live"]["portfolio_risk"]["max_total_exposure_ratio"] <= 0.8
    assert profile["symbol"] == "BTCUSDT"
    scenario = _coverage_item("synchronized_btc_eth_safety")
    assert scenario["coverage_status"] == "covered"
    assert scenario["missing"] == ()
    assert "backtest.rs" in scenario["evidence"][0]


def test_roadmap_preflight_does_not_mutate_config():
    config = _roadmap_config()
    before = copy.deepcopy(config)

    validate_roadmap_config(config)

    assert config == before


def test_invalid_roadmap_preflight_stops_scenario_runner(tmp_path, monkeypatch):
    config = _roadmap_config()
    config["live"]["portfolio_risk"]["enabled"] = False
    config_path = tmp_path / "invalid-roadmap-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "risk-grid-result.json"

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("scenario checks must not run after failed roadmap preflight")

    monkeypatch.setattr(crypto_risk_grid, "_run_check", fail_if_called)

    result = crypto_risk_grid.run(
        [
            "--config",
            str(config_path),
            "--config-mode",
            "roadmap",
            "--output",
            str(output_path),
        ]
    )

    assert result["status"] == "failed"
    assert result["config_preflight"]["status"] == "failed"
    assert result["checks"] == []
    assert result["candidate_admission"] is False
    assert result["trading_operations_count"] == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "failed"


def test_test_mode_keeps_candidate_admission_disabled(tmp_path, monkeypatch):
    config_path = tmp_path / "educational-config.json"
    config_path.write_text(json.dumps({"live": {"leverage": 10}}), encoding="utf-8")
    output_path = tmp_path / "risk-grid-result.json"

    monkeypatch.setattr(
        crypto_risk_grid,
        "_run_check",
        lambda name, command, toolchain, *, timeout_seconds: {
            "name": name,
            "passed": True,
            "command": list(command),
            "exit_code": 0,
            "output_sha256": "test",
            "output_tail": "",
        },
    )

    result = crypto_risk_grid.run(
        [
            "--config",
            str(config_path),
            "--config-mode",
            "test",
            "--output",
            str(output_path),
        ]
    )

    assert result["status"] == "passed"
    assert result["config_preflight"]["status"] == "test_only"
    assert result["roadmap_config_eligible"] is False
    assert result["candidate_admission"] is False
    assert result["scenario_coverage_complete"] is True
    assert len(result["checks"]) == len(SCENARIO_SPECS)


def test_scenario_coverage_promotes_only_executed_engine_evidence():
    checks = [
        {
            "name": name,
            "passed": True,
            "exit_code": 0,
            "command": [],
            "output_sha256": "test",
            "output_tail": "",
        }
        for name, _command, _toolchain in SCENARIO_SPECS
    ]
    coverage = crypto_risk_grid._build_scenario_coverage(checks, checks_ran=True)
    assert all(item["status"] == "passed" for item in coverage)
    summary = crypto_risk_grid._scenario_summary(coverage)
    assert summary["blocked"] == 0
    assert summary["passed"] == 14
