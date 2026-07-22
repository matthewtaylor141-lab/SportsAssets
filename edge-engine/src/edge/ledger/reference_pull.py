"""
pull_data_v3.py — Streaming, parallel full-history puller.

Design (for a 3-6M fill account; July 2026 API constraints):
  - /activity (type=TRADE) honors start/end unix-second filters; /trades caps
    offset at ~10,500 and ignores time params, so it cannot serve full history.
  - History [2025-07-01, now] is split into ~4-day windows on a shared queue.
    N_WORKERS threads each take a window and cursor-paginate inside it
    (end=cursor moving down to window start), appending trimmed rows straight
    to a per-worker CSV shard — constant memory, nothing held in RAM.
  - Boundary duplicates (same-second overlaps within and across windows) are
    removed in the merge step by fill key (tx_hash, asset, side, price, size,
    timestamp).
  - Gamma metadata: chunks of 40 condition_ids WITH explicit limit=40 (gamma
    silently caps the response at 20 rows otherwise), parallelized.

Outputs: data/trades_raw.csv, data/markets_meta.csv, data/sample_raw_trade.json
"""

import csv
import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

WALLET = "0x204f72f35326db932158cba6adff0b9a1da95e14"  # swisstony
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
PAGE_SIZE = 500
N_WORKERS = 5
WINDOW_SEC = 4 * 86400
HISTORY_START = int(datetime(2025, 7, 1, tzinfo=timezone.utc).timestamp())
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SHARD_DIR = DATA_DIR / "shards"

FIELDS = ["timestamp", "condition_id", "market_slug", "market_question", "side",
          "outcome", "outcome_index", "price", "size", "usdc_size", "asset_id", "tx_hash"]

_local = threading.local()


def _sess():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def _get(url, params, retries=6):
    for attempt in range(retries):
        try:
            r = _sess().get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return None


def trim(t):
    return [t.get("timestamp"), t.get("conditionId"), t.get("slug"), t.get("title"),
            t.get("side"), t.get("outcome"), t.get("outcomeIndex"), t.get("price"),
            t.get("size"), t.get("usdcSize"), t.get("asset"), t.get("transactionHash")]


def fill_key(t):
    return (t.get("transactionHash"), t.get("asset"), t.get("side"),
            t.get("price"), t.get("size"), t.get("timestamp"))


counters = {"fills": 0, "windows_done": 0, "windows_total": 0}
lock = threading.Lock()
saved_sample = threading.Event()


def pull_window(win_start, win_end, writer):
    """Cursor-paginate one window newest->oldest, write rows via writer."""
    end_cursor = win_end
    boundary_keys = set()
    n = 0
    while True:
        params = {"user": WALLET, "type": "TRADE", "limit": PAGE_SIZE,
                  "start": win_start, "end": end_cursor}
        batch = _get(f"{DATA_API}/activity", params)
        if not batch:
            break
        if not saved_sample.is_set():
            saved_sample.set()
            (DATA_DIR / "sample_raw_trade.json").write_text(json.dumps(batch[0], indent=2))
        fresh = [t for t in batch if fill_key(t) not in boundary_keys]
        for t in fresh:
            writer.writerow(trim(t))
        n += len(fresh)
        oldest = min(t["timestamp"] for t in batch)
        if len(batch) < PAGE_SIZE:
            break
        if not fresh:
            end_cursor = oldest - 1
            boundary_keys = set()
        else:
            end_cursor = oldest
            boundary_keys = {fill_key(t) for t in batch if t["timestamp"] == oldest}
    return n


def trade_worker(wq, wid):
    shard = SHARD_DIR / f"trades_{wid}.csv"
    with open(shard, "a", newline="") as f:
        w = csv.writer(f)
        while True:
            try:
                ws, we = wq.get_nowait()
            except queue.Empty:
                return
            try:
                n = pull_window(ws, we, w)
                f.flush()
            except Exception as e:
                print(f"[w{wid}] window {datetime.utcfromtimestamp(ws).date()} FAILED: {e}",
                      flush=True)
                n = 0
            with lock:
                counters["fills"] += n
                counters["windows_done"] += 1
                print(f"[w{wid}] {datetime.utcfromtimestamp(ws).date()} -> "
                      f"{datetime.utcfromtimestamp(we).date()}: {n:,} fills | "
                      f"TOTAL {counters['fills']:,} | windows "
                      f"{counters['windows_done']}/{counters['windows_total']}", flush=True)


