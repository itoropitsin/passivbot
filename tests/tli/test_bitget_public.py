"""Offline contract tests for the pure Bitget public-surface adapter."""

from __future__ import annotations

import ast
import hashlib
import inspect
from copy import deepcopy
from pathlib import Path

import pytest

from tli_wings import bitget_public as public


@pytest.fixture
def freshness() -> public.FreshnessPolicy:
    return public.FreshnessPolicy(now_ms=1_700_000_001_000, max_age_ms=2_000)


@pytest.fixture
def instrument_payload() -> dict[str, object]:
    return {
        "code": "00000",
        "msg": "success",
        "requestTime": 1_700_000_000_000,
        "data": [
            {
                "symbol": "BTCUSDT",
                "category": "USDT-FUTURES",
                "baseCoin": "BTC",
                "quoteCoin": "USDT",
                "settleCoin": "USDT",
                "minOrderQty": "0.001",
                "quantityMultiplier": "0.001",
                "pricePrecision": "1",
                "priceMultiplier": "0.1",
                "maxLeverage": "125",
                "status": "online",
            }
        ],
    }


@pytest.fixture
def mark_index_payload() -> dict[str, object]:
    return {
        "code": "00000",
        "msg": "success",
        "requestTime": 1_700_000_000_900,
        "data": [
            {
                "symbol": "BTCUSDT",
                "category": "USDT-FUTURES",
                "markPrice": "100.5",
                "indexPrice": "100.0",
                "ts": "1700000000800",
            }
        ],
    }


@pytest.fixture
def funding_payload() -> dict[str, object]:
    return {
        "code": "00000",
        "msg": "success",
        "requestTime": 1_700_000_000_700,
        "data": [
            {
                "symbol": "BTCUSDT",
                "category": "USDT-FUTURES",
                "fundingRate": "-0.0001",
                "fundingRateInterval": "8",
                "nextUpdate": "1700000028800",
            }
        ],
    }


@pytest.fixture
def maintenance_tiers_payload() -> dict[str, object]:
    return {
        "code": "00000",
        "msg": "success",
        "requestTime": 1_700_000_000_600,
        "data": [
            {
                "tier": "1",
                "symbol": "BTCUSDT",
                "category": "USDT-FUTURES",
                "minTierValue": "0",
                "maxTierValue": "100000",
                "leverage": "125",
                "mmr": "0.004",
            },
            {
                "tier": "2",
                "symbol": "BTCUSDT",
                "category": "USDT-FUTURES",
                "minTierValue": "100000",
                "maxTierValue": "500000",
                "leverage": "50",
                "mmr": "0.005",
            },
        ],
    }


def _surface_kwargs() -> dict[str, object]:
    return {
        "expected_symbol": "BTCUSDT",
        "expected_contract": "USDT-FUTURES",
    }


def test_source_fingerprint_uses_sorted_canonical_json() -> None:
    first = {"z": [True, "x"], "a": {"n": 1}}
    second = {"a": {"n": 1}, "z": [True, "x"]}
    canonical = '{"a":{"n":1},"z":[true,"x"]}'

    assert public.canonical_source_json(first) == canonical
    assert public.canonical_source_json(first) == public.canonical_source_json(second)
    assert public.source_fingerprint(first) == public.source_fingerprint(second)
    assert (
        public.source_fingerprint(first)
        == hashlib.sha256(canonical.encode()).hexdigest()
    )


def test_instrument_normalization_is_strict_and_fresh(
    instrument_payload, freshness
) -> None:
    result = public.normalize_instrument_payload(
        instrument_payload,
        freshness=freshness,
        **_surface_kwargs(),
    )

    assert result.status is public.BitgetPublicStatus.AVAILABLE
    assert result.ok is True
    assert result.error is None
    assert result.unavailable is None
    assert result.value.symbol == "BTCUSDT"
    assert result.value.contract == "USDT-FUTURES"
    assert result.value.settle_coin == "USDT"
    assert result.value.price_step == pytest.approx(0.1)
    assert result.value.source_fingerprint == public.source_fingerprint(
        instrument_payload
    )


