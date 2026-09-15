"""U7 — the dormant cash-out hazards, and the counter that has to
survive them.

Two independent defects, neither of which moves a number that is served
today, and both of which are wrong the moment somebody presses the
button they sit behind.

1. THE RESTATEMENT'S DELTA COUNTER (`analytics/engine.py`).
   `_settle_pmus_from_venue` reported its own move as `newp - oldp`
   while reading `oldp` as zero for every row that was not already
   `settled`. A `filled` row CAN carry dollars — mirror_exit's partial
   branch accumulates realized P&L onto a row that stays `filled`
   (`live_executor.py:1528-1532`) — so the counter reported the whole
   new pnl as the move, and a write-DOWN of such a row reported a delta
   of exactly 0.0 while erasing money. `summary` is the only account a
   restatement gives of itself (`/api/admin/rescore-summary`, state key
   `rescore_copies_v2`), and a restatement that cannot state its own
   size cannot be consented to.

2. THE DESK CASH-OUT'S NULL REALIZED VALUE (`live_executor.py`).
   `_pm_held` returns no average cost when the venue's positions
   payload has none, `realized_pnl` correctly refuses to call an
   unknown entry price zero, and the desk row is inserted `cashed_out`
   with `pnl NULL`. The settlement sweep nets cash-out dollars out of
   the venue's cumulative realized per slug with `COALESCE(sum(pnl),0)`
   — and SQL `sum()` SKIPS NULLs — so that row subtracts $0.00 while
   the sale's proceeds stay inside the figure the ENTRY rows are graded
   against. The dollars are already booked once, on someone else's row.
   Nothing here can recover the missing number, and inventing one would
   be a fabricated measurement on the money path. So the row is made to
   say, on its own face, what it SUBTRACTED ($0.00) — never what the
   sale was worth.

WHICH TESTS HERE FAIL AGAINST HEAD, AND WHICH CANNOT. Measured, not
claimed: 10 of the 21 fail against a clean `git archive HEAD` copy.
The 11 that pass there are three kinds, and each kind is named so that
a green run is never mistaken for evidence it did not give:

  * the five `*_unmoved` NO-MOVE PINS assert that this unit changed
    NOTHING about what is written or served. Passing before AND after
    is their whole job;
  * two CONTRACT PINS —
    `test_a_track_record_cash_out_row_can_never_carry_a_null_pnl` and
    `test_the_frontend_still_matches_what_the_comment_says` — pin facts
    about code this unit does not own. They exist because round 1 built
    on the opposite of each, and there is no code change here for them
    to fail against: one fold was the DELETION of a probe line;
  * four WORKFLOW PINS. `.github/workflows/engine-diagnostic.yml` is
    owned by no unit in this run, so U7's probe lines ship as a patch
    (`scratchpad/cashout/u7_yml.patch`) for a hand merge, and a test
    cannot fail against a file the change is not in. Three of them run
    the probe program under `bash`+`jq` for real, and each also runs
    ROUND 1's frozen program and asserts it behaves as the review said
    it did — so the fold is proved to be a behaviour change in-file,
    not a rewording. The fourth pins the workflow to exactly one of two
    states: unpatched, or carrying the canonical block verbatim.
"""

import asyncio
import inspect
import json
import pathlib
import shutil
import subprocess

import pytest

from sportsassets import live_executor, pmus
from sportsassets.analytics import engine
from sportsassets.api import pmus_account, track_record


# ══ 1. The restatement's delta counter ════════════════════════════════


class _EnginePool:
    """Just enough asyncpg to run `_settle_pmus_from_venue`'s rescore
    branch: the row read, the cash-out subtraction read, an empty
    activity archive, and a capture of every UPDATE."""

    def __init__(self, rows, sold=()):
        self._rows = [dict(r) for r in rows]
        self._sold = [dict(s) for s in sold]
        self.updates = []

    async def fetch(self, sql, *args):
        if "pmus_activity_archive" in sql:
            return []
        if "status = 'cashed_out'" in sql:
            return list(self._sold)
        if "FROM live_orders" in sql:
            return [dict(r) for r in self._rows]
        return []

    async def fetchval(self, sql, *args):
        if "pmus_activity_archive" in sql:
            return 0
        return None

    async def execute(self, sql, *args):
        self.updates.append({"sql": sql, "args": args})
        return "UPDATE 1"


