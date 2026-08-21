# Ревью реализации плана криптовалютного бота

Дата ревью: **18 августа 2026 года**, Europe/Amsterdam. Основание плана: канал зафиксировал дату плана 17 августа 2026 года.

Канонический план: [crypto_grid_bot_development_plan_2026-08-17.md](crypto_grid_bot_development_plan_2026-08-17.md).

## Вывод

Полное промежуточное ревью 1.63 на срезе `21:37Z` подтверждает: цель и обязательная последовательность этапов сохранены, но проект ещё не имеет торгового кандидата. После v24 свежи baseline/market_profile/risk_engine/shadow_readiness; data по-прежнему отклонён из-за неполного funding. `risk_engine v30` проходит собственный machine verdict, однако его сильная метрика production parity остаётся в карантине.

Этот файл сохраняет прежние наблюдения как исторический слой. Актуальное решение задаёт [редакция 1.71 канонического плана](crypto_grid_bot_development_plan_2026-08-17.md): live NAS v7 (`passivbot-shadow-v7`) пишет public snapshots; exam `passivbot-shadow` не стартовать; v6/v24/v25 не перезаписывать; `shadow-readiness` для v7 не биндить до финала. Последний bound срез — v85/v49/v31/v25 на `d46be477…`. ETH sidecar завершился с integrity `failed`. Реальные операции, приватные настройки Bitget, покупка данных и работа с секретами не разрешены.

Срез 1.63 **не** перезапускал полный Python/Rust baseline. Канон тестового evidence остаётся v78. Историческая локальная перепроверка 1.61 дала 275/275 критических Python-тестов, 145/145 Rust-тестов через `passivbot-rust/cargotest.sh`, успешные Ruff и `git diff --check`. Прямой документированный `cargo test` воспроизводимо падает на линковке PyO3; команда уже исправлена в `docs/ai/commands.md` и `docs/ai/build_pitfalls.md`.

Два главных внешних факта не изменились. Полного BTCUSDT funding archive за окно 2024-07-01…2026-08-17 нет: официальный ряд покрывает 266 из 2 332 точек точного окна. Paper adapter теперь требует named mark/index и bound maintenance tiers; ticker `last` не подменяет mark. Queue/depth в `create_order` не моделируются. Public input feed может исключить funding без более раннего mark. Поэтому контур не является exchange-parity моделью для 90-дневного зачёта.

## Исторический полный срез после 1 дня 6 часов 23 минут — 18 августа 2026 года, финальная проверка 16:57 UTC

| Область | Наблюдаемый факт | Решение |
|---|---|---|
| Цель | historical, 90d paper, 30d technical live и 90d controlled live не пройдены | цель не завершена; никакого кандидата и торгового допуска нет |
| Baseline | v81 passed/fresh; plan SHA-256 `5c538c95…`; fingerprint `bbd19e7d…`; полное test evidence находится в v78 | историческая редакция 1.60 машинно привязана к неизменившемуся implementation; дерево можно считать замороженным только до конца текущего v5 |
| Public market/risk | market profile v47 и risk grid v49 passed/fresh; risk engine v29 формально passed/fresh | v29 проверяет research Python policy против Rust function, а не полный production live-vs-Backtest путь; до нового связанного run его сильная метрика не принимается как admission evidence |
| Data/historical | funding 266/2 332 в точном окне; data v47 failed; historical v34 blocked; optimizer не запускался | нужен полный лицензированный applied-settlement export; synthetic fill запрещён |
| Shadow v5 | PID 81180 жив; на финальном срезе 73 snapshots и 594 события; `seq=1…594` непрерывен, IDs уникальны, максимальный source gap 62,875 секунды при лимите 300 секунд, trading operations 0; финальных файлов нет | дождаться v5 и единственного v22; не менять implementation/config |
| Paper | WAL, explicit funding и bounded partial fills есть; queue/depth, реальные mark/index и tier-based liquidation не доказаны | не начинать 90-дневный зачёт; следующий инженерный пакет готовить только после v22 |
| ETH sidecar | имя содержит `24h`, но duration равен 14 520 секундам | считать примерно четырёхчасовой exploratory записью, не portfolio evidence |
| Rust-команда | прямой `cargo test` не линкует PyO3; wrapper даёт 145 passed | docs уже исправлены; использовать абсолютный путь к `.venv/bin/python` |
| Биржа | никаких заявок, переводов, смены режима или приватного профилирования не выполнено | граница разрешений соблюдена |

Главный способ ускорения — перестать пересоздавать доказательную цепочку после каждой мелкой правки. Пока v5 работает, агенты выполняют только read-only аудит, документацию или готовят изменения в изолированных копиях. После v22 один интегратор последовательно принимает два независимых P0-пакета — production-path risk evidence и общий data passport — затем объединяет paper market-surface пакет, запускает узкие тесты и один новый baseline. Funding research не повторяет уже исчерпанные публичные источники без нового проверяемого следа.

## Новые выводы полного аудита кода

### 1. `risk_engine v29` доказывает меньше, чем заявляет метрика

`src/research/risk_engine_surface.py` отправляет 14 сценариев в `research.risk_adapters.python_live_decision()`. Этот адаптер вызывает общий исследовательский `evaluate_order()`, а не production-метод `Passivbot._apply_crypto_portfolio_risk_limits`. Rust-сторона вызывает скомпилированную функцию, поэтому матрица полезна как проверка формулы и причин deny, но флаг `risk_limits_enforced_in_live_and_tester=true` для неё слишком сильный.

Отдельный `actual_risk_parity_probe_2026-08-17-v3` действительно запускает настоящий Python live-фильтр и настоящий Rust `Backtest`; шесть решений совпали. Однако его report hash и входы не входят в manifest `risk-engine-2026-08-18-v29`. Следовательно, два частичных доказательства пока нельзя складывать в один пройденный production gate. Следующий пакет обязан либо переименовать surface-метрику в исследовательскую, либо связать actual probe, snapshot, config, market profile, code fingerprint и полный scenario verdict в одном неизменяемом run-пакете.

### 2. Historical и paper не имеют общего доказательства полноты данных

Historical preflight знает точное окно и ожидаемое число funding settlements. `PublicExecutionInputFeed` хранит только фактически принятые settlement’ы. Если до settlement нет более раннего mark, строка исключается; `as_status()` сообщает оставшееся количество, но не requested start/end, cadence, expected rows и missing timestamps. Reconciliation поэтому способна подтвердить внутреннюю арифметику имеющихся событий, не доказывая полноту исходного окна.

Для qualifying historical/paper нужен один data passport: symbol, UTC start/end, правило settlement cadence, expected/actual rows, missing/duplicate/foreign/future rows, mark-before-settlement coverage и SHA-256 всех источников. Любой пропуск делает `qualifying=false`. Текущий мягкий режим можно сохранить только как явно exploratory.

### 3. Два второстепенных замечания

- `compare_decisions()` сначала нормализует обе стороны в один типизированный `RiskDecision`; предположение о возможном `KeyError` из-за отсутствующего ключа не подтвердилось.
- `_integer()` в Bitget market surface отклонял строку `"1.9"`, но для float `1.9` использовал Python `int()` и молча получал `1`. Исправлено в 1.68: parser отклоняет дробные timestamps.

## Актуализация после продолжения, 18 августа 2026 года

### Уточнение официального funding blocker’а, 13:03 UTC

