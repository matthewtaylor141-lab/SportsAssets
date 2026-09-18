#!/usr/bin/env python3
"""THE SELECTION REPAIR: identity, exhaustion, and complete row accounting.

WHAT FAILED, AND WHAT THE EVIDENCE ACTUALLY SAID. Substantive capture run
35209604615 froze no roster and never sampled: EVENTS_QUALIFYING = 0,
CANDIDATES_BOOK_READ = 0, REJECTED_EVENTS = [], BOARD_LIST_EXHAUSTED = NO.

The first reading -- that per-side board rows can never carry contest identity
-- was drawn from page 1 alone and was WRONG about the board. Across the sealed
20,000-row board, 1,557 rows DO carry two distinct venue team ids: every
moneyline (87/87) and every spread (1,470/1,470). The identity standard was
never the problem.

The actual cause is the page cap. The board is ordered futures-first, and the
600 rows the six-page cap admitted were 596 futures and 4 elections -- not one
contest row. The filter was correct and was never shown a game.

So this repair does NOT touch the identity standard, does not downgrade
CONTEST to SUBJECT, and invents no grouping key. It exhausts the board and
makes every row's fate visible. The fixture in this file is the real head of
that board, so the regression is pinned against what the run actually saw.

Nothing here contacts a venue.
"""
import json
import unittest
from pathlib import Path

import event_identity as EI
import book_schema as BS
import substantive_select as SS

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOW = "2026-09-17T10:16:50+00:00"
FIXTURE = Path(__file__).with_name("fixtures_board_35209604615.json")


def load():
    return json.loads(FIXTURE.read_text())


def two_sided_book():
    # NATIVE VENUE SHAPE. The old {"bids": ..., "asks": ...} literal was
    # a shape the venue never sends; it is what hid the book-schema
    # defect. book_schema.native_book emits the production contract.
    return BS.native_book([{"px": {"value": "0.50"}, "qty": "100"}],
                          [{"px": {"value": "0.52"}, "qty": "100"}])


def active():
    return {"HIGH_ACTIVITY_AT_DECISION": True}


class Test1_ASingleRowCannotManufactureContestIdentity(unittest.TestCase):

    def test_one_team_id_is_not_a_contest(self):
        m = {"slug": "s", "gameStartTime": "2026-09-17T11:00:00Z",
             "marketSides": [{"teamId": 1, "team": {"id": 1}}]}
        eid, level = EI.event_identity(m)
        self.assertNotEqual(level, EI.LEVEL_V1_CONTEST)

    def test_no_team_id_is_not_a_contest_however_rich_the_title(self):
        m = {"slug": "s", "gameStartTime": "2026-09-17T11:00:00Z",
             "title": "Detroit Lions vs Buffalo Bills",
             "question": "Lions vs Bills moneyline", "marketSides": []}
        eid, level = EI.event_identity(m)
        self.assertEqual(eid, NOT_IDENTIFIED)

    def test_a_one_sided_row_is_rejected_with_a_named_reason(self):
        m = {"slug": "one-sided", "gameStartTime": "2026-09-17T11:00:00Z",
             "marketSides": [{"teamId": 1, "team": {"id": 1}}]}
        sel = SS.freeze([m], {"one-sided": two_sided_book()}, NOW,
                        activity_of={"one-sided": active()})
        acct = sel["ROW_ACCOUNTING"]
        self.assertEqual(acct["UNACCOUNTED_ROWS"], 0)
        row = acct_row(acct, "one-sided")
        self.assertEqual(row["DISPOSITION"], "REJECTED_WITH_REASON")
        self.assertTrue(row["REASON"].startswith(
            "NO_VENUE_NATIVE_CONTEST_IDENTITY"))


def acct_row(acct, slug):
    for r in acct["ROWS"] if "ROWS" in acct else []:
        if r["MARKET_SLUG"] == slug:
            return r
    raise AssertionError("row %s not in accounting" % slug)


