"""Run a bounded, read-only Bitget paper shadow session.

This runner is intentionally separate from ``passivbot live``.  It builds a
sanitized paper profile, creates only the public-data paper client, records
source-hashed observations and local ledger evidence, and exits at one fixed
deadline.  It never reads credential storage, creates an order, transfers
funds, or restarts itself after an error.

The command is an exploratory engineering run.  A run with no fills is
reported as ``no_trade_observed`` and never as a successful trading period.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Awaitable, Callable, Mapping

# When this documented file is run directly, Python puts ``src/tools`` before
# ``src`` on sys.path. That makes ``passivbot`` import
# ``src/tools/trailing_diagnostics.py`` instead of the package-level module.
# Keep module and direct invocation on the same import path.
if __name__ == "__main__" and __package__ in (None, ""):
    _source_root = Path(__file__).resolve().parents[1]
    _tools_root = (_source_root / "tools").resolve()
    _cleaned_path: list[str] = []
    for _entry in sys.path:
        try:
            if Path(_entry or ".").resolve() == _tools_root:
                continue
        except (OSError, RuntimeError):
            pass
        _cleaned_path.append(_entry)
    sys.path[:] = [str(_source_root), *_cleaned_path]

from exchanges.paper import PaperBitgetCCXTClient, _paper_symbol


SCHEMA_VERSION = "crypto-paper-shadow-run-v1"
UTC = timezone.utc


def _utc_iso(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else float(timestamp)
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checksums(root: Path, checksums: Mapping[str, Any]) -> bool:
    """Verify every recorded artifact after ``checksums.json`` is written."""

    files = checksums.get("files")
    if not isinstance(files, Mapping) or not files:
        return False
    root = root.resolve()
    try:
        for name, expected in files.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                return False
            path = (root / name).resolve()
            if path.parent != root or not path.is_file():
                return False
            if _sha256_file(path) != expected:
                return False
    except (OSError, ValueError):
        return False
    return True


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _safe_symbol(value: str) -> str:
    symbol = _paper_symbol(value)
    if symbol != "BTC/USDT:USDT":
        raise ValueError("the first bounded paper shadow supports BTC/USDT:USDT only")
    return symbol


def _sanitized_profile(
    *,
    symbol: str,
    output_dir: Path,
    initial_balance: float,
    execution_inputs: Path | None,
    since_ms: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    balance = float(initial_balance)
    if not math.isfinite(balance) or balance <= 0.0 or balance > 1000.0:
        raise ValueError("initial_balance must be finite, positive and no greater than 1000")
    if execution_inputs is not None and since_ms is None:
        raise ValueError("since_ms is required when execution_inputs is configured")
    if since_ms is not None and int(since_ms) <= 0:
        raise ValueError("since_ms must be positive")

    live: dict[str, Any] = {
        "paper_mode": True,
        "paper_symbols": ["BTC"],
        "paper_initial_balance": balance,
        "leverage": 3.0,
        "hedge_mode": True,
        "margin_mode_preference": "isolated",
        "paper_state_path": str((output_dir / "ledger.json").resolve()),
    }
    if execution_inputs is not None:
        live["paper_execution_inputs_path"] = str(execution_inputs.resolve())
        live["paper_execution_inputs_since_ms"] = int(since_ms)
    config = {
        "live": live,
        "_paper_mode": True,
        "user": "paper-shadow",
    }
    user_info = {
        "exchange": "bitget",
        "quote": "USDT",
        "paper": True,
        "paper_mode": True,
        "paper_symbols": ["BTC"],
        "paper_initial_balance": balance,
        "paper_leverage": 3.0,
        "paper_state_path": str((output_dir / "ledger.json").resolve()),
    }
    if execution_inputs is not None:
        user_info["paper_execution_inputs_path"] = str(execution_inputs.resolve())
        user_info["paper_execution_inputs_since_ms"] = int(since_ms)
    # Keep the exchange-facing profile intentionally small.  In particular,
    # do not copy an input config or environment variables into this artifact.
    return config, user_info


def _market_profile(markets: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    safe_rows: dict[str, Any] = {}
    for symbol, market in sorted(markets.items()):
        safe_rows[symbol] = {
            key: market.get(key)
            for key in (
                "symbol",
                "base",
                "quote",
                "settle",
                "type",
                "swap",
                "linear",
                "contractSize",
                "maker",
                "taker",
                "precision",
                "limits",
            )
            if key in market
        }
    return {"schema_version": "crypto-paper-shadow-market-profile-v1", "markets": safe_rows}


def _event(kind: str, timestamp_ms: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    clean_payload = dict(payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": int(timestamp_ms),
        "kind": str(kind),
        "payload": clean_payload,
        "payload_hash": _canonical_hash(clean_payload),
    }


_PUBLIC_REQUEST_METHODS = {
    "load_markets",
    "fetch_ticker",
    "fetch_tickers",
    "fetch_time",
    "fetch_ohlcv",
    "fetch_order_book",
    "fetch_trades",
    "fetch_funding_rate",
    "fetch_funding_rates",
}


def _request_method(row: Mapping[str, Any]) -> str:
    return str(row.get("method") or "").strip().lower()


def _is_public_request(row: Mapping[str, Any]) -> bool:
    method = _request_method(row)
    return method in _PUBLIC_REQUEST_METHODS or method.startswith("public_")


def _is_order_create(row: Mapping[str, Any]) -> bool:
    method = _request_method(row)
    return (
        method in {"create_order", "place_order"}
        or "create_order" in method
        or "place_order" in method
        or method.endswith("/order")
    )


def _is_transfer(row: Mapping[str, Any]) -> bool:
    method = _request_method(row)
    return any(token in method for token in ("transfer", "withdraw", "deposit"))


def _request_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep boundary diagnostics free of arbitrary request payloads."""

    summary = {"method": str(row.get("method") or "unknown")}
    if row.get("timestamp") is not None:
        summary["timestamp"] = row.get("timestamp")
    return summary


