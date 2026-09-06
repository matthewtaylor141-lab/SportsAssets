"""The round-2 review's tests, folded into the patch (round 3). Three assertions
that documented round-2 findings now assert the fixed behaviour: a single-token
fill needs the token catalogue's corroboration (review 1), an unreadable echo or
state makes the book WAIT (review 4), a missing pending side is unverified (4);
the yes/no-shaped contract xfail (2) passes."""
from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from sportsassets import live_executor as le
from sportsassets import map_lane
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_c1_round2 import _cert_env, _live_tick
from tests.test_mirror_live_worker import _pool as _live_pool
from tests.test_mirror_maps_the_copy_lane import (AEC_CFB, ATC_BAYL, CFB, _aec_cfb, _atc_bayl, _cfb_fills,
                                                  _fresh, _Venue, _with_venue)
from tests.test_mirror_shadow import CID, _fill, _Pool

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
_ = _fresh


def _run(c):
    return asyncio.run(c)


def _mk(slug, d0, d1, title=None):
    return {"slug": slug, "closed": False, "question": title, "title": title,
            "marketSides": [{"identifier": slug, "description": d0, "long": True},
                            {"identifier": slug, "description": d1, "long": False}]}


# ------------------------------------------------------------- (A)

@pytest.mark.parametrize("a,b,d0,d1,pick,idx,want", [
    ("mich", "mist", "Wolverines", "Spartans", "Michigan", 0, "ok0"),
    ("mich", "mist", "Wolverines", "Spartans", "Michigan State", 1, "side_code_conflict"),
    ("mich", "mist", "Wolverines", "Spartans", "Michigan State", None, "side_code_no_index"),
    ("mich", "mist", "Wolverines", "Spartans", "Michigan", None, "side_code_no_index"),
    ("ohio", "ohst", "Bobcats", "Buckeyes", "Ohio State", 1, "side_code_conflict"),
    ("ohio", "ohst", "Bobcats", "Buckeyes", "Ohio", 0, "ok0"),
    ("tex", "txst", "Longhorns", "Bobcats", "Texas State", 1, "side_code_conflict"),
    ("tex", "txst", "Longhorns", "Bobcats", "Texas", 0, "ok0"),
    ("tex", "txst", "Longhorns", "Bobcats", "Texas", 1, "side_code_conflict"),
])
def test_prefix_collisions_refuse_or_map_correctly(a, b, d0, d1, pick, idx, want):
    slug = f"cfb-{a}-{b}-2026-09-05"
    hit, why = map_lane.aec_code_side(slug, pick, _mk("aec-" + slug, d0, d1), idx)
    if want == "ok0":
        assert why is None and hit["side_index"] == 0 and hit["outcome"] == d0
    else:
        assert hit is None and why == want


def test_code_hits_is_whole_word_or_first_word_only():
    assert not map_lane.code_hits("mich", "State Michigan X")          # not first, not whole
    assert map_lane.code_hits("state", "Michigan State")               # whole word anywhere
    assert not map_lane.code_hits("aub", "Texas Auburn")               # prefix of a later word never
    assert map_lane.code_hits("aub", "Auburn")
    assert not map_lane.code_hits("ab", "Auburn") and not map_lane.code_hits("aubrn", "Auburn")
    assert map_lane.code_hits("ame", "América") and map_lane.code_hits("america", "Amé")  # NFKD
    src = inspect.getsource(map_lane.code_hits)
    assert "_subsequence" not in src and "code in ol" not in src


def test_the_feed_index_order_assumption_is_the_only_thing_between_a_collision_and_a_wrong_side():
    """DOCUMENTED RISK: if the feed's outcome_index order ever differs from the
    slug's team order, a first-word collision on a SINGLE-token fill maps the
    wrong side and grammar_truth (side<->code) does not catch it; the pair
    rule catches it only when the sibling token is in the fills."""
    slug = "cfb-mich-mist-2026-09-05"
    mk = _mk("aec-" + slug, "Wolverines", "Spartans")
    hit, why = map_lane.aec_code_side(slug, "Michigan State", mk, 0)   # feed index swapped
    assert why is None and hit["outcome"] == "Wolverines", "Michigan State -> Michigan's side"
    con = {"slug": "atc-" + slug + "-mich", "outcome": "Wolverines"}
    assert map_lane.grammar_truth(con, mk, 0)[0] == "ok", "venue truth cannot see his pick"
    # the pair rule catches it when the sibling is present
    assert map_lane.pair_agrees(slug, 0, "Michigan", 1) == "side_code_conflict"


