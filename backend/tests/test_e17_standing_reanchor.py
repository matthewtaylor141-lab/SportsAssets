"""E17 (2026-09-08, PNL lane 5): a settled standing row on a LIVE
market re-anchors; a closed book's shares are adopted by the next
episode on his next fill.

Book 204 (hard2/book_204_1150.log): our 13 @0.60 filled 17:00:50Z
(10 % of his 134.6); the book closed 18:48:01Z "closed: standing row
settled" while the market was live; he added 6,350 sh after; every
candidate on the market was refused `venue_already_holds` on our own
13 shares.

Step M: a 'settled' row carrying NEITHER of the venue settle's marks
(engine._settle_pmus_from_venue writes pnl AND settled_at) on a book
that holds shares re-anchors -- ONLY when the gamma row is live with
resolved_prices NULL AND the venue's own quote read this tick is OPEN;
the venue's settle, a terminal read, a flat book, a cashed_out /
cancelled row, or any reader that cannot tell closes byte for byte as
before (`standing_row_ambiguous` named on the reader that could not).

The candidate: the venue's shares that ARE a closed book's ledger
(same sign, within the D1 dust) are adopted -- the prior's own row
re-anchored as the standing row (migration 014's index holds settled
rows), the ledger and cost carried, episode n+1 sized on his fills
AFTER the close only; the prior's row carrying the venue's settle is
`adopt_prior_venue_settled` (nothing reopens against the venue's word);
any other magnitude stays `venue_already_holds`.

Driven on the real planner through the worker file's fakes.
"""
import json
import math

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    CID, M, N, NOW, SLUG, _Pool, _armed, _census, _fill, _his, _mkt, _places, _rails_2026_09_06, _tick, _Venue,
)

EXPIRED = "MARKET_STATE_EXPIRED"
HALTED = "MARKET_STATE_HALTED"
OPEN = "MARKET_STATE_OPEN"
CLOSE_TS = NOW - 3600.0


class _L5Pool(_Pool):
    """The worker file's pool plus E17's four statements."""

    def _run(self, kind, sql, a):
        s = " ".join(sql.split())
        for needle, exc in self.raise_on:
            if needle in s and "ml-" in needle:
                self.sent.append((kind, s, a))
                raise exc
        if "ml-standing-read" in s:
            self.sent.append((kind, s, a))
            r = self.rows.get(a[0])
            return None if r is None else {k: r.get(k) for k in
                                           ("status", "lane", "filled_shares", "fill_price", "pnl", "raw",
                                            "settled_at")}
        if "ml-standing-reanchor" in s:
            self.sent.append((kind, s, a))
            r = self.rows.get(a[0])
            if (r is None or r["lane"] != "mirror" or r["status"] != "settled" or r.get("pnl") is not None
                    or r.get("settled_at") is not None):
                return None
            r["status"] = "filled"
            r["raw"].setdefault("mirror", {})["reanchored"] = json.loads(a[1])
            return {"id": a[0]}
        if "ml-prior-episode" in s:
            self.sent.append((kind, s, a))
            cands = [b for b in self.books.values()
                     if b["whale"] == a[0] and b["condition_id"] == a[1] and b["state"] == "closed"
                     and b["ledger_net"] != 0]
            if not cands:
                return None
            b = max(cands, key=lambda b: ((b["closed_at"] or 0.0), b["id"]))
            lo = self.rows.get(b["standing_row_id"]) or {}
            return {"id": b["id"], "episode": b["episode"], "intent": b["intent"], "ledger_net": b["ledger_net"],
                    "avg_cost": b["avg_cost"], "standing_row_id": b["standing_row_id"],
                    "closed_ts": b["closed_at"], "row_status": lo.get("status"), "row_lane": lo.get("lane"),
                    "row_pnl": lo.get("pnl"), "row_settled_at": lo.get("settled_at"),
                    "row_shares": lo.get("filled_shares")}
        if "ml-prior-dust" in s:
            # the fold (review HIGH-1): the newest closed book on the market
            # whose standing row is lane 'mirror' on one of its tokens
            self.sent.append((kind, s, a))
            cands = [b for b in self.books.values()
                     if b["whale"] == a[0] and b["condition_id"] == a[1] and b["state"] == "closed"
                     and (self.rows.get(b["standing_row_id"]) or {}).get("lane") == "mirror"
                     and (self.rows.get(b["standing_row_id"]) or {}).get("asset") in (a[2], a[3])]
            if not cands:
                return None
            b = max(cands, key=lambda b: ((b["closed_at"] or 0.0), b["id"]))
            return {"id": b["id"], "ledger_net": b["ledger_net"],
                    "row_asset": self.rows[b["standing_row_id"]]["asset"]}
        if "ml-book-adopt" in s:
            self.sent.append((kind, s, a))
            b = self.books[a[0]]
            if b["ledger_net"] != 0:
                return "UPDATE 0"
            # the fold (review MEDIUM-1): the statement writes gross_buy_usd
            # and peak_exposure_usd 0 itself -- three parameters
            assert len(a) == 3, a
            b.update(ledger_net=a[1], avg_cost=a[2], gross_buy_usd=0.0, peak_exposure_usd=0.0)
            return "UPDATE 1"
        return super()._run(kind, sql, a)


