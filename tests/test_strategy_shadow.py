from __future__ import annotations

import hashlib
import json

import pytest

from research.strategy_shadow import (
    StrategyShadowDriver,
    canonical_hash,
    normalize_snapshot,
    read_shadow_events,
    run_strategy_shadow,
)
from tools.strategy_shadow import main as strategy_shadow_main


def _snapshot(
    snapshot_id: str,
    timestamp: int,
    *,
    bid: float = 100_000.0,
    ask: float = 100_100.0,
    signal: str | None = None,
    long_signal: str | None = None,
    short_signal: str | None = None,
    execution: dict | None = None,
) -> dict:
    signals = {}
    if signal is not None:
        signals["long"] = signal
        signals["short"] = signal
    if long_signal is not None:
        signals["long"] = long_signal
    if short_signal is not None:
        signals["short"] = short_signal
    row = {
        "snapshot_id": snapshot_id,
        "timestamp": timestamp,
        "symbol": "BTC/USDT:USDT",
        "bid": bid,
        "ask": ask,
        "last": (bid + ask) / 2.0,
        "signals": signals,
    }
    if execution is not None:
        row["execution"] = execution
    return row


@pytest.mark.asyncio
async def test_shadow_uses_no_network_even_if_ccxt_constructor_is_forbidden(monkeypatch, tmp_path):
    import exchanges.paper as paper

    def forbidden_constructor(*_args, **_kwargs):
        raise AssertionError("the strategy shadow must not construct a network client")

    monkeypatch.setattr(paper.ccxt_async, "bitget", forbidden_constructor)
    result = await run_strategy_shadow(
        [_snapshot("s-1", 1_700_000_000_000, long_signal="enter")],
        output_dir=tmp_path / "no-network",
    )

    assert result["status"] == "passed"
    assert result["read_only"] is True
    assert result["public_snapshot_client"] is True
    assert result["external_network_calls"] == 0
    assert result["private_requests"] == 0
    assert result["trading_operations_count"] == 0
    assert result["public_write_calls"] == 0
    assert all(method in {"load_markets", "fetch_ticker", "close"} for method, _ in result["public_call_log"])


