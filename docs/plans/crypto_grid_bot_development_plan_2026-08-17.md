# План развития локального криптовалютного бота: Bitget BTC → портфель

Дата плана: **17 августа 2026 года**. Часовой пояс: Europe/Amsterdam.

Редакция: **1.74 от 24 августа 2026 года**, next-stage после 1.73. Попытка собрать полный funding-архив не удалась: публичный Bitget rolling-ряд снова `100, 100, 70, 0` (270 строк), в фиксированное окно `[2024-07-01T00:00:00Z, 2026-08-17T00:00:00Z]` попадает **248 / 2 332**. Frozen input не перезаписывался, promote не запускался. Канцелярия BL-24/MP-24/RE-24/SR-24 без изменений. Торговая готовность ноль. Нужен файл пользователя или отдельно разрешённый платный источник.

Подробные находки ревью: [crypto_grid_bot_implementation_review_2026-08-17.md](crypto_grid_bot_implementation_review_2026-08-17.md).

Терминология: для входного условия этапа используется английское слово `gate`. В этом плане и связанных рабочих документах используется только `gate`; исходные сообщения форума в архиве не переписываются, чтобы не менять цитируемый источник.

Голые номера вроде `v85` не использовать в новых текстах: один и тот же номер встречается у разных этапов (`market_profile v49` ≠ `risk_grid v49`; NAS exam `v7` ≠ gate `v25`). Исторические `run_id` не переименовывать. В речи и в следующих прогонах:

| Коротко | Этап | `run_id` | Смысл |
|---|---|---|---|
| BL-20 | `baseline` | `baseline-2026-08-20-v85` | historical: полный suite на `d46be477…` |
| MP-20 | `market_profile` | `market-profile-2026-08-20-v49` | historical: публичный BTC Bitget surface |
| RE-21 | `risk_engine` | `risk-engine-2026-08-21-v31` | historical: production live-фильтр ↔ Rust Backtest |
| SR-21 | `shadow_readiness` | `shadow-readiness-2026-08-21-v25` | historical: machine-допуск EX-6 |
| EX-6 | public exam | `public-stream-process-24h-2026-08-19-v6` | 24h запись на NAS, 19 авг; не перезаписывать |
| EX-7 | public exam | `public-stream-process-24h-2026-08-21-v7` | 24h запись на NAS, 21 авг; не перезаписывать |
| BL-24 | `baseline` | `baseline-2026-08-24-rebind` | bound: полный suite на `f5af0643…` |
| MP-24 | `market_profile` | `market-profile-2026-08-24-rebind` | bound: те же public surfaces, что MP-20/v47 |
| RE-24 | `risk_engine` | `risk-engine-2026-08-24-rebind` | bound: `production-risk-evidence-2026-08-24-v1` |
| SR-24 | `shadow_readiness` | `shadow-readiness-2026-08-24-exam7` | bound: machine-допуск EX-7 |

Bound-цепочка BL-24 → MP-24 → RE-24 → SR-24 закрыта. Формат новых `run_id`: `{этап}-{дата}-{роль}`, без голого счётчика. Не `v83`, не `v86`, не `v26`.

## Короткий итог ревью

Цель и риск-пределы не изменены. BTC считается готовым только после historical, 90-дневного paper, 30-дневного технического реала и 90-дневного контролируемого реала. Сейчас подходящего кандидата нет; historical, paper и все реальные этапы закрыты.

- `BL-24` — честный полный suite на `f5af0643…` / `7a8be7892`, `passed`/`fresh`. 2 641 Python собраны, 2 523 прошли, 118 пропущено; 20 fake-live; 145 Rust через `cargotest.sh`; три последовательных progress-dot SHA-256 `9a4a1346…` (совпал с BL-20). BL-20/v84 historical. v83 не существовал.
- `MP-24` — те же public surfaces, что MP-20/v47, `passed`/`fresh`. `RE-24` — production-path `production-risk-evidence-2026-08-24-v1`, 35/35, `passed`/`fresh`; не `p0d_local`. `SR-24` — EX-7, 40/40, admission passed; SR-21/v24 не перезаписаны. `data v47` failed/stale только по funding; `historical v34` blocked; optimizer не запускался.
- BTC NAS `public-stream-process-24h-2026-08-19-v6` сдан. Machine `shadow-readiness-2026-08-20-v24`: 40/40 gates, admission passed, freshness fresh. v23 — historical check-passed/admission-blocked. Mac v5/v21/v22 — historical. Это Long-only transport/recovery exam, не квалификация Long и Short.
- P0-E `data_passport` перенесён точечно из `TLI-Wings/p0e-data-passport-clean`. Frozen feed 270/2332 остаётся `qualifying=false`.
- P0-D production-path перенесён точечно из `TLI-Wings/p0d-production-risk-evidence`: `src/research/risk_evidence.py`, проводка gates/manifest/probe. Kitchen-sink overlay не копировался. `passivbot.py` не менялся. Research-matrix больше не открывает `risk_limits_enforced_in_live_and_tester`.
- `p1-paper` перенесён точечно из `TLI-Wings/p1-paper-execution-calibration`: named mark/index, bound maintenance tiers, evidence writer. Queue/depth в `create_order` не проводён. `qualifying=false`.
- `p1f-timestamps` перенесён точечно: `_integer()` отклоняет `1.9` вместо `int(1.9) -> 1`. Baseline v85 это закрыл.
- Срез 1.63 **не** перезапускал полный Python/Rust baseline. Канон тестового evidence текущего дерева — v85, не v78.
- `--resume-existing-output` изменил восемь `CODE_PATHS` shadow (правило 28) и поэтому сдвинул fingerprint. Freeze на время EX-6 и EX-7 выдержан: hash по-прежнему `b6c53f50…`. SR-24 уже bound; эти восемь файлов всё равно не менять без нового 24h. Document-only правки допустимы. Queue/depth в `TLI-Wings/p1-paper-execution-calibration` не стартовать. Документация и generated reports не входят в implementation fingerprint.
- `risk_grid v49` checks проходили, но после fingerprint-сдвига stale; в окне 1.66 не пересобирался. `risk_contract v55` — failed evidence; `account_risk_surface` не начинался.
- Funding blocker редакции 1.74: публичный полный архив 24 августа не найден. UTA v3 снова `100, 100, 70, 0` (270 строк); в фиксированное окно входят **248 / 2 332** (coverage `0.10635`). Frozen execution input не перезаписывался, `bitget-funding-promote` не запускался. Следующие агенты не повторяют исчерпанные GET без нового источника. Data gate открывает только проверенный полный raw export: файл пользователя или явно разрешённый платный источник, затем `bitget-funding-archive` и `bitget-funding-promote` в новый путь.
- Standalone ETH recorder завершился: запись `eth-public-market-24h-2026-08-18-v1`, wall-clock 4,017 ч, 231 rows, integrity `status=failed` (12 errors на 3 транспортные строки: SSL EOF / timeout / DNS). Funding continuity passed. Имя файла с `24h` не является доказательством длительности. Процесс `67935` мёртв. Не перезапускать ETH и не открывать ETH или portfolio gate.
- Paper-контур имеет WAL, явные funding settlements, bounded partial fills, named mark/index и bound maintenance tiers. `fetch_positions()` больше не подставляет ticker `last` как mark/index и не использует hardcoded 0,5% maintenance. Queue/depth по-прежнему не моделируются в `create_order`; `qualifying=false`. Public input feed исключает settlement без предшествующего mark. Поэтому текущий paper adapter не является доказательством exchange parity и не готов к 90-дневному зачётному периоду.
- Официальная документация Bitget подтверждает isolated futures с 19 мая 2026 года и отдельные Long/Short leverage fields для isolated two-way mode с 9 июня 2026 года. Приватное состояние конкретного субсчёта не проверялось и не должно проверяться до соответствующего этапа и отдельного разрешения.

Ни один существующий сет не является кандидатом на paper или реальную торговлю. Заявки, переводы, смена режима счёта, покупка внешних данных и создание API-ключей не разрешены этой редакцией плана.

## Машинный статус на 20 августа 2026 года

Источник: [`analysis_reports/crypto_bot_roadmap/status.json`](../../analysis_reports/crypto_bot_roadmap/status.json), схема `crypto-grid-roadmap-status-v3`, `updated_at_utc=2026-08-23T22:21:42Z`.

Таблица ниже фиксирует bound срез BL-24/MP-24/RE-24/SR-24. Точные `run_id` всегда берутся из `status.json`.

| Стадия | Статус | Причина или следующий вход |
|---|---|---|
| `baseline` | `passed` / `fresh` | BL-24: полный suite на `f5af0643…`; 2641/2523/118 Python, 20 fake-live, 145 Rust, hash `9a4a1346…`. BL-20 historical |
| `market_profile` | `passed` / `fresh` | MP-24: те же public surfaces, что MP-20/v47. MP-20 historical |
| `account_profile` | `stale` | Classic Account; UTA isolated/hedge не подтверждён, приватный профиль не нужен для historical |
| `data` | `failed` / `stale` | v47: 14/16 checks прошли; funding coverage `0.11578044596912522`, 270/2 332 |
| `risk_engine` | `passed` / `fresh` | RE-24: bound `production-risk-evidence-2026-08-24-v1`; 35/35. RE-21 historical |
| `risk_contract` | `failed` / `stale` | v55: `risk_model_surface_validated=false` |
| `risk_grid` | `passed` / `stale` | `risk-grid-2026-08-18-v49`: локальные checks прошли; не путать с MP-20 |
| `historical` | `blocked` / `stale` | v34: `check_status=failed`; blocker data/funding |
| `account_risk_surface` | `not_started` | не запускался; нужен только перед техническим реальным этапом |
| `shadow_readiness` (machine stage) | `passed` / `fresh` | SR-24: 40/40, admission passed; evidence EX-7. SR-21/v24 historical, не перезаписаны |
| `public shadow` (live evidence) | `completed` | EX-7 `passed`; EX-6 сдан и не перезаписывать; exam-контейнеры не стартовать |
| `paper_runtime` | `blocked` | historical не пройден; 90-дневный период не начинался |
| `technical_live`, `controlled_live` | `blocked` | запрещены до historical, paper и account surface; реальные заявки не выполнялись |
| `portfolio_historical`, `portfolio_paper` | `blocked` | BTC ещё не прошёл обязательные проверки |

Этот статус не выставляется вручную. Любая новая версия кода сначала делает предыдущие `passed`-пакеты `stale`, после чего создаётся новый run-пакет с собственными хэшами.

## Цель и неизменяемые решения

Развивать существующий Passivbot, а не создавать второго бота. Первая цель — BTC/USDT perpetual на Bitget, затем BTC+ETH и только потом другие активы.

- Исследовательский капитал: 1 000 USDT на отдельном субсчёте.
- Long и Short отбираются независимо. Совместный режим проверяется дополнительно и не заменяет независимую проверку сторон.
- Маржа: isolated, hedge mode, USDT как единственный залог, без заимствований.
- Плечо: не более 3×. Оно не увеличивает разрешённый номинал.
- Допустимая просадка: 20%.
- Безусловная остановка без автоматического перезапуска: 25%.
- Отбор: максимальная чистая доходность только среди кандидатов, прошедших все ограничения риска и устойчивости.
- Реинвестирование в первом цикле запрещено. Размер считается от `min(фактический баланс, 1 000 USDT)`.
- Любые переводы, реальные заявки, изменение режима счёта и создание либо изменение API-ключа требуют отдельного явного разрешения пользователя.

Bitget UTA поддерживает isolated futures с 19 мая 2026 года, а раздельное плечо Long/Short в isolated two-way mode — с 9 июня 2026 года. До реальной торговли это должно быть подтверждено чтением фактического состояния выбранного субсчёта и безопасной проверкой сформированных запросов.

