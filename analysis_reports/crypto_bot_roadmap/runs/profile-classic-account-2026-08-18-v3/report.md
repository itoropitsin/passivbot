# Проверка исследовательского запуска: account_profile

Результат: **failed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `account_profile.account_profile_input_present` | True | == | True | passed |
| `account_profile.account_contract_read_only` | False | == | True | failed |
| `account_profile.read_only_check` | True | == | True | passed |
| `account_profile.account_mode_uta` | False | == | True | failed |
| `account_profile.position_mode_hedge` | False | == | True | failed |
| `account_profile.margin_mode_isolated` | False | == | True | failed |
| `account_profile.collateral_usdt` | True | == | True | passed |
| `account_profile.borrowing_disabled` | False | == | True | failed |
| `account_profile.withdrawals_disabled` | False | == | True | failed |
| `account_profile.ip_allowlist_enabled` | False | == | True | failed |
| `account_profile.separate_long_short_leverage` | False | == | True | failed |
| `account_profile.raw_responses_hashed` | True | == | True | passed |
| `account_profile.trading_operations_count_zero` | True | == | True | passed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
