"""KALSHI AS A CANONICAL VENUE: ECONOMIC CLAIMS, BEST ALL-IN ROUTES, SAME- AND
CROSS-VENUE ARBITRAGE (Kalshi Canonical Venue V1). The directive's 25
required proofs, numbered, plus the market-data / fixture / book contract.

Settlement terms in the "proven" fixtures below are STATED structurally (the
shape settlement_rule_registry produces): overtime counted, a 48 h
postponement window, and a cancelled or tied game settled at 0.50 per team
strike -- terms under which Yankees YES and Rays NO pay identically in every
state. The production terms (fair-price cancellations) are exercised too:
they make the same pair NOT_ESTABLISHED, by state.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sportsassets import canonical_claims as CC
from sportsassets import kalshi_fees as KF
from sportsassets import kalshi_claims as KCL
from sportsassets import kalshi_market_data as KMD
from sportsassets.agents import adriana_arb as A
from sportsassets.agents import adriana_claims as AC
from sportsassets.canonical_venue import canonical as PC
from sportsassets.canonical_venue import mapping_contract as MC
from sportsassets.canonical_venue.models import CanonicalEvent, PROVEN
from sportsassets.canonical_venue.normalization import \
    binary_proposition_instrument

NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc).timestamp()
START = NOW + 4 * 3600
D = Decimal

PROVEN_TERMS = {"overtime_included": True, "draw_rule": "SCALAR_0_50",
                "void_rule": "SCALAR_0_50", "postponement_window_hours": 48.0,
                "postponement_payout": "SCALAR_0_50",
                "verification_sources": ["MLB"]}
FAIR_PRICE_TERMS = {"overtime_included": True, "void_rule": "LAST_FAIR_PRICE",
                    "postponement_window_hours": 48.0,
                    "postponement_payout": "LAST_FAIR_PRICE",
                    "verification_sources": ["MLB"]}
FX = CC.Fixture(event_key="MLB:2026-10-08T00:00Z:TB@NYY", sport="BASEBALL",
                league="MLB", start_epoch=START, outcome_kind="TWO_WAY",
                home="NYY", away="TB")


#: the published Kalshi fee terms in force (kalshi_fees.effective_terms over
#: the venue's own change-record shape), multiplier 1: a Kalshi alias is a
#: route / arbitrage leg only with known terms (Kalshi rep 2026-10-07)
KTERMS = KF.effective_terms(
    series_ticker="KXMLBGAME", event_ticker="KXMLBGAME-26OCT072000TBNYY",
    at=NOW, event_changes=[], series_changes=[{
        "id": "test-change-x1", "fee_type": "quadratic_with_maker_fees",
        "fee_multiplier": 1, "scheduled_ts": "2025-10-04T07:00:00Z",
        "series_ticker": "KXMLBGAME"}])
_DEFAULT = object()


def inst(venue, market, side, subject, asks=((D("0.50"), 100),), *,
         terms=PROVEN_TERMS, at=NOW, status="PROVEN", mapping="ESTABLISHED",
         sport="BASEBALL", fee_terms=_DEFAULT):
    if fee_terms is _DEFAULT:
        fee_terms = KTERMS if venue == "KALSHI" else None
    return CC.Instrument(venue=venue, market_id=market, side=side,
                         subject=subject, settlement=dict(terms),
                         settlement_status=status, mapping_status=mapping,
                         asks=tuple(asks), observed_at=at,
                         book_basis="TEST_BOOK", sport=sport,
                         fee_terms=fee_terms)


def flat(_count, _price):
    return D(0)


def build(*instruments, fx=FX):
    return CC.build_claims(fx, list(instruments))


# ── 1-3 claim identity from the full payoff vector ────────────────────

def test_01_yankees_yes_is_rays_no_in_a_proven_two_way_game():
    y = inst("KALSHI", "K-NYY", "YES", "HOME")
    r = inst("KALSHI", "K-TB", "NO", "AWAY")
    b = build(y, r)
    assert y.fingerprint and y.fingerprint == r.fingerprint
    assert len(b["classes"]) == 1
    rec = CC.equivalence_receipt(FX, y, r, b["states"])
    assert rec["verdict"] == "SAME_CLAIM" and rec["states_differing"] == []


def test_02_yankees_yes_is_not_rays_yes():
    y = inst("KALSHI", "K-NYY", "YES", "HOME")
    t = inst("KALSHI", "K-TB", "YES", "AWAY")
    b = build(y, t)
    assert y.fingerprint != t.fingerprint and len(b["classes"]) == 2
    ok, _ = CC.complement_states(y.vector, t.vector, b["states"])
    assert ok                       # but they ARE complements (0.5 + 0.5)


def test_03_team_b_no_is_not_team_a_yes_in_three_way_soccer():
    fx = CC.Fixture(event_key="EPL:2026-10-10T14:00Z:CHE@ARS",
                    sport="SOCCER", league="EPL", start_epoch=START,
                    outcome_kind="THREE_WAY", home="ARS", away="CHE")
    terms = dict(PROVEN_TERMS, draw_rule=None)
    ars = inst("KALSHI", "K-ARS", "YES", "HOME", terms=terms)
    che_no = inst("KALSHI", "K-CHE", "NO", "AWAY", terms=terms)
    tie = inst("KALSHI", "K-TIE", "YES", "DRAW", terms=terms)
    b = build(ars, che_no, tie, fx=fx)
    assert ars.fingerprint != che_no.fingerprint
    assert che_no.vector["DRAW"] == "1" and ars.vector["DRAW"] == "0"
    rec = CC.equivalence_receipt(fx, ars, che_no, b["states"])
    assert "DRAW" in rec["states_differing"]
    assert rec["verdict"] == "NOT_ESTABLISHED"


def test_the_fingerprint_is_the_packages_own_for_numeric_vectors():
    y = inst("KALSHI", "K-NYY", "YES", "HOME")
    b = build(y)
    ev = CanonicalEvent(event_key=FX.event_key, sport="BASEBALL",
                        league="MLB", start_time="x", outcomes=tuple(
                            b["states"]), exhaustive=True, basis="t")
    pi = binary_proposition_instrument(
        venue="KALSHI", instrument_id="K-NYY", market_id="K-NYY", side="YES",
        event=ev, family="MONEYLINE", period="FULL_GAME",
        subject_outcomes={s for s in b["states"] if y.vector[s] == "1"},
        nonstandard_payoff={s: D(y.vector[s]) for s in b["states"]
                            if y.vector[s] not in ("0", "1")})
    assert PC.claim_fingerprint(ev, pi) == y.fingerprint


# ── 4-8 routing: the cheapest proven all-in path ─────────────────────

def route(cls_members, qty, fees=None, now=NOW, max_age_s=30):
    fees = fees if fees is not None else {"KALSHI": flat,
                                          "POLYMARKET_US": flat}
    fp = cls_members[0].fingerprint
    return CC.route_claim(FX, fp, cls_members, qty=qty, now=now,
                          fee_by_venue=fees, max_age_s=max_age_s)


def test_04_kalshi_yankees_yes_vs_rays_no_cheapest_all_in_wins():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.49"), 50)])
    r = inst("KALSHI", "K-TB", "NO", "AWAY", [(D("0.45"), 50)])
    build(y, r)
    got = route([y, r], 10)
    assert got["chosen"]["allocations"][0]["market_id"] == "K-TB"
    assert got["chosen"]["allocations"][0]["side"] == "NO"
    assert got["best_single"]["market_id"] == "K-TB"
    assert got["runner_up"]["market_id"] == "K-NYY"
    assert any(x["why"].startswith("ALL_IN_HIGHER_BY") for x in got["lost"])


def test_05_fees_can_reverse_the_nominal_price_choice():
    k = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.49"), 50)])
    p = inst("POLYMARKET_US", "aec-mlb-tb-nyy", "NO", "HOME",
             [(D("0.50"), 50)])
    p.subject = "AWAY"            # NO on the TB-long market = NYY claim
    build(k, p)
    assert k.fingerprint == p.fingerprint

    def kfee(count, price):
        return D("0.05") * count         # an expensive venue
    got = route([k, p], 10, fees={"KALSHI": kfee, "POLYMARKET_US": flat})
    assert got["chosen"]["allocations"][0]["venue"] == "POLYMARKET_US"
    assert D(got["best_single"]["all_in"]) == D("5.00")


def test_06_a_stale_cheaper_quote_loses():
    stale = inst("KALSHI", "K-TB", "NO", "AWAY", [(D("0.45"), 50)],
                 at=NOW - 120)
    cur = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.47"), 50)])
    build(stale, cur)
    got = route([stale, cur], 5)
    assert got["chosen"]["allocations"][0]["market_id"] == "K-NYY"
    assert {"venue": "KALSHI", "market_id": "K-TB", "side": "NO",
            "why": "STALE_BOOK"} in got["lost"]


def test_07_depth_is_respected():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.44"), 3)])
    build(y)
    got = route([y], 5)
    assert got["chosen"] is None and got["refusal"] == "NO_ELIGIBLE_ROUTE"
    assert got["candidates"][0]["reason"] == "INSUFFICIENT_DEPTH"


def test_08_split_routing_3_plus_3_plus_4():
    a = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.44"), 3)])
    b = inst("KALSHI", "K-TB", "NO", "AWAY", [(D("0.45"), 3)])
    c = inst("POLYMARKET_US", "aec-mlb-tb-nyy", "NO", "AWAY",
             [(D("0.46"), 20)])
    c.subject = "AWAY"
    build(a, b, c)
    assert a.fingerprint == b.fingerprint == c.fingerprint
    got = route([a, b, c], 10)
    alloc = {(x["venue"], x["market_id"]): x["qty"]
             for x in got["chosen"]["allocations"]}
    assert alloc == {("KALSHI", "K-NYY"): 3, ("KALSHI", "K-TB"): 3,
                     ("POLYMARKET_US", "aec-mlb-tb-nyy"): 4}
    assert got["chosen"]["topology"] == "CROSS_VENUE_SPLIT"
    assert D(got["chosen"]["all_in"]) == D("4.51")   # 1.32+1.35+1.84


def test_19_unknown_fee_refuses_the_route():
    p = inst("POLYMARKET_US", "aec-mlb-tb-nyy", "NO", "AWAY",
             [(D("0.40"), 50)])
    build(p)
    got = route([p], 5, fees={"KALSHI": flat})
    assert got["chosen"] is None
    assert got["candidates"][0]["reason"] == "FEE_UNKNOWN"


def test_the_exact_optimizer_bound_refuses_rather_than_approximates():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.44"), 5000)])
    build(y)
    got = CC.route_claim(FX, y.fingerprint, [y], qty=3000, now=NOW,
                         fee_by_venue={"KALSHI": flat}, max_age_s=30,
                         max_opt_qty=2000)
    assert got["chosen"] is None
    assert got["refusal"] == "QTY_ABOVE_EXACT_OPTIMIZER_BOUND"


# ── 9-12, 20-21 Adriana, claim-first ──────────────────────────────────

def scan(*instruments, now=NOW, **kw):
    b = build(*instruments)
    return AC.scan_fixture(FX, b, now=now, **kw)


def _verdicts(res):
    return [(r["verdict"], r["claim_pair"]["topology"]) for r in
            res["records"]]


def test_09_same_venue_kalshi_arb_is_discovered():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.44"), 100)])
    t = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.45"), 100)])
    res = scan(y, t)
    rec = res["records"][0]
    assert rec["verdict"] == A.GUARANTEED_AFTER_COSTS, rec["reasons"]
    assert rec["claim_pair"]["topology"] == "SAME_VENUE"
    eco = rec["economics"]
    assert D(eco["worst_case_net_profit"]) > 0


def test_10_same_venue_polymarket_arb_is_discovered():
    # two PMUS markets on one game (e.g. a re-listed moneyline): the NYY
    # claim on one, the TB claim on the other
    y = inst("POLYMARKET_US", "aec-mlb-nyy-tb", "YES", "HOME",
             [(D("0.44"), 100)])
    t = inst("POLYMARKET_US", "aec-mlb-tb-nyy", "YES", "AWAY",
             [(D("0.45"), 100)])
    res = scan(y, t)
    rec = res["records"][0]
    assert rec["verdict"] == A.GUARANTEED_AFTER_COSTS, rec["reasons"]
    assert rec["claim_pair"]["topology"] == "SAME_VENUE"


def test_11_cross_venue_arb_is_discovered_and_topology_set_after_pricing():
    yk = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.44"), 100)])
    yp = inst("POLYMARKET_US", "aec-mlb-tb-nyy", "NO", "HOME",
              [(D("0.48"), 100)])
    tk = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.52"), 100)])
    tp = inst("POLYMARKET_US", "aec-mlb-tb-nyy", "YES", "AWAY",
              [(D("0.45"), 100)])
    res = scan(yk, yp, tk, tp)
    rec = res["records"][0]
    assert rec["verdict"] == A.GUARANTEED_AFTER_COSTS, rec["reasons"]
    used = {(x["venue"], x["market_id"]) for x in rec["economics"]["legs"]}
    assert used == {("KALSHI", "K-NYY"), ("POLYMARKET_US", "aec-mlb-tb-nyy")}
    assert rec["claim_pair"]["topology"] == "CROSS_VENUE"


def test_12_an_explicit_no_may_be_the_cheapest_arb_leg():
    y_yes = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.49"), 100)])
    tb_no = inst("KALSHI", "K-TB", "NO", "HOME", [(D("0.44"), 100)])
    tb_no.subject = "AWAY"
    t_yes = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.48"), 100)])
    nyy_no = inst("KALSHI", "K-NYY", "NO", "HOME", [(D("0.46"), 100)])
    res = scan(y_yes, tb_no, t_yes, nyy_no)
    rec = res["records"][0]
    assert rec["verdict"] == A.GUARANTEED_AFTER_COSTS, rec["reasons"]
    used = {(x["market_id"], x["side"]) for x in rec["economics"]["legs"]}
    assert used == {("K-TB", A.NO), ("K-NYY", A.NO)}


def test_20_book_skew_blocks_the_arb():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.44"), 100)], at=NOW)
    t = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.45"), 100)],
             at=NOW - 20)
    rec = scan(y, t)["records"][0]
    assert rec["verdict"] == A.REFUSED
    assert A.UNSYNCHRONIZED_BOOKS in A.reason_codes(rec)


def test_21_all_in_cost_at_or_above_payout_refuses():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.50"), 100)])
    t = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.51"), 100)])
    rec = scan(y, t)["records"][0]
    assert rec["verdict"] == A.REFUSED
    assert set(A.reason_codes(rec)) & {A.PAYOFF_FLOOR_BELOW_COST,
                                       A.NOT_PROFITABLE_AFTER_COSTS}


def test_a_stale_book_cannot_make_an_arb():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.30"), 100)],
             at=NOW - 300)
    t = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.45"), 100)])
    rec = scan(y, t)["records"][0]
    assert rec["verdict"] == A.REFUSED
    assert A.STALE_BOOK in A.reason_codes(rec)


def test_fair_price_terms_same_market_never_arb_cross_market_no_complement():
    """KALSHI REP 2026-10-07: one market's YES and NO are ONE pool (NO ask =
    1 - best YES bid) and positions in them NET -- a market's YES + its own
    NO is never structural arbitrage (excluded and counted, never
    evaluated). A per-market fair-price symbol complements only its own
    market's NO, so cross-market FP legs are no complement either."""
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.44"), 100)],
             terms=FAIR_PRICE_TERMS)
    n = inst("KALSHI", "K-NYY", "NO", "HOME", [(D("0.57"), 100)],
             terms=FAIR_PRICE_TERMS)
    t = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.45"), 100)],
             terms=FAIR_PRICE_TERMS)
    b = build(y, n, t)
    assert "FP[K-NYY]" in y.vector.values() and "1-FP[K-NYY]" in \
        n.vector.values()
    assert y.fingerprint is None and not b["classes"]
    assert CC.same_market(y, n) and not CC.same_market(y, t)
    ok, bad = CC.complement_states(y.vector, t.vector, b["states"])
    assert not ok and any("FP" in why or "UNKNOWN" in why for _s, why in bad)
    assert [(p[0].market_id, p[1].market_id) for p in b["reciprocal"]] == [
        ("K-NYY", "K-NYY")]
    res = AC.scan_fixture(FX, b, now=NOW)
    assert res["pairs_considered"] == 0 and res["records"] == []
    assert res["same_market_pairs_excluded"] == 1


