"""Deterministic, local-only strategy shadow driver.

The driver is deliberately smaller than a trading runtime.  It accepts saved
public market snapshots, turns an explicit signal into a plan, sends that
plan to the existing local :class:`PaperBitgetCCXTClient`, and records the
decision in an event envelope compatible with the monitor publisher.

There is no exchange client in this module.  ``PaperBitgetCCXTClient`` is
given an in-memory snapshot client whose methods only read the current
fixture.  The resulting orders and fills are local paper-ledger operations,
never exchange operations.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence

from exchanges.paper import PaperBitgetCCXTClient


SHADOW_SCHEMA_VERSION = "crypto-strategy-shadow-v1"
EVENT_SCHEMA_VERSION = "crypto-runtime-event-v1"
EVIDENCE_SCHEMA_VERSION = "crypto-strategy-shadow-evidence-v1"
DEFAULT_STRATEGY_ID = "crypto-grid-shadow-v1"
DEFAULT_ORDER_AMOUNT = 0.001
MAX_PAPER_BALANCE = 1_000.0
SNAPSHOT_TIMESTAMP_UNIT = "milliseconds"
_FORBIDDEN_KEY_PARTS = frozenset(
    {
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
)
_KNOWN_CONFIG_KEYS = frozenset(
    {
        "strategy_id",
        "quote",
        "leverage",
        "initial_balance",
        "order_amount",
        "amount",
        "long_order_amount",
        "short_order_amount",
        "long_enabled",
        "short_enabled",
        "entry_offset_bps",
        "exit_offset_bps",
        "order_type",
        "shadow_policy",
        "max_source_gap_seconds",
    }
)
_EXECUTION_KEYS = frozenset(
    {
        "amount",
        "qty",
        "price",
        "executed_price",
        "liquidity",
        "part_sequence",
        "fill_id",
        "event_id",
        "enabled",
    }
)
_LOCAL_CALL_TRANSPORTS = frozenset(
    {
        "in_memory",
        "local",
        "memory",
        "paper",
        "saved_public_snapshot",
        "saved_snapshot",
    }
)
_PRIVATE_CALL_MARKERS = frozenset(
    {
        "private",
        "private_api",
        "private_request",
        "private_request_fact",
    }
)
_WRITE_CALL_MARKERS = frozenset(
    {
        "write",
        "write_call",
        "external_write",
        "trading",
        "trading_operation",
    }
)
_TRADING_METHODS = frozenset(
    {
        "cancel_all_orders",
        "cancel_order",
        "close_position",
        "create_order",
        "edit_order",
        "place_order",
        "set_leverage",
        "transfer",
        "withdraw",
    }
)
_PUBLIC_CALLS_WITH_DETAIL = frozenset({"fetch_ticker"})


def canonical_hash(value: Any) -> str:
    """Return the stable SHA-256 used by configs, snapshots and events."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _normalize_operation_fact(
    raw: Any,
    *,
    channel: str,
    index: int,
) -> dict[str, Any]:
    """Normalize an observed call without retaining arbitrary injected data."""

    method = ""
    transport = ""
    marker = ""
    private_request = False
    external_network: bool | None = None
    write_call = channel == "write"
    trading_operation = False
    paper_local = False
    reasons: list[str] = []

    if isinstance(raw, Mapping):
        method = str(raw.get("method", raw.get("operation", raw.get("name", "")))).strip()
        transport = str(
            raw.get("transport", raw.get("source", raw.get("origin", "")))
        ).strip().lower()
        marker = str(raw.get("scope", raw.get("kind", ""))).strip().lower()
        private_request = _flag(
            raw.get("private_request", raw.get("private", raw.get("private_api")))
        )
        if "external_network" in raw:
            external_network = _flag(raw["external_network"])
        write_call = write_call or _flag(
            raw.get("write_call", raw.get("write", raw.get("is_write")))
        )
        trading_operation = _flag(
            raw.get(
                "trading_operation",
                raw.get("is_trading_operation", raw.get("trade")),
            )
        )
        paper_local = _flag(raw.get("paper_local", raw.get("paper")))
    elif isinstance(raw, (list, tuple)) and len(raw) == 2:
        method = str(raw[0]).strip()
        marker = str(raw[1]).strip().lower()
        transport = marker
    else:
        reasons.append("unrecognized_call_fact")

    method_lower = method.lower()
    if not method:
        reasons.append("missing_method")
    if not transport:
        reasons.append("missing_transport")
    if (
        channel == "public"
        and method_lower in _PUBLIC_CALLS_WITH_DETAIL
        and transport not in (
            _PRIVATE_CALL_MARKERS
            | _WRITE_CALL_MARKERS
            | {"external", "network", "remote", "unknown"}
        )
    ):
        # The legacy public-call tuple stores the requested symbol in its
        # second field.  It is a local detail, not a transport marker.
        transport = "local"
        reasons = [reason for reason in reasons if reason != "unrecognized_transport"]
    if marker in _PRIVATE_CALL_MARKERS or "private" in method_lower:
        private_request = True
    if marker in _WRITE_CALL_MARKERS:
        write_call = True
    if marker in {"trading", "trading_operation"}:
        trading_operation = True
    if method_lower in _TRADING_METHODS and not paper_local:
        trading_operation = True

    recognized_transport = transport in _LOCAL_CALL_TRANSPORTS or transport in {
        "external",
        "network",
        "remote",
        "unknown",
    }
    if not recognized_transport:
        reasons.append("unrecognized_transport")
    if external_network is None:
        external_network = transport not in _LOCAL_CALL_TRANSPORTS
    if private_request or write_call or trading_operation:
        # Private/write/trading facts are boundary violations even if an
        # injected test labels their transport as local.
        external_network = bool(external_network)

    return {
        "fact_id": f"{channel}:{index}",
        "channel": channel,
        "method": method or None,
        "transport": transport or None,
        "private_request": private_request,
        "external_network": bool(external_network),
        "write_call": write_call,
        "trading_operation": trading_operation,
        "paper_local": paper_local,
        "recognized": not reasons,
        "reasons": reasons,
    }


def _operation_evidence(
    *,
    public_calls: Sequence[Any],
    write_calls: Sequence[Any],
    client_call_facts: Sequence[Any],
) -> dict[str, Any]:
    """Derive boundary counters and fail-closed violations from call facts."""

    raw_facts = [
        ("public", raw)
        for raw in public_calls
    ] + [
        ("write", raw)
        for raw in write_calls
    ] + [
        ("client", raw)
        for raw in client_call_facts
    ]
    facts = [
        _normalize_operation_fact(raw, channel=channel, index=index)
        for index, (channel, raw) in enumerate(raw_facts)
    ]
    violations: list[str] = []
    for fact in facts:
        fact_id = str(fact["fact_id"])
        for reason in fact["reasons"]:
            violations.append(f"{fact_id}:{reason}")
        if fact["private_request"]:
            violations.append(f"{fact_id}:private_request")
        if fact["external_network"]:
            violations.append(f"{fact_id}:external_network")
        if fact["write_call"]:
            violations.append(f"{fact_id}:write_call")
        if fact["trading_operation"]:
            violations.append(f"{fact_id}:trading_operation")

    return {
        "facts": facts,
        "private_requests": sum(bool(fact["private_request"]) for fact in facts),
        "external_network_calls": sum(bool(fact["external_network"]) for fact in facts),
        "trading_operations_count": sum(
            bool(fact["trading_operation"]) for fact in facts
        ),
        "public_write_calls": sum(
            bool(fact["write_call"]) and fact["channel"] == "write"
            for fact in facts
        ),
        "boundary_violations": violations,
        "status": "failed" if violations else "passed",
    }


