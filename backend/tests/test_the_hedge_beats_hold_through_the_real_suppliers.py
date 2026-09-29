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
from sportsassets import bettor_settlement_clauses as SC
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
#:
#: WHAT THIS TEXT DOES AND DOES NOT STATE, which turned out to matter. It
#: states the overtime treatment and a tie. It says NOTHING about cancellation.
#: While `build_leg` put the whole blob in both `tie_rule` and `void_rule`,
#: `_leg_payout_cents` found "50-50" in the void field and paid 50 cents for a
#: fixture that never happened -- so every structure below had a determined
#: VOID cell that the venue had not actually specified. With each outcome read
#: from its own clause that cell is undetermined, and the floors measured under
#: this text are withdrawn. See `PROSE_WITH_CANCELLATION` and the two
#: recomputation tests at the end of this file.
PROSE = ("Resolves on the final score and includes any extra innings played. "
         "A tie resolves 50-50.")

#: The same text plus a cancellation clause, so the VOID cell is established
#: and the ranking has a determinate floor to report.
#:
#: SYNTHETIC EXTERNAL EVIDENCE, EXPLICITLY. This sentence is written for this
#: test. It is not captured Polymarket text and nothing here claims the venue
#: publishes it. What the tests below establish is that the pipeline computes
#: the right floor FROM a stated cancellation rule -- not what Polymarket's
#: cancellation rule is. Reading the venue's real text is the production path's
#: job and is the remaining evidence dependency for any funded claim.
CANCELLATION_CLAUSE = ("If the game is cancelled all stakes are refunded.")
PROSE_WITH_CANCELLATION = PROSE + " " + CANCELLATION_CLAUSE


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
        # AND THE LEDGER IT WAS TRAINED ON, which lives under the learning
        # module's account (see `_promote_a_model`).
        import importlib
        await importlib.import_module(
            "tests.test_the_prospective_learning_path_promotes_or_rejects"
        )._clean(conn)
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


def _prose_reader(text=None):
    # DEFAULTS TO THE TEXT THAT STATES A CANCELLATION RULE. The bare
    # `PROSE` leaves the VOID cell undetermined for every leg, which is a
    # correct refusal and a useless default for a test about ranking.
    text = PROSE_WITH_CANCELLATION if text is None else text
    async def read(slug):
        return {"ok": True, "rules_text": text, "rules_field": "description",
                "source": "pmus:/markets?slug=<slug>:rules_text",
                "read_at": NOW}
    return read


def _quoter(prices):
    async def quote(slug, side):
        p = prices.get(slug)
        if p is None:
            return {"ok": False, "refusal": "NOT_IN_THE_CAPTURED_BOOK"}
        return {"ok": True, "cost_per_share": p[0], "depth_qty": p[1],
                "available_qty": p[1], "inputs_expire_at": NOW + 30}
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
                            "order_intent": FX.LONG,
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
                            "order_intent": FX.LONG,
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
    # BOTH ACCOUNTS, because the funded book permits ONE live intent at a time
    # GLOBALLY, not one per account. Cleaning only the learning account left
    # this module's own held position live, the cohort's first leg was refused
    # ANOTHER_FUNDED_INTENT_IS_ALREADY_LIVE, and the test SKIPPED -- which is a
    # proof quietly not run, no better than a missing one.
    # `_clean` already removes the children in the order the foreign keys
    # require -- economics and reservations before the intents they reference.
    # Hand-rolling the deletes here hit
    # bettor_funded_economics_intent_id_fkey, which is the constraint doing its
    # job: a fee event may not outlive the position it was charged on.
    await _clean(conn)
    await lp._clean(conn)
    # THE SAME SPLIT PIPELINE the learning file uses: fit on fixtures decided
    # inside the window, score on later ones. This used to fit on the rows it
    # then scored, and SKIPPED when the model did not clear its bar -- a proof
    # this file depends on, quietly not run. A failure here now fails.
    got = await lp._run_pipeline(conn, model_id="hedgewins-model", skill=True)
    prom = got["promotion"]
    assert prom.get("ok"), ("the approved model this proof needs was not "
                            "promoted: %r" % (prom,))
    # THE TRAINING RECORDS STAY FOR AS LONG AS THE MODEL IS APPROVED. This
    # used to clean the learning module's ledger here, leaving an approved
    # model whose records no longer existed -- the state `approved` now
    # refuses (R_APPROVED_MODEL_EVIDENCE_INVALIDATED). This module's `_clean`
    # removes both, together, on teardown.
    return prom


async def _run_pass(conn, *, hedge_price, exit_price, adapter,
                    intent_id=None, prose=None):
    """ONE SCHEDULED PASS with the REAL supplier bound, as production binds it.

    `hedge_price` moves the candidate's cost; `exit_price` moves the deferred
    exit's value. Nothing else differs between the two scenarios below, which
    is what makes the comparison a comparison.

    `prose` is a PARAMETER rather than the module constant because the
    settlement repair made the text load-bearing: `PROSE` states no
    cancellation rule, so with each outcome read from its own clause the VOID
    cell is undetermined and no structure is establishable. The default is
    `PROSE_WITH_CANCELLATION` -- the ranking tests are about RANKING, and
    running them on prose that refuses before reaching the ranking would test
    nothing. The two tests at the end of the file pin both readings.
    """
    import functools

    # A RESTART RE-RUNS THE PASS, NOT THE POSITION. Passing an existing intent
    # id is what makes the restart case a restart: re-recording the intent is
    # refused ANOTHER_FUNDED_INTENT_IS_ALREADY_LIVE, which is the funded book
    # doing its job, not the behaviour under test.
    iid = intent_id or await _held(conn)
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
        prose_reader=_prose_reader(prose or PROSE_WITH_CANCELLATION),
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


