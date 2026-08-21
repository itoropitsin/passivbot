from __future__ import annotations

import json

import pytest
from test_paper_bitget import PublicBitgetStub, _client, _default_surface

from exchanges.paper import PaperBitgetCCXTClient
from research.paper_market_surfaces import (
    DEFAULT_MAX_BOOK_AGE_MS,
    DEPTH_FILL_MODEL_MODE,
    EVIDENCE_SCHEMA,
    PaperMarketSurfaceError,
    build_paper_execution_evidence,
    consume_queue,
    extract_mark_index,
    load_margin_tiers,
    load_paper_market_profile,
    maintenance_for_notional,
    paper_fill_model,
    queue_ahead_quantity,
    require_fresh_book,
    validate_paper_execution_evidence,
    walk_visible_depth,
    write_paper_execution_evidence,
)


def _tiers(symbol="BTC/USDT:USDT", rate=0.004):
    return [
        {
            "symbol": symbol,
            "tier": "1",
            "min_notional": 0.0,
            "max_notional": 200_000.0,
            "maintenance_margin_rate": rate,
        }
    ]


def test_extract_mark_index_rejects_missing_named_fields():
    with pytest.raises(PaperMarketSurfaceError, match="cannot substitute"):
        extract_mark_index(
            {"last": 100_050.0, "bid": 100_000.0, "ask": 100_100.0},
            symbol="BTC/USDT:USDT",
            required=True,
        )


def test_extract_mark_index_does_not_use_last_as_mark():
    quote = extract_mark_index(
        {
            "last": 100_050.0,
            "markPrice": 99_900.0,
            "indexPrice": 99_910.0,
        },
        symbol="BTC/USDT:USDT",
    )

    assert quote["mark_price"] == pytest.approx(99_900.0)
    assert quote["index_price"] == pytest.approx(99_910.0)
    assert quote["last_price"] == pytest.approx(100_050.0)
    assert quote["mark_source"] == "markPrice"
    assert quote["last_substituted"] is False


def test_maintenance_requires_explicit_tiers_and_uses_bound_rate():
    with pytest.raises(PaperMarketSurfaceError, match="margin_tiers"):
        load_margin_tiers({"symbol": "BTCUSDT"}, symbol="BTCUSDT")

    selected = maintenance_for_notional(
        _tiers(rate=0.004),
        symbol="BTC/USDT:USDT",
        notional=1_000.0,
    )
    assert selected["maintenance_margin_rate"] == pytest.approx(0.004)
    assert selected["source"] == "bound_margin_tiers"


def test_fill_model_is_explicit_and_not_qualifying():
    model = paper_fill_model()

    assert model["queue_modeled"] is False
    assert model["depth_modeled"] is False
    assert model["qualifying"] is False
    assert model["mode"] == "explicit_amounts_only"


def _book(timestamp=1_700_000_000_000, *, asks=None, bids=None, symbol="BTC/USDT:USDT"):
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "bids": bids if bids is not None else [[100_000.0, 0.5]],
        "asks": asks if asks is not None else [[100_100.0, 0.2], [100_101.0, 1.0]],
    }


def test_walk_visible_depth_partial_and_rejects_empty_or_stale():
    clock = 1_700_000_000_000
    plan = walk_visible_depth(
        _book(),
        side="buy",
        quantity=2.0,
        clock_ms=clock,
    )
    assert plan["filled_quantity"] == pytest.approx(1.2)
    assert plan["remaining_quantity"] == pytest.approx(0.8)
    assert plan["depth_exhausted"] is True
    assert plan["vwap"] == pytest.approx((0.2 * 100_100.0 + 1.0 * 100_101.0) / 1.2)
    assert plan["depth_modeled"] is True
    assert plan["queue_modeled"] is False

    with pytest.raises(PaperMarketSurfaceError, match="empty"):
        walk_visible_depth(
            _book(asks=[]),
            side="buy",
            quantity=0.01,
            clock_ms=clock,
        )
    with pytest.raises(PaperMarketSurfaceError, match="stale"):
        walk_visible_depth(
            _book(),
            side="buy",
            quantity=0.01,
            clock_ms=clock + DEFAULT_MAX_BOOK_AGE_MS + 1,
        )
    with pytest.raises(PaperMarketSurfaceError, match="integer"):
        require_fresh_book(
            {**_book(), "timestamp": 1_700_000_000_000.5},
            clock_ms=clock,
        )


def test_queue_ahead_is_conservative_and_partial_consume():
    clock = 1_700_000_000_000
    snapshot = queue_ahead_quantity(
        _book(bids=[[99_000.0, 0.02], [98_900.0, 1.0]]),
        side="buy",
        price=99_000.0,
        clock_ms=clock,
    )
    assert snapshot["queue_ahead"] == pytest.approx(0.02)

    first = consume_queue(
        queue_ahead=0.02, aggressive_quantity=0.015, order_remaining=0.01
    )
    assert first["fill_quantity"] == pytest.approx(0.0)
    assert first["queue_ahead"] == pytest.approx(0.005)

    second = consume_queue(
        queue_ahead=0.005, aggressive_quantity=0.01, order_remaining=0.01
    )
    assert second["fill_quantity"] == pytest.approx(0.005)
    assert second["queue_ahead"] == pytest.approx(0.0)


def test_fresh_book_fill_model_records_depth_but_not_qualifying():
    model = paper_fill_model(book=_book(), clock_ms=1_700_000_000_000)

    assert model["mode"] == DEPTH_FILL_MODEL_MODE
    assert model["queue_modeled"] is True
    assert model["depth_modeled"] is True
    assert model["qualifying"] is False
    assert model["book_stale"] is False


