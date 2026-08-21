from __future__ import annotations

import json

from tools.bitget_market_recorder import (
    collect_market_snapshot,
    record_market_data,
    verify_market_log,
)


def _request(path, params):
    if path.endswith("/tickers"):
        return {
            "code": "00000",
            "data": [
                {
                    "symbol": params["symbol"],
                    "lastPrice": "100.5",
                    "bid1Price": "100",
                    "ask1Price": "101",
                    "bid1Size": "2",
                    "ask1Size": "3",
                    "markPrice": "100.4",
                    "indexPrice": "100.3",
                    "fundingRate": "0.0001",
                    "ts": "1700000001234",
                }
            ],
        }
    if path.endswith("/current-fund-rate"):
        return {
            "code": "00000",
            "data": [
                {
                    "symbol": params["symbol"],
                    "fundingRate": "0.0001",
                    "fundingRateInterval": "8",
                    "nextUpdate": "2000000",
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
                        "fundingRateTimestamp": "1999000",
                    }
                ]
            },
        }
    assert path.endswith("/orderbook")
    assert params["limit"] == "2"
    return {
        "code": "00000",
        "data": {
            "ts": "1700000001235",
            "b": [["100", "2"], ["99", "1"]],
            "a": [["101", "3"], ["102", "1"]],
        },
    }


def test_collect_market_snapshot_keeps_prices_and_hashes():
    snapshot = collect_market_snapshot(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=2,
        request=_request,
        captured_at_ms=2000,
    )

    assert snapshot["trading_operations_count"] == 0
    assert snapshot["ticker"]["bid"] == 100.0
    assert snapshot["ticker"]["mark"] == 100.4
    assert snapshot["funding"]["rate"] == 0.0001
    assert snapshot["funding"]["interval_hours"] == 8.0
    assert snapshot["funding"]["next_funding_time_ms"] == 2000000
    assert snapshot["orderbook"]["asks"][0] == [101.0, 3.0]
    assert snapshot["funding"]["latest_settlement_rate"] == 0.00009
    assert snapshot["funding"]["latest_settlement_time_ms"] == 1999000
    assert set(snapshot["response_sha256"]) == {"ticker", "orderbook", "current_funding", "funding_history"}
    assert snapshot["source_responses"]["ticker"]["data"][0]["lastPrice"] == "100.5"


def test_recorder_appends_one_public_snapshot(tmp_path):
    output = tmp_path / "snapshots.jsonl"
    result = record_market_data(
        output=output,
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=2,
        interval_seconds=1.0,
        duration_seconds=0.0,
        max_samples=1,
        request=_request,
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
    )

    assert result["samples"] == 1
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["read_only_only"] is True

    audit = verify_market_log(output)
    assert audit["status"] == "passed"
    assert audit["row_count"] == 1

    output.write_text(output.read_text() + '{"read_only_only": true,\n', encoding="utf-8")
    tampered = verify_market_log(output)
    assert tampered["status"] == "failed"
    assert any(error["reason"] == "invalid_json: Expecting property name enclosed in double quotes" for error in tampered["errors"])


def test_recorder_rejects_incomplete_current_funding():
    def incomplete_request(path, params):
        payload = _request(path, params)
        if path.endswith("/current-fund-rate"):
            payload["data"][0].pop("nextUpdate")
        return payload

    try:
        collect_market_snapshot(request=incomplete_request, depth=2)
    except ValueError as exc:
        assert "current funding" in str(exc)
    else:
        raise AssertionError("incomplete funding must block a snapshot")


def test_verify_rejects_tampered_v2_funding_snapshot(tmp_path):
    output = tmp_path / "snapshots.jsonl"
    snapshot = collect_market_snapshot(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=2,
        request=_request,
        captured_at_ms=2000,
    )
    snapshot["funding"].pop("next_funding_time_ms")
    output.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")

    audit = verify_market_log(output)

    assert audit["status"] == "failed"
    assert any(error["reason"] == "funding_next_funding_time_ms_missing" for error in audit["errors"])


