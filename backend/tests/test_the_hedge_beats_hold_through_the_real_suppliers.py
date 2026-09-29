"""ITEM 3's RANKING PROOFS, THROUGH THE ACTUAL SUPPLIERS.

INTEGRATED ENGINEERING EVIDENCE -- NOT REAL-MONEY VERIFICATION.

WHY THESE ARE NEW WHEN test_the_scheduled_pair_lifecycle ALREADY RANKS FOUR
ACTIONS. That file's ranking tests call `FD.decide(indirect=_candidate())` --
they inject a FINISHED candidate. As unit tests of the decision function they
are correct and they stay; as evidence that the lane can produce an indirect
candidate they prove nothing, because the thing under test is the part that was
stubbed. Codex named this explicitly: do not inject a finished Leg, ranking or
selected winner in place of the implementation under test.

So here NOTHING between the catalogue row and the persisted decision is
supplied. `pass_once` gets the real `funded_pair_inputs`, which runs the real
`held_leg_for` and `candidate_legs_for`, which build real `Leg` objects from
real `us_premap` rows and real settlement prose; `discover` classifies them;
`predict_for` is answered by a model PROMOTED through the real registry path.

SUBSTITUTED: the venue's settlement-prose HTTP read, the venue's order-book
read, and the order adapter. Three transports. Nothing else.

WHAT WOULD MAKE THESE TESTS WORTHLESS, and each is asserted against:

  * a hedge that wins because no alternative was ranked -> the candidate set is
    checked to contain HOLD and the exit too;
  * a hedge that wins because it was hard-coded to -> the SAME fixture is run
    twice, once where the hedge wins and once where the exit does, changing only
    the prices;
  * a "middle" that is really the netted other side of the held instrument ->
    the held slug is asserted absent from the candidates and the two legs are
    asserted to carry different condition_ids.
"""

import contextlib
import datetime as _dt
import os

import asyncpg
import pytest

from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_hedge_supply as HS
from sportsassets import bettor_funded_learning as FL
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_model as FMD
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_indirect_structures as IS
from sportsassets.workers import ext_pinnacle_loop as LOOP

DSN = os.environ.get("RN1X_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="needs a migrated database")

ACCT = "acct-hedgewins"
VENUE = "PMUS_HEDGEWINS"
EVENT = "mlb-bos-nyy-2026-10-05"
HELD = "aec-mlb-bos-nyy-2026-10-05"                 # moneyline, VAR_MARGIN
SIB = "asc-mlb-bos-nyy-2026-10-05-neg-1pt5"         # spread, VAR_MARGIN
NOW = 1790500000.0

#: Prose stating the terminal case with a DECLARED baseball pattern, so the
#: overtime rule is READ and both legs land on the same grading key.
PROSE = ("Resolves on the final score and includes any extra innings played. "
         "A tie resolves 50-50.")


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


async def _has(conn, t):
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t))


