"""C1 round 3: the single-token fill is corroborated against the token
catalogue (review 1), the venue-truth check trips only on the OTHER side's
name (2), the per-side contract's suffix is the venue's, read off us_premap
(3), an unverified first fill makes the book wait (4), and the two code
collisions the mutants needed (5). Driven with fakes: no venue, no
database."""
from __future__ import annotations

import asyncio

from sportsassets import map_lane
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_c1_round2 import _cert_env, _live_tick
from tests.test_mirror_live_worker import _pool as _live_pool
from tests.test_mirror_maps_the_copy_lane import (AEC_CFB, ATC_BAYL, CFB, _aec_cfb, _atc_bayl,
                                                  _cfb_fills, _fresh, _Venue, _with_venue)
from tests.test_mirror_shadow import CID, _Pool

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
_ = _fresh


def _run(c):
    return asyncio.run(c)


class _CataloguePool(_Pool):
    """A pool whose market_tokens answers for the condition."""

    def __init__(self, rows, **kw):
        super().__init__(**kw)
        self.rows = rows
        self.asked = []

    async def fetch(self, sql, *a):
        if "map-catalogue-pair" in sql:
            self.asked.append(a)
            if isinstance(self.rows, Exception):
                raise self.rows
            return list(self.rows)
        return await super().fetch(sql, *a)


def _single(monkeypatch, rows, fills=None, venue=None):
    fills = fills or _cfb_fills()[:1]
    venue = venue or _Venue(_aec_cfb())
    _with_venue(monkeypatch, venue)
    monkeypatch.setattr(ms, "_map_cache", {})
    p = _CataloguePool(rows, fills=fills, mapped=False)
    out: dict = {}
    m = _run(ms.map_market(p, fills, venue, whale="rn1", condition_id=CID, out=out))
    return m, out, p


# ------------------------------------------------------- (1) single token

def test_a_single_token_fill_maps_only_when_the_token_catalogue_corroborates_both_indices(monkeypatch):
    """No sibling in his fills: nothing in them pins the feed's index order
    to the slug's team order, so the market's own market_tokens rows must
    -- the mapped token at the code's position, the sibling at the
    complement naming no claim on the mapped code -- else side_code_pair."""
    good = [{"token_id": "tok-bayl", "outcome": "Baylor", "outcome_index": 0},
            {"token_id": "tok-aubrn", "outcome": "Auburn", "outcome_index": 1}]
    m, out, p = _single(monkeypatch, good)
    assert m == {"us_slug": AEC_CFB, "long_asset": "tok-bayl", "other_asset": None, "source": "grammar"}
    assert p.asked == [(CID,)]
    # no catalogue rows: refuse
    m, out, _ = _single(monkeypatch, [])
    assert m is None and out["refusal"] == "side_code_pair"
    # the catalogue unreadable: refuse
    m, out, _ = _single(monkeypatch, RuntimeError("db down"))
    assert m is None and out["refusal"] == "side_code_pair"
    # the mapped token's own catalogue index disagrees with the code's position
    m, out, _ = _single(monkeypatch, [dict(good[0], outcome_index=1), dict(good[1], outcome_index=0)])
    assert m is None and out["refusal"] == "side_code_pair"
    # the sibling's index is not the complement
    m, out, _ = _single(monkeypatch, [good[0], dict(good[1], outcome_index=0)])
    assert m is None and out["refusal"] == "side_code_pair"
    # the sibling names the mapped code: conflict
    m, out, _ = _single(monkeypatch, [good[0], dict(good[1], outcome="Baylor")])
    assert m is None and out["refusal"] == "side_code_conflict"
    # three rows, or a catalogue that does not carry his token: refuse
    m, out, _ = _single(monkeypatch, good + [{"token_id": "tok-x", "outcome": "Draw", "outcome_index": 2}])
    assert m is None and out["refusal"] == "side_code_pair"
    m, out, _ = _single(monkeypatch, [dict(good[0], token_id="tok-other"), good[1]])
    assert m is None and out["refusal"] == "side_code_pair"
    # THE COLLISION THE RULE EXISTS FOR: Michigan State, feed index swapped,
    # single token -- the catalogue says Michigan is index 0 and Michigan
    # State index 1, so the mapped token's row disagrees: refused
    slug = "cfb-mich-mist-2026-09-05"
    aec = "aec-" + slug
    mk = {aec: {"slug": aec, "closed": False, "question": "Michigan vs. Michigan State",
                "marketSides": [{"identifier": aec, "description": "Wolverines", "long": True},
                                {"identifier": aec, "description": "Spartans", "long": False}]}}
    f = _cfb_fills("Michigan State", "Michigan", index_a=0, index_b=1)[:1]
    for x in f:
        x["market_slug"] = x["event_slug"] = slug
    cat = [{"token_id": "tok-bayl", "outcome": "Michigan State", "outcome_index": 1},
           {"token_id": "tok-aubrn", "outcome": "Michigan", "outcome_index": 0}]
    m, out, _ = _single(monkeypatch, cat, fills=f, venue=_Venue(mk))
    assert m is None and out["refusal"] == "side_code_pair"
    # no condition id at all: refuse
    venue = _Venue(_aec_cfb())
    _with_venue(monkeypatch, venue)
    monkeypatch.setattr(ms, "_map_cache", {})
    out2: dict = {}
    fills = _cfb_fills()[:1]
    assert _run(ms.map_market(_CataloguePool(good, fills=fills, mapped=False), fills, venue, whale="rn1",
                              condition_id=None, out=out2)) is None
    assert out2["refusal"] == "side_code_pair"
    # both tokens in the fills: the pair rule reads the fills, never the catalogue
    monkeypatch.setattr(ms, "_map_cache", {})
    p = _CataloguePool(RuntimeError("never read"), fills=_cfb_fills(), mapped=False)
    m = _run(ms.map_market(p, _cfb_fills(), venue, whale="rn1", condition_id=CID))
    assert m and m["source"] == "grammar" and p.asked == []


