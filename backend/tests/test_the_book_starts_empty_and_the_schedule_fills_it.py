"""THE SCHEDULED CALLER, FROM AN EMPTY BOOK, THROUGH ENTRY, MANAGEMENT AND EXIT.

WHAT THIS PROVES. `ext_pinnacle_loop.cycle()` -- the function the worker runs on
its schedule -- starts with no funded position, finds a candidate, establishes
its identity and settlement terms, prices it, admits it, fits it to the
owner-approved limits, sends the order, ingests the venue's fills as inventory,
manages that inventory on later cycles (holding while the market is below fair
value, exiting when it moves above), survives a restart without a duplicate
order, and ends with the book and the venue agreeing to the contract and the
cent.

WHAT RUNS FOR REAL. The mapping (`premap.resolve`, on catalogue rows written by
the premap worker's own writer), the de-vig, the venue book read
(`venue_quote`), the settlement prose read and its condition-to-payout
comparison, the fixture scope acquisition (from the league's own schedule
payload), the execution estimate, sizing, the risk engine, the entry gate, the
funded connector, the rails, the account-wide exposure measurement, the
execution gate, the adapter, fill ingestion, the fee ledger, `manage`, the pair
pass, exit selection and dispatch, and recovery.

WHAT IS SUBSTITUTED, AND WHAT IS SUPPLIED -- see `_emptybook_fixture`. In short:
the odds provider, the league schedule and the venue, at their transport
boundaries; and, NAMED AS ASSUMPTIONS THIS PROOF RUNS UNDER, a book-currency
mechanism the venue does not document today (P5/P6), a calibration measurement
production does not have, and owner activation records production does not
have. Nothing here is evidence that production can do this today; research-sql
run 271 read production and found all three absent.

DEFECTS THIS RUN FOUND AND THAT ARE FIXED ALONGSIDE IT -- each would have kept
the lane from ever trading, and none was visible to a test that stubbed the
stage it lives in:

  1. `bettor_venue_currency.evaluate` did not carry its inputs, and
     `_entry_freshness` re-evaluates currency at the decision instant from them,
     so STALE_DATA refused every entry even when the read had ESTABLISHED
     currency.
  2. The funded connector took the SHADOW lane's size (a $1,000 cohort)
     verbatim, so any pilot-scale approval refused every entry on the per-order
     rail. It now fits the count to the approved rails with `check_rails`.
  3. Neither scheduled caller supplied the venue's account read, so the
     execution gate refused every entry and every hedge with
     ACCOUNT_WIDE_EXPOSURE_COULD_NOT_BE_MEASURED.
  4. `manage` read the settlement verdict from `settlement_rule`, which the
     entry lane fills with the bookmaker rule's NAME; the verdict the entry was
     admitted on is `settlement_comparison`. Every funded exit on an admitted
     position was refused THE_SETTLEMENT_RULE_IS_NOT_ESTABLISHED.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from sportsassets import bettor_fee_schedule as FEES
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_venue_currency as VC
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import _emptybook_fixture as F

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")


async def _connect():
    import asyncpg

    return await asyncpg.connect(DSN)


async def _book(conn):
    return [dict(r) for r in await conn.fetch(
        "SELECT intent_id, kind, state, us_market_slug, quantity, "
        "       limit_price::float8 AS lp, residual_qty::float8 AS rq, "
        "       venue_order_id "
        "  FROM bettor_funded_intents WHERE account_id=$1 "
        " ORDER BY created_at", F.ACCT)]


async def _economics(conn):
    """Every economic event on this account's book. An exit's proceeds and
    fees are recorded against the ENTRY it consumes, so the ledger is read by
    account and split by kind rather than by intent."""
    return [dict(r) for r in await conn.fetch(
        "SELECT e.kind, e.amount_usd::float8 AS a, e.qty::float8 AS q, "
        "       e.provisional, i.kind AS intent_kind "
        "  FROM bettor_funded_economics e "
        "  JOIN bettor_funded_intents i USING (intent_id) "
        " WHERE i.account_id=$1 ORDER BY e.at, e.event_id", F.ACCT)]


def _order_fee(qty, price) -> float:
    """The published schedule's fee for ONE ORDER of this size -- which is what
    the venue charges, capping each fill so the order's total never exceeds
    the banker's rounding of the cumulative exact fee."""
    return float(FEES.LATEST.fill_fee(Decimal(qty), Decimal(str(price)),
                                      maker=False))


