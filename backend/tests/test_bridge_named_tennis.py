"""The named-tennis bridge, pinned by the tournament that designed it.

The 2026-08-27 census measured the named-moneyline class at 91 picks
per 48h and captured the venue's plain wording — tennis, named sides.
The grounding then proved the tennis class dies at ONE clause: the
venue question's '1:30 AM UTC' clock time is stamped as line='30' by
_market_rows, and match_side's _lined_ok vetoes the already-successful
exact name-equality hit — the same misparse family as the date-as-line
bug that killed the yes/no class.

The obvious fix (blank the stamp) was EXECUTED TO DEATH in the
tournament: un-poisoning the line arms match_side's surname
CONTAINMENT tier on the live order path ('Sena Saito' takes Airi
Saito's row). So the stamp stays; this lane quarantines the clock
artifact behind its own witness and selects by set equality only. Two
designs died in attack (9 verified kills); every kill is pinned here.

PHASE 0: only resolve_explain's probe consumes the lane. resolve()
takes no named argument; match_side, _market_rows, _lines_of and
bridge_explain are byte-untouched.
"""

from __future__ import annotations

import inspect

from sportsassets.workers import premap as pm

Q = ("Who will win in the upcoming tennis event Hiromasa Koyama vs "
     "Luca Castelnuovo scheduled for August 27, 2026 at 1:30 AM UTC?")
WT = "Koyama vs Castelnuovo"
WE = "Hiromasa Koyama vs Luca Castelnuovo"
WS = "itf-koyama-castelnuovo-2026-08-27"


def vrow(side, intent, line="30",
         ident="aec-itfme-hirkoy-lucacas-2026-08-27", q=Q, kind="side",
         ev_slug=None, signed="",
         ev_title="Hiromasa Koyama vs. Luca Castelnuovo"):
    return {"identifier": ident, "side_norm": side, "kind": kind,
            "line": line, "question": q, "event_title": ev_title,
            "intent": intent, "signed": signed,
            "event_slug": ev_slug or ident[4:], "market_slug": ident}


K = vrow("hiromasa koyama", "ORDER_INTENT_BUY_LONG")
C = vrow("luca castelnuovo", "ORDER_INTENT_BUY_SHORT")


def run(rows, outcome, title, slug, ev, all_rows=None):
    t: dict = {}
    hit, why = pm.named_ml_bridge_explain(rows, all_rows or rows,
                                          outcome, title, slug, ev,
                                          trace=t)
    return hit, why, t


class TestAttestedRecovery:
    def test_attested_pair_recovers_both_polarities(self):
        """B1/B1b: the verbatim census pair, poisoned line intact.
        Polarity is structurally single-path: his pick reaches only
        his OWN name's row; intent is the venue's stored side."""
        h, w, _ = run([K, C], "Hiromasa Koyama", WT, WS, WE)
        assert w == "ok" and h is K
        assert h["intent"] == "ORDER_INTENT_BUY_LONG"
        h, w, _ = run([K, C], "Luca Castelnuovo", WT, WS, WE)
        assert w == "ok" and h is C
        assert h["intent"] == "ORDER_INTENT_BUY_SHORT"

    def test_tournament_prefixed_title_recovers(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "US Open, Qualification ITF: Koyama vs "
                      "Castelnuovo", WS, WE)
        assert w == "ok"

    def test_unlined_rows_also_recover(self):
        """B1d: the WTA-style already-unlined shape."""
        h, w, _ = run([dict(K, line=""), dict(C, line="")],
                      "Hiromasa Koyama", WT, WS, WE)
        assert w == "ok"

    def test_the_poisoned_pair_still_refuses_in_match_side(self):
        """The grounding's A1 anchor: TODAY's matcher refuses the
        attested pair at _lined_ok — pinned so the lane's reason for
        existing stays measured."""
        assert pm.match_side([K, C], "Hiromasa Koyama", WT,
                             "itf-koyama-castelnuovo-2026-08-27") \
            is None
        assert pm._lines_of(Q) == {"30"}


