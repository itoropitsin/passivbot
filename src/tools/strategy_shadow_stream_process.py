"""Run the public strategy shadow in separate local child processes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.strategy_shadow_stream_process import run_stream_process_supervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--category", default="USDT-FUTURES")
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--side", choices=("long", "short"), default="long")
    parser.add_argument("--entry-bps", type=float)
    parser.add_argument("--take-profit-bps", type=float)
    parser.add_argument("--order-amount", type=float)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--max-source-gap-seconds", type=float)
    parser.add_argument("--segment-duration-seconds", type=float, default=4 * 60 * 60)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--restart-count", type=int, default=5)
    parser.add_argument("--deterministic-replay-runs", type=int, default=3)
    parser.add_argument(
        "--fixture",
        type=Path,
        help="saved public recorder snapshots for process tests only",
    )
    parser.add_argument(
        "--market-profile",
        type=Path,
        help="public market profile to bind into the immutable shadow result",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--resume-existing-output",
        action="store_true",
        help="continue an unfinished public stream directory after a host cutover",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_stream_process_supervisor(
            output_dir=args.output_dir,
            config_path=args.config,
            symbol=args.symbol,
            category=args.category,
            depth=args.depth,
            side=args.side,
            entry_bps=args.entry_bps,
            take_profit_bps=args.take_profit_bps,
            order_amount=args.order_amount,
            interval_seconds=args.interval_seconds,
            max_source_gap_seconds=args.max_source_gap_seconds,
            segment_duration_seconds=args.segment_duration_seconds,
            max_samples=args.max_samples,
            restart_count=args.restart_count,
            deterministic_replay_runs=args.deterministic_replay_runs,
            fixture_path=args.fixture,
            market_profile_path=args.market_profile,
            repo_root=args.repo_root,
            resume_existing_output=args.resume_existing_output,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