def _steps(out):
    fs = out.get("funded_servicing") or {}
    return list(((fs.get("pair_cycle") or {}).get("considered")) or [])


# ═════════════════════════════════════════════════════════════════════
# 1 · THE WHOLE LIFECYCLE
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_schedule_enters_manages_exits_and_reconciles_from_empty(
        monkeypatch):
    conn = await _connect()
    venue = F.Venue(split=[50, 35])
    try:
        await F.clean(conn)
        await F.seed(conn)
        F.substitute(monkeypatch, venue)

        # ── THE BOOK IS EMPTY ─────────────────────────────────────────
        assert await _book(conn) == []

        # ── CYCLE 1: DISCOVER, ADMIT, FIT, SEND, FILL ─────────────────
        c1 = await loop.cycle(conn)
        assert c1["ran"] is True, c1.get("why")
        assert c1["evaluated"] == 1
        assert c1["refusals"].get("ADMITTED") == 1, c1["refusals"]
        assert c1["refusals"].get("FUNDED:SUBMITTED") == 1, c1["refusals"]
        # THE FUNNEL RECONCILES OVER THE SAME POPULATION: one provider event,
        # one mapped contract, one evaluation, one admission.
        fn = c1["funnel_by_provider_sport"]["baseball_mlb"]
        assert (fn["provider_events"], fn["mapped_to_a_venue_contract"],
                fn["evaluated"]) == (1, 1, 1)
        ledger = c1["mapped_candidate_ledger"]
        assert [(e["us_market_slug"], e["stage"]) for e in ledger] == [
            (F.US_SLUG, "ADMITTED")]

        # ONE ORDER, THE ONE THE DECISION DESCRIBED, FITTED TO THE LIMITS.
        creates = venue.creates_sent()
        assert len(creates) == 1, creates
        buy = creates[0]
        assert buy["marketSlug"] == F.US_SLUG
        assert buy["intent"] == "ORDER_INTENT_BUY_LONG"
        assert buy["tif"] == FX.TIF
        qty = int(buy["quantity"])
        limit = float(buy["price"]["value"])
        # The shadow decision sized 900; the approved $60 per-market and
        # per-event rails bind at the LIMIT price, so the fit is the largest
        # count whose collateral stays inside $60.
        assert qty == int(60.0 // limit), (qty, limit)
        assert qty * limit <= 60.0 < (qty + 1) * limit

        # THE FILLS ARE THE INVENTORY.
        book = await _book(conn)
        assert len(book) == 1
        entry = book[0]
        assert (entry["kind"], entry["state"]) == ("ENTRY", "FILLED")
        assert entry["rq"] == pytest.approx(qty)
        fills = await conn.fetch(
            "SELECT venue_fill_id, qty::float8 AS q, price::float8 AS p "
            "  FROM bettor_funded_fills WHERE intent_id=$1 ORDER BY 1",
            entry["intent_id"])
        assert [f["q"] for f in fills] == [50.0, float(qty - 50)]
        fill_px = F.OFFERS[0][0]              # matched at the best level
        assert all(f["p"] == pytest.approx(fill_px) for f in fills)
        eco = await _economics(conn)
        cost = sum(e["a"] for e in eco if e["kind"] == "ENTRY_COST")
        fee_in = -sum(e["a"] for e in eco if e["kind"] == "FEE")
        assert cost == pytest.approx(-qty * fill_px)
        # The venue caps an order's fees at the order's own rounded fee.
        assert fee_in == pytest.approx(_order_fee(qty, fill_px))

        # ── CYCLE 2: THE SAME POSITION IS MANAGED, AND HELD ───────────
        c2 = await loop.cycle(conn)
        steps = _steps(c2)
        assert [s["intent_id"] for s in steps] == [entry["intent_id"]]
        assert steps[0]["decision"]["action"] == "HOLD", steps[0]["decision"]
        assert len(venue.creates_sent()) == 1, "HOLD sends nothing"

        # ── THE MARKET MOVES ABOVE FAIR VALUE ─────────────────────────
        venue.bids = [(0.93, 400), (0.91, 300)]
        c3 = await loop.cycle(conn)
        steps = _steps(c3)
        assert steps[0]["decision"]["action"] == "EXIT", steps[0]["decision"]
        assert steps[0]["dispatched"] == "EXIT"
        sell = venue.creates_sent()[-1]
        assert sell["intent"] == "ORDER_INTENT_SELL_LONG"
        assert int(sell["quantity"]) == qty
        book = await _book(conn)
        assert [b["kind"] for b in book] == ["ENTRY", "EXIT"]
        assert book[0]["rq"] == pytest.approx(0.0), "the exit closed it"
        eco = await _economics(conn)
        proceeds = sum(e["a"] for e in eco if e["kind"] == "EXIT_PROCEEDS")
        fee_out = -sum(e["a"] for e in eco if e["kind"] == "FEE") - fee_in
        assert proceeds == pytest.approx(qty * 0.93)
        assert fee_out == pytest.approx(_order_fee(qty, 0.93))

        # ── A RESTART, ON A FRESH CONNECTION ──────────────────────────
        await conn.close()
        conn = await _connect()
        sent_before = len(venue.creates_sent())
        c4 = await loop.cycle(conn)
        assert len(venue.creates_sent()) == sent_before, (
            "a restart must not send anything the first process sent")
        rec = (c4.get("funded_servicing") or {}).get("recovered") or {}
        assert rec.get("ok") is True
        assert rec.get("resubmitted_anything") is False
        assert rec.get("unresolved") == [] and rec.get("orphans") == []
        assert await _book(conn) == book

        # ── THE LEDGER AND THE VENUE AGREE ────────────────────────────
        read = (c4.get("funded_servicing") or {}).get("venue_account_read")
        assert read["ok"] is True
        assert read["held_usd"] == pytest.approx(0.0)
        assert read["working_usd"] == pytest.approx(0.0)
        assert all(b["rq"] == pytest.approx(0.0) for b in book)
        disc = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_discrepancies WHERE intent_id "
            "IN (SELECT intent_id FROM bettor_funded_intents "
            "     WHERE account_id=$1)", F.ACCT)
        assert disc == 0
        net = proceeds + cost - fee_in - fee_out
        assert net == pytest.approx(qty * (0.93 - fill_px)
                                    - _order_fee(qty, fill_px)
                                    - _order_fee(qty, 0.93))
    finally:
        await F.clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · NO QUALIFYING CANDIDATE, NO ORDER -- AND THE REFUSAL IS KEPT
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_candidate_without_an_edge_is_refused_and_recorded(monkeypatch):
    """Priced so the fair value sits BELOW the venue's ask: the only honest
    outcome is no trade. The candidate's identity and its specific refusal are
    persisted on the valuation row, and nothing reaches the venue."""
    conn = await _connect()
    venue = F.Venue()
    try:
        await F.clean(conn)
        await F.seed(conn)
        F.substitute(monkeypatch, venue,
                     odds=lambda now: F.odds_event(now, home_price=1.80,
                                                   away_price=2.10))
        out = await loop.cycle(conn)
        assert out["evaluated"] == 1
        assert "ADMITTED" not in out["refusals"], out["refusals"]
        assert venue.creates_sent() == []
        assert await _book(conn) == []
        row = await conn.fetchrow(
            "SELECT us_market_slug, decision, admissible, refusals "
            "  FROM external_valuations WHERE condition_id=$1 "
            " ORDER BY observed_at DESC LIMIT 1", F.CONDITION)
        assert row["us_market_slug"] == F.US_SLUG
        assert row["admissible"] is False
        assert row["refusals"], "the refusal is named, not just absent"
        ledger = out["mapped_candidate_ledger"]
        assert ledger and ledger[0]["first_refusal"] in row["refusals"]
    finally:
        await F.clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · A FILL-OR-KILL THE VENUE CANNOT FILL LEAVES NO INVENTORY
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_killed_order_leaves_no_inventory_and_no_live_claim(
        monkeypatch):
    """The book SHOWS enough depth; the venue MATCHES less. A fill-or-kill
    cannot be partially filled, so the venue cancels it: no fill, no residual,
    and nothing left counting as live exposure."""
    conn = await _connect()
    venue = F.Venue(depth_cap=10)
    try:
        await F.clean(conn)
        await F.seed(conn)
        F.substitute(monkeypatch, venue)
        out = await loop.cycle(conn)
        assert out["refusals"].get("ADMITTED") == 1
        assert len(venue.creates_sent()) == 1
        book = await _book(conn)
        assert len(book) == 1
        assert book[0]["rq"] == pytest.approx(0.0)
        n = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_fills WHERE intent_id=$1",
            book[0]["intent_id"])
        assert n == 0
        # NOT RELEASED ON ITS OWN SAY-SO. The create answered with no fills;
        # the claim stays outstanding until the venue's own record of the
        # order confirms it is terminal -- absence is not evidence.
        live = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1 "
            "   AND bettor_funded_order_is_outstanding(state)", F.ACCT)
        assert live == 1, book
        # THE NEXT CYCLE ASKS THE VENUE, which confirms CANCELED with nothing
        # filled, and only then is the claim closed.
        await loop.cycle(conn)
        assert ("orders.retrieve", book[0]["venue_order_id"]) in venue.sent
        live = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1 "
            "   AND bettor_funded_order_is_outstanding(state)", F.ACCT)
        assert live == 0, await _book(conn)
        assert (await _book(conn))[0]["rq"] == pytest.approx(0.0)
    finally:
        await F.clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · THE FOUR REPAIRS, PINNED ON THEIR OWN  (pure where they can be)
