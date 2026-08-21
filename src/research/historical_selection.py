"""Select a historical grid candidate without weakening any roadmap gate.

The optimizer produces many Pareto members, while the roadmap selects a
candidate only after four validation windows, stress checks, and one-at-a-time
neighbour checks.  This module consumes a small, explicit JSON envelope for
those results.  It never treats the final holdout as a training or ranking
metric: the holdout is only recorded as reserved until the centre candidate is
frozen.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "crypto-historical-selection-v1"
EXPECTED_WINDOWS = (1, 2, 3, 4)
EXPECTED_NEIGHBORS = 32
EXPECTED_SEEDS = 5


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(metrics: Mapping[str, Any], name: str) -> float | None:
    value = metrics.get(name)
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or abs(parsed) == float("inf"):
        return None
    return parsed


def _gate(
    gates: list[dict[str, Any]],
    *,
    name: str,
    actual: Any,
    operator: str,
    limit: float | int | bool,
) -> None:
    if actual is None:
        gates.append({"id": name, "status": "blocked", "reason": "missing_metric"})
        return
    if isinstance(limit, bool):
        passed = bool(actual) is limit
    elif operator == "<":
        passed = float(actual) < float(limit)
    elif operator == ">":
        passed = float(actual) > float(limit)
    elif operator == "<=":
        passed = float(actual) <= float(limit)
    elif operator == ">=":
        passed = float(actual) >= float(limit)
    elif operator == "==":
        passed = float(actual) == float(limit)
    else:
        raise ValueError(f"unsupported operator: {operator}")
    gates.append(
        {
            "id": name,
            "status": "passed" if passed else "failed",
            "actual": actual,
            "operator": operator,
            "limit": limit,
        }
    )


def _bool_metric(metrics: Mapping[str, Any], name: str) -> bool | None:
    value = metrics.get(name)
    return value if isinstance(value, bool) else None


def evaluate_metrics(metrics: Mapping[str, Any], *, scope: str) -> dict[str, Any]:
    """Evaluate one centre, neighbour, or stress result."""

    if not isinstance(metrics, Mapping):
        return {
            "scope": scope,
            "status": "blocked",
            "gates": [{"id": "metrics.object", "status": "blocked", "reason": "not_an_object"}],
        }
    gates: list[dict[str, Any]] = []
    net_result = _number(metrics, "net_result")
    if net_result is None:
        net_result = _number(metrics, "net_daily_return")
    _gate(gates, name=f"{scope}.net_result", actual=net_result, operator=">", limit=0.0)
    _gate(gates, name=f"{scope}.max_drawdown", actual=_number(metrics, "max_drawdown"), operator="<=", limit=0.20)
    _gate(gates, name=f"{scope}.recovery_days", actual=_number(metrics, "recovery_days"), operator="<=", limit=90.0)
    _gate(gates, name=f"{scope}.closed_cycles", actual=_number(metrics, "closed_cycles"), operator=">=", limit=20.0)
    _gate(gates, name=f"{scope}.liquidations", actual=_number(metrics, "liquidations"), operator="==", limit=0.0)
    _gate(gates, name=f"{scope}.gross_exposure_ratio", actual=_number(metrics, "gross_exposure_ratio"), operator="<=", limit=0.80)
    _gate(gates, name=f"{scope}.used_margin_ratio", actual=_number(metrics, "used_margin_ratio"), operator="<=", limit=0.35)
    _gate(gates, name=f"{scope}.min_liquidation_distance", actual=_number(metrics, "min_liquidation_distance"), operator=">=", limit=0.15)
    _gate(gates, name=f"{scope}.slippage_median_bps", actual=_number(metrics, "execution_slippage_median_bps"), operator="<=", limit=2.0)
    _gate(gates, name=f"{scope}.slippage_p95_bps", actual=_number(metrics, "execution_slippage_p95_bps"), operator="<=", limit=10.0)
    _gate(gates, name=f"{scope}.slippage_max_bps", actual=_number(metrics, "execution_slippage_max_bps"), operator="<=", limit=25.0)
    _gate(gates, name=f"{scope}.daily_loss", actual=_number(metrics, "max_daily_loss"), operator="<=", limit=0.02)
    _gate(gates, name=f"{scope}.reconciliation", actual=_number(metrics, "reconciliation_mismatches"), operator="==", limit=0.0)
    _gate(gates, name=f"{scope}.events_present", actual=_bool_metric(metrics, "events_present"), operator="==", limit=True)
    passed = sum(gate["status"] == "passed" for gate in gates)
    failed = sum(gate["status"] == "failed" for gate in gates)
    blocked = sum(gate["status"] == "blocked" for gate in gates)
    return {
        "scope": scope,
        "status": "failed" if failed else "blocked" if blocked else "passed",
        "passed_count": passed,
        "failed_count": failed,
        "blocked_count": blocked,
        "gates": gates,
        "net_result": net_result,
        "max_drawdown": _number(metrics, "max_drawdown"),
        "gross_exposure_ratio": _number(metrics, "gross_exposure_ratio"),
        "recovery_days": _number(metrics, "recovery_days"),
    }


def _evaluate_stress(metrics: Any) -> dict[str, Any]:
    if not isinstance(metrics, Mapping):
        return {
            "scope": "stress",
            "status": "blocked",
            "gates": [{"id": "stress.object", "status": "blocked", "reason": "not_an_object"}],
        }
    gates: list[dict[str, Any]] = []
    _gate(
        gates,
        name="stress.min_equity_ratio",
        actual=_number(metrics, "min_equity_ratio"),
        operator=">=",
        limit=0.75,
    )
    _gate(
        gates,
        name="stress.max_drawdown",
        actual=_number(metrics, "max_drawdown"),
        operator="<",
        limit=0.25,
    )
    _gate(
        gates,
        name="stress.liquidations",
        actual=_number(metrics, "liquidations"),
        operator="==",
        limit=0.0,
    )
    failed = sum(gate["status"] == "failed" for gate in gates)
    blocked = sum(gate["status"] == "blocked" for gate in gates)
    return {
        "scope": "stress",
        "status": "failed" if failed else "blocked" if blocked else "passed",
        "passed_count": sum(gate["status"] == "passed" for gate in gates),
        "failed_count": failed,
        "blocked_count": blocked,
        "gates": gates,
    }


def _evaluate_neighbors(neighbors: Any) -> dict[str, Any]:
    if not isinstance(neighbors, list):
        return {
            "status": "blocked",
            "reason": "neighbors_not_a_list",
            "expected_count": EXPECTED_NEIGHBORS,
            "actual_count": None,
        }
    results = [
        evaluate_metrics(item.get("metrics"), scope=f"neighbor.{index + 1}")
        if isinstance(item, Mapping)
        else evaluate_metrics(None, scope=f"neighbor.{index + 1}")
        for index, item in enumerate(neighbors)
    ]
    count_ok = len(neighbors) == EXPECTED_NEIGHBORS
    profitable = sum(result["status"] == "passed" for result in results)
    ratio = profitable / len(results) if results else 0.0
    over_emergency = any(
        (_number(item.get("metrics", {}), "max_drawdown") or 1.0) >= 0.25
        for item in neighbors
        if isinstance(item, Mapping)
    )
    blocked = not results or any(result["status"] == "blocked" for result in results)
    failed = not count_ok or ratio < 0.80 or over_emergency
    return {
        "status": "failed" if failed else "blocked" if blocked else "passed",
        "expected_count": EXPECTED_NEIGHBORS,
        "actual_count": len(neighbors),
        "profitable_count": profitable,
        "profitable_ratio": ratio,
        "emergency_drawdown_seen": over_emergency,
        "results": results,
    }


def _candidate_result(candidate: Mapping[str, Any]) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id", "<missing>"))
    blocked_reasons: list[str] = []
    if not str(candidate.get("asset", "")).strip():
        blocked_reasons.append("asset identity is missing")
    if str(candidate.get("side", "")).lower() not in {"long", "short", "combined"}:
        blocked_reasons.append("side must be long, short or combined")
    validation = candidate.get("validation")
    if not isinstance(validation, list):
        blocked_reasons.append("validation must be a list")
        validation = []
    windows = sorted(
        [item.get("window") for item in validation if isinstance(item, Mapping)]
    )
    if windows != list(EXPECTED_WINDOWS):
        blocked_reasons.append("validation must contain exactly windows 1, 2, 3 and 4")
    seeds = candidate.get("optimizer_seeds")
    if not isinstance(seeds, list) or len(seeds) != EXPECTED_SEEDS or len(set(seeds)) != EXPECTED_SEEDS:
        blocked_reasons.append("optimizer_seeds must contain five distinct seeds")
    if not isinstance(candidate.get("holdout"), Mapping):
        blocked_reasons.append("holdout reservation is missing")
    validation_results: list[dict[str, Any]] = []
    for item in validation:
        if not isinstance(item, Mapping):
            validation_results.append(evaluate_metrics(None, scope="validation"))
            continue
        window = item.get("window")
        center = evaluate_metrics(item.get("metrics"), scope=f"validation.window_{window}")
        neighbors = _evaluate_neighbors(item.get("neighbors"))
        validation_results.append({"window": window, "center": center, "neighbors": neighbors})
    stress = _evaluate_stress(candidate.get("stress"))
    if blocked_reasons:
        status = "blocked"
    elif any(item["center"]["status"] == "blocked" for item in validation_results):
        status = "blocked"
    elif any(item["neighbors"]["status"] == "blocked" for item in validation_results):
        status = "blocked"
    elif any(item["center"]["status"] != "passed" for item in validation_results):
        status = "failed"
    elif any(item["neighbors"]["status"] != "passed" for item in validation_results):
        status = "failed"
    elif stress["status"] != "passed":
        status = "failed" if stress["status"] == "failed" else "blocked"
    else:
        status = "passed"
    centres = [item["center"] for item in validation_results if "center" in item]
    returns = [item["net_result"] for item in centres if item.get("net_result") is not None]
    drawdowns = [item["max_drawdown"] for item in centres if item.get("max_drawdown") is not None]
    exposures = [item["gross_exposure_ratio"] for item in centres if item.get("gross_exposure_ratio") is not None]
    recoveries = [item["recovery_days"] for item in centres if item.get("recovery_days") is not None]
    return {
        "candidate_id": candidate_id,
        "asset": candidate.get("asset"),
        "side": candidate.get("side"),
        "status": status,
        "blocked_reasons": blocked_reasons,
        "validation": validation_results,
        "stress": stress,
        "holdout": {"status": "reserved", "evaluated": False},
        "ranking": {
            "median_net_result": median(returns) if len(returns) == 4 else None,
            "max_validation_drawdown": max(drawdowns) if len(drawdowns) == 4 else None,
            "max_validation_exposure": max(exposures) if len(exposures) == 4 else None,
            "max_validation_recovery_days": max(recoveries) if len(recoveries) == 4 else None,
        },
    }


def select_candidate(
    candidates: Sequence[Mapping[str, Any]],
    *,
    asset: str | None = None,
    side: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic selection verdict for a candidate collection."""

    filtered = [
        candidate
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and (asset is None or str(candidate.get("asset", "")).upper() == asset.upper())
        and (side is None or str(candidate.get("side", "")).lower() == side.lower())
    ]
    results = [_candidate_result(candidate) for candidate in filtered]
    passed = [result for result in results if result["status"] == "passed"]
    passed.sort(
        key=lambda result: (
            -(result["ranking"]["median_net_result"] or float("-inf")),
            result["ranking"]["max_validation_drawdown"] or float("inf"),
            result["ranking"]["max_validation_exposure"] or float("inf"),
            result["ranking"]["max_validation_recovery_days"] or float("inf"),
            result["candidate_id"],
        )
    )
    if passed:
        status = "passed"
        selected = passed[0]
    elif not results:
        status = "blocked"
        selected = None
    elif any(result["status"] == "blocked" for result in results):
        status = "blocked"
        selected = None
    else:
        status = "no_suitable_candidate"
        selected = None
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "asset": asset,
        "side": side,
        "candidate_count": len(results),
        "passed_candidate_count": len(passed),
        "selected_candidate": selected,
        "candidates": results,
        "holdout_policy": "reserved_until_centre_candidate_is_frozen",
        "no_candidate_is_a_valid_rejection": True,
    }


