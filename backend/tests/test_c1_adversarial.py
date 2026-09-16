"""The C1 adversarial review's tests (round 2), folded into the patch. Three
assertions that documented round-1 FINDINGS now assert the fixed behaviour
(review C: no cache shell for a capped market; review E: a refused token
refuses the market; the two xfails: a description naming both codes, and a
unique prefix hit with no outcome index, both refuse) and the end-to-end
grammar test asserts the certification refusal that now stands before
admission (grammar_echo_unverified: the venue lists no per-side contract).
"""
from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from sportsassets import live_executor as le
from sportsassets import map_lane, pmus
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap
from tests.test_mirror_live_worker import _Http
from tests.test_mirror_live_worker import _pool as _live_pool
from tests.test_mirror_maps_the_copy_lane import (AEC_CFB, ATC_AME, CFB, Q_AME, _aec_cfb, _ame_fills,
                                                  _cfb_fills, _Recorder, _Venue,
                                                  _with_venue, _yesno_market)
from tests.test_mirror_shadow import CID, HIS, M, SLUG, _fill, _nosleep, _Pool

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"


def _run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    slept = _nosleep(monkeypatch)
    monkeypatch.setattr(ms, "_map_cache", {})
    monkeypatch.setattr(ms, "_slug_404_until", {})
    monkeypatch.setattr(ms, "_unmapped_until", {})
    monkeypatch.setattr(ml, "_unmapped_until", {})
    monkeypatch.delenv("PREMAP_YN_IDENTITY", raising=False)
    yield slept


def _map(monkeypatch, fills, venue, budget=None, out=None, cid=CID):
    _with_venue(monkeypatch, venue)
    p = _Pool(fills=fills, mapped=False)
    return _run(ms.map_market(p, fills, venue, whale="rn1", condition_id=cid, budget=budget, out=out))


# ------------------------------------------------ (2) drift pin WITH a hit

class _HitRecorder(_Recorder):
    def __init__(self, hit_step, hit_slug=None):
        super().__init__()
        self.hit_step, self.hit_slug = hit_step, hit_slug

    def _m(self, slug):
        return {"market_slug": slug, "title": "t", "outcome": "x", "intent": LONG,
                "matched_by": "desk_exact", "score": 1.0}

    def exact(self, cands, outcome, diag_out=None):
        for c in cands:
            self.calls.append(("exact", c, outcome))
            if self.hit_step == "exact" and c == self.hit_slug:
                return self._m(c)
            if diag_out is not None:
                diag_out.append("404")
        return None

    def derivative(self, slug, outcome, title=None):
        self.calls.append(("derivative", slug, outcome))
        return self._m("tsc-" + slug) if self.hit_step == "derivative" else None

    def yesno(self, slug, outcome, title=None, event=None, diag_out=None):
        self.calls.append(("yesno", slug, outcome))
        return self._m("atc-" + slug) if self.hit_step == "yesno" else None


def _copy_hit(monkeypatch, ctx, rec):
    from tests.test_live_executor_mapping import _MapPool, _payload, _wire
    pool = _MapPool()
    _wire(monkeypatch, pool)
    monkeypatch.setenv("KALSHI_FIRST_PCT", "0")

    async def fake_ctx(_pool, _payload):
        return dict(ctx)

    async def _no_premap(*a, **k):
        return None

    monkeypatch.setattr(premap, "resolve", _no_premap)
    monkeypatch.setattr(le, "_market_context", fake_ctx)
    monkeypatch.setattr(pmus, "resolve_market_exact", rec.exact)
    monkeypatch.setattr(pmus, "resolve_derivative_exact", rec.derivative)
    monkeypatch.setattr(pmus, "resolve_team_yesno_exact", rec.yesno)
    monkeypatch.setattr(pmus, "resolve_market", rec.fuzzy)
    monkeypatch.setattr(pmus, "account_holds", lambda slug: False)
    monkeypatch.setattr(pmus, "submit_fok", lambda *a, **k: {"ok": True, "filled_shares": 1.0,
                                                              "fill_price": 0.5, "order_id": "o", "raw": {}})
    asyncio.run(le.maybe_execute(_payload(outcome=ctx["outcome"]), 5.0))
    return rec.calls


