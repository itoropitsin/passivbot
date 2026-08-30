# Промежуточный checkpoint после продолжения внедрения

Дата проверки: **24 августа 2026 года**, Europe/Amsterdam. Дата исходного плана: **17 августа 2026 года**.

## Текущий блок редакции 1.74 — полный funding-архив публично не собран

Цель и gates не ослаблены; торгового кандидата нет. `status.json` не редактировался в этом окне (`updated_at_utc=2026-08-23T22:21:42Z`).

- Публичный UTA v3: 270 строк, в окно плана 248/2332, coverage `0.10635`. Oldest `2026-05-26T16:00Z`. Tardis/Amberdata/Laevitas/Coinank без ключа не открываются. Frozen input не перезаписывался. Evidence: [funding_archive_collect_attempt_2026-08-24.md](funding_archive_collect_attempt_2026-08-24.md).
- Следующий вход: файл пользователя или явное разрешение купить/использовать ключ. Затем `bitget-funding-archive` и `bitget-funding-promote` в новый путь. Исчерпанные публичные GET не повторять.
- Machine bound без изменений: BL-24/MP-24/RE-24/SR-24.

## Исторический блок редакции 1.73 — bound-срез BL-24/MP-24/RE-24/SR-24

Цель и gates не ослаблены; торгового кандидата нет. `status.json` обновлён штатной командой `research-gates` в `2026-08-23T22:21:42Z`, не вручную. Идентификаторы `v83`/`v86`/`v26` запрещены.

- Machine: `baseline-2026-08-24-rebind` 5/5, `market-profile-2026-08-24-rebind` 4/4, `risk-engine-2026-08-24-rebind` 35/35, `shadow-readiness-2026-08-24-exam7` 40/40 admission passed — `passed`/`fresh` на `f5af0643…` / `7a8be7892`.
- BL-24: 2641/2523/118 Python, 20 fake-live, 145 Rust, progress-dot hash `9a4a1346…`.
- RE-24: bound `production-risk-evidence-2026-08-24-v1`, snapshot `cb50c9a1…`. Не `p0d_local`. RE-21 historical.
- SR-24: EX-7, исходный `result.json` SHA-256 `5b427211…` не менялся. SR-21/v24 не перезаписаны.
- Следующее окно: funding 270/2332. Queue/depth не стартовать. ETH не перезапускать. Exam-контейнеры не стартовать. CODE_PATHS не менять без нового 24h.

## Исторический блок редакции 1.72 — v7 сдан; следующие тесты не запускались

Цель и gates не ослаблены; торгового кандидата нет. `status.json` тогда не редактировался (`updated_at_utc=2026-08-20T23:26:05Z`). Идентификатор `v83` запрещён.

- NAS v7: `passed`, wall-clock 25.52 ч, coverage 24.25 ч, 1397 snapshots, 0 stream errors, max gap 72.1 с, 51 planned/fill, replay 3/3. `result.json` SHA-256 `5b427211…` не менялся.
- Локальная копия и `result.mac-path-bind-2026-08-24.json` (56 путей). Artifact hashes совпали. v6 не перезаписывался.
- In-memory 40/40 passed; admission был бы `blocked` на fingerprint `f5af0643…` (commit `7a8be7892`, dirty=false). Штатный `research-gates` в том окне не запускался.
- Следующие тесты тогда: BL-24 → MP-24 → RE-24 → SR-24. Закрыто в 1.73.

## Исторический блок редакции 1.71 — live 24h v7 запущен

Цель и gates не ослаблены; торгового кандидата нет. `status.json` не редактировался. Идентификатор `v83` запрещён.

- Live: `public-stream-process-24h-2026-08-21-v7` в контейнере `passivbot-shadow-v7` (образ `passivbot-shadow:v6`, `SHADOW_RESUME=0`). Старт `2026-08-20T23:58:33Z`.
- Сбор подтверждён к `2026-08-21T00:03:06Z`: 5 snapshots, 40 events, `fixture_mode=false`, BTC last ≈73026→73086, source span 248 с, 0 trading operations.
- Exam `passivbot-shadow` остаётся `exited 0`. Каталог v6 не трогали. Это новый `docker run`, не `docker start`.
- Bound machine без изменений: v85/v49/v31/v25 на `d46be477…`. `shadow-readiness` для v7 не биндить до финала.
- Восемь `CODE_PATHS` не менять до финала v7. Queue/depth не стартовать. ETH не перезапускать.

