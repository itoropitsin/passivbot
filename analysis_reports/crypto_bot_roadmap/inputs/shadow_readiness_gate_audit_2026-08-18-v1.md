# Read-only аудит shadow readiness gate — 18 августа 2026 года

Дата и время: 18 августа 2026 года, 14:48 UTC.

Аудит выполнил Luna Max в read-only режиме. Файлы реализации, `status.json`,
работающие процессы и настройки Bitget не изменялись. На момент проверки
`public-stream-process-24h-2026-08-18-v4` ещё выполнялся; `result.json` и
`process_supervisor.json` отсутствовали. Поэтому gate не запускался.

## Что подтверждено

- Изолированный набор проверок gate и shadow: `53 passed`.
- v4 продолжал работать, `fixture_mode=false`.
- Для принятия v4 нужны `result.json`, `events.ndjson`, текущие public
  `market_profile.json`, конфигурация и snapshots; повторный запуск должен
  использовать новый уникальный `run_id`.
- Требования readiness включают 24 часа wall-clock, 20 плановых заявок, пять
  driver/process restart, recovery не более 60 секунд, три deterministic replay,
  ноль внешних/private/trading/transfer операций, совпадение хэшей и успешный
  source-gap check.

## Найденные дефекты

### 1. Неправильный тип `provenance.artifacts` может пройти

`src/tools/research_gates.py` сначала вычисляет `bool(artifacts)`, а проверяет
содержимое только если значение является словарём. Непустая строка или список
может поэтому получить `shadow_artifact_hashes_match=true` без проверки файлов.

Исправление после завершения v4:

- требовать непустой mapping;
- проверять каждый элемент и путь/хэш;
- требовать evidence для events, snapshots и process supervisor.

Обязательная регрессия: `test_shadow_gate_rejects_non_mapping_artifact_provenance`.

### 2. Переданный market profile не связан с shadow verdict

`--market-profile` читается, но `shadow_readiness` не добавляет проверки его
public-only статуса и соответствия переданному файлу. Неверный или private
profile может остаться без отдельного gate.

Исправление после завершения v4:

- проверять public-only profile фактически переданного файла;
- связывать его хэш с manifest/run evidence;
- отклонять отсутствующий, private или неподтверждённый profile.

Обязательная регрессия: `test_shadow_readiness_rejects_invalid_or_unbound_market_profile`.

### 3. Event-файл не является обязательным на уровне CLI

`shadow_readiness` отсутствует в `EVENT_REQUIRED_STAGES`. При отсутствии
`--events` run bundle может получить пустой `events.jsonl.gz`, хотя readiness
должен требовать непустой event evidence.

Исправление после завершения v4:

- сделать `--events` обязательным для shadow readiness;
- отклонять отсутствующий или пустой файл до создания принятого run;
- добавить проверку непустого event input.

Обязательная регрессия: `test_shadow_readiness_requires_nonempty_event_input`.

## Порядок исправления

1. Не менять implementation code или применяемую конфигурацию до окончания v4.
2. Сохранить v4 только как partial/engineering evidence, независимо от его
   числовых показателей.
3. После окончания v4 внести три исправления отдельным пакетом, запустить
   targeted tests и пересобрать baseline/fingerprint.
4. Создать новый public shadow на новом hash; старый v4 не передавать как
   окончательный readiness result.
5. Только после нового результата последовательно запустить
   `research_gates --stage shadow_readiness`.

Эти дефекты не открывают data gate: полный applied-settlement funding archive
по-прежнему отсутствует.