# ----------------------------------------------------- (2) venue truth

def test_grammar_truth_trips_only_on_the_other_sides_name():
    mk = _aec_cfb()[AEC_CFB]
    # a yes/no-shaped per-side contract: unverified, never a trip
    con = {"slug": ATC_BAYL, "question": "Will Baylor win against Auburn?",
           "marketSides": [{"identifier": ATC_BAYL, "description": "Yes", "long": True},
                           {"identifier": ATC_BAYL, "description": "No", "long": False}]}
    assert map_lane.grammar_truth(con, mk, 0)[0] == "unverified"
    # a contract naming neither side: unverified
    assert map_lane.grammar_truth(_atc_bayl("Baylor")[ATC_BAYL], mk, 0)[0] == "unverified"
    assert map_lane.grammar_truth({"slug": ATC_BAYL}, mk, 0)[0] == "unverified"
    # the OTHER side's name, in any field: mismatch
    assert map_lane.grammar_truth(_atc_bayl("Tigers")[ATC_BAYL], mk, 0)[0] == "mismatch"
    assert map_lane.grammar_truth({"slug": ATC_BAYL, "team": {"alias": "Tigers"}}, mk, 0)[0] == "mismatch"
    # the side's own name: ok; a side list missing a description: unverified
    assert map_lane.grammar_truth(_atc_bayl()[ATC_BAYL], mk, 0)[0] == "ok"
    thin = {**mk, "marketSides": [dict(mk["marketSides"][0]), {"identifier": AEC_CFB, "long": False}]}
    assert map_lane.grammar_truth(_atc_bayl()[ATC_BAYL], thin, 0)[0] == "unverified"


# ------------------------------------------ (3) the venue's own suffix

class _PremapPool(_Pool):
    def __init__(self, rows, **kw):
        super().__init__(**kw)
        self.rows = rows
        self.likes = []

    async def fetch(self, sql, *a):
        if "ml-grammar-contracts" in sql:
            self.likes.append(a[0])
            if isinstance(self.rows, Exception):
                raise self.rows
            return list(self.rows)
        return await super().fetch(sql, *a)


