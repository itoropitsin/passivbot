# Проверка исследовательского запуска: shadow_readiness

Результат: **failed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `shadow_readiness.duration_hours` | 0.2 | >= | 24 | failed |
| `shadow_readiness.planned_orders` | 4 | >= | 20 | failed |
| `shadow_readiness.restart_count` | 0 | >= | 5 | failed |
| `shadow_readiness.max_restart_recovery_seconds` | 0 | <= | 60 | passed |
| `shadow_readiness.duplicate_orders` | 0 | == | 0 | passed |
| `shadow_readiness.unknown_positions` | 0 | == | 0 | passed |
| `shadow_readiness.private_calls` | 0 | == | 0 | passed |
| `shadow_readiness.order_creates` | 0 | == | 0 | passed |
| `shadow_readiness.transfers` | 0 | == | 0 | passed |
| `shadow_readiness.events_present` | True | == | True | passed |
| `shadow_readiness.determinism_runs` | 2 | >= | 3 | failed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
