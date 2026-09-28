"""OUR OWN CONTRIBUTION TO QUOTE STALENESS IS BOUNDED, AND IT COSTS CREDITS.

THE MEASUREMENT THIS EXISTS FOR (2026-09-28, all 1,126 evaluation rows in
production). 399 rows produced no fair value. Every single one failed at
the SAME link -- QUOTE_STALE -- and none failed at provider coverage,
mapping, the de-vig, calibration, persistence or consumer lookup. Splitting
`pinnacle_age_s` the way `_entry_freshness` already splits it:

    provider lag (already old on arrival)  median 14.6 s   103 rows > 30 s
    our processing delay                   median 28.7 s   296 rows made stale

So on the median row the provider left us ~15 s of the 30 s budget and we
spent ~29 s before deciding. 74% of the staleness is ours.

WHY IT IS NOT A DEFECT. `venue_pace` is a deliberate process-wide serial
gate -- one venue request per MIN_GAP_S, shared with the protected
collector because the venue 429s above ~3 req/s. Each event needs several
paced reads before its decision instant and `received_at` is stamped once
per sport, so event N carries a quote aged by every preceding event's
reads. Concurrency cannot fix that; the gate would serialise it anyway.

THIS FILE ONCE CLAIMED THIS WAS THE ONLY LEVER. WITHDRAWN (2026-09-28).
It read "SO THE ONLY LEVER IS HOW MANY EVENTS SHARE ONE FETCH, AND IT IS
PAID FOR IN CREDITS", and both halves were overstated:

  * NOT THE ONLY LEVER. Three others exist and none of them costs a
    credit: skipping candidates already past the limit before spending
    their venue read (so they stop making their successors stale),
    ordering events freshest-first, and sharing one venue read between
    two provider events that resolve to the same instrument. All three
    are implemented and measured -- see
    `test_the_latency_levers_are_wired_and_measured`.
  * AND THE CREDIT COST WAS MATERIALLY OVERSTATED. Production reports
    `credits_remaining 6,438,238` against ~5,760/day of use: roughly
    1,100 days of headroom. Presenting the re-fetch as a resource
    decision the owner had to weigh was wrong.

What remains true is the mechanism: at the default this knob spends
nothing extra, and a lowered setting must refresh the PRICES and not
merely the timestamp -- which would be a false freshness certificate.
Those two halves are what this file pins.
"""

import pytest

from sportsassets.workers import ext_pinnacle_loop as L


def _event(eid, *, home="Home FC", away="Away FC", last_update, price=2.0):
    return {"id": eid, "home_team": home, "away_team": away,
            "commence_time": "2026-09-28T20:00:00Z",
            "bookmakers": [{
                "key": "pinnacle", "last_update": last_update,
                "markets": [{"key": "h2h", "last_update": last_update,
                             "outcomes": [{"name": home, "price": price},
                                          {"name": away, "price": price}]}]}]}


# ═════════════════════════════════════════════════════════════════════
# 1 · THE DEFAULT SPENDS NOTHING EXTRA
# ═════════════════════════════════════════════════════════════════════

def test_the_default_is_todays_behaviour_and_issues_no_extra_fetch():
    """EVENTS_PER_ODDS_FETCH == MAX_PER_CYCLE, so the branch cannot fire.

    This is the whole safety argument for shipping the knob: at the default
    the credit spend is byte-for-byte what it was, so the change carries no
    resource decision with it.
    """
    assert L.EVENTS_PER_ODDS_FETCH == L.MAX_PER_CYCLE
    # And the condition in the loop is `served >= EVENTS_PER_ODDS_FETCH`,
    # which at equality can only be reached after MAX_PER_CYCLE events --
    # by which point `evaluated >= MAX_PER_CYCLE` has already broken out.
    assert L.EVENTS_PER_ODDS_FETCH >= L.MAX_PER_CYCLE


def test_the_knob_declares_that_freshness_is_bought_with_credits():
    """The cost must be stated where the setting is, not discovered later."""
    import inspect
    src = inspect.getsource(L)
    i = src.index("EVENTS_PER_ODDS_FETCH = ")
    note = src[max(0, i - 3000):i]
    assert "credits" in note
    assert "DEFAULTS TO TODAY'S BEHAVIOUR" in note
    # The measured basis, so the number is not a guess dressed as a setting.
    assert "296" in note and "103" in note
    assert "venue_pace" in note


