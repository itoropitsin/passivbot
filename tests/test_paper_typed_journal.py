from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fill_events_manager import FillEvent
from monitor_publisher import MonitorPublisher
from research.runtime_evidence import summarize_runtime_events

from exchanges.paper import PaperCrashInjected
from test_paper_bitget import PublicBitgetStub, _client


def _publisher(tmp_path):
    return MonitorPublisher(
        exchange="bitget",
        user="paper",
        root_dir=str(tmp_path),
        snapshot_interval_seconds=1.0,
        checkpoint_interval_minutes=10.0,
        event_rotation_mb=128.0,
        event_rotation_minutes=60.0,
        retain_days=7.0,
        max_total_bytes=1_000_000,
        compress_rotated_segments=False,
        retain_price_ticks=True,
        retain_candles=True,
        retain_fills=True,
        price_tick_min_interval_ms=0,
        emit_completed_candles=True,
        include_raw_fill_payloads=True,
    )


class _PaperMonitorBot:
    _log_new_fill_events = __import__("passivbot").Passivbot._log_new_fill_events
    _monitor_record_event = __import__("passivbot").Passivbot._monitor_record_event
    _monitor_fill_payload = __import__("passivbot").Passivbot._monitor_fill_payload
    _monitor_record_fill_history = __import__("passivbot").Passivbot._monitor_record_fill_history

    def __init__(self, publisher):
        self.monitor_publisher = publisher
        self._health_fills = 0
        self._health_pnl = 0.0
        self.quote = "USDT"
        self.strategy_id = "bitget:paper"
        self._monitor_cycle_id = "cycle-1"
        self.config_hash = "config-hash-1"

    def _log_fill_event(self, event):
        return f"fill {event.id}"


@pytest.mark.asyncio
async def test_actual_paper_fill_reaches_monitor_and_typed_runtime_reader(tmp_path):
    state_path = tmp_path / "paper" / "ledger.json"
    client = _client(PublicBitgetStub(), state_path=state_path)
    await client.load_markets()
    await client.create_order(
        "BTC/USDT:USDT",
        "market",
        "buy",
        0.01,
        params={
            "posSide": "long",
            "clientOid": "paper-entry-1",
            "strategy_id": "bitget:paper",
            "cycle_id": "cycle-1",
            "config_hash": "config-hash-1",
        },
    )

    fill = client.fills[0]
    event = FillEvent.from_dict(client._fill_to_event(fill))
    publisher = _publisher(tmp_path / "monitor")
    bot = _PaperMonitorBot(publisher)
    bot._log_new_fill_events([event])

    event_path = tmp_path / "monitor" / "bitget" / "paper" / "events" / "current.ndjson"
    evidence = summarize_runtime_events(event_path)
    rows = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    fill_rows = [row for row in rows if row["kind"] == "order.filled"]

    assert len(fill_rows) == 1
    payload = fill_rows[0]["payload"]
    assert payload["strategy_id"] == "bitget:paper"
    assert payload["cycle_id"] == "cycle-1"
    assert payload["config_hash"] == "config-hash-1"
    assert payload["order_id"] == str(fill["order"])
    assert evidence["typed_evidence"]["typed_fee_count"] == 1
    assert evidence["typed_evidence"]["typed_slippage_count"] == 1
    assert evidence["status"] == "blocked"
    assert "typed_evidence" in evidence["verdict"]["blockers"]


def _fill_event(event_id: str, fill_id: str) -> dict:
    return {
        "event_id": event_id,
        "fill_id": fill_id,
        "order_id": f"order-{fill_id}",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "position_side": "long",
        "amount": 0.01,
        "price": 100_100.0,
        "reference_price": 100_000.0,
        "timestamp": 1_700_000_001_000,
        "liquidity": "taker",
        "strategy_id": "bitget:paper",
        "cycle_id": "cycle-1",
        "config_hash": "config-hash-1",
        "part_sequence": 1,
    }


@pytest.mark.asyncio
async def test_paper_wal_replays_after_crash_after_wal(tmp_path):
    state_path = tmp_path / "paper" / "ledger.json"
    event = _fill_event("event-after-wal", "fill-after-wal")
    client = _client(PublicBitgetStub(), state_path=state_path)
    await client.load_markets()
    client.set_paper_crash_injection("after_wal")

    with pytest.raises(PaperCrashInjected, match="after_wal"):
        client.apply_fill_event(event)

    assert client.fills == []
    wal_path = state_path.with_name("ledger.wal.ndjson")
    assert wal_path.is_file()
    wal_row = json.loads(wal_path.read_text(encoding="utf-8").splitlines()[0])
    assert wal_row["kind"] == "order.filled"
    assert wal_row["payload"]["strategy_id"] == "bitget:paper"
    assert wal_row["payload"]["cycle_id"] == "cycle-1"
    assert wal_row["payload"]["config_hash"] == "config-hash-1"
    assert wal_row["payload"]["part_sequence"] == 1
    assert wal_row["payload"]["quantity_before"] == 0.0
    assert wal_row["payload"]["quantity_after"] == 0.01
    assert set(wal_row["payload"]["fee"]) == {"amount", "currency", "rate"}
    assert wal_row["payload"]["slippage"] == {
        "reference_price": 100_000.0,
        "executed_price": 100_100.0,
    }

    evidence = summarize_runtime_events(wal_path)
    assert evidence["typed_evidence"]["typed_fee_count"] == 1
    assert evidence["typed_evidence"]["typed_slippage_count"] == 1
    assert evidence["status"] == "blocked"
    assert "typed_evidence" in evidence["verdict"]["blockers"]

    restarted = _client(PublicBitgetStub(), state_path=state_path)
    await restarted.load_markets()
    assert len(restarted.fills) == 1
    assert restarted.fills[0]["id"] == "fill-after-wal"
    balance_after_replay = restarted.balance_total
    duplicate = restarted.apply_fill_event(event)
    assert duplicate["id"] == "fill-after-wal"
    assert len(restarted.fills) == 1
    assert restarted.balance_total == pytest.approx(balance_after_replay)


@pytest.mark.asyncio
async def test_paper_wal_replays_after_crash_after_ledger_mutation(tmp_path):
    state_path = tmp_path / "paper" / "ledger.json"
    event = _fill_event("event-after-mutation", "fill-after-mutation")
    client = _client(PublicBitgetStub(), state_path=state_path)
    await client.load_markets()
    client.set_paper_crash_injection("after_ledger_mutation")

    with pytest.raises(PaperCrashInjected, match="after_ledger_mutation"):
        client.apply_fill_event(event)

    assert len(client.fills) == 1
    balance_after_mutation = client.balance_total
    snapshot_before_recovery = json.loads(state_path.read_text(encoding="utf-8"))
    assert snapshot_before_recovery["fills"] == []
    assert not list(state_path.parent.glob("*.tmp"))

    restarted = _client(PublicBitgetStub(), state_path=state_path)
    await restarted.load_markets()
    assert len(restarted.fills) == 1
    assert restarted.fills[0]["id"] == "fill-after-mutation"
    assert restarted.balance_total == pytest.approx(balance_after_mutation)

    duplicate = restarted.apply_fill_event(event)
    assert duplicate["id"] == "fill-after-mutation"
    assert len(restarted.fills) == 1
    assert restarted.balance_total == pytest.approx(balance_after_mutation)