class Test2_SameCanonicalEventResolvesToTheSameId(unittest.TestCase):

    def test_moneyline_and_spread_on_one_game_share_an_event_id(self):
        """Structured: the venue's own gameStartTime plus its own team ids."""
        start = "2026-09-17T11:00:00Z"
        sides = [{"teamId": 501, "team": {"id": 501, "league": "nfl"}},
                 {"teamId": 502, "team": {"id": 502, "league": "nfl"}}]
        ml = {"slug": "aec-g1", "gameStartTime": start, "marketSides": sides}
        sp = {"slug": "asc-g1", "gameStartTime": start, "marketSides": sides}
        a, la = EI.event_identity(ml)
        b, lb = EI.event_identity(sp)
        self.assertEqual(a, b)
        self.assertEqual(la, EI.LEVEL_V1_CONTEST)
        self.assertEqual(lb, EI.LEVEL_V1_CONTEST)

    def test_side_order_does_not_change_the_event_id(self):
        start = "2026-09-17T11:00:00Z"
        a = EI.event_identity({"slug": "x", "gameStartTime": start,
                               "marketSides": [{"teamId": 1}, {"teamId": 2}]})[0]
        b = EI.event_identity({"slug": "y", "gameStartTime": start,
                               "marketSides": [{"teamId": 2}, {"teamId": 1}]})[0]
        self.assertEqual(a, b)

    def test_the_sealed_board_groups_into_multi_market_contests(self):
        rows = load()["CONTEST_ROWS"]
        keys = {}
        for m in rows:
            eid, lv = EI.event_identity(m)
            if lv == EI.LEVEL_V1_CONTEST:
                keys.setdefault(eid, []).append(m["slug"])
        self.assertGreater(len(keys), 0)
        self.assertGreater(sum(1 for v in keys.values() if len(v) >= 2), 0)


class Test3_DifferentContestsAreNeverGrouped(unittest.TestCase):

    def test_same_teams_different_start_times_are_different_events(self):
        sides = [{"teamId": 1}, {"teamId": 2}]
        a = EI.event_identity({"slug": "a", "marketSides": sides,
                               "gameStartTime": "2026-09-17T11:00:00Z"})[0]
        b = EI.event_identity({"slug": "b", "marketSides": sides,
                               "gameStartTime": "2026-09-24T11:00:00Z"})[0]
        self.assertNotEqual(a, b)

    def test_same_start_time_different_teams_are_different_events(self):
        start = "2026-09-17T11:00:00Z"
        a = EI.event_identity({"slug": "a", "gameStartTime": start,
                               "marketSides": [{"teamId": 1},
                                               {"teamId": 2}]})[0]
        b = EI.event_identity({"slug": "b", "gameStartTime": start,
                               "marketSides": [{"teamId": 3},
                                               {"teamId": 4}]})[0]
        self.assertNotEqual(a, b)

    def test_a_shared_kickoff_alone_never_merges_two_games(self):
        """Simultaneous kickoffs are routine; time alone is not identity."""
        start = "2026-09-17T17:00:00Z"
        ms, books, act = [], {}, {}
        for i, (x, y) in enumerate(((1, 2), (3, 4), (5, 6))):
            for k in range(2):
                s = "g%d-m%d" % (i, k)
                ms.append({"slug": s, "gameStartTime": start,
                           "marketSides": [{"teamId": x}, {"teamId": y}]})
                books[s] = two_sided_book()
                act[s] = active()
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        self.assertEqual(len(set(sel["EVENT_IDS"])), 3)


class Test4_NoTitleOrFuzzyNameHeuristic(unittest.TestCase):

    def test_the_identity_source_reads_no_title_field(self):
        import inspect
        src = inspect.getsource(EI.event_identity) + inspect.getsource(
            EI.team_ids) + inspect.getsource(EI.sides)
        for field in ("title", "question", "description", "slug",
                      "difflib", "fuzz", "lower()"):
            self.assertNotIn(field, src, field)

    def test_the_selector_does_not_group_on_names(self):
        import inspect
        src = inspect.getsource(SS.eligible_events)
        for bad in ("difflib", "SequenceMatcher", "fuzz", "startswith(\"aec",
                    "title.lower"):
            self.assertNotIn(bad, src, bad)

    def test_identical_titles_with_no_team_ids_do_not_group(self):
        ms = [{"slug": "a", "title": "Lions vs Bills",
               "gameStartTime": "2026-09-17T11:00:00Z", "marketSides": []},
              {"slug": "b", "title": "Lions vs Bills",
               "gameStartTime": "2026-09-17T11:00:00Z", "marketSides": []}]
        sel = SS.freeze(ms, {s["slug"]: two_sided_book() for s in ms}, NOW,
                        activity_of={s["slug"]: active() for s in ms})
        self.assertEqual(sel["EVENTS_SELECTED"], 0)
        self.assertEqual(sel["ROW_ACCOUNTING"]["UNACCOUNTED_ROWS"], 0)


