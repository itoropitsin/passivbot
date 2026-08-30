# Development Commands

Use from repo root unless noted.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -e .              # live-only
# or: python3 -m pip install -e ".[full]"
# or: python3 -m pip install -e ".[dev]"
```

## Tests

```bash
pytest
pytest tests/test_specific.py
pytest tests/test_specific.py::test_name
```

## Live Bot

```bash
passivbot live -u {account_name}
passivbot live path/to/config.json --log-level {warning|info|debug|trace|0-3}
```

## Backtest

```bash
passivbot backtest path/to/config.json
passivbot backtest --suite
```

## Optimize

```bash
passivbot optimize path/to/config.json
passivbot optimize --suite
```

## Rust

```bash
cd passivbot-rust && maturin develop --release && cd ..
PYO3_PYTHON="$(pwd)/.venv/bin/python" passivbot-rust/cargotest.sh
cd passivbot-rust && cargo check --tests && cd ..
```

On macOS, direct `cargo test` does not link the Python runtime required by the
PyO3 test binary. `cargotest.sh` locates and links the selected interpreter;
pass an absolute `PYO3_PYTHON` path because the script changes into
`passivbot-rust/` before invoking Cargo.

## Useful Tools

```bash
passivbot tool pareto optimize_results/.../pareto
passivbot tool pareto-compress optimize_results/.../pareto 8 --output-dir selected_pareto_8
passivbot tool pareto-dash --data-root optimize_results
passivbot tool verify-hlcvs-data
passivbot tool ohlcvs-doctor --repair-catalog
passivbot tool streamline-json configs/examples/default_trailing_grid_long_npos7.json
```

## High-Signal Gotcha

If Rust changes seem ignored by tests/runtime, rebuild extension again before debugging behavior.

## Synology public shadow (no keys)

DS923+ project: `/volume1/docker/_cm/passivbot-shadow/`. Build native amd64, cap CPU:

```bash
ssh -p 20022 ivan@192.168.178.102
export PATH=$PATH:/usr/local/bin
cd /volume1/docker/_cm/passivbot-shadow
docker build --cpu-period=100000 --cpu-quota=100000 -t passivbot-shadow:v6 -f repo/Dockerfile_shadow repo
SHADOW_RESUME=1 docker compose up -d --no-build   # unfinished copied journal, gap < 300s
# SHADOW_RESUME=0 docker compose up -d --no-build  # empty new directory only
```

On this DS923+ DSM kernel, Compose `cpus:` / PID limits are discarded (no CFS/cgroup). Leave them out. Runtime is a 60s public poll, so it stays cheap next to Immich/Jellyfin.

macOS `openrsync` does not pass SSH port 20022; copy with `tar|ssh` or `ssh cat`. Do not copy `api-keys.json`. After a Mac writer stop, the next NAS snapshot must land within `max_source_gap_seconds` (300). NAS wall-clock of this supervisor must still reach 24 hours.

The finished v6 exam container is `passivbot-shadow` (`exited 0`). Do not `docker start` it. A new 24h uses a new output directory and a new container name:

```bash
ssh -p 20022 ivan@192.168.178.102
export PATH=$PATH:/usr/local/bin
# SHADOW_RESUME must stay 0. Do not pre-create the output directory.
docker run -d --name passivbot-shadow-v7 --restart on-failure:1 --memory 768m \
  --security-opt no-new-privileges:true \
  -e TZ=Europe/Amsterdam -e PYTHONUNBUFFERED=1 -e SHADOW_RESUME=0 \
  -v /volume1/docker/_cm/passivbot-shadow/data:/data \
  -v /volume1/docker/_cm/passivbot-shadow/inputs:/inputs:ro \
  -w /app passivbot-shadow:v6 \
  --output-dir /data/public-stream-process-24h-2026-08-21-v7 \
  --config /inputs/strategy_shadow_stream_config_v1.json \
  --market-profile /inputs/bitget_btc_market_profile_public_only_2026-08-17.json \
  --symbol BTCUSDT --category USDT-FUTURES --depth 20 --side long \
  --interval-seconds 60 --max-source-gap-seconds 300 \
  --segment-duration-seconds 14520 --restart-count 5 \
  --deterministic-replay-runs 3 --repo-root /app
```

Live evidence: `passivbot-shadow-v7` finished `2026-08-22T01:29:41Z`, `exit=0`. Original `result.json` sha256 `5b427211…` must not be modified. Local copy: `analysis_reports/crypto_bot_roadmap/shadow_runs/public-stream-process-24h-2026-08-21-v7/`. Mac gates used `result.mac-path-bind-2026-08-24.json`. Bound machine packet: `shadow-readiness-2026-08-24-exam7` (40/40, admission passed) on fingerprint `f5af0643…`. Do not `docker start` the exam containers. Do not overwrite EX-6/EX-7 `result.json` or `shadow-readiness-2026-08-21-v25`.

## Bitget funding archive (edition 1.74)

Public full archive was not found on 2026-08-24: 248/2332 applied settlements in `[2024-07-01T00:00:00Z, 2026-08-17T00:00:00Z]`. Do not repeat exhausted public funding GETs. Do not overwrite the frozen execution input. Do not write `status.json` by hand.

Next input is a user file or explicit paid-source permission, then validate and promote into a **new** path:

```bash
passivbot tool bitget-funding-archive \
  --archive /path/to/user_or_licensed_bitget_funding.csv \
  --symbol BTCUSDT \
  --start 2024-07-01T00:00:00+00:00 \
  --end 2026-08-17T00:00:00+00:00 \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_archive_validation_new.json \
  --normalized-output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_archive_normalized_new.json

passivbot tool bitget-funding-promote \
  --archive /path/to/user_or_licensed_bitget_funding.csv \
  --symbol BTCUSDT \
  --start 2024-07-01T00:00:00+00:00 \
  --end 2026-08-17T00:00:00+00:00 \
  --execution-inputs analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_public_execution_inputs_2024-07-01_to_2026-08-17-repaired-pagination-v2.json \
  --validation-output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_funding_promotion_validation_new.json \
  --output analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_public_execution_inputs_2024-07-01_to_2026-08-17-promoted-new.json
```

`--output` must be a new path that does not exist. `--execution-inputs` is the current frozen file and is read, not overwritten. Incomplete archives stay `blocked`.

