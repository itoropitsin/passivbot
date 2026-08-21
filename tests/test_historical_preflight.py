from __future__ import annotations

import hashlib
import json

from research.historical_preflight import (
    CANONICAL_OHLCV_CACHE_DIR,
    _asset_paths,
    _execution_checks,
    _ohlcv_quality_diagnostics,
    _ohlcv_quality_checks,
    build_preflight,
    public_market_fee_provenance,
)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _valid_status():
    return {
        "stages": {
            name: {"status": "passed"}
            for name in ("baseline", "market_profile", "data", "risk_engine", "risk_grid")
        }
    }


def _valid_profile():
    return {
        "schema_version": "bitget-public-market-profile-v1",
        "exchange": "bitget",
        "symbol": "BTCUSDT",
        "read_only_only": True,
        "trading_operations_count": 0,
        "source_response_sha256": {"market_instruments": "hash"},
        "instrument": {
            "status": "online",
            "contract_type": "perpetual",
            "quote_coin": "USDT",
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0006,
            "funding_interval_hours": 8,
        },
        "mark_and_index": {"mark_price": 62_000.0, "index_price": 62_001.0},
        "margin_tiers": [{"tier": "1", "max_notional": 200_000.0}],
    }


def _valid_ohlcv_quality():
    return {
        "status": "passed",
        "exchange": "bitget",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1m",
        "range": {
            "start_utc": "2024-07-01T00:00:00+00:00",
            "end_utc": "2026-08-17T00:00:00+00:00",
        },
        "cache_path": "caches/ohlcvs",
        "remote_fetch_policy": "local_only",
        "remote_fetch_count": 0,
        "checks": {
            "coverage_complete": True,
            "values_finite": True,
            "prices_positive": True,
            "prices_ordered": True,
            "volume_nonnegative": True,
            "chunk_checksums_verified": True,
            "no_future_rows": True,
        },
        "trading_operations_count": 0,
    }


def _valid_execution():
    start_ms = 1719792000000
    funding_rows = [
        {
            "timestamp_ms": start_ms + index * 8 * 60 * 60 * 1000,
            "funding_rate": 0.0,
            "symbol": "BTCUSDT",
        }
        for index in range(2332)
    ]
    return {
        "schema_version": "bitget-public-execution-inputs-v1",
        "exchange": "bitget",
        "symbol": "BTCUSDT",
        "read_only_only": True,
        "trading_operations_count": 0,
        "quality": {
            "requested_start_utc": "2024-07-01T00:00:00+00:00",
            "requested_end_utc": "2026-08-17T00:00:00+00:00",
            "funding": {
                "expected_interval_hours": 8,
                "rows": 2332,
                "future_rows": 0,
            },
            "mark": {"coverage_ratio": 1.0},
            "index": {"coverage_ratio": 1.0},
            "no_future_market_rows": True,
            "no_nan_or_invalid_mark_rows": True,
            "no_nan_or_invalid_index_rows": True,
        },
        "funding_history": funding_rows,
    }


def _valid_metrics():
    return {
        "history_through_plan_date": True,
        "history_coverage_ratio": 1.0,
        "funding_history_complete": True,
    }


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_valid_btc(root, *, quality=None, metrics=None):
    inputs = root / "analysis_reports" / "crypto_bot_roadmap" / "inputs"
    reports = root / "analysis_reports" / "crypto_bot_roadmap"
    _write(reports / "status.json", _valid_status())
    _write(inputs / "bitget_btc_market_profile_public_only_2026-08-17.json", _valid_profile())
    execution_path = (
        inputs
        / "bitget_btc_public_execution_inputs_2024-07-01_to_2026-08-17-repaired-pagination-v2.json"
    )
    _write(execution_path, _valid_execution())
    metric_payload = dict(metrics or _valid_metrics())
    metric_payload["execution_inputs_sha256"] = _sha256(execution_path)
    _write(reports / "data_metrics_public_snapshot_2026-08-17.json", metric_payload)
    _write(
        inputs / "bitget_btc_ohlcv_repair_2024-07-01_to_2026-08-17-v4.json",
        {"status": "repaired", "after_missing_rows": 0, "trading_operations_count": 0},
    )
    (root / CANONICAL_OHLCV_CACHE_DIR).mkdir(parents=True)
    _write(
        inputs / "bitget_btc_ohlcv_quality_2024-07-01_to_2026-08-17-v1.json",
        quality or _valid_ohlcv_quality(),
    )