def _row(row_id, *, slug="atc-mlb-x", whale="w1", filled_usd=100.0,
         pnl=0.0, status="filled"):
    return {"id": row_id, "slug": slug, "whale": whale,
            "filled_usd": filled_usd, "pnl": pnl, "status": status}


def _run_restatement(monkeypatch, pool, truth):
    async def _truth(_from_day):
        return dict(truth)

    monkeypatch.setattr(pmus_account, "resolution_truth", _truth)
    return asyncio.run(engine._settle_pmus_from_venue(
        pool, rescore_since="2026-08-01"))


def test_delta_of_a_filled_row_that_already_carries_dollars(monkeypatch):
    """A partial exit accumulated +18.00 onto a row that stayed
    `filled`. The market then resolves at a venue cumulative of +42.00.
    The restatement MOVED that row by +24.00, not by +42.00."""
    pool = _EnginePool([_row(1, pnl=18.0, status="filled")])
    summary = _run_restatement(monkeypatch, pool,
                               {"atc-mlb-x": {"realized": 42.0, "ts": ""}})
    assert summary["settled"] == 1
    assert summary["delta"] == pytest.approx(24.0), (
        "the restatement described a +24.00 move as +42.00 — it read the "
        "row's stored pnl as zero because the row was not yet 'settled'")


def test_a_write_down_is_not_reported_as_no_move(monkeypatch):
    """The dangerous direction. A `filled` row carrying +18.00 of
    accumulated partial realizations is re-graded to the venue's +12.00.
    Six dollars leave the record. The counter must say -6.00; reading
    `oldp` as zero said +12.00 — a WRITE-DOWN reported as a gain."""
    pool = _EnginePool([_row(1, pnl=18.0, status="filled")])
    summary = _run_restatement(monkeypatch, pool,
                               {"atc-mlb-x": {"realized": 12.0, "ts": ""}})
    assert summary["delta"] == pytest.approx(-6.0), (
        "money was removed from a row and the restatement's own summary "
        "reported it as an increase")


def test_per_whale_old_carries_the_filled_rows_prior_pnl(monkeypatch):
    """`whales[w]['old']` is the before-figure a human compares against
    `new`. A `filled` row's accumulated dollars are part of `old`."""
    pool = _EnginePool([_row(1, whale="alice", pnl=18.0, status="filled")])
    summary = _run_restatement(monkeypatch, pool,
                               {"atc-mlb-x": {"realized": 42.0, "ts": ""}})
    w = summary["whales"]["alice"]
    assert w["rows"] == 1
    assert w["new"] == pytest.approx(42.0)
    assert w["old"] == pytest.approx(18.0), (
        "the per-whale before/after pair claimed this whale started at "
        "$0.00 on a row that already held $18.00")


def test_a_settled_row_delta_is_unchanged_unmoved(monkeypatch):
    """NO-MOVE PIN. For an already-`settled` row the old expression and
    the new one are equal (the SELECT COALESCEs pnl, so it is never
    NULL), and `changed` still counts only true restatements."""
    pool = _EnginePool([_row(1, pnl=30.0, status="settled")])
    summary = _run_restatement(monkeypatch, pool,
                               {"atc-mlb-x": {"realized": 42.0, "ts": ""}})
    assert summary["delta"] == pytest.approx(12.0)
    assert summary["changed"] == 1
    assert summary["whales"]["w1"]["old"] == pytest.approx(30.0)


def test_a_settled_row_at_the_venue_figure_is_still_skipped_unmoved(
        monkeypatch):
    """NO-MOVE PIN. The idempotence skip is gated on `status ==
    'settled'` and must stay there: reading the old value
    unconditionally must not start skipping `filled` rows that happen to
    already carry the venue's number, because those rows still need
    their status and settled_at written."""
    pool = _EnginePool([_row(1, pnl=42.0, status="settled"),
                        _row(2, slug="b-slug", pnl=42.0, status="filled")])
    summary = _run_restatement(
        monkeypatch, pool,
        {"atc-mlb-x": {"realized": 42.0, "ts": ""},
         "b-slug": {"realized": 42.0, "ts": ""}})
    written = [u["args"][0] for u in pool.updates]
    assert written == [2], (
        "the settled row must be skipped and the filled row must still be "
        "settled even though its pnl already matches")
    assert summary["settled"] == 1


