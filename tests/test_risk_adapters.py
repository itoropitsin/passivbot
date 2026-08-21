from __future__ import annotations

import json

import pytest

from research.risk_adapters import (
    RiskAdapterUnavailable,
    compare_adapters,
    python_live_decision,
    rust_tester_decision,
)
from research.risk_parity import fixture_case


def test_python_live_adapter_is_a_real_named_adapter():
    case = fixture_case("initial_entry")
    decision = python_live_decision(case["snapshot"], case["request"])
    assert decision["engine"] == "python-live"
    assert decision["snapshot_hash"]
    assert decision["decision"] == "allow"


def test_rust_tester_adapter_uses_the_compiled_extension():
    case = fixture_case("initial_entry")
    try:
        decision = rust_tester_decision(case["snapshot"], case["request"])
    except RiskAdapterUnavailable as exc:
        pytest.fail(str(exc))
    assert decision["engine"] == "rust-tester"
    assert decision["snapshot_hash"]
    assert decision["decision"] == "allow"


@pytest.mark.parametrize("name", ["initial_entry", "max_notional", "panic_close", "unknown_position"])
def test_python_and_compiled_rust_adapters_match_on_decision_boundary(name):
    case = fixture_case(name)
    result = compare_adapters(case["snapshot"], case["request"])
    assert result["parity"] == "passed"
    assert result["fail_closed"] is False
    assert result["diagnostic"]["status"] == "connected"
    assert result["diagnostic"]["risk_parity_reached"] is True
    assert result["candidate_admission"] is False
    assert result["trading_operations_count"] == 0


def test_harness_real_adapter_mode_is_read_only(tmp_path):
    from tools.risk_parity_harness import compare_json_files

    case = fixture_case("initial_entry")
    snapshot = tmp_path / "snapshot.json"
    request = tmp_path / "request.json"
    snapshot.write_text(json.dumps(case["snapshot"]), encoding="utf-8")
    request.write_text(json.dumps(case["request"]), encoding="utf-8")

    result = compare_json_files(
        snapshot_path=snapshot,
        left_decision_path=None,
        right_decision_path=None,
        request_path=request,
    )
    assert result["diagnostic"]["trading_operations_count"] == 0
    assert result["candidate_admission"] is False
