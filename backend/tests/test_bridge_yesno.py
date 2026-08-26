"""The yes/no bridge, tested with the attack corpus that designed it.

no_side_match is 239 of 400 sampled unmapped rejections (59.8%) — the
"Will TEAM win?" family structurally refused because the two feeds word
the same proposition differently. The bridge (premap.bridge_explain)
triangulates his slug code, his title's team, the venue question's
subject AND opponent, and the row's own event_title, refusing on any
disagreement.

Every test here is either a shipped incident replay or a constructed
wrong-market attack from the adversarial design pass (56 executed
checks, 0 failures, harness bridge_verify.py). The rule set:

  * double uniqueness — exactly one candidate AND exactly one
    event-wide subject match; no ordered selection (2026-08-23);
  * literal yes/no polarity — never a named side (2026-08-24 inversion);
  * distinctive-token SET EQUALITY — never containment (Ito);
  * anchored closed-tail grammars on BOTH feeds (bare-Yes/No);
  * unlined, unsigned rows only (line-equality rule untouched);
  * his title's date consumed by the grammar, never by _lines_of.

PHASE 0: nothing on the order path consumes a bridge hit. resolve()
takes no bridge argument; the probe in resolve_explain and the census
tally are the only consumers, and they are read-only.
"""

from __future__ import annotations

import inspect

from sportsassets.workers import premap as pm

SLUG = "col-aus-scb-2026-08-27-scb"
TITLE = "Will SC Braga win on 2026-08-27?"
QB = "Will SC Braga win against Austin FC?"
QA = "Will Austin FC win against SC Braga?"
QD = "Will the match end in a draw?"


def row(ident, side, q, ev="atc-col-aus-scb-2026-08-27",
        evt="Austin FC vs SC Braga", line="", signed="",
        intent="ORDER_INTENT_BUY_LONG"):
    return {"identifier": ident, "side_norm": side, "question": q,
            "event_slug": ev, "event_title": evt, "line": line,
            "signed": signed, "intent": intent, "kind": "side"}


def board(*qs):
    out = []
    for i, q in enumerate(qs):
        ident = f"atc-col-aus-scb-2026-08-27-m{chr(97 + i)}"
        out.append(row(ident, "yes", q))
        out.append(row(ident, "no", q))
    return out


def bridge(rows_kept, rows_all, outcome, title, slug):
    return pm.bridge_explain(rows_kept, rows_all, outcome, title, slug)


class TestBaselineRecovery:
    def test_the_sc_braga_production_example_resolves(self):
        """The exact row from the 2026-08-26 unmapped census."""
        b = board(QB, QA, QD)
        h, why = bridge(b, b, "Yes", TITLE, SLUG)
        assert why == "ok" and h is not None
        assert h["side_norm"] == "yes" and h["question"] == QB

    def test_no_takes_the_no_side_of_the_same_market(self):
        b = board(QB, QA, QD)
        h, why = bridge(b, b, "No", TITLE, SLUG)
        assert why == "ok" and h["side_norm"] == "no"
        assert h["question"] == QB

    def test_month_name_date_resolves_and_mismatch_refuses(self):
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
        """The measured misparse that refuses every dated yes/no title
        today: _lines_of(TITLE) reads {'08','27'} and signed_line reads
        '-08'. The bridge must not consult either on his title."""
        assert pm._lines_of(TITLE) == {"08", "27"}
        assert pm.signed_line(TITLE) == "-08"
        # CODE, not prose: the docstring legitimately NAMES the two
        # functions while explaining why the code must not call them.
        fn = pm._bridge_title_subject
        src = inspect.getsource(fn)
        doc = fn.__doc__ or ""
        code = src.replace(doc, "")
        assert "_lines_of" not in code and "signed_line" not in code


