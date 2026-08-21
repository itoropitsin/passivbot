#!/bin/sh
# Host cutover helper: Mac public shadow -> Synology Docker.
# The Bitget source-gap limit is 300s. After this script stops the Mac
# writer, the NAS child must append the next snapshot inside that window.
# macOS openrsync does not pass SSH port 20022 reliably; copy via tar|ssh.
set -eu

ROOT="${TLI_SETKA_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)}"
NAS="${NAS_HOST:-ivan@192.168.178.102}"
NAS_SSH_PORT="${NAS_SSH_PORT:-20022}"
NAS_PROJECT="${NAS_PROJECT:-/volume1/docker/_cm/passivbot-shadow}"
MAC_SHADOW="${MAC_SHADOW:-$ROOT/analysis_reports/crypto_bot_roadmap/shadow_runs/public-stream-process-24h-2026-08-18-v5}"
NAS_RUN="${NAS_RUN:-public-stream-process-24h-2026-08-19-v6}"
WATCHER_PID="${WATCHER_PID:-}"
SUPERVISOR_PID="${SUPERVISOR_PID:-}"

ssh_nas() {
    ssh -o BatchMode=yes -p "$NAS_SSH_PORT" "$NAS" "$@"
}

tar_to_nas() {
    src_dir=$1
    dest_dir=$2
    shift 2
    ssh_nas "mkdir -p '$dest_dir'"
    COPYFILE_DISABLE=1 tar -C "$src_dir" "$@" -cf - . \
        | ssh_nas "tar -xf - -C '$dest_dir'"
}

copy_file_to_nas() {
    local_path=$1
    remote_path=$2
    ssh_nas "cat > '$remote_path'" < "$local_path"
}

echo "pre-seeding NAS project"
ssh_nas "mkdir -p '$NAS_PROJECT/repo' '$NAS_PROJECT/data' '$NAS_PROJECT/inputs'"
tar_to_nas "$ROOT" "$NAS_PROJECT/repo" \
    --exclude '.venv' --exclude 'venv' --exclude '.git' \
    --exclude 'analysis_reports' --exclude 'api-keys.json' \
    --exclude 'caches' --exclude 'backtests' --exclude 'historical_data' \
    --exclude 'optimize_results' --exclude 'monitor' \
    --exclude 'passivbot-rust/target' --exclude '.pytest_cache'
copy_file_to_nas \
    "$ROOT/analysis_reports/crypto_bot_roadmap/inputs/strategy_shadow_stream_config_v1.json" \
    "$NAS_PROJECT/inputs/strategy_shadow_stream_config_v1.json"
copy_file_to_nas \
    "$ROOT/analysis_reports/crypto_bot_roadmap/inputs/bitget_btc_market_profile_public_only_2026-08-17.json" \
    "$NAS_PROJECT/inputs/bitget_btc_market_profile_public_only_2026-08-17.json"
copy_file_to_nas \
    "$ROOT/container/shadow/docker-compose.synology.yml" \
    "$NAS_PROJECT/docker-compose.yml"

echo "pre-seeding shadow data while Mac writer is still live"
tar_to_nas "$MAC_SHADOW" "$NAS_PROJECT/data/$NAS_RUN"

echo "stopping Mac watcher then supervisor"
if [ -n "$WATCHER_PID" ]; then
    kill -TERM "$WATCHER_PID" 2>/dev/null || true
fi
if [ -n "$SUPERVISOR_PID" ]; then
    kill -TERM "$SUPERVISOR_PID" 2>/dev/null || true
    i=0
    while kill -0 "$SUPERVISOR_PID" 2>/dev/null; do
        i=$((i + 1))
        if [ "$i" -gt 20 ]; then
            kill -KILL "$SUPERVISOR_PID" 2>/dev/null || true
            break
        fi
        sleep 1
    done
fi

echo "final copy of frozen Mac state"
tar_to_nas "$MAC_SHADOW" "$NAS_PROJECT/data/$NAS_RUN"

echo "starting NAS shadow with SHADOW_RESUME=1"
ssh_nas "export PATH=\$PATH:/usr/local/bin; cd '$NAS_PROJECT' && SHADOW_RESUME=1 docker compose up -d --no-build"
echo "cutover issued"
