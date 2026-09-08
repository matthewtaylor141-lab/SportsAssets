"""L7 (2026-09-08; PNL program lane 7, items 1, 3 and 4): coverage --
the tennis witness READ as a render-ops preset, the exact lane's 404
trail named in the census, and the event-stale memo on the candidate
walk. Owner ~11:50Z: "Fix all 3 of these immediately ... This needs to
be right."

Evidence (hard2/PNL_lane3_coverage.md §3, cand_refusals_1158 row 686):
`atp-gea-zandsch-2026-09-07 | 200546 | 20:33:01 | 01:58:15 | 32 |
unmapped@20:32 ...` -- $200,546 of his flow over 24 h on one tennis
market the mirror's exact lane asked as two slug orders, both 404, and
the census filed under premap's bare `no_key_intersection` with no
word that the venue was asked. Whether the venue LISTS the match is
not on disk: the `tennis-witness` preset is the read that settles it
(item 2, the title witness, is built only on rows it returns).

THE RULES, EXACTLY (pinned below):
- explain_unmapped prints `<premap step>:exact:404:<n>` only when
  premap's step carries NO split and the venue answered 404 on n > 0
  DISTINCT candidate slugs the exact lane asked (each slug once,
  however many of his tokens asked it -- the fold of the review's
  MEDIUM-2, 2026-09-08: on atp-gea-zandsch's shape five slugs, not the
  twelve asks); a split (the venue's own rows certified the absence)
  stands byte for byte; a refusal the lane named wins; 0 / unreadable
  prints premap's name alone. The count rides _map_exact's cache.
- a candidate whose slug's own date is MORE than one day past (now >=
  day + 2 days) AND whose newest fill of his (the newest of the walk's
  stamp and the rows read -- the fold of MEDIUM-1) is older than
  EVENT_STALE_FILL_S is `event_stale`: the D1 terminal memo, no map
  read, no quote read, a refusal row carrying the name alone (the
  fold of LOW-1: the row has no column for event_date / fill_age_s).
  Either witness missing -- undated, a fill inside the day, no readable
  fill instant, a wake -- READS. The memo is released at the walk by
  his next fill or a wake (the fold of HIGH-1, test_pnl_l7_review_pins).
- the preset: `tennis-witness=<YYYY-MM-DD>[:<surname>,...]`, read-only,
  the regex the only door to SQL.
"""
import inspect
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sportsassets.api import app as api_app
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap
from tests.test_e7_cand_memo import _bbos, _dt, _spool
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _Http, _NoThrottle, _Pool, _Venue, _armed, _census, _fill, _flat, _his, _kinds,
    _pool, _ratio_fills, _run, _tick,
)
from tests.test_mirror_maps_the_copy_lane import _Recorder
from tests.test_mirror_shadow import HIS, RATIO, _nosleep
from tests.test_mirror_shadow import _Pool as _ShadowPool

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
KEY = ("rn1", CID)
# his slug, verbatim (cand_refusals_1158 row 686); the tick's clock is
# set so that its date is more than one day past: 2026-09-09 13:00Z is
# past 2026-09-07 + 2 days
GEA = "atp-gea-zandsch-2026-09-07"
GEA_TITLE = "US Open ATP: Arthur Gea vs Botic van de Zandschulp"
T = datetime(2026, 9, 9, 13, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400.0


def _gea(ts, slug=GEA):
    return _fill(M, "BUY", 300.0, 0.31, ts, market_slug=slug, event_slug=slug, market_title=GEA_TITLE)


def _cand(ts, stamp=..., slug=GEA, snap_at=None):
    """A stamped pool holding one candidate on his verbatim slug: a fill
    at `ts`, the walk's stamp at `stamp` (default: the fill's instant;
    None: the plain fake, no stamp at all)."""
    stamps = {CID: _dt(ts)} if stamp is ... else ({CID: _dt(stamp)} if stamp is not None else {})
    return _spool(stamps=stamps, fills=[_gea(ts, slug)], snap={M: 300.0, N: 0.0},
                  snap_at=T - 40 if snap_at is None else snap_at)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ms, "_map_cache", {})
    monkeypatch.setattr(ms, "_slug_404_until", {})
    ml._terminal_until.clear()
    ml._unmapped_until.clear()
    ml._unmapped_memo.clear()
    ml._no_mark_until.clear()
    ml._no_mark_memo.clear()
    ml._event_stale_memo.clear()
    yield
    ml._terminal_until.clear()
    ml._event_stale_memo.clear()