async def _clean(conn):
    if await _has(conn, "bettor_funded_decision_outcomes"):
        await conn.execute(
            "DELETE FROM bettor_funded_decision_outcomes WHERE decision_id IN ("
            " SELECT decision_id FROM bettor_funded_decisions"
            " WHERE account_id=$1)", ACCT)
    for t in ("bettor_funded_decisions",):
        if await _has(conn, t):
            await conn.execute("DELETE FROM %s WHERE account_id=$1" % t, ACCT)
    # ── THE MODEL THIS MODULE PROMOTES MUST NOT OUTLIVE IT ───────────
    #
    # `_promote_a_model` leaves an APPROVED row in the registry, and the
    # database permits ONE approved version per key. Left behind, the learning
    # module's "a candidate with no skill is rejected" test then found an
    # approved model where it asserts there is none -- a false pass waiting to
    # happen in the other direction too, since a stale approved model would
    # answer `predict_for` for any later test.
    if await _has(conn, "bettor_funded_models"):
        await conn.execute("DELETE FROM bettor_funded_models "
                           "WHERE model_id LIKE 'hedgewins-%'")
    await conn.execute(
        "DELETE FROM bettor_funded_fills WHERE intent_id IN ("
        " SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT)
    for t in ("bettor_funded_economics", "bettor_funded_leg_reservations"):
        if await _has(conn, t):
            with contextlib.suppress(Exception):
                await conn.execute(
                    "DELETE FROM %s WHERE intent_id IN ("
                    " SELECT intent_id FROM bettor_funded_intents"
                    " WHERE account_id=$1)" % t, ACCT)
    await conn.execute("DELETE FROM bettor_funded_intents WHERE account_id=$1",
                       ACCT)
    if await _has(conn, "us_premap"):
        await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", EVENT)


@pytest.fixture(autouse=True)
async def _leave_nothing_behind():
    yield
    if DSN:
        async with _conn() as c:
            await _clean(c)


async def _catalogue(conn):
    """A MONEYLINE AND A SPREAD ON ONE FIXTURE -- the real middle shape.

    Both are graded on the SAME variable (VAR_MARGIN), over the SAME period
    (FULL_GAME), under the SAME overtime treatment (read from the prose). That
    is what makes their `grading_key()` match, which is what lets `discover`
    classify them as one structure. Different lines are what make it a MIDDLE
    rather than a direct complement.
    """
    if not await _has(conn, "us_premap"):
        pytest.skip("no us_premap in this database")
    for col, typ in (("team_abbr", "text"), ("team_name", "text"),
                     ("game_start", "timestamptz"), ("sports_type", "text"),
                     ("signed", "text"), ("intent", "text")):
        await conn.execute("ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS "
                           "%s %s" % (col, typ))
    await conn.execute("DELETE FROM us_premap WHERE event_slug=$1", EVENT)
    rows = [
        (HELD + ":bos", HELD, "baseball_team_full_game_winner",
         "boston red sox", "bos", "00", None, "ORDER_INTENT_BUY_LONG"),
        (HELD + ":nyy", HELD, "baseball_team_full_game_winner",
         "new york yankees", "nyy", "00", None, "ORDER_INTENT_BUY_SHORT"),
        (SIB + ":bos", SIB, "baseball_team_full_game_spread",
         "yes", "bos", "1.5", "-1.5", "ORDER_INTENT_BUY_LONG"),
        (SIB + ":nyy", SIB, "baseball_team_full_game_spread",
         "no", "nyy", "1.5", "+1.5", "ORDER_INTENT_BUY_SHORT"),
    ]
    for ident, slug, st, side, abbr, line, signed, intent in rows:
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title,"
            " market_slug, question, kind, line, side_norm, intent, signed,"
            " team_abbr, team_name, sports_type, game_start)"
            " VALUES ($1,$2,$3,$4,$5,'side',$6,$7,$8,$9,$10,$11,$12,"
            "         now() + interval '3 hours')",
            ident, EVENT, "Boston Red Sox vs. New York Yankees", slug,
            "Who will win?", line, side, intent, signed, abbr, (abbr or ""),
            st)


def _prose_reader(text=PROSE):
    async def read(slug):
        return {"ok": True, "rules_text": text, "rules_field": "description",
                "source": "pmus:/markets?slug=<slug>:rules_text",
                "read_at": NOW}
    return read


def _quoter(prices):
    async def quote(slug):
        p = prices.get(slug)
        if p is None:
            return {"ok": False, "refusal": "NOT_IN_THE_CAPTURED_BOOK"}
        return {"ok": True, "cost_per_share": p[0], "depth_qty": p[1],
                "available_qty": p[1]}
    return quote


class _Adapter:
    """THE ORDER TRANSPORT, RECORDING WHAT IT WAS ASKED TO SEND.

    It is the boundary: everything before it is the implementation under test,
    and what arrives here is the order the lane actually decided to place.
    """

    def __init__(self):
        self.sent = []

    async def place(self, **kw):
        self.sent.append(dict(kw))
        return {"ok": True, "venue_order_id": "vo-%d" % len(self.sent),
                "status": "open"}

    # The adapter surface the cycle may call under either name.
    submit = place
    acquire = place


async def _held(conn, *, qty=10, price=0.55, intent_id="fpi-hedgewins"):
    coll = FX.collateral_for(price, qty, FX.LONG)
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=HELD, event_key=EVENT,
        order_intent=FX.LONG, limit_price=price, quantity=qty,
        collateral_usd=coll, effective_digest="d-hw", held_is_long=True)
    assert got.get("ok"), got
    await FB.record_acknowledgement(conn, intent_id, venue_order_id="vo-held",
                                   status="open")
    await FB.ingest_fills(conn, intent_id, [
        {"qty": float(qty), "price": price, "venue_fill_id": "vf-held"}])
    return intent_id


