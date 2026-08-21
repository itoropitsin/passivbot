from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from research.bitget_market_surfaces import (
    BitgetMarketSurfaceError,
    _INT64_MAX,
    _INT64_MIN,
    _integer,
    normalize_bitget_public_market_surface,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_market_profile_read_only.json"


def _responses() -> dict:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    return profile["responses"]


def _surface(**overrides):
    responses = _responses()
    params = {
        "internal_symbol": "BTC/USDT:USDT",
        "exchange_symbol": "BTCUSDT",
        "category": "USDT-FUTURES",
        "instrument_response": responses["market_instruments"],
        "tier_response": responses["market_position_tier"],
        "ticker_response": responses["market_tickers"],
        "funding_response": responses["market_current_fund_rate"],
        "contract_size": 1.0,
    }
    params.update(overrides)
    return normalize_bitget_public_market_surface(**params)


def test_normalizes_captured_bitget_public_responses_without_defaults():
    surface = _surface()

    assert surface["symbol"] == "BTC/USDT:USDT"
    assert surface["exchange_symbol"] == "BTCUSDT"
    assert surface["mark_price"] == pytest.approx(62878.3)
    assert surface["index_price"] == pytest.approx(62902.3305)
    assert surface["taker_fee"] == pytest.approx(0.0006)
    assert surface["qty_step"] == pytest.approx(0.0001)
    assert surface["contract_size"] == pytest.approx(1.0)
    assert surface["funding"]["interval_hours"] == pytest.approx(8.0)
    assert surface["margin_tiers"][0]["min_notional"] == 0.0
    assert surface["margin_tiers"][1]["min_notional"] == 200_000.0
    assert len(surface["source_response_sha256"]) == 4
    assert len(surface["surface_hash"]) == 64


@pytest.mark.parametrize(
    "value",
    [
        1_786_925_721_429,
        "1786925721429",
        0,
        -1,
        _INT64_MIN,
        _INT64_MAX,
        str(_INT64_MIN),
        str(_INT64_MAX),
    ],
)
def test_integer_parser_keeps_valid_integers_unchanged(value):
    expected = int(value)
    assert _integer(value, field="ts") == expected
    assert _integer(value, field="ts") is not True


def test_integer_parser_rejects_float_instead_of_truncating():
    with pytest.raises(BitgetMarketSurfaceError, match="must be an integer"):
        _integer(1.9, field="market_tickers.ts")
    with pytest.raises(BitgetMarketSurfaceError, match="must be an integer"):
        _integer(1.0, field="market_tickers.ts")


@pytest.mark.parametrize("value", ["1.9", "1.0", "1e3", "+1786925721429", " 1786925721429", "1_786"])
def test_integer_parser_rejects_numeric_strings_that_are_not_integers(value):
    with pytest.raises(BitgetMarketSurfaceError, match="must be an integer"):
        _integer(value, field="market_tickers.ts")


@pytest.mark.parametrize("value", [True, False])
def test_integer_parser_rejects_bool(value):
    with pytest.raises(BitgetMarketSurfaceError, match="is required"):
        _integer(value, field="market_tickers.ts")


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, float("nan")])
def test_integer_parser_rejects_nan_and_non_finite_floats(value):
    with pytest.raises(BitgetMarketSurfaceError, match="must be an integer"):
        _integer(value, field="market_tickers.ts")


@pytest.mark.parametrize(
    "value",
    [
        _INT64_MAX + 1,
        _INT64_MIN - 1,
        str(_INT64_MAX + 1),
        str(_INT64_MIN - 1),
        "9" * 40,
    ],
)
def test_integer_parser_rejects_overflow(value):
    with pytest.raises(BitgetMarketSurfaceError, match="overflows"):
        _integer(value, field="market_tickers.ts")


def test_fractional_ticker_timestamp_is_rejected_by_surface():
    responses = _responses()
    ticker = copy.deepcopy(responses["market_tickers"])
    ticker["data"][0]["ts"] = 1.9

    with pytest.raises(BitgetMarketSurfaceError, match="market_tickers.ts"):
        _surface(ticker_response=ticker)


def test_valid_integer_ticker_timestamp_is_unchanged():
    responses = _responses()
    ticker = copy.deepcopy(responses["market_tickers"])
    ticker["data"][0]["ts"] = 1_786_925_721_429

    surface = _surface(ticker_response=ticker)

    assert surface["observed_at_ms"] == 1_786_925_721_429


def test_fractional_next_funding_timestamp_is_rejected_by_surface():
    responses = _responses()
    funding = copy.deepcopy(responses["market_current_fund_rate"])
    funding["data"][0]["nextUpdate"] = 1.9

    with pytest.raises(BitgetMarketSurfaceError, match="nextUpdate"):
        _surface(funding_response=funding)


def test_missing_mark_price_is_blocked_instead_of_filled_from_last_price():
    responses = _responses()
    ticker = copy.deepcopy(responses["market_tickers"])
    del ticker["data"][0]["markPrice"]

    with pytest.raises(BitgetMarketSurfaceError, match="markPrice"):
        _surface(ticker_response=ticker)


