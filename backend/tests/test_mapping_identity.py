"""The yes/no IDENTITY branch, and the phantom-line fix it needed
(to-a-tee program Phase 2, owner order 2026-09-02 "I want us to match
everything ... mirror the whales to a tee").

The venue lists RN1's soccer per-team markets and premap refused every
one of them twice over (the coverage lens's §2.f reproduction, confirmed
by its market and engineering refutations):

    his title   Will NEOM SC win on 2026-09-03?
    his slug    spl-neo-kha-2026-09-03-neo            outcome Yes / No
    venue rows  atc-spl-neo-kha-2026-09-03-neo        yes BUY_LONG
                atc-spl-neo-kha-2026-09-03-neo        no  BUY_SHORT
    venue q     Will NEOM SC win against Al Khaleej Saudi Club in the
                Saudi Pro League match scheduled for Sep 3, 2026?

  1. the ISO date in his title parsed as the lines {'03','09'} and the
     sign '-09', and _yn_line_ok refused the unlined row against those
     phantoms (probe UNMAPEG: his_lines=['03','09']);
  2. _questions_agree needs the two feeds to WORD the proposition alike,
     and they never do.

The first is a bug and is fixed for every caller (test_premap_round2
pins that side). The second is answered by identity: the row whose
identifier is byte-for-byte "atc-" + his slug IS his market, on his
date, on his side — admitted only when the venue's question fullmatches
the one measured per-team template on his date and his own dated
win-title names its subject. Source stays 'premap' (the class the
quarantine admits); the branch is dark until PREMAP_YN_IDENTITY=on.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets.workers import premap

Q_NEO = ("Will NEOM SC win against Al Khaleej Saudi Club in the Saudi "
         "Pro League match scheduled for Sep 3, 2026?")
Q_KHA = ("Will Al Khaleej Saudi Club win against NEOM SC in the Saudi "
         "Pro League match scheduled for Sep 3, 2026?")
T_NEO = "Will NEOM SC win on 2026-09-03?"
T_KHA = "Will Al Khaleej Saudi Club win on 2026-09-03?"
S_NEO = "spl-neo-kha-2026-09-03-neo"
S_KHA = "spl-neo-kha-2026-09-03-kha"
ID_NEO = "atc-" + S_NEO
ID_KHA = "atc-" + S_KHA
EV = {"slug": "aec-spl-neo-kha-2026-09-03",
      "title": "NEOM SC vs. Al Khaleej Saudi Club"}
LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"


def _market(ident: str, q: str) -> dict:
    """The venue's own side expansion, as probe NAMEDML-Q shows it:
    both sides share the identifier; `long` names the intent."""
    return {"slug": ident, "question": q, "marketSides": [
        {"identifier": ident, "description": "Yes", "long": True},
        {"identifier": ident, "description": "No", "long": False}]}


def _board(q_neo: str = Q_NEO, q_kha: str = Q_KHA) -> list[dict]:
    """Both per-team contracts of the one game, built by the sweep's
    own row builder and keyed the way the sweep keys them."""
    rows = (premap._market_rows(EV, _market(ID_NEO, q_neo))
            + premap._market_rows(EV, _market(ID_KHA, q_kha)))
    keys = premap.event_keys_for(EV["title"], EV["slug"])
    for r in rows:
        r["event_keys"] = keys
    return rows


class _Pool:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        assert "us_premap" in sql
        k = set(a[0])
        return [r for r in self.rows if set(r["event_keys"]) & k]


def _resolve(rows, title, slug, outcome):
    return asyncio.run(premap.resolve(_Pool(rows), title, EV["title"],
                                      outcome, slug))


def _explain(rows, title, slug, outcome):
    return asyncio.run(premap.resolve_explain(_Pool(rows), title,
                                              EV["title"], outcome, slug))


def _hit(h):
    return (h["identifier"], h["side_norm"], h["intent"]) if h else None


@pytest.fixture
def dark(monkeypatch):
    monkeypatch.delenv(premap.PREMAP_YN_IDENTITY_ENV, raising=False)


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, "on")


class TestTheFixtureAsItStoodBefore:
    def test_the_row_builder_yields_the_probes_shape(self):
        rows = _board()
        assert [(r["identifier"], r["side_norm"], r["intent"])
                for r in rows[:2]] == [(ID_NEO, "yes", LONG),
                                       (ID_NEO, "no", SHORT)]
        assert all(r["line"] == "" and r["signed"] == "" for r in rows)

    def test_wording_alone_still_refuses_it(self):
        """The second blocker, unchanged: the two feeds word the same
        proposition differently and _questions_agree says so."""
        assert premap.match_side(_board(), "Yes", T_NEO, S_NEO) is None
        assert not premap._questions_agree(premap._norm(T_NEO),
                                           premap._norm(Q_NEO))

    def test_the_switch_is_off_by_default(self, dark):
        assert premap.yn_identity_on() is False
        assert _resolve(_board(), T_NEO, S_NEO, "Yes") is None

    def test_anything_but_on_is_off(self, monkeypatch):
        for v in ("", "off", "true", "1", "ON "):
            monkeypatch.setenv(premap.PREMAP_YN_IDENTITY_ENV, v)
            assert premap.yn_identity_on() is (v.strip().lower() == "on")

    def test_off_means_byte_identical(self, dark):
        """With the switch off the census still says no_side_match with
        no trace of an identity hit in the step."""
        ex = _explain(_board(), T_NEO, S_NEO, "Yes")
        assert ex["step"] == "no_side_match"


class TestTheIdentityBranchAdmitsHisOwnContract:
    def test_neom_yes_is_the_yes_row_long(self):
        h = premap.match_side(_board(), "Yes", T_NEO, S_NEO,
                              yn_identity=True)
        assert _hit(h) == (ID_NEO, "yes", LONG)

    def test_neom_no_is_the_no_row_short(self):
        """His No token is draw plus opponent — the venue's SHORT on the
        same identifier (refutation F2): equal payoff."""
        h = premap.match_side(_board(), "No", T_NEO, S_NEO,
                              yn_identity=True)
        assert _hit(h) == (ID_NEO, "no", SHORT)

    def test_resolve_keeps_source_premap_under_its_own_label(self, armed):
        y = _resolve(_board(), T_NEO, S_NEO, "Yes")
        n = _resolve(_board(), T_NEO, S_NEO, "No")
        assert (y["market_slug"], y["intent"]) == (ID_NEO, LONG)
        assert (n["market_slug"], n["intent"]) == (ID_NEO, SHORT)
        assert y["matched_by"] == n["matched_by"] == "premap_identity"
        assert y["outcome"] == "yes" and n["outcome"] == "no"

    def test_a_kha_slug_resolves_only_the_kha_identifier(self, armed):
        for outcome, intent in (("Yes", LONG), ("No", SHORT)):
            h = _resolve(_board(), T_KHA, S_KHA, outcome)
            assert h["market_slug"] == ID_KHA and h["intent"] == intent
            assert h["market_slug"] != ID_NEO

    def test_a_title_naming_the_other_team_is_shear_and_refuses(self):
        """His slug says -kha, his title asks about NEOM: contradictory
        metadata, never let one outvote the other."""
        assert premap.match_side(_board(), "Yes", T_NEO, S_KHA,
                                 yn_identity=True) is None
        assert premap.match_side(_board(), "Yes", T_KHA, S_NEO,
                                 yn_identity=True) is None

    def test_the_named_arm_is_untouched(self):
        """Identity is a yes/no branch only: a named pick against these
        yes/no rows still finds no named side."""
        assert premap.match_side(_board(), "NEOM SC", T_NEO, S_NEO,
                                 yn_identity=True) is None


class TestTheWordingArmAnswersToTheSameIdentity:
    """The mapping unit's review (2026-09-03, blocking): the phantom
    fix made the wording arm reach dated per-team titles for the first
    time, and that arm filtered on side, question containment and the
    line guard — never on the identifier. His slug '-kha' with a title
    asking about NEOM, against a venue row worded tersely 'Will NEOM SC
    win?', resolved atc-...-NEO with source 'premap', no switch (HEAD
    refused it only because the phantom '-09' refused everything). A
    per-team contract that is not 'atc-' + his slug is metadata shear
    in the wording arm exactly as it is in the identity arm."""

    def test_a_kha_slug_never_takes_the_neo_contract_by_wording(
            self, dark):
        rows = _board(q_neo="Will NEOM SC win?")
        assert premap.match_side(rows, "Yes", T_NEO, S_KHA) is None
        assert _resolve(rows, T_NEO, S_KHA, "Yes") is None
        # both venue rows terse: the same shear, the same refusal
        both = _board(q_neo="Will NEOM SC win?",
                      q_kha="Will Al Khaleej Saudi Club win?")
        assert premap.match_side(both, "Yes", T_NEO, S_KHA) is None
        assert _resolve(both, T_NEO, S_KHA, "Yes") is None

    def test_the_same_shear_refuses_armed(self, armed):
        rows = _board(q_neo="Will NEOM SC win?")
        assert premap.match_side(rows, "Yes", T_NEO, S_KHA,
                                 yn_identity=True) is None
        assert _resolve(rows, T_NEO, S_KHA, "Yes") is None
        ex = _explain(rows, T_NEO, S_KHA, "Yes")
        assert ex["step"] == "no_side_match"
        assert ex["yn_identity"]["would_resolve"] is False

    def test_a_dateless_title_on_a_sheared_slug_refuses_too(self, dark):
        """HEAD resolved this one (probe_shear case 4: no date, so no
        phantom): a dateless NEOM title on a -kha slug took the -neo
        contract. That was the same wrong-market trade; it refuses now,
        which is the one deliberate departure from HEAD's answers."""
        rows = _board(q_neo="Will NEOM SC win?")
        assert premap.match_side(rows, "Yes", "Will NEOM SC win?",
                                 S_KHA) is None
        assert _resolve(rows, "Will NEOM SC win?", S_KHA, "Yes") is None

    def test_his_own_identifier_still_resolves_by_wording(self, dark):
        """The matching slug keeps the wording hit and its source — the
        veto removes only rows that are not his."""
        rows = _board(q_neo="Will NEOM SC win?")
        h = premap.match_side(rows, "Yes", T_NEO, S_NEO)
        assert _hit(h) == (ID_NEO, "yes", LONG)
        assert _resolve(rows, T_NEO, S_NEO, "Yes")["matched_by"] == "premap"
        # and the venue's own identifier as his slug is identity too
        assert _hit(premap.match_side(rows, "Yes", T_NEO, ID_NEO)) == \
            (ID_NEO, "yes", LONG)

    def test_a_bare_event_slug_or_a_draw_token_refuses_every_atc_row(
            self, dark):
        rows = _board(q_neo="Will NEOM SC win?")
        for s in ("spl-neo-kha-2026-09-03", "spl-neo-kha-2026-09-03-draw"):
            assert premap.match_side(rows, "Yes", T_NEO, s) is None, s

    def test_rows_without_the_atc_kind_are_untouched(self, dark):
        """The veto names the per-team kind only: a yes/no row on any
        other identifier answers to wording exactly as before."""
        rows = _board(q_neo="Will NEOM SC win?")
        other = [dict(r, identifier="astatc-spl-neo-kha-2026-09-03-x")
                 for r in rows[:2]]
        h = premap.match_side(other, "Yes", T_NEO, S_KHA)
        assert h is not None and h["side_norm"] == "yes"
        # and with no slug at all there is no identity to hold a row to
        h = premap.match_side(rows, "Yes", T_NEO, None)
        assert h is not None and h["identifier"] == ID_NEO

    def test_the_veto_reads_both_arms(self):
        src = inspect.getsource(premap.match_side)
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert code.count("_yn_identity_ok(r)") == 2
        assert code.index("def _yn_identity_ok") < code.index(
            "_questions_agree(want_q")


