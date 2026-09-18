#!/usr/bin/env python3
"""The events-to-markets adapter and the declared pagination rule.

Driven by RETAINED /v1/events bodies from run85_trackbl_BLOCK_3, not by
invented flat market rows. The historical as-of travels with them: these
events were eligible on 2026-09-14 and are not eligible today, and every test
that evaluates eligibility says so explicitly.
"""
import json
import os

import pytest

import event_identity as EI
import events_adapter as EA
import substantive_select as S

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures_events_block3.json")


@pytest.fixture(scope="module")
def fx():
    with open(FIXTURES) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def page0(fx):
    return fx["RETAINED_PAGES"][0]["body"]


@pytest.fixture(scope="module")
def as_of(fx):
    return fx["AS_OF_UTC"]


class Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._b = body

    def json(self):
        return self._b


def distinct_page(body, tag):
    """A mechanically relabelled copy of a retained page.

    Pagination tests need SUCCESSIVE pages, and serving the same retained page
    twice is a stalled walk -- correctly detected as such. So ids and slugs get
    a suffix. Only the walk's paging mechanics are under test here; the
    identity and eligibility tests use the retained rows untouched.
    """
    out = []
    for e in body["events"]:
        e2 = dict(e)
        e2["id"] = "%s-%s" % (e["id"], tag)
        e2["slug"] = "%s-%s" % (e["slug"], tag)
        e2["markets"] = [dict(m, id="%s-%s" % (m["id"], tag),
                              slug="%s-%s" % (m["slug"], tag))
                         for m in e.get("markets") or []]
        out.append(e2)
    return {"events": out}


def walker(pages):
    """Serve pages in order; anything past the end is an empty listing."""
    seq = list(pages)

    def get(params):
        i = int(params.get("offset", 0)) // int(params.get("limit", 100))
        if i < len(seq):
            p = seq[i]
            return Resp(p.get("body"), p.get("http_status", 200))
        return Resp({"events": []})
    return get


# --------------------------------------------------------------- the fixtures

def test_the_fixture_is_retained_evidence_with_a_historical_as_of(fx):
    assert fx["PROVENANCE"]["SOURCE_RUN"] == "run85_trackbl_BLOCK_3"
    assert fx["AS_OF_IS_HISTORICAL"] is True
    assert fx["THESE_MARKETS_ARE_NOT_ELIGIBLE_TODAY"] is True
    assert fx["RETAINED_PAGES"][0]["PROVENANCE"] == "RETAINED_VERBATIM"


def test_the_terminal_page_fixture_is_labelled_synthetic(fx):
    """It has to be. BLOCK_3 never saw one."""
    t = fx["SYNTHETIC_PAGES"]["DECLARED_TERMINAL_EMPTY_PAGE"]
    assert "SYNTHETIC" in t["PROVENANCE"]
    assert "NEVER_OBSERVED" in t["PROVENANCE"]
    assert fx["RETAINED_TERMINATION_EVIDENCE"]["TERMINAL_CONDITION_OBSERVED"] \
        is False


def test_the_retained_evidence_shows_advance_and_not_an_ending(fx):
    ev = fx["RETAINED_TERMINATION_EVIDENCE"]
    assert ev["PAGINATION_ADVANCES"] == "YES"
    assert ev["DUPLICATE_EVENT_IDS"] == 0
    assert ev["UNIQUE_EVENT_IDS_ACROSS_PAGES"] == 2600
    # and the half that was never observed
    assert ev["DISCOVERY_LIST_EXHAUSTED"] == "NO"
    assert ev["FIRST_TERMINAL_OFFSET"] is None
    assert ev["LAST_PAGE_EVENT_COUNT"] == 100
    assert ev["WALK_STOPPED_BY"] == "PAGE_CAP_AT_26_PAGES"


def test_the_endpoint_carries_no_continuation_or_total_field(fx):
    ev = fx["RETAINED_TERMINATION_EVIDENCE"]
    assert ev["TOP_LEVEL_KEYS"] == ["events"]
    assert ev["CONTINUATION_FIELD_PRESENT"] is False
    assert ev["TOTAL_COUNT_FIELD_PRESENT"] is False


# --------------------------------------------------------------- the contract

