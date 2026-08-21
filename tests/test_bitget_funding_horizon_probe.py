from __future__ import annotations

from tools.bitget_funding_horizon_probe import build_probe


def test_horizon_probe_compares_v3_and_v2_without_claiming_completeness():
    def request(path, params):
        page = int(params.get("cursor", params.get("pageNo", "1")))
        if path.endswith("history-fund-rate"):
            if page == 1:
                if path.startswith("/api/v3"):
                    return {
                        "data": {
                            "resultList": [
                                {
                                    "symbol": "BTCUSDT",
                                    "fundingRate": "0.001",
                                    "fundingRateTimestamp": "0",
                                }
                            ]
                        }
                    }
                return {
                    "data": [
                        {
                            "symbol": "BTCUSDT",
                            "fundingRate": "0.001",
                            "fundingTime": "0",
                        }
                    ]
                }
            return {"data": {"resultList": []}} if path.startswith("/api/v3") else {"data": []}
        raise AssertionError(path)

    report = build_probe(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        start="0",
        end=str(8 * 60 * 60 * 1000),
        request=request,
        max_pages=3,
    )

    assert report["status"] == "blocked"
    assert report["complete_requested_window_available"] is False
    assert report["trading_operations_count"] == 0
    assert report["endpoints"]["uta_v3"]["page_row_counts"] == [1, 0]
    assert report["endpoints"]["classic_v2"]["page_row_counts"] == [1, 0]
    assert report["endpoints"]["uta_v3"]["quality_for_requested_window"]["missing_rows"] == 1


def test_horizon_probe_accepts_a_complete_requested_window_from_a_public_endpoint():
    def request(path, params):
        page = int(params.get("cursor", params.get("pageNo", "1")))
        rows = [
                {
                    "symbol": "ETHUSDT",
                    "fundingRate": "0",
                    "fundingRateTimestamp": str(
                        (page - 1) * 60 * 60 * 1000
                    ),
            }
        ] if path.startswith("/api/v3") and page <= 3 else []
        if path.startswith("/api/v3"):
            return {"data": {"resultList": rows}}
        return {"data": []}

    report = build_probe(
        symbol="ETHUSDT",
        category="USDT-FUTURES",
        start="0",
        end=str(2 * 60 * 60 * 1000),
        request=request,
        max_pages=4,
        interval_hours=1,
    )

    assert report["status"] == "passed"
    assert report["complete_requested_window_available"] is True
    assert report["endpoints"]["uta_v3"]["observed_rows"] == 3


def test_horizon_probe_validates_malformed_and_duplicate_rows_before_reporting_horizon():
    def request(path, params):
        page = int(params.get("cursor", params.get("pageNo", "1")))
        if page > 1:
            return {"data": {"resultList": []}} if path.startswith("/api/v3") else {"data": []}
        rows = [
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.001",
                "fundingRateTimestamp": "0",
            },
            {
                "symbol": "BTCUSDT",
                "fundingRate": "not-a-rate",
                "fundingRateTimestamp": "3600000",
            },
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.001",
                "fundingRateTimestamp": "0",
            },
        ]
        if path.startswith("/api/v3"):
            return {"data": {"resultList": rows}}
        return {
            "data": [
                {
                    "symbol": row["symbol"],
                    "fundingRate": row["fundingRate"],
                    "fundingTime": row["fundingRateTimestamp"],
                }
                for row in rows
            ]
        }

    report = build_probe(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        start="0",
        end=str(60 * 60 * 1000),
        request=request,
        max_pages=3,
        interval_hours=1,
    )

    for endpoint in report["endpoints"].values():
        assert endpoint["observed_rows"] == 3
        assert endpoint["valid_rows"] == 2
        assert endpoint["status"] == "blocked"
        assert endpoint["quality_for_requested_window"]["invalid_rows"] == 1
        assert endpoint["quality_for_requested_window"]["duplicate_rows"] == 1
        assert endpoint["quality_for_requested_window"]["complete_requested_window"] is False


def test_horizon_probe_blocks_foreign_symbols_and_page_errors_even_if_rows_look_complete():
    def request(path, params):
        page = int(params.get("cursor", params.get("pageNo", "1")))
        if page == 1:
            if path.startswith("/api/v3"):
                return {
                    "data": {
                        "resultList": [
                            {
                                "symbol": "ETHUSDT",
                                "fundingRate": "0",
                                "fundingRateTimestamp": "0",
                            }
                        ]
                    }
                }
            return {
                "data": [
                    {
                        "symbol": "BTCUSDT",
                        "fundingRate": "0",
                        "fundingTime": "0",
                    }
                ]
            }
        raise RuntimeError("page fetch failed")

    report = build_probe(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        start="0",
        end="0",
        request=request,
        max_pages=2,
    )

    assert report["status"] == "blocked"
    assert report["complete_requested_window_available"] is False
    assert report["endpoints"]["uta_v3"]["status"] == "blocked"
    assert report["endpoints"]["uta_v3"]["foreign_symbol_rows"] == 1
    assert report["endpoints"]["classic_v2"]["status"] == "blocked"
    assert "page fetch failed" in report["endpoints"]["classic_v2"]["error"]


def test_horizon_probe_keeps_malformed_page_members_as_invalid_evidence():
    def request(path, params):
        page = int(params.get("cursor", params.get("pageNo", "1")))
        if page > 1:
            return {"data": {"resultList": []}} if path.startswith("/api/v3") else {"data": []}
        if path.startswith("/api/v3"):
            return {"data": {"resultList": [{"symbol": "BTCUSDT", "fundingRate": "0", "fundingRateTimestamp": "0"}, "bad"]}}
        return {"data": [{"symbol": "BTCUSDT", "fundingRate": "0", "fundingTime": "0"}, "bad"]}

    report = build_probe(
        symbol="BTCUSDT",
        category="USDT-FUTURES",
        start="0",
        end="0",
        request=request,
        max_pages=2,
    )

    for endpoint in report["endpoints"].values():
        assert endpoint["malformed_rows"] == 1
        assert endpoint["status"] == "blocked"
        assert endpoint["quality_for_requested_window"]["invalid_rows"] == 1