def _shared_hit(ctx, rec):
    class _V:
        resolve_market_exact = staticmethod(rec.exact)
        resolve_derivative_exact = staticmethod(rec.derivative)
        resolve_team_yesno_exact = staticmethod(rec.yesno)

    class _NoDh:
        async def fetchval(self, *a):
            return None

    async def _read(fn, *args):
        return fn(*args)

    diag: list = []
    mapping, source, refusal = asyncio.run(map_lane.exact_lane(_NoDh(), _V(), ctx, _read, diag=diag, grammar=True))
    return rec.calls, mapping, source, refusal


_T = "2026-09-06"
_CTX = {
    "tennis": {"market_slug": f"atp-nakashi-michels-{_T}", "event_slug": None,
               "market_title": "US Open ATP: Brandon Nakashima vs Alex Michelsen", "event_title": None,
               "outcome": "Alex Michelsen"},
    "cfb": {"market_slug": f"cfb-bayl-aubrn-{_T}", "event_slug": f"cfb-bayl-aubrn-{_T}",
            "market_title": "Baylor vs. Auburn", "event_title": "Baylor vs. Auburn", "outcome": "Baylor"},
    "yesno": {"market_slug": f"lmx-ame-san-{_T}-ame", "event_slug": f"lmx-ame-san-{_T}",
              "market_title": f"Will CF América win on {_T}?", "event_title": "CF América vs. Club Santos Laguna",
              "outcome": "Yes"},
    "total": {"market_slug": f"epl-ars-che-{_T}-total-2pt5", "event_slug": None,
              "market_title": "Arsenal vs. Chelsea: O/U 2.5", "event_title": None, "outcome": "Over"},
    "spread": {"market_slug": f"epl-ars-che-{_T}-ars-1pt5", "event_slug": None,
               "market_title": "Arsenal spread", "event_title": None, "outcome": "Arsenal"},
}


@pytest.mark.parametrize("name,step,slug", [
    ("tennis", "exact", f"aec-atp-alemic-branak-{_T}"),   # second tennis candidate
    ("tennis", "exact", f"atp-nakashi-michels-{_T}"),     # his slug verbatim (from us cands)
    ("cfb", "exact", f"aec-cfb-bayl-aubrn-{_T}"),
    ("cfb", "yesno", None),
    ("yesno", "yesno", None),
    ("yesno", "exact", f"lmx-ame-san-{_T}-ame"),
    ("total", "derivative", None),
    ("spread", "derivative", None),
])
def test_both_lanes_stop_at_the_same_hit(monkeypatch, name, step, slug):
    ctx = _CTX[name]
    copy = _copy_hit(monkeypatch, ctx, _HitRecorder(step, slug))
    shared, mapping, source, refusal = _shared_hit(ctx, _HitRecorder(step, slug))
    assert ("fuzzy",) not in copy, copy          # the copy lane mapped: no fuzzy step
    assert copy == shared, (copy, shared)
    assert mapping is not None and refusal is None
    assert source == ("yesno" if step == "yesno" else "exact")


def test_the_mirror_never_runs_fuzzy_even_when_everything_refuses(monkeypatch):
    rec = _Recorder()
    shared, mapping, source, refusal = _shared_hit(_CTX["tennis"], rec)
    assert mapping is None and source is None and refusal is None
    assert all(c[0] != "fuzzy" for c in shared)


# --------------------------------------------------- (3) fail closed

def _sooners_venue():
    return _Venue(_aec_cfb())


def test_ambiguous_code_on_both_tokens_refuses_by_name(monkeypatch):
    out: dict = {}
    m = _map(monkeypatch, _cfb_fills("Bayl Aubrn", "Aubrn Bayl"), _Venue(_aec_cfb()), out=out)
    assert m is None and out["refusal"] == "side_code_ambiguous"


def test_a_refused_token_refuses_the_market_even_when_its_sibling_maps(monkeypatch):
    """Review E: token A ambiguous (both codes as whole words), token B would
    map -> the MARKET refuses by A's name."""
    out: dict = {}
    m = _map(monkeypatch, _cfb_fills("Bayl Aubrn", "Auburn"), _Venue(_aec_cfb()), out=out)
    assert m is None and out["refusal"] == "side_code_ambiguous"


def test_code_matching_neither_refuses(monkeypatch):
    out: dict = {}
    assert _map(monkeypatch, _cfb_fills("Sooners", "Aggies"), _sooners_venue(), out=out) is None
    assert out["refusal"] == "side_code_unmatched"


