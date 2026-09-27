from __future__ import annotations

import json
from pathlib import Path

import pytest

from tli_wings.runtime_policy import (
    RuntimePolicyError,
    load_runtime_policy,
    policy_hash,
    validate_runtime_policy,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs" / "tli" / "runtime_policy.json"


def test_project_policy_loads_all_agreed_limits_and_hashes_canonically():
    policy = load_runtime_policy(POLICY_PATH)

    assert policy.sizing_balance_cap == 1000.0
    assert policy.max_initial_entry_sizing_balance_pct == 0.03
    assert policy.max_total_exposure_ratio == 0.80
    assert policy.max_symbol_exposure_ratio == 0.50
    assert policy.max_used_margin_ratio == 0.35
    assert policy.max_active_grids == 2
    assert policy.new_entry_min_liquidation_distance == 0.30
    assert policy.reduce_only_min_liquidation_distance == 0.20
    assert policy.emergency_liquidation_distance == 0.15
    assert policy.daily_loss_stop_ratio == 0.02
    assert policy.margin_mode == "isolated"
    assert policy.hedge_mode is True
    assert policy.leverage_cap == 3.0
    assert policy.max_automatic_restarts_per_day == 1
    assert policy.policy_hash == policy_hash(policy)
    assert len(policy.policy_hash) == 64

    reordered = dict(reversed(list(policy.as_dict().items())))
    assert policy_hash(reordered) == policy.policy_hash


def test_policy_loader_does_not_supply_missing_required_values():
    source = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    source.pop("leverage_cap")

    with pytest.raises(RuntimePolicyError, match="missing required policy field"):
        validate_runtime_policy(source)


def test_policy_loader_rejects_unknown_fields():
    source = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    source["unexpected_default"] = 0

    with pytest.raises(RuntimePolicyError, match="unknown policy field"):
        validate_runtime_policy(source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_active_grids", 2.0),
        ("hedge_mode", 1),
        ("max_total_exposure_ratio", True),
        ("leverage_cap", float("nan")),
    ],
)
def test_policy_loader_rejects_wrong_types_and_non_finite_values(field, value):
    source = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    source[field] = value

    with pytest.raises(RuntimePolicyError):
        validate_runtime_policy(source)


def test_policy_file_rejects_duplicate_json_keys(tmp_path):
    duplicate = '{"schema_version":"tli-runtime-policy-v1","schema_version":"other"}'
    path = tmp_path / "duplicate.json"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(RuntimePolicyError, match="duplicate JSON key"):
        load_runtime_policy(path)