class _V(_Recorder):
    """The venue module for the exact lane: the copy lane's three
    resolvers, every candidate 404 (the recorder's note), a client."""

    resolve_market_exact = _Recorder.exact
    resolve_derivative_exact = _Recorder.derivative
    resolve_team_yesno_exact = _Recorder.yesno

    def _get_client(self):
        class _C:
            class markets:
                @staticmethod
                def retrieve_by_slug(slug):
                    return None
        return _C()


# ------------------------------------------------ item 3: the 404 trail

def test_c8_the_404_trail_rides_on_premaps_bare_step_and_never_on_a_split_or_a_refusal(monkeypatch):
    seen = {}

    async def _explain(*a, **k):
        return dict(seen)
    monkeypatch.setattr(premap, "resolve_explain", _explain)
    ctx = {"title": GEA_TITLE, "event_title": None, "outcome": "Arthur Gea", "his_slug": GEA}
    seen.update(step="no_key_intersection")
    assert _run(ms.explain_unmapped(None, ctx, None, exact_404=2)) == "no_key_intersection:exact:404:2"
    assert _run(ms.explain_unmapped(None, ctx, None, exact_404=3)) == "no_key_intersection:exact:404:3"
    # no trail, or one that cannot be read: premap's name alone, never a guess
    for n in (0, None, -1, True, "2", float("nan"), "x"):
        got = _run(ms.explain_unmapped(None, ctx, None, exact_404=n))
        assert got == "no_key_intersection" or (n == "2" and got == "no_key_intersection:exact:404:2"), n
    assert _run(ms.explain_unmapped(None, ctx)) == "no_key_intersection", "the old signature"
    # a split premap named from the venue's own rows stands byte for byte
    seen.update(split="venue:league-unlisted")
    assert _run(ms.explain_unmapped(None, ctx, None, exact_404=4)) == "no_key_intersection:venue:league-unlisted"
    # a refusal the lane named wins over both
    assert _run(ms.explain_unmapped(None, ctx, "side_code_unmatched", exact_404=4)) == "side_code_unmatched"
    assert ms._exact_404_count(2) == 2 and ms._exact_404_count(2.9) == 2 and ms._exact_404_count(None) == 0
    assert ms._exact_404_count(True) == 0 and ms._exact_404_count(-3) == 0 and ms._exact_404_count("x") == 0


