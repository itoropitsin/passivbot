from __future__ import annotations

from types import SimpleNamespace

import pytest

from exchanges.bitget import BitgetBot


def _bot() -> BitgetBot:
    bot = BitgetBot.__new__(BitgetBot)
    bot.config = {
        "live": {
            "time_in_force": "post_only",
            "margin_mode_preference": "isolated",
            "leverage": 3,
        }
    }
    bot.is_uta = True
    bot.markets_dict = {}
    bot.positions = {}
    bot.open_orders = {}
    bot._live_margin_modes = {}
    bot._calc_leverage_for_symbol = lambda _symbol: 3
    bot.get_symbol_id = lambda symbol: "BTCUSDT"
    return bot


def test_bitget_uta_order_contains_isolated_hold_side_and_leverage():
    bot = _bot()
    params = bot._build_order_params(
        {
            "symbol": "BTC/USDT:USDT",
            "position_side": "short",
            "reduce_only": True,
            "custom_id": "0x0007abcdef",
        }
    )

    assert params["marginMode"] == "isolated"
    assert params["holdSide"] == "short"
    assert params["leverage"] == 3
    assert params["posSide"] == "short"
    assert params["postOnly"] is True
    assert "reduceOnly" not in params


@pytest.mark.asyncio
async def test_bitget_uta_sets_leverage_separately_for_both_position_sides():
    bot = _bot()
    calls = []

    async def set_leverage(leverage, *, symbol, params=None):
        calls.append((leverage, symbol, params))
        return {"leverage": leverage}

    bot.cca = SimpleNamespace(set_leverage=set_leverage, has={})
    bot._get_margin_mode_for_symbol = lambda _symbol: "isolated"

    await bot.update_exchange_config_by_symbols(["BTC/USDT:USDT"])

    assert calls == [
        (
            3,
            "BTC/USDT:USDT",
            {"uta": True, "marginMode": "isolated", "posSide": "long"},
        ),
        (
            3,
            "BTC/USDT:USDT",
            {"uta": True, "marginMode": "isolated", "posSide": "short"},
        ),
    ]
