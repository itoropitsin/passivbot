from __future__ import annotations

import logging
import math
import os
import sys
from copy import deepcopy
from typing import Any, Iterable, Optional

import numpy as np
import passivbot_rust as pbr

from trailing_diagnostics import (
    build_trailing_close_diagnostic,
    build_trailing_entry_diagnostic,
    normalize_trailing_extrema,
)
from utils import utc_ms

try:
    import psutil  # type: ignore
except Exception:
    psutil = None

try:
    import resource  # type: ignore
except Exception:
    resource = None


def _get_process_rss_bytes() -> Optional[int]:
    """Return current process RSS in bytes or None if unavailable."""
    try:
        if psutil is not None:
            return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        pass
    if resource is not None:
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform.startswith("linux"):
                usage = int(usage) * 1024
            else:
                usage = int(usage)
            return int(usage)
        except Exception:
            pass
    return None


def _calc_monitor_pnl(position_side, entry_price, close_price, qty, c_mult):
    """Calculate trade PnL using the same Rust helpers as the live bot."""
    try:
        if isinstance(position_side, str):
            if position_side == "long":
                return pbr.calc_pnl_long(entry_price, close_price, qty, c_mult)
            return pbr.calc_pnl_short(entry_price, close_price, qty, c_mult)
        return pbr.calc_pnl_long(entry_price, close_price, qty, c_mult)
    except Exception:
        raise


def _normalize_higher_is_better(values: list[float]) -> list[float]:
    if not values:
        return []
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return [1.0 for _ in values]
    min_value = min(finite)
    max_value = max(finite)
    if abs(max_value - min_value) <= 1e-12:
        return [1.0 if math.isfinite(value) else 0.0 for value in values]
    return [
        ((value - min_value) / (max_value - min_value)) if math.isfinite(value) else 0.0
        for value in values
    ]


def _normalize_lower_is_better(values: list[float]) -> list[float]:
    if not values:
        return []
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return [1.0 for _ in values]
    min_value = min(finite)
    max_value = max(finite)
    if abs(max_value - min_value) <= 1e-12:
        return [1.0 if math.isfinite(value) else 0.0 for value in values]
    return [
        ((max_value - value) / (max_value - min_value)) if math.isfinite(value) else 0.0
        for value in values
    ]


def _monitor_record_event(
    self,
    kind: str,
    tags: Iterable[str],
    payload: Optional[dict] = None,
    *,
    symbol: Optional[str] = None,
    pside: Optional[str] = None,
    ts: Optional[int] = None,
) -> Optional[dict]:
    publisher = getattr(self, "monitor_publisher", None)
    if publisher is None:
        return None
    enriched = dict(payload or {})
    context = {
        "strategy_id": getattr(self, "strategy_id", None),
        "cycle_id": getattr(self, "_monitor_cycle_id", None),
        "config_hash": getattr(self, "config_hash", None),
    }
    event_context = getattr(self, "_monitor_event_context", {})
    if isinstance(event_context, dict):
        context.update(event_context)
    for key, value in context.items():
        if value is not None and key not in enriched:
            enriched[key] = value
    return publisher.record_event(
        kind, tags, enriched, ts=ts, symbol=symbol, pside=pside
    )


def _monitor_record_error(
    self,
    kind: str,
    error: Exception,
    *,
    tags: Optional[Iterable[str]] = None,
    payload: Optional[dict] = None,
    symbol: Optional[str] = None,
    pside: Optional[str] = None,
    ts: Optional[int] = None,
) -> Optional[dict]:
    publisher = getattr(self, "monitor_publisher", None)
    if publisher is None:
        return None
    enriched = dict(payload or {})
    context = {
        "strategy_id": getattr(self, "strategy_id", None),
        "cycle_id": getattr(self, "_monitor_cycle_id", None),
        "config_hash": getattr(self, "config_hash", None),
    }
    for key, value in context.items():
        if value is not None and key not in enriched:
            enriched[key] = value
    return publisher.record_error(
        kind,
        error,
        tags=tags,
        payload=enriched,
        ts=ts,
        symbol=symbol,
        pside=pside,
    )


def _monitor_emit_stop(
    self, reason: str, *, ts: Optional[int] = None, payload: Optional[dict] = None
) -> Optional[dict]:
    if getattr(self, "_monitor_stop_emitted", False):
        return None
    self._monitor_stop_emitted = True
    stop_payload = {"reason": str(reason)}
    if payload:
        stop_payload.update(payload)
    return self._monitor_record_event(
        "bot.stop",
        ("bot", "lifecycle", "stop"),
        stop_payload,
        ts=ts,
    )


def _monitor_hsl_payload(self, pside: str) -> dict:
    enabled = self._equity_hard_stop_enabled(pside)
    state = self._hsl_state(pside)
    last_metrics = state.get("last_metrics") or {}
    payload = {
        "enabled": bool(enabled),
        "tier": str(last_metrics.get("tier", "disabled" if not enabled else "unknown")),
        "halted": bool(state.get("halted", False)),
        "no_restart_latched": bool(state.get("no_restart_latched", False)),
        "pending_red_since_ms": state.get("pending_red_since_ms"),
        "cooldown_until_ms": state.get("cooldown_until_ms"),
        "cooldown_intervention_active": bool(state.get("cooldown_intervention_active", False)),
        "cooldown_repanic_reset_pending": bool(state.get("cooldown_repanic_reset_pending", False)),
        "last_metrics": dict(last_metrics) if isinstance(last_metrics, dict) else {},
    }
    return {k: v for k, v in payload.items() if v is not None}


def _monitor_order_payload(self, order: dict, *, source: str) -> dict:
    payload = {
        "id": order.get("id"),
        "custom_id": order.get("custom_id"),
        "side": order.get("side"),
        "position_side": order.get("position_side"),
        "qty": abs(float(order.get("qty", 0.0) or 0.0)),
        "price": float(order.get("price", 0.0) or 0.0),
        "reduce_only": bool(
            order.get("reduce_only")
            or (order.get("position_side") == "long" and order.get("side") == "sell")
            or (order.get("position_side") == "short" and order.get("side") == "buy")
        ),
        "pb_order_type": self._resolve_pb_order_type(order),
        "source": source,
    }
    planned_level = order.get("planned_level", order.get("_planned_level"))
    if planned_level is not None:
        payload["planned_level"] = planned_level
    if order.get("execution_price") is not None:
        payload["actual_execution_price"] = float(order["execution_price"])
    if order.get("_context"):
        payload["context"] = str(order["_context"])
    if order.get("_reason"):
        payload["reason"] = str(order["_reason"])
    if isinstance(order.get("_delta"), dict):
        payload["delta"] = dict(order["_delta"])
    return {k: v for k, v in payload.items() if v is not None}


