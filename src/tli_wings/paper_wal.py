"""Deterministic, local-only write-ahead storage for paper state.

This module deliberately has no exchange or framework dependency.  A WAL
record is appended and flushed before the caller is allowed to publish the
corresponding in-memory state.  The same event id may be submitted again, but
only with the same kind and payload.  A different payload is an integrity
failure rather than a new event.

The optional :class:`PaperLedger` is a small state reducer for the seven
paper surfaces used by the TLI evidence contract: balances, positions,
orders, funding, mark, index, and tiers.  It is useful in tests and in an
adapter, but it does not call an adapter itself.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar


PAPER_WAL_SCHEMA = "crypto-paper-wal-v3"
WAL_SCHEMA = PAPER_WAL_SCHEMA
GENESIS_CHECKSUM = "0" * 64

STATE_SURFACES = (
    "balances",
    "positions",
    "orders",
    "funding",
    "mark",
    "index",
    "tiers",
)

_STATE_SURFACE_ALIASES = {
    "balance": "balances",
    "balances": "balances",
    "position": "positions",
    "positions": "positions",
    "order": "orders",
    "orders": "orders",
    "funding": "funding",
    "fundings": "funding",
    "mark": "mark",
    "marks": "mark",
    "mark_price": "mark",
    "mark_prices": "mark",
    "index": "index",
    "indices": "index",
    "index_price": "index",
    "index_prices": "index",
    "tier": "tiers",
    "tiers": "tiers",
}

_KIND_ALIASES = {
    "balance": "balance.updated",
    "balances.updated": "balance.updated",
    "position": "position.updated",
    "positions.updated": "position.updated",
    "order": "order.updated",
    "orders.updated": "order.updated",
    "funding": "funding.settled",
    "mark": "mark.updated",
    "market.mark": "mark.updated",
    "index": "index.updated",
    "market.index": "index.updated",
    "tier.updated": "tiers.updated",
    "tier": "tiers.updated",
    "tiers": "tiers.updated",
    "snapshot": "state.snapshot",
}

KNOWN_EVENT_KINDS = frozenset(
    {
        "balance.updated",
        "position.updated",
        "order.created",
        "order.updated",
        "order.canceled",
        "order.filled",
        "funding.settled",
        "mark.updated",
        "index.updated",
        "tiers.updated",
        "state.snapshot",
    }
)

_KIND_SURFACES = {
    "balance.updated": frozenset({"balances"}),
    "position.updated": frozenset({"positions"}),
    "order.created": frozenset({"orders"}),
    "order.updated": frozenset({"orders"}),
    "order.canceled": frozenset({"orders"}),
    "order.filled": frozenset({"orders", "positions", "balances"}),
    "funding.settled": frozenset({"funding", "balances"}),
    "mark.updated": frozenset({"mark"}),
    "index.updated": frozenset({"index"}),
    "tiers.updated": frozenset({"tiers"}),
    "state.snapshot": frozenset(STATE_SURFACES),
}

_HEX_DIGEST_LENGTH = 64
_T = TypeVar("_T")


class PaperWALError(ValueError):
    """Base class for malformed or unsafe paper WAL input."""


class WALIntegrityError(PaperWALError):
    """Raised when the on-disk WAL is not the chain that was written."""


class UnknownStateError(PaperWALError):
    """Raised when a transition contains an unknown or unsafe state."""


class StateMismatchError(PaperWALError):
    """Raised when an event's claimed prior state is not the current state."""


def _canonical_value(value: Any, *, path: str = "value") -> Any:
    """Return JSON data in a deterministic form, rejecting non-JSON values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PaperWALError(f"{path} contains a non-finite number")
        # JSON distinguishes -0.0 textually.  It is not a meaningful paper
        # state distinction, so normalize it to make replay hashes stable.
        return 0.0 if value == 0.0 else value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise PaperWALError(f"{path} has a non-string or empty key")
            result[raw_key] = _canonical_value(child, path=f"{path}.{raw_key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    raise PaperWALError(
        f"{path} contains unsupported {type(value).__name__}; "
        "paper WAL payloads must be JSON values"
    )


def canonical_json(value: Any) -> str:
    """Serialize JSON data using the exact representation used for hashes."""

    return json.dumps(
        _canonical_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    """Return the SHA-256 digest of :func:`canonical_json`."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(value).hexdigest()


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HEX_DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _event_id(value: Any) -> str:
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        raise PaperWALError("paper WAL event_id must be a scalar string")
    normalized = str(value).strip()
    if not normalized:
        raise PaperWALError("paper WAL event_id must not be empty")
    if len(normalized) > 512:
        raise PaperWALError("paper WAL event_id is too long")
    return normalized


def normalize_event_kind(value: Any) -> str:
    if not isinstance(value, str):
        raise UnknownStateError("paper WAL event kind must be a string")
    normalized = value.strip().lower()
    normalized = _KIND_ALIASES.get(normalized, normalized)
    if normalized not in KNOWN_EVENT_KINDS:
        raise UnknownStateError(f"unsupported paper WAL event kind {value!r}")
    return normalized


def _normalize_surface(value: Any) -> str:
    if not isinstance(value, str):
        raise UnknownStateError("paper state surface must be a string")
    normalized = _STATE_SURFACE_ALIASES.get(value.strip().lower())
    if normalized is None:
        raise UnknownStateError(f"unsupported paper state surface {value!r}")
    return normalized


