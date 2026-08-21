"""Run the local Python/Rust crypto risk surface probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.risk_engine_surface import run_risk_surface_probe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis_reports/crypto_bot_roadmap/inputs/risk_engine_surface_probe_2026-08-18.json"),
    )
    args = parser.parse_args(argv)
    result = run_risk_surface_probe()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "status": result["status"]}, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