# ── 13-18 settlement and mapping fail closed ──────────────────────────

def test_13_a_settlement_mismatch_blocks_equivalence():
    y = inst("KALSHI", "K-NYY", "YES", "HOME")
    p = inst("POLYMARKET_US", "aec-mlb-tb-nyy", "NO", "AWAY",
             terms=dict(PROVEN_TERMS, void_rule="RESOLVES_NO"))
    b = build(y, p)
    assert y.fingerprint != p.fingerprint
    rec = CC.equivalence_receipt(FX, y, p, b["states"])
    assert "NEVER_COMPLETED" in rec["states_differing"]


def test_13b_production_fair_price_terms_keep_yes_and_other_no_apart():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", terms=FAIR_PRICE_TERMS)
    r = inst("KALSHI", "K-TB", "NO", "AWAY", terms=FAIR_PRICE_TERMS)
    b = build(y, r)
    rec = CC.equivalence_receipt(FX, y, r, b["states"])
    assert rec["verdict"] == "NOT_ESTABLISHED"
    assert "NEVER_COMPLETED" in rec["states_differing"]


def test_14_unknown_settlement_blocks_equivalence():
    y = inst("KALSHI", "K-NYY", "YES", "HOME",
             terms=dict(PROVEN_TERMS, void_rule=None))
    r = inst("KALSHI", "K-TB", "NO", "AWAY")
    b = build(y, r)
    assert y.fingerprint is None and y in b["refused"]
    assert any(x.startswith("UNKNOWN_STATES") for x in y.refusals)
    u = inst("KALSHI", "K-TB", "YES", "AWAY", status="NOT_PROVEN")
    build(u)
    assert "SETTLEMENT_RULES_NOT_PARSED" in u.refusals