def _sha256_file(path: Path) -> str | None:
    """Return a file digest for local provenance, or ``None`` if absent."""

    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_input_provenance(
    provenance: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Keep caller-supplied provenance descriptive and credential-free."""

    if provenance is None:
        return {
            "source_kind": "in_memory_snapshot_iterable",
            "source_path": None,
            "source_sha256": None,
            "format": "python-iterable",
        }
    if not isinstance(provenance, Mapping):
        raise TypeError("input provenance must be an object")
    raw = dict(provenance)
    _reject_credential_fields(raw, path="input_provenance")
    allowed = {"source_kind", "source_path", "source_sha256", "format"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(
            "unsupported strategy shadow provenance fields: " + ", ".join(unknown)
        )
    source_sha256 = raw.get("source_sha256")
    if source_sha256 not in (None, ""):
        source_sha256 = str(source_sha256).lower()
        if re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
            raise ValueError("input provenance source_sha256 must be a SHA-256 digest")
    source_path = raw.get("source_path")
    return {
        "source_kind": str(raw.get("source_kind", "local_saved_snapshot_file")),
        "source_path": str(source_path) if source_path not in (None, "") else None,
        "source_sha256": source_sha256 or None,
        "format": str(raw.get("format", "unknown")),
    }


def _reject_credential_fields(value: Any, *, path: str = "input") -> None:
    """Reject credential-shaped fields without ever persisting their values."""

    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            compact = key.replace("_", "")
            if key in _FORBIDDEN_KEY_PARTS or compact in _FORBIDDEN_KEY_PARTS:
                raise ValueError(f"credential-shaped field is not allowed in {path}")
            _reject_credential_fields(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_credential_fields(nested, path=f"{path}[{index}]")


def _finite(value: Any, *, name: str, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0.0):
        requirement = "finite and positive" if positive else "finite"
        raise ValueError(f"{name} must be {requirement}")
    return number


def normalize_shadow_policy(policy: Mapping[str, Any]) -> dict[str, float]:
    """Validate the stream policy values that affect shadow decisions."""

    if not isinstance(policy, Mapping):
        raise ValueError("shadow_policy must be an object")
    raw = dict(policy)
    unknown = sorted(set(raw) - {"entry_bps", "take_profit_bps", "order_amount"})
    if unknown:
        raise ValueError("unsupported shadow_policy fields: " + ", ".join(unknown))
    entry_bps = _finite(raw.get("entry_bps"), name="shadow_policy.entry_bps", positive=True)
    take_profit_bps = _finite(
        raw.get("take_profit_bps"),
        name="shadow_policy.take_profit_bps",
        positive=True,
    )
    order_amount = _finite(
        raw.get("order_amount"),
        name="shadow_policy.order_amount",
        positive=True,
    )
    if entry_bps > 10_000.0 or take_profit_bps > 10_000.0:
        raise ValueError("shadow policy thresholds must not exceed 10000 bps")
    return {
        "entry_bps": entry_bps,
        "take_profit_bps": take_profit_bps,
        "order_amount": order_amount,
    }


def _integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def _canonical_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("snapshot symbol must not be empty")
    if "/" not in text:
        text = f"{text}/USDT:USDT"
    base, quote_part = text.split("/", 1)
    quote = quote_part.split(":", 1)[0]
    settle = quote_part.split(":", 1)[1] if ":" in quote_part else quote
    if quote != "USDT" or settle != "USDT" or not base:
        raise ValueError(f"strategy shadow supports only USDT-settled swaps, got {value!r}")
    return f"{base}/USDT:USDT"


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{canonical_hash(list(parts))[:24]}"


def normalize_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate and reduce the strategy config to a credential-free contract."""

    raw = dict(config or {})
    _reject_credential_fields(raw, path="config")
    unknown = sorted(set(raw) - _KNOWN_CONFIG_KEYS)
    if unknown:
        raise ValueError(f"unsupported strategy shadow config fields: {', '.join(unknown)}")
    shadow_policy = (
        normalize_shadow_policy(raw["shadow_policy"])
        if "shadow_policy" in raw
        else None
    )

    strategy_id = str(raw.get("strategy_id", DEFAULT_STRATEGY_ID)).strip()
    if not strategy_id or len(strategy_id) > 160:
        raise ValueError("strategy_id must be non-empty and at most 160 characters")
    quote = str(raw.get("quote", "USDT")).strip().upper()
    if quote != "USDT":
        raise ValueError("strategy shadow supports USDT only")
    leverage = _finite(raw.get("leverage", 3.0), name="leverage", positive=True)
    if leverage > 3.0:
        raise ValueError("strategy shadow leverage must not exceed 3x")
    initial_balance = _finite(
        raw.get("initial_balance", MAX_PAPER_BALANCE),
        name="initial_balance",
        positive=True,
    )
    if initial_balance > MAX_PAPER_BALANCE:
        raise ValueError("strategy shadow initial_balance must not exceed 1000 USDT")

    default_amount = _finite(
        raw.get("order_amount", raw.get("amount", DEFAULT_ORDER_AMOUNT)),
        name="order_amount",
        positive=True,
    )
    long_amount = _finite(
        raw.get("long_order_amount", default_amount),
        name="long_order_amount",
        positive=True,
    )
    short_amount = _finite(
        raw.get("short_order_amount", default_amount),
        name="short_order_amount",
        positive=True,
    )
    entry_offset = _finite(
        raw.get("entry_offset_bps", 0.0), name="entry_offset_bps"
    )
    exit_offset = _finite(
        raw.get("exit_offset_bps", 0.0), name="exit_offset_bps"
    )
    if entry_offset < 0.0 or exit_offset < 0.0 or entry_offset > 10_000.0 or exit_offset > 10_000.0:
        raise ValueError("entry and exit offsets must be between 0 and 10000 bps")
    order_type = str(raw.get("order_type", "limit")).strip().lower()
    if order_type != "limit":
        raise ValueError("strategy shadow permits limit plans only")

    normalized = {
        "strategy_id": strategy_id,
        "quote": quote,
        "leverage": leverage,
        "initial_balance": initial_balance,
        "long_order_amount": long_amount,
        "short_order_amount": short_amount,
        "long_enabled": bool(raw.get("long_enabled", True)),
        "short_enabled": bool(raw.get("short_enabled", True)),
        "entry_offset_bps": entry_offset,
        "exit_offset_bps": exit_offset,
        "order_type": order_type,
    }
    if "max_source_gap_seconds" in raw:
        normalized["max_source_gap_seconds"] = _finite(
            raw["max_source_gap_seconds"],
            name="max_source_gap_seconds",
            positive=True,
        )
    if shadow_policy is not None:
        normalized["shadow_policy"] = shadow_policy
    return normalized


def _signal_value(snapshot: Mapping[str, Any], position_side: str) -> Any:
    for container_name in ("signals", "intent"):
        container = snapshot.get(container_name)
        if isinstance(container, Mapping) and position_side in container:
            return container[position_side]
    direct = snapshot.get(f"{position_side}_signal")
    if direct not in (None, ""):
        return direct
    generic = snapshot.get("signal", snapshot.get("direction"))
    if isinstance(generic, Mapping):
        return generic.get(position_side)
    return generic


def _normalize_action(value: Any, position_side: str) -> str:
    if isinstance(value, bool):
        return "enter" if value else "hold"
    text = str(value or "").strip().lower()
    if text in {"enter", "open", "entry", "buy" if position_side == "long" else "sell", position_side}:
        return "enter"
    if text in {"exit", "close", "flatten", "reduce"}:
        return "exit"
    if text in {"hold", "none", "skip", "", "false", "0"}:
        return "hold"
    raise ValueError(f"unsupported {position_side} strategy signal: {value!r}")


def normalize_snapshot(snapshot: Mapping[str, Any], *, index: int = 0) -> dict[str, Any]:
    """Validate one saved public snapshot and drop fields the driver does not use."""

    if not isinstance(snapshot, Mapping):
        raise TypeError("each strategy shadow snapshot must be an object")
    _reject_credential_fields(snapshot, path=f"snapshot[{index}]")
    timestamp = _integer(
        snapshot.get("timestamp", snapshot.get("ts")),
        name="snapshot timestamp",
    )
    symbol = _canonical_symbol(snapshot.get("symbol"))
    bid = _finite(snapshot.get("bid"), name="snapshot bid", positive=True)
    ask = _finite(snapshot.get("ask"), name="snapshot ask", positive=True)
    last = _finite(snapshot.get("last", (bid + ask) / 2.0), name="snapshot last", positive=True)
    if bid > ask:
        raise ValueError("snapshot bid must not exceed ask")
    snapshot_id = str(
        snapshot.get("snapshot_id", snapshot.get("id", f"{timestamp}:{symbol}"))
    ).strip()
    if not snapshot_id:
        raise ValueError("snapshot_id must not be empty")

    result: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "timestamp": timestamp,
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "last": last,
    }
    for key in ("mark_price", "index_price"):
        if snapshot.get(key) not in (None, ""):
            result[key] = _finite(snapshot[key], name=f"snapshot {key}", positive=True)

    signals: dict[str, Any] = {}
    for position_side in ("long", "short"):
        value = _signal_value(snapshot, position_side)
        if isinstance(value, (str, bool, int, float)) or value is None:
            signals[position_side] = value
        else:
            raise ValueError(f"{position_side} signal must be scalar")
    result["signals"] = signals

    execution = snapshot.get("execution")
    if execution is not None:
        if not isinstance(execution, Mapping):
            raise ValueError("snapshot execution must be an object")
        unknown_execution = sorted(set(execution) - _EXECUTION_KEYS)
        if unknown_execution:
            raise ValueError(
                "unsupported snapshot execution fields: " + ", ".join(unknown_execution)
            )
        safe_execution: dict[str, Any] = {}
        for key in _EXECUTION_KEYS:
            if key not in execution:
                continue
            value = execution[key]
            if key in {"amount", "qty", "price", "executed_price"}:
                safe_execution[key] = _finite(value, name=f"execution {key}", positive=True)
            elif key == "part_sequence":
                safe_execution[key] = _integer(value, name="execution part_sequence")
            elif key == "enabled":
                safe_execution[key] = bool(value)
            else:
                safe_execution[key] = str(value)
        result["execution"] = safe_execution

    result["source_hash"] = canonical_hash(result)
    return result