def test_the_written_pnl_is_the_allocation_unmoved(monkeypatch):
    """NO-MOVE PIN — the load-bearing one. `oldp` reaches the counters
    and the skip; it must never reach the UPDATE. The value written is
    the venue's cumulative less what cash-out rows subtracted, whatever
    the row was carrying before."""
    pool = _EnginePool(
        [_row(1, pnl=18.0, status="filled")],
        sold=[{"slug": "atc-mlb-x", "pnl": 10.0}])
    _run_restatement(monkeypatch, pool,
                     {"atc-mlb-x": {"realized": 42.0, "ts": ""}})
    assert len(pool.updates) == 1
    args = pool.updates[0]["args"]
    assert args[0] == 1
    assert args[1] == pytest.approx(32.0), (
        "the value written must be 42.00 - 10.00; the row's own prior "
        "18.00 is reporting, not arithmetic")


# ══ 2. The desk cash-out's NULL realized value ════════════════════════


class _DeskPool:
    def __init__(self):
        self.inserts = []

    async def fetchval(self, q, *a, **k):
        if "INSERT INTO live_orders" in q:
            self.inserts.append(a)
            return 77
        return None


_STATUS, _PROCEEDS, _ERROR, _PNL = 5, 11, 13, 14


def _wire_desk(monkeypatch, pool, *, held, bid=0.55, fok=None):
    async def fake_pool():
        return pool

    async def fake_held(_slug):
        return held

    async def fake_leg(_slug):
        return True

    monkeypatch.setattr(live_executor, "get_pool", fake_pool)
    monkeypatch.setattr(live_executor, "active_venue",
                        lambda: "polymarket-us")
    monkeypatch.setattr(live_executor, "_pm_held", fake_held)
    monkeypatch.setattr(live_executor, "_pm_long_leg", fake_leg)
    monkeypatch.setattr(pmus, "slug_bid", lambda _s, long_leg=None: bid)
    monkeypatch.setattr(
        pmus, "submit_fok",
        fok or (lambda slug, limit, qty, sell, tif: {
            "ok": True, "order_id": "o1", "status": "filled",
            "fill_price": limit, "filled_shares": float(qty), "raw": {}}))


def test_an_unmeasured_cash_out_names_itself_on_the_row(monkeypatch):
    """The venue gave no average cost, so the row is written with
    `pnl NULL`. The sale still happened and is still recorded — but the
    row now carries the sentence that says its subtraction is $0.00,
    instead of an empty `error` indistinguishable from a clean sale."""
    pool = _DeskPool()
    _wire_desk(monkeypatch, pool, held=(30, None))
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))

    # The sale is NOT refused: the money already moved at the venue.
    assert r["ok"] is True
    assert r["filled_shares"] == 30.0
    assert r["pnl"] is None
    assert r["pnl_unmeasured"] is True
    assert "unknown, not zero" in r["detail"]

    args = pool.inserts[0]
    assert args[_STATUS] == "cashed_out"
    assert args[_PNL] is None, "an unknown P&L must never become a zero"
    assert args[_ERROR] == live_executor.CASHOUT_PNL_UNMEASURED_TEXT


def test_the_disclosure_states_what_was_subtracted_not_what_was_intended():
    """The row may only assert the fact it has: a NULL is skipped by
    `sum()`, so what this row took off its market's settlement target is
    $0.00. It must not name a sale value, a proceeds figure or a
    reconstructed price — none of those are measurements here."""
    text = live_executor.CASHOUT_PNL_UNMEASURED_TEXT
    assert "$0.00" in text
    assert "UNKNOWN, not zero" in text
    # Migration 037's NOTIFY publishes left(error, 120): the hazard has
    # to survive that truncation, not a preamble.
    head = text[:120]
    assert "REALIZED NOT CAPTURED" in head
    assert "$0.00" in head


