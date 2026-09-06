"""C1 round 2 (owner order 2026-09-06 18:25Z: "college football and la
liga should be very easy to map"): the grammar class goes LIVE under its
own certification, La Liga is pinned on both paths with the exact reason
each refuses the feed's own row, and the read budget's knob is proven
downward-only. Driven with fakes: no venue, no database."""
from __future__ import annotations

import asyncio
import inspect

from sportsassets import live_executor as le
from sportsassets import pmus
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap
from tests.test_mirror_live_worker import _Http, _NoThrottle
from tests.test_mirror_live_worker import _pool as _live_pool
from tests.test_mirror_maps_the_copy_lane import (AEC_CFB, AEC_UH, ATC_BAYL, CFB, UH, _aec_cfb,
                                                  _atc_bayl, _cfb_fills, _fresh, _Venue,
                                                  _with_venue, _yesno_fills, _yesno_market)
from tests.test_mirror_shadow import CID, _Pool

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
_ = _fresh          # the autouse fixture of the main file, imported so it arms here too


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------- 6. round 2: La Liga

LAL_RMA = "lal-bet-rma-2026-09-04-bet"          # the coordinator's spelling
ATC_LAL_RMA = "atc-lal-bet-rma-2026-09-04-bet"   # the venue's slug (probe SHORTROW)
LAL_REA = "lal-bet-rea-2026-09-04-bet"          # HIS feed's spelling (probe MIRRORUNMAPMKT)
Q_LAL = ("Will Real Betis win against Real Madrid in the La Liga match scheduled for "
         "Sep 4, 2026?")
T_LAL = "Will Real Betis win on 2026-09-04?"
T_LAL_FEED = "Will Real Betis Balompié win on 2026-09-04?"
EV_LAL = "Real Betis vs. Real Madrid"
EV_LAL_FEED = "Real Betis Balompié vs. Real Madrid CF"


def _premap_pool(fills, rows):
    class _P(_Pool):
        async def fetch(self, sql, *a):
            if "FROM us_premap" in sql:
                return [dict(r) for r in rows]
            return await super().fetch(sql, *a)
    return _P(fills=fills, mapped=False)


def test_la_liga_maps_by_premap_identity_and_the_yesno_lane_refuses_it_by_its_own_rule(monkeypatch):
    """lal-bet-rma-2026-09-04-bet, outcome Yes, the venue listing
    atc-lal-bet-rma-2026-09-04-bet: with PREMAP_YN_IDENTITY=on premap's
    identity branch maps it (source 'premap', the class the quarantine
    admits). With the flag off the copy lane's yes/no lane refuses it at
    yn:title-code, PRECISELY because its W3 veto needs the title's club
    name, collapsed ('realbetis'), to START WITH the slug code 'bet'
    (premap._code_prefix_hit strips only the ten GENERIC_CLUB_TOKENS --
    'real' is not one) -- the wording the lane needs is a subject whose
    collapsed distinctive form starts with 'bet' ('Betis' alone is one raw
    token and refuses at yn:anchor-thin), which no La Liga feed title
    supplies. So La Liga rides `yesno`'s admission class only through the
    identity branch; the lane itself cannot carry it."""
    rows = premap._market_rows({"slug": "aec-lal-bet-rma-2026-09-04", "title": EV_LAL},
                               _yesno_market(ATC_LAL_RMA, Q_LAL, "lal-bet-rma-2026-09-04")[ATC_LAL_RMA])
    fills = _yesno_fills(LAL_RMA, T_LAL, EV_LAL, "lal-bet-rma-2026-09-04")
    venue = _Venue(_yesno_market(ATC_LAL_RMA, Q_LAL, "lal-bet-rma-2026-09-04"))
    _with_venue(monkeypatch, venue)
    # flag off: premap's wording arm refuses, the yes/no lane refuses by name
    d: list = []
    assert pmus.resolve_team_yesno_exact(LAL_RMA, "Yes", T_LAL, EV_LAL, d) is None
    assert d == ["yn:title-code"]
    assert not premap._code_prefix_hit("real betis", "bet")
    assert premap._code_prefix_hit("betis sevilla", "bet"), "the wording the veto accepts"
    out: dict = {}
    assert _run(ms.map_market(_premap_pool(fills, rows), fills, venue, whale="rn1",
                              condition_id=CID, out=out)) is None
    assert "refusal" not in out
    # flag on: the identity branch answers from Postgres, no venue read
    monkeypatch.setenv("PREMAP_YN_IDENTITY", "on")
    monkeypatch.setattr(ms, "_map_cache", {})
    n = len(venue.slug_calls)
    m = _run(ms.map_market(_premap_pool(fills, rows), fills, venue, whale="rn1", condition_id=CID))
    assert m == {"us_slug": ATC_LAL_RMA, "long_asset": "tok-yes", "other_asset": "tok-no",
                 "source": "premap"}
    assert len(venue.slug_calls) == n