# ------------------------------------------------------------- (B)

def test_descriptions_naming_both_codes_refuse_with_real_names():
    mk = _mk(AEC_CFB, "Baylor Bears vs Auburn Tigers", "Baylor Bears vs Auburn Tigers")
    assert map_lane.aec_code_side(CFB, "Baylor", mk, 0) == (None, "side_code_conflict")
    mk2 = _mk("aec-cfb-ucla-hawaii-2026-09-05", "UCLA Hawaii", "Hawaii")
    assert map_lane.aec_code_side("cfb-ucla-hawaii-2026-09-05", "UCLA", mk2, 0) == (None, "side_code_conflict")


# ------------------------------------------------------------- (C)

def test_capped_market_has_no_cache_entry_and_partial_reads_are_kept(monkeypatch):
    v = _Venue({})
    _with_venue(monkeypatch, v)
    v.resolve_derivative_exact = lambda slug, outcome, title=None: None
    budget = ms.MapBudget(cap=1)
    f = [_fill("tok-a", "BUY", 100.0, 0.5, 1000, market_slug="lg-t-o-2026-09-05-t-1pt5", outcome="T",
               outcome_index=0, event_slug=None, event_title=None, market_title="T spread")]
    out: dict = {}
    _run(ms.map_market(_Pool(fills=f, mapped=False), f, v, whale="rn1", condition_id="c1", budget=budget, out=out))
    assert out.get("refusal") is None and ("rn1", "c1") in ms._map_cache
    out2: dict = {}
    f2 = [dict(f[0], asset="tok-b")]
    _run(ms.map_market(_Pool(fills=f2, mapped=False), f2, v, whale="rn1", condition_id="c2", budget=budget, out=out2))
    assert out2["refusal"] == "map_reads_capped" and ("rn1", "c2") not in ms._map_cache
    # pruning happens on every map
    ms._map_cache[("rn1", "old")] = {"until": 1.0, "tokens": {"x": None}, "refusals": {}}
    ms._slug_404_until["old-slug"] = 1.0
    _run(ms.map_market(_Pool(fills=f, mapped=False), f, v, whale="rn1", condition_id="c1", budget=ms.MapBudget(), out={}))
    assert ("rn1", "old") not in ms._map_cache and "old-slug" not in ms._slug_404_until


# ------------------------------------------------------------- (E)

def test_a_conflict_on_one_token_refuses_the_market_even_when_the_other_maps(monkeypatch):
    fills = _cfb_fills("Baylor", "Aubrn", index_a=0, index_b=1)
    fills[1]["outcome"] = "Baylor"          # the sibling claims the mapped code
    out: dict = {}
    _with_venue(monkeypatch, _Venue(_aec_cfb()))
    assert _run(ms.map_market(_Pool(fills=fills, mapped=False), fills, _Venue(_aec_cfb()), whale="rn1",
                              condition_id=CID, out=out)) is None
    assert out["refusal"] == "side_code_conflict"
    # sibling with no index: side_code_pair
    fills = _cfb_fills("Baylor", "Auburn", index_a=0, index_b=None)
    monkeypatch.setattr(ms, "_map_cache", {})
    out = {}
    assert _run(ms.map_market(_Pool(fills=fills, mapped=False), fills, _Venue(_aec_cfb()), whale="rn1",
                              condition_id=CID, out=out)) is None
    assert out["refusal"] == "side_code_pair"
    # a single-token fill (no sibling in his fills) needs the token
    # catalogue's corroboration (round 3, review 1): none here -> side_code_pair
    fills = _cfb_fills()[:1]
    monkeypatch.setattr(ms, "_map_cache", {})
    out = {}
    m = _run(ms.map_market(_Pool(fills=fills, mapped=False), fills, _Venue(_aec_cfb()), whale="rn1",
                           condition_id=CID, out=out))
    assert m is None and out["refusal"] == "side_code_pair"