def test_preflight_passes_only_when_all_btc_inputs_and_stages_are_ready(tmp_path):
    root = tmp_path
    inputs = root / "analysis_reports" / "crypto_bot_roadmap" / "inputs"
    reports = root / "analysis_reports" / "crypto_bot_roadmap"
    _write(reports / "status.json", _valid_status())
    _write(inputs / "bitget_btc_market_profile_public_only_2026-08-17.json", _valid_profile())
    execution_path = (
        inputs
        / "bitget_btc_public_execution_inputs_2024-07-01_to_2026-08-17-repaired-pagination-v2.json"
    )
    _write(execution_path, _valid_execution())
    metrics = _valid_metrics()
    metrics["execution_inputs_sha256"] = _sha256(execution_path)
    _write(reports / "data_metrics_public_snapshot_2026-08-17.json", metrics)
    _write(
        inputs / "bitget_btc_ohlcv_repair_2024-07-01_to_2026-08-17-v4.json",
        {"status": "repaired", "after_missing_rows": 0, "trading_operations_count": 0},
    )

    (root / "caches" / "ohlcvs").mkdir(parents=True)
    _write(
        inputs / "bitget_btc_ohlcv_quality_2024-07-01_to_2026-08-17-v1.json",
        _valid_ohlcv_quality(),
    )

    result = build_preflight(root=root, assets=("BTC",))

    assert result["status"] == "passed"
    assert result["assets"]["BTC"]["ready"] is True
    assert len(result["protocol"]["walk_forward_windows"]) == 4
    assert result["protocol"]["independent_optimizer_runs"] == 5


def test_asset_routing_uses_public_profiles_and_actual_execution_input_names(tmp_path):
    btc = _asset_paths(tmp_path, "BTC")
    eth = _asset_paths(tmp_path, "ETH")

    assert btc["profile"].name == "bitget_btc_market_profile_public_only_2026-08-17.json"
    assert eth["profile"].name == "bitget_eth_market_profile_public_only_2026-08-17.json"
    assert "market_profile_read_only" not in btc["profile"].name
    assert "market_profile_read_only" not in eth["profile"].name
    assert btc["execution_inputs"].name == (
        "bitget_btc_public_execution_inputs_2024-07-01_to_2026-08-17-"
        "repaired-pagination-v2.json"
    )
    assert eth["execution_inputs"].name == (
        "bitget_eth_public_execution_inputs_2024-07-01_to_2026-08-17.json"
    )
    assert btc["ohlcv_cache_dir"] == tmp_path / CANONICAL_OHLCV_CACHE_DIR


def test_funding_gate_is_closed_below_100_percent_and_without_requested_window_proof():
    incomplete = _valid_execution()
    incomplete["funding_history"] = incomplete["funding_history"][:-1]
    checks = _execution_checks(incomplete)

    assert checks["funding_coverage_ratio"] < 1.0
    assert checks["funding_complete"] is False
    assert checks["funding_fail_closed"] is True
    assert checks["funding_failure_reason"] == "coverage_below_100_percent"

    observed_only = _valid_execution()
    observed_only.pop("funding_history")
    observed_only["quality"]["funding"] = {
        "expected_interval_hours": 8,
        "rows": 2332,
        "complete_between_observed_bounds": True,
    }
    observed_checks = _execution_checks(observed_only)

    assert observed_checks["funding_coverage_ratio"] == 0.0
    assert observed_checks["funding_complete"] is False
    assert observed_checks["funding_failure_reason"] == "funding_history_rows_unavailable"


def test_preflight_data_passport_blocks_declared_rows_when_source_has_one_event(tmp_path):
    _seed_valid_btc(tmp_path)
    execution_path = _asset_paths(tmp_path, "BTC")["execution_inputs"]
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    execution["funding_history"] = execution["funding_history"][:1]
    _write(execution_path, execution)

    metrics_path = (
        tmp_path
        / "analysis_reports"
        / "crypto_bot_roadmap"
        / "data_metrics_public_snapshot_2026-08-17.json"
    )
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["execution_inputs_sha256"] = _sha256(execution_path)
    _write(metrics_path, metrics)

    result = build_preflight(root=tmp_path, assets=("BTC",))
    asset = result["assets"]["BTC"]
    passport = asset["data_passport"]

    assert result["status"] == "blocked"
    assert passport["expected_rows"] == 2332
    assert passport["actual_rows"] == 1
    assert passport["missing_rows"] == 2331
    assert passport["qualifying"] is False
    assert asset["execution_checks"]["funding_passport_qualifying"] is False
    assert any("funding gate closed" in reason for reason in asset["reasons"])