def test_la_liga_as_his_feed_spells_it_maps_by_neither_path_because_the_codes_differ(monkeypatch):
    """The probe's own row (MIRRORUNMAPMKT 2026-09-06): his slug is
    lal-bet-REA-2026-09-04-bet, the venue's is atc-lal-bet-RMA-...-bet --
    the two feeds spell Real Madrid's code differently. The identity
    branch needs 'atc-' + his slug byte for byte; the yes/no lane builds
    atc-lal-bet-rea-...-bet and atc-lal-rea-bet-...-bet, both unlisted,
    and refuses his title before it reads either. Neither can map it;
    premap's own explain stands (no_side_match: the rows are found by
    title keys). What would map it is a code alias read off the venue's
    own row -- his side code + league + date pin the venue slug -- a rule
    this patch does not add."""
    rows = premap._market_rows({"slug": "aec-lal-bet-rma-2026-09-04", "title": EV_LAL},
                               _yesno_market(ATC_LAL_RMA, Q_LAL, "lal-bet-rma-2026-09-04")[ATC_LAL_RMA])
    fills = _yesno_fills(LAL_REA, T_LAL_FEED, EV_LAL_FEED, "lal-bet-rea-2026-09-04")
    venue = _Venue(_yesno_market(ATC_LAL_RMA, Q_LAL, "lal-bet-rma-2026-09-04"))
    _with_venue(monkeypatch, venue)
    monkeypatch.setenv("PREMAP_YN_IDENTITY", "on")
    out: dict = {}
    assert _run(ms.map_market(_premap_pool(fills, rows), fills, venue, whale="rn1",
                              condition_id=CID, out=out)) is None
    assert "refusal" not in out
    d: list = []
    assert pmus.resolve_team_yesno_exact(LAL_REA, "Yes", T_LAL_FEED, EV_LAL_FEED, d) is None
    assert d == ["yn:title-code"], d
    assert premap._yn_his_identifiers(LAL_REA) == frozenset({"atc-" + LAL_REA})
    assert "atc-" + LAL_REA != ATC_LAL_RMA
    assert all(c not in venue.table for c in ("atc-lal-bet-rea-2026-09-04-bet",
                                               "atc-lal-rea-bet-2026-09-04-bet"))


# ------------------------------ 7. round 2: the grammar class goes live

def _cert_env(monkeypatch):
    """The quarantine HOLDS and the resume lane is open for rn1: what the
    copy lane trades 'exact' under today."""
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
    monkeypatch.setenv("LIVE_PREMAP", "on")
    monkeypatch.setenv("LIVE_PREMAP_WHALES", "rn1")
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "")
    monkeypatch.delenv("LIVE_HOLD_WHALES", raising=False)
    monkeypatch.setattr(le, "overrides_unreadable", lambda: False)
    monkeypatch.setattr(le, "_roster_override", None)


def _live_tick(monkeypatch, pool, venue, now=5000.0):
    from sportsassets import ratelimit
    monkeypatch.setattr(ratelimit, "_throttle", _NoThrottle())
    monkeypatch.setattr(ml, "pace", lambda s=ms.READ_PACING_S: 0.0)
    ml._current_stats = ml._new_stats()
    return ml._Tick(pool=pool, pmus=venue, http=_Http(), now=now, stats=ml._current_stats)


