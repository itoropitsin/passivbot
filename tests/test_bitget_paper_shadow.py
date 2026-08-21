from __future__ import annotations

import gzip
import json

import pytest

from exchanges.paper import PaperBitgetCCXTClient
from test_paper_bitget import PublicBitgetStub
from tools.bitget_paper_shadow import run_shadow


@pytest.mark.asyncio
async def test_bounded_shadow_is_public_only_and_never_calls_live_entrypoint(tmp_path):
    def factory(config, user_info, **kwargs):
        return PaperBitgetCCXTClient(
            config,
            user_info,
            public_client=PublicBitgetStub(),
            **kwargs,
        )

    result = await run_shadow(
        output_dir=tmp_path / "shadow-run",
        duration_seconds=604800.0,
        poll_seconds=60.0,
        max_observations=2,
        client_factory=factory,
    )

    assert result["verdict"]["status"] == "no_trade_observed"
    assert result["verdict"]["checks"]["public_only_request_log"] is True
    assert result["verdict"]["checks"]["no_order_creates"] is True
    assert result["verdict"]["counts"]["observations"] == 2
    assert result["verdict"]["counts"]["fills"] == 0

    run_dir = tmp_path / "shadow-run"
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "config.json").is_file()
    assert (run_dir / "market_profile.json").is_file()
    assert (run_dir / "ledger.json").is_file()
    assert (run_dir / "checksums.json").is_file()
    with gzip.open(run_dir / "events.jsonl.gz", "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    assert len(rows) == 4
    assert [row["kind"] for row in rows] == [
        "market.observation",
        "shadow.heartbeat",
        "market.observation",
        "shadow.heartbeat",
    ]
    assert all(row["payload_hash"] for row in rows)

    # The persisted profile contains only the sanitized paper identity and no
    # credential-store path or live-account setting.
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert config["_paper_mode"] is True
    assert "api_key" not in json.dumps(config).lower()
    assert "api-keys" not in json.dumps(config).lower()

