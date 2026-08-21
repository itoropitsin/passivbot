"""Run the deterministic safety scenarios for the crypto grid risk gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections.abc import Mapping
from numbers import Real
from pathlib import Path
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
ROADMAP_CONFIG_PATH = (
    REPO_ROOT
    / "analysis_reports"
    / "crypto_bot_roadmap"
    / "historical_protocol"
    / "2026-08-17"
    / "configs"
    / "bitget_btc_long_historical.json"
)
ROADMAP_PROFILE_PATH = (
    REPO_ROOT
    / "analysis_reports"
    / "crypto_bot_roadmap"
    / "inputs"
    / "bitget_btc_market_profile_public_only_2026-08-17.json"
)
SCENARIO_SPECS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "no_liquidations",
        (
            "cargo",
            "test",
            "--offline",
            "--manifest-path",
            "passivbot-rust/Cargo.toml",
            "--lib",
            "crypto_portfolio_entry_gate_blocks_entries_inside_liquidation_buffer",
        ),
        ("rust",),
    ),
    (
        "no_duplicate_orders",
        (
            "-m",
            "pytest",
            "-q",
            "tests/test_passivbot_balance_split.py::test_disappeared_emitted_order_record_arms_duplicate_guardrail_without_recent_execution",
        ),
        ("python",),
    ),
    (
        "limits_not_bypassed",
        (
            "-m",
            "pytest",
            "-q",
            "tests/test_passivbot_balance_split.py::test_crypto_portfolio_risk_caps_initial_entry_without_liquidation_price",
            "tests/test_passivbot_balance_split.py::test_crypto_portfolio_risk_handles_executable_dicts_and_allows_only_panic_market_close",
        ),
        ("python",),
    ),
    (
        "state_recovery_consistent",
        (
            "-m",
            "pytest",
            "-q",
            "tests/test_passivbot_balance_split.py::test_restarted_inherited_order_disappearance_uses_confirmation_not_symbol_block",
            "tests/test_passivbot_balance_split.py::test_bot_cancelled_order_disappearance_does_not_arm_duplicate_guardrail",
        ),
        ("python",),
    ),
    (
        "api_outage_safe",
        (
            "-m",
            "pytest",
            "-q",
            "tests/test_passivbot_balance_split.py::test_update_balance_failure_keeps_previous",
            "tests/test_passivbot_balance_split.py::test_pre_create_snapshot_filter_blocks_stale_market_snapshots",
        ),
        ("python",),
    ),
    (
        "manual_position_safe",
        (
            "-m",
            "pytest",
            "-q",
            "tests/test_unstucking_safeguards.py::test_manual_mode_skips_side_orders",
            "tests/test_passivbot_balance_split.py::test_unknown_manual_or_exchange_cancel_requests_confirmation_without_symbol_block",
        ),
        ("python",),
    ),
    (
        "ordinary_market_orders_blocked",
        (
            "-m",
            "pytest",
            "-q",
            "tests/test_passivbot_balance_split.py::test_crypto_portfolio_risk_handles_executable_dicts_and_allows_only_panic_market_close",
        ),
        ("python",),
    ),
    (
        "symmetric_long_short_safety",
        (
            "cargo",
            "test",
            "--offline",
            "--manifest-path",
            "passivbot-rust/Cargo.toml",
            "--lib",
            "one_way_blocks_disabled",
        ),
        ("rust",),
    ),
    (
        "gap_and_multi_level_same_candle",
        (
            "cargo",
            "test",
            "--offline",
            "--manifest-path",
            "passivbot-rust/Cargo.toml",
            "--lib",
            "crypto_portfolio_entry_gate_limits_multiple_gap_levels_in_one_step",
        ),
        ("rust",),
    ),
    (
        "partial_fills",
        (
            "cargo",
            "test",
            "--offline",
            "--manifest-path",
            "passivbot-rust/Cargo.toml",
            "--lib",
            "execution_model_partial_fill_retains_unfilled_quantity",
        ),
        ("rust",),
    ),
    (
        "cancel_repeat_unknown_order_id",
        (
            "-m",
            "pytest",
            "-q",
            "tests/test_passivbot_balance_split.py::test_bot_cancelled_order_disappearance_does_not_arm_duplicate_guardrail",
            "tests/test_passivbot_balance_split.py::test_disappeared_emitted_order_record_arms_duplicate_guardrail_without_recent_execution",
            "tests/test_passivbot_balance_split.py::test_unknown_manual_or_exchange_cancel_requests_confirmation_without_symbol_block",
            "tests/test_passivbot_balance_split.py::test_ambiguous_cancel_forces_full_authoritative_confirmation",
        ),
        ("python",),
    ),
    (
        "funding_input_alignment",
        (
            "-m",
            "pytest",
            "-q",
            "tests/test_backtest_execution_model.py::test_execution_inputs_are_aligned_without_substituting_leading_values",
            "tests/test_risk_parity.py::test_missing_required_snapshot_data_is_rejected",
        ),
        ("python",),
    ),
    (
        "mark_price_funding_valuation",
        (
            "cargo",
            "test",
            "--offline",
            "--manifest-path",
            "passivbot-rust/Cargo.toml",
            "--lib",
            "execution_model_uses_mark_price_for_funding_valuation",
        ),
        ("rust",),
    ),
    (
        "missing_tier_blocks_entries",
        (
            "cargo",
            "test",
            "--offline",
            "--manifest-path",
            "passivbot-rust/Cargo.toml",
            "--lib",
            "crypto_portfolio_entry_gate_blocks_entries_without_position_tiers",
        ),
        ("rust",),
    ),
    (
        "synchronized_btc_eth_safety",
        (
            "cargo",
            "test",
            "--offline",
            "--manifest-path",
            "passivbot-rust/Cargo.toml",
            "--lib",
            "crypto_portfolio_entry_gate_limits_synchronized_btc_eth_exposure",
        ),
        ("rust",),
    ),
)


# These are scenario-level claims, not admission results. A scenario may have
# useful partial evidence and still remain blocked when one required case is
# absent. The runner never turns a missing case into a passing check.
SCENARIO_COVERAGE: tuple[dict[str, Any], ...] = (
    {
        "name": "no_liquidations",
        "required_checks": ("no_liquidations",),
        "coverage_status": "covered",
        "evidence": (
            "passivbot-rust/src/backtest.rs::crypto_portfolio_entry_gate_blocks_entries_inside_liquidation_buffer",
        ),
        "missing": (),
    },
    {
        "name": "no_duplicate_orders",
        "required_checks": ("no_duplicate_orders",),
        "coverage_status": "covered",
        "evidence": (
            "tests/test_passivbot_balance_split.py::test_disappeared_emitted_order_record_arms_duplicate_guardrail_without_recent_execution",
        ),
        "missing": (),
    },
    {
        "name": "limits_not_bypassed",
        "required_checks": ("limits_not_bypassed",),
        "coverage_status": "covered",
        "evidence": (
            "tests/test_passivbot_balance_split.py::test_crypto_portfolio_risk_caps_initial_entry_without_liquidation_price",
            "tests/test_passivbot_balance_split.py::test_crypto_portfolio_risk_handles_executable_dicts_and_allows_only_panic_market_close",
        ),
        "missing": (),
    },
    {
        "name": "state_recovery_consistent",
        "required_checks": ("state_recovery_consistent",),
        "coverage_status": "covered",
        "evidence": (
            "tests/test_passivbot_balance_split.py::test_restarted_inherited_order_disappearance_uses_confirmation_not_symbol_block",
            "tests/test_passivbot_balance_split.py::test_bot_cancelled_order_disappearance_does_not_arm_duplicate_guardrail",
        ),
        "missing": (),
    },
    {
        "name": "api_outage_stale_data",
        "required_checks": ("api_outage_safe",),
        "coverage_status": "covered",
        "evidence": (
            "tests/test_passivbot_balance_split.py::test_update_balance_failure_keeps_previous",
            "tests/test_passivbot_balance_split.py::test_pre_create_snapshot_filter_blocks_stale_market_snapshots",
        ),
        "missing": (),
    },
    {
        "name": "manual_position_safe",
        "required_checks": ("manual_position_safe",),
        "coverage_status": "covered",
        "evidence": (
            "tests/test_unstucking_safeguards.py::test_manual_mode_skips_side_orders",
            "tests/test_passivbot_balance_split.py::test_unknown_manual_or_exchange_cancel_requests_confirmation_without_symbol_block",
        ),
        "missing": (),
    },
    {
        "name": "ordinary_market_orders_blocked",
        "required_checks": ("ordinary_market_orders_blocked",),
        "coverage_status": "covered",
        "evidence": (
            "tests/test_passivbot_balance_split.py::test_crypto_portfolio_risk_handles_executable_dicts_and_allows_only_panic_market_close",
        ),
        "missing": (),
    },
    {
        "name": "trend_plus_minus_35_or_symmetric_long_short_safety",
        "required_checks": ("symmetric_long_short_safety",),
        "coverage_status": "covered",
        "requested_forms": ("trend +35/-35", "symmetric long/short safety"),
        "evidence": (
            "passivbot-rust/src/orchestrator.rs::one_way_blocks_disabled_long_allows_short_initials",
            "passivbot-rust/src/orchestrator.rs::one_way_blocks_disabled_short_allows_long_initials",
        ),
        "missing": (),
    },
    {
        "name": "gap_and_multi_level_same_candle",
        "required_checks": ("gap_and_multi_level_same_candle",),
        "coverage_status": "covered",
        "evidence": (
            "passivbot-rust/src/backtest.rs::crypto_portfolio_entry_gate_limits_multiple_gap_levels_in_one_step",
        ),
        "missing": (),
    },
    {
        "name": "partial_fills",
        "required_checks": ("partial_fills",),
        "coverage_status": "covered",
        "evidence": (
            "passivbot-rust/src/backtest.rs::execution_model_partial_fill_retains_unfilled_quantity",
        ),
        "missing": (),
    },
    {
        "name": "cancel_repeat_unknown_order_id",
        "required_checks": ("cancel_repeat_unknown_order_id",),
        "coverage_status": "covered",
        "evidence": (
            "tests/test_passivbot_balance_split.py::test_bot_cancelled_order_disappearance_does_not_arm_duplicate_guardrail",
            "tests/test_passivbot_balance_split.py::test_disappeared_emitted_order_record_arms_duplicate_guardrail_without_recent_execution",
            "tests/test_passivbot_balance_split.py::test_unknown_manual_or_exchange_cancel_requests_confirmation_without_symbol_block",
            "tests/test_passivbot_balance_split.py::test_ambiguous_cancel_forces_full_authoritative_confirmation",
        ),
        "missing": (),
    },
    {
        "name": "restart_manual_position",
        "required_checks": ("state_recovery_consistent", "manual_position_safe"),
        "coverage_status": "covered",
        "evidence": (
            "tests/test_passivbot_balance_split.py::test_restarted_inherited_order_disappearance_uses_confirmation_not_symbol_block",
            "tests/test_unstucking_safeguards.py::test_manual_mode_skips_side_orders",
        ),
        "missing": (),
    },
    {
        "name": "funding_mark_tier_inputs",
        "required_checks": (
            "funding_input_alignment",
            "mark_price_funding_valuation",
            "missing_tier_blocks_entries",
        ),
        "coverage_status": "covered",
        "evidence": (
            "tests/test_backtest_execution_model.py::test_execution_inputs_are_aligned_without_substituting_leading_values",
            "tests/test_risk_parity.py::test_missing_required_snapshot_data_is_rejected",
            "passivbot-rust/src/backtest.rs::execution_model_uses_mark_price_for_funding_valuation",
            "passivbot-rust/src/backtest.rs::crypto_portfolio_entry_gate_blocks_entries_without_position_tiers",
        ),
        "missing": (),
    },
    {
        "name": "synchronized_btc_eth_safety",
        "required_checks": ("synchronized_btc_eth_safety",),
        "coverage_status": "covered",
        "evidence": (
            "passivbot-rust/src/backtest.rs::crypto_portfolio_entry_gate_limits_synchronized_btc_eth_exposure",
        ),
        "missing": (),
    },
)


ROADMAP_MAX_GRID_LEVELS = 8
ROADMAP_MAX_INITIAL_ENTRY_PCT = 0.03
ROADMAP_MAX_LEVERAGE = 3.0
CONFIG_MODES = ("test", "roadmap")
_MISSING = object()


def _config_value(config: Mapping[str, Any], path: str) -> Any:
    """Read a dotted configuration path without changing the input mapping."""

    current: Any = config
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _shown_value(value: Any) -> Any:
    return None if value is _MISSING else value


def _check(
    check_id: str,
    path: str,
    value: Any,
    *,
    passed: bool,
    expected: Any,
    message: str,
    source: str = "config",
) -> dict[str, Any]:
    missing = value is _MISSING
    return {
        "id": check_id,
        "path": path,
        "source": source,
        "passed": bool(passed) and not missing,
        "expected": expected,
        "observed": _shown_value(value),
        "missing": missing,
        "message": message if passed and not missing else f"{path}: {message}",
    }


def _is_number_at_most(value: Any, maximum: float) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0 < float(value) <= maximum
    )


def _is_finite_real(value: Any, *, minimum: float | None = None) -> bool:
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and (minimum is None or numeric >= minimum)


def _is_partial_fill_schedule(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, Real)
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            and 0 < float(item) <= 1
            for item in value
        )
        and any(float(item) < 1 for item in value)
    )


def _is_level_limit(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= ROADMAP_MAX_GRID_LEVELS
    )


def _missing_config_preflight(*, mode: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "crypto-risk-grid-config-preflight-v1",
        "mode": mode,
        "config_class": "roadmap_admission" if mode == "roadmap" else "test_strategy",
        "status": "failed",
        "eligible_for_roadmap": False,
        "candidate_admission": False,
        "read_only_external_operations": True,
        "trading_operations_count": 0,
        "checks": [],
        "failed_checks": [],
        "reasons": [reason],
    }


def validate_risk_grid_config(
    config: Mapping[str, Any], *, mode: str = "test"
) -> dict[str, Any]:
    """Validate a risk-grid config without running the bot or touching an account.

    ``test`` deliberately does not perform roadmap admission checks. This keeps
    old educational configs useful for unit/backtest work while making it
    impossible to mistake them for an approved roadmap config. Only the
    explicit ``roadmap`` mode evaluates the fixed safety contract.
    """

    if mode not in CONFIG_MODES:
        raise ValueError(f"unsupported risk-grid config mode: {mode!r}")
    if not isinstance(config, Mapping):
        raise TypeError("risk-grid config must be a mapping")

    if mode == "test":
        return {
            "schema_version": "crypto-risk-grid-config-preflight-v1",
            "mode": "test",
            "config_class": "test_strategy",
            "status": "test_only",
            "eligible_for_roadmap": False,
            "candidate_admission": False,
            "read_only_external_operations": True,
            "trading_operations_count": 0,
            "checks": [],
            "failed_checks": [],
            "reasons": [
                "test mode is intentionally not a roadmap admission check",
            ],
        }

    checks: list[dict[str, Any]] = []

    portfolio_risk_enabled = _config_value(config, "live.portfolio_risk.enabled")
    checks.append(
        _check(
            "portfolio_risk_enabled",
            "live.portfolio_risk.enabled",
            portfolio_risk_enabled,
            passed=portfolio_risk_enabled is True,
            expected=True,
            message="roadmap mode requires live.portfolio_risk.enabled=true",
        )
    )

    explicit_authorization = _config_value(
        config, "live.portfolio_risk.require_explicit_live_authorization"
    )
    checks.append(
        _check(
            "explicit_live_authorization_required",
            "live.portfolio_risk.require_explicit_live_authorization",
            explicit_authorization,
            passed=explicit_authorization is True,
            expected=True,
            message="roadmap mode requires a separate explicit live authorization",
        )
    )

    hedge_mode = _config_value(config, "live.hedge_mode")
    checks.append(
        _check(
            "hedge_mode_enabled",
            "live.hedge_mode",
            hedge_mode,
            passed=hedge_mode is True,
            expected=True,
            message="roadmap mode requires hedge mode",
        )
    )

    margin_mode = _config_value(config, "live.margin_mode_preference")
    margin_is_isolated = (
        isinstance(margin_mode, str) and margin_mode.strip().lower() == "isolated"
    )
    checks.append(
        _check(
            "isolated_margin_mode",
            "live.margin_mode_preference",
            margin_mode,
            passed=margin_is_isolated,
            expected="isolated",
            message="roadmap mode rejects cross-only margin configuration",
        )
    )

    leverage = _config_value(config, "live.leverage")
    checks.append(
        _check(
            "leverage_at_most_3",
            "live.leverage",
            leverage,
            passed=_is_number_at_most(leverage, ROADMAP_MAX_LEVERAGE),
            expected={"minimum_exclusive": 0, "maximum": ROADMAP_MAX_LEVERAGE},
            message="live leverage must be positive and no greater than 3x",
        )
    )

    initial_entry_cap = _config_value(
        config, "live.portfolio_risk.max_initial_entry_sizing_balance_pct"
    )
    initial_entry_cap_source = "config"
    if initial_entry_cap is _MISSING:
        # This is the schema default used by the backtest/live adapters.  Keep
        # the source visible so a roadmap report cannot confuse a default with
        # a value explicitly present in the JSON file.
        initial_entry_cap = ROADMAP_MAX_INITIAL_ENTRY_PCT
        initial_entry_cap_source = "schema_default"
    checks.append(
        _check(
            "max_initial_entry_sizing_pct_at_most_0_03",
            "live.portfolio_risk.max_initial_entry_sizing_balance_pct",
            initial_entry_cap,
            passed=_is_number_at_most(initial_entry_cap, ROADMAP_MAX_INITIAL_ENTRY_PCT),
            expected={
                "minimum_exclusive": 0,
                "maximum": ROADMAP_MAX_INITIAL_ENTRY_PCT,
            },
            message="the portfolio first-entry cap must be positive and no greater than 3%",
            source=initial_entry_cap_source,
        )
    )

    for side in ("long", "short"):
        initial_entry_path = f"bot.{side}.entry_initial_qty_pct"
        initial_entry_qty_pct = _config_value(config, initial_entry_path)
        checks.append(
            _check(
                f"{side}_entry_initial_qty_pct_at_most_0_03",
                initial_entry_path,
                initial_entry_qty_pct,
                passed=_is_number_at_most(
                    initial_entry_qty_pct, ROADMAP_MAX_INITIAL_ENTRY_PCT
                ),
                expected={
                    "minimum_exclusive": 0,
                    "maximum": ROADMAP_MAX_INITIAL_ENTRY_PCT,
                },
                message=f"{side} first entry must be positive and no greater than 3%",
            )
        )
        path = f"bot.{side}.entry_grid_max_levels"
        levels = _config_value(config, path)
        checks.append(
            _check(
                f"{side}_entry_grid_levels_at_most_8",
                path,
                levels,
                passed=_is_level_limit(levels),
                expected={"minimum": 1, "maximum": ROADMAP_MAX_GRID_LEVELS},
                message=f"{side} entry grid must contain no more than 8 levels",
            )
        )

    market_orders_allowed = _config_value(config, "live.market_orders_allowed")
    checks.append(
        _check(
            "ordinary_market_orders_disabled",
            "live.market_orders_allowed",
            market_orders_allowed,
            passed=market_orders_allowed is False,
            expected=False,
            message="ordinary market orders must remain disabled",
        )
    )

    execution_model_enabled = _config_value(config, "backtest.execution_model.enabled")
    checks.append(
        _check(
            "execution_model_enabled",
            "backtest.execution_model.enabled",
            execution_model_enabled,
            passed=execution_model_enabled is True,
            expected=True,
            message="roadmap mode requires the bounded execution model",
        )
    )

    funding_enabled = _config_value(config, "backtest.execution_model.funding_enabled")
    checks.append(
        _check(
            "funding_execution_enabled",
            "backtest.execution_model.funding_enabled",
            funding_enabled,
            passed=funding_enabled is True,
            expected=True,
            message="roadmap mode requires funding input handling",
        )
    )

    funding_missing_data = _config_value(
        config, "backtest.execution_model.funding_missing_data"
    )
    checks.append(
        _check(
            "missing_funding_data_blocks",
            "backtest.execution_model.funding_missing_data",
            funding_missing_data,
            passed=(
                isinstance(funding_missing_data, str)
                and funding_missing_data.strip().lower() == "blocked"
            ),
            expected="blocked",
            message="missing funding input must block the scenario",
        )
    )

    mark_price_basis = _config_value(
        config, "backtest.execution_model.mark_price_basis"
    )
    checks.append(
        _check(
            "mark_price_valuation_enabled",
            "backtest.execution_model.mark_price_basis",
            mark_price_basis,
            passed=(
                isinstance(mark_price_basis, str)
                and mark_price_basis.strip().lower() == "mark"
            ),
            expected="mark",
            message="roadmap mode requires mark-price valuation",
        )
    )

    same_candle_ordering = _config_value(
        config, "backtest.execution_model.adverse_same_candle_ordering"
    )
    checks.append(
        _check(
            "same_candle_ordering_is_worst_case",
            "backtest.execution_model.adverse_same_candle_ordering",
            same_candle_ordering,
            passed=(
                isinstance(same_candle_ordering, str)
                and same_candle_ordering.strip().lower() == "worst_case"
            ),
            expected="worst_case",
            message="same-candle stress must use the adverse ordering",
        )
    )

    partial_fill_ratios = _config_value(
        config, "backtest.execution_model.partial_fill_ratios"
    )
    checks.append(
        _check(
            "partial_fill_schedule_present",
            "backtest.execution_model.partial_fill_ratios",
            partial_fill_ratios,
            passed=_is_partial_fill_schedule(partial_fill_ratios),
            expected="non-empty values in (0, 1] including a partial value",
            message="roadmap mode requires a partial-fill schedule",
        )
    )

    gap_stress_pct = _config_value(config, "backtest.execution_model.gap_stress_pct")
    checks.append(
        _check(
            "gap_stress_enabled",
            "backtest.execution_model.gap_stress_pct",
            gap_stress_pct,
            passed=_is_number_at_most(gap_stress_pct, 1.0),
            expected={"minimum_exclusive": 0, "maximum": 1.0},
            message="roadmap mode requires a bounded gap stress input",
        )
    )

    failed_checks = [check["id"] for check in checks if not check["passed"]]
    eligible = not failed_checks
    return {
        "schema_version": "crypto-risk-grid-config-preflight-v1",
        "mode": "roadmap",
        "config_class": "roadmap_admission",
        "status": "passed" if eligible else "failed",
        "eligible_for_roadmap": eligible,
        "candidate_admission": False,
        "read_only_external_operations": True,
        "trading_operations_count": 0,
        "checks": checks,
        "failed_checks": failed_checks,
        "reasons": (
            []
            if eligible
            else [
                f"configuration check failed: {check_id}" for check_id in failed_checks
            ]
        ),
    }


def validate_roadmap_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Explicit alias for callers that only want roadmap admission checks."""

    return validate_risk_grid_config(config, mode="roadmap")


