"""Per-team Yes/No exact lane (LANE B, round-4 final 2026-08-30).

Four adversarial rounds designed this lane; the terminal round
approved it 3/3. Every round's executed break is replayed here as a
pin, by its round-1 name where it has one. The zero-network pattern:
a client factory that RAISES proves every whale-side refusal is
reached before _get_client() touches the wire.

Fixture discipline (the design's own coverage costs): every
corroboration-weight name has >= 2 RAW tokens, and at least one side
of every ACCEPTING fixture carries >= 2 DISTINCTIVE tokens — 'CF
América vs Club Puebla' (both furniture+surname) is an acknowledged
yn:twin refusal, cost (vii), so acceptance fixtures pair América with
'Club Santos Laguna' and Houston with 'Seattle Sounders'.
"""

import pytest

from sportsassets import pmus

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"


class _Markets:
    def __init__(self, table):
        self.table = table

    def retrieve_by_slug(self, slug):
        if slug not in self.table:
            raise KeyError(slug)
        return {"market": self.table[slug]}


class _Client:
    def __init__(self, table):
        self.markets = _Markets(table)


def _use(monkeypatch, table):
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))


def _no_network(monkeypatch):
    def _boom():
        raise AssertionError("network reached on a whale-side refusal")
    monkeypatch.setattr(pmus, "_get_client", _boom)


AME = "atc-lmx-ame-san-2026-08-29-ame"
Q_AME = ("Will CF América win against Club Santos Laguna in the "
         "Liga MX match scheduled for August 29, 2026?")
T_AME = "CF América vs Club Santos Laguna"


def _mkt(slug, q):
    return {slug: {
        "slug": slug, "closed": False, "question": q,
        "marketSides": [
            {"identifier": f"{slug}-yes", "description": "Yes",
             "long": True},
            {"identifier": f"{slug}-no", "description": "No",
             "long": False},
        ]}}


def _resolve(slug, outcome, title=None, event=None, diag=None):
    return pmus.resolve_team_yesno_exact(slug, outcome, title, event,
                                         diag)


# ── the accepting shape: everything corroborates ─────────────────────

def test_team_pick_maps_the_yes_side(monkeypatch):
    _use(monkeypatch, _mkt(AME, Q_AME))
    d: list = []
    r = _resolve("lmx-ame-san-2026-08-29-ame", "CF América", T_AME,
                 "CF América vs. Club Santos Laguna - More Markets", d)
    assert r is not None, d
    assert r["market_slug"] == f"{AME}-yes"
    assert r["intent"] == LONG
    assert r["matched_by"] == "team_yesno_exact"


def test_swapped_order_only_when_primary_unlisted(monkeypatch):
    swapped = "atc-lmx-san-ame-2026-08-29-ame"
    _use(monkeypatch, _mkt(swapped, Q_AME))
    r = _resolve("lmx-ame-san-2026-08-29-ame", "CF América", T_AME)
    assert r is not None and r["market_slug"] == f"{swapped}-yes"


def test_listed_primary_that_fails_never_falls_to_swap(monkeypatch):
    table = _mkt(AME, "Will CF América lead at halftime?")
    table.update(_mkt("atc-lmx-san-ame-2026-08-29-ame", Q_AME))
    _use(monkeypatch, table)
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    T_AME, None, d) is None
    assert "yn:shape" in d


# ── round-1 break replays ────────────────────────────────────────────

def test_break1_suffix_side_shear(monkeypatch):
    """His slug's own side token says -san; his pick says América.
    Round 1 validated the token then DISCARDED it. Now: refuse,
    pre-network."""
    _no_network(monkeypatch)
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-san", "CF América",
                    T_AME, None, d) is None
    assert "yn:suffix-side" in d


def test_break2_unwitnessed_opponent(monkeypatch):
    """Title-less row: the question names an opponent nothing
    whale-side witnesses. Refuses at yn:opp-unwitnessed — never maps
    (P4 always names an opponent; the title-less-friendly P1 shape
    was deleted in round 4)."""
    _use(monkeypatch, _mkt(AME, Q_AME))
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    None, None, d) is None
    assert "yn:opp-unwitnessed" in d


def test_break3_title_shear(monkeypatch):
    """His own plain title names the OTHER team: contradictory
    metadata; outcome never outvotes his title. Pre-network."""
    _no_network(monkeypatch)
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    "Club Santos Laguna", None, d) is None
    assert "yn:title-shear" in d


