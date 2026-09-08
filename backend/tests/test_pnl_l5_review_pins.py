"""PNL lane 5 (E17) adversarial review pins (2026-09-08).

Mutants of the lane's rules the lane's 29 tests did not kill (the
review's table): the gamma row reading closed NULL; a prior on the
OTHER leg handed to admission; the prior's row settling between the
prior read and the adopt transaction; a close clock in the future; the
adopted magnitude taken from the venue instead of the closed ledger.
Each is pinned here on the real planner through the worker file's fakes.

Two findings were pinned AS THE CODE STOOD and say so in their name
(`_DEFECT_`): a sub-share venue residual after a flip close (Martinez
2026-09-08 12:10Z: 371.2 bought vs 371 sold, the venue +0.2, the
INTEGER ledger 0) was `venue_already_holds` on every tick with nothing
to adopt; the adopted cost was written as the new book's gross_buy_usd /
peak_exposure_usd beside the prior's, so the day's peak_stake summed it
twice. The fold (2026-09-08) inverted both under their names: the
residual beside a closed mirror book on the market is the mirror's own
dust (`venue_dust_ours`, the book opened at ledger 0); the adopt writes
gross_buy_usd / peak_exposure_usd 0. The last test runs the lane's
statements on the real schema (the local Postgres; skipped, never
faked, without one).
"""
import datetime as _dt
import json
import math
import os
import pathlib
import uuid

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.scripts import migrate
from sportsassets.workers import mirror_live as ml
from tests.test_e17_standing_reanchor import (
    OPEN, _204_fills, _L5Pool, _pool, _prior, _retired,
)
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    CID, M, N, NOW, SLUG, _armed, _census, _his, _mkt, _places, _rails_2026_09_06, _run, _shorts_on, _tick,
    _Venue,
)

MIG_DIR = pathlib.Path(migrate.MIGRATIONS_DIR)
DSN_BASE = os.environ.get(
    "MIRROR_SQL_PIN_DSN",
    os.environ.get("S1_SQL_PIN_DSN", "postgresql://sportsassets:sportsassets@localhost:5432/postgres"))


# ------------------------------------------------ step M: the readers

def test_r1_a_gamma_row_reading_closed_null_never_reanchors():
    """Mutant M04 (closed=None read as live) survived: a markets row
    whose closed / resolved are NULL is not `closed=f resolved=f` -- the
    lane's own words -- so the book closes as before whatever the venue
    says. Folded (2026-09-08, the review's LOW-3): the close was silent
    where every other reader that cannot tell is named; it is now
    `standing_row_ambiguous` with `market_unreadable` as its why, the
    close itself byte for byte as before."""
    p = _pool()
    p.markets[CID] = {"closed": None, "resolved": None, "resolved_prices": None}
    b, row = _retired(p)
    v = _Venue(state=OPEN, held={SLUG: 13})
    st = _tick(p, v)
    assert b["state"] == "closed" and row["status"] == "settled"
    assert b["last_reason"] == "closed: standing row settled"
    assert _census(st, "standing_row_reanchored") == 0 and st["closed_books"] == 1
    assert _census(st, "standing_row_ambiguous") == 1
    assert [x["why"] for x in ml._RECENT if x["what"] == "standing_row_ambiguous"] == ["market_unreadable"]


# ------------------------------------------- the candidate: the leg

