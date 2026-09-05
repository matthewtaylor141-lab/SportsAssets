"""whale_exits walks a whale's book only while we hold one of his rows.

The evidence (2026-09-05, read from the service): sportsassets-workers
OOM-killed at 2 GiB about every twenty minutes (14 kills today), RSS
stepping up 350-540 MB in single minutes, and each of those minutes the
one in which a 24,000-position walk finished -- RN1's and
ferrariChampions2026's, 48 pages of 500 rows, back to back, every 120 s.
The mechanism inside the walk is another unit's; this one removes the
walks that were WASTE. The detector exists to catch a whale reducing a
position WE HOLD a per-fill row on, and today the live roster is rn1
alone, with ferrari and the others cut (clip 0): a whale we hold nothing
on needs no book walk.

So _cycle reads ONCE per cycle the whales we hold a live NON-MIRROR row
for and walks only those. An unheld whale's baseline is deleted so that
being held again starts from a first snapshot -- never a diff against a
book we stopped watching. An unreadable held set walks everyone, as
before. These tests drive the real _cycle and the real _fetch_positions
against a paging venue, so the page count is the walk's own, and the
exit dispatch is the harness of test_refused_exit_is_held.
"""

from __future__ import annotations

import inspect
import json
import logging
import re

import pytest

from sportsassets import live_executor as le
from sportsassets.workers import whale_exits as we

from tests.test_refused_exit_is_held import FakePool

LOGGER = "sportsassets.workers.whale_exits"
RN1 = {"username": "rn1", "address": "0xrn1"}
FERRARI = {"username": "ferrariChampions2026", "address": "0xfe787d2d"}
KEY = we._KEY % "rn1"
INFO_LINE = "whale-exit: no live rows for %s; book not walked"
WARN_LINE = ("whale-exit: held-whale read failed (%s); walking every "
             "roster book as before")
# The reaper's own wording (live_executor's ledger reaper), which
# _entry_in_flight matches by its first words for _NAMED_HORIZON.
POSITION_ERROR = ("venue holds a POSITION on this market that no ledger row "
                  "explains (held 12, explained 0, ours would be 12; no fill at "
                  "our cent in the trade log) — reconcile against the venue account")
FILLED_ROW = {"whale_username": "rn1", "status": "filled", "lane": None}
NAMED_ROW = {"whale_username": "rn1", "status": "error", "lane": None,
             "error": POSITION_ERROR}


class _Resp:
    def __init__(self, rows):
        self.status_code = 200
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


class _Venue:
    """The data API's /positions, paged the way the venue pages it: full
    pages while the book lasts, then a short or empty one. Every GET is
    recorded, so a walk that did not happen is a count of zero."""

    def __init__(self):
        self.books: dict[str, dict[str, float]] = {}
        self.calls: list[tuple[str, dict]] = []

    async def get(self, path, params=None):
        p = dict(params or {})
        self.calls.append((path, p))
        items = sorted(self.books.get(p.get("user"), {}).items())
        off, lim = int(p.get("offset", 0)), int(p.get("limit", 0))
        return _Resp([{"asset": a, "size": s} for a, s in items[off:off + lim]])

    def walks(self, address: str) -> int:
        return sum(1 for path, p in self.calls
                   if path == "/positions" and p.get("user") == address)


@pytest.fixture
def wired(monkeypatch):
    """A paging venue, a recorded exit dispatch, a two-whale roster and a
    page size of 2 so the walk actually pages."""
    venue = _Venue()
    calls: list[dict] = []
    outcome = {"reason": "mx_SOLD"}

    async def _copy(payload):
        calls.append(payload)
        return outcome["reason"]

    monkeypatch.setattr(le, "execute_copy", _copy)
    monkeypatch.setattr(we, "POSITIONS_PAGE", 2)
    monkeypatch.setattr("sportsassets.api.copies_record.COPY_WHALES",
                        {"rn1", "ferrarichampions2026"}, raising=False)
    return venue, calls, outcome


def _infos(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.INFO]


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.WARNING]


# ───────────────────────── (a) a held whale is walked as before ─────────────────────────

