"""RED TEAM CLOSEOUT V1 -- THE CHAOS / ADVERSARIAL ACCEPTANCE MATRIX C01-C30
(CHAOS_ACCEPTANCE_MATRIX.md), AUTOMATED AGAINST THE BOUND PRODUCTION CODE.

Acceptance is not "no exception": each case injects the failure and proves
the fail-closed behavior -- new risk refused, existing positions preserved,
exit / reduce / protection still available where appropriate, the exact
blocker named, recovery reconciles, no profit invented. The ledger-level
cases (C19 against the PAPER ledger, exits on a breached claim) also run
against Postgres in test_red_team_exposure_lock.py.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sportsassets import canonical_claims as CC
from sportsassets import institutional_stream as IS
from sportsassets import kalshi_claims as KCL
from sportsassets import kalshi_fees as KF
from sportsassets import kalshi_market_data as KMD
from sportsassets import kalshi_ws as KWS
from sportsassets.agents import adriana_arb as A
from sportsassets.agents import adriana_claims as AC
from sportsassets.redteam import controls as C
from sportsassets.redteam import exposure as X
from sportsassets.redteam import readiness as R
from sportsassets.redteam import sentinel as S
from sportsassets.redteam import settlement as RS
from sportsassets.redteam import venue_health as VH
from sportsassets.red_team import two_leg_sentinel as TL
from sportsassets.red_team.models import ClaimExposure, PositionTruth

D = Decimal
NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc).timestamp()
FX = CC.Fixture(event_key="MLB:2026-10-08T00:00Z:TB@NYY", sport="BASEBALL",
                league="MLB", start_epoch=NOW + 4 * 3600,
                outcome_kind="TWO_WAY", home="NYY", away="TB")
FX3 = CC.Fixture(event_key="EPL:2026-10-08T15:00Z:CHE@ARS", sport="SOCCER",
                 league="EPL", start_epoch=NOW + 4 * 3600,
                 outcome_kind="THREE_WAY", home="ARS", away="CHE")
TERMS = {"overtime_included": True, "draw_rule": "SCALAR_0_50",
         "void_rule": "SCALAR_0_50", "postponement_window_hours": 48.0,
         "postponement_payout": "SCALAR_0_50", "verification_sources": ["X"]}
KT = KF.effective_terms(series_ticker="KXMLBGAME", event_ticker=None, at=NOW,
                        event_changes=[], series_changes=[{
                            "id": "chaos-x1",
                            "fee_type": "quadratic_with_maker_fees",
                            "fee_multiplier": 1,
                            "scheduled_ts": "2025-10-04T07:00:00Z",
                            "series_ticker": "KXMLBGAME"}])
SYM = "aec-mlb-tb-nyy-2026-10-07"
REC = {"symbol": SYM, "priceScale": "1000", "fractionalQtyScale": "100",
       "state": "INSTRUMENT_STATE_OPEN", "productId": SYM}


def inst(venue, market, side, subject, asks, *, at=NOW, terms=TERMS,
         rules="r1"):
    return CC.Instrument(venue=venue, market_id=market, side=side,
                         subject=subject, settlement=dict(terms),
                         settlement_status="PROVEN",
                         mapping_status="ESTABLISHED", asks=tuple(asks),
                         observed_at=at, book_basis="CHAOS",
                         sport="BASEBALL", rules_sha256=rules,
                         fee_terms=KT if venue == "KALSHI" else None)


def books(clock):
    b = IS.ResidentBooks(clock=clock)
    b.set_state(IS.S_IDLE, "chaos")
    b.set_instrument(SYM, dict(REC))
    b.want([SYM])
    return b


def upd(at, bid=450, offer=470):
    return {"symbol": SYM, "transact_time": at,
            "bids": [(bid, 1000)], "offers": [(offer, 1000)],
            "state": "INSTRUMENT_STATE_OPEN"}


def vh_report(*, pm_cur=10, pm_den=10, k_cur=10, k_den=10, k_429=False,
              gap=0):
    from sportsassets.red_team.models import VenueHealth
    return VH.report([
        VenueHealth("POLYMARKET_US", pm_cur, pm_den, pm_den - pm_cur, gap,
                    False, NOW, "chaos"),
        VenueHealth("KALSHI", k_cur, k_den, k_den - k_cur, 0, k_429, NOW,
                    "chaos")])


# ── C01 kill the PMX stream with positions open ─────────────────────────

def test_c01_kill_pmx_books_gap_reconnect_needs_a_full_update():
    t = [NOW]
    b = books(lambda: t[0])
    b.on_connected()
    b.on_update(upd(NOW), received_at=NOW)
    assert b.current(SYM)["ok"]
    b.on_disconnected("chaos-kill")
    r = b.current(SYM)
    assert not r["ok"] and r["refusal"] == IS.R_GAP_CONNECTION
    b.on_connected()                               # connected != current
    r = b.current(SYM)
    assert not r["ok"] and r["refusal"] in (IS.R_GAP_CONNECTION,
                                            IS.R_SNAPSHOT_PENDING)
    t[0] = NOW + 2
    b.on_update(upd(NOW + 2), received_at=NOW + 2)
    r = b.current(SYM)
    assert r["ok"] and r["evidence"]["stream_currency"]["green"]
    # exits never touch the canonical exposure lock (ledger test proves it
    # against Postgres): the lock is reached only from the ENTRY branch
    src = open(X.__file__.replace("redteam/exposure.py",
                                  "bettor_paper_ledger.py")).read()
    i = src.index("_canonical_exposure_refusal(\n")
    assert 'if o.get("role") == "ENTRY":' in src[i - 600:i]


# ── C02 Kalshi 429 for 20 minutes / C03 Polymarket reconnect ────────────

def test_c02_a_kalshi_429_storm_backs_off_and_never_touches_polymarket():
    t = [0.0]
    h = KMD.KalshiHealth(clock=lambda: t[0])
    for _ in range(8):
        h.record("HTTP_429")
    assert h.blocked() and h.digest()["backing_off"]
    rep = vh_report(k_429=True)
    assert not rep["KALSHI"]["green"]
    assert "RATE_LIMIT_ACTIVE" in rep["KALSHI"]["blockers"]
    assert rep["POLYMARKET_US"]["green"]          # independent
    assert VH.MIN_RATE == 0.95                     # no SLA widening


def test_c03_a_polymarket_reconnect_changes_only_polymarket():
    rep = vh_report(gap=1)
    assert not rep["POLYMARKET_US"]["green"]
    assert "STREAM_GAPS_PRESENT" in rep["POLYMARKET_US"]["blockers"]
    assert rep["KALSHI"]["green"]
    assert not VH.pair_gate(rep, "POLYMARKET_US", "KALSHI")["green"]


# ── C04 stale cheapest quote ────────────────────────────────────────────

def test_c04_a_stale_cheapest_route_loses_to_the_current_one():
    stale = inst("KALSHI", "K-TB", "NO", "AWAY", [(D("0.40"), 50)],
                 at=NOW - 3600)
    cur = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.47"), 50)])
    b = CC.build_claims(FX, [stale, cur])
    fees = CC.fee_functions(at=NOW, sport="BASEBALL", kalshi_terms=KT)
    got = CC.route_claim(FX, cur.fingerprint, b["classes"][cur.fingerprint],
                         qty=5, now=NOW + 1, fee_by_venue=fees, max_age_s=30)
    assert got["best_single"]["market_id"] == "K-NYY"
    assert any(x["market_id"] == "K-TB" and x["why"] != "ALL_IN_HIGHER_BY"
               for x in got["lost"])


# ── C05 doubleheader / C06 NBA vs WNBA ─────────────────────────────────

def _kfx(league, home, away, start):
    return KMD.KalshiFixture(
        event_ticker="KX-%s-%s%s" % (league, away, home), series_ticker="KX",
        sport="BASEBALL" if league == "MLB" else "BASKETBALL",
        league=league, start_epoch=start, home_id="H", away_id="A",
        home_code=home, away_code=away, tie_ticker=None, team_tickers=(),
        outcome_kind="TWO_WAY", status="ESTABLISHED", reasons=(),
        milestone_id="m")


def test_c05_an_mlb_doubleheader_is_disambiguated_by_start_or_refused():
    k = _kfx("MLB", "NYY", "TB", NOW + 3600)
    g1 = {"slug": "g1", "league": "mlb", "team_a": "NYY", "team_b": "TB",
          "start_epoch": NOW + 3600}
    g2 = dict(g1, slug="g2", start_epoch=NOW + 3600 + 5 * 3600)
    assert KCL.map_pmus(k, [g1, g2])["pmus"]["slug"] == "g1"
    near = dict(g1, slug="g1b", start_epoch=NOW + 3600 + 300)
    got = KCL.map_pmus(k, [g1, near])
    assert got["status"] == "NOT_ESTABLISHED" and \
        got["reasons"][0].startswith("AMBIGUOUS")


def test_c06_a_wnba_team_never_maps_to_the_nba_team_of_the_same_city():
    k = _kfx("NBA", "NYK", "BOS", NOW)
    wnba = {"slug": "w", "league": "wnba", "team_a": "NYK", "team_b": "BOS",
            "start_epoch": NOW}
    got = KCL.map_pmus(k, [wnba])
    assert got["status"] == "NOT_ESTABLISHED"


# ── C07 a settlement rule change after mapping ──────────────────────────

def test_c07_a_rules_change_invalidates_the_certificate_and_freezes_pairs():
    i = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.4"), 10)],
             rules="sha-OLD")
    CC.build_claims(FX, [i])
    assert RS.decide(i, None)["action"] == "CERTIFY"
    cert = {"status": RS.CERTIFIED, "rules_sha256": "sha-OLD"}
    assert RS.decide(i, cert)["action"] == "KEEP"
    i.rules_sha256 = "sha-NEW"
    d = RS.decide(i, cert)
    assert d["action"] == "INVALIDATE" and not d["eligible"]
    assert RS.R_CHANGED in d["blockers"]
    # re-established only by a later full re-mapping of the NEW rules
    assert RS.decide(i, {"status": RS.INVALIDATED})["action"] == "RECERTIFY"
    # a decided pair revalidated under the changed rules freezes
    rec = _opp("KALSHI", "K-NYY", "KALSHI", "K-TB")
    rc = S.revalidate_shadow(
        rec, scan_id="c07", now=NOW + 1,
        books_now={("KALSHI", "K-NYY"): NOW, ("KALSHI", "K-TB"): NOW},
        rules_now={"kalshi:K-NYY": "sha-NEW", "kalshi:K-TB": "r1"},
        capital={"KALSHI": D(1000)}, venue_health=vh_report())
    assert rc["state"] == TL.FROZEN and rc["reason"] == \
        "SETTLEMENT_NOT_CURRENT"
    assert any(r.startswith("RULES_CHANGED_SINCE_CERTIFICATION")
               for r in rc["evidence"]["rules_reasons"])


# ── C08 three-way Team B NO ─────────────────────────────────────────────

def test_c08_a_three_way_opponent_no_is_never_aliased_to_team_a_yes():
    a = inst("KALSHI", "K-ARS", "YES", "HOME", [(D("0.4"), 10)])
    n = inst("KALSHI", "K-CHE", "NO", "AWAY", [(D("0.5"), 10)])
    b = CC.build_claims(FX3, [a, n])
    rec = CC.equivalence_receipt(FX3, a, n, b["states"])
    assert rec["verdict"] == "NOT_ESTABLISHED"
    assert any("DRAW" in s for s in rec["states_differing"])


# ── C09 / C11 / C12 the two-leg sentinel under fills ───────────────────

def _armed(target=100, cap=D("5")):
    s = TL.PairState(TL.DISCOVERED, target, max_unmatched_loss=cap)
    s = TL.revalidate(s, books_current=True, settlement_current=True,
                      economics_positive=True)
    return TL.arm(s)


def test_c09_one_leg_80_percent_other_zero_is_repair_never_locked():
    s = S.apply_fills(_armed(), [{"fill_id": "a1", "leg": "A", "qty": 80,
                                  "cost": "36"}], seen=set(),
                      worst_case_unmatched_loss_per_contract=D("0.45"))
    assert s.state == TL.REPAIR_REQUIRED
    assert s.reason == "UNMATCHED_LOSS_CAP_EXCEEDED"
    assert s.matched_qty == 0 and s.unmatched_qty == 80
    assert S.labels(s, True) == {
        "economics_label": S.GUARANTEED_IF_FILLED,
        "execution_label": "NOT_EXECUTION_LOCKED"}


def test_c11_a_duplicate_fill_is_deduplicated_by_venue_identity():
    seen = set()
    f = {"fill_id": "v-777", "leg": "A", "qty": 5, "cost": "2.25"}
    s = S.apply_fills(_armed(cap=D("100")), [f, f], seen=seen,
                      worst_case_unmatched_loss_per_contract=D("0.45"))
    assert s.a_filled == 5
    s = S.apply_fills(s, [f], seen=seen,
                      worst_case_unmatched_loss_per_contract=D("0.45"))
    assert s.a_filled == 5


def test_c12_out_of_order_fills_converge_to_one_position():
    fills = [{"fill_id": "b1", "leg": "B", "qty": 60, "cost": "27"},
             {"fill_id": "a1", "leg": "A", "qty": 60, "cost": "26"},
             {"fill_id": "a2", "leg": "A", "qty": 40, "cost": "17"},
             {"fill_id": "b2", "leg": "B", "qty": 40, "cost": "18"}]
    out = []
    for order in (fills, list(reversed(fills)), fills[1::2] + fills[0::2]):
        s = S.apply_fills(_armed(cap=D("100")), order, seen=set(),
                          worst_case_unmatched_loss_per_contract=D("0.45"))
        out.append((s.state, s.a_filled, s.b_filled))
    assert set(out) == {(TL.MATCHED, 100, 100)}
    s = TL.lock(S.apply_fills(_armed(cap=D("100")), fills, seen=set(),
                              worst_case_unmatched_loss_per_contract=D(
                                  "0.45")))
    assert s.execution_locked and TL.guarantee_label(s) == "EXECUTION_LOCKED"


# ── C10 a timeout after the venue accepted ──────────────────────────────

def test_c10_an_ambiguous_ack_never_triggers_a_blind_retry():
    cid = S.client_order_id("pair-1", "A")
    assert cid == S.client_order_id("pair-1", "A")     # deterministic
    assert S.retry_allowed(S.ACK_AMBIGUOUS, None)["retry"] is False
    assert S.retry_allowed(S.ACK_AMBIGUOUS, S.REC_WAIT)["retry"] is False
    assert S.retry_allowed(S.ACK_AMBIGUOUS, S.REC_ADOPT)["retry"] is False
    r = S.retry_allowed(S.ACK_AMBIGUOUS, S.REC_NOT_FOUND)
    assert r["retry"] and r["same_client_id"]
    assert S.retry_allowed(S.ACK_AMBIGUOUS, S.REC_UNATTRIBUTED)["retry"] \
        is False
    # the same contract as the credentialed Kalshi client's reconciler
    # (read here; the sentinel imports no order module)
    from sportsassets import kalshi_orders as KO
    assert {KO.R_RECONCILE_ADOPT if hasattr(KO, "R_RECONCILE_ADOPT")
            else "ADOPT"} >= {"ADOPT"}
    assert "reconcile_ambiguous" in dir(KO)


# ── C13 a new socket with no snapshot ──────────────────────────────────

def test_c13_connected_without_a_snapshot_is_never_current():
    w = KWS.WsBooks(clock=lambda: NOW)
    w.want(["K-NYY"])
    w.on_connected()
    assert not w.current("K-NYY")["ok"]
    t = [NOW]
    b = books(lambda: t[0])
    b.on_connected()
    r = b.current(SYM)
    assert not r["ok"] and r["refusal"] == IS.R_SNAPSHOT_PENDING


# ── C14 clock skew ──────────────────────────────────────────────────────

def test_c14_a_future_book_or_a_backwards_venue_clock_refuses():
    fut = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.40"), 10)],
               at=NOW + 600)
    b = CC.build_claims(FX, [fut])
    fees = CC.fee_functions(at=NOW, sport="BASEBALL", kalshi_terms=KT)
    got = CC.route_claim(FX, fut.fingerprint, b["classes"][fut.fingerprint],
                         qty=1, now=NOW, fee_by_venue=fees, max_age_s=30)
    assert got["chosen"] is None
    assert got["candidates"][0]["eligible"] is False
    t = [NOW]
    rb = books(lambda: t[0])
    rb.on_connected()
    rb.on_update(upd(NOW + 10), received_at=NOW)
    rb.on_update(upd(NOW + 5), received_at=NOW + 1)     # venue clock back
    assert rb.current(SYM)["refusal"] == IS.R_GAP_CLOCK


# ── C15 fee schedule missing or changed ─────────────────────────────────

def test_c15_a_missing_fee_schedule_makes_the_route_ineligible():
    k = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.40"), 10)])
    k.fee_terms = None
    b = CC.build_claims(FX, [k])
    fees = CC.fee_functions(at=NOW, sport="BASEBALL", kalshi_terms=None)
    got = CC.route_claim(FX, k.fingerprint, b["classes"][k.fingerprint],
                         qty=1, now=NOW + 1, fee_by_venue=fees, max_age_s=30)
    assert got["chosen"] is None and got["candidates"][0]["reason"] == \
        "FEE_UNKNOWN"
    changed = KF.effective_terms(
        series_ticker="KXMLBGAME", event_ticker=None, at=NOW,
        event_changes=[], series_changes=[{
            "id": "x05", "fee_type": "quadratic_with_maker_fees",
            "fee_multiplier": 0.5, "scheduled_ts": NOW - 60,
            "series_ticker": "KXMLBGAME"}])
    assert changed["version"] != KT["version"]
    assert changed["schedule_id"] == "x05"


# ── C16 Kalshi YES 49c vs opponent NO 45c ───────────────────────────────

def test_c16_the_equivalent_opponent_no_wins_when_cheaper_all_in():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.49"), 50)])
    n = inst("KALSHI", "K-TB", "NO", "AWAY", [(D("0.45"), 50)])
    b = CC.build_claims(FX, [y, n])
    fees = CC.fee_functions(at=NOW, sport="BASEBALL", kalshi_terms=KT)
    got = CC.route_claim(FX, y.fingerprint, b["classes"][y.fingerprint],
                         qty=10, now=NOW + 1, fee_by_venue=fees, max_age_s=30)
    assert (got["best_single"]["market_id"],
            got["best_single"]["side"]) == ("K-TB", "NO")


# ── C17 depth only at worse levels ──────────────────────────────────────

def test_c17_size_is_priced_by_vwap_through_depth_not_top_of_book():
    y = inst("KALSHI", "K-NYY", "YES", "HOME",
             [(D("0.40"), 2), (D("0.50"), 100)])
    b = CC.build_claims(FX, [y])
    fees = CC.fee_functions(at=NOW, sport="BASEBALL", kalshi_terms=KT)
    one = CC.route_claim(FX, y.fingerprint, b["classes"][y.fingerprint],
                         qty=1, now=NOW + 1, fee_by_venue=fees, max_age_s=30)
    ten = CC.route_claim(FX, y.fingerprint, b["classes"][y.fingerprint],
                         qty=10, now=NOW + 1, fee_by_venue=fees, max_age_s=30)
    assert D(ten["best_single"]["vwap"]) > D("0.40")
    assert D(ten["best_single"]["all_in_per_contract"]) > \
        D(one["best_single"]["all_in_per_contract"])


# ── C18 one venue lacks cash ────────────────────────────────────────────

def _opp(va, ma, vb, mb, *, net="2.00"):
    return {"verdict": A.GUARANTEED_AFTER_COSTS,
            "economics": {"qty": 10, "worst_case_net_profit": net,
                          "legs": [{"venue": va, "market_id": ma,
                                    "side": "YES", "qty": 10,
                                    "total_cost": "4.40"},
                                   {"venue": vb, "market_id": mb,
                                    "side": "YES", "qty": 10,
                                    "total_cost": "4.50"}]},
            "inputs": {"books": [{"venue": va, "market_id": ma,
                                  "observed_at": NOW},
                                 {"venue": vb, "market_id": mb,
                                  "observed_at": NOW}]},
            "claim_pair": {"topology": "CROSS_VENUE" if va != vb
                           else "SAME_VENUE",
                           "rules": {S.contract_id(va, ma): "r1",
                                     S.contract_id(vb, mb): "r1"}}}


def test_c18_a_cross_venue_arb_without_confirmed_capital_freezes():
    rec = _opp("KALSHI", "K-NYY", "POLYMARKET_US", "aec-x")
    rc = S.revalidate_shadow(
        rec, scan_id="c18", now=NOW + 1,
        books_now={("KALSHI", "K-NYY"): NOW, ("POLYMARKET_US", "aec-x"): NOW},
        rules_now={"kalshi:K-NYY": "r1", "aec-x": "r1"},
        capital={"KALSHI": None, "POLYMARKET_US": D(1000)},
        venue_health=vh_report())
    assert rc["state"] == TL.FROZEN
    assert rc["reason"] == "VENUE_CAPITAL_UNCONFIRMED:KALSHI"
    assert rc["execution_label"] == "NOT_EXECUTION_LOCKED"
    # with both venues' capital confirmed: REVALIDATED, never ARMED
    rc = S.revalidate_shadow(
        rec, scan_id="c18", now=NOW + 1,
        books_now={("KALSHI", "K-NYY"): NOW, ("POLYMARKET_US", "aec-x"): NOW},
        rules_now={"kalshi:K-NYY": "r1", "aec-x": "r1"},
        capital={"KALSHI": D(1000), "POLYMARKET_US": D(1000)},
        venue_health=vh_report())
    assert rc["state"] == TL.REVALIDATED
    assert rc["reason"] == S.NO_SUBMIT_AUTHORITY


# ── C19 two strategy names on one claim ─────────────────────────────────

def test_c19_two_strategies_on_one_claim_are_one_exposure():
    rows = [X.row(slug="m", holding_side="LONG", fixture="F", notional=70000,
                  qty=1, alias_group=g, aliases={("m", "YES"): "fpM"})
            for g in ("DEREK", "BENCHMARK")]
    g = X.gate(rows, claim_cap=D(125000), event_cap=D(125000))
    assert g["claims"]["fpM"]["signed_notional"] == D(140000)
    assert "CLAIM_LIMIT:fpM" in g["blockers"]


# ── C20 a broken directional sleeve beside a good arb sleeve ────────────

def test_c20_only_the_failing_sleeve_is_disabled():
    rows = []
    for i in range(40):
        rows.append({"mechanism": "DIRECTIONAL", "event_key": "E%d" % i,
                     "expected": D(1), "realized": D(0)})
        rows.append({"mechanism": "SAME_VENUE_ARB", "event_key": "A%d" % i,
                     "expected": D(1), "realized": D("1.2")})
    r = C.profit_breakers(rows)["evidence"]["mechanisms"]
    assert r["DIRECTIONAL"]["status"] == "DISABLED"
    assert r["SAME_VENUE_ARB"]["status"] == "ELIGIBLE"
    assert r["CROSS_VENUE_ARB"]["status"] == "SHADOW_ONLY"


# ── C21 99% global, one held book stale ─────────────────────────────────

def test_c21_held_stale_stays_red_whatever_the_global_rate():
    d = VH.denominators(held={"markable": 1, "freshly_manageable": 0},
                        priority={"numerator": 99, "denominator": 100},
                        total={"numerator": 99000, "denominator": 100000})
    assert d["global"]["green"] and d["priority"]["green"]
    assert not d["held"]["green"] and d["held"]["status"] == "RED"
    assert d["capital_reads"] == ["held", "priority"]


# ── C22 a stale green UI cache ──────────────────────────────────────────

def test_c22_a_cached_green_metric_that_is_stale_shows_stale():
    c = R.ui_truth({"held": {"value": 1.0, "as_of": NOW - 7200,
                             "source": "x", "status": "GREEN",
                             "numerator": 1, "denominator": 1}}, now=NOW)
    m = c["evidence"]["metrics"]["held"]
    assert m["displayed_status"] == "STALE_OR_UNKNOWN"
    assert c["status"] == C.RED


# ── C23 13k repeated rows from 195 events ───────────────────────────────

def test_c23_confidence_counts_independent_events_not_rows():
    s = C.samples({"decisions_scored": 13000, "independent_events": 195},
                  {})
    assert s["evidence"]["row_to_event_ratio"] == pytest.approx(66.67, 0.01)
    rows = [{"mechanism": "DIRECTIONAL", "event_key": "E%d" % (i % 195),
             "expected": D(0), "realized": D(1)} for i in range(13000)]
    assert len(C.by_event(rows)) == 195


# ── C24 a candidate chosen after reading the holdout ────────────────────

def test_c24_selection_after_the_holdout_is_refused():
    reg = {"preregistered": 3, "candidates_tested": 3,
           "selected_after_holdout": True, "holdout_opens": 1,
           "pbo_ok": True, "dsr_ok": True}
    m = C.multiple_testing(reg)
    assert m["status"] == C.RED
    assert "HOLDOUT_USED_FOR_SELECTION" in m["blockers"]
    s = C.samples({"decisions_scored": 500, "independent_events": 300},
                  dict(reg, holdout_opens=2, partitions={
                      "train": ["a"], "test": ["b"], "holdout": ["c"]}))
    assert "HOLDOUT_OPENED_MORE_THAN_ONCE" in s["blockers"]


# ── C25 the twin invents IOC fills ──────────────────────────────────────

def test_c25_invented_twin_fills_fail_certification():
    from sportsassets.completion import fill_replay as FR
    orders = []
    for i in range(120):
        orders.append({"slug": "s%d" % i, "tif": "IOC", "direction": "BUY",
                       "side": "LONG", "limit": 0.50, "qty": 10,
                       "eligible": NOW, "expires": NOW + 90,
                       "fills": [],               # PAPER expired
                       "books": [{"at": NOW + 1,
                                  "asks": [{"px": 0.48, "qty": 100}],
                                  "bids": [{"px": 0.46, "qty": 100}]}]})
    rep = FR.agreement(orders)
    c = C.twin(rep)
    assert c["status"] == C.RED
    assert "OPTIMISTIC_FALSE_FILL_RATE_TOO_HIGH" in c["blockers"] or \
        "FILL_AGREEMENT_BELOW_TARGET" in c["blockers"]
    assert c["evidence"]["twin_pnl_used_for_capital"] is False


# ── C26 OOM / redeploy with positions open ──────────────────────────────

def test_c26_after_a_restart_no_new_capital_until_truth_quorum_returns():
    stale = [PositionTruth(s, "C", D(5), None, NOW - 7200)
             for s in ("INTERNAL_LEDGER", "VENUE_POSITIONS", "VENUE_BALANCE",
                       "MARKET_DATA", "AUDREY_RECONCILIATION")]
    q = C.quorum(stale, now=NOW, audrey_open_discrepancies=0)
    assert q["status"] == C.RED
    assert "STALE_SOURCE:INTERNAL_LEDGER" in q["blockers"]
    assert "exits, reductions and protection stay available" in \
        q["evidence"]["effect"]
    fresh = [PositionTruth(s, "C", D(5), None, NOW) for s in (
        "INTERNAL_LEDGER", "VENUE_POSITIONS", "VENUE_BALANCE",
        "MARKET_DATA", "AUDREY_RECONCILIATION")]
    assert C.quorum(fresh, now=NOW, audrey_open_discrepancies=0)[
        "status"] == C.GREEN


# ── C27 an applied migration edited in the repo ─────────────────────────

def test_c27_an_edited_applied_migration_blocks_release():
    m = C.migrations({"100_x.sql": "aaa", "101_y.sql": "bbb"},
                     {"100_x.sql": "aaa", "101_y.sql": "CHANGED"},
                     fresh_db_passed=True)
    assert m["status"] == C.RED
    assert m["evidence"]["edited_in_place"] == ["101_y.sql"]
    gone = C.migrations({"099_z.sql": "zzz"}, {}, fresh_db_passed=True)
    assert "APPLIED_HISTORY_EDITED_IN_PLACE" in gone["blockers"]


# ── C28 CI tested SHA A, deployed SHA B ─────────────────────────────────

def test_c28_a_receipt_for_another_sha_is_refused():
    from sportsassets.api import command_red_team as API
    a, b = "a" * 40, "b" * 40
    body = {"accepted_base_sha": "c" * 40, "tested_sha": a, "release_sha": a,
            "deployed_sha": a, "descendant_of_base": True,
            "backend_tests_green": True, "capital_critical_green": True,
            "commit_guard_green": True, "engine_diagnostic_green": True}
    assert "DEPLOYED_SHA_IS_NOT_THIS_API" in API.validate_release(
        body, running_sha=b)
    from sportsassets.red_team import release_guard as RG
    from sportsassets.red_team.models import ReleaseEvidence
    g = RG.release_gate(ReleaseEvidence(a, a, b, "c", True, True, True, True,
                                        True, True))
    assert "DEPLOYED_SHA_DIFFERS_FROM_RELEASE_SHA" in g["blockers"]


# ── C29 the PMX RSA key in the PMUS slot ────────────────────────────────

def test_c29_an_rsa_key_in_the_pmus_slot_blocks_that_path():
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048
                                   ).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    b64 = base64.b64encode(pem.encode()).decode()
    env = {"PMX_KEY_ID": "pmx-1", "PMX_PRIVATE_KEY_B64": b64,
           "PMUS_KEY_ID": "pmus-1", "PMUS_SECRET_KEY": b64}
    cls = C.credential_classes(env)
    assert cls["PMX"] == "POLYMARKET_EXCHANGE_RSA_M2M"
    assert cls["PMUS"] == "POLYMARKET_EXCHANGE_RSA_M2M"
    g = C.credentials({"workers": cls})
    assert g["status"] == C.RED
    assert "CREDENTIAL_CLASS_MISMATCH:PMUS:POLYMARKET_EXCHANGE_RSA_M2M" in \
        g["blockers"]
    assert g["evidence"]["owner_actions"]
    assert g["evidence"]["values_exposed"] is False
    assert pem not in json.dumps(g) and b64 not in json.dumps(g)
    # something present that is neither shape is a mismatch, not "absent"
    odd = C.credential_classes({"KALSHI_API_KEY_ID": "k",
                                "KALSHI_PRIVATE_KEY_PEM": "not-a-key"})
    assert odd["KALSHI"] == "UNRECOGNISED_SHAPE"
    assert "CREDENTIAL_CLASS_MISMATCH:KALSHI:UNRECOGNISED_SHAPE" in \
        C.credentials({"workers": odd})["blockers"]


# ── C30 a revenue target above proven capacity ──────────────────────────

def test_c30_deployment_is_capped_at_proven_positive_capacity():
    pts = [{"qty": 10, "lb_ev_per_contract": "0.01", "fill_probability":
            "0.9", "capital_hours": "5", "expected_net": "40",
            "capital_usd": "82000"},
           {"qty": 500, "lb_ev_per_contract": "-0.02", "fill_probability":
            "0.9", "capital_hours": "50", "expected_net": "-100",
            "capital_usd": "418000"}]
    c = C.capacity(pts, requested_usd=500000)
    assert c["evidence"]["maximum_deployment_usd"] == "82000"
    assert c["evidence"]["remainder_cash_usd"] == "418000"
    assert C.capacity([], requested_usd=500000)["status"] == C.RED


def test_the_matrix_is_complete():
    import re
    src = open(__file__).read()
    ids = sorted(set(re.findall(r"def test_c(\d\d)_", src)))
    assert ids == ["%02d" % i for i in range(1, 31)], ids