def _row(slug, q=""):
    return {"identifier": slug, "market_slug": slug, "question": q}


def test_the_per_side_contract_suffix_is_read_off_the_venues_rows_exactly_one():
    hs = "cfb-hawaii-stan-2026-08-29"
    base = "atc-cfb-hawaii-stan-2026-08-29-"
    # the venue's short suffix ('h' for hawaii): a prefix of his code and of no other
    rows = [_row(base + "h", "Will Hawaii win?"), _row(base + "s", "Will Stanford win?"),
            _row(base + "winner-2q-h", "2Q winner")]
    p = _PremapPool(rows)
    assert _run(ml._contract_candidates(p, hs, 0, "Rainbow Warriors")) == [base + "h"]
    assert p.likes == [base + "%"]
    assert _run(ml._contract_candidates(p, hs, 1, "Cardinal")) == [base + "s"]
    # his own code as the suffix
    rows = [_row("atc-cfb-bayl-aubrn-2026-09-05-bayl", "Will Baylor win?"),
            _row("atc-cfb-bayl-aubrn-2026-09-05-aubrn", "Will Auburn win?")]
    assert _run(ml._contract_candidates(_PremapPool(rows), CFB, 0, "Bears")) == [ATC_BAYL]
    assert _run(ml._contract_candidates(_PremapPool(rows), CFB, 1, "Tigers")) == [ATC_BAYL[:-4] + "aubrn"]
    # a question naming the aec side whole fits too; a segment row never does
    rows = [_row("atc-cfb-bayl-aubrn-2026-09-05-bu", "Will the Bears win against the Tigers?"),
            _row("atc-cfb-bayl-aubrn-2026-09-05-winner-1h-bu", "Bears 1H")]
    assert _run(ml._contract_candidates(_PremapPool(rows), CFB, 0, "Bears")) \
        == ["atc-cfb-bayl-aubrn-2026-09-05-bu"]
    # a suffix that prefixes BOTH codes fits neither; two fits are not one
    rows = [_row("atc-cfb-boise-bost-2026-09-05-bo", "Will Boise State win?")]
    assert _run(ml._contract_candidates(_PremapPool(rows), "cfb-boise-bost-2026-09-05", 0, "Broncos")) == []
    rows = [_row(ATC_BAYL, "Will Baylor win?"), _row("atc-cfb-bayl-aubrn-2026-09-05-bay", "Will Baylor win?")]
    assert len(_run(ml._contract_candidates(_PremapPool(rows), CFB, 0, "Bears"))) == 2
    # the sweep lists nothing: his code's slug is the one candidate; unreadable: None
    assert _run(ml._contract_candidates(_PremapPool([]), CFB, 0, "Bears")) == [ATC_BAYL]
    assert _run(ml._contract_candidates(_PremapPool(RuntimeError("down")), CFB, 0, "Bears")) is None
    assert _run(ml._contract_candidates(_PremapPool([]), "nonsense", 0, "Bears")) == []


