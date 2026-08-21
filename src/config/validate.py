import math

from .access import require_config_dict
from .bot import validate_bot_config, validate_forager_config
from .coerce import normalize_hsl_cooldown_position_policy, normalize_hsl_signal_mode
from research.risk_policy import CryptoRiskLimits, validate_grid_controls


def validate_config(
    config: dict, *, raw_optimize=None, verbose: bool = True, tracker=None
) -> None:
    from analysis_visibility import validate_visible_metrics_config
    from optimization.config_adapter import validate_optimize_bounds_against_bot_config

    require_config_dict(config, "monitor")
    optimize_bounds = (
        raw_optimize.get("bounds")
        if isinstance(raw_optimize, dict)
        and isinstance(raw_optimize.get("bounds"), dict)
        else config.get("optimize", {}).get("bounds", {})
    )
    validate_bot_config(config)
    validate_optimize_bounds_against_bot_config(config["bot"], optimize_bounds)
    normalize_hsl_signal_mode(config["live"]["hsl_signal_mode"])
    normalize_hsl_cooldown_position_policy(
        config["live"]["hsl_position_during_cooldown_policy"]
    )
    ticker_strategy = str(
        config["live"].get("market_snapshot_ticker_strategy", "auto")
    ).lower()
    if ticker_strategy not in {"auto", "bulk", "symbols"}:
        raise ValueError(
            "config.live.market_snapshot_ticker_strategy must be one of: auto, bulk, symbols"
        )
    try:
        hysteresis_pct = float(config["live"]["forager_score_hysteresis_pct"])
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "config.live.forager_score_hysteresis_pct must be numeric"
        ) from exc
    if not math.isfinite(hysteresis_pct) or hysteresis_pct < 0.0:
        raise ValueError(
            "config.live.forager_score_hysteresis_pct must be finite and >= 0.0"
        )
    try:
        limit_order_market_dist = float(
            config["live"]["limit_order_create_max_market_dist_pct"]
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "config.live.limit_order_create_max_market_dist_pct must be numeric"
        ) from exc
    if (
        not math.isfinite(limit_order_market_dist)
        or limit_order_market_dist < 0.0
        or limit_order_market_dist >= 1.0
    ):
        raise ValueError(
            "config.live.limit_order_create_max_market_dist_pct must be "
            "finite and >= 0.0 and < 1.0"
        )
    try:
        active_tail_gap_minutes = float(
            config["live"]["max_active_candle_tail_gap_minutes"]
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "config.live.max_active_candle_tail_gap_minutes must be numeric"
        ) from exc
    if not math.isfinite(active_tail_gap_minutes) or active_tail_gap_minutes <= 0.0:
        raise ValueError(
            "config.live.max_active_candle_tail_gap_minutes must be finite and > 0.0"
        )
    try:
        forager_refresh_seconds = float(config["live"]["max_forager_candle_refresh_seconds"])
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "config.live.max_forager_candle_refresh_seconds must be numeric"
        ) from exc
    if not math.isfinite(forager_refresh_seconds) or forager_refresh_seconds <= 0.0:
        raise ValueError(
            "config.live.max_forager_candle_refresh_seconds must be finite and > 0.0"
        )
    for key in ("fee_pct_fallback", "fee_pct_sanity_abs_max"):
        try:
            value = float(config["live"][key])
        except (TypeError, ValueError) as exc:
            raise TypeError(f"config.live.{key} must be numeric") from exc
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"config.live.{key} must be finite and >= 0.0")
    fee_conversion_max_age_ms_raw = config["live"]["fee_conversion_max_age_ms"]
    if isinstance(fee_conversion_max_age_ms_raw, bool) or isinstance(
        fee_conversion_max_age_ms_raw, float
    ):
        raise TypeError("config.live.fee_conversion_max_age_ms must be an integer")
    try:
        fee_conversion_max_age_ms = int(fee_conversion_max_age_ms_raw)
    except (TypeError, ValueError) as exc:
        raise TypeError("config.live.fee_conversion_max_age_ms must be an integer") from exc
    if str(fee_conversion_max_age_ms_raw).strip() != str(fee_conversion_max_age_ms):
        raise TypeError("config.live.fee_conversion_max_age_ms must be an integer")
    if fee_conversion_max_age_ms < 0:
        raise ValueError("config.live.fee_conversion_max_age_ms must be >= 0")
    max_cancellations = int(config["live"]["max_n_cancellations_per_batch"])
    max_creations = int(config["live"]["max_n_creations_per_batch"])
    if max_cancellations <= max_creations:
        raise ValueError(
            "config.live.max_n_cancellations_per_batch must be greater than "
            "config.live.max_n_creations_per_batch"
        )
    monitor_cfg = require_config_dict(config, "monitor")
    if not str(monitor_cfg["root_dir"]).strip():
        raise ValueError("config.monitor.root_dir must be a non-empty string")
    validate_visible_metrics_config(config)
    validate_forager_config(config, verbose=verbose, tracker=tracker)
    _validate_crypto_profile(config)
    _validate_execution_model(config)
    _validate_paper_execution_feed(config)