def test_remote_fetch_and_cache_contract_fail_closed_on_legacy_or_missing_declarations(tmp_path):
    quality = _valid_ohlcv_quality()
    quality["cache_path"] = "caches/ohlcv"
    legacy_checks = _ohlcv_quality_checks(quality, root=tmp_path)

    assert legacy_checks["canonical_cache_path"] is False

    quality["cache_path"] = CANONICAL_OHLCV_CACHE_DIR
    conflicting_metrics = {"remote_fetch_policy": "local_only", "remote_fetch_count": 1}
    (tmp_path / CANONICAL_OHLCV_CACHE_DIR).mkdir(parents=True)
    conflicting_checks = _ohlcv_quality_checks(
        quality, root=tmp_path, data_metrics=conflicting_metrics
    )

    assert conflicting_checks["canonical_cache_path"] is True
    assert conflicting_checks["remote_fetch_policy_declared_local_only"] is True
    assert conflicting_checks["remote_fetch_count_zero"] is False


def test_quality_provenance_is_not_inferred_from_cache_directory_or_metrics(tmp_path):
    (tmp_path / CANONICAL_OHLCV_CACHE_DIR).mkdir(parents=True)
    quality = _valid_ohlcv_quality()
    quality.pop("cache_path")
    quality.pop("remote_fetch_policy")
    quality.pop("remote_fetch_count")
    metrics = {
        "ohlcv_cache_path": CANONICAL_OHLCV_CACHE_DIR,
        "remote_fetch_policy": "local_only",
        "remote_fetch_count": 0,
    }

    checks = _ohlcv_quality_checks(quality, root=tmp_path, data_metrics=metrics)
    diagnostics = _ohlcv_quality_diagnostics(quality, checks, data_metrics=metrics)

    assert checks["canonical_cache_exists"] is True
    assert checks["canonical_cache_path"] is False
    assert checks["remote_fetch_policy_declared_local_only"] is False
    assert checks["remote_fetch_count_zero"] is False
    assert any("no explicit cache_path" in item for item in diagnostics)
    assert any("no explicit remote_fetch_policy" in item for item in diagnostics)
    assert any("no explicit remote_fetch_count" in item for item in diagnostics)


def test_preflight_surfaces_missing_ohlcv_provenance_as_a_blocker(tmp_path):
    quality = _valid_ohlcv_quality()
    quality.pop("cache_path")
    quality.pop("remote_fetch_policy")
    quality.pop("remote_fetch_count")
    _seed_valid_btc(tmp_path, quality=quality)

    result = build_preflight(root=tmp_path, assets=("BTC",))
    asset = result["assets"]["BTC"]

    assert result["status"] == "blocked"
    assert any(
        "selected quality report has no explicit cache_path declaration" in reason
        for reason in asset["reasons"]
    )
    assert any(
        "selected quality report has no explicit remote_fetch_policy declaration" in reason
        for reason in asset["reasons"]
    )
    assert any(
        "selected quality report has no explicit remote_fetch_count declaration" in reason
        for reason in asset["reasons"]
    )


def test_preflight_blocks_quality_report_for_foreign_symbol(tmp_path):
    _seed_valid_btc(tmp_path)
    quality_path = _asset_paths(tmp_path, "BTC")["ohlcv_quality"]
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["symbol"] = "ETH/USDT:USDT"
    _write(quality_path, quality)

    result = build_preflight(root=tmp_path, assets=("BTC",))

    assert result["status"] == "blocked"
    assert any(
        "symbol does not match expected BTC/USDT:USDT" in reason
        for reason in result["assets"]["BTC"]["reasons"]
    )


