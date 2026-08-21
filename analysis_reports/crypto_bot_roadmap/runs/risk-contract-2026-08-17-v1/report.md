# Проверка исследовательского запуска: risk_contract

Результат: **blocked**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `risk_contract.risk_contract_implemented` | — | — | — | blocked: missing_metric |
| `risk_contract.live_tester_decisions_identical` | — | — | — | blocked: missing_metric |
| `risk_contract.risk_limits_enforced_in_live_and_tester` | — | — | — | blocked: missing_metric |
| `stage.prerequisites` | — | — | — | blocked: baseline=stale |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