def _monitor_fill_payload(self, event) -> dict:
    def _raw_objects() -> list[dict]:
        raw = getattr(event, "raw", None)
        rows = raw if isinstance(raw, list) else [raw]
        objects: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            objects.append(row)
            data = row.get("data")
            if isinstance(data, dict):
                objects.append(data)
                info = data.get("info")
                if isinstance(info, dict):
                    objects.append(info)
            info = row.get("info")
            if isinstance(info, dict):
                objects.append(info)
        return objects

    raw_objects = _raw_objects()

    def _raw_values(*names: str) -> list[object]:
        values = []
        for row in raw_objects:
            for name in names:
                if row.get(name) not in (None, ""):
                    values.append(row[name])
        return values

    def _raw_mappings(*names: str) -> list[dict]:
        mappings = []
        for value in _raw_values(*names):
            if isinstance(value, dict):
                mappings.append(value)
        return mappings

    def _first_number(*values: object) -> float | None:
        for value in values:
            if value in (None, ""):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                return number
        return None

    event_id = getattr(event, "id", None)
    fill_id = str(event_id).strip() if event_id not in (None, "") else None
    order_id = getattr(event, "order_id", None)
    if order_id in (None, ""):
        raw_order_ids = (
            _raw_values("order_id")
            + _raw_values("orderId")
            + _raw_values("order")
        )
        order_id = raw_order_ids[0] if raw_order_ids else None
    order_id = str(order_id).strip() if order_id not in (None, "") else None
    qty = _first_number(getattr(event, "qty", None))
    price = _first_number(getattr(event, "price", None))
    typed_fees = _raw_mappings("typed_fee")
    typed_fee = typed_fees[0] if typed_fees else {}
    fee_amount = _first_number(
        typed_fee.get("amount"),
        getattr(event, "fee_paid", None),
        getattr(event, "fee", None),
    )
    # FillEvent.fee_paid is a signed balance impact. Runtime evidence stores
    # the paid amount as a non-negative typed cost, so normalize only this
    # audit field and leave PnL accounting untouched.
    if fee_amount is not None:
        fee_amount = abs(float(fee_amount))
    fee_currency = typed_fee.get("currency") or getattr(event, "fee_currency", None)
    if fee_currency in (None, ""):
        fee_currency = getattr(self, "quote", None)
    fee_currency = str(fee_currency).strip().upper() if fee_currency not in (None, "") else None
    fee_rate = _first_number(
        typed_fee.get("rate"),
        getattr(event, "fee_ratio", None),
        getattr(event, "fee_rate", None),
    )
    if fee_rate is not None:
        fee_rate = abs(float(fee_rate))
    if fee_rate is None and fee_amount is not None and qty and price:
        c_mult = _first_number(getattr(event, "c_mult", None), 1.0)
        notional = qty * price * c_mult if c_mult and c_mult > 0.0 else 0.0
        if notional > 0.0:
            fee_rate = fee_amount / notional
    typed_slippages = _raw_mappings("typed_slippage")
    typed_slippage = typed_slippages[0] if typed_slippages else {}
    reference_price = _first_number(
        typed_slippage.get("reference_price"),
        getattr(event, "reference_price", None),
        getattr(event, "expected_price", None),
        getattr(event, "order_price", None),
        *_raw_values("reference_price"),
        *_raw_values("expected_price"),
        *_raw_values("order_price"),
    )
    part_sequence = getattr(event, "part_sequence", None)
    if part_sequence in (None, ""):
        raw_parts = _raw_values("part_sequence")
        part_sequence = raw_parts[0] if raw_parts else None
    quantity_before = _first_number(
        getattr(event, "quantity_before", None),
        *_raw_values("quantity_before"),
    )
    quantity_after = _first_number(
        getattr(event, "quantity_after", None),
        *_raw_values("quantity_after"),
    )
    payload = {
        "id": event_id,
        "fill_id": fill_id,
        "order_id": order_id,
        "timestamp": int(getattr(event, "timestamp", 0) or 0),
        "symbol": getattr(event, "symbol", None),
        "side": str(getattr(event, "side", "") or "").lower(),
        "position_side": str(getattr(event, "position_side", "") or "").lower(),
        "qty": qty,
        "price": price,
        "executed_price": price,
        "reference_price": reference_price,
        "part_sequence": part_sequence,
        "quantity_before": quantity_before,
        "quantity_after": quantity_after,
        "pnl": float(getattr(event, "pnl", 0.0) or 0.0),
        "fee": {
            "amount": fee_amount,
            "currency": fee_currency,
            "rate": fee_rate,
        },
        "slippage": {
            "reference_price": reference_price,
            "executed_price": price,
        },
        "pb_order_type": str(getattr(event, "pb_order_type", "") or "").lower(),
        "client_order_id": getattr(event, "client_order_id", None),
        "source_ids": list(getattr(event, "source_ids", []) or []),
    }
    for context_key in ("strategy_id", "cycle_id", "config_hash"):
        value = getattr(event, context_key, None)
        if value in (None, ""):
            values = _raw_values(context_key)
            value = values[0] if values else None
        if value not in (None, ""):
            payload[context_key] = str(value)
    # Keep typed-audit fields even when the source event does not provide
    # them. ``summarize_runtime_events`` must see the missing value and stay
    # blocked; dropping it would hide why the evidence is incomplete.
    required_audit_fields = {
        "fill_id",
        "order_id",
        "reference_price",
        "part_sequence",
        "quantity_before",
        "quantity_after",
        "fee",
        "slippage",
    }
    return {
        k: v
        for k, v in payload.items()
        if v is not None or k in required_audit_fields
    }


def _monitor_record_fill_history(self, event) -> Optional[dict]:
    publisher = getattr(self, "monitor_publisher", None)
    if publisher is None:
        return None
    return publisher.record_fill(
        self._monitor_fill_payload(event),
        symbol=getattr(event, "symbol", None),
        pside=str(getattr(event, "position_side", "") or "").lower() or None,
        ts=int(getattr(event, "timestamp", 0) or 0) or None,
        raw_payload=getattr(event, "raw", None),
    )


def _monitor_record_price_ticks(
    self,
    last_prices: dict[str, float],
    *,
    ts: Optional[int] = None,
    source: Optional[str] = None,
) -> int:
    publisher = getattr(self, "monitor_publisher", None)
    if publisher is None:
        return 0
    emitted = 0
    for symbol in sorted(last_prices):
        try:
            price = float(last_prices[symbol])
        except Exception:
            continue
        if not math.isfinite(price) or price <= 0.0:
            continue
        if publisher.record_price_tick(symbol, price, ts=ts, source=source) is not None:
            emitted += 1
    return emitted


