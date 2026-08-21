"""Bitget public-data paper trading adapter.

The paper adapter deliberately keeps the exchange boundary split in two:

* market data comes from an unauthenticated public Bitget CCXT client;
* balances, positions, orders and fills live in a local ledger, kept in memory
  by default and persisted only when an explicit state path is supplied.

The adapter never passes API credentials to CCXT and never calls a private
exchange endpoint.  It is therefore suitable for a no-money paper run, but
its simulated liquidation price is only an explicit stress assumption.  It
must not be presented as Bitget's live liquidation calculation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, TypedDict

import ccxt.async_support as ccxt_async

from config.access import get_optional_live_value
from exchanges.bitget import BitgetBot
from exchanges.fake import FakeCCXTClient
from research.paper_market_surfaces import (
    PaperMarketSurfaceError,
    build_paper_execution_evidence,
    extract_mark_index,
    load_margin_tiers,
    load_paper_market_profile,
    maintenance_for_notional,
    paper_fill_model,
    paper_surface_status,
    symbol_key,
    write_paper_execution_evidence,
)
from research.paper_public_inputs import (
    PublicExecutionInputFeed,
    load_public_execution_feed,
)
from utils import ts_to_date, utc_ms

logger = logging.getLogger(__name__)


class PaperCrashInjected(RuntimeError):
    """Test-only crash boundary for paper WAL recovery."""


PAPER_TYPED_FILL_SCHEMA = "crypto-paper-typed-fill-v1"
PAPER_FUNDING_SCHEMA = "crypto-paper-funding-v1"
PAPER_RECONCILIATION_SCHEMA = "crypto-paper-reconciliation-v1"
PAPER_ORDER_JOURNAL_SCHEMA = "crypto-paper-order-journal-v1"
PAPER_CANCEL_JOURNAL_SCHEMA = "crypto-paper-cancel-journal-v1"
PAPER_WAL_SCHEMA = "crypto-paper-wal-v2"


class PaperFundingPayload(TypedDict, total=False):
    """Typed payload emitted by one explicit paper funding settlement."""

    id: str
    settlement_id: str
    event_id: str
    timestamp: int
    symbol: str
    symbols: list[str]
    position_side: str
    position_sides: list[str]
    quote_currency: str
    strategy_id: str
    cycle_id: str
    config_hash: str
    funding: dict[str, float | str]
    positions: list[dict[str, float | str]]
    funding_events: list[dict[str, Any]]
    source: str
    balance_before: float
    balance_after: float
    realized_pnl_before: float
    realized_pnl_after: float
    realized_funding_before: float
    realized_funding_after: float
    realized_fees_before: float
    realized_fees_after: float
    paper_contract: str
    source: str
    source_hash: str
    source_hash_kind: str


class PaperFundingEvidence(TypedDict):
    """Runtime-event-shaped evidence for one local funding settlement."""

    schema_version: str
    ts: int
    kind: str
    tags: list[str]
    payload: PaperFundingPayload


def _paper_symbol(value: object, quote: str = "USDT") -> str:
    """Return the canonical linear swap symbol for a coin or CCXT symbol."""

    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("paper symbol must not be empty")
    if "/" not in text:
        return f"{text}/{quote}:{quote}"
    base, rest = text.split("/", 1)
    market_quote = rest.split(":", 1)[0]
    settle = rest.split(":", 1)[1] if ":" in rest else market_quote
    if market_quote != quote or settle != quote:
        raise ValueError(
            f"paper mode only supports {quote}-settled linear swaps, got {value!r}"
        )
    return f"{base}/{quote}:{quote}"


def _paper_coins(config: dict, user_info: dict, quote: str = "USDT") -> List[str]:
    # A user entry is the explicit paper-run contract and must be able to
    # override the schema's default ["BTC"].
    raw = user_info.get("paper_symbols")
    if raw is None:
        raw = get_optional_live_value(config, "paper_symbols", None)
    if raw is None:
        raw = ["BTC"]
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(raw, (list, tuple, set)):
        raise TypeError("live.paper_symbols or api-keys paper_symbols must be a list")
    symbols = [_paper_symbol(item, quote=quote) for item in raw]
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        raise ValueError("paper mode requires at least one symbol")
    return symbols


def _placeholder_market(symbol: str, *, price: float, quote: str) -> dict:
    base = symbol.split("/", 1)[0]
    return {
        "id": symbol.replace("/", "").replace(":", "_"),
        "symbol": symbol,
        "base": base,
        "quote": quote,
        "settle": quote,
        "type": "swap",
        "swap": True,
        "linear": True,
        "inverse": False,
        "active": True,
        "precision": {"amount": 0.001, "price": 0.1},
        "limits": {
            "amount": {"min": 0.001},
            "cost": {"min": 5.1},
        },
        "contractSize": 1.0,
        "maker": 0.0002,
        "taker": 0.00055,
        "info": {"source": "paper_placeholder", "last": price},
    }


def _copy_order(order: dict) -> dict:
    copied = copy.deepcopy(order)
    copied["info"] = dict(order.get("info") or {})
    return copied


def _optional_path(value: object, *, name: str) -> Path | None:
    """Normalize an explicitly configured path without creating anything."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a filesystem path, not a boolean")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return Path(text).expanduser().resolve()


def _configured_paper_state_path(
    config: dict,
    user_info: dict,
    *,
    state_path: str | os.PathLike[str] | None,
    state_dir: str | os.PathLike[str] | None,
) -> Path | None:
    """Return a state file only when the paper caller opted into persistence."""

    if state_path is not None and state_dir is not None:
        raise ValueError("paper state_path and state_dir are mutually exclusive")

    raw_path = state_path
    if raw_path is None:
        raw_path = user_info.get("paper_state_path")
    if raw_path is None:
        raw_path = get_optional_live_value(config, "paper_state_path", None)

    raw_dir = state_dir
    if raw_dir is None:
        raw_dir = user_info.get("paper_state_dir")
    if raw_dir is None:
        raw_dir = get_optional_live_value(config, "paper_state_dir", None)

    if raw_path is not None:
        path = _optional_path(raw_path, name="paper state_path")
        assert path is not None
        if path.exists() and path.is_dir():
            return path / "paper_ledger.json"
        return path
    if raw_dir is not None:
        directory = _optional_path(raw_dir, name="paper state_dir")
        assert directory is not None
        return directory / "paper_ledger.json"
    return None


