"""Fail-closed paper mark/index, maintenance tiers, and execution evidence.

Qualifying paper cannot treat ticker ``last`` as mark or index, cannot assume
a hardcoded 0.5% maintenance rate, and cannot invent an optimistic fill.
Queue/depth may be recorded only from a fresh bound book; missing or stale
depth never becomes a silent full fill. Qualifying stays closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from research.risk_policy import select_maintenance_margin_rate

SURFACE_SCHEMA = "crypto-paper-market-surface-v1"
FILL_MODEL_SCHEMA = "crypto-paper-fill-model-v1"
EVIDENCE_SCHEMA = "crypto-paper-execution-evidence-v1"
BOOK_SCHEMA = "crypto-paper-order-book-v1"
FILL_MODEL_MODE = "explicit_amounts_only"
DEPTH_FILL_MODEL_MODE = "conservative_visible_depth"
DEFAULT_MAX_BOOK_AGE_MS = 5_000
_ALLOWED_FILL_MODEL_MODES = frozenset({FILL_MODEL_MODE, DEPTH_FILL_MODEL_MODE})

_MARK_KEYS = ("markPrice", "mark_price", "mark")
_INDEX_KEYS = ("indexPrice", "index_price", "index")
_LAST_KEYS = frozenset({"last", "lastPrice", "last_price", "close", "bid", "ask"})


class PaperMarketSurfaceError(ValueError):
    """Raised when paper cannot form a safe mark, index, or maintenance surface."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def sha256_path(path: str | os.PathLike[str]) -> str:
    resolved = Path(path).resolve()
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _required_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PaperMarketSurfaceError(f"{field} must be an object")
    return value


def _required_text(value: Any, *, field: str) -> str:
    if isinstance(value, bool) or value in (None, ""):
        raise PaperMarketSurfaceError(f"{field} is required")
    text = str(value).strip()
    if not text:
        raise PaperMarketSurfaceError(f"{field} is required")
    return text


def _required_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise PaperMarketSurfaceError(f"{field} must be a boolean")
    return value