def _monitor_record_runtime_observations(
    self,
    snapshot: dict,
    *,
    ts: Optional[int] = None,
) -> int:
    """Write typed runtime observations without inventing missing surfaces.

    The paper process has a local ledger and a validated public Bitget market
    surface.  Record those observations explicitly so the runtime reader can
    distinguish known evidence from absent funding, reconciliation, or live
    account data.  A missing surface is intentionally omitted and therefore
    keeps the runtime gate fail-closed.
    """

    publisher = getattr(self, "monitor_publisher", None)
    if publisher is None:
        return 0
    now_ms = utc_ms() if ts is None else int(ts)
    emitted = 0
    account = snapshot.get("account") if isinstance(snapshot, dict) else {}
    account = account if isinstance(account, dict) else {}
    heartbeat_payload: dict[str, Any] = {
        "source": "monitor_snapshot",
        "uptime_ms": max(0, now_ms - int(getattr(self, "start_time_ms", now_ms) or now_ms)),
    }
    for key in ("equity", "realized_pnl_cumsum"):
        if key in account:
            heartbeat_payload[key] = deepcopy(account[key])
    if self._monitor_record_event(
        "runtime.heartbeat",
        ("runtime", "heartbeat"),
        heartbeat_payload,
        ts=now_ms,
    ) is not None:
        emitted += 1

    surfaces = getattr(self, "_crypto_market_surfaces", {})
    if isinstance(surfaces, dict):
        for symbol in sorted(surfaces):
            surface = surfaces.get(symbol)
            if not isinstance(surface, dict):
                continue
            try:
                mark_price = float(surface["mark_price"])
                index_price = float(surface["index_price"])
            except (KeyError, TypeError, ValueError):
                continue
            if not all(
                math.isfinite(value) and value > 0.0
                for value in (mark_price, index_price)
            ):
                continue
            market_payload: dict[str, Any] = {
                "market": {
                    "mark_price": mark_price,
                    "index_price": index_price,
                },
                "source": "bitget_public_market_surface",
            }
            for key in ("surface_hash", "observed_at_ms"):
                if surface.get(key) not in (None, ""):
                    market_payload[key] = surface[key]
            if self._monitor_record_event(
                "market.mark_index",
                ("market", "risk", "mark_index"),
                market_payload,
                symbol=str(symbol),
                ts=now_ms,
            ) is not None:
                emitted += 1

    # Only the local paper ledger has a declared margin model here.  Live
    # account margin is deliberately not reconstructed from notional; it must
    # come from an authoritative exchange account surface in a later package.
    paper_mode = bool(
        getattr(self, "paper_mode", False)
        or getattr(self, "_paper_mode", False)
        or (
            isinstance(getattr(self, "config", None), dict)
            and bool(getattr(self, "config", {}).get("_paper_mode", False))
        )
    )
    positions = snapshot.get("positions") if isinstance(snapshot, dict) else {}
    positions = positions if isinstance(positions, dict) else {}
    if paper_mode and isinstance(surfaces, dict):
        equity = account.get("equity", account.get("balance_raw"))
        try:
            equity = float(equity)
        except (TypeError, ValueError):
            equity = 0.0
        leverage = getattr(self, "paper_leverage", None)
        if leverage in (None, "") and isinstance(getattr(self, "config", None), dict):
            leverage = getattr(self, "config", {}).get("leverage", 3.0)
        try:
            leverage = float(leverage)
        except (TypeError, ValueError):
            leverage = 0.0
        gross_exposure = 0.0
        position_count = 0
        complete = equity > 0.0 and math.isfinite(equity) and leverage > 0.0
        for symbol, symbol_positions in positions.items():
            surface = surfaces.get(symbol)
            if not isinstance(surface, dict):
                if any(
                    isinstance(symbol_positions, dict)
                    and float((symbol_positions.get(pside) or {}).get("size", 0.0) or 0.0)
                    != 0.0
                    for pside in ("long", "short")
                    if isinstance(symbol_positions.get(pside), dict)
                ):
                    complete = False
                continue
            try:
                mark_price = float(surface["mark_price"])
                contract_size = float(surface["contract_size"])
            except (KeyError, TypeError, ValueError):
                complete = False
                continue
            if (
                not math.isfinite(mark_price)
                or mark_price <= 0.0
                or not math.isfinite(contract_size)
                or contract_size <= 0.0
            ):
                complete = False
                continue
            if not isinstance(symbol_positions, dict):
                continue
            for pside in ("long", "short"):
                side_position = symbol_positions.get(pside)
                if not isinstance(side_position, dict):
                    continue
                try:
                    size = abs(float(side_position.get("size", 0.0) or 0.0))
                except (TypeError, ValueError):
                    complete = False
                    continue
                if not math.isfinite(size) or size < 0.0:
                    complete = False
                    continue
                if size > 0.0:
                    position_count += 1
                    gross_exposure += size * mark_price * contract_size
        distance = getattr(self, "_crypto_min_liquidation_distance", None)
        if position_count == 0:
            distance = 1.0
        try:
            distance = float(distance)
        except (TypeError, ValueError):
            complete = False
            distance = 0.0
        if not math.isfinite(distance) or distance < 0.0 or distance > 1.0:
            complete = False
        if complete:
            margin_payload = {
                "margin": {
                    "equity": equity,
                    "gross_exposure": gross_exposure,
                    "used_margin": gross_exposure / leverage,
                    "liquidation_distance": distance,
                },
                "source": "paper_local_ledger_model",
                "leverage": leverage,
                "position_count": position_count,
            }
            if self._monitor_record_event(
                "margin.snapshot",
                ("risk", "margin", "snapshot"),
                margin_payload,
                ts=now_ms,
            ) is not None:
                emitted += 1
    return emitted


def _monitor_record_funding_evidence(
    self,
    evidence: Any,
    *,
    ts: Optional[int] = None,
) -> int:
    """Persist one already-validated explicit funding settlement.

    Funding is deliberately supplied by the paper ledger caller.  This
    helper never fetches a rate or invents a settlement; malformed evidence is
    ignored so the runtime gate cannot be made to pass by an incomplete row.
    """

    if not isinstance(evidence, dict):
        return 0
    if str(evidence.get("kind") or "").strip().lower() != "funding.settled":
        return 0
    payload = evidence.get("payload")
    if not isinstance(payload, dict):
        return 0
    funding = payload.get("funding")
    if not isinstance(funding, dict):
        return 0
    required = ("amount", "rate", "notional", "currency")
    if any(key not in funding for key in required):
        return 0
    try:
        amount = float(funding["amount"])
        rate = float(funding["rate"])
        notional = float(funding["notional"])
    except (TypeError, ValueError, OverflowError):
        return 0
    currency = str(funding.get("currency") or "").strip().upper()
    if (
        not all(math.isfinite(value) for value in (amount, rate, notional))
        or notional < 0.0
        or not currency
        or not math.isclose(abs(amount), abs(notional * rate), rel_tol=1e-5, abs_tol=1e-8)
    ):
        return 0
    raw_ts = evidence.get("ts", payload.get("timestamp", ts))
    try:
        event_ts = int(raw_ts)
    except (TypeError, ValueError, OverflowError):
        return 0
    if event_ts <= 0:
        return 0
    event_payload = deepcopy(payload)
    event_payload["funding"] = {
        "amount": amount,
        "rate": rate,
        "notional": notional,
        "currency": currency,
    }
    recorded = self._monitor_record_event(
        "funding.settled",
        tuple(evidence.get("tags") or ("funding", "settlement")),
        event_payload,
        symbol=payload.get("symbol"),
        pside=payload.get("position_side"),
        ts=event_ts,
    )
    return 1 if recorded is not None else 0


def _monitor_record_reconciliation_evidence(
    self,
    evidence: Any,
    *,
    ts: Optional[int] = None,
) -> int:
    """Persist one explicit paper-ledger reconciliation result.

    The paper ledger is responsible for calculating the checks.  This
    boundary only accepts the two known evidence envelopes and verifies that
    they carry a non-empty source and a SHA-256 source hash.  It never turns
    an incomplete cost record into a complete one.
    """

    if not isinstance(evidence, dict):
        return 0
    kind = str(evidence.get("kind") or "").strip().lower()
    if kind not in {"reconciliation.check", "costs.reconciled"}:
        return 0
    payload = evidence.get("payload")
    if not isinstance(payload, dict):
        return 0
    source = str(payload.get("source") or "").strip()
    source_hash = str(payload.get("source_hash") or "").strip().lower()
    if (
        not source
        or len(source_hash) != 64
        or any(char not in "0123456789abcdef" for char in source_hash)
    ):
        return 0
    raw_ts = evidence.get("ts", payload.get("timestamp", ts))
    try:
        event_ts = int(raw_ts)
    except (TypeError, ValueError, OverflowError):
        return 0
    if event_ts <= 0:
        return 0
    recorded = self._monitor_record_event(
        kind,
        tuple(evidence.get("tags") or ("paper", "reconciliation")),
        deepcopy(payload),
        symbol=payload.get("symbol"),
        pside=payload.get("position_side"),
        ts=event_ts,
    )
    return 1 if recorded is not None else 0


def _monitor_record_paper_reconciliation(
    self,
    *,
    ts: Optional[int] = None,
) -> int:
    """Ask an explicitly paper-only bot to publish its current ledger proof.

    The paper bot owns the ledger calculation.  If an explicit saved public
    execution feed is configured, it is asked to apply only newly validated
    settlements before reconciliation.  This helper never downloads funding,
    calls a private exchange endpoint, or fills a missing source value.
    """

    paper_mode = bool(
        getattr(self, "paper_mode", False)
        or getattr(self, "_paper_mode", False)
        or (
            isinstance(getattr(self, "config", None), dict)
            and bool(getattr(self, "config", {}).get("_paper_mode", False))
        )
    )
    recorder = getattr(self, "record_paper_reconciliation", None)
    if not paper_mode or not callable(recorder):
        return 0
    emitted = 0
    funding_recorder = getattr(self, "apply_recorded_public_execution_inputs", None)
    if callable(funding_recorder):
        try:
            funding_evidence = funding_recorder(timestamp=ts)
            if isinstance(funding_evidence, list):
                emitted += len(funding_evidence)
        except Exception as exc:
            logging.error("[monitor] public paper execution feed failed: %s", exc)
    try:
        evidence = recorder(timestamp=ts)
    except Exception as exc:
        logging.error("[monitor] paper reconciliation failed: %s", exc)
        return emitted
    return emitted + (len(evidence) if isinstance(evidence, list) else 0)


