"""Collect a Bitget UTA market/account profile without trading operations."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse
from typing import Any, Mapping

import ccxt.async_support as ccxt_async

from procedures import load_user_info


READ_ONLY_ENDPOINTS = {
    "account_assets": "private_uta_get_v3_account_assets",
    "account_settings": "private_uta_get_v3_account_settings",
    "account_fee_rate": "private_uta_get_v3_account_fee_rate",
    "market_instruments": "public_uta_get_v3_market_instruments",
    "market_position_tier": "public_uta_get_v3_market_position_tier",
    "market_tickers": "public_uta_get_v3_market_tickers",
    "market_current_fund_rate": "public_uta_get_v3_market_current_fund_rate",
}
PROFILE_SCHEMA_VERSION = "bitget-uta-market-profile-v1"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _redact(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("secret", "passphrase", "password", "apikey", "api_key", "signature", "token")):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(child_key): _redact(child_value, key=str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact(child) for child in value]
    return value


def _ccxt_config(user_info: Mapping[str, Any]) -> dict[str, Any]:
    ignored = {"exchange", "options", "quote"}
    config = {key: value for key, value in user_info.items() if key not in ignored}
    aliases = {
        "key": "apiKey",
        "api_key": "apiKey",
        "passphrase": "password",
    }
    for old_key, new_key in aliases.items():
        if old_key in config and new_key not in config:
            config[new_key] = config.pop(old_key)
    config["enableRateLimit"] = True
    config.setdefault("timeout", 30_000)
    return config


def _request_params(name: str, *, category: str, symbol: str) -> dict[str, str]:
    if name in {"account_assets", "account_settings", "account_fee_rate"}:
        return {} if name != "account_fee_rate" else {"category": category}
    return {"category": category, "symbol": symbol}


def _safe_error_details(exc: Exception) -> dict[str, str]:
    """Keep only exchange error code/message; never persist URLs or credentials."""

    details = {"error_type": type(exc).__name__}
    message = str(exc)
    message = re.sub(r"https?://\S+", "<url>", message)
    message = re.sub(r"(?i)(api[_-]?key|secret|passphrase|password|signature|access[_-]?key)[=:][^\s,}]+", r"\1=<redacted>", message)
    if message and len(message) <= 240:
        details["message"] = message
    payload = getattr(exc, "response", None) or getattr(exc, "body", None)
    if isinstance(payload, Mapping):
        for key in ("code", "msg", "message"):
            if payload.get(key) not in (None, ""):
                details[key] = str(payload[key])
    elif isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except Exception:
            decoded = None
        if isinstance(decoded, Mapping):
            for key in ("code", "msg", "message"):
                if decoded.get(key) not in (None, ""):
                    details[key] = str(decoded[key])
        else:
            parsed = urlparse(payload)
            if parsed.scheme and parsed.netloc:
                details["error_type"] = details["error_type"]
                if parsed.path:
                    details["path"] = parsed.path
                if parse_qs(parsed.query):
                    details["query_present"] = "true"
    return details


async def collect_profile(
    *, user: str, api_keys_path: str | Path, symbol: str, category: str
) -> dict[str, Any]:
    """Collect only allow-listed GET endpoints and return a non-authorizing profile."""

    user_info = load_user_info(user, str(api_keys_path))
    if str(user_info.get("exchange", "")).lower() != "bitget":
        raise ValueError(f"user {user!r} is not configured for Bitget")

    client = ccxt_async.bitget(_ccxt_config(user_info))
    client.options.update({"uta": True, "defaultType": "swap"})
    user_options = user_info.get("options")
    if isinstance(user_options, Mapping):
        client.options.update(user_options)
    responses: dict[str, Any] = {}
    response_hashes: dict[str, str] = {}
    errors: dict[str, dict[str, str]] = {}
    try:
        for name, method_name in READ_ONLY_ENDPOINTS.items():
            method = getattr(client, method_name, None)
            if method is None:
                errors[name] = {"error_type": "UnsupportedEndpoint"}
                continue
            try:
                response = await method(_request_params(name, category=category, symbol=symbol))
            except Exception as exc:  # keep the profile inspectable even with a permission gap
                errors[name] = _safe_error_details(exc)
                continue
            responses[name] = _redact(response)
            response_hashes[name] = _sha256(response)
    finally:
        await client.close()

    classic_account = any(
        "40084" in details.get("message", "")
        or "Classic Account mode" in details.get("message", "")
        for details in errors.values()
    )
    status = "failed_uta_account_mode" if classic_account else "collected_read_only_pending_controls"
    reason = (
        "The API key is in Classic Account mode; UTA isolated hedge trading cannot be authorized for this key."
        if classic_account
        else "A collected snapshot never authorizes trading; normalized account settings and API-key controls must pass the profile gates first."
    )
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "status": status,
        "plan_date": "2026-08-17",
        "exchange": "bitget",
        "symbol": symbol,
        "category": category,
        "user": user,
        "collected_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "read_only_only": True,
        "trading_operations_count": 0,
        "account_controls": {
            "withdrawals_enabled": None,
            "ip_allowlist_enabled": None,
            "source": "must be confirmed from API-key settings; not inferred from account data",
        },
        "normalized": {
            "account_mode": "classic" if classic_account else None,
            "position_mode": None,
            "margin_mode": None,
            "leverage_long": None,
            "leverage_short": None,
            "collateral_currency": "USDT",
        },
        "read_only_endpoints": dict(READ_ONLY_ENDPOINTS),
        "response_sha256": response_hashes,
        "responses": responses,
        "errors": errors,
        "reason": reason,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="bitget_01")
    parser.add_argument("--api-keys", type=Path, default=Path("api-keys.json"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--category", default="USDT-FUTURES")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_market_profile_read_only.json"),
    )
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    profile = await collect_profile(
        user=args.user,
        api_keys_path=args.api_keys,
        symbol=args.symbol,
        category=args.category,
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "responses": len(profile["responses"]), "errors": len(profile["errors"])}))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_async(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
