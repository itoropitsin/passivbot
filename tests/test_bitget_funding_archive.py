from __future__ import annotations

from datetime import datetime, timezone
import json

from tools.bitget_funding_archive import build_validation


def _metadata(*, symbol="BTCUSDT", start_ms=0, end_ms=16 * 60 * 60 * 1000):
    return {
        "rate_semantics": "applied_settlement",
        "source_provider": "test-provider",
        "source_reference": "https://example.test/bitget-funding.csv",
        "source_exchange": "bitget",
        "source_symbol": symbol,
        "window_start_utc": datetime.fromtimestamp(
            start_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "window_end_utc": datetime.fromtimestamp(
            end_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "cadence_hours": 8,
    }


def test_archive_validation_requires_the_full_requested_window(tmp_path):
    archive = tmp_path / "funding.json"
    archive.write_text(
        json.dumps(
            {
                "data": {
                    "resultList": [
                        {"fundingRateTimestamp": "0", "fundingRate": "0"},
                        {
                            "fundingRateTimestamp": str(8 * 60 * 60 * 1000),
                            "fundingRate": "0",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    report, normalized = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="0",
        end=str(16 * 60 * 60 * 1000),
    )

    assert report["status"] == "blocked"
    assert report["eligible_for_historical_selection"] is False
    assert report["quality"]["complete_between_observed_bounds"] is False
    assert report["quality"]["complete_requested_window"] is False
    assert report["quality"]["missing_rows"] == 3
    assert report["quality"]["missing_symbol_rows"] == 2
    assert len(normalized) == 0


def test_archive_validation_accepts_complete_csv_and_records_hash(tmp_path):
    archive = tmp_path / "funding.csv"
    archive.write_text(
        "fundingTime,fundingRate,symbol\n"
        "0,0,BTCUSDT\n"
        f"{8 * 60 * 60 * 1000},0,BTCUSDT\n"
        f"{16 * 60 * 60 * 1000},0,BTCUSDT\n",
        encoding="utf-8",
    )

    report, normalized = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="0",
        end=str(16 * 60 * 60 * 1000),
        source_kind="test_csv",
        metadata=_metadata(),
    )

    assert report["status"] == "passed"
    assert report["eligible_for_historical_selection"] is True
    assert report["source"]["input_format"] == "csv"
    assert len(report["source"]["sha256"]) == 64
    assert len(report["source"]["normalized_payload_sha256"]) == 64
    assert len(normalized) == 3


def test_archive_validation_rejects_foreign_symbol_rows(tmp_path):
    archive = tmp_path / "funding.csv"
    archive.write_text(
        "fundingTime,fundingRate,symbol\n"
        "0,0,BTCUSDT\n"
        f"{8 * 60 * 60 * 1000},0,ETHUSDT\n"
        f"{16 * 60 * 60 * 1000},0,BTCUSDT\n",
        encoding="utf-8",
    )

    report, normalized = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="0",
        end=str(16 * 60 * 60 * 1000),
    )

    assert report["status"] == "blocked"
    assert report["eligible_for_historical_selection"] is False
    assert report["quality"]["foreign_symbol_rows"] == 1
    assert len(normalized) == 2


def test_archive_validation_keeps_invalid_target_rows_in_quality_report(tmp_path):
    archive = tmp_path / "funding.json"
    archive.write_text(
        json.dumps(
            [
                {"timestamp_ms": "0", "funding_rate": "0", "symbol": "BTCUSDT"},
                {"timestamp_ms": "not-a-time", "funding_rate": "0", "symbol": "BTCUSDT"},
                {
                    "timestamp_ms": str(16 * 60 * 60 * 1000),
                    "funding_rate": "0",
                    "symbol": "BTCUSDT",
                },
            ]
        ),
        encoding="utf-8",
    )

    report, normalized = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="0",
        end=str(16 * 60 * 60 * 1000),
    )

    assert report["status"] == "blocked"
    assert report["quality"]["invalid_rows"] == 1
    assert report["quality"]["foreign_symbol_rows"] == 0
    assert len(normalized) == 2


def test_archive_validation_accepts_iso_timestamps_only_with_explicit_provenance(tmp_path):
    archive = tmp_path / "funding.csv"
    archive.write_text(
        "fundingTime,fundingRate,symbol\n"
        "1970-01-01T00:00:00Z,0,BTCUSDT\n"
        "1970-01-01T08:00:00+00:00,0,BTCUSDT\n"
        "1970-01-01T16:00:00+00:00,0,BTCUSDT\n",
        encoding="utf-8",
    )

    report, normalized = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="1970-01-01T00:00:00+00:00",
        end="1970-01-01T16:00:00Z",
        metadata=_metadata(),
    )

    assert report["status"] == "passed"
    assert [row["timestamp_ms"] for row in normalized] == [
        0,
        8 * 60 * 60 * 1000,
        16 * 60 * 60 * 1000,
    ]


def test_archive_validation_requires_row_symbol_or_strict_single_symbol_declaration(tmp_path):
    archive = tmp_path / "funding.json"
    archive.write_text(
        json.dumps(
            {
                "metadata": _metadata(),
                "funding_history": [
                    {"timestamp": "1970-01-01T00:00:00Z", "rate": "0"},
                    {"timestamp": "1970-01-01T08:00:00Z", "rate": "0"},
                    {"timestamp": "1970-01-01T16:00:00Z", "rate": "0"},
                ],
            }
        ),
        encoding="utf-8",
    )

    blocked, _ = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="1970-01-01T00:00:00Z",
        end="1970-01-01T16:00:00Z",
    )

    assert blocked["status"] == "blocked"
    assert blocked["quality"]["missing_symbol_rows"] == 3

    metadata = _metadata()
    metadata["single_symbol_declaration"] = {
        "symbol": "BTCUSDT",
        "scope": "all_rows",
        "all_rows": True,
    }
    archive.write_text(
        json.dumps({"metadata": metadata, "funding_history": json.loads(archive.read_text())["funding_history"]}),
        encoding="utf-8",
    )
    accepted, normalized = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="1970-01-01T00:00:00Z",
        end="1970-01-01T16:00:00Z",
    )

    assert accepted["status"] == "passed"
    assert accepted["archive_contract"]["single_symbol_declaration_valid"] is True
    assert all(row["symbol"] == "BTCUSDT" for row in normalized)


def test_archive_validation_rejects_ambiguous_symbol_even_with_declaration(tmp_path):
    archive = tmp_path / "funding.json"
    metadata = _metadata()
    metadata["single_symbol_declaration"] = {
        "symbol": "BTCUSDT",
        "scope": "all_rows",
        "all_rows": True,
    }
    archive.write_text(
        json.dumps(
            {
                "metadata": metadata,
                "funding_history": [
                    {"timestamp": "1970-01-01T00:00:00Z", "rate": "0", "symbol": []},
                    {"timestamp": "1970-01-01T08:00:00Z", "rate": "0"},
                    {"timestamp": "1970-01-01T16:00:00Z", "rate": "0"},
                ],
            }
        ),
        encoding="utf-8",
    )

    report, _ = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="1970-01-01T00:00:00Z",
        end="1970-01-01T16:00:00Z",
    )

    assert report["status"] == "blocked"
    assert report["quality"]["ambiguous_symbol_rows"] == 1