def _monitor_handle_candlestick_persist(
    self,
    symbol: str,
    timeframe: str,
    batch: np.ndarray,
) -> None:
    publisher = getattr(self, "monitor_publisher", None)
    if publisher is None or not getattr(self, "_bot_ready", False):
        return
    timeframe = str(timeframe)
    if timeframe not in ("1m", "1h"):
        return
    if not isinstance(batch, np.ndarray) or batch.size == 0:
        return
    candles = [
        {
            "ts": int(row["ts"]),
            "o": float(row["o"]),
            "h": float(row["h"]),
            "l": float(row["l"]),
            "c": float(row["c"]),
            "bv": float(row["bv"]),
        }
        for row in batch
    ]
    publisher.record_completed_candles(symbol, timeframe, candles)


def _build_health_summary_payload(self, *, now_ms: Optional[int] = None) -> dict:
    now_ms = utc_ms() if now_ms is None else int(now_ms)
    n_long = 0
    n_short = 0
    for _, pos_data in self.positions.items():
        if pos_data.get("long", {}).get("size", 0.0) != 0.0:
            n_long += 1
        if pos_data.get("short", {}).get("size", 0.0) != 0.0:
            n_short += 1
    balance_raw = float(self.get_raw_balance())
    balance_snapped = float(self.get_hysteresis_snapped_balance())
    error_counts = getattr(self, "error_counts", [])
    recent_errors = len([x for x in error_counts if x > now_ms - 1000 * 60 * 60])
    payload = {
        "uptime_ms": max(0, now_ms - self._health_start_ms),
        "last_loop_duration_ms": int(getattr(self, "_last_loop_duration_ms", 0) or 0),
        "positions_long": n_long,
        "positions_short": n_short,
        "balance_raw": balance_raw,
        "balance_snapped": balance_snapped,
        "equity": float(getattr(self, "_monitor_last_equity", balance_raw) or balance_raw),
        "orders_placed": int(self._health_orders_placed),
        "orders_cancelled": int(self._health_orders_cancelled),
        "fills": int(self._health_fills),
        "pnl": float(self._health_pnl),
        "errors_last_hour": recent_errors,
        "ws_reconnects": int(self._health_ws_reconnects),
        "rate_limits": int(self._health_rate_limits),
    }
    rss = _get_process_rss_bytes()
    if rss is not None:
        payload["rss_bytes"] = int(rss)
    return payload


def _monitor_recent_orders_payload(
    self,
    orders: list[dict],
    *,
    limit: int = 20,
) -> list[dict]:
    trimmed = list(orders[-limit:]) if orders else []
    payloads: list[dict] = []
    for order in trimmed:
        try:
            payload = self._monitor_order_payload(order, source=str(order.get("source", "runtime")))
        except Exception:
            continue
        execution_ts = order.get("execution_timestamp")
        if execution_ts is not None:
            try:
                payload["execution_timestamp"] = int(execution_ts)
            except Exception:
                pass
        payloads.append(payload)
    return payloads


def _build_monitor_market_section(self) -> dict[str, dict]:
    symbols = (
        set(getattr(self, "active_symbols", []) or [])
        | set(getattr(self, "positions", {}).keys())
        | set(getattr(self, "open_orders", {}).keys())
        | set(getattr(self, "trailing_prices", {}).keys())
        | set(getattr(self, "effective_min_cost", {}).keys())
    )
    approved_minus_ignored = getattr(self, "approved_coins_minus_ignored_coins", {})
    approved = getattr(self, "approved_coins", {})
    ignored = getattr(self, "ignored_coins", {})
    for pside in ("long", "short"):
        symbols |= set(approved_minus_ignored.get(pside, set()) or set())
        symbols |= set(approved.get(pside, set()) or set())
        symbols |= set(ignored.get(pside, set()) or set())
    market_snapshot_cache = getattr(getattr(self, "market_snapshot_provider", None), "_cache", {})
    runtime_hints = getattr(self, "_monitor_runtime_market_hints", {})
    out: dict[str, dict] = {}
    for symbol in sorted(symbols):
        entry: dict[str, Any] = {
            "active_symbol": symbol in set(getattr(self, "active_symbols", []) or []),
            "tradable": bool(getattr(self, "markets_dict", {}).get(symbol, {}).get("active", True)),
            "approved": {
                "long": symbol in set(approved_minus_ignored.get("long", set()) or set()),
                "short": symbol in set(approved_minus_ignored.get("short", set()) or set()),
            },
            "ignored": {
                "long": symbol in set(ignored.get("long", set()) or set()),
                "short": symbol in set(ignored.get("short", set()) or set()),
            },
            "effective_min_cost": float(
                getattr(self, "effective_min_cost", {}).get(symbol, 0.0) or 0.0
            ),
            "min_cost": float(getattr(self, "min_costs", {}).get(symbol, 0.0) or 0.0),
            "min_qty": float(getattr(self, "min_qtys", {}).get(symbol, 0.0) or 0.0),
            "price_step": float(getattr(self, "price_steps", {}).get(symbol, 0.0) or 0.0),
            "qty_step": float(getattr(self, "qty_steps", {}).get(symbol, 0.0) or 0.0),
            "c_mult": float(getattr(self, "c_mults", {}).get(symbol, 0.0) or 0.0),
            "has_open_orders": bool(getattr(self, "open_orders", {}).get(symbol)),
            "has_position": bool(self.has_position(symbol=symbol)),
        }
        cached = market_snapshot_cache.get(symbol)
        if cached is not None:
            try:
                entry["last_price"] = float(cached.last)
                entry["last_price_ts_ms"] = int(cached.fetched_ms)
                entry["last_price_source"] = str(cached.source)
            except Exception as exc:
                logging.debug(
                    "[monitor] invalid cached market snapshot | symbol=%s error_type=%s error=%s",
                    symbol,
                    type(exc).__name__,
                    exc,
                )
        try:
            entry["last_refresh_ms"] = int(self.cm.get_last_refresh_ms(symbol))
            entry["last_final_candle_ts_ms"] = int(self.cm.get_last_final_ts(symbol))
        except Exception:
            pass
        trailing = getattr(self, "trailing_prices", {}).get(symbol, {})
        if trailing:
            entry["trailing"] = {
                pside: {k: float(v) for k, v in trailing.get(pside, {}).items()}
                for pside in ("long", "short")
                if trailing.get(pside)
            }
        hint = runtime_hints.get(symbol, {})
        if isinstance(hint, dict):
            ema_bands = hint.get("ema_bands")
            if isinstance(ema_bands, dict):
                entry["ema_bands"] = deepcopy(ema_bands)
        entry_volatility_logrange_ema: dict[str, float] = {}
        for pside in ("long", "short"):
            entry_volatility_logrange_ema[pside] = float(_monitor_h1_entry_logrange(self, pside, symbol))
        entry["entry_volatility_logrange_ema"] = entry_volatility_logrange_ema
        out[symbol] = entry
    return out