def test_r2_a_short_candidate_never_adopts_a_long_prior_on_the_same_venue_shares(monkeypatch):
    """Mutant M13 (the leg check dropped) survived. His net has FLIPPED
    short since the close (100 long against 400 other); the closed
    episode's ledger is +13 and the venue holds exactly +13. Those are
    ours, but on the leg he has left: handed to admission as no prior,
    `venue_already_holds` as before, no book, the prior's row untouched.
    Without the check the short book would open adopting +13 LONG
    shares as its opening ledger against a negative target."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    p = _pool(fills=_his(100, other_size=400, other_px=0.72), snap={M: 100.0, N: 400.0})
    prior, row = _prior(p, ledger=13)
    v = _Venue(bid=0.30, ask=0.32, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert _census(st, "venue_already_holds") == 1, st["census"]
    assert _census(st, "adopted_prior_episode") == 0 and _census(st, "short_open") == 0
    assert len(p.books) == 1 and row["status"] == "settled" and not _places(v)


# ------------------------------------ the candidate: the transaction

class _SettlesAfterRead(_L5Pool):
    """The prior's row gets the venue's two marks right after the prior
    read (the settle sweep running beside the tick)."""

    def _run(self, kind, sql, a):
        out = super()._run(kind, sql, a)
        if "ml-prior-episode" in " ".join(sql.split()) and out:
            r = self.rows[out["standing_row_id"]]
            r["pnl"], r["settled_at"] = 5.2, NOW - 1
        return out


def test_r3_the_priors_row_settling_between_the_read_and_the_open_rolls_the_open_back(monkeypatch):
    """Mutant M16 (the transaction's re-anchor touching no row not
    raised) survived: the row's marks land between the prior read and
    the adopt transaction, the re-anchor UPDATE finds no re-anchorable
    row, and the whole open must roll back -- `open_failed:RuntimeError`,
    one book, the row 'settled' with its marks, nothing placed. Without
    the raise a live book would stand on a venue-settled row."""
    _rails_2026_09_06(monkeypatch)
    p = _SettlesAfterRead(fills=_204_fills(), snap={M: 6484.6, N: 0.0}, snap_at=NOW - 40, ratio_fills=None)
    prior, row = _prior(p)
    v = _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(6484.6, 0.0))
    assert st["census"].get("open_failed:RuntimeError") == 1, st["census"]
    assert _census(st, "adopted_prior_episode") == 0
    assert len(p.books) == 1 and row["status"] == "settled" and row["pnl"] == 5.2
    assert p.tx_events[-1] == "rollback" and not _places(v)


def test_r4_a_close_clock_in_the_future_is_unreadable(monkeypatch):
    """Mutant M19 survived: a closed_at ahead of the tick's clock cannot
    set the reopen's first sight -- `adopt_prior_unreadable`, nothing
    opens (every fill of his would read as flow on a future clock)."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
    prior, row = _prior(p, closed_at=NOW + 60.0)
    v = _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(6484.6, 0.0))
    assert _census(st, "adopt_prior_unreadable") == 1 and _census(st, "adopted_prior_episode") == 0
    assert len(p.books) == 1 and row["status"] == "settled" and not _places(v)


def test_r5_the_adopted_magnitude_is_the_closed_ledger_never_the_venues_figure(monkeypatch):
    """Mutant M20 survived: the venue reads 13.9 (inside the D1 dust of
    the closed ledger 13). The adopted shares are the LEDGER's 13 -- the
    plan, the book's ledger, the row's shares -- never 13.9 (an INTEGER
    column would silently truncate a venue figure). After the fold
    (2026-09-08, MEDIUM-1) the cost travels as avg_cost alone:
    gross_buy_usd is 0, nothing bought this episode."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
    prior, row = _prior(p)
    v = _Venue(bid=0.70, ask=0.71, state=OPEN, held={SLUG: 13.9})
    st = _tick(p, v, http=_mkt(6484.6, 0.0))
    assert _census(st, "adopted_prior_episode") == 1
    b = [b for b in p.books.values() if b["id"] != prior["id"]][0]
    assert b["ledger_net"] == 13 and b["last_plan"]["adopted_prior_episode"]["shares"] == 13.0
    assert b["avg_cost"] == 0.60 and b["gross_buy_usd"] == 0.0 and row["filled_shares"] == 13.0


# --------------------------------------- the two findings, as they stand

def test_r6_DEFECT_sub_share_venue_dust_after_a_flip_close_is_refused_forever(monkeypatch):
    """Martinez 2026-09-08 12:10Z: the flip close's IOC bought 371.2
    against the 371 sold; the venue holds +0.2, the INTEGER ledger reads
    0, the book closed flat. The lane read a prior only with
    `ledger_net <> 0`, so there was nothing to adopt, and admission read
    any non-zero venue figure as foreign: `venue_already_holds` on every
    later fill of his, on every tick, until someone cleared the dust by
    hand. Pinned AS IT STOOD by the review (its HIGH-1); INVERTED by the
    fold (2026-09-08, the owner's mandate) under its name: the residual
    under mi.VENUE_LEDGER_TOL_SHARES beside a CLOSED mirror book on the
    market whose standing row is lane 'mirror' on the asset (identity,
    never magnitude: `_prior_dust_ours`, the fact
    `AdmissionFacts.venue_dust_ours` True) is the mirror's own dust --
    admitted, the book opened at ledger 0 (the freeze reads |0.2 - 0|
    as agreement), `venue_dust_ours` on the census, `venue_dust =
    {venue, prior_book}` on the first plan; nothing adopted, the
    prior's cashed_out row untouched; the later ticks plan the live
    book and never refuse. Any residual at or over the tolerance stays
    `venue_already_holds` (the lane's magnitude pins)."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
    prior, row = _prior(p, ledger=0, row_status="cashed_out")
    v = _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: 0.2})
    assert 0.2 < mi.VENUE_LEDGER_TOL_SHARES, "the residual is inside the dust the freeze tolerates"
    st = _tick(p, v, http=_mkt(6484.6, 0.0))
    assert _census(st, "venue_dust_ours") == 1 and _census(st, "venue_already_holds") == 0, st["census"]
    assert _census(st, "adopted_prior_episode") == 0
    new = [b for b in p.books.values() if b["id"] != prior["id"]]
    assert len(new) == 1
    b = new[0]
    assert b["state"] == "live" and b["ledger_net"] == 0 and b["episode"] == 2
    assert b["last_plan"]["venue_dust"] == {"venue": 0.2, "prior_book": prior["id"]}
    assert "adopted_prior_episode" not in b["last_plan"]
    assert row["status"] == "cashed_out" and prior["state"] == "closed", "nothing adopted, nothing re-anchored"
    assert any(x["what"] == "venue_dust_ours" and x["book"] == b["id"] for x in ml._RECENT)
    for now in (NOW + 30, NOW + 600):
        st = _tick(p, v, now=now, http=_mkt(6484.6, 0.0))
        assert _census(st, "venue_already_holds") == 0 and _census(st, "venue_dust_ours") == 0, st["census"]
        assert b["state"] == "live" and len(p.books) == 2


