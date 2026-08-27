"""The yes/no bridge, round 2: the MEASURED template, re-pinned.

Round 1 built a grammar under adversarial attack and shipped it Phase 0
(probe-only). The production census then measured would_resolve=0: the
venue's real win-questions carry a tail the round-1 whitelist never
imagined — "... in the <league> match scheduled for <Mon D, YYYY>?" —
and a round-2 tournament (3 designs x 3 attack lenses) was run against
the CAPTURED wordings. Every design took at least one executed kill,
and two of the kills went through THE SHIPPED ROUND-1 CODE itself:

  * the dateless bare form ('Will X win against Y?') admitted a row on
    an aggregate-market pool, and on a pool whose identifiers carried
    2026-08-21 under a 2026-08-27 slug — no gate compared any
    venue-side date to the slug when the question was dateless;
  * gate 11 (multi_event_pool) was DEAD CODE — neither SELECT fetched
    event_slug, so every row read '' and the refusal could never fire.

This file pins the synthesis that survived: ONE measured template
(strict), a CLOSED league whitelist, mandatory year + tail-date-equals-
slug-date, identifier-date-equals-slug-date, dated whale titles only, a
5-token name cap, and a two-form corroboration bounded to the reviewed
ten. _BRIDGE_Q_RE (the raw/BLOCKING form) is byte-frozen, so everything
the old strict admitted still BLOCKS — the safe direction. Every
tournament kill and near-miss below is a pinned refusal; every honest
miss is pinned AS a miss so a future flip is a reviewed change with the
probe's evidence in hand, never a drift.

PHASE 0 still: nothing on the order path consumes a bridge hit.
resolve() takes no bridge argument; the probe in resolve_explain and
the census tally are the only consumers, and they are read-only.
"""

from __future__ import annotations

import inspect

from sportsassets.workers import premap as pm

SLUG = "col-aus-scb-2026-08-27-scb"
TITLE = "Will SC Braga win on 2026-08-27?"
TAIL = " in the UECL match scheduled for Aug 27, 2026?"
QB = "Will SC Braga win against Austin FC" + TAIL
QA = "Will Austin FC win against SC Braga" + TAIL
QD = ("Will the UECL match Austin FC vs SC Braga scheduled for "
      "Aug 27, 2026 end in a draw?")
# Round 1's recovery wording — now a REFUSAL shape (unselectable alone,
# blocking beside the true tailed market).
QB_BARE = "Will SC Braga win against Austin FC?"


def row(ident, side, q, ev="atc-col-aus-scb-2026-08-27",
        evt="Austin FC vs SC Braga", line="", signed="",
        intent="ORDER_INTENT_BUY_LONG"):
    return {"identifier": ident, "side_norm": side, "question": q,
            "event_slug": ev, "event_title": evt, "line": line,
            "signed": signed, "intent": intent, "kind": "side"}


def board(*qs, date="2026-08-27"):
    out = []
    for i, q in enumerate(qs):
        ident = f"atc-col-aus-scb-{date}-m{chr(97 + i)}"
        out.append(row(ident, "yes", q))
        out.append(row(ident, "no", q))
    return out


def bridge(rows_kept, rows_all, outcome, title, slug):
    return pm.bridge_explain(rows_kept, rows_all, outcome, title, slug)


