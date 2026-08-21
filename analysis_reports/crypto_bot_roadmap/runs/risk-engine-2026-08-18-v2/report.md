# Проверка исследовательского запуска: risk_engine

Результат: **blocked**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `risk_engine.risk_engine_implemented` | True | == | True | passed |
| `risk_engine.risk_snapshot_contract_present` | True | == | True | passed |
| `risk_engine.parity_harness_present` | True | == | True | passed |
| `risk_engine.live_tester_decisions_identical` | True | == | True | passed |
| `risk_engine.risk_limits_enforced_in_live_and_tester` | True | == | True | passed |
| `risk_engine.risk_parity_reached` | True | == | True | passed |
| `risk_engine.risk_engine_fails_closed_on_mismatch` | True | == | True | passed |
| `risk_engine.risk_engine_surface_validated` | — | — | — | blocked: missing_metric |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
