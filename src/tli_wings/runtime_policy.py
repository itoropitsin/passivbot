"""Strict, local loading of the TLI-Wings runtime policy.

This module deliberately has no exchange, credential, or Passivbot imports.  It
is the Python-side policy boundary for the migration; execution code can use
the returned immutable value without changing the policy on the way in.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


POLICY_SCHEMA_VERSION = "tli-runtime-policy-v1"


class RuntimePolicyError(ValueError):
    """Raised when a runtime policy is not an exact, safe policy document."""


# The names intentionally match the TLI risk vocabulary used by the migration
# plan.  Every field is required.  A missing value is an invalid policy, not a
# request to use a module-level default.
_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "collateral_currency",
        "sizing_balance_cap",
        "max_initial_entry_sizing_balance_pct",
        "max_total_exposure_ratio",
        "max_symbol_exposure_ratio",
        "max_used_margin_ratio",
        "max_active_grids",
        "new_entry_min_liquidation_distance",
        "reduce_only_min_liquidation_distance",
        "emergency_liquidation_distance",
        "daily_loss_stop_ratio",
        "margin_mode",
        "hedge_mode",
        "leverage_cap",
        "max_automatic_restarts_per_day",
        "require_explicit_live_authorization",
        "allow_borrowing",
        "withdrawals_allowed",
        "ip_allowlist_required",
    }
)

_NUMERIC_FIELDS = frozenset(
    {
        "sizing_balance_cap",
        "max_initial_entry_sizing_balance_pct",
        "max_total_exposure_ratio",
        "max_symbol_exposure_ratio",
        "max_used_margin_ratio",
        "new_entry_min_liquidation_distance",
        "reduce_only_min_liquidation_distance",
        "emergency_liquidation_distance",
        "daily_loss_stop_ratio",
        "leverage_cap",
    }
)


def _duplicate_key_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimePolicyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> None:
    raise RuntimePolicyError(f"non-standard JSON number is not allowed: {value}")


def _parse_json_document(text: str, source: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_duplicate_key_object,
            parse_constant=_reject_nonstandard_number,
        )
    except RuntimePolicyError as exc:
        raise RuntimePolicyError(f"{source}: {exc}") from exc
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimePolicyError(f"{source}: invalid JSON: {exc}") from exc


def _require_string(data: Mapping[str, Any], field_name: str) -> str:
    value = data[field_name]
    if not isinstance(value, str) or not value:
        raise RuntimePolicyError(f"{field_name} must be a non-empty string")
    return value


def _require_bool(data: Mapping[str, Any], field_name: str) -> bool:
    value = data[field_name]
    if type(value) is not bool:
        raise RuntimePolicyError(f"{field_name} must be boolean")
    return value


def _require_number(data: Mapping[str, Any], field_name: str) -> float:
    value = data[field_name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimePolicyError(f"{field_name} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise RuntimePolicyError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise RuntimePolicyError(f"{field_name} must be finite")
    return number


def _require_integer(data: Mapping[str, Any], field_name: str) -> int:
    value = data[field_name]
    if type(value) is not int:
        raise RuntimePolicyError(f"{field_name} must be an integer")
    return value


def _validate_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimePolicyError("runtime policy must be a JSON object")

    data = dict(value)
    missing = sorted(_REQUIRED_FIELDS - data.keys())
    unknown = sorted(data.keys() - _REQUIRED_FIELDS, key=str)
    if missing:
        raise RuntimePolicyError(
            "missing required policy field(s): " + ", ".join(missing)
        )
    if unknown:
        raise RuntimePolicyError(
            "unknown policy field(s): " + ", ".join(str(name) for name in unknown)
        )

    schema_version = _require_string(data, "schema_version")
    if schema_version != POLICY_SCHEMA_VERSION:
        raise RuntimePolicyError(
            f"schema_version must be {POLICY_SCHEMA_VERSION!r}"
        )

    collateral_currency = _require_string(data, "collateral_currency")
    if collateral_currency != "USDT":
        raise RuntimePolicyError("collateral_currency must be 'USDT'")

    margin_mode = _require_string(data, "margin_mode")
    if margin_mode != "isolated":
        raise RuntimePolicyError("margin_mode must be 'isolated'")

    for field_name in _NUMERIC_FIELDS:
        _require_number(data, field_name)
    max_active_grids = _require_integer(data, "max_active_grids")
    max_restarts = _require_integer(data, "max_automatic_restarts_per_day")

    hedge_mode = _require_bool(data, "hedge_mode")
    require_authorization = _require_bool(
        data, "require_explicit_live_authorization"
    )
    allow_borrowing = _require_bool(data, "allow_borrowing")
    withdrawals_allowed = _require_bool(data, "withdrawals_allowed")
    ip_allowlist_required = _require_bool(data, "ip_allowlist_required")

    sizing_balance_cap = float(data["sizing_balance_cap"])
    initial_entry = float(data["max_initial_entry_sizing_balance_pct"])
    total_exposure = float(data["max_total_exposure_ratio"])
    symbol_exposure = float(data["max_symbol_exposure_ratio"])
    used_margin = float(data["max_used_margin_ratio"])
    new_entry_distance = float(data["new_entry_min_liquidation_distance"])
    reduce_only_distance = float(data["reduce_only_min_liquidation_distance"])
    emergency_distance = float(data["emergency_liquidation_distance"])
    daily_loss = float(data["daily_loss_stop_ratio"])
    leverage_cap = float(data["leverage_cap"])

    if not 0.0 < sizing_balance_cap <= 1_000.0:
        raise RuntimePolicyError(
            "sizing_balance_cap must be greater than zero and no more than 1000"
        )
    if not 0.0 < initial_entry <= 0.03:
        raise RuntimePolicyError(
            "max_initial_entry_sizing_balance_pct must be in (0, 0.03]"
        )
    if not 0.0 < total_exposure <= 0.80:
        raise RuntimePolicyError("max_total_exposure_ratio must be in (0, 0.80]")
    if not 0.0 < symbol_exposure <= 0.50:
        raise RuntimePolicyError("max_symbol_exposure_ratio must be in (0, 0.50]")
    if not 0.0 < used_margin <= 0.35:
        raise RuntimePolicyError("max_used_margin_ratio must be in (0, 0.35]")
    if not 1 <= max_active_grids <= 2:
        raise RuntimePolicyError("max_active_grids must be an integer in [1, 2]")
    if not 0.0 < emergency_distance <= 1.0:
        raise RuntimePolicyError(
            "emergency_liquidation_distance must be in (0, 1]"
        )
    if not 0.0 < reduce_only_distance <= 1.0:
        raise RuntimePolicyError(
            "reduce_only_min_liquidation_distance must be in (0, 1]"
        )
    if not 0.0 < new_entry_distance < 1.0:
        raise RuntimePolicyError(
            "new_entry_min_liquidation_distance must be in (0, 1)"
        )
    if emergency_distance < 0.15:
        raise RuntimePolicyError(
            "emergency_liquidation_distance must be at least 0.15"
        )
    if reduce_only_distance < 0.20:
        raise RuntimePolicyError(
            "reduce_only_min_liquidation_distance must be at least 0.20"
        )
    if new_entry_distance < 0.30:
        raise RuntimePolicyError(
            "new_entry_min_liquidation_distance must be at least 0.30"
        )
    if not (
        emergency_distance <= reduce_only_distance <= new_entry_distance
    ):
        raise RuntimePolicyError(
            "liquidation distances must satisfy emergency <= reduce_only <= new_entry"
        )
    if not 0.0 < daily_loss <= 0.02:
        raise RuntimePolicyError("daily_loss_stop_ratio must be in (0, 0.02]")
    if not 0.0 < leverage_cap <= 3.0:
        raise RuntimePolicyError("leverage_cap must be in (0, 3.0]")
    if max_restarts < 0 or max_restarts > 1:
        raise RuntimePolicyError(
            "max_automatic_restarts_per_day must be an integer in [0, 1]"
        )

    if not hedge_mode:
        raise RuntimePolicyError("hedge_mode must be true")
    if not require_authorization:
        raise RuntimePolicyError("require_explicit_live_authorization must be true")
    if allow_borrowing:
        raise RuntimePolicyError("allow_borrowing must be false")
    if withdrawals_allowed:
        raise RuntimePolicyError("withdrawals_allowed must be false")
    if not ip_allowlist_required:
        raise RuntimePolicyError("ip_allowlist_required must be true")

    # Preserve the validated JSON values, including their original integer or
    # float spelling, for a stable hash of the actual document semantics.
    return data


@dataclass(frozen=True, slots=True)
class RuntimePolicy(Mapping[str, Any]):
    """Validated, immutable view of one TLI runtime policy document."""

    schema_version: str
    collateral_currency: str
    sizing_balance_cap: float
    max_initial_entry_sizing_balance_pct: float
    max_total_exposure_ratio: float
    max_symbol_exposure_ratio: float
    max_used_margin_ratio: float
    max_active_grids: int
    new_entry_min_liquidation_distance: float
    reduce_only_min_liquidation_distance: float
    emergency_liquidation_distance: float
    daily_loss_stop_ratio: float
    margin_mode: str
    hedge_mode: bool
    leverage_cap: float
    max_automatic_restarts_per_day: int
    require_explicit_live_authorization: bool
    allow_borrowing: bool
    withdrawals_allowed: bool
    ip_allowlist_required: bool
    _raw: dict[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimePolicy":
        if isinstance(value, cls):
            return value
        data = _validate_mapping(value)
        return cls(
            schema_version=data["schema_version"],
            collateral_currency=data["collateral_currency"],
            sizing_balance_cap=float(data["sizing_balance_cap"]),
            max_initial_entry_sizing_balance_pct=float(
                data["max_initial_entry_sizing_balance_pct"]
            ),
            max_total_exposure_ratio=float(data["max_total_exposure_ratio"]),
            max_symbol_exposure_ratio=float(data["max_symbol_exposure_ratio"]),
            max_used_margin_ratio=float(data["max_used_margin_ratio"]),
            max_active_grids=data["max_active_grids"],
            new_entry_min_liquidation_distance=float(
                data["new_entry_min_liquidation_distance"]
            ),
            reduce_only_min_liquidation_distance=float(
                data["reduce_only_min_liquidation_distance"]
            ),
            emergency_liquidation_distance=float(
                data["emergency_liquidation_distance"]
            ),
            daily_loss_stop_ratio=float(data["daily_loss_stop_ratio"]),
            margin_mode=data["margin_mode"],
            hedge_mode=data["hedge_mode"],
            leverage_cap=float(data["leverage_cap"]),
            max_automatic_restarts_per_day=data["max_automatic_restarts_per_day"],
            require_explicit_live_authorization=data[
                "require_explicit_live_authorization"
            ],
            allow_borrowing=data["allow_borrowing"],
            withdrawals_allowed=data["withdrawals_allowed"],
            ip_allowlist_required=data["ip_allowlist_required"],
            _raw=deepcopy(data),
        )

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def as_dict(self, *, include_hash: bool = False) -> dict[str, Any]:
        result = deepcopy(self._raw)
        if include_hash:
            result["policy_hash"] = self.policy_hash
        return result

    to_dict = as_dict

    @property
    def policy_hash(self) -> str:
        return policy_hash(self)

    # Short names are read-only conveniences for callers that use the policy as
    # a risk object.  The JSON names above remain the only accepted input names.
    @property
    def max_initial_entry_pct(self) -> float:
        return self.max_initial_entry_sizing_balance_pct

    @property
    def sizing_balance_cap_usdt(self) -> float:
        return self.sizing_balance_cap

    @property
    def max_total_exposure(self) -> float:
        return self.max_total_exposure_ratio

    @property
    def max_symbol_exposure(self) -> float:
        return self.max_symbol_exposure_ratio

    @property
    def max_used_margin(self) -> float:
        return self.max_used_margin_ratio

    @property
    def new_entry_min_distance(self) -> float:
        return self.new_entry_min_liquidation_distance

    @property
    def reduce_only_min_distance(self) -> float:
        return self.reduce_only_min_liquidation_distance

    @property
    def emergency_distance(self) -> float:
        return self.emergency_liquidation_distance

    @property
    def daily_loss_stop(self) -> float:
        return self.daily_loss_stop_ratio

    @property
    def leverage(self) -> float:
        return self.leverage_cap

    @property
    def position_mode(self) -> str:
        return "hedge" if self.hedge_mode else "one_way"

    @property
    def max_restarts_per_day(self) -> int:
        return self.max_automatic_restarts_per_day


def validate_runtime_policy(value: Mapping[str, Any]) -> RuntimePolicy:
    """Validate a mapping without adding or inferring any missing values."""

    return RuntimePolicy.from_mapping(value)


def load_runtime_policy(path: str | Path) -> RuntimePolicy:
    """Load and validate one UTF-8 JSON policy file strictly."""

    policy_path = Path(path)
    try:
        text = policy_path.read_text(encoding="utf-8")
    except OSError:
        raise
    document = _parse_json_document(text, str(policy_path))
    if not isinstance(document, Mapping):
        raise RuntimePolicyError(f"{policy_path}: root must be a JSON object")
    return RuntimePolicy.from_mapping(document)


def canonical_policy_json(value: RuntimePolicy | Mapping[str, Any]) -> str:
    """Return the canonical JSON representation used for hashing."""

    policy = (
        value if isinstance(value, RuntimePolicy) else RuntimePolicy.from_mapping(value)
    )
    return json.dumps(
        policy.as_dict(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def policy_hash(value: RuntimePolicy | Mapping[str, Any]) -> str:
    """Return the lowercase SHA-256 hash of canonical policy JSON."""

    canonical = canonical_policy_json(value).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# Small aliases keep the module pleasant to use without creating alternate
# input schemas or weakening the strict loader.
load_policy = load_runtime_policy
validate_policy = validate_runtime_policy


__all__ = [
    "POLICY_SCHEMA_VERSION",
    "RuntimePolicy",
    "RuntimePolicyError",
    "canonical_policy_json",
    "load_policy",
    "load_runtime_policy",
    "policy_hash",
    "validate_policy",
    "validate_runtime_policy",
]
