"""Mirror P1, step 7a: the executor's own referees keep a book intact.

Position mirroring (owner order 2026-09-02, "go for it, let's get this
working") holds ONE standing live_orders row per book -- lane='mirror',
status='filled' from the INSERT, placed once at open and held for days.
The per-fill executor reads that row through six of its own statements,
and the panel review's predicate audit (spec 1d, GAIN A CLAUSE; spec 3.2
for the exit path) gives each exactly one clause:

  never-add prior / one-per-game   the book outlives the 48 h window: the
                                   other outcome and the game stay claimed
  _his_row (+ lane in its SELECT)  after a switch-off the whale's own fresh
                                   BUY on the standing asset must not become
                                   an add leg merged INTO the book
  add-holder                       belt for the same case: the asset stays
                                   taken
  _merge_add_leg / named merge     a merge can never target a mirror row
  mirror_exit _sel_tail            neither exit lane can claim, sell or
                                   close_position a book, whatever any
                                   switch says; his vanish on a booked
                                   token is named mx_mirror_owns_market,
                                   a settled reason like the one it
                                   shadows

Two things are pinned per site, the way test_mirror_live_consumers pins
the non-executor half:

1. the SOURCE: the clause stands in the function's CODE (comment lines
   stripped, so a clause named only in prose cannot pass), the text it
   replaced or sits beside is otherwise unchanged, and every refusal
   text the census parses is byte-identical;
2. the BEHAVIOUR: the function is driven against a fixture ledger that
   evaluates the one predicate this file is about the way Postgres would
   -- and only when that clause stands in the statement's code -- so a
   NULL-lane row (every row that exists today) keeps its path and a
   mirror row is refused by name.

Postgres is not in the test tree. Nothing here places an order: every
drive ends in a named refusal, and the one drive that proceeds is the
NULL-lane control, which proves the fixture has teeth.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import types

import pytest

from sportsassets import live_executor as le
from sportsassets import pmus
from tests.test_rest_lane_end_to_end import TODAY, _Pool, _payload, _wire

# The one clause, bare (every statement here is single-table).
_GUARD = "COALESCE(lane,'') <> 'mirror'"
_GUARD_RE = re.compile(r"COALESCE\(lane,\s*''\)\s*<>\s*'mirror'")
# The spelling that would DROP every NULL-lane row -- must never appear.
_BARE_RE = re.compile(r"(?<![\w.(])lane\s*(?:<>|!=)\s*'mirror'")
# The window the two referees keep, before and after the widening.
_BARE_WINDOW = "AND placed_at > now() - interval '48 hours'"
_WIDE_WINDOW = "AND (placed_at > now() - interval '48 hours' OR lane = 'mirror')"
_MIRROR_TEXT = "never-add: this market is held by the mirror book"
_LEGACY_TEXT = "never-add: this market was already copied"

DAY = 86400.0


def _code(src: str) -> str:
    """Comment lines removed: Python '#' lines and SQL '--' lines."""
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith(("#", "--")))


def _src(fn) -> str:
    return _code(inspect.getsource(fn))


def _flat(s: str) -> str:
    return " ".join(s.split())


def _sql(py_src: str) -> str:
    """Python source flattened with its adjacent string literals joined,
    so a statement spelled across lines reads as the text Postgres
    receives (comment lines already stripped by the caller)."""
    return _flat(re.sub(r'"\s*f?"', "", _flat(py_src)))


def _pos(text: str, needle: str) -> int:
    assert needle in text, f"not found: {needle!r}"
    return text.index(needle)


# ───────────────────────────── the fixture ledger ─────────────────────────────

def _lane_admits(sql: str, row: dict) -> bool:
    """The lane guard as Postgres reads it: a 'mirror' lane fails it,
    NULL and every other lane pass; an unguarded statement admits all."""
    if not _GUARD_RE.search(_code(sql)):
        return True
    return (row.get("lane") or "") != "mirror"


def _window_admits(sql: str, row: dict) -> bool:
    """The 48 h clock, widened or not, exactly as the statement spells
    it. A statement that carries neither spelling cannot be evaluated
    and fails loudly rather than admitting a row by default."""
    s = _flat(_code(sql))
    recent = float(row.get("age_s") or 0.0) <= 48 * 3600
    if _WIDE_WINDOW in s:
        return recent or (row.get("lane") or "") == "mirror"
    if _BARE_WINDOW in s:
        return recent
    raise AssertionError(f"the fixture ledger cannot evaluate the window in {s[:120]!r}")


def _row(**over) -> dict:
    r = {"id": 7, "status": "filled", "whale": "rn1", "asset": "123",
         "us_market_slug": f"tsc-epl-ars-che-{TODAY}-o3pt5", "lane": None,
         "age_s": 0.0, "filled_shares": 100.0, "fill_price": 0.50,
         "filled_usd": 50.0, "adds": None, "tx_hash": "0xaaa", "qty": 100.0}
    r.update(over)
    return r


def _book(**over) -> dict:
    """A three-day-old mirror book's standing row, unless overridden."""
    return _row(**{"id": 900, "whale": "mirrored", "lane": "mirror",
                   "age_s": 3 * DAY, "filled_shares": 40.0, "fill_price": 0.42,
                   "filled_usd": 16.8, "tx_hash": None, **over})