## Исторический блок редакции 1.70 — risk_engine v31 и shadow v25

Цель и gates не ослаблены; торгового кандидата нет. `status.json` обновлён штатной командой `research-gates` в `2026-08-20T23:26:05Z`, не вручную. Идентификатор `v83` запрещён.

- Machine: `baseline-2026-08-20-v85`, `market-profile-2026-08-20-v49`, `risk-engine-2026-08-21-v31`, `shadow-readiness-2026-08-21-v25` — `passed`/`fresh` на `d46be477…`.
- v31: bound `production-risk-evidence-2026-08-21-v1`, 35/35 gates. Не `p0d_local`. v30 historical.
- v25: 40/40 на NAS v6. Исходный `result.json` не менялся. v24 не перезаписан. Второй 24h не запускался: code hash `b6c53f50…` совпал.
- Следующее окно: funding 270/2332. Queue/depth не стартовать. ETH не перезапускать. Контейнер не стартовать.
- Восемь `CODE_PATHS` не менялись.

## Исторический блок редакции 1.69 — baseline v85 и market_profile v49

Цель и gates не ослаблены; торгового кандидата нет. `status.json` обновлён штатной командой `research-gates` в `2026-08-20T22:11:12Z`, не вручную. Идентификатор `v83` запрещён.

- Machine: `baseline-2026-08-20-v85` и `market-profile-2026-08-20-v49` — `passed`/`fresh` на fingerprint `d46be477…`.
- v85 evidence: 2641 collected / 2523 passed / 118 skipped Python; 20 fake-live; 145 Rust; три последовательных raw hash `9a4a1346…`. Rust stamp `1b9d2604…` совпал после `cargo clean` + `maturin develop --release`.
- v49: те же public surfaces, что v47/v48.
- `risk-engine-2026-08-20-v30` и `shadow-readiness-2026-08-20-v24` — stale. v24 не перезаписывать. Контейнер не стартовать.
- Следующее окно: новый `risk_engine` с bound `risk-parity-production-evidence-v1`. Не `p0d_local`. Не второй 24h. Queue/depth не стартовать. ETH не перезапускать.
- Восемь `CODE_PATHS` не менялись.

## Исторический блок редакции 1.68 — p1-paper и p1f в live; дальше один baseline

Цель и gates не ослаблены; торгового кандидата нет. `status.json` не редактировать вручную. NAS v6, v23, окно 1.66 и merge 1.67 — закрытые факты. Идентификатор `v83` запрещён.

- Machine на момент v24: `baseline-2026-08-20-v84`, `market-profile-2026-08-20-v48`, `risk-engine-2026-08-20-v30`, `shadow-readiness-2026-08-20-v24` — `passed`/`fresh`. `status.json` `updated_at_utc=2026-08-20T10:41:13Z`. После P0-E/P0-D/`p1-paper`/`p1f` fingerprint изменился; status не переписывать этим пакетом.
- P0-E перенесён точечно. Frozen feed `qualifying=false`.
- P0-D перенесён точечно: `risk_evidence.py` + gates. Kitchen-sink не копировали. `p0d_local/` не кормить в shared status.
- `p1-paper` перенесён точечно без queue/depth: named mark/index, bound tiers, evidence writer. `qualifying=false`.
- `p1f-timestamps` перенесён точечно: `_integer()` отклоняет `1.9`.
- Targeted tests: 111 passed. Следующее окно: один новый baseline. Не v83. Не второй 24h.
- v24 не перезаписывать. Контейнер `passivbot-shadow` не стартовать. ETH не перезапускать. Queue/depth не стартовать.
- Восемь `CODE_PATHS` не менялись.

## Исторический блок редакции 1.67 — v24 passed; P0-E и P0-D в live; дальше p1-paper

Цель и gates не ослаблены; торгового кандидата нет. `status.json` не редактировать вручную. NAS v6, v23 и окно 1.66 — закрытые факты. Идентификатор `v83` запрещён.

