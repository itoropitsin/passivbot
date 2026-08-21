from __future__ import annotations

from tools.bitget_public_data_metrics import build_metrics


def _execution(*, funding_rows: int = 3):
    start_ms = 1767225600000
    return {
        "exchange": "bitget",
        "symbol": "ETHUSDT",
        "schema_version": "execution-v1",
        "trading_operations_count": 0,
        "quality": {
            "requested_start_utc": "2026-01-01T00:00:00+00:00",
            "requested_end_utc": "2026-01-02T00:00:00+00:00",
            "funding": {
                "expected_interval_hours": 8,
                "rows": funding_rows,
                "future_rows": 0,
                "complete_between_observed_bounds": funding_rows == 4,
            },
            "mark": {
                "coverage_ratio": 1.0,
                "future_rows": 0,
                "invalid_rows": 0,
                "nan_values": 0,
            },
            "index": {
                "coverage_ratio": 1.0,
                "future_rows": 0,
                "invalid_rows": 0,
                "nan_values": 0,
            },
        },
        "funding_history": [
            {
                "timestamp_ms": start_ms + index * 8 * 60 * 60 * 1000,
                "funding_rate": 0.0,
                "symbol": "ETHUSDT",
            }
            for index in range(funding_rows)
        ],
        "snapshots": {"position_tiers": {"data": [{"tier": "1"}]}},
    }


def test_metrics_keep_incomplete_funding_and_ohlcv_visible():
    result = build_metrics(
        asset="ETH",
        execution_inputs=_execution(funding_rows=3),
        ohlcv_repair={
            "status": "blocked",
            "before_missing_rows": 10,
            "repaired_rows": 2,
            "after_missing_rows": 8,
            "trading_operations_count": 0,
        },
        execution_inputs_path="execution.json",
        ohlcv_repair_path="repair.json",
    )

    assert result["funding_expected_rows"] == 4
    assert result["funding_actual_rows"] == 3
    assert result["funding_history_complete"] is False
    assert result["history_expected_rows"] == 12
    assert result["history_actual_rows"] == 4
    assert result["history_missing_rows"] == 8
    assert result["read_only_external_operations"] is True


def test_metrics_require_exact_plan_boundary_for_history_through_date():
    result = build_metrics(
        asset="BTC",
        execution_inputs={
            **_execution(funding_rows=4),
            "quality": {
                **_execution(funding_rows=4)["quality"],
                "requested_end_utc": "2026-08-17T00:00:00+00:00",
            },
        },
        ohlcv_repair={
            "status": "repaired",
            "before_missing_rows": 0,
            "repaired_rows": 0,
            "after_missing_rows": 0,
            "trading_operations_count": 0,
        },
        execution_inputs_path="execution.json",
        ohlcv_repair_path="repair.json",
        execution_model_test_count=3,
    )

    assert result["history_through_plan_date"] is True
    assert result["history_coverage_ratio"] == 1.0
    assert result["execution_model_implemented"] is True


def test_metrics_derive_funding_coverage_from_rows_not_claimed_count():
    execution = _execution(funding_rows=4)
    start_ms = 1767225600000
    execution["funding_history"] = [
        {
            "timestamp_ms": start_ms + index * 8 * 60 * 60 * 1000,
            "funding_rate": 0.0,
        }
        for index in range(3)
    ]

    result = build_metrics(
        asset="ETH",
        execution_inputs=execution,
        ohlcv_repair={
            "status": "repaired",
            "before_missing_rows": 0,
            "repaired_rows": 0,
            "after_missing_rows": 0,
            "trading_operations_count": 0,
        },
        execution_inputs_path="execution.json",
        ohlcv_repair_path="repair.json",
    )

    assert result["funding_expected_rows"] == 4
    assert result["funding_actual_rows"] == 3
    assert result["funding_known_coverage_ratio"] == 0.75
    assert result["funding_missing_rows"] == 1
    assert result["funding_history_complete"] is False


def test_metrics_never_use_declared_funding_quality_without_actual_rows():
    execution = _execution(funding_rows=4)
    execution.pop("funding_history")

    result = build_metrics(
        asset="ETH",
        execution_inputs=execution,
        ohlcv_repair={
            "status": "repaired",
            "before_missing_rows": 0,
            "repaired_rows": 0,
            "after_missing_rows": 0,
            "trading_operations_count": 0,
        },
        execution_inputs_path="execution.json",
        ohlcv_repair_path="repair.json",
    )

    assert result["funding_history_complete"] is False
    assert result["funding_actual_rows"] == 0
    assert result["funding_known_coverage_ratio"] == 0.0
