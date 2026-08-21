"""Strict Bitget public market surfaces used by the live risk boundary.

The exchange returns instrument rules, prices, funding and maintenance tiers
through separate endpoints.  This module joins those responses into one
immutable-in-practice record.  Missing fields are errors: a risk calculation
must never quietly replace an exchange value with a local default.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


SURFACE_SCHEMA_VERSION = "bitget-market-surface-v1"


class BitgetMarketSurfaceError(ValueError):
    """Raised when a Bitget public response cannot form a safe surface."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _number(
    value: Any,
    *,
    field: str,
    positive: bool = False,
    nonnegative: bool = False,
) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise BitgetMarketSurfaceError(f"{field} is required")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BitgetMarketSurfaceError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise BitgetMarketSurfaceError(f"{field} must be finite")
    if positive and result <= 0.0:
        raise BitgetMarketSurfaceError(f"{field} must be positive")
    if nonnegative and result < 0.0:
        raise BitgetMarketSurfaceError(f"{field} must be non-negative")
    return result


_INT64_MIN = -(2**63)
_INT64_MAX = (2**63) - 1


def _integer(value: Any, *, field: str, positive: bool = False) -> int:
    """Parse an exact integer. Floats, bools, and rounded timestamps are errors."""

    if isinstance(value, bool) or value in (None, ""):
        raise BitgetMarketSurfaceError(f"{field} is required")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        digits = value[1:] if value.startswith("-") else value
        if not digits.isdigit():
            raise BitgetMarketSurfaceError(f"{field} must be an integer")
        result = int(value)
    else:
        raise BitgetMarketSurfaceError(f"{field} must be an integer")
    if result < _INT64_MIN or result > _INT64_MAX:
        raise BitgetMarketSurfaceError(
            f"{field} overflows signed 64-bit integer range"
        )
    if positive and result <= 0:
        raise BitgetMarketSurfaceError(f"{field} must be positive")
    return result


def _response_rows(response: Any, *, endpoint: str) -> list[Mapping[str, Any]]:
    if not isinstance(response, Mapping):
        raise BitgetMarketSurfaceError(
            f"{endpoint} response must be an object"
        )
    code = response.get("code")
    if code not in (None, "00000", 0, "0"):
        raise BitgetMarketSurfaceError(
            f"{endpoint} returned exchange code {code!r}"
        )
    rows = response.get("data")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise BitgetMarketSurfaceError(f"{endpoint}.data must be a list")
    result = [row for row in rows if isinstance(row, Mapping)]
    if len(result) != len(rows):
        raise BitgetMarketSurfaceError(
            f"{endpoint}.data contains a non-object row"
        )
    if not result:
        raise BitgetMarketSurfaceError(f"{endpoint}.data is empty")
    return result


def _select_symbol_row(
    response: Any,
    *,
    endpoint: str,
    exchange_symbol: str,
) -> Mapping[str, Any]:
    rows = _response_rows(response, endpoint=endpoint)
    matches = [
        row
        for row in rows
        if str(row.get("symbol") or "").upper() == str(exchange_symbol).upper()
    ]
    if len(matches) != 1:
        raise BitgetMarketSurfaceError(
            f"{endpoint} must contain exactly one row for {exchange_symbol!r}; "
            f"matches={len(matches)}"
        )
    return matches[0]


def _normalize_tiers(
    response: Any,
    *,
    endpoint: str,
    internal_symbol: str,
) -> list[dict[str, Any]]:
    rows = _response_rows(response, endpoint=endpoint)
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        tier = str(row.get("tier") or row.get("tierId") or "").strip()
        if not tier:
            raise BitgetMarketSurfaceError(f"{endpoint}.data[{index}].tier is required")
        minimum = _number(
            row.get("minTierValue", row.get("min_notional")),
            field=f"{endpoint}.data[{index}].minTierValue",
            nonnegative=True,
        )
        maximum = _number(
            row.get("maxTierValue", row.get("max_notional")),
            field=f"{endpoint}.data[{index}].maxTierValue",
            positive=True,
        )
        if maximum <= minimum:
            raise BitgetMarketSurfaceError(
                f"{endpoint}.data[{index}] has maxTierValue <= minTierValue"
            )
        maintenance = _number(
            row.get("mmr", row.get("maintenance_margin_rate")),
            field=f"{endpoint}.data[{index}].mmr",
            nonnegative=True,
        )
        if maintenance >= 1.0:
            raise BitgetMarketSurfaceError(
                f"{endpoint}.data[{index}].mmr must be below 1"
            )
        leverage = _number(
            row.get("leverage", row.get("max_leverage")),
            field=f"{endpoint}.data[{index}].leverage",
            positive=True,
        )
        normalized.append(
            {
                "symbol": internal_symbol,
                "tier": tier,
                "min_notional": minimum,
                "max_notional": maximum,
                "maintenance_margin_rate": maintenance,
                "max_leverage": leverage,
            }
        )
    normalized.sort(key=lambda item: (item["min_notional"], item["max_notional"], item["tier"]))
    if normalized[0]["min_notional"] != 0.0:
        raise BitgetMarketSurfaceError(
            f"{endpoint} tiers must start at notional zero"
        )
    for previous, current in zip(normalized, normalized[1:]):
        if current["min_notional"] != previous["max_notional"]:
            raise BitgetMarketSurfaceError(
                f"{endpoint} tiers contain a gap or overlap between "
                f"{previous['tier']!r} and {current['tier']!r}"
            )
    return normalized


