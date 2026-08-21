import json
import math

from backtest import (
    BacktestExecutionSettings,
    get_backtest_execution_settings,
    _materialize_execution_model_inputs,
)
from config import get_template_config


def _settings(inputs_path):
    return BacktestExecutionSettings(
        market_orders_allowed=False,
        market_order_near_touch_threshold=0.001,
        market_order_slippage_pct=0.0005,
        pnls_max_lookback_days=30.0,
        pnls_max_lookback_days_backtest_value=30.0,
        execution_model={
            "enabled": True,
            "funding_enabled": True,
            "funding_missing_data": "blocked",
            "mark_price_basis": "mark",
            "latency_ms": 60_000,
            "partial_fill_ratios": [0.5, 1.0],
            "adverse_same_candle_ordering": "worst_case",
            "gap_stress_pct": 0.005,
            "emergency_close_slippage_pct": 0.01,
            "inputs_path": str(inputs_path),
        },
    )


def test_execution_inputs_are_aligned_without_substituting_leading_values(tmp_path):
    source = tmp_path / "execution_inputs.json"
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "mark_price_history": [
                    [60_000, "100", "101", "99", "100", "0", "0"],
                    [120_000, "110", "111", "109", "110", "0", "0"],
                ],
                "index_price_history": [
                    [60_000, "100", "101", "99", "100", "0", "0"],
                    [120_000, "110", "111", "109", "110", "0", "0"],
                ],
                "funding_history": [
                    {"timestamp_ms": 60_000, "funding_rate": 0.001, "symbol": "BTCUSDT"}
                ],
                "snapshots": {
                    "position_tiers": {
                        "data": [
                            {
                                "tier": "1",
                                "minTierValue": "0",
                                "maxTierValue": "200000",
                                "leverage": "150",
                                "mmr": "0.004",
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = _materialize_execution_model_inputs(
        _settings(source),
        timestamps=[0, 60_000, 120_000],
        coins=["BTC"],
    )

    assert math.isnan(result.execution_model["mark_prices"][0])
    assert result.execution_model["mark_prices"][1:] == [100.0, 110.0]
    assert math.isnan(result.execution_model["index_prices"][0])
    assert result.execution_model["index_prices"][1:] == [100.0, 110.0]
    assert result.execution_model["funding_events"] == [
        {"timestamp_ms": 60_000, "rate": 0.001}
    ]
    assert result.execution_model["position_tiers"] == [
        [
            {
                "min_notional": 0.0,
                "max_notional": 200_000.0,
                "maintenance_margin_rate": 0.004,
            }
        ]
    ]


def test_enabled_crypto_profile_is_forwarded_to_the_rust_backtest_contract():
    config = get_template_config()
    config["live"].update(
        {
            "hedge_mode": True,
            "leverage": 3.0,
            "margin_mode_preference": "isolated",
            "market_orders_allowed": False,
            "sizing_balance_cap": 1000.0,
        }
    )
    config["live"]["portfolio_risk"]["enabled"] = True
    settings = get_backtest_execution_settings(config)

    risk = settings.execution_model["portfolio_risk"]
    assert risk == {
        "enabled": True,
        "sizing_balance_cap": 1000.0,
        "max_initial_entry_sizing_balance_pct": 0.03,
        "max_total_exposure": 0.80,
        "max_used_margin": 0.35,
        "max_symbol_exposure": 0.50,
        "max_active_grids": 2,
        "leverage": 3.0,
        "new_entry_min_liquidation_distance": 0.30,
        "reduce_only_min_liquidation_distance": 0.20,
        "emergency_liquidation_distance": 0.15,
    }


def test_configured_crypto_initial_entry_sizing_cap_is_forwarded():
    config = get_template_config()
    config["live"]["portfolio_risk"]["max_initial_entry_sizing_balance_pct"] = 0.02

    settings = get_backtest_execution_settings(config)

    assert (
        settings.execution_model["portfolio_risk"][
            "max_initial_entry_sizing_balance_pct"
        ]
        == 0.02
    )
