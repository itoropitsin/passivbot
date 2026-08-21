# Tools

Most tools below require the full install profile:

```shell
python3 -m pip install -e ".[full]"
```

## Pareto dashboard for optimizer runs

`passivbot tool pareto-dash` scans one or more `optimize_results/` directories and launches a Plotly Dash UI with scatter plots, histograms, suite-aware metrics, and CSV export. Use the full install profile (`python3 -m pip install -e ".[full]"`) if those dashboard dependencies are not already installed.

```shell
passivbot tool pareto-dash --data-root optimize_results
```

Pass `--run optimize_results/<timestamp>/` to load a specific run or point it at the entire results directory to browse multiple runs at once.

## Pareto single-candidate explorer

`passivbot tool pareto` reads a Pareto directory of JSON members, optionally filters it with
optimizer-style limit expressions, and selects one candidate using a named decision method.
If you omit the path entirely, it defaults to the newest local `optimize_results/.../pareto`
directory by lexicographic run-directory name, considering only runs whose `pareto/`
subdirectory contains at least one `*.json` candidate. If you point it at an optimize run
directory instead of the nested `pareto/` directory, it resolves that automatically.

```shell
passivbot tool pareto optimize_results/.../pareto
passivbot tool pareto
passivbot tool pareto optimize_results/.../pareto -m reference \
  --target adg_strategy_eq=0.001 \
  --target drawdown_worst_strategy_eq=0.25
passivbot tool pareto optimize_results/.../pareto \
  -l 'drawdown_worst_strategy_eq<=0.35' \
  -l 'adg_strategy_eq>0.0'
passivbot tool pareto -o sharpe_ratio_strategy_eq,adg_strategy_eq,strategy_eq_recovery_days_max \
  -m ideal
passivbot tool pareto optimize_results/... -m utility \
  --weight adg_strategy_eq=4 \
  --weight drawdown_worst_strategy_eq=2 \
  --show-top 5
passivbot tool pareto --json
```

Available methods:

- `knee` - balanced compromise point when you want a compromise selector instead of ideal-point distance
- `reference` - closest to user-specified aspiration targets
- `ideal` - closest to the observed ideal point on the current front; default method
- `utility` - highest weighted normalized utility
- `lexicographic` - strict objective priority order
- `outranking` - simplified PROMETHEE-style pairwise net-flow ranking

The explorer applies limits first, then ranks the retained candidates. It is intended for quickly
promoting one config out of a large Pareto front without opening the dashboard. Its selection
methods are practical decision heuristics for high-dimensional Passivbot fronts, not full formal
multi-criteria decision-analysis implementations.

`passivbot tool pareto-explorer` is a CLI alias for the same tool.

The output also shows the retained front's ideal point: the best observed value for each active
objective after any `--limit` filters are applied.

`-o` / `--objectives` is not limited to the original `optimize.scoring` list. You can also name
other stored metrics such as `sharpe_ratio_strategy_eq` as long as the Pareto JSON
members contain that metric and Passivbot knows whether higher or lower is better.

## Pareto transformations / static plots

`src/tools/pareto_transform.py` converts `all_results.bin` or individual Pareto JSON entries into CSV/JSON summaries for external analysis. The legacy `src/pareto_store.py` still produces quick matplotlib scatter plots if you prefer static images.

```shell
passivbot tool pareto-transform optimize_results/.../all_results.bin --out summary.csv
python3 src/pareto_store.py optimize_results/.../pareto/
```

`passivbot tool pareto-analyze` inspects a Pareto front's config and metric distributions.
`passivbot tool pareto-analysis` is an alias for the same command. `passivbot tool
pareto-compress` selects a compact representative subset from a front, and
`passivbot tool merge-paretos` merges Pareto fronts into starting configs.

```shell
passivbot tool pareto-analyze optimize_results/.../pareto
passivbot tool pareto-compress optimize_results/.../pareto 8 --output-dir selected_pareto_8
passivbot tool merge-paretos optimize_results/run_a/pareto optimize_results/run_b/pareto
```

## Iterative backtester utilities

`src/tools/iterative_backtester.py` and `iterative_history_plot.py` help replay slices of the backtester (or real fills) interactively so you can inspect order-by-order behaviour. Useful when tuning configs by hand.

```shell
passivbot tool iterative-backtester --config configs/your_config.json --symbol BTC/USDT:USDT
passivbot tool iterative-history-plot backtests/.../fills.csv
```

## Historical data helpers