# ── round-3 amendment pins ───────────────────────────────────────────

def test_outcome_folds_refuses_pre_network(monkeypatch):
    _no_network(monkeypatch)
    for pick in ("Ολυμπιακός", "CF América (٢)"):
        d: list = []
        assert _resolve("lmx-ame-san-2026-08-29-ame", pick,
                        T_AME, None, d) is None
        assert "yn:outcome-folds" in d


def test_accented_latin_survives_the_fold_gate(monkeypatch):
    """Recall pins from the verifying voter: accents decompose to
    base+Mn and survive; América still maps end-to-end."""
    _use(monkeypatch, _mkt(AME, Q_AME))
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    T_AME) is not None
    from sportsassets.workers import premap as pm
    for name in ("CF América", "Fenerbahçe SK", "Atlético Potosí"):
        assert not pm._folds_away(name)


def test_literal_title_folds_before_subject_parse(monkeypatch):
    _no_network(monkeypatch)
    from sportsassets.workers import premap as pm
    calls = []
    monkeypatch.setattr(pm, "_bridge_title_subject",
                        lambda *a, **k: calls.append(1))
    d: list = []
    assert _resolve("lmx-pan-oly-2026-08-29-pan", "Yes",
                    "Will Panathinaikos FC win on 2026-08-29? "
                    "(Παράταση)", None, d) is None
    assert "yn:title-folds" in d
    assert not calls, "the gate must run BEFORE consumption"


def test_anchor_thin_refuses_pre_network(monkeypatch):
    _no_network(monkeypatch)
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "América",
                    T_AME, None, d) is None
    assert "yn:anchor-thin" in d


def test_executor_fixture_dies_thin_with_zero_network(monkeypatch):
    """The executor test fixture (outcome 'Arsenal') replayed: dies at
    the evidence floor before any candidate is built."""
    _no_network(monkeypatch)
    d: list = []
    assert _resolve("epl-ars-che-2026-08-30", "Arsenal",
                    "Arsenal vs Chelsea", None, d) is None
    assert "yn:anchor-thin" in d


def test_witness_thin_rapid_vs_union(monkeypatch):
    """The executed premap round-2.3 kill: both feeds rendering
    'Rapid vs Union' — refused before either side is consumed, and
    before yn:title-side's hit computation."""
    _no_network(monkeypatch)
    d: list = []
    assert _resolve("uecl-rap-uni-2026-08-29-rap", "CF América",
                    "Rapid vs Union", None, d) is None
    assert "yn:witness-thin" in d
    assert "yn:title-side" not in d


def test_subj_thin_distinctive_gift_refused(monkeypatch):
    q = ("Will America win against Club Santos Laguna in the Liga MX "
         "match scheduled for August 29, 2026?")
    _use(monkeypatch, _mkt(AME, q))
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    T_AME, None, d) is None
    assert "yn:subj-thin" in d


def test_opp_thin_distinctive_gift_refused(monkeypatch):
    q = ("Will CF América win against Santos in the Liga MX match "
         "scheduled for August 29, 2026?")
    _use(monkeypatch, _mkt(AME, q))
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    T_AME, None, d) is None
    assert "yn:opp-thin" in d


def test_league_slot_is_not_floored(monkeypatch):
    """The league slot is scope-screened only — a 1-token league must
    not refuse at a thin floor (it corroborates nothing)."""
    q = ("Will CF América win against Club Santos Laguna in the "
         "Apertura match scheduled for August 29, 2026?")
    _use(monkeypatch, _mkt(AME, q))
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    T_AME) is not None


def test_plain_single_name_title_is_veto_only(monkeypatch):
    """A plain single-name title that MATCHES the anchor via the
    distinctive path is not floored: it is a veto-only consistency
    check, never a witness. P4 always names an opponent, so with no
    witness this refuses at yn:opp-unwitnessed — NOT at a thin or
    title gate, and never at the twin floor (len==2 only)."""
    _use(monkeypatch, _mkt(AME, Q_AME))
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    "América - More Markets", None, d) is None
    assert "yn:opp-unwitnessed" in d
    assert "yn:title-shear" not in d
    assert "yn:twin" not in d


# ── round-4 amendment pins ───────────────────────────────────────────