async def _build_monitor_forager_section(self) -> dict[str, dict]:
    out: dict[str, dict] = {}
    approved_minus_ignored = getattr(self, "approved_coins_minus_ignored_coins", {})
    approved = getattr(self, "approved_coins", {})
    ignored = getattr(self, "ignored_coins", {})
    for pside in ("long", "short"):
        candidate_universe = sorted(approved_minus_ignored.get(pside, set()) or set())
        approved_symbols = sorted(approved.get(pside, set()) or set())
        ignored_symbols = sorted(ignored.get(pside, set()) or set())
        current_n = int(self.get_current_n_positions(pside))
        max_n = int(self.get_max_n_positions(pside))
        selected_symbols = sorted(
            set(getattr(self, "PB_modes", {}).get(pside, {}).keys())
            | {sym for sym in getattr(self, "positions", {}) if self.has_position(pside, sym)}
        )
        held_symbols = {sym for sym in getattr(self, "positions", {}) if self.has_position(pside, sym)}
        entry_order_symbols = set()
        for symbol in getattr(self, "open_orders", {}):
            for order in self.open_orders.get(symbol, []):
                if str(order.get("position_side", "")) != pside:
                    continue
                side = str(order.get("side", ""))
                reduce_only = (pside == "long" and side == "sell") or (
                    pside == "short" and side == "buy"
                )
                if not reduce_only:
                    entry_order_symbols.add(symbol)
                    break
        pending_symbols = [
            sym for sym in selected_symbols if sym not in held_symbols and sym not in entry_order_symbols
        ]
        out[pside] = {
            "enabled": bool(self.is_pside_enabled(pside)),
            "forager_mode": bool(self.is_forager_mode(pside)),
            "forced_mode": self.live_value(f"forced_mode_{pside}"),
            "slots": {
                "current": current_n,
                "max": max_n,
                "open": max(0, max_n - current_n),
            },
            "approved_symbols": approved_symbols,
            "ignored_symbols": ignored_symbols,
            "candidate_universe": candidate_universe,
            "selected_symbols": selected_symbols,
            "active_symbols": sorted(
                sym
                for sym in set(getattr(self, "active_symbols", []) or [])
                if sym in candidate_universe or sym in selected_symbols
            ),
            "pending_symbols": pending_symbols,
            "next_symbol": pending_symbols[0] if pending_symbols else None,
            "score_weights": dict(self.bot_value(pside, "forager_score_weights") or {}),
            "volume_drop_pct": float(self.bot_value(pside, "forager_volume_drop_pct") or 0.0),
        }
        if not out[pside]["forager_mode"] or not candidate_universe:
            continue
        if not hasattr(self, "build_forager_candidate_payload"):
            continue
        try:
            min_cost_flags = {
                sym: bool(self.effective_min_cost_is_low_enough(pside, sym)) for sym in candidate_universe
            }
        except Exception:
            min_cost_flags = {sym: True for sym in candidate_universe}
        try:
            candidate_payload = await self.build_forager_candidate_payload(
                pside,
                candidate_universe,
                min_cost_flags,
                max_age_ms=60_000,
                max_network_fetches=0,
            )
        except Exception:
            continue
        if not isinstance(candidate_payload, list):
            continue
        features: list[dict[str, Any]] = []
        weights = out[pside]["score_weights"]
        for symbol, candidate in zip(candidate_universe, candidate_payload):
            if not isinstance(candidate, dict):
                continue
            enabled = bool(candidate.get("enabled", False))
            volume_score = float(candidate.get("volume_score", 0.0) or 0.0)
            volatility_score = float(candidate.get("volatility_score", 0.0) or 0.0)
            ema_readiness_raw = None
            entry_trigger_price = None
            try:
                entry_initial_ema_dist = float(candidate.get("entry_initial_ema_dist", 0.0) or 0.0)
                if pside == "long":
                    ema_lower = float(candidate.get("ema_lower", 0.0) or 0.0)
                    bid = float(candidate.get("bid", 0.0) or 0.0)
                    if ema_lower > 0.0 and bid > 0.0:
                        entry_trigger_price = ema_lower * (1.0 - entry_initial_ema_dist)
                        ema_readiness_raw = bid / entry_trigger_price - 1.0
                else:
                    ema_upper = float(candidate.get("ema_upper", 0.0) or 0.0)
                    ask = float(candidate.get("ask", 0.0) or 0.0)
                    if ema_upper > 0.0 and ask > 0.0:
                        entry_trigger_price = ema_upper * (1.0 + entry_initial_ema_dist)
                        ema_readiness_raw = 1.0 - ask / entry_trigger_price
            except Exception:
                ema_readiness_raw = None
            features.append(
                {
                    "symbol": symbol,
                    "enabled": enabled,
                    "volume_score_raw": volume_score,
                    "volatility_score_raw": volatility_score,
                    "ema_readiness_score_raw": ema_readiness_raw,
                    "entry_trigger_price": entry_trigger_price,
                }
            )
        enabled_features = [feature for feature in features if feature["enabled"]]
        if not enabled_features:
            continue
        volume_norm = _normalize_higher_is_better(
            [float(feature["volume_score_raw"]) for feature in enabled_features]
        )
        volatility_norm = _normalize_higher_is_better(
            [float(feature["volatility_score_raw"]) for feature in enabled_features]
        )
        ema_norm = _normalize_lower_is_better(
            [
                float(feature["ema_readiness_score_raw"])
                if feature["ema_readiness_score_raw"] is not None
                else float("nan")
                for feature in enabled_features
            ]
        )
        for feature, vol_norm, vola_norm, ema_score_norm in zip(
            enabled_features, volume_norm, volatility_norm, ema_norm
        ):
            feature["volume_score_normalized"] = vol_norm
            feature["volatility_score_normalized"] = vola_norm
            feature["ema_readiness_score_normalized"] = ema_score_norm
            feature["total_score"] = (
                float(weights.get("volume", 0.0) or 0.0) * vol_norm
                + float(weights.get("volatility", 0.0) or 0.0) * vola_norm
                + float(weights.get("ema_readiness", 0.0) or 0.0) * ema_score_norm
            )
        top_total = max(enabled_features, key=lambda feature: (feature["total_score"], feature["symbol"]))
        top_volume = max(
            enabled_features,
            key=lambda feature: (feature["volume_score_raw"], feature["symbol"]),
        )
        top_volatility = max(
            enabled_features,
            key=lambda feature: (feature["volatility_score_raw"], feature["symbol"]),
        )
        top_ema = None
        ema_candidates = [
            feature
            for feature in enabled_features
            if feature["ema_readiness_score_raw"] is not None
            and math.isfinite(float(feature["ema_readiness_score_raw"]))
        ]
        if ema_candidates:
            top_ema = min(
                ema_candidates,
                key=lambda feature: (feature["ema_readiness_score_raw"], feature["symbol"]),
            )

        def _ranking_payload(
            feature: Optional[dict[str, Any]],
            raw_key: str,
            normalized_key: str,
        ) -> Optional[dict[str, Any]]:
            if feature is None:
                return None
            payload = {
                "symbol": str(feature["symbol"]),
                "raw_score": float(feature[raw_key]) if feature.get(raw_key) is not None else None,
                "normalized_score": float(feature.get(normalized_key, 0.0) or 0.0),
            }
            if "total_score" in feature:
                payload["total_score"] = float(feature["total_score"])
            if feature.get("entry_trigger_price") is not None:
                payload["entry_trigger_price"] = float(feature["entry_trigger_price"])
            return payload

        out[pside]["ranking"] = {
            "top_total": _ranking_payload(top_total, "total_score", "total_score"),
            "top_volume": _ranking_payload(
                top_volume, "volume_score_raw", "volume_score_normalized"
            ),
            "top_volatility": _ranking_payload(
                top_volatility, "volatility_score_raw", "volatility_score_normalized"
            ),
            "top_ema_readiness": _ranking_payload(
                top_ema, "ema_readiness_score_raw", "ema_readiness_score_normalized"
            ),
        }
        if out[pside]["next_symbol"] is not None:
            next_symbol = str(out[pside]["next_symbol"])
            next_feature = next(
                (feature for feature in enabled_features if feature["symbol"] == next_symbol),
                None,
            )
            if next_feature is not None:
                if next_feature.get("entry_trigger_price") is not None:
                    out[pside]["next_entry_trigger_price"] = float(next_feature["entry_trigger_price"])
                if next_feature.get("ema_readiness_score_raw") is not None:
                    out[pside]["next_entry_distance_ratio"] = float(
                        next_feature["ema_readiness_score_raw"]
                    )
    return out


