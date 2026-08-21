# Проверка исследовательского запуска: shadow_readiness

Результат: **failed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `shadow_readiness.evidence_schema_version` | crypto-strategy-shadow-evidence-v1 | == | crypto-strategy-shadow-evidence-v1 | passed |
| `shadow_readiness.evidence_status` | passed | == | passed | passed |
| `shadow_readiness.candidate_admission` | False | == | False | passed |
| `shadow_readiness.read_only` | True | == | True | passed |
| `shadow_readiness.external_network_calls` | 0 | == | 0 | passed |
| `shadow_readiness.private_requests` | 0 | == | 0 | passed |
| `shadow_readiness.trading_operations_count` | 0 | == | 0 | passed |
| `shadow_readiness.public_write_calls` | 0 | == | 0 | passed |
| `shadow_readiness.determinism_status` | passed | == | passed | passed |
| `shadow_readiness.input_provenance_present` | True | == | True | passed |
| `shadow_readiness.artifact_provenance_present` | True | == | True | passed |
| `shadow_readiness.duration_hours` | 0.03333333333333333 | >= | 24 | failed |
| `shadow_readiness.planned_orders` | 4 | >= | 20 | failed |
| `shadow_readiness.restart_count` | 0 | >= | 5 | failed |
| `shadow_readiness.max_restart_recovery_seconds` | — | — | — | blocked: invalid_metric |
| `shadow_readiness.duplicate_orders` | 0 | == | 0 | passed |
| `shadow_readiness.unknown_positions` | 0 | == | 0 | passed |
| `shadow_readiness.private_calls` | 0 | == | 0 | passed |
| `shadow_readiness.order_creates` | 0 | == | 0 | passed |
| `shadow_readiness.transfers` | 0 | == | 0 | passed |
| `shadow_readiness.events_present` | True | == | True | passed |
| `shadow_readiness.determinism_runs` | 3 | >= | 3 | passed |
| `stage.prerequisites` | — | — | — | blocked: baseline=stale, market_profile=stale, risk_engine=stale |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
