"""Build a normalized public Bitget market profile from saved public inputs.

This command never downloads data and never reads credentials.  It converts a
``bitget-public-execution-inputs-v1`` artifact into the smaller public market
profile consumed by historical preflight and risk checks.  The source
artifact's response hashes are preserved, and account/private fields are not
introduced.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


INPUT_SCHEMA_VERSION = "bitget-public-execution-inputs-v1"
PROFILE_SCHEMA_VERSION = "bitget-public-market-profile-v1"


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


def _required_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _required_rows(snapshot: Mapping[str, Any], *, name: str) -> list[Mapping[str, Any]]:
    rows = snapshot.get("data")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError(f"public snapshot {name} has no object rows")
    return [row for row in rows if isinstance(row, Mapping)]


def _select_symbol(rows: list[Mapping[str, Any]], *, symbol: str, name: str) -> Mapping[str, Any]:
    matches = [row for row in rows if str(row.get("symbol", "")).upper() == symbol.upper()]
    if len(matches) != 1:
        raise ValueError(f"public snapshot {name} must contain exactly one {symbol} row")
    return matches[0]


def _float(value: Any, *, name: str, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if number != number or abs(number) == float("inf") or (positive and number <= 0.0):
        requirement = "finite and positive" if positive else "finite"
        raise ValueError(f"{name} must be {requirement}")
    return number


def _int(value: Any, *, name: str, positive: bool = False) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if positive and number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def _fixed_collected_at_utc(value: Any) -> str:
    """Require an explicit, timezone-aware UTC collection timestamp."""

    if value in (None, ""):
        raise ValueError(
            "public execution inputs must provide collected_at_utc; "
            "the current time fallback is not allowed"
        )
    text = str(value).strip()
    if not text:
        raise ValueError(
            "public execution inputs must provide collected_at_utc; "
            "the current time fallback is not allowed"
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("collected_at_utc must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("collected_at_utc must be timezone-aware UTC")
    return text


def build_public_market_profile(execution_inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one saved public execution-input artifact."""

    if execution_inputs.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise ValueError("unsupported public execution-input schema")
    if str(execution_inputs.get("exchange", "")).lower() != "bitget":
        raise ValueError("public market profile supports Bitget inputs only")
    if execution_inputs.get("read_only_only") is not True:
        raise ValueError("public execution inputs must be read-only")
    if execution_inputs.get("trading_operations_count") != 0:
        raise ValueError("public execution inputs contain trading operations")
    symbol = str(execution_inputs.get("symbol", "")).strip().upper()
    if not symbol:
        raise ValueError("public execution inputs have no symbol")

    snapshots = _required_mapping(execution_inputs.get("snapshots"), name="snapshots")
    instrument = _select_symbol(
        _required_rows(_required_mapping(snapshots.get("instruments"), name="instruments"), name="instruments"),
        symbol=symbol,
        name="instruments",
    )
    ticker = _select_symbol(
        _required_rows(_required_mapping(snapshots.get("tickers"), name="tickers"), name="tickers"),
        symbol=symbol,
        name="tickers",
    )
    current_funding = _select_symbol(
        _required_rows(
            _required_mapping(snapshots.get("current_funding"), name="current_funding"),
            name="current_funding",
        ),
        symbol=symbol,
        name="current_funding",
    )
    tiers = _select_tiers(
        _required_rows(_required_mapping(snapshots.get("position_tiers"), name="position_tiers"), name="position_tiers"),
        symbol=symbol,
    )
    response_hashes = execution_inputs.get("response_sha256")
    if not isinstance(response_hashes, Mapping) or not response_hashes:
        raise ValueError("public execution inputs have no response hashes")

    profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "plan_date": "2026-08-17",
        "exchange": "bitget",
        "symbol": symbol,
        "category": execution_inputs.get("category"),
        "collected_at_utc": _fixed_collected_at_utc(
            execution_inputs.get("collected_at_utc")
        ),
        "read_only_only": True,
        "trading_operations_count": 0,
        "instrument": {
            "status": str(instrument.get("status", "")),
            "contract_type": str(instrument.get("type", "")),
            "base_coin": str(instrument.get("baseCoin", "")),
            "quote_coin": str(instrument.get("quoteCoin", "")),
            "maker_fee_rate": _float(instrument.get("makerFeeRate"), name="makerFeeRate"),
            "taker_fee_rate": _float(instrument.get("takerFeeRate"), name="takerFeeRate"),
            "funding_interval_hours": _int(instrument.get("fundInterval"), name="fundInterval", positive=True),
            "max_leverage": _float(instrument.get("maxLeverage"), name="maxLeverage", positive=True),
            "min_order_amount": _float(instrument.get("minOrderAmount"), name="minOrderAmount", positive=True),
            "min_order_qty": _float(instrument.get("minOrderQty"), name="minOrderQty", positive=True),
            "price_multiplier": _float(instrument.get("priceMultiplier"), name="priceMultiplier", positive=True),
            "price_precision": _int(instrument.get("pricePrecision"), name="pricePrecision"),
            "quantity_multiplier": _float(instrument.get("quantityMultiplier"), name="quantityMultiplier", positive=True),
            "quantity_precision": _int(instrument.get("quantityPrecision"), name="quantityPrecision"),
        },
        "mark_and_index": {
            "observed_at_ms": _int(ticker.get("ts"), name="ticker.ts", positive=True),
            "mark_price": _float(ticker.get("markPrice"), name="ticker.markPrice", positive=True),
            "index_price": _float(ticker.get("indexPrice"), name="ticker.indexPrice", positive=True),
            "bid_price": _float(ticker.get("bid1Price"), name="ticker.bid1Price", positive=True),
            "ask_price": _float(ticker.get("ask1Price"), name="ticker.ask1Price", positive=True),
        },
        "funding": {
            "current_rate": _float(
                current_funding.get("fundingRate"), name="current_funding.fundingRate"
            ),
            "interval_hours": _int(
                current_funding.get("fundingRateInterval"),
                name="current_funding.fundingRateInterval",
                positive=True,
            ),
            "next_settlement_at_ms": _int(
                current_funding.get("nextUpdate"),
                name="current_funding.nextUpdate",
                positive=True,
            ),
        },
        "margin_tiers": tiers,
        "source_response_sha256": {str(key): str(value) for key, value in response_hashes.items()},
        "source_execution_inputs_sha256": _sha256(execution_inputs),
        "source_execution_inputs_schema": INPUT_SCHEMA_VERSION,
        "source_documentation": execution_inputs.get("documentation", {}),
    }
    if profile["instrument"]["base_coin"].upper() != symbol.removesuffix("USDT"):
        raise ValueError("instrument base coin does not match symbol")
    if profile["instrument"]["quote_coin"].upper() != "USDT":
        raise ValueError("public market profile requires USDT quote")
    return profile


def _select_tiers(rows: list[Mapping[str, Any]], *, symbol: str) -> list[dict[str, Any]]:
    # Position-tier responses are symbol-scoped by the request, but accepting
    # an optional symbol field makes mixed artifacts fail closed rather than
    # silently combining tiers from another market.
    filtered = [row for row in rows if row.get("symbol") in (None, "", symbol)]
    if not filtered:
        raise ValueError(f"public snapshot position_tiers has no {symbol} rows")
    result = []
    for row in filtered:
        result.append(
            {
                "tier": str(row.get("tier")),
                "min_notional": _float(row.get("minTierValue"), name="minTierValue"),
                "max_notional": _float(row.get("maxTierValue"), name="maxTierValue", positive=True),
                "maintenance_margin_rate": _float(row.get("mmr"), name="mmr", positive=True),
                "max_leverage": _float(row.get("leverage"), name="leverage", positive=True),
            }
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.execution_inputs.expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("execution inputs JSON root must be an object")
    profile = build_public_market_profile(value)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "schema_version": PROFILE_SCHEMA_VERSION}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
