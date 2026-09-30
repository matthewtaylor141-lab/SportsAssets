"""OBSERVING A PAIR IS NOT APPROVING ITS ACQUISITION (2026-10-01).

On 2026-09-30 production examined 120 siblings on 18 fixtures whose settlement
key matched the held leg and discarded every one because the cancelled-fixture
cell had no determined payout. These tests pin what replaced that:

  * the venue's cancellation wording is read into one of the named treatments
    -- refund of basis, fixed 50 cents, last fair market price, another stated
    resolution, unstated/ambiguous -- and a variable last-fair-price settlement
    is never turned into a refund or fifty cents;
  * a correctly identified pair whose ONLY undetermined region is the
    cancelled cell is recorded WITHOUT admission, with the clause, the
    unresolved region and the ordinary-play table, and no floor -- while funded
    discovery still refuses the same pair and training reads admitted rows only;
  * its labels keep a between-0-and-1 settlement as unresolved evidence, not a
    push, and a declared void still counts in the void rate;
  * the read budget is spent on siblings that can pair, rotates through them
    persistently, and a search that ran out of budget concludes nothing.

Prose marked SYNTHETIC is written for these tests; it is not venue text.
"""
import json
import time

import pytest

from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_pair_observations as PO
from sportsassets import bettor_settlement_clauses as S
from tests import test_the_hedge_beats_hold_through_the_real_suppliers as HW
from tests.test_the_observer_is_fed_by_the_catalogue_and_accounts_for_every_attempt import (  # noqa: E501
    DSN, PRICES, _clean, _conn, _leave_nothing_behind, _reader, _row,  # noqa: F401
    _seed_the_catalogue, _sweep_ran)

LONG, SHORT = PO.LONG, PO.SHORT
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")

#: SYNTHETIC cancellation wordings, one per treatment.
LAST_FAIR = ("If the game is cancelled, the market will resolve at the last "
             "fair market price at the time of cancellation.")
FIFTY = "If the game is cancelled, the market resolves 50-50."
REFUND = HW.CANCELLATION_CLAUSE


# ═════════════════════════════════════════════════════════════════════
# 1 · THE TREATMENTS, AS READ (pure)
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,want", [
    (REFUND, S.T_REFUND_OF_BASIS),
    (FIFTY, S.T_FIXED_50C),
    ("If cancelled, contracts pay $0.50.", S.T_FIXED_50C),
    (LAST_FAIR, S.T_LAST_FAIR_PRICE),
    ("If the game is cancelled, the market resolves NO.", S.T_OTHER_STATED),
    (HW.PROSE, S.T_UNSTATED_OR_AMBIGUOUS),
    ("If the game is cancelled, settlement may be at fair value.",
     S.T_UNSTATED_OR_AMBIGUOUS),
    ("If cancelled, contracts are refunded or resolve 50-50.",
     S.T_UNSTATED_OR_AMBIGUOUS),
])
def test_each_cancellation_wording_reads_as_its_own_treatment(text, want):
    t = S.cancellation_treatment(S.read_outcome(text, S.CANCELLED))
    assert t["interpretation"] == want, t


def test_a_last_fair_price_settlement_is_variable_never_a_refund_or_fifty():
    r = S.read_outcome(LAST_FAIR, S.CANCELLED)
    assert r["established"] and r["resolution"] == S.RES_LAST_FAIR_PRICE
    # "at the time of cancellation" names when the price is taken; it is not
    # a second resolution that makes the clause ambiguous
    assert r.get("refusal") is None
    for side in S.SIDES:
        got = S.side_payout_cents(r["resolution"], side, basis_cents=40)
        assert got["cents"] is None and got["variable"] is True
        assert got["refusal"] == S.R_VARIABLE_PAYOUT


def _t(kind):
    return {"interpretation": kind}


def test_matching_wording_does_not_prove_the_pair_cannot_lose():
    # 50 + 50 cents against a pair that cost 104: both "fifty-fifty", and a loss
    got = S.pair_cancellation(_t(S.T_FIXED_50C), _t(S.T_FIXED_50C),
                              held_cost_cents=55, other_cost_cents=49)
    assert got["verdict"] == S.P_FIXED and got["receives_cents"] == 100.0
    assert got["can_both_lose"] is True
    # refunds on both legs return the basis; fees are not assumed refunded
    got = S.pair_cancellation(_t(S.T_REFUND_OF_BASIS), _t(S.T_REFUND_OF_BASIS),
                              held_cost_cents=55, other_cost_cents=49)
    assert got["verdict"] == S.P_BASIS_RETURNED and got["can_both_lose"] is None
    # a last-fair-price leg has no floor at all
    got = S.pair_cancellation(_t(S.T_LAST_FAIR_PRICE), _t(S.T_REFUND_OF_BASIS),
                              held_cost_cents=55, other_cost_cents=49)
    assert got["verdict"] == S.P_VARIABLE and got["receives_cents"] is None
    assert got["can_both_lose"] is True
    got = S.pair_cancellation(_t(S.T_UNSTATED_OR_AMBIGUOUS),
                              _t(S.T_FIXED_50C), held_cost_cents=55,
                              other_cost_cents=49)
    assert got["verdict"] == S.P_UNDETERMINED


