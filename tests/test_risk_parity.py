from __future__ import annotations

from copy import deepcopy
import json

import pytest

from research.risk_parity import (
    canonical_json,
    canonical_snapshot_json,
    compare_decisions,
    evaluate_order,
    fixture_case,
    make_decision,
    normalize_snapshot,
    parity_diagnostic,
    snapshot_hash,
)


FIXTURE_NAMES = (
    "initial_entry",
    "max_notional",
    "liquidation_buffer",
    "panic_close",
    "unknown_position",
)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_reproducible_fixture_has_expected_decision(name):
    case = fixture_case(name)
    first = evaluate_order(case["snapshot"], case["request"], engine="fixture-a")
    second = evaluate_order(case["snapshot"], case["request"], engine="fixture-b")

    assert first["decision"] == case["expected"]["decision"]
    assert first["reason"] == case["expected"]["reason"]
    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert first["order_identity"] == second["order_identity"]
    assert first["risk_metrics"] == second["risk_metrics"]
    assert first["engine"] == "fixture-a"
    assert second["engine"] == "fixture-b"


def test_snapshot_canonicalization_is_order_and_float_spelling_independent():
    case = fixture_case("max_notional")
    original = deepcopy(case["snapshot"])
    changed = deepcopy(case["snapshot"])
    changed["balance"] = {"available": "1000.0", "equity": "1e3"}
    changed["mark_prices"] = {"BTC/USDT:USDT": "1.000e2"}
    changed["leverage"] = {"short": "3.0", "long": "3e0"}
    changed["positions"]["long"][0] = {
        "liquidation_price": "50.0",
        "mark_price": "100.0",
        "entry_price": "100e0",
        "notional": "790.0",
        "qty": "7.90",
        "symbol": "BTC/USDT:USDT",
    }

    assert canonical_snapshot_json(original) == canonical_snapshot_json(changed)
    assert snapshot_hash(original) == snapshot_hash(changed)
    assert normalize_snapshot(changed)["balance"]["equity"] == "1000"


