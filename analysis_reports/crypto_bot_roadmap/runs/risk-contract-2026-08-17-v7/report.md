# Проверка исследовательского запуска: risk_contract

Результат: **failed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `risk_contract.risk_contract_implemented` | True | == | True | passed |
| `risk_contract.risk_snapshot_contract_present` | True | == | True | passed |
| `risk_contract.parity_harness_present` | True | == | True | passed |
| `risk_contract.live_tester_decisions_identical` | False | == | True | failed |
| `risk_contract.risk_limits_enforced_in_live_and_tester` | False | == | True | failed |
| `risk_contract.risk_parity_reached` | False | == | True | failed |
| `risk_contract.risk_contract_fails_closed_on_mismatch` | True | == | True | passed |
| `stage.prerequisites` | — | — | — | blocked: baseline=stale |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
