# Paper runtime: typed market, margin and funding evidence

Дата проверки: **17 августа 2026 года**, Europe/Amsterdam.

## Что теперь доказано

Paper-контур разделяет расчёт и доказательство:

- `PaperBitgetCCXTClient.apply_funding_settlement(...)` принимает только явно переданный settlement: id, rate, timestamp, symbol и mark price. Он не обращается к Bitget, не подставляет `last` вместо mark price и не придумывает исторический funding.
- Funding меняет локальный balance, realized PnL и realized funding; комиссии не меняются. Повтор того же id после перезапуска возвращает исходное evidence и не начисляет расчёт второй раз. Изменённый payload с тем же id отклоняется.
- Paper monitor при снимке процесса записывает `runtime.heartbeat`, публичные `market.mark_index` только при наличии проверенной mark/index surface и локальную `margin.snapshot` только для paper-модели. Для live-счёта маржа из номинала не выводится.
- Уже проверенное `funding.settled` можно передать в monitor writer через `_monitor_record_funding_evidence`. Неполное или арифметически противоречивое событие не записывается.

## Проверка

Целевой набор: **39 тестов**, включая paper funding, paper WAL, monitor writer и runtime evidence — все прошли.

Полный Python-прогон после изменений также завершился успешно; новая базовая версия будет создана после синхронизации плана и зависимых run-пакетов.

## Что это не доказывает

Это не закрывает `data` или `paper_runtime` gate:

- исторический funding Bitget всё ещё неполон: публичная проверка подтверждает 268 из 2 332 ожидаемых расчётов;
- runtime сам по себе не получает полный архив funding и не сверяет его с биржей автоматически;
- reconciliation, реальные комиссии/исполнения, очередь, частичные исполнения и authoritative isolated margin остаются незавершёнными;
- текущий приватный профиль — Classic Account, а не требуемый UTA isolated/hedge;
- 7-дневный shadow-run и 90-дневный зачётный paper-период не начинались.

Следующий безопасный пакет: подключить явный источник funding к paper runner с сохранением source/hash и добавить reconciliation evidence. До этого paper остаётся `not_started`, а торговые заявки и переводы не выполняются.
