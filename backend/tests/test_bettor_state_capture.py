"""The unselected prospective capture: what makes it unselected.

Owner directive 2026-09-20, "APPROVED -- START THE READ-ONLY
PROSPECTIVE PMUS UNSELECTED CAPTURE":

    §1 "Do not call the resulting quantity
       UNCONDITIONAL_MAKER_ADVERSE_SELECTION."
    §2 "Never substitute A for C. Never substitute A for D."
    §3 "Freeze the sampling rule BEFORE row 1. ... Selection must be
       independent of future economics."
    §4 "Do not change these after seeing outcomes without creating a
       new version."
    §5 "Do not silently manufacture missing values."
    §6 "Do not call any future observation a fill."

The defect this dataset exists to avoid is subtle enough to be worth
naming: a sampling rule that looks neutral but reads something about
the market before deciding whether to record it. Every such rule
conditions the frame on whatever it read. The tests below check that
the selection function cannot see a book, that an unreadable book
still lands, and that the name of the resulting statistic cannot
overstate what was identified.
"""

import io
import tokenize
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import bettor_state_capture as sc


def _code_only(src: str) -> str:
    """The source with comments and string literals removed.

    Every one of these tests asks what the CODE does. Scanning raw text
    instead answers a different question -- whether the prose mentions
    the thing -- and a module that carefully documents what it refuses
    to do fails its own guard. That is not hypothetical: the first
    version of this file failed on the sentence "reads no book", and an
    earlier workflow guard in this repo failed on a docstring
    disclaiming credentials.
    """
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)

T0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

BOOK = {"bids": [{"px": {"value": "0.52"}, "qty": "120"}],
        "offers": [{"px": {"value": "0.55"}, "qty": "60"}],
        "stats": {"sharesTraded": "900"},
        "state": "MARKET_STATE_OPEN"}


def _subject(i=1, **kw):
    s = {"symbol": "aec-%d" % i, "marketId": "aec-%d" % i,
         "eventId": "ev-%d" % i, "identifier": "id-%d" % i,
         "outcomeLeg": "yes", "kind": "nfl_moneyline",
         "sport": "football", "league": "nfl",
         "gameStart": "2026-09-20T16:00:00Z"}
    s.update(kw)
    return s


def _cands(n=240):
    return [{"identifier": "id-%d" % i} for i in range(n)]


# ── §3. SELECTION CANNOT SEE WHAT IT MUST NOT SEE ────────────────────

def test_selection_takes_no_book_no_price_and_no_outcome():
    """Stated as a property of the signature, not of the body: there is
    no argument through which economics could enter."""
    import inspect
    params = set(inspect.signature(sc.select).parameters)
    assert params == {"candidates", "at", "max_markets"}
    src = _code_only(inspect.getsource(sc.select)).lower()
    for forbidden in ("book", "spread", "mid", "depth", "outcome",
                      "settle", "volume", "price"):
        assert forbidden not in src, forbidden


def test_a_markets_slice_depends_only_on_its_identifier():
    assert sc.slice_of("id-7") == sc.slice_of("id-7")
    assert 0 <= sc.slice_of("id-7") < sc.ROTATION_SLICES


def test_selection_is_reproducible_from_identifier_and_clock_alone():
    a = sc.select(_cands(), at=T0)
    b = sc.select(list(reversed(_cands())), at=T0)
    assert [c["identifier"] for c in a["SELECTED"]] == \
           [c["identifier"] for c in b["SELECTED"]]


def test_every_market_is_reached_within_one_full_rotation():
    """A rotation that never reaches some markets would be a silent
    exclusion of exactly the kind the rule forbids."""
    seen = set()
    for k in range(sc.ROTATION_SLICES):
        at = T0 + timedelta(seconds=k * sc.SAMPLING_CADENCE_S)
        seen |= {c["identifier"]
                 for c in sc.select(_cands(), at=at,
                                    max_markets=10_000)["SELECTED"]}
    assert seen == {c["identifier"] for c in _cands()}


