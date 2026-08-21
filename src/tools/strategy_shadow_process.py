"""Run a local strategy shadow across separate child processes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.strategy_shadow_process import run_process_supervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run saved public snapshots through separate local strategy-shadow "
            "processes and record restart evidence. No network or exchange writes."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="saved JSON or JSONL snapshots")
    parser.add_argument("--output-dir", required=True, type=Path, help="new local session directory")
    parser.add_argument("--config", required=True, type=Path, help="local strategy-shadow config JSON")
    parser.add_argument("--restart-count", type=int, default=5)
    parser.add_argument("--deterministic-replay-runs", type=int, default=3)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_process_supervisor(
            input_path=args.input,
            output_dir=args.output_dir,
            config_path=args.config,
            restart_count=args.restart_count,
            deterministic_replay_runs=args.deterministic_replay_runs,
            repo_root=args.repo_root,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