async def run_shadow(
    *,
    output_dir: str | Path,
    duration_seconds: float,
    poll_seconds: float = 60.0,
    symbol: str = "BTC/USDT:USDT",
    initial_balance: float = 1000.0,
    execution_inputs: str | Path | None = None,
    since_ms: int | None = None,
    max_observations: int | None = None,
    client_factory: Callable[..., Any] = PaperBitgetCCXTClient,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    wall_time: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run one bounded public-data session and return its machine summary.

    ``max_observations`` exists only to make short deterministic tests possible;
    real exploratory runs must use the requested wall-clock duration and leave
    this argument unset.
    """

    if not math.isfinite(float(duration_seconds)) or float(duration_seconds) <= 0.0:
        raise ValueError("duration_seconds must be finite and positive")
    if not math.isfinite(float(poll_seconds)) or float(poll_seconds) <= 0.0:
        raise ValueError("poll_seconds must be finite and positive")
    if max_observations is not None and int(max_observations) <= 0:
        raise ValueError("max_observations must be positive")
    canonical_symbol = _safe_symbol(symbol)
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=False)
    input_path = (
        None
        if execution_inputs is None
        else Path(execution_inputs).expanduser().resolve()
    )
    config, user_info = _sanitized_profile(
        symbol=canonical_symbol,
        output_dir=target,
        initial_balance=initial_balance,
        execution_inputs=input_path,
        since_ms=since_ms,
    )
    config_path = target / "config.json"
    market_profile_path = target / "market_profile.json"
    events_path = target / "events.jsonl.gz"
    manifest_path = target / "manifest.json"
    verdict_path = target / "verdict.json"
    checksums_path = target / "checksums.json"
    _write_json(config_path, config)
    _write_json(market_profile_path, {"status": "not_available"})
    with gzip.open(events_path, "wb"):
        pass

    started_wall = float(wall_time())
    started_mono = float(monotonic())
    deadline = started_mono + float(duration_seconds)
    observations = 0
    funding_events = 0
    error: dict[str, str] | None = None
    client: Any | None = None
    event_count = 0

    try:
        client = client_factory(
            config,
            user_info,
            initial_balance=float(initial_balance),
            clock_ms=int(started_wall * 1000),
            state_path=target / "ledger.json",
        )
        markets = await client.load_markets()
        _write_json(market_profile_path, _market_profile(markets))
        model_status = client.get_paper_model_status()
        with gzip.open(events_path, "wt", encoding="utf-8") as events:
            while True:
                ticker = await client.fetch_ticker(canonical_symbol)
                timestamp_ms = int(ticker["timestamp"])
                normalized_ticker = {
                    key: ticker[key]
                    for key in ("symbol", "timestamp", "bid", "ask", "last")
                    if key in ticker
                }
                source_hash = _canonical_hash(normalized_ticker)
                row = _event(
                    "market.observation",
                    timestamp_ms,
                    {
                        "symbol": canonical_symbol,
                        "ticker": normalized_ticker,
                        "source": "bitget_public_market_data",
                        "source_hash": source_hash,
                        "source_hash_kind": "normalized_public_response",
                    },
                )
                events.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                event_count += 1
                observations += 1
                funding = client.apply_recorded_public_execution_inputs(
                    timestamp=timestamp_ms
                )
                for evidence in funding:
                    funding_row = _event(
                        "funding.settled",
                        int(evidence["ts"]),
                        {
                            "evidence": evidence,
                            "source_hash": (
                                evidence.get("payload", {}).get("source_hash")
                                if isinstance(evidence.get("payload"), Mapping)
                                else None
                            ),
                        },
                    )
                    events.write(
                        json.dumps(funding_row, ensure_ascii=False, sort_keys=True) + "\n"
                    )
                    event_count += 1
                    funding_events += 1
                heartbeat = _event(
                    "shadow.heartbeat",
                    timestamp_ms,
                    {
                        "observation": observations,
                        "funding_events": funding_events,
                        "private_api_calls": 0,
                        "order_creates": 0,
                        "transfers": 0,
                    },
                )
                events.write(json.dumps(heartbeat, ensure_ascii=False, sort_keys=True) + "\n")
                event_count += 1
                if max_observations is not None and observations >= int(max_observations):
                    break
                remaining = deadline - float(monotonic())
                if remaining <= 0.0:
                    break
                # A bounded observation limit is a deterministic test hook,
                # not a request to wait real wall-clock polling intervals.
                await sleep(
                    0.0
                    if max_observations is not None
                    else min(float(poll_seconds), remaining)
                )
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception as exc:
                if error is None:
                    error = {"type": type(exc).__name__, "message": str(exc)}

    ended_wall = float(wall_time())
    elapsed = max(0.0, ended_wall - started_wall)
    ledger_path = target / "ledger.json"
    wal_path = target / "ledger.wal.ndjson"
    model_status = (
        client.get_paper_model_status()
        if client is not None
        else {"status": "client_not_created"}
    )
    request_log = list(
        getattr(client, "public_request_log", []) if client is not None else []
    ) + list(getattr(client, "request_log", []) if client is not None else [])
    private_calls = [
        _request_summary(row)
        for row in request_log
        if not _is_public_request(row)
    ]
    fills = list(getattr(client, "fills", []) if client is not None else [])
    bounded = bool(max_observations is not None or elapsed >= float(duration_seconds))
    checks = {
        "bounded_deadline_or_test_limit": bounded,
        "public_only_request_log": not private_calls,
        "no_order_creates": not any(_is_order_create(row) for row in request_log),
        "no_transfers": not any(_is_transfer(row) for row in request_log),
        "source_hashed_observations": observations > 0,
        "local_ledger_present": ledger_path.is_file(),
        "events_present": event_count > 0,
        "no_runtime_error": error is None,
        # This is provisional while the first checksum set is being written.
        # The returned verdict is produced only after the verification below.
        "checksums_verified": True,
    }

    def _status() -> str:
        if not all(bool(value) for value in checks.values()):
            return "failed"
        if not fills:
            return "no_trade_observed"
        return "observed_not_qualified"

    verdict_status = _status()
    strategy_boundary = {
        "status": "transport_only",
        "strategy_driver_present": False,
        "strategy_events": 0,
        "planned_orders": 0,
        "reason": (
            "This bounded observer reads public market data only. It does not "
            "run a strategy, create order intents, or start a paper qualification period."
        ),
    }
    if error is not None:
        verdict_status = "failed"
    verdict = {
        "schema_version": "crypto-paper-shadow-verdict-v1",
        "mode": "bounded_public_transport_shadow",
        "status": verdict_status,
        "counts": {
            "observations": observations,
            "funding_events": funding_events,
            "fills": len(fills),
            "events": event_count,
        },
        "checks": checks,
        "model_status": model_status,
        "strategy_boundary": strategy_boundary,
        "qualification": {
            "calendar_period_started": False,
            "is_success": False,
            "reason": "transport_only observer; no strategy trade period can be admitted",
        },
        "private_boundary": {
            "private_calls": private_calls,
            "public_client_only": not private_calls,
        },
        "error": error,
        "note": (
            "no_trade_observed is not a successful paper period; the run must be "
            "repeated with enough strategy events before any calendar gate can start"
        ),
    }
    _write_json(verdict_path, verdict)
    manifest = {
        "schema_version": "crypto-paper-shadow-manifest-v1",
        "run_type": "bounded_public_bitget_paper_shadow",
        "run_status": verdict_status,
        "symbol": canonical_symbol,
        "started_at_utc": _utc_iso(started_wall),
        "ended_at_utc": _utc_iso(ended_wall),
        "requested_duration_seconds": float(duration_seconds),
        "observed_duration_seconds": elapsed,
        "poll_seconds": float(poll_seconds),
        "max_observations": max_observations,
        "config_hash": _sha256_file(config_path),
        "execution_inputs_path": str(input_path) if input_path is not None else None,
        "execution_inputs_hash": (
            _sha256_file(input_path) if input_path is not None and input_path.is_file() else None
        ),
        "artifacts": {
            "config": config_path.name,
            "market_profile": market_profile_path.name,
            "events": events_path.name,
            "ledger": ledger_path.name,
            "wal": wal_path.name if wal_path.exists() else None,
            "verdict": verdict_path.name,
        },
        "error": error,
    }
    _write_json(manifest_path, manifest)
    checksum_paths = [
        manifest_path,
        config_path,
        market_profile_path,
        events_path,
        verdict_path,
    ]
    for path in (ledger_path, wal_path):
        if path.is_file():
            checksum_paths.append(path)
    checksums = {
        "schema_version": "crypto-paper-shadow-checksums-v1",
        "files": {path.name: _sha256_file(path) for path in checksum_paths},
    }
    _write_json(checksums_path, checksums)
    checksum_verified = _verify_checksums(target, checksums)
    if not checksum_verified:
        checks["checksums_verified"] = False
        checksum_error = {
            "type": "ChecksumVerificationError",
            "message": "one or more recorded artifacts failed verification after writing",
        }
        verdict["status"] = "failed"
        verdict["checks"] = checks
        verdict["error"] = error or checksum_error
        manifest["run_status"] = "failed"
        manifest["error"] = verdict["error"]
        _write_json(verdict_path, verdict)
        _write_json(manifest_path, manifest)
        checksums = {
            "schema_version": "crypto-paper-shadow-checksums-v1",
            "files": {path.name: _sha256_file(path) for path in checksum_paths},
        }
        _write_json(checksums_path, checksums)
        # A second verification makes the failed result itself inspectable. The
        # check remains false because the first post-write verification failed.
        _verify_checksums(target, checksums)
    return {
        "run_dir": str(target),
        "manifest": manifest,
        "verdict": verdict,
        "checksums": checksums,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--duration-seconds", required=True, type=float)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--symbol", default="BTC/USDT:USDT")
    parser.add_argument("--initial-balance", type=float, default=1000.0)
    parser.add_argument("--execution-inputs", type=Path)
    parser.add_argument("--since-ms", type=int)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.execution_inputs is not None and args.since_ms is None:
        parser.error("--since-ms is required with --execution-inputs")
    result = asyncio.run(
        run_shadow(
            output_dir=args.output_dir,
            duration_seconds=args.duration_seconds,
            poll_seconds=args.poll_seconds,
            symbol=args.symbol,
            initial_balance=args.initial_balance,
            execution_inputs=args.execution_inputs,
            since_ms=args.since_ms,
        )
    )
    print(json.dumps(result["verdict"], ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["verdict"]["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
