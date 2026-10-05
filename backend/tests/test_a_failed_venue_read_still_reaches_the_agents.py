"""P0 INCIDENT (2026-10-04): A FAILED COLLECTOR VENUE READ STILL REACHES THE
AGENTS -- AS A NO-PRICE CALIBRATION-ONLY RECORD WITH A PRECISE REFUSAL.

Measured in production on 191b299: when the collector's own venue book read
failed (VENUE_BOOK_READ_FAILED -- our request gate's cooldown refusal, the await
timing out -- or VENUE_BOOK_READ_RETURNED_ERROR -- a 429, a timeout, a 404, any
venue error) no valuation was written at all, so no paper agent received the
opportunity: 165 PinnAPI-triggered evaluations a day (mostly held events,
starving Xavier's fresh evidence) and 48+ discovery events a day. No paper
strategy prices from the collector's read -- each reads its own book.

Proved here, on production-shaped failures:

  * the precise refusal: VENUE_GATE_COOLDOWN / VENUE_RATE_LIMITED /
    VENUE_TIMEOUT / VENUE_NOT_FOUND / VENUE_ERROR, from the failed quote's own
    diagnostic; every other refusal builds nothing new;
  * through the real scheduled cycle: the valuation is persisted
    CALIBRATION_ONLY with NO displayed price (executable price NULL, the
    displayed quote's price NULL, `no_book_read`), its refusals led by the
    lane code and the precise code, the event still counted REFUSED under
    the lane code, the paper hook handed the row -- and nothing trades;
  * a paper agent decides such a row on ITS OWN book (an ENTER is priced
    from the paper book, never from the record), and with no readable book
    of its own it refuses by name and places nothing.
SYNTHETIC books, valuations and venue answers; no venue is contacted.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_valuation_purpose as vp
from sportsassets import venue_request_gate as GRT
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")

SLUG = "aec-mlb-d4-d8-2026-10-02"


# ── THE FAILED READS, AS `_read_book_blocking` RETURNS THEM ───────────
def gate_refusal(slug):
    code = GRT.R_HOLD_EXCEEDS_UNDEADLINED_CAP
    return {"marketData": None, "error": code,
            "refused_by": "OUR_REQUEST_GATE",
            "gate_detail": {"cooldown_left_s": 31.0},
            "diagnostic": {"slug": slug, "stage": "REQUEST_GATE",
                           "refusal": code, "cooldown_left_s": 31.0}}


def rate_limited(slug):
    return {"marketData": None, "feed": None, "error": "RateLimitError",
            "diagnostic": {"slug": slug, "stage": "BOOK_READ",
                           "endpoint": "markets.book",
                           "code": "RateLimitError", "status": None,
                           "error_type": "RateLimitError",
                           "http_status": 429, "is_rate_limited": True,
                           "retry_after_s": 5.0, "is_timeout": False}}


def sdk_timeout(slug):
    return {"marketData": None, "feed": None, "error": "APITimeoutError",
            "diagnostic": {"slug": slug, "stage": "BOOK_READ",
                           "error_type": "APITimeoutError",
                           "http_status": None, "is_rate_limited": False,
                           "is_timeout": True}}


def not_found(slug):
    return {"marketData": None, "feed": None, "error": "NotFoundError",
            "diagnostic": {"slug": slug, "stage": "BOOK_READ",
                           "error_type": "NotFoundError",
                           "http_status": 404, "is_rate_limited": False,
                           "is_timeout": False}}


def connection_error(slug):
    return {"marketData": None, "feed": None,
            "error": "APIConnectionError",
            "diagnostic": {"slug": slug, "stage": "BOOK_READ",
                           "error_type": "APIConnectionError",
                           "http_status": None, "is_rate_limited": False,
                           "is_timeout": False}}


def await_timed_out(slug):
    raise TimeoutError("the await on the book read timed out")


class _Conn:
    pass


def _quote(monkeypatch, reader):
    monkeypatch.setattr(loop, "_read_book_blocking", reader)
    return asyncio.run(loop.venue_quote(
        _Conn(), us_slug=SLUG, intent="ORDER_INTENT_BUY_LONG"))


@pytest.mark.parametrize("reader,lane,precise", [
    (gate_refusal, loop.R_VENUE_READ_ERROR, loop.VR_GATE_COOLDOWN),
    (rate_limited, loop.R_VENUE_READ_ERROR, loop.VR_RATE_LIMITED),
    (sdk_timeout, loop.R_VENUE_READ_ERROR, loop.VR_TIMEOUT),
    (not_found, loop.R_VENUE_READ_ERROR, loop.VR_NOT_FOUND),
    (connection_error, loop.R_VENUE_READ_ERROR, loop.VR_ERROR),
    (await_timed_out, loop.R_VENUE_READ_FAILED, loop.VR_TIMEOUT),
])
def test_the_precise_venue_read_refusal(monkeypatch, reader, lane, precise):
    vq = _quote(monkeypatch, reader)
    assert vq["ok"] is False and vq["refusal"] == lane, vq
    assert loop.venue_read_refusal(vq) == precise
    basis = loop._read_failure_basis(vq)
    assert basis["refusal"] == lane
    assert basis["venue_read_refusal"] == precise
    assert basis["no_book_read"] is True
    shown = basis["displayed"]
    assert shown["acquisition_price"] is None and shown["api_price"] is None
    assert shown["depth"] is None and shown["ok"] is False
    assert shown["usable_for_orders"] is False
    ms = loop._displayed_market_state(basis)
    assert ms["ask"] is None and ms["readable"] is False
    assert ms["ask_basis"] == "NO_BOOK_WAS_READ__NO_PRICE"
    # the read failure never builds the CURRENCY basis (it has no book)
    assert loop._calibration_only_basis(vq) is None


def test_every_other_refusal_builds_nothing_new():
    for vq in ({"ok": True},
               {"ok": False, "refusal": loop.R_NO_DEPTH},
               {"ok": False, "refusal": loop.R_NO_SLUG},
               {"ok": False, "refusal": loop.R_BOOK_CURRENCY_NOT_ESTABLISHED},
               None, {}):
        assert loop.venue_read_refusal(vq) is None
        assert loop._read_failure_basis(vq) is None


def test_the_precise_codes_are_staged_and_classified():
    for code in loop.VENUE_READ_REFUSALS + loop.CALIBRATION_ONLY_AFTER_READ_FAILURE:
        assert ext.STAGE_OF[code] == "2_FRESHNESS", code
        assert code in ext.EVALUABILITY_OF, code
    # our own gate is OURS; the venue's answers are theirs
    assert ext.EVALUABILITY_OF[loop.VR_GATE_COOLDOWN] == ext.COULD_NOT_EVALUATE
    for code in (loop.VR_RATE_LIMITED, loop.VR_TIMEOUT, loop.VR_NOT_FOUND,
                 loop.VR_ERROR):
        assert ext.EVALUABILITY_OF[code] == ext.EXTERNAL_DEPENDENCY
    refusals = [loop.R_VENUE_READ_ERROR, loop.VR_RATE_LIMITED,
                "EXECUTION_ESTIMATE_NOT_IDENTIFIED", vp.R_CALIBRATION_ONLY]
    assert ext.first_stage(refusals) == "2_FRESHNESS"
    cyc = ext.cycle_evaluability([refusals])
    assert cyc["verdict"] != ext.V_CLASSIFICATION_HAS_DRIFTED, cyc


# ═════════════════════════════════════════════════════════════════════
# THROUGH THE REAL SCHEDULED CYCLE
# ═════════════════════════════════════════════════════════════════════

async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("reader,lane,precise", [
    (gate_refusal, loop.R_VENUE_READ_ERROR, loop.VR_GATE_COOLDOWN),
    (rate_limited, loop.R_VENUE_READ_ERROR, loop.VR_RATE_LIMITED),
    (await_timed_out, loop.R_VENUE_READ_FAILED, loop.VR_TIMEOUT),
])
async def test_a_failed_read_writes_a_no_price_calibration_row_and_nothing_trades(
        monkeypatch, reader, lane, precise):
    from tests import _emptybook_fixture as F

    conn = await _connect()
    venue = F.Venue()
    handed: list = []
    try:
        await F.clean(conn)
        await F.seed(conn)
        # THE STRONGEST SETTING: the fixture turns the submission switches ON,
        # so anything that could carry this record to a venue would.
        F.substitute(monkeypatch, venue)
        monkeypatch.setattr(loop, "_read_book_blocking", reader)

        async def paper_hook(conn_, valuation_id):
            handed.append(valuation_id)
        monkeypatch.setattr(loop, "_paper_valuation", paper_hook)

        out = await loop.cycle(conn)
        assert out["ran"] is True, out.get("why")
        # ── THE EVENT STAYS REFUSED UNDER THE LANE'S VENUE-READ CODE ──────
        assert out["refusals"].get(lane) == 1, out["refusals"]
        for code in ("ADMITTED", "ENTRY_INVENTORY_WRITTEN",
                     "EXPOSURE_RESERVED"):
            assert code not in out["refusals"], out["refusals"]
        assert not any(k.startswith("FUNDED:") for k in out["refusals"])
        assert out["evaluated"] == 0 and out["written"] == 0
        assert out["calibration_only"]["recorded"] == 1
        assert out["calibration_only"]["after_read_failure"] == {precise: 1}
        assert out["entries"] == []
        led = [e for e in out["mapped_candidate_ledger"]
               if e.get("us_market_slug") == F.US_SLUG]
        assert led[0]["first_refusal"] == lane
        assert led[0]["venue_read_refusal"] == precise
        assert led[0]["calibration_only_record"] == "RECORDED"

        # ── THE ROW: CALIBRATION_ONLY, NO PRICE, THE PRECISE REFUSAL ──────
        row = await conn.fetchrow(
            "SELECT id, record_purpose, admissible, decision, refusals, "
            "probability, executable_price, cost_per_contract, "
            "estimated_edge_per_contract, proposed_size, execution_estimate, "
            "risk_verdict, exposure_observed, calibration_only_evidence, "
            "us_market_slug, event_key FROM external_valuations "
            "WHERE condition_id=$1", F.CONDITION)
        assert row["record_purpose"] == vp.CALIBRATION_ONLY
        assert row["admissible"] is False and row["decision"] == "NO_TRADE"
        refusals = list(row["refusals"])
        assert refusals[:2] == [lane, precise], refusals
        assert vp.R_CALIBRATION_ONLY in refusals
        for empty in ("executable_price", "cost_per_contract",
                      "estimated_edge_per_contract", "proposed_size",
                      "execution_estimate", "risk_verdict",
                      "exposure_observed"):
            assert row[empty] is None, empty
        assert row["probability"] is not None       # the opportunity is seen
        assert row["us_market_slug"] == F.US_SLUG
        ev = json.loads(row["calibration_only_evidence"])
        assert ev["usable_for_orders"] is False
        assert ev["venue_read_refusal"] == precise
        assert ev["venue_read_lane_refusal"] == lane
        assert ev["no_book_read"] is True and ev["displayed_price"] is None
        assert ev["displayed_quote"]["acquisition_price"] is None
        assert ev["displayed_quote"]["usable_for_orders"] is False
        assert ev["compared_at_the_displayed_price"]["price"] is None
        assert ev["book_currency"]["verdict"] == "NOT_READ"

        # ── THE AGENTS ARE HANDED THE ROW ─────────────────────────────────
        assert handed == [row["id"]]

        # ── NOTHING TRADED ────────────────────────────────────────────────
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE condition_id=$1",
            F.CONDITION) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            F.ACCT) == 0
        assert venue.creates_sent() == []
        import asyncpg
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                "UPDATE external_valuations SET admissible=true, "
                "decision='BUY' WHERE id=$1", row["id"])
    finally:
        await F.clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# A PAPER AGENT DECIDES IT ON ITS OWN BOOK
# ═════════════════════════════════════════════════════════════════════

async def _no_price_valuation(conn, *, decided_at: float, p_pin=0.62):
    """The record the collector now writes after a failed read: CALIBRATION_
    ONLY, no displayed price, refusals led by the lane and precise codes."""
    import uuid

    from tests import paper_live_fixture as PL
    slug = "%s%s" % (PL.SYN, uuid.uuid4().hex[:10])
    ev = {"usable_for_orders": False, "record_purpose": vp.CALIBRATION_ONLY,
          "venue_read_refusal": loop.VR_RATE_LIMITED,
          "venue_read_lane_refusal": loop.R_VENUE_READ_ERROR,
          "no_book_read": True, "displayed_price": None,
          "displayed_quote": {"ok": False, "acquisition_price": None,
                              "usable_for_orders": False,
                              "what_this_is": loop.NO_BOOK_READ}}
    vid = await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, probability, decision, admissible, refusals, why, "
        " payout_event, payout_is_complement, buy_intent, ladder_side, "
        " record_purpose, decided_at, event_key, settlement_comparison, "
        " calibration_only_evidence) "
        "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'the-odds-api.com/v4','pinnacle','power','PMUS',$2,$2,'HOME',"
        " 'baseball','h2h','FULL_GAME','{}'::jsonb,2,2,to_timestamp($3),"
        " to_timestamp($3 + 1),$4,'NO_TRADE',false,$5::text[],'synthetic',"
        " 'HOME',false,'ORDER_INTENT_BUY_LONG','ASK','CALIBRATION_ONLY',"
        " to_timestamp($6),'e-' || $2,$7::jsonb,$8::jsonb) RETURNING id",
        ext.EXPERIMENT_ID, slug, decided_at - 5.0, float(p_pin),
        [loop.R_VENUE_READ_ERROR, loop.VR_RATE_LIMITED,
         "MARKET_STATE_UNREADABLE", vp.R_CALIBRATION_ONLY],
        decided_at,
        json.dumps(PL.settlement_comparison("INCOMPATIBLE"), default=str),
        json.dumps(ev))
    return {"valuation_id": vid, "slug": slug}


@pytest.fixture
def cg_on(monkeypatch, new_strategies_off):
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_derek as PD

    from tests import paper_live_fixture as PL
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


@pg
async def test_a_paper_agent_decides_the_no_price_row_on_its_own_book(cg_on):
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_runtime as PR

    from tests import paper_harness as H
    from tests import paper_live_fixture as PL

    conn = await _connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "nobook", now=now)
        t = PL.Transport(now)
        v = await _no_price_valuation(conn, decided_at=now - 2)
        # ITS OWN BOOK: the paper agent reads it and prices from it
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        g = await PR.decide_valuation(
            conn, valuation_id=v["valuation_id"], now=now,
            market_data=PL.client(t), account_id=acct["account_id"],
            fee_fn=H.flat_fee(0.01), schedule_fill=lambda: {"scheduled": False})
        cg = g["benchmark_completed_game"]
        assert cg["verdict"] == "ENTER" and cg.get("order_id"), cg
        o = await conn.fetchrow("SELECT limit_price, us_market_slug FROM "
                                " paper_orders WHERE order_id=$1",
                                cg["order_id"])
        # priced from the paper book (0.50 offer), never from the record,
        # which carries no price at all
        assert float(o["limit_price"]) == 0.50
        d = await conn.fetchrow("SELECT pinnacle, book FROM paper_decisions "
                                " WHERE decision_id=$1", cg["decision_id"])
        pin = H.j(d["pinnacle"])
        assert pin["valuation_record_purpose"] == vp.CALIBRATION_ONLY
        assert pin["displayed_quote_used_as_price"] is False
        assert H.j(d["book"])["basis"] == \
            "OBSERVED_PAPER_BOOK_LEVELS_NOT_THE_VALUATION_QUOTE"

        # WITH NO READABLE BOOK OF ITS OWN it refuses by name: no order
        v2 = await _no_price_valuation(conn, decided_at=now - 2)
        g2 = await PR.decide_valuation(
            conn, valuation_id=v2["valuation_id"], now=now,
            market_data=PL.client(t), account_id=acct["account_id"],
            fee_fn=H.flat_fee(0.01), schedule_fill=lambda: {"scheduled": False},
            book_retry=False)
        cg2 = g2["benchmark_completed_game"]
        assert cg2["verdict"] == "REFUSE" and not cg2.get("order_id"), cg2
        assert cg2["refusal"] == PB.R_NO_BOOK
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders o JOIN paper_decisions d ON "
            " d.decision_id = o.decision_id WHERE d.valuation_id=$1",
            v2["valuation_id"]) == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()
