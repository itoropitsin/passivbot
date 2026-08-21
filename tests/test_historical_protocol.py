from __future__ import annotations

import json
from pathlib import Path

from config import load_prepared_config
from research.historical_protocol import (
    DEFAULT_SEEDS,
    PARAMETER_GROUPS,
    build_protocol_plan,
    execute_training_jobs,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONFIG = ROOT / "configs/examples/default_trailing_grid_long_npos7.json"


def _preflight(status: str = "blocked"):
    return {
        "schema_version": "crypto-historical-preflight-v1",
        "status": status,
        "assets": {
            asset: {
                "ready": status == "passed",
                "reasons": [] if status == "passed" else ["test preflight blocker"],
                "paths": {"execution_inputs": f"inputs/{asset.lower()}-execution.json"},
            }
            for asset in ("BTC", "ETH")
        },
    }


def test_protocol_is_blocked_without_passed_preflight_but_writes_full_work_plan(tmp_path):
    output = tmp_path / "plan.json"
    plan = build_protocol_plan(
        root=ROOT,
        config_path=SOURCE_CONFIG,
        preflight=_preflight(),
        output_path=output,
    )

    assert plan["status"] == "blocked"
    assert plan["execution"]["status"] == "blocked"
    assert plan["expected_counts"]["optimizer_jobs"] == 240
    assert plan["expected_counts"]["validation_templates"] == 16
    assert plan["expected_counts"]["neighbor_templates"] == 16
    assert plan["expected_counts"]["combined_templates"] == 8
    assert plan["expected_counts"]["holdout_templates"] == 4
    assert len(plan["optimizer"]["seeds"]) == 5
    assert set(plan["optimizer"]["seeds"]) == set(DEFAULT_SEEDS)
    assert all(job["max_changed_parameters"] <= 8 for job in plan["tasks"]["optimizer"])
    assert output.parent.joinpath("configs/bitget_btc_long_historical.json").exists()


def test_protocol_parameter_selectors_exist_and_generated_configs_obey_hard_limits(tmp_path):
    output = tmp_path / "plan.json"
    plan = build_protocol_plan(
        root=ROOT,
        config_path=SOURCE_CONFIG,
        preflight=_preflight(),
        output_path=output,
    )

    for group, suffixes in PARAMETER_GROUPS.items():
        assert len(suffixes) == 8
        for side in ("long", "short"):
            selectors = plan["inputs"]["generated_configs"][f"BTC:{side}"]["selectors"][group]
            assert selectors == [f"{side}_{suffix}" for suffix in suffixes]

    for config_path in sorted(output.parent.joinpath("configs").glob("*.json")):
        config = load_prepared_config(str(config_path), verbose=False, log_info=False)
        assert config["backtest"]["execution_model"]["enabled"] is True
        assert config["backtest"]["execution_model"]["funding_enabled"] is True
        assert config["live"]["portfolio_risk"]["enabled"] is True
        assert config["live"]["portfolio_risk"]["require_explicit_live_authorization"] is True
        assert config["live"]["hedge_mode"] is True
        assert config["live"]["margin_mode_preference"] == "isolated"
        assert config["live"]["leverage"] == 3.0
        for side in ("long", "short"):
            assert config["bot"][side]["entry_grid_max_levels"] == 8
            assert config["bot"][side]["entry_initial_qty_pct"] <= 0.03
            assert config["bot"][side]["entry_grid_double_down_factor"] <= 1.25
            assert config["bot"][side]["hsl_red_threshold"] == 0.20
            assert config["bot"][side]["hsl_no_restart_drawdown_threshold"] == 0.25


def test_blocked_protocol_never_starts_optimizer_processes(tmp_path):
    output = tmp_path / "plan.json"
    plan = build_protocol_plan(
        root=ROOT,
        config_path=SOURCE_CONFIG,
        preflight=_preflight(),
        output_path=output,
    )

    result = execute_training_jobs(plan, root=ROOT, output_path=output)

    assert result["status"] == "blocked"
    assert result["completed_jobs"] == []
    assert result["failed_jobs"] == []
    assert not output.parent.joinpath("optimizer_logs").exists()


def test_plan_json_is_serializable(tmp_path):
    output = tmp_path / "plan.json"
    plan = build_protocol_plan(
        root=ROOT,
        config_path=SOURCE_CONFIG,
        preflight=_preflight(),
        output_path=output,
    )
    output.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    assert json.loads(output.read_text(encoding="utf-8"))["protocol_id"].startswith("historical-protocol-")
