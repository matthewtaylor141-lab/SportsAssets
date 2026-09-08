"""PNL lane 7 (2026-09-08) adversarial review pins -- coverage items 1, 3
and 4: the tennis-witness preset, the exact lane's 404 trail, the
event-stale memo. Applied on top of hard2/PNL_L7.patch.

Every `review_*` test that pins a FINDING pinned the DEFECT AS IT STOOD
and said so in its docstring: it went red when the fold fixed it, and
the fold (2026-09-08) flipped the assertion to the rule the docstring
names -- h1 (the memo released by his next fill or a wake), m1 (the
newest of the stamp and the rows), m2 (distinct slugs), l1 (the docs
say the row carries the name alone); m3 is a note and stands. The
other pins hold what the lane got right on the real schema and
against the walk as landed (E7 stamps, E6 persistence, E13's key).
"""
import inspect
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_c8_tennis_witness import DAY, GEA, GEA_TITLE, KEY, T, _cand, _gea, _no_map, _preset_lines, _V
from tests.test_e7_cand_memo import _bbos, _dt
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails ride the import
    CID, M, N, NOW, SLUG, _Http, _NoThrottle, _Pool, _Venue, _armed, _census, _fill, _flat, _his, _kinds,
    _pool, _ratio_fills, _run, _tick,
)
from tests.test_mirror_shadow import HIS, _nosleep
from tests.test_mirror_shadow import _Pool as _ShadowPool

DSN_BASE = "postgresql://sportsassets:sportsassets@localhost:5432/postgres"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ms, "_map_cache", {})
    monkeypatch.setattr(ms, "_slug_404_until", {})
    for d in (ml._terminal_until, ml._event_stale_memo, ml._unmapped_until, ml._unmapped_memo,
              ml._no_mark_until, ml._no_mark_memo):
        d.clear()
    ml._WOKEN.discard(CID)
    yield
    ml._terminal_until.clear()
    ml._event_stale_memo.clear()
    ml._WOKEN.discard(CID)


# --------------------------------------------------- HIGH-1 (folded 2026-09-08)

