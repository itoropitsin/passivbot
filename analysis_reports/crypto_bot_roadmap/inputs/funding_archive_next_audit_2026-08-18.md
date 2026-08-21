# Повторная проверка источников funding для Bitget BTCUSDT

Дата и время проверки: 18 августа 2026 года, `03:14:56–03:19:41 UTC`.

Область проверки: `BTCUSDT`, Bitget USDT perpetual, расчёт funding каждые 8 часов.
Целевое окно принято включительно:

- начало: `2024-07-01T00:00:00Z`;
- конец: `2026-08-17T00:00:00Z`;
- ожидаемое количество расчётов: `2 332`.

## Строгий вывод

**Нет. В доступных и разрешённых для этой проверки public-источниках нет
проверяемого полного ряда из 2 332 расчётов.** Историческая проверка должна
оставаться `blocked`.

Причина установлена точно:

1. Официальные публичные Bitget API сейчас возвращают только 270 последних
   расчётов. В целевое окно попадают 267, а 2 065 расчётов отсутствуют.
2. Оба официальных endpoint дают один и тот же ряд: одинаковые timestamps и
   ставки. Второй endpoint не является дополнительным архивом.
3. Tardis действительно отдаёт проверяемые public sample-файлы, но покрытие
   Bitget Futures начинается только с `2024-11-08T00:00:00Z`. До этой даты не
   хватает как минимум 390 ожидаемых расчётов. Полный доступ зависит от
   подписки и API key; он не использовался.
4. Amberdata и Laevitas документируют возможные исторические маршруты, но без
   ключа не выдают строки. Полученные ответы — отказ в доступе, а не архив.
5. Ни одна строка не была придумана, интерполирована, скопирована с другой
   биржи или заменена нулём.

Следовательно, сейчас нельзя честно запускать исторический отбор, используя
этот ряд как полный funding history.

## Проверенное официальное API Bitget

### UTA v3

Документация: <https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate>

Запрос:

```text
GET https://api.bitget.com/api/v3/market/history-fund-rate
  ?category=USDT-FUTURES
  &symbol=BTCUSDT
  &limit=100
  &cursor=N
```

В документации указаны поля `symbol`, `fundingRate` и
`fundingRateTimestamp`; последняя дата — Unix timestamp в миллисекундах.

| Проверка | Результат |
|---|---:|
| HTTP/code | `200` / `00000` |
| Последовательные страницы 1–4 | `100, 100, 70, 0` |
| Страницы 5, 10, 50, 100 | `0, 0, 0, 0` |
| Все строки до очистки границы | `270` |
| Строки внутри целевого окна | `267` |
| Первая доступная строка | `2026-05-20T08:00:00Z` |
| Последняя доступная строка | `2026-08-18T00:00:00Z` |
| Строки после целевой границы | `3` |
| Дубли timestamps | `0` |
| Неверные или нечисловые строки | `0` |
| Разрывы в наблюдаемом диапазоне | `0` |
| Чужие symbols | `0`; только `BTCUSDT` |
| Ожидалось во всём окне | `2 332` |
| Отсутствует | `2 065` |
| Покрытие целевого окна | `267 / 2 332 = 11,45%` |

Raw-ответы сохранены без заголовков и без секретов в файлах с префиксом
`funding_next_audit_2026-08-18_bitget_uta_v3_BTCUSDT_`.

| Страница | Строк | Размер, байт | SHA-256 |
|---:|---:|---:|---|
| 1 | 100 | 8563 | `47d59e001df332c1666e61d566e2512e99b79728921b7c3c324d484a3b3ed41a` |
| 2 | 100 | 8559 | `b6ea7bde93dce1538209a02464b1b31d9941bcf2e3cee7f5068cb1d19aaa7bdb` |
| 3 | 70 | 6031 | `1cacf07cf0086cb779a8dd85e6e0c2f7506ec3a943409bdb021ba57c06b9ae79` |
| 4 | 0 | 85 | `64dd105f7b1b3cce8deed3da753ff26a2765f00d62ba54ee5e5dc6a7a49852e7` |
| 5 | 0 | 85 | `04dc7e128f5fbb78f1df17bbcc1156843f33646cc77e21a365a006437852b05f` |
| 10 | 0 | 85 | `eb86eed140bf8e57ecd0b44bf7c927ef17b3e0ffa48725bdc6d50a5c9dda7a23` |
| 50 | 0 | 85 | `3b3acfe2c642ee5bbc8ee0a52b9b360e84234e141c3f715d3ccbebd16af66806` |
| 100 | 0 | 85 | `1476b1aeb06fbf788694a8ea85489831c9859e10f5f53796f161a223102d6cb5` |

### Classic v2

Документация: <https://www.bitget.com/api-doc/classic/contract/market/Get-History-Funding-Rate>

Запрос:

```text
GET https://api.bitget.com/api/v2/mix/market/history-fund-rate
  ?symbol=BTCUSDT
  &productType=USDT-FUTURES
  &pageSize=100
  &pageNo=N
```

Документация называет `fundingTime` временем расчёта.

