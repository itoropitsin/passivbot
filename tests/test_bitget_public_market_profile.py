from __future__ import annotations

import pytest

from tools.bitget_public_market_profile import build_public_market_profile


def _inputs() -> dict:
    return {
        "schema_version": "bitget-public-execution-inputs-v1",
        "exchange": "bitget",
        "symbol": "ETHUSDT",
        "category": "USDT-FUTURES",
        "collected_at_utc": "2026-08-17T03:00:00+00:00",
        "read_only_only": True,
        "trading_operations_count": 0,
        "response_sha256": {
            "current_funding": "a",
            "instruments": "b",
            "position_tiers": "c",
            "tickers": "d",
        },
        "documentation": {"instruments": "https://example.invalid/instruments"},
        "snapshots": {
            "instruments": {"data": [{
                "symbol": "ETHUSDT", "status": "online", "type": "perpetual",
                "baseCoin": "ETH", "quoteCoin": "USDT", "makerFeeRate": "0.0002",
                "takerFeeRate": "0.0006", "fundInterval": "8", "maxLeverage": "150",
                "minOrderAmount": "5", "minOrderQty": "0.01", "priceMultiplier": "0.01",
                "pricePrecision": "2", "quantityMultiplier": "0.01", "quantityPrecision": "2",
            }]},
            "tickers": {"data": [{
                "symbol": "ETHUSDT", "ts": "1786935970646", "markPrice": "1901.42",
                "indexPrice": "1902.5115", "bid1Price": "1901.30", "ask1Price": "1901.31",
            }]},
            "current_funding": {"data": [{
                "symbol": "ETHUSDT", "fundingRate": "0.000092",
                "fundingRateInterval": "8", "nextUpdate": "1786953600000",
            }]},
            "position_tiers": {"data": [{
                "tier": "1", "minTierValue": "0", "maxTierValue": "200000",
                "mmr": "0.004", "leverage": "150",
            }]},
        },
    }


def test_builds_public_profile_without_private_fields():
    profile = build_public_market_profile(_inputs())
    assert profile["schema_version"] == "bitget-public-market-profile-v1"
    assert profile["symbol"] == "ETHUSDT"
    assert profile["instrument"]["funding_interval_hours"] == 8
    assert profile["funding"]["current_rate"] == pytest.approx(0.000092)
    assert profile["funding"]["interval_hours"] == 8
    assert profile["mark_and_index"]["mark_price"] == 1901.42
    assert profile["margin_tiers"][0]["max_leverage"] == 150.0
    assert profile["read_only_only"] is True
    assert profile["trading_operations_count"] == 0
    assert "account_controls" not in profile
    assert "responses" not in profile


def test_rejects_missing_collection_timestamp():
    value = _inputs()
    value.pop("collected_at_utc")
    with pytest.raises(ValueError, match="collected_at_utc"):
        build_public_market_profile(value)


@pytest.mark.parametrize(
    "change",
    [
        {"read_only_only": False},
        {"trading_operations_count": 1},
        {"schema_version": "bitget-uta-market-profile-v1"},
    ],
)
def test_rejects_non_public_or_wrong_schema(change):
    value = _inputs()
    value.update(change)
    with pytest.raises(ValueError):
        build_public_market_profile(value)