# The four titles the re-review ran (probe B): each contains his bare
# win-question, so containment admitted it; none is his game.
QUALIFIED = ("Will NEOM SC win the 1st half on 2026-09-03?",
             "Will NEOM SC win (Reserves) on 2026-09-03?",
             "Will NEOM SC win on 2026-09-03? (Aggregate)",
             "Will NEOM SC win by 2 or more goals on 2026-09-03?")


class TestTheWordingArmAnswersToTheSameTitleGate:
    """The mapping unit's re-review (2026-09-03, major): the identifier
    veto above was scope-blind. On his OWN -neo slug, a dated title
    asking about the first half, the reserves, the aggregate or a
    two-goal margin passed the veto, passed containment ('will neom sc
    win' sits inside every one of them), passed the line guard ('1st'
    and 'by 2' are not lines) and traded the FULL-GAME atc- contract
    with source 'premap' and no switch. HEAD refused the ISO forms only
    by the phantom '-09'. The wording arm now answers to the bridge's
    own title gate (_yn_title_is_his_game): his dated win-question on
    his slug's date, or the bare 'Will X win?', and nothing else."""

    @pytest.mark.parametrize("t", QUALIFIED)
    def test_a_qualifier_in_his_dated_title_refuses_dark(self, dark, t):
        rows = _board(q_neo="Will NEOM SC win?")
        # containment alone would admit it — that is the hole
        assert premap._questions_agree(premap._norm("Will NEOM SC win?"),
                                       premap._norm(t))
        assert premap.match_side(rows, "Yes", t, S_NEO) is None
        assert _resolve(rows, t, S_NEO, "Yes") is None
        assert _resolve(rows, t, S_NEO, "No") is None

    @pytest.mark.parametrize("t", QUALIFIED)
    def test_the_same_qualifier_refuses_armed(self, armed, t):
        rows = _board(q_neo="Will NEOM SC win?")
        assert premap.match_side(rows, "Yes", t, S_NEO,
                                 yn_identity=True) is None
        assert _resolve(rows, t, S_NEO, "Yes") is None
        ex = _explain(rows, t, S_NEO, "Yes")
        assert ex["step"] == "no_side_match"
        assert ex["yn_identity"]["would_resolve"] is False
        # and the identity arm refuses the same title on the
        # long-template rows, as it always did
        assert _resolve(_board(), t, S_NEO, "Yes") is None

    def test_a_dateless_qualifier_refuses_too(self, dark):
        """HEAD resolved every one of these (no date, so no phantom):
        the same wrong-market trade with the date left off. A
        deliberate departure from HEAD, in the refusing direction."""
        rows = _board(q_neo="Will NEOM SC win?")
        for t in ("Will NEOM SC win? (Aggregate)",
                  "Will NEOM SC win the 1st half?",
                  "Will NEOM SC win (Reserves)?"):
            assert premap.match_side(rows, "Yes", t, S_NEO) is None, t
            assert _resolve(rows, t, S_NEO, "Yes") is None, t

    def test_a_title_the_fold_goes_blind_on_refuses(self, dark):
        """'(Παράταση)' folds to nothing, leaving the clean template:
        content the gate stack cannot read refuses (HEAD admitted the
        dateless form)."""
        rows = _board(q_neo="Will NEOM SC win?")
        for t in ("Will NEOM SC win (Παράταση)?",
                  "Will NEOM SC win (Παράταση) on 2026-09-03?"):
            assert premap._folds_away(t)
            assert premap.match_side(rows, "Yes", t, S_NEO) is None, t
            assert _resolve(rows, t, S_NEO, "Yes") is None, t
        # accented Latin survives the fold and stays admissible
        assert premap._yn_title_is_his_game(
            "Will Fenerbahçe win?", "tsl-fen-gal-2026-09-03-fen")

    def test_a_title_dated_another_day_than_his_slug_refuses(self, dark):
        """The wrong game outright. HEAD resolved the month-name form
        ('Sep 4, 2026' on a 2026-09-03 slug): the third departure."""
        rows = _board(q_neo="Will NEOM SC win?")
        for t in ("Will NEOM SC win on 2026-09-04?",
                  "Will NEOM SC win on Sep 4, 2026?"):
            assert premap.match_side(rows, "Yes", t, S_NEO) is None, t
            assert _resolve(rows, t, S_NEO, "Yes") is None, t

    def test_a_date_the_grammar_cannot_read_refuses(self, dark):
        """'9/3/2026' resolved at HEAD and refuses now: a date the gate
        cannot read is a date it cannot hold against his slug. The
        month-name and ISO forms keep resolving, question mark or not."""
        rows = _board(q_neo="Will NEOM SC win?")
        assert premap.match_side(rows, "Yes", "Will NEOM SC win on 9/3/2026?",
                                 S_NEO) is None
        assert _resolve(rows, "Will NEOM SC win on 9/3/2026?", S_NEO,
                        "Yes") is None
        for t in ("Will NEOM SC win on Sep 3, 2026?",
                  "Will NEOM SC win on 2026-09-03",
                  T_NEO):
            assert _hit(premap.match_side(rows, "Yes", t, S_NEO)) == \
                (ID_NEO, "yes", LONG), t
            assert _resolve(rows, t, S_NEO, "Yes")["matched_by"] == \
                "premap", t

    def test_the_gate_reads_every_reason(self):
        g = premap._yn_title_is_his_game
        assert g(T_NEO, S_NEO) is True
        assert g("Will NEOM SC win?", S_NEO) is True          # title_undated
        assert g("Will NEOM SC win?", "spl-neo-kha-neo") is True
        assert g(QUALIFIED[0], S_NEO) is False                # not win shape
        assert g("NEOM SC vs Al Khaleej", S_NEO) is False
        assert g("Will NEOM SC win on 2026-09-04?", S_NEO) is False
        assert g(T_NEO, "spl-neo-kha-neo") is False           # dateless slug
        assert g("Will FC Schalke 04 win on 2026-09-03?",
                 "bl1-s04-bvb-2026-09-03-s04") is False      # subject digit
        assert g("Will NEOM SC win (Παράταση)?", S_NEO) is False
        assert g(None, S_NEO) is False and g("", S_NEO) is False

    def test_the_gate_holds_only_his_own_atc_row(self, dark):
        """The gate is the second half of the veto: no slug means no
        identity to hold a row to, and a non-atc row answers to wording
        exactly as before (the existing untouched pins)."""
        rows = _board(q_neo="Will NEOM SC win?")
        h = premap.match_side(rows, "Yes", QUALIFIED[0], None)
        assert h is not None and h["identifier"] == ID_NEO
        src = inspect.getsource(premap.match_side)
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        i = code.index("def _yn_identity_ok")
        body = code[i:code.index("cands = [r for r in rows", i)]
        assert "return title_is_his_game" in body
        assert "_yn_his_identifiers(his_slug)" in body

    def test_both_arms_read_one_title_gate_and_one_identifier_set(self):
        ms = inspect.getsource(premap.match_side)
        ir = inspect.getsource(premap.yn_identity_rows)
        tg = inspect.getsource(premap._yn_title_is_his_game)
        assert "_yn_title_is_his_game(his_title, his_slug)" in ms
        assert "_bridge_title_subject(his_title, his_slug)" in tg
        assert "_bridge_title_subject(his_title, his_slug)" in ir
        assert "_yn_his_identifiers(his_slug)" in ms
        assert "_yn_his_identifiers(his_slug)" in ir
        assert '"atc-" + his_slug' not in ms and '"atc-" + his_slug' not in ir