class TestBaselineRecovery:
    def test_the_measured_template_resolves(self):
        """D0/D14: the venue's REAL wording family, from the 2026-08-26
        census, resolves for both polarities."""
        b = board(QB, QA, QD)
        h, why = bridge(b, b, "Yes", TITLE, SLUG)
        assert why == "ok" and h is not None
        assert h["side_norm"] == "yes" and h["question"] == QB
        h, why = bridge(b, b, "No", TITLE, SLUG)
        assert why == "ok" and h["side_norm"] == "no"
        assert h["question"] == QB

    def test_the_mirror_pick_takes_the_mirror_question(self):
        b = board(QB, QA, QD)
        h, why = bridge(b, b, "Yes", "Will Austin FC win on 2026-08-27?",
                        "col-aus-scb-2026-08-27-aus")
        assert why == "ok" and h["question"] == QA

    def test_draw_rows_are_never_selected_and_never_block(self):
        b = board(QD)
        assert bridge(b, b, "Yes", TITLE, SLUG)[0] is None
        b = board(QB, QD)
        assert bridge(b, b, "Yes", TITLE, SLUG)[1] == "ok"

    def test_month_name_title_resolves_and_mismatch_refuses(self):
        b = board(QB, QA, QD)
        assert bridge(b, b, "Yes", "Will SC Braga win on August 27?",
                      SLUG)[1] == "ok"
        assert bridge(b, b, "Yes", "Will SC Braga win on August 28?",
                      SLUG) == (None, "title_date_mismatch")
        assert bridge(b, b, "Yes", "Will SC Braga win on 2026-08-28?",
                      SLUG) == (None, "title_date_mismatch")
        assert bridge(b, b, "Yes", "Will SC Braga win on Floop 27?",
                      SLUG) == (None, "title_month_unknown")

    def test_the_date_never_reaches_the_line_parsers(self):
        """The measured misparse: _lines_of(TITLE) reads {'08','27'}
        and signed_line reads '-08'. Neither the title parser nor the
        strict question parser may consult them — the dates are
        consumed by the grammars."""
        assert pm._lines_of(TITLE) == {"08", "27"}
        assert pm.signed_line(TITLE) == "-08"
        for fn in (pm._bridge_title_subject, pm._q_parse_strict):
            src = inspect.getsource(fn)
            code = src.replace(fn.__doc__ or "", "")
            assert "_lines_of" not in code, fn.__name__
            assert "signed_line" not in code, fn.__name__


class TestVenueQuestionSinks:
    """Different propositions wearing win-adjacent wording. Each alone
    on the board must refuse — a sink must never be selected. The
    round-2 corpus added the venue's real derivative wordings (inning
    winners, covers, totals, halftime) to the round-1 constructions."""

    SINKS = [
        "Will SC Braga win or draw against Austin FC?",
        "Will SC Braga win the first half against Austin FC?",
        "Will SC Braga win to nil against Austin FC?",
        "Will SC Braga win without conceding against Austin FC?",
        "Will SC Braga win by 2 or more goals against Austin FC?",
        "Will SC Braga win in their match on aggregate?",
        "Will SC Braga win in their match in extra time?",
        "Will SC Braga win in their match by two or more goals?",
        "Will SC Braga win against Austin FC in extra time?",
        "Will SC Braga win against Austin FC on aggregate?",
        "Will SC Braga win against Austin FC in game 2 of the "
        "doubleheader?",
        "Will SC Braga win against Austin FC in the second game?",
        "Will SC Braga win on penalties?",
        "Will SC Braga keep a clean sheet?",
        "Will both teams score?",
        # Round-2 corpus, verbatim venue derivative families:
        "Will the SC Braga win the 7th inning vs the Austin FC?",
        "Will the SC Braga cover +17.5 vs the Austin FC in SCB vs AUS?",
        "Will the total in SCB vs AUS be more than 4.5?",
        "Will SC Braga lead Austin FC at halftime?",
        "Will SCB vs AUS be tied at halftime?",
        "Will the first 5 innings of Austin FC vs SC Braga end in a "
        "tie?",
    ]

    def test_every_sink_alone_refuses(self):
        for q in self.SINKS:
            b = board(q)
            h, _ = bridge(b, b, "Yes", TITLE, SLUG)
            assert h is None, f"sink selected: {q}"

    def test_the_partial_capture_asymmetry(self):
        """A RAW-PARSEABLE sink beside the true market BLOCKS —
        refusing even though the true market is present. An
        unparseable different-proposition is invisible, and the true
        market is selected. Both directions are load-bearing: partial
        capture is a measured production state."""
        b = board(QB, "Will SC Braga win against Austin FC in extra "
                      "time?")
        assert bridge(b, b, "Yes", TITLE, SLUG)[0] is None
        b = board(QB, "Will SC Braga win or draw against Austin FC?")
        h, why = bridge(b, b, "Yes", TITLE, SLUG)
        assert why == "ok" and h["question"] == QB


