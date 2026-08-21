from __future__ import annotations

import pytest

import passivbot_rust as pbr
from passivbot import Passivbot
from research.risk_policy import select_maintenance_margin_rate


SYMBOL = "BTC/USDT:USDT"


def _tiers():
    return [
        {
            "symbol": SYMBOL,
            "tier": 1,
            "min_notional": 0.0,
            "max_notional": 200.0,
            "maintenance_margin_rate": 0.004,
        },
        {
            "symbol": SYMBOL,
            "tier": 2,
            "min_notional": 200.0,
            "max_notional": 1000.0,
            "maintenance_margin_rate": 0.005,
        },
    ]


def _bot():
    bot = Passivbot.__new__(Passivbot)
    bot.balance_raw = 1000.0
    bot.balance = 1000.0
    bot.config = {
        "live": {
            "portfolio_risk": {
                "enabled": True,
                "max_initial_entry_sizing_balance_pct": 0.03,
            },
            "leverage": 3.0,
            "sizing_balance_cap": 1000.0,
        }
    }
    bot.positions = {
        SYMBOL: {
            "long": {"size": 0.0, "price": 0.0},
            "short": {"size": 0.0, "price": 0.0},
        }
    }
    bot.open_orders = {}
    bot.fetched_positions = []
    bot.c_mults = {SYMBOL: 1.0}
    bot.qty_steps = {SYMBOL: 0.001}
    bot.markets_dict = {SYMBOL: {"taker": 0.00055}}
    bot._crypto_daily_loss_ratio = 0.0
    bot._crypto_min_liquidation_distance = None
    bot._crypto_margin_tiers = _tiers()
    bot._crypto_hsl_state = {
        "long": {"state": "normal"},
        "short": {"state": "normal"},
    }
    bot.get_raw_balance = lambda: 1000.0
    bot.get_sizing_balance = lambda: 1000.0
    return bot


def test_margin_tier_boundaries_are_explicit_and_deterministic():
    assert select_maintenance_margin_rate(_tiers(), symbol=SYMBOL, notional=199.99) == (
        0.004,
        "1",
    )
    assert select_maintenance_margin_rate(_tiers(), symbol=SYMBOL, notional=200.0) == (
        0.005,
        "2",
    )
    assert select_maintenance_margin_rate(_tiers(), symbol=SYMBOL, notional=1000.0) == (
        0.005,
        "2",
    )
    assert select_maintenance_margin_rate(_tiers(), symbol="ETH/USDT:USDT", notional=10.0) is None


def test_rust_liquidation_formula_is_available_to_live_boundary():
    distance = pbr.calc_liquidation_distance_for_position(
        1.0,
        100.0,
        100.0,
        1.0,
        3.0,
        0.005,
        0.00055,
        True,
    )

    assert distance == pytest.approx(0.32961268372802394)


def test_python_gate_projects_initial_fill_into_next_grid_level():
    bot = _bot()
    orders = {
        SYMBOL: [
            (0.1, 100.0, "entry_initial_normal_long", 0, "limit"),
            (0.2, 90.0, "entry_grid_normal_long", 1, "limit"),
        ]
    }

    filtered = bot._apply_crypto_portfolio_risk_limits(orders, {SYMBOL: 100.0})

    assert filtered[SYMBOL] == orders[SYMBOL]


def test_python_gate_denies_grid_when_projected_distance_is_below_limit():
    bot = _bot()
    orders = {
        SYMBOL: [
            (0.1, 100.0, "entry_initial_normal_long", 0, "limit"),
            (0.2, 130.0, "entry_grid_normal_long", 1, "limit"),
        ]
    }

    filtered = bot._apply_crypto_portfolio_risk_limits(orders, {SYMBOL: 100.0})

    assert filtered[SYMBOL] == [orders[SYMBOL][0]]


def test_python_gate_applies_projected_distance_to_initial_with_valid_tier():
    bot = _bot()
    orders = {
        SYMBOL: [(0.1, 130.0, "entry_initial_normal_long", 0, "limit")]
    }

    filtered = bot._apply_crypto_portfolio_risk_limits(orders, {SYMBOL: 100.0})

    assert filtered == {}


