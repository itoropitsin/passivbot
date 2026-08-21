"""Adapters that connect the live and tester risk engines to the parity boundary.

The adapter boundary is deliberately small.  It accepts one already collected
``risk-snapshot-v1`` and one order request, then asks the Python live policy
and the Rust tester extension for their independent decisions.  It never
contacts an exchange and never submits an order.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from research.risk_parity import (
    compare_decisions,
    evaluate_order,
    normalize_snapshot,
    normalize_decision,
    snapshot_hash,
)


RUST_ADAPTER_NAME = "rust-tester"
PYTHON_ADAPTER_NAME = "python-live"


class RiskAdapterUnavailable(RuntimeError):
    """Raised when the compiled tester adapter is not available."""


def python_live_decision(
    snapshot: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    """Evaluate a request through the Python live risk policy."""

    return evaluate_order(snapshot, request, engine=PYTHON_ADAPTER_NAME)


def rust_tester_decision(
    snapshot: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    """Evaluate a request through the compiled Rust tester adapter.

    Importing the extension happens lazily so ordinary documentation and
    read-only tooling can still explain a blocked adapter when the extension
    has not been built yet.
    """

    try:
        import passivbot_rust as pbr
    except Exception as exc:  # pragma: no cover - depends on local build
        raise RiskAdapterUnavailable(f"Rust tester extension unavailable: {exc}") from exc
    function = getattr(pbr, "evaluate_crypto_risk_decision_json", None)
    if not callable(function):
        raise RiskAdapterUnavailable(
            "Rust tester extension lacks evaluate_crypto_risk_decision_json"
        )
    canonical_snapshot = normalize_snapshot(snapshot)
    payload = json.dumps(
        {
            "snapshot": canonical_snapshot,
            "request": dict(request),
            # The Python boundary owns canonical JSON today.  Rust must bind
            # its decision to this exact snapshot digest; it must never invent
            # a digest for a different input.
            "snapshot_hash": snapshot_hash(canonical_snapshot),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    try:
        raw = function(payload)
    except Exception as exc:
        raise RiskAdapterUnavailable(f"Rust tester adapter rejected input: {exc}") from exc
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise RiskAdapterUnavailable("Rust tester adapter returned invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise RiskAdapterUnavailable("Rust tester adapter returned a non-object")
    result = dict(decoded)
    result.setdefault("engine", RUST_ADAPTER_NAME)
    return normalize_decision(result)


def compare_adapters(
    snapshot: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare the two real adapters and fail closed when Rust is unavailable."""

    left = python_live_decision(snapshot, request)
    try:
        right = rust_tester_decision(snapshot, request)
    except RiskAdapterUnavailable as exc:
        return {
            "schema_version": "risk-parity-comparison-v1",
            "parity": "blocked",
            "fail_closed": True,
            "decision": "deny",
            "reason": str(exc),
            "side": left.get("side"),
            "order_identity": left.get("order_identity"),
            "risk_metrics": {"left": left.get("risk_metrics", {})},
            "snapshot_hash": left.get("snapshot_hash"),
            "differences": ["right_adapter"],
            "left_engine": left.get("engine"),
            "right_engine": None,
            "candidate_admission": False,
            "trading_operations_count": 0,
            "diagnostic": _connected_diagnostic(
                tester_connected=False,
                risk_parity_reached=False,
            ),
        }

    result = compare_decisions(left, right, snapshot=snapshot)
    result["candidate_admission"] = False
    result["trading_operations_count"] = 0
    result["diagnostic"] = _connected_diagnostic(
        tester_connected=True,
        risk_parity_reached=result.get("parity") == "passed",
    )
    return result


def _connected_diagnostic(
    *, tester_connected: bool, risk_parity_reached: bool
) -> dict[str, Any]:
    return {
        "schema_version": "risk-parity-diagnostic-v1",
        "status": "connected" if tester_connected else "adapter_pending",
        "risk_parity_reached": bool(risk_parity_reached),
        "candidate_admission": False,
        "second_engine_adapter_connected": bool(tester_connected),
        "trading_operations_count": 0,
        "integration_point": {
            "snapshot": "Python live adapter and Rust tester adapter emit risk-snapshot-v1",
            "decision": "each adapter emits risk-decision-v1",
            "comparison": "read-only parity comparison runs before candidate admission",
            "external_operations": "none",
        },
    }


__all__ = [
    "PYTHON_ADAPTER_NAME",
    "RUST_ADAPTER_NAME",
    "RiskAdapterUnavailable",
    "compare_adapters",
    "python_live_decision",
    "rust_tester_decision",
]