def test_r6b_the_dust_admits_only_on_the_identity_never_on_the_magnitude_alone(monkeypatch):
    """The fold's rails (2026-09-08, HIGH-1): the same 0.2 with NO closed
    mirror book on the market, or with the identity read failing, is
    `venue_already_holds` as before; a residual AT the tolerance (1.0)
    beside the closed book is `venue_already_holds` too (the dust is
    strictly under it). The pure rule: only the bool True admits."""
    _rails_2026_09_06(monkeypatch)
    for shape in ("no_prior_book", "identity_unreadable", "at_tolerance"):
        p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
        held = 0.2
        if shape != "no_prior_book":
            _prior(p, ledger=0, row_status="cashed_out")
        if shape == "identity_unreadable":
            p.raise_on.append(("ml-prior-dust", RuntimeError("blip")))
        if shape == "at_tolerance":
            held = mi.VENUE_LEDGER_TOL_SHARES
        v = _Venue(bid=0.59, ask=0.60, state=OPEN, held={SLUG: held})
        st = _tick(p, v, http=_mkt(6484.6, 0.0))
        assert _census(st, "venue_already_holds") == 1 and _census(st, "venue_dust_ours") == 0, (shape, st["census"])
        assert not _places(v) and all(b["state"] == "closed" for b in p.books.values()), shape
    tol = mi.VENUE_LEDGER_TOL_SHARES
    assert rules.venue_dust_is_ours(0.2, True) is True and rules.venue_dust_is_ours(-0.2, True) is True
    assert rules.venue_dust_is_ours(tol, True) is False and rules.venue_dust_is_ours(0.0, True) is False
    for bad in (False, None, 1, "True"):
        assert rules.venue_dust_is_ours(0.2, bad) is False, bad
    for bad in (None, "0.2", math.nan, math.inf, True):
        assert rules.venue_dust_is_ours(bad, True) is False, bad