- Machine на момент v24: `baseline-2026-08-20-v84`, `market-profile-2026-08-20-v48`, `risk-engine-2026-08-20-v30`, `shadow-readiness-2026-08-20-v24` — `passed`/`fresh`. `status.json` `updated_at_utc=2026-08-20T10:41:13Z`. После P0-E/P0-D fingerprint изменился; status не переписывать этим пакетом.
- P0-E перенесён точечно. Frozen feed `qualifying=false`.
- P0-D перенесён точечно: `risk_evidence.py` + gates. Kitchen-sink не копировали. `p0d_local/` не кормить в shared status. Research-matrix больше не открывает production-parity.
- Следующее окно: `p1-paper` без queue/depth → `p1f-timestamps` → один новый baseline. Не v83. Не второй 24h.
- v24 не перезаписывать. Контейнер `passivbot-shadow` не стартовать. ETH не перезапускать.
- Восемь `CODE_PATHS` не менялись.

## Исторический блок редакции 1.66 — перепривязка freshness, затем v24

Цель и gates не ослаблены; торгового кандидата нет. `status.json` не редактировать вручную. NAS v6 и v23 — закрытые факты 1.65. Идентификатор `v83` запрещён.

- Следующее окно: targeted shadow/process/gate тесты → `baseline-2026-08-20-v84` (plan-binding на v78, fingerprint `dcae540a…`) → новый `market_profile` и новый `risk_engine` на тех же входах → `shadow-readiness-2026-08-20-v24` на неизменном NAS `result.json`.
- Один baseline admission не откроет: schema v3 требует fresh `baseline` + `market_profile` + `risk_engine`.
- v24 запускать только `docker run --rm --entrypoint python` с `/data` mapping. Не `docker start` exam-контейнера. v23 не перезаписывать.
- Восемь shadow `CODE_PATHS` не менять. Queue/depth не стартовать. ETH не перезапускать. Isolated merge только после v24 admission: `p0e-clean` → `p0d` production-path → `p1-paper` → `p1f-timestamps`.
- Карантин production-parity `risk_engine v29` сохраняется при перепривязке.

## Исторический блок редакции 1.65 — NAS v6 сдан, v23 check passed / admission blocked

Цель и gates не ослаблены; торгового кандидата нет. `status.json` обновлён штатной командой `research-gates` в `2026-08-20T00:43:04Z`, не вручную. Полный baseline не перезапускался. v83 не запускать без отдельного решения.

- Контейнер `passivbot-shadow` завершился `exit=0`, OOM=false, `2026-08-20T00:38:00Z`. Не делать `docker start` того же compose: resume увидит completed `process_supervisor.json` и откажется.
- NAS `public-stream-process-24h-2026-08-19-v6`: nested `process_supervisor`, `status=passed`, wall-clock 92638.6 с (supervisor `2026-08-18T22:53:58Z`–`2026-08-20T00:37:57Z`), coverage 31.43 ч, 1803 events, 65 planned, 5 process restarts, 7 driver restarts, 3 replay, 0 stream errors, max source gap 262.3 с, `candidate_admission=false`. Mac URLError’ы в `policy_events` не попали в child `stream.errors`.
- Машинный пакет: [shadow-readiness-2026-08-19-v23](../runs/shadow-readiness-2026-08-19-v23/report.md). 40/40 shadow-gates passed; `stage.prerequisites` blocked: `baseline=stale`, `market_profile=stale`, `risk_engine=stale`. Fingerprint `dcae540a…` отличается от v82 из-за `--resume-existing-output` (правило 28). Повторный 24h shadow для этого не нужен; новый admission потребовал бы новый baseline и новый run_id (не перезапись v23).
- Копия журнала: `analysis_reports/crypto_bot_roadmap/shadow_runs/public-stream-process-24h-2026-08-19-v6/`.
- P0-E/P0-D/P1 paper/P1-F по-прежнему изолированы. Пока v23 admission blocked, merge-review не считать roadmap-passed. Queue/depth не стартовать. ETH sidecar не перезапускать.

## Исторический блок редакции 1.64 — перенос на Synology Docker

Пользователь явно перенёс 24-часовой public shadow на NAS. Mac sleep убивает exam (`max_source_gap_seconds=300`). Цель и gates не ослаблены; торгового кандидата нет. `status.json` не редактировался вручную. Полный baseline не перезапускался. v83 не запускать.

