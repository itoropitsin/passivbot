from __future__ import annotations

import json

from research.risk_parity import evaluate_order, fixture_case
from research.risk_trace import rust_trace_record_to_decision
from tools.risk_parity_harness import compare_json_files, main


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_harness_blocks_when_an_engine_adapter_is_missing(tmp_path):
    case = fixture_case("initial_entry")
    snapshot_path = tmp_path / "snapshot.json"
    output_path = tmp_path / "comparison.json"
    _write(snapshot_path, case["snapshot"])

    result = compare_json_files(
        snapshot_path=snapshot_path,
        left_decision_path=None,
        right_decision_path=None,
    )

    assert result["parity"] == "blocked"
    assert result["decision"] == "deny"
    assert result["candidate_admission"] is False
    assert result["trading_operations_count"] == 0

    assert main(["--snapshot", str(snapshot_path), "--output", str(output_path)]) == 2
    assert json.loads(output_path.read_text(encoding="utf-8"))["decision"] == "deny"


def test_harness_compares_two_engine_outputs_and_fails_closed_on_difference(tmp_path):
    case = fixture_case("initial_entry")
    snapshot_path = tmp_path / "snapshot.json"
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write(snapshot_path, case["snapshot"])
    left = evaluate_order(case["snapshot"], case["request"], engine="python-live")
    right = evaluate_order(case["snapshot"], case["request"], engine="rust-tester")
    _write(left_path, left)
    _write(right_path, right)

    result = compare_json_files(
        snapshot_path=snapshot_path,
        left_decision_path=left_path,
        right_decision_path=right_path,
    )
    assert result["parity"] == "passed"
    assert result["decision"] == "allow"
    assert result["diagnostic"]["risk_parity_reached"] is False

    right["reason"] = "different"
    _write(right_path, right)
    failed = compare_json_files(
        snapshot_path=snapshot_path,
        left_decision_path=left_path,
        right_decision_path=right_path,
    )
    assert failed["parity"] == "failed"
    assert failed["decision"] == "deny"
    assert failed["fail_closed"] is True


def test_harness_compares_actual_live_export_and_rust_trace(tmp_path):
    case = fixture_case("initial_entry")
    snapshot = case["snapshot"]
    request = case["request"]
    trace = {
        "order_id": request["order_identity"]["order_id"],
        "order_side": request["order_identity"]["order_side"],
        "symbol": request["order_identity"]["symbol"],
        "position_side": request["order_identity"]["position_side"],
        "order_type": "entry_initial_normal_long",
        "execution_type": "limit",
        "reduce_only": False,
        "panic_close": False,
        "qty": request["qty"],
        "price": request["price"],
        "requested_notional": request["qty"] * request["price"],
        "decision": "allow",
        "reason": "portfolio_risk_gate_allow",
        "snapshot": snapshot,
    }
    rust_decision = rust_trace_record_to_decision(trace)
    live_decision = dict(rust_decision)
    live_decision["engine"] = "python-live-gate"

    snapshot_path = tmp_path / "snapshot.json"
    live_path = tmp_path / "live-export.json"
    trace_path = tmp_path / "rust-trace.json"
    _write(snapshot_path, snapshot)
    _write(live_path, {"status": "passed", "snapshot": snapshot, "decisions": [live_decision]})
    _write(trace_path, [trace])

    result = compare_json_files(
        snapshot_path=snapshot_path,
        left_decision_path=None,
        right_decision_path=None,
        live_export_path=live_path,
        rust_trace_path=trace_path,
    )

    assert result["parity"] == "passed"
    assert result["source_mode"] == "actual_engine_trace_set"
    assert result["candidate_admission"] is False
    assert result["trading_operations_count"] == 0


def test_harness_blocks_actual_mode_when_rust_trace_is_empty(tmp_path):
    case = fixture_case("initial_entry")
    snapshot_path = tmp_path / "snapshot.json"
    live_path = tmp_path / "live-export.json"
    trace_path = tmp_path / "rust-trace.json"
    _write(snapshot_path, case["snapshot"])
    _write(live_path, {"status": "blocked", "reason": "missing margin tiers"})
    _write(trace_path, [])

    result = compare_json_files(
        snapshot_path=snapshot_path,
        left_decision_path=None,
        right_decision_path=None,
        live_export_path=live_path,
        rust_trace_path=trace_path,
    )

    assert result["parity"] == "blocked"
    assert result["decision"] == "deny"
