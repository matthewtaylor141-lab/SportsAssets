"""THE AGENTS' WORK STATES, PURE (owner R30: "Xavier is NOT idle when he
owns open positions ... No fake 'working' state").

  §1 the vocabulary is exactly the six owner states;
  §2 XAVIER: every state from his recorded facts, the invariant (never
     IDLE_NO_OPEN_WORK with an open position -- checked over every
     combination of position classes, market health and status rows), and
     the per-position classification;
  §3 every other agent: every state it can hold, from its own predicate,
     and BLOCKED_ON_MARKET_DATA never for an agent that reads no market
     data (Karen, Audrey, Scout, the Chief Allocator);
  §4 no fake WORKING: an output outside the window, a run that finished, a
     run older than the window or a future stamp is not work;
  §5 the constants mirror the modules they are read from.
"""
from __future__ import annotations

import itertools

import pytest

from sportsassets import agent_work_state as W
from sportsassets import xavier_freshness as XF

NOW = 1_790_400_000.0
FEED_OK = {"recorded": True, "state": "OWNER_SYNCED", "beat_at": NOW - 5}
VENUE_OK = {"recorded": True, "reads": [{"at": NOW - 20, "error": False}] * 5}
MARKET_OK = {"feed": FEED_OK, "venue": VENUE_OK}


def _st(agent, facts):
    out = W.derive(agent, facts, now=NOW)
    assert out["state"] in W.WORK_STATES or out["state"] is None
    assert isinstance(out["basis"], list) and out["detail"]
    return out


def _status(state="IDLE", *, started=None, finished=None, hb=NOW - 10,
            activity=None):
    return {"state": state, "activity": activity, "heartbeat_at": hb,
            "last_run_started_at": started, "last_run_finished_at": finished}


def _pos(gid, *, review="CURRENT", book_age=10.0, book_error=None,
         kind="PAPER", requests=None):
    cr = None if review is None else {
        "review_id": "paperrev:%s" % gid, "reviewed_at": NOW - 30,
        "management_state": review,
        "recommendation_state": review}
    book = None if book_age is None else {
        "obs_id": 1, "observed_at": NOW - book_age, "error": book_error}
    return {"position_kind": kind, "group_id": gid, "slug": "s-%s" % gid,
            "first_fill_at": NOW - 600, "current_review": cr, "book": book,
            "requests": requests or {}}


def _x(positions, *, open_n=None, market=MARKET_OK, status=None, **kw):
    return dict({"open_positions": len(positions) if open_n is None
                 else open_n, "positions": positions, "market": market,
                 "status": status}, **kw)


# ═════════════════════════════════════════════════════════════════════
# §1 THE VOCABULARY
# ═════════════════════════════════════════════════════════════════════

def test_the_six_owner_states_exactly():
    assert W.WORK_STATES == (
        "WORKING", "REVIEWING", "WAITING_FOR_FRESH_EVIDENCE",
        "BLOCKED_ON_MARKET_DATA", "HANDOFF_PENDING", "IDLE_NO_OPEN_WORK")
    assert set(W.AGENTS) == {"DEREK", "KAREN", "SCOUT", "EDDIE",
                             "CHIEF_ALLOCATOR", "AUDREY", "XAVIER",
                             "ADRIANA"}


# ═════════════════════════════════════════════════════════════════════
# §2 XAVIER
# ═════════════════════════════════════════════════════════════════════