- `--resume-existing-output` позволяет supervisor продолжить незавершённый каталог: state files, integrity market log, отказ если live source старше 300 с или уже есть `process_supervisor.json`. Сегментный Mac `result.json` уходит в `segments/pre-resume-result.json`. Это новая версия shadow-контракта (правило 28): 24 часа wall-clock считаются от NAS supervisor’а. Mac-часы могут увеличить coverage, не сокращают ожидание.
- Targeted tests: 13/13 `tests/test_strategy_shadow_stream_process.py`.
- Docker: `Dockerfile_shadow` + compose в `/volume1/docker/_cm/passivbot-shadow/`, public-only, без ключей, без CFS `cpus:` (DSM не поддерживает). Образ `passivbot-shadow:v6` native amd64. macOS openrsync не видит порт 20022; копия через `tar|ssh`.
- Mac watcher `57680` остановлен, чтобы не создать `shadow-readiness-2026-08-18-v22`. Cutover `2026-08-18T22:53:49Z`: NAS контейнер `passivbot-shadow` running, resume принят, source-gap ≈66 с, `ingest_seq` 412→414. Readiness id после NAS: `shadow-readiness-2026-08-19-v23`.
- P0-E/P0-D/P1 paper/P1-F по-прежнему изолированы, не merge до v23. Queue/depth не стартовать. ETH sidecar не перезапускать.

## Исторический блок редакции 1.63 — полный промежуточный срез 21:37 UTC

Это document-only постановка. Цель и gates не ослаблены; торгового кандидата нет. `status.json` не редактировался вручную. Полный baseline не перезапускался.

- `baseline-2026-08-18-v82` passed/fresh как plan-binding на evidence v78. Plan SHA-256 v82: `0eeffcd9e1651621dec364f30b25376de2d8824287decdae752929f903d1a823`. Файл плана 1.62 перед 1.63 имел SHA-256 `ad8f96f4c109247e6993df2339a54f4c16a754ff5ef168235287e34d4d7d8b60`. v83 не запускать.
- Неизменность fingerprint не доказана: metrics v82 содержат `bbd19e7d…`, manifest и freshness — `35a72db6…`. Из-за этого `market_profile v47`, `risk_engine v29`, `risk_grid v49`, `data v47` и `historical v34` штатно stale.
- v5 на срезе `21:37Z`: supervisor PID `81180` жив, child `61667` жив (сегмент 2), watcher `57680` жив. `--side long`, `fixture_mode=false`. Live counters: 339 snapshots / 2722 events. Restart-0001 completed за 1,442 с. Сегментный `result.json` `@ 19:45:30Z` со `status=failed` (~4,03 ч, `shadow_planned_orders=5`, `candidate_admission=false`). `process_supervisor.json` нет, каталог `shadow-readiness-2026-08-18-v22` нет. Это не завершённый readiness-run. Журнал: 686 `order.planned` (681 hold/skip, 5 enter/exit). Порог `shadow_planned_orders >= 20` не ослаблять. Supervisor пишет `process_supervisor.json` раньше финальной перезаписи `result.json`; после появления файлов проверить nested `process_supervisor` и cumulative duration.
- ETH sidecar `eth-public-market-24h-2026-08-18-v1` завершился: 4,017 ч, 231 rows, integrity `failed` (12 errors / 3 транспортные строки). Не 24h evidence. Не перезапускать.
- Сейчас: live `src/`/`tests/`/`passivbot-rust/`/`configs/` заморожены. Единственный обязательный саб-агент — read-only runtime reviewer. Не запускать второй shadow, v83, public/risk rerun, ETH rerun, queue/depth в этой пачке или новый baseline. Не писать v22 и `process_supervisor.json` вручную. Не патчить live `CODE_PATHS`.
- P0-E остаётся в `/Users/ivan/Documents/Projects-SaaS/Trading/TLI-Setka-p0e-clean` / `p0e/data-passport-clean`, uncommitted. Sibling `TLI-Setka-p0e` не для merge.
- P0-D изолированная копия готова, uncommitted, kitchen-sink, не merge-ready до v22: `TLI-Setka-p0d` / `codex/p0d-production-risk-evidence-20260818`. После v22 вынимать только production-path файлы. Shared `risk_engine v29` по-прежнему завышает метрику.
- P1 paper изолированная копия готова, uncommitted, не merge-ready до v22: `TLI-Setka-p1-paper` / `p1/paper-execution-calibration` @ `9dee208e5`. `qualifying=false`; queue/depth calibration artifact отсутствует. Queue/depth **не стартовать в этой пачке**. Имя `TLI-Setka-p1-paper-cal` не использовать.
- P1-F timestamp parser изолированная копия готова, uncommitted, не merge-ready до v22: `TLI-Setka-p1f-timestamps`. Live `_integer()` по-прежнему делает `int(1.9) -> 1`. Второй P1-F не запускать.
- После v22 принять `passed`/`failed`/`blocked` как есть. Проверить, что v22 читал финальный `result.json`, а не сегмент 1. Merge-review: `p0e-clean` → `p0d` → `p1-paper` → `p1f-timestamps`. Один новый полный baseline — только если fingerprint изменился. Historical/paper/live по-прежнему требуют полный лицензированный funding archive.
- Runtime recheck после документов, `22:04Z`: PIDs `81180`/`61667`/`57680` живы; counters 364/2922; `process_supervisor.json` нет; v22 нет; v83 нет; watcher poll 60 с + 3 hash-пробы. ETH/queue-depth/merge/второй shadow не запускались.
- Continue `22:07Z`: те же PIDs; counters 367/2946; сегментный `result.json` без nested `process_supervisor`; v22 нет. Queue/depth не стартовал.
- Continue `22:17Z`: те же PIDs; counters 377/3026; v22 нет. Queue/depth не стартовал.

