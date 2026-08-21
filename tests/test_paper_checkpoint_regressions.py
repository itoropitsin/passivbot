from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from procedures import load_user_info
from exchanges.paper import PaperCrashInjected
from test_paper_bitget import PublicBitgetStub, _client
from test_paper_public_inputs import (
    FUNDING_TS,
    _public_execution_input,
    _write_public_input,
)
from test_paper_reconciliation import _settled_client


SYMBOL = "BTC/USDT:USDT"
FUNDING_RATE = 0.001


def _fill_event(*, event_id: str, fill_id: str, order_id: str, price: float) -> dict:
    return {
        "event_id": event_id,
        "fill_id": fill_id,
        "order_id": order_id,
        "symbol": SYMBOL,
        "side": "buy",
        "position_side": "long",
        "amount": 0.01,
        "price": price,
        "timestamp": 1_700_000_001_000,
        "liquidity": "maker",
    }


@pytest.mark.asyncio
async def test_reused_fill_id_with_different_order_is_rejected():
    client = _client(PublicBitgetStub())
    await client.load_markets()
    first_order = await client.create_order(
        SYMBOL,
        "limit",
        "buy",
        0.01,
        price=99_000.0,
        params={"posSide": "long", "orderId": "fill-id-order-1"},
    )
    client.apply_fill_event(
        _fill_event(
            event_id="fill-id-event-1",
            fill_id="same-fill-id",
            order_id=first_order["id"],
            price=99_000.0,
        )
    )
    second_order = await client.create_order(
        SYMBOL,
        "limit",
        "buy",
        0.01,
        price=98_900.0,
        params={"posSide": "long", "orderId": "fill-id-order-2"},
    )

    with pytest.raises(ValueError, match="fill_id conflicts with a different order"):
        client.apply_fill_event(
            _fill_event(
                event_id="fill-id-event-2",
                fill_id="same-fill-id",
                order_id=second_order["id"],
                price=98_900.0,
            )
        )


@pytest.mark.asyncio
async def test_automatic_partial_fill_caps_final_chunk_to_remaining():
    public = PublicBitgetStub()
    client = _client(public)
    await client.load_markets()
    order = await client.create_order(
        SYMBOL,
        "limit",
        "buy",
        0.01,
        price=99_000.0,
        params={
            "posSide": "long",
            "orderId": "automatic-partial-order",
            "paper_fill_amount": 0.004,
        },
    )
    public.prices[SYMBOL] = {
        "bid": 98_900.0,
        "ask": 98_950.0,
        "last": 98_925.0,
    }

    for _ in range(3):
        await client.fetch_ticker(SYMBOL)

    assert [fill["amount"] for fill in client.fills] == pytest.approx(
        [0.004, 0.004, 0.002]
    )
    stored_order = client._paper_orders[order["id"]]
    assert stored_order["status"] == "closed"
    assert stored_order["remaining"] == pytest.approx(0.0)
    assert await client.fetch_open_orders(SYMBOL) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_point", ["after_wal", "after_ledger_mutation"])
