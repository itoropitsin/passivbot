# Аудит paper-контура и следующий безопасный шаг

Дата: **17 августа 2026 года**  
Репозиторий: `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Setka`  
Область: `src/exchanges/paper.py`, `src/research/runtime_evidence.py`, monitor-слой, paper/runtime-тесты и раздел paper в плане.

Аудит выполнен независимо основным агентом и отдельным агентом Luna Max. Результаты совпали.

## Вывод

Paper-контур пригоден для локального smoke- и shadow-run без денег. Он не готов к зачётному 90-дневному paper-периоду.

В этом аудите не изменялись `status.json`, план или README. Реальные заявки, приватные Bitget-запросы, настройки счёта и переводы не выполнялись.

Код не изменён. Причина: полезный следующий шаг должен быть подключён одновременно к paper-ledger и к production monitor-событиям. Разрешённая область записи (`src/research/paper*` и `tests/test_paper*`) не включает эти точки подключения. Отдельный несвязанный helper создал бы видимость прогресса, но не дал бы paper-доказательств.

## Что уже доказано

- Paper-клиент создаёт Bitget CCXT без ключей и использует его только для публичных markets, ticker и OHLCV: `src/exchanges/paper.py:154-212`, `625-757`.
- `PaperBitgetBot` отключает приватную сессию и WebSocket: `src/exchanges/paper.py:1119-1127`.
- Market- и limit-заявки исполняются только в локальном fake-ledger: `src/exchanges/paper.py:784-906`; публичный клиент не получает вызов создания заявки.
- По явно заданному пути локальное состояние записывается атомарно: баланс, позиции, заявки, fills, комиссии, идентификаторы и обработанные event ID: `src/exchanges/paper.py:287-353`.
- Корректный restart и повторное применение уже сохранённых order/fill ID покрыты тестами: `tests/test_paper_bitget.py:178-285`.
- Ограничение модели явно опубликовано в коде: funding, depth и partial fills имеют статус `not_implemented`, а liquidation — только стрессовое предположение: `src/exchanges/paper.py:165-183`.

Это полезная техническая база, но не доказательство качества исполнения или доходности.

## Блокирующие находки

### 1. Paper не создаёт typed runtime evidence

`src/research/runtime_evidence.py:519-665` — это проверяющий читатель, а не источник событий. Он требует:

- typed fee и typed slippage для каждого `order.filled`;
- хотя бы одно событие с mark/index;
- funding, margin, reconciliation и cost-reconciliation evidence.

Фактический production payload строится в `src/passivbot_monitor.py:224-239`. Там комиссия записывается числом (`"fee": 0.55055`), без currency и typed object; reference price и slippage не записываются. Поэтому текущая цепочка не может сама произвести журнал, который примет `runtime_evidence`.

Это проверено на настоящем локальном paper fill:

```text
typed_fee_error: fee must be a typed object with amount/currency
typed_slippage_error: typed slippage requires prices or slippage bps
```

Тесты `tests/test_runtime_evidence.py:29-141` создают полные typed-события вручную. Они проверяют валидатор, но не связь paper-клиента с этим валидатором.

### 2. Есть атомарный snapshot, но нет журнала перед изменением

`_persist_paper_state()` записывает новый JSON после изменения ledger (`src/exchanges/paper.py:314-353`). В `_fill_order()` сначала вызывается fake-ledger mutation, затем сохранение (`src/exchanges/paper.py:950-960`). Поэтому падение между этими действиями оставляет изменение только в памяти. После перезапуска оно может исчезнуть, а входное событие — прийти повторно.

`processed_events` хранит только связь event ID с order/fill ID (`src/exchanges/paper.py:484-498`), но не исходное событие, решение, typed cost evidence или контрольную цепочку. Это защита от некоторых повторов, не write-ahead journal.

### 3. Mark/index и liquidation не являются рыночными данными Bitget

В `fetch_positions()` обычный `last` используется и как `markPrice`, и как `indexPrice`: `src/exchanges/paper.py:1081-1095`. Liquidation считается фиксированной формулой с maintenance `0.5%`, а не по фактическим position tiers Bitget: `src/exchanges/paper.py:1085-1104`.

