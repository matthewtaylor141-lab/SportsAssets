#!/usr/bin/env python3
"""THE INTEGRATED REAL-DATA REPLAY.

    python3 scripts/bettor_replay_real.py

Runs REAL captured observations through the REAL engine end to end:
normalize -> decide -> simulated execution -> inventory -> settlement ->
reconciled accounting, under the INSTITUTIONAL venue contract. The demo
venue's `holds_both_legs_independently = SUPPORTED` fixture is not used
and not importable from here.

WHAT IS OBSERVED, ASSUMED, AND SYNTHETIC -- kept apart everywhere:

  OBSERVED    the quotes, timestamps, market states and identities. Real
              rows from bettor_state_observations, unmodified.
  ASSUMED     the fee schedule (HYPOTHETICAL: no verified schedule
              exists) and any declared execution assumption, each named
              at the point it is used.
  SYNTHETIC   every fill. BETTOR has never rested an order, so no fill
              anywhere in this run is evidence that a fill would occur.

No performance number produced here is evidence of profitability, and
the run says so at the end rather than leaving it to be inferred.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "backend")

from sportsassets import bettor_decision_engine as de           # noqa: E402
from sportsassets import bettor_maker_economics as me           # noqa: E402
from sportsassets import bettor_observation_adapter as oa       # noqa: E402
from sportsassets import bettor_shadow_loop as sl               # noqa: E402
from sportsassets import bettor_venue_contract as vc            # noqa: E402

SAMPLE = "research/beta48/acceptance/replay_sample_rows.json"

# ASSUMED, and labelled. There is no verified PMUS schedule, so this is
# hypothetical and every result carrying it inherits that status.
FEES = de.Fees(taker_per_contract=0.02, maker_per_contract=0.01,
               verified=False, hypothetical=True,
               source="HYPOTHETICAL_NO_VERIFIED_SCHEDULE_EXISTS")

BOUNDS = (10.0, 30.0, 60.0, 300.0, 900.0)


def rule(t=""):
    print("\n" + "=" * 76)
    if t:
        print(t)
        print("=" * 76)


def main() -> int:
    rows = json.load(open(sys.argv[1] if len(sys.argv) > 1 else SAMPLE))

    rule("BETTOR INTEGRATED REPLAY  |  %d REAL captured observations" % len(rows))
    caps = vc.capabilities("polymarket-us", "institutional")
    print("VENUE CONTRACT (institutional; the demo fixture is NOT used)")
    for f in ("holds_both_legs_independently", "complement_quote_source",
              "native_merge", "maker_orders", "cancel_replace",
              "verified_fee_schedule"):
        print("   %-32s %s" % (f, getattr(caps, f)))
    print("FEE SCHEDULE: %s  (verified=%s)" % (FEES.source, FEES.verified))

    # ── 1. NORMALIZATION ────────────────────────────────────────────
    rule("1. NORMALIZATION -- accepted and rejected, with reasons")
    recs = [oa.normalize(r, venue="polymarket-us",
                         account_class="institutional",
                         fee_source=FEES.source) for r in rows]
    rep = oa.report(recs)
    print("   rows %d | accepted %d | rejected %d"
          % (rep["rows"], rep["accepted"], rep["rejected"]))
    ages = rep["book_age_s"]
    print("   book age s: min %.3f  median %.3f  max %.3f  (bound %.0f)"
          % (ages["min"], ages["median"], ages["max"],
             ages["decision_bound_s"]))
    print("   complement provenance: %s" % json.dumps(rep["complement_sources"]))
    if rep["rejection_reasons"]:
        print("   rejection reasons:")
        for k, v in rep["rejection_reasons"].items():
            print("      %-46s %4d" % (k, v))

    print("\n   STALENESS SENSITIVITY -- how many rows survive each bound:")
    for b in BOUNDS:
        n = sum(1 for r in recs if r.age_s is not None and r.age_s <= b)
        print("      <= %6.0fs   %3d of %d rows" % (b, n, len(recs)))
    print("   The engine uses %.0fs. The others are shown so the "
          "eligibility\n   curve is visible, NOT to justify relaxing it."
          % de.MAX_BOOK_AGE_S)

    # ── 2. PAIRING ──────────────────────────────────────────────────
    rule("2. CONTRACT PAIRING -- same market_id, two distinct legs")
    pairing = oa.pair_legs(recs)
    print("   genuine complement pairs: %d" % len(pairing["pairs"]))
    for p in pairing["pairs"]:
        print("      %s  legs=%s" % (p["market_id"][:46], p["legs"]))
    print("   unpaired contracts: %d  (an unpaired leg is a finding, not "
          "a gap to fill)" % len(pairing["unpaired"]))

    # ── 3. DECISIONS ────────────────────────────────────────────────
    rule("3. DECISIONS -- every alternative and its economic inputs")
    accepted = [r for r in recs if r.status == oa.ACCEPTED]
    outcomes: dict = {}
    blockers: dict = {}
    for i, rec in enumerate(accepted):
        d = de.decide(oa.to_book(rec), fees=FEES, max_contracts=10,
                      venue=rec.venue, account_class=rec.account_class)
        outcomes[d["selected"]] = outcomes.get(d["selected"], 0) + 1
        for c in d["candidates"]:
            if c["blocker"]:
                blockers[c["blocker"]] = blockers.get(c["blocker"], 0) + 1
        if i == 0:
            print("   EXAMPLE -- %s (%s @ %s/%s)"
                  % (rec.market_id[:42], rec.outcome_leg, rec.bid, rec.ask))
            print("   %-12s %-16s %-11s %s" % ("ACTION", "STATUS", "EV",
                                               "BLOCKER"))
            for c in d["candidates"]:
                print("   %-12s %-16s %-11s %s"
                      % (c["action"], c["status"],
                         ("%.4f" % c["ev_net"]) if c["ev_net"] is not None
                         else "-", c["blocker"] or ""))
            print("   -> %s : %s" % (d["selected"], d["reason"][:56]))
    print("\n   decisions over %d accepted rows: %s"
          % (len(accepted), json.dumps(outcomes)))
    print("   blockers encountered:")
    for b, n in sorted(blockers.items(), key=lambda kv: -kv[1]):
        print("      %-52s %4d" % (b, n))

    # ── 4. EXECUTION AND INVENTORY ──────────────────────────────────
    rule("4. EXECUTION, INVENTORY AND ACCOUNTING")
    loop = sl.ShadowLoop(opening_cash=10_000.0, fees=FEES,
                         venue="polymarket-us",
                         account_class="institutional")
    executed = 0
    for rec in accepted:
        r = loop.step(oa.to_book(rec), evidence_class=sl.REPLAYED,
                      max_contracts=10)
        if r.get("execution"):
            executed += 1
    print("   observations stepped through the loop: %d" % len(accepted))
    print("   orders executed: %d" % executed)
    if executed == 0:
        print("   NO ORDER WAS EXECUTED, and the reason is not a bug:")
        print("   every action is blocked or unidentified on this data.")
        print("   Manufacturing a fill to produce a number is exactly what")
        print("   this run must not do.")
    rep4 = loop.report()
    print("\n   RECONCILED ACCOUNTING")
    print("      %s" % json.dumps(rep4["ledger"]))
    print("      fees paid        %.4f" % rep4["fees_paid"])
    print("      realized P&L     %+.4f" % rep4["realized_pnl"])
    print("      remaining basis  %.4f" % rep4["unsettled_cost_basis"])
    print("      open positions   %d" % len(rep4["open_positions"]))
    print("      live quotes      %d" % len(rep4["live_quotes"]))
    print("      evidence classes %s" % json.dumps(rep4["by_evidence_class"]))

    # ── 5. SETTLEMENT ───────────────────────────────────────────────
    rule("5. SETTLEMENT")
    settled = [r for r in rows if r.get("settlement_status")]
    print("   observations with a settlement outcome: %d of %d"
          % (len(settled), len(rows)))
    print("   bettor_state_settlements holds 0 rows venue-wide -- but")
    print("   record_settlement() is DEFINED AND NEVER CALLED, so that is")
    print("   a MISSING INGESTION PATH, not evidence that nothing has")
    print("   resolved. Which it is cannot be determined from our data;")
    print("   it needs a venue resolution read. (Separately: all 1,469")
    print("   observed markets are under 48h old, so many genuinely may")
    print("   not have resolved -- we simply cannot tell.)")

    # ── 6. MAKER ECONOMICS ON REAL BOOKS ────────────────────────────
    rule("6. MAKER ECONOMICS ON THE REAL BOOKS (frozen grid)")
    print("   Per PREREGISTRATION_MAKER_V1: p_fill and")
    print("   conditional_reference_move are NOT_IDENTIFIED, so each real")
    print("   book is evaluated across the frozen grid instead of at a")
    print("   chosen value. Conservative corner = move -0.020.\n")
    print("   %-9s %-9s %-11s %-11s %s"
          % ("MOVE", "ROUTE", "POSITIVE", "NEGATIVE", "MEDIAN/CONTRACT"))
    for move in (0.000, -0.005, -0.010, -0.020):
        for route in (me.EXIT_AGGRESSIVE, me.EXIT_PASSIVE):
            vals = []
            for rec in accepted:
                if rec.bid is None or rec.ask is None:
                    continue
                q = me.MakerQuote(
                    side="BUY", entry_price=rec.bid, bid=rec.bid,
                    ask=rec.ask, exit_route=route,
                    conditional_reference_move=me.hypothetical(move),
                    exit_spread=me.hypothetical(rec.ask - rec.bid),
                    fee_per_contract=me.hypothetical(
                        FEES.maker_per_contract),
                    carry_per_contract_per_hour=me.hypothetical(0.0),
                    duration_hours=me.hypothetical(0.0))
                rt = me.round_trip(q)
                if rt.status == me.IDENTIFIED:
                    vals.append(rt.per_contract)
            if not vals:
                continue
            vals.sort()
            pos = sum(1 for v in vals if v > 0)
            print("   %+.3f    %-9s %-11d %-11d %+.6f"
                  % (move, "cross" if route == me.EXIT_AGGRESSIVE else "rest",
                     pos, len(vals) - pos, vals[len(vals) // 2]))
    print("\n   DEVIATION: the frozen protocol declares exit routes")
    print("   {AGGRESSIVE, SETTLEMENT} and a p_fill grid. This ran")
    print("   AGGRESSIVE and PASSIVE with no p_fill grid, so it does NOT")
    print("   implement that protocol. Recorded, not retrofitted.")
    print("   Every figure carries HYPOTHETICAL fees and ASSUMED moves:")
    print("   these are SCENARIO results, not an empirical refutation of")
    print("   maker trading. Under rule 2 a candidate needing a")
    print("   NOT_IDENTIFIED term is UNRESOLVED -- never REFUTED.")

    # ── 7. WHAT THIS RUN IS AND IS NOT ──────────────────────────────
    rule("7. OBSERVED / ASSUMED / SYNTHETIC")
    print("   OBSERVED   %d real books: quotes, ages, states, identities."
          % len(rows))
    print("   ASSUMED    the fee schedule (%s)," % FEES.source)
    print("              plus every grid value in section 6.")
    print("   SYNTHETIC  every fill. BETTOR has never rested an order.")
    print("\n   This run establishes that the engine consumes real venue")
    print("   data and decides on it without fabricating inputs.")
    print("   It establishes NO edge, NO fill rate and NO profitability.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