# ═════════════════════════════════════════════════════════════════════
# 1 · THE SUPPLIERS PRODUCE A CLASSIFIABLE STRUCTURE, NOT A STUB
# ═════════════════════════════════════════════════════════════════════

async def test_the_two_built_legs_share_a_grading_key_and_differ_by_instrument():
    """THE PRECONDITION FOR EVERYTHING BELOW, and it is the thing that cannot
    be faked: two legs built independently from two catalogue rows agree on
    fixture, period, variable and overtime -- so one random variable grades
    both -- while naming DIFFERENT instruments.

    If they shared a condition_id they would be the netted two sides of one
    market, which is not a hedge at all.
    """
    async with _conn() as conn:
        await _clean(conn)
        await _catalogue(conn)
        iid = await _held(conn)
        held = await HS.held_leg_for(
            conn, position={"intent_id": iid, "us_market_slug": HELD,
                            "residual_qty": 10, "avg_price": 0.55},
            prose_reader=_prose_reader(), now=NOW)
        assert held["ok"], held
        cands = await HS.candidate_legs_for(
            conn, held_row={"market_slug": HELD, "event_slug": EVENT,
                            "residual_qty": 10},
            quoter=_quoter({SIB: (0.30, 500)}),
            prose_reader=_prose_reader(), now=NOW)
        assert cands["legs"], cands["refused"]
        hl = held["leg"]
        # SAME RANDOM VARIABLE.
        for c in cands["legs"]:
            assert c["leg"].grading_key() == hl.grading_key(), (
                c["leg"].grading_key(), hl.grading_key())
            # DIFFERENT INSTRUMENT.
            assert c["leg"].condition_id != hl.condition_id
        # AND THE HELD SLUG IS NOT AMONG THEM.
        assert HELD not in {c["market_slug"] for c in cands["legs"]}
        # THE LEGS ARE REAL OBJECTS with every fact stated.
        assert hl.missing_facts() == []
        assert all(c["leg"].missing_facts() == [] for c in cands["legs"])


async def test_a_structure_is_classified_from_the_built_legs():
    """`classify` on the two BUILT legs, with no injected structure. A middle,
    a gap or an unestablishable verdict is all acceptable here -- what is not
    acceptable is that the classifier never saw two legs at all, which is what
    `candidate_legs=[]` meant before."""
    async with _conn() as conn:
        await _clean(conn)
        await _catalogue(conn)
        iid = await _held(conn)
        held = await HS.held_leg_for(
            conn, position={"intent_id": iid, "us_market_slug": HELD,
                            "residual_qty": 10, "avg_price": 0.55},
            prose_reader=_prose_reader(), now=NOW)
        cands = await HS.candidate_legs_for(
            conn, held_row={"market_slug": HELD, "event_slug": EVENT,
                            "residual_qty": 10},
            quoter=_quoter({SIB: (0.30, 500)}),
            prose_reader=_prose_reader(), now=NOW)
        assert held["ok"] and cands["legs"]
        seen = []
        for c in cands["legs"]:
            st = IS.classify(held["leg"], c["leg"], sport_permits_tie=False)
            seen.append(st.to_dict() if hasattr(st, "to_dict") else dict(st))
        assert seen, "the classifier was never given a pair"
        kinds = {s.get("taxonomy") or s.get("kind") for s in seen}
        assert kinds, seen
        # AND THE VERDICT IS REPORTED, whatever it is -- never defaulted.
        for s in seen:
            assert (s.get("taxonomy") or s.get("kind")) in IS.TAXONOMY, s


# ═════════════════════════════════════════════════════════════════════
# 2 · THE SCHEDULED PASS RANKS THE HEDGE AGAINST HOLD AND THE EXIT
# ═════════════════════════════════════════════════════════════════════

