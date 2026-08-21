# Проверка исследовательского запуска: risk_grid

Результат: **blocked**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `risk_grid.no_liquidations` | True | == | True | passed |
| `risk_grid.no_duplicate_orders` | True | == | True | passed |
| `risk_grid.limits_not_bypassed` | True | == | True | passed |
| `risk_grid.state_recovery_consistent` | True | == | True | passed |
| `risk_grid.api_outage_safe` | True | == | True | passed |
| `risk_grid.manual_position_safe` | True | == | True | passed |
| `risk_grid.ordinary_market_orders_blocked` | True | == | True | passed |
| `stage.prerequisites` | — | — | — | blocked: profile=failed, data=failed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