# ═════════════════════════════════════════════════════════════════════
# 2 · RECORDED WITHOUT ADMISSION; STILL REFUSED FOR MONEY
# ═════════════════════════════════════════════════════════════════════

async def _observe(conn, text):
    return await PO.observe_candidate(
        conn, us_market_slug=HW.HELD, side=LONG, quoter=HW._quoter(PRICES),
        prose_reader=HW._prose_reader(text), now=time.time())


async def _rows(conn):
    out = []
    for r in await conn.fetch(
            "SELECT * FROM bettor_pair_observations WHERE fixture=$1",
            HW.EVENT):
        d = dict(r)
        for k in ("structure", "cancellation_terms", "unresolved"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


@pg
@pytest.mark.parametrize("text,treatment,pair", [
    (HW.PROSE, S.T_UNSTATED_OR_AMBIGUOUS, S.P_UNDETERMINED),
    (HW.PROSE + " " + LAST_FAIR, S.T_LAST_FAIR_PRICE, S.P_VARIABLE),
])
async def test_an_unresolved_cancellation_is_observed_and_not_admitted(
        text, treatment, pair):
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        got = await _observe(conn, text)
        assert got["ok"] is True, got
        assert got["admitted"] == 0
        assert got["conclusion"] == PO.N_OBSERVED_NOT_ADMITTED, got
        written = [r for r in got["observed_not_admitted"] if r["written"]]
        assert written, got
        # THE CLAUSE, THE TREATMENT AND THE VENUE'S WORDS ARE IN THE RECORD
        assert got["held"]["cancellation"]["interpretation"] == treatment
        rows = await _rows(conn)
        assert rows and all(r["admission_status"] == PO.NOT_ADMITTED
                            for r in rows)
        r = rows[0]
        assert "fixture cancelled or abandoned" in r["unresolved"]
        ct = r["cancellation_terms"]
        assert ct["held"]["interpretation"] == treatment
        assert ct["hedge"]["interpretation"] == treatment
        assert ct["pair"]["verdict"] == pair
        assert ct["held"]["prose_sha256"]
        # NO FLOOR IS CLAIMED
        st = r["structure"]
        assert st["taxonomy"] == "UNESTABLISHABLE"
        assert st["ordinary_play"]["is_a_floor"] is False
        assert st["cancelled_cell"] == "UNRESOLVED"
        assert st["authorizes_nothing"] is True
        assert st.get("min_payout_cents") is None
        # THE SAME EVIDENCE STILL REFUSES THE FUNDED PATH
        held = await HS.held_leg_for(
            conn, position={"intent_id": "t", "us_market_slug": HW.HELD,
                            "order_intent": LONG, "limit_price": 0.55,
                            "filled_qty": 1, "residual_qty": 1},
            prose_reader=HW._prose_reader(text), now=time.time())
        cands = await HS.candidate_legs_for(
            conn, held_row=dict(held["row"], market_slug=HW.HELD,
                                residual_qty=1),
            quoter=HW._quoter(PRICES), prose_reader=HW._prose_reader(text),
            now=time.time())
        found = PC.discover(held_leg=held["leg"],
                            candidate_legs=[c["leg"] for c in cands["legs"]],
                            sport_permits_tie=False)
        assert found["admitted"] == [], found
        assert found["refusal"] == PC.R_NO_SECOND_CONTRACT


@pg
async def test_a_stated_refund_is_admitted_as_before():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        got = await _observe(conn, HW.PROSE_WITH_CANCELLATION)
        assert got["admitted"] >= 1 and got["conclusion"] == PO.N_ADMITTED
        rows = await _rows(conn)
        assert rows and all(r["admission_status"] == PO.ADMITTED
                            for r in rows)
        assert got["held"]["cancellation"]["interpretation"] \
            == S.T_REFUND_OF_BASIS


# ═════════════════════════════════════════════════════════════════════
# 3 · LABELS KEEP THE EVIDENCE; TRAINING READS ADMITTED ROWS ONLY
# ═════════════════════════════════════════════════════════════════════

def _unadmitted_row(**kw):
    return dict({"primary_side": LONG, "hedge_side": LONG,
                 "admission_status": PO.NOT_ADMITTED}, **kw)


def test_a_between_price_on_an_unresolved_pair_is_evidence_not_a_push():
    from sportsassets import bettor_live_read as LR

    res = {"status": LR.RESOLVED}
    lab = PO.label_from(_unadmitted_row(), dict(res, settlement_price=0.43),
                        dict(res, settlement_price=1.0))
    assert lab["status"] == PO.NOT_A_LABEL
    assert lab["why"] == PO.WHY_NONBINARY_UNRESOLVED
    assert lab["primary_price"] == 0.43
    # an admitted pair's between price is still a push, as before
    lab = PO.label_from(dict(_unadmitted_row(), admission_status=PO.ADMITTED),
                        dict(res, settlement_price=0.5),
                        dict(res, settlement_price=1.0))
    assert lab["status"] == PO.LABELLED and lab["push"] is True
    # a declared void is kept, as before
    lab = PO.label_from(_unadmitted_row(), {"status": PO.VOID},
                        dict(res, settlement_price=1.0))
    assert lab["status"] == PO.NOT_A_LABEL and lab["why"] == PO.WHY_VOID


@pg
async def test_training_reads_admitted_rows_only_and_voids_still_count():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        got = await _observe(conn, HW.PROSE)
        assert got["observed_not_admitted"]
        # settle the unadmitted observation(s) binary through the real
        # labeller: LABELLED, and still excluded from training
        from sportsassets import bettor_live_read as LR

        def settled(slug):
            return {"status": LR.RESOLVED,
                    "settlement_price": 1.0 if slug == HW.HELD else 0.0}
        lp = await PO.label_pending(conn, settlement_reader=settled,
                                    now=time.time() + 60)
        assert lp.get("labelled", 0) >= 1, lp
        n = await conn.fetchval(
            "SELECT count(*) FROM bettor_pair_observations WHERE fixture=$1 "
            " AND label_status='LABELLED' AND admission_status=$2",
            HW.EVENT, PO.NOT_ADMITTED)
        assert n >= 1
        lab = await PO.labelled(conn)
        assert lab.get("ok", True) is not False
        assert HW.EVENT not in {str(f) for f in lab.get("fixtures") or []}


# ═════════════════════════════════════════════════════════════════════
# 4 · THE READ BUDGET GOES TO SIBLINGS THAT CAN PAIR, PERSISTENTLY
# ═════════════════════════════════════════════════════════════════════

async def _other_period_rows(conn, n):
    for i in range(n):
        await _row(conn, event=HW.EVENT,
                   slug="asc-mlb-bos-nyy-2026-10-05-fh-x%02d" % i,
                   st="baseball_team_first_half_spread", abbr="bos",
                   intent=LONG, signed="-0.5",
                   title="Boston Red Sox vs. New York Yankees")


@pg
async def test_other_period_siblings_are_excluded_before_reads_and_the_cap():
    calls = []
    inner = HW._quoter(PRICES)

    async def counting(slug, side):
        calls.append(slug)
        return await inner(slug, side)
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _other_period_rows(conn, HS.MAX_CANDIDATE_ROWS + 5)
        got = await PO.observe_candidate(
            conn, us_market_slug=HW.HELD, side=LONG, quoter=counting,
            prose_reader=HW._prose_reader(HW.PROSE_WITH_CANCELLATION),
            now=time.time())
        assert got["excluded_before_reads"].get(PO.X_OTHER_PERIOD) \
            >= HS.MAX_CANDIDATE_ROWS + 5, got
        assert not any("-fh-" in c for c in calls), calls
        assert HW.SIB in calls
        assert got["siblings_deferred"] == 0
        assert got["admitted"] >= 1


@pg
async def test_a_budget_limited_search_concludes_nothing_and_rotates_on(
        monkeypatch):
    monkeypatch.setattr(HS, "MAX_CANDIDATE_ROWS", 1)
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await _row(conn, event=HW.EVENT,
                   slug="asc-mlb-bos-nyy-2026-10-05-pos-1pt5",
                   st="baseball_team_full_game_spread", abbr="nyy",
                   intent=LONG, signed="+1.5",
                   title="Boston Red Sox vs. New York Yankees")
        first = await PO.observe_candidate(
            conn, us_market_slug=HW.HELD, side=LONG,
            quoter=HW._quoter(PRICES), prose_reader=HW._prose_reader(),
            now=time.time())
        assert first["siblings_examined"] == 1
        assert first["siblings_deferred"] >= 1
        assert first["siblings_truncated_at_limit"] is True
        if first["conclusion"] not in (PO.N_ADMITTED,
                                       PO.N_OBSERVED_NOT_ADMITTED):
            assert first["conclusion"] in (PO.N_LIMIT, PO.N_BUDGET,
                                           PO.N_UNPRICED), first
        # THE NEXT ATTEMPT SKIPS WHAT WAS EXAMINED CONCLUSIVELY
        done = {HS.candidate_identity(s["market_slug"], s["side"])
                for s in first["siblings"]
                if s["stage"] in ("BUILD", "DISCOVERY", "ADMITTED")}
        second = await PO.observe_candidate(
            conn, us_market_slug=HW.HELD, side=LONG,
            quoter=HW._quoter(PRICES), prose_reader=HW._prose_reader(),
            now=time.time(), exhausted_ids=done)
        assert second["siblings_exhausted"] == len(done)
        seen2 = {HS.candidate_identity(s["market_slug"], s["side"])
                 for s in second["siblings"] if s["stage"] != "SCREEN"}
        assert not (seen2 & done)


@pg
async def test_an_incomplete_search_is_not_remembered_as_a_refusal():
    async with _conn() as conn:
        await _clean(conn)
        await conn.execute(
            "INSERT INTO bettor_pair_observation_attempts (pass_id, "
            " attempted_at, finished_at, candidate_source, us_market_slug, "
            " side, fixture, outcome, refusal, observations_written, detail, "
            " venue_reads) VALUES ('p', now(), now(), $1, $2, $3, $4, "
            " 'NOTHING_ADMITTED', $5, 0, $6::jsonb, '{}'::jsonb)",
            PO.SOURCE_CATALOGUE, HW.HELD, LONG, HW.EVENT,
            PO.R_NOTHING_ADMITTED,
            json.dumps({"siblings_deferred": 3, "conclusion": PO.N_LIMIT}))
        mem = await PO.recent_attempts(conn, now=time.time())
        assert HW.EVENT not in mem["refused_fixtures"]
        assert HW.EVENT in mem["last_attempted"]


# ═════════════════════════════════════════════════════════════════════
# 5 · A FIRST LEG THAT CANNOT BE BUILT COSTS NO READ; PAIRABLE GOES FIRST
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_one_participant_first_leg_is_skipped_before_any_read():
    calls = []
    inner = HW._quoter(PRICES)

    async def counting(slug, side):
        calls.append(slug)
        return await inner(slug, side)
    ev = "afcq-eri-rsa-2026-10-05"
    slug = "atc-afcq-eri-rsa-2026-10-05-eri"
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", ev)
        try:
            for intent, sn in ((LONG, "yes"), (SHORT, "no")):
                await _row(conn, event=ev, slug=slug,
                           st="soccer_team_full_time_winner", abbr="eri",
                           intent=intent, side=sn,
                           title="Eritrea vs. South Africa")
            got = await PO.observation_pass(
                conn, candidates=[(slug, LONG)], quoter=counting,
                prose_reader=HW._prose_reader(), settlement_reader=_reader,
                now=time.time())
            assert got["attempted"] == 0
            assert got["not_attempted"][PO.X_FIRST_LEG_ONE_PARTICIPANT] == 1
            assert slug not in calls
        finally:
            await conn.execute("DELETE FROM us_premap WHERE event_slug=$1",
                               ev)


@pg
async def test_a_structurally_pairable_catalogue_candidate_is_attempted_first():
    async with _conn() as conn:
        await _clean(conn)
        await _seed_the_catalogue(conn)
        pairable = {"us_market_slug": HW.HELD, "side": LONG,
                    "fixture": HW.EVENT,
                    "structural_verdict": PO.S_PAIRABLE,
                    "source": PO.SOURCE_CATALOGUE}
        got = await PO.observation_pass(
            conn, candidates=[("aec-mlb-sea-tex-2026-10-05", LONG)],
            catalogue=[pairable], quoter=HW._quoter(PRICES),
            prose_reader=HW._prose_reader(), settlement_reader=_reader,
            now=time.time(), per_pass=1)
        assert got["observed"][0]["us_market_slug"] == HW.HELD, got