def _finite_positive(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise PaperMarketSurfaceError(f"{field} is required")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PaperMarketSurfaceError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise PaperMarketSurfaceError(f"{field} must be finite and positive")
    return number


def _finite_non_negative(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise PaperMarketSurfaceError(f"{field} is required")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PaperMarketSurfaceError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise PaperMarketSurfaceError(f"{field} must be finite and non-negative")
    return number


def _required_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise PaperMarketSurfaceError(f"{field} is required")
    if type(value) is not int:
        raise PaperMarketSurfaceError(f"{field} must be an integer timestamp")
    return value


def _normalize_book_side(
    levels: Any,
    *,
    field: str,
    descending: bool,
) -> list[tuple[float, float]]:
    if not isinstance(levels, Sequence) or isinstance(levels, (str, bytes)):
        raise PaperMarketSurfaceError(f"{field} must be a list of [price, qty]")
    normalized: list[tuple[float, float]] = []
    for index, raw in enumerate(levels):
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) < 2:
            raise PaperMarketSurfaceError(f"{field}[{index}] must be [price, qty]")
        price = _finite_positive(raw[0], field=f"{field}[{index}].price")
        quantity = _finite_positive(raw[1], field=f"{field}[{index}].qty")
        normalized.append((price, quantity))
    normalized.sort(key=lambda item: item[0], reverse=descending)
    return normalized


def validate_order_book(payload: Any) -> dict[str, Any]:
    """Require an explicit public book. ``last`` cannot invent depth."""

    row = _required_mapping(payload, field="paper order book")
    symbol = _required_text(row.get("symbol"), field="order_book.symbol")
    timestamp = _required_int(row.get("timestamp"), field="order_book.timestamp")
    bids = _normalize_book_side(row.get("bids"), field="order_book.bids", descending=True)
    asks = _normalize_book_side(row.get("asks"), field="order_book.asks", descending=False)
    canonical = {
        "schema_version": BOOK_SCHEMA,
        "symbol": symbol,
        "timestamp": timestamp,
        "bids": [[price, quantity] for price, quantity in bids],
        "asks": [[price, quantity] for price, quantity in asks],
    }
    canonical["sha256"] = sha256_payload(canonical)
    return canonical


def book_age_ms(book: Mapping[str, Any], *, clock_ms: Any) -> int:
    timestamp = _required_int(book.get("timestamp"), field="order_book.timestamp")
    clock = _required_int(clock_ms, field="clock_ms")
    age = clock - timestamp
    if age < 0:
        raise PaperMarketSurfaceError("order book timestamp is in the future")
    return age


def require_fresh_book(
    payload: Any,
    *,
    clock_ms: Any,
    max_age_ms: Any = DEFAULT_MAX_BOOK_AGE_MS,
) -> dict[str, Any]:
    book = validate_order_book(payload)
    max_age = _required_int(max_age_ms, field="max_age_ms")
    if max_age <= 0:
        raise PaperMarketSurfaceError("max_age_ms must be a positive integer")
    age = book_age_ms(book, clock_ms=clock_ms)
    if age > max_age:
        raise PaperMarketSurfaceError(
            f"order book is stale: age_ms={age} max_age_ms={max_age}"
        )
    book["age_ms"] = age
    book["max_age_ms"] = max_age
    return book


def walk_visible_depth(
    payload: Any,
    *,
    side: str,
    quantity: Any,
    clock_ms: Any,
    max_age_ms: Any = DEFAULT_MAX_BOOK_AGE_MS,
) -> dict[str, Any]:
    """Fill only visible opposite-side depth. Never use ``last`` as a fill."""

    order_side = _required_text(side, field="side").lower()
    if order_side not in {"buy", "sell"}:
        raise PaperMarketSurfaceError("side must be buy or sell")
    want = _finite_positive(quantity, field="quantity")
    book = require_fresh_book(payload, clock_ms=clock_ms, max_age_ms=max_age_ms)
    levels = book["asks"] if order_side == "buy" else book["bids"]
    remaining = want
    parts: list[dict[str, float]] = []
    for price, available in levels:
        if remaining <= 0.0:
            break
        take = min(float(available), remaining)
        parts.append({"price": float(price), "quantity": take})
        remaining -= take
    filled = want - remaining
    if filled <= 0.0:
        raise PaperMarketSurfaceError(
            "visible depth is empty; refusing silent full fill"
        )
    vwap = sum(part["price"] * part["quantity"] for part in parts) / filled
    return {
        "symbol": book["symbol"],
        "side": order_side,
        "requested_quantity": want,
        "filled_quantity": filled,
        "remaining_quantity": remaining,
        "depth_exhausted": remaining > 1e-12,
        "vwap": vwap,
        "levels": parts,
        "book_sha256": book["sha256"],
        "book_timestamp": book["timestamp"],
        "queue_modeled": False,
        "depth_modeled": True,
    }


def queue_ahead_quantity(
    payload: Any,
    *,
    side: str,
    price: Any,
    clock_ms: Any,
    max_age_ms: Any = DEFAULT_MAX_BOOK_AGE_MS,
) -> dict[str, Any]:
    """Conservative queue: all equal-or-better visible size is ahead of us."""

    order_side = _required_text(side, field="side").lower()
    if order_side not in {"buy", "sell"}:
        raise PaperMarketSurfaceError("side must be buy or sell")
    limit_price = _finite_positive(price, field="price")
    book = require_fresh_book(payload, clock_ms=clock_ms, max_age_ms=max_age_ms)
    if order_side == "buy":
        ahead = sum(
            float(qty)
            for level_price, qty in book["bids"]
            if float(level_price) >= limit_price
        )
    else:
        ahead = sum(
            float(qty)
            for level_price, qty in book["asks"]
            if float(level_price) <= limit_price
        )
    return {
        "symbol": book["symbol"],
        "side": order_side,
        "price": limit_price,
        "queue_ahead": ahead,
        "book_sha256": book["sha256"],
        "book_timestamp": book["timestamp"],
        "queue_modeled": True,
        "depth_modeled": True,
    }


def consume_queue(
    *,
    queue_ahead: Any,
    aggressive_quantity: Any,
    order_remaining: Any,
) -> dict[str, float]:
    """Fill only after visible queue ahead is exhausted. No optimistic jump."""

    ahead = _finite_non_negative(queue_ahead, field="queue_ahead")
    aggressive = _finite_positive(aggressive_quantity, field="aggressive_quantity")
    remaining = _finite_non_negative(order_remaining, field="order_remaining")
    if aggressive <= ahead:
        return {
            "queue_ahead": ahead - aggressive,
            "fill_quantity": 0.0,
            "aggressive_remaining": 0.0,
        }
    leftover = aggressive - ahead
    fill = min(remaining, leftover)
    return {
        "queue_ahead": 0.0,
        "fill_quantity": fill,
        "aggressive_remaining": leftover - fill,
    }


def symbol_key(value: Any) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


def _lookup_named_price(
    payload: Mapping[str, Any],
    names: Sequence[str],
    *,
    field: str,
    required: bool,
    allow_last_fields: bool = False,
) -> tuple[float | None, str | None]:
    info = payload.get("info")
    sources: list[Mapping[str, Any]] = [payload]
    if isinstance(info, Mapping):
        sources.append(info)
    for source in sources:
        for name in names:
            if name not in source:
                continue
            if source[name] in (None, ""):
                continue
            if name in _LAST_KEYS and not allow_last_fields:
                raise PaperMarketSurfaceError(
                    f"{field} cannot be taken from last-price field {name}"
                )
            return _finite_positive(source[name], field=f"{field}.{name}"), name
    if required:
        raise PaperMarketSurfaceError(
            f"{field} is missing; last/bid/ask cannot substitute for it"
        )
    return None, None


def extract_mark_index(
    payload: Any,
    *,
    symbol: str,
    required: bool = True,
) -> dict[str, Any]:
    """Read named mark and index prices. ``last`` is never a substitute."""

    row = _required_mapping(payload, field=f"{symbol}.ticker")
    mark, mark_source = _lookup_named_price(
        row, _MARK_KEYS, field=f"{symbol}.mark", required=required
    )
    index, index_source = _lookup_named_price(
        row, _INDEX_KEYS, field=f"{symbol}.index", required=required
    )
    last, last_source = _lookup_named_price(
        row,
        ("last", "lastPrice", "last_price"),
        field=f"{symbol}.last",
        required=False,
        allow_last_fields=True,
    )
    if mark_source in _LAST_KEYS or index_source in _LAST_KEYS:
        raise PaperMarketSurfaceError(
            f"{symbol} mark/index source must not be a last-price field"
        )
    if last is not None and mark is not None and math.isclose(mark, last, rel_tol=0.0, abs_tol=0.0):
        # Equal values are allowed only when they arrived through different
        # named fields.  The source names still prove last was not substituted.
        pass
    return {
        "symbol": _required_text(symbol, field="symbol"),
        "mark_price": mark,
        "index_price": index,
        "last_price": last,
        "mark_source": mark_source,
        "index_source": index_source,
        "last_source": last_source,
        "last_substituted": False,
    }


def load_margin_tiers(profile: Any, *, symbol: str | None = None) -> list[dict[str, Any]]:
    """Return explicit maintenance tiers from a bound market/account surface."""

    row = _required_mapping(profile, field="paper market surface")
    if "margin_tiers" not in row:
        raise PaperMarketSurfaceError("margin_tiers are required for paper maintenance")
    raw_tiers = row["margin_tiers"]
    if not isinstance(raw_tiers, Sequence) or isinstance(raw_tiers, (str, bytes)):
        raise PaperMarketSurfaceError("margin_tiers must be a non-empty list")
    if not raw_tiers:
        raise PaperMarketSurfaceError("margin_tiers must be a non-empty list")
    expected = symbol_key(symbol) if symbol not in (None, "") else ""
    profile_symbol = row.get("symbol")
    if expected and profile_symbol not in (None, "") and symbol_key(profile_symbol) != expected:
        raise PaperMarketSurfaceError(
            f"market surface symbol {profile_symbol!r} does not match {symbol!r}"
        )
    tiers: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_tiers):
        item = _required_mapping(raw, field=f"margin_tiers[{index}]")
        if "maintenance_margin_rate" not in item and "mmr" not in item:
            raise PaperMarketSurfaceError(
                f"margin_tiers[{index}].maintenance_margin_rate is required"
            )
        rate_key = (
            "maintenance_margin_rate" if "maintenance_margin_rate" in item else "mmr"
        )
        rate = _finite_positive(
            item[rate_key], field=f"margin_tiers[{index}].maintenance_margin_rate"
        )
        if rate >= 1.0:
            raise PaperMarketSurfaceError(
                f"margin_tiers[{index}].maintenance_margin_rate must be < 1"
            )
        if "min_notional" not in item:
            raise PaperMarketSurfaceError(f"margin_tiers[{index}].min_notional is required")
        if "max_notional" not in item:
            raise PaperMarketSurfaceError(f"margin_tiers[{index}].max_notional is required")
        minimum = float(item["min_notional"])
        maximum = float(item["max_notional"])
        if not math.isfinite(minimum) or not math.isfinite(maximum) or maximum <= minimum:
            raise PaperMarketSurfaceError(
                f"margin_tiers[{index}] notional bounds are invalid"
            )
        if "tier" not in item and "tier_id" not in item:
            raise PaperMarketSurfaceError(f"margin_tiers[{index}].tier is required")
        tier_id = item["tier"] if "tier" in item else item["tier_id"]
        bound_symbol = item["symbol"] if "symbol" in item else profile_symbol  # noqa: SIM401
        if bound_symbol in (None, "") and symbol not in (None, ""):
            bound_symbol = symbol
        if bound_symbol in (None, ""):
            raise PaperMarketSurfaceError(f"margin_tiers[{index}].symbol is required")
        if expected and symbol_key(bound_symbol) != expected:
            continue
        tiers.append(
            {
                "symbol": str(bound_symbol),
                "tier": str(tier_id),
                "min_notional": minimum,
                "max_notional": maximum,
                "maintenance_margin_rate": rate,
            }
        )
    if not tiers:
        raise PaperMarketSurfaceError(
            f"margin_tiers contain no rows for {symbol or 'the bound surface'}"
        )
    return tiers


