from __future__ import annotations

import json
from pathlib import Path

import pytest

from tli_wings import cli


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs" / "tli" / "runtime_policy.json"


@pytest.mark.parametrize("command", ["live"])
def test_live_class_commands_fail_before_policy_loading(command, monkeypatch, capsys, tmp_path):
    def unexpected_policy_load(_path):
        pytest.fail("policy must not load before the live authorization check")

    monkeypatch.setattr(cli, "load_runtime_policy", unexpected_policy_load)

    result = cli.main([command, "--policy", str(tmp_path / "missing.json")])

    assert result == 2
    assert "--authorize-live" in capsys.readouterr().err


def test_preflight_is_local_and_prints_policy_hash(capsys):
    result = cli.main(
        ["preflight", "--policy", str(POLICY_PATH), "--json"]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "preflight"
    assert payload["status"] == "ok"
    assert payload["network_used"] is False
    assert payload["keys_loaded"] is False
    assert payload["exchange_calls"] == 0
    assert len(payload["policy_hash"]) == 64
    assert payload["risk_hook"] == {
        "actions": 0,
        "allow_new_entries": True,
        "status": "passed",
    }


@pytest.mark.parametrize(
    ("config_name", "active_side"),
    [("btc_long.json", "long"), ("btc_short.json", "short")],
)
def test_smoke_configs_are_complete_v81_and_one_direction(config_name, active_side):
    config_path = ROOT / "configs" / "tli" / config_name

    result = cli.main(
        [
            "preflight",
            "--policy",
            str(POLICY_PATH),
            "--config",
            str(config_path),
            "--json",
        ]
    )

    assert result == 0
    # The CLI result is deliberately machine-readable, so this test exercises
    # the same validation boundary without importing the trading runtime.
    # stdout is checked by the direct helper below to keep the assertion clear.
    info = cli._validate_project_config(config_path)
    assert info["active_directions"] == [active_side]
    assert info["approved_coins"][active_side] == ["BTC"]
    assert info["approved_coins"]["short" if active_side == "long" else "long"] == []


def test_authorized_live_class_command_remains_diagnostic_only(capsys):
    result = cli.main(
        ["live", "--authorize-live", "--policy", str(POLICY_PATH), "--json"]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["live_authorized"] is True
    assert payload["live_execution"] is False
    assert payload["network_used"] is False
    assert payload["keys_loaded"] is False
    assert payload["exchange_calls"] == 0
    assert payload["orders_submitted"] == 0


def test_evidence_command_builds_fingerprint_and_keeps_admission_closed(capsys):
    result = cli.main(
        ["evidence", "--policy", str(POLICY_PATH), "--json"]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["version_fingerprint"]["fingerprint"]) == 64
    assert payload["data_quality"]["actual_rows"] == 248
    assert payload["risk_evidence"]["admission"] is False
    assert payload["risk_evidence"]["status"] == "blocked"


def test_paper_command_starts_with_empty_v3_state(capsys):
    result = cli.main(
        ["paper", "--policy", str(POLICY_PATH), "--json"]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["paper"]["wal_schema"] == "crypto-paper-wal-v3"
    assert payload["paper"]["records"] == 0
    assert payload["paper"]["safe_to_continue"] is False
