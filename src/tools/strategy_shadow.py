"""CLI for replaying saved public snapshots through the strategy shadow."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from research.strategy_shadow import run_strategy_shadow


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_snapshots(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"snapshot line {line_number} must be an object")
                rows.append(value)
        return rows
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict) and isinstance(value.get("snapshots"), list):
        value = value["snapshots"]
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("snapshot input must be a JSON list or an object with snapshots")
    return value


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).expanduser().resolve()
    snapshots = _load_snapshots(input_path)
    config: dict[str, Any] = {
        "strategy_id": args.strategy_id,
        "order_amount": args.order_amount,
        "entry_offset_bps": args.entry_offset_bps,
        "exit_offset_bps": args.exit_offset_bps,
    }
    result = await run_strategy_shadow(
        snapshots,
        output_dir=Path(args.output_dir).expanduser().resolve(),
        config=config,
        max_snapshots=args.max_snapshots,
        deterministic_replay_runs=args.deterministic_replay_runs,
        input_provenance={
            "source_kind": "local_saved_snapshot_file",
            "source_path": str(input_path),
            "source_sha256": _sha256_file(input_path),
            "format": input_path.suffix.lower().lstrip(".") or "json",
        },
    )
    result_path = Path(args.output_dir).expanduser().resolve() / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="saved JSON or JSONL public snapshots")
    parser.add_argument("--output-dir", required=True, help="local checkpoint/event output directory")
    parser.add_argument("--strategy-id", default="crypto-grid-shadow-v1")
    parser.add_argument("--order-amount", type=float, default=0.001)
    parser.add_argument("--entry-offset-bps", type=float, default=0.0)
    parser.add_argument("--exit-offset-bps", type=float, default=0.0)
    parser.add_argument("--max-snapshots", type=int)
    parser.add_argument(
        "--deterministic-replay-runs",
        type=int,
        default=3,
        help="number of independent local runs to compare (default: 3)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
