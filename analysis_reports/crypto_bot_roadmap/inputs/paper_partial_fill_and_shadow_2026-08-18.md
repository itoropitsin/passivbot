# Paper partial fills and bounded shadow-runner

Дата проверки: **18 августа 2026 года**, Europe/Amsterdam.

## Что изменено

- `src/exchanges/paper.py` принимает несколько явных частей одного исполнения.
- После неполной части заявка остаётся в `open_orders`; закрытие происходит только при нулевом остатке.
- Для каждой части сохраняются `part_sequence`, количество до и после, остаток заявки, fee, slippage и typed write-ahead WAL.
- Повтор того же `event_id`/`fill_id` идемпотентен; неправильный порядок частей и превышение остатка блокируются.
- `src/tools/bitget_paper_shadow.py` напрямую создаёт `PaperBitgetCCXTClient` с очищенным BTC-профилем и public Bitget client.
- Runner не вызывает `passivbot live`, `setup_bot` или `load_user_info`, не читает credential storage, не создаёт заявки и не перезапускается автоматически.
- Runner пишет `manifest.json`, `config.json`, `market_profile.json`, `events.jsonl.gz`, local ledger, `verdict.json` и `checksums.json`.

## Проверка

Прошли:

```text
.venv/bin/python -m pytest -q --disable-warnings \
  tests/test_bitget_paper_shadow.py \
  tests/test_paper_bitget.py \
  tests/test_paper_typed_journal.py \
  tests/test_paper_funding.py \
  tests/test_paper_reconciliation.py \
  tests/test_paper_public_inputs.py
```

Результат: **36 тестов**.

Полный стабильный набор после изменений: **2 448 Python-тестов**, три одинаковых успешных прогона; hash захваченного вывода последнего прогона:
`989a4818ee6a8d6b0852d99beade70d5123d76f38b93ed4f0277658e67562d94`.

## Ограничения

Это не доказательство биржевой глубины и позиции в очереди. Явный `paper_fill_amount` или входное fill-событие задаёт количество части; один ticker/candle не может считаться доказательством maker queue или partial-fill probability. Для этого нужны сохранённые depth/trade inputs и отдельная калибровка.

Тестовый bounded run с двумя наблюдениями получил `no_trade_observed`. Это ожидаемый безопасный результат: отсутствие заявок и исполнений не превращается в успешный paper-период. Реальный 7-дневный exploratory shadow-run и зачётный 90-дневный paper ещё не начинались.

Полный historical data gate остаётся `failed`: funding покрывает только 268 из 2 332 ожидаемых точек точного окна после строгой проверки официального API. `risk_contract` остаётся закрыт без isolated UTA account ledger.