class TestRoundOneWordingsNowRefuse:
    """D1: the round-1 strict branches were IMAGINED wordings — the
    census measured would_resolve=0 through them, and the tournament
    executed wrong-market admissions through the dateless bare form
    against shipped code. They are gone from SELECTION and kept in
    BLOCKING (raw is byte-frozen)."""

    def test_bare_wording_alone_refuses(self):
        b = board(QB_BARE)
        assert bridge(b, b, "Yes", TITLE, SLUG) == \
            (None, "no_candidate_row")

    def test_bare_wording_beside_the_true_market_blocks(self):
        b = board(QB, QB_BARE)
        assert bridge(b, b, "Yes", TITLE, SLUG) == \
            (None, "event_scan_ambiguous")

    def test_vs_tail_alone_refuses_and_blocks_beside(self):
        """D7: 'vs' appears in zero observed win-questions."""
        qvs = "Will SC Braga win vs Austin FC" + TAIL
        b = board(qvs)
        assert bridge(b, b, "Yes", TITLE, SLUG) == \
            (None, "no_candidate_row")
        b = board(QB, qvs)
        assert bridge(b, b, "Yes", TITLE, SLUG) == \
            (None, "event_scan_ambiguous")

    def test_their_match_and_on_date_forms_refuse_alone(self):
        for q in ("Will SC Braga win against Austin FC in their "
                  "match?",
                  "Will SC Braga win against Austin FC on Aug 27?",
                  "Will SC Braga win against Austin FC on August 27 "
                  "2026?"):
            b = board(q)
            assert bridge(b, b, "Yes", TITLE, SLUG)[0] is None, q


class TestHistoricalIncidents:
    def test_ito_refuses(self):
        """Containment certified a wrong player once. 'ito' is not one
        of the two pre-date team codes, so the slug gate refuses before
        any name comparison can be tempted."""
        bb = [row("atc-wta-mito-aito-2026-08-27-ma", "yes",
                  "Will Mai Ito win against Aoi Ito?",
                  ev="atc-wta-mito-aito-2026-08-27",
                  evt="Mai Ito vs Aoi Ito"),
              row("atc-wta-mito-aito-2026-08-27-ao", "yes",
                  "Will Aoi Ito win against Mai Ito?",
                  ev="atc-wta-mito-aito-2026-08-27",
                  evt="Mai Ito vs Aoi Ito")]
        assert bridge(bb, bb, "Yes", "Will Ito win on 2026-08-27?",
                      "wta-mito-aito-2026-08-27-ito") == \
            (None, "code_not_team")

    def test_inversion_yes_never_takes_a_named_side(self):
        bb = [row("aec-col-aus-scb-2026-08-27", "sc braga", QB),
              row("aec-col-aus-scb-2026-08-27", "austin fc", QA)]
        assert bridge(bb, bb, "Yes", TITLE, SLUG)[0] is None

    def test_double_listing_refuses_by_count_in_any_order(self):
        """2026-08-23: a tie must never fall to venue ordering. The
        bridge has no ordered selection to fall to — it counts."""
        bb = board(QB, QB)
        assert bridge(bb, bb, "Yes", TITLE, SLUG)[0] is None
        rb = list(reversed(bb))
        assert bridge(rb, rb, "Yes", TITLE, SLUG)[0] is None

    def test_over_under_never_enters(self):
        b = board(QB)
        assert bridge(b, b, "Over", "Total 2.5", SLUG) == \
            (None, "not_yes_no")

    def test_lined_slugs_and_rows_refuse(self):
        b = board(QB)
        assert bridge(b, b, "Yes", "Will there be over 2.5 goals?",
                      "col-aus-scb-2026-08-27-total-2pt5") == \
            (None, "wrong_type")
        bb = [row("atc-col-aus-scb-2026-08-27-ma", "yes", QB,
                  line="2.5"),
              row("atc-col-aus-scb-2026-08-27-mb", "yes", QB,
                  signed="-08")]
        assert bridge(bb, bb, "Yes", TITLE, SLUG)[0] is None

    def test_draw_and_dnb_final_tokens_refuse(self):
        b = board(QD)
        assert bridge(b, b, "Yes",
                      "Will neither team win on 2026-08-27?",
                      "col-aus-scb-2026-08-27-draw") == \
            (None, "code_not_team")
        b2 = board(QB)
        assert bridge(b2, b2, "Yes", TITLE,
                      "col-aus-scb-2026-08-27-dnb") == \
            (None, "code_not_team")