def test_xavier_every_state_from_his_positions():
    cur, wait = XF.S_CURRENT, XF.S_WAITING
    # REVIEWING: a review run in progress
    s = _st("XAVIER", _x([_pos("a", review=wait)], status=_status(
        "EVALUATING", started=NOW - 20, activity="PAPER_REVIEW_RUNNING")))
    assert s["state"] == W.REVIEWING and s["basis"][0]["table"] == \
        "agent_status"
    # HANDOFF_PENDING: an open position with no review recorded
    s = _st("XAVIER", _x([_pos("a"), _pos("b", review=None)]))
    assert s["state"] == W.HANDOFF
    assert s["counts"]["UNREVIEWED"] == 1
    assert s["basis"][0]["positions"][0]["group_id"] == "b"
    # BLOCKED_ON_MARKET_DATA: not current and its book is stale
    s = _st("XAVIER", _x([_pos("a"), _pos("b", review=wait,
                                          book_age=900)]))
    assert s["state"] == W.BLOCKED
    assert "VENUE_BOOK_OLDER_THAN_300S" in s["detail"]
    # WAITING_FOR_FRESH_EVIDENCE: not current, market data fine
    s = _st("XAVIER", _x([_pos("a"), _pos("b", review=wait, requests={
        "PROBABILITY": {"request_id": "r1"}})]))
    assert s["state"] == W.WAITING
    assert s["counts"]["open_requests_by_kind"] == {"PROBABILITY": 1}
    assert s["counts"]["non_current_without_open_request"] == 0
    # REVIEWING: every open position on a CURRENT review
    s = _st("XAVIER", _x([_pos("a", review=cur), _pos("b", review=cur)]))
    assert s["state"] == W.REVIEWING
    assert s["basis"][0]["table"] == "xavier_current_review"
    # HANDOFF_PENDING from a hand-off row when every position is current
    s = _st("XAVIER", _x([_pos("a")], handoffs=[{
        "kind": "KAREN_CHALLENGE_TO_ANSWER", "table": "karen_challenges",
        "count": 2, "oldest_at": NOW - 50, "ids": ["c1", "c2"]}]))
    assert s["state"] == W.HANDOFF
    # IDLE_NO_OPEN_WORK: only with no open position at all
    s = _st("XAVIER", _x([]))
    assert s["state"] == W.IDLE
    # an unreadable count is never zero, never IDLE
    s = _st("XAVIER", {"open_positions": None, "positions": []})
    assert s["state"] is None and "never taken as zero" in s["detail"]


def test_the_scheduler_said_idle_but_he_owns_positions():
    """THE PRODUCTION DEFECT: agent_status IDLE /
    SCHEDULER_RAN_NO_OWNED_INVENTORY (the funded servicing pass counts only
    FUNDED inventory) while 59 paper groups were his and 27 waited for fresh
    evidence. The work state follows the positions, not that row."""
    status = _status("IDLE", activity="SCHEDULER_RAN_NO_OWNED_INVENTORY")
    pos = [_pos("w%d" % i, review=XF.S_WAITING) for i in range(27)] + \
        [_pos("c%d" % i, review=XF.S_CURRENT) for i in range(32)]
    s = _st("XAVIER", _x(pos, status=status))
    assert s["state"] == W.WAITING
    assert s["counts"]["open_positions"] == 59
    assert s["counts"]["WAITING_FOR_FRESH_EVIDENCE"] == 27
    assert "27 positions with none open" in s["detail"]


@pytest.mark.parametrize("market", [
    MARKET_OK,
    {"feed": dict(FEED_OK, state="DISARMED"), "venue": VENUE_OK},
    {"feed": dict(FEED_OK, beat_at=NOW - 3600), "venue": VENUE_OK},
    {"feed": {"recorded": False}, "venue": {"recorded": False}},
    {"feed": FEED_OK, "venue": {"recorded": True, "reads": [
        {"at": NOW - 5, "error": True}] * 6}},
])
@pytest.mark.parametrize("status", [
    None, _status("IDLE", activity="SCHEDULER_RAN_NO_OWNED_INVENTORY"),
    _status("WAITING_FOR_EVIDENCE"), _status("FAILED"),
    _status("EVALUATING", started=NOW - 5000),
    _status("EVALUATING", started=NOW - 5, finished=NOW - 1)])
def test_xavier_with_any_open_position_is_never_idle(market, status):
    classes = [XF.S_CURRENT, XF.S_WAITING, XF.S_UNAVAILABLE, None]
    books = [10.0, 900.0, None]
    for n in (1, 2):
        for combo in itertools.product(classes, books, repeat=n):
            pos = [_pos("g%d" % i, review=combo[2 * i],
                        book_age=combo[2 * i + 1]) for i in range(n)]
            for extra in (0, 3):           # positions beyond the classified
                f = _x(pos, open_n=len(pos) + extra, market=market,
                       status=status)
                s = W.derive("XAVIER", f, now=NOW)
                assert s["state"] != W.IDLE, (combo, extra, s)
                assert W.invariant_holds("XAVIER", f, s["state"])
                assert s.get("guard") is None    # never needed the guard
    # open count only (positions list not read): still never idle
    s = W.derive("XAVIER", _x([], open_n=4, market=market, status=status),
                 now=NOW)
    assert s["state"] != W.IDLE
    assert not W.invariant_holds("XAVIER", {"open_positions": 1}, W.IDLE)
    assert W.invariant_holds("XAVIER", {"open_positions": 0,
                                        "positions": []}, W.IDLE)
    assert W.invariant_holds("DEREK", {"open_positions": 9}, W.IDLE)