def _pool(**kw):
    kw.setdefault("fills", _his())
    kw.setdefault("snap", {M: 300.0, N: 0.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", None)
    return _L5Pool(**kw)


def _retired(p, status="settled", ledger=13, avg_cost=0.60, pnl=None, settled_at=None, **over):
    """A live book holding `ledger` whose standing row has left 'filled'."""
    b = p.add_book(ledger=ledger, avg_cost=avg_cost, standing_status=status, **over)
    row = p.rows[b["standing_row_id"]]
    row["pnl"], row["settled_at"] = pnl, settled_at
    return b, row


def _settled_writes(p, book_id):
    return [a for k, s, a in p.sent if "ml-book-settled" in s and a[0] == book_id]


def _bbos(v):
    return [c for c in v.calls if c[0] == "bbo"]


# ------------------------------------------------ step M: the re-anchor

def test_e17_a_settled_row_without_the_venues_marks_on_a_live_open_market_reanchors_and_the_book_lives():
    p = _pool()
    b, row = _retired(p)
    v = _Venue(state=OPEN, held={SLUG: 13})
    st = _tick(p, v)
    assert row["status"] == "filled" and row["filled_shares"] == 13.0, "the row back to filled, its shares kept"
    assert row["raw"]["mirror"]["reanchored"]["status_was"] == "settled"
    assert b["state"] == "live" and b["ledger_net"] == 13 and b["standing_row_id"] == row["id"]
    assert b["last_reason"] == "standing_row_reanchored"
    assert b["last_plan"]["standing_row_reanchored"] == {"row": row["id"], "status_was": "settled", "ledger": 13.0,
                                                          "venue_state": OPEN}
    assert _census(st, "standing_row_reanchored") == 1 and _census(st, "standing_row_ambiguous") == 0
    assert not _settled_writes(p, b["id"]) and st["closed_books"] == 0
    assert len(_bbos(v)) == 1, "one paced quote read: the venue's own word this tick"
    # the next tick: an ordinary live book, planned on its row
    st2 = _tick(p, _Venue(state=OPEN, held={SLUG: 13}), now=NOW + 30, http=_mkt(300.0, 0.0))
    assert b["state"] == "live" and row["status"] == "filled" and _census(st2, "standing_row_reanchored") == 0
    assert b["target"] is not None


def test_e17_the_venues_own_settle_closes_byte_for_byte_as_before_with_no_quote_read():
    p = _pool()
    p.markets[CID] = {"closed": False, "resolved": False, "resolved_prices": None}
    b, row = _retired(p, pnl=5.2, settled_at=NOW - 10)
    v = _Venue(state=OPEN, held={SLUG: 13})
    st = _tick(p, v)
    assert b["state"] == "closed" and b["last_reason"] == "closed: standing row settled"
    assert row["status"] == "settled" and not _bbos(v)
    assert _settled_writes(p, b["id"]) == [(b["id"], 5.2, None, None, "closed: standing row settled")]
    assert _census(st, "standing_row_reanchored") == 0 and _census(st, "standing_row_ambiguous") == 0
    assert st["closed_books"] == 1


@pytest.mark.parametrize("shape", ["gamma_unreadable", "venue_halted", "venue_unread", "row_shares_not_ledger",
                                   "no_settled_at_but_pnl", "pnl_none_but_settled_at"])
def test_e17_a_reader_that_cannot_tell_closes_as_before_and_is_named_ambiguous(shape):
    p = _pool()
    kw = {}
    v = _Venue(state=OPEN, held={SLUG: 13})
    if shape == "gamma_unreadable":
        p.raise_on.append(("ml-market", RuntimeError("blip")))
    elif shape == "venue_halted":
        v = _Venue(state=HALTED, held={SLUG: 13})
    elif shape == "venue_unread":
        v = _Venue(raise_bbo=True, held={SLUG: 13})
    elif shape == "no_settled_at_but_pnl":
        kw["pnl"] = 5.2
    elif shape == "pnl_none_but_settled_at":
        kw["settled_at"] = NOW - 10
    b, row = _retired(p, **kw)
    if shape == "row_shares_not_ledger":
        row["filled_shares"] = 40.0
    st = _tick(p, v)
    assert b["state"] == "closed" and row["status"] == "settled"
    assert b["last_reason"] == "closed: standing row settled"
    assert _census(st, "standing_row_ambiguous") == 1 and _census(st, "standing_row_reanchored") == 0
    assert any(x["what"] == "standing_row_ambiguous" for x in ml._RECENT)


@pytest.mark.parametrize("shape", ["venue_expired", "gamma_closed", "gamma_resolved", "resolved_prices"])
def test_e17_a_terminal_reader_closes_as_before_and_names_nothing(shape):
    p = _pool()
    v = _Venue(state=OPEN, held={SLUG: 13})
    if shape == "venue_expired":
        v = _Venue(state=EXPIRED, held={SLUG: 13})
    elif shape == "gamma_closed":
        p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    elif shape == "gamma_resolved":
        p.markets[CID] = {"closed": False, "resolved": True, "resolved_prices": None}
    elif shape == "resolved_prices":
        p.markets[CID] = {"closed": False, "resolved": False, "resolved_prices": [1, 0]}
    b, row = _retired(p)
    st = _tick(p, v)
    assert b["state"] == "closed" and row["status"] == "settled"
    assert _census(st, "standing_row_ambiguous") == 0 and _census(st, "standing_row_reanchored") == 0


@pytest.mark.parametrize("status, ledger", [("settled", 0), ("cashed_out", 13), ("cancelled", 13)])
def test_e17_a_flat_book_or_the_mirrors_own_close_never_reanchors(status, ledger):
    p = _pool()
    b, row = _retired(p, status=status, ledger=ledger)
    v = _Venue(state=OPEN, held={SLUG: ledger})
    st = _tick(p, v)
    assert b["state"] == "closed" and row["status"] == status and not _bbos(v)
    assert b["last_reason"] == f"closed: standing row {status}"
    assert _census(st, "standing_row_reanchored") == 0 and _census(st, "standing_row_ambiguous") == 0


def test_e17_the_reanchor_write_touching_no_row_is_named_and_the_close_stands():
    """The UPDATE's WHERE re-checks the marks: a row the venue settled
    between the read and the write (or any write failure) writes nothing
    -- `standing_row_reanchor_failed`, the close as before."""
    p = _pool()
    b, row = _retired(p)

    def _settle_between(kind, s, a):
        row["pnl"], row["settled_at"] = 5.2, NOW
    p.raise_on.append(("ml-standing-reanchor", RuntimeError("write failed")))
    st = _tick(p, _Venue(state=OPEN, held={SLUG: 13}))
    assert b["state"] == "closed" and _census(st, "standing_row_reanchor_failed") == 1
    assert _census(st, "standing_row_reanchored") == 0
    assert row["status"] == "settled"


# ------------------------------------------- the candidate: the adoption

def _prior(p, ledger=13, avg_cost=0.60, closed_at=CLOSE_TS, row_status="settled", pnl=None, settled_at=None,
           row_shares=None):
    b = p.add_book(ledger=ledger, avg_cost=avg_cost, state="closed", standing_status=row_status,
                   closed_at=closed_at, last_reason="closed: standing row settled")
    row = p.rows[b["standing_row_id"]]
    row["pnl"], row["settled_at"] = pnl, settled_at
    if row_shares is not None:
        row["filled_shares"] = row_shares
    return b, row


def _204_fills(after=6350.0):
    """His 134.6 @0.60 two hours ago (before the close), then `after`
    shares @0.60 ten minutes ago (after it), both clocked as ingested."""
    fs = [_fill(M, "BUY", 134.6, 0.60, NOW - 7200, detected_at=NOW - 7200)]
    if after:
        fs.append(_fill(M, "BUY", after, 0.60, NOW - 600, detected_at=NOW - 600))
    return fs


def test_e17_204s_shape_the_closed_books_13_are_adopted_and_episode_2_sizes_his_flow_since_the_close(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
    prior, row = _prior(p)
    # the mark 10c over his cost: the block is not admitted (E12's allowance), the arithmetic is pinned
    v = _Venue(bid=0.70, ask=0.71, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(6484.6, 0.0))
    assert _census(st, "venue_already_holds") == 0 and _census(st, "adopted_prior_episode") == 1
    new = [b for b in p.books.values() if b["id"] != prior["id"]]
    assert len(new) == 1
    b = new[0]
    assert b["episode"] == 2 and b["state"] == "live" and b["standing_row_id"] == row["id"]
    assert row["status"] == "filled" and row["filled_shares"] == 13.0 and row["fill_price"] == 0.60
    assert row["raw"]["mirror"]["reanchored"]["adopted_by"] == b["id"]
    # the fold (2026-09-08, review MEDIUM-1): the cost is carried as avg_cost
    # only; nothing was bought this episode, so gross_buy_usd and
    # peak_exposure_usd are 0 and the prior keeps its own 7.8
    assert b["avg_cost"] == 0.60 and b["gross_buy_usd"] == 0.0 and b["peak_exposure_usd"] == 0.0
    assert b["last_plan"]["adopted_prior_episode"] == {"book": prior["id"], "row": row["id"], "episode": 1,
                                                        "shares": 13.0, "avg_cost": 0.60, "closed_at": CLOSE_TS}
    assert b["last_plan"]["catchup"]["since"] == CLOSE_TS
    # the block is his pre-close 134.6 less the 130 the 13 cover at 0.10: 4.6 -> flow 6,480 -> target 648
    assert b["last_plan"]["catchup"]["flow_base"] == pytest.approx(4.6)
    assert b["last_plan"]["catchup"]["why"] == "flow_only" and b["flow_base"] == pytest.approx(4.6)
    assert b["target"] == 648
    # the increase is the target less the adopted 13, resting at his cent (the ask is 11c over him)
    assert [c[2:5] for c in _places(v)] == [(0.60, 635, False)]
    assert b["ledger_net"] == 13
    assert prior["state"] == "closed", "the prior stays closed; only its row moved"


def test_e17_a_different_magnitude_on_the_venue_stays_venue_already_holds(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    for held in (20, 11, -13):
        p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
        prior, row = _prior(p)
        st = _tick(p, _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: held}), http=_mkt(6484.6, 0.0))
        assert _census(st, "venue_already_holds") == 1 and _census(st, "adopted_prior_episode") == 0, held
        assert len(p.books) == 1 and row["status"] == "settled"


def test_e17_his_fills_before_the_close_never_size_the_reopen(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(after=0.0), snap={M: 134.6, N: 0.0})
    prior, row = _prior(p)
    v = _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(134.6, 0.0))
    assert _census(st, "adopt_no_fill_since_close") == 1 and _census(st, "adopted_prior_episode") == 0
    assert len(p.books) == 1 and row["status"] == "settled" and not _places(v)


def test_e17_the_priors_row_carrying_the_venues_settle_is_refused_by_name_nothing_reopens(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
    prior, row = _prior(p, pnl=5.2, settled_at=NOW - 3500)
    v = _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(6484.6, 0.0))
    assert _census(st, "adopt_prior_venue_settled") == 1 and _census(st, "adopted_prior_episode") == 0
    assert _census(st, "venue_already_holds") == 0
    assert len(p.books) == 1 and row["status"] == "settled" and row["pnl"] == 5.2 and not _places(v)


@pytest.mark.parametrize("shape", ["no_close_clock", "row_shares_not_ledger", "row_cashed_out", "prior_unreadable"])
def test_e17_a_prior_that_cannot_be_read_refuses_by_name(monkeypatch, shape):
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
    kw = {}
    if shape == "no_close_clock":
        kw["closed_at"] = None
    elif shape == "row_shares_not_ledger":
        kw["row_shares"] = 40.0
    elif shape == "row_cashed_out":
        kw["row_status"] = "cashed_out"
    prior, row = _prior(p, **kw)
    if shape == "prior_unreadable":
        p.raise_on.append(("ml-prior-episode", RuntimeError("blip")))
    v = _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(6484.6, 0.0))
    name = "venue_already_holds" if shape == "prior_unreadable" else "adopt_prior_unreadable"
    assert _census(st, name) == 1 and _census(st, "adopted_prior_episode") == 0, st["census"]
    assert len(p.books) == 1 and not _places(v)