def test_15_and_16_period_and_line_mismatch_block_mapping():
    base = MC.MarketIdentity("SPREAD", "FULL_GAME", D("1.5"), "MLB:NYY")
    assert MC.map_market_exact(base, MC.MarketIdentity(
        "SPREAD", "FIRST_5", D("1.5"), "MLB:NYY")).reasons == (
        "PERIOD_MISMATCH",)
    assert MC.map_market_exact(base, MC.MarketIdentity(
        "SPREAD", "FULL_GAME", D("2.5"), "MLB:NYY")).reasons == (
        "LINE_MISMATCH",)


def _kfx(league="MLB", home="NYY", away="TB", start=START):
    return KMD.KalshiFixture(
        event_ticker="KXMLBGAME-26OCT072000TBNYY", series_ticker="KXMLBGAME",
        sport=KMD.LEAGUE_SPORT[league][0], league=league, start_epoch=start,
        home_id="h", away_id="a", home_code=home, away_code=away,
        tie_ticker=None, team_tickers=("K-NYY", "K-TB"),
        outcome_kind="TWO_WAY", status="ESTABLISHED", reasons=(),
        milestone_id="m")


def test_17_an_nba_wnba_collision_cannot_map():
    k = _kfx(league="NBA", home="NY", away="LV")
    got = KCL.map_pmus(k, [{"slug": "aec-wnba-lv-ny-2026-10-08",
                            "league": "wnba", "team_a": "lv",
                            "team_b": "ny", "start_epoch": START}])
    assert got["status"] == "NOT_ESTABLISHED"
    assert "LEAGUE_MISMATCH" in got["rejected"]["aec-wnba-lv-ny-2026-10-08"]


