import gzip
import json
from pathlib import Path

import pytest

from research.experiment_manifest import (
    EVENT_REQUIRED_STAGES,
    apply_stage_prerequisite_gate,
    build_experiment_manifest,
    evaluate_candidate_gates,
    profile_input_metrics,
    replay_events_present,
    sha256_path,
    write_run_artifacts,
)
from research.risk_policy import (
    CryptoRiskLimits,
    capped_sizing_balance,
    drawdown_action,
    validate_grid_controls,
)
from tools.research_gates import (
    _load_metrics_for_stage,
    migrate_status_stage_names,
    refresh_stale_status,
    verify_run_integrity,
    write_run_integrity_record,
)
from tools.research_gates import (
    main as research_gates_main,
)


def test_shadow_gate_loads_machine_result_without_manual_metric_copy(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "evidence_schema_version": "crypto-strategy-shadow-evidence-v1",
                "candidate_admission": False,
                "read_only": True,
                "external_network_calls": 0,
                "private_requests": 0,
                "trading_operations_count": 0,
                "public_write_calls": 0,
                "readiness_metrics": {
                    "shadow_duration_hours": 24.0,
                    "shadow_planned_orders": 20,
                    "shadow_restart_count": 5,
                    "shadow_max_restart_recovery_seconds": 30.0,
                    "shadow_duplicate_orders": 0,
                    "shadow_unknown_positions": 0,
                    "shadow_private_calls": 0,
                    "shadow_order_creates": 0,
                    "shadow_transfers": 0,
                    "shadow_events_present": True,
                    "shadow_determinism_runs": 3,
                },
                "deterministic_replay": {"status": "passed"},
                "provenance": {"input": {"source_sha256": "abc"}, "artifacts": {}},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    metrics = _load_metrics_for_stage(result_path, stage="shadow_readiness")

    assert metrics["shadow_duration_hours"] == 24.0
    assert metrics["shadow_evidence_status"] == "passed"
    assert metrics["shadow_result_sha256"] == sha256_path(result_path)
    assert metrics["shadow_input_provenance_present"] is True
    assert metrics["shadow_artifact_provenance_present"] is False
    assert metrics["shadow_artifact_hashes_match"] is False
    assert evaluate_candidate_gates(metrics, stage="shadow_readiness")["status"] == "failed"


def test_shadow_gate_verifies_provenance_artifact_hashes(tmp_path):
    artifact = tmp_path / "events.ndjson"
    artifact.write_text("event\n", encoding="utf-8")
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "evidence_schema_version": "crypto-strategy-shadow-evidence-v1",
                "readiness_metrics": {},
                "deterministic_replay": {"status": "passed"},
                "provenance": {
                    "input": {"source_sha256": "abc"},
                    "artifacts": {
                        "events": {
                            "path": str(artifact),
                            "sha256": sha256_path(artifact),
                        }
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    metrics = _load_metrics_for_stage(result_path, stage="shadow_readiness")
    assert metrics["shadow_artifact_hashes_match"] is True

    artifact.write_text("changed\n", encoding="utf-8")
    changed = _load_metrics_for_stage(result_path, stage="shadow_readiness")
    assert changed["shadow_artifact_hashes_match"] is False


def test_shadow_gate_rejects_non_mapping_artifact_provenance(tmp_path):
    for index, artifacts in enumerate((["events.ndjson"], "events.ndjson", 1)):
        result_path = tmp_path / f"result-{index}.json"
        result_path.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "evidence_schema_version": "crypto-strategy-shadow-evidence-v1",
                    "readiness_metrics": {},
                    "deterministic_replay": {"status": "passed"},
                    "provenance": {
                        "input": {"source_sha256": "abc"},
                        "artifacts": artifacts,
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        metrics = _load_metrics_for_stage(result_path, stage="shadow_readiness")

        assert metrics["shadow_artifact_provenance_present"] is True
        assert metrics["shadow_artifact_hashes_match"] is False


def _valid_shadow_market_profile() -> dict:
    return {
        "schema_version": "bitget-public-market-profile-v1",
        "exchange": "bitget",
        "symbol": "BTCUSDT",
        "category": "USDT-FUTURES",
        "read_only_only": True,
        "trading_operations_count": 0,
        "instrument": {},
        "funding": {},
        "mark_and_index": {},
        "margin_tiers": [{"tier": "1"}],
        "source_response_sha256": {"tickers": "hash"},
    }


def _shadow_result_with_profile_binding(path: Path) -> Path:
    result_path = path.parent / f"result-{path.stem}.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "evidence_schema_version": "crypto-strategy-shadow-evidence-v1",
                "readiness_metrics": {},
                "deterministic_replay": {"status": "passed"},
                "provenance": {
                    "input": {"source_sha256": "abc"},
                    "artifacts": {},
                    "market_profile": {
                        "path": str(path),
                        "sha256": sha256_path(path),
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return result_path


def test_shadow_readiness_rejects_invalid_or_unbound_market_profile(tmp_path):
    invalid_profile = tmp_path / "invalid-profile.json"
    invalid_profile.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
    invalid_result = _shadow_result_with_profile_binding(invalid_profile)
    invalid_metrics = _load_metrics_for_stage(invalid_result, stage="shadow_readiness")
    invalid_metrics.update(
        profile_input_metrics("shadow_readiness", market_profile_path=invalid_profile)
    )
    invalid_metrics["shadow_market_profile_contract_valid"] = invalid_metrics[
        "market_profile_contract_valid"
    ]
    invalid_metrics["shadow_market_profile_binding_matches_input"] = True
    invalid_verdict = evaluate_candidate_gates(invalid_metrics, stage="shadow_readiness")
    invalid_gate = next(
        gate
        for gate in invalid_verdict["gates"]
        if gate["id"] == "shadow_readiness.market_profile_contract_valid"
    )
    assert invalid_gate["status"] == "failed"

    bound_profile = tmp_path / "bound-profile.json"
    bound_profile.write_text(
        json.dumps(_valid_shadow_market_profile(), sort_keys=True), encoding="utf-8"
    )
    other_profile = tmp_path / "other-profile.json"
    other_profile.write_text(
        json.dumps({**_valid_shadow_market_profile(), "symbol": "ETHUSDT"}, sort_keys=True),
        encoding="utf-8",
    )
    unbound_result = _shadow_result_with_profile_binding(other_profile)
    unbound_metrics = _load_metrics_for_stage(unbound_result, stage="shadow_readiness")
    unbound_metrics.update(
        profile_input_metrics("shadow_readiness", market_profile_path=bound_profile)
    )
    unbound_metrics["shadow_market_profile_contract_valid"] = unbound_metrics[
        "market_profile_contract_valid"
    ]
    unbound_metrics["shadow_market_profile_binding_matches_input"] = (
        unbound_metrics["shadow_market_profile_binding_sha256"] == sha256_path(bound_profile)
    )
    unbound_verdict = evaluate_candidate_gates(unbound_metrics, stage="shadow_readiness")
    unbound_gate = next(
        gate
        for gate in unbound_verdict["gates"]
        if gate["id"] == "shadow_readiness.market_profile_binding_matches_input"
    )
    assert unbound_metrics["market_profile_contract_valid"] is True
    assert unbound_gate["status"] == "failed"


def test_shadow_readiness_requires_nonempty_event_input(tmp_path):
    assert "shadow_readiness" in EVENT_REQUIRED_STAGES
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "evidence_schema_version": "crypto-strategy-shadow-evidence-v1",
                "readiness_metrics": {},
                "deterministic_replay": {"status": "passed"},
                "provenance": {"input": {}, "artifacts": {}},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    metrics = _load_metrics_for_stage(result_path, stage="shadow_readiness")
    metrics["shadow_events_input_present"] = replay_events_present(None)
    metrics["shadow_events_present"] = replay_events_present(None)
    verdict = evaluate_candidate_gates(metrics, stage="shadow_readiness")
    input_gate = next(
        gate
        for gate in verdict["gates"]
        if gate["id"] == "shadow_readiness.events_input_present"
    )
    assert input_gate["status"] == "failed"


def _metrics(**overrides):
    metrics = {
        "max_drawdown": 0.08,
        "liquidations": 0,
        "max_recovery_days": 12,
        "gross_exposure_ratio": 0.55,
        "used_margin_ratio": 0.20,
        "min_liquidation_distance": 0.40,
        "execution_slippage_median_bps": 1.0,
        "execution_slippage_p95_bps": 5.0,
        "execution_slippage_max_bps": 12.0,
        "max_daily_loss": 0.01,
        "reconciliation_mismatches": 0,
        "positive_validation_windows": 4,
        "validation_windows": 4,
        "independent_optimizer_runs": 5,
        "sides_evaluated_independently": True,
        "neighbor_checks_complete": True,
        "holdout_reserved": True,
        "execution_stresses_complete": True,
        "closed_cycles_per_window_min": 25,
        "stress_min_equity_ratio": 0.80,
        "neighbor_profitable_ratio": 0.90,
        "events_present": True,
        "net_result": 10.0,
        "market_profile_input_present": True,
        "market_profile_public_api_only": True,
        "market_profile_private_api_data_present": False,
    }
    metrics.update(overrides)
    return metrics


def test_risk_policy_uses_exact_plan_boundaries():
    assert capped_sizing_balance(1_250.0) == 1_000.0
    assert capped_sizing_balance(800.0) == 800.0
    assert drawdown_action(0.0999) == "normal"
    assert drawdown_action(0.10) == "warning"
    assert drawdown_action(0.15) == "close_only"
    assert drawdown_action(0.20) == "emergency_close"
    assert drawdown_action(0.25) == "terminal_stop_no_restart"
    validate_grid_controls(max_levels=8, initial_entry_pct=0.03, double_down_factor=1.25)
    with pytest.raises(ValueError):
        validate_grid_controls(max_levels=9, initial_entry_pct=0.03, double_down_factor=1.25)


def test_crypto_risk_policy_keeps_initial_entry_cap_at_three_percent():
    with pytest.raises(ValueError):
        CryptoRiskLimits(max_initial_entry_pct=0.030001)


def test_historical_verdict_passes_only_when_all_metrics_pass():
    verdict = evaluate_candidate_gates(_metrics(), stage="historical")
    assert verdict["status"] == "passed"
    failed = evaluate_candidate_gates(_metrics(neighbor_profitable_ratio=0.79), stage="historical")
    assert failed["status"] == "failed"
    assert any(gate["id"] == "candidate.neighbor_stability" for gate in failed["gates"])


def test_historical_verdict_requires_the_planned_research_protocol():
    metrics = _metrics(independent_optimizer_runs=4)
    verdict = evaluate_candidate_gates(metrics, stage="historical")
    assert verdict["status"] == "failed"
    assert any(
        gate["id"] == "candidate.independent_optimizer_runs"
        for gate in verdict["gates"]
    )
    incomplete = _metrics(holdout_reserved=False)
    assert evaluate_candidate_gates(incomplete, stage="historical")["status"] == "failed"


def test_risk_contract_requires_real_engine_parity_evidence():
    research_only = {
        "risk_contract_implemented": True,
        "risk_snapshot_contract_present": True,
        "parity_harness_present": True,
        "live_tester_decisions_identical": True,
        "risk_limits_enforced_in_live_and_tester": True,
        "risk_parity_reached": True,
        "risk_contract_fails_closed_on_mismatch": True,
        "risk_model_surface_validated": True,
    }
    assert evaluate_candidate_gates(research_only, stage="risk_contract")["status"] == "blocked"

    complete = {
        **research_only,
        "risk_surface_matrix_status": "passed",
        "risk_surface_matrix_validated": True,
        "risk_surface_matrix_artifact_bound": True,
        "risk_surface_matrix_source_mode": "research_policy",
        "risk_surface_matrix_fixture_mode": True,
        "risk_surface_matrix_scenario_count": 14,
        "risk_surface_matrix_passed_count": 14,
        "risk_surface_matrix_failed_count": 0,
        "risk_production_evidence_status": "passed",
        "risk_production_evidence_manifest_present": True,
        "risk_production_evidence_contract_valid": True,
        "risk_production_path_validated": True,
        "risk_production_python_live_gate_called": True,
        "risk_production_python_live_gate_method": (
            "Passivbot._apply_crypto_portfolio_risk_limits"
        ),
        "risk_production_python_live_gate_engine": "python-live-gate",
        "risk_production_rust_backtest_called": True,
        "risk_production_rust_trace_present": True,
        "risk_production_fixture_mode": False,
        "risk_production_research_policy_used": False,
        "risk_production_candidate_admission": False,
        "risk_production_trading_operations_count": 0,
        "risk_production_transfers_count": 0,
        "risk_production_account_changes_count": 0,
        "risk_production_probe_bound": True,
        "risk_production_snapshot_bound": True,
        "risk_production_config_bound": True,
        "risk_production_market_profile_bound": True,
        "risk_production_code_fingerprint_bound": True,
        "risk_production_report_bound": True,
        "risk_production_surface_matrix_bound": True,
    }
    assert evaluate_candidate_gates(complete, stage="risk_contract")["status"] == "passed"

    scaffold_only = dict(complete, risk_parity_reached=False)
    verdict = evaluate_candidate_gates(scaffold_only, stage="risk_contract")
    assert verdict["status"] == "failed"
    assert any(
        gate["id"] == "risk_contract.risk_parity_reached"
        for gate in verdict["gates"]
    )


def test_risk_grid_requires_roadmap_config_and_complete_scenario_coverage():
    metrics = {
        "market_profile_input_present": True,
        "market_profile_public_api_only": True,
        "market_profile_private_api_data_present": False,
        "roadmap_config_eligible": True,
        "scenario_coverage_complete": True,
        "no_liquidations": True,
        "no_duplicate_orders": True,
        "limits_not_bypassed": True,
        "state_recovery_consistent": True,
        "api_outage_safe": True,
        "manual_position_safe": True,
        "ordinary_market_orders_blocked": True,
    }
    assert evaluate_candidate_gates(metrics, stage="risk_grid")["status"] == "passed"
    incomplete = evaluate_candidate_gates(
        dict(metrics, scenario_coverage_complete=False), stage="risk_grid"
    )
    assert incomplete["status"] == "failed"


def test_stage_prerequisites_block_promotion_after_a_failed_gate():
    gated = apply_stage_prerequisite_gate(
        evaluate_candidate_gates(
            {
                "days": 90,
                "planned_orders": 100,
                "closed_cycles": 30,
                "uptime_ratio": 1.0,
                "max_restart_recovery_seconds": 1,
                "duplicate_orders": 0,
                "unknown_positions": 0,
                "all_costs_present": True,
                "net_result": 1.0,
                "max_drawdown": 0.01,
                "liquidations": 0,
                "max_recovery_days": 1,
                "gross_exposure_ratio": 0.5,
                "used_margin_ratio": 0.2,
                "min_liquidation_distance": 0.4,
                "execution_slippage_median_bps": 1,
                "execution_slippage_p95_bps": 5,
                "execution_slippage_max_bps": 10,
                "max_daily_loss": 0.01,
                "reconciliation_mismatches": 0,
            },
            stage="paper",
        ),
        {
            "stages": {
                "baseline": {"status": "passed"},
                "profile": {"status": "failed"},
                "data": {"status": "failed"},
                "risk_grid": {"status": "not_started"},
                "historical": {"status": "not_started"},
            }
        },
        stage="paper",
    )
    assert gated["status"] == "blocked"
    prerequisite_gate = next(gate for gate in gated["gates"] if gate["id"] == "stage.prerequisites")
    assert prerequisite_gate["status"] == "blocked"


def test_status_migration_removes_legacy_stage_aliases_without_promoting_results():
    migrated = migrate_status_stage_names(
        {
            "schema_version": "crypto-grid-roadmap-status-v1",
            "stages": {
                "baseline": {"status": "passed"},
                "profile": {"status": "failed", "run_id": "old-profile"},
                "paper": {"status": "blocked", "run_id": "old-paper"},
            },
        }
    )

    stages = migrated["stages"]
    assert migrated["schema_version"] == "crypto-grid-roadmap-status-v3"
    assert "profile" not in stages
    assert "paper" not in stages
    assert stages["account_profile"]["status"] == "failed"
    assert stages["paper_runtime"]["status"] == "blocked"
    assert stages["market_profile"]["status"] == "not_started"
    assert stages["risk_contract"]["status"] == "not_started"
    assert stages["baseline"]["check_status"] == "passed"
    assert stages["baseline"]["stage_admission_status"] == "passed"
    assert stages["baseline"]["outcome"] == "passed"
    assert stages["baseline"]["freshness"] == "not_applicable"


def test_v3_keeps_check_result_when_prerequisite_blocks_admission():
    check = evaluate_candidate_gates(
        {
            "days": 90,
            "planned_orders": 100,
            "closed_cycles": 30,
            "uptime_ratio": 1.0,
            "max_restart_recovery_seconds": 1,
            "duplicate_orders": 0,
            "unknown_positions": 0,
            "all_costs_present": True,
            "net_result": 1.0,
            "max_drawdown": 0.01,
            "liquidations": 0,
            "max_recovery_days": 1,
            "gross_exposure_ratio": 0.5,
            "used_margin_ratio": 0.2,
            "min_liquidation_distance": 0.4,
            "execution_slippage_median_bps": 1,
            "execution_slippage_p95_bps": 5,
            "execution_slippage_max_bps": 10,
            "max_daily_loss": 0.01,
            "reconciliation_mismatches": 0,
            "events_present": True,
            "market_profile_input_present": True,
            "market_profile_public_api_only": True,
            "market_profile_private_api_data_present": False,
        },
        stage="paper_runtime",
    )
    gated = apply_stage_prerequisite_gate(
        check,
        {
            "schema_version": "crypto-grid-roadmap-status-v3",
            "stages": {
                "baseline": {"status": "passed", "stage_admission_status": "passed"},
                "market_profile": {"status": "failed", "stage_admission_status": "failed"},
            },
        },
        stage="paper_runtime",
    )
    assert check["status"] == "passed"
    assert gated["check_status"] == "passed"
    assert gated["stage_admission_status"] == "blocked"
    assert gated["outcome"] == "blocked"
    assert any(gate["id"] == "stage.prerequisites" for gate in gated["gates"])


def test_stale_status_propagates_to_passed_dependents():
    refreshed = refresh_stale_status(
        {
            "stages": {
                "baseline": {"status": "stale"},
                "market_profile": {"status": "passed"},
                "data": {"status": "passed"},
            }
        },
        runs_root=Path(__file__).resolve().parents[1] / "analysis_reports" / "crypto_bot_roadmap" / "runs",
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert refreshed["stages"]["market_profile"]["status"] == "stale"
    assert refreshed["stages"]["data"]["status"] == "stale"


def test_v3_refresh_clears_old_blockers_from_not_started_stage():
    refreshed = refresh_stale_status(
        {
            "schema_version": "crypto-grid-roadmap-status-v3",
            "stages": {
                "baseline": {"status": "passed", "stage_admission_status": "passed"},
                "account_risk_surface": {
                    "status": "blocked",
                    "check_status": "not_started",
                    "stage_admission_status": "blocked",
                    "outcome": "blocked",
                    "freshness": "not_applicable",
                    "admission_blockers": ["baseline"],
                },
            },
        },
        runs_root=Path(__file__).resolve().parents[1] / "analysis_reports" / "crypto_bot_roadmap" / "runs",
        repo_root=Path(__file__).resolve().parents[1],
    )

    entry = refreshed["stages"]["account_risk_surface"]
    assert entry["status"] == "not_started"
    assert entry["stage_admission_status"] == "not_started"
    assert entry["outcome"] == "not_started"
    assert "admission_blockers" not in entry


def test_profile_verdict_requires_read_only_account_contract():
    fields = (
        "account_profile_input_present",
        "account_contract_read_only",
        "read_only_check",
        "account_mode_uta",
        "position_mode_hedge",
        "margin_mode_isolated",
        "collateral_usdt",
        "borrowing_disabled",
        "withdrawals_disabled",
        "ip_allowlist_enabled",
        "separate_long_short_leverage",
        "raw_responses_hashed",
        "trading_operations_count_zero",
    )
    verdict = evaluate_candidate_gates({field: True for field in fields}, stage="account_profile")
    assert verdict["status"] == "passed"
    incomplete = {field: True for field in fields[:-1]}
    assert evaluate_candidate_gates(incomplete, stage="profile")["status"] == "blocked"


def test_market_profile_rejects_private_account_material(tmp_path):
    public_profile = tmp_path / "market-public.json"
    public_profile.write_text(
        json.dumps({"exchange": "bitget", "symbol": "BTCUSDT", "source": "public_api"}),
        encoding="utf-8",
    )
    public_metrics = profile_input_metrics("market_profile", market_profile_path=public_profile)
    assert public_metrics["market_profile_public_api_only"] is True
    assert evaluate_candidate_gates(public_metrics, stage="market_profile")["status"] == "passed"

    private_profile = tmp_path / "market-contaminated.json"
    private_profile.write_text(
        json.dumps({"account_controls": {"withdrawals_enabled": False}}),
        encoding="utf-8",
    )
    private_metrics = profile_input_metrics("market_profile", market_profile_path=private_profile)
    assert private_metrics["market_profile_private_api_data_present"] is True
    verdict = evaluate_candidate_gates(private_metrics, stage="market_profile")
    assert verdict["status"] == "failed"
    assert any(gate["id"] == "market_profile.private_api_data_absent" for gate in verdict["gates"])


def test_public_data_does_not_require_account_profile():
    data_metrics = {
        "market_profile_input_present": True,
        "market_profile_public_api_only": True,
        "market_profile_private_api_data_present": False,
        "history_through_plan_date": True,
        "ohlcv_cache_quality_complete": True,
        "funding_history_complete": True,
        "mark_price_history_complete": True,
        "position_tiers_captured": True,
        "execution_model_implemented": True,
        "history_coverage_ratio": 1.0,
        "unmarked_synthetic_rows": 0,
        "nan_rows": 0,
        "future_data_violations": 0,
        "funding_known_coverage_ratio": 1.0,
        "identical_run_count": 3,
    }
    verdict = evaluate_candidate_gates(data_metrics, stage="data")
    assert verdict["status"] == "passed"
    gated = apply_stage_prerequisite_gate(
        verdict,
        {"stages": {"baseline": {"status": "passed"}, "market_profile": {"status": "passed"}}},
        stage="data",
    )
    assert gated["status"] == "passed"


def test_cli_public_data_run_does_not_require_account_profile(tmp_path):
    config = tmp_path / "config.json"
    dataset = tmp_path / "dataset"
    profile = tmp_path / "market-profile.json"
    metrics_path = tmp_path / "metrics.json"
    output_dir = tmp_path / "runs"
    config.write_text("{}\n", encoding="utf-8")
    dataset.mkdir()
    (dataset / "BTC.jsonl").write_text("2026-01-01,1\n", encoding="utf-8")
    profile.write_text('{"exchange":"bitget","source":"public_api"}\n', encoding="utf-8")
    metrics_path.write_text(
        json.dumps(
            {
                "history_through_plan_date": True,
                "ohlcv_cache_quality_complete": True,
                "funding_history_complete": True,
                "mark_price_history_complete": True,
                "position_tiers_captured": True,
                "execution_model_implemented": True,
                "history_coverage_ratio": 1.0,
                "unmarked_synthetic_rows": 0,
                "nan_rows": 0,
                "future_data_violations": 0,
                "funding_known_coverage_ratio": 1.0,
                "identical_run_count": 3,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "stages": {
                    "baseline": {"status": "passed"},
                    "market_profile": {"status": "passed"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = research_gates_main(
        [
            "--run-id",
            "public-data-only",
            "--stage",
            "data",
            "--metrics",
            str(metrics_path),
            "--config",
            str(config),
            "--dataset",
            str(dataset),
            "--market-profile",
            str(profile),
            "--output-dir",
            str(output_dir),
            "--repo-root",
            str(Path(__file__).resolve().parents[1]),
        ]
    )
    assert result == 0
    assert (output_dir / "public-data-only" / "market_profile.json").exists()
    assert not (output_dir / "public-data-only" / "account_profile.json").exists()


def test_technical_live_requires_account_profile():
    metrics = _metrics(
        days=30,
        fills=50,
        closed_cycles=10,
        max_drawdown=0.01,
        fee_reconciliation_abs_usdt=0.0,
        funding_reconciliation_abs_usdt=0.0,
        execution_p95_bps=5.0,
        order_confirmation_p95_seconds=1.0,
        hsl_emergency_triggers=0,
        account_profile_input_present=True,
        account_contract_read_only=True,
    )
    verdict = evaluate_candidate_gates(metrics, stage="technical_live")
    status = {
        "stages": {
            "baseline": {"status": "passed"},
            "market_profile": {"status": "passed"},
            "data": {"status": "passed"},
            "risk_contract": {"status": "passed"},
            "risk_grid": {"status": "passed"},
            "historical": {"status": "passed"},
            "paper_runtime": {"status": "passed"},
        }
    }
    gated = apply_stage_prerequisite_gate(verdict, status, stage="technical_live")
    assert gated["status"] == "blocked"
    assert any(
        blocker["stage"] == "account_profile" and blocker["status"] == "not_started"
        for gate in gated["gates"]
        if gate["id"] == "stage.prerequisites"
        for blocker in gate["blockers"]
    )


def test_manifest_and_checksums_keep_account_profile_separate(tmp_path):
    config = tmp_path / "config.json"
    dataset = tmp_path / "dataset"
    market_profile = tmp_path / "market-profile.json"
    account_profile = tmp_path / "account-profile.json"
    config.write_text("{}\n", encoding="utf-8")
    dataset.mkdir()
    (dataset / "BTC.jsonl").write_text("2026-01-01,1\n", encoding="utf-8")
    market_profile.write_text('{"source":"public_api"}\n', encoding="utf-8")
    account_profile.write_text(
        json.dumps(
            {
                "read_only_only": True,
                "trading_operations_count": 0,
                "write_operations_count": 0,
                "normalized": {"account_mode": "uta"},
            }
        ),
        encoding="utf-8",
    )
    manifest = build_experiment_manifest(
        repo_root=Path(__file__).resolve().parents[1],
        run_id="technical-profile-separation",
        stage="technical_live",
        config_path=config,
        dataset_path=dataset,
        market_profile_path=market_profile,
        account_profile_path=account_profile,
        command=("pytest",),
    )
    inputs = manifest.to_dict()["inputs"]
    assert inputs["market_profile_sha256"] == sha256_path(market_profile)
    assert inputs["account_profile_sha256"] == sha256_path(account_profile)
    environment = manifest.to_dict()["environment"]
    assert environment["python"]
    assert "rust_extension_sha256" in environment
    run_dir = write_run_artifacts(
        run_dir=tmp_path / "runs" / "technical-profile-separation",
        manifest=manifest,
        metrics={},
        verdict={"stage": "technical_live", "status": "blocked", "gates": []},
        config_path=config,
        market_profile_path=market_profile,
        account_profile_path=account_profile,
    )
    write_run_integrity_record(run_dir)
    assert verify_run_integrity(run_dir)["status"] == "passed"


def test_risk_contract_run_can_be_profile_free(tmp_path):
    config = tmp_path / "config.json"
    dataset = tmp_path / "dataset"
    config.write_text("{}\n", encoding="utf-8")
    dataset.mkdir()
    (dataset / "BTC.jsonl").write_text("2026-01-01,1\n", encoding="utf-8")
    manifest = build_experiment_manifest(
        repo_root=Path(__file__).resolve().parents[1],
        run_id="risk-contract-no-profile",
        stage="risk_contract",
        config_path=config,
        dataset_path=dataset,
        market_profile_path=None,
        command=("pytest",),
    )
    run_dir = write_run_artifacts(
        run_dir=tmp_path / "runs" / "risk-contract-no-profile",
        manifest=manifest,
        metrics={},
        verdict={"stage": "risk_contract", "status": "blocked", "gates": []},
        config_path=config,
        market_profile_path=None,
    )
    write_run_integrity_record(run_dir)
    assert verify_run_integrity(run_dir)["status"] == "passed"
    assert not (run_dir / "market_profile.json").exists()
    assert not (run_dir / "account_profile.json").exists()


@pytest.mark.parametrize("status", ["stale", "failed", "blocked"])
def test_non_passed_prerequisite_statuses_never_pass(status):
    verdict = evaluate_candidate_gates(
        {
            "market_profile_input_present": True,
            "market_profile_public_api_only": True,
            "market_profile_private_api_data_present": False,
            "history_through_plan_date": True,
            "ohlcv_cache_quality_complete": True,
            "funding_history_complete": True,
            "mark_price_history_complete": True,
            "position_tiers_captured": True,
            "execution_model_implemented": True,
            "history_coverage_ratio": 1.0,
            "unmarked_synthetic_rows": 0,
            "nan_rows": 0,
            "future_data_violations": 0,
            "funding_known_coverage_ratio": 1.0,
            "identical_run_count": 3,
        },
        stage="data",
    )
    gated = apply_stage_prerequisite_gate(
        verdict,
        {
            "stages": {
                "baseline": {"status": "passed"},
                "market_profile": {"status": status},
            }
        },
        stage="data",
    )
    assert gated["status"] == "blocked"


def test_data_verdict_fails_when_history_or_execution_inputs_are_stale():
    metrics = {
        "history_through_plan_date": False,
        "funding_history_complete": False,
        "mark_price_history_complete": False,
        "position_tiers_captured": False,
        "execution_model_implemented": False,
    }
    verdict = evaluate_candidate_gates(metrics, stage="data")
    assert verdict["status"] == "failed"
    assert any(gate["id"] == "data.history_through_plan_date" for gate in verdict["gates"])


def test_missing_metric_blocks_instead_of_using_a_default():
    metrics = _metrics()
    del metrics["execution_slippage_max_bps"]
    verdict = evaluate_candidate_gates(metrics, stage="historical")
    assert verdict["status"] == "blocked"
    blocked = [gate for gate in verdict["gates"] if gate["status"] == "blocked"]
    assert blocked[0]["metric"] == "execution_slippage_max_bps"


def test_manifest_and_run_bundle_are_content_addressed(tmp_path):
    config = tmp_path / "config.json"
    dataset = tmp_path / "dataset"
    profile = tmp_path / "market_profile.json"
    metrics = _metrics()
    config.write_text('{"capital":1000}\n', encoding="utf-8")
    dataset.mkdir()
    (dataset / "BTC.jsonl").write_text("2026-01-01,1\n", encoding="utf-8")
    profile.write_text('{"symbol":"BTCUSDT"}\n', encoding="utf-8")
    manifest = build_experiment_manifest(
        repo_root=Path(__file__).resolve().parents[1],
        run_id="run-1",
        stage="historical",
        config_path=config,
        dataset_path=dataset,
        market_profile_path=profile,
        command=("pytest", "-q"),
        random_seeds=(1, 2, 3),
        created_at_utc="2026-08-17T00:00:00+00:00",
    )
    assert manifest.to_dict()["inputs"]["config_sha256"] == sha256_path(config)
    run_dir = write_run_artifacts(
        run_dir=tmp_path / "runs" / "run-1",
        manifest=manifest,
        metrics=metrics,
        verdict=evaluate_candidate_gates(metrics, stage="historical"),
        config_path=config,
        market_profile_path=profile,
    )
    for name in (
        "manifest.json",
        "metrics.json",
        "verdict.json",
        "report.md",
        "events.jsonl.gz",
        "config.json",
        "market_profile.json",
    ):
        assert (run_dir / name).exists()
    with gzip.open(run_dir / "events.jsonl.gz", "rb") as events:
        assert events.read() == b""
    with pytest.raises(FileExistsError):
        write_run_artifacts(
            run_dir=run_dir,
            manifest=manifest,
            metrics=metrics,
            verdict=evaluate_candidate_gates(metrics, stage="historical"),
            config_path=config,
            market_profile_path=profile,
        )


def test_run_bundle_can_include_replay_events(tmp_path):
    config = tmp_path / "config.json"
    dataset = tmp_path / "dataset"
    profile = tmp_path / "market_profile.json"
    events = tmp_path / "events.jsonl"
    config.write_text("{}\n", encoding="utf-8")
    dataset.mkdir()
    (dataset / "BTC.jsonl").write_text("2026-01-01,1\n", encoding="utf-8")
    profile.write_text("{}\n", encoding="utf-8")
    events.write_text('{"event":"fill","cycle_id":"c1"}\n', encoding="utf-8")
    manifest = build_experiment_manifest(
        repo_root=Path(__file__).resolve().parents[1],
        run_id="run-events",
        stage="historical",
        config_path=config,
        dataset_path=dataset,
        market_profile_path=profile,
        command=("pytest",),
    )
    run_dir = write_run_artifacts(
        run_dir=tmp_path / "runs" / "run-events",
        manifest=manifest,
        metrics=_metrics(),
        verdict=evaluate_candidate_gates(_metrics(), stage="historical"),
        config_path=config,
        market_profile_path=profile,
        events_path=events,
    )
    with gzip.open(run_dir / "events.jsonl.gz", "rt", encoding="utf-8") as handle:
        assert handle.read() == events.read_text(encoding="utf-8")


def test_replay_events_presence_requires_a_non_empty_file(tmp_path):
    assert replay_events_present(None) is False
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert replay_events_present(empty) is False
    events = tmp_path / "events.jsonl"
    events.write_text('{"event":"fill"}\n', encoding="utf-8")
    assert replay_events_present(events) is True


def test_runtime_gate_overwrites_claimed_time_and_event_counters(tmp_path):
    config = tmp_path / "config.json"
    dataset = tmp_path / "dataset"
    profile = tmp_path / "market_profile.json"
    metrics_path = tmp_path / "metrics.json"
    events = tmp_path / "events.ndjson"
    output_dir = tmp_path / "runs"
    config.write_text("{}\n", encoding="utf-8")
    dataset.mkdir()
    (dataset / "BTC.jsonl").write_text("2026-01-01,1\n", encoding="utf-8")
    profile.write_text("{}\n", encoding="utf-8")
    identity = {
        "strategy_id": "test:strategy",
        "cycle_id": 0,
        "config_hash": "test-config-hash",
    }
    rows = [
        {
            "ts": 0,
            "kind": "bot.start",
            "payload": {
                **identity,
                "uptime_ratio": 1.0,
                "recovery_seconds": 1.0,
                "equity": 1000.0,
                "net_result": 0.0,
            },
        }
    ]
    rows.extend(
        {
            "ts": day * 24 * 60 * 60 * 1000,
            "kind": "account.balance",
            "payload": {
                **identity,
                "uptime_ratio": 1.0,
                "equity": 1000.0 + float(day),
                **({"net_result": 1.0} if day == 90 else {}),
            },
        }
        for day in range(1, 91)
    )
    rows.extend(
        {
            "ts": index + 1,
            "kind": "order.opened",
            "payload": {**identity, "custom_id": f"order-{index}"},
        }
        for index in range(100)
    )
    rows.extend(
        {
            "ts": 90 * 24 * 60 * 60 * 1000,
            "kind": "strategy.cycle_closed",
            "payload": {**identity, "cycle_id": index + 1},
        }
        for index in range(30)
    )
    rows.append(
        {
            "ts": 90 * 24 * 60 * 60 * 1000,
            "kind": "reconciliation.check",
            "payload": {**identity, "mismatch_count": 0},
        }
    )
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    metrics_path.write_text(
        json.dumps(
            {
                "days": 1,
                "planned_orders": 1,
                "closed_cycles": 1,
                "uptime_ratio": 1.0,
                "max_restart_recovery_seconds": 1,
                "duplicate_orders": 0,
                "unknown_positions": 0,
                "all_costs_present": True,
                "net_result": 1.0,
                "max_drawdown": 0.01,
                "liquidations": 0,
                "max_recovery_days": 1,
                "gross_exposure_ratio": 0.5,
                "used_margin_ratio": 0.2,
                "min_liquidation_distance": 0.4,
                "execution_slippage_median_bps": 1,
                "execution_slippage_p95_bps": 5,
                "execution_slippage_max_bps": 10,
                "max_daily_loss": 0.01,
                "reconciliation_mismatches": 0,
            }
        ),
        encoding="utf-8",
    )
    status = {
        "stages": {
            stage: {"status": "passed"}
            for stage in (
                "baseline",
                "market_profile",
                "data",
                "risk_contract",
                "risk_grid",
                "historical",
            )
        }
    }
    (tmp_path / "status.json").write_text(json.dumps(status), encoding="utf-8")

    # The event log proves the time and order counters, but it contains no
    # fill-level fee/funding evidence.  The v2 runtime contract must therefore
    # reject the run even though metrics.json claims that costs are present.
    assert (
        research_gates_main(
            [
                "--run-id",
                "paper-runtime-evidence",
                "--stage",
                "paper",
                "--metrics",
                str(metrics_path),
                "--config",
                str(config),
                "--dataset",
                str(dataset),
                "--market-profile",
                str(profile),
                "--events",
                str(events),
                "--output-dir",
                str(output_dir),
                "--repo-root",
                str(Path(__file__).resolve().parents[1]),
            ]
        )
        == 1
    )
    run_metrics = json.loads(
        (output_dir / "paper-runtime-evidence" / "metrics.json").read_text(encoding="utf-8")
    )
    assert run_metrics["days"] == 90.0
    assert run_metrics["planned_orders"] == 100
    assert run_metrics["closed_cycles"] == 30
    assert run_metrics["all_costs_present"] is False
    assert run_metrics["runtime_evidence_status"] == "blocked"
    assert verify_run_integrity(output_dir / "paper-runtime-evidence")["status"] == "passed"


def test_manifest_hashes_execution_inputs_referenced_by_config(tmp_path):
    config = tmp_path / "config.json"
    inputs = tmp_path / "execution_inputs.json"
    dataset = tmp_path / "dataset"
    profile = tmp_path / "market_profile.json"
    dataset.mkdir()
    inputs.write_text('{"mark_price_history": []}\n', encoding="utf-8")
    config.write_text(
        json.dumps({"backtest": {"execution_model": {"inputs_path": str(inputs)}}}),
        encoding="utf-8",
    )
    profile.write_text("{}\n", encoding="utf-8")

    manifest = build_experiment_manifest(
        repo_root=Path(__file__).resolve().parents[1],
        run_id="run-execution-inputs",
        stage="data",
        config_path=config,
        dataset_path=dataset,
        market_profile_path=profile,
        command=("pytest",),
    )

    assert manifest.to_dict()["inputs"]["execution_inputs_sha256"] == sha256_path(inputs)


def test_manifest_hashes_explicit_execution_inputs(tmp_path):
    config = tmp_path / "config.json"
    inputs = tmp_path / "execution_inputs.json"
    dataset = tmp_path / "dataset"
    profile = tmp_path / "market_profile.json"
    dataset.mkdir()
    config.write_text("{}\n", encoding="utf-8")
    inputs.write_text('{"funding_history": []}\n', encoding="utf-8")
    profile.write_text("{}\n", encoding="utf-8")

    manifest = build_experiment_manifest(
        repo_root=Path(__file__).resolve().parents[1],
        run_id="run-explicit-execution-inputs",
        stage="data",
        config_path=config,
        dataset_path=dataset,
        market_profile_path=profile,
        command=("pytest",),
        execution_inputs_path=inputs,
    )

    assert manifest.to_dict()["inputs"]["execution_inputs_sha256"] == sha256_path(inputs)


def _create_integrity_bundle(tmp_path):
    config = tmp_path / "config.json"
    dataset = tmp_path / "dataset"
    profile = tmp_path / "market_profile.json"
    dataset.mkdir(parents=True)
    config.write_text('{"capital": 1000}\n', encoding="utf-8")
    (dataset / "BTC.jsonl").write_text("2026-01-01,1\n", encoding="utf-8")
    profile.write_text('{"symbol": "BTCUSDT"}\n', encoding="utf-8")
    manifest = build_experiment_manifest(
        repo_root=Path(__file__).resolve().parents[1],
        run_id="integrity-run",
        stage="historical",
        config_path=config,
        dataset_path=dataset,
        market_profile_path=profile,
        command=("pytest",),
        created_at_utc="2026-08-17T00:00:00+00:00",
    )
    run_dir = write_run_artifacts(
        run_dir=tmp_path / "runs" / "integrity-run",
        manifest=manifest,
        metrics=_metrics(),
        verdict=evaluate_candidate_gates(_metrics(), stage="historical"),
        config_path=config,
        market_profile_path=profile,
    )
    write_run_integrity_record(run_dir)
    return run_dir, config, dataset, profile


def test_run_integrity_detects_changes_to_manifest_inputs_and_verdict(tmp_path):
    for name in ("manifest.json", "config.json", "market_profile.json", "verdict.json"):
        run_dir, _, _, _ = _create_integrity_bundle(tmp_path / name.replace(".json", ""))
        path = run_dir / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["tampered"] = True
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

        integrity = verify_run_integrity(run_dir)

        assert integrity["status"] == "failed"
        assert name in integrity["changed_files"]


def test_checksums_cover_metrics_report_and_event_log(tmp_path):
    for name in ("metrics.json", "report.md", "events.jsonl.gz"):
        run_dir, _, _, _ = _create_integrity_bundle(tmp_path / name.replace(".", "-"))
        path = run_dir / name
        path.write_bytes(path.read_bytes() + b"tampered")

        integrity = verify_run_integrity(run_dir)

        assert integrity["status"] == "failed"
        assert name in integrity["changed_files"]


def test_integrity_verification_does_not_read_or_replace_status_json(tmp_path):
    run_dir, _, _, _ = _create_integrity_bundle(tmp_path)
    status_path = tmp_path / "status.json"
    status_path.write_text('{"stages": {"historical": {"status": "passed"}}}\n', encoding="utf-8")
    before = status_path.read_bytes()

    assert verify_run_integrity(run_dir)["status"] == "passed"
    assert status_path.read_bytes() == before


def test_normal_roadmap_run_rejects_existing_run_id_without_safe_overwrite(tmp_path):
    run_dir, config, dataset, profile = _create_integrity_bundle(tmp_path)
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps(_metrics()), encoding="utf-8")
    status_path = tmp_path / "status.json"
    status_path.write_text('{"stages": {}}\n', encoding="utf-8")
    before = status_path.read_bytes()

    with pytest.raises(FileExistsError, match="--overwrite/--safe-overwrite"):
        research_gates_main(
            [
                "--run-id",
                "integrity-run",
                "--stage",
                "historical",
                "--metrics",
                str(metrics_path),
                "--config",
                str(config),
                "--dataset",
                str(dataset),
                "--market-profile",
                str(profile),
                "--output-dir",
                str(tmp_path / "runs"),
                "--repo-root",
                str(Path(__file__).resolve().parents[1]),
            ]
        )

    assert status_path.read_bytes() == before


def test_safe_overwrite_requires_an_intact_existing_bundle(tmp_path):
    run_dir, config, dataset, profile = _create_integrity_bundle(tmp_path)
    (run_dir / "verdict.json").write_text('{"status": "passed"}\n', encoding="utf-8")
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps(_metrics()), encoding="utf-8")
    status_path = tmp_path / "status.json"
    status_path.write_text('{"stages": {"historical": {"status": "passed"}}}\n', encoding="utf-8")
    before = status_path.read_bytes()

    with pytest.raises(ValueError, match="unverified run bundle"):
        research_gates_main(
            [
                "--run-id",
                "integrity-run",
                "--stage",
                "historical",
                "--metrics",
                str(metrics_path),
                "--config",
                str(config),
                "--dataset",
                str(dataset),
                "--market-profile",
                str(profile),
                "--output-dir",
                str(tmp_path / "runs"),
                "--repo-root",
                str(Path(__file__).resolve().parents[1]),
                "--safe-overwrite",
                "--scratch",
            ]
        )

    assert status_path.read_bytes() == before


def test_safe_overwrite_replaces_an_intact_bundle_and_refreshes_anchor(tmp_path):
    run_dir, config, dataset, profile = _create_integrity_bundle(tmp_path)
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps(_metrics()), encoding="utf-8")
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "stages": {
                    stage: {"status": "passed"}
                    for stage in ("baseline", "profile", "data", "risk_grid")
                }
            }
        ),
        encoding="utf-8",
    )

    result = research_gates_main(
        [
            "--run-id",
            "integrity-run",
            "--stage",
            "historical",
            "--metrics",
            str(metrics_path),
            "--config",
            str(config),
            "--dataset",
            str(dataset),
            "--market-profile",
            str(profile),
            "--output-dir",
            str(tmp_path / "runs"),
            "--repo-root",
            str(Path(__file__).resolve().parents[1]),
            "--safe-overwrite",
            "--scratch",
        ]
    )

    assert result == 1
    assert verify_run_integrity(run_dir)["status"] == "passed"


def test_cli_research_surface_cannot_admit_production_risk(tmp_path):
    config = tmp_path / "config.json"
    dataset = tmp_path / "dataset"
    profile = tmp_path / "market-profile.json"
    metrics_path = tmp_path / "metrics.json"
    surface = tmp_path / "surface_probe.json"
    output_dir = tmp_path / "runs"
    config.write_text("{}\n", encoding="utf-8")
    dataset.mkdir()
    (dataset / "BTC.jsonl").write_text("2026-01-01,1\n", encoding="utf-8")
    profile.write_text('{"exchange":"bitget","source":"public_api"}\n', encoding="utf-8")
    surface.write_text(
        json.dumps(
            {
                "schema_version": "risk-engine-surface-probe-v1",
                "source_mode": "research_policy",
                "fixture_mode": True,
                "status": "passed",
                "scenario_count": 14,
                "passed_count": 14,
                "failed_count": 0,
                "read_only": True,
                "candidate_admission": False,
                "trading_operations_count": 0,
                "scenarios": [
                    {
                        "name": f"scenario-{index}",
                        "status": "passed",
                        "trading_operations_count": 0,
                    }
                    for index in range(14)
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    metrics_path.write_text(
        json.dumps(
            {
                "risk_contract_implemented": True,
                "risk_snapshot_contract_present": True,
                "parity_harness_present": True,
                "live_tester_decisions_identical": True,
                "risk_limits_enforced_in_live_and_tester": True,
                "risk_parity_reached": True,
                "risk_contract_fails_closed_on_mismatch": True,
                "surface_probe_path": str(surface),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "stages": {
                    "baseline": {"status": "passed"},
                    "market_profile": {"status": "passed"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = research_gates_main(
        [
            "--run-id",
            "research-surface-only",
            "--stage",
            "risk_engine",
            "--metrics",
            str(metrics_path),
            "--config",
            str(config),
            "--dataset",
            str(dataset),
            "--market-profile",
            str(profile),
            "--output-dir",
            str(output_dir),
            "--repo-root",
            str(Path(__file__).resolve().parents[1]),
        ]
    )

    written = json.loads(
        (output_dir / "research-surface-only" / "metrics.json").read_text(
            encoding="utf-8"
        )
    )
    verdict = json.loads(
        (output_dir / "research-surface-only" / "verdict.json").read_text(
            encoding="utf-8"
        )
    )
    assert result != 0
    assert written["risk_limits_enforced_in_live_and_tester"] is False
    assert written["risk_production_evidence_status"] == "missing"
    assert written["risk_surface_matrix_validated"] is True
    assert verdict["status"] in {"failed", "blocked"}
    assert any(
        gate["id"] == "risk_engine.production_evidence_manifest"
        for gate in verdict["gates"]
    )