class Test5_EveryCandidateIsAccountedFor(unittest.TestCase):

    def build(self):
        """A board with one qualifying contest and a row failing each gate."""
        start = "2026-09-17T10:40:00Z"
        ms, books, act = [], {}, {}
        for i in range(3):                       # three qualifying events
            for k in range(2):
                s = "ok%d-%d" % (i, k)
                ms.append({"slug": s, "gameStartTime": start,
                           "marketSides": [{"teamId": 100 + i * 2},
                                           {"teamId": 101 + i * 2}]})
                books[s] = two_sided_book()
                act[s] = active()
        ms.append({"slug": "noident", "gameStartTime": start,
                   "marketSides": [{"teamId": 9}]})
        ms.append({"slug": "nostart",
                   "marketSides": [{"teamId": 7}, {"teamId": 8}]})
        ms.append({"slug": "future", "gameStartTime": "2027-06-01T00:00:00Z",
                   "marketSides": [{"teamId": 11}, {"teamId": 12}]})
        ms.append({"slug": "onesided", "gameStartTime": start,
                   "marketSides": [{"teamId": 13}, {"teamId": 14}]})
        books["onesided"] = BS.native_book(
            [{"px": {"value": "0.5"}, "qty": "1"}], [])   # bids only
        act["onesided"] = active()
        ms.append({"slug": "quiet", "gameStartTime": start,
                   "marketSides": [{"teamId": 15}, {"teamId": 16}]})
        books["quiet"] = two_sided_book()
        act["quiet"] = {"HIGH_ACTIVITY_AT_DECISION": False}
        ms.append({"slug": "unread", "gameStartTime": start,
                   "marketSides": [{"teamId": 17}, {"teamId": 18}]})
        return ms, books, act

    def test_no_row_is_unaccounted(self):
        ms, books, act = self.build()
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        acct = sel["ROW_ACCOUNTING"]
        self.assertEqual(acct["UNACCOUNTED_ROWS"], 0)
        self.assertEqual(acct["ROWS_IN"], len(ms))
        self.assertEqual(acct["SELECTED"] + acct["REJECTED_WITH_REASON"],
                         acct["ROWS_IN"])
        self.assertTrue(acct["EVERY_ROW_ACCOUNTED"])

    def test_each_gate_produces_its_own_named_reason(self):
        ms, books, act = self.build()
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        reasons = sel["ROW_ACCOUNTING"]["REASON_COUNTS"]
        joined = " ".join(reasons)
        for expect in ("NO_VENUE_NATIVE_CONTEST_IDENTITY", "NO_GAME_START",
                       "SEASON_FUTURE_BEYOND_HORIZON", "BOOK_NOT_TWO_SIDED",
                       "NOT_CURRENTLY_ACTIVE",
                       "NO_BOOK_READ_WITHIN_READ_BUDGET"):
            self.assertIn(expect, joined, expect)

    def test_the_selected_rows_are_marked_selected(self):
        ms, books, act = self.build()
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        self.assertEqual(sel["ROW_ACCOUNTING"]["SELECTED"], 6)
        self.assertEqual(sel["MARKETS_SELECTED"], 6)

    def test_a_qualifying_event_outside_the_top_three_is_still_accounted(self):
        ms, books, act = self.build()
        start = "2026-09-17T10:40:00Z"
        for k in range(2):                       # a fourth qualifying event
            s = "extra-%d" % k
            ms.append({"slug": s, "gameStartTime": start,
                       "marketSides": [{"teamId": 200}, {"teamId": 201}]})
            books[s] = two_sided_book()
            act[s] = active()
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        self.assertEqual(sel["ROW_ACCOUNTING"]["UNACCOUNTED_ROWS"], 0)
        self.assertIn("EVENT_QUALIFIED_BUT_NOT_TOP_3",
                      " ".join(sel["ROW_ACCOUNTING"]["REASON_COUNTS"]))


class _Resp:
    """The response shape SS.board_walk() actually receives."""

    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class Test6_PaginationCannotSilentlyTruncate(unittest.TestCase):

    def test_the_cap_is_a_runaway_bound_far_above_the_observed_board(self):
        self.assertGreaterEqual(SS.BOARD_MAX_PAGES * SS.BOARD_PAGE_LIMIT,
                                20000)
        self.assertTrue(SS.BOARD_CAP_IS_A_RUNAWAY_BOUND_NOT_A_STOP_RULE)

    def test_the_old_six_page_cap_is_gone(self):
        """600 rows was the whole defect."""
        self.assertGreater(SS.BOARD_MAX_PAGES, 6)

    # SUPERSEDED BY THE RUN 35333848994 REPAIR. These two used to read the
    # source text of SS._cli() for the boolean `exhausted`. That boolean was
    # the defect: it collapsed a non-200, an unreadable body, a repeated page
    # and a genuinely empty page into one "YES". The walk now lives in
    # SS.board_walk() and returns a STATUS, so the assertions move from source
    # text to behaviour. The intent -- a cap is not an exhausted board, and a
    # short or repeated page terminates the walk -- is preserved below and
    # covered in depth by test_board_retrieval.py.

    def test_hitting_the_cap_is_reported_as_not_exhausted(self):
        def get(params):
            n = params["offset"]
            return _Resp({"markets": [{"slug": "s%d" % (n + i)}
                                      for i in range(2)]})

        by_slug, receipts, st = SS.board_walk(get, max_pages=3, page_limit=2)
        self.assertEqual(st, SS.BOARD_PAGE_CAP_REACHED)
        blk = SS.board_retrieval_block(by_slug, receipts, st)
        self.assertEqual(blk["BOARD_LIST_EXHAUSTED"], "NO")
        self.assertFalse(blk["BOARD_UNIVERSE_ENUMERATED"])

    def test_a_short_page_or_a_stale_page_ends_the_walk(self):
        full = {"markets": [{"slug": "a"}, {"slug": "b"}]}

        def short(params):
            return _Resp(full if params["offset"] == 0
                         else {"markets": [{"slug": "c"}]})

        _, _, st = SS.board_walk(short, page_limit=2)
        self.assertEqual(st, SS.BOARD_VERIFIED_END)

        def stale(params):
            return _Resp(full)

        _, rec, st = SS.board_walk(stale, page_limit=2)
        self.assertEqual(st, SS.BOARD_PAGINATION_STALLED)
        self.assertTrue(rec[-1]["REPEATED_EARLIER_PAGE"])


