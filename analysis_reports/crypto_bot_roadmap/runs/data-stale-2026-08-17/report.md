# Проверка исследовательского запуска: data

Результат: **failed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `data.history_through_plan_date` | False | == | True | failed |
| `data.funding_history_complete` | False | == | True | failed |
| `data.mark_price_history_complete` | False | == | True | failed |
| `data.position_tiers_captured` | False | == | True | failed |
| `data.execution_model_implemented` | False | == | True | failed |
| `data.history_coverage` | — | — | — | blocked: missing_metric |
| `data.synthetic_rows_marked` | — | — | — | blocked: missing_metric |
| `data.nan_rows` | — | — | — | blocked: missing_metric |
| `data.future_data` | — | — | — | blocked: missing_metric |
| `data.funding_coverage` | — | — | — | blocked: missing_metric |
| `data.determinism` | — | — | — | blocked: missing_metric |

Отсутствующий показатель блокирует допуск. Неудачный gate не разрешается обходить изменением предела.