- `passivbot download` – Pre-warm the v2 OHLCV store using the same config/date/exchange selection as backtesting.
- `passivbot tool inspect-ohlcvs` – Inspect v2 OHLCV cache metadata and gaps.
- `passivbot tool bitget-ohlcv-quality` – Check the exact stored Bitget HLCV range for missing, invalid, future, or checksum-broken rows without downloading or changing it.
- `passivbot tool bitget-public-market-profile` – Build a normalized public Bitget market profile from a saved public execution-input file without downloading, reading credentials, or accessing account endpoints.
- `passivbot tool pad-historical-daily` – Ensures daily OHLCV shards are present for the downloader when new coins are added.
- `passivbot tool verify-hlcvs-data` – Validates prepared HLCV datasets and coverage metadata before long optimizations/backtests.
- `passivbot tool streamline-json` – Normalizes/compacts JSON configs (`passivbot tool streamline-json configs/examples/default_trailing_grid_long_npos7.json`).
- `passivbot tool candle-doctor` – Audits legacy `caches/ohlcv/...` shards for corruption, stale index entries, and legacy-format issues; add `--fix` to apply automatic repairs before importing into the v2 store.
- `passivbot tool migrate-historical-data` – Converts legacy `historical_data/ohlcvs_<exchange>/...` shards into the current `caches/ohlcv/...` layout.

## Crypto research gates and Bitget recorder

The crypto roadmap uses immutable run bundles under
`analysis_reports/crypto_bot_roadmap/runs/<run_id>/`.

```shell
passivbot tool research-gates --help
passivbot tool bitget-profile --help
passivbot tool bitget-public-data --help
passivbot tool bitget-public-market-profile --help
passivbot tool bitget-ohlcv-repair --help
passivbot tool bitget-ohlcv-quality --help
passivbot tool bitget-funding-archive --help
passivbot tool bitget-funding-horizon --help
passivbot tool crypto-historical-preflight --help
passivbot tool crypto-runtime-evidence --help
passivbot tool strategy-shadow --help
passivbot tool strategy-shadow-process --help
passivbot tool strategy-shadow-stream --help
passivbot tool strategy-shadow-stream-process --help
passivbot tool crypto-risk-grid --help
passivbot tool risk-parity-harness --help
```

Parity-harness сравнивает уже сохранённые решения двух adapters. Fixture-adapters
нужны только для регрессий; допуск даёт только evidence из настоящего Python
live gate и Rust `Backtest` gate. Если хотя бы один adapter или обязательное поле
snapshot отсутствует, результат `blocked`, а эффективное решение `deny`:

```shell
passivbot tool risk-parity-harness \
  --snapshot path/to/risk_snapshot.json \
  --left-decision path/to/live_decision.json \
  --right-decision path/to/tester_decision.json \
  --output path/to/parity.json
```

Перед историческим отбором можно выполнить локальные сценарии безопасности без
подключения к счёту. Команда запускает Rust/Python-проверки, сохраняет хэши её
вывода и создаёт машинные признаки для `research-gates`:

```shell
passivbot tool crypto-risk-grid \
  --output analysis_reports/crypto_bot_roadmap/inputs/risk_grid_scenarios_2026-08-17.json
```

Для проверки завершённого paper/live-периода передайте журнал monitor-событий.
Команда считает длительность, открытые заявки, исполнения, повторы и закрытые
торговые циклы из самого журнала:

```shell
passivbot tool crypto-runtime-evidence \
  --events path/to/events.ndjson \
  --output analysis_reports/crypto_bot_roadmap/runs/<run_id>/runtime_evidence.json
```

Журнал считается доказательным только если каждая строка содержит в `payload`
идентификаторы `strategy_id`, `cycle_id` и `config_hash`; отсутствие любого из
них блокирует paper/live gates.

`strategy-shadow` принимает только сохранённые public snapshots и создаёт
`result.json` с длительностью, количеством плановых заявок, фактическими
restarts/replays, хэшами входа и тремя независимыми local-only прогонами. Для
`shadow_readiness` этот результат можно передать напрямую как `--metrics`;
gate сам извлечёт измерения и не примет отдельно переписанные цифры:

```shell
passivbot tool strategy-shadow \
  --input path/to/public_snapshots.json \
  --output-dir analysis_reports/crypto_bot_roadmap/runs/<shadow_run_id>
passivbot tool strategy-shadow-process \
  --input path/to/public_snapshots.json \
  --output-dir analysis_reports/crypto_bot_roadmap/runs/<shadow_process_run_id> \
  --config path/to/shadow_config.json \
  --restart-count 5 \
  --deterministic-replay-runs 3
passivbot tool research-gates \
  --stage shadow_readiness \
  --metrics analysis_reports/crypto_bot_roadmap/runs/<shadow_process_run_id>/result.json \
  --events analysis_reports/crypto_bot_roadmap/runs/<shadow_process_run_id>/events.ndjson \
  --market-profile path/to/public_market_profile.json \
  --config path/to/shadow_config.json \
  --dataset path/to/public_snapshots.json \
  --run-id <readiness_run_id>
```

`strategy-shadow-process` запускает тот же local-only driver в отдельных
дочерних процессах. Он сохраняет PID, коды завершения, хэши состояния и время
восстановления, а также связывает результат с хэшами текущего кода,
конфигурации и входного файла. Это инженерное evidence для readiness gate, но
оно не заменяет фактические 24 часа работы и не создаёт заявок на бирже.

`strategy-shadow-stream` — непрерывный local-only наблюдатель для следующего
этапа. Он получает только публичные снимки Bitget, добавляет явно описанную
детерминированную policy-пробу и передаёт их в существующий `strategy-shadow`.
Заявки и исполнения остаются в локальном paper ledger. Команда требует
`--duration-seconds` или `--max-samples`, сохраняет исходные ответы Bitget,
хэши, решения policy и checkpoint восстановления. Время стратегии берётся
только из `source_timestamp_ms` ответа биржи; `observed_at_ms` используется
только для наблюдения и расписания. Повтор одинакового source event записывается
как heartbeat и не вызывает шаг стратегии. Сырые market snapshots и внутренние
strategy inputs хранятся раздельно. Ошибка публичного сбора помечает результат
как failed, а ошибка обработки останавливает сессию fail-closed. Эта policy не
является оптимизированным сетом и не может быть допущена к реальной торговле.

Пример ограниченного smoke-run:

```shell
passivbot tool strategy-shadow-stream \
  --output-dir analysis_reports/crypto_bot_roadmap/runs/<stream_run_id> \
  --symbol BTCUSDT --category USDT-FUTURES \
  --side long --interval-seconds 60 --max-source-gap-seconds 300 \
  --duration-seconds 3600
```

Для `shadow_readiness` нужны фактические 24 часа по времени источника и wall
clock, не менее 20 плановых заявок, пять подтверждённых переходов процесса и
отсутствие разрыва source timestamps выше `--max-source-gap-seconds`.
Требования в 100 заявок и 30 закрытых циклов относятся к зачётному paper
периоду, а не к короткому readiness gate. Smoke-run проверяет код и
восстановление, но не открывает readiness gate.

`strategy-shadow-stream-process` запускает stream в шести последовательных
локальных процессах по умолчанию. Общие checkpoint, policy state, paper ledger,
raw market log и strategy-input log сохраняются в одном каталоге. В результате
есть PID, коды завершения, хэши состояния и пять переходов между процессами.
Опция `--fixture` предназначена только для тестов процесса и всегда помечает
результат как fixture-mode; такой запуск не может пройти readiness gate.

The market recorder uses only public Bitget ticker, order-book, current
funding-rate, and latest-settlement requests. Each new snapshot keeps the
current funding rate, funding interval, next settlement time, latest observed
settlement, source responses, and response hashes. A
one-shot snapshot is safe to run without an API key:

```shell
passivbot tool bitget-market-recorder \
  --symbol BTCUSDT --category USDT-FUTURES \
  --duration-seconds 0 --max-samples 1 \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_market_snapshots.jsonl
passivbot tool bitget-market-recorder --verify \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_market_snapshots.jsonl
```

Для ежедневной проверки непрерывности накопленного журнала задайте допустимый
перерыв между снимками и потребуйте непрерывность расчётов funding. Команда
только проверяет файл и не обращается к счёту:

```shell
passivbot tool bitget-market-recorder --verify \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_market_snapshots.jsonl \
  --max-gap-seconds 86400 \
  --use-source-timestamp \
  --require-funding-continuity \
  --integrity-output analysis_reports/crypto_bot_roadmap/inputs/bitget_market_snapshots.integrity.json
```

Отчёт содержит хэш всего журнала, максимальный перерыв, наблюдённые даты
settlement funding и число пропущенных расчётов между первым и последним
наблюдением. Пропущенные строки не создаются автоматически; при разрыве
проверка завершается со статусом `failed`.

