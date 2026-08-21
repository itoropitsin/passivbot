from __future__ import annotations

import gzip
import json

from research.runtime_evidence import (
    DAY_MS,
    runtime_gate_metrics,
    summarize_runtime_events,
)


def _event(ts, kind, payload=None):
    event_payload = {
        "strategy_id": "test:strategy",
        "cycle_id": 0,
        "config_hash": "test-config-hash",
    }
    event_payload.update(payload or {})
    return {
        "ts": ts,
        "seq": ts,
        "kind": kind,
        "tags": [],
        "payload": event_payload,
    }


def _well_observed_rows():
    """Build a complete event fixture with explicit proof fields."""

    rows = [
        _event(
            0,
            "bot.start",
            {
                "uptime_ratio": 1.0,
                "recovery_seconds": 1.0,
                "equity": 1000.0,
                "net_result": 0.0,
            },
        )
    ]
    for day in range(1, 91):
        payload = {
            "uptime_ratio": 1.0,
            "equity": 1000.0 + float(day),
        }
        if day == 90:
            payload["net_result"] = 10.0
        rows.append(_event(day * DAY_MS, "account.balance", payload))
    rows.extend(
        [
            _event(1, "order.opened", {"custom_id": "a"}),
            _event(2, "order.opened", {"custom_id": "a"}),
            _event(
                3,
                "order.filled",
                {
                    "id": "fill-entry",
                    "side": "buy",
                    "price": 100.0,
                    "fee": {"amount": 0.1, "currency": "USDT", "rate": 0.0004},
                    "slippage": {"reference_price": 100.0, "executed_price": 100.0},
                },
            ),
            _event(
                4,
                "order.filled",
                {
                    "id": "fill-close",
                    "side": "sell",
                    "price": 101.0,
                    "pb_order_type": "close_grid_long",
                    "fee": {"amount": 0.1, "currency": "USDT", "rate": 0.0004},
                    "slippage": {"reference_price": 101.0, "executed_price": 101.0},
                },
            ),
            _event(
                5,
                "market.snapshot",
                {"market": {"mark_price": 100.0, "index_price": 99.99}},
            ),
            _event(
                6,
                "funding.settled",
                {
                    "funding": {
                        "amount": 0.01,
                        "rate": 0.00001,
                        "notional": 1000.0,
                        "currency": "USDT",
                    }
                },
            ),
            _event(
                7,
                "margin.snapshot",
                {
                    "margin": {
                        "equity": 1000.0,
                        "gross_exposure": 100.0,
                        "used_margin": 50.0,
                        "liquidation_distance": 0.5,
                    }
                },
            ),
            _event(
                90 * DAY_MS,
                "strategy.cycle_closed",
                {"trade_cycle_id": "trade-cycle-1"},
            ),
            _event(
                90 * DAY_MS,
                "reconciliation.check",
                {
                    "reconciliation": {
                        "mismatch_count": 0,
                        "checks": [{"name": "paper-ledger", "match": True}],
                        "source": "paper-ledger-v1",
                    }
                },
            ),
            _event(
                90 * DAY_MS,
                "costs.reconciled",
                {
                    "all_costs_present": True,
                    "costs": {
                        "all_costs_present": True,
                        "fee_total_usdt": 0.2,
                        "funding_total_usdt": 0.01,
                        "fee_reconciliation_abs_usdt": 0.0,
                        "funding_reconciliation_abs_usdt": 0.0,
                        "slippage_reconciliation_abs_bps": 0.0,
                    },
                },
            ),
        ]
    )
    return rows


def test_runtime_evidence_derives_duration_orders_fills_and_closed_cycles(tmp_path):
    events = tmp_path / "events.ndjson"
    rows = _well_observed_rows()
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    evidence = summarize_runtime_events(events)

    assert evidence["status"] == "passed"
    assert evidence["elapsed_days"] == 90.0
    assert evidence["calendar_span_days"] == 91
    assert evidence["calendar_coverage_ratio"] == 1.0
    assert evidence["heartbeat_coverage_ratio"] == 1.0
    assert evidence["max_heartbeat_gap_hours"] == 24.0
    assert evidence["missing_observation_days"] == 0
    assert evidence["missing_heartbeat_days"] == 0
    assert evidence["continuous_coverage_passed"] is True
    assert evidence["typed_evidence"]["status"] == "passed"
    assert evidence["typed_evidence"]["fills"] == 2
    assert evidence["planned_orders"] == 2
    assert evidence["unique_planned_orders"] == 1
    assert evidence["fills"] == 2
    assert evidence["unique_fills"] == 2
    assert evidence["close_fills"] == 1
    assert evidence["closed_cycles"] == 1
    assert evidence["closed_cycle_events"] == 1
    assert evidence["duplicate_orders"] == 1
    gate_metrics = runtime_gate_metrics(evidence)
    assert gate_metrics["days"] == 90.0
    assert gate_metrics["planned_orders"] == 1
    assert gate_metrics["fills"] == 2
    assert gate_metrics["closed_cycles"] == 1


