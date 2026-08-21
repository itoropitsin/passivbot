from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from tools.bitget_funding_promote import promote_funding_archive


START = "1970-01-01T00:00:00Z"
END = "1970-01-01T16:00:00Z"


def _metadata() -> dict[str, object]:
    return {
        "rate_semantics": "applied_settlement",
        "source_provider": "test-provider",
        "source_reference": "https://example.test/funding.csv",
        "source_exchange": "bitget",
        "source_symbol": "BTCUSDT",
        "window_start_utc": datetime.fromtimestamp(0, tz=timezone.utc).isoformat(),
        "window_end_utc": datetime.fromtimestamp(
            16 * 60 * 60, tz=timezone.utc
        ).isoformat(),
        "cadence_hours": 8,
    }


def _execution_inputs() -> dict[str, object]:
    return {
        "schema_version": "bitget-public-execution-inputs-v1",
        "exchange": "bitget",
        "symbol": "BTCUSDT",
        "read_only_only": True,
        "trading_operations_count": 0,
        "funding_history": [],
        "quality": {"funding": {"complete_requested_window": False}},
    }


def _write_archive(path, *, complete: bool = True) -> None:
    rows = [
        "fundingTime,fundingRate,symbol",
        "0,0,BTCUSDT",
        f"{8 * 60 * 60 * 1000},0,BTCUSDT",
    ]
    if complete:
        rows.append(f"{16 * 60 * 60 * 1000},0,BTCUSDT")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_promotion_creates_new_content_addressed_execution_input(tmp_path):
    archive = tmp_path / "funding.csv"
    execution = tmp_path / "execution.json"
    validation = tmp_path / "validation.json"
    normalized = tmp_path / "normalized.json"
    promoted = tmp_path / "promoted.json"
    _write_archive(archive)
    execution.write_text(json.dumps(_execution_inputs()), encoding="utf-8")

    result = promote_funding_archive(
        archive=archive,
        execution_inputs=execution,
        symbol="BTCUSDT",
        start=START,
        end=END,
        metadata=_metadata(),
        validation_output=validation,
        normalized_output=normalized,
        output=promoted,
    )

    assert result["status"] == "passed"
    assert result["trading_operations_count"] == 0
    assert validation.is_file()
    assert normalized.is_file()
    assert promoted.is_file()
    promoted_payload = json.loads(promoted.read_text(encoding="utf-8"))
    assert len(promoted_payload["funding_history"]) == 3
    provenance = promoted_payload["funding_archive_provenance"]
    assert provenance["eligible_for_historical_selection"] is True
    assert len(provenance["raw_archive_sha256"]) == 64
    assert len(provenance["validation_report_sha256"]) == 64
    assert provenance["normalized_output_sha256"] == result["normalized_output_sha256"]


def test_incomplete_archive_writes_only_blocked_validation(tmp_path):
    archive = tmp_path / "funding.csv"
    execution = tmp_path / "execution.json"
    validation = tmp_path / "validation.json"
    promoted = tmp_path / "promoted.json"
    _write_archive(archive, complete=False)
    execution.write_text(json.dumps(_execution_inputs()), encoding="utf-8")

    result = promote_funding_archive(
        archive=archive,
        execution_inputs=execution,
        symbol="BTCUSDT",
        start=START,
        end=END,
        metadata=_metadata(),
        validation_output=validation,
        output=promoted,
    )

    assert result["status"] == "blocked"
    assert validation.is_file()
    assert not promoted.exists()
    report = json.loads(validation.read_text(encoding="utf-8"))
    assert report["eligible_for_historical_selection"] is False


def test_promotion_rejects_execution_input_for_another_symbol(tmp_path):
    archive = tmp_path / "funding.csv"
    execution = tmp_path / "execution.json"
    _write_archive(archive)
    payload = _execution_inputs()
    payload["symbol"] = "ETHUSDT"
    execution.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="symbol"):
        promote_funding_archive(
            archive=archive,
            execution_inputs=execution,
            symbol="BTCUSDT",
            start=START,
            end=END,
            metadata=_metadata(),
            validation_output=tmp_path / "validation.json",
            output=tmp_path / "promoted.json",
        )
