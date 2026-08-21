from __future__ import annotations

import asyncio
import json

import pytest

from research.experiment_manifest import sha256_path
from research.strategy_shadow_stream import run_strategy_shadow_stream


def _request_factory(
    prices: list[float],
    *,
    source_start: int = 1_700_000_000_000,
    source_step: int = 60_000,
):
    state = {"index": -1, "price": prices[0]}

    def request(path, params):
        if path.endswith("/tickers"):
            state["index"] += 1
            state["price"] = prices[min(state["index"], len(prices) - 1)]
            price = state["price"]
            return {
                "code": "00000",
                "data": [
                    {
                        "symbol": params["symbol"],
                        "lastPrice": str(price),
                        "bid1Price": str(price),
                        "ask1Price": str(price + 0.01),
                        "bid1Size": "2",
                        "ask1Size": "2",
                        "markPrice": str(price),
                        "indexPrice": str(price),
                        "fundingRate": "0.0001",
                        "ts": str(source_start + state["index"] * source_step),
                    }
                ],
            }
        price = state["price"]
        if path.endswith("/orderbook"):
            return {
                "code": "00000",
                "data": {
                    "ts": str(source_start + max(state["index"], 0) * source_step),
                    "b": [[str(price), "2"]],
                    "a": [[str(price + 0.01), "2"]],
                },
            }
        if path.endswith("/current-fund-rate"):
            return {
                "code": "00000",
                "data": [
                    {
                        "symbol": params["symbol"],
                        "fundingRate": "0.0001",
                        "fundingRateInterval": "8",
                        "nextUpdate": "1700000000000",
                        "minFundingRate": "-0.003",
                        "maxFundingRate": "0.003",
                    }
                ],
            }
        if path.endswith("/history-fund-rate"):
            return {
                "code": "00000",
                "data": {
                    "resultList": [
                        {
                            "symbol": params["symbol"],
                            "fundingRate": "0.00009",
                            "fundingRateTimestamp": "1699999000000",
                        }
                    ]
                },
            }
        raise AssertionError(path)

    return request


async def _no_sleep(_seconds: float) -> None:
    return None


def _clock(start: int = 1_700_000_000_000):
    state = {"timestamp": start}

    def now():
        value = state["timestamp"]
        state["timestamp"] += 60_000
        return value

    return now


def test_stream_adds_deterministic_signals_and_local_fills(tmp_path):
    result = asyncio.run(
        run_strategy_shadow_stream(
            output_dir=tmp_path / "stream",
            config={"strategy_id": "stream-test", "max_source_gap_seconds": 300.0},
            side="long",
            entry_bps=5.0,
            take_profit_bps=5.0,
            interval_seconds=0.001,
            max_samples=6,
            request=_request_factory([100.0, 99.8, 100.0, 99.8, 100.0, 99.8]),
            async_sleep=_no_sleep,
            capture_clock_ms=_clock(),
        )
    )

    assert result["status"] == "passed"
    assert result["stream"]["samples"] == 6
    assert result["stream"]["errors"] == []
    assert result["readiness_metrics"]["shadow_planned_orders"] >= 4
    assert result["readiness_metrics"]["shadow_external_network_calls"] == 0
    assert result["readiness_metrics"]["shadow_private_calls"] == 0
    assert result["readiness_metrics"]["shadow_order_creates"] == 0
    assert result["readiness_metrics"]["shadow_stream_public_only"] is True
    for details in result["provenance"]["artifacts"].values():
        assert details["sha256"] == sha256_path(details["path"])

    snapshots = [json.loads(line) for line in (tmp_path / "stream" / "market_snapshots.jsonl").read_text().splitlines()]
    assert snapshots[1]["stream_event_type"] == "market"
    strategy_inputs = [json.loads(line) for line in (tmp_path / "stream" / "strategy_inputs.ndjson").read_text().splitlines()]
    assert strategy_inputs[1]["signals"]["long"] == "enter"
    assert strategy_inputs[2]["signals"]["long"] == "exit"
    assert all("source_responses" in row for row in snapshots)
    assert all("signals" not in row and "execution" not in row for row in snapshots)
    assert strategy_inputs[1]["timestamp"] == 1_700_000_060_000
    assert strategy_inputs[1]["observed_at_ms"] == 1_700_000_060_000