| Проверка | Результат |
|---|---:|
| HTTP/code | `200` / `00000` |
| Последовательные страницы 1–4 | `100, 100, 70, 0` |
| Страницы 5, 10, 50, 100 | `0, 0, 0, 0` |
| Всего строк | `270` |
| Строки внутри целевого окна | `267` |
| Дубли, неверные строки, разрывы, чужие symbols | `0, 0, 0, 0` |
| Отсутствует в целевом окне | `2 065` |

Сверка с UTA v3: timestamps только в одном из рядов — `0`, несовпадений
ставок — `0`. Поэтому v2 не добавляет покрытие.

Raw-ответы сохранены в файлах с префиксом
`funding_next_audit_2026-08-18_bitget_classic_v2_BTCUSDT_`.

| Страница | Строк | Размер, байт | SHA-256 |
|---:|---:|---:|---|
| 1 | 100 | 7648 | `eff303c93ee727a667a35320627a5659e7dd8ced5210aeadb35be0a8565ca696` |
| 2 | 100 | 7644 | `f999a5436b9828acca14d1afecd9873e656ddea864b04f5d85145e81a44c32b9` |
| 3 | 70 | 5386 | `5dee351e304682702d34b56ac1a1cd72da2f91d02d794ff3bd831b923d3cfb76` |
| 4 | 0 | 70 | `6b968429cf77b0eb11c654ec55c8d268ab867da9a9aade19bc3cf3e43ceff8cf` |
| 5 | 0 | 70 | `deadde9e54b625fc9a88f21c688f73817b469d6e9a1626aa6cd17e584f08c2d1` |
| 10 | 0 | 70 | `bd006d112756b424407c498f6d585dc61fc63a8b5a21961dfc587a0d21f80d32` |
| 50 | 0 | 70 | `3eb35e8a277065f9caeab53753019c36a5795b3c761a53ff5bc6bf3792e8aff8` |
| 100 | 0 | 70 | `8f1ea85a2be9e65ed4223fc931bee1e2f4a277e68ce16e997d5da71cea618ad9` |

### Старый Bitget v1

Запрос:

```text
GET https://api.bitget.com/api/mix/v1/market/history-fundRate
  ?symbol=BTCUSDT_UMCBL&pageSize=100&pageNo=1
```

Получен HTTP `400`, code `30032`, сообщение: `The V1 API has been
decommissioned. Please migrate to a newer version.`

Raw-файл: `funding_next_audit_2026-08-18_bitget_v1_BTCUSDT_decommissioned.json`.
SHA-256: `db0501bba860a1c3ef4e16167dc5417ba4ce1e3eb880c98ddf9510fe94380fbd`.

## Tardis: что проверено и почему этого недостаточно

Документы:

- <https://docs.tardis.dev/historical-data-details/bitget-futures>
- <https://docs.tardis.dev/downloadable-csv-data-types>
- <https://docs.tardis.dev/faq/billing-and-subscriptions>
- <https://docs.tardis.dev/legal/terms-of-service>

Свежий metadata API:

```text
GET https://api.tardis.dev/v1/exchanges/bitget-futures
```

В raw metadata указано:

- exchange: `bitget-futures`;
- `BTCUSDT`: perpetual;
- `availableSince`: `2024-11-08T00:00:00.000Z`;
- доступный тип, содержащий funding: `derivative_ticker`.

Файл: `funding_next_audit_2026-08-18_tardis_bitget_futures_metadata.json`.
SHA-256: `02a1763ec80250437541252bba48d61d30d84d6d264011553fa03c274b8f47dd`.

Свежий public sample:

```text
GET https://datasets.tardis.dev/v1/bitget-futures/derivative_ticker/2024/12/01/BTCUSDT.csv.gz
```

Проверено: 163 447 строк, только `BTCUSDT`, 3 разных funding timestamps за
день. Файл: `funding_next_audit_2026-08-18_tardis_BTCUSDT_derivative_ticker_2024-12-01.csv.gz`.
SHA-256: `47db0d0633045d6c3a646b6ed37571dfd65b7d882e92a24a60f47971272703c9`.

Это не settlement archive: `funding_rate` относится к ближайшему следующему
событию и может меняться; применённой считается последняя ставка перед
`funding_timestamp`. Это описано в документации и подтверждено sample.

Даже при наличии разрешённого полного Tardis CSV доступа его заявленное начало
`2024-11-08` оставляет до этой даты 390 расчётов, поэтому Tardis не закрывает
запрошенное окно. Публично без ключа доступны только sample-дни. Документация
подписки говорит, что полный CSV доступ предоставляется по API key и плану;
ключ и платная подписка в этой проверке не использовались. Условия Tardis
допускают внутреннее хранение данных в рамках договора, но конкретный план и
его разрешение для этого проекта ещё не подтверждены — это не считается
пройденным источником.

## Условные внешние варианты

Эти варианты проверены только на доступность без ключа. Ни один не был принят
как источник и ни один ключ не использовался.

### Amberdata

Документы:

- <https://docs.amberdata.io/data-dictionary/coverage/exchange-coverage>
- <https://docs.amberdata.io/http/market/futures/funding-rates>