def test_events_contract_declares_a_terminal_it_has_never_observed():
    c = S.BOARD_COMPLETION_CONTRACTS[S.EVENTS_PATH]
    assert c["ADVANCE_OBSERVED"] is True
    assert c["TERMINAL_OBSERVED_IN_RETAINED_EVIDENCE"] is False
    assert c["TERMINAL_MUST_BE_OBSERVED_IN_THIS_RUN"] is True
    assert c["DECLARED_TERMINAL_CONDITION"] == S.TERMINAL_EMPTY_PAGE
    assert c["SHORT_PAGE_IS_TERMINAL"] is False


def test_the_contract_no_longer_cites_block_3_as_terminal_evidence():
    c = S.BOARD_COMPLETION_CONTRACTS[S.EVENTS_PATH]
    assert "BLOCK_3" not in str(c.get("TERMINAL_BASIS", ""))
    assert c["TERMINAL_BASIS"] == "DOCUMENTED_LIMIT_OFFSET_SEMANTICS"


def test_markets_endpoint_contract_is_still_not_established():
    assert S.BOARD_COMPLETION_CONTRACTS[S.MARKETS_PATH]["ESTABLISHED"] is False


# --------------------------------------------------------------- extraction

def test_extraction_counts_events_and_market_rows_separately(page0):
    markets, blk = EA.extract_markets(page0["events"])
    assert blk["EVENT_ROWS"] == len(page0["events"])
    assert blk["MARKET_ROWS_EXTRACTED"] == len(markets)
    # The two numbers are different things and the block never merges them.
    assert blk["MARKET_ROWS_EXTRACTED"] > blk["EVENT_ROWS"]
    assert blk["PARENT_COUNTED_AS_A_MARKET"] is False


def test_the_parent_event_is_never_itself_a_candidate(page0):
    markets, _ = EA.extract_markets(page0["events"])
    parent_slugs = {e.get("slug") for e in page0["events"]}
    got = {m.get("slug") for m in markets}
    assert not (got & parent_slugs)


def test_the_event_slug_is_not_used_as_the_market_slug(page0):
    markets, _ = EA.extract_markets(page0["events"])
    for m in markets:
        assert m["slug"] != m[EA.PROV_EVENT_SLUG]


def test_one_event_with_several_markets_yields_distinct_candidates(page0):
    """mlb-sd-col-2026-09-16 carries five child markets: five candidates."""
    ev = [e for e in page0["events"]
          if e["slug"] == "mlb-sd-col-2026-09-16"]
    assert len(ev) == 1
    markets, blk = EA.extract_markets(ev)
    assert blk["EVENT_ROWS"] == 1
    assert len(markets) == 5
    assert len({m["slug"] for m in markets}) == 5
    assert len({m["id"] for m in markets}) == 5
    assert all(m[EA.PROV_EVENT_ID] == ev[0]["id"] for m in markets)


def test_the_canonical_event_id_is_not_the_parent_event_id(page0):
    """EVENT_ID stays the venue team-pair key; the parent id is provenance."""
    ev = [e for e in page0["events"] if e["slug"] == "mlb-sd-col-2026-09-16"]
    markets, _ = EA.extract_markets(ev)
    parent_id = ev[0]["id"]
    for m in markets:
        eid, level = EI.event_identity(m)
        assert level == EI.LEVEL_V1_CONTEST
        assert eid != parent_id
        assert "|" in eid                       # start|teamA-teamB
        assert m[EA.PROV_EVENT_ID] == parent_id


def test_provenance_travels_and_the_venue_row_survives_unchanged(page0):
    markets, _ = EA.extract_markets(
        page0["events"], page_ref={"OFFSET": 0}, endpoint=S.EVENTS_PATH)
    m = markets[0]
    prov = EA.provenance_of(m)
    assert prov[EA.PROV_SOURCE_ENDPOINT] == S.EVENTS_PATH
    assert prov[EA.PROV_ADAPTER] == EA.ADAPTER_VERSION
    assert prov[EA.PROV_SOURCE_PAGE] == {"OFFSET": 0}
    # And the venue's own object is recoverable byte-for-byte.
    raw = EA.strip_provenance(m)
    src = None
    for e in page0["events"]:
        for c in e["markets"]:
            if c.get("slug") == m["slug"]:
                src = c
    assert raw == src


