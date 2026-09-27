from __future__ import annotations

import copy

import pytest

from tli_wings.evidence import (
    DATA_QUALITY_SCHEMA,
    RISK_EVIDENCE_SCHEMA,
    DataQualityError,
    RiskEvidenceError,
    build_risk_evidence,
    build_version_fingerprint,
    data_quality_gate,
    require_data_quality,
    validate_risk_evidence,
)


def _fingerprint_paths(root):
    return {
        "tli_code": ["src"],
        "rust": ["rust"],
        "config": ["config"],
        "policy": ["policy"],
        "dependencies": ["requirements.txt"],
        "inputs": ["inputs"],
    }


def test_version_fingerprint_is_deterministic_and_excludes_docs_and_reports(tmp_path):
    for directory in ("src", "rust", "config", "policy", "inputs", "docs", "reports"):
        (tmp_path / directory).mkdir()
    (tmp_path / "src" / "engine.py").write_text("return 1\n", encoding="utf-8")
    (tmp_path / "rust" / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")
    (tmp_path / "config" / "bot.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "policy" / "rules.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "inputs" / "market.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("numpy==1\n", encoding="utf-8")

    first = build_version_fingerprint(tmp_path, components=_fingerprint_paths(tmp_path))
    second = build_version_fingerprint(tmp_path, components=_fingerprint_paths(tmp_path))
    assert first["schema_version"] == "tli-version-fingerprint-v1"
    assert first["fingerprint"] == second["fingerprint"]
    assert set(first["components"]) == {
        "tli_code",
        "rust",
        "config",
        "policy",
        "dependencies",
        "inputs",
    }

    (tmp_path / "docs" / "README.md").write_text("changed\n", encoding="utf-8")
    (tmp_path / "reports" / "run.json").write_text("changed\n", encoding="utf-8")
    assert build_version_fingerprint(tmp_path, components=_fingerprint_paths(tmp_path))["fingerprint"] == first["fingerprint"]

    (tmp_path / "src" / "engine.py").write_text("return 2\n", encoding="utf-8")
    assert build_version_fingerprint(tmp_path, components=_fingerprint_paths(tmp_path))["fingerprint"] != first["fingerprint"]


def test_incomplete_248_of_2332_is_failed_but_fresh():
    quality = data_quality_gate(actual_rows=248)
    assert quality["schema_version"] == DATA_QUALITY_SCHEMA
    assert quality["status"] == "failed"
    assert quality["freshness"] == "fresh"
    assert quality["actual_rows"] == 248
    assert quality["expected_rows"] == 2332
    assert quality["missing_rows"] == 2084
    assert quality["qualifying"] is False
    assert "funding_window_incomplete" in quality["reasons"]

    with pytest.raises(DataQualityError, match="funding data quality gate failed"):
        require_data_quality(actual_rows=248)


def test_synthetic_funding_is_rejected_even_when_counts_look_complete():
    rows = [
        {"timestamp_ms": 1, "funding_rate": 0.001, "synthetic": True},
        {"timestamp_ms": 2, "funding_rate": 0.001},
    ]
    quality = data_quality_gate(rows, expected_rows=2)
    assert quality["actual_rows"] == 1
    assert quality["synthetic_rows"] == 1
    assert quality["status"] == "failed"
    assert quality["freshness"] == "fresh"


def test_v2_risk_evidence_preserves_blocked_data_gate_and_rejects_v1():
    fingerprint = build_version_fingerprint(
        ".", components={
            "tli_code": ["src/tli_wings"],
            "rust": [],
            "config": [],
            "policy": [],
            "dependencies": [],
            "inputs": [],
        }
    )
    quality = data_quality_gate(actual_rows=248)
    evidence = build_risk_evidence(
        fingerprint=fingerprint,
        data_quality=quality,
        risk_trace={"status": "passed", "parity": "passed"},
    )
    assert evidence["schema_version"] == RISK_EVIDENCE_SCHEMA
    assert evidence["status"] == "blocked"
    assert evidence["admission"] is False
    validated = validate_risk_evidence(evidence, expected_fingerprint=fingerprint)
    assert validated["valid"] is True
    assert validated["status"] == "blocked"

    old = copy.deepcopy(evidence)
    old["schema_version"] = "risk-parity-production-evidence-v1"
    with pytest.raises(RiskEvidenceError, match="schema is unsupported"):
        validate_risk_evidence(old)


def test_risk_evidence_rejects_private_or_write_activity():
    fingerprint = "a" * 64
    with pytest.raises(RiskEvidenceError, match="private, write, or trading"):
        build_risk_evidence(
            fingerprint=fingerprint,
            data_quality=data_quality_gate(actual_rows=248),
            risk_trace={"status": "passed", "parity": "passed", "write_calls": 1},
        )
