from __future__ import annotations

from tools.bitget_public_execution_inputs import (
    _fetch_candle_history,
    _fetch_candle_history_parallel,
    _coverage,
    _funding_quality,
    _pagination_quality,
    _parse_interval_ms,
)


def test_interval_parser_uses_explicit_milliseconds():
    assert _parse_interval_ms("1m") == 60_000
    assert _parse_interval_ms("1H") == 3_600_000


def test_market_quality_reports_missing_and_future_rows():
    quality = _coverage(
        [
            [0, "1", "1", "1", "1"],
            [120_000, "1", "1", "1", "1"],
            [180_000, "1", "1", "1", "1"],
        ],
        start_ms=0,
        end_ms=120_000,
        interval_ms=60_000,
    )
    assert quality["expected_rows"] == 3
    assert quality["actual_rows"] == 2
    assert quality["missing_rows"] == 1
    assert quality["future_rows"] == 1


def test_funding_quality_detects_an_interval_gap():
    quality = _funding_quality(
        [
            {"timestamp_ms": 0, "fundingRate": "0"},
            {"timestamp_ms": 8 * 60 * 60 * 1000, "fundingRate": "0"},
            {"timestamp_ms": 24 * 60 * 60 * 1000, "fundingRate": "0"},
        ],
        start_ms=0,
        end_ms=24 * 60 * 60 * 1000,
        interval_hours=8,
    )
    assert quality["interval_gaps"] == 1
    assert quality["complete_between_observed_bounds"] is False


def test_funding_quality_does_not_treat_observed_subrange_as_requested_archive():
    quality = _funding_quality(
        [
            {"timestamp_ms": 0, "fundingRate": "0"},
            {"timestamp_ms": 8 * 60 * 60 * 1000, "fundingRate": "0"},
        ],
        start_ms=0,
        end_ms=16 * 60 * 60 * 1000,
        interval_hours=8,
    )

    assert quality["complete_between_observed_bounds"] is True
    assert quality["complete_requested_window"] is False
    assert quality["missing_rows"] == 1


def test_pagination_quality_keeps_empty_horizon_page_visible():
    quality = _pagination_quality(
        [
            {"data": {"resultList": [{"fundingRateTimestamp": "1"}] * 100}},
            {"data": {"resultList": [{"fundingRateTimestamp": "2"}] * 70}},
            {"data": {"resultList": []}},
        ],
        "resultList",
    )

    assert quality == {
        "page_count": 3,
        "page_row_counts": [100, 70, 0],
        "last_page_empty": True,
        "max_page_size": 100,
    }


def test_candle_pagination_keeps_inclusive_page_boundary():
    def request(path, params):
        assert path == "/api/v3/market/history-candles"
        end = int(params["endTime"])
        effective_end = (end // 60) * 60
        rows = [
            [timestamp, "1", "1", "1", "1"]
            for timestamp in (240, 180, 120, 60, 0)
            if timestamp < effective_end
        ]
        return {"data": rows[:3]}

    rows, _pages = _fetch_candle_history(
        request,
        category="USDT-FUTURES",
        symbol="BTCUSDT",
        candle_type="mark",
        interval="1m",
        start_ms=0,
        end_ms=240,
        max_pages=3,
        delay_seconds=0.0,
    )

    assert [row[0] for row in rows] == [0, 60, 120, 180, 240]


def test_parallel_candle_pagination_is_sorted_and_complete():
    interval_ms = 60_000

    def request_factory():
        def request(path, params):
            assert path == "/api/v3/market/history-candles"
            page_end = int(params["endTime"])
            rows = [
                [timestamp, "1", "1", "1", "1"]
                for timestamp in range(
                    max(0, page_end - 100 * interval_ms),
                    page_end,
                    interval_ms,
                )
            ]
            return {"data": list(reversed(rows))}

        return request

    rows, pages = _fetch_candle_history_parallel(
        request_factory=request_factory,
        category="USDT-FUTURES",
        symbol="ETHUSDT",
        candle_type="market",
        interval="1m",
        start_ms=0,
        end_ms=204 * interval_ms,
        max_pages=3,
        max_workers=2,
    )

    assert len(pages) == 3
    assert [row[0] for row in rows] == [index * interval_ms for index in range(205)]