def test_canonical_json_is_compact_sorted_and_rejects_non_finite_numbers():
    payload = {"z": 1.0, "a": {"value": "1.00"}, "items": [2, True]}
    assert json.loads(canonical_snapshot_json(fixture_case("initial_entry")["snapshot"]))["schema_version"] == "risk-snapshot-v1"
    assert '"a":{"value":"1"}' in canonical_json(payload)

    invalid = deepcopy(fixture_case("initial_entry")["snapshot"])
    invalid["mark_prices"]["BTC/USDT:USDT"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        snapshot_hash(invalid)


def test_matching_decisions_pass_but_diagnostic_does_not_claim_parity_reached():
    case = fixture_case("initial_entry")
    left = evaluate_order(case["snapshot"], case["request"], engine="python-live")
    right = evaluate_order(case["snapshot"], case["request"], engine="rust-tester")

    comparison = compare_decisions(left, right, snapshot=case["snapshot"])

    assert comparison["parity"] == "passed"
    assert comparison["fail_closed"] is False
    assert comparison["decision"] == "allow"
    assert comparison["side"] == "long"
    assert comparison["order_identity"]["order_id"] == "entry-001"
    assert comparison["diagnostic"]["status"] == "adapter_pending"
    assert comparison["diagnostic"]["second_engine_adapter_connected"] is False
    assert comparison["diagnostic"]["risk_parity_reached"] is False
    assert comparison["diagnostic"]["candidate_admission"] is False


def test_changed_reason_or_metric_fails_closed():
    case = fixture_case("initial_entry")
    left = evaluate_order(case["snapshot"], case["request"], engine="python-live")
    right = deepcopy(left)
    right["engine"] = "rust-tester"
    right["reason"] = "different_reason"

    comparison = compare_decisions(left, right)

    assert comparison["parity"] == "failed"
    assert comparison["fail_closed"] is True
    assert comparison["decision"] == "deny"
    assert comparison["reason"] == "engine decisions differ; parity fails closed"
    assert "reason" in comparison["differences"]

    right = deepcopy(left)
    right["risk_metrics"]["projected_exposure_ratio"] = "0.11"
    comparison = compare_decisions(left, right)
    assert comparison["decision"] == "deny"
    assert "risk_metrics" in comparison["differences"]


def test_different_snapshot_hash_fails_closed_even_when_decisions_are_otherwise_equal():
    case = fixture_case("initial_entry")
    left = evaluate_order(case["snapshot"], case["request"], engine="python-live")
    other_snapshot = deepcopy(case["snapshot"])
    other_snapshot["daily_loss"]["ratio"] = 0.006
    right = deepcopy(left)
    right["snapshot_hash"] = snapshot_hash(other_snapshot)

    comparison = compare_decisions(left, right)

    assert comparison["parity"] == "failed"
    assert comparison["fail_closed"] is True
    assert comparison["decision"] == "deny"
    assert comparison["differences"] == ["snapshot_hash"]


def test_invalid_second_decision_fails_closed_without_neutral_allow_fallback():
    case = fixture_case("initial_entry")
    left = evaluate_order(case["snapshot"], case["request"], engine="python-live")
    invalid_right = {"decision": "allow"}

    comparison = compare_decisions(left, invalid_right)

    assert comparison["parity"] == "failed"
    assert comparison["fail_closed"] is True
    assert comparison["decision"] == "deny"
    assert comparison["right_engine"] is None
    assert "invalid" in comparison["reason"]


def test_missing_required_snapshot_data_is_rejected():
    snapshot = deepcopy(fixture_case("initial_entry")["snapshot"])
    del snapshot["margin_tiers"]
    with pytest.raises(ValueError, match="margin_tiers"):
        normalize_snapshot(snapshot)


def test_snapshot_normalization_exposes_all_parity_surfaces_and_sorts_orders():
    snapshot = deepcopy(fixture_case("initial_entry")["snapshot"])
    snapshot["positions"] = {
        "short": [
            {
                "symbol": "ETH/USDT:USDT",
                "qty": 1,
                "notional": 100,
                "mark_price": 100,
                "liquidation_price": 150,
            }
        ],
        "long": [],
    }
    snapshot["mark_prices"]["ETH/USDT:USDT"] = 100
    snapshot["pending_orders"] = [
        {
            "id": "z-order",
            "symbol": "BTC/USDT:USDT",
            "position_side": "long",
            "order_side": "buy",
            "price": 99,
        },
        {
            "id": "a-order",
            "symbol": "ETH/USDT:USDT",
            "position_side": "short",
            "order_side": "sell",
            "price": 101,
        },
    ]

    normalized = normalize_snapshot(snapshot)

    assert set(normalized) >= {
        "balance",
        "positions",
        "pending_orders",
        "mark_prices",
        "margin_tiers",
        "leverage",
        "daily_loss",
        "hsl",
    }
    assert set(normalized["positions"]) == {"long", "short"}
    assert normalized["positions"]["short"][0]["position_side"] == "short"
    assert [order["order_id"] for order in normalized["pending_orders"]] == [
        "a-order",
        "z-order",
    ]
    assert normalized["pending_orders"][0]["price"] == "101"


def test_make_decision_requires_snapshot_binding_and_preserves_order_identity():
    case = fixture_case("initial_entry")
    digest = snapshot_hash(case["snapshot"])
    decision = make_decision(
        decision="allow",
        reason="fixture",
        side="long",
        order_identity=case["request"]["order_identity"],
        risk_metrics={"projected_exposure_ratio": 0.02},
        snapshot_hash_value=digest,
        engine="python-live",
    )
    assert decision["snapshot_hash"] == digest
    assert decision["side"] == decision["order_identity"]["position_side"]
    assert decision["risk_metrics"]["projected_exposure_ratio"] == "0.02"


def test_diagnostic_is_read_only_and_exposes_rust_python_integration_point():
    diagnostic = parity_diagnostic()
    assert diagnostic["trading_operations_count"] == 0
    assert diagnostic["integration_point"]["external_operations"] == "none"
    assert "Python live adapter" in diagnostic["integration_point"]["snapshot"]
    assert "Rust tester adapter" in diagnostic["integration_point"]["snapshot"]