class TestVenueQuestionSinks:
    """Different propositions wearing win-adjacent wording. Each alone
    on the board must refuse — a sink must never be selected."""

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
    ]

    def test_every_sink_alone_refuses(self):
        for q in self.SINKS:
            b = board(q)
            h, _ = bridge(b, b, "Yes", TITLE, SLUG)
            assert h is None, f"sink selected: {q}"

    def test_the_partial_capture_asymmetry(self):
        """A PARSEABLE sink (polluted opp) beside the true market
        BLOCKS — refusing even though the true market is present. An
        UNPARSEABLE different-proposition is invisible, and the true
        market is selected. Both directions are load-bearing: partial
        capture is a measured production state."""
        b = board(QB, "Will SC Braga win against Austin FC in extra time?")
        assert bridge(b, b, "Yes", TITLE, SLUG)[0] is None
        b = board(QB, "Will SC Braga win or draw against Austin FC?")
        h, why = bridge(b, b, "Yes", TITLE, SLUG)
        assert why == "ok" and h["question"] == QB


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
        bb = board(QB, "Will SC Braga win against Austin FC?")
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
        as selection."""
        bb = [row("atc-epl-man-mci-2026-08-27-mu", "yes",
                  "Will Manchester United win against Manchester City?",
                  ev="atc-epl-man-mci-2026-08-27",
                  evt="Manchester United vs Manchester City"),
              row("atc-epl-man-mci-2026-08-27-mc", "yes",
                  "Will Manchester City win against Manchester United?",
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

    def test_multi_event_pool_refuses(self):
        bb = board(QB)
        bb2 = [dict(r, event_slug="atc-col-aus-scb-2026-08-27-2")
               for r in board(QA)]
        allr = bb + bb2
        assert bridge(allr, allr, "Yes", TITLE, SLUG) == \
            (None, "multi_event_pool")

    def test_opponent_failing_corroboration_refuses(self):
        bb = [dict(r, event_title="FC Porto vs SC Braga")
              for r in board("Will SC Braga win against FC Porto?")]
        assert bridge(bb, bb, "Yes", TITLE, SLUG)[0] is None

    def test_game2_disambiguated_identifier_refuses(self):
        """The one-game-captured doubleheader: the identifier's own
        post-date '-2' disambiguator refuses the row."""
        bb = [row("atc-mlb-nyy-bos-2026-07-22-2-nyy", "yes",
                  "Will the New York Yankees win against the Boston "
                  "Red Sox?",
                  ev="atc-mlb-nyy-bos-2026-07-22-2",
                  evt="New York Yankees vs Boston Red Sox")]
        assert bridge(bb, bb, "Yes",
                      "Will the New York Yankees win on 2026-07-22?",
                      "mlb-nyy-bos-2026-07-22-nyy")[0] is None

    def test_digit_subject_refuses_in_the_safe_direction(self):
        bb = board("Will FC Schalke 04 win against Austin FC?")
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

    def test_no_against_wording_unselectable_but_blocking(self):
        """Off the attested venue family: unselectable alone, and it
        BLOCKS a selectable sibling — the asymmetry that makes partial
        knowledge refuse rather than guess."""
        bb = board("Will SC Braga win?")
        assert bridge(bb, bb, "Yes", TITLE, SLUG)[0] is None
        bb = board(QB, "Will SC Braga win?")
        assert bridge(bb, bb, "Yes", TITLE, SLUG)[0] is None

    def test_identically_named_derby_refuses(self):
        bb = [row("atc-arg-riv-rvp-2026-08-27-ma", "yes",
                  "Will River Plate win against River Plate?",
                  ev="atc-arg-riv-rvp-2026-08-27",
                  evt="River Plate vs River Plate")]
        assert bridge(bb, bb, "Yes",
                      "Will River Plate win on 2026-08-27?",
                      "arg-riv-rvp-2026-08-27-riv")[0] is None


class TestPhaseZeroBoundaries:
    def test_resolve_takes_no_bridge_argument_yet(self):
        """Phase 0 is measurement. The order path cannot consume a
        bridge hit until the census has sized the class and the
        certification plan is in place."""
        sig = inspect.signature(pm.resolve)
        assert "bridge" not in sig.parameters

    def test_match_side_is_byte_untouched_by_the_bridge(self):
        """The primary matcher must not know the bridge exists — a
        primary hit always wins, and the bridge is consulted only on
        refusal, by callers, never from inside."""
        src = inspect.getsource(pm.match_side)
        assert "bridge" not in src

    def test_the_probe_is_wired_into_resolve_explain(self):
        src = inspect.getsource(pm.resolve_explain)
        assert "bridge_explain(" in src
        assert "would_resolve" in src

    def test_live_executor_never_touches_the_bridge(self):
        import inspect as _i

        from sportsassets import live_executor as le

        assert "bridge_explain" not in _i.getsource(le)
        assert "match_side_bridge" not in _i.getsource(le)
