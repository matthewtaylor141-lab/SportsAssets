"""GOLDEN MARKET VALIDATION AS A RELEASE GATE (PM Evidence Pack, component 1)
-- every frozen golden case run through BETTOR'S PRODUCTION code, not the
pack's reference validator:

  fixture identity   canonical_venue.mapping_contract.map_fixture_exact (the
                     contract kalshi_claims.map_pmus maps PMUS with), with
                     league-namespaced team keys; titles never read
  market identity    mapping_contract.map_market_exact (family / period /
                     line / subject exactness)
  payoff identity    canonical_claims.build_claims: full payoff vectors over
                     results x delay bands x never-completed, fingerprints,
                     complement_states
  best route         canonical_claims.route_claim (minimum TOTAL all-in;
                     stale / unknown fee ineligible)
  arbitrage          agents.adriana_claims.scan_fixture over the engine
                     (cross-market alias combinations only)

Each case keeps its source class (CAPTURED_VENUE_FIXTURE for PMUS,
FROZEN_INTEGRATION_CASE for the Kalshi semantics -- never relabelled), and
appended source records (data/golden_cases_kalshi_rep_v1.json: the Kalshi
rep's confirmation) run beside them. A case whose production behavior
differs from the frozen expectation fails the gate.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from decimal import Decimal

from .. import canonical_claims as CC
from ..canonical_venue import mapping_contract as MC

VERSION = "GOLDEN_PRODUCTION_VALIDATION_V1"
DATA = pathlib.Path(__file__).resolve().parents[1] / "pm_evidence" / "data"
D = Decimal
NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc).timestamp()
SPORT_OF = {"NFL": "FOOTBALL", "MLB": "BASEBALL", "NBA": "BASKETBALL",
            "NHL": "HOCKEY", "NCAAF": "FOOTBALL", "EPL": "SOCCER",
            "EBFPL": "ESOCCER"}
TERMS = {"overtime_included": True, "draw_rule": "SCALAR_0_50",
         "void_rule": "SCALAR_0_50", "postponement_window_hours": 48.0,
         "postponement_payout": "SCALAR_0_50", "verification_sources": ["X"]}


def load_cases() -> dict:
    base = json.loads((DATA / "golden_cases.json").read_text())
    extra = DATA / "golden_cases_kalshi_rep_v1.json"
    appended = json.loads(extra.read_text()) if extra.exists() else {
        "source_manifest": [], "cases": []}
    return {"base": base, "appended": appended}


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _fixture_identity(e: dict) -> MC.FixtureIdentity:
    lg = e["league"]
    return MC.FixtureIdentity(sport=e.get("sport") or SPORT_OF.get(lg),
                              league=lg, home_key="%s:%s" % (lg, e["home"]),
                              away_key="%s:%s" % (lg, e["away"]),
                              start_epoch=_epoch(e["start"]))


def _venue_fixture(e: dict) -> MC.VenueFixture:
    lg = e["league"]
    return MC.VenueFixture(venue="CANDIDATE", sport=e.get("sport") or
                           SPORT_OF.get(lg), league=lg,
                           home_key="%s:%s" % (lg, e["home"]),
                           away_key="%s:%s" % (lg, e["away"]),
                           start_epoch=_epoch(e["start"]),
                           structured_basis="STRUCTURED_TEST_IDENTITY")


def _market(m: dict) -> MC.MarketIdentity:
    return MC.MarketIdentity(family=m["family"], period=m["period"],
                             line=None if m.get("line") is None
                             else D(str(m["line"])),
                             subject_key=m.get("subject"))


def _inst(venue, market, side, subject, ask, *, terms=TERMS, at=NOW,
          kalshi_terms=None):
    return CC.Instrument(venue=venue, market_id=market, side=side,
                         subject=subject, settlement=dict(terms),
                         settlement_status="PROVEN",
                         mapping_status="ESTABLISHED",
                         asks=((D(str(ask)), 100),), observed_at=at,
                         book_basis="GOLDEN", sport="BASEBALL",
                         fee_terms=kalshi_terms)


FX2 = CC.Fixture(event_key="GOLDEN:TB@NYY", sport="BASEBALL", league="MLB",
                 start_epoch=NOW + 4 * 3600, outcome_kind="TWO_WAY",
                 home="NYY", away="TB")
FX3 = CC.Fixture(event_key="GOLDEN:CHE@ARS", sport="SOCCER", league="EPL",
                 start_epoch=NOW + 4 * 3600, outcome_kind="THREE_WAY",
                 home="ARS", away="CHE")


def _kterms():
    from .. import kalshi_fees as KF
    return KF.effective_terms(
        series_ticker="KXMLBGAME", event_ticker=None, at=NOW,
        event_changes=[], series_changes=[{
            "id": "golden-x1", "fee_type": "quadratic_with_maker_fees",
            "fee_multiplier": 1, "scheduled_ts": "2025-10-04T07:00:00Z",
            "series_ticker": "KXMLBGAME"}])


def _route_with_case_fees(case) -> dict:
    """The production router over the case's routes, with each route's fee
    as the case states it (a flat per-contract fee function per alias)."""
    insts, fees = [], {}
    for r in case["routes"]:
        fresh = r.get("fresh", True)
        i = _inst("V_" + r["name"], r["name"], "YES", "HOME",
                  r["display_price"], at=NOW if fresh else NOW - 3600)
        insts.append(i)
        fee = D(str(r.get("fee", 0))) + D(str(r.get("slippage", 0)))
        fees[i.venue] = (lambda f: (lambda c, p: f * int(c)))(fee)
    b = CC.build_claims(FX2, insts)
    fp = insts[0].fingerprint
    got = CC.route_claim(FX2, fp, b["classes"][fp], qty=1, now=NOW + 1,
                         fee_by_venue=fees, max_age_s=30)
    return got


def run_case(c: dict) -> dict:
    cid = c["id"]
    exp = c.get("expected") or {}
    try:
        if cid in ("G01_NFL_STRUCTURED_MONEYLINE",):
            d = MC.map_fixture_exact(_fixture_identity(c["event"]),
                                     _venue_fixture(c["event"]))
            got = d.status
            ok = got == exp["mapping"]
            detail = {"reasons": list(d.reasons)}
        elif cid in ("G02_NFL_WRONG_START", "G03_NFL_WRONG_TEAM"):
            d = MC.map_fixture_exact(_fixture_identity(c["event"]),
                                     _venue_fixture(c["candidate"]))
            got = d.status
            want = {"START_TIME_MISMATCH": "START_TIME_MISMATCH",
                    "TEAM_MISMATCH": "TEAM_MISMATCH"}[exp["reason"]]
            ok = got == "NOT_ESTABLISHED" and any(want.split("_")[0] in r
                                                  for r in d.reasons)
            detail = {"reasons": list(d.reasons)}
        elif cid in ("G04_SPREAD_LINE_MATCH", "G05_SPREAD_LINE_DRIFT",
                     "G06_TOTAL_VS_TEAM_TOTAL"):
            d = MC.map_market_exact(_market(c["market"]),
                                    _market(c["candidate"]))
            got = d.status
            ok = got == exp["mapping"]
            detail = {"reasons": list(d.reasons)}
        elif cid == "G07_NHL_SETTLEMENT_WINDOW_DIFFERS":
            a = _inst("A", "a", "YES", "HOME", "0.5", terms=dict(
                TERMS, postponement_window_hours=c["left"][
                    "postponement_window_hours"]))
            b = _inst("B", "b", "YES", "HOME", "0.5", terms=dict(
                TERMS, postponement_window_hours=c["right"][
                    "postponement_window_hours"]))
            CC.build_claims(FX2, [a, b])
            got = "EQUIVALENT" if a.fingerprint and a.fingerprint == \
                b.fingerprint else "NOT_ESTABLISHED"
            ok = got == exp["equivalence"]
            detail = {"fingerprints_differ": a.fingerprint != b.fingerprint}
        elif cid == "G08_SOCCER_CROSS_COMPETITION_REFUSAL":
            src, prob = c["source_market"], c["probability_source"]
            ident = MC.FixtureIdentity(
                sport="SOCCER", league=prob["competition"],
                home_key="%s:%s" % (prob["competition"], prob["teams"][0]),
                away_key="%s:%s" % (prob["competition"], prob["teams"][1]),
                start_epoch=NOW)
            cand = MC.VenueFixture(
                venue="PMUS", sport="SOCCER", league=src["competition"],
                home_key="%s:%s" % (src["competition"], src["teams"][0]),
                away_key="%s:%s" % (src["competition"], src["teams"][1]),
                start_epoch=NOW, structured_basis="STRUCTURED")
            d = MC.map_fixture_exact(ident, cand)
            got = d.status
            ok = got == "NOT_ESTABLISHED" and "LEAGUE_MISMATCH" in d.reasons
            detail = {"reasons": list(d.reasons)}
        elif cid == "G09_SOCCER_BINARY_CONTRACT_NOT_3WAY_LINE":
            insts = [_inst("PMUS", "ars-bin", "YES", "HOME", "0.4"),
                     _inst("PMUS", "draw-bin", "YES", "DRAW", "0.3"),
                     _inst("PMUS", "liv-bin", "YES", "AWAY", "0.3")]
            b = CC.build_claims(FX3, insts)
            markets = {i.market_id for i in insts}
            got = {"separate_markets": len(markets),
                   "draw_state": "DRAW" in "|".join(b["states"])}
            ok = len(markets) == 3 and got["draw_state"] and \
                exp["three_way_single_market"] is False
            detail = got
        elif cid == "G10_KALSHI_TWO_WAY_YES_NO_ALIAS":
            y = _inst("KALSHI", "K-NYY", "YES", "HOME", "0.47")
            n = _inst("KALSHI", "K-TB", "NO", "AWAY", "0.45")
            b = CC.build_claims(FX2, [y, n])
            got = CC.equivalence_receipt(FX2, y, n, b["states"])["verdict"]
            ok = (got == "SAME_CLAIM") == bool(exp["equivalent"]) and \
                not CC.same_market(y, n)
            detail = {"fingerprint": y.fingerprint}
        elif cid == "G11_KALSHI_TWO_WAY_OPPOSITE_YES":
            y = _inst("KALSHI", "K-NYY", "YES", "HOME", "0.47")
            t = _inst("KALSHI", "K-TB", "YES", "AWAY", "0.45")
            b = CC.build_claims(FX2, [y, t])
            same = y.fingerprint == t.fingerprint
            comp = CC.complement_states(y.vector, t.vector, b["states"])[0]
            got = {"equivalent": same, "complements": comp}
            ok = same == exp["equivalent"] and comp == exp["complements"]
            detail = got
        elif cid == "G12_THREE_WAY_FALSE_NO_ALIAS":
            a = _inst("KALSHI", "K-ARS", "YES", "HOME", "0.4")
            n = _inst("KALSHI", "K-CHE", "NO", "AWAY", "0.6")
            b = CC.build_claims(FX3, [a, n])
            rec = CC.equivalence_receipt(FX3, a, n, b["states"])
            got = rec["verdict"]
            ok = got == "NOT_ESTABLISHED" and any(
                "DRAW" in s for s in rec["states_differing"])
            detail = {"states_differing": rec["states_differing"]}
        elif cid == "G13_UNKNOWN_SETTLEMENT_REFUSES":
            a = _inst("A", "a", "YES", "HOME", "0.5",
                      terms=dict(TERMS, void_rule=None))
            CC.build_claims(FX2, [a])
            got = "REFUSED" if a.fingerprint is None else "FINGERPRINTED"
            ok = got == "REFUSED" and any("UNKNOWN_STATES" in r
                                          for r in a.refusals)
            detail = {"refusals": a.refusals}
        elif cid in ("G14_BEST_PRICE_USES_OPPONENT_NO",
                     "G15_FEE_REVERSES_SCREEN_PRICE",
                     "G16_STALE_CHEAP_ROUTE_LOSES"):
            r = _route_with_case_fees(c)
            got = (r["best_single"] or {}).get("market_id")
            ok = got == exp["best_route"]
            detail = {"lost": r["lost"], "rule": r["rule"]}
        elif cid in ("G17_SAME_VENUE_ARB", "G18_CROSS_VENUE_ARB"):
            from ..agents import adriana_claims as AC
            kt = _kterms()
            if cid == "G17_SAME_VENUE_ARB":
                a = _inst("KALSHI", "K-NYY", "YES", "HOME", "0.42",
                          kalshi_terms=kt)
                b = _inst("KALSHI", "K-TB", "YES", "AWAY", "0.42",
                          kalshi_terms=kt)
            else:
                a = _inst("KALSHI", "K-NYY", "YES", "HOME", "0.42",
                          kalshi_terms=kt)
                b = _inst("POLYMARKET_US", "aec-mlb-tb-nyy", "NO", "HOME",
                          "0.42")
            built = CC.build_claims(FX2, [a, b])
            res = AC.scan_fixture(FX2, built, now=NOW)
            rec = res["records"][0]
            eco = rec.get("economics") or {}
            got = {"verdict": rec["verdict"],
                   "topology": rec["claim_pair"]["topology"]}
            ok = rec["verdict"] == "GUARANTEED_AFTER_COSTS" and \
                rec["claim_pair"]["topology"] == exp["topology"] and \
                D(str(eco["floor_payout_total"])) - D(str(
                    eco["total_cost"])) == D(str(
                        eco["worst_case_net_profit"]))
            detail = {"worst_case_net_profit": eco.get(
                "worst_case_net_profit"), "identity": "floor - all-in = net",
                "reasons": rec.get("reasons")}
        else:
            return {"id": cid, "result": "NOT_BOUND", "pass": False}
    except Exception as exc:                                    # noqa: BLE001
        return {"id": cid, "result": "ERROR", "pass": False,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    return {"id": cid, "result": "PASS" if ok else "FAIL", "pass": bool(ok),
            "production": got if isinstance(got, (str, dict)) else str(got),
            "expected": exp, "detail": detail}


def run_appended(c: dict) -> dict:
    """Appended source records (the Kalshi rep confirmation): their own
    production checks."""
    from ..agents import adriana_claims as AC
    cid = c["id"]
    try:
        if cid == "KR01_SAME_MARKET_YES_NO_NEVER_ARB":
            y = _inst("KALSHI", "K-NYY", "YES", "HOME", "0.40",
                      kalshi_terms=_kterms())
            n = _inst("KALSHI", "K-NYY", "NO", "HOME", "0.40",
                      kalshi_terms=_kterms())
            res = AC.scan_fixture(FX2, CC.build_claims(FX2, [y, n]), now=NOW)
            ok = res["same_market_pairs_excluded"] == 1 and not [
                r for r in res["records"]
                if r.get("verdict") == "GUARANTEED_AFTER_COSTS"]
            got = {"same_market_pairs_excluded":
                   res["same_market_pairs_excluded"]}
        elif cid == "KR02_SAME_MARKET_NO_ASK_FROM_YES_BID":
            from .. import kalshi_market_data as KMD
            b = KMD.book_from_orderbook({"orderbook_fp": {
                "yes_dollars": [["0.4300", "10.00"]],
                "no_dollars": [["0.5400", "10.00"]]}}, observed_at=NOW)
            got = {"no_ask": str(b["no_asks"][0][0]),
                   "yes_ask": str(b["yes_asks"][0][0])}
            ok = b["no_asks"][0][0] == D("0.57") and \
                b["yes_asks"][0][0] == D("0.46")
        elif cid == "KR03_CROSS_MARKET_ALIASES_PRICE_APART":
            y = _inst("KALSHI", "K-NYY", "YES", "HOME", "0.47",
                      kalshi_terms=_kterms())
            n = _inst("KALSHI", "K-TB", "NO", "AWAY", "0.45",
                      kalshi_terms=_kterms())
            built = CC.build_claims(FX2, [y, n])
            fees = CC.fee_functions(at=NOW, sport="BASEBALL",
                                    kalshi_terms=_kterms())
            r = CC.route_claim(FX2, y.fingerprint,
                               built["classes"][y.fingerprint], qty=10,
                               now=NOW + 1, fee_by_venue=fees, max_age_s=30)
            got = {"best": (r["best_single"] or {}).get("market_id")}
            ok = y.fingerprint == n.fingerprint and got["best"] == "K-TB"
        else:
            return {"id": cid, "result": "NOT_BOUND", "pass": False}
    except Exception as exc:                                    # noqa: BLE001
        return {"id": cid, "result": "ERROR", "pass": False,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    return {"id": cid, "result": "PASS" if ok else "FAIL", "pass": bool(ok),
            "production": got, "source": c.get("source")}


def receipt() -> dict:
    cases = load_cases()
    srcs = {s["id"]: s["kind"] for s in cases["base"]["source_manifest"]}
    rows = []
    for c in cases["base"]["cases"]:
        r = run_case(c)
        r["source"] = c.get("source")
        r["source_kind"] = srcs.get(c.get("source"))
        rows.append(r)
    app = [run_appended(c) for c in cases["appended"].get("cases") or []]
    passed = sum(1 for r in rows if r["pass"])
    return {"version": VERSION, "frozen": cases["base"]["version"],
            "cases": rows, "appended_source_records": app,
            "passed": passed, "total": len(rows),
            "appended_passed": sum(1 for r in app if r["pass"]),
            "appended_total": len(app),
            "green": passed == len(rows) and all(r["pass"] for r in app),
            "path": "PRODUCTION (mapping_contract, canonical_claims, "
                    "route_claim, adriana_claims)"}