def test_archive_validation_requires_settlement_provenance_for_a_full_archive(tmp_path):
    archive = tmp_path / "funding.csv"
    archive.write_text(
        "fundingTime,fundingRate,symbol\n"
        "0,0,BTCUSDT\n"
        f"{8 * 60 * 60 * 1000},0,BTCUSDT\n"
        f"{16 * 60 * 60 * 1000},0,BTCUSDT\n",
        encoding="utf-8",
    )
    metadata = _metadata()
    metadata.pop("rate_semantics")

    report, _ = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="0",
        end=str(16 * 60 * 60 * 1000),
        metadata=metadata,
    )

    assert report["quality"]["complete_requested_window"] is True
    assert report["status"] == "blocked"
    assert report["archive_contract"]["metadata_checks"][
        "rate_semantics_applied_settlement"
    ] is False


def test_archive_validation_requires_exact_utc_window_and_eight_hour_cadence(tmp_path):
    archive = tmp_path / "funding.csv"
    archive.write_text(
        "fundingTime,fundingRate,symbol\n"
        "0,0,BTCUSDT\n"
        f"{8 * 60 * 60 * 1000},0,BTCUSDT\n"
        f"{16 * 60 * 60 * 1000},0,BTCUSDT\n",
        encoding="utf-8",
    )
    metadata = _metadata()
    metadata["window_end_utc"] = "1970-01-02T00:00:00+00:00"
    metadata["cadence_hours"] = 1

    report, _ = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="0",
        end=str(16 * 60 * 60 * 1000),
        metadata=metadata,
    )

    assert report["status"] == "blocked"
    checks = report["archive_contract"]["metadata_checks"]
    assert checks["window_end_explicit_utc"] is False
    assert checks["cadence_is_eight_hours"] is False