def test_start_time_fields_keep_their_original_meanings(page0):
    markets, _ = EA.extract_markets(page0["events"])
    for m in markets[:20]:
        src_parent = m[EA.PROV_EVENT_START]
        # gameStartTime is the market's own and is NOT overwritten by the
        # parent's startDate, which is a different field with a different job.
        if m.get("gameStartTime") and src_parent:
            assert "gameStartTime" in m
            assert m[EA.PROV_EVENT_START] == src_parent


# ------------------------------------------------- missing / conflicting rows

def test_a_child_without_a_slug_is_named_and_dropped():
    rows = [{"id": "e1", "slug": "ev-1",
             "markets": [{"id": "m1", "gameStartTime": "2026-09-18T00:00:00Z"}]}]
    markets, blk = EA.extract_markets(rows)
    assert markets == []
    assert blk["DROPPED_COUNT"] == 1
    assert blk["DROPPED_ROWS"][0]["REASON"] == EA.DROP_NO_MARKET_SLUG


def test_a_child_without_an_id_is_named_and_dropped():
    rows = [{"id": "e1", "slug": "ev-1", "markets": [{"slug": "m-1"}]}]
    markets, blk = EA.extract_markets(rows)
    assert markets == []
    assert blk["DROPPED_ROWS"][0]["REASON"] == EA.DROP_NO_MARKET_ID


def test_a_child_wearing_the_parent_slug_is_refused():
    rows = [{"id": "e1", "slug": "ev-1",
             "markets": [{"id": "m1", "slug": "ev-1"}]}]
    markets, blk = EA.extract_markets(rows)
    assert markets == []
    assert blk["DROPPED_ROWS"][0]["REASON"] == EA.DROP_SLUG_EQUALS_PARENT


def test_an_event_with_no_markets_list_is_named_not_treated_as_a_market():
    rows = [{"id": "e1", "slug": "ev-1", "title": "no markets here"}]
    markets, blk = EA.extract_markets(rows)
    assert markets == []
    assert blk["EVENT_ROWS"] == 1
    assert blk["EVENTS_WITH_MARKETS"] == 0
    assert blk["DROPPED_ROWS"][0]["REASON"] == EA.EVENT_DROP_NO_MARKETS


def test_an_identical_duplicate_is_deduplicated_and_counted():
    child = {"id": "m1", "slug": "m-1", "gameStartTime": "2026-09-18T00:00:00Z"}
    rows = [{"id": "e1", "slug": "ev-1", "markets": [child, dict(child)]}]
    markets, blk = EA.extract_markets(rows)
    assert len(markets) == 1
    assert blk["CONFLICTING_DUPLICATE_COUNT"] == 0
    assert blk["DROPPED_ROWS"][0]["REASON"] == EA.DROP_DUPLICATE_MARKET_ID


def test_a_conflicting_duplicate_is_named_and_never_resolved_by_guessing():
    a = {"id": "m1", "slug": "m-1", "gameStartTime": "2026-09-18T00:00:00Z"}
    b = {"id": "m1", "slug": "m-1", "gameStartTime": "2026-09-19T12:00:00Z"}
    rows = [{"id": "e1", "slug": "ev-1", "markets": [a, b]}]
    markets, blk = EA.extract_markets(rows)
    assert len(markets) == 1                      # the first, not a merge
    assert blk["CONFLICTING_DUPLICATE_COUNT"] == 1
    assert blk["CONFLICTING_DUPLICATES"][0]["REASON"] == \
        EA.DROP_CONFLICTING_DUPLICATE


def test_duplicate_events_are_deduplicated_separately_from_markets():
    child = {"id": "m1", "slug": "m-1"}
    ev = {"id": "e1", "slug": "ev-1", "markets": [child]}
    markets, blk = EA.extract_markets([ev, dict(ev)])
    assert blk["EVENT_ROWS"] == 2
    assert blk["EVENTS_WITH_MARKETS"] == 1
    assert len(markets) == 1
    assert any(d["REASON"] == EA.EVENT_DROP_DUPLICATE
               for d in blk["DROPPED_ROWS"])