def _build_monitor_unstuck_section(self) -> dict[str, Any]:
    has_open = bool(self.has_open_unstuck_order())
    allowances_live = self._calc_unstuck_allowances_live(allow_new_unstuck=not has_open)
    out: dict[str, Any] = {
        "has_open_order": has_open,
        "open_orders": [],
        "sides": {},
    }
    runtime_hints = getattr(self, "_monitor_runtime_unstuck_hints", {})
    for symbol in sorted(getattr(self, "open_orders", {})):
        for order in self.open_orders.get(symbol, []):
            try:
                pb_order_type = str(order.get("custom_id", "")).lower()
            except Exception:
                pb_order_type = ""
            if "unstuck" not in pb_order_type and "unstuck" not in str(
                self._resolve_pb_order_type(order)
            ):
                continue
            payload = self._monitor_order_payload(order, source="REST")
            payload["symbol"] = symbol
            out["open_orders"].append(payload)
    for pside in ("long", "short"):
        info = self._calc_unstuck_allowance_for_logging(pside)
        side_payload: dict[str, Any] = {
            "status": info.get("status"),
            "allowance_live": float(allowances_live.get(pside, 0.0) or 0.0),
            "configured_loss_allowance_pct": float(
                self.bot_value(pside, "unstuck_loss_allowance_pct") or 0.0
            ),
            "configured_close_pct": float(self.bot_value(pside, "unstuck_close_pct") or 0.0),
            "configured_threshold": float(self.bot_value(pside, "unstuck_threshold") or 0.0),
        }
        for key in ("allowance", "peak", "pct_from_peak"):
            if key in info:
                side_payload[key] = float(info[key])
        hint = runtime_hints.get(pside, {})
        if isinstance(hint, dict):
            for key in (
                "next_symbol",
                "next_target_price",
                "next_target_distance_ratio",
                "next_unstuck_trigger_distance_ratio",
            ):
                if key in hint and hint[key] is not None:
                    side_payload[key] = hint[key]
            ema_bands = hint.get("ema_bands")
            if isinstance(ema_bands, dict):
                side_payload["ema_bands"] = deepcopy(ema_bands)
        out["sides"][pside] = side_payload
    return out