def test_a_measured_cash_out_is_left_exactly_as_it_was_unmoved(monkeypatch):
    """NO-MOVE PIN. With a readable average cost nothing about the row
    or the response changes: same pnl, same empty `error`, same
    one-word detail."""
    pool = _DeskPool()
    _wire_desk(monkeypatch, pool, held=(30, 0.40), bid=0.55)
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))
    assert r["ok"] is True
    assert r["pnl"] == pytest.approx(round((0.53 - 0.40) * 30, 4))
    assert r["detail"] == "sold"
    args = pool.inserts[0]
    assert args[_ERROR] is None
    assert args[_PNL] == pytest.approx(round((0.53 - 0.40) * 30, 4))


def test_a_measured_cash_out_is_not_flagged_unmeasured(monkeypatch):
    """The flag is about the MEASUREMENT, not about the sale. A sale
    with a readable cost must not inherit the disclosure."""
    pool = _DeskPool()
    _wire_desk(monkeypatch, pool, held=(30, 0.40))
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))
    assert r["pnl_unmeasured"] is False


def test_an_unfilled_sell_is_not_an_unmeasured_cash_out(monkeypatch):
    """Nothing was sold, so nothing subtracts and there is nothing to
    disclose. The row keeps the venue's own refusal text in `error` —
    overwriting THAT with the cash-out disclosure would lose the only
    record of why the order did not fill."""
    def fok(slug, limit, qty, sell, tif):
        return {"ok": False, "order_id": None, "status": "canceled",
                "fill_price": None, "filled_shares": 0.0,
                "raw": {"reason": "book moved"}}

    pool = _DeskPool()
    _wire_desk(monkeypatch, pool, held=(30, None), fok=fok)
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))
    assert r["ok"] is False
    assert r["pnl_unmeasured"] is False
    args = pool.inserts[0]
    assert args[_STATUS] == "unfilled"
    assert "book moved" in (args[_ERROR] or "")


# ══ 3. The fence the disclosure describes ═════════════════════════════


def test_a_null_cash_out_still_subtracts_nothing_unmoved(monkeypatch):
    """NO-MOVE PIN, and the reason this unit stops where it does.

    This is the hazard END TO END: a desk cash-out whose realized value
    was unreadable sits on the same slug as the copy entry row. The
    venue's cumulative realized (+100.00) already contains the sale's
    +40.00. `sold_by` subtracts what the cash-out rows carry — and a
    NULL carries nothing — so the entry row is graded at the FULL
    +100.00, sale included.

    That number is what is served today. Closing this gap means either
    writing money onto a row (a restatement) or changing what
    `/api/copies-record` reads (U3) — both of which need the owner's
    consent and are explicitly not in this run. So the number stays,
    and the row that caused it now says so.
    """
    pool = _EnginePool(
        [_row(1, whale="alice", filled_usd=100.0, pnl=0.0, status="filled")],
        # The desk row: cashed_out, pnl NULL -> COALESCE(sum(pnl),0) over
        # a single NULL is 0.00, which is what the sweep actually reads.
        sold=[{"slug": "atc-mlb-x", "pnl": 0.0}])
    _run_restatement(monkeypatch, pool,
                     {"atc-mlb-x": {"realized": 100.0, "ts": ""}})
    assert pool.updates[0]["args"][1] == pytest.approx(100.0)


# ══ 4. The review's folds ═════════════════════════════════════════════
#
# Round 1 of this unit shipped three claims that were not true, and one
# probe line that measured nothing and printed the nothing as a zero.
# Each fold below is pinned by the test under it.


def test_pm_held_returns_no_average_when_the_cost_is_merely_not_positive(
        monkeypatch):
    """FOLD 1 — the disclosure may not name a cause it did not observe.

    `_pm_held` returns no average for TWO different reasons: the venue's
    payload carried no cost at all, and the cost it carried was not
    positive (its `cost > 0` guard). A genuine zero-cost position is the
    second. Round 1's text said the payload "carried no readable average
    cost", which is a claim about the venue on a row where the venue may
    have spoken perfectly clearly and said zero.
    """
    def _positions():
        return {"atc-mlb-x": {"netPosition": "30", "cost": "0"}}

    monkeypatch.setattr(pmus_account, "_fetch_all_positions_sync",
                        _positions)
    qty, avg = asyncio.run(live_executor._pm_held("atc-mlb-x"))
    assert qty == 30, "the position is real and sellable"
    assert avg is None, (
        "a zero cost yields no average — the same None the 'payload "
        "carried no cost' branch yields, and the row cannot tell them "
        "apart")

    text = live_executor.CASHOUT_PNL_UNMEASURED_TEXT
    assert "carried no readable average cost" not in text, (
        "the row asserted the venue was silent on a branch where the "
        "venue may have said zero")
    assert "no usable average cost" in text
    assert "not positive" in text