class TestTwinGate:
    def test_surname_containment_twin_refuses(self):
        """B2: the D1 kill — 'Sena Saito' must never take Airi
        Saito's row. Equality only, never containment."""
        qb = ("Who will win in the upcoming tennis event Airi Saito "
              "vs Aoi Uchida scheduled for August 27, 2026 at "
              "1:30 AM UTC?")
        b1 = vrow("airi saito", "ORDER_INTENT_BUY_LONG", line="",
                  ident="aec-itfme-airsai-aoiuch-2026-08-27", q=qb,
                  ev_title="Airi Saito vs. Aoi Uchida")
        b2 = vrow("aoi uchida", "ORDER_INTENT_BUY_SHORT", line="",
                  ident="aec-itfme-airsai-aoiuch-2026-08-27", q=qb,
                  ev_title="Airi Saito vs. Aoi Uchida")
        h, w, _ = run([b1, b2], "Sena Saito", "Saito vs Uchida",
                      "itf-saito-uchida-2026-08-27",
                      "Sena Saito vs Mai Uchida")
        assert h is None

    def test_ji_kim_name_twin_refuses_on_opponent(self):
        """B3: D2-I1 — the whale pair must equal the venue pair
        bijectively; his opponent Hyun Lee != venue's Sung Lee."""
        qj = ("Who will win in the upcoming tennis event Ji Kim vs "
              "Sung Lee scheduled for August 27, 2026 at 1:30 AM "
              "UTC?")
        j1 = vrow("ji kim", "ORDER_INTENT_BUY_LONG",
                  ident="aec-itfme-jikim-sunlee-2026-08-27", q=qj,
                  ev_title="Ji Kim vs. Sung Lee")
        j2 = vrow("sung lee", "ORDER_INTENT_BUY_SHORT",
                  ident="aec-itfme-jikim-sunlee-2026-08-27", q=qj,
                  ev_title="Ji Kim vs. Sung Lee")
        h, w, _ = run([j1, j2], "Ji Kim", "Kim vs Lee",
                      "itf-kim-lee-2026-08-27", "Ji Kim vs Hyun Lee")
        assert h is None and w in ("opponent_mismatch",
                                   "no_candidate_row")

    def test_cross_gender_twin_refuses(self):
        """B4: D2-I2 — same mechanism, itfme fold direction."""
        qg = ("Who will win in the upcoming tennis event Yuki Ito vs "
              "Kaito Sato scheduled for August 27, 2026 at 4:00 AM "
              "UTC?")
        g1 = vrow("yuki ito", "ORDER_INTENT_BUY_LONG", line="00",
                  ident="aec-itfme-yukito-kaisat-2026-08-27", q=qg,
                  ev_title="Yuki Ito vs. Kaito Sato")
        g2 = vrow("kaito sato", "ORDER_INTENT_BUY_SHORT", line="00",
                  ident="aec-itfme-yukito-kaisat-2026-08-27", q=qg,
                  ev_title="Yuki Ito vs. Kaito Sato")
        h, w, _ = run([g1, g2], "Yuki Ito", "Ito vs Sato",
                      "itf-ito-sato-2026-08-27", "Yuki Ito vs Rina Sato")
        assert h is None

    def test_opponent_witness_is_mandatory(self):
        """The twin gate's precondition: a feed that does not state
        the opponent's FULL name cannot rule out a twin."""
        h, w, _ = run([K, C], "Hiromasa Koyama", WT, WS,
                      "Hiromasa Koyama vs Castelnuovo")
        assert h is None and w == "opponent_witness_thin"


