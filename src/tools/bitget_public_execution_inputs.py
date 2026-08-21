"""Collect public Bitget inputs needed for execution-aware backtests.

The collector deliberately uses only public GET endpoints.  It never loads API
keys and never sends an account, order, or transfer request.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from research.funding_quality import validate_funding_rows


API_BASE = "https://api.bitget.com"
SCHEMA_VERSION = "bitget-public-execution-inputs-v1"
DEFAULT_DOCS = {
    "instruments": "https://www.bitget.com/api-doc/uta/public/Get-Instruments",
    "funding_history": "https://www.bitget.com/api-doc/uta/public/Get-History-Funding-Rate",
    "position_tier": "https://www.bitget.com/api-doc/uta/public/Get-Position-Tier-Data",
    "history_candles": "https://www.bitget.com/api-doc/uta/public/Get-History-Candle-Data",
}

RequestFn = Callable[[str, Mapping[str, str]], Mapping[str, Any]]


class BitgetPublicApiError(RuntimeError):
    """Raised when Bitget returns a non-success public response."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _parse_timestamp(value: str, *, end_of_day: bool = False) -> int:
    raw = str(value).strip()
    if raw.isdigit():
        return int(raw)
    if len(raw) == 10:
        parsed = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        if end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999000)
    else:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _iso_timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _default_request(path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
    query = urlencode(sorted((str(key), str(value)) for key, value in params.items()))
    request = Request(
        f"{API_BASE}{path}?{query}",
        headers={"Accept": "application/json", "User-Agent": "passivbot-public-data/1"},
        method="GET",
    )
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, Mapping):
        raise BitgetPublicApiError(f"Bitget response for {path} is not an object")
    if str(payload.get("code")) != "00000":
        raise BitgetPublicApiError(
            f"Bitget public endpoint {path} returned code {payload.get('code')}: {payload.get('msg')}"
        )
    return payload


class _PersistentBitgetRequester:
    """Reuse one public HTTPS connection for long, read-only downloads.

    Historical candles can require thousands of requests.  ``urlopen`` creates
    a fresh TLS connection for every page on this machine, which makes a full
    minute archive unnecessarily slow.  This requester keeps the connection
    alive, retries one broken or rate-limited response, and has no account or
    trading credentials.
    """

    def __init__(self, *, host: str = "api.bitget.com") -> None:
        self.host = host
        self._connection: http.client.HTTPSConnection | None = None

    def _close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _get_connection(self) -> http.client.HTTPSConnection:
        if self._connection is None:
            self._connection = http.client.HTTPSConnection(self.host, timeout=30)
        return self._connection

    def __call__(self, request_path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        query = urlencode(sorted((str(key), str(value)) for key, value in params.items()))
        target = f"{request_path}?{query}"
        for attempt in range(2):
            try:
                connection = self._get_connection()
                connection.request(
                    "GET",
                    target,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "passivbot-public-data/1",
                        "Connection": "keep-alive",
                    },
                )
                response = connection.getresponse()
                body = response.read()
                if response.will_close:
                    self._close()
                if response.status == 429 or response.status >= 500:
                    self._close()
                    if attempt == 0:
                        time.sleep(0.5)
                        continue
                if response.status < 200 or response.status >= 300:
                    raise BitgetPublicApiError(
                        f"Bitget public endpoint {request_path} returned HTTP {response.status}"
                    )
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise BitgetPublicApiError(
                        f"Bitget response for {request_path} is not an object"
                    )
                if str(payload.get("code")) != "00000":
                    raise BitgetPublicApiError(
                        f"Bitget public endpoint {request_path} returned code "
                        f"{payload.get('code')}: {payload.get('msg')}"
                    )
                return payload
            except (OSError, http.client.HTTPException, json.JSONDecodeError):
                self._close()
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise
        raise BitgetPublicApiError(f"Bitget public endpoint {request_path} request failed")


def build_public_requester() -> RequestFn:
    """Create a reusable, unauthenticated requester for CLI downloads."""

    return _PersistentBitgetRequester()


def _rows(payload: Mapping[str, Any], key: str) -> list[Any]:
    data = payload.get("data")
    if key == "resultList":
        return list(data.get(key) or []) if isinstance(data, Mapping) else []
    return list(data or []) if isinstance(data, list) else []


