# Bitget BTC Optimization Case Study (2026-06-27)

This document captures the useful context from a local BTC strategy optimization session.
It is a research handoff, not a live-trading approval.

## Scope

- Workspace: `/Users/ivan/Documents/TLI-Setka`
- CLI: `.venv/bin/passivbot`
- Exchange target: Bitget
- Historical proxy used earlier: Binance
- Symbol: BTC
- Main validation period: `2025-06-26` to `2026-06-26`
- Candle interval: 1 minute
- Main starting balance: `1000 USD`
- Earlier small-balance target: about `500 USD`

Do not inspect or print `api-keys.json`. The Bitget key may exist locally, but it is not needed
for the research artifacts listed here.

## Why Binance Was Used First

Early long Bitget historical runs had data issues. Binance was used as a proxy to search for a
promising strategy region. Once a newer Bitget artifact completed successfully, the selected
configs were revalidated on Bitget history and later optimized directly on Bitget.

Key implication:

- Binance proxy results are useful for exploration only.
- Final candidate ranking must come from Bitget-native backtests.
- A config is not live-ready just because it performs well on Binance proxy data.

## Baseline And Earlier Results

Original 500 USD long-only baseline:

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-26T20_46_03`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-26T20_46_03/result_chart.html`
- ADG: `0.00011024800517955846`
- Worst drawdown: `9.95%`
- Completion: `100%`

Best later Binance proxy hedge candidate before Bitget validation:

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_32`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_32/result_chart.html`
- ADG: `0.001546798839554775`
- Worst drawdown: `15.98%`
- Final equity: `$1757.03`
- Completion: `100%`

The Binance proxy search showed that a hedge/short-heavy region was much more profitable than
long-only BTC for this year. It did not establish final Bitget suitability.

## First Successful Bitget Validation

User-provided Bitget artifact:

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_43_17`
- Config period: `2025-06-26` to `2026-06-26`
- Completion: `100%`
- ADG: `0.000044338992958969925`
- Worst drawdown: `2.85%`
- Final equity: `$1016.24`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_43_17/result_chart.html`

This proved the new Bitget 12-month history path was usable for BTC.

## Binance-Derived Configs Rechecked On Bitget

Saved configs:

- Directory: `/Users/ivan/Documents/TLI-Setka/analysis_reports/high_profit_1000_35dd/final_candidates`
- Report: `/Users/ivan/Documents/TLI-Setka/analysis_reports/high_profit_1000_35dd/final_candidates/BITGET_VALIDATION_2026-06-27.md`

Bitget validation results:

| Candidate | Avg %/day | Avg $/day | Max profit | Max drawdown | Final equity | Completion |
|---|---:|---:|---:|---:|---:|---:|
| Reference artifact | `0.0044%` | `$0.04` | `1.66% / $16.60` | `2.85% / $23.54` | `$1016.24` | `100%` |
| `profit_max_config.json` | `0.0819%` | `$0.95` | `35.21% / $352.07` | `7.61% / $76.46` | `$1347.70` | `100%` |
| `compromise_config.json` | `0.1003%` | `$1.22` | `44.41% / $444.08` | `9.51% / $90.48` | `$1443.84` | `100%` |
| `lower_dd_config.json` | `0.0311%` | `$0.33` | `12.55% / $125.47` | `3.57% / $36.02` | `$1119.82` | `100%` |

The best Binance proxy config was not the best on Bitget. The Bitget winner from this batch was
`compromise_config.json`.

## Bitget-Native Revenue Optimization

The next optimization was run directly on Bitget history from the Bitget-validated compromise
region.

Input config:

- `/Users/ivan/Documents/TLI-Setka/analysis_reports/bitget_profit_1000_dd80/configs/bitget_profit_1000_dd80_from_compromise.json`

Optimization run:

- `/Users/ivan/Documents/TLI-Setka/optimize_results/2026-06-27T11_05_25_bitget_365days_BTC_37a8281e`

Key setup:

- Exchange: Bitget
- Symbol: BTC
- Period: `2025-06-26` to `2026-06-26`
- Starting balance: `1000 USD`
- Drawdown allowance during optimization: up to `80%`
- Goal: increase revenue while preserving the Pareto trade-off against drawdown

Final saved candidates:

- Directory: `/Users/ivan/Documents/TLI-Setka/analysis_reports/bitget_profit_1000_dd80/final_candidates`
- `practical_dd10_config.json`
- `aggressive_dd20_config.json`
- `max_revenue_config.json`
- Report: `/Users/ivan/Documents/TLI-Setka/analysis_reports/bitget_profit_1000_dd80/final_candidates/README.md`

Confirmed Bitget backtests:

| Candidate | Avg %/day | Avg $/day | Max profit | Max drawdown | Final equity | Completion | Backtest |
|---|---:|---:|---:|---:|---:|---:|---|
| Previous Bitget best | `0.1003%` | `$1.22` | `44.41% / $444.08` | `9.51% / $90.48` | `$1443.84` | `100%` | `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_45_56` |
| Practical | `0.1556%` | `$2.13` | `77.84% / $778.37` | `8.43% / $141.33` | `$1776.89` | `100%` | `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_17_43` |
| Aggressive | `0.2283%` | `$3.59` | `131.19% / $1311.91` | `15.66% / $274.44` | `$2310.13` | `100%` | `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_17_56` |
| Max revenue | `0.2572%` | `$4.34` | `158.88% / $1588.77` | `22.58% / $330.09` | `$2584.61` | `100%` | `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T11_18_08` |

Recommended next validation candidate:

- Normal research path: `practical_dd10_config.json`
- High-risk isolated subaccount experiment only: compare `aggressive_dd20_config.json` and
  `max_revenue_config.json`

## Metric Conventions Used In The Session

The final reports use more user-readable aliases:

- `Avg %/day`: `adg_strategy_eq * 100`
- `Avg $/day`: simple average `(final strategy equity - starting strategy equity) / elapsed days`
- `Max profit`: peak strategy equity minus starting strategy equity, shown as percent and USD
- `Max drawdown`: worst peak-to-trough strategy equity loss, shown as percent and USD

These aliases are report-format conveniences. The underlying optimizer metric remains
`adg_strategy_eq`.

## Exposure Interpretation

`total_wallet_exposure` estimates open position notional relative to equity:

```text
wallet exposure = position notional value / account equity
```

For hedge mode:

```text
total wallet exposure = abs(long exposure) + abs(short exposure)
```

Examples:

- `0.25` means open notional is about 25% of equity.
- `1.00` means open notional is about equal to equity.
- `1.50` means open notional is about 1.5x equity.

Exposure is not a liquidation trigger and does not close trades at `1.0`. It measures how much
market risk is currently open. Drawdown measures how much equity has already fallen from a peak.

## Hedge Mode Behavior

The selected configs are hedge-style. Long and short grids can exist as separate sides:

- Long side: buys lower, closes by selling higher.
- Short side: sells higher, closes by buying lower.

The profitable region was short-heavy. In earlier Bitget-validated configs, long exposure was
small while short exposure carried most of the strategy. Before live use, the Bitget account must
be verified to use the correct hedge/dual-side position mode.

## Deposit Scaling Notes

A 100 USD replay of the Bitget compromise config completed successfully:

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_57_46`
- ADG: `0.0010528596345387164`
- Worst drawdown: `10.05%`
- Final equity: `$147.23`
- Max drawdown: about `$13.64`

The 100 USD run did not scale perfectly from the 1000 USD run because minimum order size and
quantity rounding become significant. In that run, fills reached the minimum `0.0001 BTC` size.
At BTC prices around `60k-100k`, that minimum order is roughly `6-10 USD` notional, which is a
large fraction of a 100 USD account.

Interpretation:

- 100 USD is useful for a small technical live trial.
- It should not be treated as a perfect 1/10 replica of a 1000 USD strategy.
- 1000 USD gives the grid more resolution and less rounding distortion.

## Chart Generation

Reusable helper:

- `/Users/ivan/Documents/TLI-Setka/analysis_reports/tools/generate_result_chart.py`

Usage:

```bash
.venv/bin/python analysis_reports/tools/generate_result_chart.py /path/to/backtest_dir
```

The helper writes `result_chart.html` into the backtest directory. It handles the earlier chart
bug where `balance_and_equity` used date timestamps while `fills` used numeric `minute` values.
It now prefers timestamp/date columns and includes:

- BTC close price
- Equity / balance
- Drawdown
- Cumulative realized PnL
- Total wallet exposure

## Live-Readiness Caveats

These artifacts are still backtests, not live approval.

Before any real Bitget trading:

1. Run out-of-sample or walk-forward validation.
2. Verify Bitget account mode: hedge/dual-side, isolated vs cross, leverage, margin tier.
3. Check liquidation distance and maintenance margin behavior with the chosen config.
4. Confirm fees, funding, minimum order size, and rounding behavior on live Bitget.
5. Start only with a small isolated test amount if doing a live trial.
6. Keep high-risk configs on a separate subaccount with money that can be lost.

## Session Conclusion

The most useful result from this session is that Bitget-native optimization found a practical
candidate that improved the prior Bitget best:

- Previous Bitget best: `0.1003%` / `$1.22` average per day, `9.51%` drawdown.
- New practical candidate: `0.1556%` / `$2.13` average per day, `8.43%` drawdown.

Use `practical_dd10_config.json` as the next normal validation candidate. Treat
`aggressive_dd20_config.json` and `max_revenue_config.json` as high-risk research candidates only.