def test_the_disclosure_survives_all_three_truncations():
    """FOLD 2 — every reader of `error` truncates it, at a different
    length. The load-bearing words have to be inside the SHORTEST cut,
    not merely inside the column."""
    text = live_executor.CASHOUT_PNL_UNMEASURED_TEXT
    assert "REALIZED NOT CAPTURED" in text[:80], "TradeDesk blotter card"
    assert "REALIZED NOT CAPTURED" in text[:40], "TradeDesk status cell"
    assert "$0.00" in text[:120], "migration 037 NOTIFY, left(error,120)"
    assert "$0.00" in text[:200], "app.py day-detail, left(error,200)"
    # api_manual_trades serves this column UNTRUNCATED for up to 200
    # rows (app.py:3010, the one reader that does not bound it). Keeping
    # the sentence short is this unit's only lever on that payload;
    # `left(error, 200)` there belongs to whoever owns app.py.
    assert len(text) <= 586, (
        f"{len(text)} chars x 200 blotter rows is the desk payload; the "
        "round-1 text was 586 and this must not grow")


def test_the_module_does_not_claim_a_reader_it_does_not_have():
    """FOLD 3 — the comments justified the disclosure's shape with two
    consumers that do not consume it.

    Round 1 said migration 037's NOTIFY meant "a reader on the order
    stream sees the hazard", and that "the desk is told at the moment of
    the sale". Neither is true today: DeskOrderStream's `cashed_out`
    branch builds its body from `e.pnl` and never touches `e.error`, and
    TradeDesk's success branch reads neither `detail` nor
    `pnl_unmeasured`. A comment that names a reader which does not read
    is how a guard gets believed instead of checked.

    THE SAME PIN, THE OTHER DIRECTION (2026-09-04, second review pass).
    Round 3's comment then claimed the blotter was "the whole of the
    disclosure's reach", and that was false: /api/live-status's `recent`
    selects `lo.error` with no whale filter, so a `manual` desk row is
    in it, and AITrader.tsx renders a truthy `o.error` as a chip in
    `var(--critical)`. Missing a reader is the same defect as inventing
    one — both leave the comment saying something that is not true — so
    the comment must now name AITrader too, and the assertion below
    fails if that name is dropped.
    """
    src = inspect.getsource(live_executor)
    i = src.index("CASHOUT_PNL_UNMEASURED_TEXT = (")
    head = src[max(0, i - 4000):i]
    assert "so a reader on the order stream sees the hazard" not in head
    assert "The order stream does NOT" in head, (
        "the comment must say which reader does NOT render it")
    assert "AITrader.tsx" in head, (
        "the comment must name the SECOND surface that renders it")
    assert "The desk is told at the moment of the sale" not in src


def test_the_frontend_still_matches_what_the_comment_says():
    """The other half of FOLD 3, checked at the consumer rather than
    asserted. If someone wires `e.error` into the order stream's
    `cashed_out` branch, this fails and the comment above must be
    rewritten — which is the point."""
    root = pathlib.Path(live_executor.__file__).resolve().parents[2]
    ds = root / "frontend/src/components/DeskOrderStream.tsx"
    if not ds.exists():                       # pragma: no cover
        pytest.skip("frontend not present in this checkout")
    body = ds.read_text()
    i = body.index("case 'cashed_out'")
    branch = body[i:i + 400]
    assert "e.error" not in branch, (
        "the order stream now renders the disclosure — update the "
        "comment at CASHOUT_PNL_UNMEASURED_TEXT, it says it does not")
    assert "e.pnl" in branch