class PaperBitgetCCXTClient(FakeCCXTClient):
    """Local ledger backed by public Bitget market data only.

    ``state_path`` and ``state_dir`` are deliberately opt-in. A normal smoke
    client remains memory-only and does not create a directory in the caller's
    working tree. The optional state file is a local paper-run journal, not an
    exchange reconciliation source.
    """

    id = "bitget"
    PAPER_STATE_VERSION = 1
    PAPER_MODEL_LIMITATIONS = {
        "status": "limited",
        "funding": {
            "status": "explicit_settlement_only",
            "note": (
                "automatic historical/live funding is not applied; callers may use "
                "apply_funding_settlement with an explicit local settlement"
            ),
        },
        "depth": {
            "status": "not_modeled_fail_closed",
            "queue_modeled": False,
            "depth_modeled": False,
            "qualifying": False,
            "note": (
                "no order-book depth or queue position is modeled; qualifying paper "
                "stays closed"
            ),
        },
        "partial_fills": {
            "status": "explicit_amounts_supported",
            "note": (
                "an order may be filled by several explicit amounts; the paper model "
                "does not infer exchange queue position or depth"
            ),
        },
        "mark_index": {
            "status": "required_named_fields",
            "last_substitution": False,
            "note": "ticker last is never used as mark or index",
        },
        "liquidation": {
            "status": "tiered_maintenance_from_bound_surface",
            "note": (
                "uses bound market-profile maintenance tiers; this is not Bitget's "
                "live liquidation calculation"
            ),
        },
    }

    def __init__(
        self,
        config: dict,
        user_info: dict,
        *,
        public_client: Any | None = None,
        initial_balance: float | None = None,
        clock_ms: int | None = None,
        state_path: str | os.PathLike[str] | None = None,
        state_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.paper_config = config
        self.paper_user_info = user_info
        self.state_path = _configured_paper_state_path(
            config,
            user_info,
            state_path=state_path,
            state_dir=state_dir,
        )
        self.paper_wal_path = (
            self.state_path.with_name(f"{self.state_path.stem}.wal.ndjson")
            if self.state_path is not None
            else None
        )
        self._loading_paper_state = True
        self._paper_replaying_wal = False
        self._paper_wal_records: Dict[str, dict] = {}
        self._paper_crash_injection: str | None = None
        self._paper_orders: Dict[str, dict] = {}
        self._paper_funding_settlements: Dict[str, dict] = {}
        self._processed_paper_events: Dict[str, dict] = {}
        self.public_client = public_client or ccxt_async.bitget(
            {
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            }
        )
        self.public_request_log: List[dict] = []
        self._last_quotes: Dict[str, dict] = {}
        self._clock_override_ms = int(clock_ms) if clock_ms is not None else None
        quote = str(user_info.get("quote") or "USDT").upper()
        self.paper_quote = quote
        self.paper_symbols = _paper_coins(config, user_info, quote=quote)
        raw_execution_inputs_path = user_info.get("paper_execution_inputs_path")
        if raw_execution_inputs_path is None:
            raw_execution_inputs_path = get_optional_live_value(
                config, "paper_execution_inputs_path", None
            )
        self.paper_execution_inputs_path = _optional_path(
            raw_execution_inputs_path,
            name="paper_execution_inputs_path",
        )
        raw_execution_inputs_since = user_info.get("paper_execution_inputs_since_ms")
        if raw_execution_inputs_since is None:
            raw_execution_inputs_since = get_optional_live_value(
                config, "paper_execution_inputs_since_ms", None
            )
        self.paper_execution_inputs_since_ms = (
            None
            if raw_execution_inputs_since in (None, "")
            else int(raw_execution_inputs_since)
        )
        if self.paper_execution_inputs_since_ms is not None and self.paper_execution_inputs_since_ms <= 0:
            raise ValueError("paper_execution_inputs_since_ms must be positive")
        self._paper_execution_feed: PublicExecutionInputFeed | None = None
        # The first validated feed hash is the identity of the public input
        # for this run.  It is restored from the durable state before the next
        # read so replacing the file during a run fails closed.
        self._paper_execution_feed_source_hash: str | None = None
        self._paper_execution_feed_path: str | None = (
            str(self.paper_execution_inputs_path)
            if self.paper_execution_inputs_path is not None
            else None
        )
        self._paper_execution_feed_seen_settlements: set[str] = set()
        self._paper_market_surfaces: Dict[str, dict] = {}
        self._paper_market_profile_path: str | None = None
        self._paper_market_profile_hash: str | None = None
        raw_market_profile_path = user_info.get("paper_market_profile_path")
        if raw_market_profile_path is None:
            raw_market_profile_path = get_optional_live_value(
                config, "paper_market_profile_path", None
            )
        configured_profile = _optional_path(
            raw_market_profile_path,
            name="paper_market_profile_path",
        )
        raw_evidence_path = user_info.get("paper_execution_evidence_path")
        if raw_evidence_path is None:
            raw_evidence_path = get_optional_live_value(
                config, "paper_execution_evidence_path", None
            )
        self.paper_execution_evidence_path = _optional_path(
            raw_evidence_path,
            name="paper_execution_evidence_path",
        )
        if configured_profile is not None:
            self.bind_paper_market_surface(configured_profile)
        balance = initial_balance
        if balance is None:
            balance = user_info.get("paper_initial_balance")
        if balance is None:
            balance = get_optional_live_value(config, "paper_initial_balance", 1000.0)
        balance = float(balance)
        if not math.isfinite(balance) or balance <= 0.0 or balance > 1000.0:
            raise ValueError(
                "paper_initial_balance must be finite, positive and no greater than 1000 USDT"
            )
        self.paper_leverage = float(
            user_info.get(
                "paper_leverage",
                get_optional_live_value(config, "leverage", 3.0),
            )
        )
        if (
            not math.isfinite(self.paper_leverage)
            or self.paper_leverage <= 0.0
            or self.paper_leverage > 3.0
        ):
            raise ValueError("paper leverage must be finite, positive and no greater than 3x")

        initial_prices = {
            symbol: 100_000.0 if symbol.startswith("BTC/") else 4_000.0
            for symbol in self.paper_symbols
        }
        scenario = {
            "name": "bitget-public-paper",
            "start_time": int(self._clock_ms()),
            "tick_interval_seconds": 60,
            "account": {"balance": balance},
            "symbols": {
                symbol: _placeholder_market(
                    symbol, price=initial_prices[symbol], quote=quote
                )
                for symbol in self.paper_symbols
            },
            "timeline": [{"t": 0, "prices": initial_prices}],
        }
        super().__init__(scenario, quote=quote)
        self.id = "bitget"
        self.realized_funding = 0.0
        for order_id, order in self.open_orders.items():
            self._paper_orders[str(order_id)] = order
        self._load_paper_state()
        self._load_paper_wal()
        self._loading_paper_state = False
        self._persist_paper_state()
        self.has.update(
            {
                "watchOrders": False,
                "fetchOHLCV": True,
                "fetchTickers": True,
                "fetchTicker": True,
                "fetchTime": True,
            }
        )

    def get_paper_model_status(self) -> dict:
        """Expose model gaps so paper results cannot be mistaken for live parity."""

        status = copy.deepcopy(self.PAPER_MODEL_LIMITATIONS)
        status["ledger"] = {
            "status": "persistent_atomic" if self.state_path is not None else "in_memory_only",
            "state_path": str(self.state_path) if self.state_path is not None else None,
            "wal_path": str(self.paper_wal_path) if self.paper_wal_path is not None else None,
            "typed_fill_journal": "write_ahead" if self.paper_wal_path is not None else "disabled_without_persistence",
            "idempotency": "event_order_fill_and_funding_ids",
            "typed_journal_kinds": [
                "order.created",
                "order.canceled",
                "order.filled",
                "funding.settled",
            ],
        }
        status["public_execution_feed"] = {
            "status": (
                "configured"
                if self.paper_execution_inputs_path is not None
                else "not_configured"
            ),
            "path": (
                str(self.paper_execution_inputs_path)
                if self.paper_execution_inputs_path is not None
                else None
            ),
            "since_ms": self.paper_execution_inputs_since_ms,
            "source_hash": (
                self._paper_execution_feed_source_hash
                if self._paper_execution_feed is not None
                else self._paper_execution_feed_source_hash
            ),
            "source_path": self._paper_execution_feed_path,
            "seen_settlements": len(self._paper_execution_feed_seen_settlements),
            "passport": (
                self._paper_execution_feed.passport.as_dict()
                if self._paper_execution_feed is not None
                else None
            ),
            "exploratory": (
                self._paper_execution_feed.passport.exploratory
                if self._paper_execution_feed is not None
                else False
            ),
            "qualifying": (
                self._paper_execution_feed.passport.qualifying
                if self._paper_execution_feed is not None
                else False
            ),
        }
        surfaces = [copy.deepcopy(row) for row in self._paper_market_surfaces.values()]
        tiers = [
            tier
            for surface in surfaces
            for tier in surface["margin_tiers"]
        ]
        mark_index = None
        if self._last_quotes:
            first_symbol = sorted(self._last_quotes)[0]
            mark_index = extract_mark_index(
                self._last_quotes[first_symbol],
                symbol=first_symbol,
                required=False,
            )
        status["fill_model"] = paper_fill_model()
        status["depth"] = {
            "status": "not_modeled_fail_closed",
            "queue_modeled": False,
            "depth_modeled": False,
            "qualifying": False,
            "note": status["fill_model"].get("note"),
        }
        status["market_surfaces"] = {
            "bound": bool(self._paper_market_surfaces),
            "profile_path": self._paper_market_profile_path,
            "profile_sha256": self._paper_market_profile_hash,
            "symbols": sorted(self._paper_market_surfaces),
        }
        status["execution_evidence"] = paper_surface_status(
            mark_index=mark_index,
            tiers=tiers,
            fill_model=status["fill_model"],
            input_hashes=self._paper_input_hashes(),
        )
        status["qualifying"] = False
        return status

    def _paper_input_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        if self._paper_execution_feed_source_hash is not None:
            hashes["public_execution_feed"] = self._paper_execution_feed_source_hash
        if self._paper_market_profile_hash is not None:
            hashes["market_profile"] = self._paper_market_profile_hash
        return hashes

    def _surface_for_symbol(self, symbol: str) -> dict:
        key = symbol_key(symbol)
        for bound_symbol, surface in self._paper_market_surfaces.items():
            if symbol_key(bound_symbol) == key:
                return surface
        raise PaperMarketSurfaceError(
            f"paper market surface is not bound for {symbol}; "
            "qualifying paper cannot assume 0.5% maintenance"
        )

    def bind_paper_market_surface(
        self,
        surface: Mapping[str, Any] | str | os.PathLike[str],
    ) -> dict:
        """Bind explicit margin tiers. Missing tiers fail closed later."""

        source_path: str | None = None
        source_hash: str | None = None
        if isinstance(surface, (str, os.PathLike)):
            loaded = load_paper_market_profile(surface)
            payload = {
                "symbol": loaded["symbol"],
                "margin_tiers": loaded["margin_tiers"],
            }
            source_path = loaded["path"]
            source_hash = loaded["path_sha256"]
        else:
            if not isinstance(surface, Mapping):
                raise PaperMarketSurfaceError(
                    "paper market surface must be an object or path"
                )
            payload = dict(surface)
        symbol = str(payload.get("symbol") or "").strip()
        if not symbol:
            raise PaperMarketSurfaceError("paper market surface symbol is required")
        tiers = load_margin_tiers(payload, symbol=symbol)
        bound = {
            "symbol": symbol,
            "symbol_key": symbol_key(symbol),
            "margin_tiers": tiers,
            "source_path": source_path,
            "source_sha256": source_hash,
        }
        self._paper_market_surfaces[symbol] = bound
        if source_path is not None:
            self._paper_market_profile_path = source_path
            self._paper_market_profile_hash = source_hash
        if self.paper_execution_evidence_path is not None:
            self.write_paper_execution_evidence(self.paper_execution_evidence_path)
        return copy.deepcopy(bound)

    def write_paper_execution_evidence(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        symbol: str | None = None,
    ) -> dict:
        """Write mandatory paper execution evidence. Qualifying stays false."""

        target = path or self.paper_execution_evidence_path
        if target is None:
            raise PaperMarketSurfaceError(
                "paper_execution_evidence_path is required to write evidence"
            )
        chosen = symbol
        if chosen is None:
            if self._paper_market_surfaces:
                chosen = sorted(self._paper_market_surfaces)[0]
            elif self.paper_symbols:
                chosen = self.paper_symbols[0]
            else:
                raise PaperMarketSurfaceError(
                    "paper execution evidence requires a symbol"
                )
        mark_index = None
        quote = self._last_quotes.get(_paper_symbol(chosen, self.paper_quote))
        if quote is None and self._last_quotes:
            quote = self._last_quotes[sorted(self._last_quotes)[0]]
        if quote is not None:
            mark_index = extract_mark_index(quote, symbol=str(chosen), required=False)
        tiers = None
        try:
            tiers = self._surface_for_symbol(str(chosen))["margin_tiers"]
        except PaperMarketSurfaceError:
            tiers = None
        evidence = build_paper_execution_evidence(
            symbol=str(chosen),
            mark_index=mark_index,
            tiers=tiers,
            fill_model=paper_fill_model(),
            input_hashes=self._paper_input_hashes(),
            source_paths={
                key: value
                for key, value in {
                    "market_profile": self._paper_market_profile_path,
                    "public_execution_feed": self._paper_execution_feed_path,
                    "evidence": str(Path(target)),
                }.items()
                if value
            },
        )
        written = write_paper_execution_evidence(target, evidence)
        self.paper_execution_evidence_path = Path(target)
        return written

    def _load_public_execution_feed(self) -> PublicExecutionInputFeed:
        """Load the explicitly configured public feed and retain its hash."""

        path = self.paper_execution_inputs_path
        if path is None:
            raise ValueError(
                "paper public execution feed is not configured; set "
                "live.paper_execution_inputs_path explicitly"
            )
        if self.paper_execution_inputs_since_ms is None:
            raise ValueError(
                "paper public execution feed requires "
                "live.paper_execution_inputs_since_ms"
            )
        if len(self.paper_symbols) != 1:
            raise ValueError(
                "paper public execution feed currently supports exactly one "
                "configured symbol; use the portfolio feed contract for multiple symbols"
            )
        feed = load_public_execution_feed(path, symbol=self.paper_symbols[0])
        configured_path = str(path.resolve())
        if self._paper_execution_feed_path not in (None, configured_path):
            raise ValueError(
                "paper public execution input path changed during the run"
            )
        if (
            self._paper_execution_feed_source_hash is not None
            and self._paper_execution_feed_source_hash != feed.source_hash
        ):
            raise ValueError(
                "paper public execution input artifact changed during the run: "
                f"expected {self._paper_execution_feed_source_hash}, got {feed.source_hash}"
            )
        self._paper_execution_feed_path = configured_path
        self._paper_execution_feed_source_hash = feed.source_hash
        self._paper_execution_feed = feed
        return feed

    def apply_recorded_public_execution_inputs(
        self,
        *,
        timestamp: int | None = None,
    ) -> list[PaperFundingEvidence]:
        """Apply newly observed, hash-anchored funding settlements.

        The configured feed is a saved public artifact.  Settlements without a
        mark observed before their settlement timestamp are absent from the
        feed and therefore cannot be applied.  Rows seen while no position is
        open are remembered so they cannot be applied later to a position that
        did not exist at the settlement time.
        """

        if self.paper_execution_inputs_path is None:
            return []
        feed = self._load_public_execution_feed()
        cutoff = self.now_ms if timestamp is None else int(timestamp)
        if cutoff <= 0:
            raise ValueError("paper public execution feed timestamp must be positive")
        applied: list[PaperFundingEvidence] = []
        since = int(self.paper_execution_inputs_since_ms or 0)
        for settlement in feed.settlements_up_to(cutoff):
            if settlement.timestamp_ms < since:
                continue
            settlement_id = settlement.settlement_id
            if settlement_id in self._paper_execution_feed_seen_settlements:
                continue
            raw_symbol = str(settlement.symbol).strip().upper()
            if "/" not in raw_symbol and raw_symbol.endswith(self.paper_quote):
                raw_symbol = raw_symbol[: -len(self.paper_quote)]
            normalized_symbol = _paper_symbol(raw_symbol, quote=self.paper_quote)
            has_position = any(
                float((self.positions.get((normalized_symbol, pside)) or {}).get("size", 0.0) or 0.0)
                > 0.0
                for pside in ("long", "short")
            )
            if not has_position:
                self._paper_execution_feed_seen_settlements.add(settlement_id)
                self._persist_paper_state()
                continue
            settlement_payload = settlement.as_paper_settlement()
            settlement_payload["symbol"] = normalized_symbol
            evidence = self.apply_funding_settlement(settlement_payload)
            self._paper_execution_feed_seen_settlements.add(settlement_id)
            self._persist_paper_state()
            applied.append(evidence)
        return applied

    def set_paper_crash_injection(self, point: str | None) -> None:
        """Arm a one-shot crash at a WAL recovery boundary for tests."""

        allowed = {None, "after_wal", "after_ledger_mutation"}
        if point not in allowed:
            raise ValueError(
                "paper crash injection must be None, 'after_wal' or 'after_ledger_mutation'"
            )
        self._paper_crash_injection = point

    def _inject_paper_crash(self, point: str) -> None:
        if self._paper_crash_injection != point:
            return
        self._paper_crash_injection = None
        raise PaperCrashInjected(f"paper crash injected at {point}")

    def _state_snapshot(self) -> dict:
        orders = [
            self._serialize_order(self._paper_orders[order_id])
            for order_id in sorted(self._paper_orders)
        ]
        open_orders = [
            self._serialize_order(self.open_orders[order_id])
            for order_id in sorted(self.open_orders)
        ]
        return {
            "state_version": self.PAPER_STATE_VERSION,
            "quote": self.paper_quote,
            "symbols": list(self.paper_symbols),
            "now_ms": int(self.now_ms),
            "balance_total": float(self.balance_total),
            "balance_free": float(self.balance_free),
            "realized_pnl": float(self.realized_pnl),
            "realized_fees": float(self.realized_fees),
            "realized_funding": float(self.realized_funding),
            "orders": orders,
            "open_orders": open_orders,
            "positions": self.export_positions(),
            "fills": copy.deepcopy(self.fills),
            "funding_settlements": copy.deepcopy(self._paper_funding_settlements),
            "public_execution_feed_path": self._paper_execution_feed_path,
            "public_execution_feed_source_hash": self._paper_execution_feed_source_hash,
            "public_execution_feed_seen_settlements": sorted(
                self._paper_execution_feed_seen_settlements
            ),
            "next_order_id": int(self._next_order_id),
            "next_trade_id": int(self._next_trade_id),
            "processed_events": copy.deepcopy(self._processed_paper_events),
        }

    def _persist_paper_state(self) -> None:
        """Atomically replace the opted-in state file after a ledger mutation."""

        if self.state_path is None or self._loading_paper_state:
            return
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    self._state_snapshot(),
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _paper_event_fingerprint(payload: dict) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _paper_context_value(value: object) -> str | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _paper_finite(value: object, *, name: str, positive: bool = False) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"paper fill {name} must be numeric") from exc
        if not math.isfinite(number) or (positive and number <= 0.0):
            requirement = "positive and finite" if positive else "finite"
            raise ValueError(f"paper fill {name} must be {requirement}")
        return number

    def _paper_typed_fee(
        self,
        order: dict,
        *,
        fill_price: float,
        quantity: float,
        liquidity: str,
        supplied: object = None,
    ) -> dict:
        rate = self._paper_finite(
            self._fee_rate(order["symbol"], str(liquidity)),
            name="fee rate",
        )
        expected_amount = (
            float(fill_price)
            * float(quantity)
            * float(self._c_mult(order["symbol"]))
            * rate
        )
        if supplied is None:
            return {
                "amount": float(expected_amount),
                "currency": str(self.paper_quote).upper(),
                "rate": float(rate),
            }
        if not isinstance(supplied, dict):
            raise ValueError("paper fill fee must be a typed object")
        if "amount" not in supplied or "currency" not in supplied or "rate" not in supplied:
            raise ValueError("paper fill fee requires amount, currency and rate")
        amount = self._paper_finite(supplied["amount"], name="fee amount")
        if amount < 0.0:
            raise ValueError("paper fill fee amount must not be negative")
        currency = str(supplied["currency"] or "").strip().upper()
        if not currency:
            raise ValueError("paper fill fee currency must not be empty")
        supplied_rate = self._paper_finite(supplied["rate"], name="fee rate")
        if supplied_rate < 0.0:
            raise ValueError("paper fill fee rate must not be negative")
        if not math.isclose(amount, expected_amount, rel_tol=1e-6, abs_tol=1e-10):
            raise ValueError(
                "paper fill fee does not match the local execution model"
            )
        if not math.isclose(supplied_rate, rate, rel_tol=1e-6, abs_tol=1e-10):
            raise ValueError(
                "paper fill fee rate does not match the local execution model"
            )
        return {
            "amount": float(amount),
            "currency": currency,
            "rate": float(supplied_rate),
        }

    def _paper_typed_slippage(
        self,
        *,
        fill_price: float,
        reference_price: object,
        supplied: object = None,
    ) -> dict:
        if supplied is not None:
            if not isinstance(supplied, dict):
                raise ValueError("paper fill slippage must be a typed object")
            if "reference_price" not in supplied or "executed_price" not in supplied:
                raise ValueError(
                    "paper fill slippage requires reference_price and executed_price"
                )
            raw_reference = supplied["reference_price"]
            raw_executed = supplied["executed_price"]
        else:
            raw_reference = reference_price
            raw_executed = fill_price
        reference = self._paper_finite(
            raw_reference,
            name="slippage reference price",
            positive=True,
        )
        executed = self._paper_finite(
            raw_executed,
            name="slippage executed price",
            positive=True,
        )
        if not math.isclose(executed, float(fill_price), rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(
                "paper fill slippage executed_price does not match fill price"
            )
        return {
            "reference_price": float(reference),
            "executed_price": float(executed),
        }

    def _paper_fill_payload(
        self,
        order: dict,
        *,
        fill_id: str,
        event_id: str,
        fill_price: float,
        liquidity: str,
        timestamp: int,
        part_sequence: int,
        fee: dict,
        slippage: dict,
        strategy_id: str | None,
        cycle_id: str | None,
        config_hash: str | None,
        fill_quantity: float,
    ) -> dict:
        order_id = str(order.get("id") or "").strip()
        symbol = str(order.get("symbol") or "").strip()
        side = str(order.get("side") or "").lower()
        position_side = str(
            (order.get("info") or {}).get("positionSide")
            or order.get("position_side")
            or ""
        ).lower()
        if position_side not in {"long", "short"}:
            raise ValueError("paper fill requires a long or short position side")
        position = self.positions[(symbol, position_side)]
        quantity = self._paper_finite(fill_quantity, name="amount", positive=True)
        order_amount = self._paper_finite(
            order.get("amount"), name="order amount", positive=True
        )
        quantity_before = self._paper_finite(
            position.get("size", 0.0), name="quantity_before"
        )
        opens_position = (position_side == "long" and side == "buy") or (
            position_side == "short" and side == "sell"
        )
        position_quantity = quantity if opens_position else min(quantity_before, quantity)
        quantity_after = (
            quantity_before + position_quantity
            if opens_position
            else max(0.0, quantity_before - position_quantity)
        )
        order_quantity_before = self._paper_finite(
            order.get("filled", 0.0), name="order_quantity_before"
        )
        order_quantity_after = order_quantity_before + quantity
        if order_quantity_after > order_amount + 1e-12:
            raise ValueError("paper fill amount exceeds the order remainder")
        return {
            "typed_fill_schema": PAPER_TYPED_FILL_SCHEMA,
            "event_id": str(event_id),
            "fill_id": str(fill_id),
            "order_id": order_id,
            "order_amount": float(order_amount),
            "strategy_id": strategy_id,
            "cycle_id": cycle_id,
            "config_hash": config_hash,
            "part_sequence": int(part_sequence),
            "quantity_before": float(quantity_before),
            "quantity_after": float(quantity_after),
            "order_quantity_before": float(order_quantity_before),
            "order_quantity_after": float(order_quantity_after),
            "qty": float(quantity),
            "amount": float(quantity),
            "price": float(fill_price),
            "executed_price": float(fill_price),
            "reference_price": float(slippage["reference_price"]),
            "timestamp": int(timestamp),
            "symbol": symbol,
            "side": side,
            "position_side": position_side,
            "reduce_only": bool(order.get("reduceOnly")),
            "liquidity": str(liquidity),
            "pb_order_type": str((order.get("info") or {}).get("pb_order_type") or ""),
            "fee": copy.deepcopy(fee),
            "slippage": copy.deepcopy(slippage),
            "evidence_eligible": bool(strategy_id and cycle_id and config_hash),
        }

    def _append_paper_wal_record(
        self,
        *,
        kind: str,
        payload: dict,
        event_id: str,
        schema_version: str,
        request_fingerprint: str | None = None,
    ) -> str:
        """Append one typed transition before changing the local ledger.

        The WAL remains a single NDJSON stream for compatibility with the
        existing fill evidence reader, but now covers every state transition
        that can be lost between a mutation and the atomic state snapshot.
        """

        normalized_event_id = str(event_id).strip()
        if not normalized_event_id:
            raise ValueError("paper WAL event_id must not be empty")
        if not isinstance(payload, dict):
            raise TypeError("paper WAL payload must be an object")
        journal_payload = copy.deepcopy(payload)
        if request_fingerprint is not None:
            journal_payload["request_fingerprint"] = str(request_fingerprint)
        fingerprint = self._paper_event_fingerprint(journal_payload)
        existing = self._paper_wal_records.get(normalized_event_id)
        if existing is not None:
            if str(existing.get("kind") or kind) != kind:
                raise ValueError(
                    f"paper WAL event {normalized_event_id!r} has conflicting kind"
                )
            if str(existing.get("fingerprint") or "") != fingerprint:
                raise ValueError(
                    f"paper WAL event {normalized_event_id!r} has conflicting payload"
                )
            return fingerprint

        if self.paper_wal_path is not None:
            path = self.paper_wal_path
            path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = payload.get("timestamp", payload.get("ts", self.now_ms))
            envelope = {
                "schema_version": schema_version,
                "wal_schema": PAPER_WAL_SCHEMA,
                "ts": int(timestamp),
                "kind": kind,
                "event_id": normalized_event_id,
                "fingerprint": fingerprint,
                "stream": "paper_wal",
                "exchange": "bitget",
                "user": "paper",
                "payload": copy.deepcopy(journal_payload),
            }
            if request_fingerprint is not None:
                envelope["request_fingerprint"] = str(request_fingerprint)
            line = json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        self._paper_wal_records[normalized_event_id] = {
            "event_id": normalized_event_id,
            "payload": copy.deepcopy(journal_payload),
            "fingerprint": fingerprint,
            "kind": kind,
            "request_fingerprint": request_fingerprint,
        }
        return fingerprint

    def _append_paper_wal(self, payload: dict) -> str:
        """Durably append one typed fill before changing the local ledger."""

        return self._append_paper_wal_record(
            kind="order.filled",
            payload=payload,
            event_id=str(payload["event_id"]),
            schema_version=PAPER_TYPED_FILL_SCHEMA,
        )

    def _load_paper_wal(self) -> None:
        """Read and replay durable transitions missing from the snapshot."""

        if self.paper_wal_path is None or not self.paper_wal_path.exists():
            return
        if not self.paper_wal_path.is_file():
            raise ValueError(f"paper WAL path is not a file: {self.paper_wal_path}")
        records: list[dict] = []
        try:
            with self.paper_wal_path.open("r", encoding="utf-8") as handle:
                for line_number, raw_line in enumerate(handle, 1):
                    if not raw_line.strip():
                        continue
                    try:
                        envelope = json.loads(raw_line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"paper WAL line {line_number} is not valid JSON"
                        ) from exc
                    if not isinstance(envelope, dict):
                        raise ValueError(f"paper WAL line {line_number} must be an object")
                    kind = str(envelope.get("kind") or "")
                    if kind not in {
                        "order.created",
                        "order.canceled",
                        "order.filled",
                        "funding.settled",
                    }:
                        raise ValueError(
                            f"paper WAL line {line_number} has unsupported kind {kind!r}"
                        )
                    payload = envelope.get("payload")
                    if not isinstance(payload, dict):
                        raise ValueError(
                            f"paper WAL line {line_number} payload must be an object"
                        )
                    event_id = self._event_value(
                        envelope,
                        "event_id",
                        "eventId",
                    ) or self._event_value(
                        payload,
                        "event_id",
                        "eventId",
                        "settlement_id",
                        "order_id",
                        "orderId",
                        "fill_id",
                        "fillId",
                        "id",
                    )
                    if not event_id:
                        raise ValueError(
                            f"paper WAL line {line_number} requires event_id"
                        )
                    fingerprint = self._paper_event_fingerprint(payload)
                    previous = self._paper_wal_records.get(event_id)
                    if previous is not None:
                        if (
                            str(previous.get("kind") or kind) != kind
                            or str(previous.get("fingerprint") or "") != fingerprint
                        ):
                            raise ValueError(
                                f"paper WAL event {event_id!r} has conflicting entries"
                            )
                        continue
                    record = {
                        "event_id": str(event_id),
                        "payload": copy.deepcopy(payload),
                        "fingerprint": fingerprint,
                        "kind": kind,
                        "request_fingerprint": envelope.get("request_fingerprint"),
                    }
                    self._paper_wal_records[event_id] = record
                    records.append(record)
        except OSError as exc:
            raise ValueError(f"could not read paper WAL {self.paper_wal_path}: {exc}") from exc

        self._paper_replaying_wal = True
        try:
            for record in records:
                payload = record["payload"]
                event_id = str(
                    record.get("event_id")
                    or self._event_value(
                        payload,
                        "event_id",
                        "eventId",
                        "settlement_id",
                        "order_id",
                        "orderId",
                        "fill_id",
                        "fillId",
                        "id",
                    )
                )
                kind = str(record.get("kind") or "")
                processed = self._processed_paper_events.get(event_id)
                expected_fingerprint = str(
                    record.get("request_fingerprint") or record["fingerprint"]
                )
                if processed is not None:
                    stored_fingerprint = processed.get("fingerprint")
                    if stored_fingerprint and stored_fingerprint != expected_fingerprint:
                        raise ValueError(
                            f"paper state and WAL disagree for event {event_id!r}"
                        )
                    expected_identifier = {
                        "order.created": self._event_value(payload, "order_id", "orderId"),
                        "order.canceled": self._event_value(payload, "order_id", "orderId"),
                        "order.filled": self._event_value(payload, "fill_id", "fillId", "id"),
                        "funding.settled": self._event_value(
                            payload, "settlement_id", "id"
                        ),
                    }[kind]
                    if str(processed.get("id") or "") != expected_identifier:
                        raise ValueError(
                            f"paper state and WAL disagree for event {event_id!r}"
                        )
                    continue
                if kind == "order.created":
                    self._apply_order_created_wal(payload, event_id=event_id)
                elif kind == "order.canceled":
                    self._apply_order_canceled_wal(payload, event_id=event_id)
                elif kind == "order.filled":
                    self.apply_fill_event(payload)
                elif kind == "funding.settled":
                    self._apply_funding_wal(payload, event_id=event_id)
        finally:
            self._paper_replaying_wal = False

    def _apply_order_created_wal(self, payload: dict, *, event_id: str) -> dict:
        raw_order = payload.get("order")
        order = self._state_order(raw_order)
        order_id = str(order["id"])
        existing = self._paper_orders.get(order_id)
        if existing is not None:
            if self._order_creation_identity(existing) != self._order_creation_identity(order):
                raise ValueError(
                    f"paper WAL order {order_id!r} conflicts with local state"
                )
            canonical = existing
        else:
            canonical = order
            self._paper_orders[order_id] = canonical
        if bool(payload.get("open")):
            self.open_orders[order_id] = canonical
        else:
            self.open_orders.pop(order_id, None)
        suffix = order_id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            self._next_order_id = max(self._next_order_id, int(suffix) + 1)
        self._remember_event(
            event_id,
            kind="order",
            identifier=order_id,
            fingerprint=str(payload.get("request_fingerprint") or "") or None,
        )
        return copy.deepcopy(canonical)

    def _apply_order_canceled_wal(self, payload: dict, *, event_id: str) -> dict:
        raw_order = payload.get("order")
        canceled = self._state_order(raw_order)
        order_id = str(canceled["id"])
        existing = self._paper_orders.get(order_id)
        if existing is None:
            self._paper_orders[order_id] = canceled
            existing = canceled
        else:
            existing.update(canceled)
        self.open_orders.pop(order_id, None)
        self._remember_event(
            event_id,
            kind="cancel",
            identifier=order_id,
            fingerprint=str(payload.get("request_fingerprint") or "") or None,
        )
        return copy.deepcopy(existing)

    def _apply_funding_wal(self, payload: dict, *, event_id: str) -> PaperFundingEvidence:
        evidence = payload.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("kind") != "funding.settled":
            raise ValueError(f"paper WAL funding event {event_id!r} has invalid evidence")
        aggregate = evidence.get("payload")
        if not isinstance(aggregate, dict):
            raise ValueError(f"paper WAL funding event {event_id!r} has invalid payload")
        settlement_id = str(
            aggregate.get("settlement_id") or payload.get("settlement_id") or ""
        ).strip()
        if not settlement_id:
            raise ValueError(f"paper WAL funding event {event_id!r} lacks settlement id")
        before = payload.get("accounting_before") or {}
        after = payload.get("accounting_after") or {}
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError(f"paper WAL funding event {event_id!r} has invalid accounting")
        for key in ("balance_total", "realized_pnl", "realized_funding"):
            current = float(getattr(self, key))
            expected = self._state_number(before.get(key), name=f"funding.{key}.before")
            if not math.isclose(current, expected, rel_tol=0.0, abs_tol=1e-8):
                raise ValueError(
                    f"paper WAL funding event {event_id!r} does not match current {key}"
                )
        self.now_ms = int(payload.get("timestamp") or evidence.get("ts") or self.now_ms)
        self.balance_total = self._state_number(after.get("balance_total"), name="funding.balance_total.after")
        self.balance_free = self.balance_total
        self.realized_pnl = self._state_number(after.get("realized_pnl"), name="funding.realized_pnl.after")
        self.realized_funding = self._state_number(after.get("realized_funding"), name="funding.realized_funding.after")
        request_fingerprint = str(payload.get("request_fingerprint") or "")
        if not request_fingerprint:
            raise ValueError(f"paper WAL funding event {event_id!r} lacks request fingerprint")
        self._paper_funding_settlements[settlement_id] = {
            "fingerprint": request_fingerprint,
            "evidence": copy.deepcopy(evidence),
        }
        self._remember_event(
            event_id,
            kind="funding",
            identifier=settlement_id,
            fingerprint=request_fingerprint,
        )
        return copy.deepcopy(evidence)

    @staticmethod
    def _state_number(value: object, *, name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"paper state {name} must be numeric") from exc
        if not math.isfinite(number):
            raise ValueError(f"paper state {name} must be finite")
        return number

    @staticmethod
    def _state_order(order: object) -> dict:
        if not isinstance(order, dict):
            raise ValueError("paper state order must be an object")
        result = copy.deepcopy(order)
        order_id = str(result.get("id") or "").strip()
        symbol = str(result.get("symbol") or "").strip()
        if not order_id or not symbol:
            raise ValueError("paper state order requires id and symbol")
        result["id"] = order_id
        result["symbol"] = symbol
        result["info"] = dict(result.get("info") or {})
        return result

    @staticmethod
    def _order_creation_identity(order: Mapping[str, Any]) -> dict[str, Any]:
        """Return only immutable order fields for create-WAL replay.

        A snapshot can contain the same order after fills changed ``status``,
        ``filled``, ``remaining`` and execution price. Those mutable fields
        must not make a valid create transition look like an ID conflict.
        """

        info = order.get("info") if isinstance(order.get("info"), Mapping) else {}
        return {
            "id": str(order.get("id") or ""),
            "symbol": str(order.get("symbol") or ""),
            "type": str(order.get("type") or ""),
            "side": str(order.get("side") or ""),
            "amount": float(order.get("amount")),
            "clientOrderId": str(order.get("clientOrderId") or ""),
            "reduceOnly": bool(order.get("reduceOnly")),
            "positionSide": str(
                info.get("positionSide") or info.get("posSide") or ""
            ).lower(),
            "request_fingerprint": str(info.get("request_fingerprint") or ""),
            "reference_price": float(info.get("reference_price")),
        }

    def _load_paper_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        if not self.state_path.is_file():
            raise ValueError(f"paper state_path is not a file: {self.state_path}")
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read paper state {self.state_path}: {exc}") from exc
        if not isinstance(state, dict):
            raise ValueError("paper state must contain a JSON object")
        try:
            state_version = int(state.get("state_version", -1))
        except (TypeError, ValueError) as exc:
            raise ValueError("paper state version must be an integer") from exc
        if state_version != self.PAPER_STATE_VERSION:
            raise ValueError(f"unsupported paper state version: {state.get('state_version')!r}")
        if str(state.get("quote") or "").upper() != self.paper_quote:
            raise ValueError("paper state quote does not match the requested quote")
        stored_symbols = [str(value) for value in state.get("symbols") or []]
        if stored_symbols != list(self.paper_symbols):
            raise ValueError("paper state symbols do not match the requested paper symbols")

        self.balance_total = self._state_number(
            state.get("balance_total"), name="balance_total"
        )
        self.balance_free = self._state_number(
            state.get("balance_free", self.balance_total), name="balance_free"
        )
        self.realized_pnl = self._state_number(
            state.get("realized_pnl", 0.0), name="realized_pnl"
        )
        self.realized_fees = self._state_number(
            state.get("realized_fees", 0.0), name="realized_fees"
        )
        self.realized_funding = self._state_number(
            state.get("realized_funding", 0.0), name="realized_funding"
        )
        self.now_ms = int(state.get("now_ms", self.now_ms))

        self.positions = {}
        position_keys = set()
        for symbol in self.paper_symbols:
            for position_side in ("long", "short"):
                self.positions[(symbol, position_side)] = {
                    "symbol": symbol,
                    "position_side": position_side,
                    "size": 0.0,
                    "entry_price": 0.0,
                }
        for raw_position in state.get("positions") or []:
            if not isinstance(raw_position, dict):
                raise ValueError("paper state position must be an object")
            symbol = str(raw_position.get("symbol") or "")
            position_side = str(raw_position.get("position_side") or "").lower()
            key = (symbol, position_side)
            if key not in self.positions:
                raise ValueError(f"paper state contains unknown position {key!r}")
            if key in position_keys:
                raise ValueError(f"paper state contains duplicate position {key!r}")
            position_keys.add(key)
            self.positions[key] = {
                "symbol": symbol,
                "position_side": position_side,
                "size": self._state_number(raw_position.get("size"), name="position.size"),
                "entry_price": self._state_number(
                    raw_position.get("entry_price"), name="position.entry_price"
                ),
            }

        self._paper_orders = {}
        raw_orders = state.get("orders")
        if raw_orders is None:
            raw_orders = state.get("open_orders") or []
        for raw_order in raw_orders or []:
            order = self._state_order(raw_order)
            order_id = order["id"]
            if order_id in self._paper_orders:
                raise ValueError(f"paper state contains duplicate order id {order_id!r}")
            self._paper_orders[order_id] = order
        self.open_orders = {}
        for raw_order in state.get("open_orders") or []:
            order = self._state_order(raw_order)
            order_id = order["id"]
            canonical = self._paper_orders.setdefault(order_id, order)
            if canonical is not order:
                canonical.update(order)
            self.open_orders[order_id] = canonical

        self.fills = []
        seen_fill_ids = set()
        for raw_fill in state.get("fills") or []:
            if not isinstance(raw_fill, dict):
                raise ValueError("paper state fill must be an object")
            fill = copy.deepcopy(raw_fill)
            fill_id = str(fill.get("id") or "").strip()
            if not fill_id:
                raise ValueError("paper state fill requires id")
            if fill_id in seen_fill_ids:
                raise ValueError(f"paper state contains duplicate fill id {fill_id!r}")
            seen_fill_ids.add(fill_id)
            self.fills.append(fill)

        raw_funding_settlements = state.get("funding_settlements", {})
        if not isinstance(raw_funding_settlements, dict):
            raise ValueError("paper state funding_settlements must be an object")
        self._paper_funding_settlements = {}
        for settlement_id, raw_record in raw_funding_settlements.items():
            normalized_settlement_id = str(settlement_id).strip()
            if not normalized_settlement_id or not isinstance(raw_record, dict):
                raise ValueError(
                    "paper state funding settlement requires an id and object record"
                )
            fingerprint = str(raw_record.get("fingerprint") or "").strip()
            evidence = raw_record.get("evidence")
            if not fingerprint or not isinstance(evidence, dict):
                raise ValueError(
                    f"paper state funding settlement {normalized_settlement_id!r} is malformed"
                )
            if evidence.get("kind") != "funding.settled":
                raise ValueError(
                    f"paper state funding settlement {normalized_settlement_id!r} has unsupported kind"
                )
            payload = evidence.get("payload")
            if not isinstance(payload, dict) or str(payload.get("settlement_id") or "") != normalized_settlement_id:
                raise ValueError(
                    f"paper state funding settlement {normalized_settlement_id!r} has invalid payload"
                )
            self._paper_funding_settlements[normalized_settlement_id] = {
                "fingerprint": fingerprint,
                "evidence": copy.deepcopy(evidence),
            }
        raw_feed_seen = state.get("public_execution_feed_seen_settlements", [])
        if not isinstance(raw_feed_seen, list):
            raise ValueError(
                "paper state public_execution_feed_seen_settlements must be a list"
            )
        self._paper_execution_feed_seen_settlements = {
            str(item).strip() for item in raw_feed_seen if str(item).strip()
        }
        stored_feed_path = state.get("public_execution_feed_path")
        if stored_feed_path not in (None, ""):
            normalized_feed_path = str(Path(str(stored_feed_path)).expanduser().resolve())
            if self._paper_execution_feed_path not in (None, normalized_feed_path):
                raise ValueError(
                    "paper state public execution input path does not match the requested path"
                )
            self._paper_execution_feed_path = normalized_feed_path
        stored_feed_hash = state.get("public_execution_feed_source_hash")
        if stored_feed_hash not in (None, ""):
            normalized_feed_hash = str(stored_feed_hash).strip().lower()
            if len(normalized_feed_hash) != 64 or any(
                char not in "0123456789abcdef" for char in normalized_feed_hash
            ):
                raise ValueError(
                    "paper state public execution input source hash must be SHA-256"
                )
            self._paper_execution_feed_source_hash = normalized_feed_hash

        self._next_order_id = int(state.get("next_order_id", 1))
        self._next_trade_id = int(state.get("next_trade_id", 1))
        if self._next_order_id < 1 or self._next_trade_id < 1:
            raise ValueError("paper state next IDs must be positive")
        processed = state.get("processed_events") or {}
        if not isinstance(processed, dict):
            raise ValueError("paper state processed_events must be an object")
        self._processed_paper_events = {}
        for event_id, record in processed.items():
            if not isinstance(record, dict):
                raise ValueError("paper state processed event must be an object")
            kind = str(record.get("kind") or "")
            identifier = str(record.get("id") or "")
            if kind not in {"order", "cancel", "fill", "funding"} or not identifier:
                raise ValueError("paper state processed event requires kind and id")
            self._processed_paper_events[str(event_id)] = {
                "kind": kind,
                "id": identifier,
                **(
                    {"fingerprint": str(record["fingerprint"])}
                    if record.get("fingerprint") not in (None, "")
                    else {}
                ),
            }

    def _existing_fill(self, fill_id: str) -> dict | None:
        for fill in self.fills:
            if str(fill.get("id") or "") == str(fill_id):
                return fill
        return None

    def _event_result(
        self,
        event_id: str,
        *,
        kind: str,
        fingerprint: str | None = None,
    ) -> dict | None:
        record = self._processed_paper_events.get(str(event_id))
        if record is None:
            return None
        if str(record.get("kind") or "") != kind:
            raise ValueError(
                f"paper event {event_id!r} was already applied as {record.get('kind')!r}"
            )
        stored_fingerprint = record.get("fingerprint")
        if fingerprint is not None and stored_fingerprint not in (None, fingerprint):
            raise ValueError(f"paper event {event_id!r} has a conflicting payload")
        identifier = str(record.get("id") or "")
        if kind == "order":
            result = self._paper_orders.get(identifier)
        elif kind == "fill":
            result = self._existing_fill(identifier)
        elif kind == "cancel":
            result = self._paper_orders.get(identifier)
        else:
            stored = self._paper_funding_settlements.get(identifier)
            result = stored.get("evidence") if isinstance(stored, dict) else None
        if result is None:
            raise ValueError(f"paper event {event_id!r} points to missing {kind} {identifier!r}")
        return copy.deepcopy(result)

    def apply_funding_settlement(
        self,
        settlement: Mapping[str, Any] | None = None,
        **settlement_fields: Any,
    ) -> PaperFundingEvidence:
        """Apply one explicit funding settlement to the local paper ledger.

        The caller must provide the settlement id, rate, timestamp in Unix
        milliseconds, and a mark price for every selected open position.
        Positive funding is a payment by a long and a receipt by a short.
        The signed account payment is applied once to the local balance and
        realized PnL; it is not treated as a trading fee.

        Replaying the same canonical settlement id is idempotent and returns
        the original funding.settled evidence. A changed request with an
        existing id is rejected. This synchronous method reads only the local
        paper ledger and never calls a public or private exchange operation.
        """

        if settlement is None:
            settlement = settlement_fields
        elif settlement_fields:
            raise TypeError(
                "paper funding settlement accepts either an object or keyword fields"
            )
        if not isinstance(settlement, Mapping):
            raise TypeError("paper funding settlement must be an object")
        settlement_id = settlement.get("settlement_id", settlement.get("id"))
        rate = settlement.get("rate", settlement.get("funding_rate"))
        timestamp = settlement.get(
            "timestamp", settlement.get("timestamp_ms", settlement.get("funding_timestamp"))
        )
        symbol = settlement.get("symbol")
        position_side = settlement.get(
            "position_side", settlement.get("positionSide", settlement.get("posSide"))
        )
        source = settlement.get("source", "explicit_paper_input")
        raw_mark = settlement.get("mark_price", settlement.get("markPrice"))
        mark_prices = settlement.get("mark_prices")
        if raw_mark is not None:
            mark_prices = {symbol: raw_mark}
        if mark_prices is not None and not isinstance(mark_prices, Mapping):
            raise TypeError("paper funding mark_prices must be an object")

        if settlement_id is None or isinstance(settlement_id, bool):
            raise ValueError("paper funding settlement_id must not be empty")
        normalized_id = str(settlement_id).strip()
        if not normalized_id:
            raise ValueError("paper funding settlement_id must not be empty")
        if len(normalized_id) > 256:
            raise ValueError("paper funding settlement_id is too long")

        if rate is None or isinstance(rate, bool):
            raise ValueError("paper funding rate must be numeric")
        try:
            numeric_rate = float(rate)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("paper funding rate must be numeric") from exc
        if not math.isfinite(numeric_rate) or not -1.0 <= numeric_rate <= 1.0:
            raise ValueError("paper funding rate must be finite and within [-1, 1]")

        if timestamp is None or isinstance(timestamp, bool):
            raise ValueError(
                "paper funding timestamp must be an integer in Unix milliseconds"
            )
        try:
            timestamp_number = float(timestamp)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "paper funding timestamp must be an integer in Unix milliseconds"
            ) from exc
        if (
            not math.isfinite(timestamp_number)
            or not timestamp_number.is_integer()
            or timestamp_number <= 0
            or timestamp_number > 2**63 - 1
        ):
            raise ValueError(
                "paper funding timestamp must be a positive integer in Unix milliseconds"
            )
        settlement_ts = int(timestamp_number)

        if source is None or isinstance(source, bool):
            raise ValueError("paper funding source must not be empty")
        normalized_source = str(source).strip()
        if not normalized_source:
            raise ValueError("paper funding source must not be empty")
        if len(normalized_source) > 256:
            raise ValueError("paper funding source is too long")
        raw_source_hash = settlement.get("source_hash")
        if raw_source_hash in (None, ""):
            source_hash = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
            source_hash_kind = "source_label_sha256"
        else:
            source_hash = str(raw_source_hash).strip().lower()
            if len(source_hash) != 64 or any(
                char not in "0123456789abcdef" for char in source_hash
            ):
                raise ValueError("paper funding source_hash must be a SHA-256 hex digest")
            source_hash_kind = "provided_sha256"

        context: dict[str, str] = {}
        for key in ("strategy_id", "cycle_id", "config_hash"):
            value = self._paper_context_value(settlement.get(key))
            if value is not None:
                context[key] = value

        normalized_side = (
            None
            if position_side is None
            else str(position_side).lower().strip()
        )
        if normalized_side not in {None, "long", "short"}:
            raise ValueError("paper funding position_side must be long, short, or omitted")

        if symbol is None or isinstance(symbol, bool):
            raise ValueError("paper funding settlement requires symbol")
        selected_symbols = (
            [_paper_symbol(symbol, quote=self.paper_quote)]
            if symbol is not None
            else list(self.paper_symbols)
        )
        unknown_symbols = sorted(set(selected_symbols) - set(self.paper_symbols))
        if unknown_symbols:
            raise ValueError(
                "paper funding settlement contains unknown symbol(s): "
                + ", ".join(unknown_symbols)
            )

        if mark_prices is not None and not isinstance(mark_prices, Mapping):
            raise TypeError("paper funding mark_prices must be an object")
        supplied_marks: dict[str, float] = {}
        for raw_symbol, raw_mark in (mark_prices or {}).items():
            normalized_symbol = _paper_symbol(
                raw_symbol, quote=self.paper_quote
            )
            if normalized_symbol not in self.paper_symbols:
                raise ValueError(
                    f"paper funding mark price contains unknown symbol {normalized_symbol}"
                )
            if raw_mark is None or isinstance(raw_mark, bool):
                raise ValueError(
                    f"paper funding mark price for {normalized_symbol} must be numeric"
                )
            try:
                mark = float(raw_mark)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"paper funding mark price for {normalized_symbol} must be numeric"
                ) from exc
            if not math.isfinite(mark) or mark <= 0.0 or mark > 1e12:
                raise ValueError(
                    f"paper funding mark price for {normalized_symbol} must be finite, positive and <= 1e12"
                )
            supplied_marks[normalized_symbol] = mark

        request_payload = {
            "settlement_id": normalized_id,
            "rate": numeric_rate,
            "timestamp": settlement_ts,
            "symbols": selected_symbols,
            "position_side": normalized_side,
            "mark_prices": dict(sorted(supplied_marks.items())),
            "source": normalized_source,
            "source_hash": source_hash,
            **context,
        }
        fingerprint = self._paper_event_fingerprint(request_payload)
        replayed = self._event_result(
            normalized_id,
            kind="funding",
            fingerprint=fingerprint,
        )
        if replayed is not None:
            return replayed

        payment_payloads: list[dict[str, float | str]] = []
        total_account_delta = 0.0
        signed_notional = 0.0
        active_symbols: set[str] = set()
        for selected_symbol in selected_symbols:
            sides = (normalized_side,) if normalized_side else ("long", "short")
            for side in sides:
                position = self.positions.get((selected_symbol, side))
                if not isinstance(position, dict):
                    raise ValueError(
                        f"paper funding local position is missing for {selected_symbol} {side}"
                    )
                raw_quantity = position.get("size")
                if raw_quantity is None or isinstance(raw_quantity, bool):
                    raise ValueError(
                        f"paper funding position size is missing for {selected_symbol} {side}"
                    )
                try:
                    quantity = float(raw_quantity)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(
                        f"paper funding position size must be numeric for {selected_symbol} {side}"
                    ) from exc
                if not math.isfinite(quantity) or quantity < 0.0 or quantity > 1e12:
                    raise ValueError(
                        f"paper funding position size is outside the supported range for {selected_symbol} {side}"
                    )
                if quantity == 0.0:
                    continue

                raw_mark = supplied_marks.get(selected_symbol)
                if raw_mark is None:
                    raise ValueError(
                        "paper funding requires an explicit mark price for non-empty position "
                        f"{selected_symbol}"
                    )
                contract_multiplier = float(self._c_mult(selected_symbol))
                if (
                    not math.isfinite(contract_multiplier)
                    or contract_multiplier <= 0.0
                    or contract_multiplier > 1e6
                ):
                    raise ValueError(
                        f"paper contract multiplier is outside the supported range for {selected_symbol}"
                    )
                notional = quantity * raw_mark * contract_multiplier
                if not math.isfinite(notional) or notional > 1e18:
                    raise ValueError(
                        f"paper funding notional is outside the supported range for {selected_symbol} {side}"
                    )

                position_payment = notional * numeric_rate
                if side == "short":
                    position_payment = -position_payment
                account_delta = -position_payment
                if not math.isfinite(account_delta):
                    raise ValueError("paper funding account delta must be finite")
                signed_notional += notional if side == "long" else -notional
                total_account_delta += account_delta
                active_symbols.add(selected_symbol)
                payment_payloads.append(
                    {
                        "symbol": selected_symbol,
                        "position_side": side,
                        "mark_price": float(raw_mark),
                        "notional": float(notional),
                        "amount": float(position_payment),
                        "account_delta": float(account_delta),
                        "rate": float(numeric_rate),
                        "currency": str(self.paper_quote).upper(),
                    }
                )

        if not payment_payloads:
            selected = (
                f" {normalized_side}" if normalized_side is not None else ""
            )
            scope = (
                _paper_symbol(symbol, quote=self.paper_quote)
                if symbol is not None
                else "selected symbols"
            )
            raise ValueError(
                f"paper funding settlement {normalized_id!r} has no open{selected} "
                f"paper position for {scope}"
            )
        if (
            not math.isfinite(signed_notional)
            or not math.isfinite(total_account_delta)
            or abs(signed_notional) > 1e18
            or abs(total_account_delta) > 1e18
        ):
            raise ValueError("paper funding aggregate is outside the supported range")

        balance_before = float(self.balance_total)
        realized_pnl_before = float(self.realized_pnl)
        realized_funding_before = float(self.realized_funding)
        realized_fees_before = float(self.realized_fees)
        if not all(
            math.isfinite(value)
            for value in (
                balance_before,
                realized_pnl_before,
                realized_funding_before,
                realized_fees_before,
            )
        ):
            raise ValueError("paper funding accounting state must be finite")
        balance_after = balance_before + total_account_delta
        realized_pnl_after = realized_pnl_before + total_account_delta
        realized_funding_after = realized_funding_before + total_account_delta
        if not all(
            math.isfinite(value)
            for value in (
                balance_after,
                realized_pnl_after,
                realized_funding_after,
            )
        ):
            raise ValueError("paper funding accounting result must be finite")

        settlement_funding: dict[str, float | str] = {
            # ``amount`` is the signed quote-currency account impact.  A long
            # paying funding therefore has a negative amount, matching the
            # balance and realized-funding ledger deltas.
            "amount": float(total_account_delta),
            "rate": float(numeric_rate),
            "notional": float(abs(signed_notional)),
            "currency": str(self.paper_quote).upper(),
        }
        funding_events = [
            {
                "schema_version": PAPER_FUNDING_SCHEMA,
                "ts": settlement_ts,
                "kind": "funding.settled",
                "tags": ["funding", "paper"],
                "payload": {
                    "id": f"{normalized_id}:{payment['symbol']}:{payment['position_side']}",
                    "settlement_id": normalized_id,
                    "event_id": f"{normalized_id}:{index}",
                    "timestamp": settlement_ts,
                    "symbol": payment["symbol"],
                    "position_side": payment["position_side"],
                    "quote_currency": str(self.paper_quote).upper(),
                    "funding": {
                        "amount": payment["amount"],
                        "rate": payment["rate"],
                        "notional": payment["notional"],
                        "currency": payment["currency"],
                    },
                    "positions": [payment],
                    "source": normalized_source,
                    "source_hash": source_hash,
                    "source_hash_kind": source_hash_kind,
                    "paper_contract": PAPER_FUNDING_SCHEMA,
                    **context,
                },
            }
            for index, payment in enumerate(payment_payloads)
        ]
        aggregate_payload: PaperFundingPayload = {
            "id": normalized_id,
            "settlement_id": normalized_id,
            "event_id": normalized_id,
            "timestamp": settlement_ts,
            "symbols": sorted(active_symbols),
            "position_side": (
                normalized_side
                if normalized_side is not None
                else (
                    str(payment_payloads[0]["position_side"])
                    if len(payment_payloads) == 1
                    else "net"
                )
            ),
            "position_sides": [
                str(item["position_side"]) for item in payment_payloads
            ],
            "quote_currency": str(self.paper_quote).upper(),
            "funding": settlement_funding,
            "positions": payment_payloads,
            "funding_events": funding_events,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "realized_pnl_before": realized_pnl_before,
            "realized_pnl_after": realized_pnl_after,
            "realized_funding_before": realized_funding_before,
            "realized_funding_after": realized_funding_after,
            "realized_fees_before": realized_fees_before,
            "realized_fees_after": realized_fees_before,
            "paper_contract": PAPER_FUNDING_SCHEMA,
            "source": normalized_source,
            "source_hash": source_hash,
            "source_hash_kind": source_hash_kind,
            **context,
        }
        aggregate_evidence: PaperFundingEvidence = {
            "schema_version": PAPER_FUNDING_SCHEMA,
            "ts": settlement_ts,
            "kind": "funding.settled",
            "tags": ["paper", "funding", "settlement"],
            "payload": aggregate_payload,
        }
        if len(active_symbols) == 1:
            aggregate_payload["symbol"] = next(iter(active_symbols))

        accounting_before = {
            "balance_total": balance_before,
            "realized_pnl": realized_pnl_before,
            "realized_funding": realized_funding_before,
        }
        accounting_after = {
            "balance_total": balance_after,
            "realized_pnl": realized_pnl_after,
            "realized_funding": realized_funding_after,
        }
        self._append_paper_wal_record(
            kind="funding.settled",
            payload={
                "timestamp": settlement_ts,
                "settlement_id": normalized_id,
                "evidence": copy.deepcopy(aggregate_evidence),
                "accounting_before": accounting_before,
                "accounting_after": accounting_after,
            },
            event_id=normalized_id,
            schema_version=PAPER_FUNDING_SCHEMA,
            request_fingerprint=fingerprint,
        )
        if self.paper_wal_path is not None:
            self._inject_paper_crash("after_wal")
        self.now_ms = settlement_ts
        self.balance_total = balance_after
        self.balance_free = balance_after
        self.realized_funding = realized_funding_after
        self.realized_pnl = realized_pnl_after
        self._paper_funding_settlements[normalized_id] = {
            "fingerprint": fingerprint,
            "evidence": copy.deepcopy(aggregate_evidence),
        }
        self._inject_paper_crash("after_ledger_mutation")
        self._remember_event(
            normalized_id,
            kind="funding",
            identifier=normalized_id,
            fingerprint=fingerprint,
        )
        self._persist_paper_state()
        return copy.deepcopy(aggregate_evidence)

    def build_paper_reconciliation_evidence(
        self,
        *,
        timestamp: int | None = None,
        source: str = "paper-local-ledger-v1",
        source_hash: str | None = None,
        strategy_id: object = None,
        cycle_id: object = None,
        config_hash: object = None,
    ) -> list[dict]:
        """Build explicit local ledger and cost reconciliation evidence.

        This method is read-only.  It compares the current paper ledger with
        the fills and explicit funding settlements already stored locally.  It
        never compares the ledger to an exchange claim and never turns missing
        funding into a successful result.
        """

        raw_timestamp = self.now_ms if timestamp is None else timestamp
        if isinstance(raw_timestamp, bool):
            raise ValueError("paper reconciliation timestamp must be an integer")
        try:
            reconciliation_ts = int(raw_timestamp)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("paper reconciliation timestamp must be an integer") from exc
        if reconciliation_ts <= 0:
            raise ValueError("paper reconciliation timestamp must be positive")

        normalized_source = str(source or "").strip()
        if not normalized_source:
            raise ValueError("paper reconciliation source must not be empty")
        if len(normalized_source) > 256:
            raise ValueError("paper reconciliation source is too long")

        configured_source_hash = None
        configured_passport = None
        if self.paper_execution_inputs_path is not None:
            configured_feed = self._load_public_execution_feed()
            configured_source_hash = configured_feed.source_hash
            configured_passport = configured_feed.passport
        known_source_hashes: set[str] = set()
        for record in self._paper_funding_settlements.values():
            evidence = record.get("evidence") if isinstance(record, dict) else None
            payload = evidence.get("payload") if isinstance(evidence, dict) else None
            candidate = payload.get("source_hash") if isinstance(payload, dict) else None
            if isinstance(candidate, str) and len(candidate) == 64:
                known_source_hashes.add(candidate.lower())

        supplied_source_hash = None
        if source_hash not in (None, ""):
            supplied_source_hash = str(source_hash).strip().lower()
            if len(supplied_source_hash) != 64 or any(
                char not in "0123456789abcdef" for char in supplied_source_hash
            ):
                raise ValueError(
                    "paper reconciliation source_hash must be a SHA-256 hex digest"
                )

        if configured_source_hash is not None:
            if supplied_source_hash not in (None, configured_source_hash):
                raise ValueError(
                    "paper reconciliation source_hash does not match the configured public input"
                )
            resolved_source_hash = configured_source_hash
            source_hash_kind = "public_input_artifact_sha256"
            source_hash_verified = True
        elif known_source_hashes:
            if supplied_source_hash is not None and supplied_source_hash not in known_source_hashes:
                raise ValueError(
                    "paper reconciliation source_hash does not match a funding source"
                )
            if supplied_source_hash is None and len(known_source_hashes) != 1:
                raise ValueError(
                    "paper reconciliation requires an explicit source_hash for multiple funding sources"
                )
            resolved_source_hash = supplied_source_hash or next(iter(known_source_hashes))
            source_hash_kind = "funding_artifact_sha256"
            source_hash_verified = True
        elif supplied_source_hash is not None:
            resolved_source_hash = supplied_source_hash
            source_hash_kind = "provided_sha256"
            source_hash_verified = False
        else:
            resolved_source_hash = self._paper_event_fingerprint(self._state_snapshot())
            source_hash_kind = "paper_state_sha256"
            source_hash_verified = True

        context: dict[str, str] = {}
        for key, value in (
            ("strategy_id", strategy_id),
            ("cycle_id", cycle_id),
            ("config_hash", config_hash),
        ):
            normalized = self._paper_context_value(value)
            if normalized is not None:
                context[key] = normalized

        fee_total = 0.0
        typed_fees_complete = bool(self.fills)
        typed_slippage_complete = bool(self.fills)
        slippage_difference_bps = 0.0
        for fill in self.fills:
            info = fill.get("info") if isinstance(fill, dict) else None
            info = info if isinstance(info, dict) else {}
            typed_fee = info.get("typed_fee")
            typed_slippage = info.get("typed_slippage")
            if not isinstance(typed_fee, dict):
                typed_fees_complete = False
            else:
                try:
                    fee_amount = float(typed_fee["amount"])
                    fee_currency = str(typed_fee["currency"]).strip().upper()
                except (KeyError, TypeError, ValueError, OverflowError):
                    typed_fees_complete = False
                else:
                    if (
                        not math.isfinite(fee_amount)
                        or fee_amount < 0.0
                        or fee_currency != str(self.paper_quote).upper()
                    ):
                        typed_fees_complete = False
                    else:
                        fee_total += fee_amount
            if not isinstance(typed_slippage, dict):
                typed_slippage_complete = False
            else:
                try:
                    reference = float(typed_slippage["reference_price"])
                    executed = float(typed_slippage["executed_price"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    typed_slippage_complete = False
                else:
                    actual_price = fill.get("price")
                    if not all(
                        math.isfinite(value) and value > 0.0
                        for value in (reference, executed)
                    ):
                        typed_slippage_complete = False
                    else:
                        try:
                            actual = float(actual_price)
                        except (TypeError, ValueError, OverflowError):
                            typed_slippage_complete = False
                        else:
                            if not math.isfinite(actual) or actual <= 0.0:
                                typed_slippage_complete = False
                            else:
                                slippage_difference_bps = max(
                                    slippage_difference_bps,
                                    abs(executed - actual) / actual * 10_000.0,
                                )

        funding_total = 0.0
        funding_settlements_complete = bool(self._paper_funding_settlements)
        for record in self._paper_funding_settlements.values():
            evidence = record.get("evidence") if isinstance(record, dict) else None
            payload = evidence.get("payload") if isinstance(evidence, dict) else None
            funding = payload.get("funding") if isinstance(payload, dict) else None
            if not isinstance(funding, dict):
                funding_settlements_complete = False
                continue
            try:
                amount = float(funding["amount"])
                currency = str(funding["currency"]).strip().upper()
            except (KeyError, TypeError, ValueError, OverflowError):
                funding_settlements_complete = False
                continue
            if not math.isfinite(amount) or currency != str(self.paper_quote).upper():
                funding_settlements_complete = False
                continue
            funding_total += amount
        if configured_passport is not None:
            funding_settlements_complete = (
                funding_settlements_complete and configured_passport.qualifying
            )

        realized_fees = float(self.realized_fees)
        realized_funding = float(self.realized_funding)
        fee_difference = abs(realized_fees - fee_total)
        funding_difference = abs(realized_funding - funding_total)
        balance_match = math.isclose(
            float(self.balance_total), float(self.balance_free), rel_tol=1e-9, abs_tol=1e-8
        )
        fee_match = math.isclose(fee_difference, 0.0, rel_tol=1e-9, abs_tol=1e-8)
        funding_match = math.isclose(
            funding_difference, 0.0, rel_tol=1e-9, abs_tol=1e-8
        )
        positions_match = all(
            isinstance(key, tuple)
            and len(key) == 2
            and key[0] in self.paper_symbols
            and key[1] in {"long", "short"}
            for key in self.positions
        )
        checks = [
            {"name": "balance_free_equals_total", "match": balance_match},
            {"name": "fees_equal_typed_fills", "match": fee_match},
            {"name": "funding_equal_settlements", "match": funding_match},
            {"name": "positions_have_known_symbols", "match": positions_match},
        ]
        mismatch_count = sum(1 for check in checks if not check["match"])
        all_costs_present = bool(
            self.fills
            and typed_fees_complete
            and typed_slippage_complete
            and funding_settlements_complete
        )
        reconciliation_payload = {
            "reconciliation": {
                "mismatch_count": mismatch_count,
                "checks": checks,
                "source": normalized_source,
                "source_hash": resolved_source_hash,
                "source_hash_verified": source_hash_verified,
                "data_passport": (
                    configured_passport.as_dict()
                    if configured_passport is not None
                    else None
                ),
            },
            "source": normalized_source,
            "source_hash": resolved_source_hash,
            "source_hash_kind": source_hash_kind,
            "source_hash_verified": source_hash_verified,
            "timestamp": reconciliation_ts,
            **context,
        }
        costs_payload = {
            "all_costs_present": all_costs_present,
            "costs": {
                "all_costs_present": all_costs_present,
                "fee_total_usdt": fee_total,
                "funding_total_usdt": funding_total,
                "fee_reconciliation_abs_usdt": fee_difference,
                "funding_reconciliation_abs_usdt": funding_difference,
                "slippage_reconciliation_abs_bps": slippage_difference_bps
                if typed_slippage_complete
                else 1.0,
                "source": normalized_source,
                "source_hash": resolved_source_hash,
                "source_hash_verified": source_hash_verified,
                "data_passport": (
                    configured_passport.as_dict()
                    if configured_passport is not None
                    else None
                ),
            },
            "source": normalized_source,
            "source_hash": resolved_source_hash,
            "source_hash_kind": source_hash_kind,
            "source_hash_verified": source_hash_verified,
            "timestamp": reconciliation_ts,
            **context,
        }
        evidence = [
            {
                "schema_version": PAPER_RECONCILIATION_SCHEMA,
                "ts": reconciliation_ts,
                "kind": "reconciliation.check",
                "tags": ["paper", "reconciliation"],
                "payload": reconciliation_payload,
            }
        ]
        # Do not write an invalid ``costs.reconciled`` event while funding is
        # absent.  A later complete event must be able to prove the full
        # runtime without an earlier incomplete event poisoning the validator.
        if all_costs_present:
            evidence.append(
                {
                    "schema_version": PAPER_RECONCILIATION_SCHEMA,
                    "ts": reconciliation_ts,
                    "kind": "costs.reconciled",
                    "tags": ["paper", "reconciliation", "costs"],
                    "payload": costs_payload,
                }
            )
        return evidence

    def _remember_event(
        self,
        event_id: str | None,
        *,
        kind: str,
        identifier: str,
        fingerprint: str | None = None,
    ) -> None:
        if event_id:
            record = {
                "kind": kind,
                "id": str(identifier),
            }
            if fingerprint is not None:
                record["fingerprint"] = str(fingerprint)
            self._processed_paper_events[str(event_id)] = record

    @staticmethod
    def _event_value(params: dict, *names: str) -> str:
        for name in names:
            value = params.get(name)
            if value not in (None, ""):
                return str(value)
        return ""

    def _find_existing_order(self, order_id: str, client_order_id: str) -> dict | None:
        if order_id and order_id in self._paper_orders:
            return self._paper_orders[order_id]
        if client_order_id:
            for order in self._paper_orders.values():
                if str(order.get("clientOrderId") or "") == client_order_id:
                    return order
        return None

    def _clock_ms(self) -> int:
        if self._clock_override_ms is not None:
            return int(self._clock_override_ms)
        return int(utc_ms())

    def _record_public_request(self, method: str, **payload: Any) -> None:
        row = {"timestamp": self._clock_ms(), "method": method}
        row.update({key: copy.deepcopy(value) for key, value in payload.items()})
        self.public_request_log.append(row)

    @staticmethod
    def _is_linear_usdt_swap(market: dict, quote: str) -> bool:
        return bool(
            market.get("swap")
            and market.get("linear")
            and str(market.get("quote") or "").upper() == quote
            and str(market.get("settle") or "").upper() == quote
        )

    def _normalize_public_market(self, market: dict) -> dict:
        normalized = copy.deepcopy(market)
        symbol = _paper_symbol(normalized.get("symbol"), quote=self.paper_quote)
        normalized["symbol"] = symbol
        normalized.setdefault("type", "swap")
        normalized.setdefault("swap", True)
        normalized.setdefault("linear", True)
        normalized.setdefault("inverse", False)
        normalized.setdefault("active", True)
        normalized.setdefault("base", symbol.split("/", 1)[0])
        normalized.setdefault("quote", self.paper_quote)
        normalized.setdefault("settle", self.paper_quote)
        precision = dict(normalized.get("precision") or {})
        precision.setdefault("amount", 0.001)
        precision.setdefault("price", 0.1)
        normalized["precision"] = precision
        limits = dict(normalized.get("limits") or {})
        limits.setdefault("amount", {})
        limits.setdefault("cost", {})
        limits["amount"] = dict(limits["amount"] or {})
        limits["cost"] = dict(limits["cost"] or {})
        limits["amount"].setdefault("min", precision["amount"])
        limits["cost"].setdefault("min", 5.1)
        normalized["limits"] = limits
        normalized.setdefault("contractSize", 1.0)
        normalized.setdefault("maker", 0.0002)
        normalized.setdefault("taker", 0.00055)
        info = dict(normalized.get("info") or {})
        info["paper_market_source"] = "bitget_public_ccxt"
        normalized["info"] = info
        return normalized

    def _reset_local_symbols(self, markets: Dict[str, dict]) -> None:
        previous_positions = getattr(self, "positions", {})
        previous_orders = getattr(self, "open_orders", {})
        self.markets = markets
        self.markets_by_id = {market["id"]: market for market in markets.values()}
        self.symbols = sorted(markets)
        self._candles_by_symbol = {symbol: [] for symbol in self.symbols}
        self.positions = {}
        for symbol in self.symbols:
            for pside in ("long", "short"):
                previous = previous_positions.get((symbol, pside))
                self.positions[(symbol, pside)] = copy.deepcopy(
                    previous
                    if previous is not None
                    else {
                        "symbol": symbol,
                        "position_side": pside,
                        "size": 0.0,
                        "entry_price": 0.0,
                    }
                )
        self.open_orders = {
            order_id: order
            for order_id, order in previous_orders.items()
            if order.get("symbol") in self.markets
        }

    async def load_markets(self, reload: bool = True) -> Dict[str, dict]:
        self._record_public_request("load_markets", reload=bool(reload))
        public_markets = await self.public_client.load_markets(reload)
        selected: Dict[str, dict] = {}
        requested = set(self.paper_symbols)
        for raw_market in public_markets.values():
            if not isinstance(raw_market, dict):
                continue
            if not self._is_linear_usdt_swap(raw_market, self.paper_quote):
                continue
            try:
                normalized = self._normalize_public_market(raw_market)
            except (TypeError, ValueError):
                continue
            if normalized["symbol"] in requested:
                selected[normalized["symbol"]] = normalized
        missing = sorted(requested - set(selected))
        if missing:
            raise ValueError(
                "Bitget public market metadata did not contain requested paper symbols: "
                + ", ".join(missing)
            )
        self._reset_local_symbols(selected)
        self._record_request("load_markets", reload=bool(reload), rows=len(selected))
        return copy.deepcopy(selected)

    @staticmethod
    def _ticker_value(raw: dict, key: str, fallback: float | None = None) -> float | None:
        value = raw.get(key)
        if value in (None, "") and isinstance(raw.get("info"), dict):
            value = raw["info"].get(key)
        if value in (None, ""):
            return fallback
        try:
            number = float(value)
        except (TypeError, ValueError):
            return fallback
        return number if math.isfinite(number) and number > 0.0 else fallback

    async def _fetch_public_ticker(self, symbol: str) -> dict:
        if symbol not in self.markets:
            raise KeyError(f"Unknown paper symbol {symbol}")
        self._record_public_request("fetch_ticker", symbol=symbol)
        raw = await self.public_client.fetch_ticker(symbol)
        last = self._ticker_value(raw, "last")
        bid = self._ticker_value(raw, "bid", last)
        ask = self._ticker_value(raw, "ask", last)
        if last is None:
            last = ((bid or 0.0) + (ask or 0.0)) / 2.0
        if bid is None:
            bid = last
        if ask is None:
            ask = last
        if not all(math.isfinite(float(value)) and float(value) > 0.0 for value in (bid, ask, last)):
            raise ValueError(f"Bitget paper ticker has no usable price for {symbol}: {raw!r}")
        timestamp = raw.get("timestamp") or self._clock_ms()
        try:
            timestamp = int(timestamp)
        except (TypeError, ValueError):
            timestamp = self._clock_ms()
        info = dict(raw.get("info") or {})
        info["paper_source"] = "bitget_public_market_data"
        mark_index = extract_mark_index(raw, symbol=symbol, required=False)
        if mark_index["mark_price"] is not None:
            info["markPrice"] = mark_index["mark_price"]
            info["paper_mark_source"] = mark_index["mark_source"]
        if mark_index["index_price"] is not None:
            info["indexPrice"] = mark_index["index_price"]
            info["paper_index_source"] = mark_index["index_source"]
        info["paper_last_substituted_for_mark"] = False
        result = {
            "symbol": symbol,
            "timestamp": timestamp,
            "datetime": ts_to_date(timestamp),
            "bid": float(bid),
            "ask": float(ask),
            "last": float(last),
            "mark": mark_index["mark_price"],
            "markPrice": mark_index["mark_price"],
            "index": mark_index["index_price"],
            "indexPrice": mark_index["index_price"],
            "info": info,
        }
        self._last_quotes[symbol] = result
        self.now_ms = timestamp
        self._settle_resting_orders(symbol, result)
        return copy.deepcopy(result)

    async def fetch_ticker(self, symbol: str) -> dict:
        return await self._fetch_public_ticker(symbol)

    async def fetch_tickers(self) -> Dict[str, dict]:
        result = {}
        for symbol in self.symbols:
            result[symbol] = await self._fetch_public_ticker(symbol)
        self._record_request("fetch_tickers", rows=len(result))
        return result

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: Optional[int] = None,
        limit: Optional[int] = None,
        params: Optional[dict] = None,
    ) -> List[List[float]]:
        if symbol not in self.markets:
            raise KeyError(f"Unknown paper symbol {symbol}")
        self._record_public_request(
            "fetch_ohlcv",
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            limit=limit,
        )
        rows = await self.public_client.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            since=since,
            limit=limit,
            params=params or {},
        )
        normalized = []
        until = (params or {}).get("until")
        for row in rows or []:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            values = [float(value) for value in row[:6]]
            if since is not None and values[0] < int(since):
                continue
            if until is not None and values[0] > int(until):
                continue
            normalized.append(values)
        normalized.sort(key=lambda row: row[0])
        deduped = {int(row[0]): row for row in normalized}
        result = list(deduped.values())
        self._record_request(
            "fetch_ohlcv",
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            limit=limit,
            rows=len(result),
        )
        return result

    async def fetch_time(self) -> int:
        self._record_public_request("fetch_time")
        fetch_time = getattr(self.public_client, "fetch_time", None)
        if callable(fetch_time):
            result = await fetch_time()
            self.now_ms = int(result)
            return int(result)
        self.now_ms = self._clock_ms()
        return int(self.now_ms)

    def _settle_resting_orders(self, symbol: str, quote: dict) -> None:
        to_fill = []
        for order_id, order in list(self.open_orders.items()):
            if order.get("symbol") != symbol:
                continue
            price = float(order["price"])
            if order["side"] == "buy" and float(quote["ask"]) <= price:
                to_fill.append((order, price))
            elif order["side"] == "sell" and float(quote["bid"]) >= price:
                to_fill.append((order, price))
        for order, fill_price in to_fill:
            configured_part = (order.get("info") or {}).get("paper_fill_amount")
            remaining = max(
                0.0,
                float(order.get("amount", 0.0))
                - float(order.get("filled", 0.0)),
            )
            if configured_part not in (None, ""):
                configured_part = min(float(configured_part), remaining)
            self._fill_order(
                order,
                fill_price=fill_price,
                liquidity="maker",
                reference_price=order.get("price"),
                remove_from_open_orders=True,
                fill_amount=configured_part,
            )

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> dict:
        params = dict(params or {})
        event_id = self._event_value(params, "event_id", "eventId")
        requested_order_id = self._event_value(
            params, "orderId", "order_id", "paperOrderId", "id"
        )
        client_order_id = self._event_value(
            params, "clientOid", "clientOrderId", "newClientOrderId"
        )
        request_fingerprint = self._paper_event_fingerprint(
            {
                "symbol": str(symbol),
                "type": str(type or "limit").lower(),
                "side": str(side or "").lower(),
                "amount": repr(amount),
                "price": repr(price),
                "params": params,
            }
        )
        if event_id:
            replayed = self._event_result(
                event_id,
                kind="order",
                fingerprint=request_fingerprint,
            )
            if replayed is not None:
                return _copy_order(replayed)
        existing = self._find_existing_order(requested_order_id, client_order_id)
        if existing is not None:
            if str(existing.get("symbol")) != str(symbol):
                raise ValueError(
                    f"paper order id already belongs to {existing.get('symbol')}, not {symbol}"
                )
            stored_fingerprint = (existing.get("info") or {}).get("request_fingerprint")
            if stored_fingerprint not in (None, request_fingerprint):
                raise ValueError(
                    f"paper order {existing['id']!r} has a conflicting request"
                )
            self._remember_event(
                event_id,
                kind="order",
                identifier=str(existing["id"]),
                fingerprint=request_fingerprint,
            )
            self._persist_paper_state()
            return _copy_order(existing)

        order_type = str(type or "limit").lower()
        order_side = str(side or "").lower()
        if order_side not in {"buy", "sell"}:
            raise ValueError(f"paper order side must be buy or sell, got {side!r}")
        quantity = abs(float(amount))
        if not math.isfinite(quantity) or quantity <= 0.0:
            raise ValueError("paper order amount must be finite and positive")
        if symbol not in self.markets:
            raise KeyError(f"Unknown paper symbol {symbol}")
        quote = await self._fetch_public_ticker(symbol)
        position_side = str(
            params.get("posSide")
            or params.get("positionSide")
            or params.get("position_side")
            or ("long" if order_side == "buy" else "short")
        ).lower()
        if position_side not in {"long", "short"}:
            raise ValueError(f"paper position side must be long or short, got {position_side!r}")
        reduce_only = bool(params.get("reduceOnly") or params.get("reduce_only"))
        order_id = requested_order_id or f"paper-{self._next_order_id}"
        if not requested_order_id:
            self._next_order_id += 1
        else:
            suffix = requested_order_id.rsplit("-", 1)[-1]
            if suffix.isdigit():
                self._next_order_id = max(self._next_order_id, int(suffix) + 1)
        if order_type == "market":
            order_price = float(quote["ask"] if order_side == "buy" else quote["bid"])
        else:
            if price is None:
                raise ValueError("paper limit order requires a price")
            order_price = float(price)
            if not math.isfinite(order_price) or order_price <= 0.0:
                raise ValueError("paper limit order price must be finite and positive")
        order = {
            "id": order_id,
            "symbol": symbol,
            "type": order_type,
            "side": order_side,
            "amount": quantity,
            "price": order_price,
            "timestamp": int(self.now_ms),
            "datetime": ts_to_date(int(self.now_ms)),
            "clientOrderId": client_order_id,
            "status": "open",
            "filled": 0.0,
            "remaining": quantity,
            "reduceOnly": reduce_only,
            "info": {
                "positionSide": position_side.upper(),
                "posSide": position_side,
                "reduceOnly": reduce_only,
                "marginMode": "isolated",
                "paper": True,
                "source": "local_paper_ledger",
                "strategy_id": self._paper_context_value(params.get("strategy_id")),
                "cycle_id": self._paper_context_value(
                    params.get("cycle_id", params.get("trade_cycle_id"))
                ),
                "config_hash": self._paper_context_value(params.get("config_hash")),
                "request_fingerprint": request_fingerprint,
                "reference_price": self._paper_finite(
                    params.get(
                        "reference_price",
                        params.get("referencePrice", quote["last"] if order_type == "market" else order_price),
                    ),
                    name="reference price",
                    positive=True,
                ),
            },
        }
        raw_paper_fill_amount = params.get(
            "paper_fill_amount",
            params.get("paperFillAmount"),
        )
        if raw_paper_fill_amount not in (None, ""):
            order["info"]["paper_fill_amount"] = self._paper_finite(
                raw_paper_fill_amount,
                name="paper fill amount",
                positive=True,
            )
        self._record_request(
            "create_order",
            symbol=symbol,
            order_type=order_type,
            side=order_side,
            amount=quantity,
            price=order_price,
            position_side=position_side,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
            order_id=order_id,
            params=params,
        )
        crossed = (
            order_type == "market"
            or (order_side == "buy" and order_price >= float(quote["ask"]))
            or (order_side == "sell" and order_price <= float(quote["bid"]))
        )
        post_only_rejected = crossed and order_type == "limit" and bool(params.get("postOnly"))
        if post_only_rejected:
            order["status"] = "canceled"
            order["info"]["postOnlyRejected"] = True

        order_event_id = event_id or f"paper-order-created:{order_id}"
        created_open = not post_only_rejected
        self._append_paper_wal_record(
            kind="order.created",
            payload={
                "timestamp": int(self.now_ms),
                "order_id": order_id,
                "order": copy.deepcopy(order),
                "open": created_open,
            },
            event_id=order_event_id,
            schema_version=PAPER_ORDER_JOURNAL_SCHEMA,
            request_fingerprint=request_fingerprint,
        )
        if self.paper_wal_path is not None:
            self._inject_paper_crash("after_wal")
        self._paper_orders[order_id] = order
        if created_open:
            self.open_orders[order_id] = order
        self._inject_paper_crash("after_ledger_mutation")
        if crossed:
            if post_only_rejected:
                self._remember_event(
                    order_event_id,
                    kind="order",
                    identifier=order_id,
                    fingerprint=request_fingerprint,
                )
                self._persist_paper_state()
                return _copy_order(order)
            fill_price = float(quote["ask"] if order_side == "buy" else quote["bid"])
            self._fill_order(
                order,
                fill_price=fill_price,
                liquidity="taker",
                reference_price=order["info"].get("reference_price"),
                remove_from_open_orders=True,
                fill_amount=order["info"].get("paper_fill_amount"),
            )
            self._remember_event(
                order_event_id,
                kind="order",
                identifier=order_id,
                fingerprint=request_fingerprint,
            )
            self._persist_paper_state()
            return _copy_order(order)
        self._remember_event(
            order_event_id,
            kind="order",
            identifier=order_id,
            fingerprint=request_fingerprint,
        )
        self._persist_paper_state()
        return _copy_order(order)

    def _assert_existing_fill_compatible(
        self,
        existing: dict,
        *,
        order_id: str,
        fill_price: object,
        liquidity: object,
        fill_amount: object,
        part_sequence: object,
        strategy_id: object,
        cycle_id: object,
        config_hash: object,
        typed_fee: object,
        typed_slippage: object,
    ) -> None:
        """Reject a reused fill ID whose semantic payload changed.

        A fill ID is an idempotency key, not permission to return whichever
        old fill happens to be in memory.  Event IDs may be retried, but the
        order, price, quantity, side-specific part and typed cost payload must
        remain identical.
        """

        info = existing.get("info") if isinstance(existing.get("info"), dict) else {}
        if str(existing.get("order") or "") != str(order_id):
            raise ValueError("paper fill_id conflicts with a different order")

        try:
            requested_price = float(fill_price)
            existing_price = float(existing.get("price"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("paper fill_id has an invalid execution price") from exc
        if not math.isclose(requested_price, existing_price, rel_tol=0.0, abs_tol=1e-8):
            raise ValueError("paper fill_id conflicts with a different execution price")

        if fill_amount not in (None, ""):
            try:
                requested_amount = float(fill_amount)
                existing_amount = float(existing.get("amount"))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("paper fill_id has an invalid amount") from exc
            if not math.isclose(requested_amount, existing_amount, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("paper fill_id conflicts with a different amount")

        existing_liquidity = str(
            info.get("liquidity") or existing.get("liquidity") or ""
        )
        if str(liquidity) != existing_liquidity:
            raise ValueError("paper fill_id conflicts with a different liquidity")

        if part_sequence not in (None, ""):
            try:
                requested_part = int(part_sequence)
                existing_part = int(info.get("part_sequence"))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("paper fill_id has an invalid part sequence") from exc
            if requested_part != existing_part:
                raise ValueError("paper fill_id conflicts with a different part sequence")

        for name, requested in (
            ("strategy_id", strategy_id),
            ("cycle_id", cycle_id),
            ("config_hash", config_hash),
        ):
            if requested not in (None, ""):
                if self._paper_context_value(requested) != info.get(name):
                    raise ValueError(f"paper fill_id conflicts with a different {name}")

        for name, requested in (("typed_fee", typed_fee), ("typed_slippage", typed_slippage)):
            if requested is not None:
                existing_value = info.get(name)
                if self._paper_event_fingerprint(requested) != self._paper_event_fingerprint(existing_value):
                    raise ValueError(f"paper fill_id conflicts with a different {name}")

    def _fill_order(
        self,
        order: Dict[str, Any],
        *,
        fill_price: float,
        liquidity: str,
        fill_id: str | None = None,
        event_id: str | None = None,
        reference_price: object = None,
        part_sequence: int | None = None,
        strategy_id: object = None,
        cycle_id: object = None,
        config_hash: object = None,
        typed_fee: object = None,
        typed_slippage: object = None,
        fill_timestamp: int | None = None,
        remove_from_open_orders: bool = False,
        fill_amount: object = None,
    ) -> dict | None:
        """Write a typed fill WAL entry, then apply one bounded fill part."""

        order_id = str(order.get("id") or "").strip()
        if not order_id:
            raise ValueError("paper fill requires an order id")
        if event_id:
            replayed = self._event_result(event_id, kind="fill")
            if replayed is not None:
                return replayed
        supplied_fill_id = fill_id not in (None, "")
        normalized_fill_id = str(fill_id).strip() if supplied_fill_id else ""
        if not normalized_fill_id:
            normalized_fill_id = str(self._next_trade_id)
        if not event_id:
            event_id = f"paper-fill:{order_id}:{normalized_fill_id}"

        existing_fill = self._existing_fill(normalized_fill_id)
        if existing_fill is not None:
            self._assert_existing_fill_compatible(
                existing_fill,
                order_id=order_id,
                fill_price=fill_price,
                liquidity=liquidity,
                fill_amount=fill_amount,
                part_sequence=part_sequence,
                strategy_id=strategy_id,
                cycle_id=cycle_id,
                config_hash=config_hash,
                typed_fee=typed_fee,
                typed_slippage=typed_slippage,
            )
            pending = self._paper_wal_records.get(str(event_id))
            if pending is not None and event_id not in self._processed_paper_events:
                self._remember_event(
                    event_id,
                    kind="fill",
                    identifier=normalized_fill_id,
                    fingerprint=str(pending["fingerprint"]),
                )
                self._persist_paper_state()
            elif event_id:
                self._remember_event(
                    event_id,
                    kind="fill",
                    identifier=normalized_fill_id,
                )
                self._persist_paper_state()
            return copy.deepcopy(existing_fill)

        previous_fills = [
            fill for fill in self.fills if str(fill.get("order") or "") == order_id
        ]
        expected_part_sequence = len(previous_fills) + 1
        if part_sequence is None:
            part_sequence = expected_part_sequence
        try:
            part_sequence = int(part_sequence)
        except (TypeError, ValueError) as exc:
            raise ValueError("paper fill part_sequence must be an integer") from exc
        if part_sequence <= 0:
            raise ValueError("paper fill part_sequence must be positive")
        if part_sequence != expected_part_sequence:
            raise ValueError(
                "paper fill part_sequence must be the next sequence for the order"
            )
        order_amount = self._paper_finite(
            order.get("amount"), name="order amount", positive=True
        )
        order_filled_before = self._paper_finite(
            order.get("filled", 0.0), name="order filled"
        )
        order_remaining = max(0.0, order_amount - order_filled_before)
        quantity = (
            order_remaining
            if fill_amount is None
            else self._paper_finite(fill_amount, name="amount", positive=True)
        )
        if quantity > order_remaining + 1e-12:
            raise ValueError("paper fill amount exceeds the order remainder")
        if quantity <= 0.0:
            raise ValueError("paper fill amount must be positive")
        execution_price = self._paper_finite(
            fill_price, name="executed price", positive=True
        )
        info = dict(order.get("info") or {})
        resolved_strategy_id = self._paper_context_value(
            strategy_id if strategy_id is not None else info.get("strategy_id")
        )
        resolved_cycle_id = self._paper_context_value(
            cycle_id if cycle_id is not None else info.get("cycle_id")
        )
        resolved_config_hash = self._paper_context_value(
            config_hash if config_hash is not None else info.get("config_hash")
        )
        if reference_price is None:
            reference_price = info.get("reference_price")
        if reference_price is None:
            reference_price = order.get("price", execution_price)
        fee = self._paper_typed_fee(
            order,
            fill_price=execution_price,
            quantity=quantity,
            liquidity=str(liquidity),
            supplied=typed_fee,
        )
        slippage = self._paper_typed_slippage(
            fill_price=execution_price,
            reference_price=reference_price,
            supplied=typed_slippage,
        )
        timestamp = int(self.now_ms if fill_timestamp is None else fill_timestamp)
        wal_payload = self._paper_fill_payload(
            order,
            fill_id=normalized_fill_id,
            event_id=str(event_id),
            fill_price=execution_price,
            liquidity=str(liquidity),
            timestamp=timestamp,
            part_sequence=part_sequence,
            fee=fee,
            slippage=slippage,
            strategy_id=resolved_strategy_id,
            cycle_id=resolved_cycle_id,
            config_hash=resolved_config_hash,
            fill_quantity=quantity,
        )
        wal_fingerprint = self._paper_event_fingerprint(wal_payload)
        if self._paper_replaying_wal:
            stored = self._paper_wal_records.get(str(event_id))
            if stored is None or str(stored.get("fingerprint")) != wal_fingerprint:
                raise ValueError(f"paper replay payload differs for event {event_id!r}")
        else:
            self._append_paper_wal(wal_payload)
            if self.paper_wal_path is not None:
                self._inject_paper_crash("after_wal")

        # Everything below this point is the ledger mutation.  A crash before
        # it leaves the WAL durable and the previous snapshot untouched.
        self.now_ms = timestamp
        canonical_order = self._paper_orders.get(order_id)
        if canonical_order is not None:
            order = canonical_order
        else:
            self._paper_orders[order_id] = order
        if remove_from_open_orders:
            self.open_orders.pop(order_id, None)
        order_info = dict(order.get("info") or {})
        order_info.update(
            {
                "strategy_id": resolved_strategy_id,
                "cycle_id": resolved_cycle_id,
                "config_hash": resolved_config_hash,
                "reference_price": float(slippage["reference_price"]),
            }
        )
        order["info"] = order_info

        # FakeCCXTClient's primitive assumes that its order object is exactly
        # the quantity being executed.  Use a bounded copy so the inherited
        # position/PnL/fee arithmetic remains shared while the canonical paper
        # order retains its total amount and outstanding remainder.
        fill_order = copy.deepcopy(order)
        fill_order["amount"] = float(quantity)
        fill_order["filled"] = 0.0
        fill_order["remaining"] = float(quantity)
        super()._fill_order(
            fill_order,
            fill_price=execution_price,
            liquidity=str(liquidity),
        )
        fill = self.fills[-1]
        fill["id"] = normalized_fill_id
        suffix = normalized_fill_id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            self._next_trade_id = max(self._next_trade_id, int(suffix) + 1)
        fill_info = dict(fill.get("info") or {})
        fill_info["typed_fee"] = copy.deepcopy(fee)
        fill_info["typed_slippage"] = copy.deepcopy(slippage)
        fill_info["event_id"] = str(event_id)
        fill_info["part_sequence"] = int(part_sequence)
        fill_info["strategy_id"] = resolved_strategy_id
        fill_info["cycle_id"] = resolved_cycle_id
        fill_info["config_hash"] = resolved_config_hash
        fill_info["quantity_before"] = float(wal_payload["quantity_before"])
        fill_info["quantity_after"] = float(wal_payload["quantity_after"])
        fill_info["order_quantity_before"] = float(
            wal_payload["order_quantity_before"]
        )
        fill_info["order_quantity_after"] = float(
            wal_payload["order_quantity_after"]
        )
        fill_info["reference_price"] = float(slippage["reference_price"])
        fill["info"] = fill_info
        order_filled_after = order_filled_before + quantity
        order["filled"] = float(order_filled_after)
        order["remaining"] = float(max(0.0, order_amount - order_filled_after))
        if order["remaining"] <= 1e-12:
            order["remaining"] = 0.0
            order["status"] = "closed"
            order["price"] = float(execution_price)
            self.open_orders.pop(order_id, None)
        else:
            order["status"] = "open"
            order["average"] = float(
                sum(
                    float(item.get("price", 0.0)) * float(item.get("amount", 0.0))
                    for item in previous_fills
                )
                + execution_price * quantity
            ) / order_filled_after
            order_info = dict(order.get("info") or {})
            order_info["partiallyFilled"] = True
            order_info["lastPartSequence"] = int(part_sequence)
            order["info"] = order_info
            self.open_orders[order_id] = order
        actual_fill_id = str(fill["id"])
        self._inject_paper_crash("after_ledger_mutation")
        self._remember_event(
            event_id,
            kind="fill",
            identifier=actual_fill_id,
            fingerprint=wal_fingerprint,
        )
        self._persist_paper_state()
        return copy.deepcopy(fill)

    def apply_fill_event(self, event: dict) -> dict:
        """Apply a local fill event exactly once during paper replay/recovery.

        This is intentionally a local ingestion boundary. It does not call an
        exchange API and does not model funding, depth or liquidation. A new
        event may fill only part of an order; the remainder stays open until a
        later event. Replaying the same event, order or fill ID returns the
        existing result unchanged.
        """

        if not isinstance(event, dict):
            raise TypeError("paper fill event must be an object")
        event_id = self._event_value(event, "event_id", "eventId", "id")
        fill_id = self._event_value(event, "fill_id", "fillId", "trade_id", "tradeId", "id")
        if not fill_id:
            raise ValueError("paper fill event requires fill_id or id")
        if not event_id:
            event_id = fill_id
        replayed = self._event_result(event_id, kind="fill")
        if replayed is not None:
            return replayed

        order_id = self._event_value(event, "order_id", "orderId", "order")
        if not order_id:
            order_id = f"paper-event-order-{fill_id}"
        symbol = str(event.get("symbol") or "")
        if symbol not in self.markets:
            raise KeyError(f"Unknown paper symbol {symbol}")
        side = str(event.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            raise ValueError("paper fill event side must be buy or sell")
        position_side = str(
            event.get("position_side")
            or event.get("positionSide")
            or event.get("posSide")
            or ("long" if side == "buy" else "short")
        ).lower()
        if position_side not in {"long", "short"}:
            raise ValueError("paper fill event position side must be long or short")
        raw_order_amount = event.get("order_amount", event.get("orderAmount"))
        try:
            quantity = abs(float(event.get("amount", event.get("qty"))))
            order_amount = (
                None
                if raw_order_amount in (None, "")
                else abs(float(raw_order_amount))
            )
            price = float(event["price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("paper fill event requires numeric amount and price") from exc
        if not math.isfinite(quantity) or quantity <= 0.0:
            raise ValueError("paper fill event amount must be finite and positive")
        if not math.isfinite(price) or price <= 0.0:
            raise ValueError("paper fill event price must be finite and positive")

        order = self._paper_orders.get(order_id)
        if order is not None:
            if order_amount is None:
                order_amount = float(order.get("amount", 0.0))
            if not math.isclose(
                float(order.get("amount", order_amount)),
                order_amount,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError(
                    "paper fill event order amount differs from the existing order"
                )
            remaining = max(
                0.0,
                float(order.get("amount", order_amount))
                - float(order.get("filled", 0.0)),
            )
            if quantity > remaining + 1e-12:
                raise ValueError("paper fill event amount exceeds the order remainder")
        else:
            if order_amount is None:
                order_amount = quantity
            if not math.isfinite(order_amount) or order_amount <= 0.0:
                raise ValueError("paper fill event order amount must be finite and positive")
            if quantity > order_amount + 1e-12:
                raise ValueError("paper fill event amount exceeds order amount")
            timestamp = int(event.get("timestamp") or self.now_ms)
            slippage = event.get("slippage")
            if isinstance(slippage, dict):
                reference_price = slippage.get("reference_price")
            else:
                reference_price = event.get(
                    "reference_price",
                    event.get("referencePrice", price),
                )
            order = {
                "id": order_id,
                "symbol": symbol,
                "type": str(event.get("type") or "market").lower(),
                "side": side,
                "amount": order_amount,
                "price": price,
                "timestamp": timestamp,
                "datetime": ts_to_date(timestamp),
                "clientOrderId": str(
                    event.get("client_order_id") or event.get("clientOrderId") or ""
                ),
                "status": "open",
                "filled": 0.0,
                "remaining": quantity,
                "reduceOnly": bool(event.get("reduce_only") or event.get("reduceOnly")),
                "info": {
                    "positionSide": position_side.upper(),
                    "posSide": position_side,
                    "reduceOnly": bool(event.get("reduce_only") or event.get("reduceOnly")),
                    "marginMode": "isolated",
                    "paper": True,
                    "source": "local_paper_ledger_event",
                    "strategy_id": self._paper_context_value(event.get("strategy_id")),
                    "cycle_id": self._paper_context_value(event.get("cycle_id")),
                    "config_hash": self._paper_context_value(event.get("config_hash")),
                    "reference_price": self._paper_finite(
                        reference_price,
                        name="reference price",
                        positive=True,
                    ),
                },
            }
            self._paper_orders[order_id] = order
        typed_slippage = event.get("slippage")
        if typed_slippage is None:
            typed_slippage = {
                "reference_price": event.get(
                    "reference_price",
                    event.get("referencePrice", order.get("info", {}).get("reference_price", price)),
                ),
                "executed_price": event.get("executed_price", price),
            }
        timestamp = int(event.get("timestamp") or self.now_ms)
        typed_fee = event.get("fee") if isinstance(event.get("fee"), dict) else None
        return self._fill_order(
            order,
            fill_price=price,
            liquidity=str(event.get("liquidity") or "taker"),
            fill_id=fill_id,
            event_id=event_id,
            reference_price=typed_slippage.get("reference_price")
            if isinstance(typed_slippage, dict)
            else None,
            part_sequence=event.get("part_sequence", event.get("partSequence")),
            fill_amount=quantity,
            strategy_id=event.get("strategy_id"),
            cycle_id=event.get("cycle_id"),
            config_hash=event.get("config_hash"),
            typed_fee=typed_fee,
            typed_slippage=typed_slippage,
            fill_timestamp=timestamp,
            remove_from_open_orders=True,
        )


    async def cancel_order(
        self,
        order_id: str,
        symbol: str = None,
        params: Optional[dict] = None,
    ) -> dict:
        params = dict(params or {})
        normalized_order_id = str(order_id)
        event_id = self._event_value(params, "event_id", "eventId") or (
            f"paper-order-canceled:{normalized_order_id}"
        )
        request_fingerprint = self._paper_event_fingerprint(
            {
                "order_id": normalized_order_id,
                "symbol": symbol,
                "params": params,
            }
        )
        replayed = self._event_result(
            event_id,
            kind="cancel",
            fingerprint=request_fingerprint,
        )
        if replayed is not None:
            return _copy_order(replayed)

        order = self.open_orders.get(normalized_order_id)
        if order is None:
            raise KeyError(f"Paper order {normalized_order_id} not found")
        if symbol is not None and order["symbol"] != symbol:
            raise KeyError(
                f"Paper order {normalized_order_id} belongs to {order['symbol']}, not {symbol}"
            )
        canceled = copy.deepcopy(order)
        canceled["status"] = "canceled"
        canceled["info"] = dict(canceled.get("info") or {})
        canceled["info"]["canceled"] = True
        self._record_request(
            "cancel_order",
            symbol=symbol,
            order_id=normalized_order_id,
            found=True,
            params=params,
            rows=1,
        )
        self._append_paper_wal_record(
            kind="order.canceled",
            payload={
                "timestamp": int(self.now_ms),
                "order_id": normalized_order_id,
                "order": canceled,
                "open": False,
            },
            event_id=event_id,
            schema_version=PAPER_CANCEL_JOURNAL_SCHEMA,
            request_fingerprint=request_fingerprint,
        )
        if self.paper_wal_path is not None:
            self._inject_paper_crash("after_wal")
        canonical = self._paper_orders.get(normalized_order_id, order)
        canonical.update(canceled)
        self.open_orders.pop(normalized_order_id, None)
        self._inject_paper_crash("after_ledger_mutation")
        self._remember_event(
            event_id,
            kind="cancel",
            identifier=normalized_order_id,
            fingerprint=request_fingerprint,
        )
        self._persist_paper_state()
        return _copy_order(canonical)

    async def fetch_positions(self) -> List[dict]:
        positions = await super().fetch_positions()
        for position in positions:
            symbol = str(position["symbol"])
            quote = await self._fetch_public_ticker(symbol)
            mark_index = extract_mark_index(quote, symbol=symbol, required=True)
            mark = float(mark_index["mark_price"])
            index = float(mark_index["index_price"])
            entry = float(position["entryPrice"])
            leverage = max(float(self.paper_leverage), 1.0)
            contracts = abs(float(position["contracts"]))
            market = self.markets[symbol]
            if "contractSize" not in market:
                raise PaperMarketSurfaceError(
                    f"paper market {symbol} is missing contractSize"
                )
            contract_size = float(market["contractSize"])
            if not math.isfinite(contract_size) or contract_size <= 0.0:
                raise PaperMarketSurfaceError(
                    f"paper market {symbol} contractSize must be finite and positive"
                )
            notional = contracts * mark * contract_size
            surface = self._surface_for_symbol(symbol)
            selected = maintenance_for_notional(
                surface["margin_tiers"],
                symbol=symbol,
                notional=notional,
            )
            maintenance = float(selected["maintenance_margin_rate"])
            if position["side"] == "long":
                liquidation = entry * (1.0 - 1.0 / leverage + maintenance)
            else:
                liquidation = entry * (1.0 + 1.0 / leverage - maintenance)
            position.update(
                {
                    "markPrice": mark,
                    "indexPrice": index,
                    "liquidationPrice": max(liquidation, 1e-12),
                    "leverage": leverage,
                    "marginMode": "isolated",
                    "info": {
                        **dict(position.get("info") or {}),
                        "positionSide": position["side"].upper(),
                        "posSide": position["side"],
                        "marginMode": "isolated",
                        "paperLiquidationModel": "entry-leverage-bound-maintenance-tier",
                        "paperMarkSource": mark_index["mark_source"],
                        "paperIndexSource": mark_index["index_source"],
                        "paperMaintenanceRate": maintenance,
                        "paperMaintenanceTier": selected["tier_id"],
                        "paperFillModel": paper_fill_model()["mode"],
                        "paperQualifying": False,
                    },
                }
            )
        if self.paper_execution_evidence_path is not None:
            chosen = str(positions[0]["symbol"]) if positions else None
            self.write_paper_execution_evidence(
                self.paper_execution_evidence_path, symbol=chosen
            )
        return positions

    async def close(self) -> None:
        self._persist_paper_state()
        close = getattr(self.public_client, "close", None)
        if callable(close):
            await close()


class PaperBitgetBot(BitgetBot):
    """Passivbot Bitget subclass selected only by an explicit paper account."""

    def create_ccxt_sessions(self):
        self.config["_paper_mode"] = True
        self.cca = PaperBitgetCCXTClient(self.config, self.user_info)
        self.ccp = None
        self.ws_enabled = False
        self.paper_mode = True
        logger.info(
            "bitget paper mode: using public market data and a local ledger; no private API is enabled"
        )

    async def update_exchange_config(self):
        logger.info(
            "bitget paper mode: skipping account mode, hedge, leverage and margin API writes"
        )
        self.is_uta = True
        self._account_mode_detected = True
        profile = get_optional_live_value(
            getattr(self, "config", {}), "portfolio_risk", {}
        )
        if isinstance(profile, dict) and bool(profile.get("enabled", False)):
            symbols = list(getattr(self, "active_symbols", []) or [])
            if symbols:
                await self.refresh_crypto_market_surfaces(symbols)

    async def update_exchange_config_by_symbols(self, symbols):
        profile = get_optional_live_value(
            getattr(self, "config", {}), "portfolio_risk", {}
        )
        if isinstance(profile, dict) and bool(profile.get("enabled", False)):
            await self.refresh_crypto_market_surfaces(symbols)
        return None

    def apply_paper_funding_settlement(
        self, settlement: Mapping[str, Any] | None = None, **settlement_fields: Any
    ) -> PaperFundingEvidence:
        """Apply explicit paper funding and forward its evidence to monitor.

        The method is intentionally synchronous and explicit: a caller must
        provide the settlement data, while the monitor only receives the
        evidence returned by the local ledger.  No public or private Bitget
        funding endpoint is called here.
        """

        if settlement is None:
            settlement = dict(settlement_fields)
        elif settlement_fields:
            raise TypeError(
                "paper funding settlement accepts either an object or keyword fields"
            )
        if not isinstance(settlement, Mapping):
            raise TypeError("paper funding settlement must be an object")
        payload = dict(settlement)
        for key in ("strategy_id", "cycle_id", "config_hash"):
            if payload.get(key) in (None, ""):
                value = getattr(
                    self,
                    "strategy_id" if key == "strategy_id" else (
                        "_monitor_cycle_id" if key == "cycle_id" else "config_hash"
                    ),
                    None,
                )
                if value not in (None, ""):
                    payload[key] = value
        evidence = self.cca.apply_funding_settlement(payload)
        recorder = getattr(self, "_monitor_record_funding_evidence", None)
        if callable(recorder):
            recorder(evidence)
        return evidence

    def apply_recorded_public_execution_inputs(
        self,
        *,
        timestamp: int | None = None,
    ) -> list[PaperFundingEvidence]:
        """Forward newly available saved-public funding to the monitor.

        The underlying client performs all source validation and local ledger
        updates.  This bot method only forwards the resulting typed evidence;
        it never downloads the feed and never calls a private exchange API.
        """

        evidence = self.cca.apply_recorded_public_execution_inputs(
            timestamp=timestamp
        )
        recorder = getattr(self, "_monitor_record_funding_evidence", None)
        if callable(recorder):
            for item in evidence:
                recorder(item)
        return evidence

    def record_paper_reconciliation(
        self,
        *,
        timestamp: int | None = None,
        source: str = "paper-local-ledger-v1",
        source_hash: str | None = None,
    ) -> list[dict]:
        """Record explicit paper-ledger and cost reconciliation evidence.

        Reconciliation is caller-driven so a runtime cannot silently claim
        that an exchange state or complete cost history exists.  The local
        ledger builds both evidence rows, and the monitor only persists rows
        that carry their source and source hash.
        """

        evidence = self.cca.build_paper_reconciliation_evidence(
            timestamp=timestamp,
            source=source,
            source_hash=source_hash,
            strategy_id=getattr(self, "strategy_id", None),
            cycle_id=getattr(self, "_monitor_cycle_id", None),
            config_hash=getattr(self, "config_hash", None),
        )
        recorder = getattr(self, "_monitor_record_reconciliation_evidence", None)
        if callable(recorder):
            for item in evidence:
                recorder(item)
        return evidence

    async def _do_fetch_balance(self) -> dict:
        return await self.cca.fetch_balance()

    def _get_balance(self, fetched: dict) -> float:
        total = fetched.get("total") if isinstance(fetched, dict) else None
        if not isinstance(total, dict) or self.quote not in total:
            raise ValueError("paper balance response is missing the quote currency")
        return float(total[self.quote])

    async def fetch_pnls(self, start_time=None, end_time=None, limit=None) -> list:
        params = {"until": int(end_time)} if end_time else {}
        trades = await self.cca.fetch_my_trades(
            symbol=None,
            since=int(start_time) if start_time else None,
            limit=limit,
            params=params,
        )
        result = []
        for trade in trades:
            row = copy.deepcopy(trade)
            row["qty"] = row["amount"]
            row["pnl"] = float(row.get("pnl") or row.get("info", {}).get("realizedPnl") or 0.0)
            row["position_side"] = str(
                row.get("position_side")
                or row.get("info", {}).get("positionSide")
                or "long"
            ).lower()
            result.append(row)
        return sorted(result, key=lambda item: (item["timestamp"], item["id"]))