def test_runtime_evidence_requires_every_utc_day_not_a_ninety_percent_sample(tmp_path):
    events = tmp_path / "missing-day.ndjson"
    rows = [
        row
        for row in _well_observed_rows()
        if not (
            row["kind"] == "account.balance"
            and row["ts"] == 45 * DAY_MS
        )
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    evidence = summarize_runtime_events(events)

    assert evidence["calendar_coverage_ratio"] == 90 / 91
    assert evidence["heartbeat_coverage_ratio"] == 90 / 91
    assert evidence["missing_observation_days"] == 1
    assert evidence["missing_heartbeat_days"] == 1
    assert evidence["continuous_coverage_passed"] is False
    assert evidence["status"] == "blocked"
    assert "calendar_coverage" in evidence["verdict"]["blockers"]
    assert "heartbeat_observations" in evidence["verdict"]["blockers"]


def test_runtime_evidence_blocks_a_heartbeat_gap_over_twenty_six_hours(tmp_path):
    events = tmp_path / "long-heartbeat-gap.ndjson"
    rows = _well_observed_rows()
    for row in rows:
        if row["kind"] == "account.balance" and row["ts"] == 45 * DAY_MS:
            row["ts"] += 3 * 60 * 60 * 1000
            break
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    evidence = summarize_runtime_events(events)

    assert evidence["missing_heartbeat_days"] == 0
    assert evidence["heartbeat_coverage_ratio"] == 1.0
    assert evidence["max_heartbeat_gap_hours"] == 27.0
    assert evidence["heartbeat_gap_limit_hours"] == 26.0
    assert evidence["heartbeat_passed"] is False
    assert evidence["continuous_coverage_passed"] is False
    assert evidence["status"] == "blocked"
    assert "heartbeat_observations" in evidence["verdict"]["blockers"]


def test_runtime_evidence_blocks_two_events_spanning_ninety_days(tmp_path):
    events = tmp_path / "sparse.ndjson"
    rows = [
        _event(0, "bot.start"),
        _event(90 * DAY_MS, "bot.stop"),
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    evidence = summarize_runtime_events(events)
    gate_metrics = runtime_gate_metrics(evidence)

    assert evidence["status"] == "blocked"
    assert evidence["calendar_span_days"] == 91
    assert evidence["calendar_coverage_ratio"] < 0.90
    assert evidence["heartbeat_coverage_ratio"] < 0.90
    assert evidence["max_heartbeat_gap_hours"] == 2160.0
    assert "calendar_coverage" in evidence["verdict"]["blockers"]
    assert "heartbeat_observations" in evidence["verdict"]["blockers"]
    assert gate_metrics["events_present"] is False
    assert gate_metrics["days"] == 0.0
    assert gate_metrics["planned_orders"] == 0
    assert gate_metrics["closed_cycles"] == 0
    assert gate_metrics["uptime_ratio"] == 0.0
    assert gate_metrics["max_restart_recovery_seconds"] == 61.0
    assert gate_metrics["net_result"] == 0.0
    assert gate_metrics["max_drawdown"] == 1.0
    assert gate_metrics["reconciliation_mismatches"] == 1
    assert gate_metrics["liquidations"] == 1


def test_runtime_evidence_requires_financial_proof_even_with_daily_heartbeats(tmp_path):
    events = tmp_path / "heartbeat-only.ndjson"
    rows = [_event(day * DAY_MS, "runtime.heartbeat") for day in range(91)]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    evidence = summarize_runtime_events(events)
    gate_metrics = runtime_gate_metrics(evidence)

    assert evidence["calendar_coverage_passed"] is True
    assert evidence["heartbeat_passed"] is True
    assert evidence["status"] == "blocked"
    assert {
        "uptime_evidence",
        "recovery_evidence",
        "pnl_evidence",
        "drawdown_evidence",
        "reconciliation_evidence",
    }.issubset(set(evidence["verdict"]["blockers"]))
    assert gate_metrics["events_present"] is False
    assert gate_metrics["uptime_ratio"] == 0.0
    assert gate_metrics["max_drawdown"] == 1.0


def test_runtime_evidence_counts_unique_closed_cycles(tmp_path):
    events = tmp_path / "duplicate-cycles.ndjson"
    rows = _well_observed_rows()
    rows.extend(
        [
            _event(
                90 * DAY_MS,
                "strategy.cycle_closed",
                {"trade_cycle_id": "trade-cycle-1"},
            ),
            _event(
                90 * DAY_MS,
                "strategy.cycle_closed",
                {"trade_cycle_id": "trade-cycle-2"},
            ),
        ]
    )
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    evidence = summarize_runtime_events(events)

    assert evidence["status"] == "passed"
    assert evidence["closed_cycle_events"] == 3
    assert evidence["closed_cycles"] == 2


def test_runtime_evidence_blocks_untyped_fill_even_with_complete_runtime(tmp_path):
    events = tmp_path / "untyped-fill.ndjson"
    rows = _well_observed_rows()
    for row in rows:
        if row["kind"] == "order.filled":
            row["payload"]["fee"] = 0.1
            break
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    evidence = summarize_runtime_events(events)
    metrics = runtime_gate_metrics(evidence)

    assert evidence["status"] == "blocked"
    assert "typed_evidence" in evidence["verdict"]["blockers"]
    assert evidence["typed_evidence"]["status"] == "blocked"
    assert metrics["events_present"] is False
    assert metrics["typed_evidence_complete"] is False


def test_runtime_evidence_blocks_inconsistent_funding_amount(tmp_path):
    events = tmp_path / "bad-funding.ndjson"
    rows = _well_observed_rows()
    for row in rows:
        if row["kind"] == "funding.settled":
            row["payload"]["funding"]["amount"] = 0.02
            break
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    evidence = summarize_runtime_events(events)

    assert evidence["status"] == "blocked"
    assert evidence["typed_evidence"]["status"] == "blocked"
    assert any("funding amount" in error for error in evidence["typed_evidence"]["errors"])


def test_runtime_evidence_blocks_malformed_or_missing_logs(tmp_path):
    missing = summarize_runtime_events(tmp_path / "missing.ndjson")
    assert missing["status"] == "blocked"
    assert runtime_gate_metrics(missing)["events_present"] is False

    missing_identity = tmp_path / "missing-identity.ndjson"
    missing_identity.write_text(
        json.dumps({"ts": 1, "kind": "bot.start", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    identity_evidence = summarize_runtime_events(missing_identity)
    assert identity_evidence["status"] == "blocked"
    assert identity_evidence["identity_missing_events"] == 1

    malformed = tmp_path / "malformed.ndjson.gz"
    with gzip.open(malformed, "wt", encoding="utf-8") as handle:
        handle.write(
            '{"ts": 1, "kind": "bot.start", "payload": '
            '{"strategy_id": "test:strategy", "cycle_id": 0, '
            '"config_hash": "test-config-hash"}}\nnot-json\n'
        )
    evidence = summarize_runtime_events(malformed)
    assert evidence["status"] == "blocked"
    assert runtime_gate_metrics(evidence)["planned_orders"] == 0


def test_runtime_gate_metrics_overwrites_unproven_metrics_json_claims(tmp_path):
    events = tmp_path / "runtime.ndjson"
    events.write_text(
        "\n".join(json.dumps(row) for row in _well_observed_rows()) + "\n",
        encoding="utf-8",
    )
    evidence = summarize_runtime_events(events)

    claimed_metrics = {
        "days": 1,
        "planned_orders": 1,
        "closed_cycles": 1,
        "all_costs_present": True,
        "liquidations": 999,
        "max_recovery_days": 1,
        "gross_exposure_ratio": 0.1,
        "used_margin_ratio": 0.1,
        "min_liquidation_distance": 0.9,
        "execution_slippage_median_bps": 0.1,
        "execution_slippage_p95_bps": 0.1,
        "execution_slippage_max_bps": 0.1,
        "max_daily_loss": 0.001,
        "fee_reconciliation_abs_usdt": 0.0,
        "funding_reconciliation_abs_usdt": 0.0,
        "execution_p95_bps": 0.1,
        "order_confirmation_p95_seconds": 0.1,
        "hsl_emergency_triggers": 0,
        "terminal_stop_reached": False,
    }
    claimed_metrics.update(runtime_gate_metrics(evidence))

    assert claimed_metrics["days"] == 90.0
    assert claimed_metrics["planned_orders"] == 1
    assert claimed_metrics["all_costs_present"] is True
    assert claimed_metrics["liquidations"] == 0
    assert claimed_metrics["max_recovery_days"] == 91.0
    assert claimed_metrics["gross_exposure_ratio"] == 0.1
    assert claimed_metrics["used_margin_ratio"] == 0.05
    assert claimed_metrics["min_liquidation_distance"] == 0.5
    assert claimed_metrics["execution_slippage_p95_bps"] == 0.0
    assert claimed_metrics["max_daily_loss"] > 0.02
    assert claimed_metrics["fee_reconciliation_abs_usdt"] == 0.0
    assert claimed_metrics["funding_reconciliation_abs_usdt"] == 0.0
    assert claimed_metrics["execution_p95_bps"] == 0.0
    assert claimed_metrics["order_confirmation_p95_seconds"] > 5.0
    assert claimed_metrics["hsl_emergency_triggers"] == 1
    assert claimed_metrics["terminal_stop_reached"] is False


def test_runtime_gate_metrics_rejects_old_evidence_schema(tmp_path):
    events = tmp_path / "old-schema.ndjson"
    events.write_text(
        "\n".join(json.dumps(row) for row in _well_observed_rows()) + "\n",
        encoding="utf-8",
    )
    evidence = summarize_runtime_events(events)
    evidence["schema_version"] = "crypto-runtime-evidence-v1"

    metrics = runtime_gate_metrics(evidence)

    assert metrics["events_present"] is False
    assert metrics["runtime_evidence_complete"] is False
    assert metrics["days"] == 0.0
    assert metrics["planned_orders"] == 0
    assert metrics["liquidations"] == 1
    assert metrics["all_costs_present"] is False
