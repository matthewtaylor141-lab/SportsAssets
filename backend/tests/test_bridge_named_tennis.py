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

    def test_tournament_prefixed_title_refuses_unattested_shape(self):
        """CONSCIOUS RE-PIN (round 7). This shape was INVENTED in the
        round-1 tests, never census-attested — and the flattened
        attestation it survived under is exactly what let 'ITF Most
        Double Faults' smuggle through. Under the per-segment grammar
        an unattested tournament segment refuses; if the census ever
        attests this shape, the grammar extends by observation."""
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "US Open, Qualification ITF: Koyama vs "
                      "Castelnuovo", WS, WE)
        assert h is None and w == "title_prefix_unattested"

    def test_unlined_rows_also_recover(self):
        """B1d: the WTA-style already-unlined shape."""
        h, w, _ = run([dict(K, line=""), dict(C, line="")],
                      "Hiromasa Koyama", WT, WS, WE)
        assert w == "ok"

    def test_the_clock_poisoned_pair_now_resolves_directly(self):
        """The grounding's A1 anchor, evolved (census 2026-08-29):
        match_side used to refuse this attested pair at _lined_ok on
        the phantom clock line — the lane's original reason for
        existing. _clock_artifact now clears a line that is provably
        the question's clock (sole parsed line, verbatim minutes), so
        the exact-name branch resolves the pair DIRECTLY; the
        uniqueness guard still refuses when a prop sibling shares the
        name, and the bridge remains the recovery for every shape the
        matcher still refuses. The clock stamp itself is untouched —
        the bridge's quarantine authenticates it."""
        hit = pm.match_side([K, C], "Hiromasa Koyama", WT,
                            "itf-koyama-castelnuovo-2026-08-27")
        assert hit is K, "the phantom clock no longer refuses"
        assert pm._lines_of(Q) == {"30"}, "the stamp survives"


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
        """itfwo entered by observation: mapper-evidence run 10
        (2026-08-27) recorded itf->itfwo verbatim 112 times."""
        assert pm._NAMED_TOUR_OF == {"itf": "itf", "itfme": "itf",
                                     "itfwo": "itf"}

    def test_itfwo_family_recovers_with_womens_banner(self):
        """The 112-count census class: whale lg 'itf', venue itfwo,
        ITF WOMEN two-colon banner, W-tier, poisoned clock line —
        recovers bijectively for both polarities."""
        qw = ("Who will win in the upcoming tennis event Maria "
              "Kononova vs Elena Pridankina scheduled for August 27, "
              "2026 at 9:30 AM UTC?")
        w1 = vrow("maria kononova", "ORDER_INTENT_BUY_LONG",
                  ident="aec-itfwo-markon-elepri-2026-08-27", q=qw,
                  ev_title="Maria Kononova vs. Elena Pridankina")
        w2 = vrow("elena pridankina", "ORDER_INTENT_BUY_SHORT",
                  ident="aec-itfwo-markon-elepri-2026-08-27", q=qw,
                  ev_title="Maria Kononova vs. Elena Pridankina")
        h, w, _ = run([w1, w2], "Maria Kononova",
                      "ITF WOMEN - SINGLES: W15 Monastir (Tunisia), "
                      "hard: Maria Kononova vs Elena Pridankina",
                      "itf-kononova-pridankina-2026-08-27",
                      "Maria Kononova vs Elena Pridankina")
        assert w == "ok" and h is w1
        assert h["intent"] == "ORDER_INTENT_BUY_LONG"
        h, w, _ = run([w1, w2], "Elena Pridankina",
                      "ITF WOMEN - SINGLES: W15 Monastir (Tunisia), "
                      "hard: Maria Kononova vs Elena Pridankina",
                      "itf-kononova-pridankina-2026-08-27",
                      "Maria Kononova vs Elena Pridankina")
        assert w == "ok" and h is w2
        assert h["intent"] == "ORDER_INTENT_BUY_SHORT"


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
        # slug's codes before any building is consulted. Since
        # 2026-09-03 (to-a-tee Phase 2) a segment token types prop in
        # market_type_of, so the bridge's TYPE gate refuses a '-set'
        # slug before the suffix gate is ever reached; the refusal is
        # the same, the reason moved one gate earlier.
        h, w, _ = run([K, C], "Hiromasa Koyama", WT,
                      "itf-koyama-castelnuovo-2026-08-27-set", WE)
        assert h is None and w == "wrong_type"

    def test_a_non_segment_alpha_suffix_still_dies_at_the_suffix_gate(self):
        """The suffix gate stays covered on its own: an alpha suffix
        that is neither a segment word nor one of his slug's codes
        types moneyline upstream and refuses HERE, slug_suffix_not_code
        — the Named 1.1 law is unchanged by the segment typing."""
        h, w, _ = run([K, C], "Hiromasa Koyama", WT,
                      "itf-koyama-castelnuovo-2026-08-27-xyz", WE)
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