def _unknown_marker(value: Any, *, path: str) -> None:
    """Reject explicit unknown/unavailable state markers.

    Optional order metadata may be null, so null is not rejected globally.
    Explicit state-quality markers are rejected because treating them as a
    real balance, price, or tier would make recovery unsafe.
    """

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if key in {"unknown", "state_unknown", "invalid", "corrupt"} and child is True:
                raise UnknownStateError(f"{path}.{raw_key} is unknown")
            if key in {"known", "available", "valid"} and child is False:
                raise UnknownStateError(f"{path}.{raw_key} is false")
            if key in {"status", "state", "state_status"} and isinstance(child, str):
                if child.strip().lower() in {"unknown", "unavailable", "invalid", "corrupt"}:
                    raise UnknownStateError(f"{path}.{raw_key} is unknown")
            _unknown_marker(child, path=f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _unknown_marker(child, path=f"{path}[{index}]")


def _state_value(value: Any, *, path: str) -> Any:
    normalized = _canonical_value(value, path=path)
    if normalized is None:
        raise UnknownStateError(f"{path} is unknown; use a delete transition instead")
    _unknown_marker(normalized, path=path)
    return normalized


def _copy_json(value: Any) -> Any:
    return copy.deepcopy(value)


def _replay_identity(payload: Mapping[str, Any]) -> Any:
    """Compare a typed replay by its result, not its already-consumed pre-state."""

    normalized = _canonical_value(payload, path="payload")
    if not isinstance(normalized, dict):
        return normalized
    if isinstance(normalized.get("changes"), list):
        changes: list[Any] = []
        for raw_change in normalized["changes"]:
            if not isinstance(raw_change, dict):
                changes.append(raw_change)
                continue
            change = dict(raw_change)
            change.pop("before", None)
            change.pop("before_exists", None)
            changes.append(change)
        normalized["changes"] = changes
    else:
        normalized.pop("before", None)
        normalized.pop("before_exists", None)
    return normalized


@dataclass(frozen=True)
class WALRecord(Mapping[str, Any]):
    """Immutable view of one verified WAL record."""

    wal_schema: str
    sequence: int
    event_id: str
    kind: str
    payload: dict[str, Any]
    previous_checksum: str
    checksum: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "wal_schema": self.wal_schema,
            "sequence": self.sequence,
            "event_id": self.event_id,
            "kind": self.kind,
            "payload": _copy_json(self.payload),
            "previous_checksum": self.previous_checksum,
            "checksum": self.checksum,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(
            (
                "wal_schema",
                "sequence",
                "event_id",
                "kind",
                "payload",
                "previous_checksum",
                "checksum",
            )
        )

    def __len__(self) -> int:
        return 7


def _record_body(
    *,
    sequence: int,
    event_id: str,
    kind: str,
    payload: Mapping[str, Any],
    previous_checksum: str,
) -> dict[str, Any]:
    return {
        "wal_schema": PAPER_WAL_SCHEMA,
        "sequence": sequence,
        "event_id": event_id,
        "kind": kind,
        "payload": _canonical_value(payload, path="payload"),
        "previous_checksum": previous_checksum,
    }


def _record_from_dict(raw: Mapping[str, Any], *, line_number: int | None = None) -> dict[str, Any]:
    location = f" line {line_number}" if line_number is not None else ""
    if not isinstance(raw, Mapping):
        raise WALIntegrityError(f"paper WAL{location} must contain an object")
    expected_keys = {
        "wal_schema",
        "sequence",
        "event_id",
        "kind",
        "payload",
        "previous_checksum",
        "checksum",
    }
    if set(raw) != expected_keys:
        raise WALIntegrityError(
            f"paper WAL{location} has an unsupported field set; "
            f"expected {sorted(expected_keys)}"
        )
    if raw.get("wal_schema") != PAPER_WAL_SCHEMA:
        raise WALIntegrityError(f"paper WAL{location} has an unsupported schema")
    sequence = raw.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise WALIntegrityError(f"paper WAL{location} sequence must be a positive integer")
    event_id = _event_id(raw.get("event_id"))
    kind = normalize_event_kind(raw.get("kind"))
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        raise WALIntegrityError(f"paper WAL{location} payload must be an object")
    try:
        normalized_payload = _canonical_value(payload, path="payload")
    except PaperWALError as exc:
        raise WALIntegrityError(f"paper WAL{location} payload is invalid: {exc}") from exc
    previous_checksum = raw.get("previous_checksum")
    checksum = raw.get("checksum")
    if not _valid_digest(previous_checksum):
        raise WALIntegrityError(f"paper WAL{location} previous_checksum is invalid")
    if not _valid_digest(checksum):
        raise WALIntegrityError(f"paper WAL{location} checksum is invalid")
    body = _record_body(
        sequence=sequence,
        event_id=event_id,
        kind=kind,
        payload=normalized_payload,
        previous_checksum=previous_checksum,
    )
    expected_checksum = _digest(canonical_json(body).encode("utf-8"))
    if checksum != expected_checksum:
        raise WALIntegrityError(
            f"paper WAL{location} checksum mismatch: {checksum} != {expected_checksum}"
        )
    return {
        **body,
        "checksum": checksum,
    }


class PaperWAL:
    """Append-only, checksum-chained local paper event storage.

    ``path=None`` keeps the same deterministic behavior in memory.  When a
    path is supplied, every new line is flushed and fsynced before the method
    returns.  The class never imports or calls an exchange client.
    """

    local_only = True
    private_calls = False
    write_calls = False

    def __init__(self, path: str | Path | None = None):
        self.path = None if path is None else Path(path).expanduser()
        self._records: list[dict[str, Any]] = []
        self._records_by_event_id: dict[str, dict[str, Any]] = {}
        self._mutated_event_ids: set[str] = set()
        self._replayed_event_ids: set[str] = set()
        self._last_checksum = GENESIS_CHECKSUM
        if self.path is not None and self.path.exists():
            self._load()
        elif self.path is not None and self.path.is_symlink():
            raise WALIntegrityError(f"paper WAL path must not be a symlink: {self.path}")

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, Any]]) -> "PaperWAL":
        """Build a memory-only WAL after applying the same chain checks."""

        instance = cls()
        for line_number, raw in enumerate(records, 1):
            instance._ingest(raw, line_number=line_number)
        return instance

    def _load(self) -> None:
        if self.path is None:
            return
        if not self.path.is_file():
            raise WALIntegrityError(f"paper WAL path is not a file: {self.path}")
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line_number, raw_line in enumerate(handle, 1):
                    if not raw_line.strip():
                        continue
                    try:
                        raw = json.loads(raw_line)
                    except json.JSONDecodeError as exc:
                        raise WALIntegrityError(
                            f"paper WAL line {line_number} is not valid JSON"
                        ) from exc
                    self._ingest(raw, line_number=line_number)
        except OSError as exc:
            raise WALIntegrityError(f"could not read paper WAL {self.path}: {exc}") from exc

    def _ingest(self, raw: Mapping[str, Any], *, line_number: int | None = None) -> None:
        record = _record_from_dict(raw, line_number=line_number)
        expected_sequence = len(self._records) + 1
        if record["sequence"] != expected_sequence:
            raise WALIntegrityError(
                f"paper WAL sequence mismatch at {record['sequence']}: "
                f"expected {expected_sequence}"
            )
        if record["previous_checksum"] != self._last_checksum:
            raise WALIntegrityError(
                f"paper WAL previous checksum mismatch at sequence {record['sequence']}"
            )
        existing = self._records_by_event_id.get(record["event_id"])
        if existing is not None:
            if (
                existing["kind"] != record["kind"]
                or existing["payload"] != record["payload"]
            ):
                raise WALIntegrityError(
                    f"paper WAL event {record['event_id']!r} has conflicting entries"
                )
        else:
            self._records_by_event_id[record["event_id"]] = record
        self._records.append(record)
        self._last_checksum = record["checksum"]

    def _durable_append(self, record: Mapping[str, Any]) -> None:
        if self.path is None:
            return
        if self.path.is_symlink():
            raise WALIntegrityError(f"paper WAL path must not be a symlink: {self.path}")
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        line = canonical_json(record).encode("utf-8") + b"\n"
        try:
            with self.path.open("ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            directory_fd = os.open(str(parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise WALIntegrityError(f"could not durably append paper WAL: {exc}") from exc

    def append_event(
        self,
        event_id: Any,
        kind: Any,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append one event and return its immutable record as a dictionary.

        A repeated event id is accepted only when its normalized kind and
        payload are identical.  Replays never add another line.
        """

        normalized_event_id = _event_id(event_id)
        normalized_kind = normalize_event_kind(kind)
        if not isinstance(payload, Mapping):
            raise PaperWALError("paper WAL payload must be an object")
        try:
            normalized_payload = _canonical_value(payload, path="payload")
        except PaperWALError:
            raise
        existing = self._records_by_event_id.get(normalized_event_id)
        if existing is not None:
            if (
                existing["kind"] != normalized_kind
                or existing["payload"] != normalized_payload
            ):
                raise WALIntegrityError(
                    f"paper WAL event {normalized_event_id!r} has conflicting payload"
                )
            return _copy_json(existing)

        body = _record_body(
            sequence=len(self._records) + 1,
            event_id=normalized_event_id,
            kind=normalized_kind,
            payload=normalized_payload,
            previous_checksum=self._last_checksum,
        )
        record = {
            **body,
            "checksum": _digest(canonical_json(body).encode("utf-8")),
        }
        self._durable_append(record)
        self._records.append(record)
        self._records_by_event_id[normalized_event_id] = record
        self._last_checksum = record["checksum"]
        return _copy_json(record)

    append = append_event

    def append_before_mutate(
        self,
        event_id: Any,
        kind: Any,
        payload: Mapping[str, Any],
        mutator: Callable[[], _T] | None = None,
        *,
        mutate: Callable[[], _T] | None = None,
    ) -> dict[str, Any] | _T:
        """Append durably, then run a caller-supplied local mutation once.

        If the mutator raises, the durable event remains available for a
        restart replay.  Calling this method again with the same event id
        retries the mutation; a successful mutation is not run twice in this
        instance.
        """

        if mutator is not None and mutate is not None:
            raise TypeError("provide either mutator or mutate, not both")
        callback = mutator if mutator is not None else mutate
        normalized_event_id = _event_id(event_id)
        record = self.append_event(normalized_event_id, kind, payload)
        if callback is None:
            return record
        if normalized_event_id in self._mutated_event_ids:
            return record
        result = callback()
        self._mutated_event_ids.add(normalized_event_id)
        return result

    def records(self) -> list[dict[str, Any]]:
        """Return all verified records, including identical duplicate lines."""

        return [_copy_json(record) for record in self._records]

    def unique_records(self) -> list[dict[str, Any]]:
        """Return the first occurrence of each event id in chain order."""

        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for record in self._records:
            event_id = record["event_id"]
            if event_id in seen:
                continue
            seen.add(event_id)
            unique.append(_copy_json(record))
        return unique

    @property
    def last_checksum(self) -> str:
        return self._last_checksum

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(record["event_id"] for record in self.unique_records())

    def replay(
        self,
        reducer: Callable[[Mapping[str, Any]], _T],
        *,
        once: bool = True,
    ) -> list[_T]:
        """Replay verified events through a pure/local reducer.

        The reducer receives a copied record.  ``once=True`` makes repeated
        calls idempotent for this WAL instance; a fresh process can replay the
        same durable records after it has rebuilt its state.
        """

        results: list[_T] = []
        for record in self.unique_records():
            event_id = record["event_id"]
            if once and event_id in self._replayed_event_ids:
                continue
            result = reducer(_copy_json(record))
            results.append(result)
            if once:
                self._replayed_event_ids.add(event_id)
        return results

    def recover_state(
        self,
        initial_state: Mapping[str, Any] | PaperState | None = None,
        *,
        require_complete: bool = False,
    ) -> dict[str, Any]:
        """Recover the seven paper surfaces without any external calls."""

        ledger = PaperLedger(self, initial_state=initial_state)
        return ledger.recover(require_complete=require_complete)

    recover = recover_state


def _canonical_key(value: Any, *, field_name: str = "key") -> str:
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        raise UnknownStateError(f"paper state {field_name} must be a scalar")
    normalized = str(value).strip()
    if not normalized:
        raise UnknownStateError(f"paper state {field_name} must not be empty")
    return normalized


def _position_key(payload: Mapping[str, Any]) -> str | None:
    raw_key = payload.get("key", payload.get("position_key"))
    if raw_key not in (None, ""):
        return _canonical_key(raw_key, field_name="position key")
    symbol = payload.get("symbol")
    side = payload.get("position_side", payload.get("pside", payload.get("pos_side")))
    if symbol in (None, ""):
        return None
    symbol_text = _canonical_key(symbol, field_name="position symbol")
    if side in (None, ""):
        return symbol_text
    side_text = _canonical_key(side, field_name="position side").lower()
    if side_text not in {"long", "short"}:
        raise UnknownStateError("position side must be long or short")
    return f"{symbol_text}|{side_text}"


def _key_for_surface(surface: str, payload: Mapping[str, Any]) -> str | None:
    if surface == "positions":
        return _position_key(payload)
    candidates = {
        "balances": ("key", "asset", "currency", "code"),
        "orders": ("key", "order_id", "orderId", "id"),
        "funding": ("key", "settlement_id", "settlementId", "funding_id", "id"),
        "mark": ("key", "symbol", "coin"),
        "index": ("key", "symbol", "coin"),
        "tiers": ("key", "symbol", "coin"),
    }
    for candidate in candidates[surface]:
        value = payload.get(candidate)
        if value not in (None, ""):
            return _canonical_key(value, field_name=f"{surface} key")
    return None


def _default_surface(kind: str) -> str:
    if kind == "balance.updated":
        return "balances"
    if kind == "position.updated":
        return "positions"
    if kind.startswith("order."):
        return "orders"
    if kind == "funding.settled":
        return "funding"
    if kind == "mark.updated":
        return "mark"
    if kind == "index.updated":
        return "index"
    if kind == "tiers.updated":
        return "tiers"
    raise UnknownStateError(f"event kind {kind!r} has no state surface")


def _transition_change(
    *,
    surface: str,
    key: str | None,
    before: Any = None,
    before_exists: bool | None = None,
    after: Any = None,
    operation: str = "set",
    replace: bool = False,
) -> dict[str, Any]:
    change: dict[str, Any] = {
        "surface": surface,
        "operation": operation,
    }
    if key is not None:
        change["key"] = key
    if replace:
        change["replace"] = True
    if before_exists is not None:
        change["before_exists"] = before_exists
        change["before"] = _copy_json(before) if before_exists else None
    elif before is not None:
        change["before"] = _copy_json(before)
    if operation != "delete":
        change["after"] = _copy_json(after)
    return change


def _simple_change(kind: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Turn common event-shaped payloads into explicit state transitions."""

    allowed = _KIND_SURFACES[kind]
    if "changes" in payload:
        raw_changes = payload["changes"]
        if not isinstance(raw_changes, list) or not raw_changes:
            raise UnknownStateError("paper WAL changes must be a non-empty list")
        return [dict(_canonical_value(change, path="payload.changes")) for change in raw_changes]
    if "change" in payload:
        change = payload["change"]
        if not isinstance(change, Mapping):
            raise UnknownStateError("paper WAL change must be an object")
        return [dict(_canonical_value(change, path="payload.change"))]

    if kind == "state.snapshot":
        raw_state = payload.get("state", payload.get("after"))
        if not isinstance(raw_state, Mapping):
            raise UnknownStateError("state.snapshot requires a complete state object")
        state_keys = {_normalize_surface(key) for key in raw_state}
        if state_keys != set(STATE_SURFACES) or len(raw_state) != len(STATE_SURFACES):
            raise UnknownStateError(
                "state.snapshot must contain exactly balances, positions, orders, "
                "funding, mark, index, and tiers"
            )
        before_state = payload.get("before")
        if before_state is not None and not isinstance(before_state, Mapping):
            raise UnknownStateError("state.snapshot before must be an object")
        changes: list[dict[str, Any]] = []
        for surface in STATE_SURFACES:
            if surface not in raw_state:
                raise UnknownStateError(f"state.snapshot is missing {surface}")
            before_exists = before_state is not None and surface in before_state
            changes.append(
                _transition_change(
                    surface=surface,
                    key=None,
                    before=before_state.get(surface) if before_exists else None,
                    before_exists=before_exists if before_state is not None else None,
                    after=raw_state[surface],
                    replace=True,
                )
            )
        return changes

    raw_surface = payload.get("surface")
    surface = _normalize_surface(raw_surface) if raw_surface is not None else _default_surface(kind)
    if surface not in allowed:
        raise UnknownStateError(f"{kind} cannot update {surface}")

    # A map form is used for multi-symbol marks, indices, or a full balance
    # map.  A funding object and an order object are single records instead.
    map_value = None
    if "values" in payload:
        map_value = payload["values"]
    elif surface == "mark" and isinstance(payload.get("mark"), Mapping):
        map_value = payload["mark"]
    elif surface == "index" and isinstance(payload.get("index"), Mapping):
        map_value = payload["index"]
    elif surface == "tiers" and isinstance(payload.get("tiers"), Mapping):
        map_value = payload["tiers"]
    elif surface == "balances" and isinstance(payload.get("balances"), Mapping):
        map_value = payload["balances"]
    if map_value is not None and _key_for_surface(surface, payload) is None:
        if not isinstance(map_value, Mapping) or not map_value:
            raise UnknownStateError(f"{surface} values must be a non-empty object")
        return [
            {
                "surface": surface,
                "key": _canonical_key(key, field_name=f"{surface} key"),
                "operation": "set",
                **(
                    {
                        "before": payload["before"].get(key),
                        "before_exists": key in payload["before"],
                    }
                    if isinstance(payload.get("before"), Mapping)
                    else {}
                ),
                "after": value,
            }
            for key, value in map_value.items()
        ]

    key = _key_for_surface(surface, payload)
    if key is None:
        raise UnknownStateError(f"{kind} requires a key for {surface}")

    if kind == "order.canceled" and "after" not in payload and "order" not in payload:
        return [
            {
                "surface": "orders",
                "key": key,
                "operation": "delete",
                **(
                    {
                        "before": payload["before"],
                        "before_exists": True,
                    }
                    if "before" in payload
                    else {}
                ),
            }
        ]

    raw_after = payload.get("after")
    if raw_after is None:
        if surface == "orders" and "order" in payload:
            raw_after = payload["order"]
        elif surface == "positions" and "position" in payload:
            raw_after = payload["position"]
        elif surface == "funding" and "funding" in payload:
            raw_after = payload["funding"]
        elif surface == "mark" and "mark" in payload:
            raw_after = payload["mark"]
        elif surface == "index" and "index" in payload:
            raw_after = payload["index"]
        elif surface == "tiers" and "tier" in payload:
            raw_after = payload["tier"]
        elif surface == "balances" and "balance" in payload:
            raw_after = payload["balance"]
        elif "value" in payload:
            raw_after = payload["value"]
    if raw_after is None:
        # A compact mark/index event commonly uses the surface name as its
        # scalar value, e.g. {symbol: "BTC", mark_price: 100}.
        for candidate in {
            "mark": ("mark_price", "price"),
            "index": ("index_price", "price"),
            "tiers": ("tier_value",),
        }.get(surface, ()):
            if candidate in payload:
                raw_after = payload[candidate]
                break
    if raw_after is None:
        raise UnknownStateError(f"{kind} requires an after/value state")
    return [
        {
            "surface": surface,
            "key": key,
            "operation": "set",
            **(
                {
                    "before": payload["before"],
                    "before_exists": payload.get("before_exists", True),
                }
                if "before" in payload
                else {}
            ),
            "after": raw_after,
        }
    ]


def _apply_change(state: dict[str, Any], change: Mapping[str, Any], *, event_id: str) -> str:
    if not isinstance(change, Mapping):
        raise UnknownStateError(f"paper WAL event {event_id!r} has a non-object change")
    raw_surface = change.get("surface")
    surface = _normalize_surface(raw_surface)
    if surface not in STATE_SURFACES:
        raise UnknownStateError(f"paper WAL event {event_id!r} has unknown surface")
    target = state[surface]
    if not isinstance(target, dict):
        raise UnknownStateError(f"paper state {surface} is not an object")
    operation = str(change.get("operation", "set")).strip().lower()
    if operation not in {"set", "upsert", "delete"}:
        raise UnknownStateError(
            f"paper WAL event {event_id!r} has unknown operation {operation!r}"
        )
    replace = change.get("replace") is True
    if replace:
        if "key" in change:
            raise UnknownStateError("a replacement change cannot contain a key")
        if operation == "delete":
            raise UnknownStateError("a replacement change cannot delete a surface")
        if "after" not in change or not isinstance(change["after"], Mapping):
            raise UnknownStateError("a replacement change requires an object after")
        if "before" in change or "before_exists" in change:
            expected_before = change.get("before")
            expected_exists = bool(change.get("before_exists", expected_before is not None))
            if expected_exists and target != _state_map(expected_before, path="before"):
                raise StateMismatchError(
                    f"paper WAL event {event_id!r} replacement before-state differs"
                )
            if not expected_exists and target:
                raise StateMismatchError(
                    f"paper WAL event {event_id!r} expected an empty replacement state"
                )
        replacement = _state_map(change["after"], path="after")
        state[surface] = replacement
        return surface

    if "key" not in change:
        raise UnknownStateError(f"paper WAL event {event_id!r} change requires a key")
    key = _canonical_key(change["key"], field_name=f"{surface} key")
    current_exists = key in target
    if "before_exists" in change and not isinstance(change["before_exists"], bool):
        raise UnknownStateError("before_exists must be boolean")
    expected_exists = bool(change["before_exists"]) if "before_exists" in change else "before" in change
    expected_before = change.get("before")
    if expected_exists != current_exists:
        raise StateMismatchError(
            f"paper WAL event {event_id!r} {surface}[{key!r}] existence differs"
        )
    if expected_exists:
        if expected_before is None:
            raise UnknownStateError(
                f"paper WAL event {event_id!r} has an unknown before-state"
            )
        try:
            normalized_before = _state_value(expected_before, path="before")
        except PaperWALError as exc:
            raise UnknownStateError(
                f"paper WAL event {event_id!r} has an invalid before-state: {exc}"
            ) from exc
        if target[key] != normalized_before:
            raise StateMismatchError(
                f"paper WAL event {event_id!r} {surface}[{key!r}] before-state differs"
            )
    if operation == "delete":
        if not current_exists:
            raise StateMismatchError(
                f"paper WAL event {event_id!r} cannot delete unknown {surface}[{key!r}]"
            )
        del target[key]
        return surface
    if "after" not in change:
        raise UnknownStateError(f"paper WAL event {event_id!r} change requires after")
    target[key] = _state_value(change["after"], path="after")
    return surface


def _state_map(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise UnknownStateError(f"paper state {path} must be an object")
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _canonical_key(raw_key, field_name=f"{path} key")
        result[key] = _state_value(raw_value, path=f"{path}.{key}")
    return result


def _state_from_mapping(value: Mapping[str, Any] | "PaperState" | None) -> "PaperState":
    if value is None:
        return PaperState()
    if isinstance(value, PaperState):
        return value.copy()
    if not isinstance(value, Mapping):
        raise UnknownStateError("paper state must be an object")
    unknown = set(value) - set(STATE_SURFACES)
    if unknown:
        raise UnknownStateError(
            f"paper state has unsupported surfaces: {', '.join(sorted(map(str, unknown)))}"
        )
    surfaces = {
        surface: _state_map(value.get(surface, {}), path=surface)
        for surface in STATE_SURFACES
    }
    return PaperState(**surfaces)


@dataclass
class PaperState(Mapping[str, Any]):
    """The recoverable local state, with no exchange-specific behavior."""

    balances: dict[str, Any] = field(default_factory=dict)
    positions: dict[str, Any] = field(default_factory=dict)
    orders: dict[str, Any] = field(default_factory=dict)
    funding: dict[str, Any] = field(default_factory=dict)
    mark: dict[str, Any] = field(default_factory=dict)
    index: dict[str, Any] = field(default_factory=dict)
    tiers: dict[str, Any] = field(default_factory=dict)

    @property
    def mark_prices(self) -> dict[str, Any]:
        return self.mark

    @property
    def index_prices(self) -> dict[str, Any]:
        return self.index

    def to_dict(self) -> dict[str, Any]:
        return {
            surface: _copy_json(getattr(self, surface))
            for surface in STATE_SURFACES
        }

    as_dict = to_dict

    def copy(self) -> "PaperState":
        return PaperState(**self.to_dict())

    def __getitem__(self, key: str) -> Any:
        if key not in STATE_SURFACES:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(STATE_SURFACES)

    def __len__(self) -> int:
        return len(STATE_SURFACES)


def _apply_transition(
    state: PaperState,
    *,
    event_id: str,
    kind: str,
    payload: Mapping[str, Any],
) -> tuple[PaperState, frozenset[str]]:
    normalized_kind = normalize_event_kind(kind)
    if not isinstance(payload, Mapping):
        raise UnknownStateError(f"paper WAL event {event_id!r} payload is not an object")
    normalized_payload = _canonical_value(payload, path="payload")
    _unknown_marker(normalized_payload, path="payload")
    changes = _simple_change(normalized_kind, normalized_payload)
    allowed = _KIND_SURFACES[normalized_kind]
    working = state.to_dict()
    touched: set[str] = set()
    for change in changes:
        surface = _normalize_surface(change.get("surface"))
        if surface not in allowed:
            raise UnknownStateError(
                f"paper WAL event {event_id!r} cannot update {surface}"
            )
        touched.add(_apply_change(working, change, event_id=event_id))
    return _state_from_mapping(working), frozenset(touched)


class PaperLedger:
    """A fail-closed reducer backed by :class:`PaperWAL`."""

    def __init__(
        self,
        wal: PaperWAL | str | Path | None = None,
        *,
        initial_state: Mapping[str, Any] | PaperState | None = None,
    ):
        self.wal = wal if isinstance(wal, PaperWAL) else PaperWAL(wal)
        self._state = _state_from_mapping(initial_state)
        self._known_surfaces: set[str] = set()
        if isinstance(initial_state, PaperState):
            self._known_surfaces.update(STATE_SURFACES)
        elif isinstance(initial_state, Mapping):
            self._known_surfaces.update(
                surface for surface in STATE_SURFACES if surface in initial_state
            )
        self._applied_event_ids: set[str] = set()
        for record in self.wal.unique_records():
            self._apply_record(record)

    def _apply_record(self, record: Mapping[str, Any]) -> None:
        event_id = _event_id(record.get("event_id"))
        if event_id in self._applied_event_ids:
            return
        next_state, touched = _apply_transition(
            self._state,
            event_id=event_id,
            kind=record.get("kind"),
            payload=record.get("payload"),
        )
        self._state = next_state
        self._known_surfaces.update(touched)
        self._applied_event_ids.add(event_id)

    @property
    def state(self) -> PaperState:
        return self._state.copy()

    @property
    def known_surfaces(self) -> tuple[str, ...]:
        return tuple(surface for surface in STATE_SURFACES if surface in self._known_surfaces)

    @property
    def unknown_surfaces(self) -> tuple[str, ...]:
        return tuple(surface for surface in STATE_SURFACES if surface not in self._known_surfaces)

    @property
    def state_is_complete(self) -> bool:
        return not self.unknown_surfaces

    def require_known(self, *surfaces: str) -> None:
        required = STATE_SURFACES if not surfaces else tuple(_normalize_surface(item) for item in surfaces)
        missing = [surface for surface in required if surface not in self._known_surfaces]
        if missing:
            raise UnknownStateError(
                "paper state is unknown for: " + ", ".join(missing)
            )

    def recover(self, *, require_complete: bool = False) -> dict[str, Any]:
        if require_complete:
            self.require_known()
        return self._state.to_dict()

    def recovery_report(self) -> dict[str, Any]:
        return {
            "state": self._state.to_dict(),
            "known_surfaces": list(self.known_surfaces),
            "unknown_surfaces": list(self.unknown_surfaces),
            "safe": self.state_is_complete,
            "wal_schema": PAPER_WAL_SCHEMA,
            "last_checksum": self.wal.last_checksum,
        }

    def apply_event(
        self,
        event_id: Any,
        kind: Any,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append a state transition before publishing it to the ledger."""

        normalized_event_id = _event_id(event_id)
        normalized_kind = normalize_event_kind(kind)
        if normalized_event_id in self._applied_event_ids:
            existing = self.wal._records_by_event_id[normalized_event_id]
            normalized_payload = _canonical_value(payload, path="payload")
            if existing["kind"] != normalized_kind:
                raise WALIntegrityError(
                    f"paper WAL event {normalized_event_id!r} has conflicting kind"
                )
            if existing["payload"] != normalized_payload and (
                _replay_identity(existing["payload"])
                != _replay_identity(normalized_payload)
            ):
                raise WALIntegrityError(
                    f"paper WAL event {normalized_event_id!r} has conflicting payload"
                )
            return _copy_json(existing)
        next_state, touched = _apply_transition(
            self._state,
            event_id=normalized_event_id,
            kind=normalized_kind,
            payload=payload,
        )
        record = self.wal.append_event(normalized_event_id, normalized_kind, payload)
        self._state = next_state
        self._known_surfaces.update(touched)
        self._applied_event_ids.add(normalized_event_id)
        return record

    append_before_mutate = apply_event

    def _typed_payload(
        self,
        *,
        surface: str,
        key: str,
        value: Any,
        timestamp_ms: int | None = None,
    ) -> dict[str, Any]:
        surface = _normalize_surface(surface)
        key = _canonical_key(key)
        current = getattr(self._state, surface)
        exists = key in current
        payload: dict[str, Any] = {
            "surface": surface,
            "key": key,
            "operation": "set",
            "before_exists": exists,
            "before": _copy_json(current[key]) if exists else None,
            "after": _copy_json(value),
        }
        if timestamp_ms is not None:
            if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
                raise PaperWALError("timestamp_ms must be a non-negative integer")
            payload["timestamp_ms"] = timestamp_ms
        return payload

    def set_balance(self, event_id: Any, asset: Any, value: Any, *, timestamp_ms: int | None = None) -> dict[str, Any]:
        return self.apply_event(
            event_id,
            "balance.updated",
            self._typed_payload(
                surface="balances", key=_canonical_key(asset, field_name="asset"), value=value, timestamp_ms=timestamp_ms
            ),
        )

    update_balance = set_balance

    def set_position(
        self,
        event_id: Any,
        key: Any,
        value: Any,
        *,
        position_side: str | None = None,
        timestamp_ms: int | None = None,
    ) -> dict[str, Any]:
        position_key = _canonical_key(key, field_name="position key")
        if position_side is not None:
            side = _canonical_key(position_side, field_name="position side").lower()
            if side not in {"long", "short"}:
                raise UnknownStateError("position side must be long or short")
            position_key = f"{position_key}|{side}"
        return self.apply_event(
            event_id,
            "position.updated",
            self._typed_payload(
                surface="positions", key=position_key, value=value, timestamp_ms=timestamp_ms
            ),
        )

    update_position = set_position

    def _set_order(
        self,
        event_id: Any,
        kind: str,
        order_id: Any,
        order: Mapping[str, Any],
        *,
        timestamp_ms: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(order, Mapping):
            raise UnknownStateError("paper order must be an object")
        normalized_id = _canonical_key(order_id, field_name="order id")
        normalized_order = _canonical_value(order, path="order")
        if not isinstance(normalized_order, dict):
            raise UnknownStateError("paper order must be an object")
        normalized_order.setdefault("id", normalized_id)
        if str(normalized_order["id"]).strip() != normalized_id:
            raise StateMismatchError("order id does not match order.id")
        return self.apply_event(
            event_id,
            kind,
            self._typed_payload(
                surface="orders", key=normalized_id, value=normalized_order, timestamp_ms=timestamp_ms
            ),
        )

    def create_order(self, event_id: Any, order_id: Any, order: Mapping[str, Any] | None = None, *, timestamp_ms: int | None = None) -> dict[str, Any]:
        if order is None and isinstance(order_id, Mapping):
            order = order_id
            order_id = order.get("id")
        if order is None:
            raise UnknownStateError("create_order requires an order object")
        return self._set_order(event_id, "order.created", order_id, order, timestamp_ms=timestamp_ms)

    def update_order(self, event_id: Any, order_id: Any, order: Mapping[str, Any] | None = None, *, timestamp_ms: int | None = None) -> dict[str, Any]:
        if order is None and isinstance(order_id, Mapping):
            order = order_id
            order_id = order.get("id")
        if order is None:
            raise UnknownStateError("update_order requires an order object")
        return self._set_order(event_id, "order.updated", order_id, order, timestamp_ms=timestamp_ms)

    def fill_order(self, event_id: Any, order_id: Any, order: Mapping[str, Any] | None = None, *, timestamp_ms: int | None = None) -> dict[str, Any]:
        if order is None and isinstance(order_id, Mapping):
            order = order_id
            order_id = order.get("id")
        if order is None:
            raise UnknownStateError("fill_order requires an order object")
        return self._set_order(event_id, "order.filled", order_id, order, timestamp_ms=timestamp_ms)

    def cancel_order(
        self,
        event_id: Any,
        order_id: Any,
        order: Mapping[str, Any] | None = None,
        *,
        timestamp_ms: int | None = None,
    ) -> dict[str, Any]:
        normalized_id = _canonical_key(order_id, field_name="order id")
        if order is None:
            existing = self._state.orders.get(normalized_id)
            if existing is None:
                raise UnknownStateError(f"cannot cancel unknown order {normalized_id!r}")
            order = _copy_json(existing)
        if not isinstance(order, Mapping):
            raise UnknownStateError("paper canceled order must be an object")
        canceled = _copy_json(order)
        canceled["id"] = normalized_id
        canceled["status"] = "canceled"
        return self._set_order(event_id, "order.canceled", normalized_id, canceled, timestamp_ms=timestamp_ms)

    def settle_funding(
        self,
        event_id: Any,
        settlement_id: Any,
        funding: Mapping[str, Any],
        *,
        balance_updates: Mapping[str, Any] | None = None,
        timestamp_ms: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(funding, Mapping):
            raise UnknownStateError("funding settlement must be an object")
        settlement_key = _canonical_key(settlement_id, field_name="settlement id")
        changes = [
            self._typed_payload(
                surface="funding",
                key=settlement_key,
                value=funding,
                timestamp_ms=timestamp_ms,
            )
        ]
        if balance_updates is not None:
            if not isinstance(balance_updates, Mapping):
                raise UnknownStateError("balance_updates must be an object")
            for asset, value in sorted(balance_updates.items(), key=lambda item: str(item[0])):
                changes.append(
                    self._typed_payload(
                        surface="balances",
                        key=_canonical_key(asset, field_name="asset"),
                        value=value,
                        timestamp_ms=timestamp_ms,
                    )
                )
        return self.apply_event(
            event_id,
            "funding.settled",
            {"changes": changes, "settlement_id": settlement_key, **(
                {"timestamp_ms": timestamp_ms} if timestamp_ms is not None else {}
            )},
        )

    apply_funding = settle_funding

    def set_mark(self, event_id: Any, symbol: Any, price: Any, *, timestamp_ms: int | None = None) -> dict[str, Any]:
        return self.apply_event(event_id, "mark.updated", self._typed_payload(surface="mark", key=_canonical_key(symbol, field_name="symbol"), value=price, timestamp_ms=timestamp_ms))

    update_mark = set_mark

    def set_index(self, event_id: Any, symbol: Any, price: Any, *, timestamp_ms: int | None = None) -> dict[str, Any]:
        return self.apply_event(event_id, "index.updated", self._typed_payload(surface="index", key=_canonical_key(symbol, field_name="symbol"), value=price, timestamp_ms=timestamp_ms))

    update_index = set_index

    def set_tier(self, event_id: Any, key: Any, tier: Any, *, timestamp_ms: int | None = None) -> dict[str, Any]:
        return self.apply_event(event_id, "tiers.updated", self._typed_payload(surface="tiers", key=_canonical_key(key, field_name="tier key"), value=tier, timestamp_ms=timestamp_ms))

    update_tier = set_tier

    def set_snapshot(self, event_id: Any, state: Mapping[str, Any] | PaperState) -> dict[str, Any]:
        normalized = _state_from_mapping(state)
        return self.apply_event(
            event_id,
            "state.snapshot",
            {"state": normalized.to_dict(), "before": self._state.to_dict()},
        )


def recover_state(
    path: str | Path,
    initial_state: Mapping[str, Any] | PaperState | None = None,
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Recover a local WAL into plain dictionaries."""

    return PaperWAL(path).recover_state(initial_state, require_complete=require_complete)


__all__ = [
    "GENESIS_CHECKSUM",
    "KNOWN_EVENT_KINDS",
    "PAPER_WAL_SCHEMA",
    "PaperLedger",
    "PaperState",
    "PaperWALError",
    "PaperWAL",
    "StateMismatchError",
    "STATE_SURFACES",
    "UnknownStateError",
    "WALIntegrityError",
    "WALRecord",
    "WAL_SCHEMA",
    "canonical_json",
    "normalize_event_kind",
    "recover_state",
    "sha256_json",
]