## Исторический блок редакции 1.62 — заморозка v5 до v22, 19:38 UTC; ревью 19:53 UTC; P1-F 20:14 UTC; снимок 20:42 UTC

Слой 1.62 сохранён как история freeze-постановки. Тогда: v5 жив (`81180` / `61667` / `57680`), сегментный `result.json` `failed` (~4,03 ч, 5 planned), ETH sidecar ещё описывался как running ~4h, live counters на 20:42Z были 284 / 2282. Актуальный порядок — блок 1.63 выше.

## Исторический блок редакции 1.61 — erratum после v82, 17:32 UTC

Слой 1.61 сохранён как история. Тогда: v5 жив (PIDs `81180` / `81189`, watcher `57680`), v22 нет, P0-E изолированно готов и uncommitted, P0-D ещё считался прерванным без возобновления, funding — внешний blocker. Актуальный порядок — блок 1.63 выше.

## Исторический снимок до интеграции P0

Канонический план доведён до редакции 1.48 после ревью 1 дня и 2 часов внедрения. Baseline v76 остаётся последним завершённым пакетом, но текущему дереву уже не соответствует. Целевой пакет исправлений ранее прошёл 108 тестов, затем 49 shadow/process/gate тестов; полный baseline v77 был остановлен и не считается evidence. Свежей machine-цепочки сейчас нет.

| Стадия | Run | Check | Admission |
|---|---|---|---|
| baseline | `baseline-2026-08-18-v76` | исторически `passed`, сейчас `stale` | blocked до нового baseline после P0 evidence-hygiene |
| market profile | `market-profile-2026-08-18-v45` | `passed` / stale | blocked |
| account profile | `profile-classic-account-2026-08-18-v31` | `failed` / stale | blocked |
| data | `data-public-snapshot-2026-08-18-v44` | `failed` / stale | blocked |
| risk engine | `risk-engine-2026-08-18-v27` | `passed` / stale | blocked |
| risk contract | `risk-contract-2026-08-18-v54` | `failed` / stale | blocked |
| risk scenarios | `risk-grid-2026-08-18-v47` | `passed` / stale | blocked |
| historical | `historical-rejected-2026-08-18-v31` | `failed` / stale | blocked |
| strategy shadow readiness | v3 stopped partial; no accepted result | not started | blocked |

Baseline v76 имеет `checksums.json` и прошедшую независимую проверку целостности; торговый допуск не выдан. `status.json` последний раз обновлён в `2026-08-18T10:37:17Z` и вручную не редактировался. Read-only пересчёт штатной функцией делает baseline и все зависимые run-пакеты stale/blocked. Historical admission заблокирован неполным funding, а paper — отсутствующим принимаемым shadow result.

