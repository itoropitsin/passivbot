# Bitget BTCUSDT funding archive research

Дата проверки: 17 августа 2026 года, UTC.

## Вывод

Полного проверяемого ряда funding для `BTCUSDT` за `2024-07-01..2026-08-17` в текущем репозитории и через доступные официальные публичные Bitget API не найдено. Код оставлен без изменений. Синтетические строки, нулевые значения и данные другой биржи не добавлялись.

## Проверенные официальные источники

| Источник | Что проверено | Результат |
|---|---|---|
| [UTA v3 Get Funding Rate History](https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate) | `/api/v3/market/history-fund-rate`, `BTCUSDT`, `USDT-FUTURES`, `limit=100`, страницы 1–100 | Свежий read-only probe: `100, 100, 70, 0`; 270 записей, самая старая — `2026-05-19T16:00:00+00:00`; окно не полное |
| [Classic v2 Get Historical Funding Rates](https://www.bitget.com/api-doc/classic/contract/market/Get-History-Funding-Rate) | `/api/v2/mix/market/history-fund-rate`, `pageSize=100`, `pageNo=1..100` | Свежий read-only probe: `100, 100, 70, 0`; 270 записей; окно не полное |
| Архивный [V1 Bitget API reference](https://bitgetlimited.github.io/apidoc/en/mix/) | `/api/mix/v1/market/history-fundRate?symbol=BTCUSDT_UMCBL` | Все проверенные страницы возвращают код `30032`: `The V1 API has been decommissioned` |
| [Bitget History data download](https://www.bitget.com/data-download) | Официальная страница загрузки исторических данных | Для futures перечислены свечи, сделки и глубина стакана; отдельного funding-архива на странице нет |

## Локальные доказательства

- `inputs/bitget_btc_funding_horizon_2026-08-17.json`: оба актуальных публичных endpoint имеют страницы `100, 100, 70, 0`, 270 строк и статус `blocked`.
- `inputs/bitget_btc_funding_archive_validation_2026-08-17-v2.json`: строгий валидатор проверил только 270 фактических Bitget-строк из публичного снимка против 2 332 ожидаемых; пропущено 2 062 расчёта, статус `blocked`.
- `inputs/bitget_btc_funding_archive_normalized_2026-08-17-v2.json`: это нормализованная копия тех же 270 строк, а не полный архив.
- Поиск файлов в репозитории не обнаружил отдельного полного BTCUSDT funding-файла. Найденные материалы — код, публичные снимки, нормализованные копии и отчёты проверки.

## Почему текущий код оставлен

`src/tools/bitget_funding_archive.py` принимает независимый JSON/JSONL/CSV/TSV-архив, считает хэш исходника и блокирует архив при пропуске, дубликате, неверной строке, чужом символе или неполном диапазоне. Оснований ослаблять эту проверку нет. `src/tools/bitget_funding_horizon_probe.py` правильно использует документированную для UTA v3 нумерацию страниц и не подменяет пропуски.

Торговые и аккаунтные операции не выполнялись.

## Точная следующая команда

Когда появится независимый полный файл с фактическими строками Bitget, проверить его нужно так:

```shell
cd /Users/ivan/Documents/Projects-SaaS/Trading/TLI-Setka
.venv/bin/passivbot tool bitget-funding-archive \
  --archive /absolute/path/to/bitget_btcusdt_funding_2024-07-01_2026-08-17.csv \
  --symbol BTCUSDT \
  --start 2024-07-01T00:00:00+00:00 \
  --end 2026-08-17T00:00:00+00:00 \
  --source-kind official_bitget_archive \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_archive_validation_next.json \
  --normalized-output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_archive_normalized_next.json
```

До появления такого файла исторический отбор и переход к следующему data-этапу остаются заблокированными.
