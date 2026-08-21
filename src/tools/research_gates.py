"""Create a reproducible crypto research run and evaluate its hard gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from research.experiment_manifest import (
    ACCOUNT_PROFILE_STAGES,
    CANONICAL_STAGES,
    EVENT_REQUIRED_STAGES,
    IMPLEMENTATION_FINGERPRINT_VERSION,
    LEGACY_STAGE_ALIASES,
    ROADMAP_STATUS_SCHEMA_V3,
    STAGE_PREREQUISITES,
    STAGE_PREREQUISITES_V3,
    _git_state,
    apply_stage_prerequisite_gate,
    build_experiment_manifest,
    evaluate_candidate_gates,
    normalize_stage_name,
    profile_input_metrics,
    replay_events_present,
    sha256_path,
    stage_requires_account_profile,
    stage_requires_market_profile,
    write_run_artifacts,
)
from research.risk_evidence import (
    RiskEvidenceError,
    validate_production_risk_evidence,
    validate_surface_matrix,
)
from research.runtime_evidence import runtime_gate_metrics, summarize_runtime_events
from research.strategy_shadow import canonical_hash, normalize_shadow_policy

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACKED_STAGES = CANONICAL_STAGES
RUN_INTEGRITY_FILE = "checksums.json"
INTEGRITY_CORE_FILES = (
    "manifest.json",
    "metrics.json",
    "verdict.json",
    "report.md",
    "events.jsonl.gz",
    "config.json",
)
INTEGRITY_PROFILE_FILES = ("market_profile.json", "account_profile.json")
# Kept for callers that imported the old constant.  The actual bundle list is
# selected per run because account_profile.json is present only for private
# stages.
INTEGRITY_TRACKED_FILES = INTEGRITY_CORE_FILES + ("market_profile.json",)


def _integrity_file_names(destination: Path, *, expected: dict[str, Any] | None = None) -> tuple[str, ...]:
    expected = expected or {}
    names = list(INTEGRITY_CORE_FILES)
    for profile_name in INTEGRITY_PROFILE_FILES:
        if (destination / profile_name).is_file() or profile_name in expected:
            names.append(profile_name)
    return tuple(names)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return value


SHADOW_EVIDENCE_SCHEMA_VERSION = "crypto-strategy-shadow-evidence-v1"
SHADOW_PROCESS_CODE_PATHS = (
    "src/research/strategy_shadow.py",
    "src/tools/strategy_shadow.py",
    "src/research/strategy_shadow_process.py",
    "src/tools/strategy_shadow_process.py",
    "src/research/strategy_shadow_stream.py",
    "src/tools/strategy_shadow_stream.py",
    "src/research/strategy_shadow_stream_process.py",
    "src/tools/strategy_shadow_stream_process.py",
)


def _shadow_process_code_hash(repo_root: Path) -> str | None:
    """Hash the fixed local shadow process sources for result binding."""

    files: dict[str, str] = {}
    try:
        for relative in SHADOW_PROCESS_CODE_PATHS:
            path = (repo_root / relative).resolve()
            if not path.is_file():
                return None
            files[relative] = sha256_path(path)
    except (OSError, ValueError):
        return None
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _load_metrics_for_stage(path: Path, *, stage: str) -> dict[str, Any]:
    """Load gate metrics, deriving shadow metrics from the machine result.

    A shadow run already produces the measured readiness values, provenance and
    local-only facts. Accepting that result directly prevents a caller from
    copying different numbers into a second, hand-maintained metrics file.
    Other stages keep the existing plain metrics-object contract.
    """

    raw = _read_json_object(path)
    if stage != "shadow_readiness" or "readiness_metrics" not in raw:
        return raw

    readiness = raw.get("readiness_metrics")
    if not isinstance(readiness, dict):
        raise ValueError("shadow result readiness_metrics must be an object")
    provenance = raw.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("shadow result provenance must be an object")
    deterministic_replay = raw.get("deterministic_replay")
    if not isinstance(deterministic_replay, dict):
        raise ValueError("shadow result deterministic_replay must be an object")

    artifacts = provenance.get("artifacts")
    artifact_hashes_match = isinstance(artifacts, Mapping) and bool(artifacts)
    if artifact_hashes_match:
        for details in artifacts.values():
            if not isinstance(details, Mapping):
                artifact_hashes_match = False
                break
            artifact_path_value = details.get("path")
            expected_hash = details.get("sha256")
            if not artifact_path_value or not expected_hash:
                artifact_hashes_match = False
                break
            artifact_path = Path(str(artifact_path_value)).expanduser()
            if not artifact_path.is_absolute():
                artifact_path = path.parent / artifact_path
            try:
                actual_hash = sha256_path(artifact_path)
            except (OSError, ValueError):
                artifact_hashes_match = False
                break
            if actual_hash != str(expected_hash):
                artifact_hashes_match = False
                break

    market_profile_binding = provenance.get("market_profile")
    market_profile_binding_present = isinstance(market_profile_binding, Mapping)
    market_profile_binding_sha256 = (
        market_profile_binding.get("sha256")
        if market_profile_binding_present
        else None
    )

    metrics = dict(readiness)
    process_supervisor = raw.get("process_supervisor")
    if not isinstance(process_supervisor, dict):
        process_supervisor = {}
    metrics.update(
        {
            "shadow_evidence_schema_version": raw.get("evidence_schema_version"),
            "shadow_evidence_status": raw.get("status"),
            "shadow_candidate_admission": raw.get("candidate_admission"),
            "shadow_read_only": raw.get("read_only"),
            "shadow_external_network_calls": raw.get("external_network_calls"),
            "shadow_private_requests": raw.get("private_requests"),
            "shadow_trading_operations_count": raw.get("trading_operations_count"),
            "shadow_public_write_calls": raw.get("public_write_calls"),
            "shadow_determinism_status": deterministic_replay.get("status"),
            "shadow_result_path": str(path),
            "shadow_result_sha256": sha256_path(path),
            "shadow_input_provenance_present": bool(provenance.get("input")),
            "shadow_artifact_provenance_present": bool(provenance.get("artifacts")),
            "shadow_artifact_hashes_match": artifact_hashes_match,
            "shadow_market_profile_binding_present": market_profile_binding_present,
            "shadow_market_profile_binding_sha256": market_profile_binding_sha256,
            "shadow_process_supervisor_status": process_supervisor.get("status"),
            "shadow_process_evidence_present": bool(process_supervisor),
        }
    )
    return metrics


def _resolve_surface_probe_path(
    raw_path: Any,
    *,
    metrics_path: Path,
    output_dir: Path,
    repo_root: Path,
) -> Path:
    """Resolve the matrix path without accepting a missing artifact."""

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RiskEvidenceError("surface_probe_path is missing")
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    candidates = (
        metrics_path.parent / candidate,
        metrics_path.parent.parent / candidate,
        output_dir.resolve().parent / candidate,
        repo_root / candidate,
    )
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return candidates[0].resolve()


def write_run_integrity_record(run_dir: str | Path, *, overwrite: bool = False) -> Path:
    """Write the independent hash anchor for a completed run bundle.

    The anchor is deliberately separate from ``manifest.json``: a manifest
    cannot prove its own integrity.  It contains only hashes and identity
    fields, and never changes the roadmap ``status.json``.
    """

    destination = Path(run_dir).expanduser().resolve()
    if not destination.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {destination}")
    manifest = _read_json_object(destination / "manifest.json")
    stage = normalize_stage_name(str(manifest.get("stage", "")))
    names = _integrity_file_names(destination)
    if stage_requires_market_profile(stage) and "market_profile.json" not in names:
        raise FileNotFoundError(f"stage {stage!r} requires market_profile.json: {destination}")
    if stage_requires_account_profile(stage) and "account_profile.json" not in names:
        raise FileNotFoundError(f"stage {stage!r} requires account_profile.json: {destination}")
    files: dict[str, dict[str, Any]] = {}
    for name in names:
        path = destination / name
        if not path.is_file():
            raise FileNotFoundError(f"run bundle is missing {name}: {destination}")
        files[name] = {
            "sha256": sha256_path(path),
            "size_bytes": path.stat().st_size,
        }

    record = {
        "schema_version": "crypto-grid-run-checksums-v1",
        "run_id": manifest.get("run_id"),
        "stage": manifest.get("stage"),
        "files": files,
    }
    anchor = destination / RUN_INTEGRITY_FILE
    if anchor.exists() and not overwrite:
        raise FileExistsError(f"integrity record already exists and is immutable: {anchor}")
    temporary = anchor.with_name(f".{anchor.name}.tmp")
    temporary.write_text(
        json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(anchor)
    return anchor


def verify_run_integrity(run_dir: str | Path) -> dict[str, Any]:
    """Verify a run bundle without consulting or changing ``status.json``."""

    destination = Path(run_dir).expanduser().resolve()
    issues: list[dict[str, Any]] = []
    actual_files: dict[str, str] = {}
    if not destination.is_dir():
        return {
            "status": "failed",
            "run_dir": str(destination),
            "anchor_present": False,
            "files": actual_files,
            "changed_files": [],
            "issues": [{"reason": "run_directory_missing"}],
        }

    anchor = destination / RUN_INTEGRITY_FILE
    if not anchor.is_file():
        issues.append({"file": RUN_INTEGRITY_FILE, "reason": "checksums_file_missing"})
        for name in _integrity_file_names(destination):
            path = destination / name
            if not path.is_file():
                issues.append({"file": name, "reason": "file_missing"})
            else:
                actual_files[name] = sha256_path(path)
        return {
            "status": "failed",
            "run_dir": str(destination),
            "anchor_present": False,
            "files": actual_files,
            "changed_files": [],
            "issues": issues,
        }

    try:
        record = _read_json_object(anchor)
    except ValueError as exc:
        issues.append({"file": RUN_INTEGRITY_FILE, "reason": "invalid_checksums", "detail": str(exc)})
        return {
            "status": "failed",
            "run_dir": str(destination),
            "anchor_present": True,
            "files": actual_files,
            "changed_files": [],
            "issues": issues,
        }

    if record.get("schema_version") != "crypto-grid-run-checksums-v1":
        issues.append({"file": RUN_INTEGRITY_FILE, "reason": "unsupported_checksums_schema"})
    expected_files = record.get("files")
    if not isinstance(expected_files, dict):
        issues.append({"file": RUN_INTEGRITY_FILE, "reason": "checksums_files_missing"})
        expected_files = {}

    names = _integrity_file_names(destination, expected=expected_files)
    manifest_for_requirements: dict[str, Any] | None = None
    manifest_path = destination / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest_for_requirements = _read_json_object(manifest_path)
            stage = normalize_stage_name(str(manifest_for_requirements.get("stage", "")))
        except (ValueError, TypeError) as exc:
            issues.append({"file": "manifest.json", "reason": "invalid_stage", "detail": str(exc)})
            stage = None
        if stage is not None:
            if stage_requires_market_profile(stage) and "market_profile.json" not in names:
                issues.append({"file": "market_profile.json", "reason": "required_profile_missing"})
            if stage_requires_account_profile(stage) and "account_profile.json" not in names:
                issues.append({"file": "account_profile.json", "reason": "required_profile_missing"})
    for name in names:
        path = destination / name
        if not path.is_file():
            issues.append({"file": name, "reason": "file_missing"})
        else:
            actual_files[name] = sha256_path(path)

    changed_files: list[str] = []
    for name in names:
        expected = expected_files.get(name)
        expected_hash = expected.get("sha256") if isinstance(expected, dict) else None
        actual_hash = actual_files.get(name)
        if expected_hash is None:
            issues.append({"file": name, "reason": "hash_missing_from_anchor"})
        elif actual_hash != expected_hash:
            changed_files.append(name)
            issues.append(
                {
                    "file": name,
                    "reason": "hash_mismatch",
                    "expected_sha256": expected_hash,
                    "actual_sha256": actual_hash,
                }
            )

    if manifest_path.is_file():
        try:
            manifest = _read_json_object(manifest_path)
        except ValueError as exc:
            issues.append({"file": "manifest.json", "reason": "invalid_manifest", "detail": str(exc)})
        else:
            if manifest.get("run_id") != record.get("run_id"):
                issues.append({"file": "manifest.json", "reason": "run_id_mismatch"})
            if manifest.get("stage") != record.get("stage"):
                issues.append({"file": "manifest.json", "reason": "stage_mismatch"})
            inputs = manifest.get("inputs")
            if not isinstance(inputs, dict):
                issues.append({"file": "manifest.json", "reason": "manifest_inputs_missing"})
            else:
                for name, manifest_key in (
                    ("config.json", "config_sha256"),
                    ("market_profile.json", "market_profile_sha256"),
                    ("account_profile.json", "account_profile_sha256"),
                ):
                    declared = inputs.get(manifest_key)
                    actual = actual_files.get(name)
                    if declared != actual and (declared is not None or name in actual_files):
                        issues.append(
                            {
                                "file": name,
                                "reason": "manifest_hash_mismatch",
                                "manifest_key": manifest_key,
                                "declared_sha256": declared,
                                "actual_sha256": actual,
                            }
                        )

    return {
        "status": "passed" if not issues else "failed",
        "run_dir": str(destination),
        "anchor_present": True,
        "files": actual_files,
        "changed_files": changed_files,
        "issues": issues,
    }


def _assert_existing_run_is_safe(run_dir: Path, *, overwrite: bool) -> None:
    """Reject reuse, and permit replacement only for an intact old bundle."""

    if not run_dir.exists() or not any(run_dir.iterdir()):
        return
    if not overwrite:
        raise FileExistsError(
            f"run directory already exists and is immutable: {run_dir}; "
            "use the explicit --overwrite/--safe-overwrite mode"
        )
    integrity = verify_run_integrity(run_dir)
    if integrity["status"] != "passed":
        raise ValueError(
            f"refusing to overwrite an unverified run bundle: {run_dir}; "
            f"issues={integrity['issues']}"
        )


def refresh_stale_status(
    status: dict[str, Any], *, runs_root: Path, repo_root: Path
) -> dict[str, Any]:
    """Mark old passed stages stale when their run no longer matches the tree.

    A historical ``passed`` result is useful evidence, but it must not silently
    become evidence for a changed checkout.  This function only derives status
    from the immutable run bundle and the current git state; it never upgrades a
    stage and never changes a verdict.
    """

    refreshed = json.loads(json.dumps(status))
    stages = refreshed.get("stages", {})
    if not isinstance(stages, dict):
        return refreshed
    current_commit, current_dirty, current_dirty_hash = _git_state(repo_root)
    status_schema = refreshed.get("schema_version")
    is_v3 = status_schema == ROADMAP_STATUS_SCHEMA_V3
    for stage_name, entry in stages.items():
        if not isinstance(entry, dict):
            continue
        run_id = entry.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            continue
        if not is_v3 and entry.get("status") != "passed":
            continue
        run_dir = (runs_root / run_id).resolve()
        reasons: list[str] = []
        integrity = verify_run_integrity(run_dir)
        if integrity.get("status") != "passed":
            reasons.append("run_checksums_invalid")
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.is_file():
            reasons.append("manifest_missing")
        else:
            try:
                manifest = _read_json_object(manifest_path)
            except ValueError:
                reasons.append("manifest_invalid")
            else:
                code = manifest.get("code")
                if not isinstance(code, dict):
                    reasons.append("manifest_code_missing")
                else:
                    implementation_version = code.get("implementation_fingerprint_version")
                    uses_stable_fingerprint = implementation_version == IMPLEMENTATION_FINGERPRINT_VERSION
                    if not uses_stable_fingerprint and code.get("git_commit") != current_commit:
                        reasons.append("git_commit_changed")
                    if bool(code.get("git_dirty")) != bool(current_dirty):
                        reasons.append("git_dirty_state_changed")
                    implementation_hash = code.get("implementation_fingerprint_sha256")
                    if not isinstance(implementation_version, str) or not implementation_version.strip():
                        reasons.append("implementation_fingerprint_missing")
                    if not isinstance(implementation_hash, str) or not implementation_hash.strip():
                        implementation_hash = code.get("dirty_diff_sha256")
                    if implementation_hash != current_dirty_hash:
                        reasons.append("dirty_tree_hash_changed")
        if reasons:
            if is_v3:
                entry.setdefault(
                    "outcome",
                    entry.get("stage_admission_status", entry.get("status", "stale")),
                )
                entry["freshness"] = "stale"
                entry["status"] = "stale"
            else:
                entry["status"] = "stale"
            entry["stale_reasons"] = reasons
            entry["stale_checked_against"] = {
                "git_commit": current_commit,
                "git_dirty": current_dirty,
                "dirty_diff_sha256": current_dirty_hash,
                "implementation_fingerprint_version": IMPLEMENTATION_FINGERPRINT_VERSION,
                "implementation_fingerprint_sha256": current_dirty_hash,
            }
        elif is_v3:
            entry["freshness"] = "fresh"
            entry["source_hash"] = current_dirty_hash
            entry["status"] = entry.get(
                "stage_admission_status",
                entry.get("outcome", entry.get("check_status", "not_started")),
            )
            entry.pop("stale_reasons", None)
            entry["freshness_checked_against"] = {
                "git_commit": current_commit,
                "git_dirty": current_dirty,
                "dirty_diff_sha256": current_dirty_hash,
                "implementation_fingerprint_version": IMPLEMENTATION_FINGERPRINT_VERSION,
                "implementation_fingerprint_sha256": current_dirty_hash,
            }
    # In v2 a dependency problem was represented by rewriting a passed result
    # to stale. In v3 preserve the local check result and change only the stage
    # admission projection. This is what lets a risk scenario remain passed
    # while its roadmap promotion is blocked by an upstream stage.
    changed = True
    while changed:
        changed = False
        for stage_name, entry in stages.items():
            if not isinstance(entry, dict):
                continue
            canonical_stage = normalize_stage_name(stage_name)
            prerequisites_map = STAGE_PREREQUISITES_V3 if is_v3 else STAGE_PREREQUISITES
            blockers = [
                prerequisite
                for prerequisite in prerequisites_map.get(canonical_stage, ())
                if isinstance(stages.get(prerequisite), dict)
                and (
                    stages[prerequisite].get("status")
                    in {"not_started", "running", "stale", "failed", "blocked"}
                    or stages[prerequisite].get("freshness") == "stale"
                    or stages[prerequisite].get("stage_admission_status")
                    in {"not_started", "running", "failed", "blocked", "stale"}
                )
            ]
            if blockers:
                if is_v3:
                    new_admission = "blocked"
                    if (
                        entry.get("stage_admission_status") != new_admission
                        or entry.get("outcome") != new_admission
                        or entry.get("status") != new_admission
                        or entry.get("admission_blockers") != blockers
                    ):
                        entry["stage_admission_status"] = new_admission
                        entry["outcome"] = new_admission
                        entry["status"] = new_admission
                        entry["admission_blockers"] = blockers
                        changed = True
                elif entry.get("status") == "passed":
                    entry["status"] = "stale"
                    entry["stale_reasons"] = [
                        "prerequisite_not_current:" + ",".join(blockers)
                    ]
                    changed = True
            elif is_v3:
                # A stage without a run has no check result to promote.  Older
                # status snapshots could leave a stale admission blocker on
                # such a stage after its prerequisites changed.  Keep the
                # local result fail-closed, but recompute the admission view
                # for both run-backed and not-started stages.
                if entry.get("run_id"):
                    desired = entry.get("check_status", entry.get("outcome", "not_started"))
                else:
                    # Migrated v3 entries normally have check_status.  For a
                    # hand-created/older v3 object, preserve its explicit
                    # admission/status value instead of silently downgrading
                    # a passed stage to not_started.
                    desired = (
                        entry["check_status"]
                        if "check_status" in entry
                        else entry.get(
                            "stage_admission_status",
                            entry.get("status", "not_started"),
                        )
                    )
                    if desired not in {"not_started", "running", "passed", "failed", "blocked", "stale"}:
                        desired = entry.get("status", "not_started")
                if entry.get("freshness") == "stale":
                    desired_status = "stale"
                else:
                    desired_status = desired
                if entry.get("stage_admission_status") != desired:
                    entry["stage_admission_status"] = desired
                    entry["outcome"] = desired
                    entry["status"] = desired_status
                    entry.pop("admission_blockers", None)
                    changed = True
    return refreshed


def migrate_status_stage_names(status: dict[str, Any]) -> dict[str, Any]:
    """Move legacy stage keys to the dated canonical names exactly once.

    Older snapshots used ``profile`` for the private account profile and
    ``paper`` for the paper runtime.  Keeping those keys beside the new names
    lets a later run accidentally read two different answers for one stage.
    The migration only copies an alias when its canonical destination is
    absent, then removes the alias and adds every currently known stage as
    ``not_started``.  It never turns a non-passed result into ``passed``.
    """

    migrated = json.loads(json.dumps(status))
    stages = migrated.setdefault("stages", {})
    if not isinstance(stages, dict):
        raise ValueError("status.stages must be an object")
    for legacy, canonical in LEGACY_STAGE_ALIASES.items():
        if legacy in stages:
            if canonical not in stages:
                stages[canonical] = stages[legacy]
            del stages[legacy]
    for stage_name in TRACKED_STAGES:
        entry = stages.setdefault(stage_name, {"status": "not_started"})
        if not isinstance(entry, dict):
            raise ValueError(f"status stage {stage_name!r} must be an object")
        raw_status = str(entry.get("status") or entry.get("outcome") or "not_started")
        if raw_status not in {"not_started", "running", "passed", "failed", "blocked", "stale"}:
            raw_status = "blocked"
        entry.setdefault("check_status", entry.get("outcome", raw_status))
        entry.setdefault("stage_admission_status", raw_status)
        entry.setdefault("outcome", raw_status)
        entry.setdefault(
            "freshness",
            "unknown" if entry.get("run_id") else "not_applicable",
        )
        if entry.get("run_id"):
            entry.setdefault("source_hash", None)
        entry["status"] = raw_status
    migrated["schema_version"] = ROADMAP_STATUS_SCHEMA_V3
    migrated.setdefault("plan_date", "2026-08-17")
    migrated.setdefault(
        "status_semantics",
        {
            "check_status": "result of the local checks in this run",
            "stage_admission_status": "promotion result after current prerequisites",
            "outcome": "stage_admission_status at the latest refresh",
            "freshness": "whether the run source matches the current tree",
        },
    )
    return migrated


@contextmanager
def _status_lock(status_path: Path):
    """Serialize status read/verify/write operations across model processes."""

    status_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = status_path.with_name(f"{status_path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_status(status_path: Path) -> dict[str, Any]:
    if not status_path.exists():
        return {}
    loaded_status = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(loaded_status, dict):
        raise ValueError(f"status file must contain an object: {status_path}")
    return migrate_status_stage_names(loaded_status)


def _write_status_atomic(status_path: Path, status: dict[str, Any]) -> None:
    """Replace status atomically so readers never see half-written JSON."""

    status_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{status_path.name}.", suffix=".tmp", dir=str(status_path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, status_path)
        directory_fd = os.open(str(status_path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def refresh_status_file(*, status_path: Path, runs_root: Path, repo_root: Path) -> dict[str, Any]:
    """Refresh machine freshness without creating a research run bundle."""

    if not status_path.is_file():
        raise FileNotFoundError(f"status file does not exist: {status_path}")
    with _status_lock(status_path):
        current = _read_status(status_path)
        refreshed = refresh_stale_status(current, runs_root=runs_root, repo_root=repo_root)
        if refreshed.get("schema_version") == ROADMAP_STATUS_SCHEMA_V3:
            refreshed["updated_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        _write_status_atomic(status_path, refreshed)
        return refreshed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Legacy stage aliases are migration-only: "
            "profile=account_profile and paper=paper_runtime. "
            "The profile alias is never a market_profile."
        ),
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--stage",
        required=False,
        choices=(*CANONICAL_STAGES, *LEGACY_STAGE_ALIASES),
    )
    parser.add_argument("--metrics", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument(
        "--market-profile",
        type=Path,
        help="public market profile; required by market-data and trading stages",
    )
    parser.add_argument(
        "--account-profile",
        type=Path,
        help="private read-only account profile; required only by technical/controlled live stages",
    )
    parser.add_argument(
        "--events",
        type=Path,
        help="JSONL or JSONL.gz event evidence to include in the immutable run bundle",
    )
    parser.add_argument(
        "--execution-inputs",
        type=Path,
        help="optional recorded funding/mark/index input file to hash in the manifest",
    )
    parser.add_argument(
        "--risk-evidence-manifest",
        type=Path,
        help=(
            "validated production-path risk evidence manifest; required for "
            "risk_engine/risk_contract admission"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "analysis_reports" / "crypto_bot_roadmap" / "runs",
    )
    parser.add_argument(
        "--overwrite",
        "--safe-overwrite",
        dest="overwrite",
        action="store_true",
        help="replace an existing bundle only when --scratch is also set",
    )
    parser.add_argument(
        "--scratch",
        action="store_true",
        help="mark this as a disposable scratch run; only scratch runs may be overwritten",
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--seed", type=int, action="append", default=[])
    parser.add_argument(
        "--freshness-only",
        action="store_true",
        help="refresh status.json freshness without creating a run bundle",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.freshness_only:
        if args.overwrite:
            parser.error("--freshness-only cannot be combined with --overwrite/--safe-overwrite")
        status_path = args.output_dir.expanduser().resolve().parent / "status.json"
        try:
            refreshed = refresh_status_file(
                status_path=status_path,
                runs_root=args.output_dir.expanduser().resolve(),
                repo_root=args.repo_root.expanduser().resolve(),
            )
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(
            json.dumps(
                {
                    "status_path": str(status_path),
                    "updated_at_utc": refreshed.get("updated_at_utc"),
                    "stages": {
                        name: {
                            "status": entry.get("status"),
                            "freshness": entry.get("freshness"),
                            "stale_reasons": entry.get("stale_reasons", []),
                        }
                        for name, entry in refreshed.get("stages", {}).items()
                        if isinstance(entry, dict)
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    required_args = {
        "--run-id": args.run_id,
        "--stage": args.stage,
        "--metrics": args.metrics,
        "--config": args.config,
        "--dataset": args.dataset,
    }
    missing_args = [name for name, value in required_args.items() if value is None]
    if missing_args:
        parser.error(f"the following arguments are required: {', '.join(missing_args)}")
    if args.overwrite and not args.scratch:
        parser.error("--overwrite/--safe-overwrite is allowed only together with --scratch")
    try:
        stage = normalize_stage_name(args.stage)
    except ValueError as exc:
        parser.error(str(exc))
    if stage_requires_market_profile(stage) and args.market_profile is None:
        parser.error(f"--stage {stage} requires --market-profile")
    if stage_requires_account_profile(stage) and args.account_profile is None:
        parser.error(f"--stage {stage} requires --account-profile")
    if args.account_profile is not None and stage not in ACCOUNT_PROFILE_STAGES:
        parser.error(
            f"--account-profile is not accepted for public/exploratory stage {stage}; "
            "use it only for account_profile or live stages"
        )
    if stage == "account_profile" and args.market_profile is not None:
        parser.error("account_profile uses --account-profile; it must not be masked as market_profile")
    metrics_path = args.metrics.expanduser().resolve()
    try:
        metrics = _load_metrics_for_stage(metrics_path, stage=stage)
    except ValueError as exc:
        parser.error(str(exc))
    metrics.update(
        profile_input_metrics(
            stage,
            market_profile_path=args.market_profile,
            account_profile_path=args.account_profile,
        )
    )
    risk_evidence_binding: dict[str, Any] | None = None
    if stage in {"risk_engine", "risk_contract"}:
        # These names are admission metrics, not caller assertions.  The
        # production validator below is the only path allowed to turn them
        # back to true.
        metrics["risk_engine_surface_validated"] = False
        metrics["live_tester_decisions_identical"] = False
        metrics["risk_limits_enforced_in_live_and_tester"] = False
        metrics["risk_parity_reached"] = False
        surface_probe_path: Path | None = None
        surface_input_sha256: str | None = None
        try:
            surface_probe_path = _resolve_surface_probe_path(
                metrics.get("surface_probe_path"),
                metrics_path=metrics_path,
                output_dir=args.output_dir.expanduser().resolve(),
                repo_root=args.repo_root.expanduser().resolve(),
            )
            surface_metrics = validate_surface_matrix(surface_probe_path)
            surface_input_sha256 = surface_metrics["sha256"]
            metrics.update(
                {
                    "risk_surface_matrix_status": surface_metrics["status"],
                    "risk_surface_matrix_validated": True,
                    "risk_surface_matrix_artifact_bound": True,
                    "risk_surface_matrix_source_mode": surface_metrics["source_mode"],
                    "risk_surface_matrix_fixture_mode": surface_metrics["fixture_mode"],
                    "risk_surface_matrix_sha256": surface_metrics["sha256"],
                    "risk_surface_matrix_scenario_count": surface_metrics["scenario_count"],
                    "risk_surface_matrix_passed_count": surface_metrics["passed_count"],
                    "risk_surface_matrix_failed_count": surface_metrics["failed_count"],
                    "risk_engine_surface_validated": True,
                }
            )
        except RiskEvidenceError as exc:
            metrics["risk_surface_matrix_status"] = "blocked"
            metrics["risk_surface_matrix_error"] = str(exc)

        if args.risk_evidence_manifest is None:
            metrics["risk_production_evidence_status"] = "missing"
            metrics["risk_production_evidence_error"] = (
                "--risk-evidence-manifest is required for production-path risk admission"
            )
        else:
            if args.market_profile is None:
                metrics["risk_production_evidence_status"] = "blocked"
                metrics["risk_production_evidence_error"] = (
                    "--market-profile is required with production risk evidence"
                )
                production_metrics = None
            else:
                try:
                    production_metrics = validate_production_risk_evidence(
                        args.risk_evidence_manifest.expanduser().resolve(),
                        repo_root=args.repo_root.expanduser().resolve(),
                        config_path=args.config.expanduser().resolve(),
                        market_profile_path=args.market_profile.expanduser().resolve(),
                    )
                except RiskEvidenceError as exc:
                    metrics["risk_production_evidence_status"] = "blocked"
                    metrics["risk_production_evidence_error"] = str(exc)
                    production_metrics = None
            if production_metrics is not None:
                metrics.update(production_metrics)
                risk_evidence_binding = {
                    "schema_version": "risk-parity-production-evidence-v1",
                    "manifest_path": production_metrics["manifest_path"],
                    "manifest_sha256": production_metrics["manifest_sha256"],
                }
                if (
                    surface_probe_path is not None
                    and surface_input_sha256 is not None
                    and production_metrics.get("risk_surface_matrix_sha256")
                    != surface_input_sha256
                ):
                    metrics["risk_surface_matrix_status"] = "blocked"
                    metrics["risk_surface_matrix_error"] = (
                        "surface matrix input differs from production evidence manifest"
                    )
                    metrics["risk_engine_surface_validated"] = False
                    metrics["risk_limits_enforced_in_live_and_tester"] = False
                    metrics["risk_parity_reached"] = False
                    metrics["live_tester_decisions_identical"] = False
                elif surface_probe_path is not None and surface_input_sha256 is None:
                    metrics["risk_surface_matrix_status"] = "blocked"
                    metrics["risk_surface_matrix_error"] = (
                        "surface matrix input could not be validated"
                    )
                    metrics["risk_engine_surface_validated"] = False
                    metrics["risk_limits_enforced_in_live_and_tester"] = False
                    metrics["risk_parity_reached"] = False
                    metrics["live_tester_decisions_identical"] = False
    if stage in EVENT_REQUIRED_STAGES:
        # Do not trust a caller-provided boolean here.  Event evidence is an
        # output of the run and must be present on disk before the gate can pass.
        events_present = replay_events_present(args.events)
        metrics["events_present"] = events_present
        if stage == "shadow_readiness":
            # The shadow result may contain its own measured event flag, but
            # readiness must also be bound to the immutable event input used
            # to create this gate bundle.
            metrics["shadow_events_input_present"] = events_present
            metrics["shadow_events_present"] = events_present
    if stage in {"paper_runtime", "portfolio_paper", "technical_live", "controlled_live"}:
        evidence = summarize_runtime_events(args.events) if args.events else {
            "status": "blocked",
            "errors": ["runtime event log is required"],
        }
        metrics["runtime_evidence"] = evidence
        metrics.update(runtime_gate_metrics(evidence))
    if stage == "shadow_readiness":
        result_metrics = metrics
        result_metrics["shadow_market_profile_contract_valid"] = bool(
            result_metrics.get("market_profile_contract_valid") is True
        )
        result_metrics["shadow_process_code_hash_matches_current"] = (
            result_metrics.get("shadow_process_code_hash")
            == _shadow_process_code_hash(args.repo_root.expanduser().resolve())
            and result_metrics.get("shadow_process_code_hash") is not None
        )
        try:
            result_metrics["shadow_process_config_hash_matches_config"] = (
                result_metrics.get("shadow_process_config_hash")
                == sha256_path(args.config)
            )
            config_object = _read_json_object(args.config)
            policy = config_object.get("shadow_policy")
            result_metrics["shadow_process_policy_config_hash_matches_config"] = (
                isinstance(policy, dict)
                and result_metrics.get("shadow_process_policy_config_hash")
                == canonical_hash(normalize_shadow_policy(policy))
            )
            result_metrics["shadow_process_input_hash_matches_dataset"] = (
                result_metrics.get("shadow_process_input_hash")
                == sha256_path(args.dataset)
            )
            result_metrics["shadow_market_profile_binding_matches_input"] = (
                result_metrics.get("shadow_market_profile_binding_sha256")
                == sha256_path(args.market_profile)
                and result_metrics.get("shadow_market_profile_binding_sha256") is not None
            )
        except (OSError, ValueError):
            result_metrics["shadow_process_config_hash_matches_config"] = False
            result_metrics["shadow_process_input_hash_matches_dataset"] = False
            result_metrics["shadow_market_profile_binding_matches_input"] = False
    status_path = args.output_dir.expanduser().resolve().parent / "status.json"
    manifest = build_experiment_manifest(
        repo_root=args.repo_root,
        run_id=args.run_id,
        stage=stage,
        config_path=args.config,
        dataset_path=args.dataset,
        market_profile_path=args.market_profile,
        account_profile_path=args.account_profile,
        command=["passivbot", "tool", "research-gates", *(argv or sys.argv[1:])],
        random_seeds=args.seed,
        execution_inputs_path=args.execution_inputs,
        risk_evidence=risk_evidence_binding,
    )
    with _status_lock(status_path):
        current_status = refresh_stale_status(
            _read_status(status_path),
            runs_root=args.output_dir.expanduser().resolve(),
            repo_root=args.repo_root.expanduser().resolve(),
        )
        check_verdict = evaluate_candidate_gates(metrics, stage=stage)
        verdict = apply_stage_prerequisite_gate(
            check_verdict,
            current_status,
            stage=stage,
        )
        run_dir = args.output_dir.expanduser().resolve() / args.run_id
        _assert_existing_run_is_safe(run_dir, overwrite=args.overwrite)
        write_run_artifacts(
            run_dir=run_dir,
            manifest=manifest,
            metrics=metrics,
            verdict=verdict,
            config_path=args.config,
            market_profile_path=args.market_profile,
            account_profile_path=args.account_profile,
            events_path=args.events,
            overwrite=args.overwrite,
        )
        write_run_integrity_record(run_dir, overwrite=args.overwrite)
        # ``output_dir`` is the run root (``.../runs``); the roadmap status is a
        # sibling so one file describes all runs and stages.
        status: dict = migrate_status_stage_names(current_status)
        status["schema_version"] = ROADMAP_STATUS_SCHEMA_V3
        status["plan_date"] = "2026-08-17"
        status["updated_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        stages = status.setdefault("stages", {})
        stages[stage] = {
            "status": verdict["status"],
            "check_status": check_verdict["status"],
            "stage_admission_status": verdict["status"],
            "outcome": verdict["status"],
            "freshness": "fresh",
            "source_hash": manifest.dirty_diff_sha256,
            "source": {
                "git_commit": manifest.git_commit,
                "git_dirty": manifest.git_dirty,
                "dirty_diff_sha256": manifest.dirty_diff_sha256,
                "implementation_fingerprint_version": manifest.implementation_fingerprint_version,
                "implementation_fingerprint_sha256": manifest.implementation_fingerprint_sha256,
            },
            "run_id": args.run_id,
            "verdict_path": str((run_dir / "verdict.json").relative_to(args.output_dir.resolve().parent)),
        }
        _write_status_atomic(status_path, status)
    print(json.dumps({"run_dir": str(run_dir), "status": verdict["status"]}, sort_keys=True))
    return 0 if verdict["status"] == "passed" else 1 if verdict["status"] == "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
