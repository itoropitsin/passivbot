from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from passivbot import Passivbot, enforce_crypto_live_authorization
from passivbot_exceptions import FatalBotException


def _config(*, enabled: bool = True, require_explicit: bool = True) -> dict:
    return {
        "live": {
            "portfolio_risk": {
                "enabled": enabled,
                "require_explicit_live_authorization": require_explicit,
            }
        }
    }


def test_disabled_crypto_profile_does_not_add_a_live_barrier():
    enforce_crypto_live_authorization(_config(enabled=False), "bitget")


def test_fake_exchange_does_not_need_real_live_authorization():
    enforce_crypto_live_authorization(_config(), "fake")


def test_explicit_paper_mode_does_not_need_real_live_authorization():
    config = _config()
    config["_paper_mode"] = True
    enforce_crypto_live_authorization(config, "bitget")


def test_real_exchange_requires_separate_authorization():
    with pytest.raises(FatalBotException, match="not authorized"):
        enforce_crypto_live_authorization(_config(), "bitget")


def test_authorized_real_exchange_is_allowed_by_this_barrier():
    enforce_crypto_live_authorization(_config(), "bitget", authorized=True)


def test_profile_cannot_disable_the_explicit_authorization_requirement():
    with pytest.raises(FatalBotException, match="must require"):
        enforce_crypto_live_authorization(
            _config(require_explicit=False), "bitget", authorized=True
        )


@pytest.mark.asyncio
async def test_order_write_is_blocked_even_when_called_below_the_cli():
    bot = Passivbot.__new__(Passivbot)
    bot.config = _config()
    bot.exchange = "bitget"
    bot._crypto_live_authorized = False
    bot.cca = SimpleNamespace(create_order=AsyncMock())

    with pytest.raises(FatalBotException, match="not authorized"):
        await bot.execute_order(
            {
                "symbol": "BTC/USDT:USDT",
                "type": "limit",
                "side": "buy",
                "qty": 0.001,
                "price": 50_000.0,
            }
        )

    bot.cca.create_order.assert_not_awaited()
