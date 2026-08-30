# Проверка исследовательского запуска: shadow_readiness

Результат: **passed**.

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
| `shadow_readiness.artifact_hashes_match` | True | == | True | passed |
| `shadow_readiness.market_profile_binding_present` | True | == | True | passed |
| `shadow_readiness.market_profile_binding_matches_input` | True | == | True | passed |
| `shadow_readiness.market_profile_contract_valid` | True | == | True | passed |
| `shadow_readiness.duration_hours` | 24.248715833333332 | >= | 24 | passed |
| `shadow_readiness.planned_orders` | 51 | >= | 20 | passed |
| `shadow_readiness.restart_count` | 5 | >= | 5 | passed |
| `shadow_readiness.max_restart_recovery_seconds` | 2.288444 | <= | 60 | passed |
| `shadow_readiness.duplicate_orders` | 0 | == | 0 | passed |
| `shadow_readiness.unknown_positions` | 0 | == | 0 | passed |
| `shadow_readiness.private_calls` | 0 | == | 0 | passed |
| `shadow_readiness.order_creates` | 0 | == | 0 | passed |
| `shadow_readiness.transfers` | 0 | == | 0 | passed |
| `shadow_readiness.events_input_present` | True | == | True | passed |
| `shadow_readiness.events_present` | True | == | True | passed |
| `shadow_readiness.determinism_runs` | 3 | >= | 3 | passed |
| `shadow_readiness.process_evidence_present` | True | == | True | passed |
| `shadow_readiness.process_supervisor_status` | passed | == | passed | passed |
| `shadow_readiness.wall_clock_duration_seconds` | 91858.71615639376 | >= | 86400 | passed |
| `shadow_readiness.process_restart_status` | passed | == | passed | passed |
| `shadow_readiness.process_restart_count` | 5 | >= | 5 | passed |
| `shadow_readiness.process_max_recovery_seconds` | 0.05777716636657715 | <= | 60 | passed |
| `shadow_readiness.process_duplicate_state_count` | 0 | == | 0 | passed |
| `shadow_readiness.stream_fixture_mode` | False | == | False | passed |
| `shadow_readiness.process_code_hash_matches_current` | True | == | True | passed |
| `shadow_readiness.process_config_hash_matches_config` | True | == | True | passed |
| `shadow_readiness.process_policy_config_hash_matches_config` | True | == | True | passed |
| `shadow_readiness.process_input_hash_matches_dataset` | True | == | True | passed |
| `shadow_readiness.stream_gap_check_status` | passed | == | passed | passed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