def test_archive_validation_rejects_explicit_synthetic_rows(tmp_path):
    archive = tmp_path / "funding.json"
    metadata = _metadata()
    archive.write_text(
        json.dumps(
            {
                "metadata": metadata,
                "funding_history": [
                    {
                        "timestamp": "1970-01-01T00:00:00Z",
                        "rate": "0",
                        "symbol": "BTCUSDT",
                        "synthetic": True,
                    },
                    {
                        "timestamp": "1970-01-01T08:00:00Z",
                        "rate": "0",
                        "symbol": "BTCUSDT",
                    },
                    {
                        "timestamp": "1970-01-01T16:00:00Z",
                        "rate": "0",
                        "symbol": "BTCUSDT",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report, normalized = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="1970-01-01T00:00:00Z",
        end="1970-01-01T16:00:00Z",
    )

    assert report["status"] == "blocked"
    assert report["quality"]["synthetic_rows"] == 1
    assert report["quality"]["complete_requested_window"] is False
    assert normalized == [
        {"symbol": "BTCUSDT", "funding_rate": 0.0, "timestamp_ms": 28_800_000},
        {"symbol": "BTCUSDT", "funding_rate": 0.0, "timestamp_ms": 57_600_000},
    ]


def test_archive_validation_rejects_unaligned_requested_window(tmp_path):
    archive = tmp_path / "funding.csv"
    archive.write_text(
        "fundingTime,fundingRate,symbol\n"
        "0,0,BTCUSDT\n",
        encoding="utf-8",
    )
    metadata = _metadata(start_ms=0, end_ms=60 * 60 * 1000)

    report, _ = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="0",
        end=str(60 * 60 * 1000),
        metadata=metadata,
    )

    assert report["status"] == "blocked"
    assert report["quality"]["window_alignment_valid"] is False
    assert report["quality"]["complete_requested_window"] is False


def test_archive_validation_rejects_naive_iso_row_timestamp(tmp_path):
    archive = tmp_path / "funding.csv"
    archive.write_text(
        "fundingTime,fundingRate,symbol\n"
        "1970-01-01T00:00:00,0,BTCUSDT\n"
        "1970-01-01T08:00:00Z,0,BTCUSDT\n"
        "1970-01-01T16:00:00Z,0,BTCUSDT\n",
        encoding="utf-8",
    )

    report, _ = build_validation(
        archive=archive,
        symbol="BTCUSDT",
        start="1970-01-01T00:00:00Z",
        end="1970-01-01T16:00:00Z",
        metadata=_metadata(),
    )

    assert report["status"] == "blocked"
    assert report["quality"]["invalid_rows"] == 1