def test_18_an_ambiguous_fixture_refuses_and_a_doubleheader_is_split():
    k = _kfx()
    two = [{"slug": "g1", "league": "mlb", "team_a": "tb", "team_b": "nyy",
            "start_epoch": START},
           {"slug": "g1b", "league": "mlb", "team_a": "nyy", "team_b": "tb",
            "start_epoch": START + 60}]
    got = KCL.map_pmus(k, two)
    assert got["status"] == "NOT_ESTABLISHED"
    assert got["reasons"][0].startswith("AMBIGUOUS")
    dh = [{"slug": "g1", "league": "mlb", "team_a": "tb", "team_b": "nyy",
           "start_epoch": START},
          {"slug": "g2", "league": "mlb", "team_a": "tb", "team_b": "nyy",
           "start_epoch": START + 4 * 3600}]
    one = KCL.map_pmus(k, dh)
    assert one["status"] == "ESTABLISHED" and one["pmus"]["slug"] == "g1"


def test_a_code_mismatch_never_maps_by_name():
    got = KCL.map_pmus(_kfx(), [{"slug": "x", "league": "mlb",
                                 "team_a": "tbr", "team_b": "nyy",
                                 "start_epoch": START}])
    assert got["status"] == "NOT_ESTABLISHED"


# ── 22-23 venue health never blends ───────────────────────────────────

