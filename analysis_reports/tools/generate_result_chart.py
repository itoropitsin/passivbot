#!/usr/bin/env python3
"""Generate an interactive HTML chart for a Passivbot backtest artifact."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def _read_csv(path: Path) -> pd.DataFrame:
    if path.suffix == ".gz":
        return pd.read_csv(path, compression="gzip")
    return pd.read_csv(path)


def _first_parseable_datetime_column(df: pd.DataFrame) -> pd.Series | None:
    for column in df.columns:
        parsed = pd.to_datetime(df[column], errors="coerce")
        if parsed.notna().mean() > 0.95:
            return parsed
    return None


def _balance_time_axis(df: pd.DataFrame) -> pd.Series | pd.RangeIndex:
    for column in ("timestamp", "date", "datetime"):
        if column in df.columns:
            return pd.to_datetime(df[column], errors="raise")
    parsed = _first_parseable_datetime_column(df)
    if parsed is not None:
        return parsed
    if "minute" in df.columns:
        return df["minute"]
    return df.index


def _fills_time_axis(df: pd.DataFrame) -> pd.Series | pd.RangeIndex:
    for column in ("timestamp", "date", "datetime"):
        if column in df.columns:
            return pd.to_datetime(df[column], errors="raise")
    parsed = _first_parseable_datetime_column(df)
    if parsed is not None:
        return parsed
    if "minute" in df.columns:
        return df["minute"]
    return df.index


def _first_existing(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def _elapsed_days(x_axis: pd.Series | pd.RangeIndex, row_count: int) -> float:
    if isinstance(x_axis, pd.Series):
        parsed = pd.to_datetime(x_axis, errors="coerce")
        if parsed.notna().mean() > 0.95 and len(parsed) >= 2:
            days = (parsed.iloc[-1] - parsed.iloc[0]).total_seconds() / 86400
            if days > 0:
                return days
    # balance_and_equity artifacts are usually hourly sampled.
    return max(row_count - 1, 1) / 24


def _summary_metrics(
    equity: pd.Series,
    drawdown: pd.Series,
    x_axis: pd.Series | pd.RangeIndex,
    analysis: dict[str, Any],
) -> dict[str, float]:
    start_equity = float(equity.iloc[0])
    final_equity = float(equity.iloc[-1])
    max_equity = float(equity.max())
    days = _elapsed_days(x_axis, len(equity))
    avg_pct_day = float(analysis.get("adg_strategy_eq", 0.0)) * 100
    avg_usd_day = (final_equity - start_equity) / days
    max_profit_usd = max_equity - start_equity
    max_profit_pct = (max_equity / start_equity - 1) * 100 if start_equity else 0.0
    max_drawdown_pct = float(analysis.get("drawdown_worst_strategy_eq", drawdown.max())) * 100
    max_drawdown_usd = float((equity.cummax() - equity).max())
    return {
        "days": days,
        "start_equity": start_equity,
        "final_equity": final_equity,
        "avg_pct_day": avg_pct_day,
        "avg_usd_day": avg_usd_day,
        "max_profit_usd": max_profit_usd,
        "max_profit_pct": max_profit_pct,
        "max_drawdown_usd": max_drawdown_usd,
        "max_drawdown_pct": max_drawdown_pct,
    }


def _load_price_series(backtest_dir: Path, max_points: int = 12000) -> tuple[pd.Series, np.ndarray] | None:
    dataset_path = backtest_dir / "dataset.json"
    if not dataset_path.exists():
        return None
    dataset = json.loads(dataset_path.read_text())
    hlcvs_file = dataset.get("hlcvs_file")
    timestamps_file = dataset.get("timestamps_file")
    coin_index = dataset.get("coin_index", {})
    coins = dataset.get("coins", [])
    if not hlcvs_file or not timestamps_file:
        return None
    coin = "BTC" if "BTC" in coin_index else (coins[0] if coins else None)
    if coin is None:
        return None
    idx = int(coin_index[coin])

    hlcvs_path = Path(hlcvs_file)
    timestamps_path = Path(timestamps_file)
    if not hlcvs_path.exists() or not timestamps_path.exists():
        return None

    with gzip.open(hlcvs_path, "rb") if hlcvs_path.suffix == ".gz" else open(hlcvs_path, "rb") as f:
        hlcvs = np.load(f)
    with gzip.open(timestamps_path, "rb") if timestamps_path.suffix == ".gz" else open(timestamps_path, "rb") as f:
        timestamps = np.load(f)

    # HLCV artifact shape is [time, coin, high/low/close/volume].
    close = hlcvs[:, idx, 2]
    x_price = pd.to_datetime(timestamps, unit="ms")
    if len(close) > max_points:
        step = int(np.ceil(len(close) / max_points))
        close = close[::step]
        x_price = x_price[::step]
    return pd.Series(x_price), close


def generate_chart(backtest_dir: Path, output: Path | None = None) -> Path:
    balance_path = backtest_dir / "balance_and_equity.csv.gz"
    fills_path = backtest_dir / "fills.csv"
    analysis_path = backtest_dir / "analysis.json"
    if not balance_path.exists():
        raise FileNotFoundError(balance_path)
    if not analysis_path.exists():
        raise FileNotFoundError(analysis_path)

    balance = _read_csv(balance_path)
    fills = _read_csv(fills_path) if fills_path.exists() else pd.DataFrame()
    analysis = json.loads(analysis_path.read_text())

    x_balance = _balance_time_axis(balance)
    equity_col = _first_existing(
        balance,
        ("strategy_equity", "equity", "usd_total_equity", "total_equity"),
    )
    if equity_col is None:
        raise KeyError("No equity column found in balance_and_equity.csv.gz")
    balance_col = _first_existing(balance, ("usd_total_balance", "balance", "total_balance"))

    equity = balance[equity_col]
    drawdown = (equity.cummax() - equity) / equity.cummax()
    summary = _summary_metrics(equity, drawdown, x_balance, analysis)

    fig = make_subplots(
        rows=5,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        subplot_titles=(
            "BTC price",
            "Equity / balance",
            "Drawdown",
            "Cumulative realized PnL",
            "Total wallet exposure",
        ),
        row_heights=[0.24, 0.32, 0.15, 0.15, 0.14],
    )
    price_series = _load_price_series(backtest_dir)
    if price_series is not None:
        x_price, close = price_series
        fig.add_trace(
            go.Scatter(
                x=x_price,
                y=close,
                name="BTC close",
                line={"color": "#f59e0b", "width": 1.1},
            ),
            1,
            1,
        )
    fig.add_trace(
        go.Scatter(
            x=x_balance,
            y=equity,
            name="equity",
            line={"color": "#2563eb", "width": 1.6},
        ),
        2,
        1,
    )
    if balance_col is not None:
        fig.add_trace(
            go.Scatter(
                x=x_balance,
                y=balance[balance_col],
                name="balance",
                line={"color": "#111827", "width": 1.0},
            ),
            2,
            1,
        )

    fig.add_trace(
        go.Scatter(
            x=x_balance,
            y=drawdown * 100,
            name="drawdown %",
            line={"color": "#dc2626", "width": 1.2},
            fill="tozeroy",
        ),
        3,
        1,
    )

    if not fills.empty:
        x_fills = _fills_time_axis(fills)
        if "pnl" in fills.columns:
            fig.add_trace(
                go.Scatter(
                    x=x_fills,
                    y=fills["pnl"].cumsum(),
                    name="cum pnl",
                    line={"color": "#16a34a", "width": 1.2},
                ),
                4,
                1,
            )
        if {"twe_long", "twe_short"}.issubset(fills.columns):
            total_exposure = fills["twe_long"].abs() + fills["twe_short"].abs()
            fig.add_trace(
                go.Scatter(
                    x=x_fills,
                    y=total_exposure,
                    name="total wallet exposure",
                    line={"color": "#7c3aed", "width": 1.2},
                ),
                5,
                1,
            )
        elif "wallet_exposure" in fills.columns:
            fig.add_trace(
                go.Scatter(
                    x=x_fills,
                    y=fills["wallet_exposure"].abs(),
                    name="wallet exposure",
                    line={"color": "#7c3aed", "width": 1.2},
                ),
                5,
                1,
            )

    exchange = backtest_dir.parent.name.capitalize()
    completion = analysis.get("backtest_completion_ratio", float("nan"))
    fig.update_layout(
        title=(
            f"{exchange} {backtest_dir.name} | "
            f"avg/day {summary['avg_pct_day']:.4f}% / ${summary['avg_usd_day']:.2f} | "
            f"max profit {summary['max_profit_pct']:.2f}% / ${summary['max_profit_usd']:.2f} | "
            f"max DD {summary['max_drawdown_pct']:.2f}% / ${summary['max_drawdown_usd']:.2f} | "
            f"completion {completion:.0%}"
        ),
        template="plotly_white",
        height=1120,
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.03},
        margin={"l": 55, "r": 25, "t": 85, "b": 45},
    )
    fig.update_yaxes(tickprefix="$", row=1, col=1)
    fig.update_yaxes(ticksuffix="%", row=3, col=1)
    fig.update_yaxes(tickformat=".2f", row=5, col=1)

    output = output or (backtest_dir / "result_chart.html")
    fig.write_html(output, include_plotlyjs="cdn")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backtest_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = generate_chart(args.backtest_dir, args.output)
    print(output)


if __name__ == "__main__":
    main()