class _BookPool(_Pool):
    """maybe_execute's pool, answering the asset referee, the add-holder,
    the never-add prior and the one-per-game read from `rows`, each
    under the predicate this file is about. `holder_ignores_guard` lets
    one test switch the add-holder belt off to prove the referee below
    it holds on its own."""

    def __init__(self, rows, *, holder_ignores_guard=False):
        super().__init__()
        self.rows = [dict(r) for r in rows]
        self.holder_ignores_guard = holder_ignores_guard
        self.inserts: list = []
        self.merges: list = []
        self.sqls: list[str] = []

    async def fetchval(self, sql, *a):
        s = _flat(sql)
        self.sqls.append(s)
        if "INSERT INTO live_orders" in s:
            self.inserts.append((s, a))
            return 101
        if "SELECT 1 FROM live_orders WHERE asset = $1" in s:
            # the cheap referee: no window, no lane clause (spec 1d,
            # INCLUDE: already_taken claims the long token as it is)
            return 1 if any(r["asset"] == a[0]
                            and r["status"] in ("submitting", "filled", "settled")
                            and r["whale"] not in ("manual", "underdog")
                            for r in self.rows) else None
        return await super().fetchval(sql, *a)

    async def fetchrow(self, sql, *a):
        s = _flat(sql)
        self.sqls.append(s)
        if "/* add-holder */" in s:
            cands = [r for r in self.rows
                     if r["asset"] == a[0]
                     and r["status"] in ("submitting", "filled", "settled")
                     and r["whale"] not in ("manual", "underdog")
                     and (self.holder_ignores_guard or _lane_admits(s, r))]
            cands.sort(key=lambda r: r["status"] != "submitting")
            if not cands:
                return None
            r = cands[0]
            return {"status": r["status"], "whale": r["whale"],
                    "recent": float(r["age_s"]) <= 48 * 3600}
        if "/* prior-copy */" in s:
            cands = [r for r in self.rows
                     if r["us_market_slug"] == a[1] and r["id"] != a[0]
                     and r["status"] in ("filled", "submitting")
                     and r["whale"] != "underdog" and _window_admits(s, r)]
            cands.sort(key=lambda r: (r["asset"] != a[2], r["status"] != "submitting",
                                      float(r["age_s"])))
            if not cands:
                return None
            r = cands[0]
            out = {k: r[k] for k in ("id", "status", "whale", "asset", "filled_shares",
                                     "fill_price", "filled_usd", "adds", "tx_hash")}
            # the lane reaches the row only when the SELECT projects it
            if ", lane, " in s.split("FROM live_orders")[0]:
                out["lane"] = r["lane"]
            return out
        if "SET status = 'merged'" in s:
            self.merges.append((s, a))
            return None
        return await super().fetchrow(sql, *a)

    async def fetch(self, sql, *a):
        s = _flat(sql)
        self.sqls.append(s)
        if "SELECT us_market_slug FROM live_orders" in s:
            return [{"us_market_slug": r["us_market_slug"]} for r in self.rows
                    if r["status"] in ("filled", "submitting") and r["id"] != a[0]
                    and r["whale"] != "underdog" and _window_admits(s, r)]
        return await super().fetch(sql, *a)


def _rejections(pool) -> list[str]:
    return [a[1] for s, a in pool.updates if "SET status='rejected', error=$2" in s]


def _stamps(pool) -> list:
    return [u for u in pool.updates
            if "SET raw = COALESCE(raw, '{}'::jsonb) || $2::jsonb WHERE id=$1" in u[0]]