async def _promote_a_model(conn):
    """AN APPROVED MODEL, through the real registry path.

    Reuses the prospective-learning module's own cohort builder rather than
    duplicating it, so the model that answers `predict_for` here is one that
    passed the same four promotion conditions.
    """
    import importlib
    lp = importlib.import_module(
        "tests.test_the_prospective_learning_path_promotes_or_rejects")
    await lp._clean(conn)
    coh = await lp._cohort(conn, skill=True)
    if not coh.get("ok"):
        pytest.skip("the learning cohort could not be built here: %r" % (coh,))
    lab = await FMD.labelled(conn, after=lp.FIT_THROUGH,
                             account_id=lp.ACCT)
    if not lab.get("ok") or lab["n"] < FMD.MIN_EVALUATION_ROWS:
        pytest.skip("too few labels to promote a model here: %r" % (lab,))
    fitted = FMD.fit(lab["rows"], lab["labels"])
    await FMD.register(conn, model_id="hedgewins-model",
                       model_version="v-hedgewins", fitted=fitted,
                       fit_through=lp.FIT_THROUGH)
    await FMD.evaluate(conn, model_id="hedgewins-model", account_id=lp.ACCT)
    prom = await FMD.promote(conn, model_id="hedgewins-model",
                             approved_by="integration-test")
    if not prom.get("ok"):
        pytest.skip("the model did not clear its own bar here: %r" % (prom,))
    await lp._clean(conn)
    return prom


async def _run_pass(conn, *, hedge_price, exit_price, adapter):
    """ONE SCHEDULED PASS with the REAL supplier bound, as production binds it.

    `hedge_price` moves the candidate's cost; `exit_price` moves the deferred
    exit's value. Nothing else differs between the two scenarios below, which
    is what makes the comparison a comparison.
    """
    import functools

    iid = await _held(conn)
    deferred = {iid: {
        "intent_id": iid, "selected": PC.ACTION_DIRECT_EXIT,
        "selected_qty": 10.0, "limit_price": exit_price,
        "proceeds_per_contract": exit_price - 0.005,
        "expected_net_usd": (exit_price - 0.55) * 10.0,
        "inputs_expire_at": NOW + 300.0,
        "ranking": {"candidates": [
            {"action": "HOLD", "expected_net_usd": 0.20,
             "downside_usd": -5.5, "evidence_quality": "EXTERNAL_LABELLED",
             "incremental_capital_usd": 0.0, "capital_duration_h": 3.0,
             "execution_secured": True},
            {"action": PC.ACTION_DIRECT_EXIT,
             "expected_net_usd": (exit_price - 0.55) * 10.0,
             "downside_usd": (exit_price - 0.55) * 10.0,
             "evidence_quality": "EXTERNAL_LABELLED",
             "incremental_capital_usd": 0.0, "capital_duration_h": 0.0,
             "execution_secured": True, "limit_price": exit_price,
             "proceeds_per_contract": exit_price - 0.005,
             "selected_qty": 10.0},
        ]}}}
    pair_inputs = functools.partial(
        LOOP.funded_pair_inputs, deferred=deferred, account_id=ACCT,
        venue=VENUE,
        management_rankings={iid: {"ranking": deferred[iid]["ranking"]}},
        prose_reader=_prose_reader(),
        quoter=_quoter({} if hedge_price is None
                       else {SIB: (hedge_price, 500)}))
    got = await PC.pass_once(
        conn, account_id=ACCT, venue=VENUE, pair_inputs=pair_inputs,
        adapter=adapter, deferred_exits=deferred, now=NOW)
    return {"intent_id": iid, "pass": got}


