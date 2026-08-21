from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from exchanges.paper import PaperBitgetCCXTClient
from test_paper_bitget import PublicBitgetStub
from tools import bitget_paper_shadow as shadow


def _factory_with_request_log(*rows):
    def factory(config, user_info, **kwargs):
        client = PaperBitgetCCXTClient(
            config,
            user_info,
            public_client=PublicBitgetStub(),
            **kwargs,
        )
        # This deliberately replaces the local request log. The observer must
        # treat the substituted entries as evidence, not as trusted metadata.
        client.request_log = [dict(row) for row in rows]
        return client

    return factory


@pytest.mark.asyncio
async def test_tampered_request_log_fails_the_bounded_verdict(tmp_path):
    result = await shadow.run_shadow(
        output_dir=tmp_path / "tampered-request-log",
        duration_seconds=60.0,
        max_observations=1,
        client_factory=_factory_with_request_log(
            {"method": "private_get_account", "timestamp": 1},
            {"method": "create_order", "timestamp": 2},
            {"method": "private_transfer", "timestamp": 3},
        ),
    )

    verdict = result["verdict"]
    assert verdict["status"] == "failed"
    assert verdict["checks"]["public_only_request_log"] is False
    assert verdict["checks"]["no_order_creates"] is False
    assert verdict["checks"]["no_transfers"] is False
    assert verdict["private_boundary"]["public_client_only"] is False
    assert verdict["qualification"]["is_success"] is False


@pytest.mark.asyncio
async def test_checksums_are_verified_after_write_and_detect_later_tampering(tmp_path):
    result = await shadow.run_shadow(
        output_dir=tmp_path / "checksum-run",
        duration_seconds=60.0,
        max_observations=1,
        client_factory=_factory_with_request_log(),
    )
    run_dir = Path(result["run_dir"])
    checksums_path = run_dir / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))

    assert result["verdict"]["checks"]["checksums_verified"] is True
    assert shadow._verify_checksums(run_dir, checksums) is True
    for filename, expected in checksums["files"].items():
        digest = hashlib.sha256((run_dir / filename).read_bytes()).hexdigest()
        assert digest == expected

    events_path = run_dir / "events.jsonl.gz"
    events_path.write_bytes(events_path.read_bytes() + b"tampered")
    assert shadow._verify_checksums(run_dir, checksums) is False


def test_module_and_direct_invocation_have_the_same_import_and_help_behavior():
    repo = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repo / "src")
    direct = subprocess.run(
        [sys.executable, str(repo / "src/tools/bitget_paper_shadow.py"), "--help"],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    module = subprocess.run(
        [sys.executable, "-m", "tools.bitget_paper_shadow", "--help"],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert direct.returncode == 0, direct.stderr
    assert module.returncode == 0, module.stderr
    assert direct.stdout == module.stdout
    assert direct.stderr == module.stderr == ""


def test_main_returns_nonzero_for_a_failed_verdict(monkeypatch, tmp_path):
    async def failed_run(**_kwargs):
        return {"verdict": {"status": "failed"}}

    monkeypatch.setattr(shadow, "run_shadow", failed_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bitget_paper_shadow.py",
            "--output-dir",
            str(tmp_path / "main-check"),
            "--duration-seconds",
            "1",
        ],
    )

    assert shadow.main() == 1
