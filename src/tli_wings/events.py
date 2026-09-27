"""TLI event helpers using the native v8.1 ``LiveEvent`` contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from live.event_bus import LiveEvent


TLI_EVENT_PREFIX = "tli."


def make_tli_event(
    name: str,
    *,
    data: Mapping[str, Any] | None = None,
    **fields: Any,
) -> LiveEvent:
    """Create a native v8 event whose type is explicitly in the TLI namespace."""

    if not isinstance(name, str) or not name.strip():
        raise ValueError("TLI event name must be non-empty")
    event_type = name.strip()
    if not event_type.startswith(TLI_EVENT_PREFIX):
        event_type = TLI_EVENT_PREFIX + event_type
    if "event_type" in fields:
        raise ValueError("event_type is derived from the TLI event name")
    return LiveEvent(
        event_type=event_type,
        source="tli",
        component="tli_wings",
        data=dict(data or {}),
        **fields,
    )


__all__ = ["TLI_EVENT_PREFIX", "make_tli_event"]