def test_evidence_rejects_queue_overclaim_without_book():
    mark_index = extract_mark_index(
        {"markPrice": 99_900.0, "indexPrice": 99_910.0},
        symbol="BTC/USDT:USDT",
    )
    evidence = build_paper_execution_evidence(
        symbol="BTC/USDT:USDT",
        mark_index=mark_index,
        tiers=_tiers(),
    )
    evidence["fill_model"] = {
        **evidence["fill_model"],
        "queue_modeled": True,
        "depth_modeled": True,
        "mode": DEPTH_FILL_MODEL_MODE,
    }
    with pytest.raises(PaperMarketSurfaceError, match="without a bound book"):
        validate_paper_execution_evidence(evidence)


def test_evidence_writer_stays_fail_closed_and_records_gaps(tmp_path):
    mark_index = extract_mark_index(
        {"markPrice": 99_900.0, "indexPrice": 99_910.0, "last": 100_050.0},
        symbol="BTC/USDT:USDT",
    )
    evidence = build_paper_execution_evidence(
        symbol="BTC/USDT:USDT",
        mark_index=mark_index,
        tiers=_tiers(),
        input_hashes={"market_profile": "abc"},
    )
    path = tmp_path / "paper_execution_evidence.json"
    written = write_paper_execution_evidence(path, evidence)

    assert written["schema_version"] == EVIDENCE_SCHEMA
    assert written["qualifying"] is False
    assert "queue_not_modeled" in written["qualifying_blockers"]
    assert written["maintenance"]["hardcoded_half_percent"] is False
    assert path.is_file()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert validate_paper_execution_evidence(loaded)["evidence_sha256"] == written["evidence_sha256"]


def test_evidence_rejects_qualifying_claim_and_last_substitution():
    mark_index = extract_mark_index(
        {"markPrice": 99_900.0, "indexPrice": 99_910.0},
        symbol="BTC/USDT:USDT",
    )
    evidence = build_paper_execution_evidence(
        symbol="BTC/USDT:USDT",
        mark_index=mark_index,
        tiers=_tiers(),
    )
    evidence["qualifying"] = True
    with pytest.raises(PaperMarketSurfaceError, match="cannot claim qualifying"):
        validate_paper_execution_evidence(evidence)

    evidence = build_paper_execution_evidence(
        symbol="BTC/USDT:USDT",
        mark_index={**mark_index, "last_substituted": True},
        tiers=_tiers(),
    )
    with pytest.raises(PaperMarketSurfaceError, match="last-as-mark"):
        validate_paper_execution_evidence(evidence)


def test_load_public_market_profile_requires_tiers(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "schema_version": "bitget-public-market-profile-v1",
                "margin_tiers": _tiers("BTCUSDT", 0.004),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    loaded = load_paper_market_profile(path)
    assert loaded["symbol"] == "BTCUSDT"
    assert loaded["margin_tiers"][0]["maintenance_margin_rate"] == pytest.approx(0.004)


@pytest.mark.asyncio
async def test_paper_positions_fail_closed_without_mark_or_tiers():
    public = PublicBitgetStub()
    public.prices["BTC/USDT:USDT"] = {
        "bid": 100_000.0,
        "ask": 100_100.0,
        "last": 100_050.0,
    }
    client = _client(public, bind_surface=False)
    await client.load_markets()
    await client.create_order(
        "BTC/USDT:USDT",
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "clientOid": "paper-no-mark"},
    )

    with pytest.raises(PaperMarketSurfaceError, match="cannot substitute"):
        await client.fetch_positions()

    public.prices["BTC/USDT:USDT"].update(
        {"markPrice": 99_900.0, "indexPrice": 99_910.0}
    )
    with pytest.raises(PaperMarketSurfaceError, match="not bound"):
        await client.fetch_positions()


@pytest.mark.asyncio
async def test_paper_positions_use_bound_tier_not_half_percent():
    client = _client(PublicBitgetStub())
    await client.load_markets()
    await client.create_order(
        "BTC/USDT:USDT",
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "clientOid": "paper-tier"},
    )
    positions = await client.fetch_positions()
    entry = float(positions[0]["entryPrice"])
    leverage = 3.0
    expected = entry * (1.0 - 1.0 / leverage + 0.004)
    half_percent = entry * (1.0 - 1.0 / leverage + 0.005)

    assert positions[0]["info"]["paperMaintenanceRate"] == pytest.approx(0.004)
    assert positions[0]["liquidationPrice"] == pytest.approx(expected)
    assert positions[0]["liquidationPrice"] != pytest.approx(half_percent)


@pytest.mark.asyncio
async def test_paper_client_writes_mandatory_evidence(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    client = PaperBitgetCCXTClient(
        {
            "live": {
                "paper_symbols": ["BTC"],
                "leverage": 3.0,
                "paper_execution_evidence_path": str(evidence_path),
            }
        },
        {"exchange": "bitget", "quote": "USDT", "paper": True},
        public_client=PublicBitgetStub(),
        initial_balance=1_000.0,
        clock_ms=1_700_000_000_000,
    )
    client.bind_paper_market_surface(_default_surface("BTC/USDT:USDT"))
    await client.load_markets()
    await client.create_order(
        "BTC/USDT:USDT",
        "market",
        "buy",
        0.01,
        params={"posSide": "long", "clientOid": "paper-evidence"},
    )
    await client.fetch_positions()

    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["qualifying"] is False
    assert payload["fill_model"]["queue_modeled"] is False
    assert payload["mark_index"]["mark_source"] == "markPrice"
    assert payload["maintenance"]["hardcoded_half_percent"] is False
    assert "queue_not_modeled" in payload["gaps"]