class TestConstructedAttacks:
    def test_reserve_team_marker_survives_stripping(self):
        """'SC Braga B' must never merge into 'SC Braga' — which is why
        GENERIC_CLUB_TOKENS is furniture-only and there is no
        length-based token stripping."""
        b = board(QB)
        assert bridge(b, b, "Yes", "Will SC Braga B win on 2026-08-27?",
                      SLUG)[0] is None
        assert pm._distinctive("sc braga b") == frozenset({"braga", "b"})

    def test_generic_tokens_is_exactly_the_reviewed_ten(self):
        """Re-affirmed by round 2 the hard way: a design that added
        'sk' died with an executed kill (SK Rapid Wien vs FC Rapid
        Bucharest — the legal-form prefix IS the disambiguator between
        same-name clubs)."""
        assert pm.GENERIC_CLUB_TOKENS == frozenset(
            {"fc", "cf", "sc", "ac", "afc", "ca", "cd", "club", "the",
             "de"})

    def test_both_teams_title_refuses(self):
        b = board(QB)
        assert bridge(b, b, "Yes",
                      "Will SC Braga win vs Austin FC on 2026-08-27?",
                      SLUG) == (None, "title_not_win_shape")

    def test_negated_title_refuses(self):
        b = board(QB)
        assert bridge(b, b, "Yes",
                      "Will SC Braga not win on 2026-08-27?",
                      SLUG)[0] is None

    def test_same_city_code_collision_lands_as_refusal(self):
        """'manchestercity' startswith 'man' — the slug corroboration
        passes on the WRONG code. The opponent-corroboration clause
        then refuses selection. A collision must land as refusal, never
        as selection — in the measured tailed wording too."""
        t = " in the UECL match scheduled for Aug 27, 2026?"
        bb = [row("atc-epl-man-mci-2026-08-27-mu", "yes",
                  "Will Manchester United win against Manchester "
                  "City" + t,
                  ev="atc-epl-man-mci-2026-08-27",
                  evt="Manchester United vs Manchester City"),
              row("atc-epl-man-mci-2026-08-27-mc", "yes",
                  "Will Manchester City win against Manchester "
                  "United" + t,
                  ev="atc-epl-man-mci-2026-08-27",
                  evt="Manchester United vs Manchester City")]
        assert bridge(bb, bb, "Yes",
                      "Will Manchester City win on 2026-08-27?",
                      "epl-man-mci-2026-08-27-man") == \
            (None, "no_candidate_row")

    def test_subject_matching_neither_code_refuses(self):
        b = board(QB)
        assert bridge(b, b, "Yes", "Will Sao Paulo win on 2026-08-27?",
                      SLUG) == (None, "slug_corroboration_failed")

    def test_multi_event_pool_refuses_and_is_no_longer_vacuous(self):
        """Gate 11 was DEAD CODE until round 2: neither SELECT fetched
        event_slug, so every row read '' and the refusal could never
        fire. All three attack lenses found it independently."""
        bb = board(QB)
        bb2 = [dict(r, event_slug="atc-col-aus-scb-2026-08-27-2")
               for r in board(QA)]
        allr = bb + bb2
        assert bridge(allr, allr, "Yes", TITLE, SLUG) == \
            (None, "multi_event_pool")
        for fn in (pm.resolve_explain, pm.resolve):
            src = inspect.getsource(fn)
            assert "signed, event_slug FROM us_premap" in src, \
                fn.__name__

    def test_game2_disambiguated_identifier_refuses(self):
        """The one-game-captured doubleheader: the identifier's own
        post-date '-2' disambiguator refuses the row."""
        t = " in the UECL match scheduled for Jul 22, 2026?"
        bb = [row("atc-mlb-nyy-bos-2026-07-22-2-nyy", "yes",
                  "Will the New York Yankees win against the Boston "
                  "Red Sox" + t,
                  ev="atc-mlb-nyy-bos-2026-07-22-2",
                  evt="New York Yankees vs Boston Red Sox")]
        assert bridge(bb, bb, "Yes",
                      "Will the New York Yankees win on 2026-07-22?",
                      "mlb-nyy-bos-2026-07-22-nyy")[0] is None

    def test_digit_subject_refuses_in_the_safe_direction(self):
        bb = board("Will FC Schalke 04 win against Austin FC" + TAIL)
        assert bridge(bb, bb, "Yes",
                      "Will FC Schalke 04 win on 2026-08-27?",
                      "col-aus-s04-2026-08-27-s04")[0] is None

    def test_dateless_and_bare_slugs_refuse(self):
        b = board(QB)
        assert bridge(b, b, "Yes", "Will SC Braga win?",
                      "col-aus-scb-scb")[0] is None
        assert bridge(b, b, "Yes", TITLE,
                      "col-aus-scb-2026-08-27") == \
            (None, "no_subject_token")

    def test_identically_named_derby_refuses(self):
        bb = [row("atc-arg-riv-rvp-2026-08-27-ma", "yes",
                  "Will River Plate win against River Plate" + TAIL,
                  ev="atc-arg-riv-rvp-2026-08-27",
                  evt="River Plate vs River Plate")]
        assert bridge(bb, bb, "Yes",
                      "Will River Plate win on 2026-08-27?",
                      "arg-riv-rvp-2026-08-27-riv")[0] is None