def test_22_and_23_kalshi_health_is_its_own_domain():
    clock = {"t": 1000.0}
    h = KMD.KalshiHealth(clock=lambda: clock["t"])
    h.record("HTTP_429")
    assert h.blocked() and h.digest()["http_429"] == 1
    assert h.digest()["domain"] == "KALSHI_HEALTH"
    # the Kalshi module neither reads nor writes Polymarket health
    import inspect
    src = inspect.getsource(KMD)
    for w in ("institutional_stream", "pmus", "pmx_institutional",
              "bettor_paper_freshness", "POLYMARKET_HEALTH"):
        assert w not in src.replace("Polymarket health", ""), w
    clock["t"] += 120
    assert not h.blocked()
    h.record(None)
    assert h.consecutive_failures == 0 and h.digest()["backing_off"] is False


def test_the_backoff_is_bounded_and_the_walk_stops_named_on_429():
    class TX:
        def get(self, url, params=None, timeout=None):
            class R:
                status_code = 429

                def json(self):
                    return {"error": "too many requests"}
            return R()
    h = KMD.KalshiHealth(clock=time.time)
    got = KMD.walk_open_sports(TX(), health=h, sleep=lambda s: None)
    assert got["complete"] is False
    assert got["stopped"] == KMD.R_RATE_LIMITED
    assert h.http_429 == 1 and h.blocked()