def _dedupe_by_timestamp(rows: list[list[Any]]) -> list[list[Any]]:
    by_timestamp: dict[int, list[Any]] = {}
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        try:
            timestamp = int(row[0])
        except (TypeError, ValueError):
            continue
        by_timestamp[timestamp] = row
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def _fetch_funding_history(
    request: RequestFn,
    *,
    category: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    max_pages: int,
    delay_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    raw_pages: list[dict[str, Any]] = []
    cursor = 1
    for page_number in range(1, max_pages + 1):
        payload = dict(
            request(
                "/api/v3/market/history-fund-rate",
                {
                    "category": category,
                    "symbol": symbol,
                    "limit": "100",
                    "cursor": str(cursor),
                },
            )
        )
        raw_pages.append(payload)
        page_rows = _rows(payload, "resultList")
        if not page_rows:
            break
        rows.extend(row for row in page_rows if isinstance(row, Mapping))
        timestamps = []
        for row in page_rows:
            try:
                timestamp = int(row["fundingRateTimestamp"])
            except (KeyError, TypeError, ValueError):
                continue
            timestamps.append(timestamp)
        if timestamps and min(timestamps) <= start_ms:
            break
        cursor += 1
        if delay_seconds:
            time.sleep(delay_seconds)
    filtered = []
    seen: set[int] = set()
    for row in rows:
        try:
            timestamp = int(row["fundingRateTimestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if start_ms <= timestamp <= end_ms and timestamp not in seen:
            seen.add(timestamp)
            filtered.append(
                {
                    "symbol": str(row.get("symbol", symbol)),
                    "funding_rate": float(row["fundingRate"]),
                    "timestamp_ms": timestamp,
                }
            )
    return sorted(filtered, key=lambda row: row["timestamp_ms"]), raw_pages


def _fetch_candle_history(
    request: RequestFn,
    *,
    category: str,
    symbol: str,
    candle_type: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    max_pages: int,
    delay_seconds: float,
) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    raw_rows: list[list[Any]] = []
    raw_pages: list[dict[str, Any]] = []
    # The endpoint treats endTime as an exclusive, minute-aligned boundary.
    # Advancing by one millisecond still returns the previous minute, which
    # silently loses the requested last candle.
    page_end = end_ms + _parse_interval_ms(interval)
    for _page_number in range(1, max_pages + 1):
        payload = dict(
            request(
                "/api/v3/market/history-candles",
                {
                    "category": category,
                    "symbol": symbol,
                    "interval": interval,
                    "type": candle_type,
                    "limit": "100",
                    "endTime": str(page_end),
                },
            )
        )
        raw_pages.append(payload)
        page_rows = _rows(payload, "data")
        if not page_rows:
            break
        raw_rows.extend(row for row in page_rows if isinstance(row, list))
        timestamps = []
        for row in page_rows:
            try:
                timestamps.append(int(row[0]))
            except (IndexError, TypeError, ValueError):
                continue
        if not timestamps:
            break
        oldest = min(timestamps)
        if oldest <= start_ms:
            break
        # Bitget treats endTime as an inclusive boundary.  Keep the oldest
        # row as the next page boundary and deduplicate after pagination;
        # subtracting one millisecond drops one candle at every page seam.
        next_end = oldest
        if next_end >= page_end:
            raise BitgetPublicApiError(
                f"Bitget {candle_type} history did not move backwards at {page_end}"
            )
        page_end = next_end
        if delay_seconds:
            time.sleep(delay_seconds)
    filtered = [
        row
        for row in _dedupe_by_timestamp(raw_rows)
        if start_ms <= int(row[0]) <= end_ms
    ]
    return filtered, raw_pages


def _fetch_candle_history_parallel(
    *,
    request_factory: Callable[[], RequestFn],
    category: str,
    symbol: str,
    candle_type: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    max_pages: int,
    max_workers: int,
) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    """Fetch independent candle pages with bounded public concurrency.

    Bitget returns at most 100 one-minute rows per request.  The endpoint's
    boundary is exclusive, so page ``n`` asks for the end of the next 100-row
    block.  Pages are sorted by their index before being returned, making the
    saved response hash deterministic despite concurrent completion order.
    """

    interval_ms = _parse_interval_ms(interval)
    rows_per_page = 100
    expected_pages = max(
        0,
        ((end_ms - start_ms) // (rows_per_page * interval_ms)) + 1,
    )
    page_count = min(max_pages, expected_pages)
    thread_state = threading.local()

    def fetch_page(page_index: int) -> tuple[int, list[list[Any]], dict[str, Any]]:
        page_end = end_ms - page_index * rows_per_page * interval_ms + interval_ms
        requester = getattr(thread_state, "requester", None)
        if requester is None:
            requester = request_factory()
            thread_state.requester = requester
        payload = dict(
            requester(
                "/api/v3/market/history-candles",
                {
                    "category": category,
                    "symbol": symbol,
                    "interval": interval,
                    "type": candle_type,
                    "limit": "100",
                    "endTime": str(page_end),
                },
            )
        )
        return page_index, _rows(payload, "data"), payload

    pages: list[tuple[int, list[list[Any]], dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_page, page_index) for page_index in range(page_count)]
        for future in futures:
            pages.append(future.result())
    pages.sort(key=lambda page: page[0])
    raw_rows = [row for _, rows, _ in pages for row in rows if isinstance(row, list)]
    raw_pages = [payload for _, _, payload in pages]
    filtered = [
        row
        for row in _dedupe_by_timestamp(raw_rows)
        if start_ms <= int(row[0]) <= end_ms
    ]
    return filtered, raw_pages


def _coverage(
    rows: list[list[Any]], *, start_ms: int, end_ms: int, interval_ms: int
) -> dict[str, Any]:
    timestamps: list[int] = []
    invalid_rows = 0
    nan_values = 0
    for row in rows:
        try:
            timestamp = int(row[0])
            values = [float(value) for value in row[1:5]]
        except (IndexError, TypeError, ValueError):
            invalid_rows += 1
            continue
        timestamps.append(timestamp)
        nan_values += sum(value != value for value in values)
    expected = max(0, ((end_ms - start_ms) // interval_ms) + 1)
    actual = sorted(set(timestamp for timestamp in timestamps if start_ms <= timestamp <= end_ms))
    actual_set = set(actual)
    missing = sum(
        1
        for timestamp in range(start_ms, end_ms + interval_ms, interval_ms)
        if timestamp not in actual_set
    )
    return {
        "expected_rows": expected,
        "actual_rows": len(actual),
        "coverage_ratio": (len(actual) / expected) if expected else 0.0,
        "missing_rows": missing,
        "invalid_rows": invalid_rows,
        "nan_values": nan_values,
        "future_rows": sum(timestamp > end_ms for timestamp in timestamps),
        "first_timestamp_ms": actual[0] if actual else None,
        "last_timestamp_ms": actual[-1] if actual else None,
    }


def _funding_quality(
    rows: list[dict[str, Any]], *, start_ms: int, end_ms: int, interval_hours: int
) -> dict[str, Any]:
    return validate_funding_rows(
        rows,
        start_ms=start_ms,
        end_ms=end_ms,
        interval_hours=interval_hours,
    )


def _pagination_quality(pages: list[Mapping[str, Any]], key: str) -> dict[str, Any]:
    """Record pagination boundaries without retaining duplicate raw pages."""

    counts = [len(_rows(page, key)) for page in pages]
    return {
        "page_count": len(pages),
        "page_row_counts": counts,
        "last_page_empty": bool(counts) and counts[-1] == 0,
        "max_page_size": max(counts, default=0),
    }


def collect_public_inputs(
    *,
    symbol: str = "BTCUSDT",
    category: str = "USDT-FUTURES",
    interval: str = "1H",
    start: str,
    end: str,
    max_pages: int = 100,
    delay_seconds: float = 0.0,
    request: RequestFn = _default_request,
) -> dict[str, Any]:
    """Collect and quality-check public Bitget execution inputs."""

    start_ms = _parse_timestamp(start)
    end_ms = _parse_timestamp(end, end_of_day=True)
    if start_ms > end_ms:
        raise ValueError("start must not be after end")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    request_specs = {
        "instruments": (
            "/api/v3/market/instruments",
            {"category": category, "symbol": symbol},
            "data",
        ),
        "position_tiers": (
            "/api/v3/market/position-tier",
            {"category": category, "symbol": symbol},
            "data",
        ),
        "current_funding": (
            "/api/v3/market/current-fund-rate",
            {"category": category, "symbol": symbol},
            "data",
        ),
        "tickers": (
            "/api/v3/market/tickers",
            {"category": category, "symbol": symbol},
            "data",
        ),
    }
    snapshots: dict[str, Any] = {}
    response_hashes: dict[str, str] = {}
    request_log: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for name, (path, params, _data_key) in request_specs.items():
        try:
            payload = dict(request(path, params))
            snapshots[name] = payload
            response_hashes[name] = _sha256(payload)
            request_log.append({"name": name, "path": path, "params": dict(params)})
        except Exception as exc:  # keep partial public evidence inspectable
            errors[name] = f"{type(exc).__name__}: {exc}"

    funding_rows: list[dict[str, Any]] = []
    funding_pages: list[dict[str, Any]] = []
    mark_rows: list[list[Any]] = []
    index_rows: list[list[Any]] = []
    mark_pages: list[dict[str, Any]] = []
    index_pages: list[dict[str, Any]] = []
    try:
        funding_rows, funding_pages = _fetch_funding_history(
            request,
            category=category,
            symbol=symbol,
            start_ms=start_ms,
            end_ms=end_ms,
            max_pages=max_pages,
            delay_seconds=delay_seconds,
        )
    except Exception as exc:
        errors["funding_history"] = f"{type(exc).__name__}: {exc}"
    try:
        mark_rows, mark_pages = _fetch_candle_history(
            request,
            category=category,
            symbol=symbol,
            candle_type="mark",
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
            max_pages=max_pages,
            delay_seconds=delay_seconds,
        )
    except Exception as exc:
        errors["mark_price_history"] = f"{type(exc).__name__}: {exc}"
    try:
        index_rows, index_pages = _fetch_candle_history(
            request,
            category=category,
            symbol=symbol,
            candle_type="index",
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
            max_pages=max_pages,
            delay_seconds=delay_seconds,
        )
    except Exception as exc:
        errors["index_price_history"] = f"{type(exc).__name__}: {exc}"

    for name, pages in (
        ("funding_history", funding_pages),
        ("mark_price_history", mark_pages),
        ("index_price_history", index_pages),
    ):
        response_hashes[name] = _sha256(pages)

    instruments = _rows(snapshots.get("instruments", {}), "data")
    fund_interval_hours = 8
    if instruments and isinstance(instruments[0], Mapping):
        try:
            fund_interval_hours = int(instruments[0].get("fundInterval", 8))
        except (TypeError, ValueError):
            fund_interval_hours = 8
    interval_ms = _parse_interval_ms(interval)
    mark_quality = _coverage(mark_rows, start_ms=start_ms, end_ms=end_ms, interval_ms=interval_ms)
    index_quality = _coverage(index_rows, start_ms=start_ms, end_ms=end_ms, interval_ms=interval_ms)
    funding_quality = _funding_quality(
        funding_rows,
        start_ms=start_ms,
        end_ms=end_ms,
        interval_hours=fund_interval_hours,
    )
    requested_end_is_available = end_ms <= now_ms - 60_000
    quality = {
        "requested_start_utc": _iso_timestamp(start_ms),
        "requested_end_utc": _iso_timestamp(end_ms),
        "collection_time_utc": _iso_timestamp(now_ms),
        "requested_end_is_available": requested_end_is_available,
        "market_interval": interval,
        "market_interval_ms": interval_ms,
        "funding": funding_quality,
        "pagination": {
            "funding_history": _pagination_quality(funding_pages, "resultList"),
            "mark_price_history": _pagination_quality(mark_pages, "data"),
            "index_price_history": _pagination_quality(index_pages, "data"),
        },
        "mark": mark_quality,
        "index": index_quality,
        "no_nan_or_invalid_mark_rows": mark_quality["invalid_rows"] == 0
        and mark_quality["nan_values"] == 0,
        "no_nan_or_invalid_index_rows": index_quality["invalid_rows"] == 0
        and index_quality["nan_values"] == 0,
        "no_future_market_rows": mark_quality["future_rows"] == 0
        and index_quality["future_rows"] == 0,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "collected_public_only",
        "collected_at_utc": _iso_timestamp(now_ms),
        "exchange": "bitget",
        "symbol": symbol,
        "category": category,
        "request": {
            "start": start,
            "end": end,
            "interval": interval,
            "max_pages": max_pages,
        },
        "read_only_only": True,
        "trading_operations_count": 0,
        "requests": request_log,
        "response_sha256": response_hashes,
        "snapshots": snapshots,
        "funding_history": funding_rows,
        "mark_price_history": mark_rows,
        "index_price_history": index_rows,
        "quality": quality,
        "errors": errors,
        "documentation": DEFAULT_DOCS,
    }


def _parse_interval_ms(interval: str) -> int:
    normalized = str(interval).strip().lower()
    if normalized.endswith("m"):
        return int(normalized[:-1]) * 60_000
    if normalized.endswith("h"):
        return int(normalized[:-1]) * 60 * 60_000
    if normalized.endswith("d"):
        return int(normalized[:-1]) * 24 * 60 * 60_000
    raise ValueError(f"unsupported interval {interval!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--category", default="USDT-FUTURES")
    parser.add_argument("--interval", default="1H")
    parser.add_argument("--start", required=True, help="UTC date or ISO timestamp")
    parser.add_argument("--end", required=True, help="UTC date or ISO timestamp")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--delay-seconds", type=float, default=0.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "analysis_reports/crypto_bot_roadmap/inputs/"
            "bitget_btc_public_execution_inputs.json"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_pages < 1:
        raise ValueError("--max-pages must be positive")
    result = collect_public_inputs(
        symbol=args.symbol,
        category=args.category,
        interval=args.interval,
        start=args.start,
        end=args.end,
        max_pages=args.max_pages,
        delay_seconds=args.delay_seconds,
        request=build_public_requester(),
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "funding_rows": len(result["funding_history"]),
                "mark_rows": len(result["mark_price_history"]),
                "index_rows": len(result["index_price_history"]),
                "errors": len(result["errors"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