def test_mark_index_normalization_accepts_combined_public_ticker(
    mark_index_payload, freshness
) -> None:
    result = public.normalize_mark_index_payload(
        mark_index_payload,
        freshness=freshness,
        **_surface_kwargs(),
    )

    value = result.require()
    assert value.mark_price == pytest.approx(100.5)
    assert value.index_price == pytest.approx(100.0)
    assert value.observed_at_ms == 1_700_000_000_800
    assert result.source_fingerprint == public.source_fingerprint(mark_index_payload)


def test_mark_index_normalization_can_join_two_public_payloads(freshness) -> None:
    mark_payload = {
        "code": "00000",
        "requestTime": 1_700_000_000_900,
        "data": [
            {
                "symbol": "BTCUSDT",
                "category": "USDT-FUTURES",
                "markPrice": "100.5",
                "ts": "1700000000800",
            }
        ],
    }
    index_payload = {
        "code": "00000",
        "requestTime": 1_700_000_000_900,
        "data": [
            {
                "symbol": "BTCUSDT",
                "category": "USDT-FUTURES",
                "indexPrice": "100.0",
                "ts": "1700000000850",
            }
        ],
    }

    result = public.normalize_mark_index_payload(
        mark_payload,
        index_payload,
        freshness=freshness,
        **_surface_kwargs(),
    )

    assert result.ok
    assert result.value.observed_at_ms == 1_700_000_000_800
    assert result.source_fingerprint == public.source_fingerprint(
        {"index": index_payload, "mark": mark_payload}
    )


def test_funding_normalization_preserves_missing_historical_time_as_missing(
    funding_payload, freshness
) -> None:
    result = public.normalize_funding_payload(
        funding_payload,
        freshness=freshness,
        **_surface_kwargs(),
    )

    value = result.require()
    assert value.funding_rate == pytest.approx(-0.0001)
    assert value.next_funding_time_ms == 1_700_000_028_800
    assert value.funding_time_ms is None
    assert value.funding_interval_hours == 8


def test_maintenance_tiers_normalization_checks_order_and_identity(
    maintenance_tiers_payload, freshness
) -> None:
    result = public.normalize_maintenance_tiers_payload(
        maintenance_tiers_payload,
        freshness=freshness,
        **_surface_kwargs(),
    )

    value = result.require()
    assert value.symbol == "BTCUSDT"
    assert value.contract == "USDT-FUTURES"
    assert value.levels == (1, 2)
    assert value.tiers[0].upper_bound == pytest.approx(100000.0)
    assert value.tiers[1].maintenance_margin_rate == pytest.approx(0.005)
    assert value.source_fingerprint == public.source_fingerprint(
        maintenance_tiers_payload
    )


def test_missing_required_mark_price_returns_unavailable(
    mark_index_payload, freshness
) -> None:
    payload = deepcopy(mark_index_payload)
    del payload["data"][0]["markPrice"]

    result = public.normalize_mark_index_payload(
        payload,
        freshness=freshness,
        **_surface_kwargs(),
    )

    assert result.status is public.BitgetPublicStatus.UNAVAILABLE
    assert result.value is None
    assert result.unavailable.reason == "missing_field"
    assert result.unavailable.missing_fields == ("mark_price",)
    assert result.source_fingerprint == public.source_fingerprint(payload)


def test_stale_surface_returns_unavailable_not_a_neutral_price(
    mark_index_payload,
) -> None:
    result = public.normalize_mark_index_payload(
        mark_index_payload,
        freshness=public.FreshnessPolicy(now_ms=1_700_000_010_000, max_age_ms=1_000),
        **_surface_kwargs(),
    )

    assert result.status is public.BitgetPublicStatus.UNAVAILABLE
    assert result.unavailable.reason == "stale"
    assert result.unavailable.age_ms == 9_200
    assert result.value is None


def test_contradictory_symbol_alias_returns_error(
    mark_index_payload, freshness
) -> None:
    payload = deepcopy(mark_index_payload)
    payload["data"][0]["instId"] = "ETHUSDT"

    result = public.normalize_mark_index_payload(
        payload,
        freshness=freshness,
        **_surface_kwargs(),
    )

    assert result.status is public.BitgetPublicStatus.ERROR
    assert result.error.reason == "contradictory_field"
    assert result.error.field == "symbol"
    assert result.value is None