Документация описывает historical funding route, `rateType=applied` и требует
заголовок `x-api-key`. Запрос для точного BTCUSDT окна без ключа вернул HTTP
`403` и `{"message":"Forbidden"}`.

URL запроса:

```text
https://api.amberdata.com/markets/futures/funding-rates/BTCUSDT
  ?exchange=bitget
  &startDate=2024-07-01T00:00:00Z
  &endDate=2026-08-17T00:00:00Z
  &timeFormat=milliseconds
  &rateType=applied
```

Raw-файл: `funding_next_audit_2026-08-18_amberdata_BTCUSDT_no_key.json`.
SHA-256: `12a22880bc2e59f8278b4a5e547567f0aa14d020ea456598267fa00208cfebc5`.

Возможное покрытие из документации — это только условие для следующей
проверки; фактические 2 332 строки, их хэши и разрешение на хранение здесь не
доказаны.

### Laevitas

Документы:

- <https://docs.laevitas.ch/future/historical>
- <https://docs.laevitas.ch/derivs/historical>
- <https://docs.laevitas.ch/>

Проверены без ключа два маршрута:

```text
GET https://api.laevitas.ch/historical/futures/perpetual_funding/BTC/BITGET
  ?start=2024-07-01&end=2026-08-17&limit=144&page=1

GET https://api.laevitas.ch/historical/futures/settlement_funding_rate/BITGET/BTCUSDT
  ?start=2024-07-01&end=2026-08-17&limit=144&page=1
```

Оба вернули HTTP `400` и `{"status":400,"message":"Api Key is Required"}`.
Файлы:

- `funding_next_audit_2026-08-18_laevitas_BTC_BITGET_no_key.json`;
- `funding_next_audit_2026-08-18_laevitas_settlement_BTCUSDT_no_key.json`.

SHA-256 обоих raw-ответов:
`dcb2ffeb9f6ea8b712cb4f770a56c892e1b9bf01d9242ee899fe5a182d75c12f`.

Документация сама по себе не доказывает, что после авторизации будет ровно
2 332 применённых settlement событий с разрешённым хранением raw данных.

## Локальные проверки

Выполнено:

```text
PYTHONPATH=src .venv/bin/python - <<'PY'
from research.funding_quality import validate_funding_rows
# загружены страницы Bitget 1–4, проверены timestamps, rates, symbols,
# границы, дубли, пропуски и 8-часовой шаг
PY

PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' -q --disable-warnings \
  tests/test_bitget_funding_archive.py \
  tests/test_bitget_funding_horizon_probe.py \
  tests/test_historical_preflight.py
```

Результат: `8 passed`.

Итог local validator для каждого официального endpoint:

```json
{
  "expected_rows": 2332,
  "actual_rows_in_window": 267,
  "missing_rows": 2065,
  "duplicate_rows": 0,
  "invalid_rows": 0,
  "interval_gaps": 0,
  "future_rows": 3,
  "complete_requested_window": false
}
```

## Что проверено, а что осталось предположением

Проверено непосредственно 18 августа:

- raw ответы Bitget v3 и v2 без авторизации;
- их HTTP-коды, страницы, строки, timestamps, ставки, symbols и SHA-256;
- дополнительные номера страниц 5, 10, 50 и 100;
- отключение v1;
- Tardis metadata и открытый sample;
- ответы без ключа Amberdata и Laevitas;
- локальная проверка качества и тесты.

Не утверждается как доказанный факт:

- что Amberdata или Laevitas действительно выдадут полный BTCUSDT ряд без
  пропусков после покупки доступа;
- что любой внешний источник разрешает бессрочное хранение raw responses и
  использование их в backtest без отдельного согласования;
- что Tardis `derivative_ticker` можно безошибочно превратить в settlement
  archive во всём периоде: полный файл и первые 390 событий отсутствуют.

## Следующий проверяемый вариант

Самый короткий проверяемый путь — получить от владельца лицензированного
источника полный raw export для `BTCUSDT`, Bitget USDT perpetual, с полями
settlement timestamp, applied funding rate, symbol и source metadata, а затем
проверить его локально без изменения строк:

```text
PYTHONPATH=src .venv/bin/python -m src.tools.bitget_funding_archive \
  --archive /absolute/path/to/bitget_btcusdt_funding_2024-07-01_2026-08-17.csv \
  --symbol BTCUSDT \
  --start 2024-07-01T00:00:00+00:00 \
  --end 2026-08-17T00:00:00+00:00 \
  --source-kind licensed_bitget_archive \
  --source-url 'https://provider.example/source' \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_archive_validation_next.json \
  --normalized-output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_archive_normalized_next.json
```

До получения такого файла результат остаётся `blocked`. Сбор funding с
сегодняшнего дня полезен для будущего paper/live журнала, но не восстанавливает
пропущенные расчёты июля 2024 — мая 2026 и не может быть подставлен в этот
исторический ряд.

## Ограничения аудита

- Не использовались API keys, private endpoints, платные источники или
  торговые операции.
- Не изменялись код, планы, `status.json` и run-пакеты.
- Записан только этот отчёт и public raw-файлы с префиксом
  `funding_next_audit_2026-08-18`.
