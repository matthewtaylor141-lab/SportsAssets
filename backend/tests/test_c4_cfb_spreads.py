"""C4 (2026-09-06): college football spreads whose venue rows name
mascots, and the multi-word school code.

The venue rows are c3_rows_2038.log verbatim (asc-cfb-washst-wash on
the full game names Cougars/Huskies, the 1h rows name the schools;
asc-cfb-scarst-flam names Bulldogs/Rattlers except 22.5/24.5; the aec
moneyline rows list the mascots as sides). His rows are
his-recent_2222.log: `cfb-washst-wash-2026-09-06-spread-home-<L>`,
title "Spread: Washington (-L)", outcome "Washington State" (and two
small rows with outcome "Washington"); the moneyline
`cfb-scarst-flam-2026-09-06`, outcome "South Carolina State".

The chain, and nothing else (docs/mirror-coverage.md §10): the codes
are byte-equal (C3); his outcome and his title's team resolve to a or b
by the CODE (map_lane.code_reads); the question's subject is placed at
a only by the venue's own rows on the event -- the aec row listing the
question's two names as its sides AND the C1 grammar class's venue-truth
record for that aec market (mirror_grammar_echo.certified) placing the
subject's mascot at the code's position; then C3's table unchanged.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets import map_lane
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import premap
from tests.test_c1_round2 import _cert_env, _live_tick
from tests.test_c1_round3 import _PremapPool, _row
from tests.test_mirror_live_worker import _Http
from tests.test_mirror_live_worker import _pool as _live_pool
from tests.test_mirror_maps_the_copy_lane import (AEC_CFB, ATC_BAYL, CFB, _Venue, _aec_cfb,
                                                  _atc_bayl, _cfb_fills, _with_venue)

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
D = "2026-09-06"
# the real sweep, captured once: the admission helpers below patch
# ml._contract_candidates per call, and a capture inside the helper would
# wrap the previous call's wrapper (and sweep its rows)
_REAL_CONTRACT_CANDIDATES = ml._contract_candidates
WW = f"cfb-washst-wash-{D}"
SF = f"cfb-scarst-flam-{D}"
AEC_WW, AEC_SF = "aec-" + WW, "aec-" + SF
WSU, UW = "Washington State", "Washington"
EVENTS = {AEC_WW: "Washington State vs. Washington",
          AEC_SF: "South Carolina State vs. Florida A&M"}
# his-recent_2222.log, the six rows of the brief (the his_slug column is
# cut at 40 chars there; the lines are the 2039 table's 23.5/24.5/21.5
# and the smaller 7.5/1.5 rows; the moneyline is the sixth)
HIS_ROWS = [
    ("22:01:00", 7541, f"{WW}-spread-home-23pt5", "Spread: Washington (-23.5)", WSU),
    ("22:19:31", 3848, f"{WW}-spread-home-24pt5", "Spread: Washington (-24.5)", WSU),
    ("22:17:57", 1279, f"{WW}-spread-home-21pt5", "Spread: Washington (-21.5)", WSU),
    ("22:17:06", 338, f"{WW}-spread-home-7pt5", "Spread: Washington (-7.5)", WSU),
    ("22:08:54", 113, f"{WW}-spread-home-1pt5", "Spread: Washington (-1.5)", WSU),
]
# the certified record the live worker writes at venue-truth ok
# (mirror_live._grammar_admission): the per-side contract for washst
# named "Cougars", the aec side at position 0
CERT_WW = {"outcome_desc": "Cougars", "side_index": 0, "his_slug": WW, "at": 5000.0}
CERT_UW = {"outcome_desc": "Huskies", "side_index": 1, "his_slug": WW, "at": 5000.0}


def _asc(ident: str, q: str) -> dict:
    line = ident.rsplit("-", 1)[-1].replace("pt", ".")
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": line, "long": True},
        {"identifier": ident, "description": line, "long": False}]}


def _aec(ident: str, q: str, a: str, b: str) -> dict:
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": a, "long": True},
        {"identifier": ident, "description": b, "long": False}]}


def _venue(aec: bool = True) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for ln in ("1pt5", "2pt5", "5pt5", "7pt5", "21pt5", "23pt5", "24pt5"):
        L = ln.replace("pt", ".")
        out.append((AEC_WW, _asc(f"asc-{WW}-neg-{ln}",
                                 f"Will the Cougars cover -{L} vs the Huskies in Cougars vs. Huskies?")))
        out.append((AEC_WW, _asc(f"asc-{WW}-pos-{ln}",
                                 f"Will the Cougars cover {L} vs the Huskies in Cougars vs. Huskies?")))
    for seg in ("1h", "1q", "2h"):
        out.append((AEC_WW, _asc(f"asc-{WW}-{seg}-pos-3pt5",
                                 "Will the Washington State cover 3.5 vs the Washington in "
                                 "Washington State vs. Washington?")))
    for ln in ("1pt5", "3pt5", "5pt5", "7pt5", "10pt5", "14pt5", "21pt5"):
        L = ln.replace("pt", ".")
        out.append((AEC_SF, _asc(f"asc-{SF}-neg-{ln}",
                                 f"Will the Bulldogs cover -{L} vs the Rattlers in Bulldogs vs. Rattlers?")))
        out.append((AEC_SF, _asc(f"asc-{SF}-pos-{ln}",
                                 f"Will the Bulldogs cover {L} vs the Rattlers in Bulldogs vs. Rattlers?")))
    for ln in ("22pt5", "24pt5"):
        L = ln.replace("pt", ".")
        out.append((AEC_SF, _asc(f"asc-{SF}-neg-{ln}",
                                 f"Will the South Carolina State cover -{L} vs the Florida A&M in "
                                 f"South Carolina State vs. Florida A&M?")))
    if aec:
        out.append((AEC_WW, _aec(AEC_WW, "Who will win in the upcoming football event Cougars vs "
                                         "Huskies scheduled for September 6, 2026 at 8:00 PM UTC?",
                                 "Cougars", "Huskies")))
        out.append((AEC_SF, _aec(AEC_SF, "Who will win in the upcoming football event Bulldogs vs "
                                         "Rattlers scheduled for September 6, 2026 at 7:00 PM UTC?",
                                 "Bulldogs", "Rattlers")))
    return out


def _board(venue=None, extra=(), drop=()) -> list[dict]:
    rows: list[dict] = []
    for ev, m in list(venue if venue is not None else _venue()) + list(extra):
        if m["slug"] in drop:
            continue
        keys = premap.event_keys_for(EVENTS[ev], ev)
        for r in premap._market_rows({"slug": ev, "title": EVENTS[ev]}, m):
            r["event_keys"] = keys
            rows.append(r)
    return rows


def _game(a: str, b: str, ma: str, mb: str, title: str, lines=("7pt5",)):
    """Another college game, cfb-<a>-<b>: the venue's mascot-named asc
    rows on `lines`, its aec row with sides (ma, mb), and the class state
    certifying ma at position 0 -- (his slug head, the board, the
    state)."""
    ww = f"cfb-{a}-{b}-{D}"
    aec = "aec-" + ww
    markets = []
    for ln in lines:
        L = ln.replace("pt", ".")
        markets.append(_asc(f"asc-{ww}-neg-{ln}", f"Will the {ma} cover -{L} vs the {mb} in {ma} vs. {mb}?"))
        markets.append(_asc(f"asc-{ww}-pos-{ln}", f"Will the {ma} cover {L} vs the {mb} in {ma} vs. {mb}?"))
    markets.append(_aec(aec, f"Who will win in the upcoming football event {ma} vs {mb} …", ma, mb))
    rows: list[dict] = []
    keys = premap.event_keys_for(title, aec)
    for m in markets:
        for r in premap._market_rows({"slug": aec, "title": title}, m):
            r["event_keys"] = keys
            rows.append(r)
    st = _state({aec: {"outcome_desc": ma, "side_index": 0, "his_slug": ww, "at": 1.0}})
    return ww, rows, st


UNREADABLE = object()


def _state(certified: dict | None = None, **kw) -> dict:
    st = {"ok": 0, "mismatch": 0, "unverified": 0, "tripped": False, "verified": [],
          "pending": {}, "certified": dict(certified or {})}
    st.update(kw)
    return st


class _Pool:
    """us_premap by event keys; ingestion_state for the grammar class's
    key (None: the key is absent; UNREADABLE: the read raises)."""

    def __init__(self, rows, state=None):
        self.rows, self.state = rows, state

    async def fetch(self, sql, *a):
        if "us_premap" in sql:
            k = set(a[0])
            return [dict(r) for r in self.rows if set(r["event_keys"]) & k]
        return []

    async def fetchval(self, sql, *a):
        assert "ingestion_state" in sql and a[0] == premap.GRAMMAR_CERT_KEY
        if self.state is UNREADABLE:
            raise RuntimeError("state unreadable")
        return self.state


def _resolve(rows, slug, title, outcome, state=None):
    return asyncio.run(premap.resolve(_Pool(rows, state), title, None, outcome, slug))


def _explain(rows, slug, title, outcome, state=None):
    return asyncio.run(premap.resolve_explain(_Pool(rows, state), title, None, outcome, slug))


def _short(h):
    if not h:
        return None
    return (h["market_slug"], h["outcome"], h["intent"], h["matched_by"])


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


CERTIFIED = _state({AEC_WW: CERT_WW})


# ------------------------------------------ 1. the multi-word school code

class TestTheMultiWordCode:
    def test_the_pins(self):
        cw = map_lane.code_words
        assert cw("scarst", "South Carolina State")        # s|car|st
        assert cw("flam", "Florida A&M")                   # fl|am, A&M folded to am
        assert cw("washst", "Washington State")            # wash|st
        assert cw("michst", "Michigan State")              # mich|st
        assert cw("ohiost", "Ohio State")                  # ohio|st
        assert cw("tam", "Texas A&M")                      # t|am
        # the NEGATIVE: 'tex' over Texas|Tech leaves Tech unused -- the
        # split rule refuses; the whole-word rule's own verdict (a
        # first-word prefix hit) is untouched, C1's pin
        assert not cw("tex", "Texas Tech") and map_lane.code_hits("tex", "Texas Tech")
        # every C1 pin keeps its verdict on the C1 helper
        assert not map_lane.code_hits("aubrn", "Auburn") and not map_lane.code_hits("mist", "Michigan State")
        assert not map_lane.code_hits("aubrn", "A&M Aggies") and not map_lane.code_hits("tam", "A&M")

    def test_what_the_rule_refuses(self):
        cw = map_lane.code_words
        assert not cw("bayl", "Baylor"), "one word: the first-word rule owns it"
        assert not cw("aubrn", "Auburn") and not cw("aubrn", "Sooners")
        assert not cw("sca", "South Carolina State"), "a word skipped (State unused)"
        assert not cw("sst", "South Carolina State"), "a word skipped (Carolina unused)"
        assert not cw("scarolina", "South Carolina State"), "State unused"
        assert not cw("ab", "Alpha Beta"), "codes under three letters never"
        assert not cw("abab", "Aba Bab"), "two splits (a|bab, ab|ab): not unique"
        assert not cw("scar", "South Carolina State"), "State unused"
        assert not cw("xcarst", "South Carolina State"), "every letter accounted for, in order"
        assert not cw("scarst", "Carolina South State"), "consecutive words in order, from the first"
        assert not cw("scarst", None) and not cw("", "South Carolina State")

    def test_code_reads_keeps_every_c1_and_c3_verdict(self):
        cr = map_lane.code_reads
        # names neither by the C1/C3 rules: the split rule answers
        assert cr("scarst", "South Carolina State", "flam") and not cr("flam", "South Carolina State", "scarst")
        assert cr("flam", "Florida A&M", "scarst") and not cr("scarst", "Florida A&M", "flam")
        # the C1 collision: 'Michigan State' names mich by its first word;
        # mist is NOT reached by the split rule because mich was named
        assert map_lane.code_words("mist", "Michigan State")
        assert cr("mich", "Michigan State", "mist") and not cr("mist", "Michigan State", "mich")
        # the C3 stem and whole-first-word pins, verbatim through code_reads
        assert cr("washst", WSU, "wash") and not cr("wash", WSU, "washst")
        assert cr("wash", UW, "washst") and not cr("washst", UW, "wash")
        assert cr("texas", "Texas Tech", "tex") and not cr("tex", "Texas Tech", "texas")
        assert not cr("aubrn", "A&M Aggies", "tam") and not cr("tam", "A&M Aggies", "aubrn")
        assert not cr("bayl", "Sooners", "aubrn") and not cr("aubrn", "Sooners", "bayl")


# ------------------------------------------------- 2. the moneyline row

class TestTheMoneylineBySplitCode:
    MK = {"slug": AEC_SF, "closed": False,
          "question": "Who will win in the upcoming football event Bulldogs vs Rattlers …",
          "marketSides": [{"identifier": AEC_SF, "description": "Bulldogs", "long": True},
                          {"identifier": AEC_SF, "description": "Rattlers", "long": False}]}

    def test_south_carolina_state_names_scarst(self):
        hit, why = map_lane.aec_code_side(SF, "South Carolina State", self.MK, 0)
        assert why is None and (hit["outcome"], hit["intent"], hit["side_index"]) == ("Bulldogs", LONG, 0)
        hit, why = map_lane.aec_code_side(SF, "Florida A&M", self.MK, 1)
        assert why is None and (hit["outcome"], hit["intent"], hit["side_index"]) == ("Rattlers", SHORT, 1)
        # the index still decides against a swapped feed
        assert map_lane.aec_code_side(SF, "South Carolina State", self.MK, 1)[1] == "side_code_conflict"
        assert map_lane.aec_code_side(SF, "Florida A&M", self.MK, 0)[1] == "side_code_conflict"
        # the pair agrees both ways; a mascot never votes; a stranger is unmatched
        assert map_lane.pair_agrees(SF, 0, "Florida A&M", 1) is None
        assert map_lane.pair_agrees(SF, 1, "South Carolina State", 0) is None
        assert map_lane.aec_code_side(SF, "Bulldogs", self.MK, 0)[1] == "side_code_unmatched"
        assert map_lane.aec_code_side(SF, "Sooners", self.MK, 0)[1] == "side_code_unmatched"

    def test_the_shadow_maps_it_as_grammar(self, monkeypatch):
        from tests.test_mirror_maps_the_copy_lane import _fill
        from tests.test_mirror_shadow import CID
        from tests.test_mirror_shadow import _Pool as _ShadowPool

        fills = [_fill("tok-scs", "BUY", 1000.0, 0.48, 1000, market_title=EVENTS[AEC_SF],
                       event_title=EVENTS[AEC_SF], event_slug=SF, market_slug=SF,
                       outcome="South Carolina State", outcome_index=0),
                 _fill("tok-fam", "BUY", 10.0, 0.52, 1100, market_title=EVENTS[AEC_SF],
                       event_title=EVENTS[AEC_SF], event_slug=SF, market_slug=SF,
                       outcome="Florida A&M", outcome_index=1)]
        venue = _Venue({AEC_SF: self.MK})
        _with_venue(monkeypatch, venue)
        monkeypatch.setattr(ms, "_map_cache", {})
        out: dict = {}
        m = asyncio.run(ms.map_market(_ShadowPool(fills=fills, mapped=False), fills, venue,
                                      whale="rn1", condition_id=CID, out=out))
        assert m == {"us_slug": AEC_SF, "long_asset": "tok-scs", "other_asset": "tok-fam",
                     "source": "grammar"}
        assert "refusal" not in out


# ----------------------------------------------- 3. the mascot spread

class TestTheMascotSpreadChain:
    def test_the_feeds_rows_under_the_certification(self, armed):
        rows = _board()
        for _at, _usd, slug, title, outcome in HIS_ROWS:
            ln = slug.rsplit("-", 1)[-1]
            h = _resolve(rows, slug, title, outcome, CERTIFIED)
            # Washington -L with outcome Washington State = WSU +L = the
            # Cougars (a) cover +L: pos-L YES, the venue's long side
            assert _short(h) == (f"asc-{WW}-pos-{ln}", "yes", LONG, "premap_spread_code"), slug
            ex = _explain(rows, slug, title, outcome, CERTIFIED)
            assert ex["step"] == "resolves" and ex["matched_by"] == "premap_spread_code"
            assert ex["c3"]["subject_via"] == "code" and ex["c3"]["his_team"] == "a"
            assert ex["c3"]["his_signed"] == "+" + ln.replace("pt", ".")
            assert ex["c3"]["subject"] == {"certified": "cougars", "code": "washst"}
            assert ex["c3"]["code_hits"] == ["washst"] and ex["c3"]["title_hits"] == ["wash"]
        # the two small rows with outcome "Washington" under the SAME title
        # name one team twice: no pair witness (review fold, HIGH-1) --
        # UW -L would be pos-L no, and the chain does not say so
        for ln in ("2pt5", "1pt5"):
            slug = f"{WW}-spread-home-{ln}"
            assert _resolve(rows, slug, f"Spread: Washington (-{ln.replace('pt', '.')})", UW, CERTIFIED) is None
            ex = _explain(rows, slug, f"Spread: Washington (-{ln.replace('pt', '.')})", UW, CERTIFIED)
            assert ex["split"] == "spread:pair-unwitnessed"

    @pytest.mark.parametrize("outcome,title,want_id,want_side,want_intent", [
        (WSU, f"Spread: {UW} (-23.5)", "pos", "yes", LONG),      # title names b at -L: a has +L
        (WSU, f"Spread: {UW} (+23.5)", "neg", "yes", LONG),      # title names b at +L: a has -L
        (UW, f"Spread: {WSU} (-23.5)", "neg", "no", SHORT),      # title names a at -L: b has +L
        (UW, f"Spread: {WSU} (+23.5)", "pos", "no", SHORT),      # title names a at +L: b has -L
    ])
    def test_the_c3_table_through_the_code(self, armed, outcome, title, want_id, want_side, want_intent):
        # the four rows where his title names the OTHER team: the pair
        # witness holds (washst and wash, two positions) and C3's table
        # flips the sign for the title's team
        h = _resolve(_board(), f"{WW}-spread-home-23pt5", title, outcome, CERTIFIED)
        assert _short(h) == (f"asc-{WW}-{want_id}-23pt5", want_side, want_intent, "premap_spread_code")

    @pytest.mark.parametrize("outcome,title", [
        (WSU, f"Spread: {WSU} (-23.5)"), (WSU, f"Spread: {WSU} (+23.5)"),
        (UW, f"Spread: {UW} (-23.5)"), (UW, f"Spread: {UW} (+23.5)"),
    ])
    def test_a_title_naming_his_own_team_is_no_witness(self, armed, outcome, title):
        # the other four rows of C3's table: one name, read twice, is no
        # pair -- the stem rule happens to read washst/wash right on one
        # name, and the chain cannot know that (TestThePairWitness)
        assert _resolve(_board(), f"{WW}-spread-home-23pt5", title, outcome, CERTIFIED) is None
        ex = _explain(_board(), f"{WW}-spread-home-23pt5", title, outcome, CERTIFIED)
        assert (ex["step"], ex["split"]) == ("no_side_match", "spread:pair-unwitnessed")

    def test_the_other_sides_record_certifies_the_same_subject(self, armed):
        # the class certified Huskies at position 1 (wash): the question's
        # subject is the other name, a by elimination -- the same map
        st = _state({AEC_WW: CERT_UW})
        h = _resolve(_board(), f"{WW}-spread-home-23pt5", "Spread: Washington (-23.5)", WSU, st)
        assert _short(h) == (f"asc-{WW}-pos-23pt5", "yes", LONG, "premap_spread_code")

    def test_the_source_is_premap(self, armed, monkeypatch):
        fills = [{"asset": "tok-a", "market_slug": f"{WW}-spread-home-23pt5",
                  "market_title": "Spread: Washington (-23.5)", "event_title": None, "outcome": WSU,
                  "outcome_index": 0, "side": "BUY", "size": 10.0, "price": 0.5}]
        m = asyncio.run(ms.map_market(_Pool(_board(), CERTIFIED), fills))
        assert m and m["source"] == "premap" and m["us_slug"] == f"asc-{WW}-pos-23pt5"


class TestTheSubjectCertification:
    S = f"{WW}-spread-home-23pt5"
    T = "Spread: Washington (-23.5)"

    def _refused(self, rows, state, subject, split="spread:subject-uncertified"):
        assert _resolve(rows, self.S, self.T, WSU, state) is None
        ex = _explain(rows, self.S, self.T, WSU, state)
        assert (ex["step"], ex["split"]) == ("no_side_match", split), ex.get("c3")
        assert ex["c3"]["subject"] == subject, ex["c3"]
        assert ex["c3"]["venue_names"] == ["cougars", "huskies"]
        return ex

    def test_tonight_no_record_no_map(self, armed):
        # 22:22Z: mirror_grammar_echo ok=0, nothing certified -- the key
        # absent, or present with nothing for this event: uncertified
        self._refused(_board(), None, "grammar-uncertified")
        self._refused(_board(), _state(), "grammar-uncertified")
        self._refused(_board(), _state({AEC_SF: dict(CERT_WW, outcome_desc="Bulldogs", his_slug=SF)}),
                      "grammar-uncertified")
        self._refused(_board(), _state({AEC_WW: "Cougars"}), "grammar-uncertified")
        st = _state()
        st["certified"] = ["Cougars"]
        self._refused(_board(), st, "grammar-uncertified")

    def test_the_state_unreadable_or_tripped(self, armed):
        self._refused(_board(), UNREADABLE, "grammar-unreadable")
        self._refused(_board(), "not a dict", "grammar-unreadable")
        self._refused(_board(), _state({AEC_WW: CERT_WW}, tripped=True), "grammar-tripped")
        self._refused(_board(), _state({AEC_WW: CERT_WW}, mismatch=1), "grammar-tripped")

    def test_the_aec_row_must_list_the_questions_two_names(self, armed):
        self._refused(_board(_venue(aec=False)), CERTIFIED, "aec-absent")
        other = (AEC_WW, _aec(AEC_WW, "Who will win …", "Cougars", "Wildcats"))
        self._refused(_board(_venue(aec=False), extra=[other]), CERTIFIED, "aec-names")
        swapped = (AEC_WW, _aec(AEC_WW, "Who will win …", "Huskies", "Cougars"))
        # the aec side ORDER is never read: the names are a set
        h = _resolve(_board(_venue(aec=False), extra=[swapped]), self.S, self.T, WSU, CERTIFIED)
        assert _short(h) == (f"asc-{WW}-pos-23pt5", "yes", LONG, "premap_spread_code")

    def test_a_record_placing_the_subject_elsewhere_is_a_conflict(self, armed):
        # the venue certified 'Huskies' as washst (position 0): the
        # question's subject 'Cougars' would be b -- the grammar never
        # has b as the subject, so this is a contradiction, not a map
        st = _state({AEC_WW: dict(CERT_WW, outcome_desc="Huskies")})
        ex = self._refused(_board(), st, {"certified": "huskies", "code": "washst", "slot": 1},
                           "spread:subject-conflict")
        assert ex["c3"]["aec"]["sides"] == ["cougars", "huskies"]
        st = _state({AEC_WW: dict(CERT_UW, outcome_desc="Cougars")})
        self._refused(_board(), st, {"certified": "cougars", "code": "wash", "slot": 0},
                      "spread:subject-conflict")

    def test_a_record_of_another_shape_is_no_record(self, armed):
        for rec in (dict(CERT_WW, outcome_desc="Bears"), dict(CERT_WW, side_index=2),
                    dict(CERT_WW, his_slug=SF), dict(CERT_WW, his_slug=None)):
            self._refused(_board(), _state({AEC_WW: rec}), "record-shape")

    def test_the_class_counters_certify_nothing(self, armed):
        # review fold 2026-09-06 (MEDIUM, mutant c): a class that has
        # certified OTHER markets (ok >= 1) holds no record for this one --
        # the bar is per market, the counters are not a record
        self._refused(_board(), _state(ok=5), "grammar-uncertified")
        self._refused(_board(), _state(ok=5, verified=[1, 2, 3], unverified=2), "grammar-uncertified")
        self._refused(_board(), _state({AEC_SF: dict(CERT_WW, outcome_desc="Bulldogs", his_slug=SF)}, ok=5),
                      "grammar-uncertified")
        his = premap.c3_his(f"{WW}-spread-home-23pt5")
        board = [{"identifier": AEC_WW, "side_norm": "cougars"}, {"identifier": AEC_WW, "side_norm": "huskies"}]
        trace: dict = {}
        assert premap._c4_subject_certified(his, "cfb", ("cougars", "huskies"), board, _state(ok=5), trace) \
            == "spread:subject-uncertified"
        assert trace["subject"] == "grammar-uncertified"


class TestThePairWitness:
    """Review fold 2026-09-06, HIGH-1: the code rules read each name
    alone, and a first-word prefix names ONE code for two schools ('Ole
    Miss' by the whole word, 'Mississippi State' by its first word; msst
    is named by nothing). The chain resolved his outcome and his title's
    team independently, both landed on miss, the sign was not flipped and
    the Rebels' side was bought for a Mississippi State fill. The pair
    witness: his outcome and his title's team must resolve to the two
    DIFFERENT positions -- C1's pair rule, where an unnamed sibling
    corroborates nothing. Refused before any certification is read."""

    # (a, b, mascots, his event, line, his title, his outcome, the one code both names read)
    COLLISIONS = [
        ("miss", "msst", "Rebels", "Bulldogs", "Ole Miss vs. Mississippi State", "7pt5",
         "Spread: Ole Miss (-7.5)", "Mississippi State", "miss"),
        ("miss", "msst", "Rebels", "Bulldogs", "Ole Miss vs. Mississippi State", "7pt5",
         "Spread: Mississippi State (-7.5)", "Ole Miss", "miss"),
        ("mich", "mist", "Wolverines", "Spartans", "Michigan vs. Michigan State", "17pt5",
         "Spread: Michigan (-17.5)", "Michigan State", "mich"),
        ("mich", "mist", "Wolverines", "Spartans", "Michigan vs. Michigan State", "17pt5",
         "Spread: Michigan State (-17.5)", "Michigan", "mich"),
        ("ala", "aam", "Crimson Tide", "Bulldogs", "Alabama vs. Alabama A&M", "40pt5",
         "Spread: Alabama (-40.5)", "Alabama A&M", "ala"),
        ("tex", "tam", "Longhorns", "Aggies", "Texas vs. Texas A&M", "3pt5",
         "Spread: Texas (-3.5)", "Texas A&M", "tex"),
        # the stem rule never reads a one-letter residual: 'Arkansas State' reads ark
        ("ark", "arks", "Razorbacks", "Red Wolves", "Arkansas vs. Arkansas State", "14pt5",
         "Spread: Arkansas (-14.5)", "Arkansas State", "ark"),
        # the split rule (flam = fl|a|m) is never reached once the first-word
        # rule names the other code (C1's precedence): 'Florida A&M' reads flor
        ("flor", "flam", "Gators", "Rattlers", "Florida vs. Florida A&M", "30pt5",
         "Spread: Florida (-30.5)", "Florida A&M", "flor"),
        # the whole-word rule collides too: 'Southern Miss' reads miss (b) beside smiss
        ("smiss", "miss", "Golden Eagles", "Rebels", "Southern Miss vs. Ole Miss", "10pt5",
         "Spread: Ole Miss (-10.5)", "Southern Miss", "miss"),
    ]

    @pytest.mark.parametrize("a,b,ma,mb,ev,ln,title,outcome,code", COLLISIONS)
    def test_two_names_on_one_code_never_map(self, armed, a, b, ma, mb, ev, ln, title, outcome, code):
        ww, rows, st = _game(a, b, ma, mb, ev, (ln,))
        slug = f"{ww}-spread-home-{ln}"
        assert _resolve(rows, slug, title, outcome, st) is None
        ex = _explain(rows, slug, title, outcome, st)
        assert (ex["step"], ex["split"]) == ("no_side_match", "spread:code-collision"), ex["c3"]
        assert ex["c3"]["code_hits"] == ex["c3"]["title_hits"] == [code]
        assert "subject" not in ex["c3"] and "subject_via" not in ex["c3"]
        # by name, before the state: unreadable or absent state changes nothing
        assert _explain(rows, slug, title, outcome, UNREADABLE)["split"] == "spread:code-collision"
        assert _explain(rows, slug, title, outcome, None)["split"] == "spread:code-collision"

    def test_one_name_is_no_witness(self, armed):
        # 'Mississippi State' alone reads miss -- position 0, the Rebels:
        # a map would buy the Rebels' side of a Mississippi State fill.
        # No single name can exclude the collision, so a title naming his
        # outcome's own team refuses, on every game
        ww, rows, st = _game("miss", "msst", "Rebels", "Bulldogs", "Ole Miss vs. Mississippi State")
        slug = f"{ww}-spread-home-7pt5"
        for title, outcome in (("Spread: Mississippi State (-7.5)", "Mississippi State"),
                               ("Spread: Ole Miss (-7.5)", "Ole Miss"),
                               ("Spread: Mississippi State (+7.5)", "Mississippi State")):
            assert _resolve(rows, slug, title, outcome, st) is None
            ex = _explain(rows, slug, title, outcome, st)
            assert (ex["step"], ex["split"]) == ("no_side_match", "spread:pair-unwitnessed"), (title, ex["c3"])
            assert ex["c3"]["code_hits"] == ex["c3"]["title_hits"] == ["miss"]
            assert "subject" not in ex["c3"]
        # the same on washst/wash, where the one name happens to read right
        ex = _explain(_board(), f"{WW}-spread-home-23pt5", f"Spread: {WSU} (-23.5)", WSU, CERTIFIED)
        assert ex["split"] == "spread:pair-unwitnessed" and ex["c3"]["code_hits"] == ["washst"]

    def test_two_names_on_two_positions_map(self, armed):
        # the witness holds: his outcome on one code, his title's team on
        # the other. washst/wash by the stem rule (the feed's own rows,
        # TestTheMascotSpreadChain); scarst/flam by the split rule on the
        # mascot rows Bulldogs/Rattlers, certified {Bulldogs, 0}
        st = _state({AEC_SF: {"outcome_desc": "Bulldogs", "side_index": 0, "his_slug": SF, "at": 1.0}})
        h = _resolve(_board(), f"{SF}-spread-away-21pt5", "Spread: Florida A&M (+21.5)", "South Carolina State", st)
        assert _short(h) == (f"asc-{SF}-neg-21pt5", "yes", LONG, "premap_spread_code")
        ex = _explain(_board(), f"{SF}-spread-away-21pt5", "Spread: Florida A&M (+21.5)", "South Carolina State", st)
        assert ex["c3"]["code_hits"] == ["scarst"] and ex["c3"]["title_hits"] == ["flam"]
        assert ex["c3"]["his_team"] == "a" and ex["c3"]["his_signed"] == "-21.5"
        # his Florida A&M at +21.5 (b, +L): the Bulldogs do not cover -21.5 -- neg-L no
        h = _resolve(_board(), f"{SF}-spread-away-21pt5", "Spread: South Carolina State (-21.5)", "Florida A&M", st)
        assert _short(h) == (f"asc-{SF}-neg-21pt5", "no", SHORT, "premap_spread_code")
        # and a game whose two names read two codes with no stem and no
        # split: Michigan / Ohio State -> mich / ohiost
        ww, rows, st = _game("mich", "ohiost", "Wolverines", "Buckeyes", "Michigan vs. Ohio State", ("3pt5",))
        h = _resolve(rows, f"{ww}-spread-home-3pt5", "Spread: Ohio State (-3.5)", "Michigan", st)
        assert _short(h) == (f"asc-{ww}-pos-3pt5", "yes", LONG, "premap_spread_code")

    def test_the_refusal_names_are_the_chains_own(self):
        src = inspect.getsource(premap._c4_subject_by_code)
        assert "spread:code-collision" in src and "spread:pair-unwitnessed" in src
        assert src.index("pair-unwitnessed") < src.index("_c4_subject_certified("), \
            "the witness is read before any certification"


class TestTheCodeStep:
    def test_his_outcome_must_name_one_code(self, armed):
        rows = _board()
        slug, title = f"{WW}-spread-home-23pt5", "Spread: Washington (-23.5)"
        ex = _explain(rows, slug, title, "Sooners", CERTIFIED)
        assert ex["split"] == "spread:code-unmatched" and ex["c3"]["code_hits"] == []
        assert _resolve(rows, slug, title, "Sooners", CERTIFIED) is None
        # a rendering naming both codes as whole words (no stem to share):
        # unmatched too -- refused before any certification is read
        ex = _explain(rows, f"{SF}-spread-away-21pt5", "Spread: South Carolina State (-21.5)",
                      "scarst flam", CERTIFIED)
        assert ex["split"] == "spread:code-unmatched" and ex["c3"]["code_hits"] == ["scarst", "flam"]
        # under the stem, 'washst wash' names washst by its whole first
        # word and NOT wash (the C3 rule): a hit, not an ambiguity
        assert map_lane.code_reads("washst", "washst wash", "wash") \
            and not map_lane.code_reads("wash", "washst wash", "washst")
        # a mascot in his outcome names no code (the dictionary does not
        # exist); on a mascot row it is C3's NAME path -- his outcome names
        # the question's team -- and his school-named title then fails there
        assert not map_lane.code_reads("washst", "Cougars", "wash") \
            and not map_lane.code_reads("wash", "Cougars", "washst")
        ex = _explain(rows, slug, title, "Cougars", CERTIFIED)
        assert ex["split"] == "spread:title-unreadable" and "subject_via" not in ex["c3"]

    def test_his_title_must_name_one_code(self, armed):
        rows = _board()
        slug = f"{WW}-spread-home-23pt5"
        assert _explain(rows, slug, "Spread: Huskies (-23.5)", WSU, CERTIFIED)["split"] == "spread:title-unreadable"
        assert _resolve(rows, slug, "Spread: Huskies (-23.5)", WSU, CERTIFIED) is None

    def test_a_venue_name_that_names_a_code_keeps_c3s_verdict(self, armed):
        # the 22.5 scarst-flam row names the schools: his stranger outcome
        # is names-unreadable by C3's own step, the code chain never runs
        rows = _board()
        ex = _explain(rows, f"{SF}-spread-away-22pt5", "Spread: South Carolina State (-22.5)", "Sooners", CERTIFIED)
        assert ex["split"] == "spread:names-unreadable" and "code_hits" not in ex["c3"]
        # and the school-named rows still map by NAME, label premap_spread
        h = _resolve(rows, f"{SF}-spread-away-22pt5", "Spread: South Carolina State (-22.5)",
                     "South Carolina State", None)
        assert _short(h) == (f"asc-{SF}-neg-22pt5", "yes", LONG, "premap_spread")

    def test_c3s_other_refusals_stand(self, armed):
        rows = _board()
        # the line byte for byte: 19.5 is not 21.5 (the mascot rows) or 22.5
        ex = _explain(rows, f"{SF}-spread-away-19pt5", "Spread: South Carolina State (-19.5)",
                      "South Carolina State", CERTIFIED)
        assert ex["split"] == "spread:line-absent"
        # whole numbers refuse before any row is read
        assert _explain(rows, f"{WW}-spread-home-23", "Spread: Washington (-23)", WSU,
                        CERTIFIED)["split"] == "spread:whole-number"
        # segments never cross: the full-game slug never takes the 1h row
        # (school-named) and a first-half slug never a full-game row
        only_seg = [r for r in rows if not (r["identifier"].startswith(f"asc-{WW}-neg")
                                            or r["identifier"].startswith(f"asc-{WW}-pos"))]
        ex = _explain(only_seg, f"{WW}-spread-home-3pt5", "Spread: Washington (-3.5)", WSU, CERTIFIED)
        assert (ex["step"], ex["split"]) == ("unknown_market_type", "spread:segment-absent")
        assert _resolve(only_seg, f"{WW}-spread-home-3pt5", "Spread: Washington (-3.5)", WSU, CERTIFIED) is None
        ex = _explain(rows, f"{WW}-first-half-spread-home-23pt5", "1st Half Spread: Washington (-23.5)",
                      WSU, CERTIFIED)
        assert (ex["step"], ex["split"]) == ("unknown_market_type", "spread:segment-absent")
        # the intent is the venue's own
        flipped = [dict(r, intent=SHORT) if r["identifier"] == f"asc-{WW}-pos-23pt5" and r["side_norm"] == "yes"
                   else r for r in rows]
        assert _explain(flipped, f"{WW}-spread-home-23pt5", "Spread: Washington (-23.5)", WSU,
                        CERTIFIED)["split"] == "spread:intent"


# ------------------------------------------- 4. the seams and the switch

class TestTheSeams:
    def test_explain_unmapped_prints_the_chain_refusal(self, armed):
        p = _Pool(_board(), None)
        ctx = {"title": "Spread: Washington (-23.5)", "event_title": None, "outcome": WSU,
               "his_slug": f"{WW}-spread-home-23pt5"}
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "no_side_match:spread:subject-uncertified"
        p = _Pool(_board(), CERTIFIED)
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "resolves"
        ctx["outcome"] = "Sooners"
        assert asyncio.run(ms.explain_unmapped(p, ctx)) == "no_side_match:spread:code-unmatched"

    def test_the_pick_stays_pure_and_the_state_is_read_by_the_callers(self):
        for fn in (premap.c3_pick, premap._c3_pick_spread, premap._c4_subject_by_code,
                   premap._c4_subject_certified):
            src = inspect.getsource(fn)
            for forbidden in ("await", "pool", "_get_client", "os.getenv", "SequenceMatcher"):
                assert forbidden not in src, (fn.__name__, forbidden)
        src = inspect.getsource(premap.resolve)
        assert "await grammar_cert(pool)" in src and "board=rows, cert=cert" in src
        src = inspect.getsource(premap.resolve_explain)
        assert "await grammar_cert(pool)" in src and "board=rows, cert=cert" in src
        assert premap.GRAMMAR_CERT_KEY == ml.GRAMMAR_STATE_KEY == "mirror_grammar_echo"

    def test_off_the_switch_nothing_here_runs(self, monkeypatch):
        monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)
        ex = _explain(_board(), f"{WW}-spread-home-23pt5", "Spread: Washington (-23.5)", WSU, CERTIFIED)
        assert ex["step"] == "no_side_match" and "c3" not in ex and ex["c3_on"] is False
        assert _resolve(_board(), f"{WW}-spread-home-23pt5", "Spread: Washington (-23.5)", WSU, CERTIFIED) is None

    def test_no_state_read_for_the_other_families(self, armed):
        class _NoState(_Pool):
            async def fetchval(self, sql, *a):
                raise AssertionError("a total never reads the grammar state")

        rows = _board()
        h = asyncio.run(premap.resolve(_NoState(rows, None), "Spread: Washington (-23.5)", None, WSU,
                                       f"{WW}-total-54pt5"))
        assert h is None


# ------------------------------------ 5. the live worker's record

def test_the_live_worker_records_the_venue_truth_for_the_spread(monkeypatch):
    """The C1 end-to-end open (test_c1_round2): at venue-truth ok the class
    now also keeps `certified[aec slug]` -- the per-side contract named
    'Bears' for bayl at position 0 -- and that record survives the fill
    echo popping `pending`. It is exactly what _c4_subject_certified
    reads."""
    from sportsassets import live_executor as le
    from sportsassets.analytics import mirror_live_rules as rules
    from tests.test_mirror_shadow import CID

    _cert_env(monkeypatch)
    venue = _Venue({**_aec_cfb(), **_atc_bayl()})
    _with_venue(monkeypatch, venue)
    pool = _live_pool(fills=_cfb_fills(), mapped=False, snap={"tok-bayl": 1000.0, "tok-aubrn": 10.0},
                      snap_at=5000.0 - 40)
    # a state already carrying more records than the bound, THIS event's
    # (stale, from an earlier night) the oldest of them: the write keeps
    # the newest _GRAMMAR_VERIFIED_MAX and tonight's certification is
    # the newest (review fold, LOW)
    stale = dict(CERT_WW, outcome_desc="Tigers", side_index=1, his_slug=CFB, at=1.0)
    pool.state["mirror_grammar_echo"] = _state({AEC_CFB: stale,
                                                **{f"aec-old-{i}": CERT_WW
                                                   for i in range(ml._GRAMMAR_VERIFIED_MAX + 5)}})
    pool.state["mapping_quarantine"] = True
    pool.state["premap_live"] = True
    pool.state["side_echo_last"] = {"ok": 2012}
    monkeypatch.setattr(rules, "admission", lambda f, increase=False: None)

    async def _capture(*a, **k):
        return {"ok": False, "refusal": "captured"}

    monkeypatch.setattr(le, "_open_mirror_book", _capture)
    t = _live_tick(monkeypatch, pool, venue)
    t.http = _Http(rows=[{"conditionId": CID, "asset": "tok-bayl", "size": 1000},
                         {"conditionId": CID, "asset": "tok-aubrn", "size": 10}])
    asyncio.run(ml._tick_candidate(t, "rn1", CID))
    st = pool.state["mirror_grammar_echo"]
    assert ATC_BAYL in venue.slug_calls and st["ok"] == 1
    rec = st["certified"][AEC_CFB]
    assert (rec["outcome_desc"], rec["side_index"], rec["his_slug"]) == ("Bears", 0, CFB)
    assert len(st["certified"]) == ml._GRAMMAR_VERIFIED_MAX and "aec-old-0" not in st["certified"]
    assert list(st["certified"])[-1] == AEC_CFB, "re-certified tonight: the newest record"
    assert "aec-old-5" not in st["certified"] and "aec-old-6" in st["certified"]
    assert st["pending"][AEC_CFB]["outcome_desc"] == "Bears"
    # the spread pick reads exactly this shape
    trace: dict = {}
    his = premap.c3_his("cfb-bayl-aubrn-2026-09-05-spread-home-3pt5")
    board = [{"identifier": AEC_CFB, "side_norm": "bears"}, {"identifier": AEC_CFB, "side_norm": "tigers"}]
    assert premap._c4_subject_certified(his, "cfb", ("bears", "tigers"), board, st, trace) is None
    assert trace["subject"] == {"certified": "bears", "code": "bayl"}
    assert premap._c4_subject_certified(his, "cfb", ("tigers", "bears"), board, st, trace) \
        == "spread:subject-conflict"
    ml._current_stats = None


def test_a_market_certified_again_is_the_newest_record():
    """Review fold 2026-09-06 (LOW): re-inserting under an existing key
    kept its old position, so a market certified tonight that was also
    certified long ago sat at the front and was the first evicted."""
    old = {f"aec-old-{i}": dict(CERT_WW, at=float(i)) for i in range(ml._GRAMMAR_VERIFIED_MAX)}
    st = _state(old)
    ml._grammar_certify(st, "aec-old-0", dict(CERT_WW, at=999.0))
    keys = list(st["certified"])
    assert keys[-1] == "aec-old-0" and keys[0] == "aec-old-1" and len(keys) == ml._GRAMMAR_VERIFIED_MAX
    assert st["certified"]["aec-old-0"]["at"] == 999.0
    ml._grammar_certify(st, "aec-new", dict(CERT_WW, at=1000.0))
    keys = list(st["certified"])
    assert keys[-2:] == ["aec-old-0", "aec-new"] and "aec-old-1" not in keys
    assert len(keys) == ml._GRAMMAR_VERIFIED_MAX
    # the reviewer's probe, verbatim: under the old write aec-old-0 was evicted
    # and aec-new kept; now both are kept
    assert "aec-old-0" in st["certified"] and "aec-new" in st["certified"]
    # a state whose 'certified' is not a dict starts afresh; an absent key too
    st = _state()
    st["certified"] = ["Cougars"]
    ml._grammar_certify(st, AEC_WW, CERT_WW)
    assert st["certified"] == {AEC_WW: CERT_WW}
    st = {"ok": 0}
    ml._grammar_certify(st, AEC_WW, CERT_WW)
    assert st["certified"] == {AEC_WW: CERT_WW}
    assert "_grammar_certify(st, slug," in inspect.getsource(ml._grammar_admission)


# ------------------------------ 6. the per-side contract's suffix (HIGH-2)

class TestTheContractSuffixIsNeverTheOtherSides:
    """Review fold 2026-09-06, HIGH-2 (inherited from C1 round 3, now load-
    bearing): with only atc-cfb-washst-wash-2026-09-06-wash 'Will the
    Cougars win?' in us_premap, `by_question` fitted the OTHER code's
    contract to washst (i=0); grammar_truth read the same contract naming
    'Cougars' and said ok -> certified[aec] = {Cougars, 0}: every washst-
    wash spread would place the Cougars at washst and the moneyline buy
    side 0 for Washington State, on the venue's row for wash. A suffix
    that is the other code, or prefixes it, is never ours by its
    question."""

    ROW_WASH, ROW_WASHST = f"atc-{WW}-wash", f"atc-{WW}-washst"

    def test_the_other_codes_contract_never_fits_by_its_question(self):
        rows = [_row(self.ROW_WASH, "Will the Cougars win?")]
        run = lambda *a: asyncio.run(ml._contract_candidates(_PremapPool(rows), *a))  # noqa: E731
        assert run(WW, 0, "Cougars", "Huskies") == []
        assert run(WW, 0, "Cougars") == [], "the legacy caller (no other description) too"
        # for wash (i=1) that row IS the code's own contract, by code; the
        # truth check then reads its name (below)
        assert run(WW, 1, "Huskies", "Cougars") == [self.ROW_WASH]
        # his own code's contract naming the mascot: by code, as before
        rows = [_row(self.ROW_WASHST, "Will the Cougars win?")]
        assert run(WW, 0, "Cougars", "Huskies") == [self.ROW_WASHST]
        assert run(WW, 1, "Huskies", "Cougars") == []
        # a suffix prefixing BOTH codes with a question naming our mascot:
        # it fits the other side as much as ours -- never by question
        rows = [_row("atc-cfb-boise-bost-2026-09-05-bo", "Will the Broncos win?")]
        assert run("cfb-boise-bost-2026-09-05", 0, "Broncos", "Terriers") == []
        assert run("cfb-boise-bost-2026-09-05", 1, "Terriers", "Broncos") == []
        # a suffix that EXTENDS the other code ('texas' beside tex; v3
        # LOW-3, the reviewer's note 3): not ours by question either; the
        # same suffix beside HIS code is the venue's longer rendering of
        # it and fits by its question (never by code)
        rows = [_row("atc-cfb-tam-tex-2026-09-06-texas", "Will the Aggies win?")]
        assert run("cfb-tam-tex-2026-09-06", 0, "Aggies", "Longhorns") == []
        assert run("cfb-tam-tex-2026-09-06", 1, "Longhorns", "Aggies") == []
        rows = [_row("atc-cfb-tam-tex-2026-09-06-texas", "Will the Longhorns win?")]
        assert run("cfb-tam-tex-2026-09-06", 1, "Longhorns", "Aggies") == ["atc-cfb-tam-tex-2026-09-06-texas"]
        assert run("cfb-tam-tex-2026-09-06", 0, "Aggies", "Longhorns") == []
        rows = [_row("atc-cfb-tam-tex-2026-09-06-tex", "Will the Aggies win?")]
        assert run("cfb-tam-tex-2026-09-06", 0, "Aggies", "Longhorns") == []
        # a stem pair the other way: the OTHER code prefixes the suffix, not
        # the reverse -- 'washst' beside wash (i=1) fits neither by code nor
        # by question, and the venue's short code that fits neither code
        # (C1 round 3's 'bu' for Baylor) still fits by its question alone
        rows = [_row("atc-cfb-bayl-aubrn-2026-09-05-bu", "Will the Bears win?")]
        assert run("cfb-bayl-aubrn-2026-09-05", 0, "Bears", "Tigers") == ["atc-cfb-bayl-aubrn-2026-09-05-bu"]
        assert run("cfb-bayl-aubrn-2026-09-05", 1, "Tigers", "Bears") == []

    def _admission(self, monkeypatch, i, desc, other):
        _cert_env(monkeypatch)
        con = self.ROW_WASH
        table = {AEC_WW: dict(_aec(AEC_WW, "Who will win in the upcoming football event Cougars vs Huskies …",
                                   "Cougars", "Huskies"), closed=False, title=EVENTS[AEC_WW]),
                 con: {"slug": con, "closed": False, "outcome": "Cougars", "title": "Cougars"}}
        g = {"his_slug": WW, "side_index": i, "outcome_desc": desc, "intent": LONG if i == 0 else SHORT,
             "slug": AEC_WW, "asset": "tok-x"}
        pool = _live_pool(fills=_cfb_fills(), mapped=False)
        t = _live_tick(monkeypatch, pool, _Venue(table))
        rows = [_row(con, "Will the Cougars win?")]

        async def _cands(p, his_slug, j, d, other_desc="", _rows=rows):
            return await _REAL_CONTRACT_CANDIDATES(_PremapPool(_rows), his_slug, j, d, other_desc)

        monkeypatch.setattr(ml, "_contract_candidates", _cands)
        why = asyncio.run(ml._grammar_admission(t, "rn1", AEC_WW, g))
        ml._current_stats = None
        return why, pool.state["mirror_grammar_echo"]

    def test_the_certification_refuses_it(self, monkeypatch):
        # exactly the reviewer's case: Cougars at washst (i=0) with only the
        # wash contract listed -- no candidate, unverified, nothing certified
        why, st = self._admission(monkeypatch, 0, "Cougars", "Huskies")
        assert why == "grammar_echo_unverified"
        assert st["ok"] == 0 and st["unverified"] == 1 and st["tripped"] is False
        assert not st.get("certified") and not st.get("pending")
        assert "0 per-side contracts fit" in st["last"]["detail"]
        # Huskies at wash (i=1): the wash contract is by code, and it names
        # 'Cougars' -- the OTHER side: a mismatch, the class trips (the
        # venue's own contradiction, caught before a dollar moves)
        why, st = self._admission(monkeypatch, 1, "Huskies", "Cougars")
        assert why == "side_echo_mismatch" and st["tripped"] is True and not st.get("certified")
        assert "names 'Cougars', the OTHER side" in st["last"]["detail"]


# ------------------ 7. a contradicting re-certification trips (v3, LOW-2)

class TestAContradictingRecertificationTrips:
    """Review fold v3 (2026-09-07, LOW-2; the reviewer's note 2): a second
    certification of the same aec market that CONTRADICTS the record the
    class holds -- Huskies at washst after Cougars at washst -- is the
    venue contradicting itself; `_grammar_certify` replaced the record
    silently and the earlier book stayed open on a side one of the two
    readings got wrong. Now `_grammar_admission` reads it as a mismatch:
    the class trips, the record is kept. The same reading is a refresh;
    the complementary side is the pair itself."""

    def _admission(self, monkeypatch, prior, sides, i, desc, contract_suffix, contract_names):
        _cert_env(monkeypatch)
        con = f"atc-{WW}-{contract_suffix}"
        table = {AEC_WW: dict(_aec(AEC_WW, "Who will win in the upcoming football event …", *sides),
                              closed=False, title=EVENTS[AEC_WW]),
                 con: {"slug": con, "closed": False, "outcome": contract_names, "title": contract_names}}
        g = {"his_slug": WW, "side_index": i, "outcome_desc": desc, "intent": LONG if i == 0 else SHORT,
             "slug": AEC_WW, "asset": "tok-x"}
        pool = _live_pool(fills=_cfb_fills(), mapped=False)
        if prior is not None:
            pool.state["mirror_grammar_echo"] = _state({AEC_WW: prior}, ok=1)
        t = _live_tick(monkeypatch, pool, _Venue(table))
        rows = [_row(con, f"Will the {contract_names} win?")]

        async def _cands(p, his_slug, j, d, other_desc="", _rows=rows):
            return await _REAL_CONTRACT_CANDIDATES(_PremapPool(_rows), his_slug, j, d, other_desc)

        monkeypatch.setattr(ml, "_contract_candidates", _cands)
        why = asyncio.run(ml._grammar_admission(t, "rn1", AEC_WW, g))
        ml._current_stats = None
        return why, pool.state["mirror_grammar_echo"]

    def test_the_same_mascot_at_the_other_position_trips(self, monkeypatch):
        # the venue re-ordered its aec sides and its washst contract now
        # names Huskies: truth says ok for Huskies at 0, the record says
        # Cougars at 0 -- one of the two is a wrong side; trip, keep the record
        why, st = self._admission(monkeypatch, CERT_WW, ("Huskies", "Cougars"), 0, "Huskies", "washst", "Huskies")
        assert why == "side_echo_mismatch" and st["tripped"] is True
        assert st["mismatch"] == 1 and st["ok"] == 1, "the truth's ok is not counted; the contradiction is"
        assert st["certified"][AEC_WW] == CERT_WW and not st.get("pending")
        assert st["last"]["verdict"] == "mismatch" and "certified record contradicted" in st["last"]["detail"]
        assert "'Cougars' at position 0" in st["last"]["detail"] and "'Huskies' at position 0" in st["last"]["detail"]

    def test_the_same_position_under_another_name_trips_and_the_pair_does_not(self, monkeypatch):
        # Cougars certified at 1 after Cougars at 0: the same mascot moved
        why, st = self._admission(monkeypatch, CERT_WW, ("Huskies", "Cougars"), 1, "Cougars", "wash", "Cougars")
        assert why == "side_echo_mismatch" and st["tripped"] is True and st["certified"][AEC_WW] == CERT_WW
        # the complementary side -- Huskies at wash after Cougars at washst --
        # is the pair itself: ok, the record moves to the newer reading
        why, st = self._admission(monkeypatch, CERT_WW, ("Cougars", "Huskies"), 1, "Huskies", "wash", "Huskies")
        assert why is None and st["tripped"] is False and st["ok"] == 2
        assert (st["certified"][AEC_WW]["outcome_desc"], st["certified"][AEC_WW]["side_index"]) == ("Huskies", 1)
        # the same reading again is a refresh; no prior record is nothing to contradict
        why, st = self._admission(monkeypatch, CERT_WW, ("Cougars", "Huskies"), 0, "Cougars", "washst", "Cougars")
        assert why is None and st["tripped"] is False and st["certified"][AEC_WW]["side_index"] == 0
        why, st = self._admission(monkeypatch, None, ("Huskies", "Cougars"), 0, "Huskies", "washst", "Huskies")
        assert why is None and st["certified"][AEC_WW]["outcome_desc"] == "Huskies"

    def test_the_pure_rule(self):
        c = ml._grammar_contradiction
        assert c(CERT_WW, "Huskies", 0) and c(CERT_WW, "Cougars", 1)
        assert c(CERT_WW, " COUGARS ", 1), "the names fold"
        assert c(CERT_WW, "Cougars", 0) is None and c(CERT_WW, " cougars ", 0) is None
        assert c(CERT_WW, "Huskies", 1) is None, "the complementary side"
        assert c(None, "Huskies", 0) is None and c("Cougars", "Huskies", 0) is None
        # the reviewer's reproduction: the pure writer still replaces (it is
        # the writer); the guard is the admission's, before the write
        st = _state({AEC_WW: CERT_WW}, ok=1)
        ml._grammar_certify(st, AEC_WW, dict(CERT_WW, outcome_desc="Huskies"))
        assert st["certified"][AEC_WW]["outcome_desc"] == "Huskies"
        src = inspect.getsource(ml._grammar_admission)
        assert src.index("_grammar_contradiction(") < src.index("_grammar_certify(st, slug,")

