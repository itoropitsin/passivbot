"""Explicit risk limits for the Bitget BTC research program.

The policy is deliberately small and dependency-free.  It is used by the
experiment verifier and configuration checks; trading decisions still belong
to the Rust order/risk core.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
from collections.abc import Sequence
from typing import Any, Mapping


@dataclass(frozen=True)
class CryptoRiskLimits:
    """The hard limits from the dated crypto-bot development plan."""

    sizing_balance_cap: float = 1_000.0
    max_drawdown: float = 0.20
    terminal_drawdown: float = 0.25
    target_total_exposure: float = 0.60
    max_total_exposure: float = 0.80
    target_used_margin: float = 0.25
    max_used_margin: float = 0.35
    new_entry_min_liquidation_distance: float = 0.30
    reduce_only_min_liquidation_distance: float = 0.20
    emergency_liquidation_distance: float = 0.15
    max_recovery_days: float = 90.0
    daily_loss_stop: float = 0.02
    max_active_grids: int = 2
    max_symbol_exposure: float = 0.50
    max_grid_levels: int = 8
    max_initial_entry_pct: float = 0.03
    max_double_down_factor: float = 1.25
    max_automatic_restarts_per_day: int = 1
    execution_slippage_median_bps: float = 2.0
    execution_slippage_p95_bps: float = 10.0
    execution_slippage_max_bps: float = 25.0

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool):
                raise TypeError(f"risk limit {field.name} must not be boolean")
            if isinstance(value, (int, float)) and not math.isfinite(float(value)):
                raise ValueError(f"risk limit {field.name} must be finite")
        if self.sizing_balance_cap <= 0.0:
            raise ValueError("sizing_balance_cap must be > 0")
        if not 0.0 < self.max_drawdown < self.terminal_drawdown:
            raise ValueError("max_drawdown must be > 0 and below terminal_drawdown")
        if self.max_total_exposure <= 0.0 or self.target_total_exposure > self.max_total_exposure:
            raise ValueError("target_total_exposure must be <= max_total_exposure")
        if self.max_used_margin <= 0.0 or self.target_used_margin > self.max_used_margin:
            raise ValueError("target_used_margin must be <= max_used_margin")
        if not (
            0.0 < self.emergency_liquidation_distance
            <= self.reduce_only_min_liquidation_distance
            <= self.new_entry_min_liquidation_distance
        ):
            raise ValueError("liquidation-distance limits must be ordered emergency <= reduce-only <= entry")
        if self.max_active_grids < 1:
            raise ValueError("max_active_grids must be >= 1")
        if self.max_symbol_exposure <= 0.0:
            raise ValueError("max_symbol_exposure must be > 0")
        if self.max_grid_levels < 1:
            raise ValueError("max_grid_levels must be >= 1")
        if not 0.0 < self.max_initial_entry_pct <= 0.03 or self.max_double_down_factor <= 0.0:
            raise ValueError("initial entry limit must be in (0, 0.03] and double-down limit must be positive")
        if self.max_automatic_restarts_per_day < 0:
            raise ValueError("max_automatic_restarts_per_day must be >= 0")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "CryptoRiskLimits":
        """Build limits from a mapping without accepting unknown keys silently."""

        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise TypeError("crypto portfolio risk limits must be a mapping")
        aliases = {
            "max_total_exposure_ratio": "max_total_exposure",
            "max_used_margin_ratio": "max_used_margin",
            "daily_loss_stop_ratio": "daily_loss_stop",
            "max_symbol_exposure_ratio": "max_symbol_exposure",
        }
        allowed = {field.name for field in fields(cls)} | set(aliases)
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(f"unknown crypto risk limit(s): {', '.join(unknown)}")
        values: dict[str, Any] = {}
        for key, value in raw.items():
            values[aliases.get(key, key)] = value
        return cls(**values)


def capped_sizing_balance(balance: float, cap: float = 1_000.0) -> float:
    """Return the balance used for sizing, capped but never grown by leverage."""

    value = float(balance)
    limit = float(cap)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("sizing balance must be finite and >= 0")
    if not math.isfinite(limit) or limit <= 0.0:
        raise ValueError("sizing balance cap must be finite and > 0")
    return min(value, limit)


def _margin_tier_rows(tiers: Any) -> list[Mapping[str, Any]]:
    """Flatten normalized or raw Bitget tier responses without defaults."""

    raw = tiers
    if isinstance(raw, Mapping):
        if isinstance(raw.get("data"), Sequence) and not isinstance(raw.get("data"), (str, bytes)):
            raw = raw["data"]
        elif isinstance(raw.get("tiers"), Sequence) and not isinstance(raw.get("tiers"), (str, bytes)):
            raw = raw["tiers"]
        elif "symbol" in raw:
            raw = [raw]
        else:
            expanded: list[Mapping[str, Any]] = []
            for symbol, values in raw.items():
                values = values if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) else [values]
                for value in values:
                    if isinstance(value, Mapping):
                        item = dict(value)
                        item.setdefault("symbol", symbol)
                        expanded.append(item)
            raw = expanded
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [value for value in raw if isinstance(value, Mapping)]


def _tier_symbol_key(value: Any) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


def select_maintenance_margin_rate(
    tiers: Any, *, symbol: str, notional: float
) -> tuple[float, str] | None:
    """Select one explicit tier using [min, max), last tier inclusive."""

    try:
        target_notional = float(notional)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(target_notional) or target_notional < 0.0:
        return None
    target_symbol = _tier_symbol_key(symbol)
    # Keep only complete, explicitly supplied rows. There is no safe default
    # maintenance rate when the exchange surface is missing or malformed.
    valid: list[tuple[float, float, float, str, int]] = []
    for index, row in enumerate(_margin_tier_rows(tiers)):
        row_symbol = row.get("symbol")
        if row_symbol not in (None, "") and _tier_symbol_key(row_symbol) != target_symbol:
            continue
        try:
            minimum = float(row.get("min_notional", row.get("minTierValue")))
            maximum = float(row.get("max_notional", row.get("maxTierValue")))
            maintenance = float(row.get("maintenance_margin_rate", row.get("mmr")))
        except (TypeError, ValueError):
            continue
        if (
            math.isfinite(minimum)
            and math.isfinite(maximum)
            and math.isfinite(maintenance)
            and minimum >= 0.0
            and maximum > minimum
            and 0.0 <= maintenance < 1.0
        ):
            valid.append((minimum, maximum, maintenance, str(row.get("tier", row.get("tier_id", index + 1))), index))
    valid.sort(key=lambda item: (item[0], item[1], item[3], item[4]))
    for index, (minimum, maximum, maintenance, tier_id, _source_index) in enumerate(valid):
        is_last = index == len(valid) - 1
        if target_notional >= minimum and (target_notional < maximum or (is_last and target_notional <= maximum)):
            return maintenance, tier_id
    return None


def drawdown_action(drawdown: float) -> str:
    """Map drawdown to the plan's HSL action, including exact boundaries."""

    value = float(drawdown)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("drawdown must be finite and >= 0")
    if value >= 0.25:
        return "terminal_stop_no_restart"
    if value >= 0.20:
        return "emergency_close"
    if value >= 0.15:
        return "close_only"
    if value >= 0.10:
        return "warning"
    return "normal"