# ── 24 Adriana's authority ───────────────────────────────────────────

def test_24_adriana_holds_zero_submit_cancel_capital_authority():
    assert AC.assert_no_authority()
    for k in ("submit", "cancel", "capital"):
        assert AC.AUTHORITY[k] is False
    import ast
    import inspect
    for mod in (AC, CC, KCL, KMD):
        tree = ast.parse(inspect.getsource(mod))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                names |= {(n.module or "")} | {a.name for a in n.names}
        for bad in ("kalshi_venue", "kalshi_orders", "pmus", "execmirror",
                    "live_executor", "execution_gate", "kalshi_account"):
            assert bad not in names, (mod.__name__, bad)


# ── the market-data contract ─────────────────────────────────────────

def test_the_documented_reciprocal_book_gives_two_ask_ladders():
    ob = {"orderbook_fp": {"yes_dollars": [["0.4800", "100.00"],
                                           ["0.4900", "50.00"]],
                           "no_dollars": [["0.4500", "30.00"],
                                          ["0.5000", "20.00"]]}}
    b = KMD.book_from_orderbook(ob, observed_at=NOW)
    assert b["yes_asks"][0] == (D("0.5000"), 20)     # 1 - best NO bid
    assert b["no_asks"][0] == (D("0.5100"), 50)      # 1 - best YES bid
    assert b["basis"] == "KALSHI_DOCUMENTED_RECIPROCAL_BOOK"
    assert KMD.ORDERBOOK_PROTOCOL["sha256"].startswith("f1777255")
    q = KMD.quote_agreement({"yes_ask_dollars": "0.5000",
                             "no_ask_dollars": "0.5100"}, b)
    assert q["agree"] is True


def _market(ticker, team_id, event="KXMLBGAME-26OCT072000TBNYY"):
    return {"ticker": ticker, "event_ticker": event,
            "custom_strike": {"baseball_team": team_id}}


MILESTONE = {"id": "m1", "start_date": "2026-10-08T00:00:00Z",
             "primary_event_tickers": ["KXMLBGAME-26OCT072000TBNYY"],
             "details": {"league": "MLB", "home_team_id": "H-NYY",
                         "away_team_id": "A-TB",
                         "main_game_event_ticker":
                             "KXMLBGAME-26OCT072000TBNYY"}}