def test_review_h1_the_event_stale_memo_survives_his_next_fill_and_a_wake(monkeypatch):
    """DEFECT AS IT STOOD (HIGH; the lane bar: 'a memo that survives his
    next fill'): `_memo_event_stale` wrote `_terminal_until` -- D1's
    dict, released by NOTHING but its TTL (`_release_cand_memo` read the
    unmapped and no_mark dicts only; the walk at
    `_terminal_until.get(...) > t.now` and the fast tick's twin skipped
    a woken market too). D1's memo is the VENUE's terminal word, which
    his fill cannot undo; the event-stale memo is the ABSENCE of his
    fills, which his next fill undoes at once. As it stood his next fill
    on the market -- the stamp and the rows both newer than the memo, or
    the wake his fill raises -- was skipped `cand_terminal_skipped` for
    the rest of UNMAPPED_TTL_S (900 s), and the memo persisted across a
    restart (E6 part 3). THE RULE, FOLDED 2026-09-08 (pinned below): a
    stamp newer than the memo's `at`, or a wake, drops the memo (the
    sibling `_event_stale_memo` holds the `at`, released beside the two
    others, `cand_memo_released`) and the market is read that tick;
    D1's own entries (no sibling) and a reloaded entry after a restart
    (the sibling is not persisted) stay TTL-bound."""
    maps = _no_map(monkeypatch)
    p = _cand(T - 2 * DAY - 10)
    st = _tick(p, _Venue(), now=T)
    assert _census(st, "event_stale") == 1 and ml._terminal_until == {KEY: T + ms.UNMAPPED_TTL_S}
    assert ml._event_stale_memo == {KEY: T}, "the read's `at` beside the memo"
    # his next fill: the walk's stamp AND the rows are newer than the memo -> released, read
    p.stamps[CID] = _dt(T + 30)
    p.fills.append(_gea(T + 30))
    v2 = _Venue()
    st2 = _tick(p, v2, now=T + 60)
    assert _census(st2, "cand_terminal_skipped") == 0, "FOLDED: his next fill releases the memo"
    assert _census(st2, "cand_memo_released") == 1 and _census(st2, "event_stale") == 0
    assert _bbos(v2)[:1] == [SLUG] and maps == [CID], "the market he just traded is read this tick"
    assert KEY not in ml._terminal_until and KEY not in ml._event_stale_memo, "both dropped"
    # the wake his fill raises, on a fresh memo with no newer stamp: released, read
    ml._terminal_until.clear()
    ml._event_stale_memo.clear()
    maps.clear()
    p2 = _cand(T - 2 * DAY - 10)
    st = _tick(p2, _Venue(), now=T)
    assert _census(st, "event_stale") == 1 and ml._event_stale_memo == {KEY: T}
    ml._WOKEN.add(CID)
    v3 = _Venue()
    st3 = _tick(p2, v3, now=T + 90)
    assert st3["woken"] == [CID] and _census(st3, "cand_terminal_skipped") == 0
    assert _census(st3, "cand_memo_released") == 1 and maps == [CID] and _bbos(v3)[:1] == [SLUG]
    assert KEY not in ml._terminal_until and KEY not in ml._event_stale_memo
    # D1's own entry (no sibling): his newer stamp AND a wake release nothing -- the TTL alone
    ml._WOKEN.discard(CID)
    maps.clear()
    p3 = _cand(T - 2 * DAY - 10, stamp=T + 30)
    ml._terminal_until[KEY] = T + ms.UNMAPPED_TTL_S
    ml._WOKEN.add(CID)
    v4 = _Venue()
    st4 = _tick(p3, v4, now=T + 60)
    assert _census(st4, "cand_terminal_skipped") == 1 and _census(st4, "cand_memo_released") == 0
    assert _bbos(v4) == [] and maps == [] and ml._terminal_until == {KEY: T + ms.UNMAPPED_TTL_S}
    # after a restart the sibling is gone (not persisted): the reloaded entry is TTL-bound too
    ml._WOKEN.discard(CID)
    ml._terminal_until.clear()
    p5 = _cand(T - 2 * DAY - 10)
    _tick(p5, _Venue(), now=T)
    assert ml._event_stale_memo == {KEY: T}
    ml._event_stale_memo.clear()                     # the snapshot reloads _terminal_until alone
    p5.stamps[CID] = _dt(T + 30)
    v5 = _Venue()
    st5 = _tick(p5, v5, now=T + 60)
    assert _census(st5, "cand_terminal_skipped") == 1 and _census(st5, "cand_memo_released") == 0 and _bbos(v5) == []
    assert "_terminal_until.items()" in inspect.getsource(ml._terminal_memo_snapshot)
    assert "_event_stale_memo" not in inspect.getsource(ml._terminal_memo_snapshot)
    # the writer, verbatim: D1's dict and the sibling; the release reads three pairs
    src = inspect.getsource(ml._memo_event_stale)
    assert "_terminal_until[(whale, cid)] = t.now + ms.UNMAPPED_TTL_S" in src
    assert "_event_stale_memo[(whale, cid)] = float(t.now)" in src
    rel = inspect.getsource(ml._release_cand_memo)
    assert "(_unmapped_until, _unmapped_memo), (_no_mark_until, _no_mark_memo)" in rel
    assert "(_terminal_until, _event_stale_memo)" in rel and "(_terminal_until, _event_stale_memo)" in inspect.getsource(ml._cand_memo_skips)


# ------------------------------------------------- MEDIUM-1 (folded 2026-09-08)

def test_review_m1_the_walk_stamp_wins_over_a_newer_row_the_read_holds():
    """DEFECT AS IT STOOD (MEDIUM, latent: unreachable at the shipped
    constants -- see m3 -- and the CRITICAL shape 'a memo that skips a
    candidate with a fill of his inside 24 h' the moment the fill window
    is sized under the lookback, as the lane's docs propose).
    `_newest_fill_epoch` returned the walk's stamp whenever it held one
    and never looked at the rows: a fill of his ingested between the
    walk's `active_conditions` query and this candidate's `his_fills`
    read sits in the rows, newer than the stamp, and was not a witness.
    THE RULE, FOLDED 2026-09-08 (pinned below): the newest of the stamp
    and every readable row (an unreadable row still None), so the newer
    evidence wins whichever read carried it. The lane's own tests pinned
    only the stamp-newer direction; the fix-shaped mutant (rows beside
    the stamp) is now the rule."""
    t = SimpleNamespace(now=T, woken=set(), stamps={CID: T - 3 * DAY})
    rows = [_gea(T - 3 * DAY), _gea(T - 5)]          # his fill 5 s ago is in the rows
    assert ml._newest_fill_epoch(t, CID, rows) == T - 5, "FOLDED: the newest of the stamp and the rows"
    assert ml._event_stale(t, "rn1", CID, rows) is None, "FOLDED: a 5 s old fill is the witness, the read holds"
    # the stamp newer than every row still wins; an unreadable row beside a stamp is still None
    assert ml._newest_fill_epoch(SimpleNamespace(stamps={CID: T - 1}), CID, rows) == T - 1
    assert ml._newest_fill_epoch(t, CID, [_gea(T - 3 * DAY), _gea(None)]) is None
    assert ml._event_stale(t, "rn1", CID, [_gea(T - 3 * DAY), _gea(None)]) is None
    # the same through the tick: read, no memo
    p = _cand(T - 2 * DAY - 10)
    p.fills.append(_gea(T - 5))
    v = _Venue()
    st = _tick(p, v, now=T)
    assert _census(st, "event_stale") == 0 and _bbos(v)[:1] == [SLUG] and ml._terminal_until == {}