def test_college_football_opens_a_live_book_on_the_baylor_side_under_the_quarantine(monkeypatch):
    """END TO END, the owner's order: aec-cfb-bayl-aubrn-2026-09-05 ("Baylor
    vs. Auburn", sides Bears/Tigers), his outcome Baylor, index 0. The
    real mapper answers grammar; the class's certification passes on
    venue-only truth (the per-side contract atc-...-bayl names 'Bears',
    the aec side at position 0); the resume lane's switches admit rn1
    under the quarantine exactly as they admit 'exact'; the book that
    opens is on the LONG side of the identifier -- Bears, the venue's
    long side, Baylor's -- with map_source 'grammar'."""
    from sportsassets.analytics import mirror_live_rules as rules

    _cert_env(monkeypatch)
    venue = _Venue({**_aec_cfb(), **_atc_bayl()})
    _with_venue(monkeypatch, venue)
    pool = _live_pool(fills=_cfb_fills(), mapped=False, snap={"tok-bayl": 1000.0, "tok-aubrn": 10.0},
                      snap_at=5000.0 - 40)
    pool.state["mapping_quarantine"] = True
    pool.state["premap_live"] = True
    pool.state["side_echo_last"] = {"ok": 2012}
    facts = []
    monkeypatch.setattr(rules, "admission", lambda f, increase=False: facts.append(f) or None)
    opened = []

    async def _capture(p, w, cid, slug, la, oa, ratio, anchor, his_px, target, src, gk, intent="x"):
        opened.append({"whale": w, "cid": cid, "slug": slug, "la": la, "oa": oa, "target": target,
                       "map_source": src, "intent": intent, "his_px": his_px})
        return {"ok": False, "refusal": "captured"}

    monkeypatch.setattr(le, "_open_mirror_book", _capture)
    t = _live_tick(monkeypatch, pool, venue)
    t.http = _Http(rows=[{"conditionId": CID, "asset": "tok-bayl", "size": 1000},
                         {"conditionId": CID, "asset": "tok-aubrn", "size": 10}])
    _run(ml._tick_candidate(t, "rn1", CID))
    c = ml._current_stats["census"]
    assert c["grammar_echo_ok"] == 1 and c["grammar_tripped"] == 0, {k: v for k, v in c.items() if v}
    assert c["map_source_unverified"] == 0 and c["side_echo_mismatch"] == 0
    assert facts and facts[0].mapping_ok is True and facts[0].mapping_why is None
    assert facts[0].family == "moneyline" and facts[0].per_side is False
    assert opened == [{"whale": "rn1", "cid": CID, "slug": AEC_CFB, "la": "tok-bayl",
                       "oa": "tok-aubrn", "target": opened[0]["target"], "map_source": "grammar",
                       "intent": LONG, "his_px": 0.55}]
    assert opened[0]["target"] > 0
    # the contract and the aec market were both read from the venue
    assert ATC_BAYL in venue.slug_calls and venue.slug_calls.count(AEC_CFB) >= 3
    st = pool.state["mirror_grammar_echo"]
    assert st["ok"] == 1 and st["tripped"] is False and st["pending"][AEC_CFB]["outcome_desc"] == "Bears"
    assert st["pending"][AEC_CFB]["intent"] == LONG
    # the resume lane's gates are the ones 'exact' clears: a whale off the
    # allowlist is refused by the lane's own text, named grammar
    monkeypatch.setenv("LIVE_PREMAP_WHALES", "someoneelse")
    ok, why = _run(ml._admit_source(t, "rn1", "grammar", AEC_CFB))
    assert ok is False and why.startswith("premap-live:") and "(src=grammar," in why
    ml._current_stats = None


