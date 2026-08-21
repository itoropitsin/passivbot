# Проверка исследовательского запуска: market_profile

Результат: **passed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `market_profile.input_present` | True | == | True | passed |
| `market_profile.public_api_only` | True | == | True | passed |
| `market_profile.private_api_data_absent` | False | == | False | passed |
| `market_profile.source_profile_sha256_not_placeholder` | True | == | True | passed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