def _run(monkeypatch, pool, candidate_slug=None, reaction=5.0, **over):
    slug = candidate_slug or f"tsc-epl-ars-che-{TODAY}-o3pt5"
    calls = _wire(monkeypatch, pool, slug, gtc_final_filled=0.0)
    monkeypatch.setattr(le, "ADDS_ENABLED", True)
    over.setdefault("tx_hash", "0xbbb")
    asyncio.run(le.maybe_execute(_payload(**over), reaction))
    return calls


# ───────────────────────────── 1. the source pins ─────────────────────────────

class TestTheFixtureLedgerHasTeeth:
    def test_the_lane_guard_drops_a_mirror_row_and_keeps_every_other(self):
        guarded = f"SELECT 1 FROM live_orders WHERE asset = $1 AND {_GUARD}"
        assert not _lane_admits(guarded, _row(lane="mirror"))
        for lane in (None, "", "ioc", "rest", "chain"):
            assert _lane_admits(guarded, _row(lane=lane)), lane
        assert _lane_admits("SELECT 1 FROM live_orders WHERE asset = $1", _row(lane="mirror"))

    def test_a_clause_in_a_comment_line_is_not_a_clause(self):
        sql = f"SELECT 1 FROM live_orders\n-- {_GUARD}\nWHERE asset = $1"
        assert _lane_admits(sql, _row(lane="mirror"))

    def test_the_window_reads_both_spellings_and_refuses_a_third(self):
        old, book = _row(age_s=3 * DAY), _book()
        assert not _window_admits(f"WHERE x {_BARE_WINDOW}", old)
        assert not _window_admits(f"WHERE x {_BARE_WINDOW}", book)
        assert not _window_admits(f"WHERE x {_WIDE_WINDOW}", old)
        assert _window_admits(f"WHERE x {_WIDE_WINDOW}", book)
        assert _window_admits(f"WHERE x {_WIDE_WINDOW}", _row(age_s=3600.0))
        with pytest.raises(AssertionError):
            _window_admits("WHERE placed_at > now() - interval '47 hours'", old)


class TestTheNeverAddReferee:
    def _prior_query(self) -> str:
        s = _sql(_src(le.maybe_execute))
        i = _pos(s, "/* prior-copy */")
        # from the SELECT that carries the marker to its parameters
        return s[s.rfind("prior = await pool.fetchrow(", 0, i):
                 s.index('row_id, mapping["market_slug"], str(payload["asset"]))', i)]

    def test_the_window_is_widened_for_the_mirror_lane_only(self):
        q = self._prior_query()
        assert _WIDE_WINDOW in q
        # the replaced predicate is gone as a stand-alone clause and
        # survives, to the character, inside the widened one
        assert _BARE_WINDOW not in q
        assert "placed_at > now() - interval '48 hours'" in q
        assert q.count("interval '48 hours'") == 1

    def test_every_other_predicate_of_the_query_is_unchanged(self):
        q = self._prior_query()
        for text in ("WHERE us_market_slug = $2 AND id <> $1",
                     "AND (status IN ('filled', 'submitting') OR (status = 'error' AND "
                     "(error LIKE 'venue holds a POSITION%' OR error LIKE 'ORPHAN FILL RECORDED%' "
                     "OR error LIKE 'venue has no record of order%')))",
                     "AND COALESCE(whale_username, '') <> 'underdog'",
                     "ORDER BY (asset = $3) DESC, (status = 'submitting') DESC, placed_at DESC LIMIT 1"):
            assert text in q, text

    def test_the_select_projects_the_lane_the_referee_reads(self):
        q = self._prior_query()
        assert ", lane, " in q.split("FROM live_orders")[0]

    def test_his_row_refuses_a_mirror_prior_and_names_the_book(self):
        s = _src(le.maybe_execute)
        assert '_mirror_prior = (_row_get(prior, "lane") or "") == "mirror"' in s
        i = _pos(s, "_his_row = (ADDS_ENABLED and reaction is not None and _same_leg")
        blk = s[i:i + 400]
        assert 'and _row_get(prior, "status") == "filled"' in blk
        assert 'and _row_get(prior, "whale") == username' in blk
        assert "and not _mirror_prior)" in blk
        j = _pos(s, f'"{_MIRROR_TEXT}"')
        assert "if _mirror_prior else" in s[j:j + 120]
        assert s[j:j + 400].index(f'"{_LEGACY_TEXT}"') > 0

    def test_the_legacy_refusal_texts_are_byte_identical(self):
        """gate_edge parses these off the row and four test files match
        on them; a changed byte is a changed census."""
        s = inspect.getsource(le.maybe_execute)
        for text in (f'"{_LEGACY_TEXT}"',
                     '" (adds off)"',
                     '" (add refused: not a fresh detection)"',
                     '" (add refused: his buy is the other outcome of "',
                     '"a market we hold)"',
                     '" (add refused: row not filled or another whale\'s)"',
                     '" (add refused: same transaction as a leg we hold "',
                     '"— a twin detection of one fill)"',
                     'f" (add refused: {len(_adds)} legs already)"',
                     '" (add refused: this trade already merged)"',
                     '" (add refused: standing row holds no shares)"',
                     '"one position per game"',
                     '"no-stack: account already holds this market"'):
            assert text in s, text
        from sportsassets.analytics import gate_edge
        assert gate_edge.gate_of(_MIRROR_TEXT) == "never_add"
        assert gate_edge.gate_of(_LEGACY_TEXT) == "never_add"


