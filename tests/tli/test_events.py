from __future__ import annotations

import pytest

from tli_wings.events import make_tli_event


def test_tli_event_uses_native_live_event_with_namespace():
    event = make_tli_event("risk.decision", data={"allow_new_entries": False})
    assert event.event_type == "tli.risk.decision"
    assert event.source == "tli"
    assert event.data["allow_new_entries"] is False


def test_tli_event_does_not_allow_another_event_type():
    with pytest.raises(ValueError, match="derived"):
        make_tli_event("risk.decision", event_type="other")
