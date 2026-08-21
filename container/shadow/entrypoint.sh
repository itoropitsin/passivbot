#!/bin/sh
set -eu

export PYTHONPATH="/app/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1

if [ -n "${PB_API_KEY:-}" ] || [ -n "${PB_API_SECRET:-}" ] || [ -n "${PB_PASSPHRASE:-}" ] || [ -n "${PB_USER:-}" ]; then
    echo "passivbot-shadow forbids live API credentials" >&2
    exit 2
fi
for candidate in /app/api-keys.json /data/api-keys.json /inputs/api-keys.json; do
    if [ -e "$candidate" ]; then
        echo "passivbot-shadow forbids $candidate" >&2
        exit 2
    fi
done

if [ "${SHADOW_RESUME:-0}" = "1" ]; then
    set -- --resume-existing-output "$@"
fi

exec python -m tools.strategy_shadow_stream_process "$@"
