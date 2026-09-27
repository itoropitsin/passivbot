"""Small, deterministic evidence contracts for the TLI paper contour.

The module is intentionally independent of Passivbot, Rust bindings, CCXT,
and any web service.  It hashes local inputs, evaluates funding quality, and
builds or checks a JSON-shaped risk-evidence object.  Documentation and
generated reports are never part of the version fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


VERSION_FINGERPRINT_SCHEMA = "tli-version-fingerprint-v1"
VERSION_FINGERPRINT_VERSION = VERSION_FINGERPRINT_SCHEMA
IMPLEMENTATION_FINGERPRINT_VERSION = VERSION_FINGERPRINT_SCHEMA
DATA_QUALITY_SCHEMA = "crypto-data-quality-v1"
RISK_EVIDENCE_SCHEMA = "risk-parity-production-evidence-v2"
PRODUCTION_RISK_EVIDENCE_SCHEMA = RISK_EVIDENCE_SCHEMA

EXPECTED_FUNDING_ROWS = 2_332
OBSERVED_FUNDING_ROWS = 248

_COMPONENTS = ("tli_code", "rust", "config", "policy", "dependencies", "inputs")
_COMPONENT_ALIASES = {
    "code": "tli_code",
    "tli": "tli_code",
    "python": "tli_code",
    "source": "tli_code",
    "rust_code": "rust",
    "configs": "config",
    "policies": "policy",
    "deps": "dependencies",
    "dependency": "dependencies",
    "input": "inputs",
}
_DEFAULT_PATHS = {
    "tli_code": ("src",),
    "rust": ("passivbot-rust",),
    "config": ("configs",),
    "policy": ("policy", "policies", "src/policy"),
    "dependencies": (
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-full.txt",
        "requirements-live.txt",
        "requirements-rust.txt",
        "rust-toolchain.toml",
        "passivbot-rust/Cargo.toml",
        "passivbot-rust/Cargo.lock",
    ),
    "inputs": ("inputs", "data", "datasets"),
}

# These directories contain generated output or documentation.  Excluding
# them by directory name also protects a component when a caller hashes its
# repository root instead of a carefully selected child directory.
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "target",
        "build",
        "dist",
        "node_modules",
        "artifacts",
        "caches",
        "monitor",
        "docs",
        "reports",
        "analysis_reports",
    }
)

SAFETY_CONTRACT = {
    "local_only": True,
    "network": False,
    "private_calls": False,
    "write_calls": False,
    "exchange_access": False,
}


class EvidenceError(ValueError):
    """Base class for malformed or unsafe evidence input."""


class FingerprintError(EvidenceError):
    """Raised when a local fingerprint input cannot be verified."""


class DataQualityError(EvidenceError):
    """Raised when a data-quality report cannot be accepted."""


class RiskEvidenceError(EvidenceError):
    """Raised when risk evidence is malformed or claims unsafe execution."""


def _canonical_value(value: Any, *, path: str = "value") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceError(f"{path} contains a non-finite number")
        return 0.0 if value == 0.0 else value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str) or not key:
                raise EvidenceError(f"{path} has a non-string or empty key")
            result[key] = _canonical_value(child, path=f"{path}.{key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    raise EvidenceError(f"{path} contains unsupported {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text used by all evidence hashes."""

    return json.dumps(
        _canonical_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise FingerprintError(
            f"fingerprint path must be inside repository root: {path}"
        ) from exc


def _is_excluded(relative: str) -> bool:
    parts = Path(relative).parts
    return any(part in EXCLUDED_DIRECTORY_NAMES for part in parts)


def _file_sha256(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError as exc:
        raise FingerprintError(f"cannot hash {path}: {exc}") from exc


def sha256_path(path: str | Path, *, root: str | Path | None = None) -> str:
    """Hash a local file or directory with sorted names and no output dirs."""

    target = Path(path).expanduser().resolve()
    if root is None:
        root_path = target if target.is_dir() else target.parent
    else:
        root_path = Path(root).expanduser().resolve()
    if not target.exists():
        raise FingerprintError(f"cannot hash missing path: {target}")
    if target.is_symlink():
        raise FingerprintError(f"symlinks are not valid fingerprint inputs: {target}")
    if target.is_file():
        return _file_sha256(target)
    if not target.is_dir():
        raise FingerprintError(f"cannot hash unsupported path: {target}")

    entries: list[tuple[str, str]] = []
    for candidate in sorted(target.rglob("*"), key=lambda item: item.as_posix()):
        relative = _relative_path(root_path, candidate)
        if _is_excluded(relative):
            continue
        if candidate.is_symlink():
            raise FingerprintError(f"symlinks are not valid fingerprint inputs: {candidate}")
        if candidate.is_file():
            entries.append((relative, _file_sha256(candidate)))
        elif not candidate.is_dir():
            raise FingerprintError(f"unsupported fingerprint input: {candidate}")
    return sha256_json({"files": entries})


def _normalize_component(value: Any) -> str:
    if not isinstance(value, str):
        raise FingerprintError("fingerprint component name must be a string")
    normalized = value.strip().lower()
    normalized = _COMPONENT_ALIASES.get(normalized, normalized)
    if normalized not in _COMPONENTS:
        raise FingerprintError(f"unknown fingerprint component {value!r}")
    return normalized


def _path_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [value]
    if isinstance(value, Mapping):
        # A component mapping is allowed to carry a path list alongside
        # deterministic non-path values.
        for key in ("paths", "files", "roots"):
            if key in value:
                return _path_list(value[key])
        return []
    if isinstance(value, Iterable):
        return list(value)
    raise FingerprintError("fingerprint paths must be a path or iterable of paths")


def _collect_component(
    root: Path,
    component: str,
    requested_paths: Iterable[Any],
    *,
    values: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    files: dict[str, str] = {}
    requested: list[str] = []
    missing: list[str] = []
    excluded: list[str] = []

    def add_file(candidate: Path) -> None:
        relative = _relative_path(root, candidate)
        requested.append(relative)
        if _is_excluded(relative):
            excluded.append(relative)
            return
        if candidate.is_symlink():
            raise FingerprintError(f"symlinks are not valid fingerprint inputs: {candidate}")
        if not candidate.exists():
            missing.append(relative)
            return
        if not candidate.is_file():
            raise FingerprintError(f"expected a file: {candidate}")
        files[relative] = _file_sha256(candidate)

    def add_path(raw: Any) -> None:
        if not isinstance(raw, (str, Path)):
            raise FingerprintError(f"{component} fingerprint path is not a path: {raw!r}")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        relative = _relative_path(root, candidate)
        if _is_excluded(relative):
            requested.append(relative)
            excluded.append(relative)
            return
        if not candidate.exists():
            requested.append(relative)
            missing.append(relative)
            return
        if candidate.is_symlink():
            raise FingerprintError(f"symlinks are not valid fingerprint inputs: {candidate}")
        if candidate.is_file():
            add_file(candidate)
            return
        if not candidate.is_dir():
            raise FingerprintError(f"unsupported fingerprint input: {candidate}")
        requested.append(relative)
        for child in sorted(candidate.rglob("*"), key=lambda item: item.as_posix()):
            child_relative = _relative_path(root, child)
            if _is_excluded(child_relative):
                if child.is_dir():
                    continue
                excluded.append(child_relative)
                continue
            if child.is_symlink():
                raise FingerprintError(f"symlinks are not valid fingerprint inputs: {child}")
            if child.is_file():
                files[child_relative] = _file_sha256(child)
            elif not child.is_dir():
                raise FingerprintError(f"unsupported fingerprint input: {child}")

    for raw_path in requested_paths:
        add_path(raw_path)

    file_entries = [
        {"path": path, "sha256": files[path]}
        for path in sorted(files)
    ]
    value_payload = None if values is None else _canonical_value(values, path=f"{component}.values")
    component_payload: dict[str, Any] = {
        "component": component,
        "files": file_entries,
        "missing_paths": sorted(set(missing)),
        "sha256": sha256_json(
            {
                "component": component,
                "files": file_entries,
                "missing_paths": sorted(set(missing)),
                "values": value_payload,
            }
        ),
    }
    if value_payload is not None:
        component_payload["values"] = value_payload
    component_payload["requested_paths"] = sorted(set(requested))
    component_payload["excluded_paths"] = sorted(set(excluded))
    return component_payload, sorted(set(excluded))


def _component_inputs(
    *,
    components: Mapping[str, Any] | None,
    explicit: Mapping[str, Any],
    values: Mapping[str, Any] | None,
) -> dict[str, tuple[list[Any], Any]]:
    supplied: dict[str, Any] = {}
    if components is not None:
        if not isinstance(components, Mapping):
            raise FingerprintError("components must be an object")
        supplied.update(components)
    supplied.update({key: value for key, value in explicit.items() if value is not None})
    normalized: dict[str, tuple[list[Any], Any]] = {}
    for raw_name, raw_value in supplied.items():
        name = _normalize_component(raw_name)
        value_payload = None
        if isinstance(raw_value, Mapping) and any(
            key in raw_value for key in ("paths", "files", "roots", "values")
        ):
            paths = _path_list(raw_value)
            value_payload = raw_value.get("values")
        else:
            paths = _path_list(raw_value)
        if values and name in values:
            value_payload = values[name]
        normalized[name] = (paths, value_payload)
    return normalized


def build_version_fingerprint(
    repo_root: str | Path = ".",
    *,
    components: Mapping[str, Any] | None = None,
    tli_code: Any = None,
    rust: Any = None,
    config: Any = None,
    policy: Any = None,
    dependencies: Any = None,
    inputs: Any = None,
    component_values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a content fingerprint for code and reproducibility inputs.

    The returned object contains relative paths only.  Its top-level digest
    is based on the six component digests, not on requested/excluded path
    listings.  Consequently adding or editing ``docs`` or ``reports`` cannot
    change the fingerprint.
    """

    root = Path(repo_root).expanduser().resolve()
    if not root.is_dir():
        raise FingerprintError(f"repository root is not a directory: {root}")
    explicit = {
        "tli_code": tli_code,
        "rust": rust,
        "config": config,
        "policy": policy,
        "dependencies": dependencies,
        "inputs": inputs,
    }
    selected = _component_inputs(
        components=components,
        explicit=explicit,
        values=component_values,
    )
    component_payloads: dict[str, dict[str, Any]] = {}
    excluded_paths: set[str] = set()
    for component in _COMPONENTS:
        paths, values = selected.get(component, (list(_DEFAULT_PATHS[component]), None))
        payload, excluded = _collect_component(root, component, paths, values=values)
        component_payloads[component] = payload
        excluded_paths.update(excluded)

    digest_input = {
        "schema_version": VERSION_FINGERPRINT_SCHEMA,
        "components": {
            component: component_payloads[component]["sha256"]
            for component in _COMPONENTS
        },
    }
    fingerprint = sha256_json(digest_input)
    return {
        "schema_version": VERSION_FINGERPRINT_SCHEMA,
        "fingerprint_version": VERSION_FINGERPRINT_VERSION,
        "algorithm": "sha256",
        "fingerprint": fingerprint,
        "sha256": fingerprint,
        "components": component_payloads,
        "excluded": {
            "docs_and_reports": True,
            "directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
            "paths": sorted(excluded_paths),
        },
        "safety": dict(SAFETY_CONTRACT),
    }


def compute_version_fingerprint(
    repo_root: str | Path = ".", **kwargs: Any
) -> str:
    """Return only the stable SHA-256 fingerprint string."""

    return str(build_version_fingerprint(repo_root, **kwargs)["fingerprint"])


def version_fingerprint(repo_root: str | Path = ".", **kwargs: Any) -> str:
    """Short alias for :func:`compute_version_fingerprint`."""

    return compute_version_fingerprint(repo_root, **kwargs)


def fingerprint_repository(repo_root: str | Path = ".", **kwargs: Any) -> dict[str, Any]:
    """Short alias returning the complete fingerprint manifest."""

    return build_version_fingerprint(repo_root, **kwargs)


def _synthetic_marker(row: Any) -> bool:
    if not isinstance(row, Mapping):
        return False
    for key in ("synthetic", "is_synthetic", "synthetic_row", "synthetic_funding"):
        if key not in row:
            continue
        value = row[key]
        if value is None or value is False:
            continue
        if isinstance(value, str) and value.strip().lower() in {
            "",
            "0",
            "false",
            "no",
            "real",
        }:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
            continue
        return True
    for key in ("kind", "source", "provenance"):
        value = row.get(key)
        if isinstance(value, str) and value.strip().lower() in {
            "synthetic",
            "synthetic_funding",
            "generated",
            "reconstructed",
            "interpolated",
            "estimated",
        }:
            return True
    return False


def is_synthetic_funding(row: Any) -> bool:
    """Return whether a funding row explicitly or semantically is synthetic."""

    return _synthetic_marker(row)


def _row_timestamp(row: Any) -> int | None:
    if isinstance(row, Mapping):
        for key in (
            "timestamp_ms",
            "fundingRateTimestamp",
            "fundingTime",
            "timestamp",
            "ts",
        ):
            value = row.get(key)
            if isinstance(value, bool):
                continue
            try:
                if isinstance(value, str) and not value.strip().isdigit():
                    continue
                timestamp = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if timestamp >= 0:
                return timestamp
    elif isinstance(row, (list, tuple)) and row:
        try:
            timestamp = int(row[0])
        except (TypeError, ValueError, OverflowError):
            return None
        return timestamp if timestamp >= 0 else None
    return None


def _row_rate(row: Any) -> float | None:
    if isinstance(row, Mapping):
        values = [row[key] for key in ("funding_rate", "fundingRate", "rate", "value") if key in row]
        value = values[0] if values else None
    elif isinstance(row, (list, tuple)) and len(row) > 1:
        value = row[1]
    else:
        value = None
    if isinstance(value, bool):
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return rate if math.isfinite(rate) else None


def _strict_count(value: Any, *, name: str, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise DataQualityError(f"{name} must be an integer")
    if isinstance(value, int) and value >= 0:
        return value
    raise DataQualityError(f"{name} must be a non-negative integer")


def _quality_input(
    source: Any,
    *,
    expected_rows: Any,
    actual_rows: Any,
    synthetic_rows: Any,
) -> tuple[Any, int, int | None, int | None, int, bool]:
    payload = source if isinstance(source, Mapping) else None
    if payload is not None:
        nested = payload.get("funding")
        if isinstance(nested, Mapping):
            merged = dict(payload)
            merged.update(nested)
            payload = merged
        rows = payload.get("funding_rows", payload.get("rows"))
        expected_value = payload.get(
            "expected_rows",
            payload.get("funding_expected_rows", expected_rows),
        )
        actual_value = payload.get(
            "actual_rows",
            payload.get("funding_actual_rows", actual_rows),
        )
        synthetic_value = payload.get(
            "synthetic_rows",
            payload.get("funding_synthetic_rows", synthetic_rows),
        )
        top_synthetic = bool(
            payload.get("synthetic_funding") is True
            or payload.get("funding_synthetic") is True
        )
    else:
        rows = source
        expected_value = expected_rows
        actual_value = actual_rows
        synthetic_value = synthetic_rows
        top_synthetic = False
    expected = _strict_count(expected_value, name="funding expected_rows")
    if expected is None or expected < 1:
        raise DataQualityError("funding expected_rows must be positive")
    actual = _strict_count(actual_value, name="funding actual_rows", allow_none=True)
    synthetic = _strict_count(
        synthetic_value, name="funding synthetic_rows", allow_none=True
    )
    return rows, expected, actual, synthetic, 0, top_synthetic


def data_quality_gate(
    funding_rows: Any = None,
    *,
    expected_rows: int = EXPECTED_FUNDING_ROWS,
    actual_rows: int | None = None,
    synthetic_rows: int | None = None,
    freshness: str = "fresh",
    expected_funding_rows: int | None = None,
    actual_funding_rows: int | None = None,
    synthetic_funding_rows: int | None = None,
) -> dict[str, Any]:
    """Evaluate funding quality without promoting incomplete or synthetic data.

    ``248/2332`` is therefore reported as ``status=failed`` and
    ``freshness=fresh``: the observation is current, but it is not complete
    enough for admission.  Missing counts remain ``None`` when no source
    count was supplied; they are never turned into a fabricated zero.
    """

    if expected_funding_rows is not None:
        expected_rows = expected_funding_rows
    if actual_funding_rows is not None:
        actual_rows = actual_funding_rows
    if synthetic_funding_rows is not None:
        synthetic_rows = synthetic_funding_rows
    if freshness not in {"fresh", "stale", "unknown"}:
        raise DataQualityError("freshness must be fresh, stale, or unknown")

    rows, expected, actual, declared_synthetic, _, top_synthetic = _quality_input(
        funding_rows,
        expected_rows=expected_rows,
        actual_rows=actual_rows,
        synthetic_rows=synthetic_rows,
    )
    invalid_rows = 0
    row_synthetic = 0
    duplicate_rows = 0
    derived_actual: int | None = actual
    if rows is not None:
        if isinstance(rows, Mapping) or isinstance(rows, (str, bytes)):
            raise DataQualityError("funding rows must be an iterable of rows")
        try:
            materialized = list(rows)
        except TypeError as exc:
            raise DataQualityError("funding rows must be iterable") from exc
        valid_timestamps: list[int] = []
        valid_without_timestamp = 0
        for row in materialized:
            if _synthetic_marker(row):
                row_synthetic += 1
                continue
            timestamp = _row_timestamp(row)
            rate = _row_rate(row)
            if rate is None:
                invalid_rows += 1
                continue
            if timestamp is None:
                valid_without_timestamp += 1
            else:
                valid_timestamps.append(timestamp)
        if valid_timestamps:
            duplicate_rows = len(valid_timestamps) - len(set(valid_timestamps))
            derived_actual = len(set(valid_timestamps))
            if valid_without_timestamp:
                invalid_rows += valid_without_timestamp
        else:
            derived_actual = valid_without_timestamp if valid_without_timestamp else 0
        if actual is not None and actual != derived_actual:
            invalid_rows += 1
        if declared_synthetic is not None and declared_synthetic != row_synthetic:
            invalid_rows += 1
        actual = derived_actual
    synthetic = (declared_synthetic or 0) + row_synthetic
    if top_synthetic and synthetic == 0:
        synthetic = 1

    missing_rows = None if actual is None else max(expected - actual, 0)
    coverage = None if actual is None else actual / expected
    complete = bool(
        actual is not None
        and actual == expected
        and synthetic == 0
        and invalid_rows == 0
        and duplicate_rows == 0
    )
    reasons: list[str] = []
    if synthetic:
        reasons.append("synthetic_funding_rejected")
    if actual is None:
        reasons.append("funding_coverage_unavailable")
    elif actual != expected:
        reasons.append("funding_window_incomplete")
    if invalid_rows:
        reasons.append("invalid_funding_rows")
    if duplicate_rows:
        reasons.append("duplicate_funding_timestamps")
    if freshness != "fresh":
        reasons.append("funding_freshness_not_fresh")
    status = "passed" if complete else "failed"
    qualifying = complete and freshness == "fresh"
    return {
        "schema_version": DATA_QUALITY_SCHEMA,
        "status": status,
        "freshness": freshness,
        "ok": qualifying,
        "qualifying": qualifying,
        "admission": qualifying,
        "expected_rows": expected,
        "actual_rows": actual,
        "missing_rows": missing_rows,
        "coverage": coverage,
        "synthetic_rows": synthetic,
        "invalid_rows": invalid_rows,
        "duplicate_rows": duplicate_rows,
        "complete": complete,
        "reasons": reasons,
        "funding": {
            "expected_rows": expected,
            "actual_rows": actual,
            "missing_rows": missing_rows,
            "coverage": coverage,
            "synthetic_rows": synthetic,
            "invalid_rows": invalid_rows,
            "duplicate_rows": duplicate_rows,
            "complete": complete,
        },
        "read_only": True,
    }


evaluate_data_quality = data_quality_gate
build_data_quality_gate = data_quality_gate


def require_data_quality(report_or_rows: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Return a passing gate or raise a fail-closed quality error."""

    report = data_quality_gate(report_or_rows, **kwargs)
    if not report["qualifying"]:
        raise DataQualityError(
            "funding data quality gate failed: " + ", ".join(report["reasons"])
        )
    return report


def validate_data_quality(report_or_rows: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Recompute a quality report; optionally raise with ``raise_on_failure``."""

    raise_on_failure = bool(kwargs.pop("raise_on_failure", False))
    report = data_quality_gate(report_or_rows, **kwargs)
    if raise_on_failure and not report["qualifying"]:
        raise DataQualityError(
            "funding data quality gate failed: " + ", ".join(report["reasons"])
        )
    return report


def _digest_value(value: Any, *, field: str) -> str:
    if isinstance(value, Mapping):
        value = value.get("fingerprint", value.get("sha256"))
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise RiskEvidenceError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _fingerprint_reference(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        schema = value.get("schema_version")
        if schema != VERSION_FINGERPRINT_SCHEMA:
            raise RiskEvidenceError("risk evidence fingerprint schema is unsupported")
        digest = _digest_value(value, field="version fingerprint")
        return {
            "schema_version": VERSION_FINGERPRINT_SCHEMA,
            "fingerprint_version": value.get(
                "fingerprint_version", VERSION_FINGERPRINT_VERSION
            ),
            "sha256": digest,
        }
    return {
        "schema_version": VERSION_FINGERPRINT_SCHEMA,
        "fingerprint_version": VERSION_FINGERPRINT_VERSION,
        "sha256": _digest_value(value, field="version fingerprint"),
    }


def _non_negative_count(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RiskEvidenceError(f"{field} must be a non-negative integer")
    return value


def _risk_execution(risk_trace: Any) -> dict[str, Any]:
    if risk_trace is None:
        return {
            "trace_present": False,
            "status": "blocked",
            "parity": "unproven",
        }
    if not isinstance(risk_trace, Mapping):
        raise RiskEvidenceError("risk_trace must be an object")
    private_calls = risk_trace.get("private_calls", risk_trace.get("private_call_count", 0))
    write_calls = risk_trace.get("write_calls", risk_trace.get("write_call_count", 0))
    trading_operations = risk_trace.get("trading_operations_count", 0)
    transfers = risk_trace.get("transfers_count", 0)
    account_changes = risk_trace.get("account_changes_count", 0)
    return {
        "trace_present": True,
        "status": str(risk_trace.get("status", "unproven")),
        "parity": str(risk_trace.get("parity", "unproven")),
        "private_calls": _non_negative_count(private_calls, field="private_calls"),
        "write_calls": _non_negative_count(write_calls, field="write_calls"),
        "trading_operations_count": _non_negative_count(
            trading_operations, field="trading_operations_count"
        ),
        "transfers_count": _non_negative_count(transfers, field="transfers_count"),
        "account_changes_count": _non_negative_count(
            account_changes, field="account_changes_count"
        ),
    }


def build_risk_evidence(
    *,
    fingerprint: Any = None,
    version_fingerprint: Any = None,
    repo_root: str | Path | None = None,
    fingerprint_options: Mapping[str, Any] | None = None,
    data_quality: Mapping[str, Any] | None = None,
    funding_rows: Any = None,
    expected_funding_rows: int = EXPECTED_FUNDING_ROWS,
    risk_trace: Mapping[str, Any] | None = None,
    candidate_admission: bool = False,
    read_only: bool = True,
) -> dict[str, Any]:
    """Build a v2 risk-evidence object without performing any operation."""

    supplied_fingerprint = fingerprint if fingerprint is not None else version_fingerprint
    if supplied_fingerprint is None and repo_root is not None:
        supplied_fingerprint = build_version_fingerprint(
            repo_root, **dict(fingerprint_options or {})
        )
    if supplied_fingerprint is None:
        raise RiskEvidenceError("risk evidence requires a version fingerprint")
    fingerprint_ref = _fingerprint_reference(supplied_fingerprint)
    if data_quality is None:
        quality = data_quality_gate(
            funding_rows,
            expected_rows=expected_funding_rows,
        )
    else:
        if not isinstance(data_quality, Mapping):
            raise RiskEvidenceError("data_quality must be an object")
        quality = data_quality_gate(data_quality)
    execution = _risk_execution(risk_trace)
    if not isinstance(candidate_admission, bool):
        raise RiskEvidenceError("candidate_admission must be boolean")
    if candidate_admission:
        raise RiskEvidenceError("this evidence builder never admits a candidate")
    if read_only is not True:
        raise RiskEvidenceError("risk evidence must be read-only")
    unsafe = any(
        execution[key] != 0
        for key in (
            "private_calls",
            "write_calls",
            "trading_operations_count",
            "transfers_count",
            "account_changes_count",
        )
        if key in execution
    )
    if unsafe:
        raise RiskEvidenceError("risk evidence contains private, write, or trading calls")
    risk_proven = bool(
        execution.get("trace_present")
        and execution.get("status") == "passed"
        and execution.get("parity") == "passed"
    )
    status = "passed" if quality["qualifying"] and risk_proven else "blocked"
    result = {
        "schema_version": RISK_EVIDENCE_SCHEMA,
        "risk_evidence_version": RISK_EVIDENCE_SCHEMA,
        "source_mode": "local_read_only",
        "read_only": True,
        "version_fingerprint": fingerprint_ref,
        "data_quality": quality,
        "execution": {
            **execution,
            "candidate_admission": False,
        },
        "risk": _canonical_value(risk_trace, path="risk_trace") if risk_trace is not None else None,
        "status": status,
        "candidate_admission": False,
        "admission": False,
        "safety": dict(SAFETY_CONTRACT),
    }
    return _canonical_value(result, path="risk_evidence")


def validate_risk_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_fingerprint: Any = None,
) -> dict[str, Any]:
    """Validate a v2 object and preserve a blocked quality result as blocked."""

    if not isinstance(evidence, Mapping):
        raise RiskEvidenceError("risk evidence must be an object")
    if evidence.get("schema_version") != RISK_EVIDENCE_SCHEMA:
        raise RiskEvidenceError("risk evidence schema is unsupported")
    if evidence.get("risk_evidence_version") not in (None, RISK_EVIDENCE_SCHEMA):
        raise RiskEvidenceError("risk evidence version is unsupported")
    if evidence.get("read_only") is not True:
        raise RiskEvidenceError("risk evidence is not read-only")
    fingerprint_ref = _fingerprint_reference(evidence.get("version_fingerprint"))
    if expected_fingerprint is not None:
        expected_ref = _fingerprint_reference(expected_fingerprint)
        if fingerprint_ref["sha256"] != expected_ref["sha256"]:
            raise RiskEvidenceError("risk evidence version fingerprint differs")

    raw_quality = evidence.get("data_quality")
    if not isinstance(raw_quality, Mapping):
        raise RiskEvidenceError("risk evidence data_quality is missing")
    quality = data_quality_gate(raw_quality)
    for key in ("status", "freshness", "qualifying", "expected_rows", "actual_rows", "synthetic_rows"):
        if key in raw_quality and raw_quality.get(key) != quality.get(key):
            raise RiskEvidenceError(f"risk evidence data_quality.{key} is inconsistent")

    execution = evidence.get("execution")
    if not isinstance(execution, Mapping):
        raise RiskEvidenceError("risk evidence execution is missing")
    if execution.get("candidate_admission") is True or evidence.get("candidate_admission") is True:
        raise RiskEvidenceError("risk evidence admits a candidate")
    counts = {
        "private_calls": execution.get("private_calls", 0),
        "write_calls": execution.get("write_calls", 0),
        "trading_operations_count": execution.get("trading_operations_count", 0),
        "transfers_count": execution.get("transfers_count", 0),
        "account_changes_count": execution.get("account_changes_count", 0),
    }
    for key, value in counts.items():
        if _non_negative_count(value, field=f"execution.{key}") != 0:
            raise RiskEvidenceError(f"risk evidence contains {key}")
    expected_status = "passed" if quality["qualifying"] and bool(
        isinstance(evidence.get("risk"), Mapping)
        and evidence["risk"].get("status") == "passed"
        and evidence["risk"].get("parity") == "passed"
    ) else "blocked"
    if evidence.get("status") != expected_status:
        raise RiskEvidenceError("risk evidence status is inconsistent with its inputs")
    if evidence.get("admission") is True:
        raise RiskEvidenceError("risk evidence admission must remain closed")
    return {
        "valid": True,
        "ok": True,
        "schema_version": RISK_EVIDENCE_SCHEMA,
        "status": expected_status,
        "candidate_admission": False,
        "admission": False,
        "fingerprint": fingerprint_ref["sha256"],
        "data_quality": quality,
        "safety": dict(SAFETY_CONTRACT),
    }


def require_risk_evidence(
    evidence: Mapping[str, Any], *, expected_fingerprint: Any = None
) -> dict[str, Any]:
    report = validate_risk_evidence(
        evidence, expected_fingerprint=expected_fingerprint
    )
    if report["status"] != "passed":
        raise RiskEvidenceError("risk evidence is blocked")
    return report


__all__ = [
    "DATA_QUALITY_SCHEMA",
    "EXPECTED_FUNDING_ROWS",
    "EXCLUDED_DIRECTORY_NAMES",
    "FingerprintError",
    "IMPLEMENTATION_FINGERPRINT_VERSION",
    "OBSERVED_FUNDING_ROWS",
    "PRODUCTION_RISK_EVIDENCE_SCHEMA",
    "RISK_EVIDENCE_SCHEMA",
    "RiskEvidenceError",
    "SAFETY_CONTRACT",
    "DataQualityError",
    "EvidenceError",
    "VERSION_FINGERPRINT_SCHEMA",
    "VERSION_FINGERPRINT_VERSION",
    "build_data_quality_gate",
    "build_risk_evidence",
    "build_version_fingerprint",
    "canonical_json",
    "compute_version_fingerprint",
    "data_quality_gate",
    "evaluate_data_quality",
    "fingerprint_repository",
    "is_synthetic_funding",
    "require_data_quality",
    "require_risk_evidence",
    "sha256_bytes",
    "sha256_json",
    "sha256_path",
    "validate_data_quality",
    "validate_risk_evidence",
    "version_fingerprint",
]