def test_the_second_surface_the_comment_names_still_renders_it():
    """The reader the first review pass MISSED, pinned at the consumer.

    The disclosure reaches the AI-trader page by a route nobody designed
    for it: /api/live-status's `recent` selects `lo.error` and filters on
    `placed_at` alone — no whale filter — so a `whale_username='manual'`
    desk row is in that list, and AITrader.tsx renders a truthy
    `o.error` as a chip in `var(--critical)`.

    That is the right behaviour: a sale whose realized value was never
    captured IS a hazard and red is the right colour for it. This test
    exists because the comment now asserts it. If the page stops
    rendering `o.error`, or that query grows a whale filter, the comment
    is stale and this fails — which is the point.
    """
    root = pathlib.Path(live_executor.__file__).resolve().parents[2]
    page = root / "frontend/src/pages/AITrader.tsx"
    if not page.exists():                     # pragma: no cover
        pytest.skip("frontend not present in this checkout")
    body = page.read_text()
    assert "o.error" in body, (
        "AITrader no longer renders live_orders.error — the comment at "
        "CASHOUT_PNL_UNMEASURED_TEXT names it as a renderer")
    assert "var(--critical)" in body

    # And the backend half: the query that puts a `manual` row in front
    # of that page. Located by the column list rather than by line
    # number so it survives edits above it.
    app = (root / "backend/sportsassets/api/app.py").read_text()
    j = app.index("lo.reaction_s::float8 AS reaction_s, lo.pnl::float8 "
                  "AS pnl, lo.error,")
    q = app[j:j + 900]
    k = q.index("ORDER BY lo.placed_at DESC")
    assert "whale_username" not in q[:k], (
        "the live-status recent query now filters by whale — a manual "
        "desk row may no longer reach AITrader, so the comment at "
        "CASHOUT_PNL_UNMEASURED_TEXT is stale")


def test_a_track_record_cash_out_row_can_never_carry_a_null_pnl():
    """FOLD 4 — why the CASHOUT-NULLPNL probe line was deleted.

    That line read `.rows[] | select(.cashed_out) | select(.pnl == null)`
    from /api/track-record and printed `rows=0 of 0` on every run. Two
    independent reasons, both pinned here against the REAL builder on
    synthetic tape:

      1. that payload has no top-level `rows` key at all — the row array
         is served as `trades`;
      2. `cashed_out` implies `settled` there and `pnl` is written
         `round(realized, 4)` on both row builders, so a cash-out row in
         that payload never has a null pnl.

    The population the line claimed to measure — a cash-out published
    with no realized value — lives in `live_orders.pnl` and is never
    exposed by this endpoint. U0's CASHOUTZERO line reads it from
    /api/admin/cashout-census, which queries that column directly.
    """
    slug = "nfl-syn-2026-09-03"
    t0, t1 = 1_756_800_000.0, 1_756_900_000.0

    def act(side, qty, px, rp, ts):
        return {"type": "ACTIVITY_TYPE_TRADE", "timestamp": ts,
                "trade": {"marketSlug": slug, "qty": qty, "price": px,
                          "realizedPnl": rp,
                          "aggressorExecution": {
                              "order": {"side": side}}}}

    payload = track_record.build(
        {},                                   # sold flat: no position left
        [act("ORDER_SIDE_BUY", 200.0, 0.50, 0.0, t0),
         act("ORDER_SIDE_SELL", 200.0, 0.70, 40.0, t1)],
        since_ts=t0 - 86400, copy_slugs={slug}, manual_slugs=set())

    assert "rows" not in payload, (
        "a probe reading .rows[] from this payload reads nothing and "
        "the `?` operator hides that it read nothing")
    cashouts = [t for t in payload["trades"] if t.get("cashed_out")]
    assert cashouts, "the synthetic tape must produce a cash-out row"
    assert all(t.get("pnl") is not None for t in cashouts), (
        "`select(.pnl == null)` over this payload is empty BY "
        "CONSTRUCTION, so a count taken from it is always zero and "
        "will be read as 'that population is empty'")