# ------------------------------------------- (2)/(3) the live certification

def _capture_open(monkeypatch):
    opened = []

    async def _cap(p, w, cid, slug, la, oa, ratio, anchor, his_px, target, src, gk, intent="x"):
        opened.append((slug, la, src, intent))
        return {"ok": False, "refusal": "captured"}

    monkeypatch.setattr(le, "_open_mirror_book", _cap)
    return opened


def _candidate(monkeypatch, venue, pool=None):
    from sportsassets.analytics import mirror_live_rules as rules
    _cert_env(monkeypatch)
    _with_venue(monkeypatch, venue)
    pool = pool or _live_pool(fills=_cfb_fills(), mapped=False, snap={"tok-bayl": 1000.0, "tok-aubrn": 10.0},
                              snap_at=5000.0 - 40)
    pool.state.setdefault("mapping_quarantine", True)
    pool.state.setdefault("premap_live", True)
    monkeypatch.setattr(rules, "admission", lambda f, increase=False: None)
    opened = _capture_open(monkeypatch)
    t = _live_tick(monkeypatch, pool, venue)
    from tests.test_mirror_live_worker import _Http
    t.http = _Http(rows=[{"conditionId": CID, "asset": "tok-bayl", "size": 1000},
                         {"conditionId": CID, "asset": "tok-aubrn", "size": 10}])
    monkeypatch.setattr(ml, "_unmapped_until", {})
    _run(ml._tick_candidate(t, "rn1", CID))
    return t, pool, opened, ml._current_stats["census"]


def test_truth_mismatch_before_open_refuses_trips_and_blocks_the_next_open(monkeypatch):
    t, pool, opened, c = _candidate(monkeypatch, _Venue({**_aec_cfb(), **_atc_bayl("Tigers")}))
    assert opened == [] and c["side_echo_mismatch"] == 1 and c["grammar_echo_ok"] == 0
    assert pool.state["mirror_grammar_echo"]["tripped"] is True and ("rn1", CID) in ml._unmapped_until
    # a second candidate on a market whose truth is fine: still refused, tripped
    monkeypatch.setattr(ml, "_unmapped_until", {})
    monkeypatch.setattr(ms, "_map_cache", {})
    t2, pool2, opened2, c2 = _candidate(monkeypatch, _Venue({**_aec_cfb(), **_atc_bayl()}), pool=pool)
    assert opened2 == [] and c2["grammar_tripped"] == 1
    # quarantine OFF does not help a tripped class
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
    assert _run(ml._admit_source(t2, "rn1", "grammar", AEC_CFB)) == (False, "grammar_tripped")
    assert "grammar" not in le.QUARANTINE_RESUME_SRC
    ml._current_stats = None


def test_truth_unverified_never_opens_and_truth_ok_opens_on_the_certified_side(monkeypatch):
    t, pool, opened, c = _candidate(monkeypatch, _Venue(_aec_cfb()))
    assert opened == [] and c["grammar_echo_unverified"] == 1
    monkeypatch.setattr(ms, "_map_cache", {})
    t, pool, opened, c = _candidate(monkeypatch, _Venue({**_aec_cfb(), **_atc_bayl()}))
    assert opened == [(AEC_CFB, "tok-bayl", "grammar", LONG)] and c["grammar_echo_ok"] == 1
    ml._current_stats = None


def test_a_yes_no_shaped_per_side_contract_trips_the_class_on_shape_alone():
    """DOCUMENTED: the venue's soccer per-team contracts are yes/no shaped
    (question + Yes/No sides, no `outcome`); if cfb's are too, grammar_truth
    says MISMATCH -> the class trips on a shape difference, not a wrong side."""
    mk = _aec_cfb()[AEC_CFB]
    con = {"slug": ATC_BAYL, "question": "Will Baylor win against Auburn?",
           "marketSides": [{"identifier": ATC_BAYL, "description": "Yes", "long": True},
                           {"identifier": ATC_BAYL, "description": "No", "long": False}]}
    verdict, _ = map_lane.grammar_truth(con, mk, 0)
    assert verdict == "unverified", f"a shape difference reads as {verdict!r}: false trip"


