"""Fail-closed composition of the four Bitget public market surfaces."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .bitget_public import (
    BitgetFunding,
    BitgetInstrument,
    BitgetMaintenanceTiers,
    BitgetMarkIndex,
    BitgetPublicResult,
)


class PublicProfileError(ValueError):
    """Raised when a public profile cannot be used for a new entry."""


@dataclass(frozen=True, slots=True)
class BitgetPublicProfile:
    """One consistent, entry-ready public market snapshot or a blocked one."""

    instrument: BitgetInstrument | None
    mark_index: BitgetMarkIndex | None
    funding: BitgetFunding | None
    maintenance_tiers: BitgetMaintenanceTiers | None
    reasons: tuple[str, ...]
    source_fingerprints: tuple[str, ...]

    @property
    def entry_ready(self) -> bool:
        return not self.reasons and all(
            value is not None
            for value in (
                self.instrument,
                self.mark_index,
                self.funding,
                self.maintenance_tiers,
            )
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            "source_fingerprints": list(self.source_fingerprints),
            "reasons": list(self.reasons),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def require_entry_ready(self) -> "BitgetPublicProfile":
        if not self.entry_ready:
            raise PublicProfileError(
                "Bitget public profile blocks new entries: "
                + ", ".join(self.reasons or ("surface_missing",))
            )
        return self


def _surface_result(
    result: Any,
    name: str,
    expected_type: type,
) -> tuple[Any | None, list[str], list[str]]:
    if not isinstance(result, BitgetPublicResult):
        return None, [f"{name}:invalid_result"], []
    if result.value is None:
        issue = result.unavailable or result.error
        reason = getattr(issue, "reason", "unavailable")
        return None, [f"{name}:{reason}"], []
    if not isinstance(result.value, expected_type):
        return None, [f"{name}:invalid_value"], []
    fingerprint = result.source_fingerprint
    return result.value, [], [fingerprint] if fingerprint else [f"{name}:no_hash"]


def build_public_profile(
    instrument: BitgetPublicResult[BitgetInstrument],
    mark_index: BitgetPublicResult[BitgetMarkIndex],
    funding: BitgetPublicResult[BitgetFunding],
    maintenance_tiers: BitgetPublicResult[BitgetMaintenanceTiers],
) -> BitgetPublicProfile:
    """Join public surfaces and block on missing or contradictory identity."""

    values: list[Any | None] = []
    reasons: list[str] = []
    hashes: list[str] = []
    for result, name, expected_type in (
        (instrument, "instrument", BitgetInstrument),
        (mark_index, "mark_index", BitgetMarkIndex),
        (funding, "funding", BitgetFunding),
        (maintenance_tiers, "maintenance_tiers", BitgetMaintenanceTiers),
    ):
        value, value_reasons, value_hashes = _surface_result(
            result, name, expected_type
        )
        values.append(value)
        reasons.extend(value_reasons)
        hashes.extend(value_hashes)

    available = [value for value in values if value is not None]
    identities = {(value.symbol, value.contract) for value in available}
    if len(identities) > 1:
        reasons.append("contradictory_identity")
    instrument_value = values[0]
    if instrument_value is not None and instrument_value.status not in {
        "online",
        "normal",
        "open",
    }:
        reasons.append("instrument_not_online")

    return BitgetPublicProfile(
        instrument=instrument_value,
        mark_index=values[1],
        funding=values[2],
        maintenance_tiers=values[3],
        reasons=tuple(dict.fromkeys(reasons)),
        source_fingerprints=tuple(hashes),
    )


__all__ = ["BitgetPublicProfile", "PublicProfileError", "build_public_profile"]
