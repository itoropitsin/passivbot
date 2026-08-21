# Проверка исследовательского запуска: profile

Результат: **blocked**.

| Gate | Fact | Condition | Limit | Status |
|---|---:|:---:|---:|---|
| `profile.read_only_check` | — | — | — | blocked: missing_metric |
| `profile.account_mode_uta` | — | — | — | blocked: missing_metric |
| `profile.position_mode_hedge` | — | — | — | blocked: missing_metric |
| `profile.margin_mode_isolated` | — | — | — | blocked: missing_metric |
| `profile.collateral_usdt` | — | — | — | blocked: missing_metric |
| `profile.borrowing_disabled` | — | — | — | blocked: missing_metric |
| `profile.withdrawals_disabled` | — | — | — | blocked: missing_metric |
| `profile.ip_allowlist_enabled` | — | — | — | blocked: missing_metric |
| `profile.separate_long_short_leverage` | — | — | — | blocked: missing_metric |
| `profile.raw_responses_hashed` | — | — | — | blocked: missing_metric |
| `profile.trading_operations_count_zero` | — | — | — | blocked: missing_metric |

Отсутствующий показатель блокирует допуск. Неудачный gate не разрешается обходить изменением предела.