def test_c8_the_exact_lane_counts_its_404s_onto_the_map_out_and_keeps_them_on_a_cache_hit(monkeypatch):
    _nosleep(monkeypatch)
    v = _V()
    fills = [dict(f, market_slug=GEA, event_slug=GEA, market_title=GEA_TITLE, outcome="Arthur Gea") for f in HIS]
    p = _ShadowPool(fills=fills, mapped=False)
    mo: dict = {}
    budget = ms.MapBudget(cap=100)                  # the tick's budget wide enough for both tokens
    m = _run(ms.map_market(p, fills, v, whale="rn1", condition_id=CID, budget=budget, out=mo))
    asked = [c[1] for c in v.calls if c[0] == "exact"]
    assert m is None and asked and "refusal" not in mo, "the exact lane asked the venue and named no refusal"
    # the two slug orders the lane asks for tennis (PNL_lane3_coverage §3), verbatim
    assert "aec-atp-artgea-botzan-2026-09-07" in asked and "aec-atp-botzan-artgea-2026-09-07" in asked
    # the fold (2026-09-08, review MEDIUM-2): the DISTINCT slugs the venue
    # answered 404 -- five on this shape (the two aec orders, the atc
    # slug, aec-<his slug>, his own slug), twelve asks over two tokens
    # (his own slug asked twice per token)
    distinct = set(asked)
    assert len(distinct) == 5 and len(asked) == 12 and asked.count(GEA) == 4
    assert mo["exact_404"] == len(distinct) and mo["venue_reads"] >= len(asked)
    assert ms._map_cache[("rn1", CID)]["exact_404"] == len(distinct)
    assert ms._map_cache[("rn1", CID)]["slugs_404"] == sorted(distinct)
    # the same market inside MAP_CACHE_TTL_S: no venue call, the trail kept
    n_calls = len(v.calls)
    mo2: dict = {}
    assert _run(ms.map_market(p, fills, v, whale="rn1", condition_id=CID, budget=budget, out=mo2)) is None
    assert len(v.calls) == n_calls and mo2.get("cache_hit") and mo2["exact_404"] == len(distinct)
    assert "venue_reads" not in mo2
    # a budget that cuts the lane: `map_reads_capped` wins the explain, the trail so far still counted
    monkeypatch.setattr(ms, "_map_cache", {})
    mo3: dict = {}
    assert _run(ms.map_market(p, fills, v, whale="rn1", condition_id=CID, budget=ms.MapBudget(cap=3), out=mo3)) is None
    assert mo3["refusal"] == "map_reads_capped" and mo3["exact_404"] == 3


def test_c8_the_shadow_row_prints_the_trail_on_the_bare_step_and_carries_the_count(monkeypatch):
    _nosleep(monkeypatch)

    async def _explain(*a, **k):
        return {"step": "no_key_intersection", "detail": "x", "keys": 5, "rows": 0}
    monkeypatch.setattr(premap, "resolve_explain", _explain)
    v = _V()
    fills = [dict(f, market_slug=GEA, event_slug=GEA, market_title=GEA_TITLE, outcome="Arthur Gea") for f in HIS]
    p = _ShadowPool(fills=fills, mapped=False)
    row = _run(ms.shadow_market(p, v, "rn1", CID, RATIO, {}, positions={}, map_budget=ms.MapBudget(cap=100)))
    asked = {c[1] for c in v.calls if c[0] == "exact"}     # the distinct slugs (the fold of MEDIUM-2)
    assert row["reason"].startswith("unmapped") and len(asked) == 5
    assert row["detail"]["explain"] == f"no_key_intersection:exact:404:{len(asked)}"
    assert row["detail"]["exact_404"] == len(asked) and row["detail"]["his_slug"] == GEA
    # the venue module without the lane: no trail, premap's name, no count
    from tests.test_mirror_shadow import _Pmus
    p2 = _ShadowPool(fills=fills, mapped=False)
    row2 = _run(ms.shadow_market(p2, _Pmus(), "rn1", CID, RATIO, {}, positions={}))
    assert row2["detail"]["explain"] == "no_key_intersection" and "exact_404" not in row2["detail"]


# ------------------------------------------ item 4: the event-stale memo

def _no_map(monkeypatch):
    calls = []
    orig = ms.map_market

    async def _map(pool, fills, pmus=None, **kw):
        calls.append(kw.get("condition_id"))
        return await orig(pool, fills, pmus, **kw)
    monkeypatch.setattr(ms, "map_market", _map)
    return calls


