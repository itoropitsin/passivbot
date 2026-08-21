# Аудит источников полного исторического funding Bitget

Дата проверки: 17 августа 2026 года, Europe/Amsterdam. Свежие HTTP-проверки публичных endpoint выполнены 17 августа 2026 года в 15:55 UTC.

## Краткий вывод

На момент проверки нет источника, который можно принять для исторического `gate` сразу для `BTCUSDT` и `ETHUSDT` за `2024-07-01T00:00:00Z..2026-08-17T00:00:00Z`.

- Официальные публичные endpoint Bitget возвращают только последние 270 событий. В точном целевом окне до `2026-08-17T00:00:00Z` свежая проверка нашла 269 из ожидаемых 2 332 событий на каждый символ. Пропуски нельзя заполнить предположением.
- Лучший внешний кандидат — Amberdata. В документации есть Bitget Futures, точные инструменты `BTCUSDT` и `ETHUSDT`, поле применённого funding и покрытие Bitget funding с 2 февраля 2024 года. Проверка фактического ряда невозможна без API-ключа; ключ не запрашивался и не использовался.
- Второй внешний кандидат — Laevitas. Документация указывает Bitget, `BTCUSDT` и `ETHUSDT`, а также отдельный endpoint settlement funding. Однако без ключа нельзя проверить содержимое, границы архива и точные settlement timestamps; документация для settlement endpoint не даёт рабочего примера с данными.
- Tardis предоставляет проверяемые сырые файлы `derivative_ticker` для обоих символов, но архив начинается только с 8 ноября 2024 года. Его `funding_rate` — ставка для следующего события; применённой считается последняя ставка перед `funding_timestamp`. Это полезный независимый источник для перекрёстной проверки и части периода, но не полный источник для текущего окна.
- CoinGlass, Coinalyze, CoinAPI, CryptoHFTData и опубликованный университетский набор данных не проходят строгие условия полного источника: агрегат вместо события, ограниченный период, отсутствие Bitget в покрытии, отсутствие доказуемого symbol-level raw доступа или отсутствие источника и лицензии.

Статус: `blocked`. Исторический отбор не должен запускаться, пока для обоих символов не появится полный проверенный ряд применённых ставок с сохранёнными raw responses и разрешённым использованием.

## Что считается полным источником

Для этой проверки целевое окно задано включительно:

- начало: `2024-07-01T00:00:00Z`;
- конец: `2026-08-17T00:00:00Z`;
- ожидаемая частота: 8 часов;
- ожидаемое число событий на символ: `2 332`.

Источник принимается только если одновременно выполнены условия:

1. биржа — именно Bitget, продукт — USDT perpetual, symbol — ровно `BTCUSDT` или `ETHUSDT`;
2. в ряду указана фактически применённая ставка, а не текущая, прогнозная или средняя ставка нескольких бирж;
3. есть точный timestamp события расчёта в UTC;
4. после разбиения на страницы нет пропусков, дублей, чужих символов, значений вне окна или неотмеченной синтетики;
5. для каждого запроса можно сохранить исходный ответ, URL, параметры, дату получения и SHA-256;
6. условия использования разрешают локальный backtest и сохранение необходимых raw responses;
7. результаты можно сравнить на пересечении периода с официальным Bitget API и независимым источником.

Если источник отдаёт только агрегированные OHLC funding или прогноз, он может использоваться для справочной проверки, но не закрывает этот `gate`.

## Проверенные источники

### 1. Bitget UTA v3 — официальный публичный API

Документация: [Get Funding Rate History](https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate)

Запросы:

```text
GET https://api.bitget.com/api/v3/market/history-fund-rate
  ?category=USDT-FUTURES
  &symbol=BTCUSDT|ETHUSDT
  &limit=100
  &cursor=1..100
```

Результат свежего read-only probe для каждого из `BTCUSDT` и `ETHUSDT`:

| Проверка | Результат |
|---|---:|
| HTTP / Bitget code | `200` / `00000` |
| Число строк страниц 1–4 | `100, 100, 70, 0` |
| Всего строк в rolling history | `270` |
| Строк до точной границы `2026-08-17T00:00:00Z` | `269` |
| Первая строка полного ответа | `2026-05-19T16:00:00Z` |
| Последняя строка полного ответа | `2026-08-17T08:00:00Z` |
| Дубли | `0` |
| Интервалы внутри наблюдаемого ряда | 8 часов |
| Ожидалось в целевом окне | `2 332` |
| Дефицит в точном окне | `2 063` |

Поля ответа — `symbol`, `fundingRate`, `fundingRateTimestamp`. Это подходящий формат и правильная биржа, но история фактически обрывается примерно на 90 днях. Пустая страница 4 сохранена как доказательство остановки пагинации; старые события не были восстановлены.

SHA-256 свежих raw responses, полученных с сохранением в локальные временные файлы:

| Symbol | Страница 1 | Страница 2 | Страница 3 | Страница 4 |
|---|---|---|---|---|
| `BTCUSDT` | `5ececc170fa3b5ade75e07db34197f53abae01cdab492174819446957e0d4d1e` | `a5a38ffc6e30a560e49032855b75a244d7f7cbaf3dc47291a3ed9d7b2bf50ed0` | `43cdb6b90719b36d388b8fc66ae4f4b6354ca3cfea5690736a6f5635e6bce7d7` | `f5b1234365ea2ab8929dde13bd4f9692e1ba9068824420c6deac73f6beccaf60` |
| `ETHUSDT` | `6a4b1f3eab9246805ef9adf0dfa2ad80de2f8627c83e917aa0eb4c1038c3c14a` | `11ee6b35c55cf25145d580a2f758bed119005885a87cb82ee5d57f9d04f2a695` | `5421a72dd0f9d6a77d2a18937b5d4c48c39112b2b29340f3389c65193940092c` | `e61087bc75bafe760f62ba4a749580aa98825bd4a78b8832dc0b7fe63d1299a7` |

Решение: `unacceptable_for_full_window`.

### 2. Bitget Classic v2 — официальный публичный API