def test_the_certification_uses_the_venues_suffix_and_refuses_on_two_or_none(monkeypatch):
    _cert_env(monkeypatch)
    hs = "cfb-hawaii-stan-2026-08-29"
    aec, con = "aec-" + hs, "atc-" + hs + "-h"
    table = {aec: {"slug": aec, "closed": False, "question": "Hawaii vs. Stanford",
                   "marketSides": [{"identifier": aec, "description": "Rainbow Warriors", "long": True},
                                   {"identifier": aec, "description": "Cardinal", "long": False}]},
             con: {"slug": con, "closed": False, "outcome": "Rainbow Warriors", "title": "Rainbow Warriors"}}
    g = {"his_slug": hs, "side_index": 0, "outcome_desc": "Rainbow Warriors", "intent": LONG,
         "slug": aec, "asset": "tok-h"}
    pool = _live_pool(fills=_cfb_fills(), mapped=False)
    t = _live_tick(monkeypatch, pool, _Venue(table))
    rows = [_row(con, "Will Hawaii win?"), _row("atc-" + hs + "-s", "Will Stanford win?")]
    real = ml._contract_candidates

    async def _cands(p, his_slug, i, desc, other_desc="", _rows=rows):
        return await real(_PremapPool(_rows), his_slug, i, desc, other_desc)

    monkeypatch.setattr(ml, "_contract_candidates", _cands)
    assert _run(ml._grammar_admission(t, "rn1", aec, g)) is None
    assert pool.state["mirror_grammar_echo"]["ok"] == 1
    # two rows fit: unverified, not a guess; none readable: unverified
    monkeypatch.setattr(ml, "_contract_candidates", lambda *a, **k: _ret([con, con + "x"]))
    assert _run(ml._grammar_admission(t, "rn1", aec, g)) == "grammar_echo_unverified"
    monkeypatch.setattr(ml, "_contract_candidates", lambda *a, **k: _ret(None))
    assert _run(ml._grammar_admission(t, "rn1", aec, g)) == "grammar_echo_unverified"
    assert pool.state["mirror_grammar_echo"]["tripped"] is False
    ml._current_stats = None


async def _ret(v):
    return v


# ------------------------------------------- (4) an unverified fill waits

def test_a_grammar_book_waits_on_an_unverified_first_fill_and_never_trips_on_a_missing_record(monkeypatch):
    _cert_env(monkeypatch)
    pool = _live_pool(fills=_cfb_fills(), mapped=False)
    pool.state["mirror_grammar_echo"] = {"ok": 0, "mismatch": 0, "unverified": 0, "tripped": False,
                                         "verified": [], "pending": {}}
    book = dict(pool.add_book(ledger=99, map_source="grammar", us_market_slug=AEC_CFB,
                              long_asset="tok-bayl", other_asset="tok-aubrn"))
    t = _live_tick(monkeypatch, pool, _Venue(_aec_cfb()))
    # pending[slug] absent: unverified, the book waits, nothing trips
    monkeypatch.setattr(ml, "_position_echo", lambda pmus, slug: {"net": 99.0, "outcome": "Tigers"})
    assert _run(ml._grammar_fill_check(t, book)) == "wait"
    st = pool.state["mirror_grammar_echo"]
    assert st["unverified"] == 1 and st["tripped"] is False and book["state"] == "live"
    # an unattributable echo: waits
    st["pending"][AEC_CFB] = {"outcome_desc": "Bears", "intent": LONG, "side_index": 0, "his_slug": CFB}
    pool.state["mirror_grammar_echo"] = st
    monkeypatch.setattr(ml, "_position_echo", lambda pmus, slug: {"net": 30.0, "outcome": "Bears"})
    assert _run(ml._grammar_fill_check(t, book)) == "wait"
    # the state unreadable: waits, by name
    pool.raise_on.append(("SELECT value FROM ingestion_state", RuntimeError("db down")))
    assert _run(ml._grammar_fill_check(t, book)) == "wait"
    assert ml._current_stats["census"]["grammar_echo_unreadable"] == 1
    pool.raise_on.clear()
    # a book that holds nothing yet plans (there is nothing to echo)
    flat = dict(book, ledger_net=0)
    assert _run(ml._grammar_fill_check(t, flat)) == "ok"
    ml._current_stats = None


# --------------------------------------------------- (5) the two collisions

