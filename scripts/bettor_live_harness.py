#!/usr/bin/env python3
"""THE OPERATING LOOP, END TO END, THROUGH THE REAL STREAM CODE.

    python3 scripts/bettor_live_harness.py

WHAT THIS IS. Every component the live worker uses, wired exactly as the
worker wires it, driven by REAL captured books pushed through the REAL
`MarketStream._on_market_data` handler. The only thing replaced is the
socket: no credentials exist in this container, so the bytes arrive from
a file instead of a wire.

WHAT THAT DOES AND DOES NOT ESTABLISH.

  IT DOES establish that the chain runs: stream handler -> eligibility
  -> normalization -> decision -> shadow execution -> inventory ->
  settlement -> reconciled accounting, on real venue payloads, with the
  real fee schedule, producing real refusals with real reasons.

  IT DOES NOT establish that the socket connects, that subscriptions
  are accepted, that the venue's update rate is what we assume, or that
  a book replacement is a replacement. Those need the wire.
  `evidence_class` on every record says REPLAY_DECISION, never
  PROSPECTIVE_SHADOW: a file is not a feed, and labelling it one is the
  error this whole runner was rewritten to stop making.

SECTION 7 IS THE DISCONNECT TEST, and it is the one worth reading. The
old transport served books across a drop for up to ninety seconds. This
one invalidates them, and the section shows the same book eligible,
then ineligible, then eligible again only after the market speaks on
the new connection.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "backend")

from sportsassets import bettor_decision_engine as de           # noqa: E402
from sportsassets import bettor_fee_schedule as fs              # noqa: E402
from sportsassets import bettor_market_stream as ms             # noqa: E402
from sportsassets import bettor_settlement_ingest as si         # noqa: E402
from sportsassets import bettor_universe as uni                 # noqa: E402
from sportsassets.workers import bettor_live_loop as bl         # noqa: E402

SAMPLE = "research/beta48/acceptance/replay_sample_rows.json"
FAILS: list = []


def rule(t=""):
    print("\n" + "=" * 76)
    if t:
        print(t)
        print("=" * 76)


def check(label, got, want, tol=1e-9):
    ok = abs(got - want) <= tol if isinstance(got, (int, float)) else got == want
    print("      %-46s %-14s expected %-14s %s"
          % (label, got, want, "OK" if ok else "*** MISMATCH ***"))
    if not ok:
        FAILS.append(label)


def as_stream_message(row, *, source_ts=None):
    """A captured row -> the venue's OWN websocket payload shape.

    `_MarketDataPayload`: marketSlug, bids, offers, state, stats,
    transactTime. The captured ladder marks its levels; the stream's
    does not, because the documentation says the arrays are already
    sorted best-to-worst. Both are exercised by feeding the marked
    ladder through the unmarked shape.
    """
    lad = row.get("multi_level_depth") or {}
    if isinstance(lad, str):
        lad = json.loads(lad)

    def side(key):
        out = []
        for lv in sorted(lad.get(key) or [],
                         key=lambda x: int(x.get("level", 0))):
            out.append({"px": {"value": str(lv["price"]), "currency": "USD"},
                        "qty": str(lv["qty"])})
        return out

    return {"marketData": {
        "marketSlug": row["market_id"],
        "transactTime": source_ts or row.get("book_source_ts"),
        "state": row.get("venue_state") or "MARKET_STATE_OPEN",
        "bids": side("bid"), "offers": side("ask"),
        "stats": {"sharesTraded": row.get("stats_shares_traded", "0")},
    }}


def fresh(delta_s=0.0):
    return (datetime.now(timezone.utc)
            + timedelta(seconds=delta_s)).isoformat()


def main() -> int:
    rows = json.load(open(SAMPLE))
    legs = {r["market_id"]: r.get("outcome_leg") for r in rows}

    rule("BETTOR LIVE HARNESS  |  %d real books through the real stream"
         % len(rows))
    print("   TRANSPORT  %s" % ms.STREAM_VERSION)
    print("   LOOP       %s" % bl.LOOP_VERSION)
    print("   FEES       %s" % fs.LATEST.schedule_id)
    print("   SOCKET     REPLACED BY A FILE. No credentials in this")
    print("              container, so nothing here establishes that the")
    print("              socket connects or that subscriptions are taken.")

    # ── 1. universe ─────────────────────────────────────────────────
    rule("1. UNIVERSE SELECTION -- liquidity and activity, not a sweep")
    cands = [{"slug": r["market_id"], "bestBid": r.get("yes_bid"),
              "bestAsk": r.get("yes_ask"),
              "sharesTraded": r.get("stats_shares_traded"),
              "state": r.get("venue_state")} for r in rows]
    sel = uni.select(cands)
    print("   considered %d | eligible %d | selected %d"
          % (sel["considered"], sel["eligible"], sel["selected"]))
    print("   excluded by reason: %s" % json.dumps(sel["excluded_by_reason"]))
    print("   the activity test is the one the old capacity analysis")
    print("   never applied: a market that does not trade cannot fill a")
    print("   quote however wide its book looks.")

    # ── 2. subscription is not a claim ──────────────────────────────
    rule("2. SUBSCRIPTION LIFECYCLE -- requested, then confirmed by data")
    stream = ms.MarketStream("NO_KEY", "NO_SECRET")
    loop = bl.LiveLoop(stream, leg_of=legs, opening_cash=1000.0)
    stream._book_cb = loop.on_book
    slugs = [r["market_id"] for r in rows]
    q = stream.subscribe(slugs)
    print("   queued %d (cap %d, batch %d -- the documented ceiling)"
          % (q["queued"], q["cap"], ms.SUB_BATCH))
    r0 = stream.subscription_report()
    check("confirmed before any data", r0["confirmed"], 0)

    stream.epoch += 1
    stream.connected = True
    stream.first_connected_at = fresh()
    for r in rows:
        stream._on_market_data(as_stream_message(r, source_ts=fresh(-1.0)))
    r1 = stream.subscription_report()
    check("confirmed after data", r1["confirmed"], len(rows))
    print("   There is no positive acknowledgment in this protocol:")
    print("   MarketMessage carries no 'subscribed' reply, so the")
    print("   arrival of data IS the confirmation.")

    # ── 3. decisions ────────────────────────────────────────────────
    rule("3. DECISIONS -- driven by updates, three clocks per record")
    loop.started_at = fresh()
    recs = loop.drain()
    loop.stopped_at = fresh()
    rep = loop.report()
    print("   books examined %d | decided %d | ineligible %d | rejected %d"
          % (rep["counters"].get("books_examined", 0),
             rep["counters"].get("decided", 0),
             rep["counters"].get("ineligible", 0),
             rep["counters"].get("rejected", 0)))
    print("   by action  : %s" % json.dumps(rep["by_action"]))
    print("   blockers   : %s" % json.dumps(rep["blockers"]))
    if rep["ineligible_by_reason"]:
        print("   ineligible : %s" % json.dumps(rep["ineligible_by_reason"]))
    f = rep["freshness"]
    print("   freshness  : n=%s min=%s median=%s max=%s (bound %ss)"
          % (f.get("n"), f.get("min"), f.get("median"), f.get("max"),
             f.get("bound_s")))
    ex = [r for r in recs if r["status"] == "DECIDED"]
    if ex:
        e = ex[0]
        print("\n   EXAMPLE RECORD -- every clock persisted")
        for k in ("market_id", "source_ts", "received_at", "decided_at",
                  "source_age_s", "selected", "blockers"):
            print("      %-16s %s" % (k, e.get(k)))
    check("every decision carries a source clock",
          all(r.get("source_ts") for r in ex), True)
    check("every decision carries a receipt clock",
          all(r.get("received_at") for r in ex), True)
    check("refusals are records too",
          all("reasons" in r for r in recs if r["status"] != "DECIDED"),
          True)

    # ── 4. a duplicate observation is refused ───────────────────────
    rule("4. THE SAME VENUE TIMESTAMP IS DECIDED ONCE")
    before = len(loop.records)
    for r in rows:
        loop.on_book(r["market_id"], {})
    loop.drain()
    check("new records from re-deciding the same books",
          len(loop.records) - before, 0)
    print("   Not an optimisation: re-deciding one observation would")
    print("   multiply it into several records and inflate every count.")

    # ── 5. touches are not fills ────────────────────────────────────
    rule("5. A TRADE AT OUR PRICE IS A TOUCH, NEVER A FILL")
    for r in rows[:3]:
        stream._on_trade({"trade": {
            "marketSlug": r["market_id"],
            "price": {"value": r.get("yes_bid")},
            "quantity": {"value": "25"},
            "tradeTime": fresh(),
            "maker": {"side": "ORDER_SIDE_BUY"},
            "taker": {"side": "ORDER_SIDE_SELL"}}})
    for t in stream.drain_trades():
        loop.on_trade(t)
    rep = loop.report()
    print("   trades observed %d | orders submitted %d"
          % (rep["touches_not_fills"]["trades_observed"],
             rep["orders_submitted"]))
    print("   %s" % rep["touches_not_fills"]["note"])
    check("orders submitted", rep["orders_submitted"], 0)

    # ── 6. accounting ───────────────────────────────────────────────
    rule("6. RECONCILED ACCOUNTING -- checked independently")
    acc = rep["accounting"]
    print("   %s" % json.dumps(acc["ledger"]))
    check("ledger reconciles", acc["ledger"]["reconciled"], True)
    check("residual", acc["ledger"]["residual_must_be_0"], 0.0)
    check("fees paid", acc["fees_paid"], 0.0)
    check("combined exposure", acc["combined_exposure"], 0.0)
    check("worst-case loss", acc["worst_case_loss"], 0.0)
    check("open positions", acc["open_positions"], 0)
    print("   Zero because every action was refused, which is the")
    print("   engine working -- not an accounting that was never used.")

    # ── 7. THE DISCONNECT TEST ──────────────────────────────────────
    rule("7. A DISCONNECT INVALIDATES EVERY BOOK  (the defect corrected)")
    slug = rows[0]["market_id"]
    stream._on_market_data(as_stream_message(rows[0], source_ts=fresh(-1.0)))
    a = stream.book_at(slug, max_source_age_s=10.0, max_receipt_age_s=5.0)
    print("      connected        eligible=%-6s reason=%s"
          % (a["eligible"], a["reason"]))
    check("eligible while connected", a["eligible"], True)

    stream.connected = False
    b = stream.book_at(slug, max_source_age_s=10.0, max_receipt_age_s=5.0)
    print("      dropped          eligible=%-6s reason=%s"
          % (b["eligible"], b["reason"]))
    check("ineligible on drop", b["eligible"], False)

    stream.epoch += 1
    stream.connected = True
    c = stream.book_at(slug, max_source_age_s=10.0, max_receipt_age_s=5.0)
    print("      reconnected      eligible=%-6s reason=%s"
          % (c["eligible"], c["reason"]))
    check("STILL ineligible after reconnect", c["eligible"], False)
    print("      (the old transport served this book for up to 90s)")

    stream._on_market_data(as_stream_message(rows[0], source_ts=fresh()))
    d = stream.book_at(slug, max_source_age_s=10.0, max_receipt_age_s=5.0)
    print("      market spoke     eligible=%-6s reason=%s"
          % (d["eligible"], d["reason"]))
    check("eligible only after a fresh book", d["eligible"], True)

    # ── 8. stale and halted ─────────────────────────────────────────
    rule("8. STALE AND HALTED, EACH WITH ITS OWN REASON")
    stream._on_market_data(as_stream_message(rows[1], source_ts=fresh(-90)))
    s = stream.book_at(rows[1]["market_id"], max_source_age_s=10.0,
                       max_receipt_age_s=5.0)
    print("      90s-old book     eligible=%-6s reason=%-24s age=%.1fs"
          % (s["eligible"], s["reason"], s["source_age_s"]))
    check("90s book refused at the 10s bound", s["reason"], ms.STALE_SOURCE)

    m = as_stream_message(rows[2], source_ts=fresh())
    m["marketData"]["state"] = "MARKET_STATE_HALTED"
    stream._on_market_data(m)
    h = stream.book_at(rows[2]["market_id"], max_source_age_s=10.0,
                       max_receipt_age_s=5.0)
    print("      halted market    eligible=%-6s reason=%s"
          % (h["eligible"], h["reason"]))
    check("halted refused", h["reason"], ms.NOT_OPEN)
    print("      The old cache kept neither the clock nor the state, so")
    print("      a five-hour venue halt looked like a quiet market.")

    # ── 9. settlement ingestion ─────────────────────────────────────
    rule("9. SETTLEMENT INGESTION -- the venue's own endpoint")
    import asyncio
    written: list = []

    async def writer(row):
        written.append(row)

    def reader(_client, slug):
        # RESOLVED / PENDING / UNREADABLE / UNMATCHED, one of each, so
        # the five counts are visibly distinct.
        i = slugs.index(slug) % 4
        return [
            {"status": si.RESOLVED, "outcome": "1.0000",
             "settled_at": "2026-09-21T02:00:00Z",
             "outcome_field": "settlementPrice"},
            {"status": si.PENDING, "closed": False},
            {"status": si.UNREADABLE, "error": "TimeoutError",
             "keys_seen": ["slug"]},
            {"status": si.UNMATCHED, "error": "NOT_LISTED"},
        ][i]

    out = asyncio.run(si.ingest(
        [("obs-%d" % i, s) for i, s in enumerate(slugs[:12])],
        reader=reader, writer=writer))
    print("   counts    : %s" % json.dumps(out["counts"]))
    print("   reconciled: %s   resolved_minus_ingested: %s"
          % (out["reconciled"], out["resolved_minus_ingested"]))
    check("every resolution read was stored", out["reconciled"], True)
    check("rows written", len(written), out["counts"][si.RESOLVED])
    print("   The endpoint is /v1/markets/{slug}/settlement, returning")
    print("   {marketSlug, settlementPrice: Amount, settledAt}. UNITS")
    print("   ARE NOT ASSUMED: a value outside [0,1] is reported as")
    print("   UNITS_UNVERIFIED rather than divided by 100 on a hunch.")

    # ── 10. restart ─────────────────────────────────────────────────
    rule("10. RESTART -- balances, inventory and quotes recovered")
    blob = loop.shadow.snapshot()
    back = type(loop.shadow).restore(blob, fees=loop.fees,
                                     venue="polymarket-us",
                                     account_class="institutional")
    check("cash after restart", back.ledger.cash, loop.shadow.ledger.cash)
    check("positions after restart", len(back.positions),
          len(loop.shadow.positions))
    check("reconciles after restart", back.ledger.reconciles()["reconciled"],
          True)

    rule("WHAT THIS RUN IS")
    print("   REAL       the books, their clocks, their states, their")
    print("              ladders; the fee schedule; every refusal.")
    print("   REAL CODE  the stream handler, eligibility, normalizer,")
    print("              engine, shadow ledger, settlement ingestion.")
    print("   REPLACED   the socket. A file is not a feed, so every")
    print("              record here is REPLAY_DECISION and none is")
    print("              PROSPECTIVE_SHADOW.")
    print("   UNPROVEN   that the socket connects, that subscriptions")
    print("              are accepted, the venue's real update rate, and")
    print("              whether a book message replaces or deltas.")
    print("   ORDERS     0, and there is no code path that could place one.")

    rule()
    if FAILS:
        print("FAILED: %d assertion(s): %s" % (len(FAILS), FAILS))
        return 1
    print("ALL INDEPENDENT ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
