# Проверка исследовательского запуска: risk_grid

Результат: **passed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `market_profile.market_profile_input_present` | True | == | True | passed |
| `market_profile.market_profile_public_api_only` | True | == | True | passed |
| `market_profile.market_profile_private_api_data_present` | False | == | False | passed |
| `market_profile.source_profile_sha256_not_placeholder` | True | == | True | passed |
| `risk_grid.roadmap_config_eligible` | True | == | True | passed |
| `risk_grid.scenario_coverage_complete` | True | == | True | passed |
| `risk_grid.no_liquidations` | True | == | True | passed |
| `risk_grid.no_duplicate_orders` | True | == | True | passed |
| `risk_grid.limits_not_bypassed` | True | == | True | passed |
| `risk_grid.state_recovery_consistent` | True | == | True | passed |
| `risk_grid.api_outage_safe` | True | == | True | passed |
| `risk_grid.manual_position_safe` | True | == | True | passed |
| `risk_grid.ordinary_market_orders_blocked` | True | == | True | passed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
