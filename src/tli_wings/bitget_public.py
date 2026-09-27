"""Pure, offline Bitget public market-surface adapters.

The functions in this module accept already-fetched, JSON-compatible Bitget
payloads.  They do not create exchange clients, make network requests, read
credentials, or invoke private/write operations.  A caller owns the public
HTTP transport and passes its response here for strict validation.

No normalizer returns a fabricated value for a missing or unusable surface.
The result is either a validated dataclass or an explicit ``unavailable`` or
``error`` outcome.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Generic, TypeVar


__all__ = [
    "BitgetFunding",
    "BitgetInstrument",
    "BitgetMaintenanceTier",
    "BitgetMaintenanceTiers",
    "BitgetMarkIndex",
    "BitgetPublicError",
    "BitgetPublicResult",
    "BitgetPublicStatus",
    "BitgetPublicUnavailable",
    "BitgetUTAIsolatedOrderParams",
    "FreshnessPolicy",
    "build_uta_isolated_order_params",
    "canonical_source_json",
    "normalize_funding_payload",
    "normalize_instrument_payload",
    "normalize_maintenance_tiers_payload",
    "normalize_mark_index_payload",
    "source_fingerprint",
]


_SUCCESS_CODES = frozenset({"0", "00000"})
_SYMBOL_FIELDS = ("symbol", "instId")
_CONTRACT_FIELDS = ("productType", "category", "contract", "instType")
_OBSERVATION_FIELDS = ("ts", "timestamp", "updatedTime", "updateTime", "utime")
_FINGERPRINT_LENGTH = 64

T = TypeVar("T")


class BitgetPublicStatus(str, Enum):
    """The only terminal states returned by a public-surface adapter."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


def _is_absent(raw: object) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _normalise_identity(value: object, field: str) -> str:
    return _require_text(value, field).upper()


def _validate_finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{field} must be a finite number")
    return converted


def _validate_positive(value: object, field: str) -> float:
    converted = _validate_finite(value, field)
    if converted <= 0.0:
        raise ValueError(f"{field} must be positive")
    return converted


def _validate_nonnegative(value: object, field: str) -> float:
    converted = _validate_finite(value, field)
    if converted < 0.0:
        raise ValueError(f"{field} must be non-negative")
    return converted


def _validate_integer(value: object, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return value


def _validate_timestamp(value: object, field: str = "observed_at_ms") -> int:
    return _validate_integer(value, field, minimum=1)


def _validate_optional_timestamp(value: object | None, field: str) -> None:
    if value is not None:
        _validate_timestamp(value, field)


def _validate_fingerprint(value: object, field: str = "source_fingerprint") -> str:
    if not isinstance(value, str) or len(value) != _FINGERPRINT_LENGTH:
        raise ValueError(f"{field} must be a SHA-256 hexadecimal digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a SHA-256 hexadecimal digest")
    return value


def _validate_json_value(
    value: object, path: str = "$", *, seen: set[int] | None = None
) -> None:
    """Reject non-JSON values before hashing instead of silently coercing them."""

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON number at {path}")
        return
    if seen is None:
        seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise ValueError(f"cyclic JSON value at {path}")
        seen.add(identity)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"JSON object key at {path} must be a string")
                _validate_json_value(child, f"{path}.{key}", seen=seen)
        finally:
            seen.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise ValueError(f"cyclic JSON value at {path}")
        seen.add(identity)
        try:
            for index, child in enumerate(value):
                _validate_json_value(child, f"{path}[{index}]", seen=seen)
        finally:
            seen.remove(identity)
        return
    raise TypeError(f"value at {path} is not JSON-compatible")


def canonical_source_json(value: object) -> str:
    """Return the deterministic JSON representation used for source hashes."""

    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def source_fingerprint(value: object) -> str:
    """Hash the original payload, not the normalized dataclass."""

    encoded = canonical_source_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class BitgetPublicUnavailable:
    """A public surface is absent, incomplete, or too old to use."""

    surface: str
    reason: str
    message: str
    missing_fields: tuple[str, ...] = ()
    symbol: str | None = None
    contract: str | None = None
    observed_at_ms: int | None = None
    age_ms: int | None = None
    source_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.surface, "surface")
        _require_text(self.reason, "reason")
        _require_text(self.message, "message")
        for field in self.missing_fields:
            _require_text(field, "missing_fields entry")
        if self.symbol is not None:
            _require_text(self.symbol, "symbol")
        if self.contract is not None:
            _require_text(self.contract, "contract")
        _validate_optional_timestamp(self.observed_at_ms, "observed_at_ms")
        if self.age_ms is not None:
            _validate_integer(self.age_ms, "age_ms", minimum=0)
        if self.source_fingerprint is not None:
            _validate_fingerprint(self.source_fingerprint)


@dataclass(frozen=True, slots=True)
class BitgetPublicError:
    """A present public payload is malformed, contradictory, or unsafe."""

    surface: str
    reason: str
    message: str
    field: str | None = None
    symbol: str | None = None
    contract: str | None = None
    source_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.surface, "surface")
        _require_text(self.reason, "reason")
        _require_text(self.message, "message")
        if self.field is not None:
            _require_text(self.field, "field")
        if self.symbol is not None:
            _require_text(self.symbol, "symbol")
        if self.contract is not None:
            _require_text(self.contract, "contract")
        if self.source_fingerprint is not None:
            _validate_fingerprint(self.source_fingerprint)