# ═════════════════════════════════════════════════════════════════════

def test_the_currency_verdict_carries_the_evidence_it_was_computed_from():
    now = 1_000_000.0
    sub = {"alive_at": now - 0.5, "last_update_at": now - 1.0}
    got = VC.evaluate(now=now, subscription=sub, bound_s=30.0)
    assert got["verdict"] == VC.ESTABLISHED
    assert got["subscription_input"] == sub
    # RE-AGED LATER FROM THE CARRIED INPUT: still inside, then outside.
    again = VC.evaluate(now=now + 5.0, subscription=got["subscription_input"],
                        bound_s=30.0)
    assert again["verdict"] == VC.ESTABLISHED
    stale = VC.evaluate(now=now + 60.0,
                        subscription=got["subscription_input"], bound_s=30.0)
    assert stale["verdict"] != VC.ESTABLISHED, (
        "re-ageing may only make the verdict stricter")
    # AND NOTHING SUPPLIED STILL MEANS NOTHING ESTABLISHED.
    none = VC.evaluate(now=now)
    assert none["verdict"] == VC.NOT_ESTABLISHED
    assert none["subscription_input"] is None


def test_the_entry_freshness_uses_what_the_read_established():
    now = 1_000_000.0
    sub = {"alive_at": now - 0.5, "last_update_at": now - 1.0}
    currency = VC.evaluate(now=now, subscription=sub, bound_s=30.0)
    vq = {"book_currency": currency, "venue_ts": now - 2.0, "read_at": now,
          "venue_clock": {"our_response_received_at": now}}
    quote = {"observed_at_epoch_s": now - 2.0}
    fr = loop._entry_freshness(quote, vq, now + 1.0)
    assert fr.get("venue_age_basis") != VC.NO_MECHANISM, fr
    # And a quote whose read established nothing still refuses.
    vq_none = dict(vq, book_currency=VC.evaluate(now=now))
    fr_none = loop._entry_freshness(quote, vq_none, now + 1.0)
    assert fr_none.get("fresh") is not True


