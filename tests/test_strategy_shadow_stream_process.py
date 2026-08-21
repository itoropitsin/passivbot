from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import research.strategy_shadow_stream_process as shadow_process
from research.strategy_shadow import read_shadow_events
from research.strategy_shadow_stream_process import (
    STATE_FILES,
    _require_resume_output,
    _terminate_process_tree,
    run_stream_process_supervisor,
)
from tools.bitget_market_recorder import collect_market_snapshot

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _wait_for_pid_exit(pid: int, *, timeout_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while _pid_is_running(pid):
        if time.monotonic() >= deadline:
            raise AssertionError(f"process {pid} is still running")
        time.sleep(0.01)


@pytest.mark.skipif(
    os.name != "posix", reason="process-group cleanup is POSIX-specific"
)
@pytest.mark.parametrize(
    "child_ignores_sigterm",
    [pytest.param(False, id="sigterm"), pytest.param(True, id="sigkill-fallback")],
)
def test_supervisor_cleanup_terminates_direct_child_and_grandchild(
    tmp_path: Path, child_ignores_sigterm: bool
):
    grandchild_pid_path = tmp_path / "grandchild.pid"
    grandchild_code = """
import pathlib
import signal
import sys
import time

path = pathlib.Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
path.write_text(str(__import__('os').getpid()), encoding='utf-8')
while True:
    time.sleep(1)
"""
    child_code = f"""
import signal
import subprocess
import sys
import time

if sys.argv[2] == 'ignore':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen([sys.executable, '-c', {grandchild_code!r}, sys.argv[1]])
while True:
    time.sleep(1)
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_code,
            str(grandchild_pid_path),
            "ignore" if child_ignores_sigterm else "term",
        ],
        start_new_session=True,
    )
    grandchild_pid = None
    try:
        deadline = time.monotonic() + 3.0
        while not grandchild_pid_path.is_file():
            if child.poll() is not None:
                raise AssertionError(f"child exited before grandchild startup: {child.returncode}")
            if time.monotonic() >= deadline:
                raise AssertionError("grandchild did not publish its PID")
            time.sleep(0.01)
        grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))
        assert os.getpgid(grandchild_pid) == child.pid
        if not child_ignores_sigterm:
            os.kill(child.pid, signal.SIGTERM)
            child.wait(timeout=1.0)
        _terminate_process_tree(child, timeout_seconds=0.2)
        assert child.poll() is not None
        _wait_for_pid_exit(grandchild_pid)
    finally:
        # Keep a failed test from leaking the intentionally stubborn fixture.
        if grandchild_pid is not None and _pid_is_running(grandchild_pid):
            try:
                if os.getpgid(grandchild_pid) == child.pid:
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    os.kill(grandchild_pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if child.poll() is None:
            child.wait()


def test_supervisor_cleanup_terminates_child_process_group():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        _terminate_process_tree(child, timeout_seconds=1.0)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def _request_factory():
    state = {"index": -1}
    prices = [100.0, 99.8, 100.0, 99.8, 100.0, 99.8]

    def request(path, params):
        if path.endswith("/tickers"):
            state["index"] += 1
            index = min(state["index"], len(prices) - 1)
            price = prices[index]
            return {
                "code": "00000",
                "data": [
                    {
                        "symbol": params["symbol"],
                        "lastPrice": str(price),
                        "bid1Price": str(price),
                        "ask1Price": str(price + 0.01),
                        "bid1Size": "2",
                        "ask1Size": "2",
                        "markPrice": str(price),
                        "indexPrice": str(price),
                        "fundingRate": "0.0001",
                        "ts": str(1_700_000_000_000 + index * 60_000),
                    }
                ],
            }
        index = max(state["index"], 0)
        price = prices[min(index, len(prices) - 1)]
        if path.endswith("/orderbook"):
            return {
                "code": "00000",
                "data": {
                    "ts": str(1_700_000_000_000 + index * 60_000),
                    "b": [[str(price), "2"]],
                    "a": [[str(price + 0.01), "2"]],
                },
            }
        if path.endswith("/current-fund-rate"):
            return {
                "code": "00000",
                "data": [
                    {
                        "symbol": params["symbol"],
                        "fundingRate": "0.0001",
                        "fundingRateInterval": "8",
                        "nextUpdate": "1700000000000",
                        "minFundingRate": "-0.003",
                        "maxFundingRate": "0.003",
                    }
                ],
            }
        if path.endswith("/history-fund-rate"):
            return {
                "code": "00000",
                "data": {
                    "resultList": [
                        {
                            "symbol": params["symbol"],
                            "fundingRate": "0.00009",
                            "fundingRateTimestamp": "1699999000000",
                        }
                    ]
                },
            }
        raise AssertionError(path)

    return request


def _write_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "public_fixture.json"
    request = _request_factory()
    rows = [
        collect_market_snapshot(
            symbol="BTCUSDT",
            category="USDT-FUTURES",
            depth=20,
            request=request,
            captured_at_ms=1_800_000_000_000 + index * 60_000,
        )
        for index in range(6)
    ]
    fixture.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return fixture


def _write_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "strategy_id": "stream-process-test",
                "shadow_policy": {
                    "entry_bps": 5.0,
                    "take_profit_bps": 5.0,
                    "order_amount": 0.001,
                },
                "max_source_gap_seconds": 300.0,
            }
        ),
        encoding="utf-8",
    )
    return config


def test_supervisor_records_fault_injected_child_exit(tmp_path: Path, monkeypatch):
    fixture = _write_fixture(tmp_path)
    config = _write_config(tmp_path)
    original_run_child = shadow_process._run_child_process
    calls = {"count": 0}

    def run_with_fault(command, *, cwd, env):
        calls["count"] += 1
        if calls["count"] == 1:
            command = [
                sys.executable,
                "-c",
                "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
            ]
        return original_run_child(command, cwd=cwd, env=env)

    monkeypatch.setattr(shadow_process, "_run_child_process", run_with_fault)
    result = run_stream_process_supervisor(
        output_dir=tmp_path / "fault-session",
        config_path=config,
        fixture_path=fixture,
        segment_duration_seconds=0.0,
        max_samples=1,
        restart_count=1,
        deterministic_replay_runs=2,
        repo_root=REPO_ROOT,
    )

    supervisor = result["process_supervisor"]
    assert result["status"] == "failed"
    assert supervisor["status"] == "failed"
    assert supervisor["processes"][0]["return_code"] == -signal.SIGKILL
    assert supervisor["processes"][1]["return_code"] == 0
    assert any("exited with" in error for error in supervisor["errors"])


def test_supervisor_recovers_checkpoint_without_duplicate_order_or_fill_ids(tmp_path: Path):
    fixture = _write_fixture(tmp_path)
    config = _write_config(tmp_path)
    result = run_stream_process_supervisor(
        output_dir=tmp_path / "recovery-session",
        config_path=config,
        fixture_path=fixture,
        segment_duration_seconds=0.0,
        max_samples=1,
        restart_count=5,
        deterministic_replay_runs=3,
        repo_root=REPO_ROOT,
    )

    assert result["status"] == "passed"
    supervisor = result["process_supervisor"]
    assert supervisor["duplicate_state_count"] == 0
    assert all(operation["state_files_present"] for operation in supervisor["restart_operations"])
    assert result["readiness_metrics"]["shadow_driver_restart_count"] == 5
    assert result["readiness_metrics"]["shadow_process_duplicate_state_count"] == 0

    events = read_shadow_events(tmp_path / "recovery-session" / "events.ndjson")
    order_ids = [
        str(event["payload"]["order_id"])
        for event in events
        if event["kind"] == "order.opened" and event["payload"].get("order_id")
    ]
    fill_ids = [
        str(event["payload"]["fill_id"])
        for event in events
        if event["kind"] == "order.filled" and event["payload"].get("fill_id")
    ]
    assert order_ids
    assert fill_ids
    assert len(order_ids) == len(set(order_ids))
    assert len(fill_ids) == len(set(fill_ids))

    checkpoint = json.loads(
        (tmp_path / "recovery-session" / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert len(checkpoint["processed_snapshot_ids"]) == 6
    assert checkpoint["event_count"] == len(events)


def test_supervisor_binds_market_profile_hash_into_result(tmp_path: Path):
    fixture = _write_fixture(tmp_path)
    config = _write_config(tmp_path)
    market_profile = tmp_path / "market_profile.json"
    market_profile.write_text(json.dumps({"schema_version": "test-profile"}), encoding="utf-8")

    result = run_stream_process_supervisor(
        output_dir=tmp_path / "profile-binding-session",
        config_path=config,
        fixture_path=fixture,
        market_profile_path=market_profile,
        segment_duration_seconds=0.0,
        max_samples=1,
        restart_count=1,
        deterministic_replay_runs=2,
        repo_root=REPO_ROOT,
    )

    binding = result["provenance"]["market_profile"]
    assert binding["path"] == str(market_profile.resolve())
    assert binding["sha256"] == shadow_process.sha256_file(market_profile)
    supervisor = result["process_supervisor"]
    assert supervisor["market_profile_sha256"] == binding["sha256"]


def test_stream_process_supervisor_records_pids_and_replays_fixture(tmp_path):
    fixture = _write_fixture(tmp_path)
    config = _write_config(tmp_path)
    result = run_stream_process_supervisor(
        output_dir=tmp_path / "session",
        config_path=config,
        fixture_path=fixture,
        segment_duration_seconds=0.0,
        max_samples=1,
        restart_count=5,
        deterministic_replay_runs=3,
        repo_root=REPO_ROOT,
    )

    assert result["status"] == "passed"
    supervisor = result["process_supervisor"]
    assert supervisor["status"] == "passed"
    assert supervisor["fixture_mode"] is True
    assert supervisor["observed_restart_count"] == 5
    assert len({row["pid"] for row in supervisor["processes"]}) == 6
    assert all(row["return_code"] == 0 for row in supervisor["processes"])
    assert result["readiness_metrics"]["shadow_process_restart_count"] == 5
    assert result["readiness_metrics"]["shadow_stream_fixture_mode"] is True
    assert result["readiness_metrics"]["shadow_determinism_runs"] == 3
    assert result["readiness_metrics"]["shadow_external_network_calls"] == 0
    assert result["readiness_metrics"]["shadow_stream_market_events"] == 6
    assert result["readiness_metrics"]["shadow_stream_gap_check_status"] == "passed"
    assert result["readiness_metrics"]["shadow_process_max_source_gap_seconds"] == 300.0


def _write_resume_state_dir(
    tmp_path: Path,
    *,
    last_source_age_seconds: float,
    fixture_mode: bool = False,
) -> Path:
    root = tmp_path / "resume-state"
    root.mkdir()
    request = _request_factory()
    row = collect_market_snapshot(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=20,
        request=request,
        captured_at_ms=1_800_000_000_000,
    )
    (root / "market_snapshots.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    last_ms = int((time.time() - last_source_age_seconds) * 1000)
    (root / "stream_state.json").write_text(
        json.dumps(
            {
                "schema_version": "crypto-strategy-shadow-stream-v1",
                "symbol": "BTCUSDT",
                "category": "USDT-FUTURES",
                "depth": 20,
                "side": "long",
                "policy_config_hash": "test",
                "max_source_gap_seconds": 300.0,
                "fixture_mode": fixture_mode,
                "ingest_seq": 1,
                "market_event_count": 1,
                "heartbeat_count": 0,
                "last_source_event_id": "test",
                "last_source_timestamp_ms": last_ms,
                "last_observed_at_ms": last_ms,
            }
        ),
        encoding="utf-8",
    )
    for name in STATE_FILES:
        path = root / name
        if not path.exists():
            path.write_text("{}\n" if name.endswith(".json") else "", encoding="utf-8")
    return root


def test_resume_rejects_completed_supervisor_result(tmp_path: Path):
    root = _write_resume_state_dir(tmp_path, last_source_age_seconds=10.0)
    (root / "process_supervisor.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="completed supervisor result"):
        _require_resume_output(
            root,
            fixture_mode=False,
            max_source_gap_seconds=300.0,
        )


def test_resume_rejects_stale_live_source_gap(tmp_path: Path):
    root = _write_resume_state_dir(tmp_path, last_source_age_seconds=400.0)
    with pytest.raises(ValueError, match="resume rejected"):
        _require_resume_output(
            root,
            fixture_mode=False,
            max_source_gap_seconds=300.0,
        )


def test_resume_allows_fresh_live_source(tmp_path: Path):
    root = _write_resume_state_dir(tmp_path, last_source_age_seconds=12.0)
    snapshot = _require_resume_output(
        root,
        fixture_mode=False,
        max_source_gap_seconds=300.0,
    )
    assert snapshot["market_event_count"] == 1
    assert snapshot["source_age_seconds"] < 300.0


def test_resume_skips_wall_clock_age_in_fixture_mode(tmp_path: Path):
    root = _write_resume_state_dir(
        tmp_path, last_source_age_seconds=10_000.0, fixture_mode=True
    )
    snapshot = _require_resume_output(
        root,
        fixture_mode=True,
        max_source_gap_seconds=300.0,
    )
    assert "source_age_seconds" not in snapshot


def test_supervisor_rejects_existing_output_without_resume_flag(tmp_path: Path):
    existing = tmp_path / "already-used"
    existing.mkdir()
    (existing / "checkpoint.json").write_text("{}\n", encoding="utf-8")
    config = _write_config(tmp_path)
    fixture = _write_fixture(tmp_path)
    with pytest.raises(FileExistsError, match="new and empty"):
        run_stream_process_supervisor(
            output_dir=existing,
            config_path=config,
            fixture_path=fixture,
            segment_duration_seconds=0.0,
            max_samples=1,
            restart_count=1,
            deterministic_replay_runs=2,
            repo_root=REPO_ROOT,
        )


def test_supervisor_resumes_unfinished_output_without_duplicate_ids(tmp_path: Path):
    fixture = _write_fixture(tmp_path)
    config = _write_config(tmp_path)
    output_dir = tmp_path / "cutover-session"
    first = run_stream_process_supervisor(
        output_dir=output_dir,
        config_path=config,
        fixture_path=fixture,
        segment_duration_seconds=0.0,
        max_samples=1,
        restart_count=1,
        deterministic_replay_runs=2,
        repo_root=REPO_ROOT,
    )
    assert first["status"] == "passed"
    (output_dir / "process_supervisor.json").unlink()

    resumed = run_stream_process_supervisor(
        output_dir=output_dir,
        config_path=config,
        fixture_path=fixture,
        segment_duration_seconds=0.0,
        max_samples=1,
        restart_count=1,
        deterministic_replay_runs=2,
        repo_root=REPO_ROOT,
        resume_existing_output=True,
    )
    assert resumed["process_supervisor"]["resumed_existing_output"] is True
    assert resumed["process_supervisor"]["resume_snapshot"]["market_event_count"] == 2
    assert (output_dir / "segments" / "pre-resume-result.json").is_file()
    assert (output_dir / "segments" / "segment-001-result.json").is_file()
    assert (output_dir / "segments" / "segment-003-result.json").is_file()

    events = read_shadow_events(output_dir / "events.ndjson")
    order_ids = [
        str(event["payload"]["order_id"])
        for event in events
        if event["kind"] == "order.opened" and event["payload"].get("order_id")
    ]
    fill_ids = [
        str(event["payload"]["fill_id"])
        for event in events
        if event["kind"] == "order.filled" and event["payload"].get("fill_id")
    ]
    assert order_ids
    assert fill_ids
    assert len(order_ids) == len(set(order_ids))
    assert len(fill_ids) == len(set(fill_ids))
