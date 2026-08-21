"""Compare live and tester crypto-risk decisions without external side effects.

The harness accepts decisions produced by the two engines. It never creates a
decision for a missing engine, never contacts an exchange, and never submits an
order. Missing adapters are reported as ``blocked`` and the effective decision
is ``deny``.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
import json
import os
import tempfile
from typing import Any

from research.risk_parity import (
    COMPARISON_SCHEMA,
    compare_decisions,
    parity_diagnostic,
    snapshot_hash,
)
from research.risk_adapters import RiskAdapterUnavailable, compare_adapters
from research.risk_trace import (
    RiskTraceError,
    compare_live_export_with_rust_trace,
    compare_live_export_with_rust_trace_records,
    live_export_to_decisions,
)


def _read_json(path: str | Path) -> Any:
    target = Path(path).expanduser().resolve()
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON input {target}: {exc}") from exc


def _blocked_result(reason: str, *, snapshot: Any = None) -> dict[str, Any]:
    digest = None
    if isinstance(snapshot, dict):
        try:
            digest = snapshot_hash(snapshot)
        except (TypeError, ValueError):
            digest = None
    return {
        "schema_version": COMPARISON_SCHEMA,
        "parity": "blocked",
        "fail_closed": True,
        "decision": "deny",
        "reason": reason,
        "side": None,
        "order_identity": None,
        "risk_metrics": {},
        "snapshot_hash": digest,
        "differences": [],
        "left_engine": None,
        "right_engine": None,
        "candidate_admission": False,
        "trading_operations_count": 0,
        "diagnostic": parity_diagnostic(),
    }


def compare_json_files(
    *,
    snapshot_path: str | Path,
    left_decision_path: str | Path | None,
    right_decision_path: str | Path | None,
    request_path: str | Path | None = None,
    live_export_path: str | Path | None = None,
    rust_trace_path: str | Path | None = None,
    trace_index: int | None = None,
) -> dict[str, Any]:
    """Compare two already-produced engine decisions, failing closed on gaps."""

    snapshot = _read_json(snapshot_path)
    if live_export_path is not None or rust_trace_path is not None:
        if live_export_path is None or rust_trace_path is None:
            return _blocked_result(
                "actual engine mode requires both live export and Rust trace",
                snapshot=snapshot,
            )
        try:
            live_export = _read_json(live_export_path)
            raw_trace = _read_json(rust_trace_path)
            if isinstance(raw_trace, Mapping):
                raw_trace = raw_trace.get("risk_gate_trace", raw_trace.get("trace"))
            if not isinstance(raw_trace, list):
                return _blocked_result("Rust trace file must contain a list", snapshot=snapshot)
            live_snapshot, _ = live_export_to_decisions(live_export)
            if snapshot_hash(snapshot) != snapshot_hash(live_snapshot):
                return _blocked_result(
                    "external snapshot differs from the Python live export snapshot",
                    snapshot=snapshot,
                )
            if trace_index is None:
                if not raw_trace:
                    return _blocked_result("Rust trace must contain at least one record", snapshot=snapshot)
                result = compare_live_export_with_rust_trace_records(live_export, raw_trace)
            else:
                if trace_index < 0 or trace_index >= len(raw_trace):
                    return _blocked_result("Rust trace index is outside the trace list", snapshot=snapshot)
                result = compare_live_export_with_rust_trace(
                    live_export,
                    raw_trace[trace_index],
                )
            result["candidate_admission"] = False
            result["trading_operations_count"] = 0
            result.setdefault("source_mode", "actual_engine_trace")
            return result
        except (OSError, ValueError, RiskTraceError) as exc:
            return _blocked_result(f"actual engine evidence could not be compared: {exc}", snapshot=snapshot)
    if left_decision_path is None and right_decision_path is None and request_path is not None:
        request = _read_json(request_path)
        if not isinstance(request, dict):
            return _blocked_result("order request must be a JSON object", snapshot=snapshot)
        try:
            return compare_adapters(snapshot, request)
        except (TypeError, ValueError, RiskAdapterUnavailable) as exc:
            return _blocked_result(f"adapter comparison could not be completed: {exc}", snapshot=snapshot)
    if left_decision_path is None or right_decision_path is None:
        missing = []
        if left_decision_path is None:
            missing.append("left engine decision")
        if right_decision_path is None:
            missing.append("right engine decision")
        return _blocked_result("missing engine adapter output: " + ", ".join(missing), snapshot=snapshot)
    try:
        left = _read_json(left_decision_path)
        right = _read_json(right_decision_path)
    except ValueError as exc:
        return _blocked_result(str(exc), snapshot=snapshot)
    try:
        return compare_decisions(left, right, snapshot=snapshot)
    except (TypeError, ValueError) as exc:
        return _blocked_result(f"parity comparison could not be completed: {exc}", snapshot=snapshot)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--left-decision", type=Path)
    parser.add_argument("--right-decision", type=Path)
    parser.add_argument(
        "--request",
        type=Path,
        help="order request JSON; when both decisions are omitted, run both real adapters",
    )
    parser.add_argument(
        "--live-export",
        type=Path,
        help="actual Python live export JSON; use together with --rust-trace",
    )
    parser.add_argument(
        "--rust-trace",
        type=Path,
        help="actual Rust Backtest risk_gate_trace JSON/list; use together with --live-export",
    )
    parser.add_argument(
        "--trace-index",
        type=int,
        default=None,
        help="compare one trace record; by default compare the complete actual trace set",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = compare_json_files(
            snapshot_path=args.snapshot,
            left_decision_path=args.left_decision,
            right_decision_path=args.right_decision,
            request_path=args.request,
            live_export_path=args.live_export,
            rust_trace_path=args.rust_trace,
            trace_index=args.trace_index,
        )
    except (OSError, ValueError) as exc:
        result = _blocked_result(str(exc))
    _write_json_atomic(args.output.expanduser().resolve(), result)
    print(json.dumps({"status": result["parity"], "decision": result["decision"]}, sort_keys=True))
    if result["parity"] == "passed":
        return 0
    return 2 if result["parity"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
