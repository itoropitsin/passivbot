# Проверка исследовательского запуска: historical

Результат: **failed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `market_profile.market_profile_input_present` | True | == | True | passed |
| `market_profile.market_profile_public_api_only` | True | == | True | passed |
| `market_profile.market_profile_private_api_data_present` | False | == | False | passed |
| `market_profile.source_profile_sha256_not_placeholder` | True | == | True | passed |
| `risk.max_drawdown` | — | — | — | blocked: missing_metric |
| `risk.liquidations` | — | — | — | blocked: missing_metric |
| `risk.max_recovery_days` | — | — | — | blocked: missing_metric |
| `risk.gross_exposure_ratio` | — | — | — | blocked: missing_metric |
| `risk.used_margin_ratio` | — | — | — | blocked: missing_metric |
| `risk.min_liquidation_distance` | — | — | — | blocked: missing_metric |
| `execution.slippage_median_bps` | — | — | — | blocked: missing_metric |
| `execution.slippage_p95_bps` | — | — | — | blocked: missing_metric |
| `execution.slippage_max_bps` | — | — | — | blocked: missing_metric |
| `risk.daily_loss` | — | — | — | blocked: missing_metric |
| `risk.reconciliation` | — | — | — | blocked: missing_metric |
| `candidate.validation_windows` | 1 | == | 4 | failed |
| `candidate.independent_optimizer_runs` | — | — | — | blocked: missing_metric |
| `candidate.sides_evaluated_independently` | — | — | — | blocked: missing_metric |
| `candidate.neighbor_checks_complete` | — | — | — | blocked: missing_metric |
| `candidate.holdout_reserved` | — | — | — | blocked: missing_metric |
| `candidate.execution_stresses_complete` | — | — | — | blocked: missing_metric |
| `candidate.validation_return` | 0 | == | 1 | failed |
| `candidate.cycles_per_window` | — | — | — | blocked: missing_metric |
| `candidate.stress_equity` | — | — | — | blocked: missing_metric |
| `candidate.neighbor_stability` | — | — | — | blocked: missing_metric |
| `candidate.events_present` | False | == | True | failed |
| `candidate.net_result` | — | — | — | blocked: missing_metric |
| `stage.prerequisites` | — | — | — | blocked: data=failed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