def test_a_code_that_names_both_venue_sides_should_refuse():
    """Requirement (3): a code matching BOTH sides refuses. Two identical
    side descriptions naming both codes -> expected refusal."""
    mk = _aec_cfb()[AEC_CFB]
    for s in mk["marketSides"]:
        s["description"] = "Bayl Bears vs Aubrn Tigers"
    hit, why = map_lane.aec_code_side(CFB, "Baylor", mk, 0)
    assert hit is None and why == "side_code_conflict", (hit, why)


def test_a_prefix_collision_without_outcome_index_picks_the_wrong_side():
    """Michigan vs Michigan State (codes mich/mist): 'Michigan State' uniquely
    hits 'mich' (mist is not a subsequence of 'michigan' and 'state' starts
    with s), so with no outcome_index the side is MICHIGAN'S. Wrong side."""
    mk = {"slug": "aec-cfb-mich-mist-2026-09-05", "closed": False, "question": "Wolverines vs. Spartans",
          "marketSides": [{"identifier": "aec-cfb-mich-mist-2026-09-05", "description": "Wolverines", "long": True},
                          {"identifier": "aec-cfb-mich-mist-2026-09-05", "description": "Spartans", "long": False}]}
    hit, why = map_lane.aec_code_side("cfb-mich-mist-2026-09-05", "Michigan State", mk, None)
    assert hit is None and why == "side_code_no_index", f"WRONG SIDE: Michigan State mapped onto {hit}"
    # with the index present the unique hit on the wrong team is a CONFLICT;
    # Michigan itself (index 0) maps
    assert map_lane.aec_code_side("cfb-mich-mist-2026-09-05", "Michigan State", mk, 1)[1] == "side_code_conflict"
    hit, why = map_lane.aec_code_side("cfb-mich-mist-2026-09-05", "Michigan", mk, 0)
    assert why is None and hit["outcome"] == "Wolverines" and hit["intent"] == LONG


def test_unexpected_marketsides_shapes_refuse_side_code_shape(monkeypatch):
    base = _aec_cfb()[AEC_CFB]
    three = json.loads(json.dumps(base))
    three["marketSides"].append({"identifier": AEC_CFB, "description": "Draw", "long": False})
    strings = json.loads(json.dumps(base))
    strings["marketSides"] = ["Bears", "Tigers"]
    noident = json.loads(json.dumps(base))
    noident["marketSides"][1].pop("identifier")
    missing = json.loads(json.dumps(base))
    missing.pop("marketSides")
    for shape in (three, strings, noident, missing):
        monkeypatch.setattr(ms, "_map_cache", {})
        monkeypatch.setattr(ms, "_slug_404_until", {})
        out: dict = {}
        m = _map(monkeypatch, _cfb_fills(), _Venue({AEC_CFB: shape}), out=out)
        assert m is None, shape
        assert out.get("refusal") in ("side_code_shape", None), out
    # aec_code_side itself on every shape
    for shape in (three, strings, noident, missing):
        assert map_lane.aec_code_side(CFB, "Baylor", shape, 0) == (None, "side_code_shape")
    assert map_lane.aec_code_side(CFB, "Baylor", None, 0) == (None, "side_code_shape")
    assert map_lane.aec_code_side(CFB, "Baylor", "notadict", 0) == (None, "side_code_shape")


def test_a_resolver_timeout_never_maps_and_names_itself(monkeypatch):
    monkeypatch.setattr(map_lane, "EXACT_BOX_S", 0.05)
    v = _Venue(_aec_cfb())

    import threading

    def slow(*a, **k):
        threading.Event().wait(0.3)          # time.sleep is patched by _nosleep
        return {"market_slug": AEC_CFB, "intent": LONG}

    v.resolve_market_exact = slow
    v.resolve_team_yesno_exact = slow
    out: dict = {}
    m = _map(monkeypatch, _cfb_fills(), v, out=out)
    assert m is None and "refusal" not in out and out["venue_reads"] >= 1
    threading.Event().wait(0.4)