def _coverage_from_snapshot_provenance(
    snapshot_provenance: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive coverage only from timestamps of normalized snapshots."""

    entries = [
        {
            "snapshot_id": str(snapshot_id),
            "timestamp": int(details["timestamp"]),
            "source_hash": str(details["source_hash"]),
        }
        for snapshot_id, details in snapshot_provenance.items()
    ]
    entries.sort(key=lambda item: (item["timestamp"], item["snapshot_id"]))
    timestamps = [entry["timestamp"] for entry in entries]
    if not timestamps:
        return {
            "timestamp_unit": SNAPSHOT_TIMESTAMP_UNIT,
            "timestamp_count": 0,
            "unique_timestamp_count": 0,
            "first_timestamp_ms": None,
            "last_timestamp_ms": None,
            "covered_duration_ms": 0,
            "covered_duration_seconds": 0.0,
            "covered_duration_hours": 0.0,
            "duration_formula": "max(normalized_snapshot_timestamps_ms) - min(normalized_snapshot_timestamps_ms)",
            "normalized_snapshot_timestamp_hash": canonical_hash([]),
            "normalized_snapshot_provenance_hash": canonical_hash([]),
        }
    first_timestamp = min(timestamps)
    last_timestamp = max(timestamps)
    duration_ms = last_timestamp - first_timestamp
    return {
        "timestamp_unit": SNAPSHOT_TIMESTAMP_UNIT,
        "timestamp_count": len(timestamps),
        "unique_timestamp_count": len(set(timestamps)),
        "first_timestamp_ms": first_timestamp,
        "last_timestamp_ms": last_timestamp,
        "covered_duration_ms": duration_ms,
        "covered_duration_seconds": duration_ms / 1_000.0,
        "covered_duration_hours": duration_ms / 3_600_000.0,
        "duration_formula": "max(normalized_snapshot_timestamps_ms) - min(normalized_snapshot_timestamps_ms)",
        "normalized_snapshot_timestamp_hash": canonical_hash(timestamps),
        "normalized_snapshot_provenance_hash": canonical_hash(entries),
    }


def _market_row(symbol: str, price: float) -> dict[str, Any]:
    base = symbol.split("/", 1)[0]
    return {
        "id": symbol.replace("/", "").replace(":", "_"),
        "symbol": symbol,
        "base": base,
        "quote": "USDT",
        "settle": "USDT",
        "type": "swap",
        "swap": True,
        "linear": True,
        "inverse": False,
        "active": True,
        "precision": {"amount": 0.001, "price": 0.1},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 5.1}},
        "contractSize": 1.0,
        "maker": 0.0002,
        "taker": 0.00055,
        "info": {"source": "saved_public_snapshot", "last": float(price)},
    }


class _SnapshotPublicClient:
    """The only object supplied as PaperBitgetCCXTClient.public_client."""

    def __init__(self, symbols: Sequence[str], initial_quotes: Mapping[str, Mapping[str, Any]]):
        self.calls: list[tuple[str, str]] = []
        self.write_calls: list[tuple[str, str]] = []
        self.quotes: dict[str, dict[str, Any]] = {
            symbol: dict(initial_quotes[symbol]) for symbol in symbols
        }
        self.markets = {
            symbol: _market_row(symbol, float(self.quotes[symbol]["last"]))
            for symbol in symbols
        }

    def set_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        self.quotes[str(snapshot["symbol"])] = {
            "symbol": str(snapshot["symbol"]),
            "timestamp": int(snapshot["timestamp"]),
            "bid": float(snapshot["bid"]),
            "ask": float(snapshot["ask"]),
            "last": float(snapshot["last"]),
            "info": {
                key: float(snapshot[key])
                for key in ("mark_price", "index_price")
                if key in snapshot
            },
        }

    async def load_markets(self, reload: bool = True) -> dict[str, dict[str, Any]]:
        self.calls.append(("load_markets", "local"))
        return copy.deepcopy(self.markets)

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        self.calls.append(("fetch_ticker", str(symbol)))
        if symbol not in self.quotes:
            raise KeyError(f"no saved public snapshot for {symbol}")
        return copy.deepcopy(self.quotes[symbol])

    async def close(self) -> None:
        self.calls.append(("close", "local"))


class StrategyShadowDriver:
    """Replay saved public snapshots through strategy intent and paper ledger."""

    def __init__(
        self,
        *,
        checkpoint_path: str | os.PathLike[str],
        config: Mapping[str, Any] | None = None,
        events_path: str | os.PathLike[str] | None = None,
        ledger_path: str | os.PathLike[str] | None = None,
        input_provenance: Mapping[str, Any] | None = None,
    ) -> None:
        self.config = normalize_config(config)
        self.strategy_id = self.config["strategy_id"]
        self.config_hash = canonical_hash(self.config)
        self.checkpoint_path = Path(checkpoint_path).expanduser().resolve()
        self.events_path = Path(events_path or self.checkpoint_path.with_name("events.ndjson")).expanduser().resolve()
        self.ledger_path = Path(ledger_path or self.checkpoint_path.with_name("paper_ledger.json")).expanduser().resolve()
        self._input_provenance = _normalize_input_provenance(input_provenance)
        self._events: list[dict[str, Any]] = []
        self._event_ids: dict[str, dict[str, Any]] = {}
        self._plans: dict[str, dict[str, Any]] = {}
        self._snapshot_provenance: dict[str, dict[str, Any]] = {}
        self._run_normalized_snapshots: list[dict[str, Any]] = []
        self._restart_operations: list[dict[str, Any]] = []
        self._replay_operations: list[dict[str, Any]] = []
        self._processed_snapshot_ids: set[str] = set()
        self._duplicate_snapshot_count = 0
        self._seq = 0
        self._client: PaperBitgetCCXTClient | None = None
        self._public: _SnapshotPublicClient | None = None
        self._client_call_facts: list[dict[str, Any]] = []
        self._symbols: tuple[str, ...] = ()
        self._driver_started_monotonic = time.monotonic()
        self._initial_artifacts = self._capture_artifacts()
        self._pending_restart_index: int | None = None
        self._load_events()
        self._load_checkpoint()
        self._register_restart_if_state_preexisted()

    @property
    def client(self) -> PaperBitgetCCXTClient | None:
        return self._client

    @property
    def public_client(self) -> _SnapshotPublicClient | None:
        return self._public

    def _record_client_call(
        self,
        method: str,
        *,
        paper_local: bool = False,
        write: bool = False,
        trading_operation: bool = False,
    ) -> None:
        self._client_call_facts.append(
            {
                "method": str(method),
                "transport": "local" if paper_local else "unknown",
                "paper_local": bool(paper_local),
                "write_call": bool(write),
                "trading_operation": bool(trading_operation),
                "external_network": not paper_local,
            }
        )

    def _capture_artifacts(self) -> dict[str, dict[str, Any]]:
        paths = {
            "checkpoint": self.checkpoint_path,
            "events": self.events_path,
            "ledger": self.ledger_path,
        }
        return {
            name: {
                "path": str(path),
                "exists": path.is_file(),
                "sha256": _sha256_file(path),
            }
            for name, path in paths.items()
        }

    def _register_restart_if_state_preexisted(self) -> None:
        existing = {
            name: details
            for name, details in self._initial_artifacts.items()
            if bool(details["exists"])
        }
        if not existing:
            return
        operation = {
            "operation_id": f"restart-{len(self._restart_operations) + 1:04d}",
            "operation": "restart",
            "status": "observed",
            "recovery_completed": False,
            "evidence_source": "pre-existing_local_shadow_artifacts",
            "preexisting_artifacts": copy.deepcopy(existing),
            "event_count_before": len(self._events),
            "processed_snapshot_count_before": len(self._processed_snapshot_ids),
        }
        self._restart_operations.append(operation)
        self._pending_restart_index = len(self._restart_operations) - 1

    def _complete_pending_restart(self, snapshot: Mapping[str, Any]) -> None:
        if self._pending_restart_index is None:
            return
        operation = self._restart_operations[self._pending_restart_index]
        recovery_duration_ms = round(
            max(0.0, time.monotonic() - self._driver_started_monotonic) * 1_000.0,
            3,
        )
        operation.update(
            {
                "status": "completed",
                "recovery_completed": True,
                "recovered_at_snapshot_id": str(snapshot["snapshot_id"]),
                "recovery_duration_ms": recovery_duration_ms,
                "recovery_duration_seconds": round(recovery_duration_ms / 1_000.0, 6),
                "recovered_event_count": len(self._events),
                "recovered_processed_snapshot_count": len(self._processed_snapshot_ids),
            }
        )
        self._pending_restart_index = None

    def _load_events(self) -> None:
        if not self.events_path.is_file():
            return
        with self.events_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid strategy shadow event at line {line_number}") from exc
                if not isinstance(event, dict) or event.get("schema_version") != SHADOW_SCHEMA_VERSION:
                    raise ValueError(f"unsupported strategy shadow event at line {line_number}")
                event_id = str(event.get("event_id") or "").strip()
                if not event_id:
                    raise ValueError(f"strategy shadow event has no event_id at line {line_number}")
                previous = self._event_ids.get(event_id)
                if previous is not None and previous != event:
                    raise ValueError(f"strategy shadow event ID conflict: {event_id}")
                payload = event.get("payload")
                if not isinstance(payload, Mapping):
                    raise ValueError(f"strategy shadow event payload is invalid at line {line_number}")
                if event.get("payload_hash") != canonical_hash(payload):
                    raise ValueError(f"strategy shadow event payload hash mismatch at line {line_number}")
                self._event_ids[event_id] = event
                self._events.append(event)
                self._seq = max(self._seq, int(event.get("seq", 0)))
                order_id = payload.get("order_id")
                plan = payload.get("plan")
                if event.get("kind") == "order.planned" and order_id and isinstance(plan, Mapping):
                    self._plans[str(order_id)] = copy.deepcopy(dict(plan))
                if event.get("kind") == "market.snapshot":
                    snapshot_id = payload.get("snapshot_id")
                    if snapshot_id not in (None, ""):
                        normalized_snapshot_id = str(snapshot_id)
                        snapshot_details = {
                            "timestamp": int(event.get("ts")),
                            "source_hash": str(payload.get("source_hash") or ""),
                        }
                        previous_snapshot = self._snapshot_provenance.get(normalized_snapshot_id)
                        if previous_snapshot is not None and previous_snapshot != snapshot_details:
                            raise ValueError(
                                f"strategy shadow snapshot provenance conflict: {normalized_snapshot_id}"
                            )
                        self._snapshot_provenance[normalized_snapshot_id] = snapshot_details
                if event.get("kind") == "snapshot.completed":
                    snapshot_id = payload.get("snapshot_id")
                    if snapshot_id not in (None, ""):
                        self._processed_snapshot_ids.add(str(snapshot_id))

    def _load_checkpoint(self) -> None:
        if not self.checkpoint_path.is_file():
            return
        try:
            checkpoint = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid strategy shadow checkpoint") from exc
        if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != SHADOW_SCHEMA_VERSION:
            raise ValueError("unsupported strategy shadow checkpoint")
        if checkpoint.get("config_hash") not in (None, self.config_hash):
            raise ValueError("strategy shadow checkpoint belongs to a different config")
        if checkpoint.get("strategy_id") not in (None, self.strategy_id):
            raise ValueError("strategy shadow checkpoint belongs to a different strategy")
        processed = checkpoint.get("processed_snapshot_ids", [])
        if not isinstance(processed, list) or any(not isinstance(item, str) for item in processed):
            raise ValueError("strategy shadow checkpoint has invalid processed snapshots")
        self._processed_snapshot_ids.update(processed)
        symbols = checkpoint.get("symbols", [])
        if isinstance(symbols, list):
            self._symbols = tuple(sorted(str(symbol) for symbol in symbols))
        self._seq = max(self._seq, int(checkpoint.get("next_seq", self._seq)))
        for key, target in (
            ("restart_operations", self._restart_operations),
            ("replay_operations", self._replay_operations),
        ):
            operations = checkpoint.get(key, [])
            if not isinstance(operations, list) or any(not isinstance(item, dict) for item in operations):
                raise ValueError(f"strategy shadow checkpoint has invalid {key}")
            target.extend(copy.deepcopy(operations))
        snapshot_provenance = checkpoint.get("snapshot_provenance", [])
        if not isinstance(snapshot_provenance, list):
            raise ValueError("strategy shadow checkpoint has invalid snapshot provenance")
        for item in snapshot_provenance:
            if not isinstance(item, Mapping):
                raise ValueError("strategy shadow checkpoint has invalid snapshot provenance entry")
            snapshot_id = str(item.get("snapshot_id") or "")
            if not snapshot_id:
                raise ValueError("strategy shadow checkpoint snapshot provenance has no snapshot_id")
            details = {
                "timestamp": int(item["timestamp"]),
                "source_hash": str(item["source_hash"]),
            }
            previous = self._snapshot_provenance.get(snapshot_id)
            if previous is not None and previous != details:
                raise ValueError(f"strategy shadow snapshot provenance conflict: {snapshot_id}")
            self._snapshot_provenance[snapshot_id] = details
        self._duplicate_snapshot_count = len(self._replay_operations)

    async def _ensure_client(self, snapshots: Sequence[Mapping[str, Any]]) -> None:
        symbols = tuple(sorted({str(snapshot["symbol"]) for snapshot in snapshots}))
        if not symbols:
            raise ValueError("strategy shadow requires at least one snapshot")
        if self._client is not None:
            if not set(symbols).issubset(set(self._symbols)):
                raise ValueError("new symbols cannot be added after paper state initialization")
            return
        initial_quotes = {
            str(snapshot["symbol"]): {
                "symbol": str(snapshot["symbol"]),
                "timestamp": int(snapshot["timestamp"]),
                "bid": float(snapshot["bid"]),
                "ask": float(snapshot["ask"]),
                "last": float(snapshot["last"]),
                "info": {},
            }
            for snapshot in snapshots
        }
        self._public = _SnapshotPublicClient(symbols, initial_quotes)
        self._client = PaperBitgetCCXTClient(
            {
                "live": {
                    "paper_symbols": [symbol.split("/", 1)[0] for symbol in symbols],
                    "leverage": self.config["leverage"],
                },
                "_paper_mode": True,
            },
            {
                "exchange": "bitget",
                "quote": "USDT",
                "paper": True,
                "paper_mode": True,
                "paper_symbols": [symbol.split("/", 1)[0] for symbol in symbols],
                "paper_initial_balance": self.config["initial_balance"],
                "paper_leverage": self.config["leverage"],
                "paper_state_path": str(self.ledger_path),
            },
            public_client=self._public,
            initial_balance=self.config["initial_balance"],
            clock_ms=int(snapshots[0]["timestamp"]),
            state_path=self.ledger_path,
        )
        self._record_client_call("load_markets", paper_local=True)
        await self._client.load_markets()
        self._symbols = symbols
        self._write_checkpoint()

    def _write_checkpoint(self) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SHADOW_SCHEMA_VERSION,
            "strategy_id": self.strategy_id,
            "config_hash": self.config_hash,
            "next_seq": self._seq,
            "processed_snapshot_ids": sorted(self._processed_snapshot_ids),
            "symbols": list(self._symbols),
            "event_count": len(self._events),
            "events_path": str(self.events_path),
            "ledger_path": str(self.ledger_path),
            "restart_operations": copy.deepcopy(self._restart_operations),
            "replay_operations": copy.deepcopy(self._replay_operations),
            "snapshot_provenance": [
                {
                    "snapshot_id": snapshot_id,
                    "timestamp": int(details["timestamp"]),
                    "source_hash": str(details["source_hash"]),
                }
                for snapshot_id, details in sorted(self._snapshot_provenance.items())
            ],
        }
        temporary = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, self.checkpoint_path)

    def _base_payload(
        self,
        *,
        snapshot: Mapping[str, Any],
        position_side: str | None,
        cycle_id: str | None,
        order_id: str | None,
        plan: Mapping[str, Any] | None,
        execution: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "cycle_id": cycle_id,
            "trade_cycle_id": cycle_id,
            "config_hash": self.config_hash,
            "order_id": order_id,
            "snapshot_id": snapshot.get("snapshot_id"),
            "symbol": snapshot.get("symbol"),
            "position_side": position_side,
            "plan": copy.deepcopy(dict(plan)) if isinstance(plan, Mapping) else plan,
            "execution": copy.deepcopy(dict(execution)) if isinstance(execution, Mapping) else execution,
            "execution_fact": copy.deepcopy(dict(execution)) if isinstance(execution, Mapping) else execution,
            "source": "saved_public_snapshot",
            "source_hash": snapshot.get("source_hash"),
        }

    def _emit(
        self,
        kind: str,
        *,
        event_id: str,
        timestamp: int,
        tags: Iterable[str],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if event_id in self._event_ids:
            existing = self._event_ids[event_id]
            if canonical_hash(existing.get("payload")) != canonical_hash(payload):
                raise ValueError(f"strategy shadow event ID conflict: {event_id}")
            return copy.deepcopy(existing)
        for required in (
            "strategy_id",
            "cycle_id",
            "config_hash",
            "order_id",
            "plan",
            "execution",
        ):
            if required not in payload:
                raise ValueError(f"shadow event payload is missing {required}")
        clean_payload = copy.deepcopy(dict(payload))
        event = {
            "schema_version": SHADOW_SCHEMA_VERSION,
            "event_schema": EVENT_SCHEMA_VERSION,
            "event_id": event_id,
            "ts": int(timestamp),
            "seq": self._seq + 1,
            "kind": str(kind),
            "tags": [str(tag) for tag in tags],
            "exchange": "bitget",
            "user": "strategy-shadow",
            "payload": clean_payload,
            "payload_hash": canonical_hash(clean_payload),
        }
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._seq += 1
        self._events.append(event)
        self._event_ids[event_id] = event
        payload_plan = clean_payload.get("plan")
        if kind == "order.planned" and clean_payload.get("order_id") and isinstance(payload_plan, Mapping):
            self._plans[str(clean_payload["order_id"])] = copy.deepcopy(dict(payload_plan))
        self._write_checkpoint()
        return copy.deepcopy(event)

    def _position_size(self, symbol: str, position_side: str) -> float:
        if self._client is None:
            return 0.0
        position = self._client.positions.get((symbol, position_side), {})
        try:
            return max(0.0, float(position.get("size", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _open_order_for_side(self, symbol: str, position_side: str) -> dict[str, Any] | None:
        if self._client is None:
            return None
        for order in self._client.open_orders.values():
            if str(order.get("symbol")) != symbol:
                continue
            info = order.get("info") if isinstance(order.get("info"), Mapping) else {}
            if str(info.get("posSide", info.get("positionSide", ""))).lower() == position_side:
                return order
        return None

    def _intent(self, snapshot: Mapping[str, Any], position_side: str) -> dict[str, Any]:
        enabled = bool(self.config[f"{position_side}_enabled"])
        if not enabled:
            return {"action": "hold", "reason": "position_side_disabled", "position_side": position_side}
        action = _normalize_action(snapshot["signals"].get(position_side), position_side)
        return {"action": action, "reason": "signal", "position_side": position_side}

    def _plan(self, snapshot: Mapping[str, Any], position_side: str, intent: Mapping[str, Any]) -> dict[str, Any]:
        action = str(intent["action"])
        symbol = str(snapshot["symbol"])
        cycle_id = f"{self.strategy_id}:{symbol}:{position_side}:{snapshot['snapshot_id']}"
        exchange_side = (
            "buy"
            if (position_side == "long" and action == "enter")
            or (position_side == "short" and action == "exit")
            else "sell"
        )
        if action == "hold":
            return {
                "status": "skipped",
                "action": "hold",
                "reason": str(intent["reason"]),
                "position_side": position_side,
                "symbol": symbol,
            }
        existing_order = self._open_order_for_side(symbol, position_side)
        position_size = self._position_size(symbol, position_side)
        if action == "enter" and (existing_order is not None or position_size > 1e-12):
            return {
                "status": "skipped",
                "action": "enter",
                "reason": "existing_position_or_order",
                "position_side": position_side,
                "symbol": symbol,
                "existing_order_id": existing_order.get("id") if existing_order else None,
                "position_size": position_size,
            }
        if action == "exit" and position_size <= 1e-12:
            return {
                "status": "skipped",
                "action": "exit",
                "reason": "no_position_to_close",
                "position_side": position_side,
                "symbol": symbol,
            }

        if action == "exit":
            amount = position_size
            offset = self.config["exit_offset_bps"] / 10_000.0
            reference_price = float(snapshot["bid"] if position_side == "long" else snapshot["ask"])
            planned_price = reference_price * (
                1.0 - offset if position_side == "long" else 1.0 + offset
            )
            reduce_only = True
        else:
            amount = self.config[f"{position_side}_order_amount"]
            offset = self.config["entry_offset_bps"] / 10_000.0
            reference_price = float(snapshot["ask"] if position_side == "long" else snapshot["bid"])
            planned_price = reference_price * (
                1.0 - offset if position_side == "long" else 1.0 + offset
            )
            reduce_only = False
        order_id = _stable_id(
            "shadow-order",
            self.strategy_id,
            symbol,
            position_side,
            snapshot["snapshot_id"],
            action,
        )
        return {
            "status": "planned",
            "action": action,
            "symbol": symbol,
            "position_side": position_side,
            "exchange_side": exchange_side,
            "order_type": self.config["order_type"],
            "amount": float(amount),
            "planned_price": float(planned_price),
            "reference_price": float(reference_price),
            "reduce_only": reduce_only,
            "order_id": order_id,
            "cycle_id": cycle_id,
        }

    @staticmethod
    def _order_payload(order: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
        info = order.get("info") if isinstance(order.get("info"), Mapping) else {}
        return {
            "id": order.get("id"),
            "order_id": order.get("id"),
            "custom_id": info.get("clientOid", order.get("clientOrderId")),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "position_side": str(info.get("posSide", info.get("positionSide", ""))).lower(),
            "qty": float(order.get("amount", 0.0) or 0.0),
            "price": float(order.get("price", plan.get("planned_price", 0.0)) or 0.0),
            "planned_price": float(plan.get("planned_price", 0.0)),
            "reduce_only": bool(order.get("reduceOnly") or info.get("reduceOnly")),
            "pb_order_type": str(order.get("type", "limit")).lower(),
            "status": order.get("status"),
            "filled": float(order.get("filled", 0.0) or 0.0),
            "remaining": float(order.get("remaining", 0.0) or 0.0),
            "source": "strategy_shadow",
        }

    def _execution_from_order(self, order: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "executed": str(order.get("status", "")).lower() == "closed",
            "status": str(order.get("status", "unknown")),
            "filled": float(order.get("filled", 0.0) or 0.0),
            "remaining": float(order.get("remaining", 0.0) or 0.0),
        }

    def _fill_payload(
        self,
        fill: Mapping[str, Any],
        *,
        snapshot: Mapping[str, Any],
    ) -> tuple[str, str | None, dict[str, Any], dict[str, Any]]:
        info = fill.get("info") if isinstance(fill.get("info"), Mapping) else {}
        order_id = str(fill.get("order") or info.get("order_id") or "")
        plan = self._plans.get(order_id)
        if plan is None:
            plan = {
                "status": "recovered",
                "symbol": fill.get("symbol"),
                "position_side": str(info.get("posSide", info.get("positionSide", ""))).lower(),
                "action": "unknown",
                "order_id": order_id,
                "planned_price": info.get("reference_price", fill.get("price")),
            }
        cycle_id = info.get("cycle_id") or plan.get("cycle_id")
        typed_fee = info.get("typed_fee")
        if not isinstance(typed_fee, Mapping):
            fee = fill.get("fee") if isinstance(fill.get("fee"), Mapping) else {}
            typed_fee = {
                "amount": abs(float(fee.get("cost", 0.0) or 0.0)),
                "currency": str(fee.get("currency", "USDT") or "USDT").upper(),
                "rate": abs(float(fee.get("rate", 0.0) or 0.0)),
            }
        typed_slippage = info.get("typed_slippage")
        if not isinstance(typed_slippage, Mapping):
            typed_slippage = {
                "reference_price": info.get("reference_price", fill.get("price")),
                "executed_price": fill.get("price"),
            }
        execution = {
            "executed": True,
            "status": "filled",
            "fill_id": str(fill.get("id")),
            "amount": float(fill.get("amount", 0.0) or 0.0),
            "price": float(fill.get("price", 0.0) or 0.0),
            "executed_price": float(fill.get("price", 0.0) or 0.0),
            "liquidity": str(info.get("liquidity", fill.get("liquidity", ""))),
            "part_sequence": int(info.get("part_sequence", 1)),
            "quantity_before": float(info.get("quantity_before", 0.0) or 0.0),
            "quantity_after": float(info.get("quantity_after", 0.0) or 0.0),
            "fee": dict(typed_fee),
            "slippage": dict(typed_slippage),
        }
        return order_id, str(cycle_id) if cycle_id not in (None, "") else None, dict(plan), execution

    def _emit_new_fills(self, *, snapshot: Mapping[str, Any]) -> list[str]:
        if self._client is None:
            return []
        event_ids: list[str] = []
        for fill in list(self._client.fills):
            fill_id = str(fill.get("id") or "").strip()
            if not fill_id:
                continue
            event_id = f"fill:{fill_id}"
            if event_id in self._event_ids:
                continue
            order_id, cycle_id, plan, execution = self._fill_payload(fill, snapshot=snapshot)
            info = fill.get("info") if isinstance(fill.get("info"), Mapping) else {}
            payload = self._base_payload(
                snapshot=snapshot,
                position_side=str(info.get("posSide", info.get("positionSide", ""))).lower() or None,
                cycle_id=cycle_id,
                order_id=order_id or None,
                plan=plan,
                execution=execution,
            )
            payload.update(
                {
                    "fill_id": fill_id,
                    "timestamp": int(fill.get("timestamp", snapshot["timestamp"])),
                    "side": fill.get("side"),
                    "amount": float(fill.get("amount", 0.0) or 0.0),
                    "price": float(fill.get("price", 0.0) or 0.0),
                    "fee": copy.deepcopy(execution["fee"]),
                    "slippage": copy.deepcopy(execution["slippage"]),
                }
            )
            self._emit(
                "order.filled",
                event_id=event_id,
                timestamp=int(fill.get("timestamp", snapshot["timestamp"])),
                tags=("order", "fill", "shadow"),
                payload=payload,
            )
            event_ids.append(event_id)
        return event_ids

    def _record_replay_operation(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        snapshot_id = str(snapshot["snapshot_id"])
        operation = {
            "operation_id": f"replay-{len(self._replay_operations) + 1:04d}",
            "operation": "replay",
            "status": "completed",
            "result": "no_op",
            "reason": "snapshot_already_processed",
            "snapshot_id": snapshot_id,
            "timestamp": int(snapshot["timestamp"]),
            "source_hash": str(snapshot["source_hash"]),
            "prior_completion_event_id": f"completed:{snapshot_id}",
            "evidence_source": "normalized_snapshot_identity_and_checkpoint",
        }
        self._replay_operations.append(operation)
        self._duplicate_snapshot_count = len(self._replay_operations)
        self._write_checkpoint()
        return copy.deepcopy(operation)

    async def process_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Process one snapshot; replaying its identity is a no-op."""

        normalized = normalize_snapshot(snapshot)
        self._run_normalized_snapshots.append(copy.deepcopy(normalized))
        snapshot_id = str(normalized["snapshot_id"])
        if snapshot_id in self._processed_snapshot_ids:
            known_provenance = self._snapshot_provenance.get(snapshot_id)
            if (
                known_provenance is not None
                and known_provenance["source_hash"] != normalized["source_hash"]
            ):
                raise ValueError(f"strategy shadow snapshot ID conflict: {snapshot_id}")
            replay_operation = self._record_replay_operation(normalized)
            return {
                "snapshot_id": snapshot_id,
                "duplicate": True,
                "event_ids": [],
                "planned_orders": 0,
                "fills": 0,
                "replay_operation_id": replay_operation["operation_id"],
            }
        await self._ensure_client([normalized])
        assert self._client is not None and self._public is not None
        self._complete_pending_restart(normalized)
        if str(normalized["symbol"]) not in self._symbols:
            raise ValueError(f"snapshot symbol {normalized['symbol']} is not configured")
        self._public.set_snapshot(normalized)
        event_ids: list[str] = []
        self._record_client_call("fetch_ticker", paper_local=True)
        await self._client.fetch_ticker(str(normalized["symbol"]))
        market_payload = self._base_payload(
            snapshot=normalized,
            position_side=None,
            cycle_id=None,
            order_id=None,
            plan=None,
            execution=None,
        )
        market_payload.update(
            {
                "bid": normalized["bid"],
                "ask": normalized["ask"],
                "last": normalized["last"],
                "mark_price": normalized.get("mark_price"),
                "index_price": normalized.get("index_price"),
            }
        )
        self._snapshot_provenance[snapshot_id] = {
            "timestamp": int(normalized["timestamp"]),
            "source_hash": str(normalized["source_hash"]),
        }
        event_ids.append(
            self._emit(
                "market.snapshot",
                event_id=f"market:{snapshot_id}",
                timestamp=int(normalized["timestamp"]),
                tags=("market", "public", "shadow"),
                payload=market_payload,
            )["event_id"]
        )
        event_ids.extend(self._emit_new_fills(snapshot=normalized))
        planned_count = 0

        for position_side in ("long", "short"):
            intent = self._intent(normalized, position_side)
            cycle_id = f"{self.strategy_id}:{normalized['symbol']}:{position_side}:{snapshot_id}"
            intent_payload = self._base_payload(
                snapshot=normalized,
                position_side=position_side,
                cycle_id=cycle_id,
                order_id=None,
                plan={"action": intent["action"], "status": "intent"},
                execution=None,
            )
            intent_payload["intent"] = dict(intent)
            event_ids.append(
                self._emit(
                    "strategy.intent",
                    event_id=f"intent:{snapshot_id}:{position_side}",
                    timestamp=int(normalized["timestamp"]),
                    tags=("strategy", "intent", position_side, "shadow"),
                    payload=intent_payload,
                )["event_id"]
            )

            plan = self._plan(normalized, position_side, intent)
            order_id = plan.get("order_id") if plan.get("status") == "planned" else None
            plan_payload = self._base_payload(
                snapshot=normalized,
                position_side=position_side,
                cycle_id=cycle_id,
                order_id=str(order_id) if order_id else None,
                plan=plan,
                execution=None,
            )
            event_ids.append(
                self._emit(
                    "order.planned",
                    event_id=f"plan:{snapshot_id}:{position_side}",
                    timestamp=int(normalized["timestamp"]),
                    tags=("order", "plan", position_side, "shadow"),
                    payload=plan_payload,
                )["event_id"]
            )
            if plan.get("status") != "planned":
                execution = {
                    "executed": False,
                    "status": "skipped",
                    "reason": plan.get("reason"),
                }
                decision_payload = self._base_payload(
                    snapshot=normalized,
                    position_side=position_side,
                    cycle_id=cycle_id,
                    order_id=None,
                    plan=plan,
                    execution=execution,
                )
                event_ids.append(
                    self._emit(
                        "order.execution_decision",
                        event_id=f"decision:{snapshot_id}:{position_side}",
                        timestamp=int(normalized["timestamp"]),
                        tags=("order", "decision", position_side, "shadow"),
                        payload=decision_payload,
                    )["event_id"]
                )
                continue

            planned_count += 1
            self._plans[str(order_id)] = copy.deepcopy(plan)
            params = {
                "posSide": position_side,
                "reduceOnly": bool(plan["reduce_only"]),
                "orderId": str(order_id),
                "clientOid": str(order_id),
                "eventId": f"paper-order:{order_id}",
                "strategy_id": self.strategy_id,
                "cycle_id": cycle_id,
                "config_hash": self.config_hash,
                "reference_price": float(plan["reference_price"]),
            }
            self._record_client_call("create_order", paper_local=True)
            order = await self._client.create_order(
                str(normalized["symbol"]),
                str(plan["order_type"]),
                str(plan["exchange_side"]),
                float(plan["amount"]),
                price=float(plan["planned_price"]),
                params=params,
            )
            order_payload = self._base_payload(
                snapshot=normalized,
                position_side=position_side,
                cycle_id=cycle_id,
                order_id=str(order_id),
                plan=plan,
                execution=self._execution_from_order(order),
            )
            order_payload["order"] = self._order_payload(order, plan)
            event_ids.append(
                self._emit(
                    "order.opened",
                    event_id=f"opened:{order_id}",
                    timestamp=int(normalized["timestamp"]),
                    tags=("order", "open", position_side, "shadow"),
                    payload=order_payload,
                )["event_id"]
            )

            execution_spec = normalized.get("execution")
            if (
                isinstance(execution_spec, Mapping)
                and bool(execution_spec.get("enabled", True))
                and str(order.get("status", "")).lower() != "closed"
            ):
                amount = execution_spec.get("amount", execution_spec.get("qty", order.get("remaining")))
                price = execution_spec.get("price", execution_spec.get("executed_price", plan["planned_price"]))
                part_sequence = int(execution_spec.get("part_sequence", 1))
                fill_id = str(
                    execution_spec.get("fill_id", f"{order_id}:fill:{part_sequence}")
                )
                self._client.apply_fill_event(
                    {
                        "event_id": str(execution_spec.get("event_id", f"paper-execution:{order_id}:{part_sequence}")),
                        "fill_id": fill_id,
                        "order_id": str(order_id),
                        "symbol": str(normalized["symbol"]),
                        "side": str(plan["exchange_side"]),
                        "position_side": position_side,
                        "amount": float(amount),
                        "order_amount": float(plan["amount"]),
                        "price": float(price),
                        "reference_price": float(plan["reference_price"]),
                        "timestamp": int(normalized["timestamp"]),
                        "liquidity": str(execution_spec.get("liquidity", "maker")),
                        "part_sequence": part_sequence,
                        "strategy_id": self.strategy_id,
                        "cycle_id": cycle_id,
                        "config_hash": self.config_hash,
                    }
                )

            decision = self._execution_from_order(order)
            decision_payload = self._base_payload(
                snapshot=normalized,
                position_side=position_side,
                cycle_id=cycle_id,
                order_id=str(order_id),
                plan=plan,
                execution=decision,
            )
            decision_payload["order"] = self._order_payload(order, plan)
            event_ids.append(
                self._emit(
                    "order.execution_decision",
                    event_id=f"decision:{snapshot_id}:{position_side}",
                    timestamp=int(normalized["timestamp"]),
                    tags=("order", "decision", position_side, "shadow"),
                    payload=decision_payload,
                )["event_id"]
            )
            event_ids.extend(self._emit_new_fills(snapshot=normalized))

        completion_payload = self._base_payload(
            snapshot=normalized,
            position_side=None,
            cycle_id=None,
            order_id=None,
            plan=None,
            execution={"executed": True, "status": "snapshot_processed"},
        )
        event_ids.append(
            self._emit(
                "snapshot.completed",
                event_id=f"completed:{snapshot_id}",
                timestamp=int(normalized["timestamp"]),
                tags=("shadow", "checkpoint"),
                payload=completion_payload,
            )["event_id"]
        )
        self._processed_snapshot_ids.add(snapshot_id)
        self._write_checkpoint()
        return {
            "snapshot_id": snapshot_id,
            "duplicate": False,
            "event_ids": event_ids,
            "planned_orders": planned_count,
            "fills": len([event_id for event_id in event_ids if event_id.startswith("fill:")]),
        }

    async def run(
        self,
        snapshots: Iterable[Mapping[str, Any]],
        *,
        max_snapshots: int | None = None,
    ) -> dict[str, Any]:
        rows = [normalize_snapshot(row, index=index) for index, row in enumerate(snapshots)]
        if not rows:
            raise ValueError("strategy shadow requires at least one saved snapshot")
        if max_snapshots is not None:
            if isinstance(max_snapshots, bool) or int(max_snapshots) <= 0:
                raise ValueError("max_snapshots must be positive")
            rows = rows[: int(max_snapshots)]
        self._run_normalized_snapshots = []
        await self._ensure_client(rows)
        self._complete_pending_restart(rows[0])
        for row in rows:
            await self.process_snapshot(row)
        return self.result(input_snapshot_count=len(rows))

    def _coverage(self) -> dict[str, Any]:
        provenance = dict(self._snapshot_provenance)
        if not provenance and self._run_normalized_snapshots:
            provenance = {
                str(snapshot["snapshot_id"]): {
                    "timestamp": int(snapshot["timestamp"]),
                    "source_hash": str(snapshot["source_hash"]),
                }
                for snapshot in self._run_normalized_snapshots
            }
        return _coverage_from_snapshot_provenance(provenance)

    def _max_restart_recovery_seconds(self) -> float | None:
        durations = [
            float(operation["recovery_duration_seconds"])
            for operation in self._restart_operations
            if operation.get("recovery_completed")
            and operation.get("recovery_duration_seconds") is not None
        ]
        return max(durations) if durations else None

    def _duplicate_order_count(self) -> int:
        order_ids = [
            str(event["payload"].get("order_id"))
            for event in self._events
            if event.get("kind") == "order.opened"
            and isinstance(event.get("payload"), Mapping)
            and event["payload"].get("order_id") not in (None, "")
        ]
        return len(order_ids) - len(set(order_ids))

    def _readiness_metrics(
        self,
        *,
        coverage: Mapping[str, Any],
        deterministic_replay_runs: int,
        external_network_calls: int,
        private_requests: int,
        trading_operations_count: int,
        boundary_violations: Sequence[str],
        events_present: bool,
    ) -> dict[str, Any]:
        return {
            "shadow_duration_hours": coverage["covered_duration_hours"],
            "shadow_planned_orders": len(self._plans),
            "shadow_restart_count": len(self._restart_operations),
            "shadow_max_restart_recovery_seconds": self._max_restart_recovery_seconds(),
            "shadow_duplicate_orders": self._duplicate_order_count(),
            "shadow_unknown_positions": 0,
            "shadow_external_network_calls": external_network_calls,
            "shadow_private_calls": private_requests,
            "shadow_order_creates": trading_operations_count,
            "shadow_transfers": 0,
            "shadow_events_present": events_present,
            "shadow_determinism_runs": deterministic_replay_runs,
            "shadow_restart_observed": bool(self._restart_operations),
            "shadow_replay_count": len(self._replay_operations),
            "shadow_boundary_violations": len(boundary_violations),
        }

    def result(
        self,
        *,
        input_snapshot_count: int | None = None,
        deterministic_replay_runs: int = 0,
        deterministic_replay: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        public_calls = list(self._public.calls) if self._public is not None else []
        write_calls = list(self._public.write_calls) if self._public is not None else []
        operation_evidence = _operation_evidence(
            public_calls=public_calls,
            write_calls=write_calls,
            client_call_facts=self._client_call_facts,
        )
        fills = list(self._client.fills) if self._client is not None else []
        orders = list(self._client._paper_orders.values()) if self._client is not None else []
        positions: list[dict[str, Any]] = []
        if self._client is not None:
            for (symbol, position_side), position in sorted(self._client.positions.items()):
                size = float(position.get("size", 0.0) or 0.0)
                if size <= 0.0:
                    continue
                positions.append(
                    {
                        "symbol": symbol,
                        "position_side": position_side,
                        "size": size,
                        "entry_price": float(position.get("entry_price", 0.0) or 0.0),
                    }
                )
        event_digest = canonical_hash(self._events)
        coverage = self._coverage()
        normalized_snapshot_hash = (
            canonical_hash(self._run_normalized_snapshots)
            if self._run_normalized_snapshots
            else None
        )
        artifact_provenance = self._capture_artifacts()
        deterministic_replay_evidence = (
            copy.deepcopy(dict(deterministic_replay))
            if isinstance(deterministic_replay, Mapping)
            else {
                "status": "not_requested",
                "requested_runs": 0,
                "completed_runs": 0,
                "all_hashes_equal": None,
                "runs": [],
            }
        )
        readiness_metrics = self._readiness_metrics(
            coverage=coverage,
            deterministic_replay_runs=int(deterministic_replay_runs),
            external_network_calls=operation_evidence["external_network_calls"],
            private_requests=operation_evidence["private_requests"],
            trading_operations_count=operation_evidence["trading_operations_count"],
            boundary_violations=operation_evidence["boundary_violations"],
            events_present=bool(self._events),
        )
        provenance = {
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "driver": {
                "module": str(Path(__file__).resolve()),
                "module_sha256": _sha256_file(Path(__file__).resolve()),
                "shadow_schema_version": SHADOW_SCHEMA_VERSION,
                "normalizer": "research.strategy_shadow.normalize_snapshot",
            },
            "input": {
                **copy.deepcopy(self._input_provenance),
                "normalized_snapshot_count": len(self._run_normalized_snapshots),
                "normalized_snapshot_hash": normalized_snapshot_hash,
                "normalized_snapshot_timestamp_hash": coverage[
                    "normalized_snapshot_timestamp_hash"
                ],
                "normalized_snapshot_provenance_hash": coverage[
                    "normalized_snapshot_provenance_hash"
                ],
            },
            "config": {"config_sha256": self.config_hash},
            "normalization": {
                "timestamp_unit": SNAPSHOT_TIMESTAMP_UNIT,
                "source_hash_algorithm": "sha256",
                "coverage_source": "normalized_snapshot_timestamps",
            },
            "artifacts": artifact_provenance,
            "event_log": {
                "canonical_hash": event_digest,
                "file_sha256": artifact_provenance["events"]["sha256"],
            },
            "operations": {
                "restart_operations_hash": canonical_hash(self._restart_operations),
                "replay_operations_hash": canonical_hash(self._replay_operations),
            },
            "local_only": {
                "execution_mode": "local_only",
                "public_source": "saved_public_snapshot",
                "external_network_calls": operation_evidence["external_network_calls"],
                "private_requests": operation_evidence["private_requests"],
                "trading_operations_count": operation_evidence[
                    "trading_operations_count"
                ],
                "credentials_allowed": False,
            },
        }
        evidence_payload = {
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "derived_metrics": readiness_metrics,
            "coverage": coverage,
            "normalized_snapshot_hash": normalized_snapshot_hash,
            "event_log_hash": event_digest,
            "restart_operations": self._restart_operations,
            "replay_operations": self._replay_operations,
            "deterministic_replay": deterministic_replay_evidence,
            "operation_evidence": operation_evidence,
            "artifact_hashes": {
                name: details["sha256"] for name, details in artifact_provenance.items()
            },
        }
        replay_payload = {
            "strategy_id": self.strategy_id,
            "config_hash": self.config_hash,
            "events": self._events,
            "positions": positions,
            "fills": [
                {
                    "id": fill.get("id"),
                    "order": fill.get("order"),
                    "amount": fill.get("amount"),
                    "price": fill.get("price"),
                    "side": fill.get("side"),
                    "info": fill.get("info"),
                }
                for fill in fills
            ],
        }
        restart_evidence = {
            "status": "observed" if self._restart_operations else "not_observed",
            "count": len(self._restart_operations),
            "operations": copy.deepcopy(self._restart_operations),
            "initial_artifacts": copy.deepcopy(self._initial_artifacts),
        }
        if not self._restart_operations:
            restart_evidence["reason"] = (
                "no checkpoint, event log, or paper ledger existed at driver initialization"
            )
        replay_evidence = {
            "status": "observed" if self._replay_operations else "not_observed",
            "count": len(self._replay_operations),
            "operations": copy.deepcopy(self._replay_operations),
        }
        if not self._replay_operations:
            replay_evidence["reason"] = "no already-processed snapshot identity was submitted"
        return {
            "schema_version": SHADOW_SCHEMA_VERSION,
            "status": operation_evidence["status"],
            "read_only": not operation_evidence["boundary_violations"],
            "candidate_admission": False,
            "external_network_calls": operation_evidence["external_network_calls"],
            "private_requests": operation_evidence["private_requests"],
            "trading_operations_count": operation_evidence[
                "trading_operations_count"
            ],
            "public_snapshot_client": True,
            "public_write_calls": operation_evidence["public_write_calls"],
            "public_call_log": public_calls,
            "client_call_facts": copy.deepcopy(self._client_call_facts),
            "operation_facts": operation_evidence["facts"],
            "boundary_violations": operation_evidence["boundary_violations"],
            "strategy_id": self.strategy_id,
            "config_hash": self.config_hash,
            "input_snapshot_count": (
                input_snapshot_count
                if input_snapshot_count is not None
                else len(self._run_normalized_snapshots)
            ),
            "normalized_snapshot_count": len(self._run_normalized_snapshots),
            "normalized_snapshot_hash": normalized_snapshot_hash,
            "processed_snapshot_count": len(self._processed_snapshot_ids),
            "duplicate_snapshot_count": self._duplicate_snapshot_count,
            "planned_order_count": len(self._plans),
            "paper_order_count": len(orders),
            "paper_fill_count": len(fills),
            "event_count": len(self._events),
            "event_log_hash": event_digest,
            "deterministic_replay_hash": canonical_hash(replay_payload),
            "deterministic_replay_runs": int(deterministic_replay_runs),
            "deterministic_replay": deterministic_replay_evidence,
            "covered_start_timestamp_ms": coverage["first_timestamp_ms"],
            "covered_end_timestamp_ms": coverage["last_timestamp_ms"],
            "covered_duration_ms": coverage["covered_duration_ms"],
            "covered_duration_seconds": coverage["covered_duration_seconds"],
            "covered_duration_hours": coverage["covered_duration_hours"],
            "coverage": coverage,
            "restart_count": len(self._restart_operations),
            "restart_observed": bool(self._restart_operations),
            "restart_operations": copy.deepcopy(self._restart_operations),
            "restart_evidence": restart_evidence,
            "replay_count": len(self._replay_operations),
            "replay_operations": copy.deepcopy(self._replay_operations),
            "replay_evidence": replay_evidence,
            "max_restart_recovery_seconds": self._max_restart_recovery_seconds(),
            "readiness_metrics": readiness_metrics,
            "provenance": provenance,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "evidence_hash": canonical_hash(evidence_payload),
            "checkpoint_path": str(self.checkpoint_path),
            "events_path": str(self.events_path),
            "ledger_path": str(self.ledger_path),
            "positions": positions,
        }

    async def close(self) -> None:
        self._write_checkpoint()
        if self._client is not None:
            await self._client.close()


async def run_strategy_shadow(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    output_dir: str | os.PathLike[str],
    config: Mapping[str, Any] | None = None,
    max_snapshots: int | None = None,
    deterministic_replay_runs: int = 3,
    input_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the local shadow and compare independent deterministic replays."""

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if isinstance(deterministic_replay_runs, bool) or int(deterministic_replay_runs) <= 0:
        raise ValueError("deterministic_replay_runs must be positive")
    deterministic_replay_runs = int(deterministic_replay_runs)
    input_rows = list(snapshots)
    driver = StrategyShadowDriver(
        checkpoint_path=root / "checkpoint.json",
        events_path=root / "events.ndjson",
        ledger_path=root / "paper_ledger.json",
        config=config,
        input_provenance=input_provenance,
    )
    try:
        await driver.run(input_rows, max_snapshots=max_snapshots)
        replay_summaries: list[dict[str, Any]] = []

        def replay_summary(
            run_index: int,
            replay_result: Mapping[str, Any],
            replay_root: Path,
        ) -> dict[str, Any]:
            artifacts = replay_result.get("provenance", {}).get("artifacts", {})
            return {
                "run_index": run_index,
                "mode": "fresh_local_driver",
                "status": replay_result.get("status"),
                "output_dir": str(replay_root),
                "event_log_hash": replay_result.get("event_log_hash"),
                "deterministic_replay_hash": replay_result.get("deterministic_replay_hash"),
                "normalized_snapshot_hash": replay_result.get("normalized_snapshot_hash"),
                "covered_duration_ms": replay_result.get("covered_duration_ms"),
                "planned_order_count": replay_result.get("planned_order_count"),
                "paper_fill_count": replay_result.get("paper_fill_count"),
                "artifact_sha256": {
                    name: details.get("sha256")
                    for name, details in artifacts.items()
                    if isinstance(details, Mapping)
                },
            }

        replay_summaries.append(replay_summary(1, driver.result(), root))
        replay_parent = root / "deterministic_replays"
        if deterministic_replay_runs > 1:
            replay_parent.mkdir(parents=True, exist_ok=True)
        for run_index in range(2, deterministic_replay_runs + 1):
            replay_root = Path(
                tempfile.mkdtemp(
                    prefix=f"run-{run_index:03d}-",
                    dir=str(replay_parent),
                )
            ).resolve()
            replay_driver = StrategyShadowDriver(
                checkpoint_path=replay_root / "checkpoint.json",
                events_path=replay_root / "events.ndjson",
                ledger_path=replay_root / "paper_ledger.json",
                config=config,
                input_provenance=input_provenance,
            )
            try:
                replay_result = await replay_driver.run(
                    input_rows,
                    max_snapshots=max_snapshots,
                )
                replay_summaries.append(
                    replay_summary(run_index, replay_result, replay_root)
                )
            finally:
                await replay_driver.close()

        comparison_rows = [
            {
                "event_log_hash": summary["event_log_hash"],
                "deterministic_replay_hash": summary["deterministic_replay_hash"],
                "normalized_snapshot_hash": summary["normalized_snapshot_hash"],
                "covered_duration_ms": summary["covered_duration_ms"],
                "planned_order_count": summary["planned_order_count"],
                "paper_fill_count": summary["paper_fill_count"],
            }
            for summary in replay_summaries
        ]
        all_hashes_equal = len({canonical_hash(row) for row in comparison_rows}) == 1
        deterministic_replay = {
            "status": (
                "passed"
                if all_hashes_equal and deterministic_replay_runs >= 2
                else "insufficient"
                if all_hashes_equal
                else "failed"
            ),
            "requested_runs": deterministic_replay_runs,
            "completed_runs": len(replay_summaries),
            "all_hashes_equal": all_hashes_equal,
            "comparison_hash": canonical_hash(comparison_rows),
            "runs": replay_summaries,
        }
        result = driver.result(
            input_snapshot_count=(
                len(input_rows)
                if max_snapshots is None
                else min(len(input_rows), int(max_snapshots))
            ),
            deterministic_replay_runs=len(replay_summaries),
            deterministic_replay=deterministic_replay,
        )
        if not all_hashes_equal:
            result["status"] = "failed"
            result["determinism_failure"] = "independent local replay hashes differ"
        return result
    finally:
        await driver.close()


def read_shadow_events(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read and validate the machine-readable shadow event stream."""

    target = Path(path).expanduser().resolve()
    rows: list[dict[str, Any]] = []
    if not target.is_file():
        return rows
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("schema_version") != SHADOW_SCHEMA_VERSION:
                raise ValueError(f"unsupported shadow event at line {line_number}")
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                raise ValueError(f"shadow event payload is invalid at line {line_number}")
            if row.get("payload_hash") != canonical_hash(payload):
                raise ValueError(f"shadow event payload hash mismatch at line {line_number}")
            rows.append(row)
    return rows


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "SHADOW_SCHEMA_VERSION",
    "StrategyShadowDriver",
    "canonical_hash",
    "normalize_config",
    "normalize_shadow_policy",
    "normalize_snapshot",
    "read_shadow_events",
    "run_strategy_shadow",
]