# ═════════════════════════════════════════════════════════════════════
# 2 · A REFRESH MUST REFRESH THE PRICES, NOT ONLY THE STAMP
# ═════════════════════════════════════════════════════════════════════

def test_a_refresh_replaces_the_events_still_to_come():
    """THE BUG THIS TEST WAS WRITTEN AFTER FINDING.

    My first version did `for event in got["events"]` and then rebound
    `got` on a refresh. Python binds the list once, so the fresh payload
    would have been fetched, PAID FOR, and then ignored for every event but
    the current one -- the worst possible outcome: full credit cost, no
    freshness. The loop now iterates a local list by index and overwrites
    the entries from the current position onward.
    """
    import inspect
    src = inspect.getsource(L.cycle) if hasattr(L, "cycle") else None
    if src is None:
        for name in ("run_cycle", "_cycle", "evaluate_cycle"):
            if hasattr(L, name):
                src = inspect.getsource(getattr(L, name))
                break
    assert src is not None, "the cycle function was renamed"
    assert "events = list(got[\"events\"] or [])" in src, (
        "the event loop must iterate a LOCAL list so a refresh can replace "
        "the entries still to come")
    assert "for _i in range(len(events))" in src
    assert "events[_j] = _r" in src, (
        "a refresh must overwrite the upcoming events, not just the current")
    # THE STAMP AND THE PRICES MOVE TOGETHER.
    assert "received_at = again[\"received_at\"]" in src
    j = src.index("received_at = again[\"received_at\"]")
    assert "fresh = {" in src[j:j + 600], (
        "taking the new receipt instant without the new payload's prices "
        "would be a fresh-looking age on an old quote")


def test_a_failed_refresh_keeps_the_quote_rather_than_dropping_the_sport():
    """A provider hiccup must not become missing coverage."""
    import inspect
    src = inspect.getsource(L.cycle) if hasattr(L, "cycle") else None
    if src is None:
        for name in ("run_cycle", "_cycle", "evaluate_cycle"):
            if hasattr(L, name):
                src = inspect.getsource(getattr(L, name))
                break
    assert "odds_refetch_failures += 1" in src
    # No `continue` or `break` on the failure branch: the old quote ages and
    # QUOTE_STALE names it.
    k = src.index("odds_refetch_failures += 1")
    tail = src[k:k + 200]
    assert "continue" not in tail and "break" not in tail, (
        "a failed refresh must fall through and let the existing quote age "
        "normally, so the freshness rule refuses it BY NAME")
    # The constant must say the two things that make it not a retry: it
    # never re-decides a decided event, and a failure keeps the old quote.
    note = L.ODDS_REFETCH_IS_NOT_A_RETRY
    assert "never re-decides" in note
    assert "existing quote is kept" in note


# ═════════════════════════════════════════════════════════════════════
# 3 · AND THE CYCLE SAYS WHAT IT DID
# ═════════════════════════════════════════════════════════════════════

def test_the_cycle_reports_the_setting_and_the_refetches():
    """A change in credit spend must be attributable, not guessed at."""
    import inspect
    src = inspect.getsource(L)
    assert '"odds_freshness": {' in src
    for k in ("events_per_odds_fetch", "odds_refetches",
              "odds_refetch_failures", "is_the_default"):
        assert '"%s"' % k in src, k


def test_pinnacle_h2h_reads_the_provider_last_update_not_our_clock():
    """The decomposition only works because observed_at is the PROVIDER's.

    If `observed_at` fell back to our read time, every quote would be fresh
    by construction and the 296-vs-103 split could not have been measured
    at all. This pins the property the whole diagnosis rests on.
    """
    got = L.pinnacle_h2h(_event("e1", last_update="2026-09-28T12:00:00Z"),
                         received_at=1800000000.0)
    assert got is not None
    assert got["received_at"] == 1800000000.0
    assert got["observed_at"] == "2026-09-28T12:00:00Z", (
        "observed_at must be the provider's own last_update")
    # And the two are genuinely different clocks.
    assert got["observed_at"] != got["received_at"]


def test_an_event_without_pinnacle_is_not_given_a_quote():
    e = _event("e2", last_update="2026-09-28T12:00:00Z")
    e["bookmakers"][0]["key"] = "someotherbook"
    assert L.pinnacle_h2h(e, received_at=1800000000.0) is None
