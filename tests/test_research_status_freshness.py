from pathlib import Path
import subprocess

from research.experiment_manifest import (
    IMPLEMENTATION_FINGERPRINT_VERSION,
    STAGE_PREREQUISITES_V3,
    _git_state,
    build_experiment_manifest,
)
from tools.research_gates import refresh_stale_status, refresh_status_file


def test_passed_run_without_checksums_becomes_stale(tmp_path):
    status = {
        "stages": {
            "baseline": {
                "status": "passed",
                "run_id": "old-baseline",
            }
        }
    }

    refreshed = refresh_stale_status(
        status,
        runs_root=tmp_path / "runs",
        repo_root=Path(__file__).resolve().parents[1],
    )

    entry = refreshed["stages"]["baseline"]
    assert entry["status"] == "stale"
    assert "run_checksums_invalid" in entry["stale_reasons"]
    assert "manifest_missing" in entry["stale_reasons"]


def test_status_without_run_id_is_not_promoted_or_rewritten(tmp_path):
    status = {"stages": {"baseline": {"status": "passed"}}}

    refreshed = refresh_stale_status(
        status,
        runs_root=tmp_path / "runs",
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert refreshed == status


def test_generated_roadmap_reports_do_not_change_source_fingerprint(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    # The helper shells out to git, so use a real tiny repository.  The
    # roadmap tree is deliberately untracked: creating a later immutable run
    # must not make the already-tested source baseline stale.
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "src.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
        cwd=repo,
        check=True,
    )
    before = _git_state(repo)

    generated = repo / "analysis_reports" / "crypto_bot_roadmap" / "runs" / "later"
    generated.mkdir(parents=True)
    (generated / "manifest.json").write_text("{}\n", encoding="utf-8")
    (repo / "analysis_reports" / "crypto_bot_roadmap" / "status.json").write_text(
        "{}\n", encoding="utf-8"
    )

    after = _git_state(repo)
    assert after == before


def test_docs_and_runtime_outputs_do_not_change_implementation_fingerprint(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "module.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "tests" / "test_module.py").write_text("def test_value(): pass\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "src", "tests"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
        cwd=repo,
        check=True,
    )

    before = _git_state(repo)
    (repo / "docs" / "plans").mkdir(parents=True)
    (repo / "docs" / "plans" / "crypto_grid_bot_development_plan_2026-08-17.md").write_text(
        "# plan\n", encoding="utf-8"
    )
    (repo / "monitor" / "fake").mkdir(parents=True)
    (repo / "monitor" / "fake" / "metadata.tmp").write_text("runtime\n", encoding="utf-8")
    (repo / "analysis_reports" / "crypto_bot_roadmap" / "runs").mkdir(parents=True)
    (repo / "analysis_reports" / "crypto_bot_roadmap" / "runs" / "report.json").write_text(
        "{}\n", encoding="utf-8"
    )
    assert _git_state(repo) == before

    (repo / "src" / "module.py").write_text("value = 2\n", encoding="utf-8")
    changed = _git_state(repo)
    assert changed[0] == before[0]
    assert changed[1] is True
    assert changed[2] != before[2]


def test_manifest_records_implementation_and_plan_hashes(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "module.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "config.json").write_text("{}\n", encoding="utf-8")
    (repo / "dataset.json").write_text("{}\n", encoding="utf-8")
    (repo / "docs" / "plans").mkdir(parents=True)
    plan = repo / "docs" / "plans" / "crypto_grid_bot_development_plan_2026-08-17.md"
    plan.write_text("# plan\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "src"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
        cwd=repo,
        check=True,
    )

    manifest = build_experiment_manifest(
        repo_root=repo,
        run_id="fingerprint-test",
        stage="baseline",
        config_path=repo / "config.json",
        dataset_path=repo / "dataset.json",
        market_profile_path=None,
        command=("pytest",),
    )
    payload = manifest.to_dict()
    assert payload["code"]["implementation_fingerprint_version"] == IMPLEMENTATION_FINGERPRINT_VERSION
    assert payload["code"]["implementation_fingerprint_sha256"] == payload["code"]["dirty_diff_sha256"]
    assert payload["inputs"]["plan_sha256"]


def test_refresh_status_file_writes_only_machine_status_without_run_bundle(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "module.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "src"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
        cwd=repo,
        check=True,
    )
    runs = repo / "runs"
    run_dir = runs / "old-run"
    run_dir.mkdir(parents=True)
    status_path = repo / "status.json"
    status_path.write_text(
        '{"schema_version":"crypto-grid-roadmap-status-v3","stages":'
        '{"baseline":{"status":"passed","check_status":"passed",'
        '"stage_admission_status":"passed","outcome":"passed",'
        '"freshness":"fresh","run_id":"old-run"}}}\n',
        encoding="utf-8",
    )

    refreshed = refresh_status_file(status_path=status_path, runs_root=runs, repo_root=repo)

    assert refreshed["stages"]["baseline"]["status"] == "stale"
    assert refreshed["stages"]["baseline"]["freshness"] == "stale"
    assert sorted(path.name for path in runs.iterdir()) == ["old-run"]
    assert status_path.is_file()


def test_v3_marks_failed_and_blocked_run_backed_entries_stale(tmp_path):
    runs = tmp_path / "runs"
    for run_id, status in (("failed-run", "failed"), ("blocked-run", "blocked")):
        run_dir = runs / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            '{"run_id": "' + run_id + '", "code": '
            '{"git_commit": "old", "git_dirty": false, '
            '"dirty_diff_sha256": "old-hash"}}\n',
            encoding="utf-8",
        )
    status = {
        "schema_version": "crypto-grid-roadmap-status-v3",
        "stages": {
            "baseline": {
                "status": "failed",
                "check_status": "failed",
                "stage_admission_status": "failed",
                "outcome": "failed",
                "freshness": "fresh",
                "run_id": "failed-run",
            },
            "market_profile": {
                "status": "blocked",
                "check_status": "blocked",
                "stage_admission_status": "blocked",
                "outcome": "blocked",
                "freshness": "fresh",
                "run_id": "blocked-run",
            },
        },
    }

    refreshed = refresh_stale_status(status, runs_root=runs, repo_root=Path(__file__).resolve().parents[1])

    assert refreshed["stages"]["baseline"]["freshness"] == "stale"
    assert refreshed["stages"]["baseline"]["outcome"] == "failed"
    assert refreshed["stages"]["baseline"]["status"] == "stale"
    assert refreshed["stages"]["market_profile"]["freshness"] == "stale"
    assert refreshed["stages"]["market_profile"]["outcome"] == "blocked"


def test_v3_dependency_blocks_admission_without_erasing_passed_check(tmp_path):
    status = {
        "schema_version": "crypto-grid-roadmap-status-v3",
        "stages": {
            "baseline": {
                "status": "failed",
                "check_status": "failed",
                "stage_admission_status": "failed",
                "outcome": "failed",
                "freshness": "not_applicable",
            },
            "risk_engine": {
                "status": "passed",
                "check_status": "passed",
                "stage_admission_status": "passed",
                "outcome": "passed",
                "freshness": "fresh",
                "run_id": "risk-engine-run",
            },
        },
    }
    refreshed = refresh_stale_status(status, runs_root=tmp_path / "runs", repo_root=Path(__file__).resolve().parents[1])
    entry = refreshed["stages"]["risk_engine"]
    assert entry["check_status"] == "passed"
    assert entry["stage_admission_status"] == "blocked"
    assert entry["outcome"] == "blocked"


def test_public_safety_surfaces_do_not_depend_on_historical_data_gate():
    assert STAGE_PREREQUISITES_V3["risk_engine"] == ("baseline", "market_profile")
    assert STAGE_PREREQUISITES_V3["risk_grid"] == (
        "baseline",
        "market_profile",
        "risk_engine",
    )
    assert STAGE_PREREQUISITES_V3["shadow_readiness"] == (
        "baseline",
        "market_profile",
        "risk_engine",
    )
    assert "data" not in STAGE_PREREQUISITES_V3["risk_grid"]
    assert "historical" not in STAGE_PREREQUISITES_V3["shadow_readiness"]


def test_public_paper_path_does_not_depend_on_private_risk_contract():
    assert "risk_contract" not in STAGE_PREREQUISITES_V3["paper_runtime"]
    assert "shadow_readiness" in STAGE_PREREQUISITES_V3["paper_runtime"]
    assert "risk_contract" not in STAGE_PREREQUISITES_V3["portfolio_paper"]
    assert "portfolio_historical" in STAGE_PREREQUISITES_V3["portfolio_paper"]


def test_v3_refresh_replaces_changed_admission_blocker_list(tmp_path):
    passed = {
        "status": "passed",
        "check_status": "passed",
        "stage_admission_status": "passed",
        "outcome": "passed",
        "freshness": "not_applicable",
    }
    not_started = {
        "status": "not_started",
        "check_status": "not_started",
        "stage_admission_status": "not_started",
        "outcome": "not_started",
        "freshness": "not_applicable",
    }
    refreshed = refresh_stale_status(
        {
            "schema_version": "crypto-grid-roadmap-status-v3",
            "stages": {
                "baseline": dict(passed),
                "market_profile": dict(passed),
                "historical": {
                    "status": "failed",
                    "check_status": "failed",
                    "stage_admission_status": "failed",
                    "outcome": "failed",
                    "freshness": "not_applicable",
                },
                "shadow_readiness": dict(not_started),
                "paper_runtime": {
                    **not_started,
                    "status": "blocked",
                    "stage_admission_status": "blocked",
                    "outcome": "blocked",
                    "admission_blockers": ["baseline", "risk_contract"],
                },
            },
        },
        runs_root=tmp_path / "runs",
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert refreshed["stages"]["paper_runtime"]["admission_blockers"] == [
        "historical",
        "shadow_readiness",
    ]


def test_v3_refresh_treats_not_started_prerequisite_as_blocker(tmp_path):
    refreshed = refresh_stale_status(
        {
            "schema_version": "crypto-grid-roadmap-status-v3",
            "stages": {
                "baseline": {
                    "status": "passed",
                    "check_status": "passed",
                    "stage_admission_status": "passed",
                    "outcome": "passed",
                    "freshness": "not_applicable",
                },
                "market_profile": {
                    "status": "passed",
                    "check_status": "passed",
                    "stage_admission_status": "passed",
                    "outcome": "passed",
                    "freshness": "not_applicable",
                },
                "account_risk_surface": {
                    "status": "not_started",
                    "check_status": "not_started",
                    "stage_admission_status": "not_started",
                    "outcome": "not_started",
                    "freshness": "not_applicable",
                },
                "technical_live": {
                    "status": "not_started",
                    "check_status": "not_started",
                    "stage_admission_status": "not_started",
                    "outcome": "not_started",
                    "freshness": "not_applicable",
                },
            },
        },
        runs_root=tmp_path / "runs",
        repo_root=Path(__file__).resolve().parents[1],
    )

    entry = refreshed["stages"]["technical_live"]
    assert entry["status"] == "blocked"
    assert "account_risk_surface" in entry["admission_blockers"]