# ------------------------------------------------- MEDIUM-2 (folded 2026-09-08)

def test_review_m2_the_404_trail_counts_every_ask_across_both_tokens_not_the_slugs(monkeypatch):
    """DEFECT AS IT STOOD (MEDIUM, a name that misstated). The trail was
    the count of '404' notes over the lane's WHOLE diag for EVERY token
    of the market: each candidate slug asked (the two aec orders, the
    atc slug, the aec-<his slug>, his own slug twice), then the same
    list again for his other token (in production the memo
    `_slug_404_until` answers the second pass without a venue call and
    still notes '404'). On atp-gea-zandsch's shape the census printed
    `no_key_intersection:exact:404:12` for 5 distinct slugs asked -- not
    the `:exact:404:2` ('the two slug orders were asked') docs §35 and
    the test module's header stated. THE RULE, FOLDED 2026-09-08 (pinned
    below): the count of DISTINCT candidate slugs the venue answered 404
    (collected in `_exact_for_token` as the one-slug asks are answered,
    the memo's answer counted as the venue's), so `n` reads as the docs
    now say it does: `:exact:404:5` on this shape."""
    _nosleep(monkeypatch)
    v = _V()
    fills = [dict(f, market_slug=GEA, event_slug=GEA, market_title=GEA_TITLE, outcome="Arthur Gea") for f in HIS]
    p = _ShadowPool(fills=fills, mapped=False)
    mo: dict = {}
    assert _run(ms.map_market(p, fills, v, whale="rn1", condition_id=CID, budget=ms.MapBudget(cap=100), out=mo)) is None
    asked = [c[1] for c in v.calls if c[0] == "exact"]
    distinct = set(asked)
    assert {"aec-atp-artgea-botzan-2026-09-07", "aec-atp-botzan-artgea-2026-09-07"} <= distinct
    assert mo["exact_404"] == len(distinct) == 5, "FOLDED: one per distinct slug the venue answered 404"
    assert mo["exact_404"] < len(asked) == 12, "never one per ask"
    assert ms._map_cache[("rn1", CID)]["slugs_404"] == sorted(distinct)
    assert asked.count("aec-atp-artgea-botzan-2026-09-07") == 2, "each order asked once per token, counted once"
    assert asked.count("atp-gea-zandsch-2026-09-07") == 4, "his own slug asked twice per token, counted once"
    assert len(fills) >= 2 and len({f["asset"] for f in fills}) == 2, "two tokens on the market"
    # the memo's remembered 404 is the venue's answer and counts the slug once too
    slugs: set = set()
    monkeypatch.setattr(ms, "_slug_404_until", {"aec-atp-artgea-botzan-2026-09-07": ms.time.time() + 600})
    import sportsassets.pmus as real_pmus
    f0 = fills[0]

    async def _read(fn, *args):
        return fn(*args)
    calls = []

    def _exact(cands, outcome, diag_out=None):
        calls.append(list(cands))
        if diag_out is not None:
            diag_out.append("404")
        return None
    monkeypatch.setattr(real_pmus, "resolve_market_exact", _exact)
    monkeypatch.setattr(real_pmus, "resolve_derivative_exact", lambda *a, **k: None)
    monkeypatch.setattr(real_pmus, "resolve_team_yesno_exact", lambda *a, **k: None)
    verdict, why = _run(ms._exact_for_token(p, real_pmus, f0, _read, ms.time.time(), None, slugs_404=slugs))
    assert verdict is None and why is None
    assert ["aec-atp-artgea-botzan-2026-09-07"] not in calls, "the memoised slug was not re-asked"
    assert "aec-atp-artgea-botzan-2026-09-07" in slugs and len(slugs) == 5