def maintenance_for_notional(
    tiers: Any,
    *,
    symbol: str,
    notional: float,
) -> dict[str, Any]:
    selected = select_maintenance_margin_rate(tiers, symbol=symbol, notional=notional)
    if selected is None:
        raise PaperMarketSurfaceError(
            f"no maintenance tier covers {symbol} notional {notional}"
        )
    rate, tier_id = selected
    return {
        "symbol": symbol,
        "notional": float(notional),
        "maintenance_margin_rate": float(rate),
        "tier_id": str(tier_id),
        "source": "bound_margin_tiers",
    }


def paper_fill_model(
    *,
    book: Mapping[str, Any] | None = None,
    clock_ms: Any | None = None,
    max_age_ms: Any = DEFAULT_MAX_BOOK_AGE_MS,
) -> dict[str, Any]:
    """Record fill-model honesty. Qualifying paper stays closed."""

    if book is None:
        return {
            "schema_version": FILL_MODEL_SCHEMA,
            "mode": FILL_MODEL_MODE,
            "queue_modeled": False,
            "depth_modeled": False,
            "partial_fills": "explicit_amounts_only",
            "qualifying": False,
            "book_stale": False,
            "note": (
                "paper does not infer Bitget queue position or order-book depth; "
                "only caller-supplied fill amounts are applied"
            ),
        }
    validated = validate_order_book(book)
    stale = True
    age: int | None = None
    if clock_ms is not None:
        try:
            fresh = require_fresh_book(
                validated, clock_ms=clock_ms, max_age_ms=max_age_ms
            )
            stale = False
            age = int(fresh["age_ms"])
        except PaperMarketSurfaceError:
            try:
                age = book_age_ms(validated, clock_ms=clock_ms)
            except PaperMarketSurfaceError:
                age = None
    if stale:
        return {
            "schema_version": FILL_MODEL_SCHEMA,
            "mode": FILL_MODEL_MODE,
            "queue_modeled": False,
            "depth_modeled": False,
            "partial_fills": "explicit_amounts_only",
            "qualifying": False,
            "book_stale": True,
            "book_sha256": validated["sha256"],
            "book_timestamp": validated["timestamp"],
            "book_age_ms": age,
            "note": (
                "a book is bound but not proven fresh; queue/depth stay unmodeled "
                "and silent full fills are refused"
            ),
        }
    return {
        "schema_version": FILL_MODEL_SCHEMA,
        "mode": DEPTH_FILL_MODEL_MODE,
        "queue_modeled": True,
        "depth_modeled": True,
        "partial_fills": "visible_depth_or_explicit_amounts",
        "qualifying": False,
        "book_stale": False,
        "book_sha256": validated["sha256"],
        "book_timestamp": validated["timestamp"],
        "book_age_ms": age,
        "max_age_ms": _required_int(max_age_ms, field="max_age_ms"),
        "note": (
            "conservative visible depth and queue-ahead only; not Bitget live "
            "queue; qualifying paper stays closed"
        ),
    }