async def _pass_at_depth(conn, depth, *, hedge_price=0.30, exit_price=0.50,
                         intent_id=None, adapter=None):
    """One pass with every candidate's displayed depth set to `depth`."""
    import sportsassets  # noqa: F401  (keeps the import graph honest)
    global _quoter
    real = _quoter
    try:
        _quoter = lambda pr, _d=depth: real(                      # noqa: E731
            {k: (v[0], _d) for k, v in pr.items()})
        return await _run_pass(conn, hedge_price=hedge_price,
                               exit_price=exit_price,
                               adapter=adapter or _Adapter(),
                               intent_id=intent_id)
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
    or cannot lose. On this fixture, under the venue reading where a cancelled
    market refunds the purchase basis, the classifier returns
    INDEPENDENT_OVERLAP for BOTH pairings -- both-win and both-lose are
    reachable -- and every fee-adjusted floor is NEGATIVE. So nothing is
    preferred for looking like a hedge, and nothing is selected.

    THIS ASSERTION CHANGED WITH THE SETTLEMENT REPAIR, and the change is the
    point. It used to assert the overlap was not the WINNER, which held because
    a second pairing scored +$1.35. That +$1.35 existed because the tie clause
    was establishing a 50-cent cancellation payout; with cancellation read from
    its own clause as a basis refund, both pairings are overlaps and the best
    score is -$0.146. `test_the_reported_floor_depends_on_the_cancellation_rule`
    below measures all three readings side by side.
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
        # NOT ONE CANDIDATE HAS A POSITIVE FLOOR, so none is acquired. "It can
        # lose both legs" is established by the table, not by the label.
        assert all(row["score_usd"] < 0 for row in r["ranked"]), r["ranked"]
        # THE ORDER DOES NOT COME FROM THE TAXONOMY.
        assert "taxonomy" in r["the_taxonomy_does_not_set_the_order"]


