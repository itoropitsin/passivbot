"""An isolated, read-only contract for live/tester crypto-risk parity.

This module deliberately does not import either trading engine.  It defines the
small JSON boundary that both engines must eventually implement:

1. The Python live adapter and the Rust tester adapter each export the same
   ``risk-snapshot-v1`` object.
2. Each adapter evaluates its order and exports a ``risk-decision-v1`` object.
3. A harness calls :func:`compare_decisions` with both objects.  A different
   snapshot hash, decision, reason, side, order identity, or risk metric
   produces a deny result.

The second engine adapter is intentionally not connected yet.  The diagnostic
returned by :func:`parity_diagnostic` and included in every comparison keeps
that fact visible.  This file contains no exchange calls, order submission,
account mutation, or local trading state.

Numbers in the canonical representation are decimal strings.  That is an
intentional boundary choice: ``1``, ``1.0`` and ``1e0`` have one representation,
and NaN or infinity cannot enter a snapshot or decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from decimal import ROUND_HALF_UP
import hashlib
import json
import math
import re
from typing import Any


SNAPSHOT_SCHEMA = "risk-snapshot-v1"
DECISION_SCHEMA = "risk-decision-v1"
COMPARISON_SCHEMA = "risk-parity-comparison-v1"
DIAGNOSTIC_SCHEMA = "risk-parity-diagnostic-v1"
SNAPSHOT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SIDES = frozenset({"long", "short"})
ORDER_SIDES = frozenset({"buy", "sell"})
ORDER_TYPES = frozenset({"limit", "market"})


class RiskParityError(ValueError):
    """Base error for malformed parity-boundary data."""


class SnapshotValidationError(RiskParityError):
    """Raised when a required snapshot field is absent or unsafe."""


class DecisionValidationError(RiskParityError):
    """Raised when an engine decision cannot be compared safely."""


def _decimal_text(value: Any, *, field: str) -> str:
    """Return one finite, non-exponential decimal spelling for ``value``."""

    if isinstance(value, bool):
        raise RiskParityError(f"{field} must be numeric, not boolean")
    if isinstance(value, float) and not math.isfinite(value):
        raise RiskParityError(f"{field} must be finite")
    if isinstance(value, (int, float, Decimal, str)):
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise RiskParityError(f"{field} must be a decimal number") from exc
    else:
        raise RiskParityError(f"{field} must be a decimal number")
    if not number.is_finite():
        raise RiskParityError(f"{field} must be finite")
    if number == 0:
        return "0"
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"-0", "-0.0", ""}:
        return "0"
    return text


def _decimal(value: Any, *, field: str) -> Decimal:
    return Decimal(_decimal_text(value, field=field))


def _integer_text(value: Any, *, field: str) -> str:
    number = _decimal(value, field=field)
    if number != number.to_integral_value():
        raise RiskParityError(f"{field} must be an integer")
    if number < 0:
        raise RiskParityError(f"{field} must be non-negative")
    return str(int(number))


def _risk_metric_text(value: Decimal, *, field: str) -> str:
    """Use a bounded decimal scale at the cross-language decision boundary."""

    rounded = value.quantize(Decimal("0.000000000000001"), rounding=ROUND_HALF_UP)
    return _decimal_text(rounded, field=field)


def _is_number_key(key: str | None) -> bool:
    if key is None:
        return False
    lowered = key.lower()
    if lowered.endswith("_id") or lowered in {"id", "tier", "state", "status"}:
        return False
    exact = {
        "amount",
        "available",
        "balance",
        "buffer",
        "drawdown",
        "equity",
        "leverage",
        "margin",
        "mark",
        "notional",
        "price",
        "qty",
        "quantity",
        "ratio",
        "rate",
        "size",
        "value",
    }
    if lowered in exact:
        return True
    return lowered.endswith(
        (
            "_amount",
            "_bps",
            "_buffer",
            "_distance",
            "_drawdown",
            "_leverage",
            "_loss",
            "_margin",
            "_notional",
            "_price",
            "_qty",
            "_quantity",
            "_rate",
            "_ratio",
            "_size",
            "_timestamp_ms",
            "_time_ms",
        )
    )


def _normalise_generic(value: Any, *, key: str | None = None) -> Any:
    """Normalise nested JSON values without accepting non-JSON objects."""

    if isinstance(value, Mapping):
        normalised: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise RiskParityError("JSON object keys must be strings")
            normalised[raw_key] = _normalise_generic(raw_value, key=raw_key)
        return dict(sorted(normalised.items()))
    if isinstance(value, (list, tuple)):
        return [_normalise_generic(item, key=key) for item in value]
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return _decimal_text(value, field=key or "value")
    if isinstance(value, str) and _is_number_key(key):
        try:
            return _decimal_text(value, field=key or "value")
        except RiskParityError:
            # A textual field with a numeric-looking name is still invalid when
            # it is part of a required numeric contract.  Raising here avoids a
            # silent string fallback in risk-critical data.
            raise
    if value is None or isinstance(value, (str, bool)):
        return value
    raise RiskParityError(f"unsupported JSON value type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize JSON-compatible data deterministically."""

    return json.dumps(
        _normalise_generic(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _require_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotValidationError(f"{field} must be an object")
    return value


def _first_present(raw: Mapping[str, Any], names: Sequence[str], *, field: str) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    raise SnapshotValidationError(f"missing required snapshot field: {field}")


def _normalise_balance(raw: Any) -> dict[str, Any]:
    if isinstance(raw, (int, float, Decimal, str)) and not isinstance(raw, bool):
        equity = _decimal_text(raw, field="balance")
        return {"available": equity, "equity": equity}
    value = _require_mapping(raw, field="balance")
    out = _normalise_generic(value)
    if "equity" not in out and "total" in out:
        out["equity"] = out.pop("total")
    if "available" not in out and "free" in out:
        out["available"] = out.pop("free")
    for key in ("equity", "available"):
        if key not in out:
            raise SnapshotValidationError(f"balance.{key} is required")
        out[key] = _decimal_text(out[key], field=f"balance.{key}")
    if _decimal(out["equity"], field="balance.equity") <= 0:
        raise SnapshotValidationError("balance.equity must be greater than zero")
    return out


def _side_value(raw: Mapping[str, Any], *, forced_side: str | None, field: str) -> str:
    candidate = raw.get("position_side")
    if candidate is None:
        candidate = raw.get("pside")
    if candidate is None and raw.get("side") in SIDES:
        candidate = raw["side"]
    if candidate is None:
        candidate = forced_side
    if candidate not in SIDES:
        raise SnapshotValidationError(f"{field} must be long or short")
    if forced_side is not None and candidate != forced_side:
        raise SnapshotValidationError(f"{field} conflicts with its long/short bucket")
    return str(candidate)


def _expand_records(raw: Any, *, field: str, forced_side: str | None = None) -> list[dict[str, Any]]:
    """Expand either list records or ``{symbol: record}`` adapter output."""

    if isinstance(raw, Mapping):
        if "symbol" in raw:
            records: list[Any] = [raw]
        else:
            records = []
            for symbol, record in raw.items():
                if isinstance(record, Mapping):
                    item = dict(record)
                    item.setdefault("symbol", symbol)
                    records.append(item)
                elif isinstance(record, Sequence) and not isinstance(record, (str, bytes)):
                    for nested in record:
                        if not isinstance(nested, Mapping):
                            raise SnapshotValidationError(f"{field} records must be objects")
                        item = dict(nested)
                        item.setdefault("symbol", symbol)
                        records.append(item)
                else:
                    raise SnapshotValidationError(f"{field} records must be objects or lists")
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        records = list(raw)
    else:
        raise SnapshotValidationError(f"{field} must be a list or object")

    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise SnapshotValidationError(f"{field}[{index}] must be an object")
        item = dict(record)
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise SnapshotValidationError(f"{field}[{index}].symbol is required")
        item["symbol"] = symbol.strip()
        if forced_side is not None:
            item["position_side"] = _side_value(item, forced_side=forced_side, field=f"{field}[{index}].position_side")
            item.pop("side", None)
            item.pop("pside", None)
        result.append(_normalise_generic(item))
    return result


def _normalise_positions(raw: Any) -> dict[str, list[dict[str, Any]]]:
    value = _require_mapping(raw, field="positions")
    if set(value) - SIDES:
        # A list-shaped adapter can be passed directly; the object form is the
        # canonical form and must contain only explicit side buckets.
        if "records" in value and len(value) == 1:
            records = _expand_records(value["records"], field="positions")
            buckets = {"long": [], "short": []}
            for record in records:
                side = _side_value(record, forced_side=None, field="positions.position_side")
                record["position_side"] = side
                record.pop("side", None)
                buckets[side].append(record)
        else:
            raise SnapshotValidationError("positions must contain only long and short buckets")
    else:
        buckets = {
            side: _expand_records(value.get(side, []), field=f"positions.{side}", forced_side=side)
            for side in ("long", "short")
        }
    seen: set[tuple[str, str]] = set()
    for side, records in buckets.items():
        records.sort(key=lambda item: (item["symbol"], canonical_json(item)))
        for record in records:
            identity = (record["symbol"], side)
            if identity in seen:
                raise SnapshotValidationError(f"duplicate position: {side} {record['symbol']}")
            seen.add(identity)
    return buckets


def _normalise_pending_orders(raw: Any) -> list[dict[str, Any]]:
    records = _expand_records(raw, field="pending_orders")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        item = dict(record)
        order_id = item.get("order_id", item.get("id"))
        if order_id is None or not str(order_id).strip():
            raise SnapshotValidationError(f"pending_orders[{index}].order_id is required")
        item["order_id"] = str(order_id)
        item.pop("id", None)
        item["position_side"] = _side_value(
            item, forced_side=None, field=f"pending_orders[{index}].position_side"
        )
        item.pop("side", None)
        item.pop("pside", None)
        order_side = item.get("order_side", item.get("order_direction"))
        if order_side not in ORDER_SIDES:
            raise SnapshotValidationError(f"pending_orders[{index}].order_side must be buy or sell")
        item["order_side"] = str(order_side)
        item.pop("order_direction", None)
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise SnapshotValidationError(f"pending_orders[{index}].symbol is required")
        item["symbol"] = symbol.strip()
        normalised = _normalise_generic(item)
        if normalised["order_id"] in seen:
            raise SnapshotValidationError(f"duplicate pending order: {normalised['order_id']}")
        seen.add(normalised["order_id"])
        result.append(normalised)
    return sorted(result, key=lambda item: (item["order_id"], item["symbol"]))


def _normalise_mark_prices(raw: Any) -> dict[str, str]:
    if isinstance(raw, Mapping):
        result: dict[str, str] = {}
        for symbol, price in raw.items():
            if not isinstance(symbol, str) or not symbol.strip():
                raise SnapshotValidationError("mark_prices symbols must be non-empty strings")
            if isinstance(price, Mapping):
                price = price.get("mark_price", price.get("mark"))
            canonical_price = _decimal_text(price, field=f"mark_prices.{symbol}")
            if _decimal(canonical_price, field=f"mark_prices.{symbol}") <= 0:
                raise SnapshotValidationError(f"mark_prices.{symbol} must be greater than zero")
            result[symbol.strip()] = canonical_price
        if not result:
            raise SnapshotValidationError("mark_prices must not be empty")
        return dict(sorted(result.items()))
    records = _expand_records(raw, field="mark_prices")
    result = {}
    for index, record in enumerate(records):
        price = record.get("mark_price", record.get("mark", record.get("price")))
        if price is None:
            raise SnapshotValidationError(f"mark_prices[{index}].mark_price is required")
        canonical_price = _decimal_text(price, field=f"mark_prices[{index}].mark_price")
        if _decimal(canonical_price, field=f"mark_prices[{index}].mark_price") <= 0:
            raise SnapshotValidationError(f"mark_prices[{index}].mark_price must be greater than zero")
        result[record["symbol"]] = canonical_price
    if not result:
        raise SnapshotValidationError("mark_prices must not be empty")
    return dict(sorted(result.items()))


def _normalise_margin_tiers(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, Mapping) and "symbol" not in raw:
        records: list[dict[str, Any]] = []
        for symbol, tiers in raw.items():
            if isinstance(tiers, Mapping):
                tiers = [tiers]
            if not isinstance(tiers, Sequence) or isinstance(tiers, (str, bytes)):
                raise SnapshotValidationError("margin_tiers values must be lists or objects")
            for tier in tiers:
                if not isinstance(tier, Mapping):
                    raise SnapshotValidationError("margin_tiers records must be objects")
                item = dict(tier)
                item.setdefault("symbol", symbol)
                records.append(item)
    else:
        records = _expand_records(raw, field="margin_tiers")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records):
        symbol = record.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise SnapshotValidationError(f"margin_tiers[{index}].symbol is required")
        tier = record.get("tier", record.get("tier_id"))
        if tier is None or not str(tier).strip():
            raise SnapshotValidationError(f"margin_tiers[{index}].tier is required")
        item = dict(record)
        item["symbol"] = symbol.strip()
        item["tier"] = str(tier)
        item.pop("tier_id", None)
        normalised = _normalise_generic(item)
        identity = (normalised["symbol"], normalised["tier"])
        if identity in seen:
            raise SnapshotValidationError(f"duplicate margin tier: {identity[0]} {identity[1]}")
        seen.add(identity)
        result.append(normalised)
    if not result:
        raise SnapshotValidationError("margin_tiers must not be empty")
    return sorted(result, key=lambda item: (item["symbol"], item["tier"]))


def _normalise_leverage(raw: Any) -> dict[str, str]:
    if isinstance(raw, Mapping):
        if "long" not in raw or "short" not in raw:
            raise SnapshotValidationError("leverage.long and leverage.short are required")
        values = {side: raw[side] for side in ("long", "short")}
    else:
        values = {"long": raw, "short": raw}
    result = {side: _decimal_text(value, field=f"leverage.{side}") for side, value in values.items()}
    if any(_decimal(value, field=f"leverage.{side}") <= 0 for side, value in result.items()):
        raise SnapshotValidationError("leverage must be greater than zero")
    return result


def _normalise_daily_loss(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        value = dict(raw)
        if "ratio" not in value:
            if "daily_loss_ratio" in value:
                value["ratio"] = value.pop("daily_loss_ratio")
            else:
                raise SnapshotValidationError("daily_loss.ratio is required")
        result = _normalise_generic(value)
    else:
        result = {"ratio": _decimal_text(raw, field="daily_loss.ratio")}
    ratio = _decimal(result["ratio"], field="daily_loss.ratio")
    if ratio < 0:
        raise SnapshotValidationError("daily_loss.ratio must be non-negative")
    result["ratio"] = _decimal_text(ratio, field="daily_loss.ratio")
    return result


def _normalise_hsl(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping) and set(raw).issubset(SIDES) and set(raw):
        result = {side: _normalise_generic(raw[side], key=side) for side in SIDES}
        for side in SIDES:
            if side not in result:
                result[side] = {"state": "unknown"}
        return dict(sorted(result.items()))
    if isinstance(raw, Mapping):
        common = _normalise_generic(raw)
    elif isinstance(raw, str) and raw.strip():
        common = {"state": raw.strip().lower()}
    else:
        raise SnapshotValidationError("hsl must be a side map or state object")
    return {"long": deepcopy(common), "short": deepcopy(common)}


def normalize_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return the strict canonical form of a live/tester risk snapshot."""

    raw = _require_mapping(snapshot, field="snapshot")
    allowed = {
        "schema_version",
        "as_of_ms",
        "timestamp_ms",
        "snapshot_ts_ms",
        "balance",
        "positions",
        "pending_orders",
        "mark_prices",
        "mark_price",
        "margin_tiers",
        "leverage",
        "daily_loss",
        "hsl",
        "metadata",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SnapshotValidationError(f"unknown snapshot field(s): {', '.join(unknown)}")
    if "schema_version" in raw and raw["schema_version"] not in {SNAPSHOT_SCHEMA}:
        raise SnapshotValidationError(
            f"unsupported snapshot schema_version: {raw['schema_version']!r}"
        )
    as_of_ms = _integer_text(
        _first_present(raw, ("as_of_ms", "timestamp_ms", "snapshot_ts_ms"), field="as_of_ms"),
        field="as_of_ms",
    )
    mark_raw = raw["mark_prices"] if "mark_prices" in raw else raw.get("mark_price")
    if mark_raw is None:
        raise SnapshotValidationError("missing required snapshot field: mark_prices")
    result: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA,
        "as_of_ms": as_of_ms,
        "balance": _normalise_balance(_first_present(raw, ("balance",), field="balance")),
        "positions": _normalise_positions(_first_present(raw, ("positions",), field="positions")),
        "pending_orders": _normalise_pending_orders(
            _first_present(raw, ("pending_orders",), field="pending_orders")
        ),
        "mark_prices": _normalise_mark_prices(mark_raw),
        "margin_tiers": _normalise_margin_tiers(
            _first_present(raw, ("margin_tiers",), field="margin_tiers")
        ),
        "leverage": _normalise_leverage(_first_present(raw, ("leverage",), field="leverage")),
        "daily_loss": _normalise_daily_loss(
            _first_present(raw, ("daily_loss",), field="daily_loss")
        ),
        "hsl": _normalise_hsl(_first_present(raw, ("hsl",), field="hsl")),
    }
    if "metadata" in raw:
        result["metadata"] = _normalise_generic(raw["metadata"])
    return result


def canonical_snapshot_json(snapshot: Mapping[str, Any]) -> str:
    """Serialize a risk snapshot after strict normalization."""

    return json.dumps(
        normalize_snapshot(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    """Return the SHA-256 of the canonical snapshot bytes."""

    return hashlib.sha256(canonical_snapshot_json(snapshot).encode("utf-8")).hexdigest()


def _normalise_order_identity(raw: Any, *, field: str = "order_identity") -> dict[str, Any]:
    value = _require_mapping(raw, field=field)
    item = dict(value)
    order_id = item.get("order_id", item.get("id"))
    if order_id is None or not str(order_id).strip():
        raise DecisionValidationError(f"{field}.order_id is required")
    symbol = item.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise DecisionValidationError(f"{field}.symbol is required")
    position_side = item.get("position_side", item.get("pside"))
    if position_side is None and item.get("side") in SIDES:
        position_side = item["side"]
    if position_side not in SIDES:
        raise DecisionValidationError(f"{field}.position_side must be long or short")
    order_side = item.get("order_side", item.get("order_direction"))
    if order_side is None and item.get("side") in ORDER_SIDES:
        order_side = item["side"]
    if order_side not in ORDER_SIDES:
        raise DecisionValidationError(f"{field}.order_side must be buy or sell")
    item["order_id"] = str(order_id)
    item["symbol"] = symbol.strip()
    item["position_side"] = str(position_side)
    item["order_side"] = str(order_side)
    item.pop("id", None)
    item.pop("pside", None)
    item.pop("order_direction", None)
    item.pop("side", None)
    if "client_order_id" in item and item["client_order_id"] is not None:
        item["client_order_id"] = str(item["client_order_id"])
    return _normalise_generic(item)


def _normalise_hash(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not SNAPSHOT_HASH_RE.fullmatch(value):
        raise DecisionValidationError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


@dataclass(frozen=True)
class RiskDecision:
    """A normalized allow/deny result emitted by one engine adapter."""

    decision: str
    reason: str
    side: str
    order_identity: dict[str, Any]
    risk_metrics: dict[str, Any]
    snapshot_hash: str
    engine: str = "unidentified"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RiskDecision":
        if not isinstance(value, Mapping):
            raise DecisionValidationError("decision must be an object")
        decision = value.get("decision")
        if decision not in {"allow", "deny"}:
            raise DecisionValidationError("decision must be allow or deny")
        reason = value.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise DecisionValidationError("decision.reason is required")
        side = value.get("side")
        if side not in SIDES:
            raise DecisionValidationError("decision.side must be long or short")
        identity = _normalise_order_identity(value.get("order_identity"))
        if identity["position_side"] != side:
            raise DecisionValidationError("decision.side conflicts with order_identity.position_side")
        metrics = value.get("risk_metrics")
        if not isinstance(metrics, Mapping) or not metrics:
            raise DecisionValidationError("decision.risk_metrics must be a non-empty object")
        normalised_metrics = _normalise_generic(metrics)
        digest = _normalise_hash(value.get("snapshot_hash"), field="decision.snapshot_hash")
        engine = value.get("engine", "unidentified")
        if not isinstance(engine, str) or not engine.strip():
            raise DecisionValidationError("decision.engine must be a non-empty string")
        return cls(
            decision=str(decision),
            reason=reason.strip(),
            side=str(side),
            order_identity=identity,
            risk_metrics=normalised_metrics,
            snapshot_hash=digest,
            engine=engine.strip(),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": DECISION_SCHEMA,
            "decision": self.decision,
            "reason": self.reason,
            "side": self.side,
            "order_identity": deepcopy(self.order_identity),
            "risk_metrics": deepcopy(self.risk_metrics),
            "snapshot_hash": self.snapshot_hash,
            "engine": self.engine,
        }


def make_decision(
    *,
    decision: str,
    reason: str,
    side: str,
    order_identity: Mapping[str, Any],
    risk_metrics: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None = None,
    snapshot_hash_value: str | None = None,
    engine: str = "unidentified",
) -> dict[str, Any]:
    """Create and validate one engine decision at the parity boundary."""

    if snapshot is not None and snapshot_hash_value is not None:
        expected = snapshot_hash(snapshot)
        if expected != snapshot_hash_value:
            raise DecisionValidationError("snapshot and snapshot_hash_value do not match")
    digest = snapshot_hash_value if snapshot_hash_value is not None else (
        snapshot_hash(snapshot) if snapshot is not None else None
    )
    if digest is None:
        raise DecisionValidationError("snapshot or snapshot_hash_value is required")
    return RiskDecision.from_mapping(
        {
            "decision": decision,
            "reason": reason,
            "side": side,
            "order_identity": order_identity,
            "risk_metrics": risk_metrics,
            "snapshot_hash": digest,
            "engine": engine,
        }
    ).to_mapping()


def normalize_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one adapter result without comparing it."""

    return RiskDecision.from_mapping(decision).to_mapping()


def parity_diagnostic() -> dict[str, Any]:
    """Return the explicit current wiring status of the parity contour."""

    return {
        "schema_version": DIAGNOSTIC_SCHEMA,
        "status": "adapter_pending",
        "risk_parity_reached": False,
        "candidate_admission": False,
        "second_engine_adapter_connected": False,
        "trading_operations_count": 0,
        "integration_point": {
            "snapshot": "Python live adapter and Rust tester adapter emit risk-snapshot-v1",
            "decision": "each adapter emits risk-decision-v1",
            "comparison": "a read-only harness calls compare_decisions before any order path",
            "external_operations": "none",
        },
    }


def _decision_comparison_payload(decision: RiskDecision) -> dict[str, Any]:
    payload = decision.to_mapping()
    payload.pop("engine", None)
    payload.pop("schema_version", None)
    return payload


def _fail_closed_result(
    reason: str,
    *,
    left: RiskDecision | None = None,
    right: RiskDecision | None = None,
    snapshot_digest: str | None = None,
    differences: Sequence[str] = (),
) -> dict[str, Any]:
    side = left.side if left is not None and (right is None or left.side == right.side) else "long"
    identity = deepcopy(left.order_identity) if left is not None else {
        "order_id": "parity-invalid",
        "symbol": "unknown",
        "position_side": side,
        "order_side": "buy",
    }
    metrics: dict[str, Any] = {
        "parity_mismatch": True,
        "left_decision": left.decision if left is not None else "invalid",
        "right_decision": right.decision if right is not None else "invalid",
    }
    if left is not None:
        metrics["left"] = deepcopy(left.risk_metrics)
    if right is not None:
        metrics["right"] = deepcopy(right.risk_metrics)
    return {
        "schema_version": COMPARISON_SCHEMA,
        "parity": "failed",
        "fail_closed": True,
        "decision": "deny",
        "reason": reason,
        "side": side,
        "order_identity": identity,
        "risk_metrics": metrics,
        "snapshot_hash": snapshot_digest,
        "differences": list(differences),
        "left_engine": left.engine if left is not None else None,
        "right_engine": right.engine if right is not None else None,
        "diagnostic": parity_diagnostic(),
    }


def compare_decisions(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare two engine decisions and deny on every unsafe difference."""

    left_decision: RiskDecision | None = None
    right_decision: RiskDecision | None = None
    try:
        left_decision = RiskDecision.from_mapping(left)
    except (DecisionValidationError, TypeError, ValueError) as exc:
        return _fail_closed_result(f"left decision is invalid: {exc}")
    try:
        right_decision = RiskDecision.from_mapping(right)
    except (DecisionValidationError, TypeError, ValueError) as exc:
        return _fail_closed_result(
            f"right decision is invalid: {exc}", left=left_decision
        )

    expected_digest: str | None = None
    if snapshot is not None:
        try:
            expected_digest = snapshot_hash(snapshot)
        except (SnapshotValidationError, RiskParityError, TypeError, ValueError) as exc:
            return _fail_closed_result(
                f"comparison snapshot is invalid: {exc}",
                left=left_decision,
                right=right_decision,
            )
    if left_decision.snapshot_hash != right_decision.snapshot_hash:
        return _fail_closed_result(
            "snapshot hash differs between engines",
            left=left_decision,
            right=right_decision,
            differences=("snapshot_hash",),
        )
    if expected_digest is not None and left_decision.snapshot_hash != expected_digest:
        return _fail_closed_result(
            "decision snapshot hash does not match supplied snapshot",
            left=left_decision,
            right=right_decision,
            snapshot_digest=expected_digest,
            differences=("snapshot_hash",),
        )

    left_payload = _decision_comparison_payload(left_decision)
    right_payload = _decision_comparison_payload(right_decision)
    differences = tuple(
        key for key in left_payload if canonical_json(left_payload[key]) != canonical_json(right_payload[key])
    )
    if differences:
        return _fail_closed_result(
            "engine decisions differ; parity fails closed",
            left=left_decision,
            right=right_decision,
            snapshot_digest=left_decision.snapshot_hash,
            differences=differences,
        )
    return {
        "schema_version": COMPARISON_SCHEMA,
        "parity": "passed",
        "fail_closed": False,
        "decision": left_decision.decision,
        "reason": "engine decisions match",
        "side": left_decision.side,
        "order_identity": deepcopy(left_decision.order_identity),
        "risk_metrics": deepcopy(left_decision.risk_metrics),
        "snapshot_hash": left_decision.snapshot_hash,
        "differences": [],
        "left_engine": left_decision.engine,
        "right_engine": right_decision.engine,
        "diagnostic": parity_diagnostic(),
    }


def _fixture_base() -> dict[str, Any]:
    return {
        "as_of_ms": 1_756_000_000_000,
        "balance": {"equity": 1_000.0, "available": 1_000},
        "positions": {"long": [], "short": []},
        "pending_orders": [],
        "mark_prices": {"BTC/USDT:USDT": 100.0},
        "margin_tiers": [
            {
                "symbol": "BTC/USDT:USDT",
                "tier": 1,
                "max_notional": 100_000.0,
                "maintenance_margin_rate": 0.005,
            }
        ],
        "leverage": {"long": 3.0, "short": 3},
        "daily_loss": {"ratio": 0.005, "amount": 5.0},
        "hsl": {
            "long": {"state": "normal", "drawdown": 0.02},
            "short": {"state": "normal", "drawdown": 0.02},
        },
    }


def fixture_case(name: str) -> dict[str, Any]:
    """Return one deterministic parity fixture and its expected outcome."""

    if name not in {"initial_entry", "max_notional", "liquidation_buffer", "panic_close", "unknown_position"}:
        raise KeyError(f"unknown risk parity fixture: {name}")
    snapshot = _fixture_base()
    request: dict[str, Any]
    expected: dict[str, Any]
    if name == "initial_entry":
        request = {
            "order_identity": {
                "order_id": "entry-001",
                "client_order_id": "parity-entry-001",
                "symbol": "BTC/USDT:USDT",
                "position_side": "long",
                "order_side": "buy",
            },
            "order_type": "limit",
            "qty": 0.2,
            "price": 100.0,
            "reduce_only": False,
            "panic_close": False,
        }
        expected = {"decision": "allow", "reason": "entry_within_risk_limits"}
    elif name == "max_notional":
        snapshot["positions"]["long"] = [
            {
                "symbol": "BTC/USDT:USDT",
                "qty": 7.9,
                "notional": 790.0,
                "entry_price": 100.0,
                "mark_price": 100.0,
                "liquidation_price": 50.0,
            }
        ]
        request = {
            "order_identity": {
                "order_id": "entry-maximum-001",
                "symbol": "BTC/USDT:USDT",
                "position_side": "long",
                "order_side": "buy",
            },
            "order_type": "limit",
            "qty": 0.2,
            "price": 100.0,
            "reduce_only": False,
            "panic_close": False,
        }
        expected = {"decision": "deny", "reason": "max_total_exposure_exceeded"}
    elif name == "liquidation_buffer":
        snapshot["positions"]["long"] = [
            {
                "symbol": "BTC/USDT:USDT",
                "qty": 0.5,
                "notional": 50.0,
                "entry_price": 100.0,
                "mark_price": 100.0,
                "liquidation_price": 72.0,
            }
        ]
        request = {
            "order_identity": {
                "order_id": "entry-buffer-001",
                "symbol": "BTC/USDT:USDT",
                "position_side": "long",
                "order_side": "buy",
            },
            "order_type": "limit",
            "qty": 0.1,
            "price": 100.0,
            "reduce_only": False,
            "panic_close": False,
        }
        expected = {"decision": "deny", "reason": "liquidation_buffer_below_entry_minimum"}
    elif name == "panic_close":
        snapshot["positions"]["long"] = [
            {
                "symbol": "BTC/USDT:USDT",
                "qty": 1.0,
                "notional": 100.0,
                "entry_price": 100.0,
                "mark_price": 100.0,
                "liquidation_price": 85.0,
            }
        ]
        request = {
            "order_identity": {
                "order_id": "panic-001",
                "symbol": "BTC/USDT:USDT",
                "position_side": "long",
                "order_side": "sell",
            },
            "order_type": "market",
            "reduce_only": True,
            "panic_close": True,
        }
        expected = {"decision": "allow", "reason": "panic_close_allowed"}
    else:
        request = {
            "order_identity": {
                "order_id": "unknown-close-001",
                "symbol": "BTC/USDT:USDT",
                "position_side": "short",
                "order_side": "buy",
            },
            "order_type": "limit",
            "qty": 0.1,
            "price": 100.0,
            "reduce_only": True,
            "panic_close": False,
        }
        expected = {"decision": "deny", "reason": "unknown_position"}
    return {"name": name, "snapshot": snapshot, "request": request, "expected": expected}


def _normalise_request(request: Mapping[str, Any]) -> tuple[dict[str, Any], str, Decimal, bool, bool]:
    if not isinstance(request, Mapping):
        raise DecisionValidationError("order request must be an object")
    identity = _normalise_order_identity(request.get("order_identity", request), field="order_identity")
    order_type = request.get("order_type")
    if order_type not in ORDER_TYPES:
        raise DecisionValidationError("order_type must be limit or market")
    reduce_only = request.get("reduce_only")
    panic_close = request.get("panic_close")
    if not isinstance(reduce_only, bool) or not isinstance(panic_close, bool):
        raise DecisionValidationError("reduce_only and panic_close must be booleans")
    notional_raw = request.get("notional")
    if notional_raw is not None:
        notional = abs(_decimal(notional_raw, field="order.notional"))
    else:
        qty_raw = request.get("qty")
        price_raw = request.get("price")
        if qty_raw is None or price_raw is None:
            if panic_close and reduce_only:
                notional = Decimal("0")
            else:
                raise DecisionValidationError("order qty and price, or notional, are required")
        else:
            notional = abs(_decimal(qty_raw, field="order.qty") * _decimal(price_raw, field="order.price"))
    if notional < 0:
        raise DecisionValidationError("order notional must be non-negative")
    return identity, str(order_type), notional, reduce_only, panic_close


def _position_notional(position: Mapping[str, Any], mark_prices: Mapping[str, str]) -> Decimal:
    if "notional" in position:
        return abs(_decimal(position["notional"], field="position.notional"))
    if "qty" not in position:
        raise SnapshotValidationError("position requires notional or qty")
    mark = position.get("mark_price", mark_prices.get(position["symbol"]))
    if mark is None:
        raise SnapshotValidationError(f"missing mark price for position {position['symbol']}")
    return abs(_decimal(position["qty"], field="position.qty")) * _decimal(mark, field="position.mark_price")


def _position_buffer(position: Mapping[str, Any], mark_prices: Mapping[str, str]) -> Decimal:
    if "liquidation_buffer" in position:
        return _decimal(position["liquidation_buffer"], field="position.liquidation_buffer")
    liquidation = position.get("liquidation_price")
    mark = position.get("mark_price", mark_prices.get(position["symbol"]))
    if liquidation is None or mark is None:
        raise SnapshotValidationError(f"missing liquidation buffer for position {position['symbol']}")
    mark_value = _decimal(mark, field="position.mark_price")
    liquidation_value = _decimal(liquidation, field="position.liquidation_price")
    if mark_value <= 0:
        raise SnapshotValidationError("position.mark_price must be greater than zero")
    if position["position_side"] == "long":
        return (mark_value - liquidation_value) / mark_value
    return (liquidation_value - mark_value) / mark_value


def _hsl_state(snapshot: Mapping[str, Any], side: str) -> str:
    value = snapshot["hsl"][side]
    if isinstance(value, Mapping):
        state = value.get("state")
    else:
        state = value
    if not isinstance(state, str) or not state.strip():
        raise SnapshotValidationError(f"hsl.{side}.state is required")
    return state.strip().lower()


def evaluate_order(
    snapshot: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    engine: str = "reference",
) -> dict[str, Any]:
    """Evaluate the small parity policy used by the deterministic fixtures.

    This is not a replacement for the Rust risk engine.  It is a deliberately
    narrow reference boundary used to make adapter parity observable before the
    real Rust and Python adapters are connected.
    """

    normalised = normalize_snapshot(snapshot)
    digest = snapshot_hash(normalised)
    identity, order_type, requested_notional, reduce_only, panic_close = _normalise_request(request)
    side = identity["position_side"]
    symbol = identity["symbol"]
    equity = _decimal(normalised["balance"]["equity"], field="balance.equity")
    leverage = _decimal(normalised["leverage"][side], field=f"leverage.{side}")
    positions = normalised["positions"][side]
    all_positions = normalised["positions"]["long"] + normalised["positions"]["short"]
    current_total = sum(
        (_position_notional(position, normalised["mark_prices"]) for position in all_positions),
        Decimal("0"),
    )
    buffers = [_position_buffer(position, normalised["mark_prices"]) for position in all_positions]
    min_buffer = min(buffers, default=Decimal("1"))
    matching_position = next(
        (position for position in positions if position["symbol"] == symbol), None
    )
    projected_total = current_total + requested_notional
    projected_exposure = projected_total / equity
    projected_margin = projected_total / (equity * leverage)
    daily_loss = _decimal(normalised["daily_loss"]["ratio"], field="daily_loss.ratio")
    hsl_state = _hsl_state(normalised, side)
    metrics = {
        "current_total_notional": _risk_metric_text(current_total, field="current_total_notional"),
        "requested_notional": _risk_metric_text(requested_notional, field="requested_notional"),
        "projected_total_notional": _risk_metric_text(projected_total, field="projected_total_notional"),
        "projected_exposure_ratio": _risk_metric_text(projected_exposure, field="projected_exposure_ratio"),
        "projected_used_margin_ratio": _risk_metric_text(projected_margin, field="projected_used_margin_ratio"),
        "min_liquidation_buffer": _risk_metric_text(min_buffer, field="min_liquidation_buffer"),
        "daily_loss_ratio": _risk_metric_text(daily_loss, field="daily_loss_ratio"),
        "hsl_state": hsl_state,
    }

    def result(decision: str, reason: str) -> dict[str, Any]:
        return make_decision(
            decision=decision,
            reason=reason,
            side=side,
            order_identity=identity,
            risk_metrics=metrics,
            snapshot_hash_value=digest,
            engine=engine,
        )

    if leverage > Decimal("3"):
        return result("deny", "leverage_above_roadmap_max")
    if reduce_only and matching_position is None:
        return result("deny", "unknown_position")
    if order_type == "market":
        if reduce_only and panic_close and matching_position is not None:
            return result("allow", "panic_close_allowed")
        return result("deny", "ordinary_market_order_not_allowed")
    if reduce_only:
        if min_buffer < Decimal("0.20"):
            return result("deny", "liquidation_buffer_below_reduce_only_minimum")
        return result("allow", "reduce_only_within_risk_limits")

    if daily_loss >= Decimal("0.02"):
        return result("deny", "daily_loss_stop_reached")
    if hsl_state in {"orange", "red", "close_only", "emergency_close", "terminal_stop_no_restart"}:
        return result("deny", "hsl_blocks_new_entry")
    if min_buffer < Decimal("0.30"):
        return result("deny", "liquidation_buffer_below_entry_minimum")
    if projected_exposure > Decimal("0.80"):
        return result("deny", "max_total_exposure_exceeded")
    if projected_margin > Decimal("0.35"):
        return result("deny", "max_used_margin_exceeded")
    if current_total == 0 and projected_exposure > Decimal("0.03"):
        return result("deny", "initial_entry_exceeds_limit")
    return result("allow", "entry_within_risk_limits")


def evaluate_fixture(name: str, *, engine: str = "reference") -> dict[str, Any]:
    """Evaluate one fixture without mutating or contacting an external system."""

    case = fixture_case(name)
    return evaluate_order(case["snapshot"], case["request"], engine=engine)


__all__ = [
    "COMPARISON_SCHEMA",
    "DECISION_SCHEMA",
    "DIAGNOSTIC_SCHEMA",
    "RiskDecision",
    "RiskParityError",
    "SnapshotValidationError",
    "DecisionValidationError",
    "SNAPSHOT_SCHEMA",
    "canonical_json",
    "canonical_snapshot_json",
    "compare_decisions",
    "evaluate_fixture",
    "evaluate_order",
    "fixture_case",
    "make_decision",
    "normalize_decision",
    "normalize_snapshot",
    "parity_diagnostic",
    "snapshot_hash",
]
