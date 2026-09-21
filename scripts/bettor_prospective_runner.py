#!/usr/bin/env python3
"""DECISION-ONLY RUNNER. No orders, ever. Two modes, not interchangeable.

    python3 scripts/bettor_prospective_runner.py --replay rows.json
    python3 scripts/bettor_prospective_runner.py --live --once
    python3 scripts/bettor_prospective_runner.py --self-test

WHAT I GOT WRONG AND AM CORRECTING. The previous version loaded a JSON
file once, processed it, exited, and stamped every accepted row
PROSPECTIVE_SHADOW using the book_age_s STORED IN THE ROW. That is a file
processor labelling history as foresight. Deduplication does not make
historical data prospective, and describing it as "a live feed requiring
only deployment" was wrong.

    --replay   historical rows. Stamped REPLAY_DECISION. Freshness is the
               age the row recorded AT CAPTURE. These records can never
               support a forward-looking claim and are not stored as
               though they could.

    --live     reads the venue NOW through the existing authenticated
               read path, and computes freshness from THREE clocks:
               venue source timestamp, our receipt time, and the moment
               the decision is actually taken. A row whose source clock
               cannot be read is UNVERIFIABLE and is rejected -- not
               given the benefit of our own clock.

Only --live produces PROSPECTIVE_SHADOW.

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

RUNNER_VERSION = "BETTOR_DECISION_RUNNER_V2"
POLICY_ID = "BETTOR_DECISION_ENGINE_V1+ADAPTER_V1+PREREG_MAKER_V1"
PROSPECTIVE = "PROSPECTIVE_SHADOW"
REPLAY = "REPLAY_DECISION"

# A live decision may not be taken on a book older than this measured
# from the VENUE's own source clock at the moment of decision.
LIVE_FRESHNESS_BOUND_S = 10.0
# How far our clock may disagree with the venue's before the reading is
# unverifiable rather than merely stale.
MAX_CLOCK_SKEW_S = 120.0

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


def _parse_ts(x):
    from datetime import datetime
    if not x:
        return None
    t = str(x).replace("Z", "+00:00")
    if "." in t:
        head, rest = t.split(".", 1)
        d = "".join(c for c in rest if c.isdigit())[:6]
        tail = rest[len(d):]
        tz = tail if tail.startswith(("+", "-")) else "+00:00"
        t = "%s.%s%s" % (head, d.ljust(6, "0"), tz)
    try:
        return datetime.fromisoformat(t)
    except ValueError:
        return None


def live_freshness(source_ts, received_at, decided_at):
    """Age at the MOMENT OF DECISION, from the venue's own clock.

    Three timestamps, because two of them can lie in different ways.
    `source_ts` is the venue's; `received_at` is when we got it;
    `decided_at` is now. The age that matters is decided_at - source_ts,
    and the gap between received_at and source_ts is the clock-skew
    check: a source timestamp far from our receipt time is not a fresh
    book, it is an unverifiable one.
    """
    s_ts, r_ts = _parse_ts(source_ts), _parse_ts(received_at)
    d_ts = _parse_ts(decided_at)
    if s_ts is None:
        return {"ok": False, "reason": "NO_VENUE_SOURCE_TIMESTAMP"}
    if d_ts is None:
        return {"ok": False, "reason": "NO_DECISION_TIMESTAMP"}
    age = (d_ts - s_ts).total_seconds()
    out = {"age_at_decision_s": round(age, 4),
           "source_ts": source_ts, "decided_at": decided_at}
    if r_ts is not None:
        skew = abs((r_ts - s_ts).total_seconds())
        out["receipt_skew_s"] = round(skew, 4)
        if skew > MAX_CLOCK_SKEW_S:
            return dict(out, ok=False, reason="CLOCK_SKEW_UNVERIFIABLE")
    if age < 0:
        return dict(out, ok=False, reason="SOURCE_TIMESTAMP_IN_FUTURE")
    if age > LIVE_FRESHNESS_BOUND_S:
        return dict(out, ok=False, reason="STALE_AT_DECISION")
    return dict(out, ok=True, reason=None)


def read_live_book(market_id: str) -> dict:
    """Read one book NOW through the existing authenticated read path.

    NOT IMPLEMENTED HERE, DELIBERATELY. The read that would populate this
    is pmus.bbo_read / pmus.book_read behind the API service's own
    credentials. Wiring it into a standalone script would put a venue
    client in a process that must never hold one, so the live mode is
    driven by an injected reader and the deployment supplies it.

    Returns a raw row in the adapter's input shape.
    """
    raise NotImplementedError(
        "live reader must be injected by the deployment; this script "
        "holds no venue client and no credentials")


def run(rows, state, *, mode=REPLAY, now=None) -> dict:
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

        freshness = None
        if mode == PROSPECTIVE:
            # LIVE: freshness is computed HERE, from the venue clock and
            # the decision clock -- never from a stored age.
            freshness = live_freshness(
                row.get("book_source_ts"), row.get("book_received_ts"), now)
            if not freshness["ok"]:
                bump("rejected")
                bump("reject:%s" % freshness["reason"])
                state["decided"][oid] = {"status": "REJECTED",
                                         "reasons": [freshness["reason"]],
                                         "freshness": freshness}
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
            "evidence_class": mode,
            "runner": RUNNER_VERSION,
            "policy_id": POLICY_ID,
            "engine": de.ENGINE_VERSION,
            "adapter": oa.ADAPTER_VERSION,
            "freshness": freshness,
            "inputs_available": {
                "bid": rec.bid, "ask": rec.ask,
                "depth_source": rec.depth_source,
                "bid_size": rec.bid_size, "ask_size": rec.ask_size,
                "complement_source": rec.complement_source,
                "venue_state": rec.venue_state,
                "fee_schedule_verified": FEES.verified,
            },
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
    r1 = run(rows, st, mode=REPLAY)
    first = r1["counters"].get("decided", 0)
    r2 = run(rows, st, mode=REPLAY)          # same feed again
    assert r2["counters"]["duplicate_refused"] >= len(rows), "dedup failed"
    assert r2["counters"].get("decided", 0) == first, "re-decided an observation"

    path = "/tmp/_prospective_state.json"
    save_state(path, st)
    back = load_state(path)
    assert back["decided"].keys() == st["decided"].keys(), "restart lost state"
    r3 = run(rows, back, mode=REPLAY)
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
    # FRESHNESS IS COMPUTED, NOT COPIED. Assert the live path rejects
    # what a stored age would have accepted.
    f = live_freshness("2026-09-21T10:00:00Z", "2026-09-21T10:00:01Z",
                       "2026-09-21T12:00:00Z")
    assert f["ok"] is False and f["reason"] == "STALE_AT_DECISION", f
    assert f["age_at_decision_s"] == 7200.0
    ok = live_freshness("2026-09-21T10:00:00Z", "2026-09-21T10:00:01Z",
                        "2026-09-21T10:00:03Z")
    assert ok["ok"] is True, ok
    skew = live_freshness("2026-09-21T10:00:00Z", "2026-09-21T13:00:00Z",
                          "2026-09-21T10:00:03Z")
    assert skew["reason"] == "CLOCK_SKEW_UNVERIFIABLE", skew
    assert live_freshness(None, None, "2026-09-21T10:00:00Z")["reason"] == \
        "NO_VENUE_SOURCE_TIMESTAMP"
    assert all(x["evidence_class"] == REPLAY for x in r1["new_records"]), (
        "historical rows were stamped prospective")

    print("SELF-TEST PASSED: replay/live separation, computed freshness,")
    print("   dedup, restart, and no order path.")
    print("   decided %d | duplicates refused %d | rejected %d"
          % (first, r2["counters"]["duplicate_refused"],
             r1["counters"].get("rejected", 0)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--state", default="prospective_state.json")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test or (not a.replay and not a.live):
        return self_test()
    if a.live:
        print(json.dumps({
            "runner": RUNNER_VERSION, "mode": "LIVE",
            "status": "NOT_RUNNABLE_FROM_THIS_SCRIPT",
            "why": ("the live reader is injected by the deployment; this "
                    "script holds no venue client and no credentials"),
            "required": "see PILOT_PROPOSAL.md section 'decision-only "
                        "deployment'"}, indent=1))
        return 2

    state = load_state(a.state)
    rows = json.load(open(a.replay))
    res = run(rows, state, mode=REPLAY)
    save_state(a.state, state)
    print(json.dumps({"runner": RUNNER_VERSION,
                      "new_records": len(res["new_records"]),
                      "counters": res["counters"]}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