def test_tier_gap_is_blocked_instead_of_using_a_nearest_tier():
    responses = _responses()
    tiers = copy.deepcopy(responses["market_position_tier"])
    tiers["data"][1]["minTierValue"] = "250000"

    with pytest.raises(BitgetMarketSurfaceError, match="gap or overlap"):
        _surface(tier_response=tiers)


def test_contract_size_is_required_even_when_quantity_step_is_known():
    with pytest.raises(BitgetMarketSurfaceError, match="contract_size"):
        _surface(contract_size=None)


def test_funding_interval_mismatch_is_blocked():
    responses = _responses()
    funding = copy.deepcopy(responses["market_current_fund_rate"])
    funding["data"][0]["fundingRateInterval"] = "4"

    with pytest.raises(BitgetMarketSurfaceError, match="intervals do not match"):
        _surface(funding_response=funding)


@pytest.mark.asyncio
async def test_bitget_refresh_joins_public_endpoints_and_stores_risk_inputs():
    from exchanges.bitget import BitgetBot

    responses = _responses()
    calls: list[tuple[str, dict]] = []

    class PublicClient:
        async def public_uta_get_v3_market_instruments(self, params):
            calls.append(("instruments", params))
            return copy.deepcopy(responses["market_instruments"])

        async def public_uta_get_v3_market_position_tier(self, params):
            calls.append(("tiers", params))
            return copy.deepcopy(responses["market_position_tier"])

        async def public_uta_get_v3_market_tickers(self, params):
            calls.append(("tickers", params))
            return copy.deepcopy(responses["market_tickers"])

        async def public_uta_get_v3_market_current_fund_rate(self, params):
            calls.append(("funding", params))
            return copy.deepcopy(responses["market_current_fund_rate"])

    bot = BitgetBot.__new__(BitgetBot)
    bot.config = {"live": {"portfolio_risk": {"enabled": True}}}
    bot.is_uta = True
    bot.cca = PublicClient()
    bot.markets_dict = {
        "BTC/USDT:USDT": {"id": "BTCUSDT", "contractSize": 1.0}
    }
    bot.symbol_ids = {"BTC/USDT:USDT": "BTCUSDT"}
    bot.c_mults = {"BTC/USDT:USDT": 1.0}
    bot._crypto_market_surfaces = {}

    refreshed = await bot.refresh_crypto_market_surfaces(["BTC/USDT:USDT"])

    assert list(refreshed) == ["BTC/USDT:USDT"]
    assert bot._crypto_mark_prices == {"BTC/USDT:USDT": pytest.approx(62878.3)}
    assert bot._crypto_index_prices["BTC/USDT:USDT"] == pytest.approx(62902.3305)
    assert bot._crypto_margin_tiers[0]["symbol"] == "BTC/USDT:USDT"
    assert len(bot._crypto_surface_hashes["BTC/USDT:USDT"]) == 64
    assert len(calls) == 4
    assert all(params == {"category": "USDT-FUTURES", "symbol": "BTCUSDT"} for _, params in calls)


@pytest.mark.asyncio
async def test_bitget_refresh_rejects_classic_account_before_public_surface_use():
    from exchanges.bitget import BitgetBot

    bot = BitgetBot.__new__(BitgetBot)
    bot.config = {"live": {"portfolio_risk": {"enabled": True}}}
    bot.is_uta = False
    bot.cca = SimpleNamespace()

    with pytest.raises(RuntimeError, match="requires a detected UTA account"):
        await bot.refresh_crypto_market_surfaces(["BTC/USDT:USDT"])


@pytest.mark.asyncio
async def test_crypto_exchange_setup_fails_closed_when_hedge_mode_is_rejected():
    from exchanges.bitget import BitgetBot

    bot = BitgetBot.__new__(BitgetBot)
    bot.exchange = "bitget"
    bot.config = {
        "live": {
            "portfolio_risk": {"enabled": True},
            "crypto_live_authorized": True,
        }
    }
    bot._crypto_live_authorized = True
    bot.active_symbols = []
    bot.is_uta = True
    bot._detect_account_mode = AsyncMock()
    bot.cca = SimpleNamespace(
        set_position_mode=AsyncMock(side_effect=Exception("hedge rejected"))
    )

    with pytest.raises(RuntimeError, match="hedge mode could not be confirmed"):
        await bot.update_exchange_config()


@pytest.mark.asyncio
async def test_crypto_symbol_setup_fails_closed_when_leverage_is_rejected():
    from exchanges.bitget import BitgetBot

    bot = BitgetBot.__new__(BitgetBot)
    bot.exchange = "bitget"
    bot.config = {
        "live": {
            "portfolio_risk": {"enabled": True},
            "crypto_live_authorized": True,
        }
    }
    bot._crypto_live_authorized = True
    bot.is_uta = True
    bot.cca = SimpleNamespace(
        set_leverage=AsyncMock(side_effect=Exception("leverage rejected"))
    )
    bot._get_margin_mode_for_symbol = lambda _symbol: "isolated"
    bot._calc_leverage_for_symbol = lambda _symbol: 3
    bot.refresh_crypto_market_surfaces = AsyncMock(
        return_value={"BTC/USDT:USDT": {}}
    )

    with pytest.raises(RuntimeError, match="account configuration was not applied"):
        await bot.update_exchange_config_by_symbols(["BTC/USDT:USDT"])