@pytest.mark.asyncio
async def test_injected_private_write_and_trading_facts_fail_closed(tmp_path):
    driver = StrategyShadowDriver(checkpoint_path=tmp_path / "checkpoint.json")
    try:
        await driver.process_snapshot(_snapshot("evidence-1", 1_700_000_000_000))
        assert driver.public_client is not None
        driver.public_client.calls.append(("fetch_private_balance", "private"))
        driver.public_client.write_calls.append(("create_order", "local"))
        driver._client_call_facts.append(
            {
                "method": "submit_order",
                "transport": "external",
                "trading_operation": True,
            }
        )

        result = driver.result()

        assert result["status"] == "failed"
        assert result["read_only"] is False
        assert result["private_requests"] == 1
        assert result["external_network_calls"] >= 2
        assert result["trading_operations_count"] >= 2
        assert result["public_write_calls"] == 1
        assert result["boundary_violations"]
        assert any("private_request" in item for item in result["boundary_violations"])
        assert any("write_call" in item for item in result["boundary_violations"])
        assert any(
            "trading_operation" in item for item in result["boundary_violations"]
        )
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_order_planning_records_intent_plan_and_paper_decision(tmp_path):
    driver = StrategyShadowDriver(
        checkpoint_path=tmp_path / "checkpoint.json",
        config={"strategy_id": "test-grid", "order_amount": 0.002, "entry_offset_bps": 10.0},
    )
    try:
        outcome = await driver.process_snapshot(
            _snapshot("plan-1", 1_700_000_000_000, long_signal="enter")
        )
        assert outcome["planned_orders"] == 1
        events = read_shadow_events(tmp_path / "events.ndjson")
        kinds = [event["kind"] for event in events]
        assert "strategy.intent" in kinds
        assert "order.planned" in kinds
        assert "order.opened" in kinds
        assert "order.execution_decision" in kinds
        plan_event = next(event for event in events if event["kind"] == "order.planned" and event["payload"]["position_side"] == "long")
        plan = plan_event["payload"]["plan"]
        assert plan["status"] == "planned"
        assert plan["order_id"]
        assert plan["planned_price"] < plan["reference_price"]
        assert plan_event["payload"]["strategy_id"] == "test-grid"
        assert plan_event["payload"]["cycle_id"]
        assert plan_event["payload"]["config_hash"] == driver.config_hash
        assert plan_event["payload"]["execution"] is None
        decision = next(event for event in events if event["kind"] == "order.execution_decision" and event["payload"]["position_side"] == "long")
        assert decision["payload"]["execution"]["executed"] is False
        assert decision["payload"]["execution"]["status"] == "open"
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_restart_recovers_resting_order_and_emits_one_fill(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    first = StrategyShadowDriver(
        checkpoint_path=checkpoint,
        config={"entry_offset_bps": 10.0},
    )
    try:
        await first.run(
            [_snapshot("s-1", 1_700_000_000_000, long_signal="enter")],
        )
    finally:
        await first.close()

    second = StrategyShadowDriver(checkpoint_path=checkpoint, config={"entry_offset_bps": 10.0})
    try:
        result = await second.run(
            [
                _snapshot("s-1", 1_700_000_000_000, long_signal="enter"),
                _snapshot(
                    "s-2",
                    1_700_000_060_000,
                    bid=99_900.0,
                    ask=99_950.0,
                    long_signal="hold",
                ),
            ]
        )
        events = read_shadow_events(tmp_path / "events.ndjson")
        fills = [event for event in events if event["kind"] == "order.filled"]
        assert result["duplicate_snapshot_count"] == 1
        assert len(fills) == 1
        assert fills[0]["payload"]["position_side"] == "long"
        assert fills[0]["payload"]["execution"]["executed"] is True
        assert result["paper_fill_count"] == 1
        actual_plans = [
            event
            for event in events
            if event["kind"] == "order.planned"
            and event["payload"]["plan"]["status"] == "planned"
        ]
        assert len(actual_plans) == 1
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_duplicate_snapshot_does_not_create_events_or_paper_fills(tmp_path):
    driver = StrategyShadowDriver(checkpoint_path=tmp_path / "checkpoint.json")
    try:
        row = _snapshot("same", 1_700_000_000_000, long_signal="enter")
        first = await driver.process_snapshot(row)
        before = read_shadow_events(tmp_path / "events.ndjson")
        fill_count = len(driver.client.fills)
        second = await driver.process_snapshot(dict(row))
        after = read_shadow_events(tmp_path / "events.ndjson")
        assert first["duplicate"] is False
        assert second["duplicate"] is True
        assert len(after) == len(before)
        assert len(driver.client.fills) == fill_count
        assert driver.result()["duplicate_snapshot_count"] == 1
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_long_and_short_are_independent_hedged_paper_positions(tmp_path):
    result = await run_strategy_shadow(
        [
            _snapshot(
                "hedge-1",
                1_700_000_000_000,
                long_signal="enter",
                short_signal="enter",
            )
        ],
        output_dir=tmp_path / "hedge",
        config={"order_amount": 0.001},
    )
    events = read_shadow_events(tmp_path / "hedge" / "events.ndjson")
    fills = [event for event in events if event["kind"] == "order.filled"]
    assert len(fills) == 2
    assert {event["payload"]["position_side"] for event in fills} == {"long", "short"}
    assert len({event["payload"]["order_id"] for event in fills}) == 2
    assert len({event["payload"]["cycle_id"] for event in fills}) == 2
    assert {position["position_side"] for position in result["positions"]} == {"long", "short"}


@pytest.mark.asyncio
async def test_fresh_replays_have_the_same_machine_result_hash(tmp_path):
    rows = [
        _snapshot("replay-1", 1_700_000_000_000, long_signal="enter"),
        _snapshot("replay-2", 1_700_000_060_000, long_signal="hold"),
    ]
    first = await run_strategy_shadow(rows, output_dir=tmp_path / "first")
    second = await run_strategy_shadow(rows, output_dir=tmp_path / "second")
    assert first["deterministic_replay_hash"] == second["deterministic_replay_hash"]
    assert first["event_log_hash"] == second["event_log_hash"]


@pytest.mark.asyncio
async def test_result_derives_coverage_provenance_and_determinism(tmp_path):
    rows = [
        _snapshot("late", 1_700_003_600_000, long_signal="hold"),
        _snapshot("early", 1_700_000_000_000, long_signal="hold"),
    ]
    result = await run_strategy_shadow(
        rows,
        output_dir=tmp_path / "evidence",
        deterministic_replay_runs=3,
        input_provenance={"source_kind": "test_fixture", "format": "memory"},
    )
    normalized = [normalize_snapshot(row, index=index) for index, row in enumerate(rows)]

    assert result["covered_start_timestamp_ms"] == 1_700_000_000_000
    assert result["covered_end_timestamp_ms"] == 1_700_003_600_000
    assert result["covered_duration_ms"] == 3_600_000
    assert result["covered_duration_hours"] == pytest.approx(1.0)
    assert result["coverage"]["duration_formula"].startswith("max(")
    assert result["normalized_snapshot_hash"] == canonical_hash(normalized)
    assert result["provenance"]["input"]["normalized_snapshot_hash"] == canonical_hash(normalized)
    assert result["restart_count"] == 0
    assert result["restart_operations"] == []
    assert result["restart_evidence"]["status"] == "not_observed"
    assert result["replay_count"] == 0
    assert result["replay_operations"] == []
    assert result["replay_evidence"]["status"] == "not_observed"
    assert result["deterministic_replay_runs"] == 3
    assert result["deterministic_replay"]["all_hashes_equal"] is True
    assert len(result["deterministic_replay"]["runs"]) == 3
    assert result["readiness_metrics"]["shadow_duration_hours"] == pytest.approx(1.0)
    assert result["readiness_metrics"]["shadow_determinism_runs"] == 3


@pytest.mark.asyncio
async def test_restart_and_replay_operations_are_persisted_as_facts(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    first = StrategyShadowDriver(checkpoint_path=checkpoint)
    try:
        await first.run([_snapshot("one", 1_700_000_000_000, long_signal="enter")])
    finally:
        await first.close()

    second = StrategyShadowDriver(checkpoint_path=checkpoint)
    try:
        result = await second.run(
            [
                _snapshot("one", 1_700_000_000_000, long_signal="enter"),
                _snapshot("two", 1_700_000_060_000, long_signal="hold"),
            ]
        )
        assert result["restart_count"] == 1
        assert result["restart_observed"] is True
        restart = result["restart_operations"][0]
        assert restart["operation"] == "restart"
        assert restart["status"] == "completed"
        assert restart["recovery_completed"] is True
        assert restart["preexisting_artifacts"]["checkpoint"]["exists"] is True
        assert restart["recovered_at_snapshot_id"] == "one"

        assert result["replay_count"] == 1
        replay = result["replay_operations"][0]
        assert replay["operation"] == "replay"
        assert replay["result"] == "no_op"
        assert replay["snapshot_id"] == "one"
        checkpoint_payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert checkpoint_payload["restart_operations"] == result["restart_operations"]
        assert checkpoint_payload["replay_operations"] == result["replay_operations"]
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_replay_with_changed_snapshot_content_is_rejected(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    driver = StrategyShadowDriver(checkpoint_path=checkpoint)
    try:
        original = _snapshot("same", 1_700_000_000_000, long_signal="hold")
        await driver.run([original])
        changed = _snapshot(
            "same",
            1_700_000_000_000,
            bid=99_900.0,
            ask=100_000.0,
            long_signal="hold",
        )
        with pytest.raises(ValueError, match="snapshot ID conflict"):
            await driver.process_snapshot(changed)
    finally:
        await driver.close()


def test_cli_result_json_contains_file_provenance_and_derived_metrics(tmp_path):
    input_path = tmp_path / "snapshots.json"
    input_path.write_text(
        json.dumps(
            [
                _snapshot("one", 1_700_000_000_000, long_signal="hold"),
                _snapshot("two", 1_700_000_060_000, long_signal="hold"),
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "cli-output"

    assert (
        strategy_shadow_main(
            [
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
                "--deterministic-replay-runs",
                "2",
            ]
        )
        == 0
    )

    result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    input_digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
    assert result["provenance"]["input"]["source_sha256"] == input_digest
    assert result["covered_duration_ms"] == 60_000
    assert result["deterministic_replay_runs"] == 2
    assert result["readiness_metrics"]["shadow_duration_hours"] == pytest.approx(1 / 60)
