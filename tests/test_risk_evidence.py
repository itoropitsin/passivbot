from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from research.experiment_manifest import evaluate_candidate_gates, sha256_path
from research.risk_evidence import (
    RiskEvidenceError,
    build_production_risk_evidence_manifest,
    validate_actual_probe_regression,
    validate_production_risk_evidence,
    validate_surface_matrix,
    write_production_invocation,
)

ROOT = Path(__file__).resolve().parents[1]
ACTUAL_PROBE_V3 = (
    ROOT / "analysis_reports/crypto_bot_roadmap/actual_risk_parity_probe_2026-08-17-v3"
)
MARKET_PROFILE = (
    ROOT / "analysis_reports/crypto_bot_roadmap/inputs/"
    "bitget_btc_market_profile_read_only.json"
)


def _surface_matrix(path: Path, *, count: int = 14) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "risk-engine-surface-probe-v1",
                "source_mode": "research_policy",
                "fixture_mode": True,
                "status": "passed",
                "scenario_count": count,
                "passed_count": count,
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
                    for index in range(count)
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_production_checksums(root: Path) -> None:
    checksums = {
        path.name: sha256_path(path)
        for path in sorted(root.iterdir())
        if path.is_file() and path.name != "checksums.json"
    }
    (root / "checksums.json").write_text(
        json.dumps(checksums, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _copy_actual_probe_bundle(root: Path) -> None:
    for name in (
        "snapshot.json",
        "live_export.json",
        "rust_trace.json",
        "comparison.json",
        "report.json",
    ):
        shutil.copyfile(ACTUAL_PROBE_V3 / name, root / name)
    shutil.copyfile(MARKET_PROFILE, root / "market_profile.json")
    (root / "config.json").write_text("{}\n", encoding="utf-8")
    _surface_matrix(root / "surface_probe.json")
    write_production_invocation(
        root / "invocation.json",
        live_export_sha256=sha256_path(root / "live_export.json"),
        rust_trace_sha256=sha256_path(root / "rust_trace.json"),
        snapshot_hash="cb50c9a16fb808c0f77a28d862a0a04b786bdd3eb482725e4558d49b7d5b142f",
        trace_record_count=6,
    )


def test_surface_matrix_requires_complete_explicit_research_fixture(tmp_path):
    matrix = tmp_path / "surface_probe.json"
    _surface_matrix(matrix)

    result = validate_surface_matrix(matrix)

    assert result["status"] == "passed"
    assert result["source_mode"] == "research_policy"
    assert result["fixture_mode"] is True
    assert result["scenario_count"] == 14

    _surface_matrix(matrix, count=13)
    with pytest.raises(RiskEvidenceError, match="incomplete"):
        validate_surface_matrix(matrix)


def test_actual_probe_v3_is_required_regression_not_admission():
    result = validate_actual_probe_regression(ACTUAL_PROBE_V3)

    assert result["status"] == "passed"
    assert result["trace_record_count"] == 6
    assert (
        result["snapshot_hash"]
        == "cb50c9a16fb808c0f77a28d862a0a04b786bdd3eb482725e4558d49b7d5b142f"
    )
    assert "risk_limits_enforced_in_live_and_tester" not in result


def test_actual_probe_rejects_unsafe_checksum_path(tmp_path):
    regression = tmp_path / "regression"
    shutil.copytree(ACTUAL_PROBE_V3, regression)
    checksums_path = regression / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    checksums["../outside.json"] = checksums["snapshot.json"]
    checksums_path.write_text(
        json.dumps(checksums, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RiskEvidenceError, match="unsafe path"):
        validate_actual_probe_regression(regression)


def test_legacy_v29_metrics_cannot_admit_production_risk():
    metrics = json.loads(
        (
            ROOT / "analysis_reports/crypto_bot_roadmap/runs/"
            "risk-engine-2026-08-18-v29/metrics.json"
        ).read_text(encoding="utf-8")
    )

    verdict = evaluate_candidate_gates(metrics, stage="risk_engine")

    assert verdict["status"] == "blocked"
    assert any(
        gate["id"] == "risk_engine.production_evidence_manifest"
        for gate in verdict["gates"]
    )


def test_production_manifest_binds_actual_artifacts_and_code(tmp_path):
    evidence_dir = tmp_path / "production"
    evidence_dir.mkdir()
    _copy_actual_probe_bundle(evidence_dir)

    manifest = build_production_risk_evidence_manifest(
        repo_root=ROOT,
        evidence_dir=evidence_dir,
        required_regression_path=ACTUAL_PROBE_V3,
    )
    assert manifest["required_regression"]["path"] == (
        ACTUAL_PROBE_V3.relative_to(ROOT).as_posix()
    )
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_production_checksums(evidence_dir)

    metrics = validate_production_risk_evidence(
        manifest_path,
        repo_root=ROOT,
        config_path=evidence_dir / "config.json",
        market_profile_path=evidence_dir / "market_profile.json",
    )

    assert metrics["risk_production_path_validated"] is True
    assert metrics["risk_limits_enforced_in_live_and_tester"] is True
    assert metrics["risk_production_python_live_gate_called"] is True
    assert metrics["risk_production_rust_backtest_called"] is True
    assert metrics["risk_surface_matrix_scenario_count"] == 14
    assert metrics["risk_production_report_bound"] is True


def test_production_manifest_rejects_a_changed_bound_config(tmp_path):
    evidence_dir = tmp_path / "production"
    evidence_dir.mkdir()
    _copy_actual_probe_bundle(evidence_dir)
    manifest = build_production_risk_evidence_manifest(
        repo_root=ROOT,
        evidence_dir=evidence_dir,
        required_regression_path=ACTUAL_PROBE_V3,
    )
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_production_checksums(evidence_dir)
    (evidence_dir / "config.json").write_text('{"changed": true}\n', encoding="utf-8")

    with pytest.raises(RiskEvidenceError, match="checksum mismatch|hash differs"):
        validate_production_risk_evidence(
            manifest_path,
            repo_root=ROOT,
            config_path=evidence_dir / "config.json",
            market_profile_path=evidence_dir / "market_profile.json",
        )


def test_production_builder_rejects_missing_invocation(tmp_path):
    evidence_dir = tmp_path / "production"
    evidence_dir.mkdir()
    _copy_actual_probe_bundle(evidence_dir)
    (evidence_dir / "invocation.json").unlink()

    with pytest.raises(RiskEvidenceError, match="invocation proof is missing"):
        build_production_risk_evidence_manifest(
            repo_root=ROOT,
            evidence_dir=evidence_dir,
            required_regression_path=ACTUAL_PROBE_V3,
        )


def test_production_manifest_rejects_missing_rust_trace(tmp_path):
    evidence_dir = tmp_path / "production"
    evidence_dir.mkdir()
    _copy_actual_probe_bundle(evidence_dir)
    manifest = build_production_risk_evidence_manifest(
        repo_root=ROOT,
        evidence_dir=evidence_dir,
        required_regression_path=ACTUAL_PROBE_V3,
    )
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (evidence_dir / "rust_trace.json").unlink()
    _write_production_checksums(evidence_dir)

    with pytest.raises(RiskEvidenceError, match="unavailable|missing"):
        validate_production_risk_evidence(
            manifest_path,
            repo_root=ROOT,
            config_path=evidence_dir / "config.json",
            market_profile_path=evidence_dir / "market_profile.json",
        )


def test_research_only_manifests_cannot_set_production_admission():
    with pytest.raises(RiskEvidenceError, match="schema is unsupported"):
        validate_production_risk_evidence(
            ACTUAL_PROBE_V3 / "manifest.json",
            repo_root=ROOT,
            config_path=ACTUAL_PROBE_V3 / "manifest.json",
            market_profile_path=MARKET_PROFILE,
        )

    v29 = json.loads(
        (
            ROOT / "analysis_reports/crypto_bot_roadmap/runs/"
            "risk-engine-2026-08-18-v29/metrics.json"
        ).read_text(encoding="utf-8")
    )
    assert "risk_limits_enforced_in_live_and_tester" in v29
    verdict = evaluate_candidate_gates(v29, stage="risk_engine")
    assert verdict["status"] == "blocked"
    assert any(
        gate["id"] == "risk_engine.production_evidence_manifest"
        for gate in verdict["gates"]
    )
    assert not any(
        gate["status"] == "passed"
        and "risk_limits_enforced_in_live_and_tester" in str(gate.get("id", ""))
        for gate in verdict["gates"]
    )


def test_production_invocation_rejects_research_policy_flag(tmp_path):
    evidence_dir = tmp_path / "production"
    evidence_dir.mkdir()
    _copy_actual_probe_bundle(evidence_dir)
    invocation_path = evidence_dir / "invocation.json"
    payload = json.loads(invocation_path.read_text(encoding="utf-8"))
    payload["research_policy_used"] = True
    invocation_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(RiskEvidenceError, match="research policy"):
        build_production_risk_evidence_manifest(
            repo_root=ROOT,
            evidence_dir=evidence_dir,
            required_regression_path=ACTUAL_PROBE_V3,
        )


def test_live_probe_records_invocation_after_calling_both_engines(tmp_path):
    import passivbot_rust

    if getattr(passivbot_rust, "__is_stub__", False):
        pytest.skip("compiled Rust Backtest is required for production-path invocation")

    try:
        from tools.risk_parity_actual_probe import run
    except RuntimeError as exc:
        if "does not match current Rust sources" in str(exc):
            pytest.skip(
                "compiled Rust Backtest is stale versus overlay sources; "
                "rebuild is outside this production-evidence packet"
            )
        raise

    output = tmp_path / "production-run"
    report = run(output)

    assert report["status"] == "passed"
    assert report["candidate_admission"] is False
    assert report["trading_operations_count"] == 0
    metrics = validate_production_risk_evidence(
        output / "manifest.json",
        repo_root=ROOT,
        config_path=output / "config.json",
        market_profile_path=output / "market_profile.json",
    )
    invocation = json.loads(
        (output / "invocation.json").read_text(encoding="utf-8")
    )
    assert metrics["risk_limits_enforced_in_live_and_tester"] is True
    assert metrics["risk_production_python_live_gate_called"] is True
    assert metrics["risk_production_rust_backtest_called"] is True
    assert invocation["python_live_gate_method"] == (
        "Passivbot._apply_crypto_portfolio_risk_limits"
    )
    assert invocation["rust_trace_source"] == "Rust Backtest risk_gate_trace"
    assert invocation["invoked_by"] == "src/tools/risk_parity_actual_probe.py"