def test_the_rotation_period_is_not_commensurate_with_a_day():
    """THE SELECTION THAT ARRIVES BY ARITHMETIC. A period that divides
    or equals 24h samples every market at the same hour of the day
    forever -- so a market drawn at 03:00 UTC is never seen pregame.
    Time of day tracks kickoff times and liquidity, and liquidity
    tracks the economics being measured, so that is a selection on
    economics reached without anyone intending one."""
    day = sc.SECONDS_PER_DAY
    p = sc.FULL_ROTATION_S
    assert p % day != 0
    assert day % p != 0
    # And it must precess fast enough to sweep the clock in a usable
    # time rather than crawling round it over a season.
    drift = day % p
    assert 0 < drift < p
    assert day / max(1, min(drift, p - drift)) < 60   # days to sweep


def test_a_slice_fits_inside_the_per_cycle_cap_at_the_real_universe_size():
    """Otherwise the stable within-slice ordering would draw the SAME
    markets every rotation and the rest would never be sampled at all
    -- a fixed panel wearing a rotation's clothes."""
    eligible_legs = 9_700          # measured 2026-09-20
    assert eligible_legs / sc.ROTATION_SLICES < sc.MAX_MARKETS_PER_CYCLE


def test_truncation_is_recorded_rather_than_silent():
    s = sc.select(_cands(9_700), at=T0, max_markets=3)
    assert s["SLICE_TRUNCATED"] is True
    assert s["SLICE_TRUNCATED_BY"] == s["CANDIDATES_IN_SLICE"] - 3
    assert len(s["SELECTED"]) == 3


def test_the_forbidden_selection_grounds_are_named():
    for ground in ("RN1_TRADED_IT", "LATER_OUTCOME_WAS_INTERESTING",
                   "SPREAD_LOOKS_PROFITABLE",
                   "MAKER_ECONOMICS_LOOK_ATTRACTIVE"):
        assert ground in sc.SELECTION_MUST_NOT_DEPEND_ON


# ── §4. THE RULE IS FROZEN, AND ITS DRIFT IS DETECTABLE ──────────────

def test_every_declared_universe_field_is_answered():
    for field in ("UNIVERSE_VERSION", "ELIGIBILITY_RULE",
                  "MARKET_TYPES_INCLUDED", "MARKET_TYPES_EXCLUDED",
                  "SAMPLING_CADENCE", "MAX_MARKETS",
                  "MARKET_SELECTION_METHOD", "ROTATION_METHOD",
                  "TIME_TO_EVENT_REQUIREMENTS", "IDENTITY_REQUIREMENTS",
                  "BOOK_READABILITY_REQUIREMENTS", "RETENTION_POLICY"):
        assert field in sc.FROZEN_RULE, field
        assert str(sc.FROZEN_RULE[field]).strip(), field


def test_the_rule_sha_moves_when_the_rule_moves():
    before = sc.rule_sha()
    sc.FROZEN_RULE["ELIGIBILITY_RULE"] += " (edited)"
    try:
        assert sc.rule_sha() != before
    finally:
        sc.FROZEN_RULE["ELIGIBILITY_RULE"] = \
            sc.FROZEN_RULE["ELIGIBILITY_RULE"].replace(" (edited)", "")
    assert sc.rule_sha() == before


def test_time_to_event_filters_nothing():
    assert "NONE" in sc.FROZEN_RULE["TIME_TO_EVENT_REQUIREMENTS"]
    for gs in ("2026-09-20T16:00:00Z", "2026-09-20T08:00:00Z", None):
        r = sc.state_record(_subject(gameStart=gs), observed_at=T0,
                            book=BOOK)
        assert r["OBSERVATION_ID"]


def test_readability_is_not_an_inclusion_requirement():
    assert "NONE FOR INCLUSION" in \
        sc.FROZEN_RULE["BOOK_READABILITY_REQUIREMENTS"]


# ── the row that would have been dropped ─────────────────────────────

def test_an_unreadable_book_still_produces_a_row_with_a_reason():
    """THE ONE THAT MATTERS. Dropping these conditions the frame on
    readability, and readability tracks liquidity."""
    r = sc.state_record(_subject(), observed_at=T0, book=None,
                        read_error="HTTP_503")
    assert r["OBSERVATION_ID"]
    assert r["BOOK_READABILITY_STATUS"] == "UNREADABLE:HTTP_503"
    assert r["YES_BID"] == sc.NOT_IDENTIFIED
    assert sc.R_NO_BOOK in r["MISSING_FIELD_REASONS"]