class TestPhaseOneWiring:
    """Phase 1 (2026-08-30): resolve() consults the bridge — DARK by
    default behind PREMAP_NAMED_LANE, only after match_side returns
    None, with the pre-filter pool as rows_all, and a bridge hit still
    passes the same no-intent refusal. This class deliberately
    replaces the Phase-0 pin `test_resolve_never_touches_the_named
    _lane` — the flip is the feature, made on the mapper-fail
    diagnosis's adversarially verified lane, not by accident."""

    class _P:
        def __init__(self, rows):
            self.rows = rows

        async def fetch(self, *_a):
            return self.rows

    def _resolve(self, rows, outcome="Hiromasa Koyama"):
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                pm.resolve(self._P(rows), WT, WE, outcome, WS))
        finally:
            loop.close()

    def test_dark_by_default(self, monkeypatch):
        monkeypatch.delenv("PREMAP_NAMED_LANE", raising=False)
        monkeypatch.setattr(pm, "match_side", lambda *a, **k: None)
        called = []
        monkeypatch.setattr(pm, "named_ml_bridge_explain",
                            lambda *a, **k: called.append(1))
        assert self._resolve([K, C]) is None
        assert not called, "the lane must stay dark without the switch"

    def test_switch_on_recovers_the_attested_pair(self, monkeypatch):
        # the REAL bridge runs; only match_side is forced to miss so
        # the wiring (not matcher policy) is what this test pins
        monkeypatch.setenv("PREMAP_NAMED_LANE", "on")
        monkeypatch.setattr(pm, "match_side", lambda *a, **k: None)
        r = self._resolve([K, C])
        assert r is not None
        assert r["matched_by"] == "premap_named"
        assert r["market_slug"] == K["identifier"]
        assert r["intent"] == "ORDER_INTENT_BUY_LONG"

    def test_match_side_hit_never_consults_the_bridge(self, monkeypatch):
        monkeypatch.setenv("PREMAP_NAMED_LANE", "on")

        def _boom(*_a, **_k):
            raise AssertionError("bridge consulted despite a hit")

        monkeypatch.setattr(pm, "named_ml_bridge_explain", _boom)
        monkeypatch.setattr(pm, "match_side", lambda *a, **k: K)
        r = self._resolve([K, C])
        assert r is not None and r["matched_by"] == "premap"

    def test_rows_all_is_the_prefilter_pool(self, monkeypatch):
        monkeypatch.setenv("PREMAP_NAMED_LANE", "on")
        monkeypatch.setattr(pm, "match_side", lambda *a, **k: None)
        stray = vrow("over", "ORDER_INTENT_BUY_LONG",
                     ident="tsc-itfme-hirkoy-lucacas-2026-08-27-2pt5")
        seen = {}

        def _spy(rows_kept, rows_all, *a, **k):
            seen["kept"] = list(rows_kept)
            seen["all"] = list(rows_all)
            return None, "spied"

        monkeypatch.setattr(pm, "named_ml_bridge_explain", _spy)
        assert self._resolve([K, C, stray]) is None
        assert stray in seen["all"], \
            "pool-wide blockers must see what the prefix filter drops"
        assert stray not in seen["kept"]

    def test_bridge_hit_without_intent_still_refuses(self, monkeypatch):
        monkeypatch.setenv("PREMAP_NAMED_LANE", "on")
        monkeypatch.setattr(pm, "match_side", lambda *a, **k: None)
        bare = vrow("hiromasa koyama", None)
        monkeypatch.setattr(pm, "named_ml_bridge_explain",
                            lambda *a, **k: (bare, "ok"))
        assert self._resolve([K, C]) is None

    def test_bridge_exception_falls_through_closed(self, monkeypatch):
        monkeypatch.setenv("PREMAP_NAMED_LANE", "on")
        monkeypatch.setattr(pm, "match_side", lambda *a, **k: None)

        def _boom(*_a, **_k):
            raise RuntimeError("bridge broke")

        monkeypatch.setattr(pm, "named_ml_bridge_explain", _boom)
        assert self._resolve([K, C]) is None