def test_a_conflicting_duplicate_event_is_named():
    a = {"id": "e1", "slug": "ev-1", "markets": [{"id": "m1", "slug": "m-1"}]}
    b = {"id": "e1", "slug": "ev-DIFFERENT",
         "markets": [{"id": "m2", "slug": "m-2"}]}
    _, blk = EA.extract_markets([a, b])
    assert blk["CONFLICTING_DUPLICATE_COUNT"] == 1
    assert blk["CONFLICTING_DUPLICATES"][0]["REASON"] == \
        EA.EVENT_DROP_CONFLICTING_DUPLICATE


def test_missing_identity_never_silently_becomes_an_eligible_candidate(as_of):
    """A row with no team binding must be refused, not guessed into a contest."""
    rows = [{"id": "e1", "slug": "ev-1", "title": "Some Game",
             "markets": [{"id": "m1", "slug": "m-1",
                          "gameStartTime": "2026-09-14T18:00:00Z"},
                         {"id": "m2", "slug": "m-2",
                          "gameStartTime": "2026-09-14T18:00:00Z"}]}]
    markets, _ = EA.extract_markets(rows)
    assert len(markets) == 2                      # extracted...
    books = {m["slug"]: {"bids": [1], "asks": [1]} for m in markets}
    act = {m["slug"]: {"ACTIVE_AT_DECISION": True} for m in markets}
    out, _rej, reasons = S.eligible_events(markets, books, as_of,
                                           activity_of=act)
    assert out == []                              # ...and none eligible
    for m in markets:
        assert reasons[m["slug"]].startswith("NO_VENUE_NATIVE_CONTEST_IDENTITY")


def test_two_markets_sharing_only_a_start_time_are_not_one_event(as_of):
    """The over-merge the slug key died of, arriving through the adapter."""
    t = "2026-09-14T18:00:00Z"
    rows = [{"id": "e1", "slug": "ev-1", "markets": [
        {"id": "m1", "slug": "m-1", "gameStartTime": t,
         "marketSides": [{"teamId": "A"}]},
        {"id": "m2", "slug": "m-2", "gameStartTime": t,
         "marketSides": [{"teamId": "B"}]}]}]
    markets, _ = EA.extract_markets(rows)
    keys = {EI.event_identity(m)[0] for m in markets}
    assert len(keys) == 2                         # two subjects, not one game


# ------------------------------------------------------------- the walk rules

