from __future__ import annotations

import json

import pytest

from tli_wings.paper_wal import (
    GENESIS_CHECKSUM,
    PAPER_WAL_SCHEMA,
    PaperLedger,
    PaperWAL,
    StateMismatchError,
    UnknownStateError,
    WALIntegrityError,
)


def _balance_payload(before_exists: bool, before, after):
    return {
        "surface": "balances",
        "key": "USDT",
        "operation": "set",
        "before_exists": before_exists,
        "before": before,
        "after": after,
    }


def test_append_before_mutate_is_durable_and_idempotent(tmp_path):
    path = tmp_path / "paper.wal.ndjson"
    wal = PaperWAL(path)
    calls = []
    payload = {"surface": "balances", "key": "USDT", "after": {"total": 100.0}}

    def mutate():
        assert path.read_text(encoding="utf-8").strip()
        calls.append("mutated")
        return "ok"

    assert wal.append_before_mutate("balance-1", "balance.updated", payload, mutate) == "ok"
    assert wal.append_before_mutate("balance-1", "balance.updated", payload, mutate)["event_id"] == "balance-1"
    assert calls == ["mutated"]

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["wal_schema"] == PAPER_WAL_SCHEMA
    assert rows[0]["previous_checksum"] == GENESIS_CHECKSUM
    assert rows[0]["checksum"] == wal.last_checksum


def test_recovery_replays_a_durable_event_after_mutator_failure(tmp_path):
    path = tmp_path / "paper.wal.ndjson"
    payload = _balance_payload(False, None, {"total": 100.0})
    wal = PaperWAL(path)

    def interrupted():
        raise RuntimeError("interrupted after append")

    with pytest.raises(RuntimeError, match="interrupted"):
        wal.append_before_mutate("balance-1", "balance.updated", payload, interrupted)

    restarted = PaperLedger(path)
    assert restarted.state["balances"] == {"USDT": {"total": 100.0}}
    assert restarted.state_is_complete is False
    restarted.require_known("balances")


def test_recovery_covers_all_paper_surfaces_and_is_idempotent(tmp_path):
    path = tmp_path / "paper.wal.ndjson"
    ledger = PaperLedger(path)
    ledger.set_balance("balance-1", "USDT", {"total": 100.0})
    ledger.set_position("position-1", "BTC", {"size": 0.01}, position_side="long")
    ledger.create_order(
        "order-1",
        "order-1",
        {"id": "order-1", "symbol": "BTC/USDT:USDT", "status": "open"},
    )
    ledger.set_mark("mark-1", "BTC/USDT:USDT", 100_000.0)
    ledger.set_index("index-1", "BTC/USDT:USDT", 99_999.0)
    ledger.set_tier("tier-1", "long", {"name": "green"})
    ledger.settle_funding(
        "funding-1",
        "funding-1",
        {"rate": 0.001, "amount": -1.0, "currency": "USDT"},
        balance_updates={"USDT": {"total": 99.0}},
    )

    recovered = PaperLedger(path)
    assert recovered.state["balances"] == {"USDT": {"total": 99.0}}
    assert recovered.state["positions"] == {"BTC|long": {"size": 0.01}}
    assert recovered.state["orders"]["order-1"]["status"] == "open"
    assert recovered.state["funding"]["funding-1"]["rate"] == 0.001
    assert recovered.state["mark"] == {"BTC/USDT:USDT": 100_000.0}
    assert recovered.state["index"] == {"BTC/USDT:USDT": 99_999.0}
    assert recovered.state["tiers"] == {"long": {"name": "green"}}
    assert recovered.state_is_complete is True
    assert recovered.recover(require_complete=True) == recovered.state.to_dict()

    duplicate = recovered.set_mark("mark-1", "BTC/USDT:USDT", 100_000.0)
    assert duplicate["event_id"] == "mark-1"
    assert len(recovered.wal.records()) == 7


def test_event_id_conflict_and_state_mismatch_fail_closed(tmp_path):
    wal = PaperWAL(tmp_path / "paper.wal.ndjson")
    wal.append_event("event-1", "balance.updated", {"value": 1})
    with pytest.raises(WALIntegrityError, match="conflicting payload"):
        wal.append_event("event-1", "balance.updated", {"value": 2})

    ledger = PaperLedger(tmp_path / "ledger.wal.ndjson")
    ledger.set_balance("balance-1", "USDT", {"total": 100.0})
    mismatching = _balance_payload(True, {"total": 90.0}, {"total": 80.0})
    with pytest.raises(StateMismatchError, match="before-state differs"):
        ledger.apply_event("balance-2", "balance.updated", mismatching)
    assert ledger.state["balances"] == {"USDT": {"total": 100.0}}


def test_unknown_state_is_rejected_before_recovery(tmp_path):
    ledger = PaperLedger(tmp_path / "paper.wal.ndjson")
    with pytest.raises(UnknownStateError, match="unknown"):
        ledger.apply_event(
            "unknown-1",
            "balance.updated",
            {"surface": "balances", "key": "USDT", "after": {"status": "unknown"}},
        )
    assert ledger.wal.records() == []


def test_checksum_chain_tampering_is_not_repaired(tmp_path):
    path = tmp_path / "paper.wal.ndjson"
    wal = PaperWAL(path)
    wal.append_event("event-1", "balance.updated", {"value": 1})
    row = json.loads(path.read_text(encoding="utf-8"))
    row["payload"]["value"] = 2
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(WALIntegrityError, match="checksum mismatch"):
        PaperWAL(path)
