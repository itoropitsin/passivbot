# Независимая проверка архива funding rate Bitget

Дата проверки: **17 августа 2026 года**, Europe/Amsterdam. Первичные публичные HTTP-проверки выполнены **2026-08-17T16:47:02Z–16:47:04Z**; повторный probe после нового поиска выполнен **2026-08-17T21:37:49Z–21:37:51Z**.

Проверены только публичные страницы документации и публичные GET-запросы. API-ключи, регистрации, платные запросы, реальные заявки, переводы и изменения аккаунтов не выполнялись.

## Итоговый verdict

**`rejected`** — сейчас нет доступного и проверенного полного архива применённых funding rate для обоих символов `BTCUSDT` и `ETHUSDT` за `2024-07-01T00:00:00Z..2026-08-17T00:00:00Z`.

Ни один источник не имеет verdict `accepted`:

- официальные публичные Bitget endpoints заканчиваются на rolling-архиве примерно за 90 дней;
- Amberdata и Laevitas документируют подходящие маршруты, но без ключа данные не выдаются, а получить и проверить архив без пользовательского разрешения нельзя;
- Tardis имеет публичные данные для этих символов только с `2024-11-08`, поэтому не покрывает начало целевого окна.

Исторический отбор и backtest, в котором funding влияет на результат, по-прежнему нельзя считать допущенными.

## Границы и критерий полноты

Использована та же граница, что и в плане: конец — ровно `2026-08-17T00:00:00Z`, а не конец календарного дня. При шаге 8 часов это **2 332 ожидаемых события на символ**.

Источник можно было бы принять только при одновременном выполнении следующих условий:

1. Bitget, продукт USDT perpetual и точный символ `BTCUSDT` или `ETHUSDT`.
2. Применённая ставка и точное время расчёта в UTC.
3. Все 2 332 точки с шагом 8 часов, без пропусков, дублей, чужих символов и будущих строк.
4. Сохранены исходные ответы, URL, параметры, время запроса и SHA-256.
5. Есть разрешение на локальное хранение и использование данных в backtest.
6. Пересечение с официальным Bitget API проверено независимо.

## Сверка с предыдущим отчётом

Проверенный файл: [funding_archive_source_audit_2026-08-17.md](funding_archive_source_audit_2026-08-17.md).

Предыдущий отчёт был корректен по главному выводу: полного архива не найдено. Число доступных строк на движущейся границе изменилось. В предыдущей проверке было 269 строк внутри фиксированного окна; в этой проверке осталось 268. Это ожидаемо для rolling history: новые события добавляются в конец, а самые старые удаляются. Общее число строк в ответах осталось 270, но две последние строки уже лежат после фиксированной границы `2026-08-17T00:00:00Z`.

Таким образом, новый результат не открывает data gate и, наоборот, подтверждает отсутствие стабильного полного архива.

## Проверка локальных raw-captures

В `analysis_reports/crypto_bot_roadmap/inputs/` найдены локальные публичные captures Bitget, на которые ссылаются validation reports:

| Файл | Размер | Raw SHA-256 | Funding rows | Диапазон funding | Symbol | Статус capture |
|---|---:|---|---:|---|---|---|
| `bitget_btc_public_execution_inputs_2024-07-01_to_2026-08-17-repaired-pagination-v2.json` | 5 351 520 bytes | `814551529b111f60be06f3997cebbf2002a8e4292ad551e1dfa938c9ba3cc9fd` | 270 | 2026-05-19 08:00 .. 2026-08-17 00:00 | только BTCUSDT | `collected_public_only` |
| `bitget_eth_public_execution_inputs_2024-07-01_to_2026-08-17.json` | 5 244 876 bytes | `8dee9f7252663355e43c672a838b34314470a69ee62d67462755c4e75f43e75f` | 270 | 2026-05-19 08:00 .. 2026-08-17 00:00 | только ETHUSDT | `collected_public_only` |

Проверки этих файлов:

- 270 уникальных timestamps на символ;
- некорректных funding rows: `0`;
- чужих symbols: `0`;
- ошибок collector’а: `{}`;
- `trading_operations_count=0`;
- синтетических строк не добавлено;
- внутри наблюдаемого диапазона шаг ровно 8 часов.