class TestRoundTwoKills:
    """Every executed kill from the round-2 tournament, pinned as a
    refusal. Each of these walked every gate of some design (or of the
    shipped round-1 code) end to end before the synthesis closed it."""

    def test_wrong_game_identifier_dates_refuse(self):
        """D2: the tournament executed an admission against SHIPPED
        code through a pool whose identifiers carried 2026-08-21 under
        a 2026-08-27 slug. The identifier's embedded date must now
        equal the slug date."""
        bb = [row("atc-col-aus-scb-2026-08-21-ma", "yes", QB,
                  ev="atc-col-aus-scb-2026-08-21")]
        assert bridge(bb, bb, "Yes", TITLE, SLUG) == \
            (None, "no_candidate_row")
        bb = [row("atc-col-aus-scb-2026-09-15-ma", "yes", QB,
                  ev="atc-col-aus-scb-2026-09-15")]
        assert bridge(bb, bb, "Yes", TITLE, SLUG) == \
            (None, "no_candidate_row")
        assert pm._bridge_ident_ok(
            "atc-uecl-scb-aus-2026-08-21-ma", "2026-08-27") is False
        assert pm._bridge_ident_ok(
            "atc-uecl-scb-aus-2026-08-27-ma", "2026-08-27") is True
        assert pm._bridge_ident_ok(
            "atc-mlb-nyy-bos-2026-07-22-2-nyy", "2026-07-22") is False

    def test_league_whitelist_is_closed_and_kills_each_construction(self):
        """D3: an open league slot admitted a same-day basketball
        derby, a UEFA Youth League fixture and a Primera Division
        homonym. The league value is COMPETITION IDENTITY and the
        whitelist is the ONLY gate refusing some of these boards —
        'defensayjusticia'.startswith('def') is True, so the River
        Plate ARG/URU homonym passes every name gate."""
        for lg in ("Greek Basket League", "UEFA Youth League",
                   "UEFA Womens Champions League", "MLB"):
            q = ("Will SC Braga win against Austin FC in the "
                 f"{lg} match scheduled for Aug 27, 2026?")
            b = board(q)
            assert bridge(b, b, "Yes", TITLE, SLUG) == \
                (None, "no_candidate_row"), lg
        assert "defensayjusticia".startswith("def")
        bb = [row("atc-arg-riv-def-2026-08-27-ma", "yes",
                  "Will River Plate win against Defensa y Justicia in "
                  "the Primera Division match scheduled for "
                  "Aug 27, 2026?",
                  ev="atc-arg-riv-def-2026-08-27",
                  evt="River Plate vs Defensa y Justicia")]
        assert bridge(bb, bb, "Yes",
                      "Will River Plate win on 2026-08-27?",
                      "arg-riv-def-2026-08-27-riv") == \
            (None, "no_candidate_row")

    def test_league_scope_smuggle_refuses_and_blocks(self):
        """D4: scope words hiding inside the league slot."""
        for lg in ("first half of the UECL", "first leg of the UECL",
                   "UECL playoff"):
            q = ("Will SC Braga win against Austin FC in the "
                 f"{lg} match scheduled for Aug 27, 2026?")
            b = board(q)
            assert bridge(b, b, "Yes", TITLE, SLUG) == \
                (None, "no_candidate_row"), lg
        q = ("Will SC Braga win against Austin FC in the first half "
             "of the UECL match scheduled for Aug 27, 2026?")
        b = board(QB, q)
        assert bridge(b, b, "Yes", TITLE, SLUG) == \
            (None, "event_scan_ambiguous")

    def test_tail_date_mismatch_refuses(self):
        """D5, incl. the CONSCIOUS September cliff: 'Sept' is not in
        _BRIDGE_MONTHS (prefixes are 3 letters — 'sep'), so the first
        September fixture refuses until the probe's month_seen channel
        observes what the venue actually emits. A pinned miss, not a
        surprise."""
        b = board(QB.replace("Aug 27", "Aug 28"))
        assert bridge(b, b, "Yes", TITLE, SLUG) == \
            (None, "no_candidate_row")
        b = board(QB.replace("Aug 27", "Floop 27"))
        assert bridge(b, b, "Yes", TITLE, SLUG) == \
            (None, "no_candidate_row")
        qsept = ("Will SC Braga win against Austin FC in the UECL "
                 "match scheduled for Sept 3, 2026?")
        b = board(qsept, date="2026-09-03")
        assert bridge(b, b, "Yes",
                      "Will SC Braga win on 2026-09-03?",
                      "col-aus-scb-2026-09-03-scb") == \
            (None, "no_candidate_row")

    def test_venue_truncation_is_a_miss_that_still_blocks(self):
        """D6: the verbatim 110-char cut from the census. The end
        anchor makes it a strict miss; raw still parses it, so beside
        a complete sibling it BLOCKS — the safe direction."""
        full = ("Will Fenerbahce SK win against Olympique Lyonnais in "
                "the UEFA Champions League match scheduled for "
                "Aug 26, 2026?")
        trunc = ("Will Fenerbahce SK win against Olympique Lyonnais "
                 "in the UEFA Champions League match scheduled for "
                 "Aug 26, 202")
        def frow(ident, q):
            return row(ident, "yes", q, ev="atc-ucl-oly-fen-2026-08-26",
                       evt="Olympique Lyonnais vs Fenerbahce SK")
        tf = "Will Fenerbahce SK win on 2026-08-26?"
        sf = "ucl-oly-fen-2026-08-26-fen"
        bb = [frow("atc-ucl-oly-fen-2026-08-26-ma", full)]
        assert bridge(bb, bb, "Yes", tf, sf)[1] == "ok"
        bb = [frow("atc-ucl-oly-fen-2026-08-26-ma", trunc)]
        assert bridge(bb, bb, "Yes", tf, sf) == \
            (None, "no_candidate_row")
        bb = [frow("atc-ucl-oly-fen-2026-08-26-ma", full),
              frow("atc-ucl-oly-fen-2026-08-26-mb", trunc)]
        assert bridge(bb, bb, "Yes", tf, sf) == \
            (None, "event_scan_ambiguous")

    def test_the_delta_b_kill_boundary_west_midlands_police(self):
        """D8: the killed loosening matched a code against ANY
        distinctive token ('midlands'.startswith('mid')). The two-form
        corroboration keeps original token order, so West Midlands
        Police refuses while FC Midtjylland recovers."""
        assert pm._code_prefix_hit("west midlands police fc",
                                   "mid") is False
        assert pm._code_prefix_hit("fc midtjylland", "mid") is True
        def wrow(q, evt):
            return row("atc-uecl-bra-mid-2026-08-27-ma", "yes", q,
                       ev="atc-uecl-bra-mid-2026-08-27", evt=evt)
        tb = "Will SC Braga win on 2026-08-27?"
        sb = "uecl-bra-mid-2026-08-27-bra"
        bb = [wrow("Will SC Braga win against West Midlands Police FC "
                   "in the UECL match scheduled for Aug 27, 2026?",
                   "SC Braga vs West Midlands Police FC")]
        assert bridge(bb, bb, "Yes", tb, sb) == \
            (None, "no_candidate_row")
        bb = [wrow("Will SC Braga win against FC Midtjylland in the "
                   "UECL match scheduled for Aug 27, 2026?",
                   "SC Braga vs FC Midtjylland")]
        assert bridge(bb, bb, "Yes", tb, sb)[1] == "ok"

    def test_the_delta_c_kill_boundary_sk_rapid(self):
        """D9: the killed loosening stripped 'sk', merging SK Rapid
        into Rapid. 'sk' is NOT in the reviewed ten, so the subject
        sets stay distinct and set-equality refuses."""
        assert pm._distinctive("sk rapid") == frozenset({"sk", "rapid"})
        bb = [row("atc-uecl-rap-fcr-2026-08-27-ma", "yes",
                  "Will SK Rapid win against FC Rapid Bucharest in "
                  "the UECL match scheduled for Aug 27, 2026?",
                  ev="atc-uecl-rap-fcr-2026-08-27",
                  evt="SK Rapid vs FC Rapid Bucharest")]
        assert bridge(bb, bb, "Yes", "Will Rapid win on 2026-08-27?",
                      "uecl-rap-fcr-2026-08-27-rap") == \
            (None, "no_candidate_row")

    def test_the_vikingur_name_twins_both_refuse(self):
        """D10: KF Vikingur vs Vikingur Reykjavik — furniture ('kf')
        outside the reviewed ten and a code neither form can prefix.
        Both picks die at slug corroboration: honest misses, recorded
        by the probe's shadow channel for a round-3 review."""
        t = " in the UECL match scheduled for Aug 27, 2026?"
        bb = [row("atc-uecl-vik-vre-2026-08-27-ma", "yes",
                  "Will KF Vikingur win against Vikingur Reykjavik"
                  + t,
                  ev="atc-uecl-vik-vre-2026-08-27",
                  evt="KF Vikingur vs Vikingur Reykjavik"),
              row("atc-uecl-vik-vre-2026-08-27-mb", "yes",
                  "Will Vikingur Reykjavik win against KF Vikingur"
                  + t,
                  ev="atc-uecl-vik-vre-2026-08-27",
                  evt="KF Vikingur vs Vikingur Reykjavik")]
        assert bridge(bb, bb, "Yes",
                      "Will KF Vikingur win on 2026-08-27?",
                      "uecl-vik-vre-2026-08-27-vik") == \
            (None, "slug_corroboration_failed")
        assert bridge(bb, bb, "Yes",
                      "Will Vikingur Reykjavik win on 2026-08-27?",
                      "uecl-vik-vre-2026-08-27-vre") == \
            (None, "slug_corroboration_failed")

    def test_dateless_whale_title_refuses(self):
        """D11: a bare 'Will X win?' can be an aggregate/advance
        market riding a dated moneyline-shaped slug — the tournament
        constructed that admission through shipped code."""
        b = board(QB, QA, QD)
        assert bridge(b, b, "Yes", "Will SC Braga win?", SLUG) == \
            (None, "title_undated")

    def test_generic_pad_opponent_refuses_via_token_cap(self):
        """D12: set-equality alone admits furniture padding; the
        5-token cap refuses it. The padded row still raw-parses, so
        beside the true market it blocks."""
        qpad = ("Will SC Braga win against FK Austria Wien de the "
                "club in the UECL match scheduled for Aug 27, 2026?")
        b = board(qpad)
        assert bridge(b, b, "Yes", TITLE, SLUG) == \
            (None, "no_candidate_row")
        b = board(QB, qpad)
        assert bridge(b, b, "Yes", TITLE, SLUG) == \
            (None, "event_scan_ambiguous")