# ------------------------------------------------- MEDIUM-3 (as it stands)

def test_review_m3_at_the_shipped_constants_no_walked_candidate_can_be_event_stale(monkeypatch):
    """PINNED AS IT STANDS (MEDIUM: item 4 closes $0 of cand_unread_capped
    today). The walk lists a whale's candidates from
    `ms.active_conditions(stamped=True)` -- markets with a fill of his
    inside LOOKBACK_H (6 h; capped_env, env may only LOWER it), the stamp
    being that very max(ts) -- so a walked candidate's newest fill is
    always inside 6 h < EVENT_STALE_FILL_S (24 h) and the second witness
    never holds on the live walk; the fast tick reads the same query and
    a woken market returns None before it. The lane's docs §35 say so
    ('inert until the owner sizes the fill window'). The name is on the
    census and can only be emitted by a stamp the walk never produces."""
    assert float(ml.EVENT_STALE_FILL_S) > float(ms.LOOKBACK_H) * 3600.0 and float(ms.LOOKBACK_H) <= 6.0
    src = inspect.getsource(ms.active_conditions)
    assert "hours: float = LOOKBACK_H" in src and "t.ts >= now() - ($2::float8 * interval '1 hour')" in src
    assert 'LOOKBACK_H = rules.capped_env("MIRROR_LOOKBACK_H", 6.0, floor=0.25)' in inspect.getsource(ms)
    assert inspect.getsource(ml).count("ms.active_conditions(t.pool, w, stamped=True)") == 2, "both walks: the same one query"
    maps = _no_map(monkeypatch)
    # the newest stamp the walk can hand a listed candidate: inside the lookback
    p = _cand(T - 3 * DAY, stamp=T - float(ms.LOOKBACK_H) * 3600.0 + 1.0)
    v = _Venue()
    st = _tick(p, v, now=T)
    assert _census(st, "event_stale") == 0 and maps == [CID] and _bbos(v)[:1] == [SLUG] and ml._terminal_until == {}


# ------------------------------------------------------ LOW-1 (folded 2026-09-08)

def test_review_l1_the_refusal_row_carries_no_event_date_and_no_fill_age(monkeypatch):
    """PINNED (LOW: docs §35 and the _tick_candidate comment said 'a
    refusal row with event_date and fill_age_s'; `d.update(stale)` hands
    them to `_note_candidate_refusal`, which reads its fixed key set and
    drops both -- the row has no column for them). The row is written
    with the name alone: us_slug NULL, mark NULL, his_net NULL. FOLDED
    2026-09-08 as the docs: §35 and the comment now say the row carries
    the name alone (no column added -- a migration for a census detail
    is wider than the finding); the row's shape below is the rule."""
    _no_map(monkeypatch)
    p = _cand(T - 2 * DAY - 10)
    _tick(p, _Venue(), now=T)
    assert [r["refusal"] for r in p.cand_refusals] == ["event_stale"]
    row = p.cand_refusals[0]
    assert not ({"event_date", "fill_age_s"} & set(row)), "AS IT STANDS: dropped by the row writer"
    assert row.get("us_slug") is None and row.get("mark") is None and row.get("his_net") is None
    src = inspect.getsource(ml._note_candidate_refusal)
    assert "event_date" not in src and "fill_age_s" not in src


# ------------------------------------------ what stands: fail-closed pins

def test_review_the_verdict_never_trades_and_writes_nothing_but_the_memo(monkeypatch):
    """The event-stale exit is judged before any venue call, opens no
    book, places nothing, and writes only D1's memo -- the E7 memos and
    the game-full memo untouched; a second whale-tick on the memo spends
    no slot (`cand_terminal_skipped`); the E12 first-sight window is the
    fixed FIRST_SIGHT_S before an open (never the memo's clock), so a
    market memoised here and opened later sizes on the same flow rule
    as any other."""
    maps = _no_map(monkeypatch)
    p = _cand(T - 2 * DAY - 10)
    v = _Venue()
    st = _tick(p, v, now=T)
    assert _census(st, "event_stale") == 1 and maps == [] and _bbos(v) == [] and not p.books
    assert [c[0] for c in v.calls if c[0] in ("create", "cancel", "bbo")] == []
    assert ml._unmapped_until == {} and ml._no_mark_until == {} and list(ml._terminal_until) == [KEY]
    # (on the tip lane 3+4's witnessed flip hands `since` in and the fixed
    # window is `window`; the flip clock only ever WIDENS first sight)
    src = inspect.getsource(ml._open_flow)
    assert "window = t.now - FIRST_SIGHT_S" in src
    assert "since = window if flip is None or flip >= window else flip" in src
    # judged after his fills are read and before the map read: the source order
    cand = inspect.getsource(ml._tick_candidate)
    assert cand.index("await ms.his_fills(") < cand.index("_event_stale(t, w, cid, fills)") < cand.index("ms.map_market(")