def qualifying_blockers(
    *,
    mark_index: Mapping[str, Any] | None,
    tiers: Sequence[Mapping[str, Any]] | None,
    fill_model: Mapping[str, Any] | None = None,
) -> list[str]:
    blockers: list[str] = []
    model = fill_model if fill_model is not None else paper_fill_model()
    if not isinstance(model, Mapping):
        blockers.append("fill_model_missing")
    else:
        if model.get("queue_modeled") is not True:
            blockers.append("queue_not_modeled")
        if model.get("depth_modeled") is not True:
            blockers.append("depth_not_modeled")
        if model.get("mode") not in _ALLOWED_FILL_MODEL_MODES:
            blockers.append("fill_model_unknown")
        if model.get("book_stale") is True:
            blockers.append("book_stale")
        if model.get("qualifying") is True:
            blockers.append("fill_model_overclaims_qualifying")
    blockers.append("qualifying_calendar_incomplete")
    if not isinstance(mark_index, Mapping):
        blockers.append("mark_index_missing")
    else:
        if mark_index.get("mark_price") in (None, "") or mark_index.get("mark_source") in (
            None,
            "",
        ):
            blockers.append("mark_missing")
        if mark_index.get("index_price") in (None, "") or mark_index.get("index_source") in (
            None,
            "",
        ):
            blockers.append("index_missing")
        if mark_index.get("last_substituted") is True:
            blockers.append("last_substituted_for_mark")
        if mark_index.get("mark_source") in _LAST_KEYS:
            blockers.append("mark_source_is_last")
        if mark_index.get("index_source") in _LAST_KEYS:
            blockers.append("index_source_is_last")
    if not isinstance(tiers, Sequence) or isinstance(tiers, (str, bytes)) or not tiers:
        blockers.append("margin_tiers_missing")
    return blockers