def test_preflight_blocks_quality_report_for_foreign_range(tmp_path):
    _seed_valid_btc(tmp_path)
    quality_path = _asset_paths(tmp_path, "BTC")["ohlcv_quality"]
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["range"]["start_utc"] = "2025-01-01T00:00:00+00:00"
    _write(quality_path, quality)

    result = build_preflight(root=tmp_path, assets=("BTC",))

    assert result["status"] == "blocked"
    assert any(
        "range start does not match expected" in reason
        for reason in result["assets"]["BTC"]["reasons"]
    )


def test_preflight_blocks_source_selected_quality_with_mismatched_cache(tmp_path):
    _seed_valid_btc(tmp_path)
    selected_quality = (
        tmp_path
        / "analysis_reports"
        / "crypto_bot_roadmap"
        / "inputs"
        / "selected_quality.json"
    )
    quality = _valid_ohlcv_quality()
    quality["cache_path"] = "caches/other-ohlcvs"
    _write(selected_quality, quality)

    metrics_path = (
        tmp_path
        / "analysis_reports"
        / "crypto_bot_roadmap"
        / "data_metrics_public_snapshot_2026-08-17.json"
    )
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["source_evidence"] = {
        "ohlcv_quality_report": str(selected_quality.relative_to(tmp_path))
    }
    _write(metrics_path, metrics)

    result = build_preflight(root=tmp_path, assets=("BTC",))

    assert result["status"] == "blocked"
    asset = result["assets"]["BTC"]
    assert asset["paths"]["ohlcv_quality"] == str(selected_quality.resolve())
    assert any(
        "cache_path does not resolve to the canonical caches/ohlcvs directory" in reason
        for reason in asset["reasons"]
    )


def test_remote_fetch_count_requires_an_unambiguous_zero(tmp_path):
    quality = _valid_ohlcv_quality()
    quality["remote_fetch_count"] = 0.5

    checks = _ohlcv_quality_checks(quality, root=tmp_path)

    assert checks["remote_fetch_count_zero"] is False


def test_fixture_discovery_follows_metrics_source_evidence(tmp_path):
    selected_quality = (
        tmp_path
        / "analysis_reports"
        / "crypto_bot_roadmap"
        / "inputs"
        / "verified_quality.json"
    )
    metrics = _valid_metrics()
    metrics["source_evidence"] = {
        "ohlcv_quality_report": str(selected_quality.relative_to(tmp_path))
    }
    _seed_valid_btc(tmp_path, metrics=metrics)
    # Keep a same-shaped legacy fixture at the static path.  The metrics
    # evidence must select the explicitly linked quality report instead.
    _write(
        _asset_paths(tmp_path, "BTC")["ohlcv_quality"],
        {"status": "passed", "checks": {}},
    )
    _write(selected_quality, _valid_ohlcv_quality())

    result = build_preflight(root=tmp_path, assets=("BTC",))

    assert result["status"] == "passed"
    assert result["assets"]["BTC"]["paths"]["ohlcv_quality"] == str(
        selected_quality.resolve()
    )


def test_fixture_discovery_rejects_source_evidence_outside_repo(tmp_path):
    metrics = _valid_metrics()
    metrics["source_evidence"] = {
        "ohlcv_quality_report": str(tmp_path.parent / "quality-outside-repo.json")
    }
    _seed_valid_btc(tmp_path, metrics=metrics)

    result = build_preflight(root=tmp_path, assets=("BTC",))
    asset = result["assets"]["BTC"]

    assert result["status"] == "blocked"
    assert any("outside the repository root" in reason for reason in asset["reasons"])
    assert asset["fixture_discovery_errors"]


def test_fee_provenance_keeps_public_base_separate_from_opt_in_stress():
    provenance = public_market_fee_provenance(_valid_profile())

    assert provenance["base"] == {
        "maker_fee_rate": 0.0002,
        "taker_fee_rate": 0.0006,
        "source": "public_market_profile",
        "enabled_by_default": True,
        "status": "passed",
    }
    assert provenance["stress"] == {
        "label": "stress_maker_fee_0_0004",
        "maker_fee_rate": 0.0004,
        "enabled_by_default": False,
        "source": "explicit_stress_only",
        "status": "available",
    }


def test_preflight_reports_missing_eth_as_blocked(tmp_path):
    result = build_preflight(root=tmp_path, assets=("ETH",))

    assert result["status"] == "blocked"
    assert result["assets"]["ETH"]["ready"] is False
    assert any("missing file" in reason for reason in result["assets"]["ETH"]["reasons"])