Такую модель можно использовать как явно названный стресс, но нельзя считать доказательством реальной маржи, mark price или liquidation distance.

### 4. Нет funding, depth, очереди и частичных исполнений

- Funding не начисляется: `src/exchanges/paper.py:167-170`.
- Depth и queue position не моделируются: `src/exchanges/paper.py:171-174`.
- Исполнение всегда полное в `src/exchanges/fake.py:1047-1055`.
- Повторный fill по уже исполненному order специально отклоняется как неподдерживаемый partial fill: `src/exchanges/paper.py:934-942`, `1015-1024`.

Следовательно, комиссии и локальный PnL не представляют полную модель затрат perpetual-контракта.

### 5. Restart-проверка пока слишком узкая

Тесты подтверждают корректное закрытие, восстановление и повтор уже сохранённого event ID. Они не проверяют остановку в каждой точке перехода заявки, повреждение/неполную запись, падение после mutation до persistence, пять контролируемых перезапусков или восстановление за 60 секунд.

Требование плана строже: `docs/plans/crypto_grid_bot_development_plan_2026-08-17.md:233-254` требует журнал до изменения, funding, mark/index, depth, partial fills, обязательный evidence journal и пять контролируемых падений.

## Тесты аудита

Запущены без реальных запросов и без изменения файлов плана/status/README:

```text
./.venv/bin/pytest -q --disable-warnings tests/test_paper_bitget.py
10 passed

./.venv/bin/pytest -q --disable-warnings tests/test_runtime_evidence.py
11 passed

./.venv/bin/pytest -q --disable-warnings tests/test_passivbot_monitor.py tests/test_monitor_publisher.py
16 passed

./.venv/bin/python -m compileall -q src/exchanges/paper.py src/research/runtime_evidence.py
passed
```

Дополнительное наблюдение: `tests/test_paper_bitget.py:288-298` прямо фиксирует `funding`, `depth` и `partial_fills` как `not_implemented`.

## Минимальный следующий пакет

Следующим исполнителям следует брать только пакет **typed fill journal + crash/replay test**. Funding, mark/index, margin и partial fills не следует имитировать в этом пакете.

Целевой результат:

1. Перед изменением локального ledger записывать append-only `order.filled` с `strategy_id`, `cycle_id`, `config_hash`, order/fill ID, последовательностью части исполнения, количеством до/после, typed fee `{amount, currency, rate}` и typed slippage `{reference_price, executed_price}`.
2. Сначала надёжно записывать событие, затем менять ledger; после изменения делать контрольный snapshot.
3. При повторном event ID возвращать ранее доказанный результат без второго изменения баланса, PnL или комиссии.
4. Добавить crash-injection после записи журнала и после изменения ledger. После restart итог должен совпадать с однократным replay, а незавершённое событие не должно молча исчезать.
5. Пропустить журнал, созданный настоящим paper-контуром, через `summarize_runtime_events()`. Fixture, вручную собранный только в `test_runtime_evidence.py`, не считается интеграционным доказательством.

Этот пакет требует записи как минимум в `src/exchanges/paper.py` и monitor-слое (`src/passivbot_monitor.py` или общем writer). Поэтому в рамках данного аудита он не реализовывался: это соблюдает ограничение пользователя и не добавляет неиспользуемый код.

После него всё ещё останутся отдельные обязательные блокеры: реальные или явно предоставленные mark/index surfaces, funding-модель и архив, margin tiers, depth/queue/partial fills, reconciliation и 7-дневный технический shadow-run. До их доказательства 90-дневный paper gate запускать нельзя.

## Передача следующей модели

- Не менять `status.json`, план и README в рамках этого аудита.
- Не переводить paper в `passed` по числу unit-тестов.
- Не использовать `last` как mark/index без явной пометки стрессового допущения.
- Не заполнять отсутствующий funding нулём, соседним значением или скрытой синтетикой.
- Не начинать зачётный paper-период до появления typed evidence из фактического paper-run.
- После реализации пакета повторить targeted paper/runtime тесты, затем полный Python, fake-live и Rust-набор по правилам плана.