def paper_surface_status(
    *,
    mark_index: Mapping[str, Any] | None = None,
    tiers: Sequence[Mapping[str, Any]] | None = None,
    fill_model: Mapping[str, Any] | None = None,
    input_hashes: Mapping[str, str] | None = None,
    gaps: Sequence[str] | None = None,
) -> dict[str, Any]:
    model = paper_fill_model() if fill_model is None else dict(fill_model)
    blockers = qualifying_blockers(
        mark_index=mark_index, tiers=tiers, fill_model=model
    )
    extra_gaps = [str(item) for item in (gaps or []) if str(item).strip()]
    return {
        "schema_version": SURFACE_SCHEMA,
        "qualifying": False,
        "qualifying_blockers": blockers,
        "fill_model": model,
        "mark_index": dict(mark_index) if isinstance(mark_index, Mapping) else None,
        "margin_tier_count": len(tiers) if isinstance(tiers, Sequence) and not isinstance(tiers, (str, bytes)) else 0,
        "input_hashes": dict(input_hashes or {}),
        "gaps": sorted(set(blockers + extra_gaps)),
    }


def build_paper_execution_evidence(
    *,
    symbol: str,
    mark_index: Mapping[str, Any] | None,
    tiers: Sequence[Mapping[str, Any]] | None,
    fill_model: Mapping[str, Any] | None = None,
    input_hashes: Mapping[str, str] | None = None,
    gaps: Sequence[str] | None = None,
    source_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    status = paper_surface_status(
        mark_index=mark_index,
        tiers=tiers,
        fill_model=fill_model,
        input_hashes=input_hashes,
        gaps=gaps,
    )
    evidence = {
        "schema_version": EVIDENCE_SCHEMA,
        "symbol": _required_text(symbol, field="symbol"),
        "qualifying": False,
        "qualifying_blockers": list(status["qualifying_blockers"]),
        "mark_index": status["mark_index"],
        "maintenance": {
            "source": "bound_margin_tiers" if status["margin_tier_count"] else None,
            "tier_count": status["margin_tier_count"],
            "hardcoded_half_percent": False,
        },
        "fill_model": status["fill_model"],
        "input_hashes": status["input_hashes"],
        "source_paths": dict(source_paths or {}),
        "gaps": list(status["gaps"]),
    }
    evidence["evidence_sha256"] = sha256_payload(evidence)
    return evidence


def validate_paper_execution_evidence(payload: Any) -> dict[str, Any]:
    row = _required_mapping(payload, field="paper execution evidence")
    if row.get("schema_version") != EVIDENCE_SCHEMA:
        raise PaperMarketSurfaceError(
            f"paper execution evidence schema must be {EVIDENCE_SCHEMA}"
        )
    if row.get("qualifying") is True:
        raise PaperMarketSurfaceError(
            "paper execution evidence cannot claim qualifying=true"
        )
    _required_bool(row.get("qualifying"), field="qualifying")
    fill_model = _required_mapping(row.get("fill_model"), field="fill_model")
    if fill_model.get("qualifying") is True:
        raise PaperMarketSurfaceError("paper fill model cannot claim qualifying=true")
    mode = fill_model.get("mode")
    if mode not in _ALLOWED_FILL_MODEL_MODES:
        raise PaperMarketSurfaceError(
            "paper fill model mode must be "
            f"{FILL_MODEL_MODE} or {DEPTH_FILL_MODEL_MODE}"
        )
    queue_modeled = fill_model.get("queue_modeled") is True
    depth_modeled = fill_model.get("depth_modeled") is True
    if queue_modeled or depth_modeled:
        if mode != DEPTH_FILL_MODEL_MODE:
            raise PaperMarketSurfaceError(
                "paper fill model overclaims queue/depth calibration"
            )
        if fill_model.get("book_sha256") in (None, ""):
            raise PaperMarketSurfaceError(
                "paper fill model overclaims queue/depth without a bound book"
            )
        if fill_model.get("book_stale") is True:
            raise PaperMarketSurfaceError(
                "paper fill model overclaims queue/depth on a stale book"
            )
    mark_index = row.get("mark_index")
    if mark_index is not None:
        mark_row = _required_mapping(mark_index, field="mark_index")
        if mark_row.get("last_substituted") is True:
            raise PaperMarketSurfaceError("paper evidence records last-as-mark substitution")
        if mark_row.get("mark_source") in _LAST_KEYS or mark_row.get("index_source") in _LAST_KEYS:
            raise PaperMarketSurfaceError("paper evidence mark/index source is last")
    maintenance = _required_mapping(row.get("maintenance"), field="maintenance")
    if maintenance.get("hardcoded_half_percent") is True:
        raise PaperMarketSurfaceError("paper evidence still uses hardcoded 0.5% maintenance")
    expected = dict(row)
    expected.pop("evidence_sha256", None)
    digest = sha256_payload(expected)
    if "evidence_sha256" not in row:
        raise PaperMarketSurfaceError("evidence_sha256 is required")
    if str(row["evidence_sha256"]) != digest:
        raise PaperMarketSurfaceError("evidence_sha256 does not match payload")
    return dict(row)


def write_paper_execution_evidence(
    path: str | os.PathLike[str],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    validated = validate_paper_execution_evidence(evidence)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(encoded, encoding="utf-8")
    os.replace(tmp, target)
    return validated


def load_paper_market_profile(path: str | os.PathLike[str]) -> dict[str, Any]:
    resolved = Path(path).resolve()
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    row = _required_mapping(raw, field=str(resolved))
    symbol = _required_text(row.get("symbol"), field="symbol")
    tiers = load_margin_tiers(row, symbol=symbol)
    return {
        "path": str(resolved),
        "path_sha256": sha256_path(resolved),
        "symbol": symbol,
        "schema_version": row.get("schema_version"),
        "margin_tiers": tiers,
        "source_response_sha256": (
            dict(row["source_response_sha256"])
            if isinstance(row.get("source_response_sha256"), Mapping)
            else {}
        ),
    }


__all__ = [
    "BOOK_SCHEMA",
    "DEFAULT_MAX_BOOK_AGE_MS",
    "DEPTH_FILL_MODEL_MODE",
    "EVIDENCE_SCHEMA",
    "FILL_MODEL_MODE",
    "FILL_MODEL_SCHEMA",
    "SURFACE_SCHEMA",
    "PaperMarketSurfaceError",
    "book_age_ms",
    "build_paper_execution_evidence",
    "consume_queue",
    "extract_mark_index",
    "load_margin_tiers",
    "load_paper_market_profile",
    "maintenance_for_notional",
    "paper_fill_model",
    "paper_surface_status",
    "qualifying_blockers",
    "queue_ahead_quantity",
    "require_fresh_book",
    "sha256_path",
    "sha256_payload",
    "symbol_key",
    "validate_order_book",
    "validate_paper_execution_evidence",
    "walk_visible_depth",
    "write_paper_execution_evidence",
]
