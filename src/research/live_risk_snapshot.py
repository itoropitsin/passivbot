"""Export the decision made by the existing Python live risk gate.

This module does not decide whether an order is safe.  The existing
``Passivbot._apply_crypto_portfolio_risk_limits`` remains the source of that
decision.  The module only turns the same state and the same input/output
orders into the strict parity evidence format.  Missing state is reported as
blocked rather than filled with a convenient default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

from research.risk_parity import make_decision, normalize_snapshot, snapshot_hash


class LiveRiskSnapshotError(ValueError):
    """Raised when a required live risk surface is unavailable or invalid."""


def _finite(value: Any, *, field: str, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LiveRiskSnapshotError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise LiveRiskSnapshotError(f"{field} must be finite and positive")
    return result


def _stable_decimal_text(value: float) -> str:
    """Use the same fixed-scale spelling as the Rust risk boundary."""

    text = format(abs(float(value)), ".15f").rstrip("0").rstrip(".")
    return text or "0"


def stable_order_id_v1(
    *,
    symbol: str,
    position_side: str,
    order_side: str,
    order_type: str,
    execution_type: str,
    qty: float,
    price: float,
    reduce_only: bool,
    panic_close: bool,
    ordinal: int,
) -> str:
    """Build an exchange-independent identity shared by live and tester.

    The occurrence number only disambiguates otherwise identical orders.  The
    meaningful order semantics are part of the identity so a buy and a reduce
    only sell cannot accidentally share an ID.
    """

    execution = "market" if str(execution_type).lower() == "market" else "limit"
    return ":".join(
        (
            "risk-order-v1",
            str(symbol),
            str(position_side).lower(),
            str(order_side).lower(),
            str(order_type).lower(),
            execution,
            f"ro={int(bool(reduce_only))}",
            f"panic={int(bool(panic_close))}",
            _stable_decimal_text(qty),
            _stable_decimal_text(price),
            str(int(ordinal)),
        )
    )


def _order_parts(symbol: str, order: Any, ordinal: int) -> dict[str, Any]:
    if isinstance(order, Mapping):
        raw_type = order.get("pb_order_type") or order.get("order_type")
        raw_qty = order.get("qty")
        raw_price = order.get("price")
        execution_type = order.get("type") or order.get("execution_type") or "limit"
        explicit_side = order.get("position_side") or order.get("pside")
        reduce_only = bool(order.get("reduce_only", False))
    elif isinstance(order, Sequence) and not isinstance(order, (str, bytes)) and len(order) >= 3:
        raw_qty, raw_price, raw_type = order[0], order[1], order[2]
        execution_type = order[4] if len(order) >= 5 else "limit"
        explicit_side = None
        reduce_only = False
    else:
        raise LiveRiskSnapshotError("order has unsupported shape")
    order_type = str(raw_type or "").strip().lower()
    if not order_type:
        raise LiveRiskSnapshotError("order type is missing")
    side = str(explicit_side or "").strip().lower()
    if side not in {"long", "short"}:
        if "short" in order_type:
            side = "short"
        elif "long" in order_type:
            side = "long"
        else:
            raise LiveRiskSnapshotError(f"cannot determine position side for {order_type}")
    is_close = reduce_only or "close" in order_type
    if is_close:
        reduce_only = True
    order_side = "sell" if (side == "long" and is_close) or (side == "short" and not is_close) else "buy"
    qty = None if raw_qty is None else _finite(raw_qty, field="order.qty")
    price = None if raw_price is None else _finite(raw_price, field="order.price", positive=True)
    if qty is None or price is None:
        if not (is_close and "panic" in order_type):
            raise LiveRiskSnapshotError("non-panic order requires qty and price")
        qty = 0.0
        price = 0.0
    stable_id = stable_order_id_v1(
        symbol=symbol,
        position_side=side,
        order_type=order_type,
        execution_type=str(execution_type),
        qty=qty,
        price=price,
        order_side=order_side,
        reduce_only=reduce_only,
        panic_close=bool(is_close and any(token in order_type for token in ("panic", "emergency"))),
        ordinal=ordinal,
    )
    return {
        "order_identity": {
            "order_id": stable_id,
            "symbol": symbol,
            "position_side": side,
            "order_side": order_side,
            "strategy_order_type": order_type,
            "execution_type": "market" if str(execution_type).lower() == "market" else "limit",
            "qty": abs(qty),
            "price": price,
            "reduce_only": reduce_only,
            "panic_close": bool(is_close and any(token in order_type for token in ("panic", "emergency"))),
        },
        "order_type": "market" if str(execution_type).lower() == "market" else "limit",
        "strategy_order_type": order_type,
        "execution_type": "market" if str(execution_type).lower() == "market" else "limit",
        "qty": abs(qty),
        "price": price,
        "reduce_only": reduce_only,
        "panic_close": bool(is_close and any(token in order_type for token in ("panic", "emergency"))),
    }


def _order_semantic_key(parts: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the stable identity fields used to count identical orders."""

    identity = parts["order_identity"]
    return (
        identity["symbol"],
        identity["position_side"],
        identity["order_side"],
        identity["strategy_order_type"],
        identity["execution_type"],
        bool(identity["reduce_only"]),
        bool(identity["panic_close"]),
        _stable_decimal_text(identity["qty"]),
        _stable_decimal_text(identity["price"]),
    )