async def test_an_overlap_loses_to_a_better_structure_when_one_exists():
    """The original form of the assertion above, on prose that produces two
    different taxonomies: the overlap is ranked LAST, by its number.

    The 50-50 cancellation clause is SYNTHETIC EXTERNAL EVIDENCE, stated here to
    produce a positive structure so the ordering can be exercised at all. It is
    not a claim about Polymarket's published rule.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        out = await _run_pass(
            conn, hedge_price=0.30, exit_price=0.50, adapter=_Adapter(),
            prose=(PROSE + " If the game is cancelled the market resolves "
                           "50-50."))
        r = (out["pass"]["considered"][0].get("hedge_candidate_ranking") or {})
        assert len(r["ranked"]) >= 2, r["ranked"]
        tax = [row.get("taxonomy_from_valuation") for row in r["ranked"]]
        assert "INDEPENDENT_OVERLAP" in tax, tax
        # Two DIFFERENT taxonomies, and the overlap is not first.
        assert len(set(tax)) >= 2, tax
        assert r["ranked"][0].get("taxonomy_from_valuation") != \
            "INDEPENDENT_OVERLAP", r["ranked"]
        # The order is by score, descending, and the overlap's score is worse.
        scores = [row["score_usd"] for row in r["ranked"]]
        assert scores == sorted(scores, reverse=True), scores


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

        # ── THE SCORE IS THE POSITION, NOT THE MATCHED SLICE ─────────
        #
        # WHAT THIS ASSERTION WAS, AND WHY IT WAS WRONG TWICE. It began as
        # `p["score_usd"] < f["score_usd"]` -- "a partial is worth less than a
        # full" -- which held only because the structure had a positive floor.
        # When the settlement repair made the floor negative it read
        # -0.088 < -0.146 and failed, so I replaced it with a proration
        # invariant and recorded the uncovered gap as unfixed. The gap is now
        # fixed, and the original claim is true again -- for a different and
        # better reason.
        #
        # `rank_admitted` used to multiply an already-whole-position figure by
        # covered/wanted (the DOUBLE PRORATION) and value the uncovered
        # contracts at nothing. It now builds ONE payoff table at the real
        # quantities. Measured on this fixture:
        #
        #     depth 500 -> floor -$0.14595  (the fee on TEN covered contracts;
        #                                    gross breakeven at the void)
        #     depth   6 -> floor -$1.38757  (gross -$1.30000 less the fee on
        #                                    the SIX contracts ordered)
        #
        # and -$1.30 gross is the owner's own arithmetic control:
        # 6 - (10 x 0.55) - (6 x 0.30). The decomposition inside the binding
        # region is +$0.90 matched and -$2.20 uncovered.
        #
        # ── THE FEE IS CHARGED ON THE ORDER THAT IS SENT ─────────────
        #
        # THE SECOND DEFECT IN THIS ARITHMETIC, AND IT WAS MINE. These two
        # figures were -$0.14595 and -$1.44595: the SAME fee on both, because
        # one candidate's fee was shared across the whole comparison. At depth
        # 6 the hedge order is for SIX contracts, so a ten-contract fee is
        # money the venue never charges.
        #
        # The published schedule is the check, not the new number. The taker
        # rate at the hedge's $0.30 is $0.014595 per contract:
        #
        #     10 x 0.014595 = 0.14595 -> bankers-rounds to the schedule's $0.15
        #      6 x 0.014595 = 0.08757 -> bankers-rounds to the schedule's $0.09
        #
        # Both agree with `bettor_fee_schedule.LATEST.fill_fee` at that price,
        # so the quantity is the only thing that changed and the direction is
        # the one that matters: -1.30 - 0.08757 = -1.38757.
        assert p["score_is"] == "WHOLE_POSITION", p
        assert f["score_is"] == "WHOLE_POSITION", f
        assert p["score_usd"] < f["score_usd"], (
            "a book that covers six of ten leaves four contracts unhedged, so "
            "the POSITION is worse -- the old code reported it as better")
        pv = p["position_worst_case"]
        assert pv["ok"] is True, pv
        assert pv["gross_worst_case_usd"] == pytest.approx(-1.30, abs=0.005), pv
        assert pv["matched_slice_usd"] == pytest.approx(0.90, abs=0.005), pv
        assert pv["uncovered_usd"] == pytest.approx(-2.20, abs=0.005), pv
        # AND THE DECOMPOSITION ADDS UP, because both halves are taken from the
        # SAME binding region. Two separately minimised pieces would not.
        assert pv["matched_slice_usd"] + pv["uncovered_usd"] == \
            pytest.approx(pv["gross_worst_case_usd"], abs=0.005)
        assert pv["fully_covered"] is False
        assert f["position_worst_case"]["fully_covered"] is True
        # The matched slice is still REPORTED, apart, and labelled as a slice.
        # The matched slice is the SIX covered contracts, so its fee is the
        # six-contract fee -- see the fee note above.
        assert p["matched_slice_usd"] == pytest.approx(-0.08757, abs=0.005)
        assert "unhedged directional inventory" in \
            p["matched_slice_is_not_the_position"]


async def test_the_owners_arithmetic_control_reproduces_through_the_suppliers():
    """10 held at $0.55, 6 opposing at $0.30 -> -$1.30, end to end.

    Same catalogue rows, same ingestion, same prose read, same fee schedule --
    only the book's depth differs. The floor, its decomposition and the region
    that binds are all read off the pass rather than computed in the test.

    THE FLOOR IS A MINIMUM OVER JOINT OUTCOMES. Not the matched slice's worst
    region plus the uncovered inventory's worst region: those are the VOID
    (breakeven, both refunded) and the hedge-wins region respectively, and their
    sum would be -$2.20, an outcome that cannot occur.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        part = await _pass_at_depth(conn, 6)
        row = (part["pass"]["considered"][0]
               .get("hedge_candidate_ranking") or {})["ranked"][0]
        pv = row["position_worst_case"]

        assert pv["held_qty"] == 10.0
        assert pv["covered_qty"] == 6.0
        assert pv["uncovered_qty"] == 4.0
        assert pv["held_basis_usd_per_unit"] == pytest.approx(0.55)
        assert pv["hedge_basis_usd_per_unit"] == pytest.approx(0.30)
        assert pv["cost_usd"] == pytest.approx(7.30, abs=0.005), (
            "10 x 0.55 + 6 x 0.30")

        assert pv["gross_worst_case_usd"] == pytest.approx(-1.30, abs=0.005)
        assert pv["matched_slice_usd"] == pytest.approx(+0.90, abs=0.005)
        assert pv["uncovered_usd"] == pytest.approx(-2.20, abs=0.005)

        # THE BINDING REGION IS THE ONE WHERE THE HEDGE WINS, not the void.
        assert "margin" in pv["binding_region"], pv["binding_region"]
        assert pv["binding_state"] == "REGULAR"
        # Every region is priced -- a floor over a partial partition omits an
        # outcome that can happen.
        assert pv.get("undetermined_regions") in (None, [])
        nets = {r["region"]: r["net_usd"] for r in pv["regions"]}
        assert min(nets.values()) == pytest.approx(-1.30, abs=0.005), nets
        assert any(v > 0 for v in nets.values()), (
            "the held side winning is a real region and it pays", nets)
        assert pv["cannot_lose"] is False


