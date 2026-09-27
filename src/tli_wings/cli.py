"""Safe project-level TLI-Wings command line entrypoint.

The commands are local-only adapters around the v8.1 policy, paper WAL and
evidence contracts.  They do not create exchange clients or submit orders.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from .risk import TliRiskError, empty_portfolio_snapshot, evaluate_tli_portfolio_risk
from .runtime_policy import RuntimePolicyError, load_runtime_policy
from .evidence import (
    build_risk_evidence,
    build_version_fingerprint,
    data_quality_gate,
)
from .paper_wal import PAPER_WAL_SCHEMA, PaperLedger


COMMANDS = ("backtest", "paper", "shadow", "preflight", "evidence", "live")
LIVE_COMMANDS = frozenset({"live"})

_CONFIG_ROOT_FIELDS = frozenset(
    {
        "config_version",
        "backtest",
        "bot",
        "coin_overrides",
        "live",
        "logging",
        "monitor",
        "optimize",
    }
)

# These are removed pre-v8 names. A TLI smoke config must use the native
# v8.1 trailing_martingale shape instead of hiding an old strategy under a
# new wrapper.
_LEGACY_FIELDS = frozenset(
    {
        "entry_trailing_grid_ratio",
        "close_trailing_grid_ratio",
        "close_grid_markup_start",
        "close_grid_markup_end",
        "twe_long",
        "twe_short",
        "universal_live_config",
        "trailing_grid",
    }
)


def _duplicate_key_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> None:
    raise ValueError(f"non-standard JSON number is not allowed: {value}")


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise
    try:
        return json.loads(
            text,
            object_pairs_hook=_duplicate_key_object,
            parse_constant=_reject_nonstandard_number,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc


def _legacy_key_paths(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            key_normalized = key_text.lower()
            child_path = f"{path}.{key_text}" if path else key_text
            if (
                key_normalized in _LEGACY_FIELDS
                or ("trailing" in key_normalized and "grid" in key_normalized)
                or key_normalized.startswith("twe_")
            ):
                found.append(child_path)
            found.extend(_legacy_key_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_legacy_key_paths(child, f"{path}[{index}]"))
    return found


def _validate_project_config(path: Path) -> dict[str, Any]:
    document = _read_json(path)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: config root must be a JSON object")

    legacy_paths = _legacy_key_paths(document)
    if legacy_paths:
        raise ValueError(
            f"{path}: legacy strategy fields are not allowed: {', '.join(sorted(legacy_paths))}"
        )

    missing = sorted(_CONFIG_ROOT_FIELDS - document.keys())
    if missing:
        raise ValueError(
            f"{path}: incomplete v8.1 config; missing: {', '.join(missing)}"
        )
    if document.get("config_version") != "v8.1.0":
        raise ValueError(f"{path}: config_version must be 'v8.1.0'")

    live = document.get("live")
    if not isinstance(live, dict):
        raise ValueError(f"{path}: live must be an object")
    approved_coins = live.get("approved_coins")
    if not isinstance(approved_coins, dict):
        raise ValueError(f"{path}: live.approved_coins must be an object")
    for side in ("long", "short"):
        if not isinstance(approved_coins.get(side), list):
            raise ValueError(f"{path}: live.approved_coins.{side} must be a list")

    if live.get("hedge_mode") is not True:
        raise ValueError(f"{path}: live.hedge_mode must be true")
    if live.get("margin_mode_preference") != "isolated":
        raise ValueError(
            f"{path}: live.margin_mode_preference must be 'isolated'"
        )
    leverage = live.get("leverage")
    if isinstance(leverage, bool) or not isinstance(leverage, (int, float)):
        raise ValueError(f"{path}: live.leverage must be numeric")
    if not math.isfinite(float(leverage)) or not 0.0 < float(leverage) <= 3.0:
        raise ValueError(f"{path}: live.leverage must be in (0, 3]")

    restarts = live.get("max_n_restarts_per_day")
    if isinstance(restarts, bool) or not isinstance(restarts, (int, float)):
        raise ValueError(f"{path}: live.max_n_restarts_per_day must be numeric")
    if not math.isfinite(float(restarts)) or float(restarts) < 0.0 or float(restarts) > 1.0:
        raise ValueError(f"{path}: live.max_n_restarts_per_day must be no more than 1")

    bot = document.get("bot")
    if not isinstance(bot, dict):
        raise ValueError(f"{path}: bot must be an object")
    for side in ("long", "short"):
        side_config = bot.get(side)
        if not isinstance(side_config, dict):
            raise ValueError(f"{path}: bot.{side} must be an object")
        strategy = side_config.get("strategy")
        if not isinstance(strategy, dict) or "trailing_martingale" not in strategy:
            raise ValueError(
                f"{path}: bot.{side}.strategy.trailing_martingale is required"
            )

    active_directions: list[str] = []
    positions_by_side: dict[str, Any] = {}
    for side in ("long", "short"):
        risk = bot[side].get("risk")
        if not isinstance(risk, dict):
            raise ValueError(f"{path}: bot.{side}.risk must be an object")
        n_positions = risk.get("n_positions")
        if isinstance(n_positions, bool) or not isinstance(n_positions, (int, float)):
            raise ValueError(f"{path}: bot.{side}.risk.n_positions must be numeric")
        if not math.isfinite(float(n_positions)) or float(n_positions) < 0.0:
            raise ValueError(f"{path}: bot.{side}.risk.n_positions must be non-negative")
        positions_by_side[side] = n_positions
        if approved_coins[side] or float(n_positions) > 0.0:
            active_directions.append(side)

    return {
        "path": str(path),
        "config_version": document["config_version"],
        "active_directions": active_directions,
        "approved_coins": {
            "long": list(approved_coins["long"]),
            "short": list(approved_coins["short"]),
        },
        "n_positions": positions_by_side,
    }


def _inspect_input(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"input file not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"{path}: input file is empty")

    if path.suffix.lower() in {".ndjson", ".jsonl"}:
        count = 0
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not raw_line.strip():
                continue
            try:
                json.loads(
                    raw_line,
                    object_pairs_hook=_duplicate_key_object,
                    parse_constant=_reject_nonstandard_number,
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            count += 1
        return {"path": str(path), "format": "ndjson", "records": count}

    document = _read_json(path)
    records = len(document) if isinstance(document, (dict, list)) else 1
    return {"path": str(path), "format": "json", "records": records}


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_policy_path() -> Path:
    local = Path.cwd() / "configs" / "tli" / "runtime_policy.json"
    if local.is_file():
        return local
    return _repository_root() / "configs" / "tli" / "runtime_policy.json"


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--policy",
        dest="policy_path",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="local TLI runtime policy JSON (default: configs/tli/runtime_policy.json)",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="complete v8.1 project config to inspect",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print a machine-readable result",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tli-wings",
        description="Local TLI-Wings policy and project diagnostics.",
    )
    parser.add_argument(
        "--policy",
        dest="policy_path",
        metavar="PATH",
        default=None,
        help="local TLI runtime policy JSON",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        metavar="PATH",
        default=None,
        help="complete v8.1 project config to inspect",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="print a machine-readable result",
    )
    parser.add_argument(
        "--authorize-live",
        dest="authorize_live",
        action="store_true",
        help="explicitly authorize a live-class command; this build still stays diagnostic-only",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in COMMANDS:
        subparser = subparsers.add_parser(command, help=f"run {command} diagnostics")
        _add_common_options(subparser)
        subparser.add_argument(
            "config_arg",
            nargs="?",
            metavar="CONFIG",
            help="optional complete v8.1 config",
        )
        if command == "evidence":
            subparser.add_argument(
                "--input",
                "--events",
                "--path",
                dest="input_path",
                metavar="PATH",
                default=argparse.SUPPRESS,
                help="optional local JSON or NDJSON evidence input to inspect",
            )
        if command == "paper":
            subparser.add_argument(
                "--wal",
                dest="wal_path",
                metavar="PATH",
                default=argparse.SUPPRESS,
                help="optional local crypto-paper-wal-v3 file to recover",
            )
        if command in LIVE_COMMANDS:
            subparser.add_argument(
                "--authorize-live",
                dest="authorize_live",
                action="store_true",
                default=argparse.SUPPRESS,
                help="required for this live-class command; no live operation is performed",
            )
    return parser


def _resolve_option_or_argument(
    args: argparse.Namespace, option_name: str, argument_name: str
) -> Path | None:
    option_value = getattr(args, option_name, None)
    argument_value = getattr(args, argument_name, None)
    if option_value and argument_value:
        raise ValueError(f"use either --{option_name.removesuffix('_path')} or the positional path")
    raw_value = option_value or argument_value
    return Path(raw_value) if raw_value else None


def _fingerprint_options(
    repo_root: Path,
    policy_path: Path,
    config_path: Path | None,
    input_path: Path | None,
) -> dict[str, Any]:
    """Build derived component paths without a hand-maintained code list."""

    def inside_repo(path: Path) -> str | None:
        try:
            return path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return None

    config_value = inside_repo(config_path) if config_path else None
    input_value = inside_repo(input_path) if input_path else None
    policy_value = inside_repo(policy_path)
    if policy_value is None:
        raise ValueError("policy path must be inside the project for evidence")
    return {
        "components": {
            # The TLI boundary is connected to the native Python runtime
            # (including exchanges, live adapters and config handling). Hash
            # the source tree as one component so a connected core change,
            # such as the Bitget UTA hook, cannot leave evidence looking fresh.
            "tli_code": ["src"],
            "rust": ["passivbot-rust"],
            "config": [config_value or "configs/tli"],
            "policy": [policy_value],
            "dependencies": [
                "setup.py",
                "requirements-live.txt",
                "requirements-full.txt",
                "requirements-dev.txt",
                "passivbot-rust/Cargo.toml",
                "passivbot-rust/Cargo.lock",
            ],
            "inputs": [input_value] if input_value else ["data", "inputs", "datasets"],
        }
    }


def _build_result(args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    policy_path = Path(getattr(args, "policy_path", None) or _default_policy_path())
    policy = load_runtime_policy(policy_path)

    config_path = _resolve_option_or_argument(args, "config_path", "config_arg")
    config_info = _validate_project_config(config_path) if config_path else None

    input_info = None
    input_path = None
    if command == "evidence":
        input_path = Path(getattr(args, "input_path", "")) if getattr(args, "input_path", None) else None
        input_info = _inspect_input(input_path) if input_path else None

    repo_root = _repository_root()
    fingerprint = build_version_fingerprint(
        repo_root,
        **_fingerprint_options(repo_root, policy_path, config_path, input_path),
    )
    quality = data_quality_gate(actual_rows=248)

    result: dict[str, Any] = {
        "command": command,
        "status": "ok" if command == "preflight" else "diagnostic_only",
        "policy_path": str(policy_path),
        "policy_hash": policy.policy_hash,
        "policy": policy.as_dict(),
        "config": config_info,
        "input": input_info,
        "network_used": False,
        "keys_loaded": False,
        "exchange_calls": 0,
        "orders_submitted": 0,
        "version_fingerprint": fingerprint,
        "data_quality": quality,
    }
    if command == "preflight":
        risk_decision = evaluate_tli_portfolio_risk(
            empty_portfolio_snapshot(), policy
        )
        result["risk_hook"] = {
            "status": "passed",
            "allow_new_entries": risk_decision["allow_new_entries"],
            "actions": len(risk_decision["actions"]),
        }
    elif command == "paper":
        wal_path = Path(getattr(args, "wal_path", "")) if getattr(args, "wal_path", None) else None
        if wal_path is None:
            result["paper"] = {
                "wal_schema": PAPER_WAL_SCHEMA,
                "records": 0,
                "state_complete": False,
                "safe_to_continue": False,
                "message": "paper starts with an empty v3 WAL; no state was migrated",
            }
        else:
            ledger = PaperLedger(wal_path)
            recovery = ledger.recovery_report()
            result["paper"] = {
                "wal_path": str(wal_path),
                "wal_schema": PAPER_WAL_SCHEMA,
                "records": len(ledger.wal.records()),
                "state_complete": recovery["safe"],
                "safe_to_continue": recovery["safe"],
                "known_surfaces": recovery["known_surfaces"],
                "unknown_surfaces": recovery["unknown_surfaces"],
                "last_checksum": recovery["last_checksum"],
            }
    elif command == "evidence":
        result["risk_evidence"] = build_risk_evidence(
            fingerprint=fingerprint,
            data_quality=quality,
            risk_trace=None,
        )
    if command in LIVE_COMMANDS:
        result.update(
            {
                "live_authorized": bool(args.authorize_live),
                "live_execution": False,
                "message": (
                    "Live authorization was recorded, but this migration build has "
                    "no live execution path; no keys, network, exchange calls, or "
                    "orders are used."
                ),
            }
        )
    elif command == "preflight":
        result["message"] = "Local policy and optional config preflight passed."
    elif command == "evidence":
        result["message"] = "Local evidence was inspected; admission remains closed."
    else:
        result["message"] = "The project entrypoint is wired as a local diagnostic."
    return result


def _print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return

    print(f"{result['command']}: {result['status']}")
    print(f"policy hash: {result['policy_hash']}")
    print(f"network: {'used' if result['network_used'] else 'not used'}")
    print(f"keys: {'loaded' if result['keys_loaded'] else 'not loaded'}")
    print(f"exchange calls: {result['exchange_calls']}")
    print(f"orders submitted: {result['orders_submitted']}")
    if result.get("config"):
        print(f"config: {result['config']['path']}")
    if result.get("input"):
        print(f"input: {result['input']['path']}")
    print(result["message"])


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # This check must remain before policy/config loading and before any future
    # execution imports.  It is the hard safety boundary for live-class names.
    if args.command in LIVE_COMMANDS and not bool(args.authorize_live):
        print(
            f"tli-wings: error: '{args.command}' requires explicit --authorize-live; "
            "no keys or exchange calls are allowed without it",
            file=sys.stderr,
        )
        return 2

    try:
        result = _build_result(args)
    except (
        FileNotFoundError,
        OSError,
        RuntimePolicyError,
        TliRiskError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"tli-wings: error: {exc}", file=sys.stderr)
        return 2

    _print_result(result, json_output=bool(args.json_output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["COMMANDS", "LIVE_COMMANDS", "build_parser", "main"]