def _fetched_position(bot: Any, symbol: str, side: str) -> Mapping[str, Any] | None:
    for raw in getattr(bot, "fetched_positions", []) or []:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("symbol")) != symbol:
            continue
        raw_side = raw.get("position_side") or raw.get("side") or raw.get("pside")
        if str(raw_side).lower() == side:
            return raw
    return None


def _build_positions(bot: Any, last_prices: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw_positions = getattr(bot, "positions", None)
    if not isinstance(raw_positions, Mapping):
        raise LiveRiskSnapshotError("live positions surface is missing")
    multipliers = getattr(bot, "c_mults", {}) or {}
    surfaces = getattr(bot, "_crypto_market_surfaces", {}) or {}
    surface_required = bool(getattr(bot, "_crypto_market_surface_required", False))
    buckets: dict[str, list[dict[str, Any]]] = {"long": [], "short": []}
    for symbol, side_map in raw_positions.items():
        if not isinstance(side_map, Mapping):
            raise LiveRiskSnapshotError("live positions surface has invalid side map")
        for side in ("long", "short"):
            position = side_map.get(side)
            if not isinstance(position, Mapping):
                continue
            size = _finite(position.get("size", 0.0), field=f"positions.{symbol}.{side}.size")
            if size == 0.0:
                continue
            mark = _finite(last_prices.get(symbol), field=f"mark_prices.{symbol}", positive=True)
            if surface_required:
                surface = surfaces.get(str(symbol)) if isinstance(surfaces, Mapping) else None
                if not isinstance(surface, Mapping):
                    raise LiveRiskSnapshotError(
                        f"market surface is missing for positions.{symbol}"
                    )
                multiplier_value = surface.get("contract_size")
            else:
                multiplier_value = multipliers.get(symbol, 1.0)
            multiplier = _finite(
                multiplier_value,
                field=f"c_mults.{symbol}",
                positive=True,
            )
            record: dict[str, Any] = {
                "symbol": str(symbol),
                "position_side": side,
                "qty": abs(size),
                "notional": abs(size) * mark * multiplier,
                "entry_price": _finite(position.get("price"), field=f"positions.{symbol}.{side}.price", positive=True),
                "mark_price": mark,
            }
            fetched = _fetched_position(bot, str(symbol), side)
            if fetched is not None:
                fetched_mark = fetched.get("markPrice", fetched.get("mark_price"))
                liquidation = fetched.get("liquidationPrice", fetched.get("liquidation_price"))
                if fetched_mark not in (None, ""):
                    # The caller supplies the canonical public market surface.
                    # Validate the exchange position payload too, but do not
                    # silently replace the surface mark with an older value
                    # captured during a position refresh.
                    _finite(fetched_mark, field="position.mark_price", positive=True)
                if liquidation not in (None, ""):
                    record["liquidation_price"] = _finite(
                        liquidation, field="position.liquidation_price", positive=True
                    )
            record["notional"] = abs(size) * record["mark_price"] * _finite(
                multiplier,
                field=f"c_mults.{symbol}",
                positive=True,
            )
            buckets[side].append(record)
    return buckets


def _build_pending_orders(bot: Any) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    semantic_occurrences: dict[tuple[Any, ...], int] = {}
    open_orders = getattr(bot, "open_orders", {}) or {}
    if not isinstance(open_orders, Mapping):
        raise LiveRiskSnapshotError("live open orders surface has invalid shape")
    for symbol, orders in sorted(open_orders.items(), key=lambda item: str(item[0])):
        if not isinstance(orders, Sequence) or isinstance(orders, (str, bytes)):
            raise LiveRiskSnapshotError("live open orders surface has invalid order list")
        for order in orders:
            parts = _order_parts(str(symbol), order, 0)
            semantic_key = _order_semantic_key(parts)
            ordinal = semantic_occurrences.get(semantic_key, 0)
            semantic_occurrences[semantic_key] = ordinal + 1
            if ordinal:
                parts = _order_parts(str(symbol), order, ordinal)
            pending.append(
                {
                    **parts["order_identity"],
                    "order_id": parts["order_identity"]["order_id"],
                    "position_side": parts["order_identity"]["position_side"],
                    "order_side": parts["order_identity"]["order_side"],
                    "order_type": parts["strategy_order_type"],
                    "execution_type": parts["execution_type"],
                    "price": parts["price"],
                    "qty": parts["qty"],
                    "reduce_only": parts["reduce_only"],
                    "panic_close": parts["panic_close"],
                }
            )
    return pending


def _live_hsl_state(bot: Any) -> Mapping[str, Any]:
    """Read the production HSL runtime without inventing a state value."""

    if hasattr(bot, "_crypto_hsl_state"):
        explicit = getattr(bot, "_crypto_hsl_state")
        if explicit is None:
            raise LiveRiskSnapshotError("hsl state surface is missing")
        if not isinstance(explicit, Mapping):
            raise LiveRiskSnapshotError("hsl state surface has invalid shape")
        return explicit

    state_reader = getattr(bot, "_hsl_state", None)
    if not callable(state_reader):
        raise LiveRiskSnapshotError("hsl state surface is missing")
    configured = getattr(bot, "hsl", {})
    result: dict[str, dict[str, str]] = {}
    for side in ("long", "short"):
        try:
            raw = state_reader(side)
        except Exception as exc:  # pragma: no cover - depends on bot runtime initialization
            raise LiveRiskSnapshotError(f"hsl.{side} state surface is unavailable") from exc
        if not isinstance(raw, Mapping):
            raise LiveRiskSnapshotError(f"hsl.{side} state surface is missing")
        config = configured.get(side) if isinstance(configured, Mapping) else None
        if isinstance(config, Mapping) and not bool(config.get("enabled", False)):
            state = "disabled"
        elif bool(raw.get("no_restart_latched")):
            state = "terminal"
        elif bool(raw.get("halted")):
            state = "red"
        else:
            runtime = raw.get("runtime")
            tier_reader = getattr(runtime, "tier", None)
            if not callable(tier_reader):
                raise LiveRiskSnapshotError(f"hsl.{side}.runtime tier surface is missing")
            state = str(tier_reader()).strip().lower()
            if not state or state == "unknown":
                raise LiveRiskSnapshotError(f"hsl.{side}.runtime tier is unavailable")
        result[side] = {"state": state}
    return result


def build_live_risk_snapshot(
    bot: Any,
    last_prices: Mapping[str, Any],
    *,
    as_of_ms: int,
    margin_tiers: Any = None,
) -> dict[str, Any]:
    """Build a strict snapshot from actual live state, without defaults."""

    equity = _finite(
        bot.get_raw_balance() if callable(getattr(bot, "get_raw_balance", None)) else getattr(bot, "balance_raw", None),
        field="balance.equity",
        positive=True,
    )
    tiers = margin_tiers
    if tiers is None:
        tiers = getattr(bot, "_crypto_margin_tiers", None)
    if tiers is None:
        raise LiveRiskSnapshotError("margin_tiers surface is missing")
    daily_loss = getattr(bot, "_crypto_daily_loss_ratio", None)
    if daily_loss is None:
        raise LiveRiskSnapshotError("daily_loss surface is missing")
    hsl_state = _live_hsl_state(bot)
    hsl = {}
    for side in ("long", "short"):
        value = hsl_state.get(side)
        if isinstance(value, Mapping):
            state = value.get("state")
        else:
            state = value
        if not isinstance(state, str) or not state.strip():
            raise LiveRiskSnapshotError(f"hsl.{side}.state surface is missing")
        hsl[side] = {"state": state.strip().lower()}
    prices = {str(symbol): _finite(price, field=f"mark_prices.{symbol}", positive=True) for symbol, price in last_prices.items()}
    snapshot = {
        "schema_version": "risk-snapshot-v1",
        "as_of_ms": int(as_of_ms),
        "balance": {"available": equity, "equity": equity},
        "positions": _build_positions(bot, prices),
        "pending_orders": _build_pending_orders(bot),
        "mark_prices": prices,
        "margin_tiers": tiers,
        "leverage": {
            "long": _finite(getattr(bot, "config", {}).get("live", {}).get("leverage"), field="leverage.long", positive=True),
            "short": _finite(getattr(bot, "config", {}).get("live", {}).get("leverage"), field="leverage.short", positive=True),
        },
        "daily_loss": {"ratio": _finite(daily_loss, field="daily_loss.ratio")},
        "hsl": hsl,
    }
    try:
        return normalize_snapshot(snapshot)
    except Exception as exc:
        # The canonicalizer has a stricter exception type than this module's
        # public live-snapshot contract. Convert it so an unavailable surface
        # blocks evidence export instead of escaping from the trading loop.
        if isinstance(exc, LiveRiskSnapshotError):
            raise
        raise LiveRiskSnapshotError(str(exc)) from exc


def export_live_gate_decisions(
    bot: Any,
    original_orders: Mapping[str, Sequence[Any]],
    filtered_orders: Mapping[str, Sequence[Any]],
    last_prices: Mapping[str, Any],
    *,
    as_of_ms: int,
    margin_tiers: Any = None,
) -> dict[str, Any]:
    """Export actual gate outcomes; never alter the returned order set."""

    try:
        snapshot = build_live_risk_snapshot(
            bot, last_prices, as_of_ms=as_of_ms, margin_tiers=margin_tiers
        )
    except LiveRiskSnapshotError as exc:
        return {
            "status": "blocked",
            "reason": str(exc),
            "snapshot": None,
            "decisions": [],
            "candidate_admission": False,
            "trading_operations_count": 0,
        }
    digest = snapshot_hash(snapshot)
    decisions: list[dict[str, Any]] = []
    semantic_occurrences: dict[tuple[Any, ...], int] = {}
    for symbol, orders in sorted(original_orders.items(), key=lambda item: str(item[0])):
        kept = {id(order) for order in filtered_orders.get(symbol, ())}
        for order in orders:
            try:
                request = _order_parts(str(symbol), order, 0)
                semantic_key = _order_semantic_key(request)
                ordinal = semantic_occurrences.get(semantic_key, 0)
                semantic_occurrences[semantic_key] = ordinal + 1
                if ordinal:
                    request = _order_parts(str(symbol), order, ordinal)
                decision = "allow" if id(order) in kept else "deny"
                reason = "portfolio_risk_gate_allow" if decision == "allow" else "portfolio_risk_gate_deny"
                multipliers = getattr(bot, "c_mults", {}) or {}
                multiplier = _finite(
                    multipliers.get(symbol, 1.0),
                    field=f"c_mults.{symbol}",
                    positive=True,
                )
                requested_notional = request["qty"] * request["price"] * multiplier
                decisions.append(
                    make_decision(
                        decision=decision,
                        reason=reason,
                        side=request["order_identity"]["position_side"],
                        order_identity=request["order_identity"],
                        risk_metrics={
                            "requested_notional": requested_notional,
                            "daily_loss_ratio": snapshot["daily_loss"]["ratio"],
                            "hsl_state": snapshot["hsl"][request["order_identity"]["position_side"]]["state"],
                        },
                        snapshot_hash_value=digest,
                        engine="python-live-gate",
                    )
                )
            except (LiveRiskSnapshotError, TypeError, ValueError) as exc:
                return {
                    "status": "blocked",
                    "reason": f"cannot export order decision: {exc}",
                    "snapshot": snapshot,
                    "decisions": decisions,
                    "candidate_admission": False,
                    "trading_operations_count": 0,
                }
    return {
        "status": "passed",
        "reason": "actual Python live risk gate decisions exported",
        "snapshot": snapshot,
        "decisions": decisions,
        "candidate_admission": False,
        "trading_operations_count": 0,
    }


__all__ = [
    "LiveRiskSnapshotError",
    "build_live_risk_snapshot",
    "export_live_gate_decisions",
    "stable_order_id_v1",
]