def test_stream_recovers_existing_driver_and_policy_state(tmp_path):
    output = tmp_path / "stream"
    first = asyncio.run(
        run_strategy_shadow_stream(
            output_dir=output,
            config={"strategy_id": "stream-recovery", "max_source_gap_seconds": 300.0},
            side="long",
            entry_bps=5.0,
            take_profit_bps=5.0,
            interval_seconds=0.001,
            max_samples=2,
            request=_request_factory([100.0, 99.8]),
            async_sleep=_no_sleep,
            capture_clock_ms=_clock(),
        )
    )
    second = asyncio.run(
        run_strategy_shadow_stream(
            output_dir=output,
            config={"strategy_id": "stream-recovery", "max_source_gap_seconds": 300.0},
            side="long",
            entry_bps=5.0,
            take_profit_bps=5.0,
            interval_seconds=0.001,
            max_samples=2,
            request=_request_factory([100.0, 99.8], source_start=1_700_000_120_000),
            async_sleep=_no_sleep,
            capture_clock_ms=_clock(start=1_700_000_120_000),
        )
    )

    assert first["status"] == "passed"
    assert second["status"] == "passed"
    assert second["restart_count"] >= 1
    assert second["max_restart_recovery_seconds"] is not None
    assert second["max_restart_recovery_seconds"] <= 60.0
    assert second["readiness_metrics"]["shadow_stream_snapshot_log_status"] == "passed"


def test_stream_requires_bounded_session_and_rejects_credentials(tmp_path):
    with pytest.raises(ValueError, match="duration_seconds or max_samples"):
        asyncio.run(
            run_strategy_shadow_stream(
                output_dir=tmp_path / "missing-bound",
                config={"strategy_id": "stream-test", "max_source_gap_seconds": 300.0},
                request=_request_factory([100.0]),
            )
        )

    with pytest.raises(ValueError, match="credential-shaped"):
        asyncio.run(
            run_strategy_shadow_stream(
                output_dir=tmp_path / "bad-config",
                config={
                    "strategy_id": "stream-test",
                    "max_source_gap_seconds": 300.0,
                    "api_key": "must-not-be-read",
                },
                max_samples=1,
                request=_request_factory([100.0]),
            )
        )


def test_stream_rejects_policy_override_not_bound_to_config(tmp_path):
    config = {
        "strategy_id": "stream-bound-policy",
        "max_source_gap_seconds": 300.0,
        "shadow_policy": {
            "entry_bps": 5.0,
            "take_profit_bps": 5.0,
            "order_amount": 0.001,
        },
    }
    with pytest.raises(ValueError, match="does not match config shadow_policy"):
        asyncio.run(
            run_strategy_shadow_stream(
                output_dir=tmp_path / "mismatch",
                config=config,
                entry_bps=6.0,
                max_samples=1,
                request=_request_factory([100.0]),
            )
        )


def test_stream_rejects_source_gap_override_not_bound_to_config(tmp_path):
    config = {
        "strategy_id": "stream-bound-gap",
        "max_source_gap_seconds": 300.0,
        "shadow_policy": {
            "entry_bps": 5.0,
            "take_profit_bps": 5.0,
            "order_amount": 0.001,
        },
    }
    with pytest.raises(ValueError, match="max_source_gap_seconds override does not match config"):
        asyncio.run(
            run_strategy_shadow_stream(
                output_dir=tmp_path / "mismatch-gap",
                config=config,
                max_source_gap_seconds=600.0,
                max_samples=1,
                request=_request_factory([100.0]),
            )
        )


def test_stream_requires_source_gap_limit_in_config(tmp_path):
    with pytest.raises(ValueError, match="config.max_source_gap_seconds is required"):
        asyncio.run(
            run_strategy_shadow_stream(
                output_dir=tmp_path / "missing-gap-config",
                config={"strategy_id": "stream-missing-gap"},
                max_samples=1,
                request=_request_factory([100.0]),
            )
        )