def test_the_two_code_collisions_refuse(monkeypatch):
    # A "Bayl Aubrn" (index 0) + B "Aubrn" (index 1): A is ambiguous, the market refuses
    fills = _cfb_fills("Bayl Aubrn", "Aubrn", index_a=0, index_b=1)
    _with_venue(monkeypatch, _Venue(_aec_cfb()))
    out: dict = {}
    assert _run(ms.map_market(_Pool(fills=fills, mapped=False), fills, _Venue(_aec_cfb()), whale="rn1",
                              condition_id=CID, out=out)) is None
    assert out["refusal"] == "side_code_ambiguous"
    assert map_lane.aec_code_side(CFB, "Bayl Aubrn", _aec_cfb()[AEC_CFB], 0)[1] == "side_code_ambiguous"
    # a one-letter FIRST word never stands for a code
    assert not map_lane.code_hits("aubrn", "A&M Aggies") and not map_lane.code_hits("agg", "A&M Aggies")
    assert not map_lane.code_hits("tam", "A&M") and not map_lane.code_hits("a", "A&M Aggies")
    assert map_lane.aec_code_side("cfb-tam-aubrn-2026-09-05", "A&M Aggies",
                                  {"slug": "aec-cfb-tam-aubrn-2026-09-05"}, 0)[1] == "side_code_unmatched"


# ------------------------------------------- round-3 review: the two fixes

def test_an_unnamed_catalogue_sibling_corroborates_nothing(monkeypatch):
    """Round-3 review, major-conditional: the catalogue's indices come from
    the same feed as his fill's, so with the sibling's outcome NULL the
    pair rule would pass on the index complement alone and a swapped
    feed order opens the wrong side. An unnamed sibling is side_code_pair."""
    good = [{"token_id": "tok-bayl", "outcome": "Baylor", "outcome_index": 0},
            {"token_id": "tok-aubrn", "outcome": "Auburn", "outcome_index": 1}]
    for empty in (None, "", "   "):
        m, out, _ = _single(monkeypatch, [good[0], dict(good[1], outcome=empty)])
        assert m is None and out["refusal"] == "side_code_pair", empty
    # the reviewer's repro: Michigan State idx 0 with the sibling unnamed
    slug = "cfb-mich-mist-2026-09-05"
    aec = "aec-" + slug
    mk = {aec: {"slug": aec, "closed": False, "question": "Michigan vs. Michigan State",
                "marketSides": [{"identifier": aec, "description": "Wolverines", "long": True},
                                {"identifier": aec, "description": "Spartans", "long": False}]}}
    f = _cfb_fills("Michigan State", "Michigan", index_a=0, index_b=1)[:1]
    for x in f:
        x["market_slug"] = x["event_slug"] = slug
    cat = [{"token_id": "tok-bayl", "outcome": "Michigan State", "outcome_index": 0},
           {"token_id": "tok-aubrn", "outcome": None, "outcome_index": 1}]
    m, out, _ = _single(monkeypatch, cat, fills=f, venue=_Venue(mk))
    assert m is None and out["refusal"] == "side_code_pair"


def test_a_question_naming_both_sides_is_not_our_contract():
    """Round-3 review, minor: the opponent's per-side row asks 'Will Tigers
    win against Bears?' -- it names our side, so by_question fitted it.
    With the other side's description known, a question that names both
    fits neither side; his code and the venue's short code still fit."""
    base = "atc-cfb-bayl-aubrn-2026-09-05-"
    rows = [_row(base + "bu", "Will the Bears win against the Tigers?"),
            _row(base + "au", "Will the Tigers win against the Bears?")]
    # without the other side's description (legacy callers): the venue's
    # short code fits our side by question; the 'au' row does NOT -- its
    # suffix prefixes aubrn, the OTHER side's contract, which no question
    # makes ours (C4 review fold 2026-09-06, HIGH-2; this line said 2)
    assert _run(ml._contract_candidates(_PremapPool(rows), CFB, 0, "Bears")) == [base + "bu"]
    # with it: neither question is ours alone -> no fit by question
    assert _run(ml._contract_candidates(_PremapPool(rows), CFB, 0, "Bears", "Tigers")) == []
    # a question naming our side only still fits; the code fit is untouched
    rows = [_row(base + "bu", "Will the Bears win?"), _row(base + "aubrn", "Will the Tigers win?")]
    assert _run(ml._contract_candidates(_PremapPool(rows), CFB, 0, "Bears", "Tigers")) == [base + "bu"]
    assert _run(ml._contract_candidates(_PremapPool(rows), CFB, 1, "Tigers", "Bears")) == [base + "aubrn"]
