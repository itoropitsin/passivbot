"""Immutable experiment passports and machine-verifiable research gates."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

MANIFEST_SCHEMA_VERSION = "crypto-grid-experiment-v1"
IMPLEMENTATION_FINGERPRINT_VERSION = "implementation-fingerprint-v2"
PLAN_DATE = "2026-08-17"
ROADMAP_STATUS_SCHEMA_V2 = "crypto-grid-roadmap-status-v2"
ROADMAP_STATUS_SCHEMA_V3 = "crypto-grid-roadmap-status-v3"
CANONICAL_STAGES = (
    "baseline",
    "market_profile",
    "account_profile",
    "data",
    "risk_engine",
    "account_risk_surface",
    "risk_contract",
    "risk_grid",
    "historical",
    "shadow_readiness",
    "paper_runtime",
    "technical_live",
    "controlled_live",
    "portfolio_historical",
    "portfolio_paper",
)

# These names are accepted only as an explicit migration path.  In
# particular, ``profile`` means the old private account profile; it is never
# treated as a public market profile.
LEGACY_STAGE_ALIASES = {
    "profile": "account_profile",
    "paper": "paper_runtime",
}


def normalize_stage_name(stage: str) -> str:
    """Return a canonical stage name, resolving only documented aliases."""

    raw = str(stage).strip()
    canonical = LEGACY_STAGE_ALIASES.get(raw, raw)
    if canonical not in CANONICAL_STAGES:
        known = ", ".join((*CANONICAL_STAGES, *LEGACY_STAGE_ALIASES))
        raise ValueError(f"unknown research stage {stage!r}; expected one of: {known}")
    return canonical


MARKET_PROFILE_STAGES = frozenset(
    {
        "market_profile",
        "data",
        "risk_engine",
        "risk_grid",
        "historical",
        "shadow_readiness",
        "paper_runtime",
        "technical_live",
        "controlled_live",
        "portfolio_historical",
        "portfolio_paper",
    }
)
ACCOUNT_PROFILE_STAGES = frozenset(
    {"account_profile", "account_risk_surface", "technical_live", "controlled_live"}
)


def stage_requires_market_profile(stage: str) -> bool:
    return normalize_stage_name(stage) in MARKET_PROFILE_STAGES


def stage_requires_account_profile(stage: str) -> bool:
    return normalize_stage_name(stage) in ACCOUNT_PROFILE_STAGES


REQUIRED_RUN_FILES = (
    "manifest.json",
    "metrics.json",
    "verdict.json",
    "report.md",
    "events.jsonl.gz",
    "config.json",
    "market_profile.json",
)
GENERATED_GIT_STATE_PATHS = (
    # Roadmap evidence is immutable run output, not implementation input.  A
    # new report must not invalidate the source baseline that the report
    # depends on.  Each run still records its own file hashes in checksums.json
    # and manifest inputs, so excluding this generated tree from the source
    # fingerprint does not weaken evidence integrity.
    "analysis_reports/crypto_bot_roadmap/",
)
IMPLEMENTATION_ROOTS = (
    "src/",
    "passivbot-rust/",
    "tests/",
    "configs/",
)
IMPLEMENTATION_ROOT_FILES = frozenset(
    {
        "pyproject.toml",
        "pytest.ini",
        "setup.py",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-full.txt",
        "requirements-live.txt",
        "requirements-rust.txt",
        "rust-toolchain.toml",
    }
)
STAGE_PREREQUISITES: dict[str, tuple[str, ...]] = {
    "baseline": (),
    "market_profile": ("baseline",),
    "account_profile": ("baseline",),
    "data": ("baseline", "market_profile"),
    "risk_contract": ("baseline",),
    "risk_grid": ("baseline", "market_profile", "data", "risk_contract"),
    "historical": (
        "baseline",
        "market_profile",
        "data",
        "risk_contract",
        "risk_grid",
    ),
    "paper_runtime": (
        "baseline",
        "market_profile",
        "data",
        "risk_contract",
        "risk_grid",
        "historical",
    ),
    "technical_live": (
        "baseline",
        "market_profile",
        "account_profile",
        "data",
        "risk_contract",
        "risk_grid",
        "historical",
        "paper_runtime",
    ),
    "controlled_live": (
        "baseline",
        "market_profile",
        "account_profile",
        "data",
        "risk_contract",
        "risk_grid",
        "historical",
        "paper_runtime",
        "technical_live",
    ),
    "portfolio_historical": (
        "baseline",
        "market_profile",
        "data",
        "risk_contract",
        "risk_grid",
        "historical",
    ),
    "portfolio_paper": (
        "baseline",
        "market_profile",
        "data",
        "risk_contract",
        "risk_grid",
        "historical",
        "paper_runtime",
        "portfolio_historical",
    ),
}
# The v2 graph remains available for old callers and old status snapshots.
# New status snapshots use this graph for the logical split introduced by the
# roadmap review.  ``risk_contract`` is deliberately retained in the v2 graph
# so an old verdict is never silently reinterpreted as a new risk-engine
# verdict.
STAGE_PREREQUISITES_V3: dict[str, tuple[str, ...]] = {
    **STAGE_PREREQUISITES,
    "account_risk_surface": ("baseline",),
    # These safety surfaces use the public market profile but do not depend
    # on the historical funding archive.  That archive must remain a hard
    # blocker for historical selection without blocking local safety checks.
    "risk_engine": ("baseline", "market_profile"),
    "risk_grid": ("baseline", "market_profile", "risk_engine"),
    "historical": (
        "baseline",
        "market_profile",
        "data",
        "risk_engine",
        "risk_grid",
    ),
    "shadow_readiness": (
        "baseline",
        "market_profile",
        "risk_engine",
    ),
    "paper_runtime": (
        "baseline",
        "market_profile",
        "historical",
        "shadow_readiness",
    ),
    "technical_live": (
        "baseline",
        "market_profile",
        "account_risk_surface",
        "data",
        "risk_engine",
        "risk_grid",
        "historical",
        "paper_runtime",
    ),
    "controlled_live": (
        "baseline",
        "market_profile",
        "account_risk_surface",
        "data",
        "risk_engine",
        "risk_grid",
        "historical",
        "paper_runtime",
        "technical_live",
    ),
    "portfolio_historical": (
        "baseline",
        "market_profile",
        "data",
        "risk_engine",
        "risk_grid",
        "historical",
    ),
    "portfolio_paper": (
        "baseline",
        "market_profile",
        "portfolio_historical",
        "paper_runtime",
    ),
}
EVENT_REQUIRED_STAGES = frozenset(
    {
        "historical",
        "portfolio_historical",
        "shadow_readiness",
        "paper_runtime",
        "portfolio_paper",
        "technical_live",
        "controlled_live",
    }
)


@dataclass(frozen=True)
class ExperimentManifest:
    """Content-addressed inputs needed to reproduce one research run."""

    schema_version: str
    plan_date: str
    run_id: str
    stage: str
    created_at_utc: str
    git_commit: str
    git_dirty: bool
    dirty_diff_sha256: str
    config_sha256: str
    dataset_sha256: str
    market_profile_sha256: str | None
    account_profile_sha256: str | None
    command: tuple[str, ...]
    random_seeds: tuple[int, ...]
    execution_inputs_sha256: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    implementation_fingerprint_version: str = IMPLEMENTATION_FINGERPRINT_VERSION
    implementation_fingerprint_sha256: str = ""
    plan_sha256: str | None = None
    risk_evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "plan_date": self.plan_date,
            "run_id": self.run_id,
            "stage": self.stage,
            "created_at_utc": self.created_at_utc,
            "code": {
                "git_commit": self.git_commit,
                "git_dirty": self.git_dirty,
                "dirty_diff_sha256": self.dirty_diff_sha256,
                "implementation_fingerprint_version": self.implementation_fingerprint_version,
                "implementation_fingerprint_sha256": self.implementation_fingerprint_sha256,
            },
            "inputs": {
                "config_sha256": self.config_sha256,
                "dataset_sha256": self.dataset_sha256,
                "market_profile_sha256": self.market_profile_sha256,
                "account_profile_sha256": self.account_profile_sha256,
                "execution_inputs_sha256": self.execution_inputs_sha256,
                "plan_sha256": self.plan_sha256,
            },
            "command": list(self.command),
            "random_seeds": list(self.random_seeds),
            "environment": dict(self.environment),
        }
        if self.risk_evidence is not None:
            payload["risk_evidence"] = dict(self.risk_evidence)
        return payload


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: str | Path) -> str:
    """Hash a file or directory deterministically, including directory names."""

    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"cannot hash missing input: {target}")
    if target.is_file():
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if not target.is_dir():
        raise ValueError(f"cannot hash unsupported input path: {target}")
    digest = hashlib.sha256()
    files = sorted(path for path in target.rglob("*") if path.is_file())
    for file_path in files:
        relative = file_path.relative_to(target).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_path(file_path)))
        digest.update(b"\n")
    return digest.hexdigest()


def replay_events_present(path: str | Path | None) -> bool:
    """Return true only when an event input contains at least one non-empty row."""

    if path is None:
        return False
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_file() or target.stat().st_size == 0:
        return False
    opener = gzip.open if target.suffix == ".gz" else open
    try:
        with opener(target, "rb") as handle:
            return any(line.strip() for line in handle)
    except (OSError, gzip.BadGzipFile):
        return False


def _run_git(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _is_generated_git_state_path(raw_path: bytes) -> bool:
    path = raw_path.decode("utf-8", errors="replace")
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in GENERATED_GIT_STATE_PATHS)


def _is_implementation_path(raw_path: bytes) -> bool:
    """Return whether a repository path is part of the implementation input.

    Runtime reports, monitor output and documentation intentionally live
    outside this set.  Keeping the allow-list here makes the fingerprint
    stable even when an experiment writes more evidence after it starts.
    """

    path = raw_path.decode("utf-8", errors="replace")
    if _is_generated_git_state_path(raw_path):
        return False
    if path in IMPLEMENTATION_ROOT_FILES:
        return True
    return any(path.startswith(prefix) for prefix in IMPLEMENTATION_ROOTS)


def _git_state(repo_root: Path) -> tuple[str, bool, str]:
    """Return commit, implementation-dirty state and stable implementation hash.

    ``dirty_diff_sha256`` is retained by callers as a compatibility name, but
    its value is now the implementation fingerprint.  Documentation and
    generated/runtime files are deliberately excluded; their own artifacts
    remain protected by each run's checksums and by ``plan_sha256``/input
    hashes.
    """

    commit = _run_git(repo_root, "rev-parse", "HEAD").decode("ascii").strip()
    status_raw = _run_git(repo_root, "status", "--short", "--untracked-files=all")
    status_lines = []
    for line in status_raw.splitlines(keepends=True):
        path = line[3:].rstrip(b"\r\n") if len(line) >= 3 else b""
        if _is_implementation_path(path):
            status_lines.append(line)
    status = b"".join(status_lines)
    diff = _run_git(
        repo_root,
        "diff",
        "--no-ext-diff",
        "--binary",
        "--",
        *IMPLEMENTATION_ROOTS,
        *sorted(IMPLEMENTATION_ROOT_FILES),
    )
    untracked_raw = _run_git(repo_root, "ls-files", "--others", "--exclude-standard", "-z")
    untracked = b"\0".join(
        path for path in untracked_raw.split(b"\0") if path and _is_implementation_path(path)
    )
    dirty_material = bytearray(status)
    dirty_material.extend(b"\0DIFF\0")
    dirty_material.extend(diff)
    dirty_material.extend(b"\0UNTRACKED\0")
    for raw_path in filter(None, untracked.split(b"\0")):
        path = repo_root / raw_path.decode("utf-8")
        if path.is_file():
            dirty_material.extend(raw_path)
            dirty_material.extend(b"\0")
            dirty_material.extend(bytes.fromhex(sha256_path(path)))
            dirty_material.extend(b"\n")
    return commit, bool(status.strip() or diff or untracked), _sha256_bytes(bytes(dirty_material))


def _version_command(*command: str) -> str:
    try:
        result = subprocess.run(
            list(command),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return result.stdout.strip() or "unavailable"


def _installed_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return str(version(name))
    except Exception:
        return "unavailable"


def _environment_passport() -> tuple[tuple[str, str], ...]:
    """Capture versions that can change a reproducible research result."""

    values = {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "rustc": _version_command("rustc", "--version"),
        "cargo": _version_command("cargo", "--version"),
        "ccxt": _installed_version("ccxt"),
        "numpy": _installed_version("numpy"),
        "pandas": _installed_version("pandas"),
        "scipy": _installed_version("scipy"),
    }
    spec = importlib.util.find_spec("passivbot_rust")
    extension = Path(spec.origin).expanduser().resolve() if spec and spec.origin else None
    if extension is not None and extension.is_file():
        try:
            values["rust_extension_sha256"] = sha256_path(extension)
        except (OSError, ValueError):
            values["rust_extension_sha256"] = "unavailable"
    else:
        values["rust_extension_sha256"] = "unavailable"
    values["python_executable"] = str(Path(sys.executable).resolve())
    return tuple(sorted((key, str(value)) for key, value in values.items()))


def _read_profile_object(path: str | Path | None) -> tuple[dict[str, Any] | None, str | None]:
    if path is None:
        return None, "profile_path_missing"
    target = Path(path).expanduser().resolve()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"profile_unreadable:{exc}"
    if not isinstance(value, dict):
        return None, "profile_not_an_object"
    return value, None


def _private_profile_markers(value: Any, path: tuple[str, ...] = ()) -> list[str]:
    """Find account/private material that must not enter a public profile."""

    markers: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).lower()
            child_path = path + (key,)
            if (
                key.startswith("account_")
                or key in {"account", "api_key", "api_secret", "secret", "signature", "nonce"}
                or key.startswith("private_")
            ):
                markers.append(".".join(child_path))
            if isinstance(child, str) and (
                "private_" in child.lower() or child.lower().startswith("private")
            ):
                markers.append(".".join(child_path))
            markers.extend(_private_profile_markers(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            markers.extend(_private_profile_markers(child, path + (str(index),)))
    return sorted(set(markers))


def _profile_source_hash_values(value: Any) -> list[str]:
    """Return every ``source_profile_sha256`` value in a profile payload.

    Profile producers have used both a top-level field and a nested evidence
    object.  Looking through the complete object keeps the admission rule
    independent of that presentation detail.
    """

    values: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for raw_key, child in node.items():
                if str(raw_key).lower() == "source_profile_sha256":
                    values.append(str(child))
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return values


def _profile_source_hash_metrics(value: Mapping[str, Any]) -> dict[str, Any]:
    """Describe source-profile hash evidence without inventing a hash.

    A missing field remains compatible with older public profiles.  A field
    that is present must not use the run-time placeholder that was previously
    accepted as if it were a source hash.  If a producer supplies a value, it
    must be a real SHA-256-looking value.
    """

    values = _profile_source_hash_values(value)
    placeholder = any(item.strip().lower() == "pending_run_manifest" for item in values)
    malformed = any(
        not re.fullmatch(r"[0-9a-fA-F]{64}", item.strip())
        for item in values
        if item.strip().lower() != "pending_run_manifest"
    )
    return {
        "source_profile_sha256_present": bool(values),
        "source_profile_sha256_placeholder": placeholder,
        "source_profile_sha256_malformed": malformed,
        "source_profile_sha256_not_placeholder": not placeholder and not malformed,
    }


def _nested_bool(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> bool:
    for path in paths:
        value: Any = payload
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                value = None
                break
            value = value[key]
        if isinstance(value, bool):
            return value
    return False


def profile_input_metrics(
    stage: str,
    *,
    market_profile_path: str | Path | None = None,
    account_profile_path: str | Path | None = None,
) -> dict[str, Any]:
    """Derive non-overridable profile provenance checks for a run.

    A public market profile is valid only when its JSON contains no account or
    private endpoint material.  An account profile must explicitly document a
    read-only contract and a zero-operation observation.  Missing or malformed
    files produce false metrics so a caller cannot turn an absent profile into
    a passing gate by supplying optimistic values in ``metrics.json``.
    """

    canonical_stage = normalize_stage_name(stage)
    metrics: dict[str, Any] = {}

    if stage_requires_market_profile(canonical_stage):
        payload, error = _read_profile_object(market_profile_path)
        markers = _private_profile_markers(payload or {}) if payload is not None else []
        market_profile_contract_valid = bool(
            isinstance(payload, Mapping)
            and payload.get("schema_version") == "bitget-public-market-profile-v1"
            and payload.get("exchange") == "bitget"
            and isinstance(payload.get("symbol"), str)
            and bool(payload.get("symbol"))
            and payload.get("category") == "USDT-FUTURES"
            and payload.get("read_only_only") is True
            and payload.get("trading_operations_count") == 0
            and isinstance(payload.get("instrument"), Mapping)
            and isinstance(payload.get("funding"), Mapping)
            and isinstance(payload.get("mark_and_index"), Mapping)
            and isinstance(payload.get("margin_tiers"), list)
            and bool(payload.get("margin_tiers"))
            and isinstance(payload.get("source_response_sha256"), Mapping)
            and bool(payload.get("source_response_sha256"))
        )
        metrics.update(
            {
                "market_profile_input_present": payload is not None,
                "market_profile_public_api_only": payload is not None and not markers,
                "market_profile_private_api_data_present": bool(markers),
                "market_profile_private_markers_count": len(markers),
                "market_profile_contract_valid": market_profile_contract_valid,
            }
        )
        if payload is not None:
            metrics.update(
                {
                    f"market_profile_{key}": value
                    for key, value in _profile_source_hash_metrics(payload).items()
                }
            )
        if error:
            metrics["market_profile_input_error"] = error

    if stage_requires_account_profile(canonical_stage):
        payload, error = _read_profile_object(account_profile_path)
        payload = payload or {}
        normalized = payload.get("normalized") if isinstance(payload.get("normalized"), Mapping) else {}
        contract = payload.get("account_contract") if isinstance(payload.get("account_contract"), Mapping) else {}
        read_only_declared = (
            payload.get("read_only_only") is True
            or payload.get("account_contract_mode") == "read_only"
            or contract.get("mode") == "read_only"
        )
        operation_count = payload.get("trading_operations_count")
        if operation_count is None:
            operation_count = payload.get("account_contract_trading_operations_count")
        operations_zero = (
            payload.get("trading_operations_count_zero") is True
            or contract.get("trading_operations_count_zero") is True
            or (isinstance(operation_count, (int, float)) and operation_count == 0)
        )
        write_operations = payload.get("write_operations_count")
        if write_operations is None:
            write_operations = contract.get("write_operations_count")
        no_write_operations = isinstance(write_operations, (int, float)) and write_operations == 0
        metrics.update(
            {
                "account_profile_input_present": bool(payload),
                "account_contract_read_only": bool(read_only_declared and operations_zero and no_write_operations),
                "read_only_check": bool(read_only_declared),
                "trading_operations_count_zero": bool(operations_zero),
                "account_mode_uta": normalized.get("account_mode") == "uta",
                "position_mode_hedge": normalized.get("position_mode") == "hedge",
                "margin_mode_isolated": normalized.get("margin_mode") == "isolated",
                "collateral_usdt": normalized.get("collateral_currency") == "USDT",
                "borrowing_disabled": _nested_bool(payload, ("borrowing_disabled",), ("account_controls", "borrowing_disabled")),
                "withdrawals_disabled": _nested_bool(payload, ("withdrawals_disabled",), ("account_controls", "withdrawals_disabled")),
                "ip_allowlist_enabled": _nested_bool(payload, ("ip_allowlist_enabled",), ("account_controls", "ip_allowlist_enabled")),
                "separate_long_short_leverage": bool(
                    normalized.get("leverage_long") is not None
                    and normalized.get("leverage_short") is not None
                ),
                "raw_responses_hashed": isinstance(payload.get("response_sha256"), Mapping)
                and bool(payload.get("response_sha256")),
            }
        )
        if payload:
            metrics.update(
                {
                    f"account_profile_{key}": value
                    for key, value in _profile_source_hash_metrics(payload).items()
                }
            )
        if error:
            metrics["account_profile_input_error"] = error

    return metrics


def build_experiment_manifest(
    *,
    repo_root: str | Path,
    run_id: str,
    stage: str,
    config_path: str | Path,
    dataset_path: str | Path,
    market_profile_path: str | Path | None,
    command: Iterable[str],
    random_seeds: Iterable[int] = (),
    created_at_utc: str | None = None,
    execution_inputs_path: str | Path | None = None,
    account_profile_path: str | Path | None = None,
    risk_evidence: Mapping[str, Any] | None = None,
) -> ExperimentManifest:
    root = Path(repo_root).expanduser().resolve()
    canonical_stage = normalize_stage_name(stage)
    if stage_requires_market_profile(canonical_stage) and market_profile_path is None:
        raise ValueError(f"stage {canonical_stage!r} requires a public market profile")
    if stage_requires_account_profile(canonical_stage) and account_profile_path is None:
        raise ValueError(f"stage {canonical_stage!r} requires a private account profile")
    commit, dirty, dirty_hash = _git_state(root)
    created = created_at_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not str(run_id).strip():
        raise ValueError("run_id must not be empty")
    if not str(stage).strip():
        raise ValueError("stage must not be empty")
    execution_inputs_sha256: str | None = None
    raw_inputs_path: str | Path | None = execution_inputs_path
    if raw_inputs_path is None:
        try:
            config_payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            config_payload = None
        if isinstance(config_payload, Mapping):
            backtest = config_payload.get("backtest")
            model = backtest.get("execution_model") if isinstance(backtest, Mapping) else None
            raw_inputs_path = model.get("inputs_path") if isinstance(model, Mapping) else None
    if raw_inputs_path:
        candidate = Path(str(raw_inputs_path)).expanduser()
        if not candidate.is_absolute():
            candidates = (root / candidate, Path(config_path).expanduser().resolve().parent / candidate)
            candidate = next((path for path in candidates if path.exists()), candidates[0])
        execution_inputs_sha256 = sha256_path(candidate)
    return ExperimentManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        plan_date=PLAN_DATE,
        run_id=str(run_id),
        stage=canonical_stage,
        created_at_utc=created,
        git_commit=commit,
        git_dirty=dirty,
        dirty_diff_sha256=dirty_hash,
        config_sha256=sha256_path(config_path),
        dataset_sha256=sha256_path(dataset_path),
        market_profile_sha256=(sha256_path(market_profile_path) if market_profile_path is not None else None),
        account_profile_sha256=(sha256_path(account_profile_path) if account_profile_path is not None else None),
        command=tuple(str(item) for item in command),
        random_seeds=tuple(int(seed) for seed in random_seeds),
        execution_inputs_sha256=execution_inputs_sha256,
        environment=_environment_passport(),
        implementation_fingerprint_version=IMPLEMENTATION_FINGERPRINT_VERSION,
        implementation_fingerprint_sha256=dirty_hash,
        plan_sha256=(
            sha256_path(root / "docs/plans/crypto_grid_bot_development_plan_2026-08-17.md")
            if (root / "docs/plans/crypto_grid_bot_development_plan_2026-08-17.md").is_file()
            else None
        ),
        risk_evidence=(dict(risk_evidence) if risk_evidence is not None else None),
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _metric(metrics: Mapping[str, Any], name: str) -> Any:
    if name not in metrics:
        raise KeyError(name)
    return metrics[name]


def _gate(
    gates: list[dict[str, Any]],
    *,
    gate_id: str,
    metrics: Mapping[str, Any],
    metric_name: str,
    operator: str,
    limit: float | int | bool,
) -> None:
    try:
        actual = _metric(metrics, metric_name)
    except KeyError:
        gates.append({"id": gate_id, "status": "blocked", "metric": metric_name, "reason": "missing_metric"})
        return
    try:
        if isinstance(limit, bool):
            if isinstance(actual, bool):
                passed = actual is limit
            elif isinstance(actual, (int, float)) and actual in (0, 1):
                passed = bool(actual) is limit
            else:
                passed = False
        elif operator == "<":
            passed = float(actual) < float(limit)
        elif operator == "<=":
            passed = float(actual) <= float(limit)
        elif operator == ">":
            passed = float(actual) > float(limit)
        elif operator == ">=":
            passed = float(actual) >= float(limit)
        elif operator == "==":
            if isinstance(actual, (str, bool)) or isinstance(limit, (str, bool)):
                passed = actual == limit
            else:
                passed = float(actual) == float(limit)
        else:
            raise ValueError(f"unsupported gate operator {operator!r}")
    except (TypeError, ValueError):
        gates.append(
            {
                "id": gate_id,
                "status": "blocked",
                "metric": metric_name,
                "actual": actual,
                "reason": "invalid_metric",
            }
        )
        return
    gates.append(
        {
            "id": gate_id,
            "status": "passed" if passed else "failed",
            "metric": metric_name,
            "actual": actual,
            "operator": operator,
            "limit": limit,
        }
    )


def _verdict(stage: str, gates: list[dict[str, Any]]) -> dict[str, Any]:
    passed = [gate for gate in gates if gate["status"] == "passed"]
    failed = [gate for gate in gates if gate["status"] == "failed"]
    blocked = [gate for gate in gates if gate["status"] == "blocked"]
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "stage": stage,
        "status": "failed" if failed else "blocked" if blocked else "passed",
        "gate_count": len(gates),
        "passed_count": len(passed),
        "failed_count": len(failed),
        "blocked_count": len(blocked),
        "gates": gates,
    }


def _gate_from_legacy_metric(
    gates: list[dict[str, Any]],
    *,
    metrics: Mapping[str, Any],
    metric_name: str,
    legacy_metric_name: str | None = None,
    gate_id: str,
    operator: str = "==",
    limit: float | int | bool = True,
) -> None:
    """Evaluate a new logical metric while accepting an old metric name.

    This is only an input compatibility bridge.  The emitted gate keeps the
    new metric name, so a v3 report cannot be mistaken for a v2 report merely
    because it was fed an old metrics file.
    """

    if metric_name in metrics:
        _gate(
            gates,
            gate_id=gate_id,
            metrics=metrics,
            metric_name=metric_name,
            operator=operator,
            limit=limit,
        )
        return
    if legacy_metric_name is not None and legacy_metric_name in metrics:
        bridged = dict(metrics)
        bridged[metric_name] = metrics[legacy_metric_name]
        _gate(
            gates,
            gate_id=gate_id,
            metrics=bridged,
            metric_name=metric_name,
            operator=operator,
            limit=limit,
        )
        return
    _gate(
        gates,
        gate_id=gate_id,
        metrics=metrics,
        metric_name=metric_name,
        operator=operator,
        limit=limit,
    )


def _add_profile_source_hash_gate(
    gates: list[dict[str, Any]], metrics: Mapping[str, Any], *, prefix: str
) -> None:
    """Reject the old ``pending_run_manifest`` profile placeholder.

    Older hand-built test metrics do not contain this field; they retain their
    v2 behaviour.  Metrics derived by ``profile_input_metrics`` always do, so
    actual profile runs are fail-closed.
    """

    metric_name = f"{prefix}_source_profile_sha256_not_placeholder"
    if metric_name in metrics:
        _gate(
            gates,
            gate_id=f"{prefix}.source_profile_sha256_not_placeholder",
            metrics=metrics,
            metric_name=metric_name,
            operator="==",
            limit=True,
        )


def _add_risk_surface_matrix_gates(
    gates: list[dict[str, Any]],
    metrics: Mapping[str, Any],
    *,
    stage: str,
) -> None:
    """Require the complete research matrix, without promoting it to production."""

    status = metrics.get("risk_surface_matrix_status")
    if status != "passed":
        gates.append(
            {
                "id": f"{stage}.surface_matrix_artifact",
                "status": "failed" if status == "failed" else "blocked",
                "reason": metrics.get(
                    "risk_surface_matrix_error",
                    "complete validated surface matrix is required",
                ),
                "metric": "risk_surface_matrix_status",
                "actual": status,
            }
        )
        return
    for metric_name, operator, limit in (
        ("risk_surface_matrix_validated", "==", True),
        ("risk_surface_matrix_artifact_bound", "==", True),
        ("risk_surface_matrix_source_mode", "==", "research_policy"),
        ("risk_surface_matrix_fixture_mode", "==", True),
        ("risk_surface_matrix_scenario_count", "==", 14),
        ("risk_surface_matrix_passed_count", "==", 14),
        ("risk_surface_matrix_failed_count", "==", 0),
    ):
        _gate(
            gates,
            gate_id=f"{stage}.{metric_name}",
            metrics=metrics,
            metric_name=metric_name,
            operator=operator,
            limit=limit,
        )


def _add_production_risk_evidence_gates(
    gates: list[dict[str, Any]],
    metrics: Mapping[str, Any],
    *,
    stage: str,
) -> None:
    """Require actual Python/Rust evidence before accepting risk parity metrics."""

    status = metrics.get("risk_production_evidence_status")
    if status != "passed":
        gates.append(
            {
                "id": f"{stage}.production_evidence_manifest",
                "status": "failed" if status == "failed" else "blocked",
                "reason": metrics.get(
                    "risk_production_evidence_error",
                    "validated production-path risk evidence is required",
                ),
                "metric": "risk_production_evidence_status",
                "actual": status,
            }
        )
        return
    for metric_name, operator, limit in (
        ("risk_production_evidence_manifest_present", "==", True),
        ("risk_production_evidence_contract_valid", "==", True),
        ("risk_production_path_validated", "==", True),
        ("risk_production_python_live_gate_called", "==", True),
        (
            "risk_production_python_live_gate_method",
            "==",
            "Passivbot._apply_crypto_portfolio_risk_limits",
        ),
        (
            "risk_production_python_live_gate_engine",
            "==",
            "python-live-gate",
        ),
        ("risk_production_rust_backtest_called", "==", True),
        ("risk_production_rust_trace_present", "==", True),
        ("risk_production_fixture_mode", "==", False),
        ("risk_production_research_policy_used", "==", False),
        ("risk_production_candidate_admission", "==", False),
        ("risk_production_trading_operations_count", "==", 0),
        ("risk_production_transfers_count", "==", 0),
        ("risk_production_account_changes_count", "==", 0),
        ("risk_production_probe_bound", "==", True),
        ("risk_production_snapshot_bound", "==", True),
        ("risk_production_config_bound", "==", True),
        ("risk_production_market_profile_bound", "==", True),
        ("risk_production_code_fingerprint_bound", "==", True),
        ("risk_production_report_bound", "==", True),
        ("risk_production_surface_matrix_bound", "==", True),
        ("risk_limits_enforced_in_live_and_tester", "==", True),
        ("risk_parity_reached", "==", True),
    ):
        _gate(
            gates,
            gate_id=f"{stage}.{metric_name}",
            metrics=metrics,
            metric_name=metric_name,
            operator=operator,
            limit=limit,
        )


def _risk_engine_verdict(metrics: Mapping[str, Any], *, stage: str) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    fields = (
        ("risk_engine_implemented", "risk_contract_implemented"),
        ("risk_snapshot_contract_present", "risk_snapshot_contract_present"),
        ("parity_harness_present", "parity_harness_present"),
        ("live_tester_decisions_identical", "live_tester_decisions_identical"),
        ("risk_engine_fails_closed_on_mismatch", "risk_contract_fails_closed_on_mismatch"),
    )
    for metric_name, legacy_metric_name in fields:
        _gate_from_legacy_metric(
            gates,
            metrics=metrics,
            metric_name=metric_name,
            legacy_metric_name=legacy_metric_name if legacy_metric_name != metric_name else None,
            gate_id=f"{stage}.{metric_name}",
            operator="==",
            limit=True,
        )
    _add_risk_surface_matrix_gates(gates, metrics, stage=stage)
    _add_production_risk_evidence_gates(gates, metrics, stage=stage)
    return _verdict(stage, gates)


def _account_risk_surface_verdict(metrics: Mapping[str, Any], *, stage: str) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    fields = (
        "account_profile_input_present",
        "account_contract_read_only",
        "read_only_check",
        "account_mode_uta",
        "position_mode_hedge",
        "margin_mode_isolated",
        "collateral_usdt",
        "borrowing_disabled",
        "withdrawals_disabled",
        "ip_allowlist_enabled",
        "separate_long_short_leverage",
        "raw_responses_hashed",
        "trading_operations_count_zero",
    )
    for legacy_name in fields:
        logical_name = legacy_name.replace("account_profile", "account_risk_surface", 1)
        _gate_from_legacy_metric(
            gates,
            metrics=metrics,
            metric_name=logical_name,
            legacy_metric_name=legacy_name,
            gate_id=f"{stage}.{logical_name.removeprefix('account_risk_surface_')}",
            operator="==",
            limit=True,
        )
    _add_profile_source_hash_gate(
        gates,
        metrics,
        prefix="account_profile" if "account_profile_source_profile_sha256_not_placeholder" in metrics else "account_risk_surface",
    )
    return _verdict(stage, gates)


def _shadow_readiness_verdict(metrics: Mapping[str, Any], *, stage: str) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    for metric_name, operator, limit in (
        ("shadow_evidence_schema_version", "==", "crypto-strategy-shadow-evidence-v1"),
        ("shadow_evidence_status", "==", "passed"),
        ("shadow_candidate_admission", "==", False),
        ("shadow_read_only", "==", True),
        ("shadow_external_network_calls", "==", 0),
        ("shadow_private_requests", "==", 0),
        ("shadow_trading_operations_count", "==", 0),
        ("shadow_public_write_calls", "==", 0),
        ("shadow_determinism_status", "==", "passed"),
        ("shadow_input_provenance_present", "==", True),
        ("shadow_artifact_provenance_present", "==", True),
        ("shadow_artifact_hashes_match", "==", True),
        ("shadow_market_profile_binding_present", "==", True),
        ("shadow_market_profile_binding_matches_input", "==", True),
        ("shadow_market_profile_contract_valid", "==", True),
        ("shadow_duration_hours", ">=", 24),
        ("shadow_planned_orders", ">=", 20),
        ("shadow_restart_count", ">=", 5),
        ("shadow_max_restart_recovery_seconds", "<=", 60),
        ("shadow_duplicate_orders", "==", 0),
        ("shadow_unknown_positions", "==", 0),
        ("shadow_private_calls", "==", 0),
        ("shadow_order_creates", "==", 0),
        ("shadow_transfers", "==", 0),
        ("shadow_events_input_present", "==", True),
        ("shadow_events_present", "==", True),
        ("shadow_determinism_runs", ">=", 3),
        # A readiness result must be produced by the current process
        # supervisor. Snapshot timestamps alone are not wall-clock runtime,
        # and a restarted driver in one Python process is not a process gate.
        ("shadow_process_evidence_present", "==", True),
        ("shadow_process_supervisor_status", "==", "passed"),
        ("shadow_wall_clock_duration_seconds", ">=", 86400),
        ("shadow_process_restart_status", "==", "passed"),
        ("shadow_process_restart_count", ">=", 5),
        ("shadow_process_max_recovery_seconds", "<=", 60),
        ("shadow_process_duplicate_state_count", "==", 0),
        ("shadow_stream_fixture_mode", "==", False),
        ("shadow_process_code_hash_matches_current", "==", True),
        ("shadow_process_config_hash_matches_config", "==", True),
        ("shadow_process_policy_config_hash_matches_config", "==", True),
        ("shadow_process_input_hash_matches_dataset", "==", True),
        ("shadow_stream_gap_check_status", "==", "passed"),
    ):
        _gate(
            gates,
            gate_id=f"{stage}.{metric_name.removeprefix('shadow_')}",
            metrics=metrics,
            metric_name=metric_name,
            operator=operator,
            limit=limit,
        )
    return _verdict(stage, gates)


def evaluate_candidate_gates(metrics: Mapping[str, Any], *, stage: str) -> dict[str, Any]:
    """Evaluate the plan's hard gates without filling missing metrics."""

    if not isinstance(metrics, Mapping):
        raise TypeError("metrics must be a mapping")
    stage = normalize_stage_name(stage)
    gates: list[dict[str, Any]] = []

    if stage == "risk_engine":
        return _risk_engine_verdict(metrics, stage=stage)

    if stage == "account_risk_surface":
        return _account_risk_surface_verdict(metrics, stage=stage)

    if stage == "shadow_readiness":
        return _shadow_readiness_verdict(metrics, stage=stage)

    if stage == "market_profile":
        _gate(
            gates,
            gate_id="market_profile.input_present",
            metrics=metrics,
            metric_name="market_profile_input_present",
            operator="==",
            limit=True,
        )
        _gate(
            gates,
            gate_id="market_profile.public_api_only",
            metrics=metrics,
            metric_name="market_profile_public_api_only",
            operator="==",
            limit=True,
        )
        _gate(
            gates,
            gate_id="market_profile.private_api_data_absent",
            metrics=metrics,
            metric_name="market_profile_private_api_data_present",
            operator="==",
            limit=False,
        )
        _add_profile_source_hash_gate(gates, metrics, prefix="market_profile")
        return _verdict(stage, gates)

    if stage == "account_profile":
        for metric_name in (
            "account_profile_input_present",
            "account_contract_read_only",
            "read_only_check",
            "account_mode_uta",
            "position_mode_hedge",
            "margin_mode_isolated",
            "collateral_usdt",
            "borrowing_disabled",
            "withdrawals_disabled",
            "ip_allowlist_enabled",
            "separate_long_short_leverage",
            "raw_responses_hashed",
            "trading_operations_count_zero",
        ):
            _gate(
                gates,
                gate_id=f"account_profile.{metric_name}",
                metrics=metrics,
                metric_name=metric_name,
                operator="==",
                limit=True,
            )
        _add_profile_source_hash_gate(gates, metrics, prefix="account_profile")
        return _verdict(stage, gates)

    if stage in MARKET_PROFILE_STAGES:
        for metric_name, limit in (
            ("market_profile_input_present", True),
            ("market_profile_public_api_only", True),
            ("market_profile_private_api_data_present", False),
        ):
            _gate(
                gates,
                gate_id=f"market_profile.{metric_name}",
                metrics=metrics,
                metric_name=metric_name,
                operator="==",
                limit=limit,
            )
        _add_profile_source_hash_gate(gates, metrics, prefix="market_profile")

    if stage in ACCOUNT_PROFILE_STAGES:
        for metric_name in ("account_profile_input_present", "account_contract_read_only"):
            _gate(
                gates,
                gate_id=f"account_profile.{metric_name}",
                metrics=metrics,
                metric_name=metric_name,
                operator="==",
                limit=True,
            )
        _add_profile_source_hash_gate(gates, metrics, prefix="account_profile")

    if stage == "baseline":
        def baseline_status(gate_id: str, metric_name: str) -> None:
            if metric_name not in metrics:
                gates.append({"id": gate_id, "status": "blocked", "metric": metric_name, "reason": "missing_metric"})
                return
            actual = str(metrics[metric_name]).lower()
            if actual == "passed":
                status = "passed"
            elif actual == "blocked":
                status = "blocked"
            else:
                status = "failed"
            gates.append({"id": gate_id, "status": status, "metric": metric_name, "actual": metrics[metric_name]})

        baseline_status("baseline.python_tests", "python_tests_status")
        baseline_status("baseline.fake_live_tests", "fake_live_tests_status")
        baseline_status("baseline.rust_tests", "rust_tests_status")
        baseline_status("baseline.editable_install", "editable_install_status")
        baseline_status("baseline.determinism", "determinism_status")
        passed = [gate for gate in gates if gate["status"] == "passed"]
        failed = [gate for gate in gates if gate["status"] == "failed"]
        blocked = [gate for gate in gates if gate["status"] == "blocked"]
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "stage": stage,
            "status": "failed" if failed else "blocked" if blocked else "passed",
            "gate_count": len(gates),
            "passed_count": len(passed),
            "failed_count": len(failed),
            "blocked_count": len(blocked),
            "gates": gates,
        }

    if stage == "data":
        for metric_name in (
            "history_through_plan_date",
            "ohlcv_cache_quality_complete",
            "funding_history_complete",
            "mark_price_history_complete",
            "position_tiers_captured",
            "execution_model_implemented",
        ):
            _gate(
                gates,
                gate_id=f"data.{metric_name}",
                metrics=metrics,
                metric_name=metric_name,
                operator="==",
                limit=True,
            )
        _gate(gates, gate_id="data.history_coverage", metrics=metrics, metric_name="history_coverage_ratio", operator=">=", limit=0.9995)
        _gate(gates, gate_id="data.synthetic_rows_marked", metrics=metrics, metric_name="unmarked_synthetic_rows", operator="==", limit=0)
        _gate(gates, gate_id="data.nan_rows", metrics=metrics, metric_name="nan_rows", operator="==", limit=0)
        _gate(gates, gate_id="data.future_data", metrics=metrics, metric_name="future_data_violations", operator="==", limit=0)
        _gate(gates, gate_id="data.funding_coverage", metrics=metrics, metric_name="funding_known_coverage_ratio", operator=">=", limit=1.0)
        _gate(gates, gate_id="data.determinism", metrics=metrics, metric_name="identical_run_count", operator=">=", limit=3)
        passed = [gate for gate in gates if gate["status"] == "passed"]
        failed = [gate for gate in gates if gate["status"] == "failed"]
        blocked = [gate for gate in gates if gate["status"] == "blocked"]
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "stage": stage,
            "status": "failed" if failed else "blocked" if blocked else "passed",
            "gate_count": len(gates),
            "passed_count": len(passed),
            "failed_count": len(failed),
            "blocked_count": len(blocked),
            "gates": gates,
        }

    if stage == "risk_grid":
        for metric_name in (
            "roadmap_config_eligible",
            "scenario_coverage_complete",
            "no_liquidations",
            "no_duplicate_orders",
            "limits_not_bypassed",
            "state_recovery_consistent",
            "api_outage_safe",
            "manual_position_safe",
            "ordinary_market_orders_blocked",
        ):
            _gate(
                gates,
                gate_id=f"risk_grid.{metric_name}",
                metrics=metrics,
                metric_name=metric_name,
                operator="==",
                limit=True,
            )
        return _verdict(stage, gates)

    if stage == "risk_contract":
        for metric_name in (
            "risk_contract_implemented",
            "risk_snapshot_contract_present",
            "parity_harness_present",
            "live_tester_decisions_identical",
            "risk_contract_fails_closed_on_mismatch",
            "risk_model_surface_validated",
        ):
            _gate(
                gates,
                gate_id=f"risk_contract.{metric_name}",
                metrics=metrics,
                metric_name=metric_name,
                operator="==",
                limit=True,
            )
        _add_risk_surface_matrix_gates(gates, metrics, stage=stage)
        _add_production_risk_evidence_gates(gates, metrics, stage=stage)
        return _verdict(stage, gates)

    _gate(gates, gate_id="risk.max_drawdown", metrics=metrics, metric_name="max_drawdown", operator="<=", limit=0.20)
    _gate(gates, gate_id="risk.liquidations", metrics=metrics, metric_name="liquidations", operator="==", limit=0)
    _gate(gates, gate_id="risk.max_recovery_days", metrics=metrics, metric_name="max_recovery_days", operator="<=", limit=90)
    _gate(gates, gate_id="risk.gross_exposure_ratio", metrics=metrics, metric_name="gross_exposure_ratio", operator="<=", limit=0.80)
    _gate(gates, gate_id="risk.used_margin_ratio", metrics=metrics, metric_name="used_margin_ratio", operator="<=", limit=0.35)
    _gate(gates, gate_id="risk.min_liquidation_distance", metrics=metrics, metric_name="min_liquidation_distance", operator=">=", limit=0.15)
    _gate(gates, gate_id="execution.slippage_median_bps", metrics=metrics, metric_name="execution_slippage_median_bps", operator="<=", limit=2)
    _gate(gates, gate_id="execution.slippage_p95_bps", metrics=metrics, metric_name="execution_slippage_p95_bps", operator="<=", limit=10)
    _gate(gates, gate_id="execution.slippage_max_bps", metrics=metrics, metric_name="execution_slippage_max_bps", operator="<=", limit=25)
    _gate(gates, gate_id="risk.daily_loss", metrics=metrics, metric_name="max_daily_loss", operator="<", limit=0.02)
    _gate(gates, gate_id="risk.reconciliation", metrics=metrics, metric_name="reconciliation_mismatches", operator="==", limit=0)

    if stage in {"historical", "portfolio_historical"}:
        _gate(gates, gate_id="candidate.validation_windows", metrics=metrics, metric_name="validation_windows", operator="==", limit=4)
        _gate(gates, gate_id="candidate.independent_optimizer_runs", metrics=metrics, metric_name="independent_optimizer_runs", operator=">=", limit=5)
        for metric_name in (
            "sides_evaluated_independently",
            "neighbor_checks_complete",
            "holdout_reserved",
            "execution_stresses_complete",
        ):
            _gate(
                gates,
                gate_id=f"candidate.{metric_name}",
                metrics=metrics,
                metric_name=metric_name,
                operator="==",
                limit=True,
            )
        _gate(gates, gate_id="candidate.validation_return", metrics=metrics, metric_name="positive_validation_windows", operator="==", limit=metrics.get("validation_windows", 0))
        _gate(gates, gate_id="candidate.cycles_per_window", metrics=metrics, metric_name="closed_cycles_per_window_min", operator=">=", limit=20)
        _gate(gates, gate_id="candidate.stress_equity", metrics=metrics, metric_name="stress_min_equity_ratio", operator=">=", limit=0.75)
        _gate(gates, gate_id="candidate.neighbor_stability", metrics=metrics, metric_name="neighbor_profitable_ratio", operator=">=", limit=0.80)
        _gate(gates, gate_id="candidate.events_present", metrics=metrics, metric_name="events_present", operator="==", limit=True)
        _gate(gates, gate_id="candidate.net_result", metrics=metrics, metric_name="net_result", operator=">", limit=0)
        if stage == "portfolio_historical":
            for metric_name in (
                "symbols_independently_verified",
                "synchronized_timeline",
                "correlation_one_stress_complete",
            ):
                _gate(
                    gates,
                    gate_id=f"portfolio.{metric_name}",
                    metrics=metrics,
                    metric_name=metric_name,
                    operator="==",
                    limit=True,
                )
            _gate(
                gates,
                gate_id="portfolio.max_active_grids",
                metrics=metrics,
                metric_name="max_active_grids",
                operator="<=",
                limit=2,
            )
    elif stage in {"paper_runtime", "portfolio_paper"}:
        _gate(gates, gate_id="paper_runtime.days", metrics=metrics, metric_name="days", operator=">=", limit=90)
        _gate(gates, gate_id="paper_runtime.planned_orders", metrics=metrics, metric_name="planned_orders", operator=">=", limit=100)
        _gate(gates, gate_id="paper_runtime.closed_cycles", metrics=metrics, metric_name="closed_cycles", operator=">=", limit=30)
        _gate(gates, gate_id="paper_runtime.uptime", metrics=metrics, metric_name="uptime_ratio", operator=">=", limit=0.995)
        _gate(gates, gate_id="paper_runtime.restart_recovery", metrics=metrics, metric_name="max_restart_recovery_seconds", operator="<=", limit=60)
        _gate(gates, gate_id="paper_runtime.duplicate_orders", metrics=metrics, metric_name="duplicate_orders", operator="==", limit=0)
        _gate(gates, gate_id="paper_runtime.unknown_positions", metrics=metrics, metric_name="unknown_positions", operator="==", limit=0)
        _gate(gates, gate_id="paper_runtime.costs_present", metrics=metrics, metric_name="all_costs_present", operator="==", limit=True)
        _gate(gates, gate_id="paper_runtime.events_present", metrics=metrics, metric_name="events_present", operator="==", limit=True)
        _gate(gates, gate_id="paper_runtime.net_result", metrics=metrics, metric_name="net_result", operator=">", limit=0)
    elif stage == "technical_live":
        _gate(gates, gate_id="technical.days", metrics=metrics, metric_name="days", operator=">=", limit=30)
        _gate(gates, gate_id="technical.fills", metrics=metrics, metric_name="fills", operator=">=", limit=50)
        _gate(gates, gate_id="technical.closed_cycles", metrics=metrics, metric_name="closed_cycles", operator=">=", limit=10)
        _gate(gates, gate_id="technical.fee_reconciliation", metrics=metrics, metric_name="fee_reconciliation_abs_usdt", operator="<=", limit=0.01)
        _gate(gates, gate_id="technical.funding_reconciliation", metrics=metrics, metric_name="funding_reconciliation_abs_usdt", operator="<=", limit=0.01)
        _gate(gates, gate_id="technical.execution", metrics=metrics, metric_name="execution_p95_bps", operator="<=", limit=10)
        _gate(gates, gate_id="technical.confirmation", metrics=metrics, metric_name="order_confirmation_p95_seconds", operator="<=", limit=5)
        _gate(gates, gate_id="technical.drawdown", metrics=metrics, metric_name="max_drawdown", operator="<=", limit=0.05)
        _gate(gates, gate_id="technical.hsl", metrics=metrics, metric_name="hsl_emergency_triggers", operator="==", limit=0)
        _gate(gates, gate_id="technical.events_present", metrics=metrics, metric_name="events_present", operator="==", limit=True)
    elif stage == "controlled_live":
        _gate(gates, gate_id="controlled.days", metrics=metrics, metric_name="days", operator=">=", limit=90)
        _gate(gates, gate_id="controlled.closed_cycles", metrics=metrics, metric_name="closed_cycles", operator=">=", limit=30)
        _gate(gates, gate_id="controlled.net_result", metrics=metrics, metric_name="net_result", operator=">", limit=0)
        _gate(gates, gate_id="controlled.terminal_stop", metrics=metrics, metric_name="terminal_stop_reached", operator="==", limit=False)
        _gate(gates, gate_id="controlled.uptime", metrics=metrics, metric_name="uptime_ratio", operator=">=", limit=0.995)
        _gate(gates, gate_id="controlled.events_present", metrics=metrics, metric_name="events_present", operator="==", limit=True)

    passed = [gate for gate in gates if gate["status"] == "passed"]
    failed = [gate for gate in gates if gate["status"] == "failed"]
    blocked = [gate for gate in gates if gate["status"] == "blocked"]
    status = "failed" if failed else "blocked" if blocked else "passed"
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "stage": stage,
        "status": status,
        "gate_count": len(gates),
        "passed_count": len(passed),
        "failed_count": len(failed),
        "blocked_count": len(blocked),
        "gates": gates,
    }