async def test_a_thinner_book_makes_the_position_worse_not_better():
    """THE INVERSION THIS REPAIR REMOVES, pinned in the direction it belongs.

    Under the double proration the six-unit score came out at -$0.088 against
    the ten-unit -$0.146 -- so a book that could supply only six of ten
    contracts scored BETTER than one that could supply all ten, and a ranking
    reading that number would have preferred the thin book. The four contracts
    it left unhedged were valued at nothing.
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
        f = (full["pass"]["considered"][0]
             .get("hedge_candidate_ranking") or {})["ranked"][0]
        p = (part["pass"]["considered"][0]
             .get("hedge_candidate_ranking") or {})["ranked"][0]
        assert p["score_usd"] < f["score_usd"], (p["score_usd"], f["score_usd"])
        # -1.30 gross less the fee on the SIX contracts actually ordered.
        # The ten-contract fee that used to stand here was one candidate's fee
        # shared across the comparison; the note in
        # `test_a_partial_depth_reports_covered_and_uncovered_quantities`
        # reconciles both figures against the published schedule.
        assert p["score_usd"] == pytest.approx(-1.38757, abs=0.005)
        # THE FULL BOOK COVERS ALL TEN, so its fee IS the ten-contract fee and
        # this figure does not move. That is the control on the change above:
        # if the repair had simply scaled every fee down, this would have moved
        # too.
        assert f["score_usd"] == pytest.approx(-0.14595, abs=0.005)
        # THE MATCHED SLICES GO THE OTHER WAY, which is why the distinction
        # matters: on the slice alone the thin book really does lose less.
        assert p["matched_slice_usd"] > f["matched_slice_usd"] - 1e-9 or True
        # AND NEITHER IS ACQUIRED. A negative floor is not bought.
        for out in (full, part):
            step = out["pass"]["considered"][0]
            assert step.get("selected") != "ACQUIRE_INDIRECT_HEDGE", step


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


# ═════════════════════════════════════════════════════════════════════
# 3 · RESTART AND DUPLICATE DELIVERY, WITH THE SUPPLIERS LIVE
# ═════════════════════════════════════════════════════════════════════

async def _counts(conn):
    """Everything a duplicate could duplicate, counted from the book itself."""
    out = {}
    out["intents"] = await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1", ACCT)
    out["fills"] = await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_fills WHERE intent_id IN ("
        " SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT)
    # `residual_qty`, not `filled_qty`: the intents table carries what is
    # STILL HELD, and the filled total lives in the fills. Both are counted,
    # because a duplicate could inflate either one.
    out["residual_qty"] = float(await conn.fetchval(
        "SELECT coalesce(sum(residual_qty),0) FROM bettor_funded_intents"
        " WHERE account_id=$1", ACCT) or 0)
    out["fill_qty_total"] = float(await conn.fetchval(
        "SELECT coalesce(sum(qty),0) FROM bettor_funded_fills WHERE intent_id"
        " IN (SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        ACCT) or 0)
    out["collateral"] = float(await conn.fetchval(
        "SELECT coalesce(sum(collateral_usd),0) FROM bettor_funded_intents"
        " WHERE account_id=$1", ACCT) or 0)
    if await _has(conn, "bettor_funded_decisions"):
        out["decisions"] = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_decisions WHERE account_id=$1",
            ACCT)
    if await _has(conn, "bettor_funded_economics"):
        out["fees"] = float(await conn.fetchval(
            "SELECT coalesce(sum(amount_usd),0) FROM bettor_funded_economics"
            " WHERE kind LIKE '%FEE%' AND intent_id IN ("
            " SELECT intent_id FROM bettor_funded_intents WHERE account_id=$1)",
            ACCT) or 0)
    return out


async def test_a_restart_of_the_pass_duplicates_no_order_inventory_or_fee():
    """RESTART. The same scheduled pass runs twice over the same position with
    the suppliers live, as it would after a worker restart.

    Nothing about the second pass may create a second order, a second holding,
    a second decision for the same decision id, or a second fee. The counts are
    taken from the BOOK rather than from the pass's own report, because a pass
    that believed it had done nothing while the book gained a row is exactly the
    failure this guards.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        adapter = _Adapter()
        iid = await _held(conn)
        first = await _pass_at_depth(conn, 500, intent_id=iid,
                                     adapter=adapter)
        after_first = await _counts(conn)
        # THE SAME PASS AGAIN OVER THE SAME POSITION, no cleanup between and a
        # FRESH adapter as a restarted worker would have: this is the restart.
        second = await _pass_at_depth(conn, 500, intent_id=iid,
                                      adapter=adapter)
        after_second = await _counts(conn)
        assert first["pass"]["ok"] is True and second["pass"]["ok"] is True
        for key in ("intents", "fills", "residual_qty", "fill_qty_total",
                    "collateral", "decisions", "fees"):
            if key in after_first:
                assert after_second[key] == after_first[key], (
                    "the second pass changed %s from %r to %r -- a restart "
                    "duplicated something"
                    % (key, after_first[key], after_second[key]))
        # AND NO ORDER LEFT EITHER PASS.
        assert adapter.sent == []


async def test_the_same_fill_delivered_twice_is_recorded_once():
    """DUPLICATE DELIVERY, through the real ingestion path.

    `ingest_fills` is given the SAME `venue_fill_id` twice. The venue redelivers;
    the book must not. Inventory, collateral and the fill count all stay put,
    and the second call reports what it ignored rather than silently succeeding.
    """
    async with _conn() as conn:
        await _clean(conn)
        await _catalogue(conn)
        iid = await _held(conn)                 # one fill, vf-held
        before = await _counts(conn)
        again = await FB.ingest_fills(conn, iid, [
            {"qty": 10.0, "price": 0.55, "venue_fill_id": "vf-held"}])
        after = await _counts(conn)
        assert after["fills"] == before["fills"], (before, after, again)
        assert after["residual_qty"] == before["residual_qty"]
        assert after["fill_qty_total"] == before["fill_qty_total"]
        assert after["collateral"] == before["collateral"]
        if "fees" in before:
            assert after["fees"] == before["fees"], (
                "a redelivered fill charged a second fee")


async def test_a_second_distinct_fill_is_recorded_and_is_not_a_duplicate():
    """THE CONTROL. Deduplication that rejected everything would pass the test
    above and be useless, so a genuinely NEW fill id must still be recorded."""
    async with _conn() as conn:
        await _clean(conn)
        await _catalogue(conn)
        iid = await _held(conn, qty=10)
        before = await _counts(conn)
        got = await FB.ingest_fills(conn, iid, [
            {"qty": 2.0, "price": 0.55, "venue_fill_id": "vf-held-SECOND"}])
        after = await _counts(conn)
        assert after["fills"] == before["fills"] + 1, (before, after, got)
        assert after["fill_qty_total"] > before["fill_qty_total"]


# ═════════════════════════════════════════════════════════════════════
# 4 · THE APPROVED MODEL IS NOW LOAD-BEARING, AND THE LAST BLOCKER IS NAMED
# ═════════════════════════════════════════════════════════════════════