class TestTheOnePerGameReferee:
    def _held_query(self) -> str:
        s = _sql(_src(le.maybe_execute))
        i = _pos(s, "SELECT us_market_slug FROM live_orders")
        return s[i:s.index("row_id)", i)]

    def test_the_window_is_widened_for_the_mirror_lane_only(self):
        q = self._held_query()
        assert _WIDE_WINDOW in q
        assert _BARE_WINDOW not in q
        assert q.count("interval '48 hours'") == 1

    def test_every_other_predicate_of_the_query_is_unchanged(self):
        q = self._held_query()
        assert ("SELECT us_market_slug FROM live_orders WHERE status IN ('filled', 'submitting') "
                "AND id <> $1 AND us_market_slug IS NOT NULL "
                "AND COALESCE(whale_username, '') <> 'underdog' ") in q

    def test_the_no_stack_carve_out_keeps_its_own_clock(self):
        """Spec 1d lists the dog_owned read under INCLUDE, unchanged: a
        book on the slug already refuses there (bool_or of a non-sleeve
        whale), and a book older than the window refuses by absence."""
        s = _sql(_src(le.maybe_execute))
        i = _pos(s, "bool_or(whale_username = 'underdog')")
        q = s[i:s.index('row_id, mapping["market_slug"])', i)]
        assert _BARE_WINDOW in q and "lane" not in q


class TestTheAddHolderBelt:
    def _holder_query(self) -> str:
        s = _sql(_src(le.maybe_execute))
        i = _pos(s, "/* add-holder */")
        return s[s.rfind("holder = await pool.fetchrow(", 0, i):i]

    def test_the_clause_sits_after_the_existing_predicates(self):
        q = self._holder_query()
        head = ("FROM live_orders WHERE asset = $1 AND status IN ('submitting','filled','settled') "
                "AND COALESCE(whale_username, '') NOT IN ('manual','underdog') ")
        assert head in q
        assert q.index(head) < q.index(_GUARD) < q.index("ORDER BY (status = 'submitting') DESC LIMIT 1")
        # the 48 h reading the referee keeps in step with never-add
        assert "(placed_at > now() - interval '48 hours') AS recent" in q

    def test_the_cheap_referee_itself_is_untouched(self):
        """already_taken claims the long token with no window and no lane
        clause (spec 1d, INCLUDE): the standing row IS a taken asset."""
        s = _sql(_src(le.maybe_execute))
        i = _pos(s, "taken = await pool.fetchval( \"SELECT 1 FROM live_orders WHERE asset = $1 ")
        q = s[i:s.index('str(payload["asset"]))', i)]
        assert "lane" not in q and "interval" not in q


class TestTheMergeStatements:
    def test_merge_add_leg_guards_its_standing_row_update(self):
        s = _flat(_src(le._merge_add_leg))
        i = _pos(s, "WHERE id = $2 AND status = 'filled'")
        j = _pos(s, "AND EXISTS (SELECT 1 FROM leg)")
        assert s[i:j] == f"WHERE id = $2 AND status = 'filled' AND {_GUARD} "
        # the leg half and the idempotency clause are unchanged
        assert "WHERE l.id = $1 AND l.status IN ('submitting', 'error')" in s
        assert "@> jsonb_build_array(jsonb_build_object('row_id', $1::bigint))" in s

    def test_the_named_merge_reads_its_standing_row_under_the_guard(self):
        s = _sql(_src(le._merge_named_add_leg))
        assert (f"SELECT status, settled_at FROM live_orders WHERE id = $1 AND {_GUARD} "
                "/* named-standing */") in s
        # a mirror standing row reads as gone: the promotion path, which
        # the one-fill index refuses while the book's claim stands (the
        # behavioural guarantee is test_a_mirror_standing_row_reads_as_gone;
        # this pins that the guarded read precedes the promotion return)
        assert s.index("/* named-standing */") < s.rindex("return False")

    def test_the_mirror_buy_statement_still_matches_the_merge_set_clause(self):
        """Step 5 copied _merge_add_leg's SET clause; the guard is in
        the WHERE, so the copy still matches to the character."""
        merge, mine = _flat(_src(le._merge_add_leg)), _flat(le._MIRROR_BUY_SQL)
        seg = merge[merge.index("SET fill_price = CASE"):merge.index("WHERE id = $2")]
        seg_mine = mine[mine.index("SET fill_price = CASE"):mine.index("WHERE id = $1")]
        assert re.sub(r"\$\d+", "$_", seg) == re.sub(r"\$\d+", "$_", seg_mine)


