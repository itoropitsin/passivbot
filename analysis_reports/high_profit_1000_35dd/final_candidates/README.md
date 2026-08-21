# High Profit BTC Candidates, Binance Proxy

Date: 2026-06-27

Context:
- Exchange history used for research: Binance
- Target live venue later: Bitget
- Symbol: BTC
- Period: 2025-06-26 to 2026-06-26
- Candle interval: 1 minute
- Starting balance: 1000 USD
- Optimization risk allowance: up to 35% drawdown

Important caveat:
These configs are research results on Binance history as a proxy. They are not ready for real Bitget trading until long Bitget history is tested successfully or the Bitget long-history data issue is resolved.

## Selected Candidates

### profit_max_config.json

Purpose: highest confirmed profit from the 35% drawdown search.

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_32`
- Source config: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_32/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_32/result_chart.html`
- ADG: `0.001546798839554775`
- Worst drawdown: `0.15981991813862173` (`15.98%`)
- Completion ratio: `1.0`
- Final equity: `1757.0336`
- Fills: `911`
- Net PnL: `811.5927070312493`
- Fees: `-46.69981400781249`
- Total wallet exposure mean/max: `0.5205530362413859` / `0.9199960459029163`

Notes:
- Most aggressive of the selected candidates.
- Improvement comes mainly from heavier short-side usage.
- Short total wallet exposure limit is about `0.92`, compared with about `0.43` in the previous hedge-best config.

### compromise_config.json

Purpose: best profit/drawdown compromise found in this run.

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_45`
- Source config: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_45/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_45/result_chart.html`
- ADG: `0.0013323659053481318`
- Worst drawdown: `0.09117157971839196` (`9.12%`)
- Completion ratio: `1.0`
- Final equity: `1629.8595`
- Fills: `728`
- Net PnL: `675.1378000000009`
- Fees: `-45.07062344`
- Total wallet exposure mean/max: `0.3639519505351203` / `0.8195065064888947`

Notes:
- Strong candidate if keeping drawdown near the earlier 10% target still matters.
- ADG is about 2.44x the previous confirmed hedge-best while drawdown is only slightly higher.
- Short total wallet exposure limit is about `0.82`.

### lower_dd_config.json

Purpose: more conservative candidate with lower drawdown.

- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_56`
- Source config: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_56/config.json`
- Chart: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-27T10_11_56/result_chart.html`
- ADG: `0.0009013511209752245`
- Worst drawdown: `0.06094882777521571` (`6.09%`)
- Completion ratio: `1.0`
- Final equity: `1389.0927`
- Fills: `756`
- Net PnL: `423.4759843749998`
- Fees: `-31.518393353749996`
- Total wallet exposure mean/max: `0.265758759001892` / `0.5598553609941253`

Notes:
- Lower-risk choice among the profitable candidates.
- Still beats the previous confirmed hedge-best ADG by about 1.65x with lower drawdown.
- Short total wallet exposure limit is about `0.56`.

## Baselines

Original 500 USD long-only baseline:
- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-26T20_46_03`
- ADG: `0.00011024800517955846`
- Worst drawdown: `0.0994517695369313` (`9.95%`)
- Completion ratio: `1.0`

Previous 1000 USD hedge-best:
- Backtest: `/Users/ivan/Documents/TLI-Setka/backtests/binance/2026-06-26T21_41_25`
- ADG: `0.0005468199788258854`
- Worst drawdown: `0.07921699832231309` (`7.92%`)
- Completion ratio: `1.0`