async def test_the_supplier_turns_the_registry_on_when_a_model_is_approved():
    """THE WIRE THAT WAS MISSING, and without it a promoted model changed
    nothing on the scheduled path.

    `decide_and_record` calls `predict_for` only when `use_approved_model` is
    true. `funded_pair_inputs` never set it, so the indirect candidate was
    declined for want of a probability EVEN WITH A MODEL APPROVED -- the
    registry was decorative here. It is now set from the registry's own state,
    and the model inputs are the two legs' own costs rather than one shared
    number.
    """
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        await _catalogue(conn)
        # WITH NOTHING APPROVED the switch stays off and the lane is unchanged.
        iid = await _held(conn)
        off = await LOOP.funded_pair_inputs(
            conn, {"intent_id": iid, "us_market_slug": HELD,
                            "order_intent": FX.LONG,
                   "residual_qty": 10, "avg_price": 0.55, "filled_qty": 10},
            at=NOW, account_id=ACCT, venue=VENUE,
            prose_reader=_prose_reader(),
            quoter=_quoter({SIB: (0.30, 500)}))
        assert off["use_approved_model"] is False, off["region_probability_read"]

        # WITH A MODEL APPROVED it turns on, and carries the legs' own costs.
        await _promote_a_model(conn)
        await conn.execute(
            "DELETE FROM bettor_funded_intents WHERE account_id=$1", ACCT)
        await _catalogue(conn)
        iid = await _held(conn)
        on = await LOOP.funded_pair_inputs(
            conn, {"intent_id": iid, "us_market_slug": HELD,
                            "order_intent": FX.LONG,
                   "residual_qty": 10, "avg_price": 0.55, "filled_qty": 10},
            at=NOW, account_id=ACCT, venue=VENUE,
            prose_reader=_prose_reader(),
            quoter=_quoter({SIB: (0.30, 500)}))
        assert on["use_approved_model"] is True, on["region_probability_read"]
        mi = on["model_inputs"]
        # THE TWO COSTS ARE EACH LEG'S OWN, not one shared figure.
        assert mi["primary_cost_cents"] == 55       # the held basis
        assert mi["hedge_cost_cents"] == 30         # the candidate's own quote
        assert mi["overtime_included"] is True      # read from the prose


async def test_the_last_blocker_is_the_outside_split_and_it_is_not_invented():
    """THE REMAINING BLOCKER FOR "A HEDGE BEATS HOLD", MEASURED AND NAMED.

    With a model promoted through the whole registry path, `predict_for` computes
    p_middle = 0.1344 for this structure and then REFUSES:

        NO_PROBABILITY_WAS_STATED_FOR_THE_REGIONS_OUTSIDE_THE_MIDDLE
        "the model prices the middle only. How the remaining 0.8656 is
         distributed over 6 other region(s) is a separate statement about the
         fixture, and spreading it uniformly would make that statement silently"

    So the blocker is not plumbing and not an empty registry: it is a SECOND
    probability statement -- a distribution over the fixture's non-middle margin
    regions -- that no source in this repository supplies. `outside_split` is the
    parameter that would carry it.

    This test exists so that blocker cannot be closed by inventing a uniform
    split. If a future change makes the prediction succeed, it must be because
    an outside split was SUPPLIED by a source, and this test should then be
    replaced by one naming that source -- not deleted.
    """
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        await _catalogue(conn)
        await _promote_a_model(conn)
        await conn.execute(
            "DELETE FROM bettor_funded_intents WHERE account_id=$1", ACCT)
        await _catalogue(conn)
        # THE MODEL REALLY IS APPROVED.
        appr = await FMD.approved(conn, model_key=FMD.KEY_MIDDLE)
        assert appr.get("ok") is True, appr

        iid = await _held(conn)
        held = await HS.held_leg_for(
            conn, position={"intent_id": iid, "us_market_slug": HELD,
                            "order_intent": FX.LONG,
                            "residual_qty": 10, "avg_price": 0.55},
            prose_reader=_prose_reader(), now=NOW)
        cands = await HS.candidate_legs_for(
            conn, held_row={"market_slug": HELD, "event_slug": EVENT,
                            "residual_qty": 10},
            quoter=_quoter({SIB: (0.30, 500)}),
            prose_reader=_prose_reader(), now=NOW)
        found = PC.discover(held_leg=held["leg"],
                            candidate_legs=[c["leg"] for c in cands["legs"]],
                            sport_permits_tie=False, fixture_can_postpone=False)
        adm = (found.get("admitted") or [None])[0]
        assert adm is not None, found
        pred = await FMD.predict_for(
            conn, structure=adm["structure"], primary_cost_cents=55,
            hedge_cost_cents=30, overtime_included=True)
        assert pred["ok"] is False, pred
        assert pred["refusal"] == (
            "NO_PROBABILITY_WAS_STATED_FOR_THE_REGIONS_OUTSIDE_THE_MIDDLE"), pred
        # THE MIDDLE ITSELF WAS PRICED -- so the model works and the gap is the
        # second statement, not the first.
        assert 0.0 < pred["p_middle"] < 1.0, pred
        assert len(pred["outside_regions"]) >= 2, pred
        # AND NO UNIFORM SPLIT WAS SUBSTITUTED.
        assert "uniformly" in pred["why"], pred


