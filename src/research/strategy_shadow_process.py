"""Run a local strategy-shadow session across real Python processes.

This module is deliberately a supervisor for the existing local shadow CLI.
It never creates an exchange client itself and never has a private or trading
API path.  Every child receives only a bounded chunk of saved snapshots and
the same local paper-state directory.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from research.strategy_shadow import canonical_hash, read_shadow_events, run_strategy_shadow


UTC = timezone.utc
CODE_PATHS = (
    Path("src/research/strategy_shadow.py"),
    Path("src/tools/strategy_shadow.py"),
    Path("src/research/strategy_shadow_process.py"),
    Path("src/tools/strategy_shadow_process.py"),
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


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp), tz=UTC).isoformat()


def _reject_credentials(value: Any, *, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).lower().replace("-", "_")
            if any(part in key_text for part in FORBIDDEN_KEY_PARTS):
                raise ValueError(f"credential-like field is forbidden: {path}.{key}")
            _reject_credentials(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_credentials(nested, path=f"{path}[{index}]")


def load_snapshots(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    if target.suffix.lower() in {".jsonl", ".ndjson"}:
        rows: list[dict[str, Any]] = []
        with target.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"snapshot line {line_number} must be an object")
                rows.append(value)
    else:
        value = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(value, Mapping) and isinstance(value.get("snapshots"), list):
            value = value["snapshots"]
        if not isinstance(value, list):
            raise ValueError("snapshot input must be a JSON list or an object with snapshots")
        rows = [row for row in value if isinstance(row, dict)]
        if len(rows) != len(value):
            raise ValueError("every snapshot must be an object")
    if not rows:
        raise ValueError("strategy shadow process session requires snapshots")
    _reject_credentials(rows, path="snapshots")
    return rows


def load_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("shadow process config must be an object")
    _reject_credentials(value, path="config")
    allowed = {"strategy_id", "order_amount", "entry_offset_bps", "exit_offset_bps"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unsupported shadow process config fields: " + ", ".join(unknown))
    result = {
        "strategy_id": str(value.get("strategy_id", "crypto-grid-shadow-v1")),
        "order_amount": float(value.get("order_amount", 0.001)),
        "entry_offset_bps": float(value.get("entry_offset_bps", 0.0)),
        "exit_offset_bps": float(value.get("exit_offset_bps", 0.0)),
    }
    if not result["strategy_id"].strip():
        raise ValueError("strategy_id must not be empty")
    for key in ("order_amount", "entry_offset_bps", "exit_offset_bps"):
        if not math.isfinite(result[key]):
            raise ValueError(f"{key} must be finite")
    if result["order_amount"] <= 0.0:
        raise ValueError("order_amount must be positive")
    return result


def _split_chunks(rows: Sequence[dict[str, Any]], process_count: int) -> list[list[dict[str, Any]]]:
    if process_count < 1:
        raise ValueError("process_count must be positive")
    if len(rows) < process_count:
        raise ValueError("at least one snapshot is required per child process")
    chunks: list[list[dict[str, Any]]] = []
    base, remainder = divmod(len(rows), process_count)
    cursor = 0
    for index in range(process_count):
        size = base + (1 if index < remainder else 0)
        chunks.append(list(rows[cursor : cursor + size]))
        cursor += size
    return chunks


def _state_hash(root: Path) -> tuple[str | None, bool]:
    files = {}
    for name in ("checkpoint.json", "events.ndjson", "paper_ledger.json"):
        path = root / name
        if not path.is_file():
            return None, False
        files[name] = sha256_file(path)
    return canonical_hash(files), True


def _duplicate_state_count(root: Path) -> int:
    events_path = root / "events.ndjson"
    if not events_path.is_file():
        return 0
    events = read_shadow_events(events_path)
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


def _code_hash(repo_root: Path) -> tuple[str, dict[str, str]]:
    files: dict[str, str] = {}
    for relative in CODE_PATHS:
        path = repo_root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        files[relative.as_posix()] = sha256_file(path)
    return canonical_hash(files), files


def run_process_supervisor(
    *,
    input_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    config_path: str | os.PathLike[str],
    restart_count: int = 5,
    deterministic_replay_runs: int = 3,
    python_executable: str | os.PathLike[str] | None = None,
    repo_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Run the saved-snapshot shadow through separate child processes."""

    if isinstance(restart_count, bool) or int(restart_count) < 1:
        raise ValueError("restart_count must be positive")
    if isinstance(deterministic_replay_runs, bool) or int(deterministic_replay_runs) < 2:
        raise ValueError("deterministic_replay_runs must be at least two")
    input_file = Path(input_path).expanduser().resolve()
    config_file = Path(config_path).expanduser().resolve()
    root = Path(output_dir).expanduser().resolve()
    source_root = Path(repo_root).expanduser().resolve() if repo_root else Path(__file__).resolve().parents[2]
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"output directory must be new and empty: {root}")
    root.mkdir(parents=True, exist_ok=False)
    rows = load_snapshots(input_file)
    config = load_config(config_file)
    process_count = int(restart_count) + 1
    chunks = _split_chunks(rows, process_count)
    input_sha256 = sha256_file(input_file)
    config_sha256 = sha256_file(config_file)
    code_sha256, code_files = _code_hash(source_root)
    child_python = str(python_executable or sys.executable)
    chunks_dir = root / "chunks"
    chunks_dir.mkdir()
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    source_path = str(source_root / "src")
    env["PYTHONPATH"] = source_path + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

    started_wall = time.time()
    started_perf = time.perf_counter()
    processes: list[dict[str, Any]] = []
    child_results: list[dict[str, Any]] = []
    process_errors: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_path = chunks_dir / f"chunk-{index:03d}.json"
        chunk_path.write_text(
            json.dumps(chunk, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        child_started_wall = time.time()
        command = [
            child_python,
            "-m",
            "tools.strategy_shadow",
            "--input",
            str(chunk_path),
            "--output-dir",
            str(root),
            "--strategy-id",
            config["strategy_id"],
            "--order-amount",
            str(config["order_amount"]),
            "--entry-offset-bps",
            str(config["entry_offset_bps"]),
            "--exit-offset-bps",
            str(config["exit_offset_bps"]),
            # A child resumes cumulative paper state.  Its local replay must
            # not be compared with that cumulative state; the supervisor runs
            # one complete deterministic replay after all process chunks.
            "--deterministic-replay-runs",
            "1",
        ]
        process = subprocess.Popen(
            command,
            cwd=str(source_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate()
        child_ended_wall = time.time()
        state_hash, state_present = _state_hash(root)
        process_record = {
            "process_index": index,
            "pid": int(process.pid),
            "return_code": process.returncode,
            "started_at_utc": _utc_iso(child_started_wall),
            "ended_at_utc": _utc_iso(child_ended_wall),
            "elapsed_seconds": max(0.0, child_ended_wall - child_started_wall),
            "chunk_path": str(chunk_path),
            "chunk_sha256": sha256_file(chunk_path),
            "state_hash_after": state_hash,
            "state_files_present": state_present,
            "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        }
        processes.append(process_record)
        result_path = root / "result.json"
        if result_path.is_file():
            try:
                child_result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                process_errors.append(f"child result is unreadable after process {index}: {exc}")
                child_result = {}
        else:
            process_errors.append(f"child result is missing after process {index}")
            child_result = {}
        if not isinstance(child_result, dict):
            process_errors.append(f"child result is not an object after process {index}")
            child_result = {}
        child_results.append(child_result)
        if process.returncode != 0:
            process_errors.append(f"child process {index} exited with {process.returncode}")

    ended_wall = time.time()
    ended_perf = time.perf_counter()
    elapsed_seconds = max(0.0, ended_perf - started_perf)
    restart_operations: list[dict[str, Any]] = []
    for previous, current in zip(processes, processes[1:]):
        previous_end = datetime.fromisoformat(previous["ended_at_utc"]).timestamp()
        current_end = datetime.fromisoformat(current["ended_at_utc"]).timestamp()
        recovery_seconds = max(0.0, current_end - previous_end)
        restart_operations.append(
            {
                "restart_id": len(restart_operations) + 1,
                "old_pid": previous["pid"],
                "new_pid": current["pid"],
                "old_return_code": previous["return_code"],
                "new_return_code": current["return_code"],
                "stopped_at_utc": previous["ended_at_utc"],
                "started_at_utc": current["started_at_utc"],
                "recovered_at_utc": current["ended_at_utc"],
                "recovery_seconds": recovery_seconds,
                "state_hash_before": previous["state_hash_after"],
                "state_hash_after": current["state_hash_after"],
                "state_files_present": bool(
                    previous["state_files_present"] and current["state_files_present"]
                ),
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
    final_child_result = child_results[-1] if child_results else {}
    replay_root = root / "deterministic_full_replay"
    full_replay = asyncio.run(
        run_strategy_shadow(
            rows,
            output_dir=replay_root,
            config=config,
            deterministic_replay_runs=int(deterministic_replay_runs),
            input_provenance={
                "source_kind": "local_saved_snapshot_file",
                "source_path": str(input_file),
                "source_sha256": input_sha256,
                "format": input_file.suffix.lower().lstrip(".") or "json",
            },
        )
    )
    if full_replay.get("deterministic_replay", {}).get("status") != "passed":
        process_errors.append("full deterministic replay did not pass")
    readiness_metrics = dict(final_child_result.get("readiness_metrics", {}))
    readiness_metrics.update(
        {
            # Use a monotonic clock for elapsed time.  The ISO timestamps are
            # retained for auditability, but a wall-clock adjustment must not
            # make a long run appear shorter or longer than it was.
            "shadow_wall_clock_duration_seconds": elapsed_seconds,
            "shadow_process_restart_status": restart_status,
            "shadow_process_restart_count": len(restart_operations),
            "shadow_process_max_recovery_seconds": max_recovery,
            "shadow_process_code_hash": code_sha256,
            "shadow_process_config_hash": config_sha256,
            "shadow_process_input_hash": input_sha256,
            "shadow_process_duplicate_state_count": duplicate_state_count,
        }
    )
    readiness_metrics["shadow_determinism_runs"] = int(
        full_replay.get("deterministic_replay_runs", 0)
    )
    supervisor = {
        "schema_version": "crypto-strategy-shadow-process-v1",
        "status": restart_status,
        "requested_restart_count": int(restart_count),
        "observed_restart_count": len(restart_operations),
        "started_at_utc": _utc_iso(started_wall),
        "ended_at_utc": _utc_iso(ended_wall),
        "wall_clock_duration_seconds": elapsed_seconds,
        "input_path": str(input_file),
        "input_sha256": input_sha256,
        "config_path": str(config_file),
        "config_sha256": config_sha256,
        "code_hash": code_sha256,
        "code_files": code_files,
        "processes": processes,
        "restart_operations": restart_operations,
        "duplicate_state_count": duplicate_state_count,
        "errors": process_errors,
    }
    supervisor_path = root / "process_supervisor.json"
    supervisor_path.write_text(
        json.dumps(supervisor, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    result = dict(final_child_result)
    result["status"] = "passed" if result.get("status") == "passed" and restart_status == "passed" and not process_errors else "failed"
    result["deterministic_replay"] = full_replay.get("deterministic_replay")
    result["deterministic_replay_hash"] = full_replay.get("deterministic_replay_hash")
    result["deterministic_replay_runs"] = full_replay.get("deterministic_replay_runs", 0)
    result["readiness_metrics"] = readiness_metrics
    result["process_supervisor"] = supervisor
    provenance = dict(result.get("provenance", {}))
    artifacts = dict(provenance.get("artifacts", {}))
    artifacts["process_supervisor"] = {
        "exists": True,
        "path": str(supervisor_path),
        "sha256": sha256_file(supervisor_path),
    }
    provenance["artifacts"] = artifacts
    provenance["process_supervisor"] = {
        "code_hash": code_sha256,
        "config_sha256": config_sha256,
        "input_sha256": input_sha256,
        "wall_clock_start_utc": _utc_iso(started_wall),
        "wall_clock_end_utc": _utc_iso(ended_wall),
    }
    result["provenance"] = provenance
    result["evidence_schema_version"] = "crypto-strategy-shadow-evidence-v1"
    result_path = root / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


__all__ = [
    "CODE_PATHS",
    "load_config",
    "load_snapshots",
    "run_process_supervisor",
    "sha256_file",
]