class TestTourAttestation:
    def test_atp_refuses_tour_unknown_and_is_measured(self):
        """B5: D2-I3 — only the attested itfme family ships; ATP/WTA
        refuse with the verbatim lg recorded for a future reviewed
        entry."""
        qa = ("Who will win in the upcoming tennis event Liam Draxl "
              "vs Andrew Johnson scheduled for August 27, 2026 at "
              "3:00 PM UTC?")
        a1 = vrow("liam draxl", "ORDER_INTENT_BUY_LONG", line="00",
                  ident="aec-atp-liadra-andjoh-2026-08-27", q=qa,
                  ev_title="Liam Draxl vs. Andrew Johnson")
        a2 = vrow("andrew johnson", "ORDER_INTENT_BUY_SHORT",
                  line="00",
                  ident="aec-atp-liadra-andjoh-2026-08-27", q=qa,
                  ev_title="Liam Draxl vs. Andrew Johnson")
        h, w, t = run([a1, a2], "Liam Draxl",
                      "US Open, Qualification ATP: Draxl vs Johnson",
                      "atp-draxl-johnson-2026-08-27",
                      "Liam Draxl vs Andrew Johnson")
        assert h is None and w == "tour_unknown"
        assert t.get("lg_pair_seen")

    def test_team_sport_picks_refuse_and_are_counted(self):
        """B9k: the deferred team-sports class lands at tour_unknown
        BY DESIGN — measured, never admitted, until the plain named
        wording is attested."""
        t1 = {"identifier": "atc-mlb-min-ath-2026-08-26-i5-min",
              "side_norm": "yes", "kind": "side", "line": "",
              "question": "Will the Minnesota Twins win the 5th "
                          "inning vs the Athletics?",
              "event_title": "Twins vs. Athletics",
              "intent": "ORDER_INTENT_BUY_LONG", "signed": "",
              "event_slug": "mlb-min-ath-2026-08-26",
              "market_slug": "atc-mlb-min-ath-2026-08-26-i5-min"}
        h, w, _ = run([t1], "Minnesota Twins", "Twins vs Athletics",
                      "mlb-min-oak-2026-08-26-min",
                      "Twins vs Athletics")
        assert h is None and w == "tour_unknown"

    def test_the_tour_map_is_exactly_the_attested_family(self):
        assert pm._NAMED_TOUR_OF == {"itf": "itf", "itfme": "itf"}


class TestDerivativeSiblings:
    QS = ("Who will win in the upcoming tennis event Hiromasa Koyama "
          "vs Luca Castelnuovo (1st Set) scheduled for August 27, "
          "2026 at 1:30 AM UTC?")

    def _set_rows(self):
        sk = vrow("hiromasa koyama", "ORDER_INTENT_BUY_LONG", line="",
                  ident="aec-itfme-hirkoy-lucacas-set1-2026-08-27",
                  q=self.QS)
        sc = dict(sk, side_norm="luca castelnuovo 1st set")
        return sk, sc

    def test_set_winner_is_structurally_non_candidate(self):
        """B6: the 8-token identifier fails the exactly-7 ident law
        before any wording is read."""
        sk, sc = self._set_rows()
        h, w, _ = run([sk, sc], "Hiromasa Koyama", WT, WS, WE)
        assert h is None

    def test_stale_parent_plus_derivative_refuses_both(self):
        """B6b: the D1 kill window (poisoned parent self-vetoing,
        fresh derivative surviving) refuses HERE via the event scan."""
        sk, sc = self._set_rows()
        h, w, _ = run([K, C], "Hiromasa Koyama", WT, WS, WE,
                      all_rows=[K, C, sk, sc])
        assert h is None and w in ("event_scan_ambiguous",
                                   "multi_event_pool")

    def test_second_meeting_marker_blocks_both_directions(self):
        """B6c/B6d: D2-S1 — the prefix-startswith event scan sees
        the '(2nd meeting)' sibling wherever the marker hides."""
        q2 = ("Who will win in the upcoming tennis event Hiromasa "
              "Koyama vs Luca Castelnuovo (2nd meeting) scheduled "
              "for August 27, 2026 at 4:30 AM UTC?")
        m1 = vrow("hiromasa koyama", "ORDER_INTENT_BUY_LONG",
                  ident="aec-itfme-hirkoy-lucacas-2026-08-27-2",
                  q=q2, ev_slug="itfme-hirkoy-lucacas-2026-08-27")
        for pick in ("Luca Castelnuovo", "Hiromasa Koyama"):
            h, w, _ = run([K, C, m1], pick, WT, WS, WE,
                          all_rows=[K, C, m1])
            assert h is None and w == "event_scan_ambiguous", pick

    def test_whale_set_suffix_refuses(self):
        """B9f: a '-set' post-date suffix that does not build from
        his outcome refuses slug_pick_mismatch."""
        # Named 1.1 refuses EARLIER: the suffix must BE one of his
        # slug's codes before any building is consulted.
        h, w, _ = run([K, C], "Hiromasa Koyama", WT,
                      "itf-koyama-castelnuovo-2026-08-27-set", WE)
        assert h is None and w == "slug_suffix_not_code"

    def test_derivative_marker_in_title_prefix_refuses(self):
        """B9g: 'First Set Winner: X vs Y' — the dropped tournament
        prefix is scanned before it is dropped."""
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "First Set Winner: Koyama vs Castelnuovo",
                      WS, WE)
        assert h is None and w == "title_prefix_derivative"


