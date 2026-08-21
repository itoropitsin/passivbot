from pathlib import Path

import numpy as np

from ohlcv_catalog import OhlcvCatalog
from ohlcv_store import OhlcvStore
from tools.bitget_ohlcv_quality import audit_ohlcv_cache


def _write_store(root: Path, values: np.ndarray, timestamps: list[int]) -> OhlcvStore:
    catalog = OhlcvCatalog(root / "catalog.sqlite")
    store = OhlcvStore(root, catalog)
    store.write_rows("bitget", "1m", "BTC/USDT:USDT", np.asarray(timestamps, dtype=np.int64), values)
    return store


def test_quality_passes_complete_finite_ordered_cache(tmp_path: Path):
    root = tmp_path / "ohlcvs"
    _write_store(
        root,
        np.asarray(
            [[101.0, 99.0, 100.0, 1.0], [103.0, 101.0, 102.0, 2.0], [105.0, 103.0, 104.0, 3.0]],
            dtype=np.float32,
        ),
        [0, 60_000, 120_000],
    )

    result = audit_ohlcv_cache(
        root=root,
        exchange="bitget",
        timeframe="1m",
        symbol="BTC/USDT:USDT",
        start="1970-01-01T00:00:00Z",
        end="1970-01-01T00:02:00Z",
        as_of="1970-01-01T00:02:00Z",
    )

    assert result["status"] == "passed"
    assert result["valid_rows"] == 3
    assert result["missing_rows"] == 0
    assert result["value_quality"] == {
        "negative_volume_rows": 0,
        "nonfinite_rows": 0,
        "nonpositive_price_rows": 0,
        "price_order_rows": 0,
    }
    assert result["checks"]["chunk_checksums_verified"] is True
    assert result["cache_path"] == str(root.resolve())
    assert result["remote_fetch_policy"] == "local_only"
    assert result["remote_fetch_count"] == 0


def test_quality_blocks_missing_and_invalid_rows(tmp_path: Path):
    root = tmp_path / "ohlcvs"
    _write_store(
        root,
        np.asarray(
            [[101.0, 99.0, 100.0, 1.0], [99.0, 101.0, 102.0, -1.0]],
            dtype=np.float32,
        ),
        [0, 120_000],
    )
    # The middle minute remains invalid in the v2 valid mask.
    result = audit_ohlcv_cache(
        root=root,
        exchange="bitget",
        timeframe="1m",
        symbol="BTC/USDT:USDT",
        start="1970-01-01T00:00:00Z",
        end="1970-01-01T00:02:00Z",
    )

    assert result["status"] == "blocked"
    assert result["missing_rows"] == 1
    assert result["value_quality"]["price_order_rows"] == 1
    assert result["value_quality"]["negative_volume_rows"] == 1


def test_quality_blocks_persistent_gap_even_if_range_is_present(tmp_path: Path):
    root = tmp_path / "ohlcvs"
    store = _write_store(
        root,
        np.asarray([[101.0, 99.0, 100.0, 1.0]], dtype=np.float32),
        [0],
    )
    store.catalog.mark_gap(
        exchange="bitget",
        timeframe="1m",
        symbol="BTC/USDT:USDT",
        start_ts=0,
        end_ts=0,
        reason="test_gap",
        persistent=True,
    )

    result = audit_ohlcv_cache(
        root=root,
        exchange="bitget",
        timeframe="1m",
        symbol="BTC/USDT:USDT",
        start="1970-01-01T00:00:00Z",
        end="1970-01-01T00:00:00Z",
    )

    assert result["status"] == "blocked"
    assert result["checks"]["no_persistent_gaps"] is False