В baseline v76 2 559 Python-тестов собраны (2 441 прошёл, 118 пропущено), 20 fake-live и 145 Rust-тестов прошли; три последовательных Python-прогона дали одинаковый raw output SHA-256 `1580e3404460fcb6b23d738a6f1a3928b9c34e819ebeb1206e079226ef4a317b`. Текущее дерево собирает 2 560 тестов; полного прогона этой версии нет. Загруженное Rust-расширение соответствует текущему source stamp, но его binary SHA отличается от manifest v76. Параллельный полный pytest пока недопустим: в общем fake-live cache обнаружена гонка `metadata.tmp`.

Исправлены и отдельно проверены: fail-closed shadow boundary, конфликтующий `fill_id`, последний partial-fill остаток, WAL для create/cancel/funding, crash/replay, неизменяемость public inputs, source-hash reconciliation, безопасный paper startup без credentials, привязка shadow policy к конфигурации, строгий funding archive contract, запрет synthetic rows и невыравненных окон, запрет naive ISO timestamps, обязательный top-level millisecond source timestamp и контроль обратного source-time порядка. Horizon probe теперь валидирует все raw rows до фильтрации и отдельно показывает malformed/duplicate/foreign-symbol evidence. `bitget-funding-promote` создаёт новый execution-input только после полной проверки внешнего архива и никогда не перезаписывает frozen input. Новый `strategy_shadow` driver формирует локальные intent/plan/order/fill и typed events; public stream supervisor сохраняет PID/state/recovery и связывает результат с хэшами кода, конфигурации и входа. Supervisor теперь использует отдельную process group и fail-safe cleanup при SIGINT/SIGTERM. v2 остановлен после аудита: его хэши снимались до закрытия журналов, а segment-local counters смешивались с cumulative; orphan child остановлен. v3 также остановлен до завершения и сохранён как partial evidence, readiness не считается.

## Историческое решение (18 августа)

Цель не меняется: BTC/USDT perpetual на Bitget, затем BTC+ETH; Long и Short допускаются независимо; капитал, плечо, isolated hedge mode и риск-пределы остаются зафиксированными в каноническом плане.

На срезе 18 августа historical optimizer, 90-дневный paper или live были закрыты до fingerprint/output isolation и fault-injection. Этот слой закрыт последующими редакциями; актуальный вход — блок 1.74 выше. Никакие заявки, переводы или изменения счёта не выполнялись.

## Ревью процесса после 1 дня и 2 часов

- На снимке: 37 изменённых отслеживаемых файлов, 6 784 добавленные строки и 10 588 untracked-файлов.
- 7 147 untracked-файлов находятся в `monitor/fake`, 3 193 — в generated roadmap tree.
- Создано 359 machine run-каталогов: 74 baseline и 19 shadow-readiness. Это повторная работа, а не дополнительный допуск.
- Fingerprint до этой документальной правки: `cbf4c42c9a55f499f508db3fcfd3a7fdd12ce552a13a1976ef7831bcbd9e0d75`; v76 хранит `70ce02…`.
- Текущий fingerprint хэширует почти все untracked-файлы, поэтому monitor outputs и правка плана могут сделать baseline stale.
- Shadow v2 и v3 содержат только partial snapshots; финальных `result.json`/`process_supervisor.json` нет. Активного shadow-процесса на момент снимка нет.
- Последний funding rerun: 270 валидных строк, 266 в точном окне, 2 066 ожидаемых точек отсутствуют.

Ускорение: независимые Luna Max агенты делают fingerprint, test-output isolation, shadow fault test и funding research в непересекающихся областях. Один интегратор принимает изменения, владеет canonical plan/status/shared contracts и запускает только один baseline после заморозки.

## Предыдущий снимок до интеграции P0

Все текущие run-пакеты относятся к одному `dirty_diff_sha256`:

`95ffd062d6cfdf010bff71dbcb496095e22e20b497ce909b4b89900b78d435b5`

| Стадия | Run | Результат |
|---|---|---|
| baseline | `baseline-2026-08-18-v47` | `passed` |
| market profile | `market-profile-2026-08-18-v19` | `passed` |
| account profile | `profile-classic-account-2026-08-18-v5` | `failed` |
| data | `data-public-snapshot-2026-08-18-v18` | `failed` |
| risk contract | `risk-contract-2026-08-18-v27` | `failed` |
| risk scenarios | `risk-grid-2026-08-18-v20` | `blocked`; внутренние 14 из 14 проверок прошли |
| historical | `historical-rejected-2026-08-18-v4` | `failed`; новый протокол не запускался |