def test_verify_reports_capture_gap_and_funding_continuity(tmp_path):
    output = tmp_path / "snapshots.jsonl"
    first = collect_market_snapshot(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=2,
        request=_request,
        captured_at_ms=2_000,
    )
    second = collect_market_snapshot(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=2,
        request=_request,
        captured_at_ms=2_000 + 10 * 60 * 1000,
    )
    # The fixture's settlement timestamp is the same in both rows, so the
    # funding series is continuous over its observed one-settlement window.
    output.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )

    audit = verify_market_log(
        output,
        max_gap_seconds=60,
        require_funding_continuity=True,
    )

    assert audit["status"] == "failed"
    assert audit["max_capture_gap_seconds"] == 600.0
    assert audit["funding_continuity_status"] == "passed"
    assert any(error["reason"] == "capture_gap_exceeds_limit" for error in audit["errors"])


def test_verify_can_bound_exchange_source_gap_separately_from_capture_gap(tmp_path):
    output = tmp_path / "snapshots.jsonl"
    first = collect_market_snapshot(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=2,
        request=_request,
        captured_at_ms=2_000,
    )
    second = json.loads(json.dumps(first))
    second["captured_at_ms"] = 2_100
    second["source_timestamp_ms"] = first["source_timestamp_ms"] + 10 * 60 * 1000
    output.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    audit = verify_market_log(
        output,
        max_gap_seconds=300,
        use_source_timestamp=True,
    )

    assert audit["status"] == "failed"
    assert audit["gap_timestamp_kind"] == "source"
    assert audit["max_capture_gap_seconds"] == 0.1
    assert audit["max_source_gap_seconds"] == 600.0
    assert any(error["reason"] == "source_gap_exceeds_limit" for error in audit["errors"])


def test_verify_source_gap_requires_top_level_millisecond_timestamp(tmp_path):
    output = tmp_path / "snapshots.jsonl"
    snapshot = collect_market_snapshot(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=2,
        request=_request,
        captured_at_ms=2_000,
    )
    snapshot.pop("source_timestamp_ms")
    output.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")

    audit = verify_market_log(
        output,
        max_gap_seconds=300,
        use_source_timestamp=True,
    )

    assert audit["status"] == "failed"
    assert any(error["reason"] == "source_timestamp_missing" for error in audit["errors"])

    snapshot["source_timestamp_ms"] = 1_700_000_001
    output.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
    seconds_audit = verify_market_log(
        output,
        max_gap_seconds=300,
        use_source_timestamp=True,
    )
    assert seconds_audit["status"] == "failed"
    assert any(
        error["reason"] == "source_timestamp_not_milliseconds"
        for error in seconds_audit["errors"]
    )


def test_verify_source_timestamp_order_cannot_run_backwards(tmp_path):
    output = tmp_path / "snapshots.jsonl"
    first = collect_market_snapshot(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=2,
        request=_request,
        captured_at_ms=2_000,
    )
    second = json.loads(json.dumps(first))
    second["captured_at_ms"] = 2_100
    second["source_timestamp_ms"] = first["source_timestamp_ms"] - 1_000
    output.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    audit = verify_market_log(
        output,
        max_gap_seconds=300,
        use_source_timestamp=True,
    )

    assert audit["status"] == "failed"
    assert any(error["reason"] == "source_timestamps_decrease" for error in audit["errors"])


def test_verify_rejects_missing_funding_settlement(tmp_path):
    output = tmp_path / "snapshots.jsonl"
    first = collect_market_snapshot(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        depth=2,
        request=_request,
        captured_at_ms=2_000,
    )
    second = json.loads(json.dumps(first))
    second["captured_at_ms"] = 2_000 + 8 * 60 * 60 * 1000
    second["funding"]["latest_settlement_time_ms"] = 1999000 + 16 * 60 * 60 * 1000
    output.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )

    audit = verify_market_log(output, require_funding_continuity=True)

    assert audit["status"] == "failed"
    assert audit["funding_missing_rows_between_observed_bounds"] == 1
    assert any(error["reason"] == "funding_settlement_gap" for error in audit["errors"])