The recorder has no account, order, or transfer path. Historical promotion
still requires a complete funding archive and replay events; a public market
snapshot alone never opens a trading gate.

When the crypto profile is enabled, a real `live` process also requires the
separate `--authorize-crypto-live` command-line flag. Without it, the process
stops before exchange configuration or order writes. The flag is not needed
for the fake/paper exchange and is never implied by the presence of API keys.

### Bitget paper mode

Bitget paper mode is selected by an explicit local user entry with `paper: true`.
The entry may contain `paper_symbols` and `paper_initial_balance`; it does not
need API credentials. The adapter creates no authenticated CCXT client:

```json
{
  "bitget_btc_paper": {
    "exchange": "bitget",
    "quote": "USDT",
    "paper": true,
    "paper_symbols": ["BTC"],
    "paper_initial_balance": 1000.0
  }
}
```

The bot reads Bitget public market data and keeps balances, positions, orders,
fills and fees in a local ledger. With an explicit state path, the current
prototype also persists and restores that basic state atomically; this is not
yet a write-ahead event journal. Orders are never sent to Bitget. A paper run
is still not historical evidence: its event journal must cover the full
calendar and event-count gates before it can be checked by
`crypto-runtime-evidence`. Funding, queue and partial-fill modelling are not
complete, and the paper liquidation price is an explicitly named simulation
assumption rather than Bitget's live liquidation formula.

Do not put a real API key in a paper entry. For a real account, use a separate
entry and the explicit `--authorize-crypto-live` barrier only after all earlier
roadmap gates have passed.

To record exactly how far the two public Bitget funding-history endpoints reach
for a requested window, run the horizon probe. It saves response hashes and
the oldest observed settlement; it does not fill the missing part:

```shell
passivbot tool bitget-funding-horizon \
  --symbol BTCUSDT \
  --start 2024-07-01T00:00:00+00:00 \
  --end 2026-08-17T00:00:00+00:00 \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_horizon_2026-08-17.json
```

An external funding archive can be checked without changing the saved market
inputs. The validator accepts JSON, JSONL, CSV, or TSV and records the source
hash. It returns `blocked` when any expected settlement is missing, duplicated,
invalid, outside the requested window, or only present in an observed subrange:

```shell
passivbot tool bitget-funding-archive \
  --archive /path/to/bitget_btc_funding.csv \
  --symbol BTCUSDT \
  --start 2024-07-01T00:00:00+00:00 \
  --end 2026-08-17T00:00:00+00:00 \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_archive_validation.json \
  --normalized-output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_archive_normalized.json
```

The command never interpolates rows or substitutes another exchange. A
successful validation report is necessary but is not sufficient for a
historical candidate: the Bitget account profile, OHLCV, mark/index prices,
execution model, and all later risk gates must also pass.
Rows explicitly labelled with a different symbol are excluded and block the
archive, so a BTC validation cannot silently consume ETH funding rows.

For a large minute archive, `bitget-ohlcv-repair` can use at most eight
unauthenticated public workers. Pages are sorted before their hash is written;
this keeps the result reproducible while avoiding a new TLS connection for
every page:

```shell
passivbot tool bitget-ohlcv-repair \
  --symbol 'ETH/USDT:USDT' --api-symbol ETHUSDT \
  --start 2024-07-01 --end 2026-08-17T00:00:00Z \
  --max-pages 12000 --parallel-workers 4 --delay-seconds 0 \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_eth_ohlcv_repair.json
```

The repairer writes only rows confirmed by Bitget market candles. It never
creates a row for a missing minute, and it reports `blocked` if any gap stays
unresolved.

## Fill Events Tooling

`passivbot tool fill-events-dash` launches a Dash UI for inspecting cached fill-event history,
PnL, symbol-level details, cache health, and CSV export.

```shell
passivbot tool fill-events-dash --users bybit_01
```

`passivbot tool fill-events-doctor` audits cached fill-event anomalies and optionally repairs them.

```shell
passivbot tool fill-events-doctor --exchange bybit --user bybit_01
passivbot tool fill-events-doctor --exchange bybit --user bybit_01 --repair
```

## Monitor Tooling

Monitor commands are documented in detail in [monitor.md](monitor.md). The CLI surface is:

- `passivbot tool monitor-relay`
- `passivbot tool monitor-web`
- `passivbot tool monitor-tui`
- `passivbot tool monitor-dev`

## Exchange Helpers