class TestDoubles:
    def test_doubles_refuse_on_both_feeds(self):
        """B7/B7b: 'and' is a bad token in every name slot; a singles
        pick can never select a doubles row."""
        qd = ("Who will win in the upcoming tennis event Hiromasa "
              "Koyama and Ren Nakamura vs Luca Castelnuovo and Marco "
              "Rossi scheduled for August 27, 2026 at 1:30 AM UTC?")
        d1 = vrow("hiromasa koyama and ren nakamura",
                  "ORDER_INTENT_BUY_LONG",
                  ident="aec-itfme-koynak-casros-2026-08-27", q=qd,
                  ev_title="Hiromasa Koyama and Ren Nakamura vs. "
                           "Luca Castelnuovo and Marco Rossi")
        d2 = vrow("luca castelnuovo and marco rossi",
                  "ORDER_INTENT_BUY_SHORT",
                  ident="aec-itfme-koynak-casros-2026-08-27", q=qd,
                  ev_title="Hiromasa Koyama and Ren Nakamura vs. "
                           "Luca Castelnuovo and Marco Rossi")
        h, w, _ = run([d1, d2], "Hiromasa Koyama and Ren Nakamura",
                      "Koyama and Nakamura vs Castelnuovo and Rossi",
                      "itf-koyamanakamura-castelnuovorossi-2026-08-27",
                      "Hiromasa Koyama and Ren Nakamura vs Luca "
                      "Castelnuovo and Marco Rossi")
        assert h is None and w in ("outcome_name_bad",
                                   "doubles_shape")
        h, w, _ = run([d1, d2], "Hiromasa Koyama", WT, WS, WE)
        assert h is None


class TestSideTruth:
    def test_contract_kind_refuses(self):
        h, w, _ = run([vrow("hiromasa koyama",
                            "ORDER_INTENT_BUY_LONG",
                            kind="contract")],
                      "Hiromasa Koyama", WT, WS, WE)
        assert h is None

    def test_lone_side_refuses_sibling_missing(self):
        """B8b: D2-S2 — a candidate whose sibling is absent is
        unverifiable (degraded side expansion)."""
        h, w, _ = run([C], "Luca Castelnuovo", WT, WS, WE)
        assert h is None and w == "sibling_side_missing"

    def test_duplicated_intent_refuses(self):
        h, w, _ = run([K, dict(C, intent="ORDER_INTENT_BUY_LONG")],
                      "Luca Castelnuovo", WT, WS, WE)
        assert h is None and w == "sibling_intent_broken"