# ── The workflow probe line, run rather than eyeballed ────────────────
#
# The canonical text of the RESCORE-TIE block. `.github/workflows/
# engine-diagnostic.yml` is owned by no unit in this run, so U7's lines
# are delivered as a patch (scratchpad/cashout/u7_yml.patch) for a hand
# merge. This constant is what that patch inserts, verbatim; the state
# test below pins the workflow to exactly one of two states — carrying
# this block, or not yet patched — so a mutated hand merge fails here.

RESCORE_TIE_BLOCK = r'''          jq -e -r 'if (.ran != true) or (((.whales // {}) | length) == 0)
                 then "  RESCORE-TIE UNREADABLE — ran=\(.ran // false) whales=\((.whales // {}) | length); there is no restatement summary here and this line asserts NOTHING about any number"
                 else . as $s
                   | ([($s.whales // {})[].new // 0] | add // 0) as $n
                   | ([($s.whales // {})[].old // 0] | add // 0) as $o
                   | ((($n - $o) * 100) | round) as $m
                   | ((($s.delta // 0) * 100) | round) as $d
                   | (if $m < $d then $d - $m else $m - $d end) as $g
                   | "  RESCORE-TIE delta=$\($d / 100) sum(new-old)=$\($m / 100) gap=$\($g / 100) band=$0.05 rows=\($s.settled // 0) changed=\($s.changed // 0) "
                     + (if $g <= 5
                        then "AGREE (cross-counter only — ties under any correct implementation; NOT evidence the delta fix shipped)"
                        else "DISAGREE — the two counters in one summary describe different moves; the restatement cannot state its own size, do NOT consent to a restatement on these numbers" end)
                 end' /tmp/rescore.json 2>/dev/null \
            || echo "  RESCORE-TIE UNREADABLE — /tmp/rescore.json is absent, empty or unparseable; this line asserts NOTHING about any number"
          jq -r '.whales // {} | to_entries[]? | "  RESCORE-MOVE \(.key)  move=$\(((((.value.new // 0) - (.value.old // 0)) * 100) | round) / 100)  old=\(.value.old)  new=\(.value.new)  rows=\(.value.rows)"' \
            /tmp/rescore.json 2>/dev/null || true'''

# EXACTLY what round 1 shipped, frozen. It is here to prove the fold is
# a behaviour change and not a rewording.
RESCORE_TIE_BLOCK_ROUND1 = r'''          jq -r '(.whales // {}) as $w
                 | ([$w[].new // 0] | add // 0) as $n
                 | ([$w[].old // 0] | add // 0) as $o
                 | ((($n - $o) * 100) | round) as $m
                 | (((.delta // 0) * 100) | round) as $d
                 | "  RESCORE-TIE delta=\($d / 100) sum(new-old)=\($m / 100) rows=\(.settled // 0) changed=\(.changed // 0) " + (if $m == $d then "TIES" else "DISAGREES — the restatement cannot state its own size; do NOT consent to a restatement on these numbers" end)' \
            /tmp/rescore.json 2>/dev/null || true'''

_REAL = {"ran": True, "delta": 24.0, "settled": 3, "changed": 1,
         "whales": {"alice": {"old": 18.0, "new": 42.0, "rows": 1}}}
# delta 24.006 against a per-whale move of 24.00: half a cent of drift,
# reachable because summary['delta'] and whales[w]['old'/'new'] each
# accumulate through round(...,4) by different paths.
_DRIFT = dict(_REAL, delta=24.006)
# A real divergence: the two counters describe different moves.
_SPLIT = {"ran": True, "delta": 24.0, "settled": 3, "changed": 1,
          "whales": {"alice": {"old": 0.0, "new": 42.0, "rows": 1}}}


