# Bitget Profit Optimization, 1000 USD, DD 25%

Date: 2026-06-27

Context:
- Exchange history: Bitget
- Symbol: BTC
- Period: 2025-06-26 to 2026-06-26
- Candle interval: 1 minute
- Starting balance: 1000 USD
- Source config: `/Users/ivan/Documents/TLI-Setka/analysis_reports/bitget_profit_1000_dd80/configs/bitget_profit_1000_dd80_from_compromise.json`
- Seed source: `/Users/ivan/Documents/TLI-Setka/optimize_results/2026-06-27T11_05_25_bitget_365days_BTC_37a8281e/pareto`
- Optimize run: `/Users/ivan/Documents/TLI-Setka/optimize_results/2026-06-27T11_33_57_bitget_365days_BTC_ec160a68`

Setup:
- `optimize.iters = 3000`
- `optimize.population_size = 120`
- `optimize.n_cpus = 4`
- Scoring: maximize `adg_strategy_eq`, minimize `drawdown_worst_strategy_eq`
- Limits: `drawdown_worst_strategy_eq <= 0.25`, `backtest_completion_ratio >= 0.99`

## Summary

| Candidate | Avg %/day | Avg $/day | Max profit | Max drawdown | Final equity | Recovery max | Completion | Backtest |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Previous aggressive | `0.2283%` | `$3.59` | `131.19% / $1311.91` | `15.66% / $274.44` | `$2310.13` | `30.89d` | `100%` | `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_17_56` |
| Previous max revenue | `0.2572%` | `$4.34` | `158.88% / $1588.77` | `22.58% / $330.09` | `$2584.61` | `54.48d` | `100%` | `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_18_08` |
| `aggressive_dd20_config.json` | `0.1556%` | `$2.13` | `77.84% / $778.37` | `8.43% / $141.33` | `$1776.89` | `68.76d` | `100%` | `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_51_23` |
| `aggressive_dd25_config.json` | `0.1556%` | `$2.13` | `77.84% / $778.37` | `8.43% / $141.33` | `$1776.89` | `68.76d` | `100%` | `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_51_36` |
| `max_revenue_dd25_config.json` | `0.2917%` | `$5.22` | `190.88% / $1908.84` | `17.24% / $355.53` | `$2905.64` | `28.27d` | `100%` | `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_51_46` |

Notes:
- `aggressive_dd20_config.json` and `aggressive_dd25_config.json` are identical because the Pareto `ideal` selector chose the same balanced candidate under both `<=20%` and `<=25%` drawdown filters.
- `max_revenue_dd25_config.json` is also under `20%` drawdown, so it is the revenue-max candidate for both the `<=20%` and `<=25%` bands in this run.
- Compared with the previous max-revenue candidate, the new max-revenue candidate improved Avg %/day from `0.2572%` to `0.2917%`, reduced percentage drawdown from `22.58%` to `17.24%`, and improved max recovery from `54.48d` to `28.27d`.

## Candidate Details

### aggressive_dd20_config.json

- Selection: Pareto `ideal` with `drawdown_worst_strategy_eq <= 0.20`
- Config: `/Users/ivan/Documents/TLI-Setka/analysis_reports/bitget_profit_1000_dd25/final_candidates/aggressive_dd20_config.json`
- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_51_23`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_51_23/result_chart.html`
- Total wallet exposure: max `1.6654`, mean `0.4160`

### aggressive_dd25_config.json

- Selection: Pareto `ideal` with `drawdown_worst_strategy_eq <= 0.25`
- Config: `/Users/ivan/Documents/TLI-Setka/analysis_reports/bitget_profit_1000_dd25/final_candidates/aggressive_dd25_config.json`
- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_51_36`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_51_36/result_chart.html`
- Total wallet exposure: max `1.6654`, mean `0.4160`

### max_revenue_dd25_config.json

- Selection: highest `adg_strategy_eq` with `drawdown_worst_strategy_eq <= 0.25`
- Config: `/Users/ivan/Documents/TLI-Setka/analysis_reports/bitget_profit_1000_dd25/final_candidates/max_revenue_dd25_config.json`
- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_51_46`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_51_46/result_chart.html`
- Total wallet exposure: max `1.6291`, mean `0.8715`

## Interpretation

The main useful result is `max_revenue_dd25_config.json`: it improved both return and percentage drawdown relative to the previous max-revenue candidate, while staying inside the requested 25% drawdown cap and also inside 20%.

The `ideal` selections are more conservative and duplicate the previous practical candidate. They are useful as a balanced reference, but not as the main aggressive candidate from this run.

These are still research backtests, not live-trading approval. Before live use, validate out of sample, verify Bitget hedge/dual-side mode, margin mode, leverage, liquidation distance, fees, funding, minimum order size, and rounding behavior.
