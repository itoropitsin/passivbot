# Paper fill → monitor integration

Дата: **17 августа 2026 года**, Europe/Amsterdam.

Репозиторий: `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Setka`.

## Результат

Связь локального paper fill с обычным monitor writer реализована и проверена:

```text
PaperBitgetCCXTClient
  → write-ahead typed order.filled WAL
  → local ledger
  → FillEvent
  → Passivbot monitor payload
  → MonitorPublisher events/current.ndjson
  → summarize_runtime_events
```

Тест создаёт реальный локальный fill через `PaperBitgetCCXTClient`; он не создаёт заявку на Bitget и не использует приватный API.

## Что изменено

- Paper fill сохраняет в `info` типизированные `fee` и `slippage`, order/fill/event ID, `strategy_id`, `cycle_id`, `config_hash`, `part_sequence`, количество до/после и reference price.
- Monitor payload читает эти значения из вложенного `raw.data.info`, не подменяя отсутствующие поля.
- С Signed `FillEvent.fee_paid` для typed audit сохраняется положительная сумма фактически уплаченной комиссии; PnL-учёт не меняется.
- Для фактического paper fill корректно восстанавливаются `order_id`, typed fee, typed slippage и контекст стратегии.
- При отсутствии остальных поверхностей расчётчик остаётся `blocked`.

## Проверка

Целевой прогон:

```shell
./.venv/bin/pytest -q --disable-warnings \
  tests/test_passivbot_monitor.py \
  tests/test_paper_bitget.py \
  tests/test_paper_typed_journal.py \
  tests/test_runtime_evidence.py
```

Результат: **37 passed**.

Дополнительно прошли:

- полный Python-набор: **2 417 passed**, три повторных запуска с кодом 0;
- fake-live: **20 passed**;
- Rust: **145 passed**;
- `git diff --check`: чисто.

Технический паспорт: [`baseline-2026-08-17-v37`](../runs/baseline-2026-08-17-v37/report.md).

## Что пока не доказано

`summarize_runtime_events` видит typed fee и typed slippage из фактического paper fill, но verdict остаётся `blocked`, потому что paper-контур ещё не создаёт полный набор доказательств:

- mark/index;
- funding;
- margin;
- reconciliation и cost reconciliation;
- непрерывные heartbeat и runtime snapshots;
- depth, очередь и частичные исполнения.

Поэтому этот пакет не начинает и не засчитывает 90-дневный paper-период.

## Безопасность

Во время проверки не выполнялись реальные заявки, переводы, приватные Bitget-запросы, изменение режима счёта или работа с API-ключами.