class TestAHeldWhaleIsWalkedExactlyAsBefore:
    @pytest.mark.asyncio
    async def test_the_page_count_the_diff_and_the_dispatch(self, wired):
        venue, calls, _ = wired
        pool = FakePool(held={"rn1"})
        venue.books["0xrn1"] = {"tokA": 500.0, "tokB": 100.0, "tokC": 10.0,
                                "tokD": 10.0, "tokE": 10.0}
        s1 = await we._cycle(venue, pool)
        assert venue.walks("0xrn1") == 3, "5 rows in pages of 2: 2, 2, 1"
        assert s1["first_snapshots"] == 1 and s1["whales"] == 1
        # .get: this test is the CONTROL and passes against the old
        # _cycle too; the key's unconditional presence is (f)'s pin
        assert s1["attempted"] == 1 and s1.get("unheld_skipped", 0) == 0
        assert calls == [] and pool.deletes == []
        # he leaves tokA entirely: the vanish on a live market is a full exit
        venue.books["0xrn1"].pop("tokA")
        s2 = await we._cycle(venue, pool)
        assert venue.walks("0xrn1") == 6, "4 rows: 2, 2, then the empty page"
        assert [(c["whale_username"], c["asset"], c["side"], c["closed_frac"])
                for c in calls] == [("rn1", "tokA", "SELL", 1.0)]
        assert s2["exits_sold"] == 1 and s2["exits"] == 1 and s2["vanished_live"] == 1
        # and it does not fire twice
        s3 = await we._cycle(venue, pool)
        assert len(calls) == 1 and s3["exit_attempts"] == 0
        assert pool.deletes == []

    @pytest.mark.asyncio
    async def test_a_refused_exit_is_still_held_and_retried(self, wired):
        """test_refused_exit_is_held's first case, through the gate."""
        venue, calls, outcome = wired
        pool = FakePool(held={"rn1"})
        venue.books["0xrn1"] = {"tokA": 500.0, "tokB": 100.0}
        await we._cycle(venue, pool)
        venue.books["0xrn1"].pop("tokA")
        outcome["reason"] = "mx_overspend_halt"
        s = await we._cycle(venue, pool)
        assert s["exits_pending"] == 1 and s["pend_mx_overspend_halt"] == 1
        outcome["reason"] = "mx_SOLD"
        s = await we._cycle(venue, pool)
        assert [c["asset"] for c in calls] == ["tokA", "tokA"]
        assert s["exits_sold"] == 1

    @pytest.mark.asyncio
    async def test_a_shrink_carries_its_measured_fraction(self, wired):
        venue, calls, _ = wired
        pool = FakePool(held={"rn1"})
        venue.books["0xrn1"] = {"tokA": 500.0}
        await we._cycle(venue, pool)
        venue.books["0xrn1"]["tokA"] = 200.0
        await we._cycle(venue, pool)
        assert calls[-1]["closed_frac"] == pytest.approx(0.6)

    @pytest.mark.parametrize("stored", ["RN1", "Rn1", "rn1"])
    @pytest.mark.asyncio
    async def test_the_compare_is_case_insensitive(self, wired, stored):
        """The roster spells the name one way and live_orders another:
        lower() on both sides, or a held whale reads as unheld. Pinned
        by behaviour (the mutation review's minor), not by source."""
        venue, calls, _ = wired
        pool = FakePool(live_rows=[{"whale_username": "rn1", "status": "filled",
                                    "lane": None}],
                        roster=[{"username": stored, "address": "0xrn1"}])
        pool.state[KEY] = json.dumps({"tokA": 500.0, "keep": 10.0})
        venue.books["0xrn1"] = {"keep": 10.0}
        stats = await we._cycle(venue, pool)
        assert venue.walks("0xrn1") == 1
        assert stats["unheld_skipped"] == 0
        assert pool.deletes == []
        assert [c["asset"] for c in calls] == ["tokA"]


# ───────────────────────── (b) an unheld whale is not walked ─────────────────────────