class TestBothArmsAgreeOnWhatHisIdentifierIs:
    """The re-review's second finding (2026-09-03, minor): the wording
    veto accepted his slug being the venue's own atc- identifier, the
    identity branch demanded 'atc-' + his slug and refused it. One set
    (_yn_his_identifiers) now feeds both."""

    def test_the_venues_own_identifier_as_his_slug_resolves_in_both_arms(
            self, armed):
        terse = _board(q_neo="Will NEOM SC win?")
        h = _resolve(terse, T_NEO, ID_NEO, "Yes")
        assert (h["market_slug"], h["intent"], h["matched_by"]) == \
            (ID_NEO, LONG, "premap")
        long_rows = _board()
        assert [r["identifier"] for r in premap.yn_identity_rows(
            long_rows, "Yes", T_NEO, ID_NEO)] == [ID_NEO]
        y = _resolve(long_rows, T_NEO, ID_NEO, "Yes")
        n = _resolve(long_rows, T_NEO, ID_NEO, "No")
        assert (y["market_slug"], y["intent"], y["matched_by"]) == \
            (ID_NEO, LONG, "premap_identity")
        assert (n["market_slug"], n["intent"], n["matched_by"]) == \
            (ID_NEO, SHORT, "premap_identity")

    def test_atc_atc_names_nothing_in_either_arm(self, armed):
        rows = [dict(r, identifier="atc-" + ID_NEO) for r in _board()]
        assert premap.yn_identity_rows(rows, "Yes", T_NEO, ID_NEO) == []
        assert premap.match_side(rows, "Yes", T_NEO, ID_NEO,
                                 yn_identity=True) is None
        assert _resolve(rows, T_NEO, ID_NEO, "Yes") is None
        terse = [dict(r, identifier="atc-" + ID_NEO, question="Will NEOM SC win?")
                 for r in _board()]
        assert premap.match_side(terse, "Yes", T_NEO, ID_NEO) is None

    def test_the_set_is_one_identifier_byte_for_byte(self):
        assert premap._yn_his_identifiers(S_NEO) == {ID_NEO}
        assert premap._yn_his_identifiers(ID_NEO) == {ID_NEO}
        assert premap._yn_his_identifiers(S_NEO.upper()) == \
            {"atc-" + S_NEO.upper()}
        assert premap._yn_his_identifiers("ATC-" + S_NEO) == \
            {"atc-ATC-" + S_NEO}
        # a non-string identifier on a row is not his, and never raises
        rows = [dict(r, identifier=["atc-", S_NEO]) for r in _board()]
        assert premap.yn_identity_rows(rows, "Yes", T_NEO, S_NEO) == []