@pytest.mark.parametrize("cmp_,established,compat", [
    ({"compared_on": "CONDITION_TO_PAYOUT", "compatibility": "COMPATIBLE",
      "applicable_conditions": ["COMPLETED_IN_REGULATION"],
      "unstated_conditions": [], "mismatched_conditions": []},
     True, "ESTABLISHED"),
    ({"compared_on": "CONDITION_TO_PAYOUT", "compatibility": "UNKNOWN",
      "unstated_conditions": ["STOPPED_BEFORE_THE_MINIMUM"]},
     False, "UNKNOWN"),
    ({"compared_on": "CONDITION_TO_PAYOUT", "compatibility": "INCOMPATIBLE",
      "mismatched_conditions": ["DECIDED_AFTER_REGULATION"]},
     False, "INCOMPATIBLE"),
])
def test_management_reads_the_verdict_the_entry_was_admitted_on(
        cmp_, established, compat):
    from sportsassets import bettor_hold_value as HV

    att = FM._attestation_from_comparison(
        cmp_, book_rule="FULL_GAME_INCLUDING_EXTRA_INNINGS")
    assert att["derived_from"] == "settlement_comparison"
    term = HV._terminal_rule(att)
    assert term["established"] is established
    assert term["compatibility"] == compat


def test_a_row_without_a_condition_to_payout_comparison_derives_nothing():
    assert FM._attestation_from_comparison(None) is None
    assert FM._attestation_from_comparison(
        {"verdict": "COMPATIBLE"}) is None, (
        "a comparison not made condition-to-payout is not the admission "
        "verdict, and nothing is inferred from it")