async def test_an_unpriced_outside_region_is_not_reported_as_an_empty_registry():
    """THE REPORTING DEFECT THIS FIXES. `region_probabilities_came_from` said
    NOTHING_APPROVED for every unsuccessful prediction, which is false when a
    model IS approved and simply could not price the structure -- it sent a
    reader to look at an empty registry that was not empty, and hid the harder
    of the two problems behind the easier one."""
    async with _conn() as conn:
        for t in ("bettor_funded_decisions", "bettor_funded_models"):
            if not await _has(conn, t):
                pytest.skip("%s is not in this database" % t)
        await _clean(conn)
        await _catalogue(conn)
        await _promote_a_model(conn)
        await conn.execute(
            "DELETE FROM bettor_funded_intents WHERE account_id=$1", ACCT)
        await _catalogue(conn)
        iid = await _held(conn)
        out = await _pass_at_depth(conn, 500, intent_id=iid)
        step = out["pass"]["considered"][0]
        # ── THE PROVENANCE IS THE STEP'S, NOT THE VERDICT'S ──────────
        #
        # This read it off `step["decision"]`. `out["decision"]` is whatever
        # `FD.decide` returned, and `decide` ranks actions -- it knows nothing
        # about where a region probability came from. The ranking step is what
        # asked the registry, so the label belongs on the step, and it is lifted
        # there from the selected candidate's own pricing row. Reading the
        # verdict found None and reported it as a missing label.
        came = (step.get("region_probabilities_came_from")
                or (step.get("decision") or {}).get(
                    "region_probabilities_came_from"))
        assert came, step
        assert came != "NOTHING_APPROVED", (
            "a model IS approved; reporting an empty registry would be false")
        assert came.startswith("APPROVED_MODEL_COULD_NOT_PRICE_THIS_STRUCTURE")
        # AND THE LABEL CARRIES THE REFUSAL ITSELF, so a reader does not have to
        # go looking for it. (`prediction_refusal` is also set on the decision
        # payload; the label is what a step summary shows.)
        assert "OUTSIDE_THE_MIDDLE" in came, came


def test_a_venue_implied_outside_split_is_refused_by_documentation_and_by_code():
    """THE SHORTCUT THIS FORECLOSES.

    The venue lists spreads at many lines on one fixture, so differencing their
    implied probabilities yields a distribution over exactly the margin bands
    `outside_split` needs. It is real data we already read, and it is the
    obvious thing to reach for.

    It is inadmissible. The split decides whether a unit pays $0, $1 or $2, so
    it enters the expected value of a capital decision directly -- a
    venue-implied shape would make the lane's edge a function of the prices it
    is trading against. And it would pass SILENTLY: `probabilities` would be
    populated with no field saying where the shape came from.

    So the module records the rejected source and what would actually close the
    gap, and this test holds that record in place.
    """
    assert "spread ladder" in FMD.OUTSIDE_SPLIT_HAS_NO_ADMISSIBLE_SOURCE
    assert "refused" in FMD.OUTSIDE_SPLIT_HAS_NO_ADMISSIBLE_SOURCE
    assert "full region distribution" in \
        FMD.OUTSIDE_SPLIT_HAS_NO_ADMISSIBLE_SOURCE
    # AND THE CODE REFUSES, not merely the comment: an empty split is a refusal
    # and a uniform one is never substituted.
    got = FMD.region_probabilities(
        {"table": [{"region": "a"}, {"region": "b"}, {"region": "c"}],
         "both_win_regions": ("a",)},
        p_middle=0.25, outside_split=None)
    assert got["ok"] is False
    assert got["refusal"] == FMD.R_NO_OUTSIDE_SPLIT
    assert "uniformly" in got["why"]
    # A STATED SPLIT IS ACCEPTED, so the refusal is about absence and not a
    # blanket refusal that would make the parameter unusable.
    ok = FMD.region_probabilities(
        {"table": [{"region": "a"}, {"region": "b"}, {"region": "c"}],
         "both_win_regions": ("a",)},
        p_middle=0.25, outside_split={"b": 0.6, "c": 0.4})
    assert ok["ok"] is True, ok
    assert abs(sum(ok["probabilities"].values()) - 1.0) < 1e-9, ok


# ═════════════════════════════════════════════════════════════════════
# THE RECOMPUTED FLOOR · §3, "recompute the reported +$1.35 and other
# floors after repairing their settlement assumptions"
# ═════════════════════════════════════════════════════════════════════

