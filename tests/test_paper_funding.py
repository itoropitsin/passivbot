from __future__ import annotations

import copy
import json
import math

import pytest

from research.runtime_evidence import summarize_runtime_events

from test_paper_bitget import PublicBitgetStub, _client


SYMBOL = "BTC/USDT:USDT"
SETTLEMENT_TS = 1_700_000_008_000


async def _client_with_long_position(*, state_path=None):
    client = _client(PublicBitgetStub(), state_path=state_path)
    await client.load_markets()
    await client.create_order(
        SYMBOL,
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "orderId": "paper-funding-entry"},
    )
    return client


def _settle(client, settlement_id="funding-1", **overrides):
    request = {
        "settlement_id": settlement_id,
        "rate": 0.001,
        "timestamp": SETTLEMENT_TS,
        "symbol": SYMBOL,
        "position_side": "long",
        "mark_prices": {SYMBOL: 100_000.0},
    }
    request.update(overrides)
    return client.apply_funding_settlement(**request)


@pytest.mark.asyncio
async def test_paper_funding_applies_signed_accounting_and_runtime_typed_evidence(
    tmp_path,
):
    client = await _client_with_long_position()
    balance_before = client.balance_total
    realized_pnl_before = client.realized_pnl
    realized_fees_before = client.realized_fees
    public_calls_before = copy.deepcopy(client.public_request_log)

    evidence = _settle(client)
    payload = evidence["payload"]
    funding = payload["funding"]

    assert evidence["kind"] == "funding.settled"
    assert evidence["ts"] == SETTLEMENT_TS
    assert funding == {
        "amount": pytest.approx(-1.0),
        "rate": pytest.approx(0.001),
        "notional": pytest.approx(1_000.0),
        "currency": "USDT",
    }
    assert payload["settlement_id"] == "funding-1"
    assert payload["balance_before"] == pytest.approx(balance_before)
    assert payload["balance_after"] == pytest.approx(balance_before - 1.0)
    assert client.balance_total == pytest.approx(balance_before - 1.0)
    assert client.balance_free == pytest.approx(balance_before - 1.0)
    assert client.realized_pnl == pytest.approx(realized_pnl_before - 1.0)
    assert client.realized_funding == pytest.approx(-1.0)
    assert client.realized_fees == pytest.approx(realized_fees_before)
    assert client.public_request_log == public_calls_before
    assert client.public_client.write_calls == []

    event_path = tmp_path / "funding.ndjson"
    event_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
    runtime = summarize_runtime_events(event_path)
    assert runtime["typed_evidence"]["funding_event_count"] == 1
    assert not any(
        "funding amount" in error
        for error in runtime["typed_evidence"]["errors"]
    )


@pytest.mark.asyncio
async def test_paper_funding_duplicate_settlement_is_idempotent_after_restart(tmp_path):
    state_path = tmp_path / "paper" / "ledger.json"
    client = await _client_with_long_position(state_path=state_path)
    first = _settle(client, settlement_id="funding-replay")
    balance_after_first = client.balance_total
    realized_funding_after_first = client.realized_funding
    await client.close()

    restarted = _client(PublicBitgetStub(), state_path=state_path)
    await restarted.load_markets()
    duplicate = _settle(restarted, settlement_id="funding-replay")

    assert duplicate == first
    assert restarted.balance_total == pytest.approx(balance_after_first)
    assert restarted.balance_free == pytest.approx(balance_after_first)
    assert restarted.realized_funding == pytest.approx(realized_funding_after_first)
    assert len(restarted.fills) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"rate": None}, "rate"),
        ({"timestamp": None}, "timestamp"),
        ({"rate": math.nan}, "rate"),
        ({"rate": 1.01}, "rate"),
        ({"timestamp": 1.5}, "timestamp"),
        ({"mark_prices": {SYMBOL: math.inf}}, "mark price"),
    ],
)
async def test_paper_funding_rejects_malformed_or_missing_inputs(overrides, message):
    client = _client(PublicBitgetStub())
    await client.load_markets()
    balance_before = client.balance_total

    with pytest.raises(ValueError, match=message):
        _settle(client, settlement_id="malformed-funding", **overrides)

    assert client.balance_total == pytest.approx(balance_before)
    assert client.realized_funding == pytest.approx(0.0)
    assert client._paper_funding_settlements == {}


@pytest.mark.asyncio
async def test_paper_funding_without_a_local_position_fails_explicitly():
    client = _client(PublicBitgetStub())
    await client.load_markets()
    balance_before = client.balance_total

    with pytest.raises(ValueError, match="no open.*paper position"):
        _settle(client, settlement_id="no-position")

    assert client.balance_total == pytest.approx(balance_before)
    assert client.realized_pnl == pytest.approx(0.0)
    assert client.realized_funding == pytest.approx(0.0)
    assert client._paper_funding_settlements == {}