class TestAnUnheldWhaleIsNotWalked:
    @pytest.mark.asyncio
    async def test_zero_requests_baseline_retired_counted_and_named(self, wired, caplog):
        venue, calls, _ = wired
        pool = FakePool(held=set())
        # an old baseline, and a book that has moved since: a diff here
        # would read tokA as a full exit
        pool.state[KEY] = json.dumps({"tokA": 500.0, "tokB": 100.0})
        venue.books["0xrn1"] = {"tokB": 100.0}
        with caplog.at_level(logging.INFO, logger=LOGGER):
            stats = await we._cycle(venue, pool)
        assert venue.calls == [], "no /positions request at all"
        assert KEY not in pool.state and pool.deletes == [KEY]
        assert stats["unheld_skipped"] == 1
        assert stats["attempted"] == 0 and stats["whales"] == 0
        assert calls == []
        assert _infos(caplog) == [INFO_LINE % "rn1"]

    @pytest.mark.asyncio
    async def test_one_line_names_every_skipped_whale(self, wired, caplog):
        venue, _calls, _ = wired
        pool = FakePool(held=set(), roster=[RN1, FERRARI])
        venue.books["0xrn1"] = {"tokA": 1.0}
        venue.books["0xfe787d2d"] = {"tokZ": 1.0}
        with caplog.at_level(logging.INFO, logger=LOGGER):
            stats = await we._cycle(venue, pool)
        assert stats["unheld_skipped"] == 2 and venue.calls == []
        assert _infos(caplog) == [INFO_LINE % "ferrariChampions2026, rn1"]

    @pytest.mark.asyncio
    async def test_the_held_one_is_walked_beside_the_unheld_one(self, wired, caplog):
        """Today's roster shape: rn1 held, ferrari cut. One walk."""
        venue, _calls, _ = wired
        pool = FakePool(held={"rn1"}, roster=[RN1, FERRARI])
        venue.books["0xrn1"] = {"tokA": 1.0}
        venue.books["0xfe787d2d"] = {"tokZ": 1.0}
        pool.state[we._KEY % "ferrarichampions2026"] = json.dumps({"tokZ": 1.0})
        with caplog.at_level(logging.INFO, logger=LOGGER):
            stats = await we._cycle(venue, pool)
        assert venue.walks("0xrn1") == 1 and venue.walks("0xfe787d2d") == 0
        assert stats["unheld_skipped"] == 1 and stats["whales"] == 1
        assert pool.deletes == [we._KEY % "ferrarichampions2026"]
        assert _infos(caplog) == [INFO_LINE % "ferrariChampions2026"]

    @pytest.mark.asyncio
    async def test_no_line_when_nobody_was_skipped(self, wired, caplog):
        venue, _calls, _ = wired
        pool = FakePool(held={"rn1"})
        venue.books["0xrn1"] = {"tokA": 1.0}
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await we._cycle(venue, pool)
        assert _infos(caplog) == []

    def test_a_cycle_that_skips_everyone_beats_ok(self):
        """Nothing attempted is a configuration state, not an outage."""
        assert we._beat_status({"attempted": 0, "fetch_failed": 0,
                                "unheld_skipped": 3}) == "ok"


# ───────────────────────── (c) held again: a first snapshot ─────────────────────────

class TestBeingHeldAgainStartsFromAFirstSnapshot:
    @pytest.mark.asyncio
    async def test_no_exit_fires_from_the_diff_across_the_gap(self, wired):
        venue, calls, _ = wired
        pool = FakePool(held={"rn1"})
        # `keep` stays for the whole test: an EMPTY read is refused by
        # guard_empty on its own, which is not the property under test
        venue.books["0xrn1"] = {"tokA": 500.0, "tokB": 100.0, "keep": 10.0}
        await we._cycle(venue, pool)                      # held: first snapshot
        assert json.loads(pool.state[KEY]) == {"tokA": 500.0, "tokB": 100.0,
                                               "keep": 10.0}
        pool.held = set()
        s2 = await we._cycle(venue, pool)                 # unheld: retired
        venue.books["0xrn1"].pop("tokA")                  # he leaves while we hold nothing
        pool.held = {"rn1"}
        s3 = await we._cycle(venue, pool)                 # held again
        # THE MONEY ASSERTION FIRST: against the old _cycle this is the
        # SELL of tokA placed from a diff across the gap
        assert calls == [], "an exit fired from a baseline we had stopped watching"
        assert s2["unheld_skipped"] == 1 and s2["whales"] == 0
        assert s3["first_snapshots"] == 1 and s3["unheld_skipped"] == 0
        assert json.loads(pool.state[KEY]) == {"tokB": 100.0, "keep": 10.0}
        # and the detector is alive after the re-hold
        venue.books["0xrn1"].pop("tokB")
        await we._cycle(venue, pool)
        assert [c["asset"] for c in calls] == ["tokB"]

    @pytest.mark.asyncio
    async def test_the_control_held_throughout_fires_that_exit(self, wired):
        """The same book with no gap: tokA's vanish IS an exit. This is
        the order the stale diff would have placed."""
        venue, calls, _ = wired
        pool = FakePool(held={"rn1"})
        venue.books["0xrn1"] = {"tokA": 500.0, "tokB": 100.0, "keep": 10.0}
        await we._cycle(venue, pool)
        await we._cycle(venue, pool)
        venue.books["0xrn1"].pop("tokA")
        await we._cycle(venue, pool)
        assert [c["asset"] for c in calls] == ["tokA"]