class TestTheExitPathSource:
    def _tail(self) -> list[str]:
        """The string fragments of _sel_tail, in order."""
        s = _src(le.mirror_exit)
        i = _pos(s, "_sel_tail = (")
        blk = s[i:s.index("ORDER BY placed_at DESC LIMIT 1\")", i) + len("ORDER BY placed_at DESC LIMIT 1\")")]
        return re.findall(r'f?"([^"]*)"', blk)

    def test_the_guard_is_the_only_new_fragment(self):
        frags = self._tail()
        assert frags == [
            "       fill_price::float8 AS entry, ",
            "      {ORDER_INTENT_SQL} AS intent ",
            "FROM live_orders ",
            "WHERE asset = $1 AND lower(COALESCE(whale_username,'')) = $2 ",
            "  AND status = 'filled' AND us_market_slug IS NOT NULL ",
            f"  AND {_GUARD} ",
            "ORDER BY placed_at DESC LIMIT 1",
        ]

    def test_the_mirror_reason_sits_directly_before_no_position_of_ours(self):
        s = _src(le.mirror_exit)
        i = _pos(s, "if await _mirror_owns_asset(pool, asset):")
        blk = s[i:i + 260]
        assert 'return _exit_done("mx_mirror_owns_market", whale=username,' in blk
        k = blk.index('return _exit_done("mx_no_position_of_ours"')
        assert blk[:k].count("return") == 1, "nothing else between the two returns"
        # after the in-flight lookup, before the claim: a book is never
        # claimed 'exiting', and an entry in flight still pends first
        assert _pos(s, "_entry_in_flight(pool, asset, username)") < i < _pos(s, "SET status='exiting'")
        assert s.count("_mirror_owns_asset(") == 1

    def test_the_reason_is_settled_and_the_pending_set_is_unchanged(self):
        assert "mx_mirror_owns_market" not in le.EXIT_PENDING_REASONS
        assert "mx_no_position_of_ours" not in le.EXIT_PENDING_REASONS
        # the membership as it stood before this step, to the name
        assert le.EXIT_PENDING_REASONS == frozenset({
            "mx_already_claimed", "mx_below_floor", "mx_entry_in_flight",
            "mx_exception_pending", "mx_exit_ledger_unreadable", "mx_exit_rounds_to_zero",
            "mx_halted", "mx_inflight_unreadable", "mx_no_bid_for_partial",
            "mx_overspend_halt", "mx_partial_full_exit", "mx_paused", "mx_venue_unfilled",
        })

    def test_the_helper_is_the_step_one_read(self):
        s = _src(le._mirror_owns_asset)
        assert "FROM mirror_books" in s and "state <> 'closed'" in s
        assert "(long_asset = $1 OR other_asset = $1)" in s
        assert "return False" in s


class TestNoGuardIsSpelledTheNullDroppingWay:
    @pytest.mark.parametrize("fn", [le.maybe_execute, le.mirror_exit, le._merge_add_leg,
                                    le._merge_named_add_leg, le._position_row,
                                    le._book_add_leg_if_any])
    def test_no_bare_lane_comparison(self, fn):
        assert not _BARE_RE.search(_src(fn)), fn.__name__


# ───────────────────────────── 2. the behaviour ─────────────────────────────

class _MergePool:
    """_merge_add_leg's one statement, answered from a standing row under
    the lane guard: a hit when the row is admitted, no row otherwise."""

    def __init__(self, standing):
        self.standing = standing
        self.calls: list = []

    async def fetchrow(self, sql, *a):
        self.calls.append((_flat(sql), a))
        if _lane_admits(sql, self.standing) and self.standing["status"] == "filled":
            return {"shares": 150.0, "px": 0.53, "usd": 80.0}
        return None


