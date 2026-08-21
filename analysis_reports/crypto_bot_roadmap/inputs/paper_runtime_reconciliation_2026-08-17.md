# Paper runtime reconciliation — 17 августа 2026

## Result

The local Bitget paper ledger now produces two kinds of evidence:

- `reconciliation.check` compares free/total balance, typed fill fees, explicit funding settlements and known position keys;
- `costs.reconciled` compares fee and signed funding totals and is written only when every fill has typed fee/slippage and at least one explicit funding settlement is present.

Both records carry a named source and a SHA-256 source hash. The monitor accepts only records with that provenance. A paper monitor snapshot requests this local reconciliation automatically; it never fetches funding, creates a settlement, calls a private Bitget endpoint or sends an order.

If funding is missing, the monitor writes the reconciliation result but omits `costs.reconciled`. This keeps the period visibly incomplete without allowing an early invalid cost record to poison a later complete period. The runtime validator therefore remains `blocked` until a complete cost record and all other typed surfaces exist.

## Verification

The focused regression set passed: 44 tests. It covers:

- fee and signed funding arithmetic;
- source hash validation;
- complete typed accounting acceptance;
- missing-funding fail-closed behavior;
- the paper-only monitor hook;
- restart/idempotency and existing paper WAL behavior.

The full baseline was repeated three times with the same captured-output SHA-256:

`1c59579fa169974618b89b5ac174ef3b061c500d6d04a1efd10425167679721a`

No real Bitget request, order, transfer or account setting was used.

## Still not proven

- historical funding for the full training window;
- exchange-authoritative account reconciliation or isolated liquidation state;
- queue position, partial fills and real funding ingestion in the paper runner;
- a seven-day shadow-run;
- the 90-day paper period or any real-money stage.

The next safe step is to feed a captured, hash-anchored public funding input into the paper runtime and run the separate seven-day exploratory shadow period. Its days do not count toward the 90-day paper gate.