def test_a_continuation_flag_on_a_nonterminal_page_means_continue(page0):
    """The defect management named: has_more mid-walk is not an error."""
    pages = [{"body": dict(page0, has_more=True)},
             {"body": dict(distinct_page(page0, "p2"), has_more=True)},
             {"body": {"events": [], "has_more": False}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    assert st == S.BOARD_VERIFIED_END
    cert, why = S.certify_completion(st, rec, len(idx), S.EVENTS_PATH)
    assert cert == S.BOARD_VERIFIED_END, why


def test_a_continuation_flag_on_the_terminal_page_does_contradict(page0):
    pages = [{"body": page0},
             {"body": {"events": [], "has_more": True}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    cert, why = S.certify_completion(st, rec, len(idx), S.EVENTS_PATH)
    assert cert == S.BOARD_PAGINATION_CONTRADICTS_END
    assert "has_more" in why


def test_a_declared_total_above_the_event_rows_contradicts(page0):
    pages = [{"body": dict(page0, total=999)},
             {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    cert, why = S.certify_completion(st, rec, len(idx), S.EVENTS_PATH)
    assert cert == S.BOARD_PAGINATION_CONTRADICTS_END
    assert "total" in why


def test_a_short_page_is_not_the_end_of_an_event_listing(page0, fx):
    """Six events at limit 100 means read the next offset, not 'done'."""
    short = distinct_page(fx["SYNTHETIC_PAGES"]["SHORT_PAGE_NOT_TERMINAL"]
                          ["body"], "p2")
    assert len(short["events"]) < 100          # short at limit=100
    pages = [{"body": page0}, {"body": short}, {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=100)
    assert st == S.BOARD_VERIFIED_END
    # It read all three pages: it did not stop on the short one.
    assert len(rec) == 3
    assert rec[-1]["ROWS_RETURNED"] == 0


def test_only_an_empty_page_terminates_and_it_must_be_observed(page0):
    """Stopping at the page cap can never certify, however many pages ran."""
    pages = [{"body": page0},
             {"body": distinct_page(page0, "p2")},
             {"body": distinct_page(page0, "p3")}]
    by, idx, rec, st, blk = S.discovery_walk(
        walker(pages), max_pages=3, page_limit=6)
    assert st == S.BOARD_PAGE_CAP_REACHED
    cert, why = S.certify_completion(st, rec, len(idx), S.EVENTS_PATH)
    assert cert == S.BOARD_PAGE_CAP_REACHED


def test_a_walk_that_never_saw_the_terminal_page_cannot_be_forced_to_certify():
    """Hand certify_completion a VERIFIED_END it did not earn."""
    rec = [{"PARAMS": {"offset": 0}, "ROWS_RETURNED": 100,
            "PAGINATION_FIELDS": {}}]
    cert, why = S.certify_completion(S.BOARD_VERIFIED_END, rec, 100,
                                     S.EVENTS_PATH)
    assert cert == S.BOARD_COMPLETION_CONTRACT_NOT_ESTABLISHED
    assert "NOT_OBSERVED" in why
    assert "LAST_PAGE_ROWS=100" in why


def test_an_empty_first_page_enumerates_nothing():
    by, idx, rec, st, blk = S.discovery_walk(walker([{"body": {"events": []}}]))
    assert st == S.BOARD_SINGLE_PAGE_UNCORROBORATED
    assert S.certify_completion(st, rec, 0, S.EVENTS_PATH)[0] != \
        S.BOARD_VERIFIED_END


def test_http_failure_is_never_an_enumerated_board(page0):
    pages = [{"body": page0}, {"body": {}, "http_status": 503}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    assert st == S.BOARD_HTTP_FAILURE
    assert rec[-1]["HTTP_STATUS"] == 503


def test_a_body_with_no_events_array_is_a_schema_failure_not_an_empty_page():
    pages = [{"body": {"data": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages))
    assert st == S.BOARD_SCHEMA_FAILURE
    assert rec[-1]["RESPONSE_SHAPE"].startswith("NO_EVENTS_ARRAY")


def test_an_empty_events_array_is_a_page_not_a_schema_failure(page0):
    pages = [{"body": page0}, {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    assert st == S.BOARD_VERIFIED_END
    assert rec[-1]["RESPONSE_SHAPE"] == "MAPPING_KEY:events"


def test_a_server_ignoring_offset_is_stalled_not_finished(page0):
    def get(params):
        return Resp(page0)                        # same page, always
    by, idx, rec, st, blk = S.discovery_walk(get, max_pages=5, page_limit=6)
    assert st == S.BOARD_PAGINATION_STALLED
    assert S.certify_completion(st, rec, len(idx), S.EVENTS_PATH)[0] != \
        S.BOARD_VERIFIED_END


def test_a_conflicting_duplicate_stops_the_walk_with_a_retained_receipt():
    a = {"id": "m1", "slug": "m-1", "gameStartTime": "2026-09-18T00:00:00Z"}
    b = {"id": "m1", "slug": "m-1", "gameStartTime": "2026-09-19T00:00:00Z"}
    body = {"events": [{"id": "e1", "slug": "ev-1", "markets": [a, b]}]}
    by, idx, rec, st, blk = S.discovery_walk(walker([{"body": body}]))
    assert st == S.BOARD_SCHEMA_FAILURE
    assert "CONFLICTING_DUPLICATES" in rec[-1]["RESPONSE_SHAPE"]
    assert rec[-1]["BODY_SHA256"] != S.NOT_IDENTIFIED


def test_every_page_retains_a_receipt_with_a_body_hash(page0):
    pages = [{"body": page0}, {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    for r in rec:
        assert r["BODY_SHA256"] != S.NOT_IDENTIFIED
        assert r["ENDPOINT"] == S.EVENTS_PATH
        assert r["REQUEST_UTC"] != S.NOT_IDENTIFIED
        assert "offset" in r["PARAMS"] and "limit" in r["PARAMS"]


def test_the_walk_uses_declared_limit_offset_semantics(page0):
    seen = []

    def get(params):
        seen.append(dict(params))
        return Resp(page0 if len(seen) == 1 else {"events": []})
    S.discovery_walk(get, page_limit=100)
    assert seen[0]["limit"] == 100 and seen[0]["offset"] == 0
    assert seen[1]["limit"] == 100 and seen[1]["offset"] == 100


def test_retained_receipts_match_the_declared_params(fx):
    """The walk's params are the ones BLOCK_3 actually sent."""
    for r in fx["RETAINED_PAGE_RECEIPTS"]:
        assert r["path"] == S.EVENTS_PATH
        assert r["params"]["limit"] == 100
        assert r["params"]["offset"] % 100 == 0


# ----------------------------------------------- the block the run would seal

def test_the_block_reports_event_rows_and_market_rows_apart(page0):
    pages = [{"body": page0}, {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    out = S.discovery_retrieval_block(by, idx, rec, st, blk)
    assert out["DISCOVERY_EVENT_ROWS"] == len(page0["events"])
    assert out["DISCOVERY_MARKET_ROWS_EXTRACTED"] == len(by)
    assert out["DISCOVERY_EVENT_ROWS"] != out["DISCOVERY_MARKET_ROWS_EXTRACTED"]
    assert out["EVENT_TOTAL_COMPARED_WITH_MARKET_ROW_COUNT"] is False
    assert out["DISCOVERY_ENDPOINT"] == S.EVENTS_PATH
    assert out["DISCOVERY_ADAPTER"] == EA.ADAPTER_VERSION


def test_the_block_records_the_offset_the_walk_actually_terminated_at(page0):
    pages = [{"body": page0}, {"body": distinct_page(page0, "p2")},
             {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    out = S.discovery_retrieval_block(by, idx, rec, st, blk)
    assert out["DISCOVERY_LIST_EXHAUSTED"] == "YES"
    assert out["FIRST_TERMINAL_OFFSET"] == 12


def test_an_uncertified_walk_names_no_terminal_offset(page0):
    by, idx, rec, st, blk = S.discovery_walk(
        walker([{"body": page0}]), max_pages=1, page_limit=6)
    out = S.discovery_retrieval_block(by, idx, rec, st, blk)
    assert out["DISCOVERY_LIST_EXHAUSTED"] == "NO"
    assert out["FIRST_TERMINAL_OFFSET"] is None
    assert out["BOARD_UNIVERSE_ENUMERATED"] is False


# ------------------------------------------- the whole adapted path, offline

def test_the_adapted_path_reaches_a_frozen_roster_at_the_historical_as_of(
        page0, as_of):
    """events -> children -> unchanged eligibility -> unchanged freeze."""
    pages = [{"body": page0}, {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    assert st == S.BOARD_VERIFIED_END

    markets = list(by.values())
    books = {s: {"bids": [{"price": "0.4"}], "asks": [{"price": "0.6"}]}
             for s in by}
    act = {s: {"ACTIVE_AT_DECISION": True} for s in by}

    sel = S.freeze(markets, books, as_of, activity_of=act)
    assert sel["EVENT_SELECTION_FROZEN"] == "YES", sel.get("SELECTION_STATUS")
    assert len(sel["EVENT_IDS"]) == S.EVENTS_REQUIRED
    assert len(sel["MARKET_IDS"]) == S.EVENTS_REQUIRED * S.MARKETS_PER_EVENT
    # Every selected row is a CHILD market slug, never a parent event slug.
    parents = {e["slug"] for e in page0["events"]}
    assert not (set(sel["MARKET_SLUGS"]) & parents)


def test_the_frozen_roster_carries_venue_team_pair_identities(page0, as_of):
    pages = [{"body": page0}, {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    books = {s: {"bids": [1], "asks": [1]} for s in by}
    act = {s: {"ACTIVE_AT_DECISION": True} for s in by}
    sel = S.freeze(list(by.values()), books, as_of, activity_of=act)
    for row in sel["IDENTITY_OF"].values():
        assert row["EVENT_IDENTITY_LEVEL"] == EI.LEVEL_V1_CONTEST
        assert row["EVENT_ID"] != S.NOT_IDENTIFIED
        assert "|" in row["EVENT_ID"]


def test_the_same_fixture_selects_nothing_against_a_much_earlier_clock(page0):
    """The as-of is load-bearing: a week early, every game is a future."""
    pages = [{"body": page0}, {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    books = {s: {"bids": [1], "asks": [1]} for s in by}
    act = {s: {"ACTIVE_AT_DECISION": True} for s in by}
    sel = S.freeze(list(by.values()), books, "2026-09-07T00:00:00+00:00",
                   activity_of=act)
    assert sel["EVENT_SELECTION_FROZEN"] == "NO"
    assert sel["ROW_ACCOUNTING"]["REASON_COUNTS"].get(
        "SEASON_FUTURE_BEYOND_HORIZON", 0) > 0


def test_the_frozen_rule_has_no_lower_bound_on_start_time(page0):
    """A FINDING, PINNED RATHER THAN FIXED.

    `event_rank` treats any start in the past as TIER 0, "already live, longer
    -live first", and FUTURES_HORIZON_S only bounds the FUTURE. So a game that
    finished four months ago still ranks as live and would be selected. The
    live walk never meets one because discovery sends active=true&closed=false
    and the venue filters them out -- the rule is protected by the query, not
    by itself.

    Management's decision keeps the eligibility and ranking rules unchanged, so
    this is recorded here rather than repaired. It is also precisely why the
    retained fixture carries its own AS_OF_UTC and every eligibility test uses
    it: evaluated against today's clock these historical rows WOULD read as
    eligible, and that claim must never be made.
    """
    pages = [{"body": page0}, {"body": {"events": []}}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    books = {s: {"bids": [1], "asks": [1]} for s in by}
    act = {s: {"ACTIVE_AT_DECISION": True} for s in by}
    sel = S.freeze(list(by.values()), books, "2027-01-01T00:00:00+00:00",
                   activity_of=act)
    assert sel["EVENT_SELECTION_FROZEN"] == "YES"      # the finding
    assert S.event_rank(S._parse("2026-05-01T00:00:00Z"),
                        S._parse("2026-09-18T12:00:00+00:00"))[0] == 0


def test_a_failed_discovery_blocks_the_roster_however_many_events_qualified(
        page0, as_of):
    pages = [{"body": page0}, {"body": {}, "http_status": 503}]
    by, idx, rec, st, blk = S.discovery_walk(walker(pages), page_limit=6)
    cert, _ = S.certify_completion(st, rec, len(idx), S.EVENTS_PATH)
    assert cert == S.BOARD_HTTP_FAILURE
    books = {s: {"bids": [1], "asks": [1]} for s in by}
    act = {s: {"ACTIVE_AT_DECISION": True} for s in by}
    sel = S.freeze(list(by.values()), books, as_of, activity_of=act)
    # Events did qualify from the rows that arrived...
    assert sel["EVENT_SELECTION_FROZEN"] == "YES"
    # ...and the retrieval status still refuses the capture.
    assert S.selection_status(cert, True).startswith(
        S.SELECTION_BOARD_INCOMPLETE)


def test_the_roster_is_never_drawn_from_a_page_capped_universe(page0, as_of):
    by, idx, rec, st, blk = S.discovery_walk(
        walker([{"body": page0}]), max_pages=1, page_limit=6)
    cert, _ = S.certify_completion(st, rec, len(idx), S.EVENTS_PATH)
    assert S.selection_status(cert, True) == \
        S.SELECTION_BOARD_INCOMPLETE + ":" + S.BOARD_PAGE_CAP_REACHED


# ------------------------------------------------------- scale, on real rows

def test_the_frozen_identity_rule_survives_the_whole_retained_page(page0):
    """No relaxation: the same rule, shown rows it was never shown before."""
    markets, blk = EA.extract_markets(page0["events"])
    levels = {}
    for m in markets:
        _, lv = EI.event_identity(m)
        levels[lv] = levels.get(lv, 0) + 1
    assert levels.get(EI.LEVEL_V1_CONTEST, 0) >= 15
    assert levels.get(EI.LEVEL_V3_NONE, 0) >= 1   # futures still refused
    assert sum(levels.values()) == len(markets)
