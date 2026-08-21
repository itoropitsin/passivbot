from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from dataclasses import FrozenInstanceError

import pytest

from exchanges.paper import PaperBitgetCCXTClient
from research.data_passport import build_data_passport
from research.paper_public_inputs import load_public_execution_feed
from test_paper_bitget import PublicBitgetStub


SYMBOL = "BTCUSDT"
PAPER_SYMBOL = "BTC/USDT:USDT"
INTERVAL_MS = 8 * 60 * 60 * 1000
START_MS = 1_000 * INTERVAL_MS
SETTLEMENT_TS = START_MS + INTERVAL_MS
END_MS = START_MS + 2 * INTERVAL_MS
SOURCE_HASH = hashlib.sha256(b"passport-source").hexdigest()


def _write(path, payload):
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _feed_payload(*, complete_window: bool = False):
    payload = {
        "schema_version": "bitget-public-execution-inputs-v1",
        "exchange": "bitget",
        "symbol": SYMBOL,
        "read_only_only": True,
        "trading_operations_count": 0,
        "funding_history": [
            {
                "symbol": SYMBOL,
                "funding_rate": 0.001,
                "timestamp_ms": SETTLEMENT_TS,
            }
        ],
        "mark_price_history": [
            [
                SETTLEMENT_TS - 3_600_000,
                "100000",
                "100100",
                "99900",
                "100000",
                "0",
            ]
        ],
    }
    if complete_window:
        payload["quality"] = {
            "requested_start_utc": START_MS,
            "requested_end_utc": END_MS,
            "funding": {"expected_interval_hours": 8},
        }
    return payload


def test_passport_lists_mark_gap_and_stays_unqualified():
    passport = build_data_passport(
        [
            {"symbol": SYMBOL, "timestamp_ms": START_MS, "funding_rate": 0.0},
            {"symbol": SYMBOL, "timestamp_ms": SETTLEMENT_TS, "funding_rate": 0.0},
            {"symbol": SYMBOL, "timestamp_ms": END_MS, "funding_rate": 0.0},
        ],
        symbol=SYMBOL,
        start_ms=START_MS,
        end_ms=END_MS,
        cadence_interval_ms=INTERVAL_MS,
        mark_timestamps_ms=[SETTLEMENT_TS + 1],
        source_sha256s={"funding": SOURCE_HASH},
    )

    assert passport.expected_rows == 3
    assert passport.actual_rows == 3
    assert passport.mark_missing_timestamps_ms == (START_MS, SETTLEMENT_TS)
    assert passport.missing_timestamps_ms == (START_MS, SETTLEMENT_TS)
    assert passport.mark_before_settlement_coverage == pytest.approx(1 / 3)
    assert passport.qualifying is False
    with pytest.raises(FrozenInstanceError):
        passport.qualifying = True


def test_passport_records_duplicate_foreign_and_future_rows():
    passport = build_data_passport(
        [
            {"symbol": SYMBOL, "timestamp_ms": START_MS, "funding_rate": 0.0},
            {"symbol": SYMBOL, "timestamp_ms": START_MS, "funding_rate": 0.0},
            {"symbol": "ETHUSDT", "timestamp_ms": SETTLEMENT_TS, "funding_rate": 0.0},
            {"symbol": SYMBOL, "timestamp_ms": END_MS + INTERVAL_MS, "funding_rate": 0.0},
        ],
        symbol=SYMBOL,
        start_ms=START_MS,
        end_ms=END_MS,
        cadence_interval_ms=INTERVAL_MS,
        mark_timestamps_ms=[START_MS - 1],
        source_sha256s={"funding": SOURCE_HASH},
    )

    assert passport.duplicate_rows == 1
    assert passport.duplicate_timestamps_ms == (START_MS,)
    assert passport.foreign_rows == 1
    assert passport.foreign_symbols == ("ETHUSDT",)
    assert passport.future_rows == 1
    assert passport.future_timestamps_ms == (END_MS + INTERVAL_MS,)
    assert passport.qualifying is False


def test_feed_status_exposes_window_and_source_passport(tmp_path):
    path = tmp_path / "public-inputs.json"
    source_hash = _write(path, _feed_payload(complete_window=True))

    feed = load_public_execution_feed(path, symbol=SYMBOL)
    status = feed.as_status()
    expected_start = (
        datetime.fromtimestamp(START_MS / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    expected_end = (
        datetime.fromtimestamp(END_MS / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    assert status["passport"]["start_utc"] == expected_start
    assert status["passport"]["end_utc"] == expected_end
    assert status["passport"]["expected_rows"] == 3
    assert status["passport"]["actual_rows"] == 1
    assert SETTLEMENT_TS - INTERVAL_MS in status["passport"]["missing_timestamps_ms"]
    assert status["passport"]["source_sha256s"]["public_input"] == source_hash
    assert status["passport"]["exploratory"] is False
    assert status["qualifying"] is False
    assert status["passport"]["qualifying"] is False


def test_exploratory_feed_requires_an_explicit_marker_and_cannot_qualify(tmp_path):
    path = tmp_path / "exploratory-inputs.json"
    payload = _feed_payload(complete_window=True)
    payload["exploratory"] = True
    _write(path, payload)

    feed = load_public_execution_feed(path, symbol=SYMBOL)

    assert feed.passport.exploratory is True
    assert feed.passport.qualifying is False


def test_complete_feed_qualifies_only_after_passport_checks_pass(tmp_path):
    path = tmp_path / "complete-inputs.json"
    payload = _feed_payload(complete_window=True)
    payload["funding_history"] = [
        {"symbol": SYMBOL, "funding_rate": 0.0, "timestamp_ms": timestamp}
        for timestamp in (START_MS, SETTLEMENT_TS, END_MS)
    ]
    payload["mark_price_history"] = [
        [START_MS - 1, "100000", "100100", "99900", "100000", "0"]
    ]
    _write(path, payload)

    feed = load_public_execution_feed(path, symbol=SYMBOL)

    assert feed.passport.expected_rows == 3
    assert feed.passport.actual_rows == 3
    assert feed.passport.mark_before_settlement_coverage == pytest.approx(1.0)
    assert feed.passport.qualifying is True


@pytest.mark.asyncio
async def test_reconciliation_does_not_promote_one_funding_event_to_full_window(tmp_path):
    input_path = tmp_path / "public-inputs.json"
    _write(input_path, _feed_payload(complete_window=True))
    client = PaperBitgetCCXTClient(
        {
            "live": {
                "paper_symbols": ["BTC"],
                "leverage": 3.0,
                "paper_execution_inputs_path": str(input_path),
                "paper_execution_inputs_since_ms": START_MS,
            }
        },
        {"exchange": "bitget", "quote": "USDT", "paper": True},
        public_client=PublicBitgetStub(),
        initial_balance=1_000.0,
        clock_ms=END_MS,
    )
    await client.load_markets()
    await client.create_order(
        PAPER_SYMBOL,
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "orderId": "passport-entry"},
    )
    assert len(client.apply_recorded_public_execution_inputs(timestamp=END_MS)) == 1

    evidence = client.build_paper_reconciliation_evidence(timestamp=END_MS)

    assert [row["kind"] for row in evidence] == ["reconciliation.check"]
    passport = evidence[0]["payload"]["reconciliation"]["data_passport"]
    assert passport["expected_rows"] == 3
    assert passport["actual_rows"] == 1
    assert passport["qualifying"] is False
    await client.close()