def test_contradictory_mark_and_index_symbol_returns_error(freshness) -> None:
    mark_payload = {
        "code": "00000",
        "requestTime": 1_700_000_000_900,
        "data": [{"symbol": "BTCUSDT", "markPrice": "100.5", "ts": "1700000000800"}],
    }
    index_payload = {
        "code": "00000",
        "requestTime": 1_700_000_000_900,
        "data": [{"symbol": "ETHUSDT", "indexPrice": "100.0", "ts": "1700000000850"}],
    }

    result = public.normalize_mark_index_payload(
        mark_payload,
        index_payload,
        freshness=freshness,
        expected_contract="USDT-FUTURES",
    )

    assert result.status is public.BitgetPublicStatus.ERROR
    assert result.error.reason == "contradictory_identity"
    assert result.error.field == "symbol"


def test_non_finite_numeric_is_error(mark_index_payload, freshness) -> None:
    payload = deepcopy(mark_index_payload)
    payload["data"][0]["indexPrice"] = "NaN"

    result = public.normalize_mark_index_payload(
        payload,
        freshness=freshness,
        **_surface_kwargs(),
    )

    assert result.status is public.BitgetPublicStatus.ERROR
    assert result.error.reason == "non_finite_numeric"
    assert result.error.field == "index_price"


def test_tier_identity_contradiction_is_error(
    maintenance_tiers_payload, freshness
) -> None:
    payload = deepcopy(maintenance_tiers_payload)
    payload["data"][1]["symbol"] = "ETHUSDT"

    result = public.normalize_maintenance_tiers_payload(
        payload,
        freshness=freshness,
        **_surface_kwargs(),
    )

    assert result.status is public.BitgetPublicStatus.ERROR
    assert result.error.reason == "contradictory_identity"
    assert result.error.field == "symbol"


def test_uta_isolated_builder_selects_the_requested_position_side() -> None:
    result = public.build_uta_isolated_order_params(
        symbol="BTCUSDT",
        hold_side="short",
        leverage_by_position_side={"long": 2, "short": 3},
        contract="USDT-FUTURES",
    )

    value = result.require()
    assert value.long_leverage == 2
    assert value.short_leverage == 3
    assert value.leverage == 3
    assert value.as_params() == {
        "marginMode": "isolated",
        "holdSide": "short",
        "leverage": 3,
    }
    assert "reduceOnly" not in value.as_params()


def test_uta_isolated_builder_does_not_default_a_missing_side_leverage() -> None:
    result = public.build_uta_isolated_order_params(
        symbol="BTCUSDT",
        hold_side="long",
        leverage_by_position_side={"long": 2},
    )

    assert result.status is public.BitgetPublicStatus.UNAVAILABLE
    assert result.unavailable.reason == "missing_order_param"
    assert result.unavailable.missing_fields == ("leverage_by_position_side.short",)
    assert result.value is None


def test_uta_isolated_builder_rejects_non_finite_leverage() -> None:
    result = public.build_uta_isolated_order_params(
        symbol="BTCUSDT",
        hold_side="long",
        leverage_by_position_side={"long": "nan", "short": 3},
    )

    assert result.status is public.BitgetPublicStatus.ERROR
    assert result.error.reason == "non_finite_numeric"
    assert result.error.field == "long_leverage"


def test_public_module_has_no_private_or_write_invocation() -> None:
    source_path = Path(inspect.getsourcefile(public)).resolve()
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden_import_roots = {"aiohttp", "ccxt", "httpx", "requests", "websocket"}
    forbidden_calls = {
        "cancel_all_orders",
        "cancel_order",
        "create_order",
        "fetch",
        "private",
        "set_leverage",
        "set_margin_mode",
        "sign",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(
                alias.name.split(".", 1)[0] not in forbidden_import_roots
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".", 1)[0] not in forbidden_import_roots
        elif isinstance(node, ast.Call):
            called_name = ""
            if isinstance(node.func, ast.Name):
                called_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called_name = node.func.attr
            assert called_name not in forbidden_calls
            assert not called_name.startswith("private_")