class TestItRefusesEverythingItShould:
    def test_a_different_date_refuses_three_ways(self):
        rows = _board()
        # his slug on another day: no identifier is his
        assert premap.match_side(
            rows, "Yes", "Will NEOM SC win on 2026-09-04?",
            "spl-neo-kha-2026-09-04-neo", yn_identity=True) is None
        # his title on another day than his slug: the bridge's own gate
        assert premap.match_side(
            rows, "Yes", "Will NEOM SC win on 2026-09-04?", S_NEO,
            yn_identity=True) is None
        # the venue's question dated another day than his slug
        rows4 = _board(q_neo=Q_NEO.replace("Sep 3", "Sep 4"))
        assert premap.match_side(rows4, "Yes", T_NEO, S_NEO,
                                 yn_identity=True) is None

    def test_a_titled_line_still_refuses_an_unlined_row(self):
        assert premap.match_side(
            _board(), "Yes", "Will NEOM SC win -1.5 on 2026-09-03?",
            S_NEO, yn_identity=True) is None

    def test_a_lined_row_still_refuses_an_unlined_pick(self):
        """The line guard applies to the identity row exactly as it does
        to a wording row — a lined row never takes an unlined pick."""
        rows = [dict(r, line="1.5") for r in _board()]
        assert premap.match_side(rows, "Yes", T_NEO, S_NEO,
                                 yn_identity=True) is None
        rows = [dict(r, signed="-1.5") for r in _board()]
        assert premap.match_side(rows, "Yes", T_NEO, S_NEO,
                                 yn_identity=True) is None

    def test_identity_is_byte_for_byte(self):
        rows = _board()
        assert premap.match_side(rows, "Yes", T_NEO, S_NEO.upper(),
                                 yn_identity=True) is None
        assert premap.match_side(rows, "Yes", T_NEO, S_NEO + " ",
                                 yn_identity=True) is None
        # the two-outcome aec- contract is not his per-team contract
        aec = [dict(r, identifier="aec-" + S_NEO) for r in rows[:2]]
        assert premap.match_side(aec, "Yes", T_NEO, S_NEO,
                                 yn_identity=True) is None
        # nor is the OTHER side's contract, whatever its question says
        other = [dict(r, identifier=ID_KHA) for r in rows[:2]]
        assert premap.match_side(other, "Yes", T_NEO, S_NEO,
                                 yn_identity=True) is None

    def test_the_intent_must_be_the_venues_own_for_that_side(self):
        rows = _board()
        swapped = [dict(r, intent=SHORT if r["intent"] == LONG else LONG)
                   for r in rows]
        assert premap.match_side(swapped, "Yes", T_NEO, S_NEO,
                                 yn_identity=True) is None
        unnamed = [dict(r, intent=None) for r in rows]
        assert premap.match_side(unnamed, "Yes", T_NEO, S_NEO,
                                 yn_identity=True) is None

    @pytest.mark.parametrize("q", [
        "Will NEOM SC win?",
        "Will NEOM SC win on Sep 3, 2026?",
        "Will NEOM SC win vs Al Khaleej Saudi Club in the Saudi Pro "
        "League match scheduled for Sep 3, 2026?",
        "Will NEOM SC win against Al Khaleej Saudi Club in the Saudi "
        "Pro League match scheduled for Sep 3?",
        "Will NEOM SC reserves win against Al Khaleej Saudi Club in the "
        "Saudi Pro League match scheduled for Sep 3, 2026?",
        "Will NEOM SC win against Al Khaleej Saudi Club in the Saudi "
        "Pro League match (first half) scheduled for Sep 3, 2026?",
        "Will Al Khaleej Saudi Club win against NEOM SC in the Saudi "
        "Pro League match scheduled for Sep 3, 2026?",
    ])
    def test_wording_off_the_measured_template_refuses(self, q):
        """The one measured template, fully anchored, dated, with the
        subject naming HIS team: the identity branch admits nothing
        else, including the opponent's question carried on his
        identifier. (The bare 'Will NEOM SC win?' is the WORDING
        branch's — see the containment test below — never this one.)"""
        rows = _board(q_neo=q)
        assert premap.yn_identity_rows(rows, "Yes", T_NEO, S_NEO) == []
        if not premap._questions_agree(premap._norm(T_NEO), premap._norm(q)):
            assert premap.match_side(rows, "Yes", T_NEO, S_NEO,
                                     yn_identity=True) is None

    def test_a_bare_win_question_was_always_the_wording_branchs(self, armed):
        """Pre-existing behaviour, pinned so nobody attributes it here:
        _questions_agree is containment, so a venue row worded 'Will
        NEOM SC win?' agrees with his title (either side of the
        containment), and a DATELESS whale title agreed with the long
        venue question before this unit — the refuter's own sandbox
        line 'match dateless <row … yes LONG>'. The phantom fix makes
        his dated title behave like his dateless one; the row's event
        key still carries the date, so game agreement is untouched."""
        rows = _board(q_neo="Will NEOM SC win?")
        h = _resolve(rows, T_NEO, S_NEO, "Yes")
        assert h is not None and h["matched_by"] == "premap"
        h = _resolve(_board(), "Will NEOM SC win?", S_NEO, "Yes")
        assert h is not None and h["matched_by"] == "premap"

    def test_a_title_that_is_not_his_dated_win_question_refuses(self):
        for t in ("NEOM SC vs Al Khaleej",
                  "Will NEOM SC win on 2026-09-03? (Aggregate)",
                  "Will NEOM SC advance on 2026-09-03?", None, ""):
            assert premap.yn_identity_rows(_board(), "Yes", t, S_NEO) == [], t
            assert premap.match_side(_board(), "Yes", t, S_NEO,
                                     yn_identity=True) is None, t

    def test_two_identical_rows_are_ambiguous(self):
        rows = _board()
        rows = rows + [dict(rows[0])]
        assert premap.match_side(rows, "Yes", T_NEO, S_NEO,
                                 yn_identity=True) is None

    def test_a_slug_without_a_date_refuses(self, armed):
        rows = [dict(r, identifier="atc-spl-neo-kha-neo") for r in _board()]
        assert premap.yn_identity_rows(rows, "Yes", "Will NEOM SC win?",
                                       "spl-neo-kha-neo") == []
        # and resolve refuses every dateless signal before any matcher
        assert _resolve(rows, "Will NEOM SC win?", "spl-neo-kha-neo",
                        "Yes") is None

    def test_it_is_pure(self):
        """No table, no network: yn_identity_rows sees only the rows it
        is handed and the two strings the whale supplied."""
        sig = inspect.signature(premap.yn_identity_rows)
        assert list(sig.parameters) == ["rows", "outcome", "his_title",
                                        "his_slug"]
        src = inspect.getsource(premap.yn_identity_rows)
        for forbidden in ("await", "pool", "_get_client", "os.getenv"):
            assert forbidden not in src, forbidden