- Официальный [Bitget data-download](https://www.bitget.com/data-download) перечисляет для futures только свечи, сделки и глубину стакана; funding archive в каталоге отсутствует.
- Официальная [инструкция по funding history](https://www.bitget.com/support/articles/12560603837210) подтверждает просмотр периода в интерфейсе, но не даёт воспроизводимого download/API-пути для полного ряда.
- Дополнительный публичный UTA-запрос с историческими `startTime/endTime` вернул текущую первую страницу; `cursor=4` и `cursor=100` вернули ноль строк. Это не расширяет уже зафиксированные `100, 100, 70, 0`.
- Verdict не изменился: 270/2 332 строк недостаточно, подстановка или скрытая реконструкция запрещены. Evidence сохранено в [`funding_archive_official_download_audit_2026-08-18.md`](../../analysis_reports/crypto_bot_roadmap/inputs/funding_archive_official_download_audit_2026-08-18.md).

### Машинное обновление статуса, 13:05 UTC

Штатная `research_gates --freshness-only` команда обновила `status.json` без ручной записи. Она сохранила baseline/market/risk как `passed/fresh`, data как `failed/fresh`, а historical перевела в `check_status=failed`, `outcome=blocked`, `stage_admission_status=blocked`, потому что prerequisite data не пройден.

- `market-profile-2026-08-18-v46`, `risk-engine-2026-08-18-v28` и `risk-grid-2026-08-18-v48` пересозданы на текущем implementation fingerprint и прошли свои machine checks.
- `data-public-snapshot-2026-08-18-v46` имеет 14/16 пройденных checks и `failed` только по funding: 270 из 2 332 ожидаемых строк, coverage `0.11578044596912522`.
- Metrics пересобраны штатным генератором; `execution_inputs_sha256` совпадает. Preflight v10 больше не сообщает ложный hash blocker и оставляет только неполный funding/data admission.
- Historical package v33 создан на этих входах: optimizer не запускался; его `check_status=failed` отражает отсутствие candidate events при upstream `data` failure, а не провал нового сета.
- Luna Max read-only аудит подтверждает отсутствие полного доступного funding source: два официальных Bitget endpoint совпали, Tardis покрывает только часть окна, Amberdata/Laevitas требуют отдельный доступ. Никаких ключей, покупок или внешних записей не выполнялось.
- Shadow v4 продолжает выполняться. На `2026-08-18T12:45Z` в нём было 12 market events и 98 typed events; финальный результат ещё не создан, поэтому readiness не повышается.

### Проверка живого shadow v4, 13:18 UTC

- Supervisor PID `6893` и child PID `7241` находятся в одной отдельной process group; дочерний сборщик не потерян и не дублируется.
- В checkpoint накоплено 44 market events и 354 typed events. Есть `checkpoint.json`, `stream_state.json`, raw/strategy logs, policy state и paper ledger; `result.json` и `process_supervisor.json` закономерно отсутствуют до завершения 24-часового прогона.
- Штатный `research_gates --freshness-only` обновил `status.json` в `2026-08-18T13:19:21Z`. Вердикты не изменились: baseline/market/risk — `passed/fresh`, data — `failed/fresh` только по funding, historical — `blocked/fresh`, shadow readiness — не принят до итогового результата.
- До завершения v4 запрещены второй shadow, изменения implementation-кода и применяемой конфигурации. Разрешены только read-only проверки и документирование.

### Read-only проверка продолжающегося shadow v4, 13:43 UTC

- Process group остаётся живой: supervisor PID `6893`, child PID `7241`; `fixture_mode=false`, внешний источник public-only.
- В stream state и журнале согласованы 68 market snapshots и 546 typed events. Source age составил 39 секунд; порядок `source_timestamp_ms` не нарушен, duplicate `ingest_seq` не найден.
- Итоговые `result.json` и `process_supervisor.json` ещё не созданы, поэтому machine `shadow_readiness` gate не запускался и статус не повышался.
- Следующая проверка должна дождаться штатного завершения v4; при отсутствии результата после остановки нужно сначала разобрать exit evidence, а не запускать второй процесс.

### Дополнительный funding mirror audit, 13:50 UTC

- Публичный DSpace item Petrishko действительно содержит `appendix-1.zip` (7 312 242 байта, SHA-256 `0c2057a2cb8a1b43d8f925c54b72481e6d6f72b18c98e827428b4c95e772cb7c`), но архив состоит из исходников и документации; raw `funding.parquet` в нём отсутствует.
- README указывает frozen mirror и ожидаемый SHA `9f2d7d1614fcbcd90cddf4c57240506fcc713eb410f11baafda425c7f8b62bc1`; read-only запрос к mirror вернул HTTP 530. Источник не принят, data gate не изменён.
- Evidence и безопасный путь повторной проверки сохранены в [`funding_archive_thesis_mirror_audit_2026-08-18.md`](../../analysis_reports/crypto_bot_roadmap/inputs/funding_archive_thesis_mirror_audit_2026-08-18.md).

### Проверка альтернативных mirror hostname, 13:54 UTC

- `d3lta.dev` перенаправляет на `www.d3lta.dev`, а `www` отдаёт HTML `index.html`; это не data archive.
- HTTP и HTTPS варианты `api.d3lta.dev` возвращают HTTP 530. Публичного repository или другого mirror в HTML не найдено.
- Data gate оставлен без изменений; покупать источник, использовать другой exchange или реконструировать funding не разрешено.

## Checkpoint после 19 часов

| Приоритет | Находка | Решение |
|---|---|---|
| P0 | `status.json` проверяет устаревание только у `passed`; старые `failed`/`blocked` могут выглядеть свежими | schema v3 хранит результат и свежесть отдельно для каждого run-пакета |
| P0 | Результат локальной проверки смешан с допуском зависимой стадии | разделить `check_status` и `stage_admission_status` |
| P0 | Shadow-runner способен сохранить ложные safety checks и всё равно завершиться как `no_trade_observed` с кодом 0 | любая нарушенная safety check даёт `failed` и ненулевой код |
| P0 | Один `fill_id` с разным содержимым может быть молча принят повторно | конфликт идентификатора должен завершать запуск ошибкой |
| P0 | Автоматические partial fills падают на последнем остатке, если он меньше заданной части | исполнять `min(part, remaining)` и покрыть регрессией |
| P0 | WAL защищает fill, но не все order/cancel/funding переходы | единый crash-safe typed journal для всех переходов |
| P1 | Public input можно изменить посреди запуска, а хэш фиксируется только в конце | закреплять хэш до первого события и проверять при каждом чтении/replay |
| P1 | Reconciliation принимает произвольный корректно выглядящий SHA-256 и выводит нулевое slippage по наличию поля | сверять источник с артефактом и пересчитывать отклонение из цен |
| P1 | Узкий risk probe 6/6 не сравнивал все вычисления | добавлена отдельная 14-сценарная read-only surface-проверка projected position, liquidation buffer, daily loss, HSL, exposure, margin, market-order boundary, Long/Short и missing tiers |
| P1 | Historical preflight смешивает public/private profile и неверный каталог кэша | public-only profile, `caches/ohlcvs`, запрет незаявленного remote fetch |

Текущий `bitget_paper_shadow.py` — только public transport observer. Он не запускает стратегию и не формирует плановые заявки, поэтому его нельзя растягивать до семи дней в надежде получить проверку торговли.

## Подтверждённое состояние

| Область | Факт | Вывод |
|---|---|---|
| База | `baseline-2026-08-18-v77`: 2 569 Python-тестов (2 451 passed, 118 skipped), 20 fake-live-тестов, 145 Rust-тестов; три штатных полных Python-прогона дали один raw output hash `c50dac61b48cc5bd01c2967010190b5c49d785959b5d33aecb0fffaf43a5b27e` | принят машинно, `passed` и `fresh`; implementation fingerprint v2 `3deac159…` |
| Публичный smoke | Модульный запуск получил один BTC market observation и два события без fills, private calls, orders или transfers; прямой запуск файла падает на конфликте импорта | публичный транспорт работает только через проверенный module entrypoint; стратегия не проверена |
| Partial fills | 12 новых fault-injection регрессий плюс существующие paper/shadow тесты проходят; конфликт `fill_id`, последний остаток и WAL-переходы теперь проверяются явно | критические регрессии исправлены; непрерывный runtime ещё не доказан |
| BTC OHLCV | 1 118 881 из 1 118 881 минутных строк | требуемое окно покрыто |
| Mark/index | 18 649 из 18 649 часовых строк | требуемое окно покрыто |
| Funding | Замороженный файл содержит 270 строк; аудит `03:14 UTC` дал 267 строк в точном окне, последний rerun `09:54 UTC` — 266 из 2 332 ожидаемых | snapshots не смешиваются; исторический data gate не пройден |
| Источники funding | повторный аудит также не нашёл полного проверяемого архива; Amberdata/Laevitas условны, Tardis частичен | нельзя выбирать кандидата на неполном funding |
| Приватный Bitget | UTA read-only вернул `40084`, Classic Account | счёт не готов к требуемому UTA-контракту |
| Старые кандидаты | шесть из шести убыточны на следующем периоде | кандидатов для paper/live нет |
| Внешние действия | реальные заявки, переводы и смена режима не выполнялись | граница разрешений сохранена |
| Paper runtime evidence | целевые paper-тесты, stream-тесты и process-supervisor регрессии проходят; strategy driver подключён только к local paper ledger | непрерывный 90-дневный runtime не доказан; public shadow v3 остановлен до принятия |
| Risk evidence | Python live exporter подключён после настоящего live-фильтра; Rust `Backtest` пишет trace всех исходных заявок с `allow/deny` и создаёт `risk-snapshot-v1` до очистки pending orders; новая surface-матрица прогоняет 14 named cases через оба адаптера | локальная risk surface проверена 14/14; private account ledger и historical funding остаются вне допуска |
| Actual parity probe | probe v3 использует захваченный public Bitget surface; snapshot-хэш `cb50c9…142f`; 6 из 6 решений совпали | доказано совпадение узкой выборки решений, но не полного расчёта риска |
| Live surfaces | production Bitget adapter теперь загружает и проверяет public mark/index, fees, quantity-step, funding interval и margin tiers; private isolated ledger ещё не подтверждён | `risk_engine_surface_validated=true` для локального engine; `risk_model_surface_validated=false` для старого общего risk-contract из-за account evidence |

## Критические расхождения

### 1. Риск-отчёт не воспроизводит конфигурацию плана

Запускатель `src/tools/crypto_risk_grid.py` выполняет 15 проверок, покрывающих 14 заявленных сценариев, на отдельной roadmap-конфигурации и public market profile. После добавления двух engine-тестов все 14 сценариев получили исполняемое evidence; локальный запуск v8 имеет статус `passed`. Machine stage всё ещё ждёт data и risk-contract, поэтому это не разрешение исторического отбора или торговли.

Старый пакет `risk-grid-2026-08-17-v2/config.json` одновременно записывал:

- `live.portfolio_risk.enabled=false`;
- `entry_grid_max_levels=500` для Long и Short;
- `live.leverage=10`;
- `live.hedge_mode=false`;
- `live.margin_mode_preference=cross`;
- выключенная модель исполнения.

Следовательно, старые зелёные тесты не соответствовали проверяемому крипто-профилю. Новый запуск `risk_grid_scenarios_2026-08-17-v8.json` использует дорожную конфигурацию, получает 15 нулевых кодов завершения и даёт 14 из 14 покрытых сценариев. Его результат — локальная инженерная проверка; machine stage остаётся `blocked`, пока не пройдены обязательные data и risk-contract.

### 2. Live и тестер используют разные реализации портфельного решения

Live-фильтр находится в Python: `src/passivbot.py`, метод `_apply_crypto_portfolio_risk_limits`. Оценка тестера находится в Rust: `passivbot-rust/src/backtest.rs`.

Тесты подтверждают отдельные части, а actual probe v3 теперь запускает оба настоящих контура из одного воспроизводимого состояния, используя захваченный public Bitget surface. Snapshot, идентификаторы и все шесть решений совпали. Private UTA account state и фактическая isolated liquidation ledger всё ещё не доказаны.

Исправление использует одну формулу projected liquidation distance в Rust и Python и переносит принятую позицию на следующий grid-уровень последовательно. Нормализатор и live refresh теперь получают mark price, fees, quantity step и tiers через публичные Bitget GET. Probe `passed` остаётся read-only доказательством parity, а не разрешением торговли: account state, реальные комиссии/funding и isolated liquidation ledger требуют отдельной проверки. Ослабление одной стороны или скрытая подмена real surface недопустимы. Fixture-adapter остаётся только регрессионным тестом.

### 3. Paper-прототип не пригоден для 90-дневного зачёта

`src/exchanges/paper.py` теперь имеет opt-in сохранение и восстановление базового состояния и пишет типизированное `order.filled` событие в write-ahead WAL до изменения ledger. После изменения выполняется атомарный snapshot; replay идемпотентен. В новом пакете поля из paper fill (`typed_fee`, `typed_slippage`, order/fill ID, контекст стратегии и количество до/после) не теряются при преобразовании во внутренний `FillEvent` и записи через monitor writer. Paper snapshot hook вызывает read-only локальную reconciliation только у paper-бота; она сравнивает баланс, typed fee, explicit funding и известные позиции и добавляет source/hash. Новый `src/research/paper_public_inputs.py` принимает сохранённые публичные Bitget-файлы, проверяет read-only boundary и SHA-256 исходника, запрещает будущий mark, а `PaperBitgetCCXTClient` сохраняет обработанные settlement ID и применяет их без приватного API.

Эти свойства подтверждены только для ограниченных путей. Дополнительные fault-injection проверки показали: order/funding/cancel меняют память до гарантированного журнала; одинаковый `fill_id` с иным payload скрывает конфликт; автоматический остаток partial fill может превысить remaining; входной файл может сменить хэш в середине запуска; reconciliation не доказывает источник и фактическое slippage.

Также пока отсутствуют:

- фактические mark/index вместо подстановки last;
- подтверждённые глубина стакана и позиция в очереди;
- полное историческое funding coverage и exchange-authoritative reconciliation;
- живой recorder → strategy paper pipeline и калибровка queue/partial-fill по depth/trade events;
- пять контролируемых сценариев остановки и измеренное восстановление за 60 секунд.

Прототип можно использовать только для коротких диагностических smoke после исправления fail-open verdict. Его нельзя использовать для strategy shadow: `src/tools/bitget_paper_shadow.py` не создаёт `Passivbot`, не запускает торговое решение и не создаёт плановые заявки. Статус `no_trade_observed` доказывает лишь отсутствие событий, а не безопасность или торговую корректность.

### 4. Длительность paper/live пока можно завысить формально корректным журналом

`src/research/runtime_evidence.py` теперь использует evidence v2: требует наблюдение и heartbeat каждого UTC-дня и ограничивает максимальный разрыв 26 часами. Тесты показывают, что 90 дней двумя разнесёнными группами событий больше не проходят непрерывность.

Из журнала всё ещё не доказаны все финансовые показатели. Неподтверждённые uptime, recovery, расходы, funding, PnL, просадка, slippage и сверка получают fail-closed значения; `metrics.json` больше не может вручную повысить их до допуска. Это означает, что новый paper/live период будет блокироваться до появления typed events для этих полей.

Нужно добавить регулярные heartbeat, максимальный разрыв, суточное покрытие, уникальные торговые циклы и расчёт финансовых показателей из событий и снимков. Ручные метрики могут только дополнять объяснение, но не повышать этап.

### 5. Ошибка настройки Bitget может остаться только записью в логе

До этого ревью в `src/exchanges/bitget.py` ошибки установки плеча, margin mode и hedge mode перехватывались и журналировались, после чего запуск мог продолжиться. Для crypto-профиля это исправлено: такие ошибки теперь останавливают настройку с явным fail-closed исключением.

Для обычных старых профилей прежнее поведение сохранено. Для дорожного isolated/hedge-профиля после успешной записи всё равно требуется повторное чтение фактического состояния.

### 6. Доказательный журнал необязателен для работающего процесса

Монитор можно выключить конфигурацией. Ошибка его запуска или записи только журналируется, а торговый процесс продолжает работу. Для зачётного paper/live режима это недопустимо: без доказательного журнала этап нельзя проверить.

Рекомендация: для дорожного режима монитор обязателен. Ошибка записи переводит процесс в режим «только закрытие», создаёт явное событие при первой возможности и делает текущий период незачётным.

### 7. Зависимости этапов чрезмерно последовательны

Public `market_profile` уже отделён от private `account_profile`, но `risk_contract` всё ещё требует private isolated UTA surface, а `risk_grid` смешивает успешность своих 14 проверок с допуском зависимой стадии. Это продолжает блокировать публичный historical path неподходящим приватным ключом.

Нужно разделить:

- `risk_engine` и `risk_scenarios` — для тестера, historical и paper;
- `account_risk_surface` — только перед техническим реалом;
- `shadow_readiness` — отдельно от 90-дневного `paper_runtime`.

Исторический отбор должен зависеть от публичных данных и единого риск-контракта, а не от наличия подходящего API-ключа.

### 8. Статус знает только о части случаев устаревания

Версия v2 атомарно записывает файл и умеет помечать старый `passed` как `stale`. Однако `failed` и `blocked` не проходят ту же проверку, поэтому старые отрицательные результаты могут выглядеть текущими после изменения дерева. Кроме того, один статус одновременно описывает локальную проверку и допуск всей зависимой стадии.

Нужно:

- хранить `outcome` и `freshness` раздельно для любого результата;
- хранить `check_status` и `stage_admission_status` раздельно;
- пересобирать статус из неизменяемых пакетов и их хэшей;
- сохранить одного интегратора и файловую блокировку;
- запретить перезапись зачётного `run_id`.

### 9. Явное разрешение реальной торговли привязано к включённому риск-профилю

Текущий барьер `enforce_crypto_live_authorization` возвращается без проверки флага, если `live.portfolio_risk.enabled=false`. Для обычного Passivbot это сохраняет прежнее поведение, но дорожный режим нельзя защищать только договорённостью о конфигурации.

Нужно ввести отдельный явный режим дорожного запуска. В нём риск-профиль, доказательный журнал и флаг разрешения обязательны независимо друг от друга. Конфигурация с выключенным риск-профилем должна завершаться до создания приватной сессии и до любой настройки счёта.

## Что внедрено после этого ревью

Список ниже фиксирует наличие кода. Он не означает завершение стадии: checkpoint выше отменяет прежние более сильные выводы о готовности paper, полной parity и корректной свежести статуса.

- `research-gates` теперь создаёт `checksums.json` для всего run-пакета: manifest, metrics, verdict, report, events, config и market profile.
- Повторный `run_id` отклоняется. Перезапись возможна только с явными `--safe-overwrite --scratch` и только для целого ранее проверенного scratch-пакета.
- `runtime_evidence` больше не принимает разреженный журнал за длительную работу: нужны покрытие календарных дней, heartbeat, ограничение максимального перерыва, уникальные заявки и циклы, а также события для uptime, recovery, PnL, просадки и reconciliation.
- `risk_grid` получил отдельный read-only roadmap-preflight. Старые учебные конфиги остаются `test_only` и не могут стать кандидатом; проверяются включённый риск, isolated/hedge, плечо до 3×, максимум 8 уровней и запрет обычных market orders.
- Автоматическая проверка свежести уже написана: при следующем запуске старый `passed` становится `stale`, если run не имеет контрольных сумм или текущий код не совпадает с паспортом.
- Статус дорожной карты переведён на схему `crypto-grid-roadmap-status-v3`: старые `profile`/`paper` мигрируются в `account_profile`/`paper_runtime`, добавлены `risk_engine`, `account_risk_surface` и `shadow_readiness`, а `check_status`, `stage_admission_status`, `outcome` и `freshness` хранятся раздельно. Любой run-backed `failed` или `blocked` также становится `stale` при изменении входа.
- Paper получил persistence, fill WAL, funding settlement, monitor events, public input bridge и partial-fill primitives. В текущем пакете исправлены конфликтующие IDs, остаток partial fill, crash safety, input immutability и reconciliation; 12 новых fault-injection тестов проходят. Код всё ещё считается прототипом до strategy shadow и длительного runtime evidence.
- Добавлены `risk-snapshot-v1`, `risk-decision-v1` и read-only parity-harness. При отсутствии любого фактического engine adapter harness блокирует сравнение и возвращает `deny`; fixture-сравнения не считаются доказательством parity.
- Python live exporter теперь получает состояние после фактического `_apply_crypto_portfolio_risk_limits`, не применяет вторую policy и умеет читать production HSL runtime и дневной убыток. Без margin tiers он остаётся `blocked`.
- Настоящий Rust `Backtest` сохраняет `risk_gate_trace` до раскладки принятых заявок: каждая исходная заявка получает `allow` или `deny`, включая отклонённые заявки. До очистки очереди строится `risk-snapshot-v1` с pending orders, balance, positions, mark prices, margin tiers, leverage, daily loss и HSL; ошибка snapshot блокирует entries и оставляет closes.
- Python и Rust используют общий формат `risk-order-v1`. Occurrence для разных семантических заявок теперь считается одинаково; действительно дублирующие заявки блокируют полное сравнение до окончательной канонизации.
- `risk_trace.py` преобразует фактический Rust trace в `risk-decision-v1`, а harness режимом `--live-export` + `--rust-trace` проверяет внешний snapshot и сравнивает весь набор решений одного состояния; пустой, несовместимый или дублирующийся evidence даёт `blocked`/`deny`.
- Добавлен actual probe `analysis_reports/crypto_bot_roadmap/actual_risk_parity_probe_2026-08-17-v3/`. Он вызывает реальный Python live-фильтр и реальный Rust `Backtest`, не fixture-policy; текущий результат `passed` для 6 из 6 решений сохранён как read-only evidence, но public surface не закрывает `risk_model_surface_validated` без private UTA ledger.
- Добавлен строгий `src/research/bitget_market_surfaces.py`: четыре публичных Bitget ответа объединяются только при полном наборе mark/index, fees, quantity-step, funding interval и margin tiers; пропуск, разрыв tier или расхождение с CCXT contract surface блокирует продолжение.
- `BitgetBot.refresh_crypto_market_surfaces()` подключён к live и paper пути до риск-фильтра; mark используется отдельно от strategy last, а канонические tiers для snapshot совпадают с Rust. Actual probe v3 использует захваченный public profile и получил 6 из 6 совпадений.
- `runtime_evidence` переведён на schema v2 и typed-схему `crypto-runtime-typed-evidence-v1`: каждый UTC-день, heartbeat не реже чем раз в 26 часов, уникальные заявки/циклы и запрет ручных claims из `metrics.json`; фактический paper fill уже отдаёт typed fee/slippage, complete accounting fixture проходит typed validation, а отсутствие funding или неполный cost journal остаётся fail-closed.
- Добавлены общая Rust/Python функция расчёта liquidation distance, явный выбор maintenance-margin tier и последовательное projected position state для initial/grid заявок. При отсутствии обязательной поверхности live gate остаётся fail-closed.
- Rust-тестер получил fail-closed предел первого входа `max_initial_entry_sizing_balance_pct` в диапазоне `(0, 0.03]`; закрывающие действия этим пределом не блокируются. Live-путь теперь использует тот же профильный предел, а не скрытый default.
- `risk_grid` расширен до 15 проверок для 14 явно названных сценариев. Roadmap-запуск `risk_grid_scenarios_2026-08-17-v8.json` получил 14 passed, 0 blocked, 0 failed и 0 not_run. Это инженерный результат, не допуск этапа: machine stage зависит от data и risk-contract.
- Добавлен `src/research/risk_engine_surface.py`, CLI `src/tools/risk_engine_surface_probe.py` и 15 targeted-тестов. 14 именованных сценариев проходят через фактические Python live и Rust tester adapters и проверяют решения и причины для дневного stop, HSL, leverage, exposure, margin, двух liquidation-buffer границ, обычного market order, Long/Short, panic close и missing tiers. Результат: 14/14, `candidate_admission=false`, `trading_operations_count=0`.

После редакции 1.32 финальный baseline `v53` машинно фиксирует три штатных полных Python-прогона с одинаковым нормализованным выводом: 2 486 собранных тестов (2 368 passed, 118 skipped), hash `5f5eee8a02b6d24356698e44502db1fdb8bb23d06a79fd28eb3a4c509be216b7`; fake-live и полный Rust-набор также проходят: 20 и 145 тестов. Последовательная цепочка одного снимка: `market_profile v24` — passed, `account_profile v10` — failed, `risk_engine v5` — passed, `data v23` — failed, `risk_contract v32` — failed, `risk_grid v25` — blocked из-за data, `historical v9` — failed. Все восемь run-пакетов имеют прошедшую проверку целостности; это не прогресс к торговому допуску.

## Промежуточное ревью public strategy stream — 18 августа 2026 года

После длительного внедрения отдельно перепроверен новый stream-контур. Первоначальный черновик смешивал локальное время получения с временем рынка, сохранял policy signals/fills рядом с raw snapshot и вызывал strategy driver на каждом heartbeat. Это могло искусственно завысить coverage и число strategy steps.

Исправлено:

- стратегия получает только `source_timestamp_ms` из публичного ответа; локальное `observed_at_ms` остаётся отдельным полем для расписания и liveness;
- `source_event_id` строится из symbol/category, source timestamp и хэшей четырёх public responses;
- повтор одинакового source event записывается как heartbeat без вызова strategy driver;
- raw `market_snapshots.jsonl` отделён от `strategy_inputs.ndjson`, где находятся сгенерированные signals и локальные paper execution inputs;
- fill помечен как paper crossing с `queue_model=not_modeled` и taker-подобной комиссией, а не как доказанный maker fill;
- ошибка сбора публичных данных фиксируется и делает run `failed`; ошибка обработки останавливает run fail-closed;
- добавлен `strategy-shadow-stream-process`: шесть дочерних процессов, пять PID transitions, общие state files, state hashes и три независимых replay;
- fixture mode существует только для process tests и явно блокируется readiness gate.

Фактически выполнены два smoke-run без заявок: один public stream с одной записью и один public process smoke с шестью market events, пятью recovery и примерно 13 секундами wall-clock. Они подтверждают границу транспорта и восстановления, но не 24 часа, не 20 заявок и не готовность paper. Полный readiness запуск ещё не начинался.

Новые доказательства: `tests/test_strategy_shadow_stream.py` и `tests/test_strategy_shadow_stream_process.py`; команды целевого набора проходят. Официальные public Bitget поверхности recorder остаются только GET: [tickers](https://www.bitget.com/api-doc/uta/public/Tickers), [order book](https://www.bitget.com/api-doc/uta/public/OrderBook), [current funding](https://www.bitget.com/api-doc/uta/public/Get-Current-Funding-Rate) и [funding history](https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate).

Ограничения сохраняются: paper fill не доказывает очередь/частичное исполнение биржи; `StrategyShadowDriver` всё ещё принимает внутренние signals/execution, поэтому граница должна оставаться исключительно внутренним bridge; raw market log и process supervisor должны ежедневно хэшироваться. Ни один существующий сет не допущен.

## Обновление перед промежуточным check-in — 18 августа 2026 года

После этого чернового ревью были закрыты следующие актуальные ошибки:

- `strategy_shadow_stream` теперь вызывает `driver.close()` до формирования итогового результата, поэтому SHA-256 checkpoint, events и ledger относятся к окончательному состоянию файлов;
- `strategy_shadow_stream_process` переносит в итоговые метрики накопленные market events, heartbeats и strategy inputs, а не только последний сегмент;
- recovery driver и recovery между дочерними процессами получили отдельные поля и отдельные проверки;
- `research-gates` заново вычисляет SHA-256 каждого artifact, указанного в `result.json`, и блокирует shadow при расхождении;
- funding horizon probe сохраняет malformed и foreign-symbol rows до валидации; funding identity теперь проверяется относительно конкретного execution-input файла;
- новый `bitget-funding-promote` может создать только новый execution input после полной проверки внешнего архива; неполный архив создаёт только blocked validation report и ничего не перезаписывает;
- ISO timestamps без явной временной зоны отклоняются, а execution-input hash в data metrics сравнивается с текущим файлом.
- supervisor запускает child в отдельной process group и очищает её при SIGINT/SIGTERM; orphan child, найденный у v2, остановлен, а v3 остановлен до принятия и сохранён как partial evidence.

Целевые проверки после этих изменений прошли: shadow stream/process, research-gates, funding archive/promotion, historical preflight, public data metrics и fail-closed boundary. После добавления process-group cleanup отдельный набор shadow/process/gate тестов дал 49 passed; Ruff F-проверка и `git diff --check` также прошли. Полный повтор baseline v77 был начат преждевременно и остановлен до промежуточного check-in; на том снимке v76 оставался последним полностью зафиксированным техническим пакетом, а актуальное ревью ниже уже помечает его stale.

Запуск `analysis_reports/crypto_bot_roadmap/shadow_runs/public-stream-process-24h-2026-08-18-v3` остановлен до финального `result.json` после обнаружения orphan child в старом supervisor-контуре. Его partial artifacts сохранены, но не считаются результатом и не передаются в gate. На том check-in новый shadow-run намеренно не запускался; актуальный порядок ниже требует сначала P0 evidence-hygiene и новый baseline. Все public-запросы v3 были GET-only, без credentials, private calls, order writes и transfers.

## Исторический снимок до интеграции P0 — 18 августа 2026 года

Главный новый вывод: работа замедлилась не из-за одного сложного блока, а из-за повторного обесценивания собственных доказательств. На момент снимка было 37 изменённых отслеживаемых файлов, 6 784 добавленные строки и 10 588 неотслеживаемых файлов. Из них 7 147 лежали в `monitor/fake`, 3 193 — в generated roadmap tree. В roadmap накопилось 359 run-каталогов, включая 74 baseline и 19 shadow-readiness пакетов. Это намного больше, чем нужно для одного незамороженного пакета разработки.

Текущий fingerprint-контракт хэширует все untracked-файлы, кроме `analysis_reports/crypto_bot_roadmap/`. Поэтому тестовые файлы в `monitor/fake`, другие generated outputs и правка канонического плана меняют source fingerprint. Снимок до документальной правки имел hash `cbf4c42c9a55f499f508db3fcfd3a7fdd12ce552a13a1976ef7831bcbd9e0d75`, а manifest v76 — `70ce02…`. Сохранённый `status.json` обновлён в `2026-08-18T10:37:17Z` и всё ещё показывает baseline как fresh, но read-only пересчёт штатной функцией переводит его и все зависимые пакеты в `stale`. Файл статуса вручную не менялся.

Техническая база не разрушена: окружение указывает на текущий checkout, загруженное Rust-расширение соответствует текущему Rust source stamp, целевой shadow/process/gate-набор и Ruff F-проверка проходят. Но binary SHA расширения отличается от manifest v76, текущее дерево собирает 2 560 тестов против 2 559 в v76, а полного прогона текущей версии нет. Значит, свежей machine chain сейчас нет.

Shadow v2 содержит около 1,66 часа partial snapshots, v3 — около 26 минут; ни один не имеет принимаемого финального `result.json` и `process_supervisor.json`. Активного shadow, pytest или Rust-процесса на момент снимка нет. Тест cleanup проверяет прямого sleeping child, но пока не доказывает удаление grandchild и точный crash/replay без повторных order/fill IDs. Новый 24-часовой запуск до такой проверки создаёт ненужный риск ещё одного непринятого run.

Data gate независимо закрыт. Последний public funding rerun получил 270 валидных строк, 266 из них входят в точное окно, 2 066 ожидаемых точек отсутствуют. Ожидание 2 332 точек исходит из проверенной восьмичасовой сетки BTC; если исторический интервал менялся, это требуется подтвердить отдельным источником, потому что официальная документация указывает, что funding interval зависит от символа: [Funding history](https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate).

Ускорение достигается изменением порядка работы:

1. Один раз исправить implementation fingerprint и изолировать тестовые monitor/fake outputs.
2. Закрыть коротким тестом descendant cleanup и controlled crash/replay.
3. Заморозить дерево и выполнить ровно один новый baseline.
4. После него параллельно запустить новый 24-часовой public shadow и независимый поиск полного funding archive.
5. Data/preflight/historical продолжать только при полном архиве; private UTA, ETH/SOL, optimizer и live отложить.

Такой порядок позволяет использовать реальный wall-clock shadow параллельно исследованию данных, но не повторять полный baseline после каждой промежуточной правки.

## Что делать следующим исполнителям

Порядок первых пакетов работ:

1. Первый общий пакет — `implementation_fingerprint_v2`, отдельные plan/config/data hashes и временный root для fake-live/monitor. Приёмка: targeted-тест и документальная правка не меняют implementation hash, изменение исполняемого файла меняет его, новые файлы в `monitor/fake` не появляются.
2. Отдельный shadow-пакет добавляет descendant cleanup и controlled crash/replay тест. Он не меняет общий статус и не запускает wall-clock run.
3. Интегратор просматривает оба пакета, запускает узкие тесты, lint и `git diff --check`, замораживает дерево и только тогда создаёт один полный baseline. Подагенты baseline и `research-gates` не запускают.
4. После свежего baseline интегратор запускает новую 24-часовую public shadow version. Остановленные v2/v3 в readiness gate не передаются.
5. Параллельный Luna Max research-agent продолжает поиск полного funding-архива по сохранённым аудитам. До принятого архива разрешены только явно незачётные эксперименты; historical optimizer не запускается.
6. Private UTA/account surface, ETH/SOL, optimizer и live не входят в ближайший цикл.

Приватный UTA-ключ не является ближайшим инженерным блокером. Он понадобится перед техническим реальным этапом, после исторического кандидата и 90-дневного paper.

## Правила передачи между моделями

- Один исполнитель владеет общими файлами: `status.json`, канонический план, схема конфигурации и общий Rust-риск.
- Параллельные исполнители получают непересекающиеся файлы и не выставляют статус этапа.
- Если доступен Luna Max, использовать его для параллельных исследовательских и инженерных пакетов с раздельными областями записи; общий статус, план и интеграцию ведёт один исполнитель.
- Перед началом модели читают `status.json`, последний `manifest.json`, текущий `report.md` и `git status`; старый `passed` не считается свежим после изменения кода.
- После каждого подагента интегратор просматривает изменённые строки, запускает его тесты и отдельно проверяет ограничения; сообщение «все команды завершились с кодом 0» не отменяет `blocked` coverage.
- Для public Bitget surface не использовать `last` как замену `mark`, не выводить contract size из quantity step и не принимать неполный tier list. Ошибка refresh должна оставлять только close path.
- Не запускать `research-gates` до окончания изменений в коде и документации: run-пакет получает хэш дерева и после следующего изменения устаревает.
- Для создания нового baseline выполнить три одинаковых прогона Python, fake-live, Rust и `git diff --check`; этот полный набор не повторять ещё раз перед downstream gates, если implementation/config/input hashes не изменились.
- После изменения Rust сначала пересобрать расширение через `rust_utils.check_and_maybe_compile()` или эквивалентный процесс, который обновляет source fingerprint stamp; одной команды `cargo test` недостаточно для Python-проверок.
- Любая попытка перейти из `blocked` в `passed` должна исходить из машинного verdict с новым evidence; ручное редактирование `status.json` или optimistic `metrics.json` запрещено.
- Связанные `research-gates` запускать последовательно: сначала prerequisite, затем зависящий run-пакет. Параллельные агенты не обновляют общий `status.json`.
- Для risk parity отдельные пакеты можно делегировать так: Rust trace, live snapshot surfaces и risk-grid scenarios. Агент Rust не изменяет live exporter или `status.json`; интегратор проверяет trace до принятия результата.
- Rust trace должен сохранять каждую исходную заявку до очистки/перезаписи очереди; итоговый список принятых заявок недостаточен.
- Stable `order_id` меняется только как новая версия boundary-контракта; биржевой ID не используется для доотправочного сравнения.
- Fixture adapter подключён, но не считается фактическим engine adapter и не может поднять `risk_contract`.
- Каждый результат содержит точные команды, число тестов, хэши входов, список изменённых файлов и известные ограничения.
- Зелёный unit-тест не называется пройденным этапом без фактической конфигурации и воспроизводимого сценария.
- Плохой торговый результат не исправляется ослаблением gates.
- Реальные операции Bitget остаются запрещены без отдельного разрешения пользователя.

## Передача следующей модели

Следующий исполнитель должен начать с `status.json`, последнего `manifest.json`, `report.md`, `git status` и проверки source fingerprint Rust. Без этого легко принять старый зелёный пакет за доказательство текущего кода.

Безопасный следующий пакет: проверить входы и запустить один 24-часовой public shadow на frozen implementation/config hash. Параллельно допустим только read-only поиск funding. Семидневный shadow, historical optimizer, paper и live пока не начинать.

Если задача требует параллельной работы, Luna Max можно использовать для независимых исследований и отдельных файловых областей. Один интегратор должен последовательно принимать изменения, запускать tests и research-gates и единолично обновлять `status.json`; подагент не получает разрешения на заявки, переводы или настройки Bitget.

## Историческая актуализация после baseline v77

Интеграционный пакет завершён 18 августа 2026 года. Выполнены и приняты:

- `implementation_fingerprint_v2` с отдельными хэшами плана, конфигурации и набора входов;
- временная изоляция fake-live и monitor output, включая два последовательных и два параллельных subprocess-теста;
- очистка всей POSIX process group, включая потомков, с SIGKILL fallback;
- fault-injection проверки аварийного завершения, checkpoint recovery и конфликтующих order/fill IDs;
- пересборка editable Rust-расширения и 145 Rust-тестов;
- три полных Python-прогона: 2 569 collected, 2 451 passed, 118 skipped, одинаковый raw-output hash;
- исторический immutable `baseline-2026-08-18-v77` и машинный `--freshness-only` refresh статуса.

Исторический результат не открыл data, historical, shadow-readiness, paper или live gates. Public shadow v4 был запущен на fresh v77 implementation/config hash; его partial evidence не является текущей цепочкой. Следующая модель должна была не запускать второй экземпляр, а дождаться финального machine verdict; любое изменение implementation-кода или применяемой конфигурации до завершения shadow создавало новый hash и обнуляло его срок.

## Исторический read-only аудит shadow readiness gate v4 — 18 августа 2026 года, 14:48 UTC

Luna Max проверил gate без изменения файлов и без запуска `research_gates`.
На момент проверки исторический v4 продолжал работать, `result.json` и
`process_supervisor.json` отсутствовали; targeted-набор проверки дал `53 passed`.

Аудит выявил три дефекта, которые на тот момент нельзя было исправлять до окончания v4:

1. `src/tools/research_gates.py` принимает непустой список или строку в
   `provenance.artifacts` как достаточное подтверждение хэшей, потому что
   проверка содержимого выполняется только для mapping.
2. `shadow_readiness` читает переданный market profile, но не связывает его
   public-only свойства и хэш с собственным verdict.
3. `shadow_readiness` не требует `--events` на уровне CLI и может создать
   пустой event bundle.

Подробный отчёт: [`shadow_readiness_gate_audit_2026-08-18-v1.md`](../../analysis_reports/crypto_bot_roadmap/inputs/shadow_readiness_gate_audit_2026-08-18-v1.md).

После завершения исторического v4 нужно было отдельным пакетом исправить эти проверки, добавить
три регрессии, пересобрать baseline/fingerprint и запустить новый public
shadow. Поэтому исторический v4 не должен считаться окончательным readiness evidence
даже при достаточных числах длительности и событий.

## Историческое ревью после остановки shadow v4 и исправления gate — 18 августа 2026 года, 15:20 UTC

### Подтверждено

- v4 остановился без финального результата после 158 snapshots и 1 282
  событий; `result.json` и `process_supervisor.json` отсутствуют;
- до остановки в сохранённых событиях не обнаружены дубли, пропуски sequence,
  регрессии source timestamp или exchange write operations;
- partial evidence не доказывает ни 24 часа, ни пять process restarts;
- три дефекта shadow gate исправлены, а supervisor теперь сохраняет SHA-256
  привязанного market profile;
- целевой набор тестов после исправлений: `47 passed`.

### Следующий пакет

1. Запустить новый shadow в отдельном каталоге на неизменяемом коде и
   конфигурации, с market profile binding и detached process.
2. Проверить `result.json`, `process_supervisor.json`, cumulative counters,
   five process restarts, 24 hours и deterministic replay.
3. Только после этого выполнить `research-gates` с теми же dataset, events,
   config и market profile.
4. Если запуск снова будет прерван, сохранить явный failed/aborted artifact и
   не интерпретировать partial files как evidence.

Historical gate остаётся заблокированным: funding archive по-прежнему покрывает
только 270 из ожидаемых 2 332 settlement points. Paper, technical live,
controlled live и portfolio gates не открыты.

## Промежуточное ревью после baseline v78 — 18 августа 2026 года, 15:47 UTC

### Что подтверждено

- Baseline v78 принят: 2 573 Python-теста собраны, 2 455 прошли, 118
  пропущены; три полных запуска совпали по raw output hash.
- fake-live 20/20, Rust 145/145 и scoped Ruff прошли.
- После смены implementation fingerprint перевыпущены и приняты
  `market-profile v47`, `risk-engine v29` и `risk-grid v49`.
- `data v47` и `historical v34` перевыпущены с актуальным fingerprint и
  остались fail-closed: funding 270/2 332, optimizer не запускался.
- Старый `risk-contract v55` теперь честно показывает `failed` по
  `risk_model_surface_validated=false`; это не должно маскироваться старым
  passed verdict и не заменяет канонический v3 `risk-engine` gate.

### Текущий runtime

`public-stream-process-24h-2026-08-18-v5` запущен без fixture и без доступа к
торговым операциям в отдельной OS-сессии, PID `81180`. На последней проверке
процесс жив, `fixture_mode=false`, сохранены 5 snapshots и 44 typed events;
финальных `result.json` и `process_supervisor.json` ещё нет. Поэтому readiness
gate не запускался, а v5 пока нельзя считать принятым.

### Следующий проверяемый шаг

Дождаться полного v5, затем проверить целостность финальных файлов, профиль и
хэши. После этого один раз запустить shadow-readiness gate с тем же raw market
log, `events.ndjson`, config и market profile. Funding archive остаётся
отдельным blocker: неполные публичные страницы не допускают synthetic fill и
не открывают historical gate.

## Срез редакции 1.61 — 18 августа 2026 года, 17:12 UTC

Это document-only slice после полного ревью за 1 день 6 часов 23 минуты. Цель,
риск-пределы и порядок gates не изменены; торгового кандидата нет. `status.json`
не редактировался вручную. После документов интегратор выпускает один
plan-binding baseline v82 без повторного полного тестового прогона.

### Машинная цепочка и evidence

- `baseline v81` был passed/fresh и являлся только plan-binding run. `baseline v82`
  также должен быть только plan-binding: оба переиспользуют принятое полное
  тестовое evidence v78 — 2 573 collected, 2 455 passed, 118 skipped, 20
  fake-live и 145 Rust; raw output SHA-256 —
  `a1105ac4cd1a5e5f1c6f6d5e787a38566a539972382c2a25ae50cfbeba4343eb`.
- Implementation fingerprint остаётся
  `bbd19e7dd86d05d23c303dbd9b281601748020dd57bf65c165e4be062d53baed`.
  Он совпадает с fingerprint, привязанным к shadow v5; это не означает
  «fingerprint = v5». В v81 metrics остался plan hash от v80, поэтому v82 обязан
  записать новый hash текущего плана одновременно в metrics и manifest.
- `market_profile v47` passed/fresh. `data v47` failed/fresh: 14/16 checks,
  funding 270/2 332, coverage `0.11578044596912522`.
- `risk_engine v29` passed/fresh, но production-parity metric в карантине:
  surface использует research `evaluate_order()`, а не
  `Passivbot._apply_crypto_portfolio_risk_limits`; actual probe v3 не привязан к
  manifest v29. `risk_contract v55` failed/fresh.
- `risk_grid v49` passed/fresh и имеет `stage_admission_status=passed`; admission
  не заблокирован. `historical v34` blocked/fresh, `check_status=failed`,
  optimizer не запускался: blocker — data/funding, а не private Bitget profile.

### Shadow и внешние ограничения

- Machine stage `shadow_readiness` остаётся stale по v21. Это отдельный статус
  stage, а не оценка активного запуска. Live evidence — единственный
  `public-stream-process-24h-2026-08-18-v5`: supervisor PID `81180`, child
  `81189`, watcher `57680`, start `2026-08-18T15:42:29Z`, шесть сегментов по
  14 520 секунд, ETA около `2026-08-19T15:54Z`.
- В срезе `17:12Z` v5 имел 87 snapshots и 706 events; `fixture_mode=false`,
  side=long, trading operations=0, финальных `result.json` и
  `process_supervisor.json` ещё нет. Поэтому v5 не является принятым
  readiness-result и не открывает paper.
- ETH recorder с `duration-seconds=14520` — около 4 часов 2 минут; имя файла
  с `24h` не является 24-часовым evidence. На срезе 1.63 запись
  `eth-public-market-24h-2026-08-18-v1` уже завершена с integrity `failed`.
  v1–v4 не передавались в readiness; `baseline v77` и `shadow v4` сохранены
  только как historical.
- Команда Rust уже исправлена в `docs/ai/commands.md` и
  `docs/ai/build_pitfalls.md`: на macOS используется
  `PYO3_PYTHON=<absolute .venv python> passivbot-rust/cargotest.sh`.

### Новые findings, не исправлять в этом пакете

1. `shadow_planned_orders` может считать `order.planned` для skip/hold; порог
   `≥20` не ослаблять.
2. CLI `--events` всё ещё может создать пустой `events.jsonl.gz`. Gate должен
   fail-closed, хотя bundle при этом появится; provenance mapping и profile
   binding уже исправлены и должны остаться обязательными.
3. Agent order редакции 1.62: live implementation заморожен до v22. P0-E
   остаётся в `TLI-Setka-p0e-clean` / `p0e/data-passport-clean` и не merge до
   v22; sibling `TLI-Setka-p0e` не для merge. P0-D изолированная копия готова
   и uncommitted в `TLI-Setka-p0d`, не merge-ready до v22; в live не копировать.
   Paper калибровка изолированно готова и uncommitted в `TLI-Setka-p1-paper`
   (`qualifying=false`, queue/depth не смоделированы); не merge до v22 и
   не открывает 90-дневный paper. Имя `TLI-Setka-p1-paper-cal` не использовать.
   P1-F timestamp parser изолированно готов и uncommitted в
   `TLI-Setka-p1f-timestamps` / `p1f/strict-timestamp-parser`; не merge до v22.
   Funding research только при новом проверяемом источнике. Второй shadow,
   v83, public/risk rerun, заявки, переводы, секреты и ручная запись
   `status.json` запрещены. Новый engineering-пакет до v22 не запускать.

## Erratum после v82 — 18 августа 2026 года, 17:32 UTC

`baseline-2026-08-18-v82` прошёл. Manifest и `status.json` имеют fingerprint
`35a72db6…`; metrics v82 всё ещё содержат `bbd19e7d…`. Freshness-only поэтому
перевёл `market_profile v47`, `risk_engine v29`, `risk_grid v49`, `data v47`
и `historical v34` в `stale`. Это штатное поведение schema v3, не ручная
правка статуса.

В `src/`, `tests/`, `passivbot-rust/` и `configs/` нет mtime/ctime после v81.
Восемь shadow `CODE_PATHS` не менялись после `15:23Z`. v5 жив (PID `81180`),
v22 нет. Не запускать второй shadow и не пересобирать public/risk цепочку до
машинного v22.

## Следующий этап 1.62 — 18 августа 2026 года, 19:38 UTC

Канонический план поднят до редакции 1.62. Это постановка, не торговый допуск
и не изменение gates.

Сейчас, до v22: live `src/`, `tests/`, `passivbot-rust/` и `configs/` не
менять; v5 не останавливать и не дублировать; v83 и public/risk rerun не
запускать. v5 — `--side long` transport, не Long+Short. Порог
`shadow_planned_orders >= 20` не ослаблять. ETH sidecar ~4 часа не есть 24h
evidence. P0-E остаётся uncommitted в `TLI-Setka-p0e-clean`. P0-D изолированная
копия готова и uncommitted в `TLI-Setka-p0d`, не merge-ready до v22;
`risk_engine v29` на shared status по-прежнему завышает production parity.
Paper-калибровка изолированно готова и uncommitted в `TLI-Setka-p1-paper`
на `p1/paper-execution-calibration` @ `9dee208e5`; `qualifying=false`,
queue/depth не смоделированы. Live не содержит `paper_market_surfaces.py`.
Пакет не открывает 90-дневный paper и не merge-ready до v22.

Срез `19:50Z`: у v5 есть `result.json` со `status=failed` (~4,03 ч,
`shadow_planned_orders=5`, `candidate_admission=false`).
`process_supervisor.json` нет; `shadow-readiness-2026-08-18-v22` нет.
Supervisor `81180` и watcher `57680` живы, текущий child `61667`.
Freeze остаётся «ждать v22»: watcher ждёт оба файла и не застрял.
Failed `result.json` не принимать как завершённый readiness-run и не
переписывать. Порог `shadow_planned_orders >= 20` не ослаблять.

После v22 принять `passed`/`failed`/`blocked` как есть. Порядок merge-review:
P0-E, затем P0-D, затем paper-пакет из `TLI-Setka-p1-paper`. Один новый
полный baseline — только если fingerprint изменился. Historical, 90d paper
и live по-прежнему требуют полный лицензированный funding archive; 270/2332
этот путь не открывает. Торговая готовность остаётся нулевой.

## Ревью плана 1.62 — 18 августа 2026 года, 19:53 UTC

Document-only сверка канона с machine truth и изолированными копиями.
`src/` не менялся, merge нет, второй shadow не запускался, `status.json`
не писался. Срез 19:50 UTC про paper/v5 не откатывался.

Вердикт: **редакция 1.62 в целом совпадает с фактами и остаётся
действующим порядком**. Цель не изменена. Торговая готовность ноль.
Production risk и qualifying paper не доказаны. Промежуточный failed
`result.json` не есть v22.

### Machine truth

- `status.json` schema v3, `updated_at_utc=2026-08-18T17:27:50Z`.
  `baseline-2026-08-18-v82` passed/fresh, fingerprint `35a72db6…`.
  `market_profile v47`, `risk_engine v29`, `risk_grid v49`, `data v47`,
  `historical v34` stale. `shadow_readiness` — stale v21, не v5.
- Live `master` @ `9dee208e5`: нет `risk_evidence.py`, нет
  `data_passport.py`, нет `paper_market_surfaces.py`.
- v5 supervisor PID `81180` жив (~4h11m), `--side long`,
  `fixture_mode=false`. Текущий child `61667` —
  `strategy_shadow_stream --duration-seconds 14520` (сегмент, не весь
  24h run). Watcher `57680` жив; лог только `watcher_started
  gate_id=shadow-readiness-2026-08-18-v22`. Он ждёт оба файла.
- `result.json` есть (mtime `2026-08-18 21:45` local / ~19:45 UTC):
  `status=failed`, `candidate_admission=false`,
  `covered_duration_hours≈4.032`, `shadow_planned_orders=5`,
  `shadow_determinism_runs=0`, `shadow_replay_count=0`,
  `shadow_stream_gap_check_status=passed`,
  `trading_operations_count=0`, `private_requests=0`.
  `process_supervisor.json` нет. Каталог
  `runs/shadow-readiness-2026-08-18-v22` нет.
- Это сегментный/промежуточный файл при живом supervisor, не финал
  readiness. Порог `shadow_planned_orders >= 20` не ослаблять.

### Изолированные пакеты

- P0-E: `TLI-Setka-p0e-clean` / `p0e/data-passport-clean`, uncommitted,
  есть `data_passport.py`, нет `risk_evidence.py`. Frozen feed 270/2332,
  `qualifying=false`. Sibling `TLI-Setka-p0e` не для merge.
- P0-D: `TLI-Setka-p0d` / `codex/p0d-production-risk-evidence-20260818`,
  uncommitted. Manifest schema `risk-parity-production-evidence-v1`,
  snapshot `cb50c9a…`, invocation
  `Passivbot._apply_crypto_portfolio_risk_limits` + Rust `Backtest`,
  `research_policy_used=false`, `candidate_admission=false`. Surface
  matrix остаётся `research_policy` / `fixture_mode=true`. Worktree
  fingerprint `e611043b…` не равен live `35a72db6…` и не привязан к
  shared status. `risk_engine v29` по-прежнему завышает production
  parity.
- P1-G paper: `TLI-Setka-p1-paper` / `p1/paper-execution-calibration`
  @ `9dee208e5`, uncommitted, есть `paper_market_surfaces.py`, нет
  `data_passport.py` / `risk_evidence.py`. Каталог
  `TLI-Setka-p1-paper-cal` отсутствует. `qualifying=false`. Queue/depth
  не смоделированы: даже после v22 этот пакет не закрывает исходный
  merge-check «отдельный calibration artifact очереди». Live paper
  по-прежнему может подставлять ticker `last` как mark/index.
- P1-F timestamp parser: на срезе 19:53 UTC ещё **in-flight**. Сверка
  20:14 UTC ниже переводит пакет в isolated-ready.

### Противоречия, не ломающие порядок

1. Freeze fingerprint vs изолированная работа — допустимо, пока пакеты
   не merge в live `src/`.
2. v5 `--side long` ≠ независимая квалификация Long+Short.
3. `risk_engine v29` passed/stale ≠ production parity.
4. Live last-as-mark ≠ изолированный P1-G; qualifying paper не открыт.
5. Data passport 270/2332 держит `qualifying=false`; historical закрыт.
6. Промежуточный failed `result.json` с 5 planned ≠ право ослабить ≥20
   и ≠ повод для второго shadow.
7. Срез 19:38 UTC («`result.json` отсутствует») — исторический;
   актуальный срез 19:50/19:53 UTC.

### Сейчас / после v22 / после funding

**Сейчас:** ждать финал v5 и машинный v22; не останавливать и не
дублировать shadow; не писать v22/`process_supervisor.json` вручную;
не v83 и не public/risk rerun; не merge. P0-E, P0-D, paper и P1-F уже
изолированно готовы. Новый engineering-пакет не запускать.

**После v22:** принять `passed`/`failed`/`blocked` как есть →
merge-review P0-E → P0-D → paper (mark/index/tiers/evidence writer;
queue/depth — отдельный хвост) → один полный baseline только если
fingerprint изменился.

**После funding:** data/preflight → 4 окна historical → 90d paper →
UTA isolated/hedge + явное разрешение → 30d technical live → 90d на
1000 USDT. 270/2332 этот путь не открывает.

## Сверка P1-F — 18 августа 2026 года, 20:14 UTC

Document-only. Isolation claim из isolated P1-F подтверждён. Merge нет,
в live `src/` пакет не копировался, второй P1-F не запускался,
`status.json` не писался, v83 нет.

- Live `master` @ `9dee208e5`: `_integer()` в
  `src/research/bitget_market_surfaces.py` по-прежнему делает
  `int(value)` и даёт `int(1.9) -> 1`. Live file mtime
  `2026-08-17 16:33`; live tests mtime `2026-08-17 16:43`. После
  `21:50` local в live `src/`/`tests/`/`passivbot-rust/`/`configs/`
  новых файлов нет.
- Worktree `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Setka-p1f-timestamps`
  / `p1f/strict-timestamp-parser` @ `9dee208e5`, uncommitted
  (`?? src/research/`, `?? tests/test_bitget_market_surfaces.py`).
  Нет `data_passport.py`, `risk_evidence.py`, `paper_market_surfaces.py`.
- Parser принимает int и digit strings в int64 bounds; отклоняет
  float (`1.9`, `1.0`), bool, NaN/Inf, overflow. Spot-check import:
  worktree raises на `1.9`; live возвращает `1`.
- v5: supervisor `81180`, child `61667`, watcher `57680` живы.
  `result.json` `status=failed`, `planned_order_count=5`,
  `covered_duration_hours≈4.032`, `candidate_admission=false`.
  `process_supervisor.json` нет. `runs/shadow-readiness-2026-08-18-v22`
  нет. Freeze: ждать v22. Порог `>= 20` не ослаблять.
- `status.json` `updated_at_utc` всё ещё `2026-08-18T17:27:50Z`.

**Merge-readiness:** P1-F не merge-ready до машинного v22. В live не
копировать. Остающаяся freeze-работа — ждать v22 при уже изолированных
P0-E, P0-D, paper и P1-F.

## Снимок без нового пакета — 18 августа 2026 года, 20:42 UTC

Document-only. План 1.62 не даёт следующего изолированного пакета кроме
ожидания v22 и хвоста queue/depth (им владеет существующий paper
worktree). `account_risk_surface` не начинать: он нужен только перед
technical live. Новый worktree / invent-scope пакет не создавался.
Gates не ослаблялись. `status.json` не писался.

- v22 отсутствует. `process_supervisor.json` отсутствует.
- v5 жив: supervisor `81180` (~5 ч), child `61667` (~57 мин),
  watcher `57680`. `--side long`. Live counters: 284 market events,
  2282 typed events.
- Промежуточный `result.json` всё ещё `failed` (~4.03 ч, 5 planned,
  admission false). Это сегмент 1, не readiness-run.
- Merge-readiness без изменений: ни один изолированный пакет не
  merge-ready до машинного v22.

## Полный промежуточный срез 1.63 — 18 августа 2026 года, 21:37 UTC

Document-only. Цель не сдвинута. Торгового кандидата нет. Срез **не**
перезапускал полный baseline. `status.json` не писался. `research-gates`
не запускались. Implementation-код не менялся.

| Область | Наблюдаемый факт | Решение |
|---|---|---|
| Цель | historical, 90d paper, 30d technical live и 90d controlled live не пройдены | цель не завершена |
| Freeze | live `master` @ `9dee208e5`; 0 файлов в `src/`/`tests/`/`configs/`/`passivbot-rust/` новее `20:42Z`; `CODE_PATHS` не менялись после старта v5 `15:42:32Z` | freeze держится; не патчить watcher race в live `CODE_PATHS` |
| Baseline | v82 passed/fresh; metrics fingerprint `bbd19e7d…`; gate `35a72db6…`; plan SHA v82 `0eeffcd9…`; файл плана 1.62 был `ad8f96f4…` | не запускать v83 |
| Public/risk/data | v47/v29/v49/v47/v34 stale после v82; funding 270/2332 | data/historical закрыты |
| v5 | supervisor `81180`, child `61667` (сегмент 2), watcher `57680`; 339 snapshots / 2722 events; restart-0001 1,442 с | ждать финал; не дублировать |
| Сегментный result | `result.json` = `segment-001-result.json`, mtime `19:45:30Z`, `status=failed`, 5 planned, ~4,03 ч, 4 stream errors | не принимать как v22 |
| Planned vs hold | 686 `order.planned` (681 hold/skip, 5 enter/exit); fills 5, последний `16:04:14Z` | порог `>=20` не ослаблять |
| Watcher race | supervisor пишет `process_supervisor.json` раньше финальной перезаписи `result.json`; watcher уже видит сегментный result; poll 60 с, затем 3 hash-пробы по 5 с | после появления файлов проверить nested `process_supervisor` и cumulative duration |
| ETH sidecar | `eth-public-market-24h-2026-08-18-v1` finished: 4,017 ч, 231 rows, integrity `failed` (12 errors / 3 SSL-timeout-DNS rows); PID `67935` мёртв | не перезапускать; не portfolio evidence |
| Изолированные пакеты | `p0e-clean`, `p0d` (kitchen-sink), `p1-paper`, `p1f-timestamps` @ `9dee208e5`, uncommitted | не merge; queue/depth не стартовать в этой пачке |
| Paper live | `fetch_positions()` всё ещё last-as-mark и maintenance 0.5% | qualifying paper закрыт |

**Agent order после 1.63:** единственный обязательный саб-агент — read-only
runtime reviewer v5. P0-D/P0-E/P1-F не запускать заново. Queue/depth в
`TLI-Setka-p1-paper` не стартовать в этой пачке. Второй shadow, v83, ETH
rerun, merge, ручной `status.json` запрещены.

**После v22:** принять фактический `passed`/`failed`/`blocked`; проверить, что
v22 читал финальный `result.json`, а не сегмент 1; merge-review
`p0e-clean` → `p0d` (только production-path) → `p1-paper` → `p1f-timestamps`.

Проверено после документов, `2026-08-18T22:04Z`: PIDs `81180`/`61667`/`57680`
живы; counters 364/2922; `process_supervisor.json` нет; v22 нет; v83 нет;
ETH не перезапускался; queue/depth и merge не запускались.
Continue `22:07Z`: counters 367/2946; v22 всё ещё отсутствует.
Continue `22:17Z`: counters 377/3026; PIDs те же; v22 нет.

## Редакция 1.64 — перенос на Synology Docker, 19 августа 2026 года ~00:40 Europe/Amsterdam

Пользователь явно перенёс 24-часовой public shadow на NAS: Mac sleep убивает exam. Freeze «ждать Mac v22» снят.

| Область | Наблюдаемый факт | Решение |
|---|---|---|
| Цель | historical, 90d paper, 30d technical live и 90d controlled live не пройдены | цель не завершена |
| Resume | child уже поднимает checkpoint; supervisor требовал пустой каталог; source-gap 300 с | `--resume-existing-output`; отказ если log stale или `process_supervisor.json` уже есть; не подставлять timestamps |
| CODE_PATHS | изменены `strategy_shadow_stream_process.py` и CLI-wrapper | новый контракт, новый 24h wall-clock на NAS; правило 28 |
| Тесты | 13/13 `tests/test_strategy_shadow_stream_process.py` | resume/stale/completed/duplicate-id покрыты |
| Docker | `Dockerfile_shadow` public-only, native amd64 на DS923+, без ключей; CFS `cpus:` на DSM нет | проект `/volume1/docker/_cm/passivbot-shadow/`; контейнер running с `2026-08-18T22:53:49Z` |
| Mac v5 | watcher `57680` и collector остановлены; journal продолжен на NAS | не создавать v22; два collector’а не держать |
| Cutover | source-gap ≈66 с, `ingest_seq` 412→414, `pre-resume-result.json` есть | journal сохранён; 24h wall-clock идёт заново на NAS |
| Readiness | `shadow-readiness-2026-08-19-v23` после NAS v6 | порог planned ≥20 не ослаблять |
| Isolated packages | `p0e-clean`, `p0d`, `p1-paper`, `p1f-timestamps` | не merge до v23 |

**Agent order после 1.64:** додержать NAS v6; не запускать Mac v22, v83, ETH rerun, queue/depth, merge, ручной `status.json`. После v23 — прежний merge-review порядок.