def _validate_crypto_profile(config: dict) -> None:
    """Validate the opt-in Bitget crypto profile before it can reach live code."""

    live = require_config_dict(config, "live")
    profile = live.get("portfolio_risk")
    if profile is None or not isinstance(profile, dict) or not profile.get("enabled", False):
        return

    if profile.get("require_explicit_live_authorization", True) is not True:
        raise ValueError(
            "crypto portfolio risk requires explicit live authorization"
        )
    if str(profile.get("collateral_currency", "")).upper() != "USDT":
        raise ValueError("crypto portfolio risk requires USDT collateral")
    if bool(profile.get("allow_borrowing", True)):
        raise ValueError("crypto portfolio risk forbids borrowing")
    if bool(profile.get("withdrawals_allowed", True)):
        raise ValueError("crypto portfolio risk requires withdrawals_allowed=false")
    if not bool(profile.get("ip_allowlist_required", False)):
        raise ValueError("crypto portfolio risk requires an IP allowlist")
    if str(profile.get("margin_mode", "")).lower() != "isolated":
        raise ValueError("crypto portfolio risk requires isolated margin")
    if str(live.get("margin_mode_preference", "")).lower() != "isolated":
        raise ValueError("crypto portfolio risk requires live.margin_mode_preference=isolated")
    if not bool(live.get("hedge_mode", False)):
        raise ValueError("crypto portfolio risk requires live.hedge_mode=true")
    if bool(live.get("market_orders_allowed", True)):
        raise ValueError("crypto portfolio risk forbids ordinary market orders")
    leverage = float(live.get("leverage", 0.0))
    leverage_cap = float(profile.get("leverage_cap", 0.0))
    if not math.isfinite(leverage) or leverage <= 0.0 or leverage > leverage_cap or leverage_cap > 3.0:
        raise ValueError("crypto portfolio risk requires leverage <= 3x")
    sizing_cap = float(live.get("sizing_balance_cap", 0.0))
    if not math.isfinite(sizing_cap) or sizing_cap <= 0.0 or sizing_cap > 1000.0:
        raise ValueError("crypto portfolio risk requires live.sizing_balance_cap <= 1000 USDT")
    raw_initial_entry_sizing_pct = profile.get(
        "max_initial_entry_sizing_balance_pct", 0.03
    )
    if isinstance(raw_initial_entry_sizing_pct, bool):
        raise TypeError(
            "config.live.portfolio_risk.max_initial_entry_sizing_balance_pct must be numeric"
        )
    try:
        initial_entry_sizing_pct = float(raw_initial_entry_sizing_pct)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "config.live.portfolio_risk.max_initial_entry_sizing_balance_pct must be numeric"
        ) from exc
    if not math.isfinite(initial_entry_sizing_pct):
        raise ValueError(
            "config.live.portfolio_risk.max_initial_entry_sizing_balance_pct must be finite"
        )
    if initial_entry_sizing_pct <= 0.0 or initial_entry_sizing_pct > 0.03:
        raise ValueError(
            "config.live.portfolio_risk.max_initial_entry_sizing_balance_pct must be > 0 and <= 0.03"
        )

    limit_keys = {
        "max_total_exposure_ratio",
        "max_used_margin_ratio",
        "new_entry_min_liquidation_distance",
        "reduce_only_min_liquidation_distance",
        "emergency_liquidation_distance",
        "max_active_grids",
        "max_symbol_exposure_ratio",
        "daily_loss_stop_ratio",
        "max_automatic_restarts_per_day",
    }
    limits = CryptoRiskLimits.from_mapping({key: profile[key] for key in limit_keys if key in profile})
    if (
        limits.max_total_exposure > 0.80
        or limits.max_used_margin > 0.35
        or limits.new_entry_min_liquidation_distance < 0.30
        or limits.reduce_only_min_liquidation_distance < 0.20
        or limits.emergency_liquidation_distance < 0.15
        or limits.daily_loss_stop > 0.02
        or limits.max_active_grids > 2
        or limits.max_symbol_exposure > 0.50
    ):
        raise ValueError("crypto portfolio risk is weaker than the plan's hard limits")
    if int(live.get("max_n_restarts_per_day", 0)) > limits.max_automatic_restarts_per_day:
        raise ValueError("crypto portfolio risk allows at most one automatic restart per day")
    for pside in ("long", "short"):
        side = require_config_dict(config, f"bot.{pside}")
        if not bool(side.get("hsl_enabled", False)):
            raise ValueError(f"crypto portfolio risk requires bot.{pside}.hsl_enabled=true")
        if float(side.get("hsl_red_threshold", 0.0)) != 0.20:
            raise ValueError(
                f"crypto portfolio risk requires bot.{pside}.hsl_red_threshold=0.20"
            )
        if float(side.get("hsl_no_restart_drawdown_threshold", 0.0)) != 0.25:
            raise ValueError(
                "crypto portfolio risk requires "
                f"bot.{pside}.hsl_no_restart_drawdown_threshold=0.25"
            )
        ratios = side.get("hsl_tier_ratios", {})
        if not isinstance(ratios, dict) or float(ratios.get("yellow", 0.0)) != 0.50 or float(ratios.get("orange", 0.0)) != 0.75:
            raise ValueError(
                f"crypto portfolio risk requires bot.{pside}.hsl tier ratios yellow=0.50 and orange=0.75"
            )
        validate_grid_controls(
            max_levels=side.get("entry_grid_max_levels"),
            initial_entry_pct=side.get("entry_initial_qty_pct"),
            double_down_factor=side.get("entry_grid_double_down_factor"),
            limits=limits,
        )