@pytest.mark.asyncio
async def test_the_account_read_offers_no_partial_total(monkeypatch):
    from sportsassets.workers import mirror_shadow as MS

    async def failed_walk(pmus, basis_out=None):
        return None, 3, False

    monkeypatch.setattr(MS, "account_positions_walk", failed_walk)
    got = await loop.venue_account_exposure()
    assert got["ok"] is False
    assert got["refusal"] == "VENUE_POSITIONS_WALK_INCOMPLETE"
    assert loop._venue_positions_for_gate(got) is None

    async def no_cost(pmus, basis_out=None):
        return {"some-slug": 5.0}, 1, False

    monkeypatch.setattr(MS, "account_positions_walk", no_cost)
    got = await loop.venue_account_exposure()
    assert got["ok"] is False
    assert got["refusal"] == "VENUE_POSITION_COST_NOT_STATED"


def test_an_instant_orders_the_same_before_and_after_the_database():
    """THE FEE NONDETERMINISM, PINNED AT ITS CAUSE.

    The same arrival instant, once as the raw float a fill is placed with and
    once as Postgres returns it (whole microseconds), must produce the same
    ordering key -- otherwise whether an earlier fill sorts first depends on
    sub-microsecond rounding, and the venue's cumulative fee cap is applied on
    some runs and not others.
    """
    import datetime as _dt

    for raw in (1790699139.8472354, 1790699139.8472356, 1790699139.8472350,
                1790699135.4755081):
        stored = _dt.datetime.fromtimestamp(round(raw, 6), _dt.timezone.utc)
        _b1, k_raw = FB.venue_execution_order(
            {"fill_id": "fvf:o:1", "at": round(raw, 6)})
        _b2, k_db = FB.venue_execution_order(
            {"fill_id": "fvf:o:1", "at": stored})
        assert k_raw == k_db, (raw, k_raw, k_db)
    # And two fills written at one instant sort by their venue fill ids,
    # which is the order the venue sent them.
    t = _dt.datetime.fromtimestamp(1790699139.847235, _dt.timezone.utc)
    first = FB.venue_execution_order({"fill_id": "fvf:o:vf-1", "at": t})[1]
    second = FB.venue_execution_order(
        {"fill_id": "fvf:o:vf-2", "at": round(1790699139.8472354, 6)})[1]
    assert first < second