def pull_all_trades():
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    for old in SHARD_DIR.glob("trades_*.csv"):
        old.unlink()
    now = int(time.time()) + 3600
    wq = queue.Queue()
    t = HISTORY_START
    while t < now:
        wq.put((t, min(t + WINDOW_SEC, now)))
        t += WINDOW_SEC
    counters["windows_total"] = wq.qsize()
    print(f"{counters['windows_total']} windows, {N_WORKERS} workers", flush=True)
    threads = [threading.Thread(target=trade_worker, args=(wq, i), daemon=True)
               for i in range(N_WORKERS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    print(f"TRADE PULL DONE: {counters['fills']:,} fills (pre-merge)", flush=True)


def merge_shards():
    print("merging shards...", flush=True)
    dfs = []
    for shard in sorted(SHARD_DIR.glob("trades_*.csv")):
        df = pd.read_csv(shard, names=FIELDS, header=None,
                         dtype={"outcome_index": "float64"})
        dfs.append(df)
    big = pd.concat(dfs, ignore_index=True)
    del dfs
    before = len(big)
    big = big.drop_duplicates(subset=["tx_hash", "asset_id", "side", "price",
                                      "size", "timestamp"])
    print(f"  {before:,} rows -> {len(big):,} after dedupe", flush=True)
    big["timestamp"] = pd.to_datetime(pd.to_numeric(big["timestamp"], errors="coerce"),
                                      unit="s", errors="coerce")
    big["price"] = pd.to_numeric(big["price"], errors="coerce")
    big["size"] = pd.to_numeric(big["size"], errors="coerce")
    big = big.dropna(subset=["timestamp", "price", "size"])
    big = big.sort_values("timestamp").reset_index(drop=True)
    big.to_csv(DATA_DIR / "trades_raw.csv", index=False)
    print(f"  wrote trades_raw.csv ({len(big):,} rows)", flush=True)
    return big


def pull_markets(condition_ids):
    ids = [c for c in pd.unique(condition_ids) if isinstance(c, str) and c]
    print(f"metadata for {len(ids):,} unique markets", flush=True)
    CHUNK = 40
    chunks = [ids[i:i + CHUNK] for i in range(0, len(ids), CHUNK)]
    cq = queue.Queue()
    for c in chunks:
        cq.put(c)
    rows, mlock = [], threading.Lock()
    done = {"n": 0}

    def mworker():
        while True:
            try:
                chunk = cq.get_nowait()
            except queue.Empty:
                return
            try:
                params = [("condition_ids", c) for c in chunk] + [("limit", len(chunk))]
                batch = _get(f"{GAMMA_API}/markets", params)
                if isinstance(batch, dict):
                    batch = batch.get("data", [])
            except Exception as e:
                print(f"  gamma chunk failed: {e}", flush=True)
                batch = []
            local = []
            for m in batch or []:
                tags = m.get("tags") or []
                if tags and isinstance(tags[0], dict):
                    tags = [t.get("label", "") for t in tags]
                local.append({
                    "condition_id": m.get("conditionId") or m.get("condition_id"),
                    "question": m.get("question"),
                    "slug": m.get("slug"),
                    "tags": "|".join(str(t) for t in tags),
                    "closed": m.get("closed"),
                    "outcomes": json.dumps(m.get("outcomes"))
                                if not isinstance(m.get("outcomes"), str) else m.get("outcomes"),
                    "outcome_prices": json.dumps(m.get("outcomePrices"))
                                if not isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices"),
                    "end_date": m.get("endDate") or m.get("end_date_iso"),
                    "resolved_at": m.get("closedTime") or m.get("umaResolutionStatus"),
                })
            with mlock:
                rows.extend(local)
                done["n"] += 1
                if done["n"] % 50 == 0:
                    print(f"  {done['n']}/{len(chunks)} chunks "
                          f"({len(rows):,} markets)", flush=True)

    threads = [threading.Thread(target=mworker, daemon=True) for _ in range(N_WORKERS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    meta = pd.DataFrame(rows).drop_duplicates(subset=["condition_id"])
    meta.to_csv(DATA_DIR / "markets_meta.csv", index=False)
    missing = len(ids) - len(meta)
    print(f"  wrote markets_meta.csv ({len(meta):,} rows; {missing:,} ids "
          f"unresolved by gamma)", flush=True)


if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    t0 = time.time()
    pull_all_trades()
    trades = merge_shards()
    pull_markets(trades["condition_id"])
    print(f"PULL COMPLETE in {(time.time()-t0)/60:.1f} min", flush=True)