def _build_monitor_runtime_market_hints(
    self,
    symbols: Iterable[str],
    last_prices: dict[str, float],
    m1_close_emas: dict[str, dict[float, float]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        hint: dict[str, Any] = {}
        per_side: dict[str, dict[str, float]] = {}
        last_price = last_prices.get(symbol)
        for pside in ("long", "short"):
            try:
                span0 = float(self.bp(pside, "ema_span_0", symbol))
                span1 = float(self.bp(pside, "ema_span_1", symbol))
                entry_dist = float(self.bp(pside, "entry_initial_ema_dist", symbol))
                unstuck_ema_dist = float(self.bp(pside, "unstuck_ema_dist", symbol))
            except Exception:
                continue
            if span0 <= 0.0 or span1 <= 0.0:
                continue
            span2 = (span0 * span1) ** 0.5
            emas = m1_close_emas.get(symbol, {})
            ema0 = float(emas.get(span0, 0.0) or 0.0)
            ema1 = float(emas.get(span1, 0.0) or 0.0)
            ema2 = float(emas.get(span2, 0.0) or 0.0)
            if min(ema0, ema1, ema2) <= 0.0:
                continue
            ema_lower = min(ema0, ema1, ema2)
            ema_upper = max(ema0, ema1, ema2)
            side_hint: dict[str, float] = {
                "lower": float(ema_lower),
                "upper": float(ema_upper),
                "entry_trigger_price": float(
                    ema_lower * (1.0 - entry_dist) if pside == "long" else ema_upper * (1.0 + entry_dist)
                ),
                "unstuck_trigger_price": float(
                    ema_upper * (1.0 + unstuck_ema_dist)
                    if pside == "long"
                    else ema_lower * (1.0 - unstuck_ema_dist)
                ),
            }
            if last_price is not None and float(last_price) > 0.0:
                side_hint["last_price"] = float(last_price)
            per_side[pside] = side_hint
        if per_side:
            hint["ema_bands"] = per_side
            out[symbol] = hint
    return out


def _build_monitor_runtime_unstuck_hints(
    self,
    idx_to_symbol: dict[int, str],
    orders: list[dict[str, Any]],
    last_prices: dict[str, float],
    market_hints: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {"long": {}, "short": {}}
    for order in orders:
        order_type_str = str(order.get("order_type", ""))
        if "close_unstuck" not in order_type_str:
            continue
        symbol = idx_to_symbol.get(int(order.get("symbol_idx", -1)))
        if symbol is None:
            continue
        pside = "long" if "long" in order_type_str else "short"
        target_price = float(order.get("price", 0.0) or 0.0)
        current_price = float(last_prices.get(symbol, 0.0) or 0.0)
        distance_ratio = None
        if current_price > 0.0 and target_price > 0.0:
            distance_ratio = float(target_price / current_price - 1.0)
        hint = {
            "next_symbol": symbol,
            "next_target_price": target_price,
            "next_target_distance_ratio": distance_ratio,
        }
        market_hint = market_hints.get(symbol, {})
        if isinstance(market_hint, dict):
            ema_bands = market_hint.get("ema_bands", {})
            if isinstance(ema_bands, dict) and isinstance(ema_bands.get(pside), dict):
                side_ema_bands = deepcopy(ema_bands.get(pside))
                hint["ema_bands"] = side_ema_bands
                trigger_price = float(side_ema_bands.get("unstuck_trigger_price", 0.0) or 0.0)
                if current_price > 0.0 and trigger_price > 0.0:
                    hint["next_unstuck_trigger_distance_ratio"] = float(
                        trigger_price / current_price - 1.0
                    )
        out[pside] = hint
        break
    return out


def _update_monitor_runtime_hints(
    self,
    *,
    symbols: Iterable[str],
    last_prices: dict[str, float],
    m1_close_emas: dict[str, dict[float, float]],
    h1_log_range_emas: dict[str, dict[float, float]],
    idx_to_symbol: dict[int, str],
    orders: list[dict[str, Any]],
) -> None:
    market_hints = self._build_monitor_runtime_market_hints(symbols, last_prices, m1_close_emas)
    self._monitor_runtime_market_hints = market_hints
    self._monitor_runtime_h1_log_range_emas = deepcopy(h1_log_range_emas)
    self._monitor_runtime_unstuck_hints = self._build_monitor_runtime_unstuck_hints(
        idx_to_symbol,
        orders,
        last_prices,
        market_hints,
    )


def _build_monitor_recent_section(self) -> dict[str, Any]:
    return {
        "order_executions": self._monitor_recent_orders_payload(
            getattr(self, "recent_order_executions", [])
        ),
        "order_cancellations": self._monitor_recent_orders_payload(
            getattr(self, "recent_order_cancellations", [])
        ),
    }


def _monitor_wallet_exposure_limit_with_allowance(self, pside: str, symbol: str) -> float:
    wel = float(self.bp(pside, "wallet_exposure_limit", symbol))
    allowance_pct = float(self.bp(pside, "risk_we_excess_allowance_pct", symbol))
    return wel * (1.0 + max(0.0, allowance_pct))


def _monitor_entry_trailing_limit_cap(
    self,
    pside: str,
    symbol: str,
    wallet_exposure: float,
) -> tuple[Optional[float], Optional[str]]:
    allowed_limit = _monitor_wallet_exposure_limit_with_allowance(self, pside, symbol)
    if allowed_limit <= 0.0:
        return None, None
    trailing_ratio = float(self.bp(pside, "entry_trailing_grid_ratio", symbol))
    if trailing_ratio >= 1.0 or trailing_ratio <= -1.0:
        return allowed_limit, "trailing_only"
    if trailing_ratio == 0.0:
        return None, "grid_only"
    wallet_exposure_ratio = wallet_exposure / allowed_limit if allowed_limit > 0.0 else 0.0
    if trailing_ratio > 0.0:
        if wallet_exposure_ratio < trailing_ratio:
            if wallet_exposure == 0.0:
                return allowed_limit, "trailing_first"
            return min(allowed_limit * trailing_ratio * 1.01, allowed_limit), "trailing_first"
        return None, "grid_first"
    if wallet_exposure_ratio < 1.0 + trailing_ratio:
        return None, "grid_first"
    return allowed_limit, "trailing_after_grid"


def _monitor_h1_entry_logrange(self, pside: str, symbol: str) -> float:
    h1_log_range_emas = getattr(self, "_monitor_runtime_h1_log_range_emas", {})
    if not isinstance(h1_log_range_emas, dict):
        return 0.0
    symbol_entry = h1_log_range_emas.get(symbol, {})
    if not isinstance(symbol_entry, dict):
        return 0.0
    try:
        span = float(self.bp(pside, "entry_volatility_ema_span_hours", symbol))
    except Exception:
        return 0.0
    try:
        return float(symbol_entry.get(span, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _monitor_trailing_extrema(bundle: dict[str, Any]) -> dict[str, float]:
    return normalize_trailing_extrema(bundle)


def _monitor_trailing_status(
    *,
    triggered: bool,
    threshold_met: bool,
    retracement_met: bool,
) -> str:
    if triggered:
        return "triggered"
    if not threshold_met:
        return "waiting_threshold"
    if not retracement_met:
        return "waiting_retracement"
    return "armed"


def _build_monitor_trailing_entry_payload(
    self,
    symbol: str,
    pside: str,
    *,
    balance_raw: float,
    current_price: float,
    position_size: float,
    position_price: float,
    trailing_bundle: dict[str, float],
    market_entry: dict[str, Any],
) -> Optional[dict[str, Any]]:
    ema_bands = market_entry.get("ema_bands", {}) if isinstance(market_entry, dict) else {}
    side_ema_bands = ema_bands.get(pside, {}) if isinstance(ema_bands, dict) else {}
    if not isinstance(side_ema_bands, dict):
        return None
    inputs = {
        "symbol": symbol,
        "pside": pside,
        "balance_raw": float(balance_raw),
        "current_price": float(current_price),
        "position_size": float(position_size),
        "position_price": float(position_price),
        "qty_step": float(self.qty_steps[symbol]),
        "price_step": float(self.price_steps[symbol]),
        "min_qty": float(self.min_qtys[symbol]),
        "min_cost": float(
            max(
                getattr(self, "effective_min_cost", {}).get(symbol, 0.0) or 0.0,
                getattr(self, "min_costs", {}).get(symbol, 0.0) or 0.0,
            )
        ),
        "c_mult": float(self.c_mults[symbol]),
        "ema_lower": float(side_ema_bands.get("lower", 0.0) or 0.0),
        "ema_upper": float(side_ema_bands.get("upper", 0.0) or 0.0),
        "h1_log_range_ema": float(_monitor_h1_entry_logrange(self, pside, symbol)),
        **dict(trailing_bundle),
    }
    for key in (
        "entry_grid_double_down_factor",
        "entry_grid_spacing_volatility_weight",
        "entry_grid_spacing_we_weight",
        "entry_grid_spacing_pct",
        "entry_initial_ema_dist",
        "entry_initial_qty_pct",
        "entry_trailing_double_down_factor",
        "entry_trailing_grid_ratio",
        "entry_trailing_retracement_pct",
        "entry_trailing_retracement_we_weight",
        "entry_trailing_retracement_volatility_weight",
        "entry_trailing_threshold_pct",
        "entry_trailing_threshold_we_weight",
        "entry_trailing_threshold_volatility_weight",
        "wallet_exposure_limit",
        "risk_we_excess_allowance_pct",
    ):
        inputs[key] = float(self.bp(pside, key, symbol))
    payload = build_trailing_entry_diagnostic(inputs)
    if payload is None:
        return None
    return payload


def _build_monitor_trailing_close_payload(
    self,
    symbol: str,
    pside: str,
    *,
    balance_raw: float,
    current_price: float,
    position_size: float,
    position_price: float,
    trailing_bundle: dict[str, float],
) -> Optional[dict[str, Any]]:
    inputs = {
        "symbol": symbol,
        "pside": pside,
        "balance_raw": float(balance_raw),
        "current_price": float(current_price),
        "position_size": float(position_size),
        "position_price": float(position_price),
        "qty_step": float(self.qty_steps[symbol]),
        "price_step": float(self.price_steps[symbol]),
        "min_qty": float(self.min_qtys[symbol]),
        "min_cost": float(
            max(
                getattr(self, "effective_min_cost", {}).get(symbol, 0.0) or 0.0,
                getattr(self, "min_costs", {}).get(symbol, 0.0) or 0.0,
            )
        ),
        "c_mult": float(self.c_mults[symbol]),
        **dict(trailing_bundle),
    }
    for key in (
        "close_grid_markup_end",
        "close_grid_markup_start",
        "close_grid_qty_pct",
        "close_trailing_grid_ratio",
        "close_trailing_qty_pct",
        "close_trailing_retracement_pct",
        "close_trailing_threshold_pct",
        "wallet_exposure_limit",
        "risk_we_excess_allowance_pct",
        "risk_wel_enforcer_threshold",
    ):
        inputs[key] = float(self.bp(pside, key, symbol))
    payload = build_trailing_close_diagnostic(inputs)
    if payload is None:
        return None
    return payload


def _build_monitor_trailing_section(
    self,
    *,
    balance_raw: float,
    market: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for symbol, market_entry in sorted(market.items()):
        if not isinstance(market_entry, dict):
            continue
        last_price_raw = market_entry.get("last_price")
        try:
            current_price = float(last_price_raw)
        except (TypeError, ValueError):
            continue
        if current_price <= 0.0:
            continue
        trailing_entry = market_entry.get("trailing", {}) if isinstance(market_entry.get("trailing"), dict) else {}
        pos_entry = self.positions.get(symbol, {}) if isinstance(self.positions.get(symbol), dict) else {}
        symbol_payload: dict[str, Any] = {}
        for pside in ("long", "short"):
            trailing_bundle_raw = trailing_entry.get(pside, {}) if isinstance(trailing_entry.get(pside), dict) else {}
            trailing_bundle = _monitor_trailing_extrema(trailing_bundle_raw)
            pos = pos_entry.get(pside, {}) if isinstance(pos_entry.get(pside), dict) else {}
            position_size = float(pos.get("size", 0.0) or 0.0)
            position_price = float(pos.get("price", 0.0) or 0.0)
            side_payload: dict[str, Any] = {"extrema": dict(trailing_bundle)}
            entry_payload = _build_monitor_trailing_entry_payload(
                self,
                symbol,
                pside,
                balance_raw=balance_raw,
                current_price=current_price,
                position_size=position_size,
                position_price=position_price,
                trailing_bundle=trailing_bundle,
                market_entry=market_entry,
            )
            if entry_payload is not None:
                side_payload["entry"] = entry_payload
            close_payload = _build_monitor_trailing_close_payload(
                self,
                symbol,
                pside,
                balance_raw=balance_raw,
                current_price=current_price,
                position_size=position_size,
                position_price=position_price,
                trailing_bundle=trailing_bundle,
            )
            if close_payload is not None:
                side_payload["close"] = close_payload
            if "entry" in side_payload or "close" in side_payload:
                symbol_payload[pside] = side_payload
        if symbol_payload:
            out[symbol] = symbol_payload
    return out


def _build_monitor_position_side_payload(
    self,
    symbol: str,
    pside: str,
    pos: dict[str, Any],
    *,
    balance_raw: float,
    last_price: Optional[float],
    total_we_by_pside: dict[str, float],
) -> dict[str, Any]:
    size = float(pos.get("size", 0.0) or 0.0)
    price = float(pos.get("price", 0.0) or 0.0)
    wallet_exposure = 0.0
    if size != 0.0 and balance_raw > 0.0 and symbol in self.c_mults:
        wallet_exposure = float(pbr.qty_to_cost(size, price, self.c_mults[symbol]) / balance_raw)
    wel = float(self.bp(pside, "wallet_exposure_limit", symbol))
    allowance_pct = float(self.bp(pside, "risk_we_excess_allowance_pct", symbol))
    effective_wel = wel * (1.0 + max(0.0, allowance_pct))
    twel = float(self.bot_value(pside, "total_wallet_exposure_limit") or 0.0)

    payload: dict[str, Any] = {
        "size": size,
        "price": price,
        "wallet_exposure": wallet_exposure,
        "wallet_exposure_limit": wel,
        "effective_wallet_exposure_limit": effective_wel,
        "wel_ratio": wallet_exposure / wel if wel > 0.0 else 0.0,
        "wele_ratio": wallet_exposure / effective_wel if effective_wel > 0.0 else 0.0,
        "total_wallet_exposure": float(total_we_by_pside.get(pside, 0.0) or 0.0),
        "total_wallet_exposure_limit": twel,
        "twel_ratio": float(total_we_by_pside.get(pside, 0.0) or 0.0) / twel if twel > 0.0 else 0.0,
    }
    if last_price is None:
        return payload

    payload["last_price"] = float(last_price)
    if size == 0.0 or price == 0.0 or symbol not in self.c_mults:
        payload["price_action_distance"] = 0.0
        payload["upnl"] = 0.0
        return payload
    payload["price_action_distance"] = float(
        pbr.calc_pprice_diff_int(self.pside_int_map[pside], price, last_price)
    )
    payload["upnl"] = float(_calc_monitor_pnl(pside, price, last_price, size, self.c_mults[symbol]))
    return payload


def _build_monitor_positions_section(
    self,
    *,
    balance_raw: float,
    market: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    total_we_by_pside = {"long": 0.0, "short": 0.0}
    for symbol, pos in self.positions.items():
        for pside in ("long", "short"):
            side_pos = pos.get(pside, {})
            if not isinstance(side_pos, dict):
                continue
            size = float(side_pos.get("size", 0.0) or 0.0)
            price = float(side_pos.get("price", 0.0) or 0.0)
            if size != 0.0 and balance_raw > 0.0 and symbol in self.c_mults:
                total_we_by_pside[pside] += pbr.qty_to_cost(size, price, self.c_mults[symbol]) / balance_raw

    for symbol in sorted(self.positions):
        pos = self.positions[symbol]
        market_entry = market.get(symbol, {}) if isinstance(market.get(symbol), dict) else {}
        last_price_raw = market_entry.get("last_price")
        last_price = float(last_price_raw) if last_price_raw is not None else None
        long_pos = pos.get("long", {}) if isinstance(pos.get("long"), dict) else {}
        short_pos = pos.get("short", {}) if isinstance(pos.get("short"), dict) else {}
        positions[symbol] = {
            "long": self._build_monitor_position_side_payload(
                symbol,
                "long",
                long_pos,
                balance_raw=balance_raw,
                last_price=last_price,
                total_we_by_pside=total_we_by_pside,
            ),
            "short": self._build_monitor_position_side_payload(
                symbol,
                "short",
                short_pos,
                balance_raw=balance_raw,
                last_price=last_price,
                total_we_by_pside=total_we_by_pside,
            ),
        }
    return positions


async def _build_monitor_snapshot(self, *, now_ms: Optional[int] = None) -> dict:
    now_ms = utc_ms() if now_ms is None else int(now_ms)
    balance_raw = float(self.get_raw_balance())
    balance_snapped = float(self.get_hysteresis_snapped_balance())
    equity = float(getattr(self, "_monitor_last_equity", balance_raw) or balance_raw)
    if abs(equity) < 1e-18 and balance_raw != 0.0:
        equity = balance_raw
    account = {
        "balance_raw": balance_raw,
        "balance_snapped": balance_snapped,
        "equity": equity,
    }
    try:
        account["realized_pnl_cumsum"] = {
            "current": float(self._equity_hard_stop_realized_pnl_now()),
        }
    except Exception:
        pass
    market = self._build_monitor_market_section()
    positions = self._build_monitor_positions_section(balance_raw=balance_raw, market=market)

    open_orders: dict[str, list[dict]] = {}
    for symbol in sorted(self.open_orders):
        symbol_orders = []
        for order in self.open_orders.get(symbol, []):
            try:
                symbol_orders.append(
                    {
                        "id": order.get("id"),
                        "custom_id": order.get("custom_id"),
                        "side": order["side"],
                        "position_side": order["position_side"],
                        "qty": abs(float(order["qty"])),
                        "price": float(order["price"]),
                        "reduce_only": bool(
                            (order["position_side"] == "long" and order["side"] == "sell")
                            or (order["position_side"] == "short" and order["side"] == "buy")
                        ),
                        "pb_order_type": self._resolve_pb_order_type(order),
                    }
                )
            except Exception:
                continue
        open_orders[symbol] = symbol_orders

    return {
        "meta": {
            "exchange": self.exchange,
            "user": self.user,
            "quote": self.quote,
            "pid": os.getpid(),
            "bot_start_ts_ms": self.start_time_ms,
            "current_cycle_ts_ms": now_ms,
        },
        "account": account,
        "health": self._build_health_summary_payload(now_ms=now_ms),
        "positions": positions,
        "open_orders": open_orders,
        "modes": {
            "effective": {
                "long": dict(self.PB_modes.get("long", {})),
                "short": dict(self.PB_modes.get("short", {})),
            },
            "runtime_forced": {
                "long": dict(self._runtime_forced_modes.get("long", {})),
                "short": dict(self._runtime_forced_modes.get("short", {})),
            },
        },
        "hsl": {pside: self._monitor_hsl_payload(pside) for pside in ("long", "short")},
        "market": market,
        "trailing": self._build_monitor_trailing_section(balance_raw=balance_raw, market=market),
        "forager": await self._build_monitor_forager_section(),
        "unstuck": self._build_monitor_unstuck_section(),
        "recent": self._build_monitor_recent_section(),
    }


async def _monitor_flush_snapshot(self, *, force: bool = False, ts: Optional[int] = None) -> bool:
    publisher = getattr(self, "monitor_publisher", None)
    if publisher is None:
        return False
    try:
        snapshot = await self._build_monitor_snapshot(now_ms=ts)
        self._monitor_record_runtime_observations(snapshot, ts=ts)
        _monitor_record_paper_reconciliation(self, ts=ts)
        return publisher.write_snapshot(snapshot, ts=ts, force=force)
    except Exception as exc:
        logging.error("[monitor] failed building monitor snapshot: %s", exc)
        return False
