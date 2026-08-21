# Bitget Validation, 2026-06-27

Context:
- Validation artifact provided by user: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_43_17`
- Exchange history: Bitget
- Symbol: BTC
- Period: 2025-06-26 to 2026-06-26
- Candle interval: 1 minute
- Starting balance: 1000 USD

The new Bitget artifact has `backtest_completion_ratio = 1.0`, so the long-history issue did not appear in this run.

## Reference Artifact

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_43_17`
- Config: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_43_17/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_43_17/result_chart.html`
- ADG: `0.000044338992958969925`
- Worst drawdown: `0.028456642336980177` (`2.85%`)
- Completion ratio: `1.0`
- Final equity: `1016.2431`

## Candidate Results On Bitget

| Candidate | Avg %/day | Avg $/day | Max profit | Max drawdown | Final equity | Completion |
|---|---:|---:|---:|---:|---:|---:|
| Reference artifact | `0.0044%` | `$0.04` | `1.66% / $16.60` | `2.85% / $23.54` | `$1016.24` | `100%` |
| `profit_max_config.json` | `0.0819%` | `$0.95` | `35.21% / $352.07` | `7.61% / $76.46` | `$1347.70` | `100%` |
| `compromise_config.json` | `0.1003%` | `$1.22` | `44.41% / $444.08` | `9.51% / $90.48` | `$1443.84` | `100%` |
| `lower_dd_config.json` | `0.0311%` | `$0.33` | `12.55% / $125.47` | `3.57% / $36.02` | `$1119.82` | `100%` |

### Best On Bitget: compromise_config.json

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_45_56`
- Config: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_45_56/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_45_56/result_chart.html`
- ADG: `0.001003454488320621`
- Average per day: `0.1003%` / `$1.22`
- Max profit: `44.41%` / `$444.08`
- Worst drawdown: `0.09514318109452584` (`9.51%`)
- Max drawdown: `9.51%` / `$90.48`
- Completion ratio: `1.0`
- Final equity: `1443.8428`
- Fills: `944`
- Net PnL: `475.68759999999963`
- Fees: `-31.739875447999992`
- Total wallet exposure mean/max: `0.24297264123125697` / `0.820073218991757`

Notes:
- This is the strongest validated candidate on Bitget among the three top Binance-derived configs.
- It beats the user-provided Bitget artifact by a wide margin, but uses materially higher exposure and drawdown.

### profit_max_config.json

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_45_44`
- Config: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_45_44/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_45_44/result_chart.html`
- ADG: `0.0008185510922302797`
- Average per day: `0.0819%` / `$0.95`
- Max profit: `35.21%` / `$352.07`
- Worst drawdown: `0.07609298378639238` (`7.61%`)
- Max drawdown: `7.61%` / `$76.46`
- Completion ratio: `1.0`
- Final equity: `1347.7`
- Fills: `1244`
- Net PnL: `380.37539093749933`
- Fees: `-27.186103699624994`
- Total wallet exposure mean/max: `0.20505851798503796` / `0.9200034259760075`

Notes:
- This was best on Binance, but not best on Bitget.
- The ranking change suggests the Binance proxy was useful for discovery, but not reliable enough for final selection.

### lower_dd_config.json

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_46_06`
- Config: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_46_06/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/bitget/2026-06-27T10_46_06/result_chart.html`
- ADG: `0.0003106119053342127`
- Average per day: `0.0311%` / `$0.33`
- Max profit: `12.55%` / `$125.47`
- Worst drawdown: `0.035733464910129936` (`3.57%`)
- Max drawdown: `3.57%` / `$36.02`
- Completion ratio: `1.0`
- Final equity: `1119.8174`
- Fills: `1491`
- Net PnL: `137.9397495696915`
- Fees: `-15.399467140843743`
- Total wallet exposure mean/max: `0.08533895720851004` / `0.5598301762385857`

Notes:
- Conservative choice among the tested top configs.
- Still significantly better than the reference artifact, with only modest drawdown increase.

## Conclusion

Use `compromise_config.json` as the current best Bitget-validated candidate from this set.

Do not treat this as live-ready yet. Before live Bitget trading, the next step should be a Bitget-native optimization or walk-forward/out-of-sample validation using this now-working Bitget history path.

## Chart Generation

Use this saved helper for future HTML charts:

```bash
.venv/bin/python analysis_reports/tools/generate_result_chart.py /path/to/backtest_dir
```

The helper uses timestamp/date columns when available and avoids mixing date axes with numeric `minute` values from fills.
