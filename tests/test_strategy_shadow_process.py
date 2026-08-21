from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.strategy_shadow_process import run_process_supervisor


REPO_ROOT = Path(__file__).resolve().parents[1]


def _rows(count: int = 24, *, timestamp_step: int = 60_000) -> list[dict]:
    rows = []
    for index in range(count):
        action = "enter" if index % 2 == 0 else "exit"
        rows.append(
            {
                "snapshot_id": f"process-{index:03d}",
                "timestamp": 1_700_000_000_000 + index * timestamp_step,
                "symbol": "BTC/USDT:USDT",
                "bid": 100_000.0 + index,
                "ask": 100_100.0 + index,
                "last": 100_050.0 + index,
                "signals": {"long": action, "short": "hold"},
                "execution": {
                    "enabled": True,
                    "amount": 0.001,
                    "price": 100_050.0 + index,
                    "fill_id": f"fill-process-{index:03d}",
                    "part_sequence": 1,
                },
            }
        )
    return rows


def _write_inputs(tmp_path: Path, *, timestamp_step: int = 60_000) -> tuple[Path, Path]:
    input_path = tmp_path / "snapshots.json"
    input_path.write_text(json.dumps(_rows(timestamp_step=timestamp_step)), encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "strategy_id": "process-test",
                "order_amount": 0.001,
                "entry_offset_bps": 0.0,
                "exit_offset_bps": 0.0,
            }
        ),
        encoding="utf-8",
    )
    return input_path, config_path


def test_process_supervisor_records_distinct_processes_and_restarts(tmp_path):
    input_path, config_path = _write_inputs(tmp_path)
    result = run_process_supervisor(
        input_path=input_path,
        output_dir=tmp_path / "session",
        config_path=config_path,
        restart_count=5,
        deterministic_replay_runs=2,
        repo_root=REPO_ROOT,
    )

    assert result["status"] == "passed"
    supervisor = result["process_supervisor"]
    assert supervisor["status"] == "passed"
    assert supervisor["observed_restart_count"] == 5
    assert len({row["pid"] for row in supervisor["processes"]}) == 6
    assert all(row["return_code"] == 0 for row in supervisor["processes"])
    assert result["readiness_metrics"]["shadow_process_restart_status"] == "passed"
    assert result["readiness_metrics"]["shadow_process_restart_count"] == 5
    assert result["readiness_metrics"]["shadow_process_max_recovery_seconds"] <= 60.0
    assert result["readiness_metrics"]["shadow_process_duplicate_state_count"] == 0
    assert result["readiness_metrics"]["shadow_planned_orders"] >= 20
    assert result["readiness_metrics"]["shadow_determinism_runs"] == 2
    assert result["external_network_calls"] == 0
    assert result["private_requests"] == 0
    assert result["trading_operations_count"] == 0


def test_process_supervisor_measures_wall_clock_separately_from_snapshot_time(tmp_path):
    input_path, config_path = _write_inputs(tmp_path, timestamp_step=3_600_000)
    result = run_process_supervisor(
        input_path=input_path,
        output_dir=tmp_path / "session",
        config_path=config_path,
        restart_count=5,
        deterministic_replay_runs=2,
        repo_root=REPO_ROOT,
    )

    assert result["status"] == "passed"
    assert result["covered_duration_hours"] > 20.0
    assert result["readiness_metrics"]["shadow_wall_clock_duration_seconds"] < 60.0


def test_process_supervisor_rejects_credential_like_input(tmp_path):
    input_path, config_path = _write_inputs(tmp_path)
    config_path.write_text(
        json.dumps({"strategy_id": "bad", "api_key": "must-not-be-read"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="credential-like"):
        run_process_supervisor(
            input_path=input_path,
            output_dir=tmp_path / "session",
            config_path=config_path,
            restart_count=5,
            repo_root=REPO_ROOT,
        )


def test_process_supervisor_requires_enough_snapshots_for_restarts(tmp_path):
    input_path, config_path = _write_inputs(tmp_path)
    input_path.write_text(json.dumps(_rows(5)), encoding="utf-8")
    with pytest.raises(ValueError, match="one snapshot"):
        run_process_supervisor(
            input_path=input_path,
            output_dir=tmp_path / "session",
            config_path=config_path,
            restart_count=5,
            repo_root=REPO_ROOT,
        )