`passivbot tool fetch-balance` is a lightweight credential smoke test that loads one user from
`api-keys.json`, instantiates the matching ccxt exchange, and prints the raw balance payload.

```shell
passivbot tool fetch-balance --user bybit_01
```

Ticker probes inspect CCXT ticker support and latency without placing orders:

- `passivbot tool ticker-probe` checks ticker capability behavior for one exchange/user context.
- `passivbot tool ticker-endpoint-probe` compares CCXT ticker endpoint latency across configured users.

## Hyperliquid live probes

These probes were added to investigate live Hyperliquid balance/state quirks, especially HIP-3
stock perps. They are intended to be reused for future live diagnostics instead of creating new
one-off scripts.

- `passivbot tool hyperliquid-balance-probe` is read-only. It fetches one wallet balance and prints
  a normalized summary.
- `passivbot tool hyperliquid-abstraction-probe` is read-only. It inspects Hyperliquid account
  abstraction mode and related metadata for one configured user.
- `passivbot tool hyperliquid-order-margin-probe` is mutating. It places one tiny post-only order,
  snapshots balance changes, then cancels it.
- `passivbot tool hyperliquid-position-probe` is mutating. It can open/flatten a tiny position and
  optionally place resting entry or reduce-only close orders to inspect live state transitions.

The mutating probes require `--yes` and are intended for test wallets or deliberately tiny live
positions only.

```shell
passivbot tool hyperliquid-balance-probe --user hyperliquid_01
passivbot tool hyperliquid-order-margin-probe --user hyperliquid_01 --symbol BTC/USDC:USDC --yes
passivbot tool hyperliquid-position-probe --user hyperliquid_01 --symbol XYZ-SP500/USDC:USDC --yes
passivbot tool hyperliquid-position-probe --user hyperliquid_01 --symbol XYZ-SP500/USDC:USDC --flatten-only --yes
```

Recommended usage:

- Start with `hyperliquid-balance-probe` to confirm the wallet and baseline balance fields.
- Use `hyperliquid-order-margin-probe` to inspect how a single resting order changes
  `accountValue`, `withdrawable`, and related fields.
- Use `hyperliquid-position-probe` only when you need to inspect live position-margin behavior,
  startup-state handling, or combined position-plus-order effects.

## Repro and diagnostics helpers

`src/repro_harness.py` replays a stored config or Pareto JSON through both the optimizer-evaluation path and the backtest path in one process, then compares the resulting metrics and Rust binary provenance.

```shell
PYTHONPATH=src python3 src/repro_harness.py optimize_results/.../pareto/<hash>.json --json
```

`src/tools/capture_optimize_memory.py` samples process-tree RSS, `/dev/shm`, and host memory state while an optimizer run is active.

```shell
PYTHONPATH=src python3 src/tools/capture_optimize_memory.py --wait --output tmp/optimize_memory.json
```

`src/analysis_visibility.py` is a reusable helper module for resolving `backtest.visible_metrics` against optimize scoring/limits so analysis views can consistently show only the requested metric surface.

## VPS sync helpers

`sync_tar.py` and `vpssync.sh` provide a simple tar-over-ssh/scp workflow for moving configs, logs, backtests, or optimize results between this repo and a VPS tree.

```shell
python3 sync_tar.py push optimize_results/2026-... vps3 /root/passivbot/optimize_results --remote-extract
sh vpssync.sh pull vps3 "logs/20260318*"
```

The archive helper also supports symmetric `pull` and local `extract` modes, including wildcard remote matches.

## Market-cap based approved coins

`src/tools/generate_mcap_list.py` emits a JSON list of coins filtered by market cap and optionally by exchange availability.

```shell
passivbot tool generate-mcap-list -n 80 -m 200 -e binance,bybit -o configs/approved_coins_top80.json
```
### Crypto historical protocol

`passivbot tool crypto-historical-protocol` writes the dated BTC/ETH
walk-forward procedure, generated safe research configs, optimizer commands,
validation templates, neighbour checks, holdout, and synchronized portfolio
check. It refuses to start while the read-only historical preflight is blocked.

The optional `--execute` flag starts only local optimizer processes after the
preflight passes. It never sends an exchange request and never promotes a
candidate to trading.

`passivbot tool crypto-historical-select --input CANDIDATES.json --output
SELECTION.json` verifies the four validation windows, five seeds, stress
metrics, and 32 one-parameter neighbours for each candidate. The holdout is
recorded as reserved and is not used to choose a candidate.