@pg
@pytest.mark.asyncio
async def test_a_venue_confirmed_ending_closes_the_order_and_keeps_inventory():
    """`record_venue_terminal` against the migrated state machine.

    Partially filled then cancelled by the venue: the ORDER stops being live and
    the six contracts it filled are still held. An ending that does not balance
    against the ledger is not applied.
    """
    conn = await _connect()
    try:
        await F.clean(conn)
        await F.seed(conn)
        got = await FB.record_intent(
            conn, intent_id="fpi-emptybook-terminal", account_id=F.ACCT,
            venue=F.VENUE, venue_class="FUNDED", us_market_slug=F.US_SLUG,
            event_key="ev-emptybook", order_intent=FX.LONG, limit_price=0.62,
            quantity=10, collateral_usd=6.2, effective_digest="d")
        assert got.get("ok"), got
        await FB.record_acknowledgement(conn, "fpi-emptybook-terminal",
                                        venue_order_id="vo-term", status="open")
        await FB.ingest_fills(conn, "fpi-emptybook-terminal", [
            {"qty": 6.0, "price": 0.62, "venue_fill_id": "vf-term-1"}])
        # AN ENDING THAT DOES NOT BALANCE IS NOT APPLIED.
        bad = await FB.record_venue_terminal(
            conn, "fpi-emptybook-terminal", venue_state="canceled",
            venue_filled=7.0, leaves=0.0, ledger_filled=6.0)
        assert bad["applied"] is False
        # STILL WORKING IS NOT AN ENDING.
        working = await FB.record_venue_terminal(
            conn, "fpi-emptybook-terminal", venue_state="canceled",
            venue_filled=6.0, leaves=4.0, ledger_filled=6.0)
        assert working["applied"] is False
        ok = await FB.record_venue_terminal(
            conn, "fpi-emptybook-terminal", venue_state="canceled",
            venue_filled=6.0, leaves=0.0, ledger_filled=6.0)
        assert ok["applied"] is True and ok["state"] == "CANCELLED"
        row = await conn.fetchrow(
            "SELECT state, residual_qty::float8 AS rq, "
            "       bettor_funded_order_is_outstanding(state) AS live "
            "  FROM bettor_funded_intents WHERE intent_id=$1",
            "fpi-emptybook-terminal")
        assert row["state"] == "CANCELLED" and row["live"] is False
        assert row["rq"] == pytest.approx(6.0), "inventory is not touched"
        # AND A REPLAY MOVES NOTHING.
        again = await FB.record_venue_terminal(
            conn, "fpi-emptybook-terminal", venue_state="filled",
            venue_filled=6.0, leaves=0.0, ledger_filled=6.0)
        assert again["applied"] is False
    finally:
        await conn.execute("DELETE FROM bettor_funded_economics WHERE "
                           "intent_id='fpi-emptybook-terminal'")
        await conn.execute("DELETE FROM bettor_funded_fills WHERE "
                           "intent_id='fpi-emptybook-terminal'")
        await conn.execute("DELETE FROM bettor_funded_intents WHERE "
                           "intent_id='fpi-emptybook-terminal'")
        await F.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_every_provider_event_is_recorded_with_its_identity_and_outcome(
        monkeypatch):
    """EVERY EVENT THE SCHEDULE SAW HAS A DURABLE ROW, AND THE ROWS ADD UP.

    Three events in one fetch: the fixture the venue lists (admitted), one
    with no Pinnacle price, and one between teams the venue does not carry.
    The two refusals used to be counts in a heartbeat the next cycle
    overwrote. Each now has its own row with who played, what it mapped to
    where it got that far, and the refusal that stopped it, and the rows
    reconcile to the funnel's own event count.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    venue = F.Venue()

    def odds(now):
        admitted = F.odds_event(now)
        no_pinnacle = dict(F.odds_event(now), id="odds-emptybook-no-pinnacle")
        no_pinnacle["bookmakers"] = [b for b in no_pinnacle["bookmakers"]
                                     if b["key"] != "pinnacle"]
        unlisted = dict(F.odds_event(now), id="odds-emptybook-unlisted",
                        home_team="Boston Red Sox",
                        away_team="New York Yankees")
        for b in unlisted["bookmakers"]:
            for m in b["markets"]:
                m["outcomes"] = [{"name": "Boston Red Sox", "price": 1.9},
                                 {"name": "New York Yankees", "price": 1.9}]
        return [admitted, no_pinnacle, unlisted]

    try:
        await F.clean(conn)
        await F.seed(conn)
        F.substitute(monkeypatch, venue, odds=odds)
        out = await loop.cycle(conn)
        co = out["candidate_outcomes"]
        assert co["reconciles"] is True, co
        assert co["unclassified"] == 0
        assert co["per_sport"]["baseball_mlb"] == {
            "provider_events": 3, "rows": 3, "reconciles": True}, co
        assert co["persisted"]["ok"] is True, co["persisted"]
        assert co["persisted"]["rows"] == 3

        rows = {r["provider_event_id"]: r for r in await conn.fetch(
            "SELECT * FROM ext_candidate_outcomes WHERE cycle_id=$1",
            co["persisted"]["cycle_id"])}
        assert set(rows) == {F.ODDS_EVENT, "odds-emptybook-no-pinnacle",
                             "odds-emptybook-unlisted"}

        a = rows[F.ODDS_EVENT]
        assert a["outcome"] == "ADMITTED", dict(a)
        assert a["first_refusal"] is None
        assert a["us_market_slug"] == F.US_SLUG
        assert a["home"] == F.HOME and a["away"] == F.AWAY
        assert a["commence_time"] == "%sT23:10:00Z" % F.GAME

        n = rows["odds-emptybook-no-pinnacle"]
        assert n["outcome"] == "REFUSED"
        assert n["first_refusal"] == "NO_PINNACLE_ON_EVENT"
        assert n["global_slug"] is None and n["us_market_slug"] is None

        u = rows["odds-emptybook-unlisted"]
        assert u["outcome"] == "REFUSED", dict(u)
        assert u["first_refusal"], dict(u)
        assert u["home"] == "Boston Red Sox"
        assert u["us_market_slug"] is None

        # the codes are the cycle's own tally, attributed: every refusal the
        # tally counted for these events appears on exactly one row
        import json as _json
        attributed: dict = {}
        for r in rows.values():
            for c in _json.loads(r["codes"]) if isinstance(r["codes"], str) \
                    else r["codes"]:
                attributed[c] = attributed.get(c, 0) + 1
        for code in ("NO_PINNACLE_ON_EVENT", u["first_refusal"]):
            assert attributed.get(code) == out["refusals"].get(code), \
                (code, attributed, out["refusals"])

        # AND THE HEARTBEAT CARRIES THE SUMMARY, so the operator view can
        # say whether this cycle's rows reconciled without reading them all
        hb = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            loop.HEARTBEAT_KEY)
        hb = _json.loads(hb) if isinstance(hb, str) else hb
        assert hb["candidate_outcomes"]["reconciles"] is True
        assert hb["candidate_outcomes"]["persisted"]["cycle_id"] == \
            co["persisted"]["cycle_id"]
    finally:
        await F.clean(conn)
        await conn.close()


def test_an_event_nothing_was_attributed_to_is_unclassified_not_dropped():
    """The accounting's own failure is a named outcome, and it fails the
    reconciliation rather than disappearing into it."""
    assert loop._event_outcome([])["outcome"] == "UNCLASSIFIED"
    assert loop._event_outcome(["DUPLICATE_OBSERVATION_SKIPPED"])[
        "outcome"] == "ALREADY_RECORDED"
    got = loop._event_outcome(["ADMITTED", "ENTRY_INVENTORY_WRITTEN",
                               "FUNDED:THE_ORDER_EXCEEDS_AN_EFFECTIVE_RAIL"])
    assert got["outcome"] == "ADMITTED" and got["first_refusal"] is None
    got = loop._event_outcome(["VENUE_MAPPING_AMBIGUOUS",
                               "VENUE_MAPPING_AMBIGUOUS", "X"])
    assert got == {"outcome": "REFUSED",
                   "first_refusal": "VENUE_MAPPING_AMBIGUOUS",
                   "codes": ["VENUE_MAPPING_AMBIGUOUS", "X"]}
    rows = [dict(sport_key="s", outcome="REFUSED"),
            dict(sport_key="s", outcome="UNCLASSIFIED")]
    rec = loop._reconcile_event_ledger(rows, {"s": {"provider_events": 2}})
    assert rec["reconciles"] is False and rec["unclassified"] == 1
    rec = loop._reconcile_event_ledger(rows[:1], {"s": {"provider_events": 2}})
    assert rec["reconciles"] is False
    assert rec["per_sport"]["s"] == {"provider_events": 2, "rows": 1,
                                     "reconciles": False}