Документация: [Get Historical Funding Rates](https://www.bitget.com/api-doc/classic/contract/market/Get-History-Funding-Rate)

Запросы:

```text
GET https://api.bitget.com/api/v2/mix/market/history-fund-rate
  ?symbol=BTCUSDT|ETHUSDT
  &productType=USDT-FUTURES
  &pageSize=100
  &pageNo=1..100
```

Свежий результат полностью совпал с UTA v3: `100, 100, 70, 0`, всего 270 событий, 269 событий до целевой конечной границы, первая доступная дата `2026-05-19T16:00:00Z`. Ответ содержит `symbol`, `fundingRate`, `fundingTime`.

SHA-256 страниц:

| Symbol | Страница 1 | Страница 2 | Страница 3 | Страница 4 |
|---|---|---|---|---|
| `BTCUSDT` | `dcb415ca35f103b37a7f0a3a23a476c890ef55657e049aae9476752fd235ce96` | `d1148848b183c9124ee2eae83b566c347a71d4185f919395e77584028594593e` | `bf2e2d636412d56087ecbb6f01b1e656e0fb90ba9a2c2ad5549a98521aa1a9c5` | `e245ef0805c5964b426f6d86bd72f17049a52dc39c1f0078fbf41267dfb1de07` |
| `ETHUSDT` | `0474c63629eb7d8924ed072305f51ec9f179ea5cebafeed9323f81ef1596b63e` | `c81569db58a1b3831df11188cc15ea8697f723dd823adeee2c6403652f90c486` | `b760d852da2c526934efd06b448c1a884ff5a1e174ab29e6c44c01a727424e97` | `46300825d64425c12c6d7d089cd76bba1d1b3da2be58b5b950fcf18a8f73171d` |

Решение: `unacceptable_for_full_window`. V2 не является независимым полным архивом: он имеет тот же фактический предел истории.

### 3. Bitget V1 — официальный, но отключённый API

Старая документация: [Bitget V1 API reference](https://bitgetlimited.github.io/apidoc/en/mix/)

Проверенный старый маршрут возвращает код `30032` и сообщение о том, что V1 отключён. Использовать его для восстановления старой истории нельзя.

Решение: `unavailable`.

### 4. Bitget History Data Download — официальный download-раздел

Страница: [History data download](https://www.bitget.com/data-download)

В разделе Futures перечислены свечи, сделки и глубина стакана. Отдельного архива funding rate или settlement funding на странице нет. Поэтому download-раздел не даёт требуемый источник.

Решение: `unacceptable_for_funding`.

## Внешние источники

### 5. Amberdata — самый сильный условный кандидат

Документы:

- [Покрытие бирж и дат](https://docs.amberdata.io/data-dictionary/coverage/exchange-coverage)
- [Historical funding rates](https://docs.amberdata.io/http/market/futures-funding-rates)
- [Information endpoint](https://docs.amberdata.io/http/market/futures-funding-rates-information)
- [Batch historical endpoint](https://docs.amberdata.io/http/market/futures-batch-funding-rates)
- [API fundamentals](https://docs.amberdata.io/http/http-api-fundamentals)
- [Terms of Service](https://www.amberdata.io/terms)

Что подтверждено документацией:

- В таблице покрытия есть `Bitget / Futures`; для Funding Rate указано начало `2024-02-02`, то есть раньше начала нашего окна.
- Исторический endpoint принимает `exchange=bitget`, инструмент в path, даты и `rateType=applied`.
- Пример batch-ответа содержит именно `BTCUSDT` и `ETHUSDT`, `exchangeTimestamp`, `fundingRate` и `isActualFundingRate: true`.
- Документация указывает максимальный диапазон одного запроса 731 день. Для нашего окна нужно как минимум два непересекающихся запроса, например `2024-07-01..2026-07-01` и `2026-07-01..2026-08-17`, с учётом правила `endDate` exclusive.
- Все запросы требуют пользовательский `x-api-key`.

Проверенный запрос без ключа:

```text
GET https://api.amberdata.com/markets/futures/funding-rates/BTCUSDT
  ?exchange=bitget
  &startDate=2024-07-01T00:00:00Z
  &endDate=2026-08-17T00:00:00Z
  &timeFormat=milliseconds
  &rateType=applied
```

Аналогичный запрос проверен для `ETHUSDT`, а также проверены information и batch routes. Все вернули HTTP `403` и `{"message":"Forbidden"}` из-за отсутствия ключа. SHA-256 этого ответа: `12a22880bc2e59f8278b4a5e547567f0aa14d020ea456598267fa00208cfebc5`.

Лицензия и доступ: API-ключ обязателен. В Terms для бесплатного плана есть ограничения на копирование и создание derived data; для локального backtest и хранения raw responses нужно подтвердить подходящий платный или иной разрешённый режим. Страница pricing отдельно указывает исследование и backtesting как use case, но условия конкретной подписки всё равно должны быть проверены перед загрузкой.

Решение: `conditional_candidate`, не `passed`. Источник может закрыть исторический `gate` только после получения данных и проверки:

1. exact `exchange=bitget`, `instrument=BTCUSDT` и `instrument=ETHUSDT`;
2. `rateType=applied` и `isActualFundingRate=true` для всех строк;
3. точная полнота 2 332 событий на каждый символ;
4. raw JSON, хэши и разрешение на локальное хранение;
5. совпадение пересечения с официальным Bitget за последние доступные 90 дней.

### 6. Laevitas — условный кандидат с точным symbol mapping

Документы:

- [Historical perpetual funding](https://docs.laevitas.ch/future/historical)
- [Historical derivatives and instruments](https://docs.laevitas.ch/derivs/historical)
- [Getting started and API access](https://docs.laevitas.ch/)
- [V1 API Swagger](https://api.laevitas.ch/swagger/)

Что подтверждено документацией:

- Для historical derivatives явно указана связка `BITGET` с инструментами `BTCPERP`, `BTCUSDT`, `ETHUSDT` и `ETHUSD`.
- Для historical futures funding есть endpoint `https://api.laevitas.ch/historical/futures/perpetual_funding/{currency}/{market}` с `currency=BTC|ETH` и `market=BITGET`.
- В документации также есть `settlement_funding_rate/{market}/{symbol}`, но для этого endpoint опубликован пример `N/A` с `items: [null]`. Поэтому нельзя заранее считать его доказанным архивом применённых ставок.
- Доступ к V1 API требует API key; getting-started страница указывает enterprise subscription.
- В проверенных публичных материалах не найдено отдельного разрешения на бессрочное хранение raw-ответов и использование их в backtest; это нужно подтвердить условиями выбранной подписки до загрузки.

Проверенные read-only запросы без ключа:

```text
GET https://api.laevitas.ch/historical/futures/perpetual_funding/BTC/BITGET
  ?start=2024-07-01&end=2026-08-17&limit=144&page=1

GET https://api.laevitas.ch/historical/futures/perpetual_funding/ETH/BITGET
  ?start=2024-07-01&end=2026-08-17&limit=144&page=1

GET https://api.laevitas.ch/historical/futures/settlement_funding_rate/BITGET/BTCUSDT
  ?start=2024-07-01&end=2026-08-17&limit=144&page=1
```

Для BTC, ETH и проверенных symbol-вариантов сервис вернул HTTP `400` с `{"status":400,"message":"Api Key is Required"}`. SHA-256 ответа: `dcb2ffeb9f6ea8b712cb4f770a56c892e1b9bf01d9242ee899fe5a182d75c12f`.

Решение: `conditional_candidate`, не `passed`. После получения разрешённого ключа нужно проверить точные symbol-level строки, применённость ставки, settlement timestamps, число страниц, границы и лицензию на backtest/raw storage. Если будет доступен только агрегированный perpetual funding без settlement событий, он не закрывает этот `gate`.

### 7. Tardis — проверяемый частичный архив и источник перекрёстной проверки

Документы и API:

- [Bitget Futures coverage](https://docs.tardis.dev/historical-data-details/bitget-futures)
- [Derivative ticker schema](https://docs.tardis.dev/downloadable-csv-data-types)
- [Exchange metadata](https://api.tardis.dev/v1/exchanges/bitget-futures)
- [CSV download documentation](https://docs.tardis.dev/downloadable-csv-files)
- [Billing and historical-data access](https://docs.tardis.dev/faq/billing-and-subscriptions)
- [Terms of Service](https://docs.tardis.dev/legal/terms-of-service)

Проверка метаданных вернула HTTP `200` и точные записи:

```json
{"id":"BTCUSDT","type":"perpetual","availableSince":"2024-11-08T00:00:00.000Z"}
{"id":"ETHUSDT","type":"perpetual","availableSince":"2024-11-08T00:00:00.000Z"}
```

Проверяемые sample-файлы:

- [BTCUSDT derivative ticker, 2024-12-01](https://datasets.tardis.dev/v1/bitget-futures/derivative_ticker/2024/12/01/BTCUSDT.csv.gz)
- [ETHUSDT derivative ticker, 2024-12-01](https://datasets.tardis.dev/v1/bitget-futures/derivative_ticker/2024/12/01/ETHUSDT.csv.gz)

В обоих CSV есть `exchange`, `symbol`, `timestamp`, `funding_timestamp`, `funding_rate`, `predicted_funding_rate`, `index_price` и `mark_price`. Проверенные sample-файлы скачиваются без ключа:

| Symbol | HTTP | Строк | Funding events в sample | SHA-256 |
|---|---:|---:|---:|---|
| `BTCUSDT` | 200 | `163 447` | 3 события с шагом 8 часов | `47db0d0633045d6c3a646b6ed37571dfd65b7d882e92a24a60f47971272703c9` |
| `ETHUSDT` | 200 | `167 122` | 3 события с шагом 8 часов | `6d9c17fc05e0dc10419d8451fdf1103d96704e736a36ac155a579160464858b3` |

Документация Tardis прямо описывает `funding_rate` как ставку для следующего события; итоговой применённой ставкой считается последнее значение перед `funding_timestamp`. В sample это видно на практике: перед событиями `2024-12-01T08:00Z`, `16:00Z` и `2024-12-02T00:00Z` присутствуют последние значения до соответствующего timestamp.

Ограничения:

- архив начинается только `2024-11-08`, поэтому отсутствуют первые 130 дней целевого окна;
- `derivative_ticker` — поток изменений тикера, а не отдельная запись settlement от Bitget;
- без ключа публично доступны только отдельные sample-файлы; полный исторический доступ зависит от плана и периода подписки. Документация указывает CSV-доступ для Academic/Solo и CSV плюс raw replay API для Pro/Business;
- условия использования являются договорными: разрешение на локальное хранение raw и конкретное использование в backtest нужно подтвердить по выбранной подписке и Terms of Service. Сам Tardis перечисляет backtesting среди сценариев использования, но это не заменяет проверку условий конкретного плана;
- полноту последней строки перед каждым settlement event нужно проверять отдельно;
- доступный без ключа файл — sample первого дня месяца, не весь архив.

Решение: `partial_only`. Подходит для проверки Amberdata/Laevitas на пересечении и для отдельного теста реконструкции применённой ставки, но не для закрытия полного исторического `gate`.

### 8. CoinGlass — агрегированный OHLC, не settlement archive

Документы:

- [Funding Rate History (OHLC)](https://docs.coinglass.com/reference/fr-ohlc-histroy)
- [Endpoint overview](https://docs.coinglass.com/reference/endpoint-overview)

Endpoint `https://open-api-v4.coinglass.com/api/futures/funding-rate/history` принимает `exchange`, `symbol`, `interval`, `start_time` и `end_time`, но возвращает `open/high/low/close` funding для выбранного интервала. Наличие отдельных settlement событий, поля `isActualFundingRate` и точного Bitget raw значения документацией не подтверждено. Доступ требует API plan и ключ.

Проверенные в этом аудите материалы не подтверждают разрешение сохранять и повторно использовать raw-ответы в локальном backtest; это должно быть отдельно проверено по условиям выбранного API plan.

Решение: `unacceptable_for_full_window`; можно использовать только как справочное сравнение после проверки symbol/exchange.

### 9. Coinalyze — API с ограниченной intraday history и агрегатами

Документация: [Coinalyze API](https://api.coinalyze.net/v1/doc/)

Funding endpoint возвращает OHLC-историю по внутреннему symbol mapping, а не гарантированную последовательность Bitget settlement records. Документация отдельно говорит, что для intraday granularity хранится только примерно 1 500–2 000 последних точек и старые данные ежедневно удаляются. Без ключа:

```text
GET https://api.coinalyze.net/v1/exchanges -> 401 Invalid/Missing API key
GET https://api.coinalyze.net/v1/future-markets -> 401 Invalid/Missing API key
```

Публичное разрешение на локальное хранение raw-ответов и backtest в проверенных материалах не зафиксировано; даже при выдаче ключа это потребует отдельного подтверждения.

Решение: `unacceptable_for_full_window`. Даже при наличии ключа нужно было бы доказывать точный Bitget symbol mapping и отсутствие агрегации; заявленный intraday retention не даёт надёжного полного ряда из 2 332 событий.

### 10. CoinAPI — Bitget отсутствует в опубликованном funding coverage

Документы:

- [Funding metric guide](https://www.coinapi.io/products/market-data-api/docs/how-to-guides/get-symbol-funding-rate-metric-data)
- [Market Data API FAQ](https://www.coinapi.io/products/market-data-api/faq)

В опубликованном списке бирж с funding rate data перечислены Binance, Bitfinex, Bybit, Deribit, OKX и другие, но Bitget отсутствует. Поэтому даже при наличии общего исторического metrics API источник не подтверждён для Bitget `BTCUSDT`/`ETHUSDT`.

Решение: `unacceptable_for_bitget`.

### 11. CryptoHFTData — поздний hourly archive

Страница: [Bitget Historical Funding Rate Data](https://www.cryptohftdata.com/datasets/bitget-funding-rate-data)

Документация заявляет Parquet/Zstd hourly files, поля `mark_price`, `index_price`, `funding_rate` и `next_funding_time`, а начало Bitget Futures — `2025-07-15`. Это позже начала целевого окна и описывает funding внутри mark-price файлов, а не отдельный архив применённых settlement events.

Условия лицензии или разрешение на сохранение raw-файлов в проверенной публичной странице не зафиксированы; источник нельзя принять без отдельного подтверждения этого права.

Решение: `unacceptable_for_full_window`; возможен только как дополнительная проверка позднего периода.

### 12. Университетский набор данных из публикации

Источник: [BRNO University dataset description](https://dspace.vut.cz/server/api/core/bitstreams/ccd63aba-d714-41cb-b067-8a430f91826e/content)

В описании указано `Bitget funding`, окно `2021-05..2025-12` и 10 040 строк. Но для этого набора указаны `Source None` и `Access None`; нет локального raw archive, точной пары для каждой строки, лицензии и данных до августа 2026 года.

Решение: `unacceptable_for_gate`.

## Сводная таблица решений

| Источник | BTCUSDT | ETHUSDT | Raw response | Applied/settlement proof | Покрытие окна | Решение |
|---|---|---|---|---|---|---|
| Bitget UTA v3 | да | да | да | да, но только текущий горизонт | нет, 269/2 332 | не принят |
| Bitget Classic v2 | да | да | да | да, но тот же горизонт | нет, 269/2 332 | не принят |
| Bitget Data Download | нет | нет | нет | нет | нет | не принят |
| Amberdata | заявлено | заявлено | да после API доступа | заявлено через `applied`/`isActualFundingRate` | заявлено с 2024-02-02 | условный кандидат |
| Laevitas | заявлено | заявлено | да после API доступа | не подтверждено на ответе; settlement docs N/A | не опубликовано для пары | условный кандидат |
| Tardis | да | да | да | реконструкция из последней строки до события | с 2024-11-08 | только частично |
| CoinGlass | не подтверждено | не подтверждено | да после ключа | OHLC aggregate | не доказано | справочно |
| Coinalyze | не подтверждено | не подтверждено | да после ключа | OHLC aggregate | intraday retention ограничен | не принят |
| CoinAPI | нет в coverage | нет в coverage | да по API | не подтверждено | нет | не принят |
| CryptoHFTData | заявлено | заявлено | да | hourly mark-price fields | с 2025-07-15 | не принят |
| University dataset | не доказано | не доказано | нет | не доказано | до 2025-12 | не принят |

## Рекомендуемый порядок продолжения

1. Не менять текущий data `gate` и не запускать историческую оптимизацию на неполном funding.
2. Если доступ к платному источнику допустим, сначала получить trial/API доступ Amberdata или Laevitas без прав на торговлю и без передачи Bitget credentials.
3. Скачать отдельно BTC и ETH, сохраняя каждый raw JSON/CSV, URL, параметры, время, response headers и SHA-256.
4. Проверить exact symbol, `exchange=bitget`, settlement timestamps, применённость ставки, полноту 2 332 строк, дубли, пропуски и границы.
5. Сравнить последние доступные события с официальным Bitget API и совпадающие события с Tardis. Расхождения сохранить как отдельный отчёт, а не исправлять выбором удобного значения.
6. Только после независимой проверки обоих символов передать архив штатному строгому валидатору проекта. При любом неполном или неоднозначном поле оставить статус `blocked`.

## Ограничения и безопасность

- Все обращения в этом аудите были read-only.
- Реальные заявки, переводы, настройки аккаунта и API-ключи Bitget не использовались.
- Внешние API-ключи не запрашивались и не сохранялись.
- Код, `status.json`, план и README не изменялись.
- Данные другой биржи, синтетические строки, нули и прогнозные ставки не использовались как замена пропускам.
- SHA-256 зафиксирован для свежих raw probes; сами временные raw capture-файлы не являются частью репозитория и не должны считаться принятым архивом без повторного сохранения в согласованный каталог эксперимента.
