from __future__ import annotations

import pytest

from tli_wings import bitget_public as public
from tli_wings.order_gate import TliOrderGateError, gate_planned_orders
from tli_wings.public_profile import PublicProfileError, build_public_profile
from tli_wings.risk import empty_portfolio_snapshot
from tli_wings.runtime_policy import load_runtime_policy

ROOT_POLICY = "configs/tli/runtime_policy.json"


@pytest.fixture
def freshness():
    return public.FreshnessPolicy(now_ms=1_700_000_001_000, max_age_ms=2_000)


@pytest.fixture
def surface_inputs():
    return {
        "instrument": {
            "code": "00000",
            "requestTime": 1_700_000_000_000,
            "data": [{
                "symbol": "BTCUSDT", "category": "USDT-FUTURES",
                "baseCoin": "BTC", "quoteCoin": "USDT", "settleCoin": "USDT",
                "minOrderQty": "0.001", "quantityMultiplier": "0.001",
                "pricePrecision": "1", "priceMultiplier": "0.1",
                "maxLeverage": "125", "status": "online",
            }],
        },
        "mark": {
            "code": "00000",
            "requestTime": 1_700_000_000_900,
            "data": [{
                "symbol": "BTCUSDT", "category": "USDT-FUTURES",
                "markPrice": "100.5", "indexPrice": "100.0", "ts": "1700000000800",
            }],
        },
        "funding": {
            "code": "00000",
            "requestTime": 1_700_000_000_700,
            "data": [{
                "symbol": "BTCUSDT", "category": "USDT-FUTURES",
                "fundingRate": "-0.0001", "fundingRateInterval": "8",
                "nextUpdate": "1700000028800",
            }],
        },
        "tiers": {
            "code": "00000",
            "requestTime": 1_700_000_000_600,
            "data": [
                {"tier": "1", "symbol": "BTCUSDT", "category": "USDT-FUTURES",
                 "minTierValue": "0", "maxTierValue": "100000", "leverage": "125", "mmr": "0.004"},
                {"tier": "2", "symbol": "BTCUSDT", "category": "USDT-FUTURES",
                 "minTierValue": "100000", "maxTierValue": "500000", "leverage": "50", "mmr": "0.005"},
            ],
        },
    }


def test_profile_blocks_when_one_public_surface_is_unavailable(
    surface_inputs, freshness
):
    results = [
        public.normalize_instrument_payload(
            surface_inputs["instrument"], freshness=freshness,
            expected_symbol="BTCUSDT", expected_contract="USDT-FUTURES",
        ),
        public.normalize_mark_index_payload(
            surface_inputs["mark"], freshness=freshness,
            expected_symbol="BTCUSDT", expected_contract="USDT-FUTURES",
        ),
        public.normalize_funding_payload(
            surface_inputs["funding"], freshness=freshness,
            expected_symbol="BTCUSDT", expected_contract="USDT-FUTURES",
        ),
        public.normalize_maintenance_tiers_payload(
            surface_inputs["tiers"], freshness=freshness,
            expected_symbol="BTCUSDT", expected_contract="USDT-FUTURES",
        ),
    ]
    missing = public.normalize_funding_payload(
        {"code": "00000", "data": []},
        freshness=freshness,
        expected_symbol="BTCUSDT",
        expected_contract="USDT-FUTURES",
    )
    profile = build_public_profile(results[0], results[1], missing, results[3])
    assert profile.entry_ready is False
    assert "funding:empty_surface" in profile.reasons
    with pytest.raises(PublicProfileError):
        profile.require_entry_ready()


def test_order_gate_keeps_protective_order_and_blocks_missing_entry_data():
    policy = load_runtime_policy(ROOT_POLICY)
    orders = [
        {
            "id": "entry-1",
            "symbol": "BTC/USDT:USDT",
            "risk": {
                "side": "long",
                "kind": "initial_entry",
                "exposure_usdt": 30.01,
                "margin_usdt": 10.0,
                "liquidation_distance_pct": 0.30,
                "leverage": 3.0,
                "opens_grid": True,
            },
        },
        {
            "id": "panic-1",
            "symbol": "BTC/USDT:USDT",
            "risk": {
                "side": "long",
                "kind": "panic",
                "exposure_usdt": 1000.0,
                "margin_usdt": 1000.0,
                "liquidation_distance_pct": 0.15,
                "leverage": 3.0,
                "opens_grid": False,
            },
        },
    ]
    result = gate_planned_orders(orders, empty_portfolio_snapshot(), policy)
    assert [order["id"] for order in result["blocked_orders"]] == ["entry-1"]
    assert [order["id"] for order in result["allowed_orders"]] == ["panic-1"]


def test_order_gate_does_not_infer_missing_risk_fields():
    policy = load_runtime_policy(ROOT_POLICY)
    order = {"symbol": "BTC/USDT:USDT", "risk": {"kind": "initial_entry"}}
    with pytest.raises(TliOrderGateError, match="risk is incomplete"):
        gate_planned_orders([order], empty_portfolio_snapshot(), policy)
