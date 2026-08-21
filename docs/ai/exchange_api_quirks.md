# Exchange API Quirks

Only task-specific, high-impact quirks are listed here.

## Broker Agreement Attribution

Problem:

1. Broker attribution is implemented differently per exchange: headers, CCXT options, order tags, or client order ids.
2. CCXT defaults may point attribution to CCXT, not Passivbot.
3. Removing broker code can silently break Passivbot broker agreements while trading continues normally.

Handling in Passivbot:

1. Treat broker-code handling as exchange-critical behavior.
2. Do not remove existing broker attribution without explicit user approval.
3. Broker-code registry loading must fail loudly on missing/invalid registry data and unknown exchange names.
4. For each broker-agreement exchange, verify the actual signed CCXT/raw request includes the required broker field/header/tag.
5. Add regression tests at the request-construction boundary when changing exchange sessions, signing, or order payload code.

## Bybit

### Broker referer header

Problem: Bybit broker attribution depends on the `Referer` header on order POST requests. CCXT derives this from `options["brokerId"]`, whose default may not be Passivbot.

Handling:

1. Set Bybit CCXT client `options["brokerId"]` from `broker_codes.hjson`.
2. Test that a signed `v5/order/create` request contains `Referer: passivbotbybit`.

### Closed-PnL pagination mismatch

Problem:

1. Cursor pagination has limited historical reach.
2. Time-based pagination can skip records when windows exceed page limits.

Handling in Passivbot:

1. Use hybrid pagination (cursor for recent, time-window for older).
2. Deduplicate by `orderId`.

Primary reference: `src/fill_events_manager.py` (`BybitFetcher._fetch_positions_history`).

## KuCoin Futures

### OHLCV limit behavior + sparse-minute markets

Problem:

1. Effective page size is 200 rows.
2. Illiquid symbols legitimately have missing trade minutes.

Handling:

1. Page with `limit=200`.
2. Overlap page boundaries by 1 candle to validate inter-page gaps.

## Bitget Futures

### UTA / Elite hedge-mode order direction

Problem:

1. Bitget UTA hedge-mode orders use `side` plus `posSide` for entries and closes.
2. `reduceOnly` is one-way-only in UTA and is rejected when combined with `posSide`.
3. UTA open-order responses may report close orders with `side=sell`, `posSide=long`,
   and `reduceOnly=NO`; deriving close direction from `reduceOnly` misclassifies them
   as entries.

Handling in Passivbot:

1. Send `posSide` and `clientOid` for UTA hedge-mode orders, but do not send
   `reduceOnly`.
2. Normalize UTA open orders from the explicit exchange/CCXT `side` field for
   buy/sell direction, and from `posSide` for long/short position side.
3. Keep classic Bitget v2/mix `tradeSide`/`reduceOnly` handling separate.

### `since` is effectively exclusive for OHLCV paging

Problem: naive paging can miss first candle in each page.

Handling:

1. Overlap boundaries by 1 candle.
2. Back up initial `since` by one candle on pagination start.

### Funding history has a short public API horizon

Problem:

1. The current UTA funding-history endpoint exposes `cursor` pages but the
   live BTCUSDT and ETHUSDT queries stop after 270 eight-hour records.  The
   saved page counts are `100, 100, 70, 0`; the fourth page is empty, so older
   cursors return no rows.
2. The classic v2 funding-history endpoint currently has the same effective
   horizon, so switching endpoint versions does not recover the missing years.

Handling in Passivbot:

1. Treat the missing historical range as a failed data gate, never as zero
   funding or silently forward-filled funding.  Rebuild the quality report
   with `passivbot tool bitget-data-metrics` after every new public snapshot.
2. Record public funding, mark/index prices, bid/ask, and order-book depth
   locally from the first day with `passivbot tool bitget-market-recorder`.
3. Verify the JSONL log with `--verify` and retain the generated integrity
   report beside it.
4. Do not run historical promotion until a complete, independently checked
   funding archive is available.

## OKX

### Public daily OHLCV archives

Problem:

1. OKX public candle archives are daily all-swap ZIP files, not per-symbol files.
2. Archive `vol` is contract count; Passivbot OHLCV volume should use OKX `vol_ccy`.
3. OKX archive filenames follow UTC+8 calendar days, while Passivbot cache shards
   are UTC days.

Handling:

1. Build daily URLs directly from `static.okx.com` instead of the rate-limited
   historical-data download-link API.
2. Filter exact archive instrument ids such as `BTC-USDT-SWAP` after download.
3. Combine the same named OKX archive plus the next OKX archive to materialize
   one complete UTC day.
4. Map `open_time` to timestamp and `vol_ccy` to volume before writing candles.

## Gate.io Futures

### Contract order text must start with `t-`

Problem:

1. Gate.io contract order `text` rejects values that do not start with `t-`.
2. CCXT prefixes `clientOrderId` into `text=t-...`, but raw `params["text"]` can overwrite that transformed value.

Handling:

1. Pass Passivbot custom order ids as `clientOrderId`, not raw `text`.
2. Keep broker attribution in the `X-Gate-Channel-Id` header.
3. Keep the Passivbot order-type marker inside the custom id; decoding accepts the marker inside Gate.io's `t-...` text.

### Public 1m OHLCV recent-window limit

Problem: Gate.io rejects old 1m OHLCV requests with `Candlestick too long ago. Maximum 10000 points recently are allowed`.

Handling:

1. Do not pass CCXT `until`; page forward by `since + limit`.
2. Clip 1m historical fetches to the recent-window bound and mark older spans as `no_archive`.
3. Require external OHLCV source data or another candle source for older Gate.io backtests.

## General Guidance

1. Check raw exchange payloads when CCXT abstraction is insufficient.
2. Treat intra-page gaps and inter-page gaps differently.
3. For missing data incidents, verify source data before changing logic.
