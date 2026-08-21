from __future__ import annotations

import json
from pathlib import Path

from research.historical_preflight import (
    CANONICAL_OHLCV_CACHE_DIR,
    _asset_paths,
    _ohlcv_quality_checks,
    build_preflight,
)
from research.historical_protocol import STRESS_MAKER_FEE_LABEL, build_protocol_plan


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONFIG = ROOT / "configs/examples/default_trailing_grid_long_npos7.json"


def test_historical_routing_never_falls_back_to_private_profile(tmp_path):
    paths = _asset_paths(tmp_path, "BTC")

    assert paths["profile"].name == "bitget_btc_market_profile_public_only_2026-08-17.json"
    assert "market_profile_read_only" not in paths["profile"].name


def test_undeclared_remote_fetch_fails_closed(tmp_path):
    quality = {
        "status": "passed",
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

    checks = _ohlcv_quality_checks(quality, root=tmp_path)

    assert checks["canonical_cache_path"] is False
    assert checks["remote_fetch_policy_declared_local_only"] is False
    assert checks["remote_fetch_count_zero"] is False


def test_incomplete_funding_keeps_historical_readiness_blocked():
    result = build_preflight(root=ROOT, assets=("BTC",))
    asset = result["assets"]["BTC"]

    assert result["status"] == "blocked"
    assert asset["execution_checks"]["funding_complete"] is False
    assert any("funding" in reason for reason in asset["reasons"])


def test_protocol_uses_public_fee_and_keeps_stress_fee_explicit(tmp_path):
    preflight = build_preflight(root=ROOT, assets=("BTC",))
    plan = build_protocol_plan(
        root=ROOT,
        config_path=SOURCE_CONFIG,
        assets=("BTC",),
        preflight=preflight,
        output_path=tmp_path / "plan.json",
    )

    generated = plan["inputs"]["generated_configs"]["BTC:long"]
    config = json.loads(Path(generated["path"]).read_text(encoding="utf-8"))
    fee_provenance = plan["fee_provenance"]["BTC"]

    assert config["backtest"]["ohlcv_source_dir"] == CANONICAL_OHLCV_CACHE_DIR
    assert config["backtest"]["maker_fee_override"] == 0.0002
    assert config["backtest"]["scenarios"] == [{"label": "base_public_market_profile"}]
    assert fee_provenance["base"]["source"] == "public_market_profile"
    assert fee_provenance["base"]["maker_fee_rate"] == 0.0002
    assert fee_provenance["stress"]["label"] == STRESS_MAKER_FEE_LABEL
    assert fee_provenance["stress"]["maker_fee_rate"] == 0.0004
    assert fee_provenance["stress"]["enabled_by_default"] is False
    assert plan["inputs"]["remote_fetch_policy"] == "local_only"
    assert plan["inputs"]["legacy_candidate_policy"] == "excluded_from_four_window_protocol"
    assert len(plan["protocol"]["walk_forward_windows"]) == 4
    assert not any("legacy" in str(task).lower() for task in plan["tasks"]["optimizer"])