class TestHonestMisses:
    """D13: pinned AS misses, each with the round-3 evidence channel
    that could flip it. A silent recovery here would mean the grammar
    loosened without review."""

    def test_furniture_opponent_outside_the_ten_misses(self):
        """The verbatim corpus board: opp 'FK Austria Wien' with
        other='aus' — 'fk' is not in the reviewed ten, so neither form
        starts with 'aus'. The probe's shadow-eval sizes this class."""
        bb = [row("atc-col-aus-scb-2026-08-27-ma", "yes",
                  "Will SC Braga win against FK Austria Wien" + TAIL,
                  evt="SC Braga vs FK Austria Wien")]
        assert bridge(bb, bb, "Yes", TITLE, SLUG) == \
            (None, "no_candidate_row")

    def test_short_form_title_misses_by_set_inequality(self):
        """'HNK Rijeka' pick titled bare 'Rijeka': {'rijeka'} !=
        {'hnk','rijeka'} — set EQUALITY, never containment."""
        bb = [row("atc-uecl-rij-mid-2026-08-27-ma", "yes",
                  "Will HNK Rijeka win against FC Midtjylland" + TAIL,
                  ev="atc-uecl-rij-mid-2026-08-27",
                  evt="HNK Rijeka vs FC Midtjylland")]
        assert bridge(bb, bb, "Yes", "Will Rijeka win on 2026-08-27?",
                      "uecl-rij-mid-2026-08-27-rij")[0] is None


