from __future__ import annotations

from copy import deepcopy

from passivbot import Passivbot
from research.live_risk_snapshot import (
    build_live_risk_snapshot,
    export_live_gate_decisions,
    stable_order_id_v1,
)


def _bot():
    bot = Passivbot.__new__(Passivbot)
    bot.balance_raw = 1000.0
    bot.balance = 1000.0
    bot.config = {"live": {"leverage": 3.0}}
    bot.positions = {}
    bot.open_orders = {}
    bot.fetched_positions = []
    bot.c_mults = {"BTC/USDT:USDT": 1.0}
    bot._crypto_daily_loss_ratio = 0.0
    bot._crypto_hsl_state = {"long": {"state": "normal"}, "short": {"state": "normal"}}
    bot._crypto_margin_tiers = [
        {
            "symbol": "BTC/USDT:USDT",
            "tier": 1,
            "max_notional": 100000.0,
            "maintenance_margin_rate": 0.005,
        }
    ]
    bot.get_raw_balance = lambda: 1000.0
    bot.get_sizing_balance = lambda: 1000.0
    return bot


def test_stable_order_id_matches_the_cross_engine_contract():
    assert (
        stable_order_id_v1(
            symbol="TEST",
            position_side="long",
            order_side="buy",
            order_type="entry_grid_normal_long",
            execution_type="limit",
            qty=5.0,
            price=100.0,
            reduce_only=False,
            panic_close=False,
            ordinal=2,
        )
        == "risk-order-v1:TEST:long:buy:entry_grid_normal_long:limit:ro=0:panic=0:5:100:2"
    )


def test_live_snapshot_uses_actual_positions_and_liquidation_surface():
    bot = _bot()
    bot.positions = {
        "BTC/USDT:USDT": {"long": {"size": 1.0, "price": 100.0}, "short": {"size": 0.0, "price": 0.0}}
    }
    bot.fetched_positions = [
        {
            "symbol": "BTC/USDT:USDT",
            "position_side": "long",
            "markPrice": 100.0,
            "liquidationPrice": 70.0,
        }
    ]

    snapshot = build_live_risk_snapshot(bot, {"BTC/USDT:USDT": 100.0}, as_of_ms=1)

    position = snapshot["positions"]["long"][0]
    assert position["liquidation_price"] == "70"
    assert position["notional"] == "100"
    assert snapshot["leverage"] == {"long": "3", "short": "3"}


def test_live_pending_order_uses_the_same_stable_identity():
    bot = _bot()
    bot.open_orders = {
        "BTC/USDT:USDT": [
            (0.2, 100.0, "entry_grid_normal_long", 0, "limit"),
        ]
    }

    snapshot = build_live_risk_snapshot(bot, {"BTC/USDT:USDT": 100.0}, as_of_ms=1)

    assert snapshot["pending_orders"][0]["order_id"] == (
        "risk-order-v1:BTC/USDT:USDT:long:buy:entry_grid_normal_long:limit:ro=0:panic=0:0.2:100:0"
    )


def test_live_export_records_actual_allowed_and_blocked_orders_without_mutation():
    bot = _bot()
    allowed = (0.2, 100.0, "entry_initial_normal_long", 0, "limit")
    blocked = (0.2, 100.0, "entry_initial_normal_long", 1, "market")
    original = {"BTC/USDT:USDT": [allowed, blocked]}
    filtered = {"BTC/USDT:USDT": [allowed]}

    exported = export_live_gate_decisions(
        bot,
        original,
        filtered,
        {"BTC/USDT:USDT": 100.0},
        as_of_ms=1,
        margin_tiers=bot._crypto_margin_tiers,
    )

    assert exported["status"] == "passed"
    assert [item["decision"] for item in exported["decisions"]] == ["allow", "deny"]
    assert exported["candidate_admission"] is False
    assert exported["trading_operations_count"] == 0
    assert filtered == {"BTC/USDT:USDT": [allowed]}


def test_live_export_blocks_when_required_surface_is_missing():
    bot = _bot()
    bot._crypto_margin_tiers = None

    exported = export_live_gate_decisions(
        bot,
        {"BTC/USDT:USDT": [(0.2, 100.0, "entry_initial_normal_long", 0, "limit")]},
        {},
        {"BTC/USDT:USDT": 100.0},
        as_of_ms=1,
    )

    assert exported["status"] == "blocked"
    assert "margin_tiers" in exported["reason"]
    assert exported["decisions"] == []


def test_live_export_rejects_missing_hsl_surface_instead_of_defaulting():
    bot = _bot()
    bot._crypto_hsl_state = None

    exported = export_live_gate_decisions(
        bot,
        {"BTC/USDT:USDT": [(0.2, 100.0, "entry_initial_normal_long", 0, "limit")]},
        {},
        {"BTC/USDT:USDT": 100.0},
        as_of_ms=1,
        margin_tiers=bot._crypto_margin_tiers,
    )

    assert exported["status"] == "blocked"
    assert "hsl" in exported["reason"]