- [Описание UTA](https://www.bitget.com/api-doc/uta/intro)
- [Журнал изменений](https://www.bitget.com/api-doc/uta/changelog)
- [Настройка плеча](https://www.bitget.com/api-doc/uta/account/Change-Leverage)

## Рабочие пределы

| Показатель | Нормально | Предупреждение | Обязательное действие |
|---|---:|---:|---|
| Просадка капитала | менее 10% | 10–15% | 15–20%: только закрытие; 20%: аварийное закрытие; 25%: остановка без перезапуска |
| Общий номинал позиций | до 0,60× капитала | 0,60–0,80× | выше 0,80× запрещён |
| Использованная маржа | до 25% | 25–35% | выше 35% запрещена |
| Расстояние до ликвидации | не менее 30% | 20–30% | ниже 20% — только уменьшение; ниже 15% — аварийное закрытие |
| Восстановление капитала | до 30 дней | 30–90 дней | более 90 дней — кандидат отклоняется |
| Отклонение цены исполнения | медиана до 2 б.п. | 95-й процентиль до 10 б.п. | выше 25 б.п. — остановка новых входов |
| Потеря за сутки | до 2% | — | 2% — остановка новых сеток до проверки |
| Расхождение с биржей | 0 | один цикл сверки | более одного цикла — все новые заявки блокируются |

Ограничения сетки:

- не более восьми уровней;
- первый вход — не более 3% расчётного капитала;
- рост следующего уровня — не более 1,25×;
- новый уровень запрещён, если нарушается хотя бы один предел портфеля;
- первый вход, расширение сетки и выход проверяются отдельно;
- TP покрывает комиссии, funding и ожидаемое проскальзывание;
- обычные рыночные заявки запрещены; рыночное закрытие разрешено только аварийному контролю риска;
- Yellow HSL при 10%; Orange HSL при 15%; Red HSL при 20%; окончательная блокировка при 25%;
- не более одного автоматического перезапуска за сутки.

## Исправленная схема зависимостей

Приватный профиль счёта больше не должен блокировать работу с публичными данными, тестером и paper-контуром. Он нужен только перед первым реальным техническим запуском.

| Контур | От чего зависит | Что разрешает |
|---|---|---|
| Воспроизводимая база | исходный код и окружение | разработку и тесты |
| Публичный профиль рынка | база | сбор параметров BTC/ETH без ключа |
| Исторические данные | база + публичный профиль рынка | исследовательские backtest-запуски |
| Риск-движок | база + публичный профиль рынка | одинаковое решение настоящего production live-фильтра и настоящего Rust `Backtest` на одном снимке; research-policy матрица остаётся отдельной регрессией |
| Риск-сценарии | база + риск-движок | подтверждение ограничений без зависимости от funding-архива |
| Готовность strategy shadow | база + исправный paper-движок + риск-движок | короткий и затем семидневный предварительный запуск без денег |
| Исторический отбор | полные данные + риск-движок + риск-сценарии | заморозку кандидата |
| Зачётный paper-период | замороженный кандидат + готовый paper-контур | переход к проверке реального API |
| Поверхность риска счёта | подходящий UTA-субсчёт и read-only проверка | только подготовку технического реального этапа |
| Технический реал | зачётный paper + приватный профиль + отдельное разрешение пользователя | ограниченную проверку исполнения |
| Контролируемый реал | пройденный технический реал + отдельное решение об увеличении капитала | подтверждение BTC для выбранного капитала |

Старые поля `profile` и `paper` в `status.json` мигрированы в `account_profile` и `paper_runtime`; добавлены `market_profile`, `risk_engine`, `account_risk_surface` и `shadow_readiness`. Schema v3 хранит проверку, свежесть и admission раздельно. Например, у `historical v34` локальная проверка завершилась с `check_status=failed`, а `stage_admission_status=blocked` из-за data prerequisite; это не разрешает optimizer.

## Актуальный порядок работ

Сроки считаются от прохождения gates, а не от календарной даты. Независимые исследовательские пакеты можно вести параллельно, но связанные `research-gates` запускаются последовательно: сначала базовый пакет, затем зависящий от него пакет. Один интегратор отвечает за общие файлы и машинный статус.

Актуальный ускоренный порядок после ревью 1.74:

1. **P0 — freeze CODE_PATHS.** Не менять восемь shadow файлов без нового 24h. Не стартовать exam-контейнеры. Не писать `status.json` вручную. Не использовать `v83`/`v86`/`v26`. Не начинать queue/depth, ETH rerun, optimizer, paper/live.
2. **P1–P8 закрыты.** BL-24/MP-24/RE-24/SR-24 bound на `f5af0643…`.
3. **P9 — funding.** Публичный полный архив 24 августа не найден (248/2332). Не повторять исчерпанные GET без нового источника. Нужен пользовательский файл или явное разрешение на платный провайдер. Затем `bitget-funding-archive` и `bitget-funding-promote` в **новый** путь. Frozen input не перезаписывать.

Окно 1.72 (v7 сдан, bind ждал baseline) закрыто. Окно 1.71 (запуск v7) закрыто. Окно 1.70 (v31/v25) закрыто. Окно 1.69 (v85/v49) закрыто. Окно 1.68 (p1-paper / p1f) закрыто. Окно 1.67 (P0-E/P0-D merge) закрыто. Окно 1.66 (v84/v48/v30/v24) закрыто как historical freshness. NAS v6 и v23 — факты 1.65.

### 0. Закрепить воспроизводимую базу

Статус проверки: **BL-24, MP-24, RE-24 и SR-24 passed и fresh**. Fingerprint `f5af06437b13dd7baaa6c20d144fb76884cc45dc93d2620735838b8679b40e78`, commit `7a8be7892`, `git_dirty=false`. BL-24: 2 641/2 523/118 Python, 20 fake-live, 145 Rust; progress-dot hash `9a4a1346…`. RE-24: 35/35 production-path. SR-24: 40/40 на EX-7. BL-20/MP-20/RE-21/SR-21 — historical. v83 не существовал.

Сделано в текущем пакете:

1. Статус свежести и автоматическая проверка соответствия текущих хэшей последнему прошедшему запуску.
2. Паспорт среды с версиями Python/Rust/CCXT-зависимостей и хэшем загруженного Rust-расширения.
3. Защита от попадания файла блокировки `status.json.lock` в контрольную точку.
4. Baseline v78 исторически фиксировал 2 573/2 455/118; канон текущего дерева — BL-24.
5. Окно 1.66 закрыто: v84 / v48 / v30 / v24 тогда `passed`/`fresh`; сейчас v30/v24 stale.
6. Окно 1.69 закрыто для baseline/market_profile: v85 / v49 `passed`/`fresh`.
7. Окно 1.70 закрыто для risk_engine/shadow: v31 / v25 `passed`/`fresh`.
8. Окно 1.71: live 24h v7 запущен.
9. Окно 1.72: v7 сдан и скопирован; machine-bind отложен до нового baseline.
10. Окно 1.73: BL-24/MP-24/RE-24/SR-24 bound на `f5af0643…`.

Остаётся:

1. Не менять shadow `CODE_PATHS` без нового 24h. Document-only правки допустимы и не меняют implementation fingerprint.
2. Канцелярия доказательств закрыта. Следующий внешний blocker — funding archive. Неполный архив не открывает data gate. `status.json` только штатной командой.
3. `risk_grid` stale; не считать его текущим admission. Queue/depth не стартовать.

Допуск:

- текущие исходники совпадают с паспортом baseline v78;
- три полных запуска дают одинаковые торговые результаты и одинаковые машинные вердикты;
- запуск targeted-тестов и документальная правка не меняют implementation fingerprint;
- runtime-generated файлы не входят в source fingerprint и новые fake-live выходы не создаются в общем тестовом каталоге;
- отчёт относится к точной проверенной версии, а не только к тому же `HEAD`.

### 1. Исправить доказательный процесс

Версии v2–v4 дали полезное инженерное evidence, но не завершились принимаемым результатом. Fail-closed funding/source-time контракты сохраняются: synthetic rows, naive ISO timestamps и невыравненные окна блокируются, verifier требует top-level millisecond source timestamp и фиксирует обратный порядок, а config без `max_source_gap_seconds` отвергается. Boundary counters выводятся из фактов вызовов, supervisor использует монотонное время, а readiness получает machine `result.json`. Перед завершением v5 нужно сохранить frozen implementation/config hash и полный набор входных артефактов; P0 evidence-hygiene, gate-hardening и короткий crash/replay тест приняты baseline v78.

Уже реализировано: повторное использование существующего `run_id` блокируется; run-пакет получает `checksums.json`, включающий manifest, metrics, verdict, report, events, config и профиль рынка; проверка целостности не изменяет `status.json`; старый `passed` автоматически получает `stale`; публичный `market_profile` и приватный `account_profile` разделены; старые имена стадий мигрируются; запись `status.json` атомарна и защищена файловой блокировкой; `stale` распространяется на зависимые стадии; `shadow_readiness` может извлечь measured metrics, provenance и safety facts из самого shadow result, включая его SHA-256.

Ранее последовательная проверка создавалась для baseline, public market profile, account profile, risk engine, data, risk contract, risk grid, historical и shadow readiness; baseline v78 и safety-пакеты свежие, data/historical остаются fail-closed. Нельзя одновременно запускать базовый и зависящий от него пакет: временный `stale` или незавершённый prerequisite не должен попадать в итоговый verdict. Старый run не повышается автоматически. Последовательный запуск обязателен даже если независимые sidecar-задачи работают параллельно.

1. Повторить проверку параллельной записи и подмены входных файлов после завершения public shadow v5; baseline v78 уже зафиксировал текущую базу.
2. Добавить машинную проверку фактического запуска конфигурации, если источник запуска не может доказать это через manifest.
3. Поддерживать раздельные `outcome`, `freshness`, `check_status` и `stage_admission_status` для каждого run-пакета — реализовано и проверено.
4. Сохранять отдельный `risk_engine` и `account_risk_surface`; read-only risk engine surface v29 теперь прошёл gate, а account surface по-прежнему требуется только перед техническим реалом.
5. Разделять `shadow_readiness` и 90-дневный `paper_runtime` — схема внедрена; NAS v6 и v24 закрыты (`passed`/`fresh`); v23 historical.
6. Проверять реальный `market_profile_sha256`; строка `pending_run_manifest` запрещена — реализовано и прошло в market profile v38.
7. Добавить subprocess-регрессию для документированного CLI и использовать модульный entrypoint, пока конфликт прямого импорта не исправлен — CLI supervisor зарегистрирован, документированный `--help` smoke проходит.
8. Явно различать frozen funding dataset и свежую скользящую выборку endpoint: 18 августа public horizon дал 266/2 332; сбор 24 августа дал 248/2 332. Ни один не открывает data gate без полного проверенного архива.
9. Передавать для `shadow_readiness` machine `result.json`, а не копировать его `readiness_metrics` в отдельный ручной файл; gate должен отдельно проверять evidence schema, candidate admission, read-only boundary, нулевые внешние/торговые операции, provenance и deterministic replay. Counters должны выводиться из фактов вызовов; константные нули запрещены.
10. Хранить shadow policy и `max_source_gap_seconds` в `strategy_shadow_stream_config_v1.json`, не задавать зачётные пороги CLI-аргументами и блокировать несовпадение hash; реализовано и проверено. Supervisor обязан запускать каждый child в отдельной process group и удалять её при остановке; targeted-регрессия cleanup и crash/replay прошла. v2–v5 сохранены как историческое partial/aborted evidence и не передаются в readiness. Текущий зачётный run — NAS Docker v6; второй экземпляр на Mac не запускается. `--resume-existing-output` не обходит 300-секундный source-gap и не принимает завершённый `process_supervisor.json`.

Допуск:

- подмена входного файла, события или конфигурации делает проверку неуспешной;
- два параллельных запуска не теряют состояния друг друга;
- прошлый `passed` автоматически становится `stale`, если изменился любой значимый вход;
- любой `failed` и `blocked` результат также получает отдельный признак свежести;
- результат проверки нельзя спутать с допуском зависимой стадии;
- пакет с `portfolio_risk.enabled=false`, 500 уровнями или выключенной моделью исполнения не может доказать крипто-риск этапа.
- shadow-пакет без source/artifact provenance либо с неуспешным deterministic replay не может пройти readiness даже при достаточных числах длительности и заявок.
- quality report с чужим символом, таймфреймом, диапазоном или cache не может пройти historical preflight.

### 2. Сделать единый риск-контракт

Портфельный риск по-прежнему имеет два исполняющих контура: Python live и Rust tester. Локальный `risk_engine` gate v29 прошёл: surface-пробник сравнил 14 именованных сценариев и их причины. Машинный статус legacy `risk_contract` остаётся **failed**, потому что старый пакет не подтверждает private UTA/account evidence. В schema v3 `risk_engine` проверяется отдельно от `account_risk_surface`: приватный UTA ledger нужен перед техническим реалом, но не должен блокировать публичные риск-сценарии и historical.

Сделано в текущем пакете:

- определены канонические `risk-snapshot-v1` и `risk-decision-v1`;
- добавлен read-only `risk-parity-harness`, который при отсутствии любого adapter возвращает `blocked` и эффективное решение `deny`;
- фактический Python live gate экспортирует snapshot и решения после `_apply_crypto_portfolio_risk_limits`, не принимая второго решения;
- фактический Rust `Backtest` сохраняет trace каждой исходной заявки после вызова своего portfolio-risk gate, включая `allow/deny` для отклонённых заявок, и строит `risk-snapshot-v1` до очистки очереди;
- Rust snapshot включает balance, positions, pending orders, mark prices, margin tiers, leverage, daily loss и HSL. При ошибке снимка новые entries блокируются, а close orders сохраняются;
- Python и Rust используют общий формат `risk-order-v1` с семантическими полями заявки; occurrence для разных семантических заявок выровнен, а действительно дублирующие заявки блокируются до окончательной канонизации;
- `risk_trace.py` преобразует фактический Rust trace в `risk-decision-v1`, проверяет snapshot и общие risk metrics; harness получил режим `--live-export` + `--rust-trace`, сравнивает весь набор решений одного состояния и проверяет внешний snapshot перед сравнением;
- добавлена общая функция projected liquidation distance в Rust и Python, включая одинаковое учётное правило комиссии и maintenance margin;
- live и Rust последовательно переносят принятую позицию на следующий grid-уровень, поэтому решение следующего уровня не считается от устаревшей позиции;
- read-only actual probe v3 зафиксирован в `analysis_reports/crypto_bot_roadmap/actual_risk_parity_probe_2026-08-17-v3/`: snapshot совпал и все 6 решений совпали на захваченном public Bitget surface;
- production Bitget adapter получает через public GET отдельные mark/index, fee, quantity-step, funding interval и margin-tier surfaces; отсутствующая или неполная поверхность блокирует новые entries, а обычный `last` не подменяет mark;
- production live exporter умеет читать HSL runtime и дневной убыток из настоящего процесса; отдельная surface-матрица доказала HSL и daily-loss ветви на обоих локальных адаптерах;
- Rust-тестер принимает предел первого входа `max_initial_entry_sizing_balance_pct`, по умолчанию 0,03, и применяет его только к начальному входу;
- проверка risk-contract требует не только наличие кода, но и parity полного расчёта, fail-closed при расхождении и подтверждённые public market surfaces. Private isolated account state проверяется отдельной стадией перед техническим реалом.

Нужно:

1. Сохранить actual probe и новую surface-матрицу как обязательные регрессионные сценарии: один snapshot, одинаковые `risk-order-v1` и полный набор решений одного шага.
2. Повторять оба пробника после каждого изменения risk-contract; synthetic profile использовать только для unit/regression tests, а public surface — для read-only evidence.
3. При расширении risk surface сравнивать projected balance/position, daily loss, HSL, liquidation buffer, margin tier и close-order retention, а не только итоговое решение.
4. Оставить occurrence независимым от порядка списка для разных семантических заявок; если полная канонизация одинаковых заявок ещё не доказана, дубли должны блокировать сравнение.
5. Сохранять `risk_grid` как запускаемый набор сценариев: v49 машинно принял локальные checks на roadmap-config и public profile, а его stage admission passed; дальнейшее расширение должно добавлять engine evidence, а не только проверки конфигурации.
6. Каждый сценарий запускается с явной крипто-конфигурацией: риск включён, isolated, hedge, 3×, восемь уровней, первый вход не более 3%, рост не более 1,25×.
7. Добавить обязательные сценарии: тренд +35% и −35%, гэп 5%, несколько уровней в одной свече, пропущенное касание, частичные исполнения 25/50/75%, отмена и повтор ответа, неизвестный order ID, 30 минут без REST/WebSocket, устаревшие данные, funding shock, смена margin tier, перезапуск, ручная позиция и синхронная просадка BTC+ETH.
8. Ошибка установки или чтения hedge/margin/leverage для дорожного профиля должна останавливать запуск. Простого сообщения в логе недостаточно.
9. Перед техническим реалом отдельная read-only проверка счёта должна прочитать account info/settings/assets/positions/orders/fills/financial records и доказать isolated, hedge и отдельное плечо обеих сторон. Она не выполняет настройки.

Допуск:

- live и тестер принимают одинаковые решения на одном наборе снимков, а parity-harness показывает `passed` на фактических adapter outputs;
- public market profile и полный локальный risk engine проходят собственные gates;
- private isolated-account surface проходит отдельный gate только перед техническим реалом;
- все исходные заявки имеют сопоставимый stable `order_id`, включая заявки, отклонённые одним из контуров;
- каждый обязательный сценарий создаёт воспроизводимый журнал событий и машинный вердикт;
- ни один сценарий не создаёт повторных заявок, не обходит лимиты и не допускает ликвидацию;
- отсутствие обязательного параметра блокирует запуск, а не подставляет безопасное на вид значение.

### 3. Завершить данные и модель исполнения

Уже подтверждено:

- минутное BTC-окно: 1 118 881 из 1 118 881 строк;
- часовые mark/index: 18 649 из 18 649 строк;
- нет незаявленных синтетических строк, NaN и будущих данных;
- публичные уровни поддерживаемой маржи сохранены;
- фактический кэш HLCV повторно проверен quality-командой: `bitget_btc_ohlcv_quality_2024-07-01_to_2026-08-17-v1.json` и `bitget_eth_ohlcv_quality_2024-07-01_to_2026-08-17-v1.json` имеют статус `passed`; оба отчёта теперь явно содержат канонический cache path, `remote_fetch_policy=local_only` и `remote_fetch_count=0`;
- public BTC/ETH market profiles собраны отдельной локальной командой из сохранённых `bitget-public-execution-inputs-v1`; профиль не содержит private/account fields и сохраняет хэши исходных public responses;
- замороженный локальный funding-файл содержит 270 строк из 2 332 ожидаемых и **не перезаписывался**; data metrics v47 по этому frozen снимку даёт coverage `0.11578044596912522`. Аудиты 18 августа `03:14 UTC` и `09:54 UTC` получили соответственно 267 и 266 строк в точном окне — это разные rolling snapshots, все они неполны и сохраняются раздельно.
- публичная сборка 24 августа снова дала `100, 100, 70, 0` (270 строк), из которых **248 / 2 332** попадают в фиксированное окно (coverage `0.10635`). Полного публичного архива нет. Evidence: [`funding_archive_collect_attempt_2026-08-24.md`](../../analysis_reports/crypto_bot_roadmap/inputs/funding_archive_collect_attempt_2026-08-24.md).
- исторический аудит 18 августа сохранил raw public-файлы и не нашёл полного внешнего архива; официальный UTA v3 и Classic v2 дали одинаковые страницы `100, 100, 70, 0`, Tardis покрывает только часть окна, Amberdata/Laevitas требуют ключ. Дополнительный аудит официальной страницы загрузки подтверждает отсутствие funding archive в каталоге: [`funding_archive_current_audit_2026-08-18-v1.md`](../../analysis_reports/crypto_bot_roadmap/inputs/funding_archive_current_audit_2026-08-18-v1.md), [`funding_archive_official_download_audit_2026-08-18.md`](../../analysis_reports/crypto_bot_roadmap/inputs/funding_archive_official_download_audit_2026-08-18.md).

Нужно:

1. Получить полный Bitget funding-архив от 1 июля 2024 года до 17 августа 2026 года. Публичный сбор 24 августа не нашёл полного источника (248/2332); data gate остаётся закрыт. Следующий вход — файл пользователя или явное разрешение на платный источник, затем `bitget-funding-archive` и `bitget-funding-promote` в **новый** путь; frozen input не перезаписывать. Ожидание 2 332 расчётных моментов основано на точной границе `[2024-07-01T00:00:00Z, 2026-08-17T00:00:00Z]`; это не полный календарный день 17 августа. Если границу изменят, ожидаемое число событий должно быть изменено явно и до запуска optimizer. Поскольку Bitget прямо указывает, что funding interval зависит от символа, историческое изменение расписания допускается только при отдельном официальном доказательстве. Допустимый результат содержит исходник, лицензию, хэш и список ответов, а не только обновлённый count: [Funding history](https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate).
2. Проверить символ, историческую временную сетку, дубликаты, пропуски, числовые пределы и совпадение пересекающихся строк с независимым источником.
3. Не заполнять пропуски нулём, переносом соседнего значения или скрытой синтетикой.
4. Хранить источник, лицензию/условия использования, дату получения и хэш исходного файла.
5. Продолжать локальную запись bid/ask, mark/index, funding и глубины стакана BTC и ETH с ежедневной проверкой целостности.
6. Калибровать задержку, пропущенные касания, очередь и частичные исполнения по записанным данным.
7. Не покупать внешний архив и не использовать API-ключ без отдельного решения пользователя; исчерпанные публичные GET не повторять. До нового файла или явного платного разрешения сохранять только уже записанный audit и формальный blocker.

Проверку локального HLCV-кэша выполнять отдельной командой до каждого исторического запуска:

```shell
passivbot tool bitget-ohlcv-quality \
  --symbol 'BTC/USDT:USDT' --start 2024-07-01T00:00:00Z \
  --end 2026-08-17T00:00:00Z --as-of 2026-08-17T00:00:00Z \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_ohlcv_quality_<version>.json
```

Отчёт проверяет именно те строки, которые прочитает тестер; отчёт ремонта сам по себе этого не заменяет.

Если полный архив пока недоступен, разрешены только запуски с пометкой `exploratory`. Для них выполняются несколько явно названных funding-сценариев, включая неблагоприятный для каждой стороны. Они могут проверять код и сужать область поиска, но не могут дать `passed`, заморозить кандидата или запустить зачётный paper-период.

Допуск:

- OHLCV не ниже 99,95%; mark/index и все известные funding-моменты покрыты на 100%;
- пересечение источников совпадает в заданной и записанной погрешности;
- три одинаковых запуска дают одинаковые результаты;
- все подстановки и стрессовые предположения видны в отчёте.

### 4. Довести paper-контур до готовности

Актуальный verdict: **NAS `public-stream-process-24h-2026-08-21-v7` сдан (`passed`, 25.52 ч wall-clock).** SR-24 (`shadow-readiness-2026-08-24-exam7`) 40/40, admission passed, freshness fresh на `f5af0643…`. EX-6 и SR-21 historical, не перезаписывать. Exam-контейнеры не стартовать.

### Synology Docker, public-only

Хост: DS923+, SSH `192.168.178.102:20022`, пользователь `ivan`/`Ivan`, Docker `/usr/local/bin/docker`. Проект: `/volume1/docker/_cm/passivbot-shadow/`. Образ `passivbot-shadow:v6` из `Dockerfile_shadow`: Python 3.12, native amd64 Rust wheel, `PYTHONPATH=/app/src`. Entrypoint запрещает `PB_API_KEY`/`api-keys.json`. Compose: `mem_limit: 768m`, volume `./data` и `./inputs:ro`, без ключей. Поле `cpus:` на этом DSM отбрасывается (нет CFS). `SHADOW_RESUME=1` включает `--resume-existing-output`. Cutover выполнен `2026-08-18T22:53:49Z`, source-gap ≈66 с. Exam v6 завершён `2026-08-20T00:38:00Z`, `exit=0`. Контейнер `passivbot-shadow` не стартовать. Следующий 24h — отдельный `docker run --name passivbot-shadow-v7` в новый каталог `public-stream-process-24h-2026-08-21-v7`, `SHADOW_RESUME=0`. v24/v25 gates — отдельный `docker run --rm --entrypoint python`, не resume exam.

Перенос journal без потери:

1. Child читает существующие `checkpoint.json`, `events.ndjson`, `paper_ledger.json`+WAL, `stream_state.json`, `market_snapshots.jsonl`. Это уже доказано Mac restart-0001.
2. Supervisor без флага требует пустой каталог — поэтому нужен явный resume, а не «подложить файлы».
3. `verify_market_log` смотрит разрывы **внутри** jsonl. Если между последней Mac-строкой и первой NAS-строкой больше 300 с source-time, сегмент и весь run получат integrity `failed`.
4. До старта resume supervisor отвергает live-каталог, если `now - last_source_timestamp_ms > 300`. Подставлять timestamps запрещено.
5. 24 часа wall-clock считаются от нового supervisor’а на NAS. Сохранённые Mac-часы увеличивают coverage, не сокращают ожидание.

macOS `openrsync` не передаёт порт 20022; копировать через `tar|ssh` или `ssh cat`. Пароль DSM на диск не писать.

Текущий публичный адаптер всё ещё считается только рабочим прототипом. В него добавлены opt-in persistence, атомарный JSON-снимок, write-ahead typed `order.filled` WAL, восстановление после остановки до и после изменения ledger и защита от повторного применения идентичных событий. Одно исполнение может состоять из нескольких явно переданных частей: до закрытия остаток заявки остаётся в `open_orders`, для каждой части проверяются остаток, порядок, fee/slippage и WAL. Это не даёт права выводить глубину стакана или очередь биржи из одного ticker/candle. Фактический paper fill проходит через `FillEvent` и production monitor writer: nested `info` с typed fee/slippage, order/fill ID, количеством до/после и контекстом стратегии не теряются. Paper snapshot hook вызывает локальную reconciliation только у paper-бота. Она сравнивает balance, комиссии, explicit funding и известные позиции, сохраняет source/hash и не пишет `costs.reconciled`, пока полный cost journal не собран. Новый read-only public execution feed принимает только сохранённые Bitget-файлы с нулём торговых операций, сохраняет SHA-256 исходника, выбирает mark строго до funding timestamp и идемпотентно помечает обработанные settlement ID. Полный typed fixture проходит валидатор, а сценарий без funding остаётся blocked. Подробные результаты сохранены в [`paper_runtime_monitor_integration_2026-08-17.md`](../../analysis_reports/crypto_bot_roadmap/inputs/paper_runtime_monitor_integration_2026-08-17.md), [`paper_runtime_reconciliation_2026-08-17.md`](../../analysis_reports/crypto_bot_roadmap/inputs/paper_runtime_reconciliation_2026-08-17.md), [`paper_public_execution_feed_2026-08-17.md`](../../analysis_reports/crypto_bot_roadmap/inputs/paper_public_execution_feed_2026-08-17.md) и предыдущем WAL-аудите [`paper_runtime_wal_review_2026-08-17.md`](../../analysis_reports/crypto_bot_roadmap/inputs/paper_runtime_wal_review_2026-08-17.md).

Связь WAL с fill и monitor writer, локальная reconciliation, partial-fill primitives и отдельный bounded runner существуют на уровне кода и targeted-тестов. В текущем пакете добавлены регрессии для конфликтующего `fill_id`, последнего partial-fill остатка, WAL order/cancel/funding, crash/replay, input immutability, source-hash reconciliation и безопасной загрузки paper-профиля. Все 12 новых paper-регрессий, stream и process supervisor регрессии, CLI smoke прошли. `src/research/strategy_shadow.py` и CLI `src/tools/strategy_shadow.py` добавляют deterministic intent/plan/fill/restart контур поверх локального `PaperBitgetCCXTClient`; `strategy_shadow_stream` добавляет public recorder bridge, source-time policy, raw/strategy log separation и heartbeat filtering; `strategy_shadow_stream_process` добавляет отдельные PID/state/recovery evidence и три независимых replay. Fixture mode помечается и не может пройти readiness. Реальный public smoke не читает credentials и не выполняет exchange writes.

Нужно:

1. Любая ложная public-only, no-order, no-transfer либо checksum-проверка делает verdict `failed` и процесс завершается ненулевым кодом — реализовано для shadow boundary и проверено.
2. Повтор `fill_id` принимается только при полном совпадении order, amount, price, part sequence и fingerprint; конфликт завершает запуск ошибкой — реализовано и проверено.
3. Автоматическая часть равна `min(configured_chunk, remaining)` — реализовано и проверено.
4. Создание, отмена, funding и fill получают один crash-safe typed WAL — реализовано и проверено fault injection; остаётся непрерывный runtime-run.
5. Хэш public input фиксируется до первого события и проверяется при чтении/replay — реализовано и проверено.
6. Reconciliation сверяет source hash и вычисляет slippage из reference/execution prices — реализовано и проверено.
7. Обычный paper startup не читает credential record — реализовано и проверено.
8. Получать и сохранять bid/ask, mark/index, funding, trades и стакан; блокировать новые входы при устаревших или неполных данных.
9. Создать отдельный strategy shadow driver: **реализовано и проверено** — стратегия формирует намерения, paper-движок моделирует исполнение, а журнал сохраняет `strategy_id`, `cycle_id`, `config_hash`, `order_id`, `fill_id`, часть, плановую и фактическую цену.
10. NAS v6 доведён до 24 часов wall-clock этого supervisor’а (92638.6 с), 65 planned, пяти process restorations, трёх replay и нуля stream errors; машинный v24 принят с `check_status=passed` и admission passed. v23 historical. Плановые rotations внутри run не заменяют fault-injection crash-тест. Семидневный exploratory run разрешён только после shadow admission; его дни не входят в 90-дневный зачёт.

Допуск готовности контура:

- двухпроцессный тест «остановить в любой точке — восстановить» проходит для каждого перехода заявки;
- состояние до и после восстановления совпадает;
- funding, комиссии и PnL воспроизводятся из журнала;
- нет приватных Bitget-запросов и торговых операций;
- все safety checks входят в итоговый verdict, а checksums реально пересчитываются тестом;
- readiness gate получает `result.json` напрямую и проверяет его `evidence_schema_version`, source/artifact provenance, replay status и отсутствие внешних/торговых операций;
- readiness gate дополнительно требует `shadow_wall_clock_duration_seconds`, process-supervisor status, пять разных PID transitions, recovery не более 60 секунд, отсутствие повторных order/fill IDs и совпадение code/config/input hashes с текущим run;
- readiness gate отклоняет `fixture_mode`, требует раздельного raw market log и strategy-input log, source timestamp в миллисекундах и отсутствие strategy step для heartbeat;
- есть стратегия, плановые заявки и фактические paper events; простой ticker heartbeat не считается strategy shadow;
- отдельный 7-дневный технический shadow-run проходит без необъяснённых расхождений, сохраняет source-hashed inputs и воспроизводимый WAL/ledger. Эти семь дней не входят в 90-дневный зачёт.

### Новые находки редакции 1.74 — полный funding-архив публично не собран

- Свежий UTA v3: страницы `100, 100, 70, 0`, 270 строк на `BTCUSDT` и `ETHUSDT`. В окно плана входят 248 точек, отсутствуют 2 084, coverage `0.10635`. Oldest `2026-05-26T16:00:00Z`. Classic v2 тот же горизонт; `startTime` не открывает 2024–2025. V1 decommissioned.
- Tardis без ключа: нет данных до `2024-11-08`, остальные дни 401. Amberdata 403, Laevitas/Coinank без ключа, d3lta mirror недоступен, Hugging Face `bitget funding` пуст, data-download без funding. CryptoStruct платный.
- Raw страницы сохранены как `funding_collect_2026-08-24_bitget_uta_v3_*`. Сводка: [`funding_archive_collect_attempt_2026-08-24.md`](../../analysis_reports/crypto_bot_roadmap/inputs/funding_archive_collect_attempt_2026-08-24.md). Frozen execution input не менялся. `bitget-funding-archive` и `bitget-funding-promote` не запускались. Следующий вход — файл пользователя или явное разрешение на платный источник, затем обе команды в **новый** путь.

### Исторические находки редакции 1.73 — bound-срез BL-24/MP-24/RE-24/SR-24

- `baseline-2026-08-24-rebind`: Rust stamp совпал, rebuild не делался. Три последовательных полных pytest: 2641 collected / 2523 passed / 118 skipped; progress-dot hash `9a4a1346…` совпал с BL-20. fake-live 20 passed. `cargotest.sh` 145 passed. Gates 5/5, admission passed, freshness fresh. `created_at_utc=2026-08-23T22:21:03Z`.
- `market-profile-2026-08-24-rebind`: те же public surfaces (`public_only_2026-08-17.json` sha256 `aa7038a7…`). Gates 4/4. `created_at_utc=2026-08-23T22:21:15Z`.
- `risk-engine-2026-08-24-rebind`: новый probe `production-risk-evidence-2026-08-24-v1` на текущем fingerprint. Snapshot `cb50c9a1…`, invocation `Passivbot._apply_crypto_portfolio_risk_limits` + Rust Backtest, `fixture_mode=false`, `trading_operations_count=0`. Не `p0d_local`. Probe читает `bitget_btc_market_profile_read_only.json` (sha256 `7c36c253…`); stage `market_profile` остаётся public_only. Gates 35/35. `created_at_utc=2026-08-23T22:21:33Z`.
- `shadow-readiness-2026-08-24-exam7`: 40/40, admission passed, freshness fresh. Evidence — Mac path-bind EX-7. Исходный NAS `result.json` SHA-256 `5b427211…` не менялся. SR-21/v24/EX-6 не перезаписывались. `process_code_hash_matches_current=true`. `status.json` `updated_at_utc=2026-08-23T22:21:42Z`.
- Торговая готовность ноль. Следующий внешний blocker — funding 270/2332. Queue/depth не стартовать. ETH не перезапускать. Восемь `CODE_PATHS` не менять без нового 24h.

### Исторические находки редакции 1.72 — v7 сдан; bind ждал новый baseline

- Supervisor `2026-08-20T23:58:39Z`–`2026-08-22T01:29:38Z`, wall-clock 91858.7 с (25.52 ч), coverage 24.25 ч, 1397 snapshots/events, 0 stream errors, max source gap 72.052 с, funding continuity passed, 51 planned и 51 paper fill, 5 process restarts / 6 сегментов `return_code=0`, recovery 0.058 с / 2.29 с, replay 3/3 hashes equal, `candidate_admission=false`, `fixture_mode=false`, 0 trading ops. Контейнер `exit=0`, OOM=false.
- Исходный NAS `result.json` SHA-256 `5b42721121e541c6a203527e4ba4aed074e8f873b6eee9abbf8d53d060ebb442`. Не менять. Копия: `analysis_reports/crypto_bot_roadmap/shadow_runs/public-stream-process-24h-2026-08-21-v7/`. Mac path-bind заменил 56 путей `/data/...` → локальный каталог; artifact hashes совпали. v6/v24/v25 не перезаписывались.
- In-memory `evaluate_candidate_gates`: 40/40 passed. Штатный `research-gates` не запускался и `status.json` не писался (`updated_at_utc` остаётся `2026-08-20T23:26:05Z`).
- Коммит overlay `7a8be7892` сделал дерево чистым. Текущий fingerprint `f5af06437b13dd7baaa6c20d144fb76884cc45dc93d2620735838b8679b40e78`. CODE_PATHS hash всё ещё `b6c53f50…`. Admission v7 сейчас был бы `blocked` из-за stale baseline/market_profile/risk_engine.
- Нового кода перед тестами нет: queue/depth запрещён, funding закрыт, `CODE_PATHS` нельзя менять до bind v26. Luna-Max не вызывался.

### Исторические находки редакции 1.71 — live 24h v7 запущен

- Пользователь явно попросил следующий 24h и проверку сбора данных. Это снимает freeze «второй 24h не нужен» из 1.70, но не разрешает `docker start` exam-контейнера, ключи, заявки, ETH rerun или queue/depth.
- Targeted shadow tests перед запуском: 38 passed (`test_strategy_shadow*.py`). Восемь `CODE_PATHS` не патчились.
- Запуск: `docker run -d --name passivbot-shadow-v7` на образе `passivbot-shadow:v6`, `SHADOW_RESUME=0`, новый каталог `/data/public-stream-process-24h-2026-08-21-v7`. Compose DSM-проекта не менялся. Контейнер `passivbot-shadow` остался `exited 0`.
- Сбор на срезе `2026-08-21T00:03:06Z`: 5 строк `market_snapshots.jsonl`, 40 `events.ndjson`, `ingest_seq=5`, `fixture_mode=false`, last ≈73026.5→73086.1, source span 248.4 с, `trading_operations_count=0`. Первый срез `00:00:47Z` уже имел 3 snapshots. `result.json` и `process_supervisor.json` отсутствуют — run живой.
- `shadow-readiness` для v7 не создавать до финала. `status.json` не писать. v25 остаётся последним bound shadow на v6.

### Исторические находки редакции 1.70 — production-path risk_engine v31 и shadow v25

- Live probe `production-risk-evidence-2026-08-21-v1`: schema `risk-parity-production-evidence-v1`, snapshot `cb50c9a1…`, invocation `Passivbot._apply_crypto_portfolio_risk_limits` + Rust `Backtest`, `fixture_mode=false`, `trading_operations_count=0`. Не `p0d_local`. Targeted `test_live_probe_records_invocation_after_calling_both_engines` passed.
- `risk-engine-2026-08-21-v31`: 35/35 gates, `passed`/`fresh`. Research-matrix 14/14 остаётся fixture и сама по себе не открывает parity; v30 historical.
- Probe binds `bitget_btc_market_profile_read_only.json` (нужны raw `responses`); stage `market_profile` по-прежнему `public_only_2026-08-17.json`. Это разные файлы, оба публичные входы, не private trading.
- `shadow-readiness-2026-08-21-v25`: 40/40, admission passed, freshness fresh. Evidence — тот же NAS v6. Mac path-bind только заменил `/data/...` на локальную копию; исходный `result.json` SHA-256 `027ddba0…` не менялся. v24 не перезаписывать. Восемь `CODE_PATHS` hash `b6c53f50…` совпал, второй 24h не запускался.
- `status.json` `updated_at_utc=2026-08-20T23:26:05Z`. Следующий внешний blocker — funding 270/2332.

### Исторические находки редакции 1.69 — честный baseline v85 и market_profile v49

- `baseline-2026-08-20-v85`: Rust extension rebuilt (`cargo clean` + `maturin develop --release`); stamp `1b9d2604…` совпал с sources; полный pytest три раза подряд, identical hash `9a4a1346…`; 2641 collected / 2523 passed / 118 skipped; fake-live 20 passed; `cargotest.sh` 145 passed. Fingerprint `d46be477…`. Не v83. Не reuse v78/`a1105ac4…`.
- `market-profile-2026-08-20-v49`: те же public inputs, что v47/v48. 4/4 gates passed. `status.json` `updated_at_utc=2026-08-20T22:11:12Z`.
- `risk_engine v30` и `shadow-readiness v24` stale на этом дереве. v24 не перезаписывать. Контейнер `passivbot-shadow` не стартовать. `p0d_local/` не кормить в shared status.
- Следующий шаг — новый `risk_engine` с bound production-path evidence. Queue/depth не стартовать.

### Исторические находки редакции 1.68 — p1-paper и p1f после P0-E/P0-D

- `p1-paper` перенесён точечно из `TLI-Wings/p1-paper-execution-calibration` / `p1/paper-execution-calibration` @ `9dee208e5`. Live `paper.py` сохранил P0-E passport. `fetch_positions()` требует named mark/index и bound tiers. Evidence writer пишет `crypto-paper-execution-evidence-v1`. Queue/depth в `create_order` не проводён; `qualifying=false`.
- `p1f-timestamps` перенесён точечно из `TLI-Wings/p1f-strict-timestamps`. `_integer()` отклоняет float `1.9`, `"1.9"`, bool, NaN и overflow. `bitget.py` из p1f не копировался (live больше и уже разошёлся).
- Targeted tests: 111 passed (`test_paper_*`, `test_data_passport`, `test_bitget_paper_shadow*`, `test_bitget_market_surfaces`).
- Следующий шаг на момент 1.68 — один новый baseline, не v83. Закрыто в 1.69 как v85.

### Исторические находки редакции 1.67 — P0-E и P0-D после v24

- v24: 40/40 shadow-gates, admission passed, freshness fresh. Evidence — тот же NAS v6. Manifest `plan_sha256` на NAS `null`, потому что `gate-repo` без `docs/plans/`; это не gate. v84 хранит plan hash.
- P0-E перенесён точечно из `TLI-Wings/p0e-data-passport-clean`. Frozen funding 270/2332 по-прежнему `qualifying=false`.
- P0-D перенесён точечно: `src/research/risk_evidence.py`, `tests/test_risk_evidence.py`, проводка `experiment_manifest` / `research_gates` / `risk_parity_actual_probe`. `passivbot.py` не копировался (уже совпадал). Kitchen-sink overlay не копировался. Исторический `p0d_local/` не кормить в shared `status.json`.
- `risk_limits_enforced_in_live_and_tester` теперь ставит только validator production-path. Surface 14/14 остаётся research fixture. v30 в `status.json` ещё показывает старую метрику — это historical run, не перезапускать в этом окне.
- Следующий шаг — `p1-paper` без queue/depth, затем `p1f-timestamps`. Новый baseline после этого пакета.

Исторический слой: закрыто в 1.68 (оба merge выполнены).

### Новые находки редакции 1.66 — исторический слой перепривязки gates

- v23: 40/40 shadow-gates passed; единственный blocker тогда — `stage.prerequisites` (`baseline=stale`, `market_profile=stale`, `risk_engine=stale`). Один новый baseline admission не открывал: нужны были все три fresh стадии. Закрыто v84/v48/v30/v24.
- Artifact paths в NAS `result.json` — `/data/public-stream-process-24h-2026-08-19-v6/...`. Mac gate без этого префикса получит `shadow_artifact_hashes_match=false`. Канонический запуск v24 был `docker run --rm --entrypoint python` с `-v data:/data`, не `docker start`.
- Четыре Mac `URLError` в `policy_events` не попали в child `stream.errors`; финальный v6 `status=passed`, `shadow_stream_errors=0`. Это не повод ослаблять gap/error gates.
- v23 immutable. Readiness id окна — `shadow-readiness-2026-08-20-v24`. Идентификатор `v83` не использовать.
- Git в контейнере на смонтированном repo требует `safe.directory=/app`. Fingerprint gate-repo должен совпасть с Mac `_git_state()` (`dcae540a…`); без `.gitignore` и при `core.filemode` на Synology hash расходится.

### Новые находки редакции 1.63 — фиксируются, но не исправляются в коде

- ETH sidecar `eth-public-market-24h-2026-08-18-v1` завершился `@ 20:07Z`: 4,017 ч, integrity `failed` (12 errors / 3 транспортные строки). Не перезапускать и не считать 24-часовым evidence.
- Supervisor пишет `process_supervisor.json` раньше финальной перезаписи `result.json`. Watcher уже видит сегментный `result.json`, поэтому v22 может привязаться к сегменту 1. Не патчить live `CODE_PATHS` во время freeze.
- Сегмент 1 уже несёт 4 stream errors; supervisor копирует их в `errors[]` и тогда финальный `status=failed`. Даже полный 24h/20 orders скорее всего не даст `passed`. Принять как есть.
- Журнал на `21:42Z`: 686 `order.planned` (681 hold/skip, 5 enter/exit). `shadow_planned_orders = len(_plans)` по любому `order.planned` с `order_id`. Порог `≥20` не менять ни вниз, ни в зачёт hold. После v22 разобрать unique `order_id`.
- p0d — kitchen-sink: маркер `risk_evidence.py` изолирован, но worktree несёт широкий research overlay и копию плана 1.61. Merge-review после v22 вынимает только P0-D файлы.
- `funding_missing_data="zero"` всё ещё допускается validate; не чинить во время freeze.

### Новые находки редакции 1.61 — фиксируются, но не исправляются в коде

- `shadow_planned_orders` может считать `order.planned`, относящийся к skip/hold; это не основание ослаблять обязательный порог `≥20` плановых заявок. Смысл каждой заявки должен быть проверен в финальном evidence.
- CLI `--events` всё ещё способен создать пустой `events.jsonl.gz`: gate при этом должен завершиться fail-closed, но сам bundle может появиться. Уже исправленные provenance mapping и profile binding не отменяют этой границы.
- Metrics v81 унаследовал устаревший `plan_sha256` от v80. V82 записал актуальный plan hash `0eeffcd9…` и в metrics, и в manifest.
- V82 не доказал неизменность implementation fingerprint: metrics содержат `bbd19e7d…`, manifest/status — `35a72db6…`. Повторный пересчёт `_git_state()` даёт `35a72db6…`. В `src/`, `tests/`, `passivbot-rust/` и `configs/` нет mtime/ctime после v81; восемь файлов `CODE_PATHS` shadow не менялись после `15:23Z`, то есть до старта v5. Причину сдвига полного fingerprint до v22 не искать новым baseline и не чинить ручной записью `status.json`.

### 5. Исторический отбор BTC

Запуск разрешён только после свежего baseline, полного data gate, полного локального risk engine и независимых risk-сценариев. Private UTA account surface для исторического отбора не требуется. Готовность paper-контура может доводиться параллельно, но кандидат не замораживается на неполных данных.

До первого optimizer job preflight обязан:

- использовать public-only market profile для каждого актива;
- указывать канонический локальный каталог `caches/ohlcvs` и запрещать незаявленный remote fetch;
- зафиксировать maker/taker fee из профиля рынка, а повышенную комиссию оформлять отдельным именованным стрессом;
- подтвердить четыре окна, пять seed, 32 соседние конфигурации на центр, все stress-сценарии и отдельный holdout;
- не выдавать отклонение шести старых кандидатов за результат нового протокола.

Окна:

1. обучение 2024-07-01…2025-06-30, проверка 2025-07-01…2025-09-30;
2. обучение 2024-10-01…2025-09-30, проверка 2025-10-01…2025-12-31;
3. обучение 2025-01-01…2025-12-31, проверка 2026-01-01…2026-03-31;
4. обучение 2025-04-01…2026-03-31, проверка 2026-04-01…2026-06-30.

Период 2026-07-01…2026-08-17 остаётся последним историческим holdout. Настоящая невидимая проверка начинается после заморозки кандидата.

Правила:

- Long, Short и совместный режим оцениваются отдельно;
- за один запуск меняется не более восьми параметров одной группы: геометрия, вход или выход;
- выполняются пять независимых запусков оптимизатора;
- для финалиста проверяются соседи каждого параметра ±5% и ±10%;
- основной расчёт использует предел 1 000 USDT без реинвеста;
- каждое проверочное окно должно быть прибыльным после всех расходов, иметь просадку не более 20%, восстановление не более 90 дней, не менее 20 закрытых циклов и ноль ликвидаций;
- стресс должен сохранить не менее 75% начального капитала и не допустить ликвидации;
- не менее 80% соседей должны быть прибыльны в сумме и оставаться в пределах 20%; ни один сосед не может превысить 25%;
- при равенстве выбираются меньшая просадка, меньший номинал и меньшее время восстановления.

Результат: один замороженный кандидат либо машинный вывод `no_suitable_candidate`. Ослаблять gates после отсутствия кандидата запрещено.

### 6. Зачётная торговля без денег

Дата начала `T0` — момент заморозки кандидата и паспорта paper-движка. Любое изменение торгового решения, риска, исполнения или данных создаёт новую версию и обнуляет срок.

Допуск:

- не менее 90 календарных дней и не менее 85 дней с полным суточным покрытием;
- отсутствие необъяснённого разрыва событий длиннее двух ожидаемых циклов;
- не менее 100 запланированных заявок и 30 уникальных закрытых торговых циклов;
- uptime не ниже 99,5%, рассчитанный из регулярных heartbeat-событий, а не из первой и последней отметки;
- пять контролируемых перезапусков, восстановление не дольше 60 секунд;
- ноль повторных заявок, неизвестных позиций и необъяснённых расхождений;
- комиссии и funding полностью выводятся из событий и исходных снимков;
- просадка не выше 20%, чистый результат положителен;
- все показатели пересчитываются проверяющей командой. Значения из ручного `metrics.json` не могут повысить этап.

Если событий недостаточно, период продолжается до одновременного выполнения временного и событийного условий.

### 7. Приватный профиль Bitget и технический реал

Приватный профиль можно готовить ближе к завершению paper. Он не нужен для публичной разработки.

Перед запуском:

- отдельный UTA isolated-субсчёт;
- ключ без права вывода средств;
- IP allowlist;
- read-only подтверждение USDT collateral, отсутствия заимствований, hedge mode, isolated и отдельного плеча Long/Short;
- код установки режима, плеча и маржи останавливается при любой ошибке и выполняет повторное чтение состояния;
- отдельное явное разрешение пользователя на реальные заявки и перевод 200 USDT.

Технический этап длится минимум 30 дней, использует не более 200 USDT и общий номинал не более 0,25× капитала.

Допуск:

- минимум 30 дней, 50 исполнений и 10 закрытых циклов;
- ноль неизвестных или повторных заявок;
- комиссии и funding сходятся с биржей до 0,01 USDT;
- 95% исполнений отличаются от расчётной цены не более чем на 10 б.п.;
- 95% подтверждений заявки приходят не позднее пяти секунд;
- просадка не выше 5%; аварийный HSL не срабатывает.

### 8. Контролируемая торговля на 1 000 USDT

Только после отдельного решения пользователя капитал увеличивается до 1 000 USDT. Параметры замораживаются, реинвест запрещён.

Допуск:

- минимум 90 дней и 30 закрытых циклов;
- чистый результат после всех расходов положителен;
- просадка не выше 20%, предел 25% не достигнут;
- расходы и исполнение находятся внутри проверенных стрессовых границ;
- ноль необъяснённых расхождений состояния;
- uptime не ниже 99,5%.

Только после этого BTC считается подтверждённым для выбранного капитала, субсчёта и Bitget. Это не доказывает будущую доходность и не разрешает автоматическое увеличение капитала.

### 9. BTC+ETH и последующие активы

- ETH проходит собственные данные, независимый исторический отбор и зачётные gates.
- Синхронный тест использует одну временную шкалу и общий капитал.
- Все криптоактивы сначала считаются одной коррелирующей группой.
- Не более двух активных сеток, 0,50× капитала на символ и 0,80× на портфель.
- В стресс-тесте корреляция BTC и ETH равна 1.
- BTC+ETH проходят 90 дней совместной торговли без денег и отдельный ограниченный реальный этап.
- SOL рассматривается только после завершения BTC+ETH. Новый актив добавляется не чаще одного раза за 90 дней.

## Обязательные публичные контракты

- `live.sizing_balance_cap` ограничивает рост размера и позволяет ему уменьшаться после убытка.
- `live.paper_execution_inputs_path` задаёт только сохранённый read-only Bitget-файл для paper funding/mark inputs; путь запрещён вне `paper_mode`.
- `live.paper_execution_inputs_since_ms` задаёт нижнюю границу replay, чтобы исторический файл нельзя было незаметно начислить в текущую позицию; положительное значение обязательно при включённом feed.
- `live.portfolio_risk` задаёт номинал, маржу, запас до ликвидации, число сеток и связанные группы.
- `bot.long/short.entry_grid_max_levels` жёстко ограничивает уровни.
- `backtest.execution_model` включает funding, mark price, задержку, очередь, частичные исполнения, порядок внутри свечи и расходы.
- `strategy_shadow_stream_config_v1.json.shadow_policy` является каноническим источником `entry_bps`, `take_profit_bps` и `order_amount`; CLI не может незаметно заменить эти значения, а process evidence хранит их нормализованный hash.
- `backtest.execution_model.portfolio_risk.max_initial_entry_sizing_balance_pct` задаёт жёсткий предел первого входа; допустимый диапазон — больше 0 и не выше 0,03.
- `ExperimentManifest` содержит код, среду, конфигурацию, данные, профиль рынка, события, seed и точную команду.
- `RoadmapStatusV3` хранит отдельно `check_status`, `stage_admission_status`, `outcome` и `freshness`; каждый run-backed результат содержит source hash независимо от результата.
- `shadow_readiness` отделён от 90-дневного `paper_runtime`, а `risk_engine` — от `account_risk_surface`.
- События содержат `strategy_id`, уникальный торговый `cycle_id`, `config_hash`, плановый уровень, фактическое исполнение и причину решения.
- Paper reconciliation events содержат `source`, `source_hash`, список именованных проверок и `mismatch_count`; `costs.reconciled` допускается только при `all_costs_present=true`, typed комиссиях/slippage и явном funding journal. Неполная стоимость остаётся отсутствующим cost-доказательством, а не успешным значением.
- Дорожный режим содержит явные `execution_mode=paper|live`, `evidence_required=true` и `require_explicit_live_authorization=true`. Наличие API-ключа не меняет режим.

Существующие HSL, wallet-exposure и realized-loss настройки переиспользуются. Параллельные параметры с тем же смыслом не создаются.

## Артефакты и машинный статус

Каждый запуск создаёт новый каталог:

`analysis_reports/crypto_bot_roadmap/runs/<run_id>/`

Обязательные файлы:

- `manifest.json` — входы, среда и хэши;
- `metrics.json` — исходные измерения;
- `verdict.json` — результат каждого ограничения;
- `report.md` — объяснение человеку;
- `events.jsonl.gz` — события для воспроизведения;
- `config.json` — фактически использованная конфигурация;
- `market_profile.json` — публичный либо приватный профиль с явным типом;
- `checksums.json` — контрольные суммы всего пакета.

Для исторических данных дополнительно сохраняются quality-отчёты кэша HLCV. Их статус входит в `data` gate как `ohlcv_cache_quality_complete`; отсутствие отчёта блокирует gate.

В текущей schema v3 используются статусы `not_started`, `running`, `passed`, `failed`, `blocked`, `stale`, а также отдельные поля `check_status`, `stage_admission_status`, `outcome` и `freshness`.

- `passed` выставляет только проверяющая команда.
- `stale` означает, что входы прошлого результата больше не совпадают; это относится и к прошлым `failed`/`blocked`, а не только к `passed`.
- `failed` означает, что измерение выполнено и предел нарушен.
- `blocked` означает, что измерение нельзя закончить из-за отсутствующего обязательного входа.
- `check_status=passed` не означает допуск: если prerequisite не пройден, `stage_admission_status=blocked`.
- Любой `failed`, `blocked` или `stale` запрещает продвижение.

Bitget отдаёт через UTA API исполнения только за последние 90 дней и принимает запросы окнами не более 30 дней. Поэтому локальные журналы сохраняются с первого дня и ежедневно проверяются; API не считается долгосрочным архивом.

- [История исполнений](https://www.bitget.com/api-doc/uta/trade/Get-Order-Fills)
- [Финансовые операции](https://www.bitget.com/api-doc/uta/account/Get-Financial-Records)

## Правила для следующих моделей

1. Перед работой прочитать `AGENTS.md`, этот план, файл ревью, `status.json` и последний отчёт нужного этапа.
2. Сверить `git status`, `HEAD` и хэш текущего дерева. Не считать прошлый зелёный отчёт доказательством текущего кода.
3. Брать один измеримый пакет работ. До редактирования назвать его границы и список общих файлов.
4. Не запускать несколько моделей на одни и те же файлы. `status.json`, схема конфигурации, Rust-риск и канонический план имеют одного интегратора.
5. Вспомогательные модели могут работать только в раздельных областях и возвращают список файлов, тестов, известных ограничений и точный следующий шаг.
6. Если доступен Luna Max, использовать его для параллельных исследовательских и инженерных пакетов: один агент — одна непересекающаяся область записи, один интегратор — общий план, Rust-контракт и `status.json`. Делегирование не расширяет разрешение на заявки, переводы или изменение аккаунта.
7. Связанные `research-gates` запускаются только последовательно одним интегратором: новый пакет стартует после завершения и проверки prerequisite; параллельные модели не обновляют общий `status.json`.
8. Для parity один интегратор владеет schema и boundary: агент, который пишет Rust trace, не меняет одновременно live exporter, harness, plan или `status.json`.
9. Rust trace обязан создаваться внутри настоящего `Backtest` gate до очистки/перезаписи очереди; сравнение только итогового списка принятых заявок запрещено.
10. Каждая исходная заявка получает `allow` или `deny`; исчезновение заявки без записи считается ошибкой evidence.
11. Stable `order_id` должен быть одинаковым в live и tester и независимым от биржевого ID; изменение его алгоритма создаёт новую версию контракта и обнуляет parity-срок.
12. Не редактировать машинные вердикты вручную и не использовать `--overwrite` для зачётных запусков.
13. Не менять gates после плохого результата. Новая гипотеза получает новую версию и повторяет всю проверку.
14. Не использовать слова «этап пройден» по числу тестов. Нужен пакет с фактической конфигурацией, событиями и пройденным машинным вердиктом.
15. Любой внешний сет или архив сначала помещается в карантин, хэшируется и проверяется. Не выполнять скачанные бинарники и скрипты.
16. Не выполнять приватные записи Bitget, заявки, переводы, смену режима счёта или создание ключа без отдельного сообщения пользователя с прямым разрешением.
17. После изменения Rust пересобрать расширение и сразу запустить целевые Python/Rust-тесты. Полный Python, fake-live и Rust baseline выполнять один раз после заморозки всего пакета, а не после каждой локальной правки. Baseline v78 дал 2 573 собранных Python-теста, 2 455 прошедших, 118 пропущенных, 20 fake-live и 145 Rust-тестов.
18. Не писать `costs.reconciled` с `all_costs_present=false`: такой event может заблокировать весь последующий период. До полной сверки разрешено писать только reconciliation без cost-proof либо отдельный явно неучитываемый pending-event.
19. Перед зачётным shadow/paper запуском проверить переход `incomplete -> complete` на одном event log: после появления funding старый incomplete cost-event не должен оставаться в журнале.
20. Текущий `src/tools/bitget_paper_shadow.py` использовать только как короткий public transport smoke через модульный запуск. Safety verdict теперь fail-closed, но runner не является strategy shadow и не разрешён для семидневного периода до подключения стратегии.
21. Любой новый paper переход сначала получает crash/replay и conflict-id тест. Зелёный happy-path тест не считается доказательством восстановления.
22. Historical preflight использует public-only profile и локальный `caches/ohlcvs`; незаявленный remote fetch запрещён.
23. В конце оставить короткую передачу: что изменено, что доказано, что не доказано, какие команды прошли, какой результат свежий или устаревший, и один следующий безопасный шаг.
24. Для public strategy shadow не использовать `captured_at_ms` или локальные часы как время рынка: policy и coverage используют только проверенный `source_timestamp_ms`; `observed_at_ms` и monotonic clock служат только для liveness, интервалов и recovery.
25. Raw market log не смешивать с внутренними signals, plans или fills. Повтор одинакового source event записывать как heartbeat без вызова strategy driver; любой fixture mode явно помечать и не допускать к readiness.
26. `strategy-shadow-stream-process` запускать только после целевого теста stream. Для фактического readiness использовать public network без `--fixture`, сохранить process supervisor evidence и передать тот же raw market log в `research-gates` через `--dataset`.
27. Не принимать shadow result, если любой provenance artifact отсутствует или его SHA-256 не совпадает с файлом после завершения процесса; segment-local counters не использовать вместо cumulative counters.
28. Разделять driver restart recovery и process handoff recovery в именах метрик. Если следующий агент меняет restart semantics или вводит crash injection, это новая версия shadow-контракта и новый 24-часовой run.
29. Generated outputs, `monitor/fake` и исследовательские заметки не должны менять implementation fingerprint. Для плана, данных и конфигурации хранить отдельные хэши и проверять именно ту зависимость, которую они меняют.
30. Ни один подагент не запускает полный baseline, `research-gates` или запись общего `status.json`. Это делает интегратор один раз после просмотра и объединения независимых пакетов.
31. До заморозки дерева использовать узкие тесты, lint и временные каталоги. Новый immutable run создаётся только если его результат будет принят либо сохранён как явный `failed`/`blocked`, а не для каждого промежуточного шага.

## Рекомендации для моделей внедрения

1. Сначала прочитать `status.json`, затем read-only пересчитать его свежесть и найти критический путь. Сохранённый файл может отставать от дерева. Если первым data-блокером остаётся funding, не тратить цикл на оптимизацию сетов и не называть exploratory-результат кандидатом.
2. Разделять три вещи в каждом отчёте: что локальная проверка измерила, прошёл ли её собственный gate и разрешён ли следующий gate. Это предотвращает ошибочное продвижение после частичного успеха.
3. Делегировать Luna Max только независимые пакеты с заранее указанными файлами записи. Исследовательский агент возвращает источники и ограничения; инженерный агент возвращает изменённые файлы и тесты; общий интегратор принимает решение о включении результата.
4. Не запускать зависимые команды параллельно. Дождаться завершения upstream-run, проверить наличие полного набора `manifest.json`, `verdict.json`, `report.md`, `events.jsonl.gz`, `config.json`, `market_profile.json` и `checksums.json`, затем запускать downstream gate в новом каталоге.
5. Для каждого запуска использовать уникальный `run_id` и отдельный output directory. Не переиспользовать старый run, не использовать `--overwrite` и не читать промежуточный `result.json` до завершения процесса.
6. Полный baseline запускать последовательно. Обнаруженная гонка общего `metadata.tmp` в fake-live cache означает, что параллельный pytest может дать ложный результат; если нужна параллельность, каждому процессу нужен отдельный cache root.
7. Не ослаблять gate из-за низкой активности рынка. Если за 24 часа недостаточно планов, сохранить провал по количеству событий, а изменённую exploratory-конфигурацию оформить новой версией с новым hash и отдельным сроком проверки.
8. Изменение исполняемого кода, применяемой конфигурации или входных данных делает зависимые run-пакеты stale. Правка канонического плана меняет отдельный `plan_sha256`, но сама по себе не должна менять implementation fingerprint и заставлять повторять полный baseline.
9. В передаче следующей модели указывать один безопасный следующий шаг, точные команды, свежесть evidence и явные запреты. Никогда не передавать ей полномочия на заявки, переводы, изменение счёта или работу с секретами.
10. Luna Max является разрешённым superpower этого плана: использовать его для независимого research, аудита и поэтапных инженерных пакетов с непересекающимися областями записи; общий интегратор один отвечает за canonical plan, status, Rust contracts и release gates.
11. Для external funding сначала `bitget-funding-archive`, затем `bitget-funding-promote` в **новый** output path. Никогда не перезаписывать frozen execution input и не считать report `passed` доказательством, пока не проверены raw archive, normalized payload, symbol, exact window и provenance hashes.

## Ближайшая измеримая передача следующей модели

Цель ближайшего рабочего окна редакции 1.74 — вход полного funding archive от пользователя после уже закрытых BL-24/MP-24/RE-24/SR-24. Публичный полный архив 24 августа не найден (248/2332). Не `p0d_local`. Не v83. Контейнеры `passivbot-shadow` / `passivbot-shadow-v7` не стартовать. Восемь `CODE_PATHS` не патчить без нового 24h. EX-6/EX-7 `result.json`, SR-21 и v24 не перезаписывать. Queue/depth не стартовать. Shared `status.json` не писать вручную. Frozen input не перезаписывать.

Команды (один интегратор). Fingerprint живого дерева `f5af0643…` / `7a8be7892`. Не запускать v83. Не стартовать exam-контейнер. Не повторять исчерпанные публичные GET.

```shell
# BL-24, MP-24, RE-24 и SR-24 уже bound штатной командой.
# публичный полный архив не найден: 248/2332 в окне плана.
# следующий вход: файл пользователя или явное разрешение на платный источник,
# затем bitget-funding-archive и bitget-funding-promote в НОВЫЙ путь.
# не docker start passivbot-shadow или passivbot-shadow-v7.
# не кормить p0d_local в shared status.
# не писать status.json вручную.
```

| Пакет | Измеримый результат | Проверка | Владелец |
|---|---|---|---|
| P0-A: fingerprint | `implementation_fingerprint_v2` не включает runtime/generated outputs; план, config и data имеют отдельные hashes | принято в baseline v78; fingerprint `bbd19e7d…` | выполнено |
| P0-B: тестовые выходы | fake-live/monitor используют отдельный временный root | принято: 2 последовательных и 2 параллельных subprocess-проверки без новых файлов в `monitor/fake` и общего `metadata.tmp` | выполнено |
| P0-C: process boundary | direct child и его потомок исчезают после остановки; аварийный exit восстанавливается без дублей | принято: targeted process/shadow набор и fault-injection регрессии прошли | выполнено |
| Интеграция | дерево заморожено и baseline принят | BL-24: 2 641 Python (2 523 passed, 118 skipped), 20 fake-live, 145 Rust, 3 одинаковых progress-dot hash `9a4a1346…` | BL-24 passed/fresh на `f5af0643…`; BL-20/v84 historical |
| Funding research | новый источник или формальный внешний blocker | raw source, условия использования, exact-window coverage, hashes и отчёт; frozen input не меняется | Luna Max read-only audit завершён: blocker подтверждён |
| Shadow readiness | EX-7 сдан; SR-24 admission passed на `f5af0643…` | 40/40 shadow-gates SR-24; wall-clock 91858.7 с; 51 planned/fill; 5 process restarts | **SR-24 факт принят.** EX-6/SR-21 historical. Не `docker start` exam |
| ETH public sidecar | проверить recorder/integrity без открытия portfolio gate | фактическая длительность 4,017 ч; integrity `failed`; имя с `24h` не является доказательством длительности | завершён; процесс мёртв; не перезапускать; только exploratory |
| Воспроизводимость Rust | единая рабочая команда для PyO3 test binary | `PYO3_PYTHON=<absolute .venv python> passivbot-rust/cargotest.sh` даёт 145/145; прямой `cargo test` не используется как каноническая команда на macOS | документация уже исправлена в `docs/ai/commands.md` и `docs/ai/build_pitfalls.md` |
| P0-D: production-path risk evidence | новый run вызывает `Passivbot._apply_crypto_portfolio_risk_limits` и настоящий Rust `Backtest`, связывает actual probe, snapshot, config, market profile, code fingerprint и report hash в одном manifest | fixture/research-policy не могут выставить `risk_limits_enforced_in_live_and_tester=true`; mismatch, отсутствующий artifact или неполная матрица дают fail-closed verdict | **RE-24 bound 24 августа:** `production-risk-evidence-2026-08-24-v1`. `p0d_local/` historical, не admission. RE-21/v30 historical. |
| P0-E: общий data passport | historical и qualifying paper используют один неизменяемый паспорт: symbol, UTC start/end, cadence, expected/actual, missing, duplicate, foreign/future rows, mark-before-settlement coverage и source hashes | неполный feed явно `qualifying=false`; settlement без mark не пропускается молча; reconciliation не считает один имеющийся funding event доказательством полного окна | **перенесён в live 20 августа:** `src/research/data_passport.py`, targeted tests. Frozen feed 270/2332, `qualifying=false`. Грязный sibling `TLI-Wings/p0e-data-passport` не использовать |
| P1-F: строгий timestamp parser | public market surface отклоняет дробные timestamps вместо `int(1.9) -> 1` | отдельные тесты для float, numeric string, bool, NaN и overflow; валидные целые значения не меняются | **перенесён в live 20 августа:** `_integer()` в `src/research/bitget_market_surfaces.py`. `bitget.py` из p1f не копировался |
| P1-G: paper calibration (prepare-only) | изолированная подготовка реальных mark/index, maintenance tiers, queue/depth calibration и обязательного evidence writer | mark/index/tiers/evidence writer в live; queue/depth calibration artifact отсутствует; `create_order` не моделирует очередь. Qualifying paper не открыт | **перенесён в live 20 августа без queue/depth:** `src/research/paper_market_surfaces.py`, проводка `src/exchanges/paper.py`. `qualifying=false`. Имя `TLI-Wings/p1-paper-execution-calibration-cal` не использовать |

## Полное промежуточное ревью после 1 дня 6 часов 23 минут — 18 августа 2026 года, 16:39 UTC

Цель, порядок обязательных этапов и денежные ограничения совпадают с исходной
задачей. Реализация заметно продвинулась в воспроизводимости, локальном риске и
shadow evidence, но торговая готовность остаётся на нуле: нет полного funding,
нет нового historical-кандидата, не начались 90-дневный paper и реальные этапы.

Подтверждённый срез:

1. Baseline, market profile v47, risk engine v29 и risk grid v49 — passed/fresh; точный baseline run указан в `status.json`.
2. Data v47 — fresh/failed только по двум funding checks; historical v34 — blocked, optimizer не запускался.
3. v5 жив и остаётся привязанным к fingerprint `bbd19e7d…`; это Long-only transport/recovery evidence, не двухсторонняя квалификация стратегии. На срезе sequence и source-time проверки проходят, финальных файлов нет.
4. Локально повторно прошли 275 критических Python-тестов, 145 Rust-тестов через wrapper, Ruff и `git diff --check`.
5. Прямой `cargo test` не является рабочей командой этого PyO3-проекта на текущем macOS; инструкции исправляются, код Rust при этом проходит полный wrapper-набор.
6. Paper остаётся прототипом: WAL/funding/partial fill реализованы, но mark/index, maintenance tiers и queue/depth ещё не соответствуют зачётной exchange-модели.
7. ETH recorder с именем `24h` фактически настроен примерно на четыре часа; это только exploratory sidecar.

Критический путь после этого ревью сохранён как исторический слой 1.61. Слой 1.62 фиксирует freeze до v22. Актуальный порядок — редакция 1.63 ниже.

Параллельная работа агентов разрешена только так:

- Luna Max research-agent возвращается к funding только при новом проверяемом источнике либо явном решении пользователя о лицензированном export; он не покупает доступ и не работает с секретами.
- Runtime reviewer — единственный обязательный саб-агент до v22. Read-only контроль v5: PIDs, counters, mtime `result.json` vs `process_supervisor.json`, лог watcher. ETH recorder не перезапускать. Второй экземпляр shadow не запускать. `status.json` не обновлять.
- Queue/depth calibration разрешена только в существующем `TLI-Wings/p1-paper-execution-calibration` (`qualifying=false`), но **не стартует в этой пачке**.
- Risk-evidence пакет готов только в `TLI-Wings/p0d-production-risk-evidence` (uncommitted, kitchen-sink, не merge-ready до v22; после v22 вынимать только P0-D файлы). Data-passport уже готов в `TLI-Wings/p0e-data-passport-clean` и не merge до v22. Paper-калибровка готова только в `TLI-Wings/p1-paper-execution-calibration` (`qualifying=false`, calibration artifact queue/depth отсутствует) и не merge до v22. P1-F timestamp parser готов только в `TLI-Wings/p1f-strict-timestamps` и не merge до v22. Общий интегратор не объединяет пакеты до v22 и после него идёт в порядке P0-E → P0-D → paper → P1-F. Не копировать пакеты в live-дерево.
- Канонический план, общий Rust-контракт, machine status и release gates остаются у одного интегратора.

## Erratum после plan-binding v82 — 18 августа 2026 года, 17:32 UTC

`baseline-2026-08-18-v82` прошёл как plan-binding и сейчас `passed/fresh`. Это не восстановление public/risk цепочки и не доказательство, что дерево осталось на `bbd19e7d…`.

- Watcher v5 (`57680`) и supervisor (`81180`) живы; `result.json` / `process_supervisor.json` / каталог v22 отсутствуют.
- Readiness v22 сверяет hash восьми shadow `CODE_PATHS`, а не полный implementation fingerprint. Эти восемь файлов не менялись после старта v5.
- Не запускать второй shadow, v83, market_profile/risk/data rerun и не объединять implementation-код до машинного v22.
- После v22 один интегратор либо принимает фактический verdict, либо честно фиксирует failed/blocked, затем решает, нужно ли перевыпускать public/risk пакеты уже на `35a72db6…`.

## Исторический этап 1.62 — заморозка v5 до v22, изолированные P0-D и paper-prep — 18 августа 2026 года, 19:38 UTC

Это document-only постановка. Цель не меняется: развивать Passivbot, а не второго бота; первая цель — BTC/USDT perpetual на Bitget; исследовательский капитал 1 000 USDT; isolated hedge; плечо не выше 3×; просадка 20% и жёсткая остановка 25%; Long и Short допускаются независимо. Торговая готовность остаётся нулевой: нет кандидата, нет полного funding, 90-дневный paper и реальные этапы не начаты.

Контрольный live-срез v5 на `2026-08-18T19:38Z`: supervisor PID `81180`, `--side long`, `fixture_mode=false`, `result.json` отсутствует. В `stream_state.json` — 222 market events; в `checkpoint.json` — 1 786 typed events. Это Long-only transport/recovery, не квалификация Long+Short. ETH sidecar с именем `24h` и `duration-seconds=14520` остаётся примерно четырёхчасовой exploratory записью и не является 24-часовым evidence.

### Сейчас — до машинного v22

Live checkout `TLI-Wings/main` на `master` (`9dee208e5`) заморожен для `src/`, `tests/`, `passivbot-rust/` и `configs/`. Документация и generated reports вне implementation fingerprint.

1. Не останавливать и не дублировать `public-stream-process-24h-2026-08-18-v5`. Watcher создаёт ровно один `shadow-readiness-2026-08-18-v22`, когда появятся и `result.json`, и `process_supervisor.json`.
2. Не запускать `baseline-2026-08-18-v83`, не пересобирать public/risk/data/historical пакеты и не писать `status.json` вручную.
3. Принять будущий v22 как `passed`, `failed` или `blocked` без перетолкования. Порог `shadow_planned_orders >= 20` не ослаблять, даже если часть `order.planned` относится к skip/hold.
4. P0-E не merge: worktree `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Wings/p0e-data-passport-clean`, ветка `p0e/data-passport-clean`, файлы uncommitted. Грязный sibling `TLI-Wings/p0e-data-passport` / `p0e/data-passport` не использовать для merge review.
5. P0-D изолированная копия готова и uncommitted в `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Wings/p0d-production-risk-evidence` на `codex/p0d-production-risk-evidence-20260818`. Local bundle `analysis_reports/crypto_bot_roadmap/p0d_local/production-risk-evidence-2026-08-18/` (schema `risk-parity-production-evidence-v1`) связывает invocation `Passivbot._apply_crypto_portfolio_risk_limits` и Rust `Backtest` с actual probe v3 как регрессией, не как admission. Не merge-ready до v22; в live-дерево не копировать; shared `status.json` этим bundle не обновлять. Shared `risk_engine v29` по-прежнему завышает метрику: 14-сценарная матрица идёт в research `evaluate_order()` и не может выставить `risk_limits_enforced_in_live_and_tester`.
6. Paper mark/index/tiers/queue и обязательный evidence writer — изолированная копия готова и uncommitted в `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Wings/p1-paper-execution-calibration` на `p1/paper-execution-calibration` @ `9dee208e5`. `qualifying=false`; queue/depth не смоделированы. Пакет не открывает 90-дневный paper. В live-дерево не переносить. Каталог `TLI-Wings/p1-paper-execution-calibration-cal` не создавался и не является путём merge.
7. Funding research не повторять по исчерпанным публичным источникам. Historical optimizer, 90-дневный paper и любой live остаются закрытыми.
8. P1-F timestamp parser изолированная копия готова и uncommitted в `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Wings/p1f-strict-timestamps` на `p1f/strict-timestamp-parser` @ `9dee208e5`. Live `_integer()` по-прежнему коэрцирует `int(1.9) -> 1`. В live-дерево не копировать. Второй P1-F не запускать. Не merge-ready до v22. Новый engineering-пакет до v22 не запускать.

Проверено 18 августа 2026 года, 19:44 UTC, без merge и без `research-gates` на shared status: live `master` не содержит `src/research/risk_evidence.py`; live `p0d_local` отсутствует; `status.json` `updated_at_utc` остаётся `2026-08-18T17:27:50Z`; v5 жив (PID `81180`). Worktree держит uncommitted `risk_evidence.py`, wiring `experiment_manifest`/`research_gates`, тесты и local bundle. Fixture/research-policy и actual probe v3 не являются production admission; production schema — `risk-parity-production-evidence-v1`. Не merge-ready до v22.

Проверено 18 августа 2026 года, 19:50 UTC, document-only: live `master` не содержит `src/research/paper_market_surfaces.py`; в `TLI-Wings/p1-paper-execution-calibration` нет `data_passport.py` и `risk_evidence.py`. v5: `result.json` существует, `status=failed`, `candidate_admission=false`, `shadow_duration_hours≈4.032`, `shadow_planned_orders=5`, `shadow_determinism_runs=0`, `shadow_replay_count=0`; `process_supervisor.json` отсутствует; `runs/shadow-readiness-2026-08-18-v22` отсутствует. Supervisor PID `81180` жив, текущий child `61667` (`strategy_shadow_stream`, `--duration-seconds 14520`); прежний child `81189` уже не жив. Watcher PID `57680` жив и в логе имеет только `watcher_started`; он ждёт оба файла и не застрял. Failed `result.json` не переписывать и не принимать как v22. Порог `shadow_planned_orders >= 20` не ослаблять. `status.json` `updated_at_utc` по-прежнему `2026-08-18T17:27:50Z`.

Проверено 18 августа 2026 года, 19:53 UTC, document-only plan review: 1.62 совпадает с machine truth. `result.json` — промежуточный сегментный файл при живом supervisor/child, не финал. P1-F timestamp parser тогда ещё считался in-flight. P1-G не закрывает queue/depth. Production risk и qualifying paper не утверждаются. Подробный срез — в implementation review, секция 19:53 UTC.

Проверено 18 августа 2026 года, 20:14 UTC, document-only: live `master` @ `9dee208e5` сохраняет старый `_integer()` (`int(1.9) -> 1`); live `src/`/`tests/`/`passivbot-rust/`/`configs/` этим пакетом не менялись (live `bitget_market_surfaces.py` mtime `2026-08-17 16:33`). Worktree `TLI-Wings/p1f-strict-timestamps` / `p1f/strict-timestamp-parser` @ `9dee208e5` uncommitted; нет `data_passport.py` / `risk_evidence.py` / `paper_market_surfaces.py`. Parser отклоняет float/bool/NaN/overflow и принимает digit strings / int64 bounds. v5: supervisor `81180`, child `61667`, watcher `57680` живы; `result.json` `status=failed`, `planned_order_count=5`, `covered_duration_hours≈4.032`, `candidate_admission=false`; `process_supervisor.json` нет; `runs/shadow-readiness-2026-08-18-v22` нет. Freeze остаётся «ждать v22». Порог `shadow_planned_orders >= 20` не ослаблять. `status.json` `updated_at_utc` по-прежнему `2026-08-18T17:27:50Z`.

Проверено 18 августа 2026 года, 20:42 UTC, document-only: следующего изолированного engineering-пакета кроме wait-for-v22 и хвоста queue/depth в `TLI-Wings/p1-paper-execution-calibration` нет. `account_risk_surface` по плану нужен только перед technical live — сейчас не начинать. Новый worktree не создавался. v5: supervisor `81180` (~5 ч), child `61667` (~57 мин, `--duration-seconds 14520`, `--side long`), watcher `57680` живы и ждут оба файла. Промежуточный `result.json` / `segments/segment-001-result.json` по-прежнему `status=failed`, `planned_order_count=5`, `covered_duration_hours≈4.032`, `candidate_admission=false`, replay/determinism 0; mtime `result.json` не двигался после сегмента 1, тогда как `stream_state.json` уже `market_event_count=284`, `checkpoint.json` `event_count=2282`. `process_supervisor.json` нет. `runs/shadow-readiness-2026-08-18-v22` нет. Это не финал и не повод ослаблять `shadow_planned_orders >= 20` или запускать второй shadow. `status.json` `updated_at_utc` по-прежнему `2026-08-18T17:27:50Z`.

## Следующий этап 1.63 — полный промежуточный срез 21:37 UTC, freeze до v22 — 18 августа 2026 года

Это document-only постановка. Цель не меняется: развивать Passivbot, а не второго бота; первая цель — BTC/USDT perpetual на Bitget; исследовательский капитал 1 000 USDT; isolated hedge; плечо не выше 3×; просадка 20% и жёсткая остановка 25%; Long и Short допускаются независимо. Торговая готовность остаётся нулевой: нет кандидата, нет полного funding, 90-дневный paper и реальные этапы не начаты. Срез 1.63 **не** перезапускал полный baseline; evidence v78 остаётся каноном.

Контрольный live-срез v5 на `2026-08-18T21:37Z`: supervisor PID `81180` (~5h55), child `61667` (~1h52 сегмента 2 из 14520 с), watcher `57680`, `--side long`, `fixture_mode=false`. Live counters: 339 market events, 2722 typed events. Restart `restart-0001` completed, recovery 1,442 с. Сегментный `result.json` заморожен `@ 19:45:30Z`: `status=failed`, `candidate_admission=false`, `planned_order_count=5`, ~4,03 ч, replay 0. `process_supervisor.json` нет. Каталог v22 нет. Журнал: 686 `order.planned` (681 hold/skip, 5 enter/exit); fills 5, последний `@ 16:04:14Z`. Это Long-only transport/recovery, не квалификация Long+Short.

ETH sidecar `eth-public-market-24h-2026-08-18-v1` завершился: wall-clock 4,017 ч, 231 rows, integrity `status=failed` (12 errors на 3 транспортные строки SSL/timeout/DNS), funding continuity passed. Процесс `67935` мёртв. Имя `24h` не является evidence. Не перезапускать.

Freeze implementation подтверждён: в `src/` / `tests/` / `configs/` / `passivbot-rust/` нет файлов новее `20:42Z`. Восемь shadow `CODE_PATHS` не менялись после старта v5 (`15:42:32Z`). Live не содержит `risk_evidence.py`, `data_passport.py`, `paper_market_surfaces.py`. Live `_integer()` по-прежнему `int(1.9) -> 1`.

### Сейчас — до машинного v22

Live checkout `TLI-Wings/main` на `master` (`9dee208e5`) заморожен для `src/`, `tests/`, `passivbot-rust/` и `configs/`. Документация и generated reports вне implementation fingerprint.

1. Не останавливать и не дублировать `public-stream-process-24h-2026-08-18-v5`. Не перезаписывать сегментный `result.json`. Не писать `process_supervisor.json` вручную.
2. Не запускать `baseline-2026-08-18-v83`, не пересобирать public/risk/data/historical пакеты и не писать `status.json` вручную.
3. Принять будущий v22 как `passed`, `failed` или `blocked` без перетолкования. Порог `shadow_planned_orders >= 20` не ослаблять. Сегмент 1 уже имеет 4 stream errors; финальный v5/v22 скорее `failed`.
4. После появления `process_supervisor.json` сразу проверить, что принятый `result.json` содержит nested `process_supervisor` и cumulative wall-clock, а не payload сегмента 1 (`covered_duration_hours≈4.032`, mtime `19:45:30Z`). Если v22 сгорел на stale result — принять фактический failed/blocked, не запускать второй shadow и не патчить live `CODE_PATHS`.
5. P0-E не merge: worktree `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Wings/p0e-data-passport-clean`, ветка `p0e/data-passport-clean`, файлы uncommitted. Грязный sibling `TLI-Wings/p0e-data-passport` / `p0e/data-passport` не использовать для merge review.
6. P0-D изолированная копия готова и uncommitted в `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Wings/p0d-production-risk-evidence` на `codex/p0d-production-risk-evidence-20260818`. Kitchen-sink: после v22 вынимать только production-path файлы. Schema `risk-parity-production-evidence-v1`. Shared `risk_engine v29` по-прежнему завышает метрику.
7. Paper mark/index/tiers/evidence writer — изолированная копия в `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Wings/p1-paper-execution-calibration` на `p1/paper-execution-calibration` @ `9dee208e5`. `qualifying=false`; queue/depth модель есть при bound book, calibration artifact отсутствует. Queue/depth **не стартовать в этой пачке**. Каталог `TLI-Wings/p1-paper-execution-calibration-cal` не использовать.
8. Funding research не повторять по исчерпанным публичным источникам. Historical optimizer, 90-дневный paper, ETH rerun и любой live остаются закрытыми.
9. P1-F timestamp parser изолированная копия готова и uncommitted в `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Wings/p1f-strict-timestamps`. Live `_integer()` по-прежнему коэрцирует `int(1.9) -> 1`. Второй P1-F не запускать.
10. Единственный обязательный саб-агент до v22 — read-only runtime reviewer. Новый worktree не создавать. `account_risk_surface` не начинать.

Проверено 18 августа 2026 года, 22:04 UTC, document-only после записи 1.63: supervisor `81180`, child `61667`, watcher `57680` живы. Live counters уже 364 market events / 2922 typed events; сегментный `result.json` без nested `process_supervisor`, mtime `19:45:30Z`; `process_supervisor.json` нет; v22 нет; v83 нет. Watcher спит по 60 с, пока нет обоих файлов, затем делает три SHA-256 пробы `result`/`supervisor`/`events`/`dataset` с паузой 5 с; это сужает, но не отменяет гонку. ETH не перезапускался. Queue/depth, merge и второй shadow не запускались. `status.json` `updated_at_utc` по-прежнему `2026-08-18T17:27:50Z`.

Проверено 18 августа 2026 года, 22:07 UTC, read-only continue: те же PIDs живы; counters 367 / 2946; `result.json` всё ещё сегмент 1 (`failed`, 5 planned, без `process_supervisor`); `process_supervisor.json` нет; v22 нет; второй shadow нет. Queue/depth не стартовал.

Проверено 18 августа 2026 года, 22:17 UTC, read-only continue: те же PIDs живы (supervisor `81180` ~6h35, child `61667` ~2h32 сегмента 2 из 14520 с, watcher `57680`); counters 377 / 3026; сегментный `result.json` без nested `process_supervisor`; `process_supervisor.json` нет; v22 нет; v83 нет. ETH/queue-depth/merge/второй shadow не запускались.

### После v23 — исторический слой 1.65

Один интегратор принял immutable bundle v23 как есть: `check_status=passed`, admission `blocked`. v23 читал финальный NAS `result.json` (nested `process_supervisor`, wall-clock 92638.6 с), не Mac сегмент 1 и не `pre-resume-result.json`. Контейнер `exit=0` в `2026-08-20T00:38:00Z`. `status.json` обновлён штатно в `2026-08-20T00:43:04Z`.

### Ближайшее окно 1.74 — вход funding от пользователя

Один интегратор, без ослабления gates и без повторных исчерпанных GET:

1. Публичный полный архив 24 августа не найден (248/2332). Не покупать данные без явного разрешения.
2. Когда появится файл или ключ: `bitget-funding-archive`, затем `bitget-funding-promote` в **новый** output path. Frozen input не перезаписывать.
3. BL-24/MP-24/RE-24/SR-24 уже bound. Exam-контейнеры не стартовать. CODE_PATHS не менять без нового 24h.

### Историческое окно 1.73 — funding

Закрыто в 1.74 как неудачная публичная сборка. BL-24/MP-24/RE-24/SR-24 остались bound. Data/historical ждут полный архив.

### Историческое окно 1.72 — следующие тесты

Закрыто в 1.73: BL-24 5/5, MP-24 4/4, RE-24 35/35, SR-24 40/40 admission passed на `f5af0643…`. Один интегратор, без `docker start` exam-контейнеров и без ослабления gates:

1. Честный `BL-24` (`baseline-2026-08-24-rebind`) на `7a8be7892` / `f5af0643…`. Три последовательных полных pytest, fake-live, `cargotest.sh`. Не v83. Не reuse BL-20/`d46be477…`.
2. Затем `MP-24` на тех же public surfaces и `RE-24` production-path (не `p0d_local`).
3. Затем `SR-24` (`shadow-readiness-2026-08-24-exam7`) на `result.mac-path-bind-2026-08-24.json`. Не перезаписывать SR-21/v24/EX-6/EX-7 `result.json`.
4. Не менять восемь `CODE_PATHS`. Queue/depth не стартовать. ETH не перезапускать. `status.json` только штатной командой.

### Историческое окно 1.71 — дождаться финала v7

Закрыто в 1.72: v7 `passed`, файлы на месте, локальная копия и path-bind готовы. Штатный bind выполнен в 1.73 как SR-24.

### Историческое окно 1.70 — funding, затем явный 24h

Закрыто в 1.71 запуском NAS v7 по явному запросу пользователя. Один интегратор, без `docker start` exam-контейнера и без ослабления gates:

1. v85, v49, v31 и v25 уже bound. Не v83. Не писать `status.json` вручную.
2. Data/historical ждут полный funding archive. Не покупать данные и ключи.
3. v24 не перезаписывать. Не делать `docker start` контейнера `passivbot-shadow`. Queue/depth не стартовать.

### Историческое окно 1.69 — production-path risk_engine

Закрыто в 1.70 как `risk-engine-2026-08-21-v31` + `shadow-readiness-2026-08-21-v25`. Один интегратор, без второго 24h shadow и без ослабления gates:

1. v85 и v49 уже bound. Не v83. Не писать `status.json` вручную.
2. Новый `risk_engine` только с bound `risk-parity-production-evidence-v1`. Не `p0d_local`. Не считать v30 текущим admission.
3. Новый 24-часовой shadow не начинать, пока нет fresh risk_engine. v24 не перезаписывать.
4. Не делать `docker start` контейнера `passivbot-shadow`. Queue/depth не стартовать.

### Историческое окно 1.68 — один новый baseline

Закрыто в 1.69 как `baseline-2026-08-20-v85` + `market-profile-2026-08-20-v49`. Один интегратор, без второго 24h shadow и без ослабления gates:

1. P0-E, P0-D, `p1-paper` без queue/depth и `p1f-timestamps` уже в live.
2. Один новый baseline после всего пакета. Не v83. Не писать `status.json` вручную.
3. Новый 24-часовой shadow — только если принятый код/config hash больше не совпадает с тем, что сверял v24.
4. Не делать `docker start` контейнера `passivbot-shadow`. Queue/depth не стартовать.

### Историческое окно 1.67 — merge-review после v24

Закрыто в 1.68: P0-E, P0-D, `p1-paper` без queue/depth и `p1f-timestamps` перенесены. Один интегратор, без второго 24h shadow и без ослабления gates:

1. P0-E `data_passport` уже перенесён точечно. Не копировать грязный `TLI-Wings/p0e-data-passport`.
2. P0-D production-path из `TLI-Wings/p0d-production-risk-evidence`. Kitchen-sink не копировать.
3. Затем `p1-paper` без queue/depth, затем `p1f-timestamps`.
4. Один новый baseline после всего пакета. Не v83. Не писать `status.json` вручную.
5. Новый 24-часовой shadow — только если принятый код/config hash больше не совпадает с тем, что сверял v24.
6. Не делать `docker start` контейнера `passivbot-shadow`.

### Историческое окно 1.66 — v84 затем v24

Один интегратор, без второго 24h shadow и без ослабления gates:

1. Targeted shadow/process/gate тесты на текущем дереве.
2. `baseline-2026-08-20-v84` — plan-binding на v78 + resume 13/13; fingerprint обязан быть `dcae540a…`.
3. Новый `market_profile` и новый `risk_engine` на тех же публичных входах, пока freshness не станет `fresh` при том же fingerprint. Карантин production-parity v29 не снимать метрикой.
4. `shadow-readiness-2026-08-20-v24` в контейнере с `/data` mapping. Принять `passed`/`failed`/`blocked` как есть.
5. Merge-review isolated копий — только если v24 admission `passed`. Порядок: `p0e-clean` → `p0d` production-path → `p1-paper` → `p1f-timestamps`.
6. Новый 24-часовой shadow — только если принятый код/config hash больше не совпадает с тем, что сверял v23/v24.
7. Не делать `docker start` контейнера `passivbot-shadow`. Не использовать `v83`.

## Исторический этап 1.65 — NAS v6 и машинный v23 — 20 августа 2026 года

Cutover 18 августа уложился в 300 с. Сбор на NAS шёл до `2026-08-19T23:09:32Z`, затем deterministic replay, затем supervisor. Контейнер `passivbot-shadow` `exit=0`, OOM=false.

- Run: `public-stream-process-24h-2026-08-19-v6`. Supervisor `2026-08-18T22:53:58Z`–`2026-08-20T00:37:57Z`, wall-clock 92638.6 с, coverage 31.43 ч, 1803 events, 65 planned, 5 process restarts, 7 driver restarts, 3 replay, 0 stream errors, max source gap 262.3 с, `candidate_admission=false`, nested `process_supervisor`.
- Readiness: `shadow-readiness-2026-08-19-v23`, `check_status=passed`, `stage_admission_status=blocked`, `freshness=fresh`. 40/40 shadow-gates. Blocker: stale baseline/market_profile/risk_engine.
- Копия: `analysis_reports/crypto_bot_roadmap/shadow_runs/public-stream-process-24h-2026-08-19-v6/`.
- v23 не перезаписывать. Второй 24h из-за blocked admission не запускать.

## Исторический этап 1.66 — перепривязка freshness и v24 — 20 августа 2026 года

Один интегратор закрыл окно без второго 24h и без `docker start`:

- Targeted 63 теста: `test_strategy_shadow_stream`, `test_strategy_shadow_stream_process`, `test_research_gates`.
- `baseline-2026-08-20-v84`: plan-binding на evidence v78, fingerprint `dcae540a…` совпал в metrics и manifest, `passed`/`fresh`.
- `market-profile-2026-08-20-v48` и `risk-engine-2026-08-20-v30`: те же public входы, `passed`/`fresh`. Карантин production-parity сохранён.
- `shadow-readiness-2026-08-20-v24`: 40/40 gates, admission passed, freshness fresh, NAS v6 `result.json`. `status.json` `updated_at_utc=2026-08-20T10:41:13Z`.
- v23, v82, v47, v29 — historical. v83 не существовал.

## Исторический этап 1.64 — перенос shadow на Synology Docker — 19 августа 2026 года

Пользователь явно отменил freeze «додержать Mac v5 до v22»: ноутбук завтра закроется на работе, sleep убивает source-gap. Решение: public-only контейнер на DS923+, cutover journal если ≤300 с, иначе честный пустой v6.

Сделано в этой редакции:

- `--resume-existing-output` в supervisor: непустой незавершённый каталог, integrity log, отказ при stale source или существующем `process_supervisor.json`, сохранение Mac `segment-*.json`, переименование сегментного `result.json` в `pre-resume-result.json`.
- Targeted tests: 13/13 в `tests/test_strategy_shadow_stream_process.py`.
- `Dockerfile_shadow`, `container/shadow/entrypoint.sh`, `container/shadow/docker-compose.synology.yml`, `container/shadow/cutover_from_mac.sh`.
- Mac watcher `57680` остановлен, чтобы не создать v22. Collector v5 остановлен на cutover.
- Проект NAS: `/volume1/docker/_cm/passivbot-shadow/`. Образ `passivbot-shadow:v6` собран native amd64 (~9,5 мин). Ключи не копируются. CFS `cpus:` на этом DSM не поддерживается — лимит убран; runtime лёгкий (опрос раз в 60 с).
- Cutover `2026-08-18T22:53:49Z`: Mac writer остановлен, journal скопирован, контейнер стартовал с `--resume-existing-output`. Source-gap Mac→NAS ≈66 с (лимит 300). `ingest_seq` 412→414, `pre-resume-result.json` на месте, Mac PID’ы мертвы. Watcher v22 не создавался.

### После полного лицензированного funding archive

Только тогда открывается календарный путь: data/preflight → четыре окна historical → заморозка кандидата или `no_suitable_candidate` → 90 дней paper → отдельный read-only UTA isolated/hedge и явное разрешение → 30 дней technical live ≤200 USDT → 90 дней на 1 000 USDT. Неполный ряд 270/2 332 и coverage `0.11578044596912522` этот путь не открывает.

## Критерий завершения

BTC готов только после полного исторического отбора, 90 дней зачётной торговли без денег, 30 дней технического реального запуска и 90 дней контролируемой торговли на 1 000 USDT.

Портфель готов после независимого отбора ETH, синхронного теста BTC+ETH, 90 дней совместной торговли без денег и отдельного ограниченного реального этапа.

Если стратегия не проходит любой этап, корректный результат — отклонение кандидата или новая версия с полностью повторённой проверкой. Отсутствие подходящего кандидата не является ошибкой процесса.