class TestRecoveryPins:
    """D14: the two-form corroboration's INTENDED recoveries, pinned
    so a regression is loud."""

    def test_ca_patronato_parana_board_resolves(self):
        """The measured Primera Nacional shape: 'ca' is in the ten, so
        'patronatoparana'.startswith('pat') corroborates."""
        bb = [row("atc-arg2-pat-atl-2026-08-26-ma", "yes",
                  "Will CA Atlanta win against CA Patronato Parana in "
                  "the Primera Nacional match scheduled for "
                  "Aug 26, 2026?",
                  ev="atc-arg2-pat-atl-2026-08-26",
                  evt="CA Patronato Parana vs CA Atlanta")]
        h, why = bridge(bb, bb, "Yes",
                        "Will CA Atlanta win on 2026-08-26?",
                        "arg2-pat-atl-2026-08-26-atl")
        assert why == "ok" and h is not None

    def test_subject_containing_win_parses_stably(self):
        """Grammar-stability pin: a club named 'Win City' must parse
        with the full name as subject, not stop at the first 'win'."""
        n = " ".join(pm._norm(
            "Will Win City win against Borax in the UECL match "
            "scheduled for Aug 27, 2026?").split())
        m = pm._BRIDGE_Q_STRICT_RE.fullmatch(n)
        assert m is not None and m.group("subj") == "win city"