class TestItoLaw:
    QI = ("Who will win in the upcoming tennis event Mai Ito vs Aoi "
          "Ito scheduled for August 27, 2026 at 1:30 AM UTC?")

    def _board(self):
        i1 = vrow("mai ito", "ORDER_INTENT_BUY_LONG",
                  ident="aec-itfme-maiito-aoiito-2026-08-27",
                  q=self.QI, ev_title="Mai Ito vs. Aoi Ito")
        i2 = vrow("aoi ito", "ORDER_INTENT_BUY_SHORT",
                  ident="aec-itfme-maiito-aoiito-2026-08-27",
                  q=self.QI, ev_title="Mai Ito vs. Aoi Ito")
        return i1, i2

    def test_bare_surname_outcome_refuses(self):
        h, w, _ = run([K, C], "Ito", WT,
                      "itf-ito-castelnuovo-2026-08-27", WE)
        assert h is None and w == "outcome_thin"

    def test_surname_title_cannot_corroborate_twin_codes(self):
        """B9b-1: on a same-surname board the codes carry more than
        the surname ('maiito'/'aoiito') — a surname-only title
        refuses at the witness itself."""
        i1, i2 = self._board()
        h, w, _ = run([i1, i2], "Mai Ito", "Ito vs Ito",
                      "itf-maiito-aoiito-2026-08-27",
                      "Mai Ito vs Aoi Ito")
        assert h is None and w == "title_slug_mismatch"

    def test_full_name_title_resolves_the_two_ito_board(self):
        i1, i2 = self._board()
        h, w, _ = run([i1, i2], "Mai Ito", "Mai Ito vs Aoi Ito",
                      "itf-maiito-aoiito-2026-08-27",
                      "Mai Ito vs Aoi Ito")
        assert w == "ok" and h is i1

    def test_degenerate_codes_refuse(self):
        i1, i2 = self._board()
        h, w, _ = run([i1, i2], "Mai Ito", "Ito vs Ito",
                      "itf-ito-ito-2026-08-27", "Mai Ito vs Aoi Ito")
        assert h is None and w == "degenerate_codes"

    def test_identical_fold_pair_refuses(self):
        """B9h: two players whose names fold identically make an
        unresolvable board."""
        qr = ("Who will win in the upcoming tennis event Yuki Ito vs "
              "Yuki Ito scheduled for August 27, 2026 at 1:30 AM "
              "UTC?")
        r1 = vrow("yuki ito", "ORDER_INTENT_BUY_SHORT",
                  ident="aec-itfme-yukito-yukitob-2026-08-27", q=qr,
                  ev_title="Yuki Ito vs. Yuki Ito")
        h, w, _ = run([r1], "Yuki Ito", "Ito vs Ito",
                      "itf-yukito-yukitob-2026-08-27",
                      "Yuki Ito vs Yuki Ito")
        assert h is None


class TestClockQuarantine:
    def test_smuggled_real_line_refuses(self):
        """B9d: a real bet line can never authenticate as the clock
        artifact — it fails both arms of the quarantine."""
        h, w, _ = run([dict(K, line="21.5"), C], "Hiromasa Koyama",
                      WT, WS, WE)
        assert h is None

    def test_leading_zero_minutes_recover(self):
        """B9e: '1:05 AM' stamps line='05'; verbatim equality keeps
        the zero."""
        q05 = Q.replace("1:30", "1:05")
        assert pm._lines_of(q05) == {"05"}
        k = vrow("hiromasa koyama", "ORDER_INTENT_BUY_LONG",
                 line="05", q=q05)
        c = vrow("luca castelnuovo", "ORDER_INTENT_BUY_SHORT",
                 line="05", q=q05)
        h, w, _ = run([k, c], "Hiromasa Koyama", WT, WS, WE)
        assert w == "ok"


