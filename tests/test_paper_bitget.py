from __future__ import annotations

from copy import deepcopy

import pytest

from exchanges.paper import PaperBitgetCCXTClient, _paper_symbol


class PublicBitgetStub:
    def __init__(self):
        self.calls = []
        self.write_calls = []
        self.prices = {
            "BTC/USDT:USDT": {
                "bid": 100_000.0,
                "ask": 100_100.0,
                "last": 100_050.0,
                "markPrice": 99_900.0,
                "indexPrice": 99_910.0,
            },
            "ETH/USDT:USDT": {
                "bid": 4_000.0,
                "ask": 4_001.0,
                "last": 4_000.5,
                "markPrice": 3_990.0,
                "indexPrice": 3_991.0,
            },
        }
        self.markets = {
            symbol: {
                "id": symbol.replace("/", "").replace(":", "_"),
                "symbol": symbol,
                "base": symbol.split("/", 1)[0],
                "quote": "USDT",
                "settle": "USDT",
                "type": "swap",
                "swap": True,
                "linear": True,
                "inverse": False,
                "active": True,
                "precision": {"amount": 0.001, "price": 0.1},
                "limits": {"amount": {"min": 0.001}, "cost": {"min": 5.1}},
                "contractSize": 1.0,
                "maker": 0.0002,
                "taker": 0.00055,
                "info": {"source": "stub"},
            }
            for symbol in self.prices
        }

    async def load_markets(self, reload=True):
        self.calls.append(("load_markets", bool(reload)))
        return deepcopy(self.markets)

    async def fetch_ticker(self, symbol):
        self.calls.append(("fetch_ticker", symbol))
        row = deepcopy(self.prices[symbol])
        row.update({"symbol": symbol, "timestamp": 1_700_000_000_000, "info": {}})
        return row

    async def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        self.calls.append(("fetch_ohlcv", symbol, timeframe, since, limit, params or {}))
        return [[1_700_000_000_000, 100_000, 100_200, 99_900, 100_050, 10.0]]

    async def fetch_time(self):
        self.calls.append(("fetch_time",))
        return 1_700_000_000_000

    async def close(self):
        self.calls.append(("close",))


def _default_surface(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "margin_tiers": [
            {
                "symbol": symbol,
                "tier": "1",
                "min_notional": 0.0,
                "max_notional": 1_000_000.0,
                "maintenance_margin_rate": 0.004,
            }
        ],
    }


def _client(
    public,
    symbols=("BTC",),
    *,
    state_path=None,
    state_dir=None,
    bind_surface=True,
):
    client = PaperBitgetCCXTClient(
        {
            "live": {"paper_symbols": list(symbols), "leverage": 3.0},
        },
        {"exchange": "bitget", "quote": "USDT", "paper": True},
        public_client=public,
        initial_balance=1_000.0,
        clock_ms=1_700_000_000_000,
        state_path=state_path,
        state_dir=state_dir,
    )
    if bind_surface:
        for coin in symbols:
            client.bind_paper_market_surface(_default_surface(_paper_symbol(coin)))
    return client


@pytest.mark.asyncio
async def test_paper_loads_only_requested_linear_usdt_markets():
    public = PublicBitgetStub()
    client = _client(public, symbols=("BTC", "ETH"))

    markets = await client.load_markets()

    assert sorted(markets) == ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    assert client.symbols == sorted(markets)
    assert public.write_calls == []
    assert all(call[0] == "load_markets" for call in public.calls)


@pytest.mark.asyncio
async def test_paper_orders_fill_locally_at_public_bid_ask_and_update_ledger():
    public = PublicBitgetStub()
    client = _client(public)
    await client.load_markets()

    filled = await client.create_order(
        "BTC/USDT:USDT",
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "clientOid": "paper-entry"},
    )

    assert filled["status"] == "closed"
    assert filled["price"] == pytest.approx(100_100.0)
    assert filled["info"]["posSide"] == "long"
    assert len(client.fills) == 1
    assert client.fills[0]["fee"]["cost"] > 0.0
    assert client.balance_total < 1_000.0
    positions = await client.fetch_positions()
    assert positions[0]["side"] == "long"
    assert positions[0]["contracts"] == pytest.approx(0.01)
    assert positions[0]["marginMode"] == "isolated"
    assert positions[0]["markPrice"] == pytest.approx(99_900.0)
    assert positions[0]["indexPrice"] == pytest.approx(99_910.0)
    assert positions[0]["markPrice"] != pytest.approx(100_050.0)
    assert positions[0]["info"]["paperMaintenanceRate"] == pytest.approx(0.004)
    assert positions[0]["info"]["paperQualifying"] is False
    assert positions[0]["liquidationPrice"] > 0.0
    assert public.write_calls == []