def test_a_resolver_exception_never_maps(monkeypatch):
    v = _Venue(_aec_cfb())

    def boom(*a, **k):
        raise RuntimeError("venue down")

    v.resolve_market_exact = boom
    out: dict = {}
    m = _map(monkeypatch, _cfb_fills(), v, out=out)
    assert m is None and out["lane_error"] == "RuntimeError" and "refusal" not in out
    # the grammar retrieve raising is a named non-verdict too
    monkeypatch.setattr(ms, "_map_cache", {})
    v2 = _Venue(_aec_cfb())
    real = v2.slug_calls

    class _Boom:
        def retrieve_by_slug(self, slug):
            real.append(slug)
            raise RuntimeError("500")

    class _C:
        markets = _Boom()

    first = {"n": 0}
    orig = pmus.resolve_market_exact

    def exact_listed(cands, outcome, diag_out=None):
        # resolver reports the aec candidate LISTED and ambiguous
        if diag_out is not None:
            diag_out.append("amb:0.00/0.00" if cands[0].startswith("aec-") else "404")
        return None

    v2.resolve_market_exact = exact_listed
    v2._get_client = lambda: _C()
    out2: dict = {}
    _with_venue(monkeypatch, v2)
    p = _Pool(fills=_cfb_fills(), mapped=False)
    m2 = _run(ms.map_market(p, _cfb_fills(), v2, whale="rn1", condition_id=CID, out=out2))
    assert m2 is None and "refusal" not in out2 and AEC_CFB in real
    del orig, first


def test_a_mapping_without_an_intent_or_slug_is_no_verdict(monkeypatch):
    v = _Venue(_aec_cfb())
    v.resolve_market_exact = lambda cands, outcome, diag_out=None: {"market_slug": cands[0], "intent": None}
    v.resolve_team_yesno_exact = lambda *a, **k: {"market_slug": None, "intent": LONG}
    assert _map(monkeypatch, _cfb_fills(), v) is None


def test_a_404_on_the_grammar_retrieve_is_not_a_second_read(monkeypatch):
    """The gate: grammar runs only when the exact resolver said LISTED."""
    v = _Venue({})
    out: dict = {}
    assert _map(monkeypatch, _cfb_fills(), v, out=out) is None
    # atc, aec, cfb (us cands); the verbatim cfb read is answered by the 404
    # memo (no venue call, no budget unit); yes/no refuses pre-network
    assert v.slug_calls == ["atc-cfb-bayl-aubrn-2026-09-05-bayl", AEC_CFB, CFB]
    # both tokens are walked when nothing maps: bayl atc/aec/cfb + yesno, aubrn yesno (its
    # aec/cfb/verbatim reads are the memo) = 5 budget units, 3 venue lookups
    assert out["venue_reads"] == 5 and "refusal" not in out


# ------------------- (4) grammar opens no book without its certification

def test_grammar_end_to_end_through_the_real_mapper_never_reaches_admission(monkeypatch):
    """The venue lists the aec market but NO per-side contract for the chosen
    code: the class's own venue-truth check is unverified, the market is
    refused by that name before le._mapping_admitted is ever reached."""
    v = _Venue(_aec_cfb())
    _with_venue(monkeypatch, v)

    async def _never(*a, **k):
        raise AssertionError("_mapping_admitted must not run for a grammar source")

    monkeypatch.setattr(le, "_mapping_admitted", _never)
    ml._current_stats = ml._new_stats()
    pool = _live_pool(fills=_cfb_fills(), mapped=False)
    t = ml._Tick(pool=pool, pmus=v, http=_Http(), now=5000.0, stats=ml._current_stats)
    _run(ml._tick_candidate(t, "rn1", CID))
    c = ml._current_stats["census"]
    assert c["grammar_echo_unverified"] == 1 and c["map_venue_read"] >= 1
    assert c["map_source_unverified"] == 0
    assert not any("INSERT INTO mirror_books" in w[0] for w in pool.writes)
    assert ("rn1", CID) in ml._unmapped_until
    ml._current_stats = None


def test_yesno_end_to_end_reaches_admission_with_src_yesno(monkeypatch):
    v = _Venue(_yesno_market(ATC_AME, Q_AME, "lmx-ame-san-2026-09-05"))
    _with_venue(monkeypatch, v)
    seen = []

    async def _rec(pool, w, src, slug):
        seen.append((w, src, slug))
        return False, f"quarantined: mapping class unverified (src={src}, slug={slug})"

    monkeypatch.setattr(le, "_mapping_admitted", _rec)
    ml._current_stats = ml._new_stats()
    pool = _live_pool(fills=_ame_fills(), mapped=False, snap={"tok-yes": 500.0, "tok-no": 0.0},
                      snap_at=5000.0 - 40)
    t = ml._Tick(pool=pool, pmus=v, http=_Http(), now=5000.0, stats=ml._current_stats)
    _run(ml._tick_candidate(t, "rn1", CID))
    c = ml._current_stats["census"]
    assert c["map_source_unverified"] == 0, c
    assert seen == [("rn1", "yesno", ATC_AME)], (seen, {k: v for k, v in c.items() if v})
    assert not any("INSERT INTO mirror_books" in w[0] for w in pool.writes)
    ml._current_stats = None


