"""Build and, only after the data gate passes, run the crypto history protocol.

The dated roadmap describes a selection procedure rather than one optimizer
command.  This module turns that procedure into a content-addressed work plan:
the windows, sides, parameter groups, seeds, validation templates, neighbours,
holdout, and BTC+ETH portfolio checks are all written to one JSON file.

The command never talks to an exchange.  ``--execute`` can only start local
optimizer processes after the read-only historical preflight has passed, and
it deliberately stops before candidate promotion or live trading.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

from config.parse import load_raw_config
from research.historical_preflight import (
    CANONICAL_OHLCV_CACHE_DIR,
    PLAN_DATE,
    PROTOCOL,
    WALK_FORWARD_WINDOWS,
    build_preflight,
    public_market_fees,
)


SCHEMA_VERSION = "crypto-historical-protocol-v1"
PROTOCOL_ID = "historical-protocol-2026-08-17-v1"
DEFAULT_SEEDS = (101, 202, 303, 404, 505)
DEFAULT_ASSETS = ("BTC", "ETH")
DEFAULT_ITERATIONS = 500_000
DEFAULT_CPUS = 1
STRESS_MAKER_FEE = 0.0004
STRESS_MAKER_FEE_LABEL = "stress_maker_fee_0_0004"

# Every optimizer invocation changes at most one group of eight parameters.
# These are suffixes; the actual optimizer selectors are prefixed with long_
# or short_.  Safety controls such as max grid levels and total exposure are
# fixed in the generated configs and are not optimization variables.
PARAMETER_GROUPS: dict[str, tuple[str, ...]] = {
    "geometry": (
        "entry_grid_double_down_factor",
        "entry_grid_spacing_pct",
        "entry_grid_spacing_volatility_weight",
        "entry_grid_spacing_we_weight",
        "entry_initial_ema_dist",
        "entry_initial_qty_pct",
        "entry_trailing_grid_ratio",
        "entry_trailing_threshold_pct",
    ),
    "entry_filter": (
        "ema_span_0",
        "ema_span_1",
        "forager_score_weights_ema_readiness",
        "forager_score_weights_volatility",
        "forager_score_weights_volume",
        "forager_volatility_ema_span",
        "forager_volume_drop_pct",
        "forager_volume_ema_span",
    ),
    "exit": (
        "close_grid_markup_end",
        "close_grid_markup_start",
        "close_grid_qty_pct",
        "close_trailing_grid_ratio",
        "close_trailing_qty_pct",
        "close_trailing_retracement_pct",
        "close_trailing_threshold_pct",
        "unstuck_close_pct",
    ),
}

NEIGHBOR_OFFSETS = (-0.10, -0.05, 0.05, 0.10)
METRIC_GATES = {
    "net_daily_return": {"operator": ">", "value": 0.0},
    "max_drawdown": {"operator": "<=", "value": 0.20},
    "recovery_days": {"operator": "<=", "value": 90},
    "closed_cycles_per_window": {"operator": ">=", "value": 20},
    "liquidations": {"operator": "==", "value": 0},
    "stress_equity_ratio": {"operator": ">=", "value": 0.75},
    "terminal_drawdown": {"operator": "<", "value": 0.25},
}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _sha256_bytes(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _safe_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and abs(parsed) != float("inf") else default


def _safe_bound(raw: Any, *, default: tuple[float, float], upper: float | None = None) -> list[float]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        low = _safe_float(raw[0], default[0])
        high = _safe_float(raw[1], default[1])
    else:
        low, high = default
    if low > high:
        low, high = high, low
    if upper is not None:
        high = min(high, upper)
        low = min(low, high)
    extras = list(raw[2:]) if isinstance(raw, (list, tuple)) and len(raw) > 2 else []
    return [low, high, *extras]


def _set_crypto_safety_profile(
    config: dict[str, Any],
    *,
    asset: str,
    active_side: str,
    execution_inputs: str | None,
    market_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a research config obey the same hard limits as the live profile."""

    backtest = config.setdefault("backtest", {})
    backtest["exchanges"] = ["bitget"]
    backtest["starting_balance"] = 1_000.0
    backtest["scenarios"] = [{"label": "base_public_market_profile"}]
    backtest["suite_enabled"] = False
    backtest["ohlcv_source_dir"] = CANONICAL_OHLCV_CACHE_DIR
    backtest["execution_model"] = {
        "enabled": True,
        "funding_enabled": True,
        "funding_missing_data": "blocked",
        "mark_price_basis": "mark",
        "latency_ms": 250,
        "partial_fill_ratios": [0.25, 0.50, 0.75, 1.0],
        "adverse_same_candle_ordering": "worst_case",
        "gap_stress_pct": 0.01,
        "emergency_close_slippage_pct": 0.01,
        "inputs_path": execution_inputs,
    }

    # The example config has a broad 0.0004 fee override.  Historical base
    # runs must use the captured public instrument fee instead.  The higher
    # value is retained only as a named, opt-in stress case in the plan.
    backtest["maker_fee_override"] = None
    backtest["taker_fee_override"] = None
    fee_provenance: dict[str, Any] = {
        "base": {
            "maker_fee_rate": None,
            "taker_fee_rate": None,
            "source": "public_market_profile",
            "status": "blocked_missing_public_market_profile",
        },
        "stress": {
            "label": STRESS_MAKER_FEE_LABEL,
            "maker_fee_rate": STRESS_MAKER_FEE,
            "enabled_by_default": False,
            "source": "explicit_stress_only",
        },
    }
    if market_profile is not None:
        fees = public_market_fees(market_profile)
        backtest["maker_fee_override"] = fees["maker_fee_rate"]
        backtest["taker_fee_override"] = fees["taker_fee_rate"]
        fee_provenance["base"] = {
            "maker_fee_rate": fees["maker_fee_rate"],
            "taker_fee_rate": fees["taker_fee_rate"],
            "source": "public_market_profile",
            "status": "passed",
        }

    live = config.setdefault("live", {})
    live["approved_coins"] = {"long": [asset], "short": [asset]}
    live["ignored_coins"] = {"long": [], "short": []}
    live["hedge_mode"] = True
    live["margin_mode_preference"] = "isolated"
    live["leverage"] = 3.0
    live["market_orders_allowed"] = False
    live["max_n_restarts_per_day"] = 1
    live["sizing_balance_cap"] = 1_000.0
    live["portfolio_risk"] = {
        "enabled": True,
        "require_explicit_live_authorization": True,
        "allow_borrowing": False,
        "withdrawals_allowed": False,
        "ip_allowlist_required": True,
        "collateral_currency": "USDT",
        "margin_mode": "isolated",
        "leverage_cap": 3.0,
        "max_total_exposure_ratio": 0.80,
        "max_used_margin_ratio": 0.35,
        "new_entry_min_liquidation_distance": 0.30,
        "reduce_only_min_liquidation_distance": 0.20,
        "emergency_liquidation_distance": 0.15,
        "max_active_grids": 2,
        "max_symbol_exposure_ratio": 0.50,
        "daily_loss_stop_ratio": 0.02,
        "max_automatic_restarts_per_day": 1,
        "correlation_group_default": "crypto",
    }

    bot = config.setdefault("bot", {})
    for pside in ("long", "short"):
        side = bot.setdefault(pside, {})
        side["entry_grid_max_levels"] = 8
        side["entry_initial_qty_pct"] = min(_safe_float(side.get("entry_initial_qty_pct"), 0.01), 0.03)
        side["entry_grid_double_down_factor"] = min(
            _safe_float(side.get("entry_grid_double_down_factor"), 1.0), 1.25
        )
        side["total_wallet_exposure_limit"] = 0.60 if pside == active_side else 0.0
        side["hsl_enabled"] = True
        side["hsl_red_threshold"] = 0.20
        side["hsl_no_restart_drawdown_threshold"] = 0.25
        side["hsl_tier_ratios"] = {"yellow": 0.50, "orange": 0.75}

    optimize = config.setdefault("optimize", {})
    bounds = optimize.setdefault("bounds", {})
    # These controls remain fixed even when a parameter group is optimized.
    for pside in ("long", "short"):
        bounds[f"{pside}_total_wallet_exposure_limit"] = [
            0.60 if pside == active_side else 0.0,
            0.60 if pside == active_side else 0.0,
        ]
        if f"{pside}_n_positions" in bounds:
            side_cfg = bot[pside]
            current_n = max(1.0, _safe_float(side_cfg.get("n_positions"), 1.0))
            bounds[f"{pside}_n_positions"] = [current_n, current_n, 1]
        double_down_key = f"{pside}_entry_grid_double_down_factor"
        initial_qty_key = f"{pside}_entry_initial_qty_pct"
        bounds[double_down_key] = _safe_bound(
            bounds.get(double_down_key), default=(0.5, 1.25), upper=1.25
        )
        bounds[initial_qty_key] = _safe_bound(
            bounds.get(initial_qty_key), default=(0.01, 0.03), upper=0.03
        )
    optimize["limits"] = [
        {
            "metric": "drawdown_worst_strategy_eq",
            "penalize_if": "greater_than",
            "value": 0.20,
            "enabled": True,
        },
        {
            "metric": "backtest_completion_ratio",
            "penalize_if": "less_than",
            "value": 0.99,
            "enabled": True,
        },
    ]
    return fee_provenance


