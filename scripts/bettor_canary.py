#!/usr/bin/env python3
"""THE ACTIVATION CANARY, for the OBSERVATION INFRASTRUCTURE only.

    # 1. before the restart, after ~20 minutes of collection
    python3 scripts/bettor_canary.py --minutes 20 \
        --checkpoint-write /tmp/bettor-checkpoint.json

    # 2. after `render-ops action=restart service=sportsassets-workers
    #    confirm=DO` and a few minutes of fresh collection
    python3 scripts/bettor_canary.py --minutes 20 \
        --checkpoint /tmp/bettor-checkpoint.json \
        --evidence /tmp/bettor-ops-evidence.json

EXIT CODES. An unknown is NOT a pass:

    0   every required check PASSED
    1   at least one required check FAILED
    3   ACCEPTANCE INCOMPLETE -- a required check is NOT_ESTABLISHED

Exit 3 exists because `return 1 if bad else 0` treated "I could not
tell" as success, which is how a canary comes back green over a
scheduler that never ran and a restart that never happened.

WHAT THIS CHECKS, AND WHAT IT DOES NOT.

  IT CHECKS the observation instrument: that a live socket delivered
  FRESH books, that decisions reached Postgres, that outcome checks
  ACTUALLY RAN, that this process really did restart and keep its
  records, and that no record claims an execution.

  IT IS NOT A VENUE-ACCOUNT AUDIT. Check 4 reads OUR OWN JOURNAL. It
  says no record of ours claims an execution; it does not interrogate
  the venue account and cannot. The structural guarantee -- that no
  order call is reachable from the worker's import closure -- is a
  separate, stronger claim, asserted over the SOURCE by
  `tests/test_bettor_live_loop.py::TestNoOrderCapability`, and this
  script reports it separately rather than folding the two together.

  IT IS NOT A PROFITABILITY RESULT, and a passing run is not evidence
  of profitability. Every decision is NO_TRADE by construction. A
  green canary means the instrument works. It says nothing about
  whether the strategy makes money and must never be presented as if
  it did.

OPERATIONAL EVIDENCE COMES FROM OUTSIDE. Memory, heartbeats and events
belong to Render, not to these tables. Collect them with `render-ops`
and pass them in with `--evidence`; without that file, check 5 is
NOT_ESTABLISHED and the run exits 3.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "backend")

from sportsassets import bettor_live_store as st        # noqa: E402

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "NOT_ESTABLISHED"
EXIT_OK, EXIT_FAILED, EXIT_INCOMPLETE = 0, 1, 3

RESULTS: list = []


def rule(t=""):
    print("\n" + "=" * 76)
    if t:
        print(t)
        print("=" * 76)


def verdict(name, state, detail, *, required=True):
    RESULTS.append({"check": name, "state": state, "required": required,
                    "detail": detail})
    print("   %-50s %s%s" % (name, state,
                             "" if required else "   (not required)"))
    for line in str(detail).splitlines():
        print("        %s" % line)


def state_of(ok):
    return PASS if ok is True else (FAIL if ok is False else UNKNOWN)


# ── 1. live socket, FRESH books ──────────────────────────────────────

async def check_live_books(con, lane, minutes, boot_ids):
    rule("1. LIVE SOCKET AND FRESH BOOKS")
    # One WHERE, built once. `$3` is the boot filter and is NULL when
    # no --boot was given, which the predicate treats as "any boot".
    where = ("WHERE lane = $1 AND kind = 'DECISION' "
             "  AND written_at > now() - ($2 || ' minutes')::interval "
             "  AND ($3::text[] IS NULL OR boot_id = ANY($3::text[])) ")
    a = (lane, str(minutes), list(boot_ids) or None)

    r = await con.fetchrow(
        "SELECT count(*) AS n, count(DISTINCT market_id) AS markets, "
        "       count(DISTINCT boot_id) AS boots, "
        "       min(written_at) AS first_at, max(written_at) AS last_at "
        "  FROM bettor_live_journal " + where, *a)
    cls = await con.fetch(
        "SELECT record->>'evidence_class' AS c, count(*) AS n "
        "  FROM bettor_live_journal " + where + " GROUP BY 1", *a)
    classes = {(x["c"] or "NONE"): x["n"] for x in cls}

    # BOTH CLOCKS. The previous version computed `within_bound` and
    # then passed on market count and the presence of a source
    # timestamp, so a feed delivering only STALE books passed a check
    # named "live".
    f = await con.fetchrow(
        "SELECT count(*) AS decided, "
        "  count(*) FILTER (WHERE record ? 'source_age_s') AS with_source, "
        "  count(*) FILTER (WHERE record ? 'receipt_age_s') AS with_receipt, "
        "  count(*) FILTER (WHERE (record->>'source_age_s')::float <= $4) "
        "    AS source_fresh, "
        "  count(*) FILTER (WHERE (record->>'receipt_age_s')::float <= $5) "
        "    AS receipt_fresh, "
        "  count(DISTINCT market_id) FILTER (WHERE "
        "    (record->>'source_age_s')::float <= $4 AND "
        "    (record->>'receipt_age_s')::float <= $5) AS fresh_markets "
        "  FROM bettor_live_journal " + where + " AND status = 'DECIDED' ",
        *(a + (MAX_SOURCE_AGE_S, MAX_RECEIPT_AGE_S)))

    live = classes.get("PROSPECTIVE_SHADOW", 0)
    detail = (
        "records %s over %s markets from %s process boot(s), %s..%s\n"
        "evidence classes %s\n"
        "DECIDED %s: carrying a source clock %s, a receipt clock %s\n"
        "fresh at the venue clock (<= %gs) %s; at the receipt clock "
        "(<= %gs) %s\n"
        "markets with a decision fresh on BOTH clocks: %s"
        % (r["n"], r["markets"], r["boots"], r["first_at"], r["last_at"],
           json.dumps(classes), f["decided"], f["with_source"],
           f["with_receipt"], MAX_SOURCE_AGE_S, f["source_fresh"],
           MAX_RECEIPT_AGE_S, f["receipt_fresh"], f["fresh_markets"]))
    detail += ("\nrequired: >= %d decisions fresh on BOTH clocks, every "
               "decision carrying both, and >= 1 market" % MIN_FRESH_DECISIONS)

    if r["n"] == 0:
        return verdict("a live socket delivered FRESH books", UNKNOWN,
                       detail + "\nno decision records in the window at all")
    if live == 0:
        return verdict("a live socket delivered FRESH books", FAIL,
                       detail + "\nNOT ONE record is PROSPECTIVE_SHADOW: "
                       "the transport was not a live socket")
    if f["decided"] == 0:
        return verdict("a live socket delivered FRESH books", FAIL,
                       detail + "\nbooks arrived and NOTHING was decided "
                       "on them")
    ok = (f["source_fresh"] >= MIN_FRESH_DECISIONS
          and f["receipt_fresh"] >= MIN_FRESH_DECISIONS
          and f["with_source"] == f["decided"]
          and f["with_receipt"] == f["decided"]
          and f["fresh_markets"] >= 1)
    verdict("a live socket delivered FRESH books", state_of(ok), detail)


# ── 2. decisions committed ───────────────────────────────────────────

async def check_committed(con, lane, minutes, boot_ids):
    rule("2. DECISIONS COMMITTED TO POSTGRES")
    stat = await con.fetch(
        "SELECT status, count(*) AS n FROM bettor_live_journal "
        " WHERE lane = $1 AND kind = 'DECISION' "
        "   AND written_at > now() - ($2 || ' minutes')::interval "
        " GROUP BY 1", lane, str(minutes))
    by_status = {a["status"]: a["n"] for a in stat}
    acts = await con.fetch(
        "SELECT selected, count(*) AS n FROM bettor_live_journal "
        " WHERE lane = $1 AND kind = 'DECISION' AND status = 'DECIDED' "
        "   AND written_at > now() - ($2 || ' minutes')::interval "
        " GROUP BY 1 ORDER BY 2 DESC", lane, str(minutes))
    by_action = {a["selected"]: a["n"] for a in acts}
    dupes = await con.fetchval(
        "SELECT count(*) FROM (SELECT record_key FROM bettor_live_journal "
        " WHERE lane = $1 AND written_at > now() - ($2 || ' minutes')::interval"
        " GROUP BY record_key HAVING count(*) > 1) t", lane, str(minutes))
    detail = ("by status %s\nby action %s\nduplicate record keys %s"
              % (json.dumps(by_status), json.dumps(by_action), dupes))
    if not by_status:
        return verdict("decisions and refusals are in Postgres", UNKNOWN,
                       detail + "\nno rows in the window")
    verdict("decisions and refusals are in Postgres",
            state_of(sum(by_status.values()) > 0 and dupes == 0), detail)


# ── 3. settlement ACTUALLY RAN ───────────────────────────────────────

async def check_settlement(con, store, lane, minutes):
    rule("3. OUTCOME CHECKS ACTUALLY RAN")
    now = time.time()
    horizon = now - minutes * 60.0
    rep = await store.outcome_report()
    # ATTEMPTS IN THE WINDOW, not "work is waiting". `settle_last_at`
    # is stamped by every read; a scheduler that never executed leaves
    # it null however much is due.
    attempted = await con.fetchval(
        "SELECT count(*) FROM bettor_live_cursor "
        " WHERE lane = $1 AND settle_last_at IS NOT NULL "
        "   AND settle_last_at >= $2", lane, horizon)
    tracked = await con.fetchval(
        "SELECT count(*) FROM bettor_live_cursor WHERE lane = $1", lane)
    due_now = len((await store.due_for_settlement(
        now=now, limit=10 ** 6)).get("slugs") or [])
    # NEVER ATTEMPTED means always due, so it is owed work whether or
    # not anything is due "now". Asking whether a cursor was due an
    # hour ago using its CURRENT next-attempt time asks the wrong
    # question: a successful attempt is exactly what moves that time
    # forward.
    never = await con.fetchval(
        "SELECT count(*) FROM bettor_live_cursor "
        " WHERE lane = $1 AND settle_status IS NULL "
        "   AND settle_next_at IS NULL", lane)
    rows = await con.fetchval(
        "SELECT count(*) FROM bettor_live_journal "
        " WHERE lane = $1 AND kind = 'SETTLEMENT' "
        "   AND written_at > now() - ($2 || ' minutes')::interval",
        lane, str(minutes))
    detail = ("contracts tracked %s\ncursor statuses %s\n"
              "completed AUTHORITATIVELY %s, awaiting authoritative "
              "(derived only) %s, read-escalated %s, outstanding %s\n"
              "never attempted (always due) %s, due now %s\n"
              "contracts ATTEMPTED during the window %s\n"
              "settlement rows written in the window %s"
              % (tracked, json.dumps(rep.get("by_status", {})),
                 rep.get("authoritative"), rep.get("awaiting_authoritative"),
                 rep.get("read_escalated"), rep.get("outstanding"),
                 never, due_now, attempted, rows))

    if tracked == 0:
        return verdict("outcome checks ran when work was due", UNKNOWN,
                       detail + "\nno contracts tracked yet")
    if never == 0 and due_now == 0 and attempted == 0:
        # EXPLICIT, not a silent pass. Nothing was owed, so nothing
        # running proves nothing about the scheduler.
        return verdict("outcome checks ran when work was due", UNKNOWN,
                       detail + "\nNOTHING WAS DUE in this window, so this "
                       "window cannot establish that the scheduler "
                       "executes. Re-run over a window in which work "
                       "falls due, or wait for the %gs settlement pass."
                       % SETTLEMENT_EVERY_S)
    verdict("outcome checks ran when work was due",
            state_of(attempted > 0),
            detail + "\nrequired: at least one contract ATTEMPTED while "
            "work was due -- a queue with items in it is not a scheduler "
            "that ran")


# ── 4. no order in OUR OWN RECORD ────────────────────────────────────

async def check_no_order(con, lane):
    rule("4. NO EXECUTION IN OUR OWN JOURNAL")
    ex = await con.fetchval(
        "SELECT count(*) FROM bettor_live_journal "
        " WHERE lane = $1 AND (record->>'executed') = 'true'", lane)
    sized = await con.fetchval(
        "SELECT count(*) FROM bettor_live_journal WHERE lane = $1 "
        "   AND COALESCE((record->>'size_contracts')::float, 0) <> 0", lane)
    verdict("no execution recorded in our own journal",
            state_of(ex == 0 and sized == 0),
            "records claiming an execution %s (over ALL time)\n"
            "records with a non-zero size %s\n"
            "SCOPE: this reads OUR JOURNAL. It is NOT a venue-account "
            "audit and cannot be one -- it would not see an order placed "
            "by anything other than this worker." % (ex, sized))
    verdict("no order call is reachable from the worker (STRUCTURAL)",
            UNKNOWN,
            "asserted over the SOURCE, not over these rows, by\n"
            "  pytest tests/test_bettor_live_loop.py -k NoOrderCapability\n"
            "which walks the import closure and fails if execution_gate, "
            "pmus or live_executor appears in it. Run it against the "
            "deployed SHA; this script cannot.", required=False)


# ── 5. operational evidence, collected elsewhere ─────────────────────

def check_ops(evidence):
    rule("5. EXISTING WORKERS NOT MATERIALLY DEGRADED")
    if not evidence:
        print("   NOT ANSWERABLE FROM THESE TABLES. Collect it with")
        print("   render-ops and pass it in with --evidence:")
        print()
        print("     render-ops action=metrics service=sportsassets-workers arg=120")
        print("     render-ops action=events  service=sportsassets-workers")
        print("     render-ops action=logs    service=sportsassets-workers arg=30")
        print()
        print("   then a JSON file of the form:")
        print('     {"rss_mb_p95": 980, "rss_mb_baseline_p95": 940,')
        print('      "restarts_since_deploy": 1, "oom_since_deploy": 0,')
        print('      "loops_heartbeating": 19, "loops_expected": 19,')
        print('      "collected_at": "...", "source": "render-ops run 1234"}')
        return verdict("existing workers unaffected", UNKNOWN,
                       "no --evidence file was supplied")
    need = ("rss_mb_p95", "rss_mb_baseline_p95", "restarts_since_deploy",
            "oom_since_deploy", "loops_heartbeating", "loops_expected")
    missing = [k for k in need if k not in evidence]
    if missing:
        return verdict("existing workers unaffected", UNKNOWN,
                       "evidence file is missing %s" % ", ".join(missing))
    rss, base = float(evidence["rss_mb_p95"]), float(
        evidence["rss_mb_baseline_p95"])
    growth = (rss - base) / base * 100.0 if base else float("inf")
    ok = (evidence["oom_since_deploy"] == 0
          and evidence["loops_heartbeating"] >= evidence["loops_expected"]
          and growth <= RSS_GROWTH_PCT)
    verdict("existing workers unaffected", state_of(ok),
            "RSS p95 %.0f MB against a %.0f MB baseline (%+.1f%%, "
            "allowed %+.0f%%)\nOOM kills since deploy %s\n"
            "loops heartbeating %s of %s\nrestarts since deploy %s\n"
            "collected %s from %s"
            % (rss, base, growth, RSS_GROWTH_PCT,
               evidence["oom_since_deploy"], evidence["loops_heartbeating"],
               evidence["loops_expected"],
               evidence.get("restarts_since_deploy"),
               evidence.get("collected_at", "?"),
               evidence.get("source", "?")))


# ── 6. a REAL restart, compared against a checkpoint ─────────────────

async def snapshot(con, store, lane):
    """What a restart has to preserve."""
    rows = await con.fetch(
        "SELECT record_key FROM bettor_live_journal WHERE lane = $1 "
        " ORDER BY id DESC LIMIT $2", lane, CHECKPOINT_KEYS)
    boots = await con.fetch(
        "SELECT DISTINCT boot_id FROM bettor_live_journal WHERE lane = $1 "
        "   AND boot_id IS NOT NULL", lane)
    cur = await con.fetchrow(
        "SELECT count(*) AS n, "
        "       count(*) FILTER (WHERE settle_status IS DISTINCT FROM $2) "
        "         AS outstanding FROM bettor_live_cursor WHERE lane = $1",
        lane, st.SETTLE_RESOLVED)
    led = await con.fetchrow(
        "SELECT boot_id, saved_at FROM bettor_live_ledger WHERE lane = $1",
        lane)
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "journal_rows": await con.fetchval(
            "SELECT count(*) FROM bettor_live_journal WHERE lane = $1", lane),
        "record_keys": [r["record_key"] for r in rows],
        "boot_ids": sorted(b["boot_id"] for b in boots),
        "cursors": cur["n"], "outstanding": cur["outstanding"],
        "ledger_boot_id": led["boot_id"] if led else None,
        "ledger_saved_at": str(led["saved_at"]) if led else None,
    }


async def check_restart(con, store, lane, before):
    rule("6. A REAL RESTART, AND WHAT IT RETAINED")
    after = await snapshot(con, store, lane)
    if before is None:
        print("   NO CHECKPOINT. Existing rows plus a ledger do NOT")
        print("   establish a restart: a stream epoch increments on a")
        print("   RECONNECT, and a row count that rose says nothing about")
        print("   WHICH process wrote the new rows.")
        print()
        print("   Run this first, before restarting:")
        print("     python3 scripts/bettor_canary.py "
              "--checkpoint-write /tmp/bettor-checkpoint.json")
        print("     render-ops action=restart service=sportsassets-workers confirm=DO")
        print("     python3 scripts/bettor_canary.py "
              "--checkpoint /tmp/bettor-checkpoint.json")
        return verdict("a restart happened and records were retained",
                       UNKNOWN, "no --checkpoint file was supplied")

    kept = [k for k in before["record_keys"] if k in set(after["record_keys"])]
    missing = len(before["record_keys"]) - len(kept)
    if missing:
        # The checkpoint sampled the newest CHECKPOINT_KEYS keys; after
        # more collection some may have aged past that window, so a key
        # absent from the sample is checked directly.
        still = await con.fetchval(
            "SELECT count(*) FROM bettor_live_journal "
            " WHERE lane = $1 AND record_key = ANY($2::text[])",
            lane, before["record_keys"])
    else:
        still = len(before["record_keys"])

    new_boots = sorted(set(after["boot_ids"]) - set(before["boot_ids"]))
    detail = (
        "checkpoint  %s: %s rows, %s cursors (%s outstanding), boots %s\n"
        "now         %s: %s rows, %s cursors (%s outstanding), boots %s\n"
        "NEW process boot(s) since the checkpoint: %s\n"
        "checkpointed record keys still present: %s of %s\n"
        "ledger written by boot %s at %s"
        % (before["at"], before["journal_rows"], before["cursors"],
           before["outstanding"], before["boot_ids"],
           after["at"], after["journal_rows"], after["cursors"],
           after["outstanding"], after["boot_ids"],
           new_boots or "NONE",
           still, len(before["record_keys"]),
           after["ledger_boot_id"], after["ledger_saved_at"]))

    if not new_boots:
        return verdict("a restart happened and records were retained",
                       FAIL, detail + "\nNO NEW PROCESS BOOT: the worker "
                       "did not restart, so this window cannot establish "
                       "recovery")
    ok = (still == len(before["record_keys"])
          and after["journal_rows"] >= before["journal_rows"]
          and after["cursors"] >= before["cursors"])
    verdict("a restart happened and records were retained", state_of(ok),
            detail + "\nrequired: a NEW boot_id, every checkpointed record "
            "key still present, and neither the row count nor the cursor "
            "count going backwards")


# ── wiring ───────────────────────────────────────────────────────────

MAX_SOURCE_AGE_S = 10.0
MAX_RECEIPT_AGE_S = 5.0
SETTLEMENT_EVERY_S = 600.0
MIN_FRESH_DECISIONS = 10
RSS_GROWTH_PCT = 15.0
CHECKPOINT_KEYS = 200


async def run(dsn, minutes, checkpoint, checkpoint_write, evidence,
              boot_ids) -> int:
    store = st.PgStore(dsn=dsn) if dsn else st.PgStore()
    started = await store.start()
    pool = await store._get_pool()
    lane = st.LANE

    wrote_checkpoint = False
    # NOTE: `store.close()` closes the POOL, so it must never be
    # called while a connection is still checked out of it -- the
    # close waits for the release and the release waits for the close.
    async with pool.acquire() as con:
        if checkpoint_write:
            snap = await snapshot(con, store, lane)
            with open(checkpoint_write, "w") as fh:
                json.dump(snap, fh, indent=1)
            rule("CHECKPOINT WRITTEN")
            print("   %s" % checkpoint_write)
            print("   %s rows, %s cursors, boots %s"
                  % (snap["journal_rows"], snap["cursors"],
                     snap["boot_ids"]))
            print()
            print("   Now restart the worker, then re-run with")
            print("   --checkpoint %s" % checkpoint_write)
            wrote_checkpoint = True

        if not wrote_checkpoint:
            rule("BETTOR ACTIVATION CANARY  |  %s"
                 % datetime.now(timezone.utc).isoformat())
            print("   LANE        %s" % lane)
            print("   WINDOW      the last %g minutes of this lane's rows"
                  % minutes)
            print("   STORE       %s" % json.dumps(started, default=str))
            if boot_ids:
                print("   BOOTS       restricted to %s" % ", ".join(boot_ids))
            print("   SCOPE       the OBSERVATION INSTRUMENT. Not a venue-")
            print("               account audit, not a profitability result.")

            await check_live_books(con, lane, minutes, boot_ids)
            await check_committed(con, lane, minutes, boot_ids)
            await check_settlement(con, store, lane, minutes)
            await check_no_order(con, lane)
            check_ops(evidence)
            before = None
            if checkpoint:
                with open(checkpoint) as fh:
                    before = json.load(fh)
            await check_restart(con, store, lane, before)

    await store.close()
    if wrote_checkpoint:
        return EXIT_OK

    rule("VERDICT")
    req = [r for r in RESULTS if r["required"]]
    bad = [r for r in req if r["state"] == FAIL]
    unk = [r for r in req if r["state"] == UNKNOWN]
    good = [r for r in req if r["state"] == PASS]
    print("   required checks: %d PASS, %d FAIL, %d %s"
          % (len(good), len(bad), len(unk), UNKNOWN))
    for r in bad:
        print("   FAIL             %s" % r["check"])
    for r in unk:
        print("   %s  %s" % (UNKNOWN, r["check"]))
    print()
    if bad:
        print("   RESULT: FAILED (exit %d)" % EXIT_FAILED)
        code = EXIT_FAILED
    elif unk:
        print("   RESULT: ACCEPTANCE INCOMPLETE (exit %d). An unknown is"
              % EXIT_INCOMPLETE)
        print("   NOT a pass: a required check could not be answered, so")
        print("   acceptance is not established. Supply the missing")
        print("   evidence and re-run.")
        code = EXIT_INCOMPLETE
    else:
        print("   RESULT: the observation infrastructure is ACCEPTED "
              "(exit %d)." % EXIT_OK)
    print()
    print("   ACCEPTING THIS ACCEPTS THE OBSERVATION INFRASTRUCTURE AND")
    print("   NOTHING ELSE. Every decision is NO_TRADE by construction.")
    print("   It is not a profitability result and is not evidence of")
    print("   one.")
    return code if bad or unk else EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("BETTOR_TEST_PG_DSN"))
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--checkpoint", help="compare against this checkpoint")
    ap.add_argument("--checkpoint-write", help="write a checkpoint and exit")
    ap.add_argument("--evidence", help="JSON of render-ops readings")
    ap.add_argument("--boot", action="append", default=[],
                    help="restrict checks 1-2 to these process boots")
    a = ap.parse_args()
    ev = None
    if a.evidence:
        with open(a.evidence) as fh:
            ev = json.load(fh)
    return asyncio.run(run(a.dsn, a.minutes, a.checkpoint,
                           a.checkpoint_write, ev, a.boot))


if __name__ == "__main__":
    raise SystemExit(main())