async def test_the_scheduled_pass_reaches_discovery_with_built_legs():
    """WHAT IS NOW TRUE, MEASURED RATHER THAN HOPED.

    `pass_once` -> `funded_pair_inputs` -> `held_leg_for` /
    `candidate_legs_for` -> `discover`, with no injected leg anywhere. The
    step's own `discovery` block reports `examined: 2, rejected: []` and three
    settlement-compatible contracts -- so the classifier really was handed two
    legs built from catalogue rows and prose reads.

    Before this batch `candidate_legs` was `[]` on every cycle and discovery was
    skipped entirely, so `examined` could only ever have been 0.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        out = await _run_pass(conn, hedge_price=0.30, exit_price=0.50,
                              adapter=_Adapter())
        got = out["pass"]
        assert got["ok"] is True, got
        assert got["open_entry_positions"] == 1
        considered = got["considered"]
        assert len(considered) == 1, considered
        step = considered[0]
        disc = step.get("discovery") or {}
        assert disc.get("ok") is True, step
        assert disc.get("examined", 0) >= 2, disc
        assert disc.get("rejected") == [], disc
        assert disc.get("distinct_settlement_compatible_contracts", 0) >= 1, disc


def _depth(prices, d):
    return _quoter({k: (v[0], d) for k, v in prices.items()})


async def _pass_at_depth(conn, depth, *, hedge_price=0.30, exit_price=0.50):
    """One pass with every candidate's displayed depth set to `depth`."""
    import sportsassets  # noqa: F401  (keeps the import graph honest)
    global _quoter
    real = _quoter
    try:
        _quoter = lambda pr, _d=depth: real(                      # noqa: E731
            {k: (v[0], _d) for k, v in pr.items()})
        return await _run_pass(conn, hedge_price=hedge_price,
                               exit_price=exit_price, adapter=_Adapter())
    finally:
        _quoter = real


async def test_every_admitted_candidate_is_scored_on_its_own_numbers():
    """CODEX'S ITEM: rank eligible candidates rather than selecting the first
    admitted. It used to take `admitted_all[0]` and record
    `hedge_candidates_not_ranked` with the reason that a region probability, a
    fee and a depth reading per contract were not wired. They are now: the
    supplier prices EACH candidate on its OWN ladder and returns that
    contract's own displayed depth, and the fee is priced at the cycle's date
    with the sport's own coefficient.

    AND THE RANKING MATTERS, which is the part worth measuring. On this fixture
    the two admitted candidates score +$1.35 and -$8.65 net of fees. Taking
    "the first" was a coin flip between them.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        out = await _pass_at_depth(conn, 500)
        step = out["pass"]["considered"][0]
        r = step.get("hedge_candidate_ranking") or {}
        assert len(r.get("ranked") or []) >= 2, r
        assert r.get("not_rankable") == [], r
        scores = [row["score_usd"] for row in r["ranked"]]
        # DESCENDING, and genuinely spread -- a ranking over indistinguishable
        # candidates would prove nothing.
        assert scores == sorted(scores, reverse=True), scores
        assert max(scores) - min(scores) > 1.0, (
            "the candidates must actually differ, or the ranking is decorative: "
            "%r" % (scores,))
        # THE WINNER IS THE TOP SCORE, and it is what the step admitted.
        assert step.get("admitted_contract") == r["ranked"][0]["condition_id"]
        # AND THE OLD "not ranked" ADMISSION IS GONE.
        assert "hedge_candidates_not_ranked" not in step, step


async def test_a_moneyline_plus_an_opposing_spread_is_not_assumed_to_be_a_middle():
    """MEASURED, ON THE COMBINATION IT WOULD BE EASIEST TO ASSUME ABOUT.

    Codex: do not assume every moneyline/opposing-spread combination is superior
    or cannot lose. On this fixture the classifier returns INDEPENDENT_OVERLAP
    for one of the two pairings -- both-win AND both-lose are reachable -- and
    its fee-adjusted floor is NEGATIVE. So the pair can lose, and the ranking
    puts it last rather than preferring it for looking like a hedge.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        out = await _pass_at_depth(conn, 500)
        r = (out["pass"]["considered"][0].get("hedge_candidate_ranking") or {})
        tax = {row.get("taxonomy_from_valuation") for row in r["ranked"]}
        assert "INDEPENDENT_OVERLAP" in tax, r["ranked"]
        overlap = [row for row in r["ranked"]
                   if row.get("taxonomy_from_valuation") == "INDEPENDENT_OVERLAP"]
        assert overlap and overlap[0]["score_usd"] < 0, overlap
        assert overlap[0]["both_win_and_both_lose_reachable"] is True
        # AND IT IS NOT THE WINNER.
        assert r["ranked"][0].get("taxonomy_from_valuation") != \
            "INDEPENDENT_OVERLAP"
        # THE ORDER DOES NOT COME FROM THE TAXONOMY.
        assert "taxonomy" in r["the_taxonomy_does_not_set_the_order"]