def test_the_grammar_class_refuses_by_name_at_every_certification_gate(monkeypatch):
    """No per-side contract listed: unverified, refused, nothing tripped. A
    contract naming another side: mismatch, refused, the class TRIPPED,
    the next grammar market refused grammar_tripped. A grammar book still
    awaiting its first-fill echo: the next one waits (grammar_probation,
    never TTL-skipped). State unreadable: refused by that name."""
    _cert_env(monkeypatch)
    g = {"his_slug": CFB, "side_index": 0, "outcome_desc": "Bears", "intent": LONG,
         "slug": AEC_CFB, "asset": "tok-bayl"}
    # unlisted contract
    pool = _live_pool(fills=_cfb_fills(), mapped=False)
    t = _live_tick(monkeypatch, pool, _Venue(_aec_cfb()))
    assert _run(ml._grammar_admission(t, "rn1", AEC_CFB, g)) == "grammar_echo_unverified"
    assert pool.state["mirror_grammar_echo"]["unverified"] == 1
    assert pool.state["mirror_grammar_echo"]["tripped"] is False
    # a contract naming the OTHER side: mismatch trips the class
    t2 = _live_tick(monkeypatch, pool, _Venue({**_aec_cfb(), **_atc_bayl("Tigers")}))
    assert _run(ml._grammar_admission(t2, "rn1", AEC_CFB, g)) == "side_echo_mismatch"
    assert pool.state["mirror_grammar_echo"]["tripped"] is True
    assert _run(ml._grammar_admission(t2, "rn1", AEC_CFB, g)) == "grammar_tripped"
    ok, why = _run(ml._admit_source(t2, "rn1", "grammar", AEC_CFB))
    assert (ok, why) == (False, "grammar_tripped")
    # the probation: an open grammar book not yet verified holds the next
    pool3 = _live_pool(fills=_cfb_fills(), mapped=False)
    pool3.add_book(ledger=0, map_source="grammar")
    t3 = _live_tick(monkeypatch, pool3, _Venue({**_aec_cfb(), **_atc_bayl()}))
    assert _run(ml._grammar_admission(t3, "rn1", AEC_CFB, g)) == "grammar_probation"
    # the state unreadable: refused by name, nothing written
    pool4 = _live_pool(fills=_cfb_fills(), mapped=False)
    pool4.raise_on.append(("SELECT value FROM ingestion_state", RuntimeError("db down")))
    t4 = _live_tick(monkeypatch, pool4, _Venue({**_aec_cfb(), **_atc_bayl()}))
    assert _run(ml._grammar_admission(t4, "rn1", AEC_CFB, g)) == "grammar_echo_unreadable"
    assert _run(ml._admit_source(t4, "rn1", "grammar", AEC_CFB)) == (False, "grammar_echo_unreadable")
    # a grammar map handed no side facts is unverified, not a crash
    t5 = _live_tick(monkeypatch, _live_pool(fills=_cfb_fills(), mapped=False),
                    _Venue({**_aec_cfb(), **_atc_bayl()}))
    assert _run(ml._grammar_admission(t5, "rn1", AEC_CFB, None)) == "grammar_echo_unverified"
    # the pinned source text: the certification runs before the resume
    # lane's gates and a grammar book's increase re-reads the trip
    src = inspect.getsource(ml._admit_source)
    assert 'le._mapping_admitted(t.pool, whale, "exact", slug)' in src and "grammar_tripped" in src
    assert "_admit_source(" in inspect.getsource(ml._increase_recheck)
    ml._current_stats = None


