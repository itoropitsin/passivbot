# Текущий read-only аудит funding Bitget BTCUSDT

Дата проверки: **18 августа 2026 года, примерно 12:40–12:44 UTC**.

Целевое окно для historical gate не менялось:

- начало: `2024-07-01T00:00:00Z`;
- конец: `2026-08-17T00:00:00Z`;
- cadence: 8 часов;
- ожидание: `2 332` settlement-события.

## Результат

Официальные публичные endpoint Bitget снова дали один и тот же rolling-архив:

- UTA v3: страницы `100, 100, 70, 0`, всего `270` строк;
- Classic v2: страницы `100, 100, 70, 0`, всего `270` строк;
- последовательности timestamps и ставок совпали полностью;
- oldest observed: `2026-05-20T16:00:00Z`;
- newest observed: `2026-08-18T08:00:00Z`;
- в frozen historical window попало `266` строк;
- отсутствует `2 066` из `2 332`, coverage `11.41%`;
- дублей, malformed rows, разрывов внутри наблюдаемого диапазона и чужих symbols не обнаружено.

Это rolling snapshot, а не новый полный historical input. Его нельзя объединять с frozen execution input и нельзя использовать для открытия data gate.

Документация:

- [UTA Get Funding Rate History](https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate) — `cursor` является номером страницы, максимум `100`, лимит страницы `100`;
- [Classic Get Historical Funding Rates](https://www.bitget.com/api-doc/classic/contract/market/Get-History-Funding-Rate).

## Raw evidence

Все ответы сохранены без credentials и без приватных заголовков. SHA-256:

| Endpoint | Страница 1 | Страница 2 | Страница 3 | Страница 4 |
|---|---|---|---|---|
| UTA v3 | `47d59e001df332c1666e61d566e2512e99b79728921b7c3c324d484a3b3ed41a` | `b6ea7bde93dce1538209a02464b1b31d9941bcf2e3cee7f5068cb1d19aaa7bdb` | `1cacf07cf0086cb779a8dd85e6e0c2f7506ec3a943409bdb021ba57c06b9ae79` | `64dd105f7b1b3cce8deed3da753ff26a2765f00d62ba54ee5e5dc6a7a49852e7` |
| Classic v2 | `eff303c93ee727a667a35320627a5659e7dd8ced5210aeadb35be0a8565ca696` | `f999a5436b9828acca14d1afecd9873e656ddea864b04f5d85145e81a44c32b9` | `5dee351e304682702d34b56ac1a1cd72da2f91d02d794ff3bd831b923d3cfb76` | `6b968429cf77b0eb11c654ec55c8d268ab867da9a9aade19bc3cf3e43ceff8cf` |

Файлы имеют префиксы:

- `funding_next_audit_2026-08-18_bitget_uta_v3_BTCUSDT_page*.json`;
- `funding_next_audit_2026-08-18_bitget_classic_v2_BTCUSDT_page*.json`.

## Внешние источники

В этой проверке не использовались credentials, платные подписки и внешние ключи.

- Tardis имеет только частичное доступное покрытие, начиная с `2024-11-08`.
- Amberdata и Laevitas документируют возможные маршруты, но без ключа строки не выдают.
- Ни один из этих вариантов не закрывает historical gate без отдельной проверки фактических applied settlement rows, лицензии и сохранения raw responses.

## Machine decision

`funding_history_complete=false`, `funding_known_coverage_ratio=0.11578044596912522`.

Data gate, historical preflight и продвижение execution input остаются закрыты. Пропуски не синтезировались, frozen input не перезаписывался.

## Независимая повторная проверка

18 августа 2026 года в `14:08–14:10 UTC` выполнены два независимых read-only
контроля: повторный запрос официального Classic v2 API и отдельная проверка
Luna Max.

Повторный запрос
`https://api.bitget.com/api/v2/mix/market/history-fund-rate` с
`BTCUSDT`, `USDT-FUTURES`, `pageSize=100` дал страницы `100 + 100 + 70 + 0`.
Страница `100` также вернула `0` строк. Результат не изменился: доступный
rolling-архив заканчивается на `270` строках и не покрывает окно
`2024-07-01..2026-08-17`.

Независимо проверены:

- открытый набор на Hugging Face — только небольшой sample со снимками и
  `fundingRate`, без полного ряда applied settlements;
- GitHub-проект funding collector — код для дальнейшего собственного сбора,
  но не готовый архив Bitget за нужный период.

Эти материалы не принимаются как historical input: они не доказывают полное
покрытие, settlement-семантику и пригодность для воспроизводимого backtest.
Итог не изменился: `funding_history_complete=false`, data gate и historical
selection остаются закрыты. Данные другой биржи, нулевое заполнение,
forward-fill и прогнозирование не применялись.