def test_c8_event_stale_memo_on_a_two_day_old_candidate_with_no_fill_in_a_day(monkeypatch):
    maps = _no_map(monkeypatch)
    p = _cand(T - 2 * DAY - 10)                     # his last fill: the event day, 2 days + 10 s ago
    v = _Venue()
    st = _tick(p, v, now=T)
    assert _census(st, "event_stale") == 1 and _census(st, "unmapped") == 0 and _census(st, "no_mark") == 0
    assert _bbos(v) == [] and maps == [] and st["snap_market_reads"] == 0, "no quote read, no map read"
    assert _census(st, "map_venue_read") == 0 and not p.books
    assert ml._terminal_until == {KEY: T + ms.UNMAPPED_TTL_S}
    assert [r["refusal"] for r in p.cand_refusals] == ["event_stale"]
    # inside the memo's TTL: the D1 skip, no slot, no read, no new row
    st2 = _tick(p, v, now=T + 30)
    assert _census(st2, "cand_terminal_skipped") == 1 and _census(st2, "event_stale") == 0
    assert _bbos(v) == [] and maps == [] and len(p.cand_refusals) == 1
    # the TTL ran: judged again, still stale, memoised again, still no read
    st3 = _tick(p, v, now=T + ms.UNMAPPED_TTL_S)
    assert _census(st3, "event_stale") == 1 and _bbos(v) == [] and maps == []
    assert ml._terminal_until == {KEY: T + 2 * ms.UNMAPPED_TTL_S}


def test_c8_a_candidate_with_a_fill_inside_the_day_is_read(monkeypatch):
    maps = _no_map(monkeypatch)
    p = _cand(T - 3000)                             # the same two-day-old slug, his fill 50 min ago
    v = _Venue()
    st = _tick(p, v, now=T)
    assert _census(st, "event_stale") == 0 and maps == [CID] and _bbos(v)[:1] == [SLUG]
    assert ml._terminal_until == {} and p.books, "read, mapped, quoted, opened"
    # a fill exactly a day less a second old: inside the day, read
    ml._terminal_until.clear()
    p2 = _cand(T - DAY + 1)
    v2 = _Venue()
    st2 = _tick(p2, v2, now=T)
    assert _census(st2, "event_stale") == 0 and _bbos(v2)[:1] == [SLUG]


def test_c8_an_undated_candidate_is_read_whatever_his_fills_say(monkeypatch):
    maps = _no_map(monkeypatch)
    for slug in ("atp-gea-zandsch", "atp-gea-zandsch-2026-13-40", "", None):
        maps.clear()
        ml._terminal_until.clear()
        p = _cand(T - 2 * DAY - 10, slug=slug)
        v = _Venue()
        st = _tick(p, v, now=T)
        assert _census(st, "event_stale") == 0 and maps == [CID] and _bbos(v)[:1] == [SLUG], slug
        assert ml._terminal_until == {}
    assert ml._event_day_epoch(None) is None and ml._event_day_epoch("atp-x-y") is None
    assert ml._event_day_epoch("atp-gea-zandsch-2026-09-07") == datetime(2026, 9, 7, tzinfo=timezone.utc).timestamp()


def test_c8_one_day_past_is_not_more_than_one_day_and_the_second_day_is(monkeypatch):
    maps = _no_map(monkeypatch)
    slug = "atp-gea-zandsch-2026-09-08"           # one day past at T (09-09 13:00Z)
    p = _cand(T - 2 * DAY, slug=slug)
    v = _Venue()
    st = _tick(p, v, now=T)
    assert _census(st, "event_stale") == 0 and maps == [CID] and _bbos(v)[:1] == [SLUG]
    # the instant the second day starts: stale
    t2 = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    maps.clear()
    p2 = _cand(t2 - 2 * DAY, slug=slug)
    v2 = _Venue()
    st2 = _tick(p2, v2, now=t2)
    assert _census(st2, "event_stale") == 1 and maps == [] and _bbos(v2) == []
    st3 = _tick(_cand(t2 - 2 * DAY, slug=slug), _Venue(), now=t2 - 1)
    assert _census(st3, "event_stale") == 0, "a second before it: one day past, read"