# ── §5. NOTHING IS MANUFACTURED ──────────────────────────────────────

def test_an_absent_field_is_never_zero():
    r = sc.state_record(_subject(), observed_at=T0, book=None)
    for field in ("YES_BID", "YES_ASK", "SPREAD", "MID", "NO_BID",
                  "BOOK_IMBALANCE", "RECENT_PRICE_MOVE"):
        assert r[field] == sc.NOT_IDENTIFIED, field
        assert r[field] != "0", field


def test_a_first_sighting_has_no_recent_move_rather_than_a_zero_one():
    """Zero would say the market did not move. It says nothing of the
    kind: there is no earlier observation to move from."""
    r = sc.state_record(_subject(), observed_at=T0, book=BOOK, history=[])
    assert r["RECENT_PRICE_MOVE"] == sc.NOT_IDENTIFIED
    assert sc.R_NO_HISTORY in r["MISSING_FIELD_REASONS"]


def test_recent_move_appears_once_there_is_history():
    hist = [{"mid": "0.500", "observedAt": T0 - timedelta(seconds=300)}]
    r = sc.state_record(_subject(), observed_at=T0, book=BOOK,
                        history=hist)
    assert r["RECENT_PRICE_MOVE"] == "0.035"
    # Two points are not a volatility.
    assert r["REALISED_VOLATILITY"] == sc.NOT_IDENTIFIED


def test_the_complement_leg_is_never_derived_as_one_minus_yes():
    """The taker-pair measurement already refuted that identity on this
    venue: 0 of 3,732 observed pairs traded at or below par."""
    r = sc.state_record(_subject(), observed_at=T0, book=BOOK)
    assert r["NO_BID"] == sc.NOT_IDENTIFIED
    assert r["NO_ASK"] == sc.NOT_IDENTIFIED
    assert sc.R_SIBLING_NOT_READ in r["MISSING_FIELD_REASONS"]


def test_every_declared_state_field_is_present_on_every_row():
    for book in (BOOK, None):
        r = sc.state_record(_subject(), observed_at=T0, book=book)
        for field in ("OBSERVATION_ID", "OBSERVED_AT", "EVENT_ID",
                      "MARKET_ID", "INSTRUMENT_ID", "CONDITION_ID",
                      "SPORT", "LEAGUE", "MARKET_TYPE",
                      "TIME_TO_EVENT_S", "LIVE_STATUS", "YES_BID",
                      "YES_ASK", "YES_DEPTH", "NO_BID", "NO_ASK",
                      "NO_DEPTH", "SPREAD", "MID", "MULTI_LEVEL_DEPTH",
                      "BOOK_SOURCE_TIMESTAMP", "BOOK_RECEIVED_TIMESTAMP",
                      "BOOK_AGE_S", "IDENTITY_STATUS",
                      "BOOK_READABILITY_STATUS", "RECENT_PRICE_MOVE",
                      "REALISED_VOLATILITY", "BOOK_IMBALANCE",
                      "VENUE_STATE"):
            assert field in r, (field, book is None)


def test_one_row_per_market_per_bucket():
    a = sc.state_record(_subject(), observed_at=T0, book=BOOK)
    b = sc.state_record(_subject(),
                        observed_at=T0 + timedelta(seconds=120),
                        book=BOOK)
    c = sc.state_record(_subject(),
                        observed_at=T0 + timedelta(
                            seconds=sc.SAMPLING_CADENCE_S),
                        book=BOOK)
    assert a["OBSERVATION_ID"] == b["OBSERVATION_ID"]
    assert a["OBSERVATION_ID"] != c["OBSERVATION_ID"]


# ── §6. NO FUTURE OBSERVATION IS A FILL ──────────────────────────────