def _validate_execution_model(config: dict) -> None:
    """Validate the explicit backtest execution-model contract."""

    backtest = require_config_dict(config, "backtest")
    model = backtest.get("execution_model", {})
    if not isinstance(model, dict):
        raise TypeError("config.backtest.execution_model must be a dict")
    allowed = {
        "enabled",
        "funding_enabled",
        "funding_missing_data",
        "mark_price_basis",
        "latency_ms",
        "partial_fill_ratios",
        "adverse_same_candle_ordering",
        "gap_stress_pct",
        "emergency_close_slippage_pct",
        "inputs_path",
    }
    unknown = sorted(set(model) - allowed)
    if unknown:
        raise ValueError(
            "config.backtest.execution_model has unknown key(s): "
            + ", ".join(unknown)
        )
    if str(model.get("funding_missing_data", "blocked")) not in {"blocked", "fail", "zero"}:
        raise ValueError(
            "config.backtest.execution_model.funding_missing_data must be blocked, fail or zero"
        )
    if str(model.get("mark_price_basis", "last")) not in {"last", "mark", "index"}:
        raise ValueError(
            "config.backtest.execution_model.mark_price_basis must be last, mark or index"
        )
    if str(model.get("adverse_same_candle_ordering", "worst_case")) not in {
        "worst_case",
        "best_case",
        "deterministic",
        "ohlc",
    }:
        raise ValueError(
            "config.backtest.execution_model.adverse_same_candle_ordering is invalid"
        )
    try:
        latency_ms = float(model.get("latency_ms", 0.0))
        gap_stress_pct = float(model.get("gap_stress_pct", 0.0))
        emergency_slippage = float(model.get("emergency_close_slippage_pct", 0.0))
    except (TypeError, ValueError) as exc:
        raise TypeError("execution-model numeric fields must be numeric") from exc
    if not all(math.isfinite(value) for value in (latency_ms, gap_stress_pct, emergency_slippage)):
        raise ValueError("execution-model numeric fields must be finite")
    if latency_ms < 0.0 or gap_stress_pct < 0.0 or emergency_slippage < 0.0:
        raise ValueError("execution-model numeric fields must be >= 0")
    ratios = model.get("partial_fill_ratios", [1.0])
    if not isinstance(ratios, list) or not ratios:
        raise ValueError("config.backtest.execution_model.partial_fill_ratios must be a non-empty list")
    parsed = [float(value) for value in ratios]
    if any(not math.isfinite(value) or value <= 0.0 or value > 1.0 for value in parsed):
        raise ValueError("partial-fill ratios must be finite values in (0, 1]")


def _validate_paper_execution_feed(config: dict) -> None:
    """Validate the optional saved-public feed used only by paper mode."""

    live = require_config_dict(config, "live")
    path = live.get("paper_execution_inputs_path")
    since = live.get("paper_execution_inputs_since_ms")
    if path in (None, "") and since in (None, ""):
        return
    if not bool(live.get("paper_mode", False)):
        raise ValueError(
            "paper_execution_inputs_path is allowed only when live.paper_mode=true"
        )
    if path in (None, "") or not str(path).strip():
        raise ValueError(
            "live.paper_execution_inputs_path must be a non-empty path"
        )
    if since in (None, "") or isinstance(since, bool):
        raise ValueError(
            "live.paper_execution_inputs_since_ms is required with a public feed"
        )
    try:
        since_ms = int(since)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "live.paper_execution_inputs_since_ms must be an integer"
        ) from exc
    if since_ms <= 0 or str(since).strip() != str(since_ms):
        raise ValueError(
            "live.paper_execution_inputs_since_ms must be a positive integer"
        )
