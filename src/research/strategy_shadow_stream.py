"""Run the existing strategy shadow against a public Bitget market stream.

This is an engineering observer, not a second trading bot.  It polls the
existing public market recorder, adds a small deterministic signal policy for
the shadow boundary, and passes the enriched snapshot to
``StrategyShadowDriver``.  Orders and fills remain local paper operations.
There is no account, credential, transfer, or exchange-order path here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Awaitable, Callable, Mapping

from research.strategy_shadow import (
    StrategyShadowDriver,
    canonical_hash,
    normalize_config,
    normalize_shadow_policy,
)
from tools.bitget_market_recorder import (
    RequestFn,
    _default_request,
    collect_market_snapshot,
    verify_market_log,
)


STREAM_SCHEMA_VERSION = "crypto-strategy-shadow-stream-v1"
POLICY_SCHEMA_VERSION = "crypto-strategy-shadow-grid-policy-v1"
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_ENTRY_BPS = 5.0
DEFAULT_TAKE_PROFIT_BPS = 5.0
DEFAULT_ORDER_AMOUNT = 0.001


def resolve_stream_policy(
    config: Mapping[str, Any] | None,
    *,
    entry_bps: float | None,
    take_profit_bps: float | None,
    order_amount: float | None,
) -> dict[str, float]:
    """Resolve policy values and reject overrides absent from the config."""

    raw = dict(config or {})
    _reject_credentials(raw, path="config")
    configured = (
        normalize_shadow_policy(raw["shadow_policy"])
        if "shadow_policy" in raw
        else None
    )
    defaults = {
        "entry_bps": DEFAULT_ENTRY_BPS,
        "take_profit_bps": DEFAULT_TAKE_PROFIT_BPS,
        "order_amount": DEFAULT_ORDER_AMOUNT,
    }
    provided = {
        "entry_bps": entry_bps,
        "take_profit_bps": take_profit_bps,
        "order_amount": order_amount,
    }
    resolved: dict[str, float] = {}
    for name, value in provided.items():
        if value is None:
            resolved[name] = float((configured or defaults)[name])
            continue
        explicit = _finite(value, name=name, positive=True)
        if configured is not None and not math.isclose(
            explicit,
            float(configured[name]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                f"{name} override does not match config shadow_policy; "
                "change the config file and its hash instead"
            )
        resolved[name] = explicit
    if resolved["entry_bps"] > 10_000.0 or resolved["take_profit_bps"] > 10_000.0:
        raise ValueError("policy thresholds must not exceed 10000 bps")
    if "order_amount" in raw:
        configured_amount = _finite(raw["order_amount"], name="config.order_amount", positive=True)
        if not math.isclose(
            configured_amount,
            resolved["order_amount"],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("config.order_amount does not match shadow_policy.order_amount")
    return resolved


def resolve_stream_max_source_gap(
    config: Mapping[str, Any] | None,
    *,
    max_source_gap_seconds: float | None,
    interval_seconds: float,
) -> float:
    """Resolve the source-time gap limit and reject hidden CLI overrides."""

    raw = dict(config or {})
    configured = raw.get("max_source_gap_seconds")
    if configured is None:
        raise ValueError(
            "config.max_source_gap_seconds is required; set it in the config file"
        )
    if configured is not None:
        configured = _finite(
            configured,
            name="config.max_source_gap_seconds",
            positive=True,
        )
    if max_source_gap_seconds is None:
        return float(configured)
    explicit = _finite(
        max_source_gap_seconds,
        name="max_source_gap_seconds",
        positive=True,
    )
    if not math.isclose(
        explicit,
        float(configured),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError(
            "max_source_gap_seconds override does not match config; "
            "change the config file and its hash instead"
        )
    return explicit


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_credentials(value: Any, *, path: str = "root") -> None:
    forbidden = {
        "apikey",
        "api_key",
        "secret",
        "password",
        "passphrase",
        "token",
        "privatekey",
        "private_key",
        "wallet",
        "mnemonic",
    }
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            compact = normalized.replace("_", "")
            if normalized in forbidden or compact in forbidden:
                raise ValueError(f"credential-shaped field is not allowed in {path}.{key}")
            _reject_credentials(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_credentials(nested, path=f"{path}[{index}]")


def _finite(value: Any, *, name: str, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0.0):
        requirement = "finite and positive" if positive else "finite"
        raise ValueError(f"{name} must be {requirement}")
    return number


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with temporary.open("rb") as handle:
        import os

        os.fsync(handle.fileno())
    temporary.replace(path)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        import os

        os.fsync(handle.fileno())


def _source_timestamp_ms(snapshot: Mapping[str, Any]) -> int:
    """Return the exchange timestamp used by strategy and coverage logic."""

    value = snapshot.get("source_timestamp_ms")
    try:
        timestamp = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("public market snapshot source timestamp is missing") from exc
    # Bitget public timestamps are milliseconds.  Do not silently reinterpret
    # a seconds timestamp or replace it with local observation time.
    if timestamp < 100_000_000_000:
        raise ValueError("public market snapshot source timestamp must be milliseconds")
    return timestamp


def _source_event_id(snapshot: Mapping[str, Any]) -> str:
    response_hashes = snapshot.get("response_sha256")
    if not isinstance(response_hashes, Mapping):
        raise ValueError("public market snapshot response hashes are missing")
    required = ("ticker", "orderbook", "current_funding", "funding_history")
    if any(not str(response_hashes.get(name) or "") for name in required):
        raise ValueError("public market snapshot response hashes are incomplete")
    return canonical_hash(
        {
            "exchange": str(snapshot.get("exchange") or "bitget"),
            "category": str(snapshot.get("category") or ""),
            "symbol": str(snapshot.get("symbol") or ""),
            "source_timestamp_ms": _source_timestamp_ms(snapshot),
            "response_sha256": {name: str(response_hashes[name]) for name in required},
        }
    )


def _load_json_state(path: Path, default: Mapping[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"state file must contain an object: {path.name}")
    _reject_credentials(value, path=path.name)
    return dict(value)


class DeterministicGridPolicy:
    """Generate explicit, reproducible shadow signals from prior prices only.

    The policy is deliberately an engineering probe.  It is not an optimized
    candidate and it is never promoted to trading.  Long enters after a fall
    from the highest observed flat-price anchor and exits after a fixed rise;
    Short mirrors that rule around the lowest flat-price anchor.
    """

    def __init__(
        self,
        *,
        path: Path,
        strategy_id: str,
        side: str,
        entry_bps: float = DEFAULT_ENTRY_BPS,
        take_profit_bps: float = DEFAULT_TAKE_PROFIT_BPS,
        order_amount: float = DEFAULT_ORDER_AMOUNT,
    ) -> None:
        side = str(side).strip().lower()
        if side not in {"long", "short"}:
            raise ValueError("stream policy side must be long or short")
        self.path = path.expanduser().resolve()
        self.strategy_id = str(strategy_id).strip()
        if not self.strategy_id:
            raise ValueError("strategy_id must not be empty")
        self.side = side
        self.entry_bps = _finite(entry_bps, name="entry_bps", positive=True)
        self.take_profit_bps = _finite(take_profit_bps, name="take_profit_bps", positive=True)
        self.order_amount = _finite(order_amount, name="order_amount", positive=True)
        if self.entry_bps > 10_000.0 or self.take_profit_bps > 10_000.0:
            raise ValueError("policy thresholds must not exceed 10000 bps")
        self.config = {
            "policy_name": "deterministic_grid_probe",
            "side": self.side,
            "entry_bps": self.entry_bps,
            "take_profit_bps": self.take_profit_bps,
            "order_amount": self.order_amount,
        }
        self.config_hash = canonical_hash(self.config)
        self.state = self._default_state()
        if self.path.exists():
            self._load()

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "strategy_id": self.strategy_id,
            "config_hash": self.config_hash,
            "policy": dict(self.config),
            "anchor_price": None,
            "entry_price": None,
            "open": False,
            "last_snapshot_id": None,
            "last_timestamp_ms": None,
            "last_source_event_id": None,
            "processed_samples": 0,
        }

    def _load(self) -> None:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("stream policy state must be an object")
        _reject_credentials(value, path="policy_state")
        if value.get("schema_version") != POLICY_SCHEMA_VERSION:
            raise ValueError("unsupported stream policy state schema")
        if str(value.get("strategy_id")) != self.strategy_id:
            raise ValueError("stream policy strategy_id does not match config")
        if str(value.get("config_hash")) != self.config_hash:
            raise ValueError("stream policy config hash does not match config")
        for key in ("anchor_price", "entry_price"):
            if value.get(key) is not None:
                _finite(value[key], name=f"policy_state.{key}", positive=True)
        if not isinstance(value.get("open"), bool):
            raise ValueError("stream policy open state must be boolean")
        self.state = dict(value)

    def save(self) -> None:
        _atomic_write(self.path, self.state)

    def decide(self, snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        bid = _finite(snapshot.get("ticker", {}).get("bid"), name="snapshot ticker bid", positive=True)
        ask = _finite(snapshot.get("ticker", {}).get("ask"), name="snapshot ticker ask", positive=True)
        mid = (bid + ask) / 2.0
        before = dict(self.state)
        anchor = self.state.get("anchor_price")
        entry_price = self.state.get("entry_price")
        is_open = bool(self.state.get("open"))
        if anchor is None:
            anchor = mid
        action = "hold"
        reason = "anchor_initialized"
        fill_price: float | None = None

        if not is_open:
            if self.side == "long":
                anchor = max(float(anchor), mid)
                trigger = mid <= float(anchor) * (1.0 - self.entry_bps / 10_000.0)
                fill_price = ask
            else:
                anchor = min(float(anchor), mid)
                trigger = mid >= float(anchor) * (1.0 + self.entry_bps / 10_000.0)
                fill_price = bid
            if trigger:
                action = "enter"
                reason = "flat_anchor_threshold_reached"
                entry_price = fill_price
                is_open = True
        else:
            if entry_price is None:
                raise ValueError("open policy state has no entry_price")
            if self.side == "long":
                trigger = bid >= float(entry_price) * (1.0 + self.take_profit_bps / 10_000.0)
                fill_price = bid
            else:
                trigger = ask <= float(entry_price) * (1.0 - self.take_profit_bps / 10_000.0)
                fill_price = ask
            if trigger:
                action = "exit"
                reason = "take_profit_threshold_reached"
                anchor = mid
                entry_price = None
                is_open = False

        source_timestamp_ms = _source_timestamp_ms(snapshot)
        snapshot_id = str(snapshot.get("source_event_id") or _source_event_id(snapshot))
        after = dict(self.state)
        after.update(
            {
                "anchor_price": float(anchor),
                "entry_price": float(entry_price) if entry_price is not None else None,
                "open": bool(is_open),
                "last_snapshot_id": snapshot_id,
                "last_timestamp_ms": source_timestamp_ms,
                "last_source_event_id": snapshot_id,
            }
        )
        signal = {
            "action": action,
            "reason": reason,
            "side": self.side,
            "policy": self.config["policy_name"],
        }
        enriched = dict(snapshot)
        # The recorder keeps the exchange response grouped under ``ticker``;
        # the existing shadow boundary consumes the normalized quote fields at
        # the top level.  Keep the full source response and add only this
        # explicit compatibility projection.
        enriched["symbol"] = str(snapshot.get("symbol") or "")
        enriched["bid"] = bid
        enriched["ask"] = ask
        enriched["last"] = _finite(
            snapshot.get("ticker", {}).get("last", mid) or mid,
            name="snapshot ticker last",
            positive=True,
        )
        ticker = snapshot.get("ticker", {})
        if ticker.get("mark") is not None:
            enriched["mark_price"] = _finite(ticker["mark"], name="snapshot ticker mark", positive=True)
        if ticker.get("index") is not None:
            enriched["index_price"] = _finite(ticker["index"], name="snapshot ticker index", positive=True)
        enriched["snapshot_id"] = snapshot_id
        enriched["timestamp"] = source_timestamp_ms
        enriched["signals"] = {"long": action if self.side == "long" else "hold", "short": action if self.side == "short" else "hold"}
        enriched["shadow_policy"] = {
            "policy": self.config,
            "config_hash": self.config_hash,
            "before": before,
            "after": after,
            "signal": signal,
        }
        if action in {"enter", "exit"}:
            order_id = canonical_hash([self.strategy_id, self.side, snapshot_id, action])[:24]
            execution: dict[str, Any] = {
                "enabled": True,
                "price": float(fill_price),
                "fill_id": f"stream-fill-{order_id}",
                "event_id": f"stream-execution-{order_id}",
                "part_sequence": 1,
                # The price crosses the currently visible top level.  This is
                # a deterministic paper fill, not proof of maker queue
                # priority or a real exchange execution.
                "liquidity": "taker",
            }
            enriched["execution"] = execution
            enriched["shadow_policy"]["fill_model"] = {
                "version": "stream-paper-cross-v1",
                "queue_model": "not_modeled",
                "orderbook_hash": str(snapshot["response_sha256"]["orderbook"]),
                "source_event_id": snapshot_id,
            }
        return enriched, after

    def commit(self, state: Mapping[str, Any]) -> None:
        self.state = dict(state)
        self.state["processed_samples"] = int(self.state.get("processed_samples", 0)) + 1
        self.save()


async def run_strategy_shadow_stream(
    *,
    output_dir: str | Path,
    config: Mapping[str, Any] | None = None,
    symbol: str = "BTCUSDT",
    category: str = "USDT-FUTURES",
    depth: int = 20,
    side: str = "long",
    entry_bps: float | None = None,
    take_profit_bps: float | None = None,
    order_amount: float | None = None,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_source_gap_seconds: float | None = None,
    duration_seconds: float | None = None,
    max_samples: int | None = None,
    request: RequestFn | None = None,
    fixture_mode: bool = False,
    async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    capture_clock_ms: Callable[[], int] | None = None,
) -> dict[str, Any]:
    """Collect public snapshots and process them through the existing driver."""

    if duration_seconds is None and max_samples is None:
        raise ValueError("stream requires duration_seconds or max_samples")
    if duration_seconds is not None and (
        isinstance(duration_seconds, bool) or not math.isfinite(float(duration_seconds)) or float(duration_seconds) < 0.0
    ):
        raise ValueError("duration_seconds must be finite and >= 0")
    if max_samples is not None and (isinstance(max_samples, bool) or int(max_samples) < 1):
        raise ValueError("max_samples must be positive")
    if not math.isfinite(float(interval_seconds)) or float(interval_seconds) <= 0.0:
        raise ValueError("interval_seconds must be finite and > 0")
    max_source_gap_seconds = resolve_stream_max_source_gap(
        config,
        max_source_gap_seconds=max_source_gap_seconds,
        interval_seconds=float(interval_seconds),
    )

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    snapshots_path = root / "market_snapshots.jsonl"
    strategy_inputs_path = root / "strategy_inputs.ndjson"
    policy_events_path = root / "policy_events.ndjson"
    policy_path = root / "policy_state.json"
    stream_state_path = root / "stream_state.json"
    result_path = root / "result.json"
    policy_values = resolve_stream_policy(
        config,
        entry_bps=entry_bps,
        take_profit_bps=take_profit_bps,
        order_amount=order_amount,
    )
    effective_config = dict(config or {})
    effective_config["shadow_policy"] = policy_values
    state_config = normalize_config(effective_config)
    entry_bps = policy_values["entry_bps"]
    take_profit_bps = policy_values["take_profit_bps"]
    order_amount = policy_values["order_amount"]
    policy = DeterministicGridPolicy(
        path=policy_path,
        strategy_id=state_config["strategy_id"],
        side=side,
        entry_bps=entry_bps,
        take_profit_bps=take_profit_bps,
        order_amount=order_amount,
    )
    if snapshots_path.is_file():
        audit = verify_market_log(
            snapshots_path,
            max_gap_seconds=max_source_gap_seconds,
            use_source_timestamp=True,
        )
        if audit["status"] != "passed":
            raise ValueError("existing market snapshot log failed integrity verification")
    stream_state = _load_json_state(
        stream_state_path,
        {
            "schema_version": STREAM_SCHEMA_VERSION,
            "symbol": symbol,
            "category": category,
            "depth": int(depth),
            "side": str(side).lower(),
            "policy_config_hash": policy.config_hash,
            "max_source_gap_seconds": max_source_gap_seconds,
            "fixture_mode": bool(fixture_mode),
            "ingest_seq": 0,
            "market_event_count": 0,
            "heartbeat_count": 0,
            "last_source_event_id": None,
            "last_source_timestamp_ms": None,
            "last_observed_at_ms": None,
        },
    )
    expected_state = {
        "schema_version": STREAM_SCHEMA_VERSION,
        "symbol": symbol,
        "category": category,
        "depth": int(depth),
        "side": str(side).lower(),
        "policy_config_hash": policy.config_hash,
        "max_source_gap_seconds": max_source_gap_seconds,
        "fixture_mode": bool(fixture_mode),
    }
    for key, expected in expected_state.items():
        if stream_state.get(key, expected) != expected:
            raise ValueError(f"stream state {key} does not match the current session")
    request_fn = request
    capture_ms = capture_clock_ms or (lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    started_wall = datetime.now(timezone.utc)
    started_monotonic = monotonic()
    samples = 0
    heartbeats = 0
    attempts = 0
    errors: list[dict[str, Any]] = []
    driver = StrategyShadowDriver(
        checkpoint_path=root / "checkpoint.json",
        events_path=root / "events.ndjson",
        ledger_path=root / "paper_ledger.json",
        config={
            **state_config,
            "long_enabled": side == "long",
            "short_enabled": side == "short",
            "order_amount": order_amount,
        },
        input_provenance={
            "source_kind": "local_public_market_stream",
            "source_path": str(snapshots_path),
            "source_sha256": _sha256_file(snapshots_path),
            "format": "jsonl",
        },
    )

    try:
        while True:
            elapsed = max(0.0, monotonic() - started_monotonic)
            if attempts > 0 and duration_seconds is not None and elapsed >= float(duration_seconds):
                break
            if attempts > 0 and max_samples is not None and attempts >= int(max_samples):
                break
            attempts += 1
            try:
                observed_at_ms = int(capture_ms())
                raw = collect_market_snapshot(
                    symbol=symbol,
                    category=category,
                    depth=depth,
                    request=request_fn or _default_request,
                    captured_at_ms=observed_at_ms,
                )
            except Exception as exc:
                error = {
                    "schema_version": STREAM_SCHEMA_VERSION,
                    "event": "stream.error",
                    "phase": "market_collection",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "attempt": attempts,
                    "error": f"{type(exc).__name__}: {exc}",
                    "read_only_only": True,
                    "trading_operations_count": 0,
                }
                _append_jsonl(policy_events_path, error)
                errors.append(error)
                if duration_seconds == 0.0 or (max_samples is not None and attempts >= int(max_samples)):
                    break
                await async_sleep(float(interval_seconds))
                continue

            try:
                source_timestamp_ms = _source_timestamp_ms(raw)
                source_event_id = _source_event_id(raw)
                event_type = (
                    "heartbeat"
                    if stream_state.get("last_source_event_id") == source_event_id
                    else "market"
                )
                raw = dict(raw)
                raw.update(
                    {
                        "stream_event_type": event_type,
                        "source_event_id": source_event_id,
                        "source_timestamp_origin": (
                            "orderbook.ts"
                            if int(raw["orderbook"]["timestamp_ms"]) == source_timestamp_ms
                            else "ticker.ts"
                        ),
                        "ingest_seq": int(stream_state.get("ingest_seq", 0)) + 1,
                        "observed_at_ms": observed_at_ms,
                        "observed_at_utc": datetime.fromtimestamp(
                            observed_at_ms / 1000, tz=timezone.utc
                        ).isoformat(),
                    }
                )
                # The market log contains only public recorder data plus
                # ingestion metadata.  Strategy intent and paper fills are
                # kept in a separate file so raw-source hashes cannot depend
                # on policy decisions.
                _append_jsonl(snapshots_path, raw)
                if event_type == "heartbeat":
                    stream_state.update(
                        {
                            "ingest_seq": raw["ingest_seq"],
                            "heartbeat_count": int(stream_state.get("heartbeat_count", 0)) + 1,
                            "last_source_event_id": source_event_id,
                            "last_source_timestamp_ms": source_timestamp_ms,
                            "last_observed_at_ms": observed_at_ms,
                        }
                    )
                    _atomic_write(stream_state_path, stream_state)
                    _append_jsonl(
                        policy_events_path,
                        {
                            "schema_version": STREAM_SCHEMA_VERSION,
                            "event": "market.heartbeat",
                            "phase": "market_ingest",
                            "source_event_id": source_event_id,
                            "timestamp_ms": source_timestamp_ms,
                            "observed_at_ms": observed_at_ms,
                            "policy_config_hash": policy.config_hash,
                        },
                    )
                    heartbeats += 1
                else:
                    enriched, next_policy_state = policy.decide(raw)
                    _append_jsonl(strategy_inputs_path, enriched)
                    driver_result = await driver.process_snapshot(enriched)
                    signal = enriched["shadow_policy"]["signal"]
                    if signal["action"] in {"enter", "exit"} and driver_result["planned_orders"] != 1:
                        raise RuntimeError("policy signal did not create exactly one shadow plan")
                    policy.commit(next_policy_state)
                    stream_state.update(
                        {
                            "ingest_seq": raw["ingest_seq"],
                            "market_event_count": int(stream_state.get("market_event_count", 0)) + 1,
                            "last_source_event_id": source_event_id,
                            "last_source_timestamp_ms": source_timestamp_ms,
                            "last_observed_at_ms": observed_at_ms,
                        }
                    )
                    _atomic_write(stream_state_path, stream_state)
                    _append_jsonl(
                        policy_events_path,
                        {
                            "schema_version": STREAM_SCHEMA_VERSION,
                            "event": "policy.decision",
                            "phase": "shadow_processing",
                            "snapshot_id": enriched["snapshot_id"],
                            "source_event_id": source_event_id,
                            "timestamp_ms": source_timestamp_ms,
                            "observed_at_ms": observed_at_ms,
                            "policy_config_hash": policy.config_hash,
                            "signal": signal,
                            "state": next_policy_state,
                            "driver_planned_orders": driver_result["planned_orders"],
                            "driver_fills": driver_result["fills"],
                            "fill_model": enriched["shadow_policy"].get("fill_model"),
                        },
                    )
                    samples += 1
            except Exception as exc:
                # A snapshot may already have been appended when processing
                # fails.  Continuing could advance policy state past the
                # driver's checkpoint, so stop fail-closed.
                error = {
                    "schema_version": STREAM_SCHEMA_VERSION,
                    "event": "stream.error",
                    "phase": "shadow_processing",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "attempt": attempts,
                    "error": f"{type(exc).__name__}: {exc}",
                    "read_only_only": True,
                    "trading_operations_count": 0,
                }
                _append_jsonl(policy_events_path, error)
                errors.append(error)
                break
            if duration_seconds == 0.0 or (max_samples is not None and attempts >= int(max_samples)):
                break
            await async_sleep(float(interval_seconds))
    finally:
        await driver.close()
        # ``close`` flushes the checkpoint, event log and paper ledger.  The
        # result must capture hashes after those files are final; otherwise a
        # readiness gate can accept provenance for an earlier file state.
        stream_result = driver.result(input_snapshot_count=samples)

    ended_wall = datetime.now(timezone.utc)
    elapsed_seconds = max(0.0, monotonic() - started_monotonic)
    source_sha256 = _sha256_file(snapshots_path)
    stream_audit = (
        verify_market_log(
            snapshots_path,
            max_gap_seconds=max_source_gap_seconds,
            use_source_timestamp=True,
        )
        if snapshots_path.is_file()
        else {"status": "failed"}
    )
    result = dict(stream_result)
    result.update(
        {
            "schema_version": STREAM_SCHEMA_VERSION,
            "status": "passed" if result.get("status") == "passed" and not errors and stream_audit.get("status") == "passed" else "failed",
            "stream": {
                "started_at_utc": started_wall.isoformat(),
                "ended_at_utc": ended_wall.isoformat(),
                "wall_clock_duration_seconds": elapsed_seconds,
                "attempts": attempts,
                "samples": samples,
                "market_events": samples,
                "heartbeats": heartbeats,
                "errors": errors,
                "snapshot_path": str(snapshots_path),
                "snapshot_sha256": source_sha256,
                "strategy_input_path": str(strategy_inputs_path),
                "strategy_input_sha256": _sha256_file(strategy_inputs_path),
                "stream_state_path": str(stream_state_path),
                "stream_state_sha256": _sha256_file(stream_state_path),
                "policy_path": str(policy_path),
                "policy_config": policy.config,
                "policy_config_hash": policy.config_hash,
                "max_source_gap_seconds": max_source_gap_seconds,
                "market_log_audit": stream_audit,
                "public_only": True,
                "network_writes": 0,
                "trading_operations_count": 0,
                "fixture_mode": bool(fixture_mode),
            },
        }
    )
    metrics = dict(result.get("readiness_metrics", {}))
    metrics.update(
        {
            "shadow_stream_wall_clock_duration_seconds": elapsed_seconds,
            "shadow_stream_samples": samples,
            "shadow_stream_market_events": samples,
            "shadow_stream_heartbeats": heartbeats,
            "shadow_stream_attempts": attempts,
            "shadow_stream_errors": len(errors),
            "shadow_stream_snapshot_log_status": stream_audit.get("status"),
            "shadow_stream_public_only": True,
            "shadow_stream_network_writes": 0,
            "shadow_stream_trading_operations_count": 0,
            "shadow_stream_fixture_mode": bool(fixture_mode),
            "shadow_stream_policy_config_hash": policy.config_hash,
            "shadow_stream_input_hash": source_sha256,
            "shadow_stream_strategy_input_hash": _sha256_file(strategy_inputs_path),
            "shadow_stream_max_source_gap_seconds": stream_audit.get("max_source_gap_seconds"),
            "shadow_stream_max_source_gap_limit_seconds": max_source_gap_seconds,
            "shadow_stream_gap_check_status": stream_audit.get("status"),
        }
    )
    result["readiness_metrics"] = metrics
    provenance = dict(result.get("provenance", {}))
    input_provenance = dict(provenance.get("input", {}))
    input_provenance.update(
        {
            "source_kind": "local_public_market_stream",
            "source_path": str(snapshots_path),
            "source_sha256": source_sha256,
            "format": "jsonl",
        }
    )
    provenance["input"] = input_provenance
    provenance["stream"] = {
        "schema_version": STREAM_SCHEMA_VERSION,
        "policy_config_hash": policy.config_hash,
        "snapshot_sha256": source_sha256,
        "strategy_input_sha256": _sha256_file(strategy_inputs_path),
        "stream_state_sha256": _sha256_file(stream_state_path),
        "policy_state_sha256": _sha256_file(policy_path),
        "policy_events_sha256": _sha256_file(policy_events_path),
        "max_source_gap_seconds": max_source_gap_seconds,
    }
    result["provenance"] = provenance
    result_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def run_strategy_shadow_stream_sync(**kwargs: Any) -> dict[str, Any]:
    """Synchronous entry point for the CLI and process supervisor."""

    return asyncio.run(run_strategy_shadow_stream(**kwargs))


__all__ = [
    "DEFAULT_ENTRY_BPS",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_TAKE_PROFIT_BPS",
    "DeterministicGridPolicy",
    "POLICY_SCHEMA_VERSION",
    "STREAM_SCHEMA_VERSION",
    "resolve_stream_policy",
    "resolve_stream_max_source_gap",
    "run_strategy_shadow_stream",
    "run_strategy_shadow_stream_sync",
]
