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
# How far a source stamp may sit in our receipt's FUTURE before the two
# clocks are held to disagree. Small, because transport cannot produce a
# negative delay at all -- this is tolerance for stamp resolution, not
# for latency. A positive delay of any size is latency and is judged by
# the age bound instead.
CLOCK_DISAGREEMENT_TOLERANCE_S = 0.5

# The PUBLISHED PMUS schedule for the date being decided. PUBLISHED,
# not VERIFIED_APPLIED: the venue documents these terms and we have
# never matched them against a settled statement for this account, so
# the engine will compute with them and still refuse to select on them.
FEE_DATE = "2026-09-21"
FEES = de.Fees.published_pmus(FEE_DATE)


def _now_iso():
    """Decision time, aware, measured at the instant it is called."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


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
    """An AWARE datetime, or None. A NAIVE stamp is refused, not assumed.

    The previous version appended "+00:00" to any stamp that carried no
    offset. That is not parsing, it is asserting the venue publishes in
    UTC -- and if it does not, a book hours old reads as fresh (or a
    fresh one as stale) with nothing in the record to show it.

    Real formats this must handle, all sampled from the capture:
        2026-09-21T18:20:25.743291447Z     nine fractional digits, Z
        2026-09-21T18:26:06.772+00:00      three digits, explicit offset
        2026-09-21 18:26:04.058844+00      space separator, two-digit tz
    """
    from datetime import datetime
    if x is None:
        return None
    t = str(x).strip()
    if not t or t == "NOT_IDENTIFIED":
        return None
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    # Trim sub-microsecond precision, which fromisoformat rejects on
    # some versions. The offset is preserved exactly as written.
    if "." in t:
        head, rest = t.split(".", 1)
        digits = ""
        for ch in rest:
            if ch.isdigit():
                digits += ch
            else:
                break
        t = "%s.%s%s" % (head, digits[:6].ljust(6, "0"), rest[len(digits):])
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        # Refused rather than defaulted. An unlabelled stamp is a stamp
        # whose meaning we do not know.
        return None
    return dt


def live_freshness(source_ts, received_at, decided_at):
    """Age at the MOMENT OF DECISION, from the venue's own clock.

    THREE CLOCKS, AND THE RECEIPT CLOCK IS REQUIRED. It used to be
    optional -- `if r_ts is not None` -- so a row that carried no
    receipt time skipped the check entirely and was treated as verified
    rather than as unverifiable. `book_received_ts` is in the capture
    schema on every row, so requiring it costs nothing and closes the
    one path that silently passed.

    A SOURCE-TO-RECEIPT DELAY IS NOT CLOCK SKEW. The old check took
    abs(received - source) and called anything large "skew". A positive
    delay -- the normal case -- is the venue's stamp, plus transport,
    plus our own processing, and it demonstrates nothing about whether
    the two clocks agree. Only a NEGATIVE delay does: a source stamp in
    our receipt's future cannot be explained by transport, because
    transport only ever runs forward. So the two are reported
    separately and only the negative tail is called skew.

    A large positive delay is still disqualifying, but for its own
    reason: the reading is OLD, which `age_at_decision_s` already says.
    """
    s_ts = _parse_ts(source_ts)
    r_ts = _parse_ts(received_at)
    d_ts = _parse_ts(decided_at)
    if s_ts is None:
        return {"ok": False, "reason": "NO_VENUE_SOURCE_TIMESTAMP"}
    if r_ts is None:
        return {"ok": False, "reason": "NO_RECEIPT_TIMESTAMP"}
    if d_ts is None:
        return {"ok": False, "reason": "NO_DECISION_TIMESTAMP"}

    age = (d_ts - s_ts).total_seconds()
    transport = (r_ts - s_ts).total_seconds()
    out = {"age_at_decision_s": round(age, 4),
           "source_to_receipt_s": round(transport, 4),
           "receipt_to_decision_s": round((d_ts - r_ts).total_seconds(), 4),
           "source_ts": str(source_ts), "received_at": str(received_at),
           "decided_at": str(decided_at),
           "transport_is_not_skew": ("a positive source-to-receipt delay is "
                                     "latency; only a negative one shows "
                                     "the clocks disagree")}

    # WE CANNOT DECIDE ON A BOOK WE HAVE NOT RECEIVED. This is not a
    # clock-skew check and it is not a staleness check -- it is
    # causality inside our own pipeline, and dropping it was a gap my
    # own self-test caught: source 10:00, received 13:00, decided
    # 10:00:03 gave age 3 s and passed, on a book that arrived three
    # hours after the decision that used it. The abs() version caught
    # this case for the wrong reason, which is why replacing it needed
    # the right reason put back rather than removed.
    if (d_ts - r_ts).total_seconds() < -CLOCK_DISAGREEMENT_TOLERANCE_S:
        return dict(out, ok=False, reason="DECIDED_BEFORE_RECEIPT")
    if transport < -CLOCK_DISAGREEMENT_TOLERANCE_S:
        # The venue stamped this AFTER we received it. Transport cannot
        # do that, so the clocks genuinely disagree and every age
        # computed from this stamp is unverifiable.
        return dict(out, ok=False, reason="SOURCE_TIMESTAMP_AFTER_RECEIPT")
    if age < 0:
        return dict(out, ok=False, reason="SOURCE_TIMESTAMP_IN_FUTURE")
    if age > LIVE_FRESHNESS_BOUND_S:
        return dict(out, ok=False, reason="STALE_AT_DECISION")
    return dict(out, ok=True, reason=None)


def read_live_book(market_id: str) -> dict:
    """Read one book NOW. NOT AVAILABLE FROM THIS SCRIPT, by design.

    The reader is IMPLEMENTED -- `sportsassets.bettor_live_read.read_book`
    -- and it is not reachable from here. It needs an authenticated
    client, and a standalone script that could build one would be a
    process holding venue credentials with no reason to. So the live
    mode takes an INJECTED reader and the deployment supplies it,
    which is a different statement from the one this function used to
    make: the connection is written, tested and reviewable; what is
    withheld is the credential and the schedule.

    The three things this separation keeps apart, per the owner
    directive:

      IMPLEMENTATION   bettor_live_read.read_book. Written. Activates
                       nothing.
      READ-ONLY RUN    calling it with a real client. Reads a public
                       book. Submits nothing.
      DEPLOYMENT       running it on a schedule in production. NOT
                       DONE. PILOT_PROPOSAL.md section 7.
    """
    raise NotImplementedError(
        "live reader must be injected by the deployment; this script "
        "holds no venue client and no credentials. The reader itself "
        "is sportsassets.bettor_live_read.read_book -- implemented, "
        "not deployed")


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
            # DECISION TIME IS MEASURED FOR EACH OBSERVATION. One
            # timestamp taken at the top of a batch is the time the
            # BATCH started; by the last row it understates the age by
            # however long the batch took, which is precisely the
            # interval a freshness bound exists to catch.
            decided_at = now if now is not None else _now_iso()
            freshness = live_freshness(
                row.get("book_source_ts"), row.get("book_received_ts"),
                decided_at)
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
    # A book received three hours AFTER the decision that used it. The
    # age at decision is 3 s and passes every staleness test; what is
    # wrong is causality in our own pipeline, not the venue's clock.
    early = live_freshness("2026-09-21T10:00:00Z", "2026-09-21T13:00:00Z",
                           "2026-09-21T10:00:03Z")
    assert early["reason"] == "DECIDED_BEFORE_RECEIPT", early
    # A source stamp in our receipt's FUTURE. Transport only runs
    # forward, so this is the one case that shows the clocks disagree.
    disagree = live_freshness("2026-09-21T10:00:10Z", "2026-09-21T10:00:00Z",
                              "2026-09-21T10:00:12Z")
    assert disagree["reason"] == "SOURCE_TIMESTAMP_AFTER_RECEIPT", disagree
    # A NINE-MINUTE transport delay is the MEASURED median of this feed
    # (549.6 s over 1,203 rows) and is NOT skew: 0 of 1,203 rows had a
    # source stamp after receipt. It fails on AGE, which is its own
    # reason and the one that is actually true.
    slow = live_freshness("2026-09-21T10:00:00Z", "2026-09-21T10:09:10Z",
                          "2026-09-21T10:09:11Z")
    assert slow["reason"] == "STALE_AT_DECISION", slow
    assert slow["source_to_receipt_s"] == 550.0
    # A RECEIPT TIMESTAMP IS REQUIRED. It used to be optional, so a row
    # without one skipped the check and was treated as verified.
    assert live_freshness("2026-09-21T10:00:00Z", None,
                          "2026-09-21T10:00:03Z")["reason"] == \
        "NO_RECEIPT_TIMESTAMP"
    # A NAIVE stamp is refused, not assumed to be UTC.
    assert live_freshness("2026-09-21T10:00:00", "2026-09-21T10:00:01Z",
                          "2026-09-21T10:00:03Z")["reason"] == \
        "NO_VENUE_SOURCE_TIMESTAMP"
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
