from __future__ import annotations

import hashlib
import json

import pytest

from passivbot_monitor import (
    _monitor_record_paper_reconciliation,
    _monitor_record_reconciliation_evidence,
)
from research.runtime_evidence import summarize_runtime_events
from test_monitor_typed_runtime_events import _bot, _publisher
from test_paper_bitget import PublicBitgetStub, _client


SYMBOL = "BTC/USDT:USDT"
FUNDING_TS = 1_700_000_008_000
RECONCILIATION_TS = 1_700_000_009_000


async def _filled_client():
    client = _client(PublicBitgetStub())
    await client.load_markets()
    await client.create_order(
        SYMBOL,
        "market",
        "buy",
        0.01,
        params={
            "posSide": "long",
            "orderId": "paper-reconciliation-entry",
            "strategy_id": "bitget:paper",
            "cycle_id": "cycle-1",
            "config_hash": "config-hash-1",
        },
    )
    return client


def _funding_source_hash() -> str:
    return hashlib.sha256(b"bitget-public-funding-snapshot").hexdigest()


def _runtime_event(ts: int, kind: str, payload: dict) -> dict:
    enriched = {
        "strategy_id": "bitget:paper",
        "cycle_id": "cycle-1",
        "config_hash": "config-hash-1",
    }
    enriched.update(payload)
    return {"ts": ts, "seq": ts, "kind": kind, "tags": [], "payload": enriched}


async def _settled_client():
    client = await _filled_client()
    client.apply_funding_settlement(
        settlement_id="paper-reconciliation-funding",
        rate=0.001,
        timestamp=FUNDING_TS,
        symbol=SYMBOL,
        position_side="long",
        mark_prices={SYMBOL: 100_000.0},
        source="bitget-public-funding-snapshot",
        source_hash=_funding_source_hash(),
    )
    return client