class TestWordingIsConsultedFirstAndNeverDisplaced:
    def test_a_wording_hit_keeps_its_row_and_its_source(self, armed):
        rows = _board()
        rows[0]["question"] = rows[1]["question"] = T_NEO
        h = _resolve(rows, T_NEO, S_NEO, "Yes")
        assert h["market_slug"] == ID_NEO and h["intent"] == LONG
        assert h["matched_by"] == "premap"

    def test_resolve_makes_the_literal_call_first(self):
        """The pinned four-argument call stays the first word; the
        armed call is a second statement after a None."""
        src = inspect.getsource(premap.resolve)
        first = src.index("match_side(kept, outcome, market_title, global_slug)")
        armed_call = src.index("yn_identity=True")
        assert first < armed_call
        assert "if hit is None and yn_identity_on():" in src[first:armed_call]

    def test_the_census_makes_the_same_second_call(self):
        src = inspect.getsource(premap.resolve_explain)
        first = src.index("match_side(kept, outcome, market_title, global_slug)")
        assert "if hit is None and yn_identity_on():" in src[first:]

    def test_the_keyword_fails_closed(self):
        sig = inspect.signature(premap.match_side)
        p = sig.parameters["yn_identity"]
        assert p.default is False and p.kind is p.KEYWORD_ONLY


class TestTheCensusMeasuresItDark:
    def test_dark_it_says_what_it_would_do(self, dark):
        ex = _explain(_board(), T_NEO, S_NEO, "Yes")
        assert ex["step"] == "no_side_match"
        assert ex["yn_identity"] == {
            "on": False, "would_resolve": True, "identifier": ID_NEO,
            "side_norm": "yes", "intent": LONG}
        # and it prints the lines the matcher saw, not the phantom
        assert "his_lines=[]" in ex["detail"]

    def test_dark_it_says_when_it_would_not(self, dark):
        ex = _explain(_board(), "Will NEOM SC win on 2026-09-04?", S_NEO,
                      "Yes")
        assert ex["step"] == "no_side_match"
        assert ex["yn_identity"]["would_resolve"] is False
        assert ex["yn_identity"]["identifier"] is None

    def test_armed_the_census_resolves_like_production(self, armed):
        ex = _explain(_board(), T_NEO, S_NEO, "No")
        assert ex["step"] == "resolves" and ex["detail"] == ID_NEO

    def test_the_probe_only_reads(self):
        src = inspect.getsource(premap.resolve_explain)
        i = src.index('out["yn_identity"]')
        assert "except Exception" in src[i:i + 900]
