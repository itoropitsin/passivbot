# Проверка исследовательского запуска: risk_engine

Результат: **passed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `risk_engine.risk_engine_implemented` | True | == | True | passed |
| `risk_engine.risk_snapshot_contract_present` | True | == | True | passed |
| `risk_engine.parity_harness_present` | True | == | True | passed |
| `risk_engine.live_tester_decisions_identical` | True | == | True | passed |
| `risk_engine.risk_engine_fails_closed_on_mismatch` | True | == | True | passed |
| `risk_engine.risk_surface_matrix_validated` | True | == | True | passed |
| `risk_engine.risk_surface_matrix_artifact_bound` | True | == | True | passed |
| `risk_engine.risk_surface_matrix_source_mode` | research_policy | == | research_policy | passed |
| `risk_engine.risk_surface_matrix_fixture_mode` | True | == | True | passed |
| `risk_engine.risk_surface_matrix_scenario_count` | 14 | == | 14 | passed |
| `risk_engine.risk_surface_matrix_passed_count` | 14 | == | 14 | passed |
| `risk_engine.risk_surface_matrix_failed_count` | 0 | == | 0 | passed |
| `risk_engine.risk_production_evidence_manifest_present` | True | == | True | passed |
| `risk_engine.risk_production_evidence_contract_valid` | True | == | True | passed |
| `risk_engine.risk_production_path_validated` | True | == | True | passed |
| `risk_engine.risk_production_python_live_gate_called` | True | == | True | passed |
| `risk_engine.risk_production_python_live_gate_method` | Passivbot._apply_crypto_portfolio_risk_limits | == | Passivbot._apply_crypto_portfolio_risk_limits | passed |
| `risk_engine.risk_production_python_live_gate_engine` | python-live-gate | == | python-live-gate | passed |
| `risk_engine.risk_production_rust_backtest_called` | True | == | True | passed |
| `risk_engine.risk_production_rust_trace_present` | True | == | True | passed |
| `risk_engine.risk_production_fixture_mode` | False | == | False | passed |
| `risk_engine.risk_production_research_policy_used` | False | == | False | passed |
| `risk_engine.risk_production_candidate_admission` | False | == | False | passed |
| `risk_engine.risk_production_trading_operations_count` | 0 | == | 0 | passed |
| `risk_engine.risk_production_transfers_count` | 0 | == | 0 | passed |
| `risk_engine.risk_production_account_changes_count` | 0 | == | 0 | passed |
| `risk_engine.risk_production_probe_bound` | True | == | True | passed |
| `risk_engine.risk_production_snapshot_bound` | True | == | True | passed |
| `risk_engine.risk_production_config_bound` | True | == | True | passed |
| `risk_engine.risk_production_market_profile_bound` | True | == | True | passed |
| `risk_engine.risk_production_code_fingerprint_bound` | True | == | True | passed |
| `risk_engine.risk_production_report_bound` | True | == | True | passed |
| `risk_engine.risk_production_surface_matrix_bound` | True | == | True | passed |
| `risk_engine.risk_limits_enforced_in_live_and_tester` | True | == | True | passed |
| `risk_engine.risk_parity_reached` | True | == | True | passed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
