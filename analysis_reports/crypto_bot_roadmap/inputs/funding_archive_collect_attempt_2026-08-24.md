# Попытка собрать полный Bitget funding archive

Дата проверки: **24 августа 2026 года**, `09:07 UTC` (Europe/Amsterdam).

Цель: фактический ряд applied settlements для `BTCUSDT` и `ETHUSDT`, Bitget USDT perpetual, окно `[2024-07-01T00:00:00Z, 2026-08-17T00:00:00Z]`, шаг 8 часов, **2 332** точки на символ. Read-only: публичные GET, без ключей, входа, заявок, переводов и покупок данных.

Машинный снимок: [`funding_archive_collect_attempt_2026-08-24.json`](funding_archive_collect_attempt_2026-08-24.json).

## Вердикт

**Полный архив собрать нельзя.** Публичные официальные endpoint по-прежнему отдают только rolling 270 строк. В фиксированное окно сейчас попадает **248 / 2 332** на каждый символ (покрытие `0.10635`). Это меньше, чем 18 августа (`266 / 2 332`): горизонт сдвинулся вперёд, старые точки выпали.

Frozen execution input не перезаписывался. Неполные 248 строк не продвигались через `bitget-funding-promote`. Синтетика, нули и ставки другой биржи не добавлялись. Data / historical / paper / live остаются закрыты.

## Что проверено сегодня

### Официальный UTA v3

`GET https://api.bitget.com/api/v3/market/history-fund-rate?category=USDT-FUTURES&symbol={BTCUSDT|ETHUSDT}&limit=100&cursor=N`

| Symbol | Страницы | Всего | В окне | Oldest | Newest |
|---|---|---:|---:|---|---|
| `BTCUSDT` | `100, 100, 70, 0` | 270 | 248 | `2026-05-26T16:00:00Z` | `2026-08-24T08:00:00Z` |
| `ETHUSDT` | `100, 100, 70, 0` | 270 | 248 | `2026-05-26T16:00:00Z` | `2026-08-24T08:00:00Z` |

Страница 4 пустая. Дублей timestamps нет. Параметры `startTime`/`endTime` на Classic v2 игнорируются: страница 1 всё равно текущий rolling-ряд.

### Официальный Classic v2 и V1

Classic v2 дал те же `100 / 70 / 0` на страницах 1 / 3 / 4. V1 `/api/mix/v1/market/history-fundRate` — `code=30032` decommissioned.

### Официальная страница загрузки

[History data download](https://www.bitget.com/data-download) по-прежнему перечисляет только свечи, сделки и глубину. Funding-файла нет.

### Веб-интерфейс Bitget

Страница [`/futures/usdt/BTCUSDT`](https://www.bitget.com/futures/usdt/BTCUSDT) показывает текущий funding и countdown. Отдельной выгрузки полного history нет. Страница [`/futures/introduction/funding-rate`](https://www.bitget.com/futures/introduction/funding-rate) — описание, не архив.

### Внешние источники без ключа

| Источник | Результат |
|---|---|
| Tardis `2024-07-01` | HTTP 400: данные `BTCUSDT` только с `2024-11-08` |
| Tardis день не 1-го числа | HTTP 401: без ключа доступен только first-of-month sample |
| Tardis sample `2024-12-01` | HTTP 200, sha256 `47db0d06…` — один день, не окно |
| Amberdata applied | HTTP 403 `Forbidden`, sha256 `12a22880…` |
| Laevitas settlement | HTTP 400 `Api Key is Required`, sha256 `dcb2ffeb…` |
| d3lta thesis mirror `api.` | HTTP 530, 17 байт |
| d3lta `www.` | HTTP 200 HTML, не tarball |
| Coinank indicator без ключа | HTTP 402, тело `{}` |
| Hugging Face search `bitget funding` | пустой список `[]` |
| CryptoStruct | документирует Bitget BTCUSDT с мая 2024, но файлы платные ≈ €1/день |

Ключей Tardis / Amberdata / Laevitas / Coinank / CryptoStruct в среде нет. Покупка не выполнялась.

## Почему 270 строк не архив

Это скользящие ~90 дней, а не фиксированное окно плана. Сегодня в окно до `2026-08-17T00:00:00Z` входят 248 точек; отсутствуют **2 084**. Склейка с frozen 270-строчным снимком не восстанавливает 2024-07…2026-05.

## Что нужно, чтобы закрыть data gate

Один из двух входов, затем проверка через `bitget-funding-archive` и продвижение в **новый** execution-input через `bitget-funding-promote` (frozen не трогать):

1. Пользователь кладёт полный raw-файл applied settlements Bitget на оба символа (или сначала BTC) с правом хранения.
2. Пользователь отдельно разрешает платный источник и даёт ключ/оплату. Условные кандидаты с покрытием окна: Amberdata (`rateType=applied` с 2024-02-02), полный Tardis с 2024-11-08 **не** закрывает июль–начало ноября 2024, CryptoStruct (платные дневные файлы с мая 2024, нужна проверка applied-settlement semantics).

До этого historical optimizer, paper и live не запускать.