@pytest.mark.asyncio
async def test_paper_reconciliation_matches_fills_funding_and_source_hash():
    client = await _settled_client()

    evidence = client.build_paper_reconciliation_evidence(
        timestamp=RECONCILIATION_TS,
        source="paper-local-ledger-v1",
        source_hash=_funding_source_hash(),
        strategy_id="bitget:paper",
        cycle_id="cycle-1",
        config_hash="config-hash-1",
    )

    assert [row["kind"] for row in evidence] == [
        "reconciliation.check",
        "costs.reconciled",
    ]
    assert all(row["payload"]["source_hash"] == _funding_source_hash() for row in evidence)
    assert evidence[0]["payload"]["reconciliation"]["mismatch_count"] == 0
    assert evidence[1]["payload"]["all_costs_present"] is True
    assert evidence[1]["payload"]["costs"]["fee_reconciliation_abs_usdt"] == pytest.approx(0.0)
    assert evidence[1]["payload"]["costs"]["funding_reconciliation_abs_usdt"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_complete_paper_accounting_surfaces_pass_typed_runtime_validation(tmp_path):
    client = await _settled_client()
    fill = client.fills[0]
    funding = client._paper_funding_settlements["paper-reconciliation-funding"]["evidence"]
    reconciliation = client.build_paper_reconciliation_evidence(
        timestamp=RECONCILIATION_TS,
        source_hash=_funding_source_hash(),
    )
    fill_info = fill["info"]
    rows = [
        _runtime_event(
            RECONCILIATION_TS,
            "runtime.heartbeat",
            {"equity": client.balance_total},
        ),
        _runtime_event(
            RECONCILIATION_TS,
            "market.mark_index",
            {
                "market": {
                    "mark_price": 100_000.0,
                    "index_price": 99_990.0,
                }
            },
        ),
        _runtime_event(
            RECONCILIATION_TS,
            "order.filled",
            {
                "id": fill["id"],
                "event_id": fill_info["event_id"],
                "side": fill["side"],
                "price": fill["price"],
                "fee": fill_info["typed_fee"],
                "slippage": fill_info["typed_slippage"],
            },
        ),
        _runtime_event(
            RECONCILIATION_TS,
            "funding.settled",
            {"funding": funding["payload"]["funding"]},
        ),
        _runtime_event(
            RECONCILIATION_TS,
            "margin.snapshot",
            {
                "margin": {
                    "equity": client.balance_total,
                    "gross_exposure": 1_000.0,
                    "used_margin": 333.3333333333,
                    "liquidation_distance": 0.5,
                }
            },
        ),
        _runtime_event(
            RECONCILIATION_TS,
            reconciliation[0]["kind"],
            reconciliation[0]["payload"],
        ),
        _runtime_event(
            RECONCILIATION_TS,
            reconciliation[1]["kind"],
            reconciliation[1]["payload"],
        ),
    ]
    event_path = tmp_path / "complete.ndjson"
    event_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    typed = summarize_runtime_events(event_path)["typed_evidence"]
    assert typed["status"] == "passed"
    assert typed["complete"] is True
    assert typed["fee_total_usdt"] == pytest.approx(client.realized_fees)
    assert typed["funding_total_usdt"] == pytest.approx(-1.0)
    assert typed["fee_reconciliation_abs_usdt"] == pytest.approx(0.0)
    assert typed["funding_reconciliation_abs_usdt"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_monitor_persists_only_hashed_reconciliation_evidence(tmp_path):
    client = await _settled_client()
    evidence = client.build_paper_reconciliation_evidence(
        timestamp=RECONCILIATION_TS,
        source_hash=_funding_source_hash(),
    )
    publisher = _publisher(tmp_path)
    bot = _bot(publisher)

    assert [_monitor_record_reconciliation_evidence(bot, row) for row in evidence] == [1, 1]
    invalid = dict(evidence[0])
    invalid["payload"] = dict(invalid["payload"])
    invalid["payload"]["source_hash"] = "not-a-hash"
    assert _monitor_record_reconciliation_evidence(bot, invalid) == 0

    event_path = tmp_path / "bitget" / "paper" / "events" / "current.ndjson"
    rows = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert [row["kind"] for row in rows] == [
        "reconciliation.check",
        "costs.reconciled",
    ]
    runtime = summarize_runtime_events(event_path)
    typed = runtime["typed_evidence"]
    assert typed["reconciliation_event_count"] == 1
    assert typed["cost_reconciliation_count"] == 1
    assert not any(error.startswith("reconciliation.check") for error in typed["errors"])
    assert not any(error.startswith("costs.reconciled") for error in typed["errors"])


@pytest.mark.asyncio
async def test_reconciliation_explicitly_blocks_when_funding_is_missing(tmp_path):
    client = await _filled_client()
    evidence = client.build_paper_reconciliation_evidence(
        timestamp=RECONCILIATION_TS,
        source_hash=_funding_source_hash(),
    )

    assert len(evidence) == 1
    assert evidence[0]["payload"]["reconciliation"]["mismatch_count"] == 0

    publisher = _publisher(tmp_path)
    bot = _bot(publisher)
    for row in evidence:
        assert _monitor_record_reconciliation_evidence(bot, row) == 1
    event_path = tmp_path / "bitget" / "paper" / "events" / "current.ndjson"
    runtime = summarize_runtime_events(event_path)
    typed = runtime["typed_evidence"]
    assert typed["reconciliation_event_count"] == 1
    assert typed["cost_reconciliation_count"] == 0
    assert "typed cost reconciliation evidence is missing" in typed["errors"]


def test_paper_monitor_hook_requests_only_local_reconciliation():
    calls = []

    class PaperRuntime:
        paper_mode = True

        @staticmethod
        def record_paper_reconciliation(*, timestamp):
            calls.append(timestamp)
            return [{"kind": "reconciliation.check"}, {"kind": "costs.reconciled"}]

    assert _monitor_record_paper_reconciliation(PaperRuntime(), ts=RECONCILIATION_TS) == 2
    assert calls == [RECONCILIATION_TS]

    class LiveRuntime:
        paper_mode = False

        @staticmethod
        def record_paper_reconciliation(*, timestamp):
            raise AssertionError("live runtime must not call the paper hook")

    assert _monitor_record_paper_reconciliation(LiveRuntime(), ts=RECONCILIATION_TS) == 0
