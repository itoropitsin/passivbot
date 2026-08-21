"""Supervise a public market strategy shadow across real child processes.

The supervisor is still local-only: children read public Bitget market data,
the strategy uses the existing paper ledger, and no private or write API is
available.  A fixture can be used for process-level tests, but fixture runs
are explicitly marked and cannot satisfy the readiness gate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from research.strategy_shadow import (
    canonical_hash,
    read_shadow_events,
    run_strategy_shadow,
)
from research.strategy_shadow_stream import (
    resolve_stream_max_source_gap,
    resolve_stream_policy,
)
from tools.bitget_market_recorder import verify_market_log

UTC = timezone.utc
STREAM_PROCESS_SCHEMA_VERSION = "crypto-strategy-shadow-stream-process-v1"
CODE_PATHS = (
    Path("src/research/strategy_shadow.py"),
    Path("src/tools/strategy_shadow.py"),
    Path("src/research/strategy_shadow_process.py"),
    Path("src/tools/strategy_shadow_process.py"),
    Path("src/research/strategy_shadow_stream.py"),
    Path("src/tools/strategy_shadow_stream.py"),
    Path("src/research/strategy_shadow_stream_process.py"),
    Path("src/tools/strategy_shadow_stream_process.py"),
)
FORBIDDEN_KEY_PARTS = frozenset(
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
STATE_FILES = (
    "checkpoint.json",
    "events.ndjson",
    "paper_ledger.json",
    "policy_state.json",
    "stream_state.json",
    "market_snapshots.jsonl",
    "strategy_inputs.ndjson",
)


def _require_resume_output(
    root: Path,
    *,
    fixture_mode: bool,
    max_source_gap_seconds: float,
    now: float | None = None,
) -> dict[str, Any]:
    """Validate an unfinished stream directory before a host cutover resume."""

    if not root.is_dir():
        raise FileNotFoundError(f"resume output directory does not exist: {root}")
    completed = root / "process_supervisor.json"
    if completed.is_file():
        raise FileExistsError(f"cannot resume a completed supervisor result: {completed}")
    missing = [name for name in STATE_FILES if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "resume output directory is missing state files: " + ", ".join(missing)
        )
    market_path = root / "market_snapshots.jsonl"
    audit = verify_market_log(
        market_path,
        max_gap_seconds=max_source_gap_seconds,
        use_source_timestamp=True,
    )
    if audit.get("status") != "passed":
        raise ValueError("existing market snapshot log failed integrity verification")
    parsed_state = json.loads((root / "stream_state.json").read_text(encoding="utf-8"))
    if not isinstance(parsed_state, dict):
        raise ValueError("stream_state.json must be an object")
    if bool(parsed_state.get("fixture_mode", False)) != bool(fixture_mode):
        raise ValueError("stream state fixture_mode does not match the current session")
    snapshot = {
        "ingest_seq": parsed_state.get("ingest_seq"),
        "market_event_count": parsed_state.get("market_event_count"),
        "last_source_timestamp_ms": parsed_state.get("last_source_timestamp_ms"),
        "market_log_audit_status": audit.get("status"),
    }
    if not fixture_mode:
        last_ms = parsed_state.get("last_source_timestamp_ms")
        if last_ms is None:
            raise ValueError("cannot resume live stream without last_source_timestamp_ms")
        age_seconds = float(now if now is not None else time.time()) - (float(last_ms) / 1000.0)
        if age_seconds < 0.0:
            age_seconds = 0.0
        if age_seconds > float(max_source_gap_seconds):
            raise ValueError(
                "resume rejected: last source timestamp is "
                f"{age_seconds:.1f}s old; max_source_gap_seconds={max_source_gap_seconds}"
            )
        snapshot["source_age_seconds"] = age_seconds
    return snapshot


def _highest_segment_number(segments_dir: Path) -> int:
    highest = 0
    for path in segments_dir.glob("segment-*-result.json"):
        parts = path.stem.split("-")
        if len(parts) >= 2 and parts[1].isdigit():
            highest = max(highest, int(parts[1]))
    return highest


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_file_optional(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp), tz=UTC).isoformat()


def _reject_credentials(value: Any, *, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            compact = normalized.replace("_", "")
            if normalized in FORBIDDEN_KEY_PARTS or compact in FORBIDDEN_KEY_PARTS:
                raise ValueError(f"credential-like field is forbidden: {path}.{key}")
            _reject_credentials(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_credentials(nested, path=f"{path}[{index}]")


def _load_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("stream process config must be an object")
    _reject_credentials(value, path="config")
    return value


def _code_hash(repo_root: Path) -> tuple[str, dict[str, str]]:
    files: dict[str, str] = {}
    for relative in CODE_PATHS:
        path = repo_root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        files[relative.as_posix()] = sha256_file(path)
    return canonical_hash(files), files


def _state_hash(root: Path) -> tuple[str | None, bool]:
    files: dict[str, str] = {}
    for name in STATE_FILES:
        path = root / name
        if not path.is_file():
            return None, False
        files[name] = sha256_file(path)
    return canonical_hash(files), True


def _duplicate_state_count(root: Path) -> int:
    path = root / "events.ndjson"
    if not path.is_file():
        return 0
    events = read_shadow_events(path)
    order_ids: list[str] = []
    fill_ids: list[str] = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if event.get("kind") == "order.opened" and payload.get("order_id"):
            order_ids.append(str(payload["order_id"]))
        if event.get("kind") == "order.filled" and payload.get("fill_id"):
            fill_ids.append(str(payload["fill_id"]))
    return (len(order_ids) - len(set(order_ids))) + (len(fill_ids) - len(set(fill_ids)))


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"strategy input line {line_number} must be an object")
            rows.append(value)
    return rows


def _count_jsonl_rows(path: Path) -> int:
    """Count non-empty JSONL rows without changing the source file."""

    if not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


class _SupervisorInterrupted(RuntimeError):
    """Internal signal exception used to clean up a running child process."""


def _process_group_exists(process_group_id: int) -> bool:
    """Return whether a POSIX process group still has a process."""

    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The group exists, but the current process cannot signal one of its
        # members. Treat that as live so cleanup does not return early.
        return True
    return True


def _terminate_process_tree(process: subprocess.Popen[Any], *, timeout_seconds: float = 5.0) -> None:
    """Stop a child and its descendants without leaving an orphan stream."""

    timeout = max(0.0, float(timeout_seconds))
    if os.name == "posix":
        # Children created by this supervisor use ``start_new_session=True``,
        # therefore the child PID is also the process-group ID. Inspect the
        # group even when the direct child has already exited: a grandchild
        # can outlive its parent while remaining in that group.
        process_group_id = int(process.pid)
        group_exists = _process_group_exists(process_group_id)
        if process.poll() is None or group_exists:
            try:
                os.killpg(process_group_id, signal.SIGTERM)
            except ProcessLookupError:
                group_exists = False
            deadline = time.monotonic() + timeout
            while group_exists and time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                if process.poll() is None:
                    try:
                        process.wait(timeout=min(remaining, 0.05))
                    except subprocess.TimeoutExpired:
                        pass
                else:
                    time.sleep(min(remaining, 0.01))
                group_exists = _process_group_exists(process_group_id)
            if group_exists:
                try:
                    os.killpg(process_group_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.wait()
        return
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            return
        process.wait()


def _supervisor_signal_handler(signum: int, _frame: Any) -> None:
    raise _SupervisorInterrupted(f"supervisor interrupted by signal {signum}")


def _install_supervisor_signal_handlers() -> dict[int, Any]:
    """Install cleanup-aware handlers when called from the main thread."""

    if threading.current_thread() is not threading.main_thread():
        return {}
    previous: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, _supervisor_signal_handler)
    return previous


def _restore_supervisor_signal_handlers(previous: Mapping[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _run_child_process(
    command: list[str], *, cwd: Path, env: Mapping[str, str]
) -> tuple[subprocess.Popen[str], str, str]:
    """Run one child in its own process group and clean it on interruption."""

    previous_handlers = _install_supervisor_signal_handlers()
    process: subprocess.Popen[str] | None = None
    try:
        popen_kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": dict(env),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **popen_kwargs)
        stdout, stderr = process.communicate()
        return process, stdout, stderr
    except BaseException:
        if process is not None:
            _terminate_process_tree(process)
        raise
    finally:
        _restore_supervisor_signal_handlers(previous_handlers)


def run_stream_process_supervisor(
    *,
    output_dir: str | os.PathLike[str],
    config_path: str | os.PathLike[str],
    symbol: str = "BTCUSDT",
    category: str = "USDT-FUTURES",
    depth: int = 20,
    side: str = "long",
    entry_bps: float | None = None,
    take_profit_bps: float | None = None,
    order_amount: float | None = None,
    interval_seconds: float = 60.0,
    max_source_gap_seconds: float | None = None,
    segment_duration_seconds: float = 4 * 60 * 60,
    max_samples: int | None = None,
    restart_count: int = 5,
    deterministic_replay_runs: int = 3,
    fixture_path: str | os.PathLike[str] | None = None,
    market_profile_path: str | os.PathLike[str] | None = None,
    python_executable: str | os.PathLike[str] | None = None,
    repo_root: str | os.PathLike[str] | None = None,
    resume_existing_output: bool = False,
) -> dict[str, Any]:
    """Run one bounded public stream segment in each child process."""

    if isinstance(restart_count, bool) or int(restart_count) < 1:
        raise ValueError("restart_count must be positive")
    if isinstance(deterministic_replay_runs, bool) or int(deterministic_replay_runs) < 2:
        raise ValueError("deterministic_replay_runs must be at least two")
    if not math.isfinite(float(segment_duration_seconds)) or float(segment_duration_seconds) < 0.0:
        raise ValueError("segment_duration_seconds must be finite and >= 0")
    if not math.isfinite(float(interval_seconds)) or float(interval_seconds) <= 0.0:
        raise ValueError("interval_seconds must be finite and > 0")
    if max_samples is not None and (isinstance(max_samples, bool) or int(max_samples) < 1):
        raise ValueError("max_samples must be positive")

    config_file = Path(config_path).expanduser().resolve()
    input_fixture = Path(fixture_path).expanduser().resolve() if fixture_path else None
    market_profile_file = (
        Path(market_profile_path).expanduser().resolve()
        if market_profile_path is not None
        else None
    )
    root = Path(output_dir).expanduser().resolve()
    source_root = Path(repo_root).expanduser().resolve() if repo_root else Path(__file__).resolve().parents[2]
    config = _load_config(config_file)
    if "shadow_policy" not in config:
        raise ValueError("stream process config must define shadow_policy")
    policy_values = resolve_stream_policy(
        config,
        entry_bps=entry_bps,
        take_profit_bps=take_profit_bps,
        order_amount=order_amount,
    )
    max_source_gap_seconds = resolve_stream_max_source_gap(
        config,
        max_source_gap_seconds=max_source_gap_seconds,
        interval_seconds=float(interval_seconds),
    )
    entry_bps = policy_values["entry_bps"]
    take_profit_bps = policy_values["take_profit_bps"]
    order_amount = policy_values["order_amount"]
    code_sha256, code_files = _code_hash(source_root)
    config_sha256 = sha256_file(config_file)
    fixture_mode = input_fixture is not None
    if fixture_mode and not input_fixture.is_file():
        raise FileNotFoundError(input_fixture)
    if market_profile_file is not None and not market_profile_file.is_file():
        raise FileNotFoundError(market_profile_file)
    market_profile_sha256 = (
        sha256_file(market_profile_file) if market_profile_file is not None else None
    )

    resume_snapshot: dict[str, Any] | None = None
    if resume_existing_output:
        resume_snapshot = _require_resume_output(
            root,
            fixture_mode=fixture_mode,
            max_source_gap_seconds=float(max_source_gap_seconds),
        )
        segments_dir = root / "segments"
        segments_dir.mkdir(exist_ok=True)
        resume_segment_offset = _highest_segment_number(segments_dir)
        resume_snapshot["preserved_segment_count"] = resume_segment_offset
        stale_result = root / "result.json"
        if stale_result.is_file():
            dest = segments_dir / "pre-resume-result.json"
            if dest.exists():
                dest = segments_dir / f"pre-resume-result-{int(time.time())}.json"
            stale_result.replace(dest)
    else:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"output directory must be new and empty: {root}")
        root.mkdir(parents=True, exist_ok=False)
        segments_dir = root / "segments"
        segments_dir.mkdir()
        resume_segment_offset = 0

    child_python = str(python_executable or sys.executable)
    env = os.environ.copy()
    source_path = str(source_root / "src")
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = source_path + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
    processes: list[dict[str, Any]] = []
    child_results: list[dict[str, Any]] = []
    errors: list[str] = []
    started_wall = time.time()
    started_monotonic = time.monotonic()
    resume_market_events = int((resume_snapshot or {}).get("market_event_count") or 0)

    for index in range(int(restart_count) + 1):
        command = [
            child_python,
            "-m",
            "tools.strategy_shadow_stream",
            "--output-dir",
            str(root),
            "--config",
            str(config_file),
            "--symbol",
            str(symbol),
            "--category",
            str(category),
            "--depth",
            str(depth),
            "--side",
            str(side),
            "--entry-bps",
            str(entry_bps),
            "--take-profit-bps",
            str(take_profit_bps),
            "--order-amount",
            str(order_amount),
            "--interval-seconds",
            str(interval_seconds),
            "--max-source-gap-seconds",
            str(max_source_gap_seconds),
            "--duration-seconds",
            str(segment_duration_seconds),
        ]
        if max_samples is not None:
            command.extend(("--max-samples", str(max_samples)))
        if input_fixture is not None:
            fixture_offset = resume_market_events + (
                (index * int(max_samples)) if max_samples is not None else index
            )
            command.extend(
                ("--fixture", str(input_fixture), "--fixture-offset", str(fixture_offset))
            )
        child_started_wall = time.time()
        process, stdout, stderr = _run_child_process(
            command,
            cwd=source_root,
            env=env,
        )
        child_ended_wall = time.time()
        segment_number = resume_segment_offset + index + 1
        result_path = root / "result.json"
        child_result: dict[str, Any] = {}
        if result_path.is_file():
            try:
                parsed = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    child_result = parsed
                else:
                    errors.append(f"child result is not an object after segment {segment_number}")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"child result is unreadable after segment {segment_number}: {exc}")
        else:
            errors.append(f"child result is missing after segment {segment_number}")
        segment_result_path = segments_dir / f"segment-{segment_number:03d}-result.json"
        if result_path.is_file():
            shutil.copyfile(result_path, segment_result_path)
        state_hash, state_present = _state_hash(root)
        process_record = {
            "segment": segment_number,
            "pid": int(process.pid),
            "return_code": process.returncode,
            "started_at_utc": _utc_iso(child_started_wall),
            "ended_at_utc": _utc_iso(child_ended_wall),
            "elapsed_seconds": max(0.0, child_ended_wall - child_started_wall),
            "command_sha256": hashlib.sha256(
                json.dumps(command, ensure_ascii=False, sort_keys=False).encode("utf-8")
            ).hexdigest(),
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
            "result_path": str(segment_result_path),
            "result_sha256": _sha256_file_optional(segment_result_path),
            "state_hash_after": state_hash,
            "state_files_present": state_present,
        }
        processes.append(process_record)
        child_results.append(child_result)
        if process.returncode != 0:
            errors.append(f"child process {segment_number} exited with {process.returncode}")

    restart_operations: list[dict[str, Any]] = []
    for previous, current in zip(processes, processes[1:]):
        previous_end = datetime.fromisoformat(previous["ended_at_utc"]).timestamp()
        current_start = datetime.fromisoformat(current["started_at_utc"]).timestamp()
        recovery_seconds = max(0.0, current_start - previous_end)
        restart_operations.append(
            {
                "restart_id": len(restart_operations) + 1,
                "old_pid": previous["pid"],
                "new_pid": current["pid"],
                "old_return_code": previous["return_code"],
                "new_return_code": current["return_code"],
                "stopped_at_utc": previous["ended_at_utc"],
                "started_at_utc": current["started_at_utc"],
                "recovery_seconds": recovery_seconds,
                "state_hash_before": previous["state_hash_after"],
                "state_hash_after": current["state_hash_after"],
                "state_files_present": bool(previous["state_files_present"] and current["state_files_present"]),
            }
        )

    duplicate_state_count = _duplicate_state_count(root)
    pids = [record["pid"] for record in processes]
    max_recovery = max(
        (float(operation["recovery_seconds"]) for operation in restart_operations),
        default=None,
    )
    restart_status = (
        "passed"
        if len(restart_operations) == int(restart_count)
        and len(set(pids)) == len(pids)
        and all(record["return_code"] == 0 for record in processes)
        and all(record["state_files_present"] for record in processes)
        and all(float(operation["recovery_seconds"]) <= 60.0 for operation in restart_operations)
        and duplicate_state_count == 0
        else "failed"
    )

    strategy_inputs_path = root / "strategy_inputs.ndjson"
    replay_root = root / "deterministic_full_replay"
    replay_rows = _load_jsonl_rows(strategy_inputs_path) if strategy_inputs_path.is_file() else []
    full_replay: dict[str, Any]
    if replay_rows:
        full_replay = asyncio.run(
            run_strategy_shadow(
                replay_rows,
                output_dir=replay_root,
                config=config,
                deterministic_replay_runs=int(deterministic_replay_runs),
                input_provenance={
                    "source_kind": "local_stream_strategy_inputs",
                    "source_path": str(strategy_inputs_path),
                    "source_sha256": sha256_file(strategy_inputs_path),
                    "format": "ndjson",
                },
            )
        )
    else:
        errors.append("stream produced no accepted market events for deterministic replay")
        full_replay = {
            "status": "failed",
            "deterministic_replay": {"status": "failed", "requested_runs": int(deterministic_replay_runs)},
            "deterministic_replay_runs": 0,
        }
    if full_replay.get("deterministic_replay", {}).get("status") != "passed":
        errors.append("full deterministic stream replay did not pass")

    final_result = child_results[-1] if child_results else {}
    readiness_metrics = dict(final_result.get("readiness_metrics", {}))
    driver_restart_count = readiness_metrics.get("shadow_restart_count")
    driver_max_recovery = readiness_metrics.get("shadow_max_restart_recovery_seconds")
    child_stream_errors: list[dict[str, Any]] = []
    child_stream_attempts = 0
    for index, child_result in enumerate(child_results, start=1):
        child_stream = child_result.get("stream")
        if not isinstance(child_stream, Mapping):
            continue
        child_stream_attempts += int(child_stream.get("attempts", 0) or 0)
        reported_errors = child_stream.get("errors")
        if isinstance(reported_errors, list):
            for error in reported_errors:
                if isinstance(error, Mapping):
                    child_stream_errors.append(
                        {"segment": resume_segment_offset + index, **dict(error)}
                    )
    if child_stream_errors:
        errors.append(
            f"child stream reported {len(child_stream_errors)} collection or processing errors"
        )
    stream_state_path = root / "stream_state.json"
    market_path = root / "market_snapshots.jsonl"
    strategy_inputs_path = root / "strategy_inputs.ndjson"
    stream_state = {}
    if stream_state_path.is_file():
        parsed_state = json.loads(stream_state_path.read_text(encoding="utf-8"))
        if isinstance(parsed_state, dict):
            stream_state = parsed_state
    elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
    ended_wall = time.time()
    market_sha256 = _sha256_file_optional(market_path)
    market_log_audit = (
        verify_market_log(
            market_path,
            max_gap_seconds=max_source_gap_seconds,
            use_source_timestamp=True,
        )
        if market_path.is_file()
        else {"status": "failed"}
    )
    if market_log_audit.get("status") != "passed":
        errors.append("market source gap or integrity check failed")
    cumulative_market_events = int(stream_state.get("market_event_count", 0) or 0)
    cumulative_heartbeats = int(stream_state.get("heartbeat_count", 0) or 0)
    cumulative_strategy_inputs = _count_jsonl_rows(strategy_inputs_path)
    cumulative_market_snapshots = _count_jsonl_rows(market_path)
    if cumulative_strategy_inputs != cumulative_market_events:
        errors.append("strategy input count does not match persisted market event count")
    stream_errors = child_stream_errors
    readiness_metrics.update(
        {
            "shadow_wall_clock_duration_seconds": elapsed_seconds,
            "shadow_process_restart_status": restart_status,
            "shadow_process_restart_count": len(restart_operations),
            "shadow_process_max_recovery_seconds": max_recovery,
            "shadow_process_code_hash": code_sha256,
            "shadow_process_config_hash": config_sha256,
            "shadow_process_policy_config_hash": canonical_hash(policy_values),
            "shadow_process_input_hash": market_sha256,
            "shadow_process_duplicate_state_count": duplicate_state_count,
            # Keep driver recovery separate from process handoff recovery.
            # The generic gate refers to the driver evidence; process-level
            # evidence has its own explicit fields below.
            "shadow_driver_restart_count": driver_restart_count,
            "shadow_driver_max_restart_recovery_seconds": driver_max_recovery,
            "shadow_process_restart_max_recovery_seconds": max_recovery,
            "shadow_restart_observed": bool(driver_restart_count or restart_operations),
            "shadow_determinism_runs": int(full_replay.get("deterministic_replay_runs", 0)),
            "shadow_stream_fixture_mode": fixture_mode,
            "shadow_stream_attempts": child_stream_attempts,
            "shadow_stream_samples": cumulative_strategy_inputs,
            "shadow_stream_market_events": cumulative_market_events,
            "shadow_stream_heartbeats": cumulative_heartbeats,
            "shadow_stream_market_snapshots": cumulative_market_snapshots,
            "shadow_stream_errors": len(stream_errors),
            "shadow_stream_input_hash": market_sha256,
            "shadow_process_max_source_gap_seconds": max_source_gap_seconds,
            "shadow_stream_max_source_gap_limit_seconds": max_source_gap_seconds,
            "shadow_stream_max_source_gap_seconds": market_log_audit.get("max_source_gap_seconds"),
            "shadow_stream_gap_check_status": market_log_audit.get("status"),
        }
    )
    supervisor = {
        "schema_version": STREAM_PROCESS_SCHEMA_VERSION,
        "status": restart_status,
        "fixture_mode": fixture_mode,
        "requested_restart_count": int(restart_count),
        "observed_restart_count": len(restart_operations),
        "started_at_utc": _utc_iso(started_wall),
        "ended_at_utc": _utc_iso(ended_wall),
        "wall_clock_duration_seconds": elapsed_seconds,
        "config_path": str(config_file),
        "config_sha256": config_sha256,
        "policy_config": policy_values,
        "policy_config_hash": canonical_hash(policy_values),
        "max_source_gap_seconds": max_source_gap_seconds,
        "code_hash": code_sha256,
        "code_files": code_files,
        "market_snapshot_path": str(market_path),
        "market_snapshot_sha256": market_sha256,
        "market_profile_path": str(market_profile_file) if market_profile_file else None,
        "market_profile_sha256": market_profile_sha256,
        "processes": processes,
        "restart_operations": restart_operations,
        "duplicate_state_count": duplicate_state_count,
        "errors": errors,
        "resumed_existing_output": bool(resume_existing_output),
        "resume_snapshot": resume_snapshot,
    }
    supervisor_path = root / "process_supervisor.json"
    supervisor_path.write_text(
        json.dumps(supervisor, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    result = dict(final_result)
    result["status"] = (
        "passed"
        if result.get("status") == "passed"
        and restart_status == "passed"
        and not errors
        and full_replay.get("status") == "passed"
        else "failed"
    )
    result["deterministic_replay"] = full_replay.get("deterministic_replay")
    result["deterministic_replay_hash"] = full_replay.get("deterministic_replay_hash")
    result["deterministic_replay_runs"] = full_replay.get("deterministic_replay_runs", 0)
    result["readiness_metrics"] = readiness_metrics
    result["process_supervisor"] = supervisor
    stream = dict(result.get("stream", {}))
    stream.update(
        {
            "started_at_utc": _utc_iso(started_wall),
            "ended_at_utc": _utc_iso(ended_wall),
            "wall_clock_duration_seconds": elapsed_seconds,
            "attempts": child_stream_attempts,
            "samples": cumulative_strategy_inputs,
            "market_events": cumulative_market_events,
            "heartbeats": cumulative_heartbeats,
            "errors": stream_errors,
            "snapshot_path": str(market_path),
            "snapshot_sha256": market_sha256,
            "strategy_input_path": str(strategy_inputs_path),
            "strategy_input_sha256": _sha256_file_optional(strategy_inputs_path),
            "market_log_audit": market_log_audit,
            "public_only": True,
            "network_writes": 0,
            "trading_operations_count": 0,
        }
    )
    result["stream"] = stream
    result["input_snapshot_count"] = cumulative_strategy_inputs
    result["normalized_snapshot_count"] = cumulative_strategy_inputs
    provenance = dict(result.get("provenance", {}))
    artifacts = dict(provenance.get("artifacts", {}))
    artifacts["process_supervisor"] = {
        "exists": True,
        "path": str(supervisor_path),
        "sha256": sha256_file(supervisor_path),
    }
    provenance["artifacts"] = artifacts
    provenance["stream_process_supervisor"] = {
        "code_hash": code_sha256,
        "config_sha256": config_sha256,
        "input_sha256": market_sha256,
        "max_source_gap_seconds": max_source_gap_seconds,
        "market_log_audit_status": market_log_audit.get("status"),
        "fixture_mode": fixture_mode,
        "resumed_existing_output": bool(resume_existing_output),
        "resume_snapshot": resume_snapshot,
    }
    if market_profile_file is not None and market_profile_sha256 is not None:
        provenance["market_profile"] = {
            "path": str(market_profile_file),
            "sha256": market_profile_sha256,
        }
    result["provenance"] = provenance
    result_path = root / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


__all__ = [
    "CODE_PATHS",
    "STATE_FILES",
    "STREAM_PROCESS_SCHEMA_VERSION",
    "_require_resume_output",
    "run_stream_process_supervisor",
    "sha256_file",
]