def apply_stage_prerequisite_gate(
    verdict: Mapping[str, Any],
    status: Mapping[str, Any],
    *,
    stage: str,
) -> dict[str, Any]:
    """Block promotion when any earlier roadmap stage is not passed."""

    gated = json.loads(json.dumps(verdict))
    canonical_stage = normalize_stage_name(stage)
    gated["stage"] = canonical_stage
    status_schema = status.get("schema_version") if isinstance(status, Mapping) else None
    prerequisites_map = (
        STAGE_PREREQUISITES_V3
        if status_schema == "crypto-grid-roadmap-status-v3"
        else STAGE_PREREQUISITES
    )
    prerequisites = prerequisites_map.get(canonical_stage, ())
    stages = status.get("stages", {}) if isinstance(status, Mapping) else {}
    blockers: list[dict[str, Any]] = []
    aliases_used: list[dict[str, str]] = []
    source_verdict_status = verdict.get("status") if isinstance(verdict, Mapping) else None
    existing_gate_statuses = {
        gate.get("status")
        for gate in gated.get("gates", [])
        if isinstance(gate, Mapping)
    }
    if source_verdict_status in {"failed", "blocked", "stale"} and not (
        {"failed", "blocked"} & existing_gate_statuses
    ):
        gated.setdefault("gates", []).append(
            {
                "id": "stage.verdict_status",
                "status": "blocked" if source_verdict_status == "stale" else source_verdict_status,
                "reason": f"source_verdict_{source_verdict_status}",
            }
        )
    for prerequisite in prerequisites:
        source_stage = prerequisite
        entry = stages.get(prerequisite, {}) if isinstance(stages, Mapping) else {}
        if (
            (not isinstance(entry, Mapping) or not entry)
            and isinstance(stages, Mapping)
        ):
            legacy_stage = next(
                (
                    alias
                    for alias, target in LEGACY_STAGE_ALIASES.items()
                    if target == prerequisite and alias in stages
                ),
                None,
            )
            if legacy_stage is not None:
                source_stage = legacy_stage
                entry = stages.get(legacy_stage, {})
                aliases_used.append({"legacy": legacy_stage, "canonical": prerequisite})
        if isinstance(entry, Mapping):
            if status_schema == "crypto-grid-roadmap-status-v3":
                if entry.get("freshness") == "stale":
                    actual = "stale"
                else:
                    actual = entry.get(
                        "stage_admission_status",
                        entry.get("status", "not_started"),
                    )
            else:
                actual = entry.get("status", "not_started")
        else:
            actual = "not_started"
        if actual != "passed":
            blocker = {"stage": prerequisite, "status": actual}
            if source_stage != prerequisite:
                blocker["status_stage"] = source_stage
            blockers.append(blocker)
    if aliases_used:
        gated["legacy_stage_aliases"] = aliases_used
    if blockers:
        gated.setdefault("gates", []).append(
            {
                "id": "stage.prerequisites",
                "status": "blocked",
                "required": list(prerequisites),
                "blockers": blockers,
            }
        )
    gates = gated.get("gates", [])
    gated["gate_count"] = len(gates)
    gated["passed_count"] = sum(gate.get("status") == "passed" for gate in gates)
    gated["failed_count"] = sum(gate.get("status") == "failed" for gate in gates)
    gated["blocked_count"] = sum(gate.get("status") == "blocked" for gate in gates)
    gated["status"] = (
        "failed"
        if gated["failed_count"]
        else "blocked"
        if gated["blocked_count"]
        else "passed"
    )
    gated["check_status"] = source_verdict_status
    gated["stage_admission_status"] = gated["status"]
    gated["outcome"] = gated["status"]
    return gated