def test_the_state_row_says_no_order_exists():
    r = sc.state_record(_subject(), observed_at=T0, book=BOOK)
    assert r["FILL_STATUS"] == "NO_ORDER_EXISTS"
    assert "FILL" not in str(r["MID"])


def test_a_future_mid_is_labelled_as_not_a_fill():
    m = sc.mid_observation("bsv_x", horizon_s=300, observed_at=T0,
                           read_at=T0 + timedelta(seconds=304),
                           mid="0.56")
    assert "none of them is our fill" in m["isNotAFill"]
    assert "no column here may be read as one" in m["isNotAFill"]
    assert m["STATUS"] == "OBSERVED"


def test_the_actual_lag_is_recorded_not_the_nominal_horizon():
    m = sc.mid_observation("bsv_x", horizon_s=60, observed_at=T0,
                           read_at=T0 + timedelta(seconds=74), mid="0.56")
    assert m["ACTUAL_LAG_S"] == "74.0"
    assert m["HORIZON_S"] == 60
    assert m["WITHIN_TOLERANCE"] is True
    late = sc.mid_observation("bsv_x", horizon_s=60, observed_at=T0,
                              read_at=T0 + timedelta(seconds=400),
                              mid="0.56")
    assert late["WITHIN_TOLERANCE"] is False


def test_the_unobservable_horizons_are_refused_rather_than_interpolated():
    for h in sc.HORIZONS_NOT_OBSERVABLE_S:
        m = sc.mid_observation("bsv_x", horizon_s=h, observed_at=T0,
                               read_at=T0 + timedelta(seconds=h),
                               mid="0.56")
        assert m["MID"] == sc.NOT_IDENTIFIED, h
        assert "NOT_OBSERVABLE_AT_THIS_CADENCE" in m["STATUS"], h


def test_settlement_carries_its_semantics_status():
    s = sc.settlement_record("bsv_x", outcome="1",
                             settled_at="2026-09-20T20:00:00Z")
    assert s["SETTLEMENT_STATUS"] == sc.SETTLEMENT_RESOLVED
    assert s["SETTLEMENT_SEMANTICS_STATUS"] == \
        sc.SETTLEMENT_SEMANTICS_UNVERIFIED
    pending = sc.settlement_record("bsv_y")
    assert pending["SETTLEMENT_STATUS"] == sc.SETTLEMENT_PENDING


# ── §1/§2. THE NAME IS PART OF THE CLAIM ─────────────────────────────

def test_the_maker_adverse_selection_name_is_refused():
    with pytest.raises(sc.ForbiddenName):
        sc.forbidden_name("UNCONDITIONAL_MAKER_ADVERSE_SELECTION")
    with pytest.raises(sc.ForbiddenName):
        sc.forbidden_name("maker_ev")
    with pytest.raises(sc.ForbiddenName):
        sc.forbidden_name("BASE_CASE_EV")
    sc.forbidden_name(sc.MEASURED_QUANTITY)       # allowed
    sc.forbidden_name(sc.MEASURED_QUANTITY_ALIAS)


def test_the_permitted_name_does_not_claim_a_fill():
    assert "QUOTE_TO_SETTLEMENT" in sc.MEASURED_QUANTITY
    assert "ADVERSE" not in sc.MEASURED_QUANTITY
    assert "MAKER" not in sc.MEASURED_QUANTITY


def test_the_four_objects_are_kept_apart():
    assert sc.OBJECTS[sc.OBJECT_A]["measuredBy"] == "THIS_DATASET"
    for other in (sc.OBJECT_B, sc.OBJECT_C, sc.OBJECT_D):
        assert sc.OBJECTS[other]["status"] == sc.NOT_IDENTIFIED
        assert sc.OBJECTS[other]["measuredBy"] == "NOTHING_AVAILABLE"
    assert "Never substitute A for C" in sc.NEVER_SUBSTITUTE
    assert "Never substitute A for D" in sc.NEVER_SUBSTITUTE


def test_every_row_carries_what_it_does_not_measure():
    r = sc.state_record(_subject(), observed_at=T0, book=BOOK)
    assert r["measures"] == sc.OBJECT_A
    assert set(r["doesNotMeasure"]) == {sc.OBJECT_B, sc.OBJECT_C,
                                        sc.OBJECT_D}