def test_bitget_gate_uses_public_mark_and_fee_surface_for_projected_distance():
    bot = _bot()
    bot._crypto_market_surface_required = True
    bot._crypto_market_surfaces = {
        SYMBOL: {
            "mark_price": 85.0,
            "taker_fee": 0.001,
            "contract_size": 1.0,
            "qty_step": 0.001,
            "margin_tiers": _tiers(),
        }
    }

    orders = {
        SYMBOL: [
            (0.1, 85.0, "entry_initial_normal_long", 0, "limit"),
            (0.2, 110.0, "entry_grid_normal_long", 1, "limit"),
        ]
    }

    filtered = bot._apply_crypto_portfolio_risk_limits(orders, {SYMBOL: 100.0})

    # The stale strategy last price would make the grid look safer. The
    # exchange mark surface correctly rejects the projected second level.
    assert filtered[SYMBOL] == [orders[SYMBOL][0]]


def test_bitget_gate_blocks_initial_entry_when_public_surface_is_missing():
    bot = _bot()
    bot._crypto_market_surface_required = True
    bot._crypto_market_surfaces = {}

    orders = {
        SYMBOL: [(0.1, 100.0, "entry_initial_normal_long", 0, "limit")]
    }

    assert bot._apply_crypto_portfolio_risk_limits(orders, {SYMBOL: 100.0}) == {}


def test_python_gate_exports_projected_risk_trace_for_sequential_grid_state():
    bot = _bot()
    orders = {
        SYMBOL: [
            (0.1, 100.0, "entry_initial_normal_long", 0, "limit"),
            (0.2, 90.0, "entry_grid_normal_long", 1, "limit"),
        ]
    }

    bot._apply_crypto_portfolio_risk_limits(orders, {SYMBOL: 100.0})

    trace = bot._last_crypto_risk_parity_export["projected_risk_trace"]
    initial = next(item for item in trace if item["qty"] == 0.1)
    assert initial["projected_liquidation_distance"] == pytest.approx(
        0.33028613524871503
    )
    grid = next(item for item in trace if item["position_side"] == "long" and item["qty"] == 0.2)
    assert grid["projected_position_size"] == pytest.approx(0.3)
    assert grid["projected_entry_price"] == pytest.approx(93.33333333333333)
    assert grid["projected_mark_price"] == pytest.approx(100.0)
    assert grid["projected_margin_tier"] == "1"
    assert grid["projected_liquidation_distance"] == pytest.approx(0.3749337262)
    assert grid["decision"] == "allow"


def test_python_gate_fails_closed_for_existing_position_without_surface():
    bot = _bot()
    bot.positions = {
        SYMBOL: {
            "long": {"size": 1.0, "price": 100.0},
            "short": {"size": 0.0, "price": 0.0},
        }
    }
    bot._crypto_market_surface_required = True
    bot._crypto_market_surfaces = {}
    orders = {
        SYMBOL: [
            (0.1, 90.0, "entry_grid_normal_long", 0, "limit"),
            (-0.1, 90.0, "close_grid_long", 1, "limit"),
        ]
    }

    filtered = bot._apply_crypto_portfolio_risk_limits(orders, {SYMBOL: 100.0})

    assert filtered[SYMBOL] == [orders[SYMBOL][1]]


def test_python_gate_fails_closed_for_existing_position_without_margin_tier():
    bot = _bot()
    bot.positions = {
        SYMBOL: {
            "long": {"size": 1.0, "price": 100.0},
            "short": {"size": 0.0, "price": 0.0},
        }
    }
    bot._crypto_margin_tiers = None
    orders = {
        SYMBOL: [
            (0.1, 90.0, "entry_grid_normal_long", 0, "limit"),
            (-0.1, 90.0, "close_grid_long", 1, "limit"),
        ]
    }

    filtered = bot._apply_crypto_portfolio_risk_limits(orders, {SYMBOL: 100.0})

    assert filtered[SYMBOL] == [orders[SYMBOL][1]]


def test_python_gate_uses_projected_surface_instead_of_stale_reported_distance():
    bot = _bot()
    bot.positions = {
        SYMBOL: {
            "long": {"size": 1.0, "price": 100.0},
            "short": {"size": 0.0, "price": 0.0},
        }
    }
    bot._crypto_min_liquidation_distance = 0.01
    orders = {SYMBOL: [(0.1, 90.0, "entry_grid_normal_long", 0, "limit")]}

    filtered = bot._apply_crypto_portfolio_risk_limits(orders, {SYMBOL: 100.0})

    assert filtered == orders