def test_review_the_undated_and_the_unreadable_read_and_the_env_holds_no_knob(monkeypatch):
    """Fail closed toward the read: a slug with no date, a date the
    calendar refuses, an event day exactly one day past, a stamp the
    walk could not read beside a row with no instant -- every one READS.
    No env name reaches the two windows (a knob could only make the memo
    fire on a live market)."""
    maps = _no_map(monkeypatch)
    for slug, stamp in (("atp-gea-zandsch", ...), ("atp-gea-zandsch-2026-02-30", ...),
                        ("atp-gea-zandsch-2026-09-08", ...), (GEA, None)):
        maps.clear()
        ml._terminal_until.clear()
        p = _cand(T - 2 * DAY - 10, stamp=stamp, slug=slug)
        if stamp is None:
            p.fills.append(_gea(None, slug))      # a row with no readable instant
        v = _Venue()
        st = _tick(p, v, now=T)
        assert _census(st, "event_stale") == 0 and maps == [CID] and _bbos(v)[:1] == [SLUG], slug
    src = inspect.getsource(ml)
    for name in ("EVENT_STALE_DAY_S", "EVENT_STALE_FILL_S"):
        assert f"{name} = 86400.0" in src and f'env("MIRROR_{name}' not in src and f'"{name}"' not in src


def test_review_the_memo_persists_and_reloads_as_d1s_does():
    """E6 part 3: the event-stale memo rides `_terminal_until`, so the
    persisted snapshot carries it and a boot read restores it -- the
    same writer and reader as D1's (pinned so the fold that gives it
    its own release keeps the persistence)."""
    src = inspect.getsource(ml)
    assert "_terminal_until.items()" in src and "_terminal_until[(e[0], e[1])] = float(e[2])" in src
    assert src.count("_terminal_until[(whale, cid)] = ") == 1, "one writer for both D1's and the event-stale memo"


# ------------------------------------- item 1 on the real schema (real pg)

def _scratch_db():
    """A scratch database on the local Postgres with us_premap in the
    sweep's own shape (premap.py's CREATE TABLE, the aec sides distinct
    by side_norm, no primary key on identifier) -- or a skip."""
    asyncpg = pytest.importorskip("asyncpg")
    import asyncio
    import uuid
    name = "l7r_" + uuid.uuid4().hex[:10]

    async def _mk():
        try:
            admin = await asyncpg.connect(DSN_BASE, timeout=4)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no local postgres: {type(exc).__name__}")
        await admin.execute(f'CREATE DATABASE "{name}"')
        await admin.close()
    asyncio.run(_mk())
    return name


DDL = """
CREATE TABLE us_premap (identifier text, event_slug text, event_title text, market_slug text, question text,
    kind text, line text, side_norm text, event_keys text[], intent text, signed text,
    updated_at timestamptz NOT NULL DEFAULT now(), team_abbr text, team_name text, team_safe_name text,
    team_id bigint, team_league text, game_start timestamptz, sports_type text);
INSERT INTO us_premap (identifier, event_title, question, side_norm) VALUES
 ('aec-atp-artgea-botzan-2026-09-07', 'US Open ATP: Arthur Gea vs Botic van de Zandschulp', 'Will Arthur Gea win?', 'artgea'),
 ('aec-atp-artgea-botzan-2026-09-07', 'US Open ATP: Arthur Gea vs Botic van de Zandschulp', 'Will Botic van de Zandschulp win?', 'botzan'),
 ('aec-atp-jansin-danmed-2026-09-07', 'US Open ATP: Jannik Sinner vs Daniil Medvedev', 'Will Jannik Sinner win?', 'jansin'),
 ('aec-wta-frajon-raluse-2026-09-08', 'Antalya WTA: Francesca Jones vs Raluca Serban', 'Will Francesca Jones win?', 'frajon'),
 ('atc-atp-gea-zandsch-2026-09-07-gea', 'US Open ATP: Arthur Gea vs Botic van de Zandschulp', 'spread', 'gea');
"""