def test_the_guard_turns_any_idle_away_for_an_owner(monkeypatch):
    """Belt and braces: even a broken classifier cannot return IDLE."""
    monkeypatch.setattr(W, "_xavier", lambda f, now: W._out(
        W.IDLE, "broken", [], counts={}))
    s = W.derive("XAVIER", _x([_pos("a")]), now=NOW)
    assert s["state"] == W.WAITING and s["guard"] == "XAVIER_NEVER_IDLE"


def test_position_classes():
    ms = W.market_status(MARKET_OK, NOW)
    pc = W.position_class
    assert pc(_pos("a", review=None), market=ms, now=NOW)[0] == \
        W.P_UNREVIEWED
    assert pc(_pos("a"), market=ms, now=NOW)[0] == W.P_CURRENT
    assert pc(_pos("a", review=XF.S_WAITING), market=ms, now=NOW) == (
        W.P_WAITING, "CURRENT_REVIEW_IS_WAITING_FOR_FRESH_EVIDENCE")
    assert pc(_pos("a", review=XF.S_WAITING, book_age=None), market=ms,
              now=NOW) == (W.P_BLOCKED, "NO_VENUE_BOOK_OBSERVED")
    assert pc(_pos("a", review=XF.S_WAITING, book_error="HTTP_503"),
              market=ms, now=NOW)[1] == "LATEST_VENUE_BOOK_UNREADABLE"
    # an ACTUAL position is not judged on the paper book table
    assert pc(_pos("a", review=XF.S_WAITING, book_age=None, kind="ACTUAL"),
              market=ms, now=NOW)[0] == W.P_WAITING
    bad = W.market_status({"feed": dict(FEED_OK, state="STANDBY"),
                           "venue": VENUE_OK}, NOW)
    assert pc(_pos("a", review=XF.S_WAITING), market=bad, now=NOW) == (
        W.P_BLOCKED, "FEED_STANDBY")
    # a CURRENT review is current whatever the feed does now
    assert pc(_pos("a"), market=bad, now=NOW)[0] == W.P_CURRENT


def test_market_status_claims_nothing_without_a_row():
    ms = W.market_status({}, NOW)
    assert not ms["feed"]["blocked"] and not ms["venue"]["blocked"]
    assert ms["feed"]["why"] == "FEED_TELEMETRY_NOT_RECORDED"
    ms = W.market_status({"feed": FEED_OK, "venue": {"recorded": True,
        "reads": [{"at": NOW - 5, "error": True}] * 2}}, NOW)
    assert not ms["venue"]["blocked"]           # too few reads to judge
    ms = W.market_status({"feed": FEED_OK, "venue": {"recorded": True,
        "reads": [{"at": NOW - 5000, "error": True}] * 9}}, NOW)
    assert not ms["venue"]["blocked"]           # outside the window
    ms = W.market_status({"feed": FEED_OK, "venue": {"recorded": True,
        "reads": [{"at": NOW - 5, "error": i % 2 == 0} for i in range(6)]}},
        NOW)
    assert ms["venue"]["blocked"] and ms["venue"]["recent_errors"] == 3


# ═════════════════════════════════════════════════════════════════════
# §3 EVERY OTHER AGENT
# ═════════════════════════════════════════════════════════════════════

RUN = _status("EVALUATING", started=NOW - 30, activity="PASS_STARTED")
FEED_DOWN = {"feed": dict(FEED_OK, state="WRITER_LOCK_LOST"),
             "venue": VENUE_OK}
VENUE_DOWN = {"feed": FEED_OK, "venue": {"recorded": True, "reads": [
    {"at": NOW - 5, "error": True}] * 8}}
OUT = [{"table": "t", "id": "1", "at": NOW - 60, "label": "an output"}]


def _h(kind, n=2):
    return [{"kind": kind, "table": "t", "count": n, "oldest_at": NOW - 99,
             "ids": ["x"]}]


