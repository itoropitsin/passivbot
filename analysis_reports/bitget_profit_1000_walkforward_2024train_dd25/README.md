# BTC/Bitget Walk-Forward Optimization With Validation

Date: 2026-06-29

## Scope

This run tested whether BTC/Bitget configs optimized on an earlier clean Bitget window can survive the next year out of sample.

- Train optimizer window: `2024-07-01..2025-06-26`
- Validation window: `2025-06-26..2026-06-26`
- Exchange: Bitget
- Symbol: BTC
- Starting balance: 1000 USD
- Candle interval: 1m
- Suite: `n`
- Seed source: `optimize_results/2026-06-27T11_33_57_bitget_365days_BTC_ec160a68/pareto`

An attempted train start at `2024-06-26` failed before optimization because the warmup span reached into BTC/USD rows containing NaN. The clean train config was therefore shifted to `2024-07-01`, where the optimizer reported `invalid_rows=0` and `synthetic_filled=0`.

## Optimizer Run

- Config: `configs/bitget_profit_1000_walkforward_2024-07_train_dd25_seeded.json`
- Result: `/Users/ivan/Documents/TLI-Setka/optimize_results/2026-06-29T11_53_18_bitget_360days_BTC_dcd75a70`
- Pareto size: 45
- Train objective: maximize `adg_strategy_eq`, minimize `drawdown_worst_strategy_eq`
- Limits: `drawdown_worst_strategy_eq <= 0.25`, `backtest_completion_ratio >= 0.99`

The train Pareto front found several apparently strong candidates, including roughly `0.09%/day` train ADG with sub-10% train drawdown.

## Validation Selection

Six representative train Pareto points were selected:

- best train ADG under train DD <= 1%
- best train ADG under train DD <= 2%
- best train ADG under train DD <= 5%
- best train ADG under train DD <= 7.5%
- best train ADG under train DD <= 10%
- normalized train ideal point

Selected configs and machine-readable results are stored in:

- `validation/selected_train_candidates/`
- `validation/validation_results.json`

## Validation Results

| Candidate | Train ADG | Train DD | Validation ADG | Validation DD | Validation Gain | Chart |
|---|---:|---:|---:|---:|---:|---|
| `best_train_dd010` | 0.0084% | 0.95% | -0.0110% | 4.93% | 96.06% | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-29T12_09_07/result_chart.html) |
| `best_train_dd020` | 0.0147% | 1.71% | -0.0284% | 11.68% | 90.17% | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-29T12_09_55/result_chart.html) |
| `best_train_dd050` | 0.0612% | 4.92% | -0.0468% | 20.26% | 84.31% | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-29T12_10_00/result_chart.html) |
| `best_train_dd075` | 0.0910% | 7.38% | -0.0433% | 21.07% | 85.39% | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-29T12_10_05/result_chart.html) |
| `best_train_dd100` | 0.0918% | 8.92% | -0.0493% | 21.47% | 83.53% | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-29T12_10_11/result_chart.html) |
| `train_ideal` | 0.0608% | 4.70% | -0.0491% | 20.33% | 83.60% | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-29T12_10_16/result_chart.html) |

## Conclusion

No candidate from this train window passed validation. All six selected train Pareto points had negative ADG on `2025-06-26..2026-06-26`.

This is a useful failure: the earlier Bitget year and the latest Bitget year behave differently enough that one-year train optimization does not transfer cleanly. Do not promote these walk-forward candidates.

Next research direction should be a stricter multi-window selection process:

- keep `2025-06-26..2026-06-26` as the final clean validation window;
- use `2024-07-01..2025-06-26` only as train or stress input;
- reject any candidate with negative validation ADG;
- prefer candidates that stay positive on both windows, even if single-window ADG is lower.
