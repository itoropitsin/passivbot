from __future__ import annotations

import json
from types import SimpleNamespace

from monitor_publisher import MonitorPublisher
from passivbot_monitor import (
    _monitor_record_funding_evidence,
    _monitor_record_runtime_observations,
)
from research.runtime_evidence import summarize_runtime_events


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


def _bot(publisher):
    bot = SimpleNamespace(
        monitor_publisher=publisher,
        strategy_id="bitget:paper",
        _monitor_cycle_id="cycle-1",
        config_hash="config-hash-1",
        start_time_ms=1_000,
        paper_mode=True,
        paper_leverage=3.0,
        _crypto_min_liquidation_distance=0.5,
        _crypto_market_surfaces={
            "BTC/USDT:USDT": {
                "mark_price": 100_100.0,
                "index_price": 100_000.0,
                "contract_size": 1.0,
                "surface_hash": "surface-hash",
                "observed_at_ms": 1_000,
            }
        },
    )

    def record_event(kind, tags, payload=None, **kwargs):
        enriched = dict(payload or {})
        for key, value in {
            "strategy_id": bot.strategy_id,
            "cycle_id": bot._monitor_cycle_id,
            "config_hash": bot.config_hash,
        }.items():
            enriched.setdefault(key, value)
        return publisher.record_event(kind, tags, enriched, **kwargs)

    bot._monitor_record_event = record_event
    return bot


def test_runtime_observations_emit_heartbeat_market_and_paper_margin(tmp_path):
    publisher = _publisher(tmp_path)
    bot = _bot(publisher)
    snapshot = {
        "account": {
            "balance_raw": 1_000.0,
            "equity": 990.0,
            "realized_pnl_cumsum": {"current": -10.0},
        },
        "positions": {
            "BTC/USDT:USDT": {
                "long": {"size": 0.01, "price": 100_000.0},
                "short": {"size": 0.0, "price": 0.0},
            }
        },
    }

    assert _monitor_record_runtime_observations(bot, snapshot, ts=2_000) == 3

    event_path = tmp_path / "bitget" / "paper" / "events" / "current.ndjson"
    rows = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert [row["kind"] for row in rows] == [
        "runtime.heartbeat",
        "market.mark_index",
        "margin.snapshot",
    ]
    assert rows[1]["payload"]["market"] == {
        "mark_price": 100_100.0,
        "index_price": 100_000.0,
    }
    assert rows[2]["payload"]["margin"]["used_margin"] == 333.6666666666667

    evidence = summarize_runtime_events(event_path)
    assert evidence["typed_evidence"]["market_snapshot_count"] == 1
    assert evidence["typed_evidence"]["margin_snapshot_count"] == 1
    assert evidence["typed_evidence"]["status"] == "blocked"
    assert "typed funding evidence is missing" in evidence["typed_evidence"]["errors"]


def test_runtime_observations_do_not_invent_mark_or_margin_without_surfaces(tmp_path):
    publisher = _publisher(tmp_path)
    bot = _bot(publisher)
    bot._crypto_market_surfaces = {}
    snapshot = {
        "account": {"equity": 1_000.0},
        "positions": {
            "BTC/USDT:USDT": {
                "long": {"size": 0.01, "price": 100_000.0},
                "short": {"size": 0.0, "price": 0.0},
            }
        },
    }

    assert _monitor_record_runtime_observations(bot, snapshot, ts=2_000) == 1
    event_path = tmp_path / "bitget" / "paper" / "events" / "current.ndjson"
    rows = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert [row["kind"] for row in rows] == ["runtime.heartbeat"]


def test_explicit_funding_evidence_is_written_without_fetching_or_invention(tmp_path):
    publisher = _publisher(tmp_path)
    bot = _bot(publisher)
    evidence = {
        "schema_version": "crypto-paper-funding-v1",
        "ts": 3_000,
        "kind": "funding.settled",
        "tags": ["paper", "funding", "settlement"],
        "payload": {
            "settlement_id": "funding-1",
            "symbol": "BTC/USDT:USDT",
            "position_side": "long",
            "funding": {
                "amount": -1.0,
                "rate": 0.001,
                "notional": 1_000.0,
                "currency": "USDT",
            },
        },
    }

    assert _monitor_record_funding_evidence(bot, evidence) == 1

    invalid = {
        **evidence,
        "payload": {
            **evidence["payload"],
            "funding": {**evidence["payload"]["funding"], "amount": -2.0},
        },
    }
    assert _monitor_record_funding_evidence(bot, invalid) == 0

    event_path = tmp_path / "bitget" / "paper" / "events" / "current.ndjson"
    rows = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert [row["kind"] for row in rows] == ["funding.settled"]
    assert rows[0]["payload"]["funding"]["amount"] == -1.0
    summary = summarize_runtime_events(event_path)
    assert summary["typed_evidence"]["funding_event_count"] == 1