def test_the_fill_echo_freezes_on_mismatch_and_trades_on_when_unreadable(monkeypatch):
    _cert_env(monkeypatch)
    pool = _live_pool(fills=_cfb_fills(), mapped=False)
    pool.state["mirror_grammar_echo"] = {"ok": 1, "mismatch": 0, "unverified": 0, "tripped": False,
                                         "verified": [], "pending": {AEC_CFB: {
                                             "outcome_desc": "Bears", "intent": LONG, "side_index": 0,
                                             "his_slug": CFB}}}
    book = dict(pool.add_book(ledger=99, map_source="grammar", us_market_slug=AEC_CFB,
                              long_asset="tok-bayl", other_asset="tok-aubrn"))
    t = _live_tick(monkeypatch, pool, _Venue(_aec_cfb()))
    # sign wrong: mismatch, frozen, tripped
    monkeypatch.setattr(ml, "_position_echo", lambda pmus, slug: {"net": -99.0, "outcome": "Bears"})
    assert _run(ml._grammar_fill_check(t, book)) == "frozen" and book["state"] == "frozen"
    assert pool.state["mirror_grammar_echo"]["tripped"] is True
    # round 3 (review 4): state unreadable / echo unreadable -> the book WAITS
    pool2 = _live_pool(fills=_cfb_fills(), mapped=False)
    book2 = dict(pool2.add_book(ledger=99, map_source="grammar", us_market_slug=AEC_CFB,
                                long_asset="tok-bayl", other_asset="tok-aubrn"))
    t2 = _live_tick(monkeypatch, pool2, _Venue(_aec_cfb()))
    pool2.raise_on.append(("SELECT value FROM ingestion_state", RuntimeError("db down")))
    assert _run(ml._grammar_fill_check(t2, book2)) == "wait", "unreadable certification state: waits"
    pool2.raise_on.clear()
    pool2.state["mirror_grammar_echo"] = {"tripped": False, "verified": [], "pending": {}}
    monkeypatch.setattr(ml, "_position_echo", lambda pmus, slug: None)
    assert _run(ml._grammar_fill_check(t2, book2)) == "wait", "echo unreadable: waits"
    assert book2["state"] == "live" and pool2.state["mirror_grammar_echo"].get("tripped") is False
    ml._current_stats = None


def test_pending_missing_at_fill_time_is_unverified_never_a_trip():
    """Round 3 (review 4): pending[slug] absent (popped by an earlier book
    on the same slug, or a trimmed list) -> expected None -> unverified."""
    assert map_lane.grammar_fill_echo({"net": 99.0, "outcome": "Bears"}, None, LONG, 99.0)[0] == "unverified"
    assert map_lane.grammar_fill_echo({"net": 99.0, "outcome": "Bears"}, "", LONG, 99.0)[0] == "unverified"


def test_the_grammar_contract_slug_is_his_code_and_the_venues_suffix_is_read_off_premap():
    """The venue's own per-side cfb slug in the probes is
    atc-cfb-hawaii-stan-2026-08-29-h (suffix 'h'); contract_slug builds the
    suffix from HIS code and is only the fallback when the sweep lists no
    row; the certification reads the venue's suffix off us_premap
    (mirror_live._contract_candidates, round 3 -- pinned in test_c1_round2)."""
    assert map_lane.contract_slug("cfb-hawaii-stan-2026-08-29", 0) == "atc-cfb-hawaii-stan-2026-08-29-hawaii"
    assert "_contract_candidates(" in inspect.getsource(ml._grammar_admission)


def test_integ_is_39_keys_and_the_served_prefix_is_head():
    st = ml._new_stats()
    assert len(ml._integ_block(st)) == 39 and list(st).index("integ") < 40
    for k in ("map_source_unverified", "map_reads_capped", "side_echo_mismatch"):
        assert k in ml._INTEG_CENSUS_KEYS
    assert json.dumps(ml._integ_block(st))
