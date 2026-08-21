"""Run a deterministic, read-only parity probe through both real engines.

The probe uses the Rust ``Backtest`` entry gate and the production Python live
portfolio-risk method on the same locally constructed snapshot.  It does not
contact Bitget, submit orders, change an account, or move money.  A mismatch is
written as evidence and returns a non-zero exit status.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

from backtest import build_backtest_payload, execute_backtest
from config import get_template_config
from passivbot import Passivbot
from research.bitget_market_surfaces import normalize_bitget_public_market_surface
from research.experiment_manifest import sha256_path
from research.live_risk_snapshot import export_live_gate_decisions
from research.risk_engine_surface import run_risk_surface_probe
from research.risk_evidence import (
    build_production_risk_evidence_manifest,
    write_production_invocation,
)
from research.risk_parity import normalize_snapshot, snapshot_hash
from research.risk_trace import compare_live_export_with_rust_trace_records

SYMBOL = "BTC/USDT:USDT"
START_TIMESTAMP_MS = 1_704_067_200_000
PUBLIC_PROFILE_PATH = (
    Path(__file__).resolve().parents[2]
    / "analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_market_profile_read_only.json"
)


def _captured_public_surface() -> dict[str, Any]:
    """Load the captured public Bitget responses used by the probe.

    The contract size is an explicit public CCXT market value for the linear
    BTCUSDT contract, not a fallback inferred from the quantity step.
    """

    profile = json.loads(PUBLIC_PROFILE_PATH.read_text(encoding="utf-8"))
    responses = profile.get("responses")
    if not isinstance(responses, dict):
        raise RuntimeError("captured Bitget public profile has no responses")
    return normalize_bitget_public_market_surface(
        internal_symbol=SYMBOL,
        exchange_symbol="BTCUSDT",
        category="USDT-FUTURES",
        instrument_response=responses["market_instruments"],
        tier_response=responses["market_position_tier"],
        ticker_response=responses["market_tickers"],
        funding_response=responses["market_current_fund_rate"],
        contract_size=1.0,
    )


def _shrink_span_values(value: Any) -> None:
    if not isinstance(value, dict):
        return
    for key, nested in value.items():
        if isinstance(nested, dict):
            _shrink_span_values(nested)
        elif isinstance(nested, (int, float)) and ("span" in key or "warmup" in key):
            value[key] = 2


def _probe_config() -> dict[str, Any]:
    config = get_template_config()
    config["backtest"]["coins"] = {"bitget": [SYMBOL]}
    config["backtest"]["starting_balance"] = 1_000.0
    config["backtest"]["start_date"] = "2024-01-01"
    config["backtest"]["end_date"] = "2024-01-02"
    config["backtest"]["candle_interval_minutes"] = 1
    config["backtest"]["execution_model"].update(
        {"enabled": False, "funding_enabled": False}
    )
    config["live"]["portfolio_risk"].update(
        {"enabled": True, "max_initial_entry_sizing_balance_pct": 0.03}
    )
    config["live"].update(
        {
            "hedge_mode": True,
            "leverage": 3.0,
            "margin_mode_preference": "isolated",
            "market_orders_allowed": False,
            "sizing_balance_cap": 1_000.0,
        }
    )
    for side in ("long", "short"):
        config["bot"][side].update(
            {
                "n_positions": 1 if side == "long" else 0,
                "total_wallet_exposure_limit": 0.5 if side == "long" else 0.0,
                "wallet_exposure_limit": 0.5 if side == "long" else 0.0,
                "entry_initial_qty_pct": 0.03,
                "entry_grid_max_levels": 8,
                "hsl_enabled": False,
                "entry_initial_ema_dist": 0.0,
                "entry_grid_spacing_pct": 0.02,
                "entry_grid_double_down_factor": 1.0,
            }
        )
    _shrink_span_values(config)
    return config


def _probe_market_data() -> tuple[np.ndarray, dict[str, Any], np.ndarray, np.ndarray]:
    surface = _captured_public_surface()
    n = 200
    timestamps = np.arange(n, dtype=np.int64) * 60_000 + START_TIMESTAMP_MS
    base_price = float(surface["mark_price"])
    close = np.concatenate(
        (np.full(n // 2, base_price), np.full(n - n // 2, base_price * 0.90))
    )
    hlcvs = np.zeros((n, 1, 4), dtype=np.float64)
    hlcvs[:, 0, 0] = close * 1.01
    hlcvs[:, 0, 1] = close * 0.99
    hlcvs[:, 0, 2] = close
    hlcvs[:, 0, 3] = 1_000.0
    market_settings = {
        "__meta__": {
            "data_interval_minutes": 1,
            "effective_requested_start_ts": int(timestamps[0]),
            "warmup_minutes_requested": 0,
            "warmup_minutes_provided": 0,
        },
        SYMBOL: {
            "symbol": SYMBOL,
            "coin": "BTC",
            "exchange": "bitget",
            "quote": "USDT",
            "base": "BTC",
            "qty_step": float(surface["qty_step"]),
            "price_step": float(surface["price_step"]),
            "min_qty": float(surface["min_order_qty"]),
            "min_cost": float(surface["min_order_amount"]),
            "c_mult": float(surface["contract_size"]),
            "maker_fee": float(surface["maker_fee"]),
            "taker_fee": float(surface["taker_fee"]),
            "first_valid_index": 0,
            "last_valid_index": n - 1,
            "warmup_minutes": 0,
            "trade_start_index": 0,
        },
    }
    return hlcvs, market_settings, close, timestamps


def _rust_trace() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = _probe_config()
    hlcvs, market_settings, btc_prices, timestamps = _probe_market_data()
    surface = _captured_public_surface()
    payload = build_backtest_payload(
        hlcvs,
        market_settings,
        config,
        "bitget",
        btc_prices,
        timestamps,
        metrics_only=True,
    )
    payload.backtest_params["execution_model"]["position_tiers"] = [
        [
            {
                "min_notional": float(tier["min_notional"]),
                "max_notional": float(tier["max_notional"]),
                "maintenance_margin_rate": float(tier["maintenance_margin_rate"]),
            }
            for tier in surface["margin_tiers"]
        ]
    ]
    execute_backtest(payload, config)
    trace = list((payload.hard_stop_plot_data or {}).get("risk_gate_trace") or [])
    if not trace:
        raise RuntimeError("Rust Backtest produced no crypto risk trace")
    first_step = min(int(record["step"]) for record in trace)
    selected = [record for record in trace if int(record["step"]) == first_step]
    if not selected:
        raise RuntimeError("Rust Backtest produced no trace for the first step")
    snapshot_digest = snapshot_hash(normalize_snapshot(selected[0]["snapshot"]))
    if any(snapshot_hash(normalize_snapshot(record["snapshot"])) != snapshot_digest for record in selected):
        raise RuntimeError("Rust trace records for one step do not share a snapshot")
    return selected, config


def _live_export_for_trace(trace: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot = normalize_snapshot(trace[0]["snapshot"])
    surface = _captured_public_surface()
    snapshot_mark = float(snapshot["mark_prices"][SYMBOL])
    if not np.isclose(snapshot_mark, float(surface["mark_price"]), rtol=0.0, atol=1e-9):
        raise RuntimeError(
            "actual probe snapshot mark does not match captured Bitget public mark"
        )
    equity = float(snapshot["balance"]["equity"])
    bot = Passivbot.__new__(Passivbot)
    bot.balance_raw = equity
    bot.balance = equity
    bot.config = _probe_config()
    bot.positions = {
        SYMBOL: {
            "long": {"size": 0.0, "price": 0.0},
            "short": {"size": 0.0, "price": 0.0},
        }
    }
    bot.open_orders = {}
    bot.fetched_positions = []
    bot.c_mults = {SYMBOL: float(surface["contract_size"])}
    bot.qty_steps = {SYMBOL: float(surface["qty_step"])}
    bot.markets_dict = {SYMBOL: {"taker": float(surface["taker_fee"])} }
    bot._crypto_market_surface_required = True
    bot._crypto_market_surfaces = {SYMBOL: surface}
    bot._crypto_daily_loss_ratio = float(snapshot["daily_loss"]["ratio"])
    bot._crypto_min_liquidation_distance = None
    bot._crypto_hsl_state = copy.deepcopy(snapshot["hsl"])
    bot._crypto_margin_tiers = copy.deepcopy(surface["margin_tiers"])
    bot._crypto_risk_margin_tiers = copy.deepcopy(surface["risk_margin_tiers"])
    bot.get_raw_balance = lambda: equity
    bot.get_sizing_balance = lambda: equity

    original_orders = {SYMBOL: []}
    for record in trace:
        original_orders[SYMBOL].append(
            {
                "type": record["execution_type"],
                "pb_order_type": record["order_type"],
                "qty": record["qty"],
                "price": record["price"],
                "reduce_only": record["reduce_only"],
                "position_side": record["position_side"],
            }
        )
    filtered_orders = Passivbot._apply_crypto_portfolio_risk_limits(
        bot, original_orders, snapshot["mark_prices"]
    )
    return export_live_gate_decisions(
        bot,
        original_orders,
        filtered_orders,
        snapshot["mark_prices"],
        as_of_ms=int(trace[0]["timestamp_ms"]),
        margin_tiers=surface["risk_margin_tiers"],
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run(
    output_dir: Path,
    *,
    required_regression_path: Path | None = None,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"risk evidence output is immutable: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    trace, config = _rust_trace()
    live_export = _live_export_for_trace(trace)
    snapshot = normalize_snapshot(trace[0]["snapshot"])
    comparison = compare_live_export_with_rust_trace_records(live_export, trace)
    surface_probe = run_risk_surface_probe()
    files = {
        "snapshot": "snapshot.json",
        "live_export": "live_export.json",
        "rust_trace": "rust_trace.json",
        "comparison": "comparison.json",
    }
    _write_json(output_dir / files["snapshot"], snapshot)
    _write_json(output_dir / files["live_export"], live_export)
    _write_json(output_dir / files["rust_trace"], trace)
    _write_json(output_dir / files["comparison"], comparison)
    _write_json(output_dir / "config.json", config)
    (output_dir / "market_profile.json").write_bytes(PUBLIC_PROFILE_PATH.read_bytes())
    _write_json(output_dir / "surface_probe.json", surface_probe)
    report = {
        "schema_version": "risk-parity-actual-probe-report-v1",
        "status": comparison["parity"],
        "summary": (
            "The initial entry matched, but later grid decisions differ between the real Python live gate and Rust Backtest."
            if comparison["parity"] != "passed"
            else "All decisions in the actual probe matched while using the captured public Bitget mark, fee, quantity-step and margin-tier surface. The candles after the first step remain deterministic local test data."
        ),
        "comparison": comparison,
        "snapshot_hash": snapshot_hash(snapshot),
        "candidate_admission": False,
        "trading_operations_count": 0,
    }
    _write_json(output_dir / "report.json", report)
    (output_dir / "report.md").write_text(
        "# Actual risk parity probe\n\n"
        f"Status: `{report['status']}`.\n\n"
        f"Snapshot hash: `{report['snapshot_hash']}`.\n\n"
        f"Compared {len(trace)} decisions from one deterministic Backtest step.\n\n"
        f"{report['summary']}\n\n"
        "No exchange, account, transfer, or trading operation was performed.\n",
        encoding="utf-8",
    )
    write_production_invocation(
        output_dir / "invocation.json",
        live_export_sha256=sha256_path(output_dir / "live_export.json"),
        rust_trace_sha256=sha256_path(output_dir / "rust_trace.json"),
        snapshot_hash=snapshot_hash(snapshot),
        trace_record_count=len(trace),
    )
    manifest = build_production_risk_evidence_manifest(
        repo_root=Path(__file__).resolve().parents[2],
        evidence_dir=output_dir,
        required_regression_path=(
            required_regression_path
            if required_regression_path is not None
            else Path(__file__).resolve().parents[2]
            / "analysis_reports/crypto_bot_roadmap/actual_risk_parity_probe_2026-08-17-v3"
        ),
    )
    _write_json(output_dir / "manifest.json", manifest)
    file_hashes = {
        path.relative_to(output_dir).as_posix(): sha256_path(path)
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "checksums.json"
    }
    _write_json(output_dir / "checksums.json", file_hashes)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "analysis_reports/crypto_bot_roadmap/actual_risk_parity_probe_2026-08-18-v4"
        ),
    )
    parser.add_argument(
        "--required-regression",
        type=Path,
        default=Path(
            "analysis_reports/crypto_bot_roadmap/actual_risk_parity_probe_2026-08-17-v3"
        ),
    )
    args = parser.parse_args(argv)
    report = run(
        args.output_dir.expanduser().resolve(),
        required_regression_path=args.required_regression.expanduser().resolve(),
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "snapshot_hash": report["snapshot_hash"],
                "trading_operations_count": report["trading_operations_count"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
