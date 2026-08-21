# Проверка исследовательского запуска: shadow_readiness

Результат: **blocked**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `shadow_readiness.evidence_schema_version` | — | — | — | blocked: missing_metric |
| `shadow_readiness.evidence_status` | — | — | — | blocked: missing_metric |
| `shadow_readiness.candidate_admission` | — | — | — | blocked: missing_metric |
| `shadow_readiness.read_only` | — | — | — | blocked: missing_metric |
| `shadow_readiness.external_network_calls` | — | — | — | blocked: missing_metric |
| `shadow_readiness.private_requests` | — | — | — | blocked: missing_metric |
| `shadow_readiness.trading_operations_count` | — | — | — | blocked: missing_metric |
| `shadow_readiness.public_write_calls` | — | — | — | blocked: missing_metric |
| `shadow_readiness.determinism_status` | — | — | — | blocked: missing_metric |
| `shadow_readiness.input_provenance_present` | — | — | — | blocked: missing_metric |
| `shadow_readiness.artifact_provenance_present` | — | — | — | blocked: missing_metric |
| `shadow_readiness.duration_hours` | — | — | — | blocked: missing_metric |
| `shadow_readiness.planned_orders` | — | — | — | blocked: missing_metric |
| `shadow_readiness.restart_count` | — | — | — | blocked: missing_metric |
| `shadow_readiness.max_restart_recovery_seconds` | — | — | — | blocked: missing_metric |
| `shadow_readiness.duplicate_orders` | — | — | — | blocked: missing_metric |
| `shadow_readiness.unknown_positions` | — | — | — | blocked: missing_metric |
| `shadow_readiness.private_calls` | — | — | — | blocked: missing_metric |
| `shadow_readiness.order_creates` | — | — | — | blocked: missing_metric |
| `shadow_readiness.transfers` | — | — | — | blocked: missing_metric |
| `shadow_readiness.events_present` | — | — | — | blocked: missing_metric |
| `shadow_readiness.determinism_runs` | — | — | — | blocked: missing_metric |
| `stage.prerequisites` | — | — | — | blocked: baseline=stale, market_profile=stale, risk_engine=stale |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