def _merge(pool) -> bool:
    return asyncio.run(le._merge_add_leg(
        pool, 101, 900, 50.0, 0.6, 30.0, 33.0, "ioc-9", "1",
        {"response": {}}, 1, tx_hash="0xabc", his_price=0.55))


class TestAMergeNeverTargetsABook:
    def test_a_mirror_standing_row_is_the_no_standing_row_result(self):
        p = _MergePool(_book(asset="123"))
        assert _merge(p) is False
        assert len(p.calls) == 1 and p.calls[0][1][:2] == (101, 900)

    @pytest.mark.parametrize("lane", [None, "ioc", "rest", "chain"])
    def test_a_legacy_standing_row_merges_as_before_whatever_its_lane(self, lane):
        assert _merge(_MergePool(_row(id=900, lane=lane))) is True

    def test_the_fixture_would_have_merged_without_the_guard(self):
        """The refusal above is the clause, not the fixture."""
        unguarded = "UPDATE live_orders SET x = 1 WHERE id = $2 AND status = 'filled'"
        assert _lane_admits(unguarded, _book())


class _ReaperPool:
    """A named leg (id 55) naming standing row 900; the standing row is
    served to the named-standing read and the merge under the guard."""

    def __init__(self, standing, leg_figures=True):
        self.standing = standing
        self.leg_figures = leg_figures
        self.ex: list = []
        self.merges: list = []

    async def fetchrow(self, sql, *a):
        s = _flat(sql)
        if "/* add-leg */" in s:
            return {"add_of": "900", "trade_id": 1, "requested_usd": 33.0,
                    "his_price": 0.55, "raw": "{}"}
        if "/* add-standing */" in s:
            return {"status": self.standing["status"], "adds": None}
        if "/* named-standing */" in s:
            if not _lane_admits(s, self.standing):
                return None
            return {"status": self.standing["status"], "settled_at": None}
        if "/* named-leg */" in s:
            f = 50.0 if self.leg_figures else 0.0
            return {"id": 55, "order_id": "ioc-9", "add_of": "900",
                    "filled_shares": f, "fill_price": 0.6, "filled_usd": 30.0 if f else 0.0}
        if "SET status = 'merged'" in s:
            self.merges.append((s, a))
            if _lane_admits(s, self.standing) and self.standing["status"] == "filled":
                return {"shares": 150.0, "px": 0.53, "usd": 80.0}
            return None
        return None

    async def fetchval(self, sql, *a):
        if "SELECT tx_hash FROM trades" in sql:
            return "0xbbb"
        return None

    async def execute(self, sql, *a):
        self.ex.append((_flat(sql), a))


class TestTheNamedMergeNeverTargetsABook:
    def test_a_mirror_standing_row_reads_as_gone(self):
        """False: the promotion path, which the one-fill-per-asset index
        refuses for as long as the book's standing row holds the asset
        and admits once the book has closed. Never None (a wait that
        would outlive the book) and never a merge."""
        r = {"id": 55, "add_of": "900"}
        p = _ReaperPool(_book(asset="123"))
        assert asyncio.run(le._merge_named_add_leg(p, r)) is False
        assert p.merges == [] and p.ex == []

    @pytest.mark.parametrize("lane", [None, "ioc", "rest", "chain"])
    def test_a_legacy_standing_row_merges_as_before_whatever_its_lane(self, lane):
        r = {"id": 55, "add_of": "900"}
        p = _ReaperPool(_row(id=900, lane=lane))
        assert asyncio.run(le._merge_named_add_leg(p, r)) is True
        assert len(p.merges) == 1

    def test_the_reapers_other_booking_site_names_the_leg_beside_a_book(self):
        """_book_add_leg_if_any reads the standing row unguarded, then
        merges through the guarded statement: the merge misses and the
        leg is recorded under the orphan name, never beside the book."""
        p = _ReaperPool(_book(asset="123"))
        out = asyncio.run(le._book_add_leg_if_any(p, 55, 50.0, 0.6, 30.0, "ioc-9", "why"))
        assert out is False and len(p.merges) == 1
        assert len(p.ex) == 1 and p.ex[0][1][4].startswith("ORPHAN FILL RECORDED")
        assert not [e for e in p.ex if "SET status='filled'" in e[0]]


