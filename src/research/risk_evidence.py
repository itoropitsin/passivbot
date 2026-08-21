"""Fail-closed validation for production-path crypto risk evidence.

The research surface matrix is intentionally a fixture/research-policy
regression.  It is useful evidence, but it is not proof that the live
``Passivbot`` filter and the Rust ``Backtest`` gate were both called.  This
module keeps that distinction machine-checkable and requires one immutable
manifest to bind the actual probe, snapshot, config, public market profile,
implementation fingerprint, and report.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research.experiment_manifest import (
    IMPLEMENTATION_FINGERPRINT_VERSION,
    _git_state,
    sha256_path,
)
from research.risk_parity import normalize_snapshot, snapshot_hash
from research.risk_trace import compare_live_export_with_rust_trace_records

PRODUCTION_RISK_EVIDENCE_SCHEMA = "risk-parity-production-evidence-v1"
ACTUAL_PROBE_SCHEMA = "risk-parity-actual-probe-v1"
SURFACE_PROBE_SCHEMA = "risk-engine-surface-probe-v1"
INVOCATION_SCHEMA = "risk-parity-production-invocation-v1"
SURFACE_MATRIX_SOURCE_MODE = "research_policy"
ACTUAL_ENGINE_SOURCE_MODE = "actual_engine_trace_set"
PRODUCTION_PYTHON_GATE_METHOD = "Passivbot._apply_crypto_portfolio_risk_limits"
PRODUCTION_PYTHON_LIVE_GATE_ENGINE = "python-live-gate"
PRODUCTION_RUST_TRACE_SOURCE = "Rust Backtest risk_gate_trace"
INVOCATION_SOURCE = "src/tools/risk_parity_actual_probe.py"
REQUIRED_SURFACE_SCENARIO_COUNT = 14
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PRODUCTION_CHECKSUM_FILES = (
    "manifest.json",
    "invocation.json",
    "config.json",
    "market_profile.json",
    "snapshot.json",
    "live_export.json",
    "rust_trace.json",
    "comparison.json",
    "report.json",
    "surface_probe.json",
)


class RiskEvidenceError(ValueError):
    """Raised when risk evidence is absent, stale, or not production-path."""


def _read_json(path: str | Path, *, label: str) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RiskEvidenceError(f"{label} is unreadable: {target}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise RiskEvidenceError(f"{label} must contain a JSON object: {target}")
    return dict(value)


def _read_json_list(path: str | Path, *, label: str) -> list[dict[str, Any]]:
    target = Path(path).expanduser().resolve()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RiskEvidenceError(f"{label} is unreadable: {target}: {exc}") from exc
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise RiskEvidenceError(f"{label} must contain a JSON object list: {target}")
    return [dict(item) for item in value]


def _required_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RiskEvidenceError(f"{field} must be an object")
    return dict(value)


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RiskEvidenceError(f"{field} must be a non-empty string")
    return value.strip()


def _required_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise RiskEvidenceError(f"{field} must be boolean")
    return value


def _required_hash(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise RiskEvidenceError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _file_hash(path: str | Path, *, field: str) -> str:
    try:
        return sha256_path(path)
    except (OSError, ValueError) as exc:
        raise RiskEvidenceError(
            f"{field} artifact is unavailable: {path}: {exc}"
        ) from exc


def _resolve_relative(root: Path, raw_path: Any, *, field: str) -> Path:
    value = _required_text(raw_path, field=field)
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (root / candidate).resolve()


def _relative_artifact_path(root: Path, path: Path, *, field: str) -> str:
    resolved = path.expanduser().resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise RiskEvidenceError(
            f"{field} must be stored inside the evidence bundle: {resolved}"
        ) from exc
    if not relative.parts or ".." in relative.parts:
        raise RiskEvidenceError(f"{field} has an unsafe relative path")
    return relative.as_posix()


def _checksum_value(value: Any, *, field: str) -> str:
    if isinstance(value, Mapping):
        value = value.get("sha256")
    return _required_hash(value, field=field)


def _verify_checksums(
    root: Path,
    *,
    required_files: tuple[str, ...],
    label: str,
) -> dict[str, str]:
    checksums = _read_json(root / "checksums.json", label=f"{label}.checksums")
    observed: dict[str, str] = {}
    for raw_name, raw_hash in checksums.items():
        name = _required_text(raw_name, field=f"{label}.checksums.file")
        if name == "checksums.json":
            continue
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RiskEvidenceError(
                f"{label}.checksums contains an unsafe path: {name}"
            )
        path = (root / relative).resolve()
        try:
            canonical_name = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise RiskEvidenceError(
                f"{label}.checksums contains an unsafe path: {name}"
            ) from exc
        if canonical_name != name:
            raise RiskEvidenceError(f"{label}.checksums path is not canonical: {name}")
        digest = _file_hash(path, field=f"{label}.checksums.{name}")
        declared = _checksum_value(raw_hash, field=f"{label}.checksums.{name}")
        if digest != declared:
            raise RiskEvidenceError(
                f"{label} checksum mismatch for {name}: {digest} != {declared}"
            )
        observed[name] = digest
    missing = [name for name in required_files if name not in observed]
    if missing:
        raise RiskEvidenceError(
            f"{label}.checksums is missing required artifacts: {', '.join(missing)}"
        )
    return observed


def _validate_engine_outputs(
    *,
    snapshot_path: Path,
    live_export_path: Path,
    rust_trace_path: Path,
    comparison_path: Path,
    report_path: Path,
    label: str,
) -> dict[str, Any]:
    snapshot_raw = _read_json(snapshot_path, label=f"{label}.snapshot")
    try:
        snapshot = normalize_snapshot(snapshot_raw)
    except (TypeError, ValueError) as exc:
        raise RiskEvidenceError(f"{label}.snapshot is invalid: {exc}") from exc
    digest = snapshot_hash(snapshot)

    live_export = _read_json(live_export_path, label=f"{label}.live_export")
    if live_export.get("status") != "passed":
        raise RiskEvidenceError(f"{label}.live_export is not passed")
    decisions = live_export.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise RiskEvidenceError(f"{label}.live_export has no decisions")
    for index, decision in enumerate(decisions):
        if not isinstance(decision, Mapping):
            raise RiskEvidenceError(
                f"{label}.live_export decision {index} is not an object"
            )
        if decision.get("engine") != PRODUCTION_PYTHON_LIVE_GATE_ENGINE:
            raise RiskEvidenceError(
                f"{label}.live_export decision {index} is not from "
                f"{PRODUCTION_PYTHON_LIVE_GATE_ENGINE}"
            )
    rust_trace = _read_json_list(rust_trace_path, label=f"{label}.rust_trace")
    if not rust_trace:
        raise RiskEvidenceError(f"{label}.rust_trace is empty")
    for index, record in enumerate(rust_trace):
        if not isinstance(record.get("step"), int):
            raise RiskEvidenceError(
                f"{label}.rust_trace record {index} is missing Backtest step"
            )
        if not isinstance(record.get("snapshot"), Mapping):
            raise RiskEvidenceError(
                f"{label}.rust_trace record {index} is missing snapshot"
            )
    comparison = _read_json(comparison_path, label=f"{label}.comparison")
    report = _read_json(report_path, label=f"{label}.report")

    if comparison.get("status", comparison.get("parity")) not in {"passed", None}:
        raise RiskEvidenceError(f"{label}.comparison is not passed")
    if comparison.get("parity") != "passed":
        raise RiskEvidenceError(f"{label}.comparison parity is not passed")
    if comparison.get("candidate_admission") is not False:
        raise RiskEvidenceError(f"{label}.comparison admits a candidate")
    if comparison.get("trading_operations_count") != 0:
        raise RiskEvidenceError(f"{label}.comparison records trading operations")
    if report.get("status") != "passed":
        raise RiskEvidenceError(f"{label}.report is not passed")
    if report.get("candidate_admission") is not False:
        raise RiskEvidenceError(f"{label}.report admits a candidate")
    if report.get("trading_operations_count") != 0:
        raise RiskEvidenceError(f"{label}.report records trading operations")
    if report.get("snapshot_hash") != digest:
        raise RiskEvidenceError(f"{label}.report is bound to another snapshot")
    report_comparison = report.get("comparison")
    if (
        isinstance(report_comparison, Mapping)
        and report_comparison.get("parity") != "passed"
    ):
        raise RiskEvidenceError(f"{label}.report comparison is not passed")

    try:
        actual_comparison = compare_live_export_with_rust_trace_records(
            live_export, rust_trace
        )
    except (TypeError, ValueError) as exc:
        raise RiskEvidenceError(
            f"{label} engine outputs cannot be compared: {exc}"
        ) from exc
    if actual_comparison.get("parity") != "passed":
        raise RiskEvidenceError(f"{label} engine outputs do not match")
    if actual_comparison.get("candidate_admission") is not False:
        raise RiskEvidenceError(f"{label} engine comparison admits a candidate")
    if actual_comparison.get("trading_operations_count") != 0:
        raise RiskEvidenceError(f"{label} engine comparison records trading operations")

    return {
        "snapshot": snapshot,
        "snapshot_hash": digest,
        "trace_record_count": len(rust_trace),
        "comparison": comparison,
        "report": report,
        "live_export": live_export,
        "rust_trace": rust_trace,
    }


def validate_surface_matrix(
    path: str | Path,
    *,
    expected_scenario_count: int = REQUIRED_SURFACE_SCENARIO_COUNT,
) -> dict[str, Any]:
    """Validate the research matrix without treating it as production evidence."""

    target = Path(path).expanduser().resolve()
    payload = _read_json(target, label="risk surface matrix")
    if payload.get("schema_version") != SURFACE_PROBE_SCHEMA:
        raise RiskEvidenceError("risk surface matrix schema is unsupported")
    if payload.get("source_mode") != SURFACE_MATRIX_SOURCE_MODE:
        raise RiskEvidenceError("risk surface matrix is not marked research_policy")
    if payload.get("fixture_mode") is not True:
        raise RiskEvidenceError(
            "risk surface matrix must be explicitly marked fixture_mode"
        )
    if payload.get("status") != "passed":
        raise RiskEvidenceError("risk surface matrix is not passed")
    if payload.get("read_only") is not True:
        raise RiskEvidenceError("risk surface matrix is not read-only")
    if payload.get("candidate_admission") is not False:
        raise RiskEvidenceError("risk surface matrix admits a candidate")
    if payload.get("trading_operations_count") != 0:
        raise RiskEvidenceError("risk surface matrix records trading operations")

    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != expected_scenario_count:
        raise RiskEvidenceError(
            "risk surface matrix is incomplete: "
            f"expected {expected_scenario_count} scenarios"
        )
    names: set[str] = set()
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, Mapping):
            raise RiskEvidenceError(f"risk surface scenario {index} is not an object")
        name = _required_text(
            scenario.get("name"), field=f"risk surface scenario {index}.name"
        )
        if name in names:
            raise RiskEvidenceError(f"risk surface matrix duplicates scenario {name!r}")
        names.add(name)
        if scenario.get("status") != "passed":
            raise RiskEvidenceError(f"risk surface scenario {name!r} is not passed")
        if scenario.get("candidate_admission") not in (None, False):
            raise RiskEvidenceError(
                f"risk surface scenario {name!r} admits a candidate"
            )
        if scenario.get("trading_operations_count") != 0:
            raise RiskEvidenceError(
                f"risk surface scenario {name!r} records trading operations"
            )

    passed_count = payload.get("passed_count")
    if passed_count != expected_scenario_count or payload.get("failed_count") != 0:
        raise RiskEvidenceError("risk surface matrix counts are incomplete")
    return {
        "status": "passed",
        "path": str(target),
        "sha256": _file_hash(target, field="risk surface matrix"),
        "source_mode": SURFACE_MATRIX_SOURCE_MODE,
        "fixture_mode": True,
        "scenario_count": expected_scenario_count,
        "passed_count": expected_scenario_count,
        "failed_count": 0,
    }


def validate_actual_probe_regression(path: str | Path) -> dict[str, Any]:
    """Validate the historical actual probe as a required regression only.

    This accepts the v1 artifact because it predates the production-evidence
    manifest.  It deliberately returns no production admission boolean.
    """

    root = Path(path).expanduser().resolve()
    manifest = _read_json(
        root / "manifest.json", label="actual probe regression manifest"
    )
    if manifest.get("schema_version") != ACTUAL_PROBE_SCHEMA:
        raise RiskEvidenceError("actual probe regression schema is unsupported")
    if manifest.get("source_mode") != ACTUAL_ENGINE_SOURCE_MODE:
        raise RiskEvidenceError(
            "actual probe regression is not an actual engine trace set"
        )
    if manifest.get("engine") not in {
        "Rust Backtest + Passivbot Python live gate",
    }:
        raise RiskEvidenceError("actual probe regression engine is not production-path")
    if manifest.get("read_only") is not True:
        raise RiskEvidenceError("actual probe regression is not read-only")
    if manifest.get("trading_operations_count") != 0:
        raise RiskEvidenceError("actual probe regression records trading operations")
    files = _required_mapping(
        manifest.get("files"), field="actual probe regression.files"
    )
    required_names = ("snapshot", "live_export", "rust_trace", "comparison")
    paths: dict[str, Path] = {}
    for logical_name in required_names:
        filename = _required_text(
            files.get(logical_name),
            field=f"actual probe regression.files.{logical_name}",
        )
        paths[logical_name] = (root / filename).resolve()
    required_files = tuple(
        _relative_artifact_path(
            root,
            paths[name],
            field=f"actual probe regression.files.{name}",
        )
        for name in required_names
    )
    checksums = _verify_checksums(
        root,
        required_files=required_files,
        label="actual probe regression",
    )
    report_path = root / "report.json"
    if not report_path.is_file():
        raise RiskEvidenceError("actual probe regression report.json is missing")
    outputs = _validate_engine_outputs(
        snapshot_path=paths["snapshot"],
        live_export_path=paths["live_export"],
        rust_trace_path=paths["rust_trace"],
        comparison_path=paths["comparison"],
        report_path=report_path,
        label="actual probe regression",
    )
    expected_count = manifest.get("trace_record_count")
    if expected_count != outputs["trace_record_count"]:
        raise RiskEvidenceError("actual probe regression trace count is not bound")
    if manifest.get("snapshot_hash") != outputs["snapshot_hash"]:
        raise RiskEvidenceError("actual probe regression snapshot hash is not bound")
    return {
        "status": "passed",
        "path": str(root),
        "manifest_sha256": _file_hash(
            root / "manifest.json", field="actual probe regression manifest"
        ),
        "report_sha256": _file_hash(
            report_path, field="actual probe regression report"
        ),
        "snapshot_hash": outputs["snapshot_hash"],
        "trace_record_count": outputs["trace_record_count"],
        "checksums": checksums,
    }


def _artifact_binding(
    root: Path, path: Path, *, field: str, canonical_hash: str | None = None
) -> dict[str, Any]:
    return {
        "path": _relative_artifact_path(root, path, field=field),
        "sha256": _file_hash(path, field=field),
        **({"canonical_hash": canonical_hash} if canonical_hash is not None else {}),
    }


def write_production_invocation(
    path: str | Path,
    *,
    live_export_sha256: str,
    rust_trace_sha256: str,
    snapshot_hash: str,
    trace_record_count: int,
) -> dict[str, Any]:
    """Record that this bundle's engines were invoked in-process.

    The probe is the only production writer.  Copied engine artifacts without
    this file cannot be turned into admission metrics.
    """

    if not isinstance(trace_record_count, int) or trace_record_count <= 0:
        raise RiskEvidenceError(
            "invocation.trace_record_count must be a positive integer"
        )
    payload = {
        "schema_version": INVOCATION_SCHEMA,
        "invoked_by": INVOCATION_SOURCE,
        "python_live_gate_called": True,
        "python_live_gate_method": PRODUCTION_PYTHON_GATE_METHOD,
        "python_live_gate_engine": PRODUCTION_PYTHON_LIVE_GATE_ENGINE,
        "rust_backtest_called": True,
        "rust_trace_source": PRODUCTION_RUST_TRACE_SOURCE,
        "fixture_mode": False,
        "research_policy_used": False,
        "snapshot_hash": _required_hash(
            snapshot_hash, field="invocation.snapshot_hash"
        ),
        "trace_record_count": trace_record_count,
        "live_export_sha256": _required_hash(
            live_export_sha256, field="invocation.live_export_sha256"
        ),
        "rust_trace_sha256": _required_hash(
            rust_trace_sha256, field="invocation.rust_trace_sha256"
        ),
    }
    target = Path(path).expanduser().resolve()
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def validate_production_invocation(
    path: str | Path,
    *,
    live_export_sha256: str,
    rust_trace_sha256: str,
    snapshot_hash: str,
    trace_record_count: int,
) -> dict[str, Any]:
    """Require in-process engine proof before production admission."""

    payload = _read_json(path, label="production invocation")
    if payload.get("schema_version") != INVOCATION_SCHEMA:
        raise RiskEvidenceError("production invocation schema is unsupported")
    if payload.get("invoked_by") != INVOCATION_SOURCE:
        raise RiskEvidenceError("production invocation source is not the actual probe")
    if payload.get("python_live_gate_called") is not True:
        raise RiskEvidenceError("production invocation did not call the Python live gate")
    if payload.get("python_live_gate_method") != PRODUCTION_PYTHON_GATE_METHOD:
        raise RiskEvidenceError("production invocation Python live gate method differs")
    if payload.get("python_live_gate_engine") != PRODUCTION_PYTHON_LIVE_GATE_ENGINE:
        raise RiskEvidenceError("production invocation Python live gate engine differs")
    if payload.get("rust_backtest_called") is not True:
        raise RiskEvidenceError("production invocation did not call Rust Backtest")
    if payload.get("rust_trace_source") != PRODUCTION_RUST_TRACE_SOURCE:
        raise RiskEvidenceError("production invocation Rust trace source differs")
    if payload.get("fixture_mode") is not False:
        raise RiskEvidenceError("production invocation is fixture_mode")
    if payload.get("research_policy_used") is not False:
        raise RiskEvidenceError("production invocation used research policy")
    if payload.get("snapshot_hash") != snapshot_hash:
        raise RiskEvidenceError("production invocation snapshot hash differs")
    if payload.get("trace_record_count") != trace_record_count:
        raise RiskEvidenceError("production invocation trace count differs")
    if payload.get("live_export_sha256") != live_export_sha256:
        raise RiskEvidenceError("production invocation live_export hash differs")
    if payload.get("rust_trace_sha256") != rust_trace_sha256:
        raise RiskEvidenceError("production invocation rust_trace hash differs")
    return payload


def build_production_risk_evidence_manifest(
    *,
    repo_root: str | Path,
    evidence_dir: str | Path,
    required_regression_path: str | Path,
) -> dict[str, Any]:
    """Build the single manifest consumed by the production risk gate."""

    root = Path(evidence_dir).expanduser().resolve()
    if not root.is_dir():
        raise RiskEvidenceError(f"production evidence directory is missing: {root}")
    repository = Path(repo_root).expanduser().resolve()
    regression_target = Path(required_regression_path).expanduser().resolve()
    regression_binding_path = _relative_artifact_path(
        repository,
        regression_target,
        field="required actual probe regression",
    )
    if regression_target == root:
        raise RiskEvidenceError(
            "required actual probe regression cannot be the production evidence itself"
        )
    required_regression = validate_actual_probe_regression(required_regression_path)
    surface = validate_surface_matrix(root / "surface_probe.json")
    outputs = _validate_engine_outputs(
        snapshot_path=root / "snapshot.json",
        live_export_path=root / "live_export.json",
        rust_trace_path=root / "rust_trace.json",
        comparison_path=root / "comparison.json",
        report_path=root / "report.json",
        label="production risk evidence",
    )
    invocation_path = root / "invocation.json"
    if not invocation_path.is_file():
        raise RiskEvidenceError("production invocation proof is missing")
    invocation = validate_production_invocation(
        invocation_path,
        live_export_sha256=_file_hash(
            root / "live_export.json", field="production live export"
        ),
        rust_trace_sha256=_file_hash(
            root / "rust_trace.json", field="production Rust trace"
        ),
        snapshot_hash=outputs["snapshot_hash"],
        trace_record_count=outputs["trace_record_count"],
    )
    config_path = root / "config.json"
    market_profile_path = root / "market_profile.json"
    config_sha256 = _file_hash(config_path, field="production config")
    market_profile_sha256 = _file_hash(
        market_profile_path, field="production market profile"
    )
    try:
        git_commit, git_dirty, code_fingerprint = _git_state(repository)
    except (OSError, ValueError) as exc:
        raise RiskEvidenceError(
            f"cannot capture production code fingerprint: {exc}"
        ) from exc

    comparison_path = root / "comparison.json"
    report_path = root / "report.json"
    manifest = {
        "schema_version": PRODUCTION_RISK_EVIDENCE_SCHEMA,
        "source_mode": ACTUAL_ENGINE_SOURCE_MODE,
        "engine": "Rust Backtest + Passivbot Python live gate",
        "read_only": True,
        "execution": {
            "fixture_mode": invocation["fixture_mode"],
            "research_policy_used": invocation["research_policy_used"],
            "python_live_gate_called": invocation["python_live_gate_called"],
            "python_live_gate_method": invocation["python_live_gate_method"],
            "python_live_gate_engine": invocation["python_live_gate_engine"],
            "rust_backtest_called": invocation["rust_backtest_called"],
            "rust_trace_source": invocation["rust_trace_source"],
            "candidate_admission": False,
            "trading_operations_count": 0,
            "transfers_count": 0,
            "account_changes_count": 0,
        },
        "code": {
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "implementation_fingerprint_version": IMPLEMENTATION_FINGERPRINT_VERSION,
            "implementation_fingerprint_sha256": code_fingerprint,
        },
        "inputs": {
            "config_sha256": config_sha256,
            "market_profile_sha256": market_profile_sha256,
            "surface_matrix_sha256": surface["sha256"],
        },
        "artifacts": {
            "config": _artifact_binding(root, config_path, field="production config"),
            "market_profile": _artifact_binding(
                root, market_profile_path, field="production market profile"
            ),
            "snapshot": _artifact_binding(
                root,
                root / "snapshot.json",
                field="production snapshot",
                canonical_hash=outputs["snapshot_hash"],
            ),
            "live_export": _artifact_binding(
                root, root / "live_export.json", field="production live export"
            ),
            "rust_trace": _artifact_binding(
                root, root / "rust_trace.json", field="production Rust trace"
            ),
            "comparison": _artifact_binding(
                root, comparison_path, field="production comparison"
            ),
            "probe": _artifact_binding(root, comparison_path, field="production probe"),
            "report": _artifact_binding(root, report_path, field="production report"),
            "surface_matrix": _artifact_binding(
                root, root / "surface_probe.json", field="production surface matrix"
            ),
            "invocation": _artifact_binding(
                root, invocation_path, field="production invocation"
            ),
        },
        "probe": {
            "source_mode": ACTUAL_ENGINE_SOURCE_MODE,
            "status": "passed",
            "snapshot_hash": outputs["snapshot_hash"],
            "trace_record_count": outputs["trace_record_count"],
            "probe_sha256": _file_hash(comparison_path, field="production probe"),
            "report_sha256": _file_hash(report_path, field="production report"),
        },
        "surface_matrix": surface,
        "required_regression": {
            **required_regression,
            "path": regression_binding_path,
        },
    }
    return manifest


def validate_production_risk_evidence(
    manifest_path: str | Path,
    *,
    repo_root: str | Path,
    config_path: str | Path,
    market_profile_path: str | Path,
) -> dict[str, Any]:
    """Validate production evidence and return only machine-derived metrics."""

    manifest_target = Path(manifest_path).expanduser().resolve()
    root = manifest_target.parent
    manifest = _read_json(manifest_target, label="production risk evidence manifest")
    if manifest.get("schema_version") != PRODUCTION_RISK_EVIDENCE_SCHEMA:
        raise RiskEvidenceError("production risk evidence schema is unsupported")
    if manifest.get("source_mode") != ACTUAL_ENGINE_SOURCE_MODE:
        raise RiskEvidenceError("production risk evidence source mode is not actual")
    if manifest.get("engine") != "Rust Backtest + Passivbot Python live gate":
        raise RiskEvidenceError("production risk evidence engine is not actual")
    if manifest.get("read_only") is not True:
        raise RiskEvidenceError("production risk evidence is not read-only")

    execution = _required_mapping(
        manifest.get("execution"), field="production.execution"
    )
    if execution.get("fixture_mode") is not False:
        raise RiskEvidenceError("production risk evidence is fixture_mode")
    if execution.get("research_policy_used") is not False:
        raise RiskEvidenceError("production risk evidence uses research policy")
    if execution.get("python_live_gate_called") is not True:
        raise RiskEvidenceError("production Python live gate was not called")
    if execution.get("python_live_gate_method") != PRODUCTION_PYTHON_GATE_METHOD:
        raise RiskEvidenceError("production Python live gate method is not bound")
    if execution.get("python_live_gate_engine") != PRODUCTION_PYTHON_LIVE_GATE_ENGINE:
        raise RiskEvidenceError("production Python live gate engine is not bound")
    if execution.get("rust_backtest_called") is not True:
        raise RiskEvidenceError("real Rust Backtest was not called")
    if execution.get("rust_trace_source") != PRODUCTION_RUST_TRACE_SOURCE:
        raise RiskEvidenceError("Rust Backtest trace source is not bound")
    if execution.get("candidate_admission") is not False:
        raise RiskEvidenceError("production risk evidence admits a candidate")
    if execution.get("trading_operations_count") != 0:
        raise RiskEvidenceError("production risk evidence records trading operations")
    if (
        execution.get("transfers_count") != 0
        or execution.get("account_changes_count") != 0
    ):
        raise RiskEvidenceError("production risk evidence records account changes")

    code = _required_mapping(manifest.get("code"), field="production.code")
    expected_commit, expected_dirty, expected_fingerprint = _git_state(
        Path(repo_root).expanduser().resolve()
    )
    if code.get("git_commit") != expected_commit:
        raise RiskEvidenceError("production evidence is bound to another git commit")
    if code.get("git_dirty") != expected_dirty:
        raise RiskEvidenceError("production evidence dirty-state binding differs")
    if (
        code.get("implementation_fingerprint_version")
        != IMPLEMENTATION_FINGERPRINT_VERSION
    ):
        raise RiskEvidenceError(
            "production evidence fingerprint version is unsupported"
        )
    if code.get("implementation_fingerprint_sha256") != expected_fingerprint:
        raise RiskEvidenceError("production evidence code fingerprint differs")

    artifacts = _required_mapping(
        manifest.get("artifacts"), field="production.artifacts"
    )
    required_artifacts = (
        "config",
        "market_profile",
        "snapshot",
        "live_export",
        "rust_trace",
        "comparison",
        "probe",
        "report",
        "surface_matrix",
        "invocation",
    )
    artifact_paths: dict[str, Path] = {}
    artifact_hashes: dict[str, str] = {}
    for name in required_artifacts:
        binding = _required_mapping(
            artifacts.get(name), field=f"production.artifacts.{name}"
        )
        raw_path = _required_text(
            binding.get("path"), field=f"production.artifacts.{name}.path"
        )
        if Path(raw_path).is_absolute() or ".." in Path(raw_path).parts:
            raise RiskEvidenceError(
                f"production artifact {name} path is not bundle-local"
            )
        path = _resolve_relative(
            root, raw_path, field=f"production.artifacts.{name}.path"
        )
        digest = _file_hash(path, field=f"production artifact {name}")
        declared = _required_hash(
            binding.get("sha256"), field=f"production.artifacts.{name}.sha256"
        )
        if digest != declared:
            raise RiskEvidenceError(
                f"production artifact {name} hash differs: {digest} != {declared}"
            )
        artifact_paths[name] = path
        artifact_hashes[name] = digest

    _verify_checksums(
        root,
        required_files=PRODUCTION_CHECKSUM_FILES,
        label="production risk evidence",
    )

    outputs = _validate_engine_outputs(
        snapshot_path=artifact_paths["snapshot"],
        live_export_path=artifact_paths["live_export"],
        rust_trace_path=artifact_paths["rust_trace"],
        comparison_path=artifact_paths["comparison"],
        report_path=artifact_paths["report"],
        label="production risk evidence",
    )
    validate_production_invocation(
        artifact_paths["invocation"],
        live_export_sha256=artifact_hashes["live_export"],
        rust_trace_sha256=artifact_hashes["rust_trace"],
        snapshot_hash=outputs["snapshot_hash"],
        trace_record_count=outputs["trace_record_count"],
    )
    probe = _required_mapping(manifest.get("probe"), field="production.probe")
    if probe.get("source_mode") != ACTUAL_ENGINE_SOURCE_MODE:
        raise RiskEvidenceError("production probe source mode is not actual")
    if probe.get("status") != "passed":
        raise RiskEvidenceError("production probe is not passed")
    if probe.get("snapshot_hash") != outputs["snapshot_hash"]:
        raise RiskEvidenceError("production probe snapshot hash differs")
    if probe.get("trace_record_count") != outputs["trace_record_count"]:
        raise RiskEvidenceError("production probe trace count differs")
    if probe.get("probe_sha256") != artifact_hashes["probe"]:
        raise RiskEvidenceError("production probe hash is not bound")
    if probe.get("report_sha256") != artifact_hashes["report"]:
        raise RiskEvidenceError("production report hash is not bound")

    snapshot_binding = _required_mapping(
        artifacts["snapshot"], field="production.artifacts.snapshot"
    )
    if snapshot_binding.get("canonical_hash") != outputs["snapshot_hash"]:
        raise RiskEvidenceError("production snapshot canonical hash is not bound")

    inputs = _required_mapping(manifest.get("inputs"), field="production.inputs")
    expected_config_hash = _file_hash(config_path, field="gate config")
    expected_market_profile_hash = _file_hash(
        market_profile_path, field="gate market profile"
    )
    if inputs.get("config_sha256") != expected_config_hash:
        raise RiskEvidenceError("production config hash differs from gate input")
    if inputs.get("market_profile_sha256") != expected_market_profile_hash:
        raise RiskEvidenceError(
            "production market profile hash differs from gate input"
        )
    if artifact_hashes["config"] != expected_config_hash:
        raise RiskEvidenceError("production config artifact differs from gate input")
    if artifact_hashes["market_profile"] != expected_market_profile_hash:
        raise RiskEvidenceError(
            "production market profile artifact differs from gate input"
        )
    if inputs.get("surface_matrix_sha256") != artifact_hashes["surface_matrix"]:
        raise RiskEvidenceError("production surface matrix hash is not bound")

    surface = validate_surface_matrix(artifact_paths["surface_matrix"])
    declared_surface = _required_mapping(
        manifest.get("surface_matrix"), field="production.surface_matrix"
    )
    for key in (
        "sha256",
        "source_mode",
        "fixture_mode",
        "scenario_count",
        "passed_count",
        "failed_count",
    ):
        if declared_surface.get(key) != surface.get(key):
            raise RiskEvidenceError(
                f"production surface matrix field {key} is not bound"
            )

    regression = _required_mapping(
        manifest.get("required_regression"), field="production.required_regression"
    )
    regression_raw_path = _required_text(
        regression.get("path"), field="production.required_regression.path"
    )
    if (
        Path(regression_raw_path).is_absolute()
        or ".." in Path(regression_raw_path).parts
    ):
        raise RiskEvidenceError(
            "production required regression path must be repository-relative"
        )
    regression_path = _resolve_relative(
        Path(repo_root).expanduser().resolve(),
        regression_raw_path,
        field="production.required_regression.path",
    )
    regression_actual = validate_actual_probe_regression(regression_path)
    for key in (
        "manifest_sha256",
        "report_sha256",
        "snapshot_hash",
        "trace_record_count",
    ):
        if regression.get(key) != regression_actual.get(key):
            raise RiskEvidenceError(
                f"required actual probe regression field {key} differs"
            )

    return {
        "status": "passed",
        "manifest_path": str(manifest_target),
        "manifest_sha256": _file_hash(
            manifest_target, field="production risk evidence manifest"
        ),
        "risk_production_evidence_manifest_present": True,
        "risk_production_evidence_contract_valid": True,
        "risk_production_path_validated": True,
        "risk_production_python_live_gate_called": True,
        "risk_production_python_live_gate_method": PRODUCTION_PYTHON_GATE_METHOD,
        "risk_production_python_live_gate_engine": PRODUCTION_PYTHON_LIVE_GATE_ENGINE,
        "risk_production_rust_backtest_called": True,
        "risk_production_rust_trace_present": True,
        "risk_production_fixture_mode": False,
        "risk_production_research_policy_used": False,
        "risk_production_candidate_admission": False,
        "risk_production_trading_operations_count": 0,
        "risk_production_transfers_count": 0,
        "risk_production_account_changes_count": 0,
        "risk_production_probe_bound": True,
        "risk_production_snapshot_bound": True,
        "risk_production_config_bound": True,
        "risk_production_market_profile_bound": True,
        "risk_production_code_fingerprint_bound": True,
        "risk_production_report_bound": True,
        "risk_production_surface_matrix_bound": True,
        "risk_limits_enforced_in_live_and_tester": True,
        "risk_parity_reached": True,
        "live_tester_decisions_identical": True,
        "risk_surface_matrix_status": surface["status"],
        "risk_surface_matrix_validated": True,
        "risk_surface_matrix_artifact_bound": True,
        "risk_surface_matrix_source_mode": surface["source_mode"],
        "risk_surface_matrix_fixture_mode": surface["fixture_mode"],
        "risk_surface_matrix_sha256": surface["sha256"],
        "risk_surface_matrix_scenario_count": surface["scenario_count"],
        "risk_surface_matrix_passed_count": surface["passed_count"],
        "risk_surface_matrix_failed_count": surface["failed_count"],
        "risk_production_evidence_status": "passed",
        "risk_production_evidence_error": None,
    }


__all__ = [
    "ACTUAL_ENGINE_SOURCE_MODE",
    "INVOCATION_SCHEMA",
    "INVOCATION_SOURCE",
    "PRODUCTION_PYTHON_GATE_METHOD",
    "PRODUCTION_PYTHON_LIVE_GATE_ENGINE",
    "PRODUCTION_RUST_TRACE_SOURCE",
    "PRODUCTION_RISK_EVIDENCE_SCHEMA",
    "REQUIRED_SURFACE_SCENARIO_COUNT",
    "RiskEvidenceError",
    "build_production_risk_evidence_manifest",
    "validate_actual_probe_regression",
    "validate_production_invocation",
    "validate_production_risk_evidence",
    "validate_surface_matrix",
    "write_production_invocation",
]