def _report_from_verdict(verdict: Mapping[str, Any]) -> str:
    lines = [
        f"# Проверка исследовательского запуска: {verdict.get('stage', 'unknown')}",
        "",
        f"Результат: **{verdict.get('status', 'unknown')}**.",
        "",
        "| Gate | Fact | Condition | Limit | Status |",
        "|---|---:|:---:|---:|---|",
    ]
    for gate in verdict.get("gates", []):
        if gate.get("status") == "blocked":
            reason = gate.get("reason", "missing")
            if gate.get("id") == "stage.prerequisites":
                reason = ", ".join(
                    f"{item.get('stage')}={item.get('status')}"
                    for item in gate.get("blockers", [])
                ) or "not passed"
            lines.append(f"| `{gate['id']}` | — | — | — | blocked: {reason} |")
        else:
            lines.append(
                f"| `{gate['id']}` | {gate.get('actual')} | {gate.get('operator')} | {gate.get('limit')} | {gate.get('status')} |"
            )
    lines.extend(
        [
            "",
            "Any gate with status `blocked` or `failed` stops admission; limits are not weakened.",
            "",
        ]
    )
    return "\n".join(lines)


def write_run_artifacts(
    *,
    run_dir: str | Path,
    manifest: ExperimentManifest,
    metrics: Mapping[str, Any],
    verdict: Mapping[str, Any],
    config_path: str | Path,
    market_profile_path: str | Path | None,
    account_profile_path: str | Path | None = None,
    events_path: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Write the complete run bundle and fail instead of silently overwriting it."""

    destination = Path(run_dir).expanduser().resolve()
    stage = normalize_stage_name(manifest.stage)
    if stage_requires_market_profile(stage) and market_profile_path is None:
        raise ValueError(f"stage {stage!r} requires market_profile.json")
    if stage_requires_account_profile(stage) and account_profile_path is None:
        raise ValueError(f"stage {stage!r} requires account_profile.json")
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"run directory is not empty and is immutable: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "manifest.json", manifest.to_dict())
    _write_json(destination / "metrics.json", dict(metrics))
    _write_json(destination / "verdict.json", dict(verdict))
    (destination / "report.md").write_text(_report_from_verdict(verdict), encoding="utf-8")
    (destination / "config.json").write_bytes(Path(config_path).read_bytes())
    if market_profile_path is not None:
        (destination / "market_profile.json").write_bytes(Path(market_profile_path).read_bytes())
    if account_profile_path is not None:
        (destination / "account_profile.json").write_bytes(Path(account_profile_path).read_bytes())
    events_destination = destination / "events.jsonl.gz"
    if events_path is None:
        with gzip.open(events_destination, "wb") as events:
            events.write(b"")
    else:
        source = Path(events_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"events input does not exist: {source}")
        if source.suffix == ".gz":
            events_destination.write_bytes(source.read_bytes())
        else:
            with gzip.open(events_destination, "wb") as events:
                events.write(source.read_bytes())
    return destination