class TestPartition:
    def test_the_two_lanes_are_disjoint(self):
        """B9i/B9j: yes/no refuses here as not_named; named refuses
        in bridge_explain as not_yes_no. No outcome reaches both."""
        _, w, _ = run([K, C], "Yes", WT, WS, WE)
        assert w == "not_named"
        assert pm.bridge_explain([K, C], [K, C], "Hiromasa Koyama",
                                 WT, WS, WE)[1] == "not_yes_no"


class TestPhaseZeroBoundary:
    def test_resolve_never_touches_the_named_lane(self):
        assert "named_ml" not in inspect.getsource(pm.resolve)

    def test_match_side_and_row_builder_are_untouched(self):
        """The stamp fix is FORBIDDEN until match_side's containment
        tiers are hardened — the tournament executed a wrong-person
        fill through exactly that path."""
        assert "named_ml" not in inspect.getsource(pm.match_side)
        assert "named_ml" not in inspect.getsource(pm._market_rows)

    def test_the_probe_is_wired(self):
        src = inspect.getsource(pm.resolve_explain)
        assert "named_ml_bridge_explain(" in src
        assert '"sub_gate"' in src
        assert '"attested_family"' in src

    def test_code_building_is_order_sensitive(self):
        """The D2-S4 determinism pin: a frozenset input would make
        this a hash-seed lottery."""
        assert pm._name_code_builds("hirkoy",
                                    ["hiromasa", "koyama"])
        assert not pm._name_code_builds("hirkoy",
                                        ["koyama", "hiromasa"])
        assert pm._name_code_builds("lucacas",
                                    ["luca", "castelnuovo"])
        assert pm._name_code_builds("maiito", ["mai", "ito"])

    def test_named_months_is_a_copy_not_an_alias(self):
        assert pm._NAMED_MONTHS == pm._BRIDGE_MONTHS
        assert pm._NAMED_MONTHS is not pm._BRIDGE_MONTHS