def test_stream_records_effective_policy_in_driver_config_hash(tmp_path):
    config = {
        "strategy_id": "stream-bound-policy-hash",
        "max_source_gap_seconds": 300.0,
        "shadow_policy": {
            "entry_bps": 4.0,
            "take_profit_bps": 6.0,
            "order_amount": 0.002,
        },
    }
    result = asyncio.run(
        run_strategy_shadow_stream(
            output_dir=tmp_path / "bound",
            config=config,
            max_samples=1,
            request=_request_factory([100.0]),
        )
    )

    assert result["status"] == "passed"
    assert result["stream"]["policy_config"] == {
        "policy_name": "deterministic_grid_probe",
        "side": "long",
        **config["shadow_policy"],
    }
    policy_state = json.loads((tmp_path / "bound" / "policy_state.json").read_text())
    assert policy_state["policy"] == {
        "policy_name": "deterministic_grid_probe",
        "side": "long",
        **config["shadow_policy"],
    }
    assert result["readiness_metrics"]["shadow_stream_policy_config_hash"]


def test_stream_fails_when_exchange_source_gap_exceeds_limit(tmp_path):
    result = asyncio.run(
        run_strategy_shadow_stream(
            output_dir=tmp_path / "source-gap",
            config={"strategy_id": "stream-source-gap", "max_source_gap_seconds": 300.0},
            side="long",
            interval_seconds=0.001,
            max_source_gap_seconds=300.0,
            max_samples=2,
            request=_request_factory([100.0, 99.8], source_step=10 * 60 * 1000),
            async_sleep=_no_sleep,
            capture_clock_ms=_clock(),
        )
    )

    assert result["status"] == "failed"
    assert result["readiness_metrics"]["shadow_stream_gap_check_status"] == "failed"
    assert result["readiness_metrics"]["shadow_stream_max_source_gap_seconds"] == 600.0
    assert any(
        error["reason"] == "source_gap_exceeds_limit"
        for error in result["stream"]["market_log_audit"]["errors"]
    )

def test_stream_turns_identical_public_events_into_heartbeats(tmp_path):
    result = asyncio.run(
        run_strategy_shadow_stream(
            output_dir=tmp_path / "heartbeat",
            config={"strategy_id": "stream-heartbeat", "max_source_gap_seconds": 300.0},
            side="long",
            interval_seconds=0.001,
            max_samples=3,
            request=_request_factory([100.0], source_step=0),
            async_sleep=_no_sleep,
            capture_clock_ms=_clock(start=1_800_000_000_000),
        )
    )

    assert result["status"] == "passed"
    assert result["stream"]["market_events"] == 1
    assert result["stream"]["heartbeats"] == 2
    assert result["readiness_metrics"]["shadow_stream_heartbeats"] == 2
    strategy_inputs = [
        json.loads(line)
        for line in (tmp_path / "heartbeat" / "strategy_inputs.ndjson").read_text().splitlines()
    ]
    assert len(strategy_inputs) == 1
    assert strategy_inputs[0]["timestamp"] == 1_700_000_000_000
    assert strategy_inputs[0]["observed_at_ms"] == 1_800_000_000_000
    market_rows = [
        json.loads(line)
        for line in (tmp_path / "heartbeat" / "market_snapshots.jsonl").read_text().splitlines()
    ]
    assert [row["stream_event_type"] for row in market_rows] == ["market", "heartbeat", "heartbeat"]
    assert all(row["observed_at_ms"] >= 1_800_000_000_000 for row in market_rows)


def test_stream_records_collection_error_without_unbounded_retry(tmp_path):
    delegate = _request_factory([100.0, 99.8])
    state = {"failed": False}

    def flaky_request(path, params):
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("temporary public timeout")
        return delegate(path, params)

    result = asyncio.run(
        run_strategy_shadow_stream(
            output_dir=tmp_path / "flaky",
            config={"strategy_id": "stream-flaky", "max_source_gap_seconds": 300.0},
            side="long",
            interval_seconds=0.001,
            max_samples=2,
            request=flaky_request,
            async_sleep=_no_sleep,
            capture_clock_ms=_clock(),
        )
    )

    assert result["status"] == "failed"
    assert result["stream"]["attempts"] == 2
    assert result["stream"]["samples"] == 1
    assert result["stream"]["errors"][0]["phase"] == "market_collection"