async def test_order_create_wal_recovers_after_both_crash_boundaries(tmp_path, crash_point):
    state_path = tmp_path / "paper" / "ledger.json"
    client = _client(PublicBitgetStub(), state_path=state_path)
    await client.load_markets()
    client.set_paper_crash_injection(crash_point)

    with pytest.raises(PaperCrashInjected, match=crash_point):
        await client.create_order(
            SYMBOL,
            "limit",
            "buy",
            0.01,
            price=99_000.0,
            params={"posSide": "long", "orderId": "order-wal-recovery"},
        )

    restarted = _client(PublicBitgetStub(), state_path=state_path)
    await restarted.load_markets()
    orders = await restarted.fetch_open_orders(SYMBOL)
    assert len(orders) == 1
    assert orders[0]["id"] == "order-wal-recovery"


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_point", ["after_wal", "after_ledger_mutation"])
async def test_order_cancel_wal_recovers_after_both_crash_boundaries(tmp_path, crash_point):
    state_path = tmp_path / "paper" / "ledger.json"
    client = _client(PublicBitgetStub(), state_path=state_path)
    await client.load_markets()
    order = await client.create_order(
        SYMBOL,
        "limit",
        "buy",
        0.01,
        price=99_000.0,
        params={"posSide": "long", "orderId": "cancel-wal-recovery"},
    )
    client.set_paper_crash_injection(crash_point)

    with pytest.raises(PaperCrashInjected, match=crash_point):
        await client.cancel_order(order["id"], SYMBOL)

    restarted = _client(PublicBitgetStub(), state_path=state_path)
    await restarted.load_markets()
    assert await restarted.fetch_open_orders(SYMBOL) == []
    assert restarted._paper_orders[order["id"]]["status"] == "canceled"


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_point", ["after_wal", "after_ledger_mutation"])
async def test_funding_wal_recovers_once_after_both_crash_boundaries(tmp_path, crash_point):
    state_path = tmp_path / "paper" / "ledger.json"
    client = _client(PublicBitgetStub(), state_path=state_path)
    await client.load_markets()
    await client.create_order(
        SYMBOL,
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "orderId": "funding-wal-entry"},
    )
    balance_before = client.balance_total
    client.set_paper_crash_injection(crash_point)

    with pytest.raises(PaperCrashInjected, match=crash_point):
        client.apply_funding_settlement(
            settlement_id="funding-wal-recovery",
            rate=FUNDING_RATE,
            timestamp=1_700_000_008_000,
            symbol=SYMBOL,
            position_side="long",
            mark_price=100_000.0,
        )

    restarted = _client(PublicBitgetStub(), state_path=state_path)
    await restarted.load_markets()
    assert restarted.balance_total == pytest.approx(balance_before - 1.0)
    assert restarted.realized_funding == pytest.approx(-1.0)
    assert len(restarted._paper_funding_settlements) == 1


@pytest.mark.asyncio
async def test_three_restarts_keep_the_same_paper_state_and_wal(tmp_path):
    state_path = tmp_path / "paper" / "ledger.json"
    client = _client(PublicBitgetStub(), state_path=state_path)
    await client.load_markets()
    await client.create_order(
        SYMBOL,
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "orderId": "deterministic-restart"},
    )
    expected_state = json.loads(state_path.read_text(encoding="utf-8"))
    expected_wal = state_path.with_name("ledger.wal.ndjson").read_bytes()

    for _ in range(3):
        restarted = _client(PublicBitgetStub(), state_path=state_path)
        await restarted.load_markets()
        await restarted.close()
        assert json.loads(state_path.read_text(encoding="utf-8")) == expected_state
        assert state_path.with_name("ledger.wal.ndjson").read_bytes() == expected_wal


@pytest.mark.asyncio
async def test_public_input_hash_change_fails_on_the_next_read(tmp_path):
    input_path = tmp_path / "public-inputs.json"
    _write_public_input(input_path, _public_execution_input())
    client = _client(
        PublicBitgetStub(),
        state_path=tmp_path / "paper" / "ledger.json",
    )
    client.paper_execution_inputs_path = input_path
    client.paper_execution_inputs_since_ms = FUNDING_TS - 3_600_000
    client._load_public_execution_feed()
    input_path.write_text(
        json.dumps({**_public_execution_input(), "funding_history": []}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="artifact changed"):
        client._load_public_execution_feed()


@pytest.mark.asyncio
async def test_reconciliation_rejects_a_hash_that_is_not_the_funding_source():
    client = await _settled_client()
    wrong_hash = hashlib.sha256(b"different-source").hexdigest()

    with pytest.raises(ValueError, match="does not match a funding source"):
        client.build_paper_reconciliation_evidence(
            timestamp=1_700_000_009_000,
            source_hash=wrong_hash,
        )


def test_paper_safe_user_loading_does_not_return_credentials(tmp_path):
    api_keys = tmp_path / "api-keys.json"
    api_keys.write_text(
        json.dumps(
            {
                "paper": {
                    "exchange": "bitget",
                    "paper": True,
                    "quote": "USDT",
                    "secret": "must-not-enter-paper-client",
                    "passphrase": "must-not-enter-paper-client",
                    "options": {"apiKey": "must-not-enter-paper-client"},
                }
            }
        ),
        encoding="utf-8",
    )

    safe = load_user_info("paper", str(api_keys), safe_only=True)
    assert safe["exchange"] == "bitget"
    assert "secret" not in safe
    assert "passphrase" not in safe
    assert "options" not in safe