# ───────────────────────── (d) an unreadable held set walks everyone ─────────────────────────

class TestAnUnreadableHeldSetWalksEveryone:
    @pytest.mark.asyncio
    async def test_every_whale_walked_and_the_class_named(self, wired, caplog):
        venue, calls, _ = wired
        pool = FakePool(held=RuntimeError('relation "live_orders" does not exist'),
                        roster=[RN1, FERRARI])
        venue.books["0xrn1"] = {"tokA": 500.0, "keep": 10.0}
        venue.books["0xfe787d2d"] = {"tokZ": 5.0}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            stats = await we._cycle(venue, pool)
        # the read exists and failed by name (the old _cycle never
        # reads the held set, so it has no line to log here)
        assert _warnings(caplog) == [WARN_LINE % "RuntimeError"]
        assert stats["unheld_skipped"] == 0
        assert stats["attempted"] == 2 and stats["whales"] == 2
        assert stats["first_snapshots"] == 2
        # rn1: a full page of 2, then the empty page; ferrari: one short page
        assert venue.walks("0xrn1") == 2 and venue.walks("0xfe787d2d") == 1
        assert pool.deletes == []
        # and the exit still reaches the dispatch, exactly as before
        venue.books["0xrn1"].pop("tokA")
        await we._cycle(venue, pool)
        assert [c["asset"] for c in calls] == ["tokA"]

    @pytest.mark.asyncio
    async def test_a_baseline_is_never_retired_on_a_failed_read(self, wired):
        venue, _calls, _ = wired
        pool = FakePool(held=ValueError("bad row"))
        pool.state[KEY] = json.dumps({"tokA": 500.0})
        venue.books["0xrn1"] = {"tokA": 500.0}
        await we._cycle(venue, pool)
        assert pool.deletes == [] and KEY in pool.state


# ───────────────────────── (e) only 'mirror' rows is unheld ─────────────────────────