def test_a_kalshi_fixture_is_built_only_from_structured_ids():
    ms = [_market("KXMLBGAME-26OCT072000TBNYY-NYY", "H-NYY"),
          _market("KXMLBGAME-26OCT072000TBNYY-TB", "A-TB")]
    fx = KMD.fixture_from("KXMLBGAME-26OCT072000TBNYY", ms, MILESTONE)
    assert fx.status == "ESTABLISHED", fx.reasons
    assert (fx.home_code, fx.away_code, fx.league) == ("NYY", "TB", "MLB")
    assert KCL.fixture_of(fx).event_key == "MLB:2026-10-08T00:00Z:TB@NYY"
    assert KMD.fixture_from("KXMLBGAME-26OCT072000TBNYY", ms,
                            None).status == "NOT_ESTABLISHED"
    bad = [_market("KXMLBGAME-26OCT072000TBNYY-NYY", "H-NYY"),
           _market("KXMLBGAME-26OCT072000TBNYY-TB", "SOMEONE-ELSE")]
    r = KMD.fixture_from("KXMLBGAME-26OCT072000TBNYY", bad, MILESTONE)
    assert r.status == "NOT_ESTABLISHED"
    assert "STRIKE_TEAM_NOT_IN_FIXTURE" in r.reasons
    wnba = dict(MILESTONE, details=dict(MILESTONE["details"],
                                        league="WNBA"))
    w = KMD.fixture_from("KXMLBGAME-26OCT072000TBNYY", ms, wnba)
    assert w.league == "WNBA" and w.sport == "BASKETBALL"


def test_the_catalogue_walk_is_complete_only_on_cursor_exhaustion():
    pages = {"/series": [{"series": [{"ticker": "KXMLBGAME",
                                      "category": "Sports"}]}],
             "/markets": [{"markets": [_market("KXMLBGAME-E-NYY", "x",
                                               "KXMLBGAME-E"),
                                       {"ticker": "KXBTC-1",
                                        "event_ticker": "KXBTC-1"}],
                           "cursor": "c2"},
                          {"markets": [], "cursor": ""}]}

    class TX:
        def get(self, url, params=None, timeout=None):
            path = url.replace(KMD.BASE, "")
            body = pages[path].pop(0)

            class R:
                status_code = 200

                def json(self, _b=body):
                    return _b
            return R()
    got = KMD.walk_open_sports(TX(), sleep=lambda s: None)
    assert got["complete"] is True and got["pages"] == 2
    assert [m["ticker"] for m in got["markets"]] == ["KXMLBGAME-E-NYY"]
    assert got["markets_seen"] == 2
    c = KMD.census(got)
    assert c["sports_markets"] == 1 and c["complete"] is True
    budget = KMD.walk_open_sports(TX(), max_requests=2,
                                  sleep=lambda s: None)
    assert budget["complete"] is False


def test_kalshi_and_pmus_instruments_bind_end_to_end():
    ms = {"KXMLBGAME-26OCT072000TBNYY-NYY": _market(
        "KXMLBGAME-26OCT072000TBNYY-NYY", "H-NYY"),
        "KXMLBGAME-26OCT072000TBNYY-TB": _market(
        "KXMLBGAME-26OCT072000TBNYY-TB", "A-TB")}
    k = KMD.fixture_from("KXMLBGAME-26OCT072000TBNYY", list(ms.values()),
                         MILESTONE)
    k = KMD.KalshiFixture(**dict(k.__dict__, team_tickers=tuple(sorted(ms))))
    ob = {"orderbook_fp": {"yes_dollars": [["0.5000", "100.00"]],
                           "no_dollars": [["0.4900", "100.00"]]}}
    books = {t: KMD.book_from_orderbook(ob, observed_at=NOW) for t in ms}
    ev = {t: {"status": "ESTABLISHED", "settlement": PROVEN_TERMS}
          for t in ms}
    ki = KCL.kalshi_instruments(k, ms, books, ev)
    assert len(ki) == 4 and {i.side for i in ki} == {"YES", "NO"}
    m = KCL.map_pmus(k, [{"slug": "aec-mlb-tb-nyy-2026-10-07",
                          "league": "mlb", "team_a": "tb", "team_b": "nyy",
                          "start_epoch": START}])
    assert m["status"] == "ESTABLISHED"
    pi = KCL.pmus_instruments(k, m["pmus"], evidence={
        "status": "ESTABLISHED", "settlement": PROVEN_TERMS},
        book={"offers": [("0.46", 100)], "bids": [("0.44", 100)],
              "observed_at": NOW})
    assert [i.subject for i in pi] == ["AWAY", "AWAY"]
    built = CC.build_claims(KCL.fixture_of(k), ki + pi)
    fx = KCL.fixture_of(k)
    # NYY claim aliases: Kalshi NYY YES, Kalshi TB NO, PMUS TB-long NO
    nyy = [c for c in built["classes"].values()
           if any(i.market_id.endswith("-NYY") and i.side == "YES"
                  for i in c)][0]
    assert {(i.venue, i.side) for i in nyy} == {
        ("KALSHI", "YES"), ("KALSHI", "NO"), ("POLYMARKET_US", "NO")}
    fees = CC.fee_functions(at=NOW, sport="BASEBALL")
    got = CC.route_claim(fx, nyy[0].fingerprint, nyy, qty=10, now=NOW,
                         fee_by_venue=fees, max_age_s=30)
    assert got["chosen"] is not None


