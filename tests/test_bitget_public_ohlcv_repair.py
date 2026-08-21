from pathlib import Path

import numpy as np

from ohlcv_catalog import OhlcvCatalog
from ohlcv_store import OhlcvStore
from tools.bitget_public_ohlcv_repair import repair_public_gaps


def test_public_repair_fills_only_missing_rows(tmp_path: Path):
    root = tmp_path / "ohlcvs"
    catalog = OhlcvCatalog(root / "catalog.sqlite")
    store = OhlcvStore(root, catalog)
    timestamps = np.asarray([0, 120_000, 240_000], dtype=np.int64)
    values = np.asarray(
        [[101.0, 99.0, 100.0, 1.0], [103.0, 101.0, 102.0, 2.0], [105.0, 103.0, 104.0, 3.0]],
        dtype=np.float32,
    )
    store.write_rows("bitget", "1m", "BTC/USDT:USDT", timestamps, values)

    def request(path, params):
        assert path == "/api/v3/market/history-candles"
        end = int(params["endTime"])
        rows = [
            [str(ts), "100", "101", "99", str(100 + ts / 60_000), "1"]
            for ts in (240_000, 180_000, 120_000, 60_000, 0)
            if ts <= end
        ]
        return {"data": rows[:100]}

    report = repair_public_gaps(
        root=root,
        start="1970-01-01T00:00:00Z",
        end="1970-01-01T00:04:00Z",
        max_pages=3,
        request=request,
    )

    assert report["status"] == "repaired"
    assert report["before_missing_rows"] == 2
    assert report["repaired_rows"] == 2
    assert report["after_missing_rows"] == 0
    repaired = store.read_range("bitget", "1m", "BTC/USDT:USDT", 0, 240_000)
    assert repaired.valid.tolist() == [True] * 5
    assert repaired.values[0].tolist() == values[0].tolist()
    assert repaired.values[2].tolist() == values[1].tolist()