class TestAMirrorOnlyWhaleIsUnheld:
    """The fixture ledger filters live_orders rows ONLY by the clauses
    that stand in the statement's text (test_mirror_live_consumers'
    discipline), so a clause that goes missing admits the rows it was
    excluding and the case below fails."""

    @pytest.mark.parametrize("rows, held", [
        ([{"whale_username": "RN1", "status": "filled", "lane": "mirror"}], False),
        ([{"whale_username": "rn1", "status": "filled", "lane": None}], True),
        ([{"whale_username": "rn1", "status": "filled", "lane": "ioc"}], True),
        ([{"whale_username": "rn1", "status": "submitting", "lane": "rest"}], True),
        ([{"whale_username": "RN1", "status": "exiting", "lane": None}], True),
        ([{"whale_username": "rn1", "status": "settled", "lane": None}], False),
        ([{"whale_username": "rn1", "status": "cashed_out", "lane": None}], False),
        ([{"whale_username": "rn1", "status": "unfilled", "lane": None}], False),
        ([{"whale_username": None, "status": "filled", "lane": None}], False),
        ([{"whale_username": "rn1", "status": "filled", "lane": "mirror"},
          {"whale_username": "rn1", "status": "filled", "lane": None}], True),
        ([{"whale_username": "rn1", "status": "cashed_out", "lane": None},
          {"whale_username": "rn1", "status": "filled", "lane": "mirror"}], False),
        ([], False),
        # the reaper-named 'error' rows _entry_in_flight treats as an
        # entry in flight for _NAMED_HORIZON: held
        ([{"whale_username": "rn1", "status": "error", "lane": None,
           "error": POSITION_ERROR}], True),
        ([{"whale_username": "rn1", "status": "error", "lane": None,
           "error": "venue has no record of order abc after 3 reads"}], True),
        # a plain rejection is an 'error' row nothing of ours stands behind
        ([{"whale_username": "rn1", "status": "error", "lane": None,
           "error": "rejected: insufficient balance"}], False),
        # a named row past the 48-hour horizon is no longer in flight
        ([{"whale_username": "rn1", "status": "error", "lane": None,
           "error": POSITION_ERROR, "age_s": 48 * 3600 + 1}], False),
        # and a named row on the mirror lane is the book's, not ours
        ([{"whale_username": "rn1", "status": "error", "lane": "mirror",
           "error": POSITION_ERROR}], False),
    ])
    @pytest.mark.asyncio
    async def test_which_rows_hold_him(self, wired, rows, held):
        venue, _calls, _ = wired
        pool = FakePool(live_rows=rows)
        pool.state[KEY] = json.dumps({"tokA": 1.0})
        venue.books["0xrn1"] = {"tokA": 1.0}
        stats = await we._cycle(venue, pool)
        assert (venue.walks("0xrn1") == 1) is held
        # .get so the held rows are a control that passes against the
        # old _cycle as well; (f) pins the key's presence
        assert stats.get("unheld_skipped", 0) == (0 if held else 1)
        assert (KEY in pool.state) is held

    def test_the_statement_is_the_one_the_predicate_audit_spells(self):
        assert we._HELD_SQL == (
            "SELECT DISTINCT lower(whale_username) AS whale FROM live_orders\n"
            " WHERE (status IN ('filled', 'submitting', 'exiting')\n"
            "        OR (status = 'error'\n"
            "            AND (error LIKE 'venue holds a POSITION%' "
            "OR error LIKE 'venue has no record of order%')\n"
            "            AND placed_at > now() - interval '48 hours'))\n"
            "   AND COALESCE(lane,'') <> 'mirror'")
        # the 48-hour literal is written here, not imported: pinned to
        # the horizon _entry_in_flight uses so the two cannot drift
        assert f"interval '{le._NAMED_HORIZON}'" in we._HELD_SQL
        # the spelling that would DROP every NULL-lane row must not appear
        bare = re.compile(r"(?<![\w.(])lane\s*(?:<>|!=)\s*'mirror'")
        assert not bare.search(we._HELD_SQL)

    def test_the_statuses_are_the_ones_live_executor_treats_as_held(self):
        """'filled' is the row mirror_exit sells; 'submitting' is an
        entry in flight it pends behind; 'exiting' is its own claim,
        released to 'filled' by the reaper when the venue still holds;
        and the reaper-named 'error' rows are the fourth shape
        _entry_in_flight treats as in flight, by the same two LIKE
        prefixes, verbatim."""
        mx = inspect.getsource(le.mirror_exit)
        assert "status = 'filled'" in mx
        assert "SET status='exiting'" in mx and "status='filled' RETURNING id" in mx
        inflight = inspect.getsource(le._entry_in_flight)
        assert "status = 'submitting'" in inflight
        assert "_release_exit_claim" in inspect.getsource(le._reap_stale_exiting)
        prefixes = re.findall(r"error LIKE '([^%']*)%'", we._HELD_SQL)
        assert prefixes == ["venue holds a POSITION", "venue has no record of order"]
        for p in prefixes:
            assert f"error LIKE '{p}%'" in inflight, p
        assert "status = 'error'" in inflight


# ───────────────────────── (e2) a reaper-named error row keeps him held ─────────────────────────