class TestPropertyPins:
    """D15: the structural invariants the synthesis rests on."""

    def test_raw_regex_is_byte_frozen(self):
        """Step-3 blocking semantics must not move: everything the old
        strict admitted still BLOCKS."""
        assert pm._BRIDGE_Q_RE.pattern == (
            r"^will (?:the )?(?P<subj>.+?) win"
            r"(?: (?:against|vs) (?P<opp>[a-z0-9 ]+?))?"
            r"(?: (?:in|on) (?:their )?(?:match|game))?"
            r"(?: on (?P<qmon>[a-z]+) (?P<qday>\d{1,2})"
            r"(?: (?P<qyr>\d{4}))?)?$")

    def test_strict_is_a_subset_of_raw(self):
        """Every strict-admitted string raw-parses with the IDENTICAL
        subject group — a strict candidate is always self-visible to
        the blocking scan."""
        for q in (QB, QA,
                  "Will CA Atlanta win against CA Patronato Parana in "
                  "the Primera Nacional match scheduled for "
                  "Aug 26, 2026?",
                  "Will Fenerbahce SK win against Olympique Lyonnais "
                  "in the UEFA Champions League match scheduled for "
                  "Aug 26, 2026?"):
            n = " ".join(pm._norm(q).split())
            ms = pm._BRIDGE_Q_STRICT_RE.fullmatch(n)
            mr = pm._q_parse_raw(q)
            assert ms is not None, q
            assert mr is not None, q
            assert ms.group("subj") == mr.group("subj"), q

    def test_the_league_whitelist_is_exactly_the_three_attested(self):
        """Each attested by a complete untruncated production
        win-question (2026-08-26 census). An addition is a LOOSENING
        with the review obligations written beside the constant."""
        assert pm._BRIDGE_LEAGUES == frozenset(
            {"uecl", "primera nacional", "uefa champions league"})

    def test_the_name_token_cap_is_five(self):
        assert pm._BRIDGE_NAME_TOKEN_CAP == 5

    def test_two_form_helpers_never_reorder_or_invent(self):
        assert pm._collapsed_distinctive("fc midtjylland") == \
            "midtjylland"
        assert pm._collapsed_distinctive("ca patronato parana") == \
            "patronatoparana"
        assert pm._collapsed_distinctive("hnk rijeka") == "hnkrijeka"
        assert pm._collapsed_distinctive("sk rapid") == "skrapid"


class TestPhaseZeroBoundaries:
    def test_resolve_takes_no_bridge_argument_yet(self):
        """Phase 0 is measurement. The order path cannot consume a
        bridge hit until the round-3 census evidence is in and the
        would_resolve hand audit (resolution rules included) is
        zero-mismatch."""
        sig = inspect.signature(pm.resolve)
        assert "bridge" not in sig.parameters

    def test_match_side_is_byte_untouched_by_the_bridge(self):
        """The primary matcher must not know the bridge exists — a
        primary hit always wins, and the bridge is consulted only on
        refusal, by callers, never from inside."""
        src = inspect.getsource(pm.match_side)
        assert "bridge" not in src

    def test_the_probe_carries_the_round3_evidence_channels(self):
        src = inspect.getsource(pm.resolve_explain)
        assert "bridge_explain(" in src
        assert "would_resolve" in src
        for ch in ('"audit"', '"his"', '"row_gates"', "trace=btrace",
                   "venue_q_sample"):
            assert ch in src, ch

    def test_tracing_changes_no_decision(self):
        """The same inputs must produce the same verdict with and
        without a trace dict — tracing only records."""
        b = board(QB, QA, QD)
        plain = pm.bridge_explain(b, b, "Yes", TITLE, SLUG)
        traced = pm.bridge_explain(b, b, "Yes", TITLE, SLUG, trace={})
        assert plain == traced
        b2 = board(QB.replace("Aug 27", "Aug 28"))
        assert pm.bridge_explain(b2, b2, "Yes", TITLE, SLUG) == \
            pm.bridge_explain(b2, b2, "Yes", TITLE, SLUG, trace={})

    def test_live_executor_never_touches_the_bridge(self):
        import inspect as _i

        from sportsassets import live_executor as le

        assert "bridge_explain" not in _i.getsource(le)
        assert "match_side_bridge" not in _i.getsource(le)
