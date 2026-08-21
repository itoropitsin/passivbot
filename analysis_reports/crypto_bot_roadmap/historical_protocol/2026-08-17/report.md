# Crypto historical protocol

Date: 2026-08-17
Protocol: `historical-protocol-2026-08-17-v1`
Status: **blocked**

This is a local research procedure. It cannot place orders or move funds.

Historical preflight: **blocked**

The protocol is not allowed to start. Current reasons:
- BTC: data metrics do not confirm complete funding history; execution input check failed: funding_complete; profile check failed: account_mode_uta; profile check failed: ip_allowlist_enabled; profile check failed: leverage_long_at_most_3; profile check failed: leverage_short_at_most_3; profile check failed: margin_mode_isolated; profile check failed: position_mode_hedge; profile check failed: withdrawals_disabled; roadmap stage data is not passed; roadmap stage profile is not passed; roadmap stage risk_grid is not passed
- ETH: data metrics do not confirm complete funding history; execution input check failed: funding_complete; profile check failed: account_mode_uta; profile check failed: ip_allowlist_enabled; profile check failed: leverage_long_at_most_3; profile check failed: leverage_short_at_most_3; profile check failed: margin_mode_isolated; profile check failed: position_mode_hedge; profile check failed: withdrawals_disabled; roadmap stage data is not passed; roadmap stage profile is not passed; roadmap stage risk_grid is not passed

## Fixed procedure

- Four walk-forward windows from the dated plan.
- Long and Short are optimized independently.
- Three groups of eight parameters, five distinct seeds per group.
- Generated optimizer jobs: **240**.
- Every centre candidate is checked on the next window, its four one-parameter neighbours, and the final holdout.
- BTC and ETH are later checked on one shared clock with correlation set to 1 in stress tests.

## Promotion rule

A positive result is not enough. A candidate must pass every risk, recovery, cycle-count, liquidation, stress, and neighbour gate. If no candidate passes, the output is `no suitable candidate`; no gate is weakened.

## Execution

Use `passivbot tool crypto-historical-protocol --execute` only after the read-only preflight has passed. That option runs local optimizer jobs only; candidate selection remains a separate, reviewable step.