Контрольные суммы всех семи пакетов пересчитаны: ошибок целостности нет. Historical v4 использует public-only профиль: private markers — 0.

## Архивные проверки до текущей интеграции

- Полный Python-набор выполнен три раза. Каждый запуск завершился успешно и дал одинаковый hash вывода `989a4818ee6a8d6b0852d99beade70d5123d76f38b93ed4f0277658e67562d94`; число тестов — 2 448.
- Медленный fake-live набор: 20 тестов, успешно.
- Rust: 145 тестов, успешно.
- Paper/shadow targeted: 36 тестов, успешно, но аудит показал отсутствующие регрессии.
- Risk/parity targeted: 58 тестов, успешно.
- Evidence/historical targeted: 58 тестов, успешно.
- `git diff --check`: успешно.
- Public transport smoke через `PYTHONPATH=src .venv/bin/python -m tools.bitget_paper_shadow`: один market observation, два события, ноль fills, funding, private calls, order creates и transfers; verdict `no_trade_observed`.
- Прямой запуск `PYTHONPATH=src .venv/bin/python src/tools/bitget_paper_shadow.py` падает до старта из-за импорта соседнего `src/tools/trailing_diagnostics.py` вместо `src/trailing_diagnostics.py`.

## Архивные ошибки до текущей интеграции

1. Shadow verdict не зависит от всех safety checks. Private либо order вызов может быть записан как нарушенный check, но итог всё равно стать `no_trade_observed` с кодом 0.
2. Повторный `fill_id` с другим amount/price/order может вернуть старый fill без конфликта.
3. Автоматический partial fill использует постоянный chunk. Для заявки `0.01` и chunk `0.004` третий остаток `0.002` приводит к ошибке превышения remainder.
4. Fill имеет WAL, но создание, отмена и funding могут изменить память до гарантированного crash-safe журнала. Сбой сохранения теряет переход после restart.
5. Public execution input перечитывается и не закрепляется одним хэшем на весь run.
6. Reconciliation принимает любой синтаксически корректный SHA-256 и может вывести нулевое slippage только по наличию typed-поля.
7. Обычный paper startup читает полный credential record до выбора paper mode, хотя публичному клиенту секреты не нужны.
8. Status schema v2 проверяет свежесть только у `passed`; `failed` и `blocked` могут быть от старого дерева. Она также смешивает результат проверки с допуском стадии.
9. Risk probe 6/6 сравнивает узкую часть решения. Projected position, tier, liquidation distance, HSL и daily loss не доказаны на ненейтральных сценариях; diagnostic внутри артефакта всё ещё содержит старое `adapter_pending`.
10. Historical preflight выбирает private profile, часть конфигураций указывает `caches/ohlcv` вместо `caches/ohlcvs`, а maker fee `0.0004` не отделена от сохранённой биржевой `0.0002` как явный стресс.

## Архивный снимок данных

BTC и ETH minute OHLCV содержат по 1 118 881 валидной строке; mark/index содержат по 18 649 часовых строк. Эти части пригодны как вход после сохранения их хэшей.

Замороженный funding-файл содержит 270 из 2 332 ожидаемых моментов. Архивный аудит получил 270 строк с официальных страниц, из которых 267 попали в точное окно; последний rerun получил 270 валидных строк и 266 в точном окне. Это разные snapshots. Ни один не позволяет открыть data gate; пропуски не синтезируются. Подробности: [`funding_horizon_rerun_2026-08-18.md`](funding_horizon_rerun_2026-08-18.md) и [`funding_archive_next_audit_2026-08-18.md`](funding_archive_next_audit_2026-08-18.md).

## Актуальный следующий порядок внедрения

### Выполнено и подтверждено локально

- `RoadmapStatusV3`, раздельные `check_status`/`stage_admission_status`/`outcome`/`freshness`, stale propagation и атомарная запись статуса.
- Paper WAL, конфликтующие идентификаторы, partial fills, crash/replay, reconciliation и paper-only credential boundary.
- Strategy shadow driver, public stream, process supervisor, source-time policy, heartbeat filtering, raw/strategy log separation и deterministic replay.
- Локальный `risk_engine_surface`: 14 сценариев, 15 targeted-тестов, совпадение Python/Rust и 0 внешних операций.
- Historical public-only preflight: проверка символа, биржи, таймфрейма, точного окна, cache path и запрет скрытого remote fetch.