class TestPhaseZeroBoundary:
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
        # Since 2026-09-03 (to-a-tee Phase 2) 'set' is a segment token
        # and market_type_of types the slug prop, so the bridge's type
        # gate refuses before the suffix gate; the laundering is dead
        # one gate earlier and the suffix gate's own law is pinned by
        # test_a_non_segment_alpha_suffix_still_dies_at_the_suffix_gate.
        assert h is None and w == "wrong_type"
        # and the laundering shape itself — a name-prefix collision
        # ('setk' is a DP prefix of 'Setkic') on a non-segment alpha
        # suffix — still dies at the suffix gate
        h, w, _ = run([s1, s2], "Aldin Setkic", "Setkic vs Brown",
                      "itf-setkic-brown-2026-08-27-setk",
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


class TestRoundFourKills:
    """Round-4 fleet: three wrong-market admissions the 1.2 widening
    opened, each now a refusal wherever the marker or signal sits."""

    def test_tie_break_two_word_spelling_refuses(self):
        for title in ("Match Tie-Break: M15 Cap d'Agde, clay: "
                      "Koyama vs Castelnuovo",
                      "Tie Break Winner: Koyama vs Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert h is None, title
        # and split across the MATCHUP half too
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "ITF MEN - SINGLES: M15: Koyama Tie Break vs "
                      "Castelnuovo", WS, WE)
        assert h is None

    def test_danger_vocabulary_covers_the_prefix(self):
        for title in ("2nd Meeting: Koyama vs Castelnuovo",
                      "Most Aces: Koyama vs Castelnuovo",
                      "Retirement Market: Koyama vs Castelnuovo",
                      "Winner Match 2: Koyama vs Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert h is None, title

    def test_line_and_sign_in_dropped_prefix_refuse(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "Handicap -3.5: Koyama vs Castelnuovo", WS, WE)
        assert h is None and w in ("his_signal_lined",
                                   "his_signal_signed",
                                   "title_prefix_derivative")
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "Total 22.5: Koyama vs Castelnuovo", WS, WE)
        assert h is None

    def test_attested_census_title_still_recovers(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "ITF MEN - SINGLES: M15 Cap d'Agde (France), "
                      "clay: Hiromasa Koyama vs Luca Castelnuovo",
                      WS, WE)
        assert w == "ok" and h is K, \
            "the 289-row census class must survive every new refusal"


class TestRoundFiveKills:
    """Round-5 fleet: marker spellings the round-4 vocabulary missed."""

    def test_ace_markers_refuse_in_halves_and_prefix(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "Koyama vs Castelnuovo Most Aces", WS, WE)
        assert h is None
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "Ace Count: Koyama vs Castelnuovo", WS, WE)
        assert h is None

    def test_split_and_dotted_bad_markers_refuse(self):
        for title in ("Walk Over: Koyama vs Castelnuovo",
                      "With Drawal: Koyama vs Castelnuovo",
                      "T.B.: Koyama vs Castelnuovo",
                      "W.O.: Koyama vs Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert h is None, title

    def test_whole_number_lined_markets_refuse(self):
        for title in ("Total 22: Koyama vs Castelnuovo",
                      "Games Handicap: Koyama vs Castelnuovo",
                      "Over Under: Koyama vs Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert h is None, title

    def test_attested_class_survives_round_five(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "ITF MEN - SINGLES: M15 Cap d'Agde (France), "
                      "clay: Hiromasa Koyama vs Luca Castelnuovo",
                      WS, WE)
        assert w == "ok" and h is K


class TestRoundSixKills:
    """Round-6 fleet: the vocabulary was an enumeration, not a closure.
    The prefix gate is now POSITIVE — it must attest a tour — so every
    prop-market noun phrase, present and future, refuses structurally."""

    def test_prop_market_prefixes_refuse_without_enumeration(self):
        for title in ("Most Double Faults: Koyama vs Castelnuovo",
                      "Most Points Won: Koyama vs Castelnuovo",
                      "Fastest Serve: Koyama vs Castelnuovo",
                      "Number of Tiebreaks: Koyama vs Castelnuovo",
                      "Most Break Points Won: Koyama vs Castelnuovo",
                      "Presented by Anyone: Koyama vs Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert h is None, title
            assert w in ("title_prefix_unattested",
                         "title_prefix_derivative"), (title, w)

    def test_attested_prefixes_still_recover(self):
        for title in ("ITF MEN - SINGLES: M15 Cap d'Agde (France), "
                      "clay: Hiromasa Koyama vs Luca Castelnuovo",
                      "ITF MEN - SINGLES: Koyama vs Castelnuovo",
                      "M15 Antalya, hard: Koyama vs Castelnuovo",
                      "Hiromasa Koyama vs Luca Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert w == "ok" and h is K, (title, w)


class TestRoundSevenKills:
    """Round-7 fleet: the attestation must be per-segment and the
    closure must not be keyed on colon presence."""

    def test_tour_marker_cannot_launder_prop_segments(self):
        for title in ("ITF Most Double Faults: Koyama vs Castelnuovo",
                      "ITF MEN - SINGLES: M15 Cap d'Agde (France), "
                      "clay: Most Double Faults: Koyama vs Castelnuovo",
                      "ITF Fastest Serve: Koyama vs Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert h is None, title
            assert w in ("title_prefix_unattested",
                         "title_prefix_derivative"), (title, w)

    def test_colonless_prop_titles_refuse_at_the_event_witness(self):
        for title in ("Most Double Faults Koyama vs Castelnuovo",
                      "Fastest Serve Koyama vs Castelnuovo",
                      "Most Double Faults - Koyama vs Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert h is None, title

    def test_reversed_half_order_still_recovers(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "Koyama Hiromasa vs Castelnuovo Luca", WS, WE)
        assert w == "ok" and h is K, \
            "the subsequence gate honours full reversal like _name_seq_eq"


class TestRoundEightKills:
    """Round-8 fleet: a bare tier code laundered prop segments."""

    def test_tier_code_cannot_launder_prop_segments(self):
        for title in ("M15 Most Double Faults: Koyama vs Castelnuovo",
                      "M15 Fastest Serve: Koyama vs Castelnuovo",
                      "W35 Most Points Won: Koyama vs Castelnuovo",
                      "M15: Koyama vs Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert h is None, title

    def test_surface_terminated_tier_segments_recover(self):
        for title in ("ITF MEN - SINGLES: M15 Cap d'Agde (France), "
                      "clay: Hiromasa Koyama vs Luca Castelnuovo",
                      "M15 Antalya, hard: Koyama vs Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert w == "ok" and h is K, (title, w)


class TestRoundNineForwardFix:
    """Round-9 fleet flagged it before the season could: Flashscore
    writes indoor events 'hard (indoor)', which the surface-terminal
    grammar would refuse from ~October. Accepted only surface-adjacent."""

    def test_indoor_modifier_recovers_surface_adjacent(self):
        for title in ("ITF WOMEN - SINGLES: W35 Helsinki (Finland), "
                      "hard (indoor): Hiromasa Koyama vs Luca "
                      "Castelnuovo",
                      "M15 Antalya, clay (outdoor): Koyama vs "
                      "Castelnuovo"):
            h, w, _ = run([K, C], "Hiromasa Koyama", title, WS, WE)
            assert w == "ok" and h is K, (title, w)

    def test_bare_indoor_does_not_attest(self):
        h, w, _ = run([K, C], "Hiromasa Koyama",
                      "M15 Most Double Faults, indoor: Koyama vs "
                      "Castelnuovo", WS, WE)
        assert h is None, "indoor without an adjacent surface is nothing"


class TestEventTitleBanner:
    """Census run 9 (post title-fix): the funnel moved one gate down —
    his_event_side_bad 247 + unsplittable 55, because the EVENT title
    wears the same Flashscore banner. Same strip, same gates."""

    BWE = ("ITF MEN - SINGLES: M15 Cap d'Agde (France), clay: "
           "Hiromasa Koyama vs Luca Castelnuovo")

    def test_bannered_event_title_recovers(self):
        h, w, _ = run([K, C], "Hiromasa Koyama", WT, WS, self.BWE)
        assert w == "ok" and h is K
        h, w, _ = run([K, C], "Luca Castelnuovo",
                      "ITF MEN - SINGLES: M15 Antalya, hard: Koyama vs "
                      "Castelnuovo", WS, self.BWE)
        assert w == "ok" and h is C

    def test_event_banner_gates_hold(self):
        h, w, _ = run([K, C], "Hiromasa Koyama", WT, WS,
                      "ITF MEN - DOUBLES: M15 X, clay: Koyama vs "
                      "Castelnuovo")
        assert h is None and w == "event_prefix_derivative"
        h, w, _ = run([K, C], "Hiromasa Koyama", WT, WS,
                      "Most Double Faults: Hiromasa Koyama vs Luca "
                      "Castelnuovo")
        assert h is None and w == "event_prefix_unattested"

    def test_plain_event_title_unchanged(self):
        h, w, _ = run([K, C], "Hiromasa Koyama", WT, WS, WE)
        assert w == "ok" and h is K
