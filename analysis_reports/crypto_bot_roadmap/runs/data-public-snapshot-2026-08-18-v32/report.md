# Проверка исследовательского запуска: data

Результат: **failed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `market_profile.market_profile_input_present` | True | == | True | passed |
| `market_profile.market_profile_public_api_only` | True | == | True | passed |
| `market_profile.market_profile_private_api_data_present` | False | == | False | passed |
| `market_profile.source_profile_sha256_not_placeholder` | True | == | True | passed |
| `data.history_through_plan_date` | True | == | True | passed |
| `data.ohlcv_cache_quality_complete` | True | == | True | passed |
| `data.funding_history_complete` | False | == | True | failed |
| `data.mark_price_history_complete` | True | == | True | passed |
| `data.position_tiers_captured` | True | == | True | passed |
| `data.execution_model_implemented` | True | == | True | passed |
| `data.history_coverage` | 1.0 | >= | 0.9995 | passed |
| `data.synthetic_rows_marked` | 0 | == | 0 | passed |
| `data.nan_rows` | 0 | == | 0 | passed |
| `data.future_data` | 0 | == | 0 | passed |
| `data.funding_coverage` | 0.11578044596912522 | >= | 1.0 | failed |
| `data.determinism` | 3 | >= | 3 | passed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