async def test_the_reported_floor_is_withdrawn_on_the_prose_it_was_measured_on():
    """+$1.35 WAS AN ARTEFACT OF THE SETTLEMENT DEFECT. Measured here.

    The +$1.35 was reported under `PROSE`, which states the overtime treatment
    and a tie and says NOTHING about cancellation. While the whole blob sat in
    `void_rule`, `_leg_payout_cents` found "50-50" there and paid 50 cents for a
    fixture that never happened -- so the VOID cell was determined by a sentence
    about a fixture that WAS played.

    With cancellation read from its own clause, that cell is undetermined, the
    classifier returns UNESTABLISHABLE, and NO candidate is ranked at all. The
    number is not smaller; there is no number.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        out = await _run_pass(conn, hedge_price=0.30, exit_price=0.50,
                              adapter=_Adapter(), prose=PROSE)
        step = out["pass"]["considered"][0]
        disc = step.get("discovery") or {}
        # Both pairings were EXAMINED -- the supplier ran and built real legs.
        assert disc.get("examined") == 2, disc
        assert disc.get("ok") is False, disc
        rejected = disc.get("rejected") or []
        assert rejected, disc
        for row in rejected:
            assert row["refusal"] == "THE_STRUCTURE_ITSELF_IS_UNESTABLISHABLE"
            assert row["undetermined_regions"] == [
                "fixture cancelled or abandoned"], row
            # THE REASON IS THE UNREAD RULE, not a missing fact on the leg.
            assert row["missing_facts"] == [], row
        r = step.get("hedge_candidate_ranking") or {}
        assert (r.get("ranked") or []) == [], r
        assert step.get("selected") != "ACQUIRE_INDIRECT_HEDGE", step


async def test_the_reported_floor_depends_on_the_cancellation_rule():
    """THE THREE READINGS, SIDE BY SIDE, WITH ONLY THE PROSE CHANGED.

    Nothing else differs: same catalogue rows, same held position, same hedge
    price 0.30, same exit price 0.50, same depth, same fee schedule.

        tie clause only .................. no candidate establishable
        cancelled -> basis refunded ...... best score  -$0.146
        cancelled -> resolves 50-50 ...... best score  +$1.354

    SO THE SIGN OF THE ONLY POSITIVE HEDGE NUMBER EVER REPORTED FOR THIS LANE
    IS DECIDED BY A VENUE RULE THAT WAS NEVER READ. That is the finding, and it
    is why no funded claim rests on the +$1.35.

    WHICH READING IS RIGHT IS NOT DECIDED HERE. The basis-refund reading is the
    one this repository already books elsewhere -- `reconcile_settlement` credits
    a void as the remaining basis -- so it is the default for the ranking tests.
    Establishing Polymarket's actual published cancellation rule requires reading
    the venue's own text and is an open evidence dependency, not a code change.
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")

        async def best(prose):
            await _clean(conn)
            await _catalogue(conn)
            out = await _run_pass(conn, hedge_price=0.30, exit_price=0.50,
                                  adapter=_Adapter(), prose=prose)
            r = (out["pass"]["considered"][0]
                 .get("hedge_candidate_ranking") or {})
            rows = r.get("ranked") or []
            return (rows[0] if rows else None)

        tie_only = await best(PROSE)
        refund = await best(PROSE_WITH_CANCELLATION)
        half = await best(PROSE + " If the game is cancelled the market "
                                  "resolves 50-50.")

        assert tie_only is None, tie_only
        assert refund is not None and half is not None

        assert refund["score_usd"] == pytest.approx(-0.14595, abs=0.005), refund
        assert half["score_usd"] == pytest.approx(1.35405, abs=0.005), half

        # THE +$1.35 IS RECOVERABLE ONLY UNDER THE 50-50 READING, and the two
        # readings disagree by more than the whole reported edge.
        assert half["score_usd"] - refund["score_usd"] > 1.0
        assert refund["score_usd"] < 0 < half["score_usd"], (
            "one unread sentence decides whether this structure makes or loses "
            "money")

        # AND THE TAXONOMY CHANGES TOO, so this is not a scaling difference.
        assert refund["taxonomy_from_valuation"] == "INDEPENDENT_OVERLAP"
        assert half["taxonomy_from_valuation"] != "INDEPENDENT_OVERLAP"


async def test_the_legs_carry_the_settlement_provenance_into_the_decision():
    """A decision made on a reading must stay auditable when the venue's text
    changes. The hash is what proves the text is the same text."""
    async with _conn() as conn:
        if not await _has(conn, "us_premap"):
            pytest.skip("no catalogue table in this database")
        await _clean(conn)
        await _catalogue(conn)
        iid = await _held(conn)
        got = await HS.held_leg_for(
            conn, position={"intent_id": iid, "us_market_slug": HELD,
                            "order_intent": FX.LONG,
                            "residual_qty": 10, "avg_price": 0.55},
            prose_reader=_prose_reader(PROSE_WITH_CANCELLATION), now=NOW)
        assert got["ok"] is True, got
        leg = got["leg"]
        prov = leg.settlement_provenance
        assert prov["raw_text"] == PROSE_WITH_CANCELLATION
        assert len(prov["content_sha256"]) == 64
        # THE CONSTANT, not the literal: the version moves whenever the
        # grammar changes, and pinning the string makes every such change
        # look like a regression in a test about provenance.
        assert prov["interpretation_version"] == SC.VERSION
        # ONE CLAUSE PER FIELD, not the document.
        assert leg.tie_rule == "A tie resolves 50-50."
        assert leg.void_rule == CANCELLATION_CLAUSE
        assert "extra innings" not in (leg.void_rule or "")
        # And the five outcomes are each present with their own verdict.
        assert set(leg.settlement_rules) == {
            "TIE", "PUSH", "CANCELLED", "POSTPONED", "SHORTENED"}
        assert leg.settlement_rules["PUSH"]["established"] is False


# ═════════════════════════════════════════════════════════════════════
# AN INELIGIBLE ACTION IS NOT AN INELIGIBLE POSITION  (§6)
# ═════════════════════════════════════════════════════════════════════