class TestTheReferees:
    def test_another_whales_copy_of_the_other_outcome_is_refused_by_name(self, monkeypatch):
        """Three days old, the book is past the 48 h window that judged
        every row before it; the widened window still serves it, and
        the lane read names the refusal."""
        pool = _BookPool([_book(asset="456")])
        calls = _run(monkeypatch, pool)
        assert calls == [] and pool.merges == [] and _stamps(pool) == []
        assert _rejections(pool) == [_MIRROR_TEXT]

    def test_another_whales_copy_of_the_game_is_refused_by_name(self, monkeypatch):
        book = _book(asset="999", us_market_slug=f"atc-epl-ars-che-{TODAY}-ars")
        pool = _BookPool([book])
        calls = _run(monkeypatch, pool)
        assert calls == [] and pool.merges == []
        assert _rejections(pool) == ["one position per game"]

    def test_a_three_day_old_null_lane_row_keeps_todays_path(self, monkeypatch):
        """The control: the same rows with the lane NULL are outside the
        window exactly as before, on both referees, and the copy runs."""
        rows = [_book(asset="456", lane=None),
                _book(id=901, asset="999", lane=None,
                      us_market_slug=f"atc-epl-ars-che-{TODAY}-ars")]
        pool = _BookPool(rows)
        calls = _run(monkeypatch, pool)
        assert _rejections(pool) == []
        assert calls and calls[0][0] == "place"

    def test_a_fresh_null_lane_row_on_the_other_outcome_keeps_its_text(self, monkeypatch):
        pool = _BookPool([_book(asset="456", lane=None, age_s=60.0)])
        calls = _run(monkeypatch, pool)
        assert calls == []
        rej, = _rejections(pool)
        assert rej == (_LEGACY_TEXT + " (add refused: his buy is the other outcome of "
                       "a market we hold)")

    def test_his_own_fresh_buy_on_the_standing_asset_stays_taken(self, monkeypatch):
        """The whale left the allowlist; his fresh BUY on the token the
        book holds is stopped by the asset referee, because the
        add-holder read no longer offers the book as HIS filled row."""
        pool = _BookPool([_book(whale="rn1", asset="123", age_s=3600.0)])
        before = le._COPY_CENSUS.get("already_taken|rn1", 0)
        calls = _run(monkeypatch, pool)
        assert calls == [] and pool.inserts == [] and pool.merges == []
        assert le._COPY_CENSUS.get("already_taken|rn1", 0) == before + 1
        holder = [s for s in pool.sqls if "/* add-holder */" in s]
        assert len(holder) == 1 and _GUARD in holder[0]

    def test_below_the_belt_the_never_add_referee_refuses_the_book_by_name(self, monkeypatch):
        """The add-holder belt switched off in the fixture: the referee
        below it reads the lane with the row, declares no add, stamps no
        add_of, merges nothing, places nothing, and names the book."""
        pool = _BookPool([_book(whale="rn1", asset="123", age_s=3600.0)],
                         holder_ignores_guard=True)
        calls = _run(monkeypatch, pool)
        assert calls == [] and pool.merges == [] and _stamps(pool) == []
        assert len(pool.inserts) == 1
        assert _rejections(pool) == [_MIRROR_TEXT]

    def test_his_own_fresh_buy_on_his_own_null_lane_row_is_still_an_add(self, monkeypatch):
        """The control for both belts: the add path of test_add_legs,
        untouched for a NULL-lane row."""
        pool = _BookPool([_row(id=900, whale="rn1", asset="123", age_s=3600.0)])
        calls = _run(monkeypatch, pool)
        assert _rejections(pool) == []
        assert len(pool.inserts) == 1 and _stamps(pool)
        assert calls and calls[0][0] == "place"


class _Proceeded(BaseException):
    """Raised from the first collaborator past the lookup: proof that
    mirror_exit went on WITH a row. BaseException so no `except
    Exception` on the way out can turn it into a reason."""