def test_25_existing_kalshi_isolation_tests_are_untouched():
    import pathlib
    p = pathlib.Path(__file__).with_name("test_kalshi_isolation.py")
    assert p.exists()
    src = (pathlib.Path(__file__).resolve().parents[1] / "sportsassets" /
           "kalshi_market_data.py").read_text()
    assert "import kalshi_venue" not in src and "kalshi_orders" not in \
        src.replace("nor kalshi_orders", "")


def test_the_restated_settlement_vocabulary_is_kalshi_mappings_own():
    from sportsassets import kalshi_mapping as KMAP
    assert CC.KM.RULE_PAYOFF == KMAP.RULE_PAYOFF
    for w in ([], [48.0], [48.0, 336.0], [2.0, 48.0, 336.0]):
        assert CC.KM._band_states(w) == KMAP._band_states(w)
    for v in (None, "", " last_fair_price ", "x"):
        assert CC.KM._norm(v) == KMAP._norm(v)


def test_adrianas_claim_reader_never_reaches_a_kalshi_order_path():
    """agents/adriana_runner reads canonical_claims_db; its whole import
    closure holds no Kalshi client, order or account module."""
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
    seen, stack = set(), ["canonical_claims_db"]
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        p = root / (m.replace(".", "/") + ".py")
        if not p.exists():
            continue
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.level >= 1:
                base = m.split(".")[:-n.level] if "." in m else []
                mod = ".".join(base + ([n.module] if n.module else []))
                for a in n.names:
                    cand = (mod + "." + a.name).strip(".")
                    stack.append(cand if (root / (cand.replace(".", "/")
                                                  + ".py")).exists() else mod)
    for bad in ("kalshi_venue", "kalshi_orders", "kalshi_account",
                "kalshi_linkage", "execmirror", "pmus", "live_executor",
                "execution_gate"):
        assert not any(s.split(".")[-1] == bad for s in seen), (bad, seen)
    assert "kalshi_market_data" in seen and "canonical_claims" in seen


def test_the_backend_package_is_the_imported_package_byte_for_byte():
    import hashlib
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    pkg = (root / "research" / "kalshi_canonical_venue" /
           "BETTOR_KALSHI_CANONICAL_VENUE_V1" / "bettor_kalshi_venue")
    back = root / "backend" / "sportsassets" / "canonical_venue"
    names = sorted(p.name for p in pkg.glob("*.py"))
    assert names == sorted(p.name for p in back.glob("*.py"))
    for n in names:
        assert hashlib.sha256((pkg / n).read_bytes()).digest() == \
            hashlib.sha256((back / n).read_bytes()).digest(), n


def test_the_package_tests_pass_against_the_backend_copy(monkeypatch):
    import importlib
    import importlib.util
    import pathlib
    import sys
    from sportsassets import canonical_venue as CV
    monkeypatch.setitem(sys.modules, "bettor_kalshi_venue", CV)
    for sub in ("models", "canonical", "normalization", "quotes",
                "arbitrage", "mapping_contract"):
        monkeypatch.setitem(sys.modules, "bettor_kalshi_venue." + sub,
                            importlib.import_module(
                                "sportsassets.canonical_venue." + sub))
    root = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_pkg_kalshi_tests", root / "research" / "kalshi_canonical_venue" /
        "BETTOR_KALSHI_CANONICAL_VENUE_V1" / "tests" /
        "test_kalshi_canonical_venue.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ran = 0
    for k in sorted(dir(mod)):
        if k.startswith("test_") and callable(getattr(mod, k)):
            getattr(mod, k)()
            ran += 1
    assert ran == 17