def _report(verdict: Mapping[str, Any]) -> str:
    selected = verdict.get("selected_candidate")
    lines = [
        "# Historical candidate selection",
        "",
        f"Status: **{verdict.get('status')}**",
        f"Candidates checked: {verdict.get('candidate_count', 0)}",
        f"Candidates passed: {verdict.get('passed_candidate_count', 0)}",
        "",
        "The final holdout was kept out of ranking and remains reserved until the centre candidate is frozen.",
        "",
    ]
    if selected:
        lines.extend(
            [
                f"Selected candidate: `{selected['candidate_id']}`",
                f"Median validation result: `{selected['ranking']['median_net_result']}`",
                f"Maximum validation drawdown: `{selected['ranking']['max_validation_drawdown']}`",
            ]
        )
    elif verdict.get("status") == "no_suitable_candidate":
        lines.append("No candidate passed every required gate. The correct action is rejection or a new version.")
    elif verdict.get("status") == "blocked":
        lines.append("The evidence is incomplete. No selection was attempted.")
    return "\n".join(lines) + "\n"


def write_selection_artifacts(verdict: Mapping[str, Any], *, output: str | Path, input_path: Path) -> Path:
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(verdict)
    payload["input_sha256"] = _sha256_file(input_path)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    output_path.with_name("report.md").write_text(_report(payload), encoding="utf-8")
    return output_path


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSON object or array with candidate evidence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--asset", type=str, default=None)
    parser.add_argument("--side", choices=("long", "short", "combined"), default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input.expanduser().resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates") if isinstance(payload, Mapping) else payload
    if not isinstance(candidates, list):
        raise ValueError("selection input must be a JSON array or an object with candidates[]")
    verdict = select_candidate(candidates, asset=args.asset, side=args.side)
    output = write_selection_artifacts(verdict, output=args.output, input_path=input_path)
    print(json.dumps({"output": str(output), "status": verdict["status"]}, sort_keys=True))
    if verdict["status"] == "passed":
        return 0
    return 2 if verdict["status"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