CASES = {
    # agent: [(facts, expected state)]
    "DEREK": [
        ({"status": RUN, "market": FEED_DOWN}, W.WORKING),
        ({"market": FEED_DOWN, "outputs": OUT}, W.BLOCKED),
        ({"market": VENUE_DOWN}, W.BLOCKED),
        ({"market": MARKET_OK,
          "handoffs": _h("KAREN_CHALLENGE_TO_ANSWER")}, W.HANDOFF),
        ({"market": MARKET_OK, "handoffs": _h("AGENT_TASK_OPEN")},
         W.HANDOFF),
        ({"market": MARKET_OK,
          "status": _status("WAITING_FOR_PROVIDER")}, W.WAITING),
        ({"market": MARKET_OK, "outputs": OUT}, W.WORKING),
        ({"market": MARKET_OK}, W.IDLE)],
    "EDDIE": [
        ({"status": RUN}, W.WORKING),
        ({"market": VENUE_DOWN,
          "handoffs": _h("ENTER_DECISION_WITHOUT_ESTIMATE")}, W.BLOCKED),
        ({"market": MARKET_OK,
          "handoffs": _h("ENTER_DECISION_WITHOUT_ESTIMATE")}, W.HANDOFF),
        ({"market": VENUE_DOWN}, W.IDLE),         # nothing needs the books
        ({"market": FEED_DOWN,                    # he reads books, not feed
          "handoffs": _h("ENTER_DECISION_WITHOUT_ESTIMATE")}, W.HANDOFF),
        ({"status": _status("WAITING_FOR_EVIDENCE")}, W.WAITING),
        ({"outputs": OUT}, W.WORKING),
        ({}, W.IDLE)],
    # (265) Adriana's standing census reads the books: a venue outage
    # blocks her even with nothing handed to her
    "ADRIANA": [
        ({"status": RUN}, W.WORKING),
        ({"market": VENUE_DOWN}, W.BLOCKED),
        ({"market": FEED_DOWN}, W.IDLE),          # she reads books, not feed
        ({"handoffs": _h("AGENT_TASK_OPEN")}, W.HANDOFF),
        ({"status": _status("WAITING_FOR_EVIDENCE")}, W.WAITING),
        ({"outputs": OUT}, W.WORKING),
        ({}, W.IDLE)],
    "KAREN": [
        ({"status": RUN}, W.REVIEWING),
        ({"handoffs": _h("AGENT_TASK_OPEN")}, W.HANDOFF),
        ({"status": _status("WAITING_FOR_EVIDENCE")}, W.WAITING),
        ({"outputs": OUT}, W.REVIEWING),
        ({"market": FEED_DOWN}, W.IDLE),
        ({}, W.IDLE)],
    "AUDREY": [
        ({"status": RUN}, W.REVIEWING),
        ({"handoffs": _h("CHALLENGE_ANSWER_TO_EVALUATE:DEREK")}, W.HANDOFF),
        ({"handoffs": _h("KAREN_CHALLENGE_TO_ANSWER")}, W.HANDOFF),
        ({"status": _status("WAITING_FOR_EVIDENCE",
                            activity="NO_FINAL_REPORT_DAY")}, W.WAITING),
        ({"outputs": OUT, "market": VENUE_DOWN}, W.REVIEWING),
        ({"market": VENUE_DOWN}, W.IDLE)],
    "SCOUT": [
        ({"status": RUN}, W.WORKING),
        ({"handoffs": _h("AGENT_TASK_OPEN")}, W.HANDOFF),
        ({"waiting": [{"kind": "FEATURE_UNDER_TEST_AWAITING_SAMPLES",
                       "table": "scout_features", "count": 3}]}, W.WAITING),
        ({"status": _status("WAITING_FOR_EVIDENCE")}, W.WAITING),
        ({"outputs": OUT}, W.WORKING),
        ({"market": FEED_DOWN}, W.IDLE)],
    "CHIEF_ALLOCATOR": [
        ({"run": {"in_progress": True, "table": "intel_runs", "id": "r1",
                  "started_at": NOW - 40}}, W.WORKING),
        ({"run": {"in_progress": False},
          "handoffs": _h("ENTER_DECISION_AFTER_LAST_ALLOCATION_RUN")},
         W.HANDOFF),
        ({"handoffs": _h("KAREN_CHALLENGE_TO_ANSWER")}, W.HANDOFF),
        ({"status": _status("WAITING_FOR_PROVIDER")}, W.WAITING),
        ({"run": {"in_progress": False}, "outputs": OUT}, W.WORKING),
        ({"run": {"in_progress": False}, "market": VENUE_DOWN}, W.IDLE)],
}


