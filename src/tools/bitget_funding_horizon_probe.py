"""Measure the public Bitget funding-history horizon without trading.

Bitget exposes two public history contracts that are useful to compare while
building a data-quality report: the UTA v3 endpoint and the older v2 mix
endpoint.  This probe records page counts, response hashes, and the oldest
observed settlement.  It never treats one endpoint as a substitute for the
other and never fills a missing period.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from research.funding_quality import validate_funding_rows
from tools.bitget_public_execution_inputs import (
    RequestFn,
    build_public_requester,
    _parse_timestamp,
)


SCHEMA_VERSION = "bitget-funding-horizon-probe-v1"
PUBLIC_DOCUMENTATION = {
    "uta_v3": "https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate",
    "classic_v2": "https://www.bitget.com/api-doc/classic/contract/market/Get-History-Funding-Rate",
}


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _row_timestamp(row: Mapping[str, Any], *, endpoint: str) -> int | None:
    key = "fundingRateTimestamp" if endpoint == "uta_v3" else "fundingTime"
    try:
        timestamp = int(row.get(key))
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if timestamp >= 0 else None


def _row_rate(row: Mapping[str, Any]) -> float | None:
    try:
        rate = float(row.get("fundingRate"))
    except (TypeError, ValueError, OverflowError):
        return None
    return rate if rate == rate and abs(rate) != float("inf") else None


def _rows(payload: Mapping[str, Any], *, endpoint: str) -> list[Any]:
    data = payload.get("data")
    if endpoint == "uta_v3":
        data = data.get("resultList") if isinstance(data, Mapping) else None
    if not isinstance(data, list):
        return []
    # Keep malformed list members in the evidence.  Dropping them before the
    # quality check would make a partial or corrupted response look complete.
    return list(data)


def _iso(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _probe_endpoint(
    request: RequestFn,
    *,
    endpoint: str,
    symbol: str,
    category: str,
    max_pages: int,
    start_ms: int,
    end_ms: int,
    interval_hours: int,
) -> dict[str, Any]:
    if endpoint == "uta_v3":
        path = "/api/v3/market/history-fund-rate"
        docs = PUBLIC_DOCUMENTATION[endpoint]
        page_params = lambda page: {
            "category": category,
            "symbol": symbol,
            "limit": "100",
            "cursor": str(page),
        }
    elif endpoint == "classic_v2":
        path = "/api/v2/mix/market/history-fund-rate"
        docs = PUBLIC_DOCUMENTATION[endpoint]
        page_params = lambda page: {
            "symbol": symbol,
            "productType": category,
            "pageSize": "100",
            "pageNo": str(page),
        }
    else:
        raise ValueError(f"unsupported Bitget funding endpoint: {endpoint}")

    rows: list[dict[str, Any]] = []
    page_row_counts: list[int] = []
    response_hashes: list[str] = []
    error: str | None = None
    for page in range(1, max_pages + 1):
        try:
            payload = dict(request(path, page_params(page)))
        except Exception as exc:  # preserve an inspectable partial probe
            error = f"{type(exc).__name__}: {exc}"
            break
        response_hashes.append(_canonical_hash(payload))
        page_rows = _rows(payload, endpoint=endpoint)
        page_row_counts.append(len(page_rows))
        for row in page_rows:
            if not isinstance(row, Mapping):
                rows.append(
                    {
                        "symbol": None,
                        "funding_rate": None,
                        "timestamp_ms": None,
                        "malformed": True,
                    }
                )
                continue
            raw_symbol = row.get("symbol")
            rows.append(
                {
                    "symbol": (
                        str(raw_symbol).strip().upper()
                        if raw_symbol is not None and str(raw_symbol).strip()
                        else None
                    ),
                    "funding_rate": _row_rate(row),
                    "timestamp_ms": _row_timestamp(row, endpoint=endpoint),
                }
            )
        if not page_rows:
            break

    quality = validate_funding_rows(
        rows,
        start_ms=start_ms,
        end_ms=end_ms,
        interval_hours=interval_hours,
    )
    valid_rows = [
        row
        for row in rows
        if row["timestamp_ms"] is not None and row["funding_rate"] is not None
    ]
    timestamps = [int(row["timestamp_ms"]) for row in valid_rows]
    foreign_symbol_rows = sum(
        row.get("symbol") is not None
        and str(row.get("symbol")).upper() != str(symbol).upper()
        for row in rows
    )
    missing_symbol_rows = sum(row.get("symbol") is None for row in rows)
    malformed_rows = sum(row.get("malformed") is True for row in rows)
    endpoint_status = (
        "passed"
        if error is None
        and quality["complete_requested_window"]
        and foreign_symbol_rows == 0
        and missing_symbol_rows == 0
        and malformed_rows == 0
        else "blocked"
    )
    return {
        "endpoint": endpoint,
        "path": path,
        "documentation": docs,
        "symbol": symbol,
        "category": category,
        "page_limit": 100,
        "max_pages_requested": max_pages,
        "page_count": len(page_row_counts),
        "page_row_counts": page_row_counts,
        "last_page_empty": bool(page_row_counts) and page_row_counts[-1] == 0,
        "response_sha256": response_hashes,
        "observed_rows": len(rows),
        "valid_rows": len(valid_rows),
        "foreign_symbol_rows": foreign_symbol_rows,
        "missing_symbol_rows": missing_symbol_rows,
        "malformed_rows": malformed_rows,
        "oldest_observed_timestamp_ms": min(timestamps) if timestamps else None,
        "oldest_observed_utc": _iso(min(timestamps) if timestamps else None),
        "newest_observed_timestamp_ms": max(timestamps) if timestamps else None,
        "newest_observed_utc": _iso(max(timestamps) if timestamps else None),
        "quality_for_requested_window": quality,
        "error": error,
        "status": endpoint_status,
    }


def build_probe(
    *,
    symbol: str = "BTCUSDT",
    category: str = "USDT-FUTURES",
    start: str,
    end: str,
    interval_hours: int = 8,
    max_pages: int = 100,
    request: RequestFn,
) -> dict[str, Any]:
    """Return a content-addressed public funding-horizon report."""

    start_ms = _parse_timestamp(start)
    end_ms = _parse_timestamp(end, end_of_day=True)
    if start_ms > end_ms:
        raise ValueError("start must not be after end")
    if interval_hours <= 0 or max_pages <= 0:
        raise ValueError("interval_hours and max_pages must be positive")

    endpoints = {
        endpoint: _probe_endpoint(
            request,
            endpoint=endpoint,
            symbol=symbol,
            category=category,
            max_pages=max_pages,
            start_ms=start_ms,
            end_ms=end_ms,
            interval_hours=interval_hours,
        )
        for endpoint in ("uta_v3", "classic_v2")
    }
    complete = any(result["status"] == "passed" for result in endpoints.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "exchange": "bitget",
        "symbol": symbol,
        "category": category,
        "requested_window": {
            "start_utc": _iso(start_ms),
            "end_utc": _iso(end_ms),
            "interval_hours": interval_hours,
        },
        "endpoints": endpoints,
        "complete_requested_window_available": complete,
        "status": "passed" if complete else "blocked",
        "read_only_external_operations": True,
        "trading_operations_count": 0,
        "notes": [
            "This probe records observed public pages only.",
            "No rows are synthesized, copied from another exchange, or inferred from the other endpoint.",
            "A non-empty recent horizon does not satisfy the requested historical window.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--category", default="USDT-FUTURES")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--interval-hours", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "analysis_reports/crypto_bot_roadmap/inputs/"
            "bitget_funding_horizon_probe.json"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_probe(
        symbol=args.symbol,
        category=args.category,
        start=args.start,
        end=args.end,
        interval_hours=args.interval_hours,
        max_pages=args.max_pages,
        request=build_public_requester(),
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "status": report["status"],
                "complete_requested_window_available": report[
                    "complete_requested_window_available"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