class TestANamedErrorRowKeepsHimHeld:
    """The exit-semantics review's reproduction. live_executor's
    _entry_in_flight treats a row the reaper named "venue holds a
    POSITION ..." as an entry in flight for _NAMED_HORIZON, so
    mirror_exit PENDS an exit behind it (mx_entry_in_flight is in
    EXIT_PENDING_REASONS) and the exit is retried until the reaper books
    the fill. A held set that did not admit that row shape unheld the
    whale for the whole reconcile window: baseline deleted, walk stopped,
    the exit dropped, and the re-hold's first snapshot already showed
    his post-exit book."""

    @pytest.mark.parametrize("outcome_reason", ["mx_SOLD", "mx_entry_in_flight"])
    @pytest.mark.asyncio
    async def test_an_exit_inside_the_reconcile_window_is_dispatched(
            self, wired, outcome_reason):
        venue, calls, outcome = wired
        pool = FakePool(live_rows=[dict(FILLED_ROW)])
        venue.books["0xrn1"] = {"tokA": 500.0, "keep": 10.0}
        s1 = await we._cycle(venue, pool)                 # held: first snapshot
        assert s1["first_snapshots"] == 1
        assert json.loads(pool.state[KEY]) == {"tokA": 500.0, "keep": 10.0}
        pool.live_rows = [dict(NAMED_ROW)]                # the reaper names his only row
        venue.books["0xrn1"].pop("tokA")                  # he leaves inside the window
        outcome["reason"] = outcome_reason
        s2 = await we._cycle(venue, pool)
        # THE MONEY ASSERTION: he is still walked and the exit reaches
        # the dispatch; against the unwidened statement this is [] and
        # the baseline is gone
        assert [c["asset"] for c in calls] == ["tokA"], \
            "tokA's exit never reached execute_copy"
        assert s2["unheld_skipped"] == 0 and s2["whales"] == 1
        assert pool.deletes == [] and KEY in pool.state
        if outcome_reason == "mx_SOLD":
            assert s2["exits_sold"] == 1
        else:
            assert s2["exits_pending"] == 1 and s2["pend_mx_entry_in_flight"] == 1
            # pinned in the snapshot, so the retry finds it again
            assert json.loads(pool.state[KEY])["tokA"] == 500.0
        pool.live_rows = [dict(FILLED_ROW)]               # the reaper promotes the row
        outcome["reason"] = "mx_SOLD"
        s3 = await we._cycle(venue, pool)
        assert s3["first_snapshots"] == 0 and s3["unheld_skipped"] == 0
        if outcome_reason == "mx_SOLD":
            assert [c["asset"] for c in calls] == ["tokA"], "nothing new"
            assert s3["exit_attempts"] == 0
        else:
            assert [c["asset"] for c in calls] == ["tokA", "tokA"], "the retry"
            assert s3["exits_sold"] == 1
        # and the whale is quiet after that
        s4 = await we._cycle(venue, pool)
        assert s4["exit_attempts"] == 0 and len(calls) == len(
            ["tokA"] if outcome_reason == "mx_SOLD" else ["tokA", "tokA"])


# ───────────────────────── the mutation review's minors, by behaviour ─────────────────────────

class TestTheMinorsArePinnedByBehaviour:
    @pytest.mark.asyncio
    async def test_the_delete_is_the_one_row_by_key(self, wired):
        """`WHERE key <> $1` survived the mutation round: the statement
        itself is pinned, argument and all."""
        venue, _calls, _ = wired
        pool = FakePool(held=set())
        pool.state[KEY] = json.dumps({"tokA": 1.0})
        venue.books["0xrn1"] = {"tokA": 1.0}
        stats = await we._cycle(venue, pool)
        assert stats["unheld_skipped"] == 1
        deletes = [(s, a) for s, a in pool.sqls_executed
                   if s.lstrip().upper().startswith("DELETE")]
        assert deletes == [("DELETE FROM ingestion_state WHERE key = $1", (KEY,))]

    @pytest.mark.asyncio
    async def test_the_gate_sits_after_the_roster_membership_check(self, wired, caplog):
        """A whale on the whales table but not in COPY_WHALES is skipped
        BEFORE the gate: not counted, not retired, not named."""
        venue, _calls, _ = wired
        pool = FakePool(held=set(), roster=[RN1, {"username": "stranger",
                                                  "address": "0xstr"}])
        pool.state[we._KEY % "stranger"] = json.dumps({"tokZ": 1.0})
        venue.books["0xrn1"] = {"tokA": 1.0}
        venue.books["0xstr"] = {"tokZ": 1.0}
        with caplog.at_level(logging.INFO, logger=LOGGER):
            stats = await we._cycle(venue, pool)
        assert venue.calls == []
        assert stats["unheld_skipped"] == 1
        assert pool.deletes == [KEY]
        assert we._KEY % "stranger" in pool.state
        assert _infos(caplog) == [INFO_LINE % "rn1"]

    @pytest.mark.asyncio
    async def test_a_null_whale_in_the_held_read_is_dropped(self):
        class NullRow(FakePool):
            async def fetch(self, sql, *args):
                if "lower(whale_username)" in sql and "FROM live_orders" in sql:
                    return [{"whale": None}, {"whale": "rn1"}]
                return await super().fetch(sql, *args)

        assert await we._held_whales(NullRow()) == {"rn1"}