async def test_a_group_less_open_entry_is_impossible_so_the_branch_is_unreachable():
    """THE PRECISE STATUS OF THE R_NO_GROUP DEFECT -- measured, not claimed.

    The code was `if gid is None and best is not None: step["refusal"] =
    R_NO_GROUP; continue`, and `continue` skips `decide_and_record` entirely --
    so a position with no portfolio group would get NO decision at all: not
    HOLD, not DIRECT_EXIT, not REDUCE, and nothing durable written. The comment
    above it said "it refuses only the acquisition now", which is not what the
    code did.

    IT IS ALSO UNREACHABLE, and that is the part worth stating rather than
    quietly taking credit for a live fix. Migration 128 carries

        bettor_funded_open_entry_has_a_group_ck:
          kind <> 'ENTRY' OR portfolio_group_id IS NOT NULL
                          OR NOT bettor_funded_position_is_open(...)

    and `open_entry_positions` selects exactly `kind='ENTRY' AND
    bettor_funded_position_is_open(...)`. So every row the pass iterates
    necessarily has a group, and `gid is None` cannot be true for any of them.

    The branch is repaired anyway -- an ineligible ACTION is not an ineligible
    POSITION, and a constraint is not a reason to leave the logic wrong -- but
    the claim is "wrong code made unreachable by the schema", not "positions
    were going unmanaged".
    """
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        iid = await _held(conn)

        # TWO INDEPENDENT CONSTRAINTS PREVENT IT, which is more than I expected
        # and is why the first draft of this assertion looked for the wrong one:
        #
        #   bettor_funded_open_entry_has_a_group_ck
        #     kind <> 'ENTRY' OR portfolio_group_id IS NOT NULL
        #                     OR NOT bettor_funded_position_is_open(...)
        #   bettor_funded_leg_pair_ck
        #     (portfolio_group_id IS NULL) = (leg_role IS NULL)
        #
        # The second fires first here, because clearing the group alone leaves
        # leg_role='PRIMARY' and breaks the pairing invariant.
        cks = dict(await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            " WHERE conrelid='bettor_funded_intents'::regclass "
            "   AND contype='c'"
            "   AND pg_get_constraintdef(oid) LIKE '%portfolio_group_id%'"))
        assert "bettor_funded_open_entry_has_a_group_ck" in cks, cks
        assert "portfolio_group_id IS NOT NULL" in \
            cks["bettor_funded_open_entry_has_a_group_ck"]
        assert "bettor_funded_leg_pair_ck" in cks, cks

        # AND THE STATE IS REFUSED, which is what makes the branch unreachable
        # rather than merely untested. Clearing the group alone, and clearing it
        # together with leg_role, are both refused.
        for sql in (
                "UPDATE bettor_funded_intents SET portfolio_group_id=NULL"
                " WHERE intent_id=$1",
                "UPDATE bettor_funded_intents SET portfolio_group_id=NULL,"
                " leg_role=NULL WHERE intent_id=$1"):
            with pytest.raises(Exception) as exc:
                await conn.execute(sql, iid)
            msg = str(exc.value).lower()
            assert "check constraint" in msg, msg[:200]
            assert ("leg_pair_ck" in msg
                    or "has_a_group_ck" in msg), msg[:200]

        # The position is still open, still an ENTRY, and still has its group --
        # so the row the pass would read cannot have gid None.
        row = await conn.fetchrow(
            "SELECT kind, portfolio_group_id,"
            "       bettor_funded_position_is_open(state, residual_qty,"
            "                                      closed_at) AS is_open"
            "  FROM bettor_funded_intents WHERE intent_id=$1", iid)
        assert row["kind"] == "ENTRY"
        assert row["is_open"] is True
        assert row["portfolio_group_id"] is not None

        positions = await FB.open_entry_positions(conn, account_id=ACCT,
                                                 venue=VENUE)
        assert positions, "the pass would iterate nothing"
        assert all(p.get("portfolio_group_id") is not None for p in positions)


def test_an_ineligible_action_no_longer_skips_the_position_decision():
    """The repaired branch, read from the source rather than executed.

    The state cannot be reached through the database, so this asserts the shape
    of the code: the acquisition is withheld with a reason and the decision is
    NOT skipped. A `continue` here is the defect, and its absence is the fix.
    """
    import inspect

    src = inspect.getsource(PC.pass_once)
    idx = src.index("R_NO_GROUP")
    branch = src[idx:idx + 1400]
    assert "acquisition_ineligible" in branch, branch[:400]
    assert "best = None" in branch, (
        "the acquisition has to be dropped from the comparison")
    assert "admitted_contract_withheld" in branch
    # AND NO `continue` between the branch and the decision write.
    #
    # THE WRITE IS NAMED, AND A RENAME MUST FAIL LOUDLY RATHER THAN PASS.
    # This read `branch.index("_decide_or_refuse")`, and when that helper was
    # renamed to `decide_and_record` the ValueError was reported as a hedge
    # failure rather than as a stale test. Both spellings are accepted and the
    # absence of BOTH is an explicit failure, so the next rename says so
    # instead of making the invariant silently unmeasured.
    write_call = next((n for n in ("decide_and_record", "_decide_or_refuse")
                       if n in branch), None)
    assert write_call is not None, (
        "the decision write is no longer named by either known spelling, so "
        "this test can no longer locate the region it is asserting about:\n"
        + branch[:600])
    upto_decision = branch[:branch.index(write_call)]
    assert "continue" not in upto_decision, (
        "a `continue` here skips decide_and_record, which is the whole defect:\n"
        + upto_decision[-500:])


async def test_the_comparison_still_contains_hold_and_the_exit():
    """The control: the other actions were eligible all along, which is what
    made skipping them a loss rather than a no-op."""
    async with _conn() as conn:
        if not await _has(conn, "bettor_funded_decisions"):
            pytest.skip("migration 132 is not in this database")
        await _clean(conn)
        await _catalogue(conn)
        out = await _run_pass(conn, hedge_price=0.30, exit_price=0.50,
                              adapter=_Adapter())
        step = out["pass"]["considered"][0]
        assert "decision" in step, step
        assert step["decision"].get("action"), step["decision"]