def normalize_bitget_public_market_surface(
    *,
    internal_symbol: str,
    exchange_symbol: str,
    category: str,
    instrument_response: Mapping[str, Any],
    tier_response: Mapping[str, Any],
    ticker_response: Mapping[str, Any],
    funding_response: Mapping[str, Any],
    contract_size: Any,
) -> dict[str, Any]:
    """Normalize four raw Bitget public responses into one strict surface."""

    if not str(internal_symbol).strip():
        raise BitgetMarketSurfaceError("internal_symbol is required")
    if not str(exchange_symbol).strip():
        raise BitgetMarketSurfaceError("exchange_symbol is required")
    if not str(category).strip():
        raise BitgetMarketSurfaceError("category is required")
    instrument = _select_symbol_row(
        instrument_response,
        endpoint="market_instruments",
        exchange_symbol=exchange_symbol,
    )
    ticker = _select_symbol_row(
        ticker_response,
        endpoint="market_tickers",
        exchange_symbol=exchange_symbol,
    )
    funding = _select_symbol_row(
        funding_response,
        endpoint="market_current_fund_rate",
        exchange_symbol=exchange_symbol,
    )

    if str(instrument.get("status") or "").lower() != "online":
        raise BitgetMarketSurfaceError(
            f"market_instruments.{exchange_symbol}.status is not online"
        )
    if str(instrument.get("type") or "").lower() != "perpetual":
        raise BitgetMarketSurfaceError(
            f"market_instruments.{exchange_symbol}.type is not perpetual"
        )
    if str(instrument.get("category") or category) != str(category):
        raise BitgetMarketSurfaceError(
            f"market_instruments.{exchange_symbol}.category does not match {category!r}"
        )

    maker_fee = _number(
        instrument.get("makerFeeRate"),
        field="market_instruments.makerFeeRate",
        nonnegative=True,
    )
    taker_fee = _number(
        instrument.get("takerFeeRate"),
        field="market_instruments.takerFeeRate",
        nonnegative=True,
    )
    if maker_fee >= 1.0 or taker_fee >= 1.0:
        raise BitgetMarketSurfaceError("Bitget fee rates must be below 1")
    min_order_amount = _number(
        instrument.get("minOrderAmount"),
        field="market_instruments.minOrderAmount",
        positive=True,
    )
    min_order_qty = _number(
        instrument.get("minOrderQty"),
        field="market_instruments.minOrderQty",
        positive=True,
    )
    price_step = _number(
        instrument.get("priceMultiplier"),
        field="market_instruments.priceMultiplier",
        positive=True,
    )
    qty_step = _number(
        instrument.get("quantityMultiplier"),
        field="market_instruments.quantityMultiplier",
        positive=True,
    )
    instrument_funding_interval = _number(
        instrument.get("fundInterval"),
        field="market_instruments.fundInterval",
        positive=True,
    )
    max_leverage = _number(
        instrument.get("maxLeverage"),
        field="market_instruments.maxLeverage",
        positive=True,
    )
    explicit_contract_size = _number(
        contract_size,
        field="contract_size",
        positive=True,
    )
    mark_price = _number(ticker.get("markPrice"), field="market_tickers.markPrice", positive=True)
    index_price = _number(ticker.get("indexPrice"), field="market_tickers.indexPrice", positive=True)
    last_price = _number(ticker.get("lastPrice"), field="market_tickers.lastPrice", positive=True)
    bid_price = _number(ticker.get("bid1Price"), field="market_tickers.bid1Price", positive=True)
    ask_price = _number(ticker.get("ask1Price"), field="market_tickers.ask1Price", positive=True)
    if bid_price > ask_price:
        raise BitgetMarketSurfaceError("market_tickers bid1Price is above ask1Price")
    observed_at_ms = _integer(ticker.get("ts"), field="market_tickers.ts", positive=True)

    funding_rate = _number(
        funding.get("fundingRate"),
        field="market_current_fund_rate.fundingRate",
    )
    funding_interval = _number(
        funding.get("fundingRateInterval"),
        field="market_current_fund_rate.fundingRateInterval",
        positive=True,
    )
    minimum_funding_rate = _number(
        funding.get("minFundingRate"),
        field="market_current_fund_rate.minFundingRate",
    )
    maximum_funding_rate = _number(
        funding.get("maxFundingRate"),
        field="market_current_fund_rate.maxFundingRate",
    )
    if minimum_funding_rate > maximum_funding_rate:
        raise BitgetMarketSurfaceError("funding rate bounds are reversed")
    if not minimum_funding_rate <= funding_rate <= maximum_funding_rate:
        raise BitgetMarketSurfaceError("current funding rate is outside exchange bounds")
    next_funding_ms = _integer(
        funding.get("nextUpdate"),
        field="market_current_fund_rate.nextUpdate",
        positive=True,
    )
    if funding_interval != instrument_funding_interval:
        raise BitgetMarketSurfaceError(
            "instrument and current funding intervals do not match"
        )

    tiers = _normalize_tiers(
        tier_response,
        endpoint="market_position_tier",
        internal_symbol=str(internal_symbol),
    )
    risk_margin_tiers = [
        {
            "symbol": tier["symbol"],
            "tier": tier["tier"],
            "min_notional": tier["min_notional"],
            "max_notional": tier["max_notional"],
            "maintenance_margin_rate": tier["maintenance_margin_rate"],
        }
        for tier in tiers
    ]
    source_hashes = {
        "market_instruments": _sha256(instrument_response),
        "market_position_tier": _sha256(tier_response),
        "market_tickers": _sha256(ticker_response),
        "market_current_fund_rate": _sha256(funding_response),
    }
    surface: dict[str, Any] = {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "source": "bitget_public_uta_v3",
        "category": str(category),
        "symbol": str(internal_symbol),
        "exchange_symbol": str(exchange_symbol),
        "observed_at_ms": observed_at_ms,
        "source_response_sha256": source_hashes,
        "instrument": {
            "status": str(instrument["status"]),
            "type": str(instrument["type"]),
            "base_coin": str(instrument.get("baseCoin") or ""),
            "quote_coin": str(instrument.get("quoteCoin") or ""),
            "maker_fee": maker_fee,
            "taker_fee": taker_fee,
            "min_order_amount": min_order_amount,
            "min_order_qty": min_order_qty,
            "price_step": price_step,
            "qty_step": qty_step,
            "funding_interval_hours": instrument_funding_interval,
            "max_leverage": max_leverage,
            "contract_size": explicit_contract_size,
        },
        "maker_fee": maker_fee,
        "taker_fee": taker_fee,
        "min_order_amount": min_order_amount,
        "min_order_qty": min_order_qty,
        "price_step": price_step,
        "qty_step": qty_step,
        "contract_size": explicit_contract_size,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "last_price": last_price,
        "mark_price": mark_price,
        "index_price": index_price,
        "funding": {
            "rate": funding_rate,
            "interval_hours": funding_interval,
            "next_update_ms": next_funding_ms,
            "minimum_rate": minimum_funding_rate,
            "maximum_rate": maximum_funding_rate,
        },
        "funding_rate": funding_rate,
        "funding_interval_hours": funding_interval,
        "margin_tiers": tiers,
        # This is the canonical snapshot shape shared with Rust. The
        # exchange's maximum leverage is retained above for account/profile
        # validation but is not part of the liquidation-tier calculation.
        "risk_margin_tiers": risk_margin_tiers,
    }
    surface["surface_hash"] = _sha256(surface)
    return surface


__all__ = [
    "BitgetMarketSurfaceError",
    "SURFACE_SCHEMA_VERSION",
    "normalize_bitget_public_market_surface",
]