def _run_block(block: str, payload, tmp_path) -> str:
    f = tmp_path / "rescore.json"
    f.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    r = subprocess.run(
        ["bash", "-c", block.replace("/tmp/rescore.json", str(f))],
        capture_output=True, text=True, timeout=30)
    return r.stdout


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_the_tie_line_refuses_instead_of_passing_when_it_read_nothing(
        tmp_path):
    """FOLD 5 — the consent gate failed OPEN.

    Round 1's line printed `RESCORE-TIE delta=0 sum(new-old)=0 rows=0
    changed=0 TIES` on an EMPTY object — the state reached when the curl
    above it failed (it is `|| true`), when the admin token was
    rejected, or when the restatement has simply never run and
    /api/admin/rescore-copies answers `{"ran": false}`. The one line
    whose stated job is to certify that a restatement can describe its
    own size returned the green verdict on no data at all.
    """
    for payload in ({}, {"ran": False}, {"ran": True, "whales": {}}, "",
                    "null", '{"detail":"forbidden"}'):
        out = _run_block(RESCORE_TIE_BLOCK, payload, tmp_path)
        assert "UNREADABLE" in out, (payload, out)
        assert "AGREE" not in out, (payload, out)

    # The fold is a behaviour change, not a rewording: the shipped line
    # said TIES on the same nothing.
    old = _run_block(RESCORE_TIE_BLOCK_ROUND1, {}, tmp_path)
    assert "TIES" in old and "UNREADABLE" not in old, old


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_the_tie_line_has_a_named_band_and_does_not_false_alarm(tmp_path):
    """FOLD 6 — an exact comparison in cents false-alarms on the consent
    gate itself. Half a cent of accumulated rounding drift is reachable
    over a few hundred rows, and round 1 answered it with `do NOT
    consent to a restatement on these numbers`."""
    out = _run_block(RESCORE_TIE_BLOCK, _DRIFT, tmp_path)
    assert "AGREE" in out and "band=$0.05" in out, out

    old = _run_block(RESCORE_TIE_BLOCK_ROUND1, _DRIFT, tmp_path)
    assert "DISAGREES" in old, ("round 1 refused on half a cent", old)

    # The band is a tolerance, not a blindfold: a genuine divergence
    # still refuses, and it now says by how much.
    bad = _run_block(RESCORE_TIE_BLOCK, _SPLIT, tmp_path)
    assert "DISAGREE" in bad and "gap=$18" in bad, bad


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_the_tie_line_does_not_claim_to_verify_this_units_fix(tmp_path):
    """FOLD 7 — RESCORE-TIE ties under HEAD and under the fix alike.

    `summary['delta']` and the per-whale `old`/`new` pair accumulate the
    same per-row values in the same loop (engine.py), so the identity
    holds whatever `oldp` is. It is a forward cross-counter guard, and a
    reader must not take AGREE as confirmation that the delta counter
    was corrected. RESCORE-MOVE is the line that carries that, because
    `old` is now non-zero for a `filled` row holding dollars.
    """
    out = _run_block(RESCORE_TIE_BLOCK, _REAL, tmp_path)
    assert "NOT evidence the delta fix shipped" in out, out
    assert "RESCORE-MOVE alice  move=$24  old=18.0  new=42.0" in out, out


def test_the_workflow_is_in_exactly_one_of_its_two_allowed_states():
    """The workflow file is owned by no unit in this run, so U7's lines
    ship as a patch for a hand merge. Two states are legitimate: NOT YET
    PATCHED, or carrying this block verbatim. A third — a hand merge
    that mutated the program, or one that carried round 1's deleted
    CASHOUT-NULLPNL lines back in — fails here."""
    root = pathlib.Path(live_executor.__file__).resolve().parents[2]
    y = (root / ".github/workflows/engine-diagnostic.yml").read_text()

    assert "CASHOUT-NULLPNL" not in y, (
        "that line reads .rows[] from a payload whose row array is "
        "served as .trades, and prints a permanent 0-of-0 for a "
        "population it cannot see; U0's CASHOUTZERO line measures it")

    if "RESCORE-TIE-LINES-BEGIN" not in y:
        assert "RESCORE-TIE" not in y, (
            "a RESCORE-TIE line without the markers is a hand merge "
            "this test cannot check")
        return

    lines = y.splitlines()
    i0 = next(i for i, s in enumerate(lines)
              if "RESCORE-TIE-LINES-BEGIN" in s)
    i1 = next(i for i, s in enumerate(lines)
              if "RESCORE-TIE-LINES-END" in s)
    block = "\n".join(s for s in lines[i0 + 1:i1]
                      if not s.strip().startswith("#"))
    assert block == RESCORE_TIE_BLOCK, (
        "the workflow's copy has drifted from the program this file "
        "drives; the tests above then prove nothing about CI")