@pytest.mark.parametrize("agent", sorted(CASES))
def test_every_agent_every_state_it_can_hold(agent):
    for facts, want in CASES[agent]:
        got = _st(agent, facts)
        assert got["state"] == want, (agent, facts, got)
        assert got["basis"], (agent, got)


def test_every_state_is_reached_for_every_agent_that_can_hold_it():
    reached = {a: {want for _, want in cs} for a, cs in CASES.items()}
    reached["XAVIER"] = {W.REVIEWING, W.HANDOFF, W.BLOCKED, W.WAITING,
                         W.IDLE}          # test_xavier_every_state_...
    for a in W.AGENTS:
        busy = W.BUSY.get(a, W.WORKING)
        expect = {busy, W.HANDOFF, W.WAITING, W.IDLE}
        if a in W.MARKET_SOURCES:
            expect.add(W.BLOCKED)
        assert reached[a] == expect, (a, reached[a] ^ expect)


@pytest.mark.parametrize("agent", ["KAREN", "AUDREY", "SCOUT",
                                   "CHIEF_ALLOCATOR"])
@pytest.mark.parametrize("market", [FEED_DOWN, VENUE_DOWN])
def test_no_market_data_reader_is_ever_blocked_on_market_data(agent, market):
    for h in (None, _h("AGENT_TASK_OPEN")):
        got = W.derive(agent, {"market": market, "handoffs": h or []},
                       now=NOW)
        assert got["state"] != W.BLOCKED


def test_the_blocked_basis_names_the_failing_read():
    got = W.derive("DEREK", {"market": FEED_DOWN}, now=NOW)
    assert got["basis"][0]["table"] == "ingestion_state:pinnapi_feed_last"
    assert got["basis"][0]["why"] == "FEED_WRITER_LOCK_LOST"
    got = W.derive("EDDIE", {"market": VENUE_DOWN, "handoffs": _h(
        "ENTER_DECISION_WITHOUT_ESTIMATE")}, now=NOW)
    assert got["basis"][0]["table"] == "paper_book_observations"


# ═════════════════════════════════════════════════════════════════════
# §4 NO FAKE WORKING
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("agent", ["DEREK", "EDDIE", "SCOUT", "KAREN"])
def test_no_fake_working(agent):
    base = {"market": MARKET_OK}
    for facts in (
            dict(base, outputs=[dict(OUT[0], at=NOW - 301)]),   # too old
            dict(base, outputs=[dict(OUT[0], at=NOW + 60)]),    # future
            dict(base, status=_status("EVALUATING", started=NOW - 100,
                                      finished=NOW - 50)),      # finished
            dict(base, status=_status("EVALUATING", started=NOW - 5000)),
            dict(base, status=_status("WAITING_FOR_EVIDENCE",
                                      hb=NOW - 99999))):        # stale row
        got = W.derive(agent, facts, now=NOW)
        assert got["state"] == W.IDLE, (agent, facts, got)


# ═════════════════════════════════════════════════════════════════════
# §5 THE CONSTANTS ARE THE SOURCES' OWN
# ═════════════════════════════════════════════════════════════════════

def test_the_constants_mirror_their_sources():
    from sportsassets import pinnapi_feed_runtime as FR
    from sportsassets.agents import eddie_runner as ER
    from sportsassets.agents import karen as K
    from sportsassets.api import command_floor as FL
    assert W.EVALUATOR_FOR == dict(K.EVALUATOR_FOR)
    assert W.EDDIE_LOOKBACK_S == ER.LOOKBACK_S
    assert W.FEED_HEARTBEAT_S == FR.HEARTBEAT_S
    assert W.FEED_HEARTBEAT_KEY == FR.HEARTBEAT_KEY
    assert W.ACTIVE_WINDOW_S == FL.ACTIVE_WINDOW_S
    assert W.RUN_WINDOW_FLOOR_S == FL.RUN_WINDOW_FLOOR_S
    assert W.STALE_FLOOR_S == FL.STALE_FLOOR_S
    assert set(W.AGENTS) == {s["agent"] for s in FL.SEATS}