def test_p4_is_the_only_template():
    assert len(pmus._YN_Q_PATTERNS) == 1
    pat = pmus._YN_Q_PATTERNS[0].pattern
    for group in ("subj", "opp", "lg", "mon", "day", "yr"):
        assert f"(?P<{group}>" in pat


def test_dated_p3_shape_now_refuses(monkeypatch):
    """Round-3 accepted 'will X win against Y on <date>'; round 4
    deleted it. Only the full P4 census wording maps."""
    q = ("Will CF América win against Club Santos Laguna on "
         "August 29 2026?")
    _use(monkeypatch, _mkt(AME, q))
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    T_AME, None, d) is None
    assert "yn:shape" in d


def test_twin_fixture_floor(monkeypatch):
    """Round-4 A2: both title sides single-distinctive ('FC Rapid vs
    FC Union', and the cost-(vii) example 'CF América vs Club
    Puebla') — no readable witness separates same-named fixtures
    across leagues. Refuse pre-network at yn:twin."""
    _no_network(monkeypatch)
    d: list = []
    assert _resolve("uecl-rap-uni-2026-08-29-rap", "FC Rapid",
                    "FC Rapid vs FC Union", None, d) is None
    assert "yn:twin" in d
    d2: list = []
    assert _resolve("lmx-ame-pue-2026-08-29-ame", "CF América",
                    "CF América vs Club Puebla", None, d2) is None
    assert "yn:twin" in d2


def test_two_distinctive_opponent_clears_the_twin_floor(monkeypatch):
    """'Union Berlin' pins the fixture (two teams play once a day):
    ALL-sides-single-distinctive is the rule, not any."""
    slug = "atc-uecl-rap-uni-2026-08-29-rap"
    q = ("Will FC Rapid Bucuresti win against Union Berlin in the "
         "Conference League match scheduled for August 29, 2026?")
    _use(monkeypatch, _mkt(slug, q))
    r = _resolve("uecl-rap-uni-2026-08-29-rap", "FC Rapid Bucuresti",
                 "FC Rapid Bucuresti vs Union Berlin")
    assert r is not None and r["intent"] == LONG


# ── venue-side refusals ──────────────────────────────────────────────

def test_wrong_date_question_refuses(monkeypatch):
    q = ("Will CF América win against Club Santos Laguna in the "
         "Liga MX match scheduled for August 30, 2026?")
    _use(monkeypatch, _mkt(AME, q))
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    T_AME, None, d) is None
    assert "yn:qdate" in d


def test_named_side_market_stays_with_existing_resolvers(monkeypatch):
    table = {AME: {
        "slug": AME, "closed": False, "question": Q_AME,
        "marketSides": [
            {"identifier": AME, "description": "CF América",
             "long": True},
            {"identifier": AME, "description": "Club Santos Laguna",
             "long": False},
        ]}}
    _use(monkeypatch, table)
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-ame", "CF América",
                    T_AME, None, d) is None
    assert "yn:sides" in d


def test_segment_suffix_refuses(monkeypatch):
    _no_network(monkeypatch)
    d: list = []
    assert _resolve("lmx-ame-san-2026-08-29-fh", "CF América",
                    T_AME, None, d) is None
    assert "yn:suffix" in d


def test_literal_pick_maps_its_literal_side(monkeypatch):
    q = ("Will Houston Dynamo FC win against Seattle Sounders in the "
         "MLS match scheduled for August 29, 2026?")
    slug = "atc-mls-hou-sea-2026-08-29-hou"
    _use(monkeypatch, _mkt(slug, q))
    d: list = []
    r = _resolve("mls-hou-sea-2026-08-29-hou", "No",
                 "Will Houston Dynamo FC win on 2026-08-29?",
                 "Houston Dynamo FC vs Seattle Sounders", d)
    assert r is not None, d
    assert r["market_slug"] == f"{slug}-no"
    assert r["matched_by"] == "team_yesno_pick_exact"
    assert r["intent"] == SHORT


def test_literal_pick_without_suffix_refuses(monkeypatch):
    _no_network(monkeypatch)
    d: list = []
    assert _resolve("mls-hou-sea-2026-08-29", "No",
                    "Will Houston Dynamo FC win on 2026-08-29?",
                    None, d) is None
    assert "yn:suffix-missing" in d