def _profile_tiers_are_valid(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    previous_min = -math.inf
    for tier in value:
        if not isinstance(tier, Mapping):
            return False
        minimum = tier.get("min_notional")
        maximum = tier.get("max_notional")
        maintenance = tier.get("maintenance_margin_rate")
        max_leverage = tier.get("max_leverage")
        if not (
            _is_finite_real(minimum, minimum=0.0)
            and _is_finite_real(maximum, minimum=0.0)
            and float(minimum) <= float(maximum)
            and float(minimum) >= previous_min
            and _is_finite_real(maintenance, minimum=0.0)
            and float(maintenance) <= 1.0
            and _is_number_at_most(max_leverage, 1_000_000.0)
        ):
            return False
        previous_min = float(minimum)
    return True


def validate_roadmap_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one public, read-only Bitget market profile.

    This checks only the immutable public market surface used by the local
    scenarios. It deliberately does not infer account authorization from a
    market profile.
    """

    if not isinstance(profile, Mapping):
        raise TypeError("roadmap market profile must be a mapping")

    checks: list[dict[str, Any]] = []
    schema_version = profile.get("schema_version", _MISSING)
    checks.append(
        _check(
            "public_market_profile_schema",
            "schema_version",
            schema_version,
            passed=(
                isinstance(schema_version, str)
                and schema_version == "bitget-public-market-profile-v1"
            ),
            expected="bitget-public-market-profile-v1",
            message="roadmap mode requires a normalized public market profile",
            source="profile",
        )
    )

    exchange = profile.get("exchange", _MISSING)
    checks.append(
        _check(
            "profile_exchange_is_bitget",
            "exchange",
            exchange,
            passed=isinstance(exchange, str) and exchange.strip().lower() == "bitget",
            expected="bitget",
            message="the roadmap market profile must be for Bitget",
            source="profile",
        )
    )

    read_only_only = profile.get("read_only_only", _MISSING)
    checks.append(
        _check(
            "profile_is_read_only",
            "read_only_only",
            read_only_only,
            passed=read_only_only is True,
            expected=True,
            message="the market profile must declare read-only collection",
            source="profile",
        )
    )

    trading_operations_count = profile.get("trading_operations_count", _MISSING)
    checks.append(
        _check(
            "profile_has_no_trading_operations",
            "trading_operations_count",
            trading_operations_count,
            passed=trading_operations_count == 0,
            expected=0,
            message="profile collection must contain no trading operations",
            source="profile",
        )
    )

    category = profile.get("category", _MISSING)
    checks.append(
        _check(
            "profile_category_is_usdt_futures",
            "category",
            category,
            passed=isinstance(category, str) and category == "USDT-FUTURES",
            expected="USDT-FUTURES",
            message="roadmap scenarios require the USDT futures market",
            source="profile",
        )
    )

    instrument = profile.get("instrument", _MISSING)
    instrument_status = (
        _config_value(instrument, "status")
        if isinstance(instrument, Mapping)
        else _MISSING
    )
    checks.append(
        _check(
            "profile_instrument_online",
            "instrument.status",
            instrument_status,
            passed=isinstance(instrument_status, str) and instrument_status == "online",
            expected="online",
            message="the profiled instrument must be online",
            source="profile",
        )
    )
    contract_type = (
        _config_value(instrument, "contract_type")
        if isinstance(instrument, Mapping)
        else _MISSING
    )
    checks.append(
        _check(
            "profile_instrument_perpetual",
            "instrument.contract_type",
            contract_type,
            passed=isinstance(contract_type, str) and contract_type == "perpetual",
            expected="perpetual",
            message="the profiled instrument must be perpetual",
            source="profile",
        )
    )
    quote_coin = (
        _config_value(instrument, "quote_coin")
        if isinstance(instrument, Mapping)
        else _MISSING
    )
    checks.append(
        _check(
            "profile_quote_coin_usdt",
            "instrument.quote_coin",
            quote_coin,
            passed=isinstance(quote_coin, str) and quote_coin.upper() == "USDT",
            expected="USDT",
            message="the profiled instrument must settle in USDT",
            source="profile",
        )
    )

    mark_and_index = profile.get("mark_and_index", _MISSING)
    mark_price = (
        _config_value(mark_and_index, "mark_price")
        if isinstance(mark_and_index, Mapping)
        else _MISSING
    )
    index_price = (
        _config_value(mark_and_index, "index_price")
        if isinstance(mark_and_index, Mapping)
        else _MISSING
    )
    checks.append(
        _check(
            "profile_mark_price_present",
            "mark_and_index.mark_price",
            mark_price,
            passed=_is_number_at_most(mark_price, 1_000_000_000.0),
            expected="finite positive mark price",
            message="mark price is required for the roadmap stress inputs",
            source="profile",
        )
    )
    checks.append(
        _check(
            "profile_index_price_present",
            "mark_and_index.index_price",
            index_price,
            passed=_is_number_at_most(index_price, 1_000_000_000.0),
            expected="finite positive index price",
            message="index price is required for the roadmap stress inputs",
            source="profile",
        )
    )

    funding = profile.get("funding", _MISSING)
    funding_rate = (
        _config_value(funding, "current_rate")
        if isinstance(funding, Mapping)
        else _MISSING
    )
    funding_interval = (
        _config_value(funding, "interval_hours")
        if isinstance(funding, Mapping)
        else _MISSING
    )
    checks.append(
        _check(
            "profile_funding_rate_present",
            "funding.current_rate",
            funding_rate,
            passed=_is_finite_real(funding_rate),
            expected="finite funding rate",
            message="funding input is required for the roadmap stress inputs",
            source="profile",
        )
    )
    checks.append(
        _check(
            "profile_funding_interval_present",
            "funding.interval_hours",
            funding_interval,
            passed=_is_number_at_most(funding_interval, 168.0),
            expected={"minimum_exclusive": 0, "maximum": 168.0},
            message="funding interval must be a bounded positive number",
            source="profile",
        )
    )

    margin_tiers = profile.get("margin_tiers", _MISSING)
    margin_tier_count = (
        len(margin_tiers) if isinstance(margin_tiers, list) else margin_tiers
    )
    checks.append(
        _check(
            "profile_margin_tiers_complete",
            "margin_tiers",
            margin_tier_count,
            passed=_profile_tiers_are_valid(margin_tiers),
            expected="ordered non-empty maintenance-tier list",
            message="position tiers are required and must be ordered",
            source="profile",
        )
    )

    source_hashes = profile.get("source_response_sha256", _MISSING)
    hashes_present = (
        isinstance(source_hashes, Mapping)
        and bool(source_hashes)
        and all(
            isinstance(value, str) and len(value) == 64
            for value in source_hashes.values()
        )
    )
    checks.append(
        _check(
            "profile_source_hashes_present",
            "source_response_sha256",
            source_hashes,
            passed=hashes_present,
            expected="non-empty SHA-256 source response map",
            message="profile evidence must retain source response hashes",
            source="profile",
        )
    )

    failed_checks = [check["id"] for check in checks if not check["passed"]]
    eligible = not failed_checks
    return {
        "schema_version": "crypto-risk-grid-market-profile-preflight-v1",
        "profile_class": "public_market_profile",
        "status": "passed" if eligible else "failed",
        "eligible_for_roadmap": eligible,
        "read_only_external_operations": True,
        "trading_operations_count": 0,
        "symbol": profile.get("symbol"),
        "checks": checks,
        "failed_checks": failed_checks,
        "reasons": (
            []
            if eligible
            else [
                f"market profile check failed: {check_id}" for check_id in failed_checks
            ]
        ),
    }


def _missing_profile_preflight(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "crypto-risk-grid-market-profile-preflight-v1",
        "profile_class": "public_market_profile",
        "status": "failed",
        "eligible_for_roadmap": False,
        "read_only_external_operations": True,
        "trading_operations_count": 0,
        "profiles": [],
        "failed_profiles": [],
        "reasons": [reason],
    }


def _load_profile_preflight(paths: list[Path]) -> dict[str, Any]:
    if not paths:
        return _missing_profile_preflight(
            "roadmap mode requires at least one explicit public market profile"
        )

    profiles: list[dict[str, Any]] = []
    failed_profiles: list[str] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            failed_profiles.append(str(resolved))
            profiles.append(
                {
                    **_missing_profile_preflight(
                        f"unable to read JSON market profile: {exc}"
                    ),
                    "path": str(resolved),
                }
            )
            continue

        source_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            profile = json.loads(raw.decode("utf-8"))
            result = validate_roadmap_profile(profile)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            result = _missing_profile_preflight(str(exc))
        result["path"] = str(resolved)
        result["sha256"] = source_sha256
        profiles.append(result)
        if result["status"] != "passed":
            failed_profiles.append(str(resolved))

    eligible = bool(profiles) and not failed_profiles
    return {
        "schema_version": "crypto-risk-grid-market-profile-preflight-v1",
        "profile_class": "public_market_profile",
        "status": "passed" if eligible else "failed",
        "eligible_for_roadmap": eligible,
        "read_only_external_operations": True,
        "trading_operations_count": 0,
        "profiles": profiles,
        "failed_profiles": failed_profiles,
        "reasons": (
            []
            if eligible
            else ["one or more public market profiles failed roadmap preflight"]
        ),
    }


def _load_config_preflight(path: Path, *, mode: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        result = _missing_config_preflight(
            mode=mode,
            reason=f"unable to read JSON config: {exc}",
        )
        result["path"] = str(resolved)
        return result

    source_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        config = json.loads(raw.decode("utf-8"))
        result = validate_risk_grid_config(config, mode=mode)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        result = _missing_config_preflight(mode=mode, reason=str(exc))
    result["path"] = str(resolved)
    result["sha256"] = source_sha256
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "JSON strategy config; roadmap mode defaults to the checked-in "
            "roadmap config"
        ),
    )
    parser.add_argument(
        "--profile",
        "--market-profile",
        dest="profile_paths",
        type=Path,
        action="append",
        help=(
            "normalized public market profile; repeat for more than one "
            "symbol (roadmap mode defaults to the BTC profile)"
        ),
    )
    parser.add_argument(
        "--config-mode",
        "--mode",
        dest="config_mode",
        choices=CONFIG_MODES,
        default="test",
        help=(
            "config scope: test keeps the config test-only; roadmap applies "
            "the explicit admission contract"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT
        / "analysis_reports"
        / "crypto_bot_roadmap"
        / "inputs"
        / "risk_grid_scenarios_2026-08-17.json",
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    return parser


def _rust_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # Every Rust check is a local reproducibility check.  Do not let Cargo
    # resolve or download anything while this read-only runner is executing.
    environment["CARGO_NET_OFFLINE"] = "true"
    python_path = REPO_ROOT / ".venv" / "bin" / "python"
    if python_path.exists():
        environment.setdefault("PYO3_PYTHON", str(python_path))
    framework_python = Path(
        "/opt/homebrew/opt/python@3.12/Frameworks/Python.framework/Versions/3.12/Python"
    )
    if framework_python.exists():
        link_arg = f"-C link-arg={framework_python}"
        flags = environment.get("RUSTFLAGS", "")
        if link_arg not in flags:
            environment["RUSTFLAGS"] = f"{flags} {link_arg}".strip()
    return environment


def _run_check(
    name: str,
    args: tuple[str, ...],
    toolchain: tuple[str, ...],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = (sys.executable, *args) if toolchain == ("python",) else args
    environment = _rust_environment() if toolchain == ("rust",) else os.environ.copy()
    try:
        completed = subprocess.run(
            list(command),
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        output = (completed.stdout or "") + (
            "\n" + completed.stderr if completed.stderr else ""
        )
        return {
            "name": name,
            "command": list(command),
            "exit_code": completed.returncode,
            "passed": completed.returncode == 0,
            "output_sha256": hashlib.sha256(
                output.encode("utf-8", "replace")
            ).hexdigest(),
            "output_tail": output[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        output = str(exc.output or "") + str(exc.stderr or "")
        return {
            "name": name,
            "command": list(command),
            "exit_code": 124,
            "passed": False,
            "output_sha256": hashlib.sha256(
                output.encode("utf-8", "replace")
            ).hexdigest(),
            "output_tail": output[-4000:],
            "error": "timeout",
        }


def _build_scenario_coverage(
    checks: list[dict[str, Any]], *, checks_ran: bool
) -> list[dict[str, Any]]:
    """Join executable check results to the broader scenario catalogue."""

    by_name = {check["name"]: check for check in checks}
    coverage: list[dict[str, Any]] = []
    for scenario in SCENARIO_COVERAGE:
        required_checks = tuple(scenario.get("required_checks", ()))
        observed_checks = [by_name[name] for name in required_checks if name in by_name]
        if not checks_ran:
            execution_status = "not_run"
        elif not required_checks:
            execution_status = "not_applicable"
        elif len(observed_checks) != len(required_checks):
            execution_status = "not_run"
        elif all(check["passed"] for check in observed_checks):
            execution_status = "passed"
        else:
            execution_status = "failed"

        declared_status = scenario["coverage_status"]
        if declared_status in {"blocked", "missing"}:
            status = "blocked"
        elif execution_status == "passed":
            status = "passed"
        elif execution_status == "failed":
            status = "failed"
        else:
            status = "not_run"

        evidence_checks = [
            {
                "check": check["name"],
                "passed": bool(check.get("passed", False)),
                "exit_code": check.get("exit_code"),
                "command": list(check.get("command", ())),
                "output_sha256": check.get("output_sha256"),
            }
            for check in observed_checks
        ]
        evidence_sources = list(scenario.get("evidence", ()))
        evidence_complete = bool(evidence_sources) and (
            len(observed_checks) == len(required_checks)
            and all(
                isinstance(item["output_sha256"], str) and bool(item["output_sha256"])
                for item in evidence_checks
            )
        )

        item = {
            "name": scenario["name"],
            "coverage_status": declared_status,
            "status": status,
            "execution_status": execution_status,
            "required_checks": list(required_checks),
            "evidence": evidence_sources,
            "evidence_checks": evidence_checks,
            "evidence_complete": evidence_complete,
            "missing": list(scenario.get("missing", ())),
        }
        if "requested_forms" in scenario:
            item["requested_forms"] = list(scenario["requested_forms"])
        coverage.append(item)
    return coverage


def _scenario_summary(coverage: list[dict[str, Any]]) -> dict[str, int]:
    summary = {status: 0 for status in ("passed", "failed", "blocked", "not_run")}
    for scenario in coverage:
        status = scenario["status"]
        if status not in summary:
            raise ValueError(f"unknown scenario result status: {status!r}")
        summary[status] += 1
    summary["total"] = len(coverage)
    return summary


def _input_evidence(
    config_preflight: Mapping[str, Any],
    profile_preflight: Mapping[str, Any],
) -> dict[str, Any]:
    profiles = profile_preflight.get("profiles", ())
    return {
        "config": {
            "path": config_preflight.get("path"),
            "sha256": config_preflight.get("sha256"),
            "status": config_preflight.get("status"),
        },
        "profiles": [
            {
                "path": profile.get("path"),
                "sha256": profile.get("sha256"),
                "status": profile.get("status"),
                "symbol": profile.get("symbol"),
            }
            for profile in profiles
            if isinstance(profile, Mapping)
        ],
    }


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if args.config is None and args.config_mode == "roadmap":
        config_path = ROADMAP_CONFIG_PATH
        config_preflight = _load_config_preflight(config_path, mode="roadmap")
    elif args.config is None:
        config_path = None
        config_preflight = {
            "schema_version": "crypto-risk-grid-config-preflight-v1",
            "mode": "test",
            "config_class": "not_provided",
            "status": "not_requested",
            "eligible_for_roadmap": False,
            "candidate_admission": False,
            "read_only_external_operations": True,
            "trading_operations_count": 0,
            "checks": [],
            "failed_checks": [],
            "reasons": ["no config was provided; local scenario checks only"],
        }
    else:
        config_path = args.config
        config_preflight = _load_config_preflight(config_path, mode=args.config_mode)

    profile_paths = list(args.profile_paths or ())
    if args.config_mode == "roadmap" and not profile_paths:
        profile_paths = [ROADMAP_PROFILE_PATH]
    profile_preflight = (
        _load_profile_preflight(profile_paths)
        if args.config_mode == "roadmap"
        else {
            "schema_version": "crypto-risk-grid-market-profile-preflight-v1",
            "profile_class": "not_provided",
            "status": "not_requested",
            "eligible_for_roadmap": False,
            "read_only_external_operations": True,
            "trading_operations_count": 0,
            "profiles": [],
            "failed_profiles": [],
            "reasons": ["no roadmap market profile was requested"],
        }
    )

    config_failed = config_preflight["status"] == "failed"
    profile_failed = profile_preflight["status"] == "failed"
    preflight_failed = config_failed or profile_failed
    checks_ran = not preflight_failed
    if preflight_failed:
        # A failed preflight must not be hidden by a green unit-scenario run.
        checks: list[dict[str, Any]] = []
    else:
        checks = [
            _run_check(name, command, toolchain, timeout_seconds=args.timeout_seconds)
            for name, command, toolchain in SCENARIO_SPECS
        ]
    input_evidence = _input_evidence(config_preflight, profile_preflight)
    for check in checks:
        check["input_evidence"] = input_evidence
    metrics = {check["name"]: bool(check["passed"]) for check in checks}
    scenario_coverage = _build_scenario_coverage(checks, checks_ran=checks_ran)
    for scenario in scenario_coverage:
        scenario["input_evidence"] = input_evidence
    missing_scenarios = [
        scenario["name"]
        for scenario in scenario_coverage
        if scenario["coverage_status"] in {"blocked", "missing"}
    ]
    failed_scenarios = [
        scenario["name"]
        for scenario in scenario_coverage
        if scenario["status"] == "failed"
    ]
    evidence_incomplete_scenarios = [
        scenario["name"]
        for scenario in scenario_coverage
        if not scenario["evidence_complete"]
    ]
    scenario_summary = _scenario_summary(scenario_coverage)
    if preflight_failed or failed_scenarios or evidence_incomplete_scenarios:
        status = "failed"
    elif missing_scenarios or scenario_summary["not_run"]:
        status = "blocked"
    else:
        status = "passed"
    result = {
        "schema_version": "crypto-risk-grid-scenarios-v1",
        "stage": "risk_grid",
        "status": status,
        "config_mode": args.config_mode,
        "config_path": str(config_path.resolve()) if config_path is not None else None,
        "config_preflight": config_preflight,
        "profile_preflight": profile_preflight,
        "roadmap_config_eligible": bool(
            config_preflight.get("eligible_for_roadmap", False)
        ),
        "roadmap_profile_eligible": bool(
            profile_preflight.get("eligible_for_roadmap", False)
        ),
        "scenario_coverage_complete": bool(
            not preflight_failed
            and not missing_scenarios
            and not failed_scenarios
            and not evidence_incomplete_scenarios
            and scenario_summary["not_run"] == 0
        ),
        # This tool proves only local checks. Candidate admission still needs
        # the complete scenario runner and the later roadmap stages.
        "candidate_admission": False,
        "admission_scope": "local_risk_grid_checks_only",
        "read_only_external_operations": True,
        "trading_operations_count": 0,
        **metrics,
        "metrics": metrics,
        "checks": checks,
        "scenario_coverage": scenario_coverage,
        "missing_scenarios": missing_scenarios,
        "failed_scenarios": failed_scenarios,
        "evidence_incomplete_scenarios": evidence_incomplete_scenarios,
        "scenario_summary": scenario_summary,
    }
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps({"output": str(output), "status": result["status"]}, sort_keys=True)
    )
    return result


def main(argv: list[str] | None = None) -> int:
    result = run(argv)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