# ───────────────────────── (f) the counter is always present ─────────────────────────

class TestTheCounterIsAlwaysPresent:
    def test_in_the_stats_head(self):
        src = inspect.getsource(we._cycle)
        head = src[:src.index("all_sibs")]
        assert '"unheld_skipped": 0' in head

    @pytest.mark.asyncio
    async def test_zero_on_a_cycle_with_no_skips(self, wired):
        venue, _calls, _ = wired
        pool = FakePool(held={"rn1"})
        venue.books["0xrn1"] = {"tokA": 1.0}
        stats = await we._cycle(venue, pool)
        assert stats["unheld_skipped"] == 0 and venue.walks("0xrn1") == 1


# ───────────────────────── the shape of the change ─────────────────────────

class TestTheGateStandsBeforeTheWalkAndNowhereElse:
    def test_read_once_before_the_loop_skip_before_attempted(self):
        src = inspect.getsource(we._cycle)
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        assert code.count("_held_whales(pool)") == 1
        assert code.index("FROM whales") < code.index("_held_whales(pool)") \
            < code.index("for r in rows:")
        i = code.index("uname.lower() not in held")
        assert code.index("for r in rows:") < i < code.index('stats["attempted"] += 1')
        assert i < code.index("DELETE FROM ingestion_state") \
            < code.index('stats["unheld_skipped"] += 1')
        assert code.index(INFO_LINE) > code.index("_save_retry(")

    def test_the_walk_and_everything_past_it_are_untouched(self):
        src = inspect.getsource(we._cycle)
        for line in ("now, sibs, seen = await _fetch_positions(http, r[\"address\"])",
                     "except TruncatedPositions as exc:",
                     "guard_empty(prev, now, r[\"address\"])",
                     "if not prev:",
                     "found = diff_exits(prev, now, exclusion)",
                     "found = rotate_for_fairness(found, tried)",
                     "acting = found[:MAX_EXITS_PER_CYCLE]",
                     "reason = await execute_copy({\"whale_username\": uname,"):
            assert line in src, line

    @pytest.mark.asyncio
    async def test_a_retire_that_fails_walks_him_as_before(self, wired, caplog):
        """A baseline we cannot retire is one we must keep fresh: the
        stale-diff hazard is only closed while the row is gone."""
        venue, calls, _ = wired

        class NoDelete(FakePool):
            async def execute(self, sql, *a):
                if sql.lstrip().upper().startswith("DELETE"):
                    raise RuntimeError("read-only transaction")
                return await super().execute(sql, *a)

        pool = NoDelete(held=set())
        pool.state[KEY] = json.dumps({"tokA": 500.0, "tokB": 100.0})
        venue.books["0xrn1"] = {"tokB": 100.0}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            stats = await we._cycle(venue, pool)
        assert stats["unheld_skipped"] == 0 and stats["whales"] == 1
        assert venue.walks("0xrn1") == 1
        assert [c["asset"] for c in calls] == ["tokA"], "today's path, byte for byte"
        assert any("could not retire rn1's snapshot (RuntimeError)" in w
                   for w in _warnings(caplog))