@dataclass(frozen=True, slots=True)
class BitgetPublicResult(Generic[T]):
    """Exactly one validated value, unavailable issue, or error issue."""

    value: T | None = None
    unavailable: BitgetPublicUnavailable | None = None
    error: BitgetPublicError | None = None

    def __post_init__(self) -> None:
        states = sum(
            candidate is not None
            for candidate in (self.value, self.unavailable, self.error)
        )
        if states != 1:
            raise ValueError("a public result must contain exactly one terminal state")

    @classmethod
    def success(cls, value: T) -> "BitgetPublicResult[T]":
        if value is None:
            raise ValueError("a successful public result cannot contain None")
        return cls(value=value)

    @classmethod
    def unavailable_result(
        cls, issue: BitgetPublicUnavailable
    ) -> "BitgetPublicResult[T]":
        return cls(unavailable=issue)

    @classmethod
    def error_result(cls, issue: BitgetPublicError) -> "BitgetPublicResult[T]":
        return cls(error=issue)

    @property
    def status(self) -> BitgetPublicStatus:
        if self.value is not None:
            return BitgetPublicStatus.AVAILABLE
        if self.unavailable is not None:
            return BitgetPublicStatus.UNAVAILABLE
        return BitgetPublicStatus.ERROR

    @property
    def kind(self) -> str:
        return self.status.value

    @property
    def ok(self) -> bool:
        return self.value is not None

    @property
    def is_available(self) -> bool:
        return self.ok

    @property
    def source_fingerprint(self) -> str | None:
        if self.value is not None:
            return getattr(self.value, "source_fingerprint", None)
        if self.unavailable is not None:
            return self.unavailable.source_fingerprint
        if self.error is not None:
            return self.error.source_fingerprint
        return None

    def require(self) -> T:
        if self.value is None:
            raise ValueError(f"Bitget public surface is {self.status.value}")
        return self.value


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Explicit, deterministic freshness context supplied by the caller."""

    now_ms: int
    max_age_ms: int

    def __post_init__(self) -> None:
        _validate_timestamp(self.now_ms, "now_ms")
        _validate_integer(self.max_age_ms, "max_age_ms", minimum=0)


@dataclass(frozen=True, slots=True)
class BitgetInstrument:
    symbol: str
    contract: str
    base_coin: str
    quote_coin: str
    status: str
    min_order_qty: float
    qty_step: float
    price_step: float
    price_precision: int
    max_leverage: float
    observed_at_ms: int
    source_fingerprint: str
    settle_coin: str | None = None

    def __post_init__(self) -> None:
        _normalise_identity(self.symbol, "symbol")
        _normalise_identity(self.contract, "contract")
        _require_text(self.base_coin, "base_coin")
        _require_text(self.quote_coin, "quote_coin")
        _require_text(self.status, "status")
        if self.settle_coin is not None:
            _require_text(self.settle_coin, "settle_coin")
        _validate_positive(self.min_order_qty, "min_order_qty")
        _validate_positive(self.qty_step, "qty_step")
        _validate_positive(self.price_step, "price_step")
        _validate_integer(self.price_precision, "price_precision", minimum=0)
        _validate_positive(self.max_leverage, "max_leverage")
        _validate_timestamp(self.observed_at_ms)
        _validate_fingerprint(self.source_fingerprint)

    @property
    def product_type(self) -> str:
        return self.contract

    @property
    def min_trade_num(self) -> float:
        return self.min_order_qty

    @property
    def size_multiplier(self) -> float:
        return self.qty_step


@dataclass(frozen=True, slots=True)
class BitgetMarkIndex:
    symbol: str
    contract: str
    mark_price: float
    index_price: float
    observed_at_ms: int
    source_fingerprint: str

    def __post_init__(self) -> None:
        _normalise_identity(self.symbol, "symbol")
        _normalise_identity(self.contract, "contract")
        _validate_positive(self.mark_price, "mark_price")
        _validate_positive(self.index_price, "index_price")
        _validate_timestamp(self.observed_at_ms)
        _validate_fingerprint(self.source_fingerprint)


@dataclass(frozen=True, slots=True)
class BitgetFunding:
    symbol: str
    contract: str
    funding_rate: float
    next_funding_time_ms: int | None
    observed_at_ms: int
    source_fingerprint: str
    funding_time_ms: int | None = None
    funding_interval_hours: int | None = None

    def __post_init__(self) -> None:
        _normalise_identity(self.symbol, "symbol")
        _normalise_identity(self.contract, "contract")
        _validate_finite(self.funding_rate, "funding_rate")
        _validate_optional_timestamp(self.next_funding_time_ms, "next_funding_time_ms")
        _validate_timestamp(self.observed_at_ms)
        _validate_optional_timestamp(self.funding_time_ms, "funding_time_ms")
        if self.funding_time_ms is None and self.next_funding_time_ms is None:
            raise ValueError(
                "funding data requires funding_time_ms or next_funding_time_ms"
            )
        if (
            self.funding_time_ms is not None
            and self.next_funding_time_ms is not None
            and self.next_funding_time_ms < self.funding_time_ms
        ):
            raise ValueError("next_funding_time_ms cannot precede funding_time_ms")
        if self.funding_interval_hours is not None:
            _validate_integer(
                self.funding_interval_hours, "funding_interval_hours", minimum=1
            )
        _validate_fingerprint(self.source_fingerprint)


@dataclass(frozen=True, slots=True)
class BitgetMaintenanceTier:
    symbol: str
    contract: str
    level: int
    lower_bound: float
    upper_bound: float
    max_leverage: float
    maintenance_margin_rate: float

    def __post_init__(self) -> None:
        _normalise_identity(self.symbol, "symbol")
        _normalise_identity(self.contract, "contract")
        _validate_integer(self.level, "level", minimum=1)
        _validate_nonnegative(self.lower_bound, "lower_bound")
        _validate_positive(self.upper_bound, "upper_bound")
        if self.upper_bound <= self.lower_bound:
            raise ValueError("upper_bound must exceed lower_bound")
        _validate_positive(self.max_leverage, "max_leverage")
        _validate_nonnegative(self.maintenance_margin_rate, "maintenance_margin_rate")


@dataclass(frozen=True, slots=True)
class BitgetMaintenanceTiers:
    symbol: str
    contract: str
    tiers: tuple[BitgetMaintenanceTier, ...]
    observed_at_ms: int
    source_fingerprint: str

    def __post_init__(self) -> None:
        symbol = _normalise_identity(self.symbol, "symbol")
        contract = _normalise_identity(self.contract, "contract")
        if not isinstance(self.tiers, tuple) or not self.tiers:
            raise ValueError("tiers must be a non-empty tuple")
        expected_levels = tuple(range(1, len(self.tiers) + 1))
        actual_levels = tuple(tier.level for tier in self.tiers)
        if actual_levels != expected_levels:
            raise ValueError("maintenance tier levels must be consecutive from one")
        previous: BitgetMaintenanceTier | None = None
        for tier in self.tiers:
            if not isinstance(tier, BitgetMaintenanceTier):
                raise ValueError("tiers must contain BitgetMaintenanceTier values")
            if tier.symbol != symbol or tier.contract != contract:
                raise ValueError("maintenance tiers must share symbol and contract")
            if previous is not None:
                if tier.lower_bound != previous.upper_bound:
                    raise ValueError("maintenance tier bounds must be contiguous")
                if tier.max_leverage > previous.max_leverage:
                    raise ValueError("max leverage cannot increase at a higher tier")
                if tier.maintenance_margin_rate < previous.maintenance_margin_rate:
                    raise ValueError(
                        "maintenance margin rate cannot decrease at a higher tier"
                    )
            previous = tier
        if self.tiers[0].lower_bound != 0.0:
            raise ValueError("maintenance tiers must start at zero")
        _validate_timestamp(self.observed_at_ms)
        _validate_fingerprint(self.source_fingerprint)

    @property
    def levels(self) -> tuple[int, ...]:
        return tuple(tier.level for tier in self.tiers)


@dataclass(frozen=True, slots=True)
class BitgetUTAIsolatedOrderParams:
    """Pure UTA hedge-mode order parameters for one position side."""

    symbol: str
    hold_side: str
    leverage: int
    long_leverage: int
    short_leverage: int
    margin_mode: str = "isolated"
    contract: str | None = None

    def __post_init__(self) -> None:
        _normalise_identity(self.symbol, "symbol")
        hold_side = _require_text(self.hold_side, "hold_side").lower()
        if hold_side not in {"long", "short"}:
            raise ValueError("hold_side must be long or short")
        _validate_integer(self.leverage, "leverage", minimum=1)
        _validate_integer(self.long_leverage, "long_leverage", minimum=1)
        _validate_integer(self.short_leverage, "short_leverage", minimum=1)
        if self.leverage != (
            self.long_leverage if hold_side == "long" else self.short_leverage
        ):
            raise ValueError("leverage must match the selected position side")
        if self.margin_mode != "isolated":
            raise ValueError("margin_mode must be isolated")
        if self.contract is not None:
            _normalise_identity(self.contract, "contract")

    def as_params(self) -> dict[str, object]:
        """Return only UTA order parameters; no client or network call occurs."""

        return {
            "marginMode": "isolated",
            "holdSide": self.hold_side,
            "leverage": self.leverage,
        }

    def to_payload(self) -> dict[str, object]:
        return self.as_params()


class _UnavailableProblem(Exception):
    def __init__(
        self,
        reason: str,
        message: str,
        *,
        missing_fields: tuple[str, ...] = (),
        symbol: str | None = None,
        contract: str | None = None,
        observed_at_ms: int | None = None,
        age_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.missing_fields = missing_fields
        self.symbol = symbol
        self.contract = contract
        self.observed_at_ms = observed_at_ms
        self.age_ms = age_ms


class _InvalidProblem(Exception):
    def __init__(
        self,
        reason: str,
        message: str,
        *,
        field: str | None = None,
        symbol: str | None = None,
        contract: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.field = field
        self.symbol = symbol
        self.contract = contract


@dataclass(frozen=True, slots=True)
class _Envelope:
    data: object
    request_time_ms: int | None


def _invalid(
    reason: str,
    message: str,
    *,
    field: str | None = None,
    symbol: str | None = None,
    contract: str | None = None,
) -> _InvalidProblem:
    return _InvalidProblem(
        reason,
        message,
        field=field,
        symbol=symbol,
        contract=contract,
    )


def _unavailable(
    reason: str,
    message: str,
    *,
    missing_fields: tuple[str, ...] = (),
    symbol: str | None = None,
    contract: str | None = None,
    observed_at_ms: int | None = None,
    age_ms: int | None = None,
) -> _UnavailableProblem:
    return _UnavailableProblem(
        reason,
        message,
        missing_fields=missing_fields,
        symbol=symbol,
        contract=contract,
        observed_at_ms=observed_at_ms,
        age_ms=age_ms,
    )


def _parse_number(raw: object, field: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise _invalid("invalid_numeric", f"{field} is not numeric", field=field)
    if isinstance(raw, str) and not raw.strip():
        raise _unavailable(
            "missing_field", f"{field} is empty", missing_fields=(field,)
        )
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _invalid(
            "invalid_numeric", f"{field} is not numeric", field=field
        ) from exc
    if not math.isfinite(value):
        raise _invalid("non_finite_numeric", f"{field} must be finite", field=field)
    return value


def _parse_integer(raw: object, field: str) -> int:
    value = _parse_number(raw, field)
    if not value.is_integer():
        raise _invalid("invalid_integer", f"{field} must be an integer", field=field)
    return int(value)


def _read_text(
    row: Mapping[str, object],
    aliases: tuple[str, ...],
    field: str,
    *,
    normalise: Callable[[str], str] = lambda value: value,
    required: bool = True,
) -> str | None:
    values: list[str] = []
    for alias in aliases:
        if alias not in row or _is_absent(row[alias]):
            continue
        raw = row[alias]
        if not isinstance(raw, str):
            raise _invalid("invalid_text", f"{field} is not text", field=field)
        value = raw.strip()
        if value:
            values.append(normalise(value))
    if not values:
        if required:
            raise _unavailable(
                "missing_field", f"{field} is missing", missing_fields=(field,)
            )
        return None
    if any(value != values[0] for value in values[1:]):
        raise _invalid("contradictory_field", f"{field} aliases disagree", field=field)
    return values[0]


def _read_number(
    row: Mapping[str, object],
    aliases: tuple[str, ...],
    field: str,
    *,
    required: bool = True,
) -> float | None:
    values: list[float] = []
    for alias in aliases:
        if alias not in row or _is_absent(row[alias]):
            continue
        values.append(_parse_number(row[alias], field))
    if not values:
        if required:
            raise _unavailable(
                "missing_field", f"{field} is missing", missing_fields=(field,)
            )
        return None
    if any(value != values[0] for value in values[1:]):
        raise _invalid("contradictory_field", f"{field} aliases disagree", field=field)
    return values[0]


def _read_integer(
    row: Mapping[str, object],
    aliases: tuple[str, ...],
    field: str,
    *,
    required: bool = True,
) -> int | None:
    values: list[int] = []
    for alias in aliases:
        if alias not in row or _is_absent(row[alias]):
            continue
        values.append(_parse_integer(row[alias], field))
    if not values:
        if required:
            raise _unavailable(
                "missing_field", f"{field} is missing", missing_fields=(field,)
            )
        return None
    if any(value != values[0] for value in values[1:]):
        raise _invalid("contradictory_field", f"{field} aliases disagree", field=field)
    return values[0]


def _read_timestamp(
    row: Mapping[str, object], aliases: tuple[str, ...], field: str
) -> int | None:
    values: list[int] = []
    for alias in aliases:
        if alias not in row or _is_absent(row[alias]):
            continue
        values.append(_parse_integer(row[alias], field))
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        raise _invalid(
            "contradictory_timestamp", f"{field} timestamps disagree", field=field
        )
    try:
        return _validate_timestamp(values[0], field)
    except ValueError as exc:
        raise _invalid(
            "invalid_timestamp", f"{field} must be positive", field=field
        ) from exc


def _read_envelope(payload: object, surface: str) -> _Envelope:
    if payload is None:
        raise _unavailable("payload_unavailable", f"{surface} payload is absent")
    if not isinstance(payload, Mapping):
        raise _invalid("payload_not_object", f"{surface} payload must be an object")
    if "code" not in payload or _is_absent(payload["code"]):
        raise _unavailable(
            "missing_envelope_field",
            f"{surface} response code is missing",
            missing_fields=("code",),
        )
    raw_code = payload["code"]
    if isinstance(raw_code, bool) or not isinstance(raw_code, (str, int)):
        raise _invalid(
            "invalid_envelope", "response code has an invalid type", field="code"
        )
    code = str(raw_code).strip()
    if code not in _SUCCESS_CODES:
        raise _invalid(
            "upstream_error",
            "Bitget returned a non-success response code",
            field="code",
        )
    if "data" not in payload or payload["data"] is None:
        raise _unavailable(
            "missing_envelope_field",
            f"{surface} response data is missing",
            missing_fields=("data",),
        )
    request_time_ms = _read_timestamp(payload, ("requestTime",), "request_time_ms")
    return _Envelope(data=payload["data"], request_time_ms=request_time_ms)


def _rows_from_data(
    data: object, surface: str, *, nested_keys: tuple[str, ...] = ()
) -> tuple[Mapping[str, object], ...]:
    rows_data = data
    if isinstance(data, Mapping):
        present_nested = [key for key in nested_keys if key in data]
        if len(present_nested) > 1:
            raise _invalid(
                "contradictory_data", f"{surface} contains multiple data lists"
            )
        if present_nested:
            rows_data = data[present_nested[0]]
    if not isinstance(rows_data, (list, tuple)):
        if isinstance(rows_data, Mapping) and not nested_keys:
            rows = (rows_data,)
        elif isinstance(rows_data, Mapping):
            rows = (rows_data,)
        else:
            raise _invalid(
                "invalid_data_shape", f"{surface} data must contain object rows"
            )
    else:
        if not rows_data:
            raise _unavailable("empty_surface", f"{surface} data is empty")
        rows = tuple(rows_data)
    if not rows:
        raise _unavailable("empty_surface", f"{surface} data is empty")
    for row in rows:
        if not isinstance(row, Mapping):
            raise _invalid("invalid_row", f"{surface} data contains a non-object row")
    return rows


def _select_one_row(
    rows: tuple[Mapping[str, object], ...],
    surface: str,
    expected_symbol: str | None,
) -> Mapping[str, object]:
    if len(rows) == 1:
        return rows[0]
    if expected_symbol is None:
        raise _invalid(
            "ambiguous_rows",
            f"{surface} returned multiple rows without a symbol selector",
        )
    matches: list[Mapping[str, object]] = []
    for row in rows:
        row_symbol = _read_text(
            row,
            _SYMBOL_FIELDS,
            "symbol",
            normalise=lambda value: value.upper(),
            required=False,
        )
        if row_symbol == expected_symbol:
            matches.append(row)
    if not matches:
        raise _unavailable(
            "symbol_not_found",
            f"{surface} did not return the requested symbol",
            symbol=expected_symbol,
        )
    if len(matches) > 1:
        raise _invalid(
            "duplicate_symbol",
            f"{surface} returned duplicate symbol rows",
            symbol=expected_symbol,
        )
    return matches[0]


def _normalise_expected(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        return _normalise_identity(value, field)
    except ValueError as exc:
        raise _invalid(
            "invalid_expected_identity", f"{field} is invalid", field=field
        ) from exc


def _resolve_identity(
    row: Mapping[str, object],
    aliases: tuple[str, ...],
    field: str,
    expected: str | None,
    *,
    required_source: bool,
) -> str:
    source_value = _read_text(
        row,
        aliases,
        field,
        normalise=lambda value: value.upper(),
        required=required_source,
    )
    if source_value is None:
        if expected is None:
            raise _unavailable(
                "missing_identity", f"{field} is missing", missing_fields=(field,)
            )
        return expected
    if expected is not None and source_value != expected:
        raise _invalid(
            "contradictory_identity", f"{field} disagrees with the request", field=field
        )
    return source_value


def _observed_at_ms(
    envelope: _Envelope, row: Mapping[str, object], surface: str
) -> int:
    row_timestamp = _read_timestamp(row, _OBSERVATION_FIELDS, "observed_at_ms")
    if row_timestamp is not None:
        return row_timestamp
    if envelope.request_time_ms is not None:
        return envelope.request_time_ms
    raise _unavailable(
        "missing_observation_time",
        f"{surface} has no usable observation timestamp",
        missing_fields=("observed_at_ms",),
    )


def _check_freshness(
    freshness: FreshnessPolicy | None,
    observed_at_ms: int,
    *,
    symbol: str | None = None,
    contract: str | None = None,
) -> None:
    if freshness is None:
        return
    if not isinstance(freshness, FreshnessPolicy):
        raise _invalid(
            "invalid_freshness_policy", "freshness must be a FreshnessPolicy"
        )
    if observed_at_ms > freshness.now_ms:
        raise _invalid(
            "future_observation",
            "observation timestamp is later than the freshness clock",
            field="observed_at_ms",
            symbol=symbol,
            contract=contract,
        )
    age_ms = freshness.now_ms - observed_at_ms
    if age_ms > freshness.max_age_ms:
        raise _unavailable(
            "stale",
            "public surface is older than the allowed freshness window",
            symbol=symbol,
            contract=contract,
            observed_at_ms=observed_at_ms,
            age_ms=age_ms,
        )


def _price_metadata(row: Mapping[str, object]) -> tuple[int, float]:
    price_precision = _read_integer(
        row,
        ("pricePrecision", "pricePlace", "price_precision"),
        "price_precision",
    )
    assert price_precision is not None
    direct_step = _read_number(
        row,
        ("priceMultiplier", "priceStep", "price_step"),
        "price_step",
        required=False,
    )
    end_step = _read_integer(
        row,
        ("priceEndStep", "price_end_step"),
        "price_end_step",
        required=False,
    )
    if direct_step is None and end_step is None:
        raise _unavailable(
            "missing_field",
            "price step metadata is missing",
            missing_fields=("price_step",),
        )
    if end_step is not None:
        if end_step <= 0:
            raise _invalid(
                "invalid_numeric",
                "price_end_step must be positive",
                field="price_end_step",
            )
        derived_step = float(end_step) * (10.0 ** (-price_precision))
        if not math.isfinite(derived_step) or derived_step <= 0.0:
            raise _invalid(
                "invalid_numeric", "derived price step is invalid", field="price_step"
            )
        if direct_step is not None and not math.isclose(
            direct_step, derived_step, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise _invalid(
                "contradictory_field", "price step aliases disagree", field="price_step"
            )
        direct_step = derived_step
    assert direct_step is not None
    if direct_step <= 0.0:
        raise _invalid(
            "invalid_numeric", "price_step must be positive", field="price_step"
        )
    return price_precision, direct_step


def _normalise_instrument(
    payload: object,
    fingerprint: str,
    *,
    expected_symbol: str | None,
    expected_contract: str | None,
    expected_settle_coin: str | None,
    freshness: FreshnessPolicy | None,
) -> BitgetInstrument:
    envelope = _read_envelope(payload, "instrument")
    rows = _rows_from_data(envelope.data, "instrument")
    row = _select_one_row(rows, "instrument", expected_symbol)
    symbol = _resolve_identity(
        row, _SYMBOL_FIELDS, "symbol", expected_symbol, required_source=True
    )
    contract = _resolve_identity(
        row,
        _CONTRACT_FIELDS,
        "contract",
        expected_contract,
        required_source=False,
    )
    settle_coin = _read_text(
        row,
        ("settleCoin", "settleCurrency", "settle_coin"),
        "settle_coin",
        normalise=lambda value: value.upper(),
        required=False,
    )
    if settle_coin is None and expected_settle_coin is not None:
        settle_coin = expected_settle_coin
    if (
        settle_coin is not None
        and expected_settle_coin is not None
        and settle_coin != expected_settle_coin
    ):
        raise _invalid(
            "contradictory_identity",
            "settle_coin disagrees with the request",
            field="settle_coin",
        )
    observed_at_ms = _observed_at_ms(envelope, row, "instrument")
    _check_freshness(
        freshness,
        observed_at_ms,
        symbol=symbol,
        contract=contract,
    )
    price_precision, price_step = _price_metadata(row)
    min_order_qty = _read_number(
        row,
        ("minOrderQty", "minTradeNum", "minTradeAmount", "min_order_qty"),
        "min_order_qty",
    )
    qty_step = _read_number(
        row,
        ("quantityMultiplier", "sizeMultiplier", "qtyStep", "stepSize", "qty_step"),
        "qty_step",
    )
    max_leverage = _read_number(
        row,
        ("maxLeverage", "maxLever", "max_leverage"),
        "max_leverage",
    )
    base_coin = _read_text(
        row,
        ("baseCoin", "base", "base_coin"),
        "base_coin",
        normalise=lambda value: value.upper(),
    )
    quote_coin = _read_text(
        row,
        ("quoteCoin", "quote", "quote_coin"),
        "quote_coin",
        normalise=lambda value: value.upper(),
    )
    status = _read_text(
        row,
        ("status", "symbolStatus"),
        "status",
        normalise=lambda value: value.lower(),
    )
    assert min_order_qty is not None
    assert qty_step is not None
    assert max_leverage is not None
    assert base_coin is not None
    assert quote_coin is not None
    assert status is not None
    return BitgetInstrument(
        symbol=symbol,
        contract=contract,
        base_coin=base_coin,
        quote_coin=quote_coin,
        settle_coin=settle_coin,
        status=status,
        min_order_qty=min_order_qty,
        qty_step=qty_step,
        price_step=price_step,
        price_precision=price_precision,
        max_leverage=max_leverage,
        observed_at_ms=observed_at_ms,
        source_fingerprint=fingerprint,
    )


def normalize_instrument_payload(
    payload: object,
    *,
    expected_symbol: str | None = None,
    expected_contract: str | None = None,
    expected_settle_coin: str | None = None,
    freshness: FreshnessPolicy | None = None,
) -> BitgetPublicResult[BitgetInstrument]:
    """Normalize Bitget v2/v3 instrument metadata from an existing response."""

    return _run_payload_normalizer(
        "instrument",
        payload,
        lambda fingerprint: _normalise_instrument(
            payload,
            fingerprint,
            expected_symbol=_normalise_expected(expected_symbol, "expected_symbol"),
            expected_contract=_normalise_expected(
                expected_contract, "expected_contract"
            ),
            expected_settle_coin=_normalise_expected(
                expected_settle_coin, "expected_settle_coin"
            ),
            freshness=freshness,
        ),
    )


def _normalise_mark_index_single(
    payload: object,
    fingerprint: str,
    *,
    expected_symbol: str | None,
    expected_contract: str | None,
    freshness: FreshnessPolicy | None,
) -> BitgetMarkIndex:
    envelope = _read_envelope(payload, "mark_index")
    row = _select_one_row(
        _rows_from_data(envelope.data, "mark_index"),
        "mark_index",
        expected_symbol,
    )
    symbol = _resolve_identity(
        row, _SYMBOL_FIELDS, "symbol", expected_symbol, required_source=True
    )
    contract = _resolve_identity(
        row,
        _CONTRACT_FIELDS,
        "contract",
        expected_contract,
        required_source=False,
    )
    observed_at_ms = _observed_at_ms(envelope, row, "mark_index")
    _check_freshness(
        freshness,
        observed_at_ms,
        symbol=symbol,
        contract=contract,
    )
    mark_price = _read_number(
        row,
        ("markPrice", "markPr", "mark_price"),
        "mark_price",
    )
    index_price = _read_number(
        row,
        ("indexPrice", "indexPr", "index_price"),
        "index_price",
    )
    assert mark_price is not None
    assert index_price is not None
    return BitgetMarkIndex(
        symbol=symbol,
        contract=contract,
        mark_price=_validate_positive(mark_price, "mark_price"),
        index_price=_validate_positive(index_price, "index_price"),
        observed_at_ms=observed_at_ms,
        source_fingerprint=fingerprint,
    )


def _normalise_mark_index_pair(
    mark_payload: object,
    index_payload: object,
    fingerprint: str,
    *,
    expected_symbol: str | None,
    expected_contract: str | None,
    freshness: FreshnessPolicy | None,
) -> BitgetMarkIndex:
    mark_envelope = _read_envelope(mark_payload, "mark_index")
    index_envelope = _read_envelope(index_payload, "mark_index")
    mark_row = _select_one_row(
        _rows_from_data(mark_envelope.data, "mark_index"),
        "mark_index",
        expected_symbol,
    )
    index_row = _select_one_row(
        _rows_from_data(index_envelope.data, "mark_index"),
        "mark_index",
        expected_symbol,
    )
    mark_symbol = _resolve_identity(
        mark_row, _SYMBOL_FIELDS, "symbol", expected_symbol, required_source=True
    )
    index_symbol = _resolve_identity(
        index_row, _SYMBOL_FIELDS, "symbol", expected_symbol, required_source=True
    )
    if mark_symbol != index_symbol:
        raise _invalid(
            "contradictory_identity", "mark and index symbols disagree", field="symbol"
        )
    mark_contract = _resolve_identity(
        mark_row,
        _CONTRACT_FIELDS,
        "contract",
        expected_contract,
        required_source=False,
    )
    index_contract = _resolve_identity(
        index_row,
        _CONTRACT_FIELDS,
        "contract",
        expected_contract,
        required_source=False,
    )
    if mark_contract != index_contract:
        raise _invalid(
            "contradictory_identity",
            "mark and index contracts disagree",
            field="contract",
        )
    mark_observed_at_ms = _observed_at_ms(mark_envelope, mark_row, "mark_index")
    index_observed_at_ms = _observed_at_ms(index_envelope, index_row, "mark_index")
    _check_freshness(
        freshness,
        mark_observed_at_ms,
        symbol=mark_symbol,
        contract=mark_contract,
    )
    _check_freshness(
        freshness,
        index_observed_at_ms,
        symbol=index_symbol,
        contract=index_contract,
    )
    mark_price = _read_number(
        mark_row,
        ("markPrice", "markPr", "mark_price"),
        "mark_price",
    )
    index_price = _read_number(
        index_row,
        ("indexPrice", "indexPr", "index_price"),
        "index_price",
    )
    assert mark_price is not None
    assert index_price is not None
    return BitgetMarkIndex(
        symbol=mark_symbol,
        contract=mark_contract,
        mark_price=_validate_positive(mark_price, "mark_price"),
        index_price=_validate_positive(index_price, "index_price"),
        observed_at_ms=min(mark_observed_at_ms, index_observed_at_ms),
        source_fingerprint=fingerprint,
    )


def normalize_mark_index_payload(
    mark_payload: object,
    index_payload: object | None = None,
    *,
    expected_symbol: str | None = None,
    expected_contract: str | None = None,
    freshness: FreshnessPolicy | None = None,
) -> BitgetPublicResult[BitgetMarkIndex]:
    """Normalize one combined ticker or separate mark and index responses.

    When two payloads are supplied, the fingerprint covers both original
    payloads under stable ``mark`` and ``index`` keys.
    """

    if index_payload is None:
        return _run_payload_normalizer(
            "mark_index",
            mark_payload,
            lambda fingerprint: _normalise_mark_index_single(
                mark_payload,
                fingerprint,
                expected_symbol=_normalise_expected(expected_symbol, "expected_symbol"),
                expected_contract=_normalise_expected(
                    expected_contract, "expected_contract"
                ),
                freshness=freshness,
            ),
        )
    try:
        fingerprint = source_fingerprint({"index": index_payload, "mark": mark_payload})
    except (TypeError, ValueError, OverflowError):
        return BitgetPublicResult.error_result(
            BitgetPublicError(
                surface="mark_index",
                reason="invalid_canonical_json",
                message="mark and index payloads are not canonical JSON",
            )
        )
    return _run_with_fingerprint(
        "mark_index",
        fingerprint,
        lambda: _normalise_mark_index_pair(
            mark_payload,
            index_payload,
            fingerprint,
            expected_symbol=_normalise_expected(expected_symbol, "expected_symbol"),
            expected_contract=_normalise_expected(
                expected_contract, "expected_contract"
            ),
            freshness=freshness,
        ),
    )


def _normalise_funding(
    payload: object,
    fingerprint: str,
    *,
    expected_symbol: str | None,
    expected_contract: str | None,
    freshness: FreshnessPolicy | None,
) -> BitgetFunding:
    envelope = _read_envelope(payload, "funding")
    row = _select_one_row(
        _rows_from_data(envelope.data, "funding", nested_keys=("resultList", "list")),
        "funding",
        expected_symbol,
    )
    symbol = _resolve_identity(
        row, _SYMBOL_FIELDS, "symbol", expected_symbol, required_source=True
    )
    contract = _resolve_identity(
        row,
        _CONTRACT_FIELDS,
        "contract",
        expected_contract,
        required_source=False,
    )
    observed_at_ms = _observed_at_ms(envelope, row, "funding")
    _check_freshness(
        freshness,
        observed_at_ms,
        symbol=symbol,
        contract=contract,
    )
    funding_rate = _read_number(
        row,
        ("fundingRate", "funding_rate"),
        "funding_rate",
    )
    next_funding_time_ms = _read_timestamp(
        row,
        (
            "nextUpdate",
            "nextFundingTime",
            "nextFundingTimestamp",
            "next_funding_time_ms",
        ),
        "next_funding_time_ms",
    )
    funding_time_ms = _read_timestamp(
        row,
        ("fundingTime", "fundingRateTimestamp", "funding_time_ms"),
        "funding_time_ms",
    )
    if funding_time_ms is None and next_funding_time_ms is None:
        raise _unavailable(
            "missing_field",
            "funding time is missing",
            missing_fields=("funding_time_ms_or_next_funding_time_ms",),
            symbol=symbol,
            contract=contract,
        )
    funding_interval_hours = _read_integer(
        row,
        ("fundingRateInterval", "ratePeriod", "funding_interval_hours"),
        "funding_interval_hours",
        required=False,
    )
    assert funding_rate is not None
    return BitgetFunding(
        symbol=symbol,
        contract=contract,
        funding_rate=funding_rate,
        funding_time_ms=funding_time_ms,
        next_funding_time_ms=next_funding_time_ms,
        funding_interval_hours=funding_interval_hours,
        observed_at_ms=observed_at_ms,
        source_fingerprint=fingerprint,
    )


def normalize_funding_payload(
    payload: object,
    *,
    expected_symbol: str | None = None,
    expected_contract: str | None = None,
    freshness: FreshnessPolicy | None = None,
) -> BitgetPublicResult[BitgetFunding]:
    """Normalize current or historical Bitget funding data."""

    return _run_payload_normalizer(
        "funding",
        payload,
        lambda fingerprint: _normalise_funding(
            payload,
            fingerprint,
            expected_symbol=_normalise_expected(expected_symbol, "expected_symbol"),
            expected_contract=_normalise_expected(
                expected_contract, "expected_contract"
            ),
            freshness=freshness,
        ),
    )


def _normalise_maintenance_tiers(
    payload: object,
    fingerprint: str,
    *,
    expected_symbol: str | None,
    expected_contract: str | None,
    freshness: FreshnessPolicy | None,
) -> BitgetMaintenanceTiers:
    envelope = _read_envelope(payload, "maintenance_tiers")
    rows = _rows_from_data(
        envelope.data,
        "maintenance_tiers",
        nested_keys=("resultList", "list"),
    )
    parsed: list[tuple[BitgetMaintenanceTier, int]] = []
    observed_values: list[int] = []
    resolved_symbol: str | None = None
    resolved_contract: str | None = None
    for row in rows:
        row_symbol = _resolve_identity(
            row,
            _SYMBOL_FIELDS,
            "symbol",
            expected_symbol,
            required_source=False,
        )
        row_contract = _resolve_identity(
            row,
            _CONTRACT_FIELDS,
            "contract",
            expected_contract,
            required_source=False,
        )
        if resolved_symbol is None:
            resolved_symbol = row_symbol
        elif resolved_symbol != row_symbol:
            raise _invalid(
                "contradictory_identity",
                "maintenance tier symbols disagree",
                field="symbol",
            )
        if resolved_contract is None:
            resolved_contract = row_contract
        elif resolved_contract != row_contract:
            raise _invalid(
                "contradictory_identity",
                "maintenance tier contracts disagree",
                field="contract",
            )
        observed_at_ms = _observed_at_ms(envelope, row, "maintenance_tiers")
        observed_values.append(observed_at_ms)
        level = _read_integer(row, ("level", "tier"), "level")
        lower_bound = _read_number(
            row,
            ("startUnit", "minTierValue", "lowerBound", "minNotional", "lower_bound"),
            "lower_bound",
        )
        upper_bound = _read_number(
            row,
            ("endUnit", "maxTierValue", "upperBound", "maxNotional", "upper_bound"),
            "upper_bound",
        )
        max_leverage = _read_number(
            row,
            ("leverage", "maxLeverage", "max_leverage"),
            "max_leverage",
        )
        maintenance_margin_rate = _read_number(
            row,
            (
                "keepMarginRate",
                "maintainMarginRate",
                "maintenanceMarginRate",
                "mmr",
                "maintenance_margin_rate",
            ),
            "maintenance_margin_rate",
        )
        assert level is not None
        assert lower_bound is not None
        assert upper_bound is not None
        assert max_leverage is not None
        assert maintenance_margin_rate is not None
        tier = BitgetMaintenanceTier(
            symbol=row_symbol,
            contract=row_contract,
            level=level,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            max_leverage=max_leverage,
            maintenance_margin_rate=maintenance_margin_rate,
        )
        _check_freshness(
            freshness,
            observed_at_ms,
            symbol=row_symbol,
            contract=row_contract,
        )
        parsed.append((tier, observed_at_ms))
    if resolved_symbol is None or resolved_contract is None:
        raise _unavailable(
            "missing_identity",
            "maintenance tier identity is missing",
            missing_fields=("symbol", "contract"),
        )
    parsed.sort(key=lambda item: item[0].level)
    return BitgetMaintenanceTiers(
        symbol=resolved_symbol,
        contract=resolved_contract,
        tiers=tuple(tier for tier, _ in parsed),
        observed_at_ms=min(observed_values),
        source_fingerprint=fingerprint,
    )


def normalize_maintenance_tiers_payload(
    payload: object,
    *,
    expected_symbol: str | None = None,
    expected_contract: str | None = None,
    freshness: FreshnessPolicy | None = None,
) -> BitgetPublicResult[BitgetMaintenanceTiers]:
    """Normalize Bitget tiered maintenance-margin metadata."""

    return _run_payload_normalizer(
        "maintenance_tiers",
        payload,
        lambda fingerprint: _normalise_maintenance_tiers(
            payload,
            fingerprint,
            expected_symbol=_normalise_expected(expected_symbol, "expected_symbol"),
            expected_contract=_normalise_expected(
                expected_contract, "expected_contract"
            ),
            freshness=freshness,
        ),
    )


def _normalise_order_params(
    *,
    symbol: str,
    hold_side: str,
    leverage_by_position_side: Mapping[str, object],
    contract: str | None,
) -> BitgetUTAIsolatedOrderParams:
    try:
        normalised_symbol = _normalise_identity(symbol, "symbol")
    except ValueError as exc:
        raise _invalid(
            "invalid_order_param", "symbol is invalid", field="symbol"
        ) from exc
    try:
        normalised_hold_side = _require_text(hold_side, "hold_side").lower()
    except ValueError as exc:
        raise _invalid(
            "invalid_order_param", "hold_side is invalid", field="hold_side"
        ) from exc
    if normalised_hold_side not in {"long", "short"}:
        raise _invalid(
            "invalid_order_param", "hold_side must be long or short", field="hold_side"
        )
    if not isinstance(leverage_by_position_side, Mapping):
        raise _invalid(
            "invalid_order_param",
            "leverage_by_position_side must be a mapping",
            field="leverage_by_position_side",
        )
    expected_keys = {"long", "short"}
    actual_keys = set(leverage_by_position_side)
    missing = tuple(sorted(expected_keys - actual_keys))
    if missing:
        raise _unavailable(
            "missing_order_param",
            "both position-side leverages are required",
            missing_fields=tuple(f"leverage_by_position_side.{key}" for key in missing),
            symbol=normalised_symbol,
        )
    if actual_keys != expected_keys:
        raise _invalid(
            "invalid_order_param",
            "leverage_by_position_side must contain only long and short",
            field="leverage_by_position_side",
        )
    leverages: dict[str, int] = {}
    for position_side in ("long", "short"):
        raw_leverage = leverage_by_position_side[position_side]
        try:
            leverage = _parse_integer(raw_leverage, f"{position_side}_leverage")
        except (_InvalidProblem, _UnavailableProblem):
            raise
        if leverage < 1:
            raise _invalid(
                "invalid_order_param",
                f"{position_side}_leverage must be positive",
                field=f"{position_side}_leverage",
            )
        leverages[position_side] = leverage
    normalised_contract = None
    if contract is not None:
        try:
            normalised_contract = _normalise_identity(contract, "contract")
        except ValueError as exc:
            raise _invalid(
                "invalid_order_param", "contract is invalid", field="contract"
            ) from exc
    selected_leverage = leverages[normalised_hold_side]
    return BitgetUTAIsolatedOrderParams(
        symbol=normalised_symbol,
        hold_side=normalised_hold_side,
        leverage=selected_leverage,
        long_leverage=leverages["long"],
        short_leverage=leverages["short"],
        margin_mode="isolated",
        contract=normalised_contract,
    )


def build_uta_isolated_order_params(
    symbol: str,
    hold_side: str,
    leverage_by_position_side: Mapping[str, object],
    contract: str | None = None,
) -> BitgetPublicResult[BitgetUTAIsolatedOrderParams]:
    """Build pure UTA isolated hedge parameters without invoking private/write APIs."""

    return _run_with_fingerprint(
        "uta_isolated_order_params",
        None,
        lambda: _normalise_order_params(
            symbol=symbol,
            hold_side=hold_side,
            leverage_by_position_side=leverage_by_position_side,
            contract=contract,
        ),
    )


def _run_with_fingerprint(
    surface: str,
    fingerprint: str | None,
    operation: Callable[[], T],
) -> BitgetPublicResult[T]:
    try:
        return BitgetPublicResult.success(operation())
    except _UnavailableProblem as issue:
        return BitgetPublicResult.unavailable_result(
            BitgetPublicUnavailable(
                surface=surface,
                reason=issue.reason,
                message=issue.message,
                missing_fields=issue.missing_fields,
                symbol=issue.symbol,
                contract=issue.contract,
                observed_at_ms=issue.observed_at_ms,
                age_ms=issue.age_ms,
                source_fingerprint=fingerprint,
            )
        )
    except _InvalidProblem as issue:
        return BitgetPublicResult.error_result(
            BitgetPublicError(
                surface=surface,
                reason=issue.reason,
                message=issue.message,
                field=issue.field,
                symbol=issue.symbol,
                contract=issue.contract,
                source_fingerprint=fingerprint,
            )
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return BitgetPublicResult.error_result(
            BitgetPublicError(
                surface=surface,
                reason="strict_validation_failed",
                message=f"strict validation failed ({type(exc).__name__})",
                source_fingerprint=fingerprint,
            )
        )


def _run_payload_normalizer(
    surface: str,
    payload: object,
    operation: Callable[[str], T],
) -> BitgetPublicResult[T]:
    try:
        fingerprint = source_fingerprint(payload)
    except (TypeError, ValueError, OverflowError):
        return BitgetPublicResult.error_result(
            BitgetPublicError(
                surface=surface,
                reason="invalid_canonical_json",
                message="source payload is not canonical JSON",
            )
        )
    return _run_with_fingerprint(surface, fingerprint, lambda: operation(fingerprint))
