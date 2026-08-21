# BTC/Bitget Final Candidate Stability Check

Date: 2026-06-27

## Scope

Checked the two unique final BTC/Bitget configs from `analysis_reports/bitget_profit_1000_dd25/final_candidates/`:

- `aggressive_dd20_config.json` (`aggressive_dd25` is identical)
- `max_revenue_dd25_config.json`

All runs used Bitget BTC, 1m candles, 1000 USD starting balance, suite `n`.

## Result

`max_revenue_dd25` is not robust outside the optimized 2025-06-26..2026-06-26 window. It turns negative on all extra windows and reaches roughly 75-82% worst drawdown.

`aggressive_dd20` is more stable on 2y/3y windows, but its drawdown rises above the original 20-25% target. It also turns slightly negative on the 2024-01-01..2025-01-01 check.

## Metrics

| Window | Dates | Config | ADG | Worst DD | Gain | Mean TWE | Max Recovery | Chart |
|---|---|---|---:|---:|---:|---:|---:|---|
| 2y | 2024-06-26..2026-06-26 | `max_revenue_dd25` | -0.0883% | 81.17% | 52.48% | 1.00 | 713.94d | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T12_20_48/result_chart.html) |
| 2y | 2024-06-26..2026-06-26 | `aggressive_dd20` | 0.0294% | 35.84% | 123.92% | 0.34 | 596.21d | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T12_29_14/result_chart.html) |
| 3y | 2023-06-26..2026-06-26 | `max_revenue_dd25` | -0.1156% | 82.26% | 42.83% | 1.01 | 713.94d | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T12_30_16/result_chart.html) |
| 3y | 2023-06-26..2026-06-26 | `aggressive_dd20` | 0.0549% | 28.71% | 149.38% | 0.34 | 507.12d | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T12_30_35/result_chart.html) |
| 2024y | 2024-01-01..2025-01-01 | `max_revenue_dd25` | -0.4823% | 75.12% | 39.52% | 1.00 | 172.94d | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T12_31_22/result_chart.html) |
| 2024y | 2024-01-01..2025-01-01 | `aggressive_dd20` | -0.0411% | 28.71% | 92.49% | 0.33 | 172.94d | [chart](/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T12_31_36/result_chart.html) |

## Data Coverage Note

For windows starting before June 2024, the backtest preparation reported partial Bitget BTC v2 coverage beginning at `2024-06-01T08:31:00`, with earlier rows synthetic-filled by the materializer. Treat the 3y and 2024-01..2025-01 results as stress checks rather than clean Bitget-native history.

## Conclusion

Do not promote `max_revenue_dd25` based on these stability checks. If continuing this line, use `aggressive_dd20` as the safer comparison baseline, then re-optimize or filter with wider-window constraints before considering it for live-candidate review.