class TestImplementationFleetKills:
    """The implementation fleet's 3 executed kills (the tournament
    attacked the spec; this fleet attacked the CODE), each pinned."""

    def test_setkic_suffix_laundering_refuses(self):
        """NS-K1: 'set' is a 3-char DP prefix of 'Setkic', so the
        builds test authenticated a set-winner marker as his pick —
        a first-set pick mapped onto the moneyline. The suffix must
        now BE one of his slug's own codes."""
        qs = ("Who will win in the upcoming tennis event Aldin "
              "Setkic vs Dustin Brown scheduled for August 27, 2026 "
              "at 1:30 AM UTC?")
        s1 = vrow("aldin setkic", "ORDER_INTENT_BUY_LONG",
                  ident="aec-itfme-aldset-dusbro-2026-08-27", q=qs,
                  ev_title="Aldin Setkic vs. Dustin Brown")
        s2 = vrow("dustin brown", "ORDER_INTENT_BUY_SHORT",
                  ident="aec-itfme-aldset-dusbro-2026-08-27", q=qs,
                  ev_title="Aldin Setkic vs. Dustin Brown")
        h, w, _ = run([s1, s2], "Aldin Setkic", "Setkic vs Brown",
                      "itf-setkic-brown-2026-08-27-set",
                      "Aldin Setkic vs Dustin Brown")
        assert h is None and w == "slug_suffix_not_code"
        # (A code-valued suffix would pass this gate; upstream
        # market_type_of types long suffixes differently, so the
        # suffixless recovery pin in TestAttestedRecovery is the
        # positive control.)

    def test_compound_name_order_is_identity(self):
        """NS-K2: set equality admitted 'Jose Maria Perez' for a
        'Maria Jose Perez' pick — distinct compound-name people.
        Sequence or full reversal only; the reversal keeps the
        surname-first feed variance recovering."""
        qp = ("Who will win in the upcoming tennis event Jose Maria "
              "Perez vs Ana Diaz scheduled for August 27, 2026 at "
              "1:30 AM UTC?")
        p1 = vrow("jose maria perez", "ORDER_INTENT_BUY_LONG",
                  ident="aec-itfme-josper-anadia-2026-08-27", q=qp,
                  ev_title="Jose Maria Perez vs. Ana Diaz")
        p2 = vrow("ana diaz", "ORDER_INTENT_BUY_SHORT",
                  ident="aec-itfme-josper-anadia-2026-08-27", q=qp,
                  ev_title="Jose Maria Perez vs. Ana Diaz")
        h, w, _ = run([p1, p2], "Maria Jose Perez",
                      "Maria Jose Perez vs Ana Diaz",
                      "itf-josper-anadia-2026-08-27",
                      "Maria Jose Perez vs Ana Diaz")
        assert h is None
        assert pm._name_seq_eq(["koyama", "hiromasa"],
                               ["hiromasa", "koyama"])
        assert not pm._name_seq_eq(["maria", "jose", "perez"],
                                   ["jose", "maria", "perez"])

    def test_leading_marker_blocks_via_containment(self):
        """PC-K1: '2nd Meeting: Who will win...' escaped the
        startswith blocking scan — the venue wording ANYWHERE in a
        question blocks now, both pick polarities."""
        q2 = ("2nd Meeting: Who will win in the upcoming tennis "
              "event Hiromasa Koyama vs Luca Castelnuovo scheduled "
              "for August 27, 2026 at 4:30 AM UTC?")
        m1 = vrow("hiromasa koyama", "ORDER_INTENT_BUY_LONG",
                  ident="aec-itfme-hirkoy-lucacas-2026-08-27-2",
                  q=q2, ev_slug="itfme-hirkoy-lucacas-2026-08-27")
        for pick in ("Hiromasa Koyama", "Luca Castelnuovo"):
            h, w, _ = run([K, C, m1], pick, WT, WS, WE,
                          all_rows=[K, C, m1])
            assert h is None and w == "event_scan_ambiguous", pick

    def test_folds_away_is_exactly_mn_and_cf(self):
        """Fleet finding: whole-category M*/C* exemptions let
        private-use and control characters erase silently."""
        assert not pm._folds_away("Fenerbahçe")
        assert pm._folds_away("x  y")

    def test_tb_and_vs_are_bad_name_tokens(self):
        for t in ("tb", "vs", "sf", "qf"):
            assert t in pm._NAMED_BAD_TOKENS, t


class TestTwoColonTitles:
    """Census 2026-08-27 daytime: 289 of 309 named refusals were the
    REAL ITF title shape — 'ITF MEN - SINGLES: {tournament}, {surface}:
    A vs B' — which the overnight-sized single-colon gate refused
    wholesale. The last-colon partition recovers it; the derivative
    quarantine must hold across every prefix segment."""

    ATTESTED = ("ITF MEN - SINGLES: M15 Cap d'Agde (France), clay: "
                "Hiromasa Koyama vs Luca Castelnuovo")

    def test_attested_two_colon_title_recovers(self):
        h, w, _ = run([K, C], "Hiromasa Koyama", self.ATTESTED, WS, WE)
        assert w == "ok" and h is K
        h, w, _ = run([K, C], "Luca Castelnuovo", self.ATTESTED, WS, WE)
        assert w == "ok" and h is C

    def test_doubles_prefix_refuses_wherever_it_sits(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "ITF MEN - DOUBLES: M15 Cap d'Agde (France), "
                      "clay: Koyama vs Castelnuovo", WS, WE)
        assert h is None and w == "title_prefix_derivative"

    def test_derivative_marker_in_middle_segment_refuses(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "First Set Winner: M15 Cap d'Agde (France), "
                      "clay: Koyama vs Castelnuovo", WS, WE)
        assert h is None and w == "title_prefix_derivative"

    def test_colon_inside_matchup_still_refuses(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "ITF MEN - SINGLES: M15 Cap d'Agde: Koyama vs "
                      "Castelnuovo: 6:4", WS, WE)
        assert h is None, "a trailing score fragment is not a matchup"