def test_yesno_quarantine_rule_is_the_copy_lanes(monkeypatch):
    class _P:
        async def fetchval(self, sql, *a):
            if a and a[0] in ("premap_live", "mapping_quarantine"):
                return json.dumps(True)
            return None

    monkeypatch.delenv("LIVE_MAPPING_QUARANTINE", raising=False)
    monkeypatch.delenv("LIVE_PREMAP", raising=False)
    monkeypatch.setenv("LIVE_PREMAP_WHALES", "rn1")
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "")
    monkeypatch.setattr(le, "overrides_unreadable", lambda: False)
    monkeypatch.setattr(le, "_roster_override", None)
    ok, why = _run(le._mapping_admitted(_P(), "rn1", "yesno", SLUG))
    assert ok is False and why.startswith("quarantined:")
    ok2, why2 = _run(le._mapping_admitted(_P(), "rn1", "yesno_exact", SLUG))
    assert (ok, why.split("(src=")[0]) == (ok2, why2.split("(src=")[0])

    class _Off(_P):
        async def fetchval(self, sql, *a):
            if a and a[0] == "mapping_quarantine":
                return json.dumps(False)
            return await super().fetchval(sql, *a)

    assert _run(le._mapping_admitted(_Off(), "rn1", "yesno", SLUG)) == (True, None)


# ----------------------------------------------- (5) budget and cache

def test_eleven_candidates_and_the_capped_one_has_no_cached_verdict(monkeypatch):
    v = _Venue({})
    _with_venue(monkeypatch, v)
    v.resolve_derivative_exact = lambda slug, outcome, title=None: None
    budget = ms.MapBudget(cap=10)
    assert ms.MAP_READS_PER_TICK == 10

    def _fills(i):
        return [_fill(f"tok-{i}", "BUY", 100.0, 0.5, 1000 + i, market_title=f"T{i} spread",
                      event_title=None, event_slug=None, market_slug=f"lg-t{i}-o{i}-2026-09-05-t{i}-1pt5",
                      outcome=f"T{i}", outcome_index=0)]

    outs = []
    for i in range(11):
        out: dict = {}
        f = _fills(i)
        _run(ms.map_market(_Pool(fills=f, mapped=False), f, v, whale="rn1", condition_id=f"c{i}",
                           budget=budget, out=out))
        outs.append(out)
    assert budget.reads == 10 and outs[10].get("refusal") == "map_reads_capped"
    # review C: a market the budget cut gets NO entry at all
    assert ms._map_cache.get(("rn1", "c10")) is None
    assert ("rn1", "c0") in ms._map_cache


def test_the_404_memo_never_caches_a_positive_and_resumes_without_rereading(monkeypatch):
    atc_b = "atc-cfb-bayl-aubrn-2026-09-05-bayl"
    table = {atc_b: {"slug": atc_b, "closed": False, "title": "Baylor", "outcome": "Baylor", "marketSides": []}}
    v = _Venue(table)
    out: dict = {}
    m = _map(monkeypatch, _cfb_fills(), v, out=out)
    # bayl token: atc hit (exact). aubrn token: the copy lane's grammar makes NO
    # atc-aubrn candidate for 'Auburn'; aec 404, cfb 404, verbatim from memo, yesno refuse
    assert m and m["source"] == "exact" and m["us_slug"] == atc_b and m["long_asset"] == "tok-bayl"
    assert atc_b not in ms._slug_404_until
    assert set(ms._slug_404_until) == {AEC_CFB, CFB}
    n = len(v.slug_calls)
    # market cache dropped, 404 memo kept: the aubrn token's 404s are not re-read
    monkeypatch.setattr(ms, "_map_cache", {})
    out2: dict = {}
    m2 = _map(monkeypatch, _cfb_fills(), v, out=out2)
    assert m2 == m
    assert v.slug_calls[n:] == [atc_b], v.slug_calls[n:]


