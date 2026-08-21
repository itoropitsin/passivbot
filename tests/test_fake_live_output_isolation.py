from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import hjson

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tree_paths(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
    }


def _metadata_tmp_paths(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {
        str(path.relative_to(root))
        for path in root.rglob("metadata.tmp")
        if path.is_file()
    }


def _prepare_scenario(tmp_path: Path) -> Path:
    source = REPO_ROOT / "scenarios" / "fake_live" / "hsl_long_red_restart.hjson"
    scenario = hjson.loads(source.read_text(encoding="utf-8"))
    scenario.pop("assertions", None)
    path = tmp_path / "output_isolation.hjson"
    path.write_text(hjson.dumps(scenario), encoding="utf-8")
    return path


def _command(*, scenario_path: Path, output_root: Path, user: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "tools.run_fake_live",
        str(REPO_ROOT / "configs" / "fake_live_hsl_btc.hjson"),
        str(scenario_path),
        "--user",
        user,
        "--max-steps",
        "1",
        "--output-dir",
        str(output_root),
        "--log-level",
        "0",
    ]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_success(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _run_metadata(output_root: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(output_root.glob("*/run_metadata.json"))
    ]


def test_two_sequential_fake_live_runs_use_ephemeral_output_roots(tmp_path):
    scenario_path = _prepare_scenario(tmp_path)
    output_root = tmp_path / "runs"
    monitor_root = REPO_ROOT / "monitor" / "fake"
    fill_root = REPO_ROOT / "caches" / "fill_events" / "fake"
    monitor_before = _tree_paths(monitor_root)
    metadata_before = _metadata_tmp_paths(fill_root)

    for _ in range(2):
        _assert_success(
            _run(
                _command(
                    scenario_path=scenario_path,
                    output_root=output_root,
                    user="fake_output_isolation_sequential",
                )
            )
        )

    metadata = _run_metadata(output_root)
    runtime_roots = [Path(item["runtime_root"]) for item in metadata]
    assert len(runtime_roots) == 2
    assert len(set(runtime_roots)) == 2
    assert all(not root.exists() for root in runtime_roots)
    assert _tree_paths(monitor_root) == monitor_before
    assert _metadata_tmp_paths(fill_root) == metadata_before


def test_two_parallel_fake_live_runs_use_independent_output_roots(tmp_path):
    scenario_path = _prepare_scenario(tmp_path)
    output_root = tmp_path / "runs"
    monitor_root = REPO_ROOT / "monitor" / "fake"
    fill_root = REPO_ROOT / "caches" / "fill_events" / "fake"
    monitor_before = _tree_paths(monitor_root)
    metadata_before = _metadata_tmp_paths(fill_root)
    commands = [
        _command(
            scenario_path=scenario_path,
            output_root=output_root,
            user="fake_output_isolation_parallel",
        )
        for _ in range(2)
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    processes = [
        subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for command in commands
    ]
    results = [process.communicate() for process in processes]
    assert all(
        returncode == 0
        for stdout, stderr, returncode in (
            (stdout, stderr, process.returncode)
            for process, (stdout, stderr) in zip(processes, results)
        )
    ), "\n".join(
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
        for stdout, stderr in results
    )

    metadata = _run_metadata(output_root)
    runtime_roots = [Path(item["runtime_root"]) for item in metadata]
    assert len(runtime_roots) == 2
    assert len(set(runtime_roots)) == 2
    assert all(not root.exists() for root in runtime_roots)
    assert _tree_paths(monitor_root) == monitor_before
    assert _metadata_tmp_paths(fill_root) == metadata_before