def test_e17_the_game_cap_counts_the_adopted_shares(monkeypatch):
    """His flow since the close is 60,000: 6,000 at 0.10 is $3,570 at
    the 0.595 mark, over the $2,500 game cap -- the target is the cap's
    4,201 and the adopted 13 are inside it: the increase is 4,188,
    never 4,201."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(after=60000.0), snap={M: 60134.6, N: 0.0})
    prior, row = _prior(p)
    v = _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(60134.6, 0.0))
    assert _census(st, "adopted_prior_episode") == 1
    b = [b for b in p.books.values() if b["id"] != prior["id"]][0]
    assert b["target"] == 4201
    # the take at his cent is sized by the room at the ask (4,166), the rest at the bid for the 4,188
    placed = [c[2:5] for c in _places(v)]
    assert placed[-1][1:] == (4188, False) and all(q <= 4188 and px in (0.59, 0.60) for px, q, _ in placed), placed


def test_e17_a_transaction_that_fails_leaves_nothing_half_opened(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
    prior, row = _prior(p)
    p.raise_on.append(("ml-book-adopt", RuntimeError("boom")))
    v = _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(6484.6, 0.0))
    assert st["census"].get("open_failed:RuntimeError") == 1 and _census(st, "adopted_prior_episode") == 0
    assert len(p.books) == 1 and row["status"] == "settled", "rolled back: the row and the books as before"
    assert p.tx_events[-1] == "rollback" and not _places(v)


# ----------------------------------------------------------- the rules

def test_e17_prior_episode_adoption_pure():
    tol = mi.VENUE_LEDGER_TOL_SHARES
    assert rules.prior_episode_adoption(13.0, 13.0) is True
    assert rules.prior_episode_adoption(13.0 + tol, 13.0) is True
    assert rules.prior_episode_adoption(13.0 + tol + 0.01, 13.0) is False
    assert rules.prior_episode_adoption(-13.0, -13.0) is True, "the short mirror image"
    assert rules.prior_episode_adoption(-13.0, 13.0) is False and rules.prior_episode_adoption(13.0, -13.0) is False
    assert rules.prior_episode_adoption(0.5, 0.5) is False, "a flat prior is no prior"
    for bad in (None, "13", math.nan, math.inf, True):
        assert rules.prior_episode_adoption(bad, 13.0) is False and rules.prior_episode_adoption(13.0, bad) is False
    # admission: the same rule, the fact fail-closed at None
    f = rules.AdmissionFacts(increases_ok=True, per_fill_usd=50.0, family="moneyline", per_side=False,
                             market_closed=False, market_resolved=False, game_too_far_out=False,
                             mapping_ok=True, edge_ok=True, cell_ok=True, legacy_row=False,
                             slug_recent_copy=False, underdog_coholds=False, venue_net=13.0,
                             kalshi_claimed=False, side_band_hit=False, snap_fresh=True, drift=0.0,
                             books_live=0, opened_today=0, first_fill_ok=True)
    assert rules.admission(f) == "venue_already_holds"
    assert rules.admission(rules.AdmissionFacts(**{**f.__dict__, "prior_episode_ledger": 13.0})) is None
    assert rules.admission(rules.AdmissionFacts(**{**f.__dict__, "prior_episode_ledger": 20.0})) == "venue_already_holds"
    assert rules.admission(rules.AdmissionFacts(**{**f.__dict__, "venue_net": 0.0, "prior_episode_ledger": 13.0})) is None


def test_e17_adopted_block_pure():
    assert rules.adopted_block(134.6, 13.0, 0.10) == pytest.approx(4.6)
    assert rules.adopted_block(134.6, 13.0, 1.0) == pytest.approx(121.6)
    assert rules.adopted_block(100.0, 13.0, 0.10) == 0.0, "never across zero"
    assert rules.adopted_block(-134.6, -13.0, 0.10) == pytest.approx(-4.6), "the short mirror image"
    assert rules.adopted_block(-100.0, -13.0, 0.10) == 0.0
    assert rules.adopted_block(134.6, None, 0.10) == 134.6 and rules.adopted_block(134.6, 13.0, 0.0) == 134.6
    assert rules.adopted_block(134.6, -13.0, 0.10) == 134.6, "shares against the leg cover nothing"
    assert rules.adopted_block(None, 13.0, 0.10) is None


def test_e17_the_census_names_sit_before_registered_no_increase():
    keys = ml.CENSUS_KEYS
    assert keys[-12] == "registered_no_increase"
    # the fold (2026-09-08, review HIGH-1) added `venue_dust_ours` beside
    # `adopted_prior_episode`: eight names; E19 (PNL lane 8) placed
    # `drift_smaller_open` after them, before `registered_no_increase`;
    # E14b (FILL lane 1) placed `exit_take_rested` between the two
    # (-21:-13 -> -22:-14); E20
    # placed `wrong_sign_hold` before that (-22:-14 -> -23:-15); E14
    # (FILL lane 2) placed `take_in_band` after both (-23:-15 -> -24:-16);
    # FILL lane 3 placed its three names after that (-24:-16 -> -27:-19),
    # T2 (FILL lane 4) its two (-> -29:-21), FILL lane 5 its three (-> -32:-24)
    # and E22 (FILL lane 22) its four lost_fill_* names (-> -36:-28)
    assert keys[-36:-28] == ("standing_row_reanchored", "standing_row_ambiguous", "standing_row_reanchor_failed",
                             "adopted_prior_episode", "venue_dust_ours", "adopt_prior_unreadable",
                             "adopt_no_fill_since_close", "adopt_prior_venue_settled")
    assert keys[-13] == "drift_smaller_open"
    # landed 2026-09-08 after E18 (lane 6): its six names sit between
    # `venue_market_ended` and this block
    assert keys.index("venue_market_ended") < keys.index("standing_row_reanchored") and len(set(keys)) == len(keys)