class _ExitPool:
    def __init__(self, rows, *, owns=False, books_raise=False):
        self.rows = [dict(r) for r in rows]
        self.owns = owns
        self.books_raise = books_raise
        self.sqls: list[str] = []
        self.claims: list = []
        self.book_reads = 0

    async def fetchrow(self, sql, *a):
        s = _flat(sql)
        self.sqls.append(s)
        if "age_s" in s and "'submitting'" in s:
            return None                       # nothing in flight
        if "FROM live_orders WHERE asset = $1" in s and "status = 'filled'" in s:
            cands = [r for r in self.rows
                     if r["asset"] == a[0] and r["whale"] == a[1]
                     and r["status"] == "filled" and r.get("us_market_slug")
                     and _lane_admits(s, r)]
            if not cands:
                return None
            r = cands[0]
            return {"id": r["id"], "us_market_slug": r["us_market_slug"],
                    "qty": r["qty"], "orig_qty": r["qty"], "entry": 0.5,
                    "intent": "ORDER_INTENT_BUY_LONG"}
        return None

    async def fetchval(self, sql, *a):
        s = _flat(sql)
        self.sqls.append(s)
        if "FROM mirror_books" in s:
            self.book_reads += 1
            if self.books_raise:
                raise RuntimeError('relation "mirror_books" does not exist')
            return self.owns
        if "SET status='exiting'" in s:
            self.claims.append(a)
            return a[0]
        return None

    async def execute(self, sql, *a):
        self.sqls.append(_flat(sql))
        return None

    async def fetch(self, *a):
        return []


def _drive(pool, monkeypatch) -> str:
    async def _get_pool():
        return pool

    async def _false(_p):
        return False

    async def _held(_slug):
        raise _Proceeded()

    monkeypatch.setattr(le, "get_pool", _get_pool)
    monkeypatch.setattr(le, "copy_halted", lambda: False)
    monkeypatch.setattr(le, "_whale_set", lambda _n: {"rn1"})
    monkeypatch.setattr(le, "_is_paused", _false)
    monkeypatch.setattr(le, "overspend_halt", _false)
    monkeypatch.setattr(le, "_pm_held", _held)
    monkeypatch.setattr(le, "settings", lambda: types.SimpleNamespace(
        copy_probe_enabled=True))
    monkeypatch.setattr(pmus, "close_position", lambda *a, **k: pytest.fail("close_position"))
    monkeypatch.setattr(pmus, "submit_fok", lambda *a, **k: pytest.fail("submit_fok"))
    payload = {"side": "SELL", "whale_username": "rn1", "asset": "0xasset",
               "closed_frac": 1.0}
    try:
        return asyncio.run(le.mirror_exit(payload))
    except _Proceeded:
        return "proceeded"


_BOOK_ROW = _book(whale="rn1", asset="0xasset", us_market_slug=f"aec-atp-a-b-{TODAY}", qty=100.0)


class TestTheExitPathLeavesABookToTheMirror:
    def test_a_book_is_not_selected_and_the_vanish_is_named(self, monkeypatch):
        pool = _ExitPool([_BOOK_ROW], owns=True)
        before = le._EXIT_CENSUS.get("mx_mirror_owns_market", 0)
        assert _drive(pool, monkeypatch) == "mx_mirror_owns_market"
        assert pool.claims == [] and pool.book_reads == 1
        assert not [s for s in pool.sqls if "UPDATE live_orders" in s]
        assert le._EXIT_CENSUS.get("mx_mirror_owns_market", 0) == before + 1
        lookups = [s for s in pool.sqls if "status = 'filled'" in s and "FROM live_orders" in s]
        assert len(lookups) == 1 and _GUARD in lookups[0]

    def test_the_fixture_would_have_sold_the_book_without_the_guard(self):
        assert _lane_admits("SELECT id FROM live_orders WHERE asset = $1 AND status = 'filled'",
                            _BOOK_ROW)

    def test_no_book_on_the_token_is_still_no_position_of_ours(self, monkeypatch):
        pool = _ExitPool([_BOOK_ROW], owns=False)
        assert _drive(pool, monkeypatch) == "mx_no_position_of_ours"
        assert pool.claims == [] and pool.book_reads == 1

    def test_an_unreadable_mirror_books_falls_to_todays_reason(self, monkeypatch):
        """Unreadable is False in the helper by design: both answers sell
        nothing, and today's reason keeps today's census."""
        pool = _ExitPool([_BOOK_ROW], books_raise=True)
        assert _drive(pool, monkeypatch) == "mx_no_position_of_ours"
        assert pool.claims == [] and pool.book_reads == 1

    @pytest.mark.parametrize("lane", [None, "ioc", "rest", "chain"])
    def test_a_legacy_row_is_still_sold_whatever_its_lane(self, monkeypatch, lane):
        pool = _ExitPool([{**_BOOK_ROW, "lane": lane}])
        assert _drive(pool, monkeypatch) == "proceeded"
        assert pool.book_reads == 0, "a row of ours never asks the book"
        assert len(pool.claims) == 1

    def test_both_reasons_are_settled_for_the_exit_worker(self):
        for r in ("mx_mirror_owns_market", "mx_no_position_of_ours"):
            assert r not in le.EXIT_PENDING_REASONS