def _assemble(arg: str) -> str:
    lines = _preset_lines()
    head = lines[0].split(") ", 1)[1]
    script = "set -u\nARG=\"$1\"\n" + "\n".join([head] + [ln.strip() for ln in lines[1:]]).replace("; TO=30000 ;;", "")
    script += "\nprintf '%s' \"$SQL\"\n"
    r = subprocess.run(["bash", "-c", script, "_", arg], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_review_the_tennis_witness_sql_runs_on_the_real_schema_and_reads_the_aec_rows_by_the_venues_words():
    """Item 1 on a real Postgres: the bash-assembled statements run on
    us_premap's own columns (identifier, event_title, question,
    side_norm, updated_at; the 055 columns beside them), the date
    pattern matches the aec identifier's own tail
    ('aec-atp-artgea-botzan-2026-09-07' -- premap's f"aec-{code}-{a}-{b}-{date}"),
    a surname read is the venue's event_title alone (the atc segment row
    on the same date is never an aec row), the two sides of one match
    count n=2 / matches=1, a date the table has no rows for prints 0
    rows in both statements, and psql's own exit is 0 on every arg."""
    shutil.which("bash") or pytest.skip("no bash")
    asyncpg = pytest.importorskip("asyncpg")
    import asyncio
    name = _scratch_db()
    dsn = DSN_BASE.rsplit("/", 1)[0] + "/" + name

    async def _go():
        conn = await asyncpg.connect(dsn, timeout=4)
        try:
            await conn.execute(DDL)
            out = {}
            for arg in ("tennis-witness=2026-09-07:gea,zandschulp", "tennis-witness=2026-09-08:jones,serban,carle,feistel,wu",
                        "tennis-witness=2026-09-07", "tennis-witness=2026-09-09:gea"):
                stmts = [s.strip() for s in _assemble(arg).split(";") if s.strip()]
                assert len(stmts) == 2, stmts
                out[arg] = [[dict(r) for r in await conn.fetch(s)] for s in stmts]
            return out
        finally:
            await conn.close()
            admin = await asyncpg.connect(DSN_BASE, timeout=4)
            await admin.execute(f'DROP DATABASE "{name}"')
            await admin.close()
    out = asyncio.run(_go())
    rows, tours = out["tennis-witness=2026-09-07:gea,zandschulp"]
    assert [r["identifier"] for r in rows] == ["aec-atp-artgea-botzan-2026-09-07"] * 2
    assert sorted(r["side_norm"] for r in rows) == ["artgea", "botzan"]
    assert [(t["tour"], t["n"], t["matches"]) for t in tours] == [("atp", 3, 2)]
    rows, tours = out["tennis-witness=2026-09-08:jones,serban,carle,feistel,wu"]
    assert [r["identifier"] for r in rows] == ["aec-wta-frajon-raluse-2026-09-08"]
    assert [(t["tour"], t["n"], t["matches"]) for t in tours] == [("wta", 1, 1)]
    rows, tours = out["tennis-witness=2026-09-07"]
    assert len(rows) == 3 and all(r["identifier"].startswith("aec-atp-") for r in rows), "the bare date: every aec row that day, never the atc row"
    rows, tours = out["tennis-witness=2026-09-09:gea"]
    assert rows == [] and tours == []
    # psql itself, when it is on the box (the runner's own client)
    if shutil.which("psql"):
        r = subprocess.run(["psql", DSN_BASE, "-X", "-v", "ON_ERROR_STOP=1", "-c", "SELECT 1"], capture_output=True, text=True)
        assert r.returncode == 0


def test_review_the_preset_sits_in_case_order_and_the_help_line_stays_the_labels():
    """The MEDIUM bar 'a help line out of case order': the two pattern
    presets ride the help line's tail in the order their case arms
    appear (book=* before tennis-witness=*), both after verify-day and
    before premap-rows / hourly; the label list itself is untouched
    (test_render_ops_hourly pins names == labels)."""
    from tests.test_c8_tennis_witness import YML
    text = YML.read_text()
    case = text[text.index('case "$ARG" in'):text.index('*) echo "sql: arg must be one of')]
    assert case.index("book=*)") < case.index("verify-day)") < case.index("tennis-witness=*)") < case.index("premap-rows)") < case.index("hourly)")
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    assert line.rstrip().endswith("patterns: book=<id>, tennis-witness=<YYYY-MM-DD>[:<surname>,<surname>...]\"; exit 1 ;;")
    assert line.count("tennis-witness") == 1 and line.count("book=<id>") == 1