def test_r7_DEFECT_the_adopted_cost_is_counted_on_both_episodes(monkeypatch):
    """After the adoption the prior (closed) keeps its gross_buy_usd /
    peak_exposure_usd of 7.8 and the new book is written the same 7.8 as
    ITS gross buys and peak exposure (_SQL_BOOK_ADOPT), so `mirror-pnl`'s
    peak_stake and lane M's stake denominator (both sum
    peak_exposure_usd over the day's books) carried the 13 shares twice.
    Pinned AS IT STOOD by the review (its MEDIUM-1); INVERTED by the
    fold (2026-09-08) under its name: the adopt writes gross_buy_usd 0
    and peak_exposure_usd 0 on the new book -- nothing was bought this
    episode -- while the prior keeps its 7.8, so the day's peak_stake
    sums the shares once. Lane M's stake denominator (max(peak) per
    (whale, market) across episodes) is lane M's to change, not this
    lane's."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_204_fills(), snap={M: 6484.6, N: 0.0})
    prior, row = _prior(p)
    v = _Venue(bid=0.70, ask=0.71, state=OPEN, held={SLUG: 13})
    st = _tick(p, v, http=_mkt(6484.6, 0.0))
    assert _census(st, "adopted_prior_episode") == 1
    b = [b for b in p.books.values() if b["id"] != prior["id"]][0]
    assert prior["peak_exposure_usd"] == pytest.approx(7.8) and b["peak_exposure_usd"] == 0.0
    assert b["ledger_net"] == 13 and b["avg_cost"] == 0.60, "the shares and their cost still carried"
    assert prior["gross_buy_usd"] + b["gross_buy_usd"] == pytest.approx(7.8), "7.8 of shares, 7.8 of stake"


# ------------------------------------------------ the real schema

async def _scratch():
    asyncpg = pytest.importorskip("asyncpg")
    try:
        admin = await asyncpg.connect(DSN_BASE, timeout=4)
    except Exception:  # noqa: BLE001 -- no local PG: skip, never fake
        pytest.skip("no local postgres for the E17 statement pins")
    name = "l5r_" + uuid.uuid4().hex[:10]
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
    for m in sorted(MIG_DIR.glob("*.sql")):
        try:
            await conn.execute(m.read_text())
        except Exception:  # noqa: BLE001 -- 031 / 055 name a table no migration creates; the rest apply
            pass
    return admin, conn, name


_ROW = ("INSERT INTO live_orders (asset, side, his_price, limit_price, requested_usd, requested_shares, status, lane, "
        "whale_username, condition_id, us_market_slug, filled_shares, fill_price, pnl, settled_at, raw) "
        "VALUES ($1, 'BUY', 0.6, 0.6, 7.8, 13, 'settled', 'mirror', 'rn1', $2, $3, 13, 0.6, $4, $5, $6::jsonb) "
        "RETURNING id")
_BOOK = ("INSERT INTO mirror_books (whale, condition_id, us_market_slug, long_asset, state, ledger_net, avg_cost, "
         "standing_row_id, closed_at, episode) VALUES ('rn1', $1, $2, $3, $4, $5, 0.6, $6, $7, $8) RETURNING id")


def test_r8_the_three_statements_on_the_real_schema():
    _run(_r8())


async def _r8():
    """`_SQL_PRIOR_EPISODE` (the newest closed book with shares, NULLS
    LAST, the row's marks joined), `_SQL_STANDING_REANCHOR` (the venue's
    two marks refuse the flip; a bare 'settled' row flips and carries the
    stamp when raw.mirror exists), `_SQL_BOOK_ADOPT` (guarded on ledger
    0; the worker's float on the INTEGER column is taken whole), and the
    045 premise the adoption stands on (a second 'filled' row beside a
    'settled' one on the asset is refused). Pinned as it stands: the
    stamp is silently dropped by jsonb_set when raw carries no `mirror`
    object (the status still flips; the review's LOW-2, accepted by the
    fold). The fold (2026-09-08): `_SQL_BOOK_ADOPT` takes three
    parameters and writes gross_buy_usd / peak_exposure_usd 0 itself
    (MEDIUM-1); `_SQL_PRIOR_DUST_OURS` (HIGH-1) finds the newest closed
    book whose standing row is lane 'mirror' on one of the market's
    tokens -- a flat one included -- and nothing on another token or
    another condition."""
    admin, conn, name = await _scratch()
    try:
        ago = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(hours=1)
        bare = await conn.fetchval(_ROW, "tokA", "cid1", "s-1", None, None, json.dumps({"lane": "mirror", "mirror": {}}))
        venue = await conn.fetchval(_ROW, "tokB", "cid1", "s-1", 5.2, ago, json.dumps({"lane": "mirror", "mirror": {}}))
        nomirror = await conn.fetchval(_ROW, "tokC", "cid9", "s-9", None, None, json.dumps({"lane": "mirror"}))
        with pytest.raises(Exception) as ei:
            await conn.execute("INSERT INTO live_orders (asset, side, his_price, limit_price, requested_usd, "
                               "requested_shares, status, lane, whale_username) "
                               "VALUES ('tokA', 'BUY', 0.6, 0.6, 1, 1, 'filled', 'mirror', 'rn1')")
        assert "live_orders_one_fill_per_asset" in str(ei.value), "the 045 premise the adoption stands on"
        newest = await conn.fetchval(_BOOK, "cid1", "s-1", "tokA", "closed", 13, bare, ago, 1)
        await conn.fetchval(_BOOK, "cid1", "s-1", "tokA", "closed", 13, None, None, 0)      # closed_at NULL: last
        await conn.fetchval(_BOOK, "cid1", "s-1", "tokA", "closed", 0, venue, ago + _dt.timedelta(minutes=5), 2)
        await conn.fetchval(_BOOK, "cid1", "s-1b", "tokA", "live", 13, venue, None, 3)      # live: never a prior
        r = dict(await conn.fetchrow(ml._SQL_PRIOR_EPISODE, "rn1", "cid1"))
        assert r["id"] == newest and r["ledger_net"] == 13 and r["standing_row_id"] == bare
        assert r["row_status"] == "settled" and r["row_lane"] == "mirror" and r["row_pnl"] is None
        assert r["row_settled_at"] is None and r["row_shares"] == 13.0 and isinstance(r["closed_ts"], float)
        assert await conn.fetchrow(ml._SQL_PRIOR_EPISODE, "rn1", "cid-none") is None
        st = dict(await conn.fetchrow(ml._SQL_STANDING_READ, bare))
        assert st["status"] == "settled" and st["pnl"] is None and st["settled_at"] is None
        assert await conn.fetchrow(ml._SQL_STANDING_REANCHOR, venue, json.dumps({"at": 1.0})) is None
        assert (await conn.fetchval("SELECT status FROM live_orders WHERE id = $1", venue)) == "settled"
        got = await conn.fetchrow(ml._SQL_STANDING_REANCHOR, bare, json.dumps({"at": 1.0, "status_was": "settled"}))
        assert got["id"] == bare
        raw = json.loads(await conn.fetchval("SELECT raw::text FROM live_orders WHERE id = $1", bare))
        assert raw["mirror"]["reanchored"] == {"at": 1.0, "status_was": "settled"}
        assert (await conn.fetchval("SELECT status FROM live_orders WHERE id = $1", bare)) == "filled"
        assert await conn.fetchrow(ml._SQL_STANDING_REANCHOR, bare, json.dumps({"at": 2.0})) is None, "filled: once"
        got = await conn.fetchrow(ml._SQL_STANDING_REANCHOR, nomirror, json.dumps({"at": 3.0}))
        raw = json.loads(await conn.fetchval("SELECT raw::text FROM live_orders WHERE id = $1", nomirror))
        assert got["id"] == nomirror and "mirror" not in raw, "as it stands: the stamp is dropped, the flip lands"
        new = await conn.fetchval(_BOOK, "cid1", "s-1", "tokA", "live", 0, None, None, 4)
        assert (await conn.execute(ml._SQL_BOOK_ADOPT, new, 13.0, 0.6)) == "UPDATE 1"
        got = dict(await conn.fetchrow(
            "SELECT ledger_net, avg_cost::float8 AS avg_cost, gross_buy_usd::float8 AS g, "
            "peak_exposure_usd::float8 AS pk FROM mirror_books WHERE id = $1", new))
        assert got == {"ledger_net": 13, "avg_cost": 0.6, "g": 0.0, "pk": 0.0}, got
        assert (await conn.execute(ml._SQL_BOOK_ADOPT, new, 13.0, 0.6)) == "UPDATE 0", "guarded on 0"
        # the fold (HIGH-1): the dust identity. The newest closed book on
        # cid1 is the flat one (ledger 0, closed ago + 5 min) whose row is
        # `venue` on tokB -- found on either token order; nothing on a
        # token the market does not carry, nothing on another condition
        d = dict(await conn.fetchrow(ml._SQL_PRIOR_DUST_OURS, "rn1", "cid1", "tokA", "tokB"))
        assert d["ledger_net"] == 0 and d["row_asset"] == "tokB"
        assert dict(await conn.fetchrow(ml._SQL_PRIOR_DUST_OURS, "rn1", "cid1", "tokB", None))["id"] == d["id"]
        assert await conn.fetchrow(ml._SQL_PRIOR_DUST_OURS, "rn1", "cid1", "tokZ", None) is None
        assert await conn.fetchrow(ml._SQL_PRIOR_DUST_OURS, "rn1", "cid-none", "tokA", "tokB") is None
    finally:
        await conn.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()