@pytest.mark.asyncio
async def test_paper_limit_order_rests_then_fills_without_public_order_write():
    public = PublicBitgetStub()
    client = _client(public)
    await client.load_markets()

    order = await client.create_order(
        "BTC/USDT:USDT",
        "limit",
        "buy",
        0.01,
        price=99_000.0,
        params={"posSide": "long", "clientOid": "paper-resting"},
    )
    assert order["status"] == "open"
    assert len(await client.fetch_open_orders("BTC/USDT:USDT")) == 1

    public.prices["BTC/USDT:USDT"] = {
        "bid": 98_900.0,
        "ask": 98_950.0,
        "last": 98_925.0,
        "markPrice": 98_880.0,
        "indexPrice": 98_885.0,
    }
    await client.fetch_ticker("BTC/USDT:USDT")

    assert await client.fetch_open_orders("BTC/USDT:USDT") == []
    assert len(client.fills) == 1
    assert client.fills[0]["info"]["liquidity"] == "maker"
    assert public.write_calls == []


@pytest.mark.asyncio
async def test_paper_limit_order_supports_explicit_partial_fill_queue():
    public = PublicBitgetStub()
    client = _client(public)
    await client.load_markets()

    order = await client.create_order(
        "BTC/USDT:USDT",
        "limit",
        "buy",
        0.01,
        price=99_000.0,
        params={
            "posSide": "long",
            "clientOid": "paper-partial-queue",
            "paper_fill_amount": 0.004,
        },
    )
    assert order["status"] == "open"
    assert order["filled"] == pytest.approx(0.0)

    public.prices["BTC/USDT:USDT"] = {
        "bid": 98_900.0,
        "ask": 98_950.0,
        "last": 98_925.0,
        "markPrice": 98_880.0,
        "indexPrice": 98_885.0,
    }
    for _ in range(2):
        await client.fetch_ticker("BTC/USDT:USDT")

    assert len(client.fills) == 2
    assert [fill["amount"] for fill in client.fills] == pytest.approx(
        [0.004, 0.004]
    )
    assert [fill["info"]["part_sequence"] for fill in client.fills] == [1, 2]
    assert sum(fill["fee"]["cost"] for fill in client.fills) > 0.0
    assert order["status"] == "open"
    assert (await client.fetch_open_orders())[0]["status"] == "open"
    final_order = (await client.fetch_open_orders())[0]
    assert final_order["filled"] == pytest.approx(0.008)
    assert final_order["remaining"] == pytest.approx(0.002)

    # The final part is filled by an explicit event; no queue state is
    # inferred from a later quote once the configured queue amount is gone.
    final_fill = client.apply_fill_event(
        {
            "event_id": "partial-final-event",
            "fill_id": "partial-final-fill",
            "order_id": order["id"],
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "amount": 0.002,
            "price": 98_950.0,
            "liquidity": "maker",
            "part_sequence": 3,
        }
    )
    assert final_fill["amount"] == pytest.approx(0.002)
    assert await client.fetch_open_orders() == []
    assert len(client.fills) == 3
    assert (await client.fetch_positions())[0]["contracts"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_paper_fill_events_keep_order_open_until_all_parts_arrive():
    client = _client(PublicBitgetStub())
    await client.load_markets()
    order = await client.create_order(
        "BTC/USDT:USDT",
        "limit",
        "buy",
        0.01,
        price=99_000.0,
        params={"posSide": "long", "orderId": "paper-event-partial"},
    )

    first = client.apply_fill_event(
        {
            "event_id": "partial-event-1",
            "fill_id": "partial-fill-1",
            "order_id": order["id"],
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "amount": 0.004,
            "price": 99_000.0,
            "liquidity": "maker",
        }
    )
    assert first["amount"] == pytest.approx(0.004)
    open_order = (await client.fetch_open_orders())[0]
    assert open_order["filled"] == pytest.approx(0.004)
    assert open_order["remaining"] == pytest.approx(0.006)

    second = client.apply_fill_event(
        {
            "event_id": "partial-event-2",
            "fill_id": "partial-fill-2",
            "order_id": order["id"],
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "amount": 0.006,
            "price": 98_990.0,
            "liquidity": "maker",
            "part_sequence": 2,
        }
    )
    assert second["amount"] == pytest.approx(0.006)
    assert await client.fetch_open_orders() == []
    assert len(client.fills) == 2
    assert client.apply_fill_event({
        **{
            "event_id": "partial-event-1",
            "fill_id": "partial-fill-1",
            "order_id": order["id"],
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "amount": 0.004,
            "price": 99_000.0,
            "liquidity": "maker",
        }
    })["id"] == "partial-fill-1"


@pytest.mark.asyncio
async def test_paper_ohlcv_is_public_and_until_filter_is_explicit():
    public = PublicBitgetStub()
    client = _client(public)
    await client.load_markets()

    rows = await client.fetch_ohlcv(
        "BTC/USDT:USDT",
        since=1_699_000_000_000,
        params={"until": 1_700_000_000_000},
    )

    assert len(rows) == 1
    assert rows[0][0] == 1_700_000_000_000
    assert public.write_calls == []


def test_paper_symbol_rejects_non_usdt_settlement():
    with pytest.raises(ValueError, match="USDT-settled"):
        _paper_symbol("BTC/USDC:USDC")


@pytest.mark.asyncio
async def test_paper_rejects_missing_requested_market():
    public = PublicBitgetStub()
    client = _client(public, symbols=("SOL",))
    with pytest.raises(ValueError, match="did not contain"):
        await client.load_markets()


@pytest.mark.asyncio
async def test_paper_ledger_survives_restart_and_replays_order_once(tmp_path):
    state_path = tmp_path / "paper" / "ledger.json"
    public = PublicBitgetStub()
    client = _client(public, state_path=state_path)
    await client.load_markets()

    filled = await client.create_order(
        "BTC/USDT:USDT",
        "market",
        "buy",
        0.01,
        params={
            "posSide": "long",
            "orderId": "paper-order-41",
            "eventId": "order-event-41",
            "clientOid": "paper-client-41",
        },
    )
    resting = await client.create_order(
        "BTC/USDT:USDT",
        "limit",
        "buy",
        0.02,
        price=99_000.0,
        params={"posSide": "long", "orderId": "paper-order-42"},
    )
    balance_before_restart = client.balance_total
    next_order_before_restart = client._next_order_id
    await client.close()

    assert state_path.is_file()
    assert not list(state_path.parent.glob("*.tmp"))

    restarted = _client(PublicBitgetStub(), state_path=state_path)
    await restarted.load_markets()
    assert restarted.balance_total == pytest.approx(balance_before_restart)
    assert restarted._next_order_id == next_order_before_restart
    assert len(restarted.fills) == 1
    assert restarted.fills[0]["order"] == filled["id"]
    assert [order["id"] for order in await restarted.fetch_open_orders()] == [resting["id"]]
    positions = await restarted.fetch_positions()
    assert positions[0]["contracts"] == pytest.approx(0.01)

    replayed = await restarted.create_order(
        "BTC/USDT:USDT",
        "market",
        "buy",
        0.01,
        params={
            "posSide": "long",
            "orderId": "paper-order-41",
            "eventId": "order-event-41",
            "clientOid": "paper-client-41",
        },
    )
    assert replayed["id"] == filled["id"]
    assert len(restarted.fills) == 1
    assert restarted.balance_total == pytest.approx(balance_before_restart)
    assert (await restarted.fetch_positions())[0]["contracts"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_paper_fill_event_is_idempotent_before_and_after_restart(tmp_path):
    state_path = tmp_path / "ledger.json"
    client = _client(PublicBitgetStub(), state_path=state_path)
    await client.load_markets()
    order = await client.create_order(
        "BTC/USDT:USDT",
        "limit",
        "buy",
        0.01,
        price=99_000.0,
        params={"posSide": "long", "orderId": "paper-event-order-1"},
    )
    event = {
        "event_id": "fill-event-1",
        "fill_id": "fill-1",
        "order_id": order["id"],
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "position_side": "long",
        "amount": 0.01,
        "price": 99_000.0,
        "timestamp": 1_700_000_001_000,
        "liquidity": "maker",
    }
    first = client.apply_fill_event(event)
    balance_after_first = client.balance_total
    position_after_first = (await client.fetch_positions())[0]["contracts"]
    duplicate = client.apply_fill_event(deepcopy(event))

    assert first["id"] == "fill-1"
    assert duplicate == first
    assert len(client.fills) == 1
    assert client.balance_total == pytest.approx(balance_after_first)
    assert (await client.fetch_positions())[0]["contracts"] == pytest.approx(position_after_first)
    assert await client.fetch_open_orders() == []
    await client.close()

    restarted = _client(PublicBitgetStub(), state_path=state_path)
    await restarted.load_markets()
    replayed_after_restart = restarted.apply_fill_event(event)
    assert replayed_after_restart["id"] == "fill-1"
    assert len(restarted.fills) == 1
    assert restarted.balance_total == pytest.approx(balance_after_first)
    assert (await restarted.fetch_positions())[0]["contracts"] == pytest.approx(
        position_after_first
    )


def test_paper_smoke_is_memory_only_and_reports_model_limits():
    client = _client(PublicBitgetStub())
    status = client.get_paper_model_status()

    assert client.state_path is None
    assert status["ledger"]["status"] == "in_memory_only"
    assert status["ledger"]["idempotency"] == "event_order_fill_and_funding_ids"
    assert status["funding"]["status"] == "explicit_settlement_only"
    assert status["depth"]["status"] == "not_modeled_fail_closed"
    assert status["depth"]["queue_modeled"] is False
    assert status["depth"]["qualifying"] is False
    assert status["partial_fills"]["status"] == "explicit_amounts_supported"
    assert status["liquidation"]["status"] == "tiered_maintenance_from_bound_surface"
    assert status["qualifying"] is False
    assert status["fill_model"]["mode"] == "explicit_amounts_only"
    assert status["execution_evidence"]["qualifying"] is False


def test_paper_state_dir_is_explicit_and_uses_a_single_ledger_file(tmp_path):
    state_dir = tmp_path / "roadmap-paper-state"
    client = _client(PublicBitgetStub(), state_dir=state_dir)

    assert client.state_path == (state_dir / "paper_ledger.json").resolve()
    assert client.state_path.is_file()
    assert sorted(path.name for path in state_dir.iterdir()) == ["paper_ledger.json"]
