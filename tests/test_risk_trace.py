from __future__ import annotations

from research.risk_parity import make_decision, snapshot_hash
from research.risk_trace import (
    RiskTraceError,
    compare_live_export_with_rust_trace,
    compare_live_export_with_rust_trace_records,
    live_export_to_decisions,
    rust_trace_record_to_decision,
)


def _snapshot():
    return {
        "schema_version": "risk-snapshot-v1",
        "as_of_ms": 1,
        "balance": {"available": 1000.0, "equity": 1000.0},
        "positions": {"long": [], "short": []},
        "pending_orders": [],
        "mark_prices": {"TEST": 100.0},
        "margin_tiers": [
            {
                "symbol": "TEST",
                "tier": 1,
                "max_notional": 100000.0,
                "maintenance_margin_rate": 0.005,
            }
        ],
        "leverage": {"long": 3.0, "short": 3.0},
        "daily_loss": {"ratio": 0.0},
        "hsl": {"long": {"state": "normal"}, "short": {"state": "normal"}},
    }


def _rust_record(snapshot, *, decision="allow"):
    return {
        "step": 0,
        "timestamp_ms": 1,
        "candidate_index": 0,
        "order_id": "risk-order-v1:TEST:long:buy:entry_grid_normal_long:limit:ro=0:panic=0:5:100:0",
        "order_side": "buy",
        "symbol": "TEST",
        "position_side": "long",
        "order_type": "entry_grid_normal_long",
        "execution_type": "limit",
        "reduce_only": False,
        "panic_close": False,
        "qty": 5.0,
        "price": 100.0,
        "requested_notional": 500.0,
        "decision": decision,
        "reason": "portfolio_risk_gate_allow" if decision == "allow" else "portfolio_risk_gate_deny",
        "snapshot": snapshot,
    }


def test_rust_trace_becomes_a_canonical_decision():
    decision = rust_trace_record_to_decision(_rust_record(_snapshot()))

    assert decision["schema_version"] == "risk-decision-v1"
    assert decision["engine"] == "rust-backtest"
    assert decision["risk_metrics"] == {
        "daily_loss_ratio": "0",
        "hsl_state": "normal",
        "requested_notional": "500",
    }


def test_actual_live_and_rust_outputs_can_be_compared_on_one_snapshot():
    snapshot = _snapshot()
    digest = snapshot_hash(snapshot)
    identity = {
        "order_id": _rust_record(snapshot)["order_id"],
        "symbol": "TEST",
        "position_side": "long",
        "order_side": "buy",
        "strategy_order_type": "entry_grid_normal_long",
        "execution_type": "limit",
        "qty": 5.0,
        "price": 100.0,
        "reduce_only": False,
        "panic_close": False,
    }
    live_decision = make_decision(
        decision="allow",
        reason="portfolio_risk_gate_allow",
        side="long",
        order_identity=identity,
        risk_metrics={
            "requested_notional": 500.0,
            "daily_loss_ratio": 0.0,
            "hsl_state": "normal",
        },
        snapshot_hash_value=digest,
        engine="python-live-gate",
    )
    live_export = {"status": "passed", "snapshot": snapshot, "decisions": [live_decision]}

    comparison = compare_live_export_with_rust_trace(live_export, _rust_record(snapshot))

    assert comparison["parity"] == "passed"
    assert comparison["decision"] == "allow"


def test_live_export_rejects_a_blocked_snapshot():
    try:
        live_export_to_decisions({"status": "blocked", "reason": "missing tiers"})
    except RiskTraceError as exc:
        assert "missing tiers" in str(exc)
    else:  # pragma: no cover - assertion keeps the fail-closed contract explicit
        raise AssertionError("blocked live export must not become parity evidence")


def test_rust_blocked_snapshot_cannot_become_a_decision():
    blocked = _rust_record({"snapshot_status": "blocked"})

    try:
        rust_trace_record_to_decision(blocked)
    except RiskTraceError as exc:
        assert "blocked" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("blocked Rust snapshot must not become parity evidence")


def test_actual_trace_set_compares_every_order_and_reports_mismatch():
    snapshot = _snapshot()
    first = _rust_record(snapshot)
    second = dict(first)
    second["candidate_index"] = 1
    second["order_id"] = "risk-order-v1:TEST:long:buy:entry_grid_normal_long:limit:ro=0:panic=0:1:100:0"
    second["order_type"] = "entry_grid_normal_long"
    second["qty"] = 1.0
    second["requested_notional"] = 100.0
    second["decision"] = "deny"
    second["reason"] = "portfolio_risk_gate_deny"
    live_export = {
        "status": "passed",
        "snapshot": snapshot,
        "decisions": [
            make_decision(
                decision="allow",
                reason="portfolio_risk_gate_allow",
                side="long",
                order_identity={
                    "order_id": first["order_id"],
                    "symbol": "TEST",
                    "position_side": "long",
                    "order_side": "buy",
                    "strategy_order_type": "entry_grid_normal_long",
                    "execution_type": "limit",
                    "qty": 5.0,
                    "price": 100.0,
                    "reduce_only": False,
                    "panic_close": False,
                },
                risk_metrics={
                    "requested_notional": 500.0,
                    "daily_loss_ratio": 0.0,
                    "hsl_state": "normal",
                },
                snapshot=snapshot,
                engine="python-live-gate",
            ),
            make_decision(
                decision="allow",
                reason="portfolio_risk_gate_allow",
                side="long",
                order_identity={
                    "order_id": second["order_id"],
                    "symbol": "TEST",
                    "position_side": "long",
                    "order_side": "buy",
                    "strategy_order_type": "entry_grid_normal_long",
                    "execution_type": "limit",
                    "qty": 1.0,
                    "price": 100.0,
                    "reduce_only": False,
                    "panic_close": False,
                },
                risk_metrics={
                    "requested_notional": 100.0,
                    "daily_loss_ratio": 0.0,
                    "hsl_state": "normal",
                },
                snapshot=snapshot,
                engine="python-live-gate",
            ),
        ],
    }

    result = compare_live_export_with_rust_trace_records(live_export, [first, second])

    assert result["parity"] == "failed"
    assert result["fail_closed"] is True
    assert result["decision"] == "deny"
    assert result["risk_metrics"] == {"record_count": 2, "failed_count": 1}


def test_actual_trace_set_blocks_duplicate_order_identity():
    snapshot = _snapshot()
    record = _rust_record(snapshot)
    live_decision = make_decision(
        decision="allow",
        reason="portfolio_risk_gate_allow",
        side="long",
        order_identity={
            "order_id": record["order_id"],
            "symbol": "TEST",
            "position_side": "long",
            "order_side": "buy",
            "strategy_order_type": "entry_grid_normal_long",
            "execution_type": "limit",
            "qty": 5.0,
            "price": 100.0,
            "reduce_only": False,
            "panic_close": False,
        },
        risk_metrics={
            "requested_notional": 500.0,
            "daily_loss_ratio": 0.0,
            "hsl_state": "normal",
        },
        snapshot=snapshot,
        engine="python-live-gate",
    )
    try:
        compare_live_export_with_rust_trace_records(
            {"status": "passed", "snapshot": snapshot, "decisions": [live_decision]},
            [record, record],
        )
    except RiskTraceError as exc:
        assert "duplicate Rust order identity" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("duplicate identities must block actual parity")
