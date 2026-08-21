# Проверка исследовательского запуска: profile

Результат: **failed**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `profile.read_only_check` | True | == | True | passed |
| `profile.account_mode_uta` | False | == | True | failed |
| `profile.position_mode_hedge` | — | — | — | blocked: missing_metric |
| `profile.margin_mode_isolated` | — | — | — | blocked: missing_metric |
| `profile.collateral_usdt` | — | — | — | blocked: missing_metric |
| `profile.borrowing_disabled` | — | — | — | blocked: missing_metric |
| `profile.withdrawals_disabled` | — | — | — | blocked: missing_metric |
| `profile.ip_allowlist_enabled` | — | — | — | blocked: missing_metric |
| `profile.separate_long_short_leverage` | — | — | — | blocked: missing_metric |
| `profile.raw_responses_hashed` | True | == | True | passed |
| `profile.trading_operations_count_zero` | True | == | True | passed |

Any gate with status `blocked` or `failed` stops admission; limits are not weakened.