# ── eligibility, and the categories excluded before collection ───────

def test_eligibility_reads_no_book_and_no_outcome():
    import inspect
    src = _code_only(inspect.getsource(sc.eligible)).lower()
    for forbidden in ("book", "spread", "depth", "outcome", "settle"):
        assert forbidden not in src, forbidden


def test_identity_is_required_and_non_sport_categories_are_excluded():
    ok, why = sc.eligible({"market_slug": "m", "event_slug": "e",
                           "side_norm": "yes", "kind": "nfl_moneyline"})
    assert ok and why is None
    no, why = sc.eligible({"market_slug": "m", "event_slug": "e",
                           "side_norm": "yes", "kind": "election_pres"})
    assert not no and "ELECTION" in why
    for missing in ("market_slug", "event_slug", "side_norm"):
        row = {"market_slug": "m", "event_slug": "e", "side_norm": "yes",
               "kind": "nfl_moneyline"}
        row[missing] = None
        assert sc.eligible(row)[0] is False


# ── the write path ───────────────────────────────────────────────────

def test_the_store_has_no_update_and_no_delete():
    """A state row written before any outcome existed must have no code
    path by which a later value edits it."""
    import inspect
    from sportsassets import bettor_state_store as ss
    # The SQL lives in string literals, so _code_only would remove the
    # very thing under test. Scan the literals themselves instead --
    # every statement this module can send is one of them.
    import ast
    src = inspect.getsource(ss)
    literals = [n.value.upper() for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    sql = [s for s in literals if "INSERT INTO" in s or "SELECT " in s]
    assert sql, "no statements found to check"
    # Word boundaries, because the column SLICE_TRUNCATED contains the
    # keyword TRUNCATE and a substring match would fail on it.
    import re
    for s in sql:
        for stmt in ("UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
            assert not re.search(r"\b%s\b" % stmt, s), (stmt, s[:120])
    assert all("ON CONFLICT DO NOTHING" in s or "SELECT " in s
               for s in sql)


def test_the_worker_has_no_order_path():
    import inspect
    from sportsassets.workers import bettor_state as w
    src = _code_only(inspect.getsource(w))
    for forbidden in ("order_submit", "place_order", "cancel_order",
                      "replace_order", "post_order", "create_order"):
        assert forbidden not in src, forbidden
    assert "mirrorLive" in inspect.getsource(w)


def test_the_sampling_pass_runs_once_per_bucket_not_once_per_tick():
    """The loop ticks at 60s and the cadence is 300s. Sampling on every
    tick would re-read the same markets five times, write four rows the
    primary key discards, and spend five times the venue budget."""
    import asyncio

    from sportsassets.workers import bettor_state as w

    class _Pool:
        def __init__(self):
            self.fetches = 0

        async def fetch(self, *a, **k):
            self.fetches += 1
            return []

    pool = _Pool()
    same = sc.bucket_of(datetime.now(tz=timezone.utc))
    first = asyncio.run(w.tick(pool, last_bucket=None))
    assert first["sampled"] is True
    assert first["bucket"] == same
    again = asyncio.run(w.tick(pool, last_bucket=first["bucket"]))
    assert again["sampled"] is False
    assert again["read"] == 0


def test_a_failed_sampling_pass_does_not_consume_its_bucket():
    import asyncio

    from sportsassets.workers import bettor_state as w

    class _Broken:
        async def fetch(self, *a, **k):
            raise RuntimeError("premap gone")

    r = asyncio.run(w.tick(_Broken(), last_bucket=None))
    assert r["status"] == "premap_unreadable"
    assert r["sampled"] is True      # it tried; run() checks the status


def test_the_worker_does_not_order_its_universe_by_activity():
    """ORDER BY updated_at upstream of the rotation would put a
    selection in front of the frozen rule."""
    import inspect
    from sportsassets.workers import bettor_state as w
    assert "ORDER BY" not in w.PREMAP_SQL
    assert "LIMIT" not in w.PREMAP_SQL
    assert "updated_at" in inspect.getsource(w)   # only in the WHERE
