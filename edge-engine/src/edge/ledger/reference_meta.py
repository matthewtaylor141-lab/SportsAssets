"""pull_meta_v2.py - metadata-only pull. Gamma drift (July 2026): closed
markets are excluded from /markets?condition_ids= unless closed=true is
passed. Query closed=true first (most of a year of history is resolved),
then retry the remainder with the default filter for still-open markets."""
import json
import queue
import threading
import time
from pathlib import Path

import pandas as pd
import requests

GAMMA = "https://gamma-api.polymarket.com/markets"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
N_WORKERS, CHUNK = 5, 40
_local = threading.local()


def _sess():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def _get(params, retries=5):
    for a in range(retries):
        try:
            r = _sess().get(GAMMA, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** a)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if a == retries - 1:
                raise
            time.sleep(2 ** a)


def row_of(m):
    tags = m.get("tags") or []
    if tags and isinstance(tags[0], dict):
        tags = [t.get("label", "") for t in tags]
    return {"condition_id": m.get("conditionId") or m.get("condition_id"),
            "question": m.get("question"), "slug": m.get("slug"),
            "tags": "|".join(str(t) for t in tags), "closed": m.get("closed"),
            "outcomes": json.dumps(m.get("outcomes"))
                        if not isinstance(m.get("outcomes"), str) else m.get("outcomes"),
            "outcome_prices": json.dumps(m.get("outcomePrices"))
                        if not isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices"),
            "end_date": m.get("endDate") or m.get("end_date_iso"),
            "resolved_at": m.get("closedTime") or m.get("umaResolutionStatus")}


def fetch(ids):
    rows, mlock = [], threading.Lock()
    chunks = [ids[i:i + CHUNK] for i in range(0, len(ids), CHUNK)]
    cq = queue.Queue()
    for c in chunks:
        cq.put(c)
    done = {"n": 0}

    def worker():
        while True:
            try:
                chunk = cq.get_nowait()
            except queue.Empty:
                return
            local = []
            try:
                b1 = _get([("condition_ids", c) for c in chunk]
                          + [("limit", len(chunk)), ("closed", "true")]) or []
                local += [row_of(m) for m in b1]
                got = {r["condition_id"] for r in local}
                rest = [c for c in chunk if c not in got]
                if rest:
                    b2 = _get([("condition_ids", c) for c in rest]
                              + [("limit", len(rest))]) or []
                    local += [row_of(m) for m in b2]
            except Exception as e:
                print(f"  chunk failed: {e}", flush=True)
            with mlock:
                rows.extend(local)
                done["n"] += 1
                if done["n"] % 100 == 0:
                    print(f"  {done['n']}/{len(chunks)} chunks, "
                          f"{len(rows):,} markets", flush=True)

    ts = [threading.Thread(target=worker, daemon=True) for _ in range(N_WORKERS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return rows


if __name__ == "__main__":
    ids = [c for c in pd.read_csv(DATA_DIR / "trades_raw.csv",
                                  usecols=["condition_id"])["condition_id"].unique()
           if isinstance(c, str) and c]
    print(f"{len(ids):,} unique markets", flush=True)
    meta = pd.DataFrame(fetch(ids)).drop_duplicates(subset=["condition_id"])
    meta.to_csv(DATA_DIR / "markets_meta.csv", index=False)
    print(f"wrote markets_meta.csv ({len(meta):,}/{len(ids):,}; "
          f"{len(ids) - len(meta):,} unresolved)", flush=True)
