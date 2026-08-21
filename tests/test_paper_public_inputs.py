from __future__ import annotations

import hashlib
import json

import pytest

from exchanges.paper import PaperBitgetCCXTClient
from research.paper_public_inputs import (
    PublicExecutionInputError,
    load_public_execution_feed,
)
from test_paper_bitget import PublicBitgetStub


SYMBOL = "BTCUSDT"
PAPER_SYMBOL = "BTC/USDT:USDT"
FUNDING_TS = 1_700_000_008_000


def _public_execution_input() -> dict:
    return {
        "schema_version": "bitget-public-execution-inputs-v1",
        "exchange": "bitget",
        "symbol": SYMBOL,
        "category": "USDT-FUTURES",
        "read_only_only": True,
        "trading_operations_count": 0,
        "funding_history": [
            {
                "symbol": SYMBOL,
                "funding_rate": 0.001,
                "timestamp_ms": FUNDING_TS,
            }
        ],
        "mark_price_history": [
            [FUNDING_TS - 3_600_000, "100000", "100100", "99900", "100000", "0"],
            [FUNDING_TS, "100001", "100101", "99901", "100001", "0"],
        ],
        "index_price_history": [],
    }


def _write_public_input(path, payload: dict) -> str:
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _client(public, *, feed_path=None, state_path=None):
    live = {
        "paper_symbols": ["BTC"],
        "leverage": 3.0,
    }
    if feed_path is not None:
        live.update(
            {
                "paper_execution_inputs_path": str(feed_path),
                "paper_execution_inputs_since_ms": FUNDING_TS - 3_600_000,
            }
        )
    return PaperBitgetCCXTClient(
        {"live": live},
        {"exchange": "bitget", "quote": "USDT", "paper": True},
        public_client=public,
        initial_balance=1_000.0,
        clock_ms=1_700_000_000_000,
        state_path=state_path,
    )


def test_public_execution_feed_keeps_file_hash_and_uses_only_prior_mark(tmp_path):
    path = tmp_path / "public-inputs.json"
    source_hash = _write_public_input(path, _public_execution_input())

    feed = load_public_execution_feed(path, symbol=SYMBOL)

    assert feed.source_hash == source_hash
    assert feed.source_schema == "bitget-public-execution-inputs-v1"
    assert len(feed.settlements) == 1
    settlement = feed.settlements[0]
    assert settlement.timestamp_ms == FUNDING_TS
    assert settlement.mark_price == pytest.approx(100_000.0)
    assert settlement.as_paper_settlement()["source_hash"] == source_hash


def test_public_execution_feed_rejects_non_read_only_input(tmp_path):
    path = tmp_path / "not-read-only.json"
    payload = _public_execution_input()
    payload["trading_operations_count"] = 1
    _write_public_input(path, payload)

    with pytest.raises(PublicExecutionInputError, match="trading operations"):
        load_public_execution_feed(path, symbol=SYMBOL)


def test_public_execution_feed_does_not_use_mark_at_or_after_settlement(tmp_path):
    path = tmp_path / "future-mark.json"
    payload = _public_execution_input()
    payload["mark_price_history"] = [
        [FUNDING_TS, "100001", "100101", "99901", "100001", "0"],
        [FUNDING_TS + 3_600_000, "100002", "100102", "99902", "100002", "0"],
    ]
    _write_public_input(path, payload)

    feed = load_public_execution_feed(path, symbol=SYMBOL)

    assert feed.settlements == ()


def test_public_execution_feed_rejects_naive_iso_timestamps(tmp_path):
    path = tmp_path / "naive-timestamp.json"
    payload = _public_execution_input()
    payload["funding_history"][0]["timestamp_ms"] = "2023-11-14T22:13:28"
    _write_public_input(path, payload)

    with pytest.raises(PublicExecutionInputError, match="explicit timezone"):
        load_public_execution_feed(path, symbol=SYMBOL)


def test_paper_public_execution_feed_rejects_multiple_symbols(tmp_path):
    input_path = tmp_path / "public-inputs.json"
    _write_public_input(input_path, _public_execution_input())
    client = PaperBitgetCCXTClient(
        {
            "live": {
                "paper_symbols": ["BTC", "ETH"],
                "leverage": 3.0,
                "paper_execution_inputs_path": str(input_path),
                "paper_execution_inputs_since_ms": FUNDING_TS - 3_600_000,
            }
        },
        {"exchange": "bitget", "quote": "USDT", "paper": True},
        public_client=PublicBitgetStub(),
        initial_balance=1_000.0,
        clock_ms=1_700_000_000_000,
    )

    with pytest.raises(ValueError, match="exactly one configured symbol"):
        client.apply_recorded_public_execution_inputs(timestamp=FUNDING_TS)


@pytest.mark.asyncio
async def test_paper_applies_each_hash_anchored_public_settlement_once(tmp_path):
    input_path = tmp_path / "public-inputs.json"
    source_hash = _write_public_input(input_path, _public_execution_input())
    state_path = tmp_path / "paper" / "ledger.json"
    client = _client(PublicBitgetStub(), feed_path=input_path, state_path=state_path)
    await client.load_markets()
    await client.create_order(
        PAPER_SYMBOL,
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "orderId": "feed-entry"},
    )

    evidence = client.apply_recorded_public_execution_inputs(timestamp=FUNDING_TS)
    balance_after = client.balance_total
    repeated = client.apply_recorded_public_execution_inputs(timestamp=FUNDING_TS)

    assert len(evidence) == 1
    assert repeated == []
    assert balance_after < 1_000.0
    funding = evidence[0]["payload"]
    assert funding["source_hash"] == source_hash
    assert funding["funding"]["amount"] == pytest.approx(-1.0)

    await client.close()
    restarted = _client(
        PublicBitgetStub(), feed_path=input_path, state_path=state_path
    )
    await restarted.load_markets()
    assert restarted.apply_recorded_public_execution_inputs(timestamp=FUNDING_TS) == []
    assert restarted.balance_total == pytest.approx(balance_after)


@pytest.mark.asyncio
async def test_paper_feed_marks_settlement_seen_when_no_position_is_open(tmp_path):
    input_path = tmp_path / "public-inputs.json"
    _write_public_input(input_path, _public_execution_input())
    client = _client(PublicBitgetStub(), feed_path=input_path)
    await client.load_markets()

    assert client.apply_recorded_public_execution_inputs(timestamp=FUNDING_TS) == []
    await client.create_order(
        PAPER_SYMBOL,
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "orderId": "late-entry"},
    )
    assert client.apply_recorded_public_execution_inputs(timestamp=FUNDING_TS) == []
