# Hash-anchored public execution feed

Дата: **17 августа 2026 года**, Europe/Amsterdam.

## Что добавлено

Paper-клиент теперь может получить путь к сохранённому публичному Bitget-файлу через:

- `live.paper_execution_inputs_path`;
- `live.paper_execution_inputs_since_ms`.

Поддержаны сохранённые `bitget-public-execution-inputs-v1` и JSONL-снимки market recorder. Перед использованием проверяются Bitget, `read_only_only=true`, ноль торговых операций, символ, временные метки и числовые значения. В событие funding попадает SHA-256 всего исходного файла.

Для funding берётся только mark, записанный строго до момента расчёта. Если такой mark отсутствует, settlement не создаётся. Текущая цена, будущая свеча и нулевая ставка не используются как замена.

Каждый settlement имеет стабильный id `SYMBOL:timestamp`. Уже просмотренные settlement сохраняются в локальном paper state, поэтому перезапуск не начисляет funding повторно. Settlement, обнаруженный при отсутствии позиции, также помечается просмотренным и не может быть применён к более поздней позиции.

Monitor перед локальной reconciliation просит paper-бот применить только новые строки из явно настроенного файла. Этот путь не загружает данные из Bitget, не читает API-ключи, не отправляет заявки и не меняет счёт.

## Проверка

Целевой прогон после изменения: **106 тестов прошли**:

```text
tests/test_paper_public_inputs.py
tests/test_paper_bitget.py
tests/test_paper_funding.py
tests/test_paper_reconciliation.py
tests/test_paper_typed_journal.py
tests/test_monitor_typed_runtime_events.py
tests/test_passivbot_monitor.py
tests/test_config_pipeline.py
```

Дополнительно прошли compileall и `git diff --check`.

## Что это не доказывает

Feed намеренно принимает ровно один настроенный символ: при BTC+ETH он останавливается с явной ошибкой, а не применяет только BTC. Портфельный feed будет отдельным пакетом с общей временной шкалой.

Этот пакет не закрывает data gate: полного исторического funding-архива всё ещё нет. Он также не моделирует глубину, очередь и частичные исполнения и не запускает 7-дневный exploratory shadow-run. Поэтому статус `paper_runtime` остаётся `not_started`, а зачётный 90-дневный paper не начинается.