def test_c8_no_stamp_reads_the_rows_an_unreadable_row_reads_the_market_and_a_wake_reads(monkeypatch):
    maps = _no_map(monkeypatch)
    # no walk stamp at all: the rows' newest ts is the witness
    p = _cand(T - 2 * DAY - 10, stamp=None)
    v = _Venue()
    st = _tick(p, v, now=T)
    assert _census(st, "event_stale") == 1 and maps == [] and _bbos(v) == []
    # a row with no readable instant among the rows, no stamp: read
    ml._terminal_until.clear()
    p2 = _cand(T - 2 * DAY - 10, stamp=None)
    p2.fills.append(_gea(None))
    v2 = _Venue()
    st2 = _tick(p2, v2, now=T)
    assert _census(st2, "event_stale") == 0 and maps == [CID]
    # the stamp says a day old, the rows are older: the stamp is the walk's evidence
    maps.clear()
    ml._terminal_until.clear()
    p3 = _cand(T - 2 * DAY - 10, stamp=T - 100)
    v3 = _Venue()
    st3 = _tick(p3, v3, now=T)
    assert _census(st3, "event_stale") == 0 and maps == [CID] and _bbos(v3)[:1] == [SLUG]
    # a wake this tick: read, whatever the date and the stamp say
    maps.clear()
    ml._terminal_until.clear()
    p4 = _cand(T - 2 * DAY - 10)
    ml._WOKEN.add(CID)
    v4 = _Venue()
    st4 = _tick(p4, v4, now=T)
    assert _census(st4, "event_stale") == 0 and maps == [CID] and st4["woken"] == [CID]
    # the pure verdict, by hand (a tick's clock, wake set and stamps)
    t = SimpleNamespace(now=T, woken=set(), stamps={CID: T - 3 * DAY})
    assert ml._event_stale(t, "rn1", CID, [_gea(T - 3 * DAY)]) == {"event_date": "2026-09-07", "fill_age_s": 3 * DAY}
    assert ml._event_stale(t, "rn1", CID, []) is None, "no rows: no slug, no witness"
    assert ml._event_stale(SimpleNamespace(now=T, woken={CID}, stamps={CID: T - 3 * DAY}), "rn1", CID,
                           [_gea(T - 3 * DAY)]) is None
    assert ml._newest_fill_epoch(SimpleNamespace(stamps={}), CID, [_gea(T - 5), _gea(T - 9)]) == T - 5
    assert ml._newest_fill_epoch(SimpleNamespace(stamps={}), CID, [_gea(T - 5), _gea(None)]) is None
    assert ml._newest_fill_epoch(SimpleNamespace(stamps={CID: T - 1}), CID, [_gea(T - 5)]) == T - 1


def test_c8_the_census_key_the_constants_and_the_one_writer():
    keys = ml.CENSUS_KEYS
    assert "event_stale" in keys and keys.count("event_stale") == 1 and len(set(keys)) == len(keys)
    assert keys.index("event_stale") + 1 == keys.index("venue_market_ended") < keys.index("registered_no_increase")
    assert keys.index("event_stale") >= api_app._DETAIL_MAX_KEYS and ml._new_stats()["census"]["event_stale"] == 0
    # every pinned tail stands (E12, E9, E7 / E6 / D1, E13)
    assert keys[-12] == "registered_no_increase"
    assert keys[-11:-8] == ("open_flow_only", "open_catchup", "flow_guard_unreadable")
    assert keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    assert keys[-4:] == ("cand_no_mark_skipped", "cand_memo_released", "book_quiet_skipped", "cand_terminal_skipped")
    assert ml.EVENT_STALE_DAY_S == 86400.0 and ml.EVENT_STALE_FILL_S == 86400.0
    src = inspect.getsource(ml)
    assert 'capped_env("MIRROR_EVENT_STALE' not in src and 'min_wait_env("MIRROR_EVENT_STALE' not in src
    assert "EVENT_STALE_DAY_S = 86400.0" in src and "EVENT_STALE_FILL_S = 86400.0" in src
    # the memo's one writer, and the verdict judged before any read
    assert src.count("_terminal_until[(whale, cid)] = ") == 1
    assert "_terminal_until[(whale, cid)] = " in inspect.getsource(ml._memo_event_stale)
    cand = inspect.getsource(ml._tick_candidate)
    assert cand.index("_event_stale(t, w, cid, fills)") < cand.index("ms.map_market(")
    assert cand.index("await ms.his_fills(") < cand.index("_event_stale(t, w, cid, fills)")
    assert "event_stale" not in ml._CAND_NOT_RECORDED, "a refusal row is written"
    assert "from .premap import date_of" in inspect.getsource(ml._event_day_epoch)