class Test7_IdentityTravelsIntoTheCaptureOutput(unittest.TestCase):

    def test_selected_rows_carry_identity_into_the_tick(self):
        import collect as C
        ms, books, act = Test5_EveryCandidateIsAccountedFor().build()
        sel = SS.freeze(ms, books, NOW, activity_of=act)
        slug = sel["MARKET_SLUGS"][0]
        blk = sel["IDENTITY_OF"][slug]
        row = C.tick_row(slug, {"bids": [{"price": "0.5", "size": "1"}],
                                "asks": [{"price": "0.52", "size": "1"}]},
                         "2026-09-17T10:41:00+00:00", 0, 0.0, identity=blk,
                         request_iso="2026-09-17T10:40:59+00:00")
        self.assertNotEqual(row["EVENT_ID"], NOT_IDENTIFIED)
        self.assertEqual(row["MARKET_SLUG"], slug)
        self.assertNotEqual(row["GAME_START"], NOT_IDENTIFIED)

    def test_identity_is_not_reconstructed_downstream(self):
        import substantive_harvest as SH
        import inspect
        src = inspect.getsource(SH.event_map)
        self.assertIn("EVENT_ID", src)
        self.assertNotIn("board", src.lower())


class Test8_TheRun35209604615Fixture(unittest.TestCase):
    """The exact board head that produced the failure, plus contest rows.

    The eligibility DEFINITION is unchanged; only the universe reaches it."""

    def test_the_old_600_row_head_really_did_contain_no_contest(self):
        head = load()["HEAD_600_AS_RUN_35209604615_SAW_IT"]
        self.assertEqual(len(head), 600)
        contests = [m for m in head
                    if EI.event_identity(m)[1] == EI.LEVEL_V1_CONTEST]
        self.assertEqual(len(contests), 0)

    def test_that_head_still_selects_nothing_and_now_says_why(self):
        head = load()["HEAD_600_AS_RUN_35209604615_SAW_IT"]
        sel = SS.freeze(head, {}, NOW)
        self.assertEqual(sel["SELECTION_STATUS"],
                         "INSUFFICIENT_QUALIFYING_EVENTS")
        acct = sel["ROW_ACCOUNTING"]
        self.assertEqual(acct["UNACCOUNTED_ROWS"], 0)
        self.assertEqual(acct["ROWS_IN"], 600)
        self.assertEqual(acct["IDENTITY_FAILURES"], 600)

    def test_the_wider_board_now_progresses_past_the_defect(self):
        """Same eligibility rules; the exhausted universe reaches contests."""
        rows = load()["CONTEST_ROWS"]
        books = {m["slug"]: two_sided_book() for m in rows}
        act = {m["slug"]: active() for m in rows}
        # A now_iso inside the horizon of the fixture's own game times.
        starts = sorted(m["gameStartTime"] for m in rows
                        if m.get("gameStartTime"))
        now = starts[0]
        sel = SS.freeze(rows, books, now, activity_of=act)
        self.assertGreater(sel["CANONICAL_EVENTS_RESOLVED"], 0)
        self.assertEqual(sel["ROW_ACCOUNTING"]["UNACCOUNTED_ROWS"], 0)
        self.assertGreater(sel["ROW_ACCOUNTING"]["ROWS_IN"], 0)

    def test_the_identity_standard_was_not_weakened(self):
        import inspect
        src = inspect.getsource(SS.eligible_events)
        self.assertIn("LEVEL_V1_CONTEST", src)
        self.assertNotIn("LEVEL_V2_SUBJECT", src)


if __name__ == "__main__":
    unittest.main()