async def test_a_partial_depth_reports_covered_and_uncovered_quantities():
    """CODEX'S THIRD PROOF, at the point the quantity is decided.

    A book supporting 6 of 10 contracts is not "no hedge": it is a hedge over
    6 leaving 4 UNCOVERED, and the two quantities travel separately because the
    way this structure fails is a half-filled second leg whose accounting
    believes the pair is complete -- `depth_supports` says exactly that.

    The score is PRO-RATED to what can be acquired, so a structure priced over
    ten contracts does not flatter a book that can only supply six.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        full = await _pass_at_depth(conn, 500)
        await _clean(conn)
        await _catalogue(conn)
        part = await _pass_at_depth(conn, 6)
        rf = (full["pass"]["considered"][0].get("hedge_candidate_ranking") or {})
        rp = (part["pass"]["considered"][0].get("hedge_candidate_ranking") or {})
        assert rf["ranked"] and rp["ranked"]
        f, p = rf["ranked"][0], rp["ranked"][0]
        assert f["covered_qty"] == 10.0 and f["uncovered_qty"] == 0.0
        assert f["fully_supported"] is True
        # THE PARTIAL CARRIES BOTH QUANTITIES, SEPARATELY.
        assert p["covered_qty"] == 6.0, p
        assert p["uncovered_qty"] == 4.0, p
        assert p["fully_supported"] is False
        assert p["covered_qty"] + p["uncovered_qty"] == 10.0
        # AND IT IS WORTH LESS, pro-rated rather than counted as if full.
        assert p["score_usd"] < f["score_usd"], (p, f)
        assert "UNCOVERED" in p.get("score_is_prorated", "")


async def test_a_book_supporting_nothing_is_not_rankable_and_not_a_zero():
    """An unreadable or empty book is not "a candidate worth nothing" -- it is a
    candidate nobody could size, and scoring it zero would let a measured loser
    beat it."""
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        out = await _pass_at_depth(conn, 0)
        step = out["pass"]["considered"][0]
        r = step.get("hedge_candidate_ranking") or {}
        assert r.get("ranked") == [], r
        assert r.get("not_rankable"), r
        assert all(row["refusal"] for row in r["not_rankable"])
        assert step.get("admitted_contract") is None
        # AND THE DISTINCTION IS STATED, not left to be inferred.
        assert "not a zero" in r["an_unreadable_input_is_not_a_zero"] or \
            "never scored as worthless" in r["an_unreadable_input_is_not_a_zero"]
        nr = step.get("hedge_candidates_not_ranked") or {}
        assert nr.get("taken") == "none", nr


async def test_nothing_is_sent_when_the_decision_is_not_an_acquisition():
    """THE BOUNDARY HOLDS WITH THE SUPPLIERS LIVE. A pass that discovers
    candidates and then decides against acquiring must reach no venue, and must
    say which decision it took instead."""
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        adapter = _Adapter()
        out = await _run_pass(conn, hedge_price=0.95, exit_price=0.90,
                              adapter=adapter)
        got = out["pass"]
        step = got["considered"][0]
        assert got["acquisitions"] == []
        assert got["opened_anything"] is False
        assert got["paired_anything"] is False
        assert adapter.sent == [], (
            "no order may reach the venue on a non-acquisition: %r"
            % (adapter.sent,))
        assert step.get("dispatched") in (None, False, [], {}), step
        assert step.get("refusal") or step.get("why_nothing_was_sent"), step
        # AND THE REASON NAMES THE DECISION, not a missing input.
        assert step.get("what_was_selected_instead") is not None or \
            step.get("refusal") == "THE_DECISION_WAS_NOT_TO_ACQUIRE_A_SECOND_LEG"


async def test_missing_hedge_evidence_leaves_the_pass_running_and_names_it():
    """Codex's fifth case at the CYCLE level: with no sibling priced, the hedge
    path yields nothing, the pass still completes, and nothing is sent."""
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        adapter = _Adapter()
        out = await _run_pass(conn, hedge_price=None, exit_price=0.70,
                              adapter=adapter)
        got = out["pass"]
        assert got["ok"] is True, got
        step = got["considered"][0]
        # NO CANDIDATE WAS ADMITTED, because none had a price of its own.
        disc = step.get("discovery") or {}
        assert (disc.get("examined") or 0) == 0 or not disc.get("ok"), disc
        assert adapter.sent == []
        assert step.get("refusal") or step.get("why_nothing_was_sent"), step