# ------------------------------------------- item 1: the preset, pinned

def _preset_lines():
    text = YML.read_text()
    i = text.index("                tennis-witness=*) ")
    j = text.index("                premap-rows) SQL=", i)
    return text[i:j].rstrip("\n").splitlines()


def test_c8_the_tennis_witness_preset_is_read_only_dated_and_its_regex_is_the_only_door_to_sql():
    lines = _preset_lines()
    body = "\n".join(lines)
    assert lines[0].lstrip().startswith("tennis-witness=*) if [[ ! \"$ARG\" =~ ^tennis-witness=([0-9]{4}-[0-9]{2}-[0-9]{2})(:([a-z]+(,[a-z]+)*))?$ ]]; then echo")
    assert "FROM us_premap WHERE identifier LIKE 'aec-%-$TD%' AND $TW ORDER BY identifier LIMIT 200;" in body
    assert "count(*) AS n, count(DISTINCT event_title) AS matches" in body and "GROUP BY 1 ORDER BY 1;\"; TO=30000 ;;" in body
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "need_confirm"):
        assert bad not in body, bad
    text = YML.read_text()
    help_line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    assert "patterns: book=<id>, tennis-witness=<YYYY-MM-DD>[:<surname>,<surname>...]" in help_line
    assert help_line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")[-1] == "hourly", "the hourly pin stands"


def test_c8_the_preset_assembles_its_sql_on_the_real_shell_and_refuses_every_other_arg():
    lines = _preset_lines()
    head = lines[0].split(") ", 1)[1]                  # after the case label
    script = "set -u\nARG=\"$1\"\n" + "\n".join([head] + [ln.strip() for ln in lines[1:]]).replace("; TO=30000 ;;", "")
    script += "\nprintf '%s' \"$SQL\"\n"

    def run(arg):
        r = subprocess.run(["bash", "-c", script, "_", arg], capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    rc, sql = run("tennis-witness=2026-09-07:gea,zandschulp")
    assert rc == 0
    assert sql.startswith("SELECT identifier, left(event_title, 70) AS event_title, left(question, 90) AS question, side_norm, "
                          "updated_at::timestamp(0) AS upd FROM us_premap WHERE identifier LIKE 'aec-%-2026-09-07%' AND "
                          "(lower(event_title) LIKE '%gea%' OR lower(event_title) LIKE '%zandschulp%') ORDER BY identifier LIMIT 200; ")
    assert sql.endswith("FROM us_premap WHERE identifier LIKE 'aec-%-2026-09-07%' GROUP BY 1 ORDER BY 1;")
    rc, sql = run("tennis-witness=2026-09-08")
    assert rc == 0 and "WHERE identifier LIKE 'aec-%-2026-09-08%' AND TRUE ORDER BY identifier LIMIT 200;" in sql
    rc, sql = run("tennis-witness=2026-09-08:jones,serban,carle,feistel,wu")
    assert rc == 0 and sql.count("lower(event_title) LIKE '%") == 5 and "'%wu%'" in sql
    for bad in ("tennis-witness=2026-09-08:x;drop", "tennis-witness=2026-09-08:a,,b", "tennis-witness=2026-9-8",
                "tennis-witness=", "tennis-witness=2026-09-08:Gea", "tennis-witness=2026-09-08:gea'", "book=3"):
        rc, out = run(bad)
        assert rc == 1 and out.startswith("tennis-witness: arg must be tennis-witness=<YYYY-MM-DD>"), bad