def validate_grid_controls(
    *,
    max_levels: int | float,
    initial_entry_pct: float,
    double_down_factor: float,
    limits: CryptoRiskLimits | None = None,
) -> None:
    """Raise when a grid violates the hard crypto profile limits."""

    policy = limits or CryptoRiskLimits()
    if isinstance(max_levels, bool):
        raise TypeError("grid max_levels must be an integer")
    levels = int(max_levels)
    if float(max_levels) != float(levels):
        raise ValueError("grid max_levels must be an integer")
    if levels < 1 or levels > policy.max_grid_levels:
        raise ValueError(
            f"grid max_levels={levels} exceeds hard limit {policy.max_grid_levels}"
        )
    initial = float(initial_entry_pct)
    factor = float(double_down_factor)
    if not math.isfinite(initial) or initial < 0.0 or initial > policy.max_initial_entry_pct:
        raise ValueError(
            f"initial entry percentage must be in [0, {policy.max_initial_entry_pct}]"
        )
    if not math.isfinite(factor) or factor <= 0.0 or factor > policy.max_double_down_factor:
        raise ValueError(
            f"double-down factor must be in (0, {policy.max_double_down_factor}]"
        )


def validate_portfolio_snapshot(
    snapshot: Mapping[str, Any],
    *,
    limits: CryptoRiskLimits | None = None,
) -> list[dict[str, Any]]:
    """Return explicit risk violations for a live/backtest portfolio snapshot.

    Required values are intentionally not defaulted.  A missing risk input is a
    blocking violation because a missing number cannot safely authorize an entry.
    """

    policy = limits or CryptoRiskLimits()
    required = (
        "total_exposure_ratio",
        "used_margin_ratio",
        "min_liquidation_distance",
        "active_grids",
        "daily_loss_ratio",
        "reconciliation_mismatches",
    )
    violations: list[dict[str, Any]] = []
    for key in required:
        if key not in snapshot:
            violations.append({"id": f"risk_input.{key}", "status": "blocked", "reason": "missing"})
    if violations:
        return violations

    def number(key: str) -> float:
        value = float(snapshot[key])
        if not math.isfinite(value):
            raise ValueError(f"portfolio risk input {key} must be finite")
        return value

    exposure = number("total_exposure_ratio")
    margin = number("used_margin_ratio")
    liq = number("min_liquidation_distance")
    grids = int(snapshot["active_grids"])
    daily_loss = number("daily_loss_ratio")
    mismatches = int(snapshot["reconciliation_mismatches"])
    checks = (
        ("total_exposure", exposure <= policy.max_total_exposure, exposure, policy.max_total_exposure, "<="),
        ("used_margin", margin <= policy.max_used_margin, margin, policy.max_used_margin, "<="),
        ("active_grids", grids <= policy.max_active_grids, grids, policy.max_active_grids, "<="),
        ("daily_loss", daily_loss < policy.daily_loss_stop, daily_loss, policy.daily_loss_stop, "<"),
        ("reconciliation", mismatches == 0, mismatches, 0, "=="),
    )
    for key, passed, actual, limit, operator in checks:
        if not passed:
            violations.append(
                {"id": f"risk.{key}", "status": "failed", "actual": actual, "limit": limit, "operator": operator}
            )
    if liq < policy.emergency_liquidation_distance:
        violations.append(
            {"id": "risk.liquidation_distance", "status": "failed", "action": "emergency_close", "actual": liq, "limit": policy.emergency_liquidation_distance}
        )
    elif liq < policy.reduce_only_min_liquidation_distance:
        violations.append(
            {"id": "risk.liquidation_distance", "status": "failed", "action": "reduce_only", "actual": liq, "limit": policy.reduce_only_min_liquidation_distance}
        )
    elif liq < policy.new_entry_min_liquidation_distance:
        violations.append(
            {"id": "risk.liquidation_distance", "status": "failed", "action": "block_new_entries", "actual": liq, "limit": policy.new_entry_min_liquidation_distance}
        )
    return violations