def test_the_first_fill_echo_verifies_or_freezes_a_grammar_book(monkeypatch):
    """After the first fill the venue's positions payload must hold the
    side the rule chose with the book's sign: ok verifies the book (the
    next grammar book may open), a mismatch freezes it side_echo_mismatch
    and trips the class; an unreadable echo waits."""
    _cert_env(monkeypatch)
    pool = _live_pool(fills=_cfb_fills(), mapped=False)
    pool.state["mirror_grammar_echo"] = {"ok": 1, "mismatch": 0, "unverified": 0, "tripped": False,
                                         "verified": [], "pending": {AEC_CFB: {
                                             "outcome_desc": "Bears", "intent": LONG,
                                             "side_index": 0, "his_slug": CFB}}}
    # the worker reads book ROWS (copies), never the pool's own dicts
    book = dict(pool.add_book(ledger=99, map_source="grammar", us_market_slug=AEC_CFB,
                              long_asset="tok-bayl", other_asset="tok-aubrn"))
    t = _live_tick(monkeypatch, pool, _Venue(_aec_cfb()))
    # no position yet at the venue: unverified, the book WAITS (no plan,
    # no freeze -- round 3, review 4)
    monkeypatch.setattr(ml, "_position_echo", lambda pmus, slug: {"net": 0.0, "outcome": None})
    assert _run(ml._grammar_fill_check(t, book)) == "wait"
    assert pool.state["mirror_grammar_echo"]["unverified"] == 1 and book["state"] == "live"
    # the venue holds Bears, +99: verified
    monkeypatch.setattr(ml, "_position_echo",
                        lambda pmus, slug: {"net": 99.0, "outcome": "Bears", "title": "Baylor vs. Auburn"})
    assert _run(ml._grammar_fill_check(t, book)) == "ok"
    st = pool.state["mirror_grammar_echo"]
    assert st["ok"] == 2 and book["id"] in st["verified"] and AEC_CFB not in st["pending"]
    assert ml._current_stats["census"]["grammar_echo_ok"] == 1

    def _never(pmus, slug):
        raise AssertionError("a verified book is never re-read")

    monkeypatch.setattr(ml, "_position_echo", _never)
    assert _run(ml._grammar_fill_check(t, book)) == "ok"
    # a second book whose venue echo names the OTHER side: frozen and tripped
    book2 = dict(pool.add_book(ledger=50, map_source="grammar", us_market_slug=AEC_UH,
                               long_asset="tok-u", other_asset="tok-h"))
    st["pending"][AEC_UH] = {"outcome_desc": "Bruins", "intent": LONG, "side_index": 0, "his_slug": UH}
    pool.state["mirror_grammar_echo"] = st
    monkeypatch.setattr(ml, "_position_echo",
                        lambda pmus, slug: {"net": 50.0, "outcome": "Rainbow Warriors", "title": "x"})
    assert _run(ml._grammar_fill_check(t, book2)) == "frozen"
    assert book2["state"] == "frozen" and book2["frozen_reason"] == "side_echo_mismatch"
    st = pool.state["mirror_grammar_echo"]
    assert st["tripped"] is True and st["mismatch"] == 1
    assert ml._current_stats["census"]["side_echo_mismatch"] == 1
    g = {"his_slug": CFB, "side_index": 0, "outcome_desc": "Bears", "intent": LONG}
    assert _run(ml._grammar_admission(t, "rn1", AEC_CFB, g)) == "grammar_tripped"
    # the hook in _tick_book: a grammar book is checked before any plan
    src = inspect.getsource(ml._tick_book)
    assert 'book.get("map_source") or "") == "grammar"' in src
    assert src.index("_grammar_fill_check(") < src.index("rules.step_ratio(")
    assert 'if fc != "ok":' in src and '"grammar_echo_unverified"' in src, "a waiting book plans nothing"
    ml._current_stats = None


# ---------------------------------------- 8. round 2: the budget knob

def test_the_environment_may_only_lower_the_map_read_budget(monkeypatch):
    """Mutant M10: MIRROR_MAP_READS is a capped_env -- a shell can lower
    it, never raise it, and an unreadable value is the default."""
    from sportsassets.analytics import mirror_live_rules as rules

    src = inspect.getsource(ms)
    assert 'MAP_READS_PER_TICK = int(rules.capped_env("MIRROR_MAP_READS", 10.0, floor=0.0))' in src
    assert 'os.getenv("MIRROR_MAP_READS"' not in src and 'os.environ.get("MIRROR_MAP_READS"' not in src
    for raw, want in (("50", 10), ("3", 3), ("0", 0), ("-4", 0), ("junk", 10), ("", 10)):
        monkeypatch.setenv("MIRROR_MAP_READS", raw)
        assert int(rules.capped_env("MIRROR_MAP_READS", 10.0, floor=0.0)) == want, raw
    monkeypatch.delenv("MIRROR_MAP_READS", raising=False)
    assert int(rules.capped_env("MIRROR_MAP_READS", 10.0, floor=0.0)) == 10
    # the budget object honours whatever the module computed
    monkeypatch.setattr(ms, "MAP_READS_PER_TICK", 3)
    assert ms.MapBudget().cap == 3 and ms.MapBudget(cap=1).cap == 1
    # and the memories are pruned on the TTL (review C)
    monkeypatch.setattr(ms, "_map_cache", {("rn1", "old"): {"until": 1.0, "tokens": {"a": None}},
                                           ("rn1", "new"): {"until": 9e9, "tokens": {"a": None}}})
    monkeypatch.setattr(ms, "_slug_404_until", {"gone": 1.0, "kept": 9e9})
    ms._prune_maps(5000.0)
    assert set(ms._map_cache) == {("rn1", "new")} and set(ms._slug_404_until) == {"kept"}
