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

Live evidence: `passivbot-shadow-v7` started `2026-08-20T23:58:33Z`. Do not bind `shadow-readiness` until `result.json` and `process_supervisor.json` exist.

