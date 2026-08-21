# Bitget Profit Optimization, 1000 USD, DD 80%

Date: 2026-06-27

Context:
- Exchange history: Bitget
- Symbol: BTC
- Period: 2025-06-26 to 2026-06-26
- Candle interval: 1 minute
- Starting balance: 1000 USD
- Optimization target: higher revenue / average daily growth
- Optimization drawdown allowance: up to 80%
- Source config: `/Users/ivan/Documents/TLI-Setka/analysis_reports/high_profit_1000_35dd/final_candidates/compromise_config.json`
- Optimize run: `/Users/ivan/Documents/TLI-Setka/optimize_results/2026-06-27T11_05_25_bitget_365days_BTC_37a8281e`

## Summary

| Candidate | Avg %/day | Avg $/day | Max profit | Max drawdown | Final equity | Completion |
|---|---:|---:|---:|---:|---:|---:|
| Previous Bitget best | `0.1003%` | `$1.22` | `44.41% / $444.08` | `9.51% / $90.48` | `$1443.84` | `100%` |
| `practical_dd10_config.json` | `0.1556%` | `$2.13` | `77.84% / $778.37` | `8.43% / $141.33` | `$1776.89` | `100%` |
| `aggressive_dd20_config.json` | `0.2283%` | `$3.59` | `131.19% / $1311.91` | `15.66% / $274.44` | `$2310.13` | `100%` |
| `max_revenue_config.json` | `0.2572%` | `$4.34` | `158.88% / $1588.77` | `22.58% / $330.09` | `$2584.61` | `100%` |

Notes:
- Average USD/day is simple average equity gain per elapsed day.
- Average %/day is `adg_strategy_eq * 100`.
- Max profit is measured from starting equity to peak equity.
- Max drawdown USD is peak-to-trough equity loss; the percent drawdown is the strategy's worst drawdown ratio.
- HTML result charts include BTC close price, equity/balance, drawdown, cumulative realized PnL, and total wallet exposure.

## Candidate Details

### practical_dd10_config.json

Best practical candidate from this run.

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_17_43`
- Config: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_17_43/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_17_43/result_chart.html`
- Avg/day: `0.1556%` / `$2.13`
- Max profit: `77.84%` / `$778.37`
- Max drawdown: `8.43%` / `$141.33`
- Final equity: `$1776.89`
- Completion ratio: `1.0`

Why it matters:
- It improves average daily revenue over the previous Bitget best by about 55%.
- Percent drawdown is lower than the previous Bitget best, though dollar drawdown is higher because equity grows much more before the worst drop.

### aggressive_dd20_config.json

Higher revenue with materially higher risk.

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_17_56`
- Config: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_17_56/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_17_56/result_chart.html`
- Avg/day: `0.2283%` / `$3.59`
- Max profit: `131.19%` / `$1311.91`
- Max drawdown: `15.66%` / `$274.44`
- Final equity: `$2310.13`
- Completion ratio: `1.0`

### max_revenue_config.json

Maximum confirmed revenue from this run.

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_18_08`
- Config: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_18_08/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_18_08/result_chart.html`
- Avg/day: `0.2572%` / `$4.34`
- Max profit: `158.88%` / `$1588.77`
- Max drawdown: `22.58%` / `$330.09`
- Final equity: `$2584.61`
- Completion ratio: `1.0`

## Current Preference

For a normal next validation step, prefer `practical_dd10_config.json`.

For a high-risk separate subaccount experiment, compare `aggressive_dd20_config.json` and `max_revenue_config.json`, but only with isolated margin and money that can be lost.

These results are still backtests. Before live trading, run out-of-sample or walk-forward validation and verify Bitget account mode, isolated/cross margin, leverage, position mode, liquidation distance, fees, and minimum order behavior.