def test_two_ticks_one_read_per_market_per_side_family(monkeypatch):
    slug = "cfb-bayl-aub-2026-09-05"
    atc_b, atc_a = f"atc-{slug}-bayl", f"atc-{slug}-aub"
    table = {atc_b: {"slug": atc_b, "closed": False, "title": "Baylor", "outcome": "Baylor", "marketSides": []},
             atc_a: {"slug": atc_a, "closed": False, "title": "Auburn", "outcome": "Auburn", "marketSides": []}}
    fills = _cfb_fills()
    for f in fills:
        f["market_slug"] = f["event_slug"] = slug
    v = _Venue(table)
    out: dict = {}
    m = _map(monkeypatch, fills, v, out=out)
    assert m == {"us_slug": atc_b, "long_asset": "tok-bayl", "other_asset": "tok-aubrn", "source": "exact",
                 "per_side": True}
    assert v.slug_calls == [atc_b, atc_a] and out["venue_reads"] == 2
    out2: dict = {}
    assert _map(monkeypatch, fills, v, out=out2) == m
    assert v.slug_calls == [atc_b, atc_a] and out2["cache_hit"] == 2 and "venue_reads" not in out2


# ------------------------------------------------ (6) pace per venue call

def test_pace_calls_equal_resolver_calls_including_the_grammar_retrieve(monkeypatch, _fresh):
    v = _Venue(_aec_cfb())
    n = {"resolver": 0}
    for name in ("resolve_market_exact", "resolve_derivative_exact", "resolve_team_yesno_exact"):
        real = getattr(v, name)

        def wrapped(*a, _real=real, **k):
            n["resolver"] += 1
            return _real(*a, **k)

        setattr(v, name, wrapped)
    out: dict = {}
    m = _map(monkeypatch, _cfb_fills(), v, out=out)
    assert m and m["source"] == "grammar"
    paced = _fresh.count(ms.READ_PACING_S)
    # 4 resolver calls (atc, aec, cfb, yesno; the verbatim cfb is the 404 memo) + 1 grammar
    # retrieve, first token only
    assert n["resolver"] == 4 and paced == 5 == out["venue_reads"]
    assert v.slug_calls.count(AEC_CFB) == 2     # exact + grammar retrieve


# ---------------------------------------------------- (7) his_fills SQL

def test_his_fills_sql_parses_and_coalesces_from_market_tokens():
    import pglast
    src = inspect.getsource(ms.his_fills)
    sql = src.split('"""')[3]
    tree = pglast.parse_sql(sql)
    assert tree
    assert "COALESCE(t.outcome, mt.outcome) AS outcome" in sql
    assert "COALESCE(t.outcome_index, mt.outcome_index) AS outcome_index" in sql
    assert "LEFT JOIN market_tokens mt ON mt.token_id = t.asset" in sql


# --------------------------------------------- (9) mapped_by sums

class _MultiPool(_Pool):
    def __init__(self, by_cid, **kw):
        super().__init__(conds=list(by_cid), **kw)
        self.by_cid = by_cid

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        if "AS market_title, t.event_slug" in s:
            return list(self.by_cid[a[1]])
        if "FROM live_orders WHERE asset = ANY($1::text[])" in s:
            return ([{"asset": M, "us_market_slug": SLUG, "intent": LONG}] if M in a[0] else [])
        return await super().fetch(sql, *a)


def test_mapped_by_adds_up_to_the_mapped_rows(monkeypatch):
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    ms._ratio_cache.update(at=0.0, by_whale={})
    ms._backoff_until = 0.0
    table = {**_aec_cfb(), **_yesno_market(ATC_AME, Q_AME, "lmx-ame-san-2026-09-05")}
    v = _Venue(table)
    _with_venue(monkeypatch, v)
    by_cid = {"c-ledger": HIS, "c-yesno": _ame_fills(), "c-grammar": _cfb_fills(),
              "c-unmapped": _cfb_fills("Sooners", "Aggies")}
    p = _MultiPool(by_cid, fills=HIS, whales_ratio_fills=[])
    monkeypatch.setattr(ms, "MAP_READS_PER_TICK", 100)   # four markets need > 10 reads cold
    stats = _run(ms.tick_once(p, v, now_ts=5000.0))
    assert not stats.get("abandoned"), stats
    rows = [a for s, a in p.writes if "INSERT INTO mirror_shadow" in s]
    mapped_rows = [r for r in rows if r[2]]          # us_market_slug column
    assert stats["mapped_by"] == {"ledger": 1, "premap": 0, "exact": 0, "grammar": 1, "yesno": 1}
    assert sum(stats["mapped_by"].values()) == len(mapped_rows) == 3
    assert stats["unmapped"] == 1 and stats["map_reads_capped"] == 0
    ms._backoff_until = 0.0
