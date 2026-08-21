# Аудит публичного приложения к работе Petrishko и frozen funding mirror

Дата проверки: **18 августа 2026 года**, Europe/Amsterdam. Проверка read-only.

## Зачем проверялся источник

Поисковая выдача указывала на работу Aleksei Petrishko, где описаны 10 040 записей Bitget funding за период 2021-05—2025-12. Нужно было установить, доступен ли сам набор данных, а не только его описание.

## Проверенные публичные объекты

- Репозиторий работы: [`DSpace item`](https://dspace.vut.cz/items/0c45b1d0-a8d3-4a43-9941-fc71ef4f84fd/full).
- Машиночитаемые метаданные item: `https://dspace.vut.cz/server/api/core/items/0c45b1d0-a8d3-4a43-9941-fc71ef4f84fd`.
- Bundle `ORIGINAL`: `8789a15d-820b-478a-a328-13bbd3c8ef3d`.
- Архив приложения: [`appendix-1.zip`](https://dspace.vut.cz/server/api/core/bitstreams/15de3fa6-8826-4574-8f3e-77c8fd6ac54b/content).
- SHA-256 скачанного архива: `0c2057a2cb8a1b43d8f925c54b72481e6d6f72b18c98e827428b4c95e772cb7c`.
- Размер архива: `7 312 242` байта.

Архив содержит 775 файлов исходного кода, тестов и документации. В нём нет `*.csv`, `*.parquet`, SQLite-базы, pickle/Feather-файлов или другого raw market/funding dataset. Каталог `implementation/static-data/` содержит только `.gitkeep`.

## Что заявляет документация работы

README приложения указывает:

- frozen cache должен содержать `funding.parquet` размером около 0,4 MB;
- funding описан как `Coinglass-style 8 h funding settlements`;
- default mirror: `https://api.d3lta.dev/static/d3lta_data_v2.tar.gz`;
- ожидаемый SHA-256 mirror в `scripts/fetch_thesis_data.py`: `9f2d7d1614fcbcd90cddf4c57240506fcc713eb410f11baafda425c7f8b62bc1`.

Отдельно thesis metadata сообщает о 10 040 Bitget funding records за 2021-05—2025-12 и называет способ получения через CCXT `fetchFundingRateHistory`. Это подтверждает описание эксперимента, но не даёт raw rows, их хэша, точного ответа Bitget API или отдельной лицензии на redistribution.

## Проверка mirror

18 августа 2026 года read-only `HEAD` к `https://api.d3lta.dev/static/d3lta_data_v2.tar.gz` вернул:

- HTTP `530` от Cloudflare;
- `Content-Length: 17` для страницы ошибки;
- tarball не скачан.

Поэтому frozen cache нельзя проверить по заявленному SHA и нельзя извлечь из него Bitget BTCUSDT funding rows.

## Проверка альтернативных hostname

Чтобы исключить ошибку только одного адреса, 18 августа 2026 года проверены также:

- `https://d3lta.dev/static/d3lta_data_v2.tar.gz` — HTTP 307 на `www.d3lta.dev`;
- `https://www.d3lta.dev/static/d3lta_data_v2.tar.gz` — HTTP 200, но `Content-Disposition` указывает `index.html`, а ответ имеет `Content-Type: text/html`, то есть это HTML-приложение, а не tarball;
- `http://api.d3lta.dev/static/d3lta_data_v2.tar.gz` — HTTP 530;
- `https://api.d3lta.dev/static/d3lta_data_v2.tar.gz` — HTTP 530.

HTML главной страницы не содержит ссылки на публичный GitHub/GitLab repository или альтернативный data mirror. DNS-проверка `www` указывает на Vercel, а `api` — на Cloudflare; это не даёт доступа к отсутствующему origin-файлу.

## Вердикт для roadmap

**Источник не принят как funding archive.** Причины:

1. raw archive с funding rows недоступен;
2. описание `Coinglass-style` само по себе не доказывает venue/symbol semantics и applied-settlement semantics;
3. ожидаемый mirror SHA не проверен, потому что mirror недоступен;
4. appendix не содержит данные и не заменяет источник.

Нельзя подставлять данные из thesis description, использовать другой exchange, заполнять пропуски нулями или считать 10 040 заявленных строк доказательством покрытия. До появления доступного raw archive data gate остаётся закрытым, а historical selection не запускается.

## Что делать при восстановлении доступа

Скачать mirror в отдельный raw-файл, проверить SHA `9f2d7d1614fcbcd90cddf4c57240506fcc713eb410f11baafda425c7f8b62bc1`, извлечь только `funding.parquet`, проверить фактические поля `exchange`, `symbol`, timestamps и applied-settlement semantics, затем прогнать через существующий `bitget-funding-promote`. Никаких overwrite frozen input и автоматического допуска до успешной проверки.
