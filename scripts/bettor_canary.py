#!/usr/bin/env python3
"""THE ACTIVATION CANARY.

    python3 scripts/bettor_canary.py --minutes 20
    python3 scripts/bettor_canary.py --dsn postgresql://... --minutes 20

A SHORT OPERATIONAL CHECK, run against the deployed worker's own
tables, before continuous collection. It establishes six things and
nothing more:

    1  the live socket connected and delivered valid books
    2  decisions are committed to Postgres
    3  outcome checks are progressing
    4  no real order was submitted
    5  the existing workers are not materially degraded
    6  a stop and a restart retained the records

WHAT IT IS NOT. It is NOT a profitability result and must never be read
as one. Every decision this worker takes is NO_TRADE, by construction:
`MAX_CONTRACTS` defaults to 0 and no order call is reachable from the
loop's import closure. Accepting the observation infrastructure means
the instrument works. It says nothing about whether the strategy makes
money, and a canary that passed is not evidence that it does.

EACH CHECK IS INDEPENDENT and reports what it measured. A check that
cannot be answered says NOT_ESTABLISHED and why, rather than passing
on silence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "backend")

from sportsassets import bettor_live_store as st        # noqa: E402

NOT_ESTABLISHED = "NOT_ESTABLISHED"
RESULTS: list = []


def rule(t=""):
    print("\n" + "=" * 76)
    if t:
        print(t)
        print("=" * 76)


def verdict(name, ok, detail):
    RESULTS.append({"check": name, "ok": ok, "detail": detail})
    mark = "PASS" if ok is True else ("FAIL" if ok is False
                                      else NOT_ESTABLISHED)
    print("   %-52s %s" % (name, mark))
    for line in str(detail).splitlines():
        print("        %s" % line)


async def _q(con, sql, *args):
    return await con.fetch(sql, *args)


async def run(dsn: str, minutes: float) -> int:
    store = st.PgStore(dsn=dsn) if dsn else st.PgStore()
    started = await store.start()
    pool = await store._get_pool()

    rule("BETTOR ACTIVATION CANARY  |  %s" % datetime.now(timezone.utc)
         .isoformat())
    print("   LANE        %s" % st.LANE)
    print("   WINDOW      the last %g minutes of this lane's rows" % minutes)
    print("   STORE       %s" % json.dumps(started, default=str))
    print("   NOT A PROFITABILITY RESULT. Every decision is NO_TRADE by")
    print("   construction; this checks the INSTRUMENT, not the strategy.")

    async with pool.acquire() as con:
        window = "now() - ($2 || ' minutes')::interval"

        # ── 1. the socket connected and delivered valid books ────────
        rule("1. LIVE SOCKET AND VALID BOOKS")
        rows = await _q(con,
                        "SELECT count(*) AS n, "
                        "       count(DISTINCT market_id) AS markets, "
                        "       min(written_at) AS first_at, "
                        "       max(written_at) AS last_at "
                        "  FROM bettor_live_journal "
                        " WHERE lane = $1 AND kind = 'DECISION' "
                        "   AND written_at > " + window, st.LANE,
                        str(minutes))
        r = rows[0]
        cls = await _q(con,
                       "SELECT record->>'evidence_class' AS c, count(*) AS n "
                       "  FROM bettor_live_journal "
                       " WHERE lane = $1 AND written_at > " + window +
                       " GROUP BY 1", st.LANE, str(minutes))
        classes = {x["c"]: x["n"] for x in cls}
        fresh = await _q(con,
                         "SELECT count(*) FILTER (WHERE "
                         "         (record->>'source_age_s')::float <= 10) "
                         "         AS within_bound, "
                         "       count(*) FILTER (WHERE record ? 'source_ts' "
                         "         AND record->>'source_ts' <> '') AS clocked "
                         "  FROM bettor_live_journal "
                         " WHERE lane = $1 AND kind = 'DECISION' "
                         "   AND record ? 'source_age_s' "
                         "   AND written_at > " + window, st.LANE,
                         str(minutes))
        f = fresh[0]
        live = classes.get("PROSPECTIVE_SHADOW", 0)
        detail = ("records %s over %s markets, %s..%s\n"
                  "evidence classes %s\n"
                  "with a venue clock %s, within the 10 s bound %s"
                  % (r["n"], r["markets"], r["first_at"], r["last_at"],
                     json.dumps(classes), f["clocked"], f["within_bound"]))
        if r["n"] == 0:
            verdict("a live socket delivered books", None,
                    detail + "\nno records in the window at all")
        elif live == 0:
            verdict("a live socket delivered books", False,
                    detail + "\nNOT ONE record is PROSPECTIVE_SHADOW: the "
                    "transport was not a live socket")
        else:
            verdict("a live socket delivered books",
                    bool(r["markets"] and f["clocked"]), detail)

        # ── 2. decisions committed to Postgres ───────────────────────
        rule("2. DECISIONS COMMITTED TO POSTGRES")
        acts = await _q(con,
                        "SELECT selected, count(*) AS n "
                        "  FROM bettor_live_journal "
                        " WHERE lane = $1 AND kind = 'DECISION' "
                        "   AND status = 'DECIDED' AND written_at > " +
                        window + " GROUP BY 1 ORDER BY 2 DESC", st.LANE,
                        str(minutes))
        by_action = {a["selected"]: a["n"] for a in acts}
        stat = await _q(con,
                        "SELECT status, count(*) AS n "
                        "  FROM bettor_live_journal "
                        " WHERE lane = $1 AND kind = 'DECISION' "
                        "   AND written_at > " + window +
                        " GROUP BY 1", st.LANE, str(minutes))
        by_status = {a["status"]: a["n"] for a in stat}
        dupes = await _q(con,
                         "SELECT count(*) AS n FROM ("
                         "  SELECT record_key FROM bettor_live_journal "
                         "   WHERE lane = $1 AND written_at > " + window +
                         "   GROUP BY record_key HAVING count(*) > 1) t",
                         st.LANE, str(minutes))
        detail = ("by status %s\nby action %s\nduplicate record keys %s"
                  % (json.dumps(by_status), json.dumps(by_action),
                     dupes[0]["n"]))
        verdict("decisions and refusals are in Postgres",
                (sum(by_status.values()) > 0 and dupes[0]["n"] == 0)
                if by_status else None,
                detail if by_status else detail + "\nno rows in the window")

        # ── 3. outcome checks progressing ────────────────────────────
        rule("3. OUTCOME CHECKS PROGRESSING")
        rep = await store.outcome_report()
        sett = await _q(con,
                        "SELECT count(*) AS n FROM bettor_live_journal "
                        " WHERE lane = $1 AND kind = 'SETTLEMENT' "
                        "   AND written_at > " + window, st.LANE,
                        str(minutes))
        due = await store.due_for_settlement(
            now=__import__("time").time(), limit=5)
        detail = ("cursor statuses %s\n"
                  "completed AUTHORITATIVELY %s, awaiting authoritative "
                  "(derived only) %s, read-escalated %s\n"
                  "outstanding obligations %s, settlement rows written in "
                  "the window %s"
                  % (json.dumps(rep.get("by_status", {})),
                     rep.get("authoritative"),
                     rep.get("awaiting_authoritative"),
                     rep.get("read_escalated"), rep.get("outstanding"),
                     sett[0]["n"]))
        attempted = sum(n for s_, n in rep.get("by_status", {}).items()
                        if s_ != "NEVER_ATTEMPTED")
        if not rep.get("by_status"):
            verdict("outcome collection is making progress", None,
                    detail + "\nno contracts tracked yet")
        else:
            verdict("outcome collection is making progress",
                    attempted > 0 or bool(due.get("slugs")), detail)
            print("        A DERIVED outcome is NOT a completed one: it is")
            print("        an inference from converged prices and the")
            print("        contract stays queued for the authoritative read.")

        # ── 4. no real order ─────────────────────────────────────────
        rule("4. NO REAL ORDER WAS SUBMITTED")
        ex = await _q(con,
                      "SELECT count(*) AS n FROM bettor_live_journal "
                      " WHERE lane = $1 AND (record->>'executed') = 'true'",
                      st.LANE)
        sized = await _q(con,
                         "SELECT count(*) AS n FROM bettor_live_journal "
                         " WHERE lane = $1 "
                         "   AND COALESCE((record->>'size_contracts')::float,"
                         "                0) <> 0", st.LANE)
        detail = ("records claiming an execution %s (over ALL time, not "
                  "just the window)\nrecords with a non-zero size %s"
                  % (ex[0]["n"], sized[0]["n"]))
        verdict("no execution and nothing sized",
                ex[0]["n"] == 0 and sized[0]["n"] == 0, detail)
        print("        This is a check on the RECORD. The structural")
        print("        guarantee is separate and stronger: no order call")
        print("        is reachable from the loop's import closure, and a")
        print("        test asserts it over the source.")

        # ── 5. the other workers ─────────────────────────────────────
        rule("5. EXISTING WORKERS NOT MATERIALLY DEGRADED")
        print("   NOT ANSWERABLE FROM THESE TABLES. The worker's own rows")
        print("   say nothing about its eighteen siblings. Read, in order:")
        print("     render-ops action=metrics arg=120 service=sportsassets-workers")
        print("       -> memory per instance, against the pre-deploy band;")
        print("          this service was OOM-killed at 2 GiB thirteen")
        print("          times in one evening, so RSS is the number to")
        print("          watch.")
        print("     render-ops action=events service=sportsassets-workers")
        print("       -> any restart or OOM since the deploy.")
        print("     render-ops action=logs  arg=30")
        print("       -> each sibling loop's heartbeat still arriving.")
        verdict("existing workers unaffected", None,
                "requires the Render metrics and events reads above; "
                "this script reads only the worker's own tables")

        # ── 6. stop and restart retained the records ─────────────────
        rule("6. STOP AND RECOVERY RETAINED THE RECORDS")
        boots = await _q(con,
                         "SELECT count(DISTINCT record->>'stream_epoch') "
                         "         AS epochs, count(*) AS n "
                         "  FROM bettor_live_journal "
                         " WHERE lane = $1 AND kind = 'DECISION'", st.LANE)
        led = await _q(con,
                       "SELECT saved_at, loop_version FROM bettor_live_ledger "
                       " WHERE lane = $1", st.LANE)
        total = boots[0]["n"]
        detail = ("journal rows across ALL restarts %s\n"
                  "distinct stream epochs seen %s\n"
                  "ledger last saved %s"
                  % (total, boots[0]["epochs"],
                     led[0]["saved_at"] if led else "never"))
        verdict("records survived across restarts",
                (total > 0 and bool(led)) if total else None,
                detail if total else
                detail + "\nno rows at all: nothing has run yet")
        print("        The restart itself is the operator's action:")
        print("          render-ops action=restart service=sportsassets-workers confirm=DO")
        print("        then re-run this script. Row count must RISE and")
        print("        never fall, and the recovered cursor count must")
        print("        match what was there before.")
        print("        EXPECT A SMALL LOSS AT THE SEAM. A Render restart")
        print("        arrives as SIGTERM and workers/all.py installs no")
        print("        handler, so Python terminates without running the")
        print("        final flush -- measured, not assumed. Up to one")
        print("        flush interval (%.1f s) of decisions is lost, and"
              % st.FLUSH_EVERY_S)
        print("        the last report before the restart shows how much")
        print("        in oldest_uncommitted_age_s.")

    await store.close()

    rule("VERDICT")
    ok = [r for r in RESULTS if r["ok"] is True]
    bad = [r for r in RESULTS if r["ok"] is False]
    unk = [r for r in RESULTS if r["ok"] is None]
    print("   PASS %d   FAIL %d   %s %d" % (len(ok), len(bad),
                                            NOT_ESTABLISHED, len(unk)))
    for r in bad:
        print("   FAIL  %s" % r["check"])
    for r in unk:
        print("   %s  %s" % (NOT_ESTABLISHED, r["check"]))
    print()
    print("   ACCEPTING THIS CANARY ACCEPTS THE OBSERVATION")
    print("   INFRASTRUCTURE AND NOTHING ELSE. It is not a profitability")
    print("   result, it is not evidence of one, and it must not be")
    print("   presented as either.")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("BETTOR_TEST_PG_DSN"))
    ap.add_argument("--minutes", type=float, default=20.0)
    a = ap.parse_args()
    return asyncio.run(run(a.dsn, a.minutes))


if __name__ == "__main__":
    raise SystemExit(main())