def _side_config(
    raw_config: Mapping[str, Any],
    *,
    asset: str,
    side: str,
    execution_inputs: str | None,
    market_profile: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = deepcopy(dict(raw_config))
    fee_provenance = _set_crypto_safety_profile(
        config,
        asset=asset,
        active_side=side,
        execution_inputs=execution_inputs,
        market_profile=market_profile,
    )
    config["backtest"]["start_date"] = WALK_FORWARD_WINDOWS[0]["train_start"]
    config["backtest"]["end_date"] = PROTOCOL["holdout_end"]
    return config, fee_provenance


def _selector_names(side: str, group: str, bounds: Mapping[str, Any]) -> list[str]:
    if group not in PARAMETER_GROUPS:
        raise ValueError(f"unknown parameter group: {group}")
    selectors = [f"{side}_{name}" for name in PARAMETER_GROUPS[group]]
    missing = [selector for selector in selectors if selector not in bounds]
    if missing:
        raise ValueError(f"config is missing optimize bounds: {', '.join(missing)}")
    if len(selectors) > 8:
        raise ValueError(f"parameter group changes more than eight values: {group}")
    return selectors


def _preflight_snapshot(
    *, root: Path, assets: Sequence[str], preflight_path: Path | None
) -> dict[str, Any]:
    if preflight_path is not None:
        snapshot = _load_json(preflight_path)
    else:
        snapshot = build_preflight(root=root, assets=tuple(assets))
    if not isinstance(snapshot.get("assets"), Mapping):
        raise ValueError("historical preflight has no assets object")
    missing = [asset for asset in assets if asset not in snapshot["assets"]]
    if missing:
        raise ValueError(f"historical preflight has no entries for: {', '.join(missing)}")
    return snapshot


def _load_public_market_profile(
    *, root: Path, asset: str, asset_preflight: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Load only the public profile named by historical preflight."""

    paths = asset_preflight.get("paths")
    paths = paths if isinstance(paths, Mapping) else {}
    raw_path = paths.get("profile")
    if raw_path in (None, ""):
        return None, None
    profile_path = Path(str(raw_path)).expanduser()
    if not profile_path.is_absolute():
        profile_path = root / profile_path
    profile_path = profile_path.resolve()
    if "public_only" not in profile_path.name:
        raise ValueError(
            f"historical protocol refuses non-public market profile for {asset}: {profile_path}"
        )
    if not profile_path.exists():
        raise ValueError(f"missing public market profile for {asset}: {profile_path}")
    return _load_json(profile_path), str(profile_path)


def _executable(root: Path, requested: str | None) -> str:
    if requested:
        return requested
    candidate = root / ".venv" / "bin" / "passivbot"
    return str(candidate) if candidate.exists() else "passivbot"


def _task_command(
    *,
    executable: str,
    config_path: Path,
    asset: str,
    start: str,
    end: str,
    seed: int,
    selectors: Sequence[str],
    iterations: int,
    cpus: int,
) -> list[str]:
    return [
        executable,
        "optimize",
        str(config_path),
        "--symbols",
        asset,
        "--exchanges",
        "bitget",
        "--start-date",
        start,
        "--end-date",
        end,
        "--iters",
        str(iterations),
        "--cpus",
        str(cpus),
        "--seed",
        str(seed),
        "--fine-tune-params",
        ",".join(selectors),
    ]


def build_protocol_plan(
    *,
    root: str | Path,
    config_path: str | Path,
    assets: Sequence[str] = DEFAULT_ASSETS,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    preflight: Mapping[str, Any] | None = None,
    preflight_path: str | Path | None = None,
    output_path: str | Path | None = None,
    iterations: int = DEFAULT_ITERATIONS,
    cpus: int = DEFAULT_CPUS,
    executable: str | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).expanduser().resolve()
    source_config_path = Path(config_path).expanduser()
    if not source_config_path.is_absolute():
        source_config_path = repo_root / source_config_path
    source_config_path = source_config_path.resolve()
    if not source_config_path.exists():
        raise FileNotFoundError(f"missing optimizer config: {source_config_path}")
    if iterations <= 0 or cpus <= 0:
        raise ValueError("iterations and cpus must be positive")
    normalized_assets = tuple(dict.fromkeys(str(asset).upper() for asset in assets))
    if not normalized_assets or any(asset not in {"BTC", "ETH"} for asset in normalized_assets):
        raise ValueError("assets must be BTC and/or ETH")
    normalized_seeds = tuple(int(seed) for seed in seeds)
    if len(normalized_seeds) != 5 or len(set(normalized_seeds)) != 5:
        raise ValueError("the protocol requires five independent, distinct seeds")
    if any(seed < 0 or seed > 2**32 - 1 for seed in normalized_seeds):
        raise ValueError("seeds must be between 0 and 4294967295")

    if preflight is None:
        resolved_preflight_path = (
            Path(preflight_path).expanduser().resolve() if preflight_path is not None else None
        )
        preflight = _preflight_snapshot(
            root=repo_root,
            assets=normalized_assets,
            preflight_path=resolved_preflight_path,
        )
    else:
        preflight = deepcopy(dict(preflight))

    if output_path is None:
        resolved_output = (
            repo_root
            / "analysis_reports"
            / "crypto_bot_roadmap"
            / "historical_protocol"
            / PLAN_DATE
            / "plan.json"
        )
    else:
        resolved_output = Path(output_path).expanduser()
        if not resolved_output.is_absolute():
            resolved_output = repo_root / resolved_output
        resolved_output = resolved_output.resolve()
    protocol_dir = resolved_output.parent
    config_dir = protocol_dir / "configs"
    raw_config = load_raw_config(str(source_config_path), log_errors=False)
    runner = _executable(repo_root, executable)

    generated_configs: dict[str, dict[str, Any]] = {}
    fee_provenance_by_asset: dict[str, dict[str, Any]] = {}
    for asset in normalized_assets:
        asset_preflight = preflight["assets"].get(asset, {})
        paths = asset_preflight.get("paths", {}) if isinstance(asset_preflight, Mapping) else {}
        execution_path = paths.get("execution_inputs") if isinstance(paths, Mapping) else None
        market_profile, market_profile_path = _load_public_market_profile(
            root=repo_root,
            asset=asset,
            asset_preflight=asset_preflight if isinstance(asset_preflight, Mapping) else {},
        )
        if preflight.get("status") == "passed" and market_profile is None:
            raise ValueError(f"passed historical preflight has no public market profile for {asset}")
        for side in ("long", "short"):
            config, fee_provenance = _side_config(
                raw_config,
                asset=asset,
                side=side,
                execution_inputs=str(execution_path) if execution_path else None,
                market_profile=market_profile,
            )
            selectors_by_group = {
                group: _selector_names(side, group, config["optimize"]["bounds"])
                for group in PARAMETER_GROUPS
            }
            config_name = f"bitget_{asset.lower()}_{side}_historical.json"
            generated_path = config_dir / config_name
            _write_json(generated_path, config)
            generated_configs[f"{asset}:{side}"] = {
                "path": str(generated_path),
                "sha256": _sha256_file(generated_path),
                "selectors": selectors_by_group,
                "market_profile_path": market_profile_path,
                "fee_provenance": fee_provenance,
            }
            fee_provenance_by_asset[asset] = fee_provenance

    optimizer_tasks: list[dict[str, Any]] = []
    for asset in normalized_assets:
        for window_number, window in enumerate(WALK_FORWARD_WINDOWS, start=1):
            for side in ("long", "short"):
                config_meta = generated_configs[f"{asset}:{side}"]
                for group in PARAMETER_GROUPS:
                    selectors = config_meta["selectors"][group]
                    for seed in normalized_seeds:
                        job_id = f"train-{asset.lower()}-w{window_number}-{side}-{group}-s{seed}"
                        optimizer_tasks.append(
                            {
                                "job_id": job_id,
                                "stage": "train",
                                "asset": asset,
                                "side": side,
                                "window": window_number,
                                "parameter_group": group,
                                "seed": seed,
                                "max_changed_parameters": len(selectors),
                                "config": config_meta["path"],
                                "config_sha256": config_meta["sha256"],
                                "train_start": window["train_start"],
                                "train_end": window["train_end"],
                                "command": _task_command(
                                    executable=runner,
                                    config_path=Path(config_meta["path"]),
                                    asset=asset,
                                    start=window["train_start"],
                                    end=window["train_end"],
                                    seed=seed,
                                    selectors=selectors,
                                    iterations=iterations,
                                    cpus=cpus,
                                ),
                                "expected_output": "optimize_results/<timestamp>/*/pareto/",
                            }
                        )

    validation_templates: list[dict[str, Any]] = []
    neighbor_templates: list[dict[str, Any]] = []
    for asset in normalized_assets:
        for window_number, window in enumerate(WALK_FORWARD_WINDOWS, start=1):
            for side in ("long", "short"):
                validation_templates.append(
                    {
                        "task_id": f"validate-{asset.lower()}-w{window_number}-{side}",
                        "stage": "validation",
                        "asset": asset,
                        "side": side,
                        "window": window_number,
                        "depends_on": [
                            task["job_id"]
                            for task in optimizer_tasks
                            if task["asset"] == asset
                            and task["side"] == side
                            and task["window"] == window_number
                        ],
                        "candidate_input": "one selected Pareto config from every group/seed, after deterministic selection",
                        "validation_start": window["validation_start"],
                        "validation_end": window["validation_end"],
                        "required_metrics": METRIC_GATES,
                    }
                )
                neighbor_templates.append(
                    {
                        "task_id": f"neighbors-{asset.lower()}-w{window_number}-{side}",
                        "stage": "neighbors",
                        "asset": asset,
                        "side": side,
                        "window": window_number,
                        "depends_on": [f"validate-{asset.lower()}-w{window_number}-{side}"],
                        "candidate_input": "validated centre candidate",
                        "parameter_offsets_pct": list(NEIGHBOR_OFFSETS),
                        "parameters_per_group": 8,
                        "expected_variants_per_centre": 32,
                        "required_metrics": METRIC_GATES,
                    }
                )

    combined_templates = [
        {
            "task_id": f"combined-{asset.lower()}-w{window_number}",
            "stage": "combined",
            "asset": asset,
            "window": window_number,
            "depends_on": [
                f"neighbors-{asset.lower()}-w{window_number}-long",
                f"neighbors-{asset.lower()}-w{window_number}-short",
            ],
            "candidate_input": "independently validated Long and Short centres",
            "command_template": [
                runner,
                "backtest",
                "<combined_config.json>",
                "--symbols",
                asset,
                "--exchanges",
                "bitget",
                "--start-date",
                window["validation_start"],
                "--end-date",
                window["validation_end"],
            ],
            "required_metrics": METRIC_GATES,
        }
        for asset in normalized_assets
        for window_number, window in enumerate(WALK_FORWARD_WINDOWS, start=1)
    ]
    holdout_templates = [
        {
            "task_id": f"holdout-{asset.lower()}-{side}",
            "stage": "holdout",
            "asset": asset,
            "side": side,
            "depends_on": [
                f"combined-{asset.lower()}-w{window_number}"
                for window_number in range(1, len(WALK_FORWARD_WINDOWS) + 1)
            ],
            "candidate_input": "one frozen candidate after all validation and neighbour gates",
            "holdout_start": PROTOCOL["holdout_start"],
            "holdout_end": PROTOCOL["holdout_end"],
            "required_metrics": METRIC_GATES,
        }
        for asset in normalized_assets
        for side in ("long", "short")
    ]
    portfolio_is_applicable = set(normalized_assets) == {"BTC", "ETH"}
    portfolio_template = {
        "task_id": "portfolio-btc-eth-synchronized",
        "stage": "portfolio_historical",
        "status": "ready" if portfolio_is_applicable else "not_applicable_until_btc_and_eth_are_selected",
        "assets": [asset for asset in ("BTC", "ETH") if asset in normalized_assets],
        "depends_on": [
            task["task_id"]
            for task in holdout_templates
            if task["asset"] in {"BTC", "ETH"}
        ],
        "candidate_input": "separately frozen BTC and ETH candidates",
        "single_time_scale": True,
        "correlation_assumption_in_stress": 1.0,
        "max_active_grids": 2,
        "max_symbol_exposure_ratio": 0.50,
        "max_total_exposure_ratio": 0.80,
        "command_template": [
            runner,
            "backtest",
            "<btc_eth_portfolio_config.json>",
            "--symbols",
            "BTC,ETH",
            "--exchanges",
            "bitget",
            "--start-date",
            PROTOCOL["holdout_start"],
            "--end-date",
            PROTOCOL["holdout_end"],
        ],
        "required_metrics": METRIC_GATES,
    }

    preflight_status = str(preflight.get("status", "blocked"))
    overall_status = "planned" if preflight_status == "passed" else "blocked"
    plan = {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "plan_date": PLAN_DATE,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": overall_status,
        "prerequisites": {
            "status": preflight_status,
            "preflight_sha256": _sha256_bytes(preflight),
            "preflight_path": str(Path(preflight_path).expanduser().resolve())
            if preflight_path is not None
            else None,
            "blocked_reasons": {
                asset: list(preflight["assets"][asset].get("reasons", []))
                for asset in normalized_assets
                if not preflight["assets"][asset].get("ready", False)
            },
        },
        "inputs": {
            "source_config": str(source_config_path),
            "source_config_sha256": _sha256_file(source_config_path),
            "generated_configs": generated_configs,
            "ohlcv_cache_dir": CANONICAL_OHLCV_CACHE_DIR,
            "remote_fetch_policy": "local_only",
            "legacy_candidate_policy": "excluded_from_four_window_protocol",
        },
        "fee_provenance": fee_provenance_by_asset,
        "protocol": deepcopy(PROTOCOL),
        "parameter_groups": {
            group: {
                "parameter_suffixes": list(parameters),
                "max_changed_parameters": len(parameters),
            }
            for group, parameters in PARAMETER_GROUPS.items()
        },
        "optimizer": {
            "executable": runner,
            "iterations": iterations,
            "cpus": cpus,
            "seeds": list(normalized_seeds),
            "live_operations_allowed": False,
        },
        "selection_rules": {
            "centre_must_pass_on_every_validation_window": True,
            "neighbour_profitable_ratio_min": PROTOCOL["min_neighbor_profitable_ratio"],
            "neighbour_risk_limit": PROTOCOL["max_drawdown"],
            "ranking": [
                "median_net_daily_return_descending",
                "max_drawdown_ascending",
                "total_exposure_ascending",
                "recovery_days_ascending",
            ],
            "no_candidate_is_a_valid_result": True,
        },
        "selection": {
            "command_template": [
                runner,
                "tool",
                "crypto-historical-select",
                "--input",
                "<candidate_evidence.json>",
                "--output",
                "<selection.json>",
            ],
            "candidate_evidence_contract": {
                "optimizer_seeds": 5,
                "validation_windows": 4,
                "neighbors_per_window": 32,
                "stress_metrics_required": True,
                "holdout_required_but_not_ranked": True,
            },
            "no_suitable_candidate_is_a_valid_result": True,
        },
        "tasks": {
            "optimizer": optimizer_tasks,
            "validation_templates": validation_templates,
            "neighbor_templates": neighbor_templates,
            "combined_templates": combined_templates,
            "holdout_templates": holdout_templates,
            "portfolio_template": portfolio_template,
        },
        "expected_counts": {
            "optimizer_jobs": len(optimizer_tasks),
            "validation_templates": len(validation_templates),
            "neighbor_templates": len(neighbor_templates),
            "combined_templates": len(combined_templates),
            "holdout_templates": len(holdout_templates),
            "portfolio_templates": 1,
        },
        "execution": {
            "status": "not_started" if preflight_status == "passed" else "blocked",
            "completed_jobs": [],
            "failed_jobs": [],
            "selection_status": "pending",
        },
    }
    return plan


def _render_report(plan: Mapping[str, Any]) -> str:
    prerequisites = plan["prerequisites"]
    counts = plan["expected_counts"]
    lines = [
        "# Crypto historical protocol",
        "",
        f"Date: {plan['plan_date']}",
        f"Protocol: `{plan['protocol_id']}`",
        f"Status: **{plan['status']}**",
        "",
        "This is a local research procedure. It cannot place orders or move funds.",
        "",
        f"Historical preflight: **{prerequisites['status']}**",
    ]
    blocked = prerequisites.get("blocked_reasons", {})
    if blocked:
        lines.extend(["", "The protocol is not allowed to start. Current reasons:"])
        for asset, reasons in blocked.items():
            lines.append(f"- {asset}: " + "; ".join(reasons))
    lines.extend(
        [
            "",
            "## Fixed procedure",
            "",
            "- Four walk-forward windows from the dated plan.",
            "- Long and Short are optimized independently.",
            "- Three groups of eight parameters, five distinct seeds per group.",
            f"- Generated optimizer jobs: **{counts['optimizer_jobs']}**.",
            "- Every centre candidate is checked on the next window, its four one-parameter neighbours, and the final holdout.",
            "- BTC and ETH are later checked on one shared clock with correlation set to 1 in stress tests.",
            "",
            "## Promotion rule",
            "",
            "A positive result is not enough. A candidate must pass every risk, recovery, cycle-count, liquidation, stress, and neighbour gate. If no candidate passes, the output is `no suitable candidate`; no gate is weakened.",
            "",
            "## Execution",
            "",
            "Use `passivbot tool crypto-historical-protocol --execute` only after the read-only preflight has passed. That option runs local optimizer jobs only; candidate selection remains a separate, reviewable step.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_protocol_artifacts(plan: Mapping[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, plan)
    (output.parent / "report.md").write_text(_render_report(plan), encoding="utf-8")
    return output


def execute_training_jobs(plan: Mapping[str, Any], *, root: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Run independent local training jobs, never validation or live code."""

    if plan["prerequisites"].get("status") != "passed":
        return {
            "status": "blocked",
            "completed_jobs": [],
            "failed_jobs": [],
            "reason": "historical preflight did not pass",
        }
    repo_root = Path(root).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    log_dir = output.parent / "optimizer_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    completed: list[str] = []
    failed: list[dict[str, Any]] = []
    for task in plan["tasks"]["optimizer"]:
        job_id = str(task["job_id"])
        log_path = log_dir / f"{job_id}.log"
        try:
            result = subprocess.run(
                list(task["command"]),
                cwd=repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            output_text = result.stdout or ""
            log_path.write_text(output_text, encoding="utf-8")
            if result.returncode == 0:
                completed.append(job_id)
            else:
                failed.append({"job_id": job_id, "returncode": result.returncode, "log": str(log_path)})
                break
        except OSError as exc:
            failed.append({"job_id": job_id, "error": f"{type(exc).__name__}: {exc}", "log": str(log_path)})
            break
    return {
        "status": "failed" if failed else "training_complete_selection_pending",
        "completed_jobs": completed,
        "failed_jobs": failed,
        "selection_status": "pending",
    }


def build_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/examples/default_trailing_grid_long_npos7.json"),
        help="source optimizer config; generated crypto configs are never written over it",
    )
    parser.add_argument("--asset", action="append", dest="assets")
    parser.add_argument("--seed", action="append", type=int, dest="seeds")
    parser.add_argument("--preflight", type=Path, help="saved historical preflight JSON")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "analysis_reports/crypto_bot_roadmap/historical_protocol/2026-08-17/plan.json"
        ),
    )
    parser.add_argument("--iters", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--cpus", type=int, default=DEFAULT_CPUS)
    parser.add_argument("--executable", type=str, default=None)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run local optimizer jobs only; requires a passed historical preflight",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.repo_root.expanduser().resolve()
    output = args.output.expanduser()
    if not output.is_absolute():
        output = root / output
    output = output.resolve()
    preflight_path = args.preflight.expanduser().resolve() if args.preflight else None
    preflight = _preflight_snapshot(
        root=root,
        assets=tuple(args.assets or DEFAULT_ASSETS),
        preflight_path=preflight_path,
    )
    plan = build_protocol_plan(
        root=root,
        config_path=args.config,
        assets=tuple(args.assets or DEFAULT_ASSETS),
        seeds=tuple(args.seeds or DEFAULT_SEEDS),
        preflight=preflight,
        preflight_path=preflight_path,
        output_path=output,
        iterations=args.iters,
        cpus=args.cpus,
        executable=args.executable,
    )
    if args.execute:
        plan = deepcopy(plan)
        plan["status"] = "running"
        execution = execute_training_jobs(plan, root=root, output_path=output)
        plan["execution"] = execution
        if execution["status"] == "failed":
            plan["status"] = "failed"
        elif execution["status"] == "training_complete_selection_pending":
            plan["status"] = "pending_selection"
    write_protocol_artifacts(plan, output)
    print(json.dumps({"output": str(output), "status": plan["status"], "execution": plan["execution"]["status"]}, sort_keys=True))
    if plan["status"] == "blocked":
        return 2
    if plan["execution"]["status"] == "failed":
        return 1
    if plan["status"] == "pending_selection":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
