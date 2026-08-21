from __future__ import annotations

from copy import deepcopy

from research.historical_selection import select_candidate


def _metrics(*, net_result=1.0, drawdown=0.10, recovery=10.0, equity=0.80):
    return {
        "net_result": net_result,
        "net_daily_return": net_result / 1000.0,
        "max_drawdown": drawdown,
        "recovery_days": recovery,
        "closed_cycles": 20,
        "liquidations": 0,
        "gross_exposure_ratio": 0.60,
        "used_margin_ratio": 0.20,
        "min_liquidation_distance": 0.30,
        "execution_slippage_median_bps": 1.0,
        "execution_slippage_p95_bps": 5.0,
        "execution_slippage_max_bps": 20.0,
        "max_daily_loss": 0.01,
        "reconciliation_mismatches": 0,
        "events_present": True,
        "min_equity_ratio": equity,
    }


def _candidate(candidate_id: str, *, net_result: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "asset": "BTC",
        "side": "long",
        "optimizer_seeds": [101, 202, 303, 404, 505],
        "validation": [
            {
                "window": window,
                "metrics": _metrics(net_result=net_result + window / 100.0),
                "neighbors": [
                    {"parameter": f"p{index}", "offset_pct": 0.05, "metrics": _metrics(net_result=0.5)}
                    for index in range(32)
                ],
            }
            for window in (1, 2, 3, 4)
        ],
        "stress": _metrics(net_result=0.5, drawdown=0.20, equity=0.75),
        "holdout": {"metrics": {"not_evaluated": True}},
    }


def test_selection_uses_only_passed_validation_and_deterministic_tiebreakers():
    result = select_candidate(
        [_candidate("slower", net_result=1.0), _candidate("better", net_result=2.0)],
        asset="BTC",
        side="long",
    )

    assert result["status"] == "passed"
    assert result["selected_candidate"]["candidate_id"] == "better"
    assert result["selected_candidate"]["holdout"]["evaluated"] is False


def test_one_bad_neighbor_rejects_candidate_even_when_centre_is_profitable():
    candidate = _candidate("unstable", net_result=2.0)
    candidate["validation"][2]["neighbors"][0]["metrics"]["max_drawdown"] = 0.25

    result = select_candidate([candidate], asset="BTC", side="long")

    assert result["status"] == "no_suitable_candidate"
    assert result["selected_candidate"] is None
    assert result["candidates"][0]["status"] == "failed"


def test_neighbour_gate_allows_up_to_twenty_percent_unprofitable_neighbours():
    candidate = _candidate("mostly_stable", net_result=2.0)
    for item in candidate["validation"][0]["neighbors"][:6]:
        item["metrics"]["net_result"] = -0.1

    result = select_candidate([candidate], asset="BTC", side="long")

    assert result["status"] == "passed"


def test_missing_evidence_blocks_selection_instead_of_filling_metrics():
    candidate = _candidate("incomplete", net_result=2.0)
    candidate = deepcopy(candidate)
    del candidate["validation"][0]["neighbors"]
    candidate["optimizer_seeds"] = [101, 202]

    result = select_candidate([candidate], asset="BTC", side="long")

    assert result["status"] == "blocked"
    assert result["candidates"][0]["status"] == "blocked"
    assert result["candidates"][0]["blocked_reasons"]


def test_negative_holdout_is_not_used_before_candidate_freeze():
    candidate = _candidate("centre", net_result=2.0)
    candidate["holdout"] = {"metrics": _metrics(net_result=-100.0, drawdown=0.50, equity=0.40)}

    result = select_candidate([candidate], asset="BTC", side="long")

    assert result["status"] == "passed"
    assert result["holdout_policy"] == "reserved_until_centre_candidate_is_frozen"