При этом в самом raw capture поле качества funding говорит только о непрерывности **между наблюдаемыми границами** (`complete_between_observed_bounds=true`). Оно не доказывает покрытие запрошенного окна. Отдельные validation reports корректно блокируют эти же файлы: `expected_rows=2332`, `actual_rows=270`, `missing_rows=2062`, `complete_requested_window=false`.

Файлы `bitget_btc_funding_archive_normalized_2026-08-17-v2.json` и `bitget_eth_funding_archive_normalized_2026-08-17-v2.json` содержат те же 270 строк и не расширяют покрытие. Их SHA-256: `e578c5efb45503f53a9556d3c52fc3a7ec0ac922a79ceaa44492ef92f78af591` и `ad2157add3ff1a9877e1c30a7171166f28157f5d3834cac0e475cb28444770e5` соответственно.

В локальном каталоге не найдено отдельного полного raw-архива Tardis, CoinAnk, Amberdata или другого внешнего источника. `funding_archive_probe_2026-08-17.json` также содержит `status=no_acceptable_complete_archive_found`, `synthetic_rows_added=0` и `trading_operations_count=0`.

## Свежая проверка официальных Bitget endpoints

Официальная документация подтверждает следующие контракты:

- [UTA v3: Get Funding Rate History](https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate) — `/api/v3/market/history-fund-rate`, `cursor` — номер страницы, максимум 100; `limit` — максимум 100; время передаётся в миллисекундах.
- [Classic v2: Get Historical Funding Rates](https://www.bitget.com/api-doc/classic/contract/market/Get-History-Funding-Rate) — `/api/v2/mix/market/history-fund-rate`, `pageNo` и `pageSize`, максимум 100 строк на страницу.
- [Bitget History data download](https://www.bitget.com/data-download) перечисляет свечи, сделки и глубину стакана для futures, но отдельного публичного download-архива funding rate там нет.

Команды выполнялись с точной ISO-границей, чтобы не активировать поведение collector’а `end_of_day` для даты без времени:

```bash
./.venv/bin/python -m tools.bitget_funding_horizon_probe \
  --symbol BTCUSDT \
  --category USDT-FUTURES \
  --start 2024-07-01T00:00:00Z \
  --end 2026-08-17T00:00:00Z \
  --interval-hours 8 \
  --max-pages 100 \
  --output /tmp/bitget-funding-collector-followup/BTCUSDT.json

./.venv/bin/python -m tools.bitget_funding_horizon_probe \
  --symbol ETHUSDT \
  --category USDT-FUTURES \
  --start 2024-07-01T00:00:00Z \
  --end 2026-08-17T00:00:00Z \
  --interval-hours 8 \
  --max-pages 100 \
  --output /tmp/bitget-funding-collector-followup/ETHUSDT.json
```

Обе команды завершились статусом `blocked` и кодом возврата 2. Результат collector’а:

| Endpoint | Symbol | Страницы | Всего строк | Самая старая строка | Самая новая строка | Ожидалось в окне | В окне | Пропущено | После границы | Дубли в нормализованном выводе | Интервальные gaps |
|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|
| UTA v3 | BTCUSDT | `100,100,70,0` | 270 | 2026-05-20 00:00 | 2026-08-17 16:00 | 2 332 | 268 | 2 064 | 2 | 0 | 0 |
| Classic v2 | BTCUSDT | `100,100,70,0` | 270 | 2026-05-20 00:00 | 2026-08-17 16:00 | 2 332 | 268 | 2 064 | 2 | 0 | 0 |
| UTA v3 | ETHUSDT | `100,100,70,0` | 270 | 2026-05-20 00:00 | 2026-08-17 16:00 | 2 332 | 268 | 2 064 | 2 | 0 | 0 |
| Classic v2 | ETHUSDT | `100,100,70,0` | 270 | 2026-05-20 00:00 | 2026-08-17 16:00 | 2 332 | 268 | 2 064 | 2 | 0 | 0 |

В каждом ответе был HTTP `200` и Bitget code `00000`. Пустая четвёртая страница завершила пагинацию. Дополнительный запрос страницы 100 также вернул HTTP `200`, `code=00000` и пустой список для обоих символов и обоих endpoints.

Примеры точных публичных запросов:

```bash
curl -sS \
  'https://api.bitget.com/api/v3/market/history-fund-rate?category=USDT-FUTURES&symbol=BTCUSDT&limit=100&cursor=100'
# {"code":"00000","msg":"success","requestTime":1786985194061,"data":{"resultList":[]}}

curl -sS \
  'https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol=BTCUSDT&productType=USDT-FUTURES&pageSize=100&pageNo=100'
# {"code":"00000","msg":"success","requestTime":1786985194380,"data":[]}
```

SHA-256 raw body для свежей страницы 100:

| Endpoint | BTCUSDT | ETHUSDT |
|---|---|---|
| UTA v3 | `a9b1c46b707c0d86ea9e0b399680dab8ab51df543ad2985ded9279272943c789` | `597bf518e30248578a393981001712786c335610e76c89c2c0bdc9db3d502be1` |
| Classic v2 | `89c2bcc4787a9a128a22cf79f7a5c0902d216c43b835bf283ba1f8023eef14c5` | `7d558086659bb75cfb6596305707d23055688554014af8bf8f34e0bc46c18493` |

### Независимая проверка raw строк

Помимо результата collector’а, свежие ответы страниц 1–4 были разобраны напрямую, без его нормализации. Для каждого сочетания endpoint/symbol получено 270 строк:

- некорректных timestamps или rates: `0`;
- чужих символов: `0`;
- повторяющихся timestamps: `0`;
- строки внутри наблюдаемого rolling-диапазона идут с шагом 8 часов без gaps;
- в целевое окно попадает 268 строк, две строки находятся после его конца.

SHA-256 raw body страниц 1–4:

| Symbol | Endpoint | Page 1 | Page 2 | Page 3 | Page 4 |
|---|---|---|---|---|---|
| BTCUSDT | UTA v3 | `36ff13a8a0a5f36de1e306f67b65aa4b2501c79a40c5044664c6ba8ed14f6f60` | `12b0810f60f06839b3dad8997a8e47eed43072e842f3c64988220cda5f152598` | `64fd1a69bf8fb09444af50447c372dea14659b880f4cb0d4d220bb711e63cda5` | `0e87390a375663c0150885a1699c9e213639f9e9ad49f00b96218fa18f2e1f4a` |
| ETHUSDT | UTA v3 | `360a5734d1087e713a809a291e492f9c3fa4d3d26d970d91c3b61890d0b57c95` | `0060452b2793b0b0a63c4c3656e6f1692484be843a62d3df85b5c5be290242d9` | `c542b720300c84ee39ebdf4b511fc666e30392824e9925569aa1d1a8fb1116ca` | `79a8942291b84410d43320cb8ba46c5001892fe2e5384c0a04d62e6fec6637f6` |
| BTCUSDT | Classic v2 | `897dce349877fdd9707c304ccc604daf91d594ed73e19710ccefac69776dc356` | `b0bf0bab81c38d2d8505840123c7354c52a8692180cb0fb9cff11bf44cb70d21` | `03912c6740621de8df6a50fefc7c6edcfd9303bc7b3dd2a971ea77202c82c7fb` | `bf9579ddaf125a88299e97b9d806ce1a67d343c1ca4a55a85d4563b7609945e3` |
| ETHUSDT | Classic v2 | `2b42063015918ed22cb685a575da045603da72010d257a5158a615300095dc2c` | `ef154acce929c1ffc6d60b9737fe049f75865dcb425b7408129e9a761b1f9465` | `dc8536732d2c423ed65938b913766a7ac6d3f163028e2d753b1359e0e82cd0b2` | `6f798086659bb75cfb6596305707d23055688554014af8bf8f34e0bc46c18493` |

## Проверка public collectors

Проверены следующие файлы:

- `src/tools/bitget_funding_horizon_probe.py`;
- `src/research/funding_quality.py`;
- `src/tools/bitget_public_execution_inputs.py`.

Что работает правильно:

- UTA v3 и Classic v2 запрашиваются раздельно, поэтому один endpoint не выдаётся за независимое подтверждение другого.
- Запрашивается точный символ и `USDT-FUTURES`.
- Collector не синтезирует строки и выставляет `blocked`, если полный requested window не подтверждён.
- В текущем запуске он корректно показал `2 064` пропущенных строк и `2` будущих относительно фиксированной границы.

Ограничения, которые важно исправить до принятия будущего архива:

1. В `bitget_funding_horizon_probe.py` строки с нечисловым timestamp или rate отбрасываются до вызова `validate_funding_rows`. Поэтому поле `invalid_rows` внутри отчёта collector’а не является полным контролем исходного ответа. В свежем raw capture некорректных строк не было, поэтому это не меняет текущий verdict.
2. Перед проверкой качества collector сворачивает строки по timestamp в словарь. Дубли с одним timestamp могут быть скрыты. В независимом разборе raw страниц сейчас найдено `0` таких дублей, но будущий принятный архив должен проверяться до дедупликации.
3. `response_sha256` в horizon probe — SHA-256 канонически сериализованного разобранного JSON. Это не raw-body hash и не включает HTTP-заголовки. Для воспроизводимого архива нужно сохранять исходное тело ответа, URL, параметры, заголовки и их хэши.
4. `_parse_timestamp(..., end_of_day=True)` превращает дату без времени в конец календарного дня. Для данного аудита использована полная ISO-граница, совпадающая с планом и ожидаемыми 2 332 событиями.

Хэши проверенных локальных входов этого аудита:

```text
analysis_reports/crypto_bot_roadmap/inputs/funding_archive_source_audit_2026-08-17.md
1209d6703f6884d8c2d558173a222a7d7af37fa01d0af4da1ce8e2fa5b645369

src/tools/bitget_funding_horizon_probe.py
a676670555b01ec3fd0f51f72054c435f967c0144e19071633e7a12cc45853e2

src/research/funding_quality.py
d428e7c586823e4512085c3f8c9b33aa2df1d668a23ab66a641dcaf45053f3e5

src/tools/bitget_public_execution_inputs.py
cb2594c27f5d087db6bf5a1bb0e39acb33b4f367e610a5a32381e42b59205e5b
```

## Проверка внешних кандидатов без ключей

### Amberdata — `conditional`

Официальная документация:

- [Market Data Coverage](https://docs.amberdata.io/data-dictionary/coverage/exchange-coverage);
- [Historical Funding Rates](https://docs.amberdata.io/http/market/futures-funding-rates);
- [Batch Historical Funding Rates](https://docs.amberdata.io/http/market/futures-batch-funding-rates).

Документация описывает `exchange=bitget`, `rateType=applied`, applied/actual funding fields, ограничение одного диапазона в 731 день и обязательный заголовок `x-api-key`. Для целевого окна потребовались бы как минимум два запроса.

Запросы без ключа:

```bash
curl -sS \
  'https://api.amberdata.com/markets/futures/funding-rates/BTCUSDT?exchange=bitget&startDate=2024-07-01T00:00:00Z&endDate=2026-08-17T00:00:00Z&timeFormat=milliseconds&rateType=applied'
# HTTP 403
# {"message":"Forbidden"}

curl -sS \
  'https://api.amberdata.com/markets/futures/funding-rates/ETHUSDT?exchange=bitget&startDate=2024-07-01T00:00:00Z&endDate=2026-08-17T00:00:00Z&timeFormat=milliseconds&rateType=applied'
# HTTP 403
# {"message":"Forbidden"}
```

Оба ответа имели raw SHA-256 `12a22880bc2e59f8278b4a5e547567f0aa14d020ea456598267fa00208cfebc5`.

Источник остаётся только `conditional`: документация выглядит подходящей, но ни одной строки BTC/ETH из целевого окна не удалось получить и проверить публично. Никакой ключ не запрашивался.

### Laevitas — `conditional`

Официальная документация:

- [Historical perpetual funding](https://docs.laevitas.ch/future/historical);
- [Historical derivatives and instruments](https://docs.laevitas.ch/derivs/historical);
- [API access](https://docs.laevitas.ch/).

Документация описывает маршрут `historical/futures/perpetual_funding/{currency}/{market}` и отдельный settlement funding route; API key доступен только при соответствующем доступе к API.

Публичные запросы без ключа:

```bash
curl -sS \
  'https://api.laevitas.ch/historical/futures/perpetual_funding/BTC/BITGET?start=2024-07-01&end=2026-08-17&limit=144&page=1'
# HTTP 400
# {"status":400,"message":"Api Key is Required"}

curl -sS \
  'https://api.laevitas.ch/historical/futures/perpetual_funding/ETH/BITGET?start=2024-07-01&end=2026-08-17&limit=144&page=1'
# HTTP 400
# {"status":400,"message":"Api Key is Required"}

curl -sS \
  'https://api.laevitas.ch/historical/futures/settlement_funding_rate/BITGET/BTCUSDT?start=2024-07-01&end=2026-08-17&limit=144&page=1'
# HTTP 400
# {"status":400,"message":"Api Key is Required"}
```

Все три ответа имели raw SHA-256 `dcb2ffeb9f6ea8b712cb4f770a56c892e1b9bf01d9242ee899fe5a182d75c12f`.

Источник остаётся `conditional`: требуется получить разрешённый доступ, проверить точный symbol mapping, applied/settlement semantics, полноту 2 332 строк и возможность сохранять raw data. Внешний ключ не запрашивался.

### Tardis — `rejected` для полного окна

Официальные страницы:

- [Bitget Futures historical data](https://docs.tardis.dev/historical-data-details/bitget-futures);
- [CSV data types](https://docs.tardis.dev/downloadable-csv-files/data-types);
- [public exchange metadata](https://api.tardis.dev/v1/exchanges/bitget-futures).

Свежий публичный metadata-запрос:

```bash
curl -sS 'https://api.tardis.dev/v1/exchanges/bitget-futures'
```

HTTP `200`. В ответе:

```json
{
  "availableSince": "2024-11-08T00:00:00.000Z",
  "BTCUSDT": {"type": "perpetual", "availableSince": "2024-11-08T00:00:00.000Z"},
  "ETHUSDT": {"type": "perpetual", "availableSince": "2024-11-08T00:00:00.000Z"}
}
```

Raw SHA-256 полного metadata-ответа этой проверки: `8bf8fe9908f9be99a4e1906339fe3b7c5f7803c171fecb8232a7f58e2549f9e2`.

Документация Tardis объясняет, что `funding_timestamp` — ближайшее будущее событие относительно строки, а `funding_rate` может изменяться до события; применённой считается последняя ставка непосредственно перед `funding_timestamp`. Это пригодно для отдельной реконструкции, но не превращает источник в settlement archive. Главное ограничение — первые примерно 130 дней целевого окна отсутствуют: архив начинается 8 ноября 2024 года.

Verdict: `rejected` для полного окна; возможен только partial/cross-check источник для периода с 2024-11-08.

## Повторный поиск бесплатного архива

### CoinAnk — `rejected` как бесплатный источник

В поиске найден официальный [CoinAnk API documentation](https://docs.coinank.com/docs/api/version-1.html) с маршрутом [Historical Settlement Funding Rate](https://s.apifox.cn/apidoc/shared/c31de8d5-fb5f-4bb1-a9fa-f9cc476e4354/api-214005326). Документация помечает этот метод как `VIP1`; пример запроса требует заголовок `apikey`.

Проверены публичные GET-запросы без ключа для обоих символов:

```bash
curl -sS \
  'https://open-api.coinank.com/api/fundingRate/hist?exchange=Bitget&symbol=BTCUSDT&startTime=1719792000000&endTime=1786924800000&size=1000'
# HTTP 402
# {}

curl -sS \
  'https://open-api.coinank.com/api/fundingRate/hist?exchange=Bitget&symbol=ETHUSDT&startTime=1719792000000&endTime=1786924800000&size=1000'
# HTTP 402
# {}
```

Оба ответа имеют raw SHA-256 `44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a`. Строки и покрытие получить без платного/VIP доступа нельзя.

Verdict: `rejected` в рамках бесплатного доступа; источник не может закрыть data gate без отдельного разрешения на платный доступ.

### Kaggle dataset `ramkrijal/perpetuals-funding-rates` — `rejected`

Публичный metadata endpoint: [Kaggle API dataset view](https://www.kaggle.com/api/v1/datasets/view/ramkrijal/perpetuals-funding-rates).

Команда:

```bash
curl -sS \
  'https://www.kaggle.com/api/v1/datasets/view/ramkrijal/perpetuals-funding-rates'
```

Проверенный ответ: `title=Crypto Perpetuals Funding Rates`, `lastUpdated=2023-03-16T00:19:32.707Z`, `currentVersionNumber=2`, `totalBytes=1 068 391`; в описании нет подтверждения Bitget как биржи. Raw SHA-256 metadata-ответа: `83af4119928a01bf72e42f21aea1cbc1c8f8e4061e3d828a8667236518305e49`.

Источник не может содержать полный ряд до 2026-08-17 и не доказывает venue-level mapping для Bitget.

Verdict: `rejected`.

### CryptoHFTData — `rejected` для полного окна

Актуальная публичная страница [Bitget Historical Funding Rate Data](https://www.cryptohftdata.com/datasets/bitget-funding-rate-data) указывает hourly Parquet/Zstd-файлы, бесплатный sample/rate-limited access и `History start: 2025-07-15`. В схеме есть текущий `funding_rate` и `next_funding_time`, но начало архива позже требуемого `2024-07-01`.

Verdict: `rejected` для полного окна; подходит только для частичного сравнения позднего периода.

### Публичные GitHub collectors — `rejected` как архивы

- [funding-rate-tools](https://github.com/jooray/funding-rate-tools) хранит/получает историю только для Binance, Hyperliquid и Bybit; Bitget в списке поддерживаемых исторических источников отсутствует.
- [funding-collector](https://github.com/clementslowik/funding-collector) перечисляет Bitget, но использует current ticker/current funding endpoints и сам объясняет, что историю нужно накопить после запуска. Готового архива с 2024-07-01 там нет.

Verdict: `rejected`; это инструменты будущего накопления, а не бесплатные полные Bitget archives.

## Повторный официальный probe после нового поиска

Команды повторены без изменения кода и без записи в репозиторий:

```bash
./.venv/bin/python -m tools.bitget_funding_horizon_probe \
  --symbol BTCUSDT --category USDT-FUTURES \
  --start 2024-07-01T00:00:00Z --end 2026-08-17T00:00:00Z \
  --interval-hours 8 --max-pages 100 --output /tmp/bitget-funding-repeat/BTCUSDT.json

./.venv/bin/python -m tools.bitget_funding_horizon_probe \
  --symbol ETHUSDT --category USDT-FUTURES \
  --start 2024-07-01T00:00:00Z --end 2026-08-17T00:00:00Z \
  --interval-hours 8 --max-pages 100 --output /tmp/bitget-funding-repeat/ETHUSDT.json
```

Обе команды вернули `status=blocked`. Для UTA v3 и Classic v2, отдельно для BTCUSDT и ETHUSDT:

| Время capture | Страницы | Всего строк | Самая старая | Самая новая | Ожидалось | В окне | Пропущено | После границы |
|---|---|---:|---|---|---:|---:|---:|---:|
| 21:37:49Z / BTCUSDT | `100,100,70,0` | 270 | 2026-05-20 00:00 | 2026-08-17 16:00 | 2 332 | 268 | 2 064 | 2 |
| 21:37:51Z / ETHUSDT | `100,100,70,0` | 270 | 2026-05-20 00:00 | 2026-08-17 16:00 | 2 332 | 268 | 2 064 | 2 |

UTA v3 и Classic v2 дали одинаковые результаты. Повторная проверка не обнаружила расширения публичного горизонта и не изменила verdict.

## Остальные источники из предыдущего отчёта

Статусы CoinGlass, Coinalyze, CoinAPI, CryptoHFTData и university dataset в этой проверке не повышались. Они не дают одновременно доказанного symbol-level raw ряда Bitget, полного покрытия, применённых settlement events и разрешения на воспроизводимый локальный backtest. Оснований менять их прежние решения нет.

## Решение для процесса

1. Не менять текущий `data` gate: он остаётся не пройденным.
2. Не запускать историческую оптимизацию и не выбирать кандидата до получения полного архива funding для обоих символов.
3. Если позже появится разрешённый внешний архив, сначала сохранить raw ответы отдельно для BTC и ETH, затем прогнать штатный строгий валидатор **до** дедупликации и сравнить пересечение с официальным Bitget API и Tardis.
4. При любом пропуске, неоднозначном времени settlement, прогнозной ставке вместо applied или неясных условиях хранения оставить verdict `rejected` либо `conditional`, но не `accepted`.

В рамках этой проверки изменён только этот follow-up-файл. `src`, `tests`, `status.json`, план, README и настройки Bitget не изменялись.
