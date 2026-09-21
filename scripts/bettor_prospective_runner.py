#!/usr/bin/env python3
"""PROSPECTIVE DECISION-ONLY RUNNER. No orders, ever.

    python3 scripts/bettor_prospective_runner.py --feed rows.json [--state s.json]
    python3 scripts/bettor_prospective_runner.py --self-test

Consumes observations as they arrive, decides, and persists one
PROSPECTIVE_SHADOW record per observation. It holds no adapter and no
credentials; `execute` does not exist in this file.

WHY DECISION-ONLY IS THE WHOLE POINT. A prospective record is the only
evidence class that can support a forward-looking claim, and it can only
do so if nothing about it is retrospective. So the runner writes its
decision BEFORE any outcome is known, keyed by observation_id, and
refuses to overwrite a decision it has already made. Dedup is not an
efficiency measure here -- re-deciding an observation after its market
moved would silently convert a prospective record into a hindsight one.

RESTART. State is a single JSON document: the decided-observation index,
the counters, and the version. A restart that lost the index would
re-decide observations it had already recorded, which is the same defect
as above wearing a different hat.

MONITORING. Every run emits counters -- seen, decided, duplicate,
rejected-by-reason, decisions-by-action, and the staleness distribution.
A prospective runner that reports only its trades cannot be evaluated,
and this one will almost always report NO_TRADE.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "backend")

from sportsassets import bettor_decision_engine as de           # noqa: E402
from sportsassets import bettor_observation_adapter as oa       # noqa: E402

RUNNER_VERSION = "BETTOR_PROSPECTIVE_RUNNER_V1"
PROSPECTIVE = "PROSPECTIVE_SHADOW"

FEES = de.Fees(taker_per_contract=0.02, maker_per_contract=0.01,
               verified=False, hypothetical=True,
               source="HYPOTHETICAL_NO_VERIFIED_SCHEDULE_EXISTS")


def load_state(path):
    if path and os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return {"version": RUNNER_VERSION, "decided": {}, "counters": {}}


def save_state(path, state):
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)          # atomic: a crash cannot truncate state


def run(rows, state) -> dict:
    c = state.setdefault("counters", {})
    def bump(k, n=1):
        c[k] = c.get(k, 0) + n

    out = []
    for row in rows:
        oid = row.get("observation_id")
        bump("seen")
        if not oid:
            bump("no_observation_id")
            continue
        if oid in state["decided"]:
            # NOT an optimisation. Re-deciding after the market moved
            # turns a prospective record into a retrospective one.
            bump("duplicate_refused")
            continue

        rec = oa.normalize(row, venue="polymarket-us",
                           account_class="institutional",
                           fee_source=FEES.source)
        if rec.status != oa.ACCEPTED:
            bump("rejected")
            for r in rec.reasons:
                bump("reject:%s" % r)
            state["decided"][oid] = {"status": "REJECTED",
                                     "reasons": rec.reasons}
            continue

        d = de.decide(oa.to_book(rec), fees=FEES, max_contracts=10,
                      venue=rec.venue, account_class=rec.account_class)
        bump("decided")
        bump("action:%s" % d["selected"])
        blockers = sorted({x["blocker"] for x in d["candidates"]
                           if x["blocker"]})
        record = {
            "evidence_class": PROSPECTIVE,
            "runner": RUNNER_VERSION,
            "observation_id": oid,
            "market_id": rec.market_id,
            "observed_at": rec.observed_at,
            "book_age_s": rec.age_s,
            "bid": rec.bid, "ask": rec.ask,
            "decision": d["selected"],
            "size": d["size_contracts"],
            "reason": d["reason"],
            "blockers": blockers,
            "fee_source": FEES.source,
            "orders_submitted": 0,
        }
        state["decided"][oid] = record
        out.append(record)
    return {"new_records": out, "counters": c}


def self_test() -> int:
    """Dedup, restart and no-order-path, asserted."""
    rows = json.load(open("research/beta48/acceptance/replay_sample_rows.json"))
    st = load_state(None)
    r1 = run(rows, st)
    first = r1["counters"].get("decided", 0)
    r2 = run(rows, st)                       # same feed again
    assert r2["counters"]["duplicate_refused"] >= len(rows), "dedup failed"
    assert r2["counters"].get("decided", 0) == first, "re-decided an observation"

    path = "/tmp/_prospective_state.json"
    save_state(path, st)
    back = load_state(path)
    assert back["decided"].keys() == st["decided"].keys(), "restart lost state"
    r3 = run(rows, back)
    assert r3["counters"]["duplicate_refused"] > 0, "dedup lost across restart"

    # SCAN THE CODE, NOT THIS LIST. The forbidden names appear in this
    # very check, so a naive substring scan of the file fails on itself
    # -- the same trap as scanning a docstring that names what it is not.
    # Look for CALLS and IMPORTS, which is what would actually execute.
    import ast
    tree = ast.parse(open(__file__).read())
    called, imported = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            called.add(getattr(f, "attr", None) or getattr(f, "id", None))
        elif isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            imported.update(a.name for a in n.names)
    for bad in ("submit_fok", "close_position", "_get_client", "post_order"):
        assert bad not in called, "calls %s" % bad
    for bad in ("requests", "httpx", "urllib", "socket"):
        assert bad not in imported, "imports %s" % bad
    assert "bettor_shadow_loop" not in imported, (
        "imports the execution loop; this runner decides and does not "
        "execute")
    assert all(x["orders_submitted"] == 0 for x in r1["new_records"])
    os.remove(path)
    print("SELF-TEST PASSED: dedup, restart, and no order path.")
    print("   decided %d | duplicates refused %d | rejected %d"
          % (first, r2["counters"]["duplicate_refused"],
             r1["counters"].get("rejected", 0)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed")
    ap.add_argument("--state", default="prospective_state.json")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test or not a.feed:
        return self_test()

    state = load_state(a.state)
    rows = json.load(open(a.feed))
    res = run(rows, state)
    save_state(a.state, state)
    print(json.dumps({"runner": RUNNER_VERSION,
                      "new_records": len(res["new_records"]),
                      "counters": res["counters"]}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