Эти пункты не означают допуска исторического отбора или торговли. Они подтверждают только локальные контракты и защитные границы.

### Следующий блок 1 — стабильное evidence

Область записи: fingerprint/manifest, fake-live/monitor test helpers и их тесты. Общий `status.json` изменяет только интегратор.

- добавить `implementation_fingerprint_v2`, отдельные plan/config/data hashes и freshness-only режим без создания run-пакета;
- исключить runtime/generated outputs из implementation hash без удаления существующих файлов;
- перевести fake-live и monitor tests на отдельные временные roots;
- доказать, что targeted-тест и правка документации не меняют implementation hash, а изменение исполняемого файла меняет;
- доказать, что параллельные тесты не делят `metadata.tmp` и не создают новые файлы в `monitor/fake`.

До принятия этого блока полный baseline и новый wall-clock shadow не запускать.

### Следующий блок 2 — process fault boundary

Область записи: `strategy_shadow_stream_process` и его узкие тесты; общий plan/status не изменять.

- завершить direct child и его grandchild после сигнала;
- смоделировать аварийный child exit и восстановление checkpoint;
- подтвердить отсутствие повторных order/fill IDs и orphan processes;
- не считать пять плановых rotations заменой этого fault-injection теста.

### Параллельный research — полный funding source

- получить полный ряд Bitget BTCUSDT USDT perpetual за 2024-07-01…2026-08-17 либо формально зафиксировать внешний blocker;
- сохранить исходник, способ получения, лицензию/условия, дату, хэш и raw ответы;
- проверить ожидаемую historical cadence, дубликаты, пропуски, будущие строки, чужие символы и скрытую синтетику;
- не перезаписывать frozen input и не открывать data gate неполным рядом.

### После интеграции P0

1. Интегратор замораживает дерево и выполняет targeted tests, lint, `git diff --check`, затем ровно один полный baseline.
2. После свежего baseline запускает новую 24-часовую public shadow version: минимум 24 часа, 20 плановых заявок, 5 process rotations, 3 replay и ноль внешних записей.
3. Параллельно funding research продолжается независимо. Недостаток funding не блокирует сбор shadow evidence, но блокирует data/preflight/historical.

### Последующие gates

1. После полного funding: повторить `data` и historical preflight, затем запустить четыре окна отбора и holdout по каноническому плану.
2. После одновременно пройденных historical и shadow readiness: начать 90-дневный paper; дата старта считается только с первого полного accepted run.
3. Перед technical live получить отдельный read-only UTA isolated/hedge/account risk surface и явное разрешение пользователя. Текущий Classic Account этого не подтверждает.
4. После 30-дневного technical live и отдельного разрешения — 90 дней на 1 000 USDT. Затем отдельно повторить полный цикл для ETH и BTC+ETH.

## Разделение работы между Luna Max моделями

- Один агент — implementation fingerprint и manifest-тесты.
- Один агент — fake-live/monitor output isolation.
- Один агент — shadow descendant cleanup и crash/replay тест, без запуска 24-часового run.
- Один research-agent — funding archive и условия источника, без изменения frozen input.
- Один интегратор владеет каноническим планом, `status.json`, общей схемой и shared Rust risk. Он просматривает каждый результат и запускает единую последовательную цепочку.

Luna Max используется как постоянный инструмент параллельного исследования и поэтапной инженерной проверки. Он не получает разрешения на реальные заявки, переводы, изменение счёта, создание ключей или чтение секретов.

## Официальные Bitget поверхности для будущей read-only проверки

- [UTA overview](https://www.bitget.com/api-doc/uta/intro)
- [UTA changelog](https://www.bitget.com/api-doc/uta/changelog)
- [Account info: IP whitelist и permissions](https://www.bitget.com/api-doc/uta/account/Get-Account-Info)
- [Funding history](https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate)
- [Position tiers](https://www.bitget.com/api-doc/uta/public/Get-Position-Tier-Data)

Эти endpoint нужны для read-only доказательств. Их наличие не разрешает приватную запись или торговлю.
