"""Run the existing local strategy shadow against public Bitget snapshots."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from research.strategy_shadow_stream import run_strategy_shadow_stream_sync


def _load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    target = path.expanduser().resolve()
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("stream config must be a JSON object")
    return value


def _load_fixture(path: Path) -> Any:
    target = path.expanduser().resolve()
    if target.suffix.lower() in {".jsonl", ".ndjson"}:
        rows = [
            json.loads(line)
            for line in target.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        value = json.loads(target.read_text(encoding="utf-8"))
        rows = value.get("snapshots") if isinstance(value, dict) else value
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("stream fixture must contain a non-empty list of snapshots")
    for index, row in enumerate(rows):
        source_responses = row.get("source_responses")
        if not isinstance(source_responses, dict):
            raise ValueError(f"stream fixture row {index} lacks source_responses")
        for name in ("ticker", "orderbook", "current_funding", "funding_history"):
            if not isinstance(source_responses.get(name), dict):
                raise ValueError(f"stream fixture row {index} lacks source response {name}")
    return rows


def _fixture_request(rows: list[dict[str, Any]], *, offset: int = 0):
    state = {"index": int(offset) - 1, "row": rows[min(int(offset), len(rows) - 1)]}

    def request(path, _params):
        if path.endswith("/tickers"):
            state["index"] += 1
            state["row"] = rows[min(state["index"], len(rows) - 1)]
        source_responses = state["row"]["source_responses"]
        if path.endswith("/tickers"):
            name = "ticker"
        elif path.endswith("/orderbook"):
            name = "orderbook"
        elif path.endswith("/current-fund-rate"):
            name = "current_funding"
        elif path.endswith("/history-fund-rate"):
            name = "funding_history"
        else:
            raise ValueError(f"unsupported fixture path: {path}")
        return copy.deepcopy(source_responses[name])

    return request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, help="credential-free local strategy config JSON")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--category", default="USDT-FUTURES")
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--side", choices=("long", "short"), default="long")
    parser.add_argument("--entry-bps", type=float)
    parser.add_argument("--take-profit-bps", type=float)
    parser.add_argument("--order-amount", type=float)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--max-source-gap-seconds", type=float)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument(
        "--fixture",
        type=Path,
        help="saved public recorder snapshots for local tests only; never readiness evidence",
    )
    parser.add_argument("--fixture-offset", type=int, default=0, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = _load_config(args.config)
        fixture_rows = _load_fixture(args.fixture) if args.fixture else None
        result = run_strategy_shadow_stream_sync(
            output_dir=args.output_dir,
            config=config,
            symbol=args.symbol,
            category=args.category,
            depth=args.depth,
            side=args.side,
            entry_bps=args.entry_bps,
            take_profit_bps=args.take_profit_bps,
            order_amount=args.order_amount,
            interval_seconds=args.interval_seconds,
            max_source_gap_seconds=args.max_source_gap_seconds,
            duration_seconds=args.duration_seconds,
            max_samples=args.max_samples,
            request=(
                _fixture_request(fixture_rows, offset=args.fixture_offset)
                if fixture_rows is not None
                else None
            ),
            fixture_mode=fixture_rows is not None,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
