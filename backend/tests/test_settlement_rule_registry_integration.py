"""THE SETTLEMENT RULE REGISTRY AS INTEGRATED (BETTOR_Settlement_Rule_Registry_v1
onto claude/p0-closeout).

Named regressions 1-15 (the owner's list), then the integration proofs:
real venue fixture text parses as documented, SCALAR_0_50 is never
STAKE_BACK in the payoff vector, the Kalshi sports catalogue is cursor-
complete (TRUNCATED by name) behind a GET-only transport, the premap capture
and the ext-pinnacle provenance, and the pins that keep the copies honest.

Evidence only throughout: nothing here grants order, cancel, funding,
capital or promotion authority; decision-time bettor_venue_settlement.attest
remains the controlling compatibility decision.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import time

import asyncpg
import pytest

from sportsassets import bettor_settlement_terms as ST
from sportsassets import bettor_venue_settlement as V
from sportsassets import kalshi_catalogue as KC
from sportsassets import kalshi_mapping as KM
from sportsassets import kalshi_public_rules as KPR
from sportsassets import settlement_rule_registry as R
from sportsassets.market_plane import opportunity as OPP
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import rules as RULES
from sportsassets.market_plane import settlement as S
from sportsassets.workers import universal_market_plane as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
BACKEND = pathlib.Path(__file__).resolve().parents[1]
PKG = BACKEND / "sportsassets"
FIX = BACKEND / "tests" / "fixtures"


def run(coro):
    return asyncio.run(coro)


def _fixture_markets():
    out = []
    for p in sorted(FIX.glob("pmus_*listing*.json")):
        for m in json.loads(p.read_text())["markets"]:
            out.append((p.name, m))
    return out


def _desc(slug):
    for _f, m in _fixture_markets():
        if m["slug"] == slug:
            return m["description"]
    raise KeyError(slug)


NFL = "aec-nfl-ind-was-2026-10-04"
NBA = "aec-nba-gs-lac-2026-10-04"
NHL = "aec-nhl-uta-nyr-2026-10-04"
MLB_SPREAD = "asc-mlb-atl-lad-2026-10-04-neg-1pt5"
CFB = "aec-cfb-cin-arz-2026-10-03"


# ═════════════════════════════════════════════════════════════════════
# THE OWNER'S NAMED REGRESSIONS
# ═════════════════════════════════════════════════════════════════════

def test_01_pmus_nfl_overtime_tie_050_and_last_fair_price():
    e = R.polymarket_us_rule_evidence(_desc(NFL), sport_family="football")
    s = e["settlement"]
    assert s["overtime_included"] is True
    assert s["draw_rule"] == R.SCALAR_0_50
    assert s["postponement_window_hours"] == 336.0
    assert s["postponement_payout"] == R.LAST_FAIR_PRICE == s["void_rule"]
    assert R.SC_TIE_SCALAR in e["special_conditions"]
    assert e["verification_sources"] == ["NFL"]
    assert e["rules_sha256"] == R.fingerprint(_desc(NFL))


def test_02_pmus_nba_two_calendar_day_window():
    s = R.polymarket_us_rule_evidence(_desc(NBA))["settlement"]
    assert s["overtime_included"] is True
    assert s["postponement_window_hours"] == 48.0
    assert s["postponement_payout"] == R.LAST_FAIR_PRICE


def test_03_pmus_nhl_overtime_and_shootout():
    s = R.polymarket_us_rule_evidence(_desc(NHL))["settlement"]
    assert s["overtime_included"] is True and s["shootout_included"] is True
    assert s["postponement_window_hours"] == 48.0


def test_04_pmus_mlb_extra_innings():
    e = R.polymarket_us_rule_evidence(_desc(MLB_SPREAD))
    s = e["settlement"]
    assert s["extra_innings_included"] is True and s["overtime_included"] is True
    assert s["postponement_window_hours"] == 336.0
    assert R.SC_SHORTENED_OFFICIAL in e["special_conditions"]


def test_05_explicit_overtime_exclusion_on_a_period_market_wins():
    t = ("Overtime is included if played. Important: Overtime does not count "
         "for 2H and 4Q markets.")
    e = R.polymarket_us_rule_evidence(t, period="SECOND_HALF")
    assert e["settlement"]["overtime_included"] is False
    assert "OVERTIME_EXCLUDED_EXPLICIT" in e["matched"]


def test_06_kalshi_48h_postponement_and_fair_price_cancellation():
    m = {"ticker": "KXT", "rules_primary": (
        "If the game is postponed but begins within 48 hours, the market "
        "remains open. If the game is cancelled or not started within 48 "
        "hours, the market resolves to a fair market price.")}
    s = R.kalshi_rule_evidence(m)["settlement"]
    assert s["postponement_window_hours"] == 48.0
    assert s["void_rule"] == R.LAST_FAIR_PRICE
    assert s["postponement_payout"] == R.LAST_FAIR_PRICE


def test_07_kalshi_tennis_retirement_is_a_named_condition():
    e = R.kalshi_rule_evidence({"ticker": "KXATP", "rules_secondary": (
        "If a retirement occurs, markets that cannot be unconditionally "
        "settled resolve to a Fair Market Price.")})
    assert R.SC_RETIREMENT in e["special_conditions"]
    assert "void_rule" not in e["settlement"]          # never the game void
    assert e["settlement"].get("void_rule") != R.STAKE_BACK


def test_08_kalshi_dnp_non_starter_is_a_named_condition():
    e = R.kalshi_rule_evidence({"ticker": "KXP", "rules_primary": (
        "If the player is active but never takes a snap, the market resolves "
        "to a fair price.")})
    assert R.SC_DNP in e["special_conditions"]
    assert "void_rule" not in e["settlement"]
    # walkover / scratch / multi-winner stay named too, never STAKE_BACK
    w = R.kalshi_rule_evidence({"ticker": "KXW", "rules_primary": (
        "If the match is decided by walkover, the market resolves to a fair "
        "market price. If a horse is scratched, resolves to a fair price. "
        "In a dead heat the market is reviewed.")})
    assert {R.SC_WALKOVER, R.SC_SCRATCH, R.SC_MULTI_WINNER} <= set(
        w["special_conditions"])
    assert "void_rule" not in w["settlement"]
    assert R.STAKE_BACK not in json.dumps(w)


def test_09_kalshi_plain_winner_text_does_not_infer_overtime():
    e = R.kalshi_rule_evidence({"ticker": "KXN", "rules_primary": (
        "If Team A wins the game, the market resolves to Yes. Outcome "
        "verified from League X.")})
    assert e["settlement"] == {} and e["status"] == R.PARTIAL
    assert "overtime_included" not in e["settlement"]


def test_10_a_rule_change_changes_the_fingerprint_and_invalidates_evidence():
    old = _desc(NFL)
    new = old.replace("If the game ends in a tie, the market will settle "
                      "to $0.50. ", "")
    assert R.fingerprint(old) != R.fingerprint(new)
    # a decision attested on the OLD text no longer proves the NEW one
    rules_new = {"venue": "POLYMARKET_US", "rules_published": True,
                 "rules_sha256": R.fingerprint(new), "parse_status":
                 "ESTABLISHED", "evidence": {}}
    st = S.state_for(
        {"contract_id": NFL, "sport": "football", "family": "WINNER",
         "period": "FULL_EVENT"},
        valuation={"settlement_verdict": "COMPATIBLE",
                   "decision_rules_fingerprint": R.fingerprint(old)},
        rules=rules_new, rules_looked_up=True)
    assert st["state"] == S.NOT_PROVEN
    assert st["why"] == S.R_RULES_CHANGED_SINCE_DECISION
    same = S.state_for(
        {"contract_id": NFL}, valuation={
            "settlement_verdict": "COMPATIBLE",
            "decision_rules_fingerprint": R.fingerprint(new)},
        rules=rules_new, rules_looked_up=True)
    assert same["state"] == S.COMPATIBLE and same["basis"] == \
        S.BASIS_DECISION_ATTEST


def test_11_explicit_structure_vs_parsed_prose_conflict_refuses():
    sett = {"overtime_included": False, "void_rule": "LAST_FAIR_PRICE",
            "postponement_window_hours": 48, "postponement_payout":
            "LAST_FAIR_PRICE", "draw_rule": "IMPOSSIBLE"}
    b = {"us_market_slug": NBA, "market_type": "MONEYLINE",
         "rules_text": "Overtime is included if played.",
         "settlement": dict(sett), "outcome": {"team": "GSW"}}
    k = {"ticker": "KXNBA-GSW", "market_type": "MONEYLINE",
         "settlement": dict(sett), "outcome": {"team": "GSW"}}
    sv = KM.settlement_compatibility(b, k)
    assert sv.verdict == KM.NOT_ESTABLISHED
    assert sv.missing_fields == ["settlement.rule_evidence_conflict"]
    v = KM.establish(b, k)
    assert not v.established
    assert "settlement.rule_evidence_conflict" in v.missing
    # the explicit field is never overwritten
    got = R.enrich_contract(b, venue=R.POLYMARKET_US,
                            text_fields=("rules_text",))
    assert got["settlement"]["overtime_included"] is False
    assert got["settlement_rule_evidence"]["conflicts"] == {
        "overtime_included": {"explicit": False, "parsed": True}}


def test_12_absent_rules_remain_not_established():
    assert R.polymarket_us_rule_evidence(None)["status"] == R.ABSENT
    assert R.kalshi_rule_evidence({"ticker": "X"})["status"] == R.ABSENT
    c = {"contract_id": "m", "sport": "football", "family": "WINNER",
         "period": "FULL_EVENT", "venue": "POLYMARKET_US"}
    st = S.state_for(c, rules=None, rules_looked_up=True)
    assert st["state"] == S.NOT_PROVEN
    assert st["why"] == S.R_VENUE_RULES_NOT_CAPTURED
    # Kalshi side with no rules: kalshi_mapping stays NOT_ESTABLISHED
    sv = KM.settlement_compatibility(
        {"market_type": "MONEYLINE", "settlement": {}},
        {"ticker": "KX", "market_type": "MONEYLINE"})
    assert sv.verdict == KM.NOT_ESTABLISHED and sv.missing_fields
    # silence is never agreement: a read listing with NO rules field is
    # EXTERNAL only with that evidence, never COMPATIBLE
    st = S.state_for(c, rules={"venue": "POLYMARKET_US",
                               "rules_published": False,
                               "parse_status": "ABSENT"},
                     rules_looked_up=True)
    assert st["state"] == S.EXTERNAL and not st["proven"]


NEW_MODULES = ("settlement_rule_registry.py", "kalshi_public_rules.py",
               "kalshi_catalogue.py", "market_plane/rules.py",
               "market_plane/settlement.py", "market_plane/populate.py",
               "market_plane/opportunity.py")
FORBIDDEN_IMPORTS = ("kalshi_orders", "kalshi_venue",
                     "bettor_funded_execution", "bettor_entry_execution",
                     "execmirror", "live_executor", "pmx", "execution_gate",
                     "bettor_capital_eligibility")
FORBIDDEN_CALLS = {"submit", "cancel", "place_order", "submit_order",
                   "cancel_order", "create_order", "submit_fok",
                   "close_position", "post", "put", "delete", "patch",
                   "cancel_all", "post_order"}


def test_13_no_order_cancel_or_funding_call_is_reachable_from_new_modules():
    for rel in NEW_MODULES:
        tree = ast.parse((PKG / rel).read_text())
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.ImportFrom):
                mods = [n.module or ""] + [a.name for a in n.names]
            elif isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            for m in mods:
                leaf = (m or "").split(".")[-1]
                assert leaf not in FORBIDDEN_IMPORTS, (rel, m)
                assert "paper" not in leaf and "funded" not in leaf, (rel, m)
            if isinstance(n, ast.Call):
                f = n.func
                nm = f.attr if isinstance(f, ast.Attribute) else getattr(
                    f, "id", None)
                assert nm not in FORBIDDEN_CALLS, (rel, ast.unparse(n))
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                assert n.value.upper() not in ("POST", "PUT", "DELETE",
                                               "PATCH"), (rel, n.value)
        top = [x for x in tree.body if isinstance(x, (ast.Import,
                                                      ast.ImportFrom))]
        for x in top:
            names = ([a.name for a in x.names] if isinstance(x, ast.Import)
                     else [x.module or ""])
            assert not any(nm.split(".")[0] in ("requests", "httpx",
                                                "aiohttp") for nm in names), \
                (rel, names)
    # the worker step reaches the catalogue only, never an order module
    src = (PKG / "workers" / "universal_market_plane.py").read_text()
    for bad in ("kalshi_orders", "kalshi_venue", "KalshiClient"):
        assert bad not in src


@pg
def test_14_radar_and_snapshot_classify_settlement_gaps():
    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await _isolate(c)
            await _seed_premap(c, "srr-nfl", event=NFL,
                               sports_type="football_team_full_game_winner")
            await _seed_premap(c, "srr-nba-sp", event="aec-nba-gs-lac-2026",
                               sports_type="basketball_team_full_game_spread")
            await _seed_premap(c, "srr-silent", event="aec-nhl-a-b-2026",
                               sports_type="hockey_team_full_game_winner")
            await _seed_premap(c, "srr-none", event="aec-nhl-c-d-2026",
                               sports_type="hockey_team_full_game_winner")
            now = time.time()
            await POP.populate(c, since=0.0, now=now, full=True)
            await RULES.upsert(c, [
                RULES.pmus_row({"slug": "srr-nfl", "description": _desc(NFL),
                                "sportsMarketType":
                                    "football_team_full_game_winner"}),
                RULES.pmus_row({"slug": "srr-nba-sp",
                                "description": _desc(
                                    "asc-nba-gs-lac-2026-10-04-neg-2pt5")}),
                RULES.pmus_row({"slug": "srr-silent", "description":
                                "This market will settle to the winner. "
                                "Outcome sourced from NHL."}),
                RULES.pmus_row({"slug": "srr-none"})])
            res = KC.walk(_FakeKalshi(), sleep=lambda s: None,
                          clock=_Clock())
            await POP.populate_kalshi(c, res, now=now)
            cov = await POP.coverage_pass(c, fresh_symbols=set(), now=now)
            sett = cov["settlement"]
            st = {r["contract_id"]: dict(r) for r in await c.fetch(
                "SELECT contract_id, settlement_state, settlement_why, "
                "       settlement_basis, coverage_state, coverage_why, "
                "       settlement_evidence FROM market_plane_registry "
                " WHERE active")}
            nfl = st["srr-nfl"]
            assert nfl["settlement_state"] == S.NOT_PROVEN
            assert nfl["settlement_why"].startswith(
                "INCOMPATIBLE_NOT_PRICEABLE:POSTPONED_OR_ABANDONED")
            assert nfl["settlement_basis"] == S.BASIS_RULES_TERMS
            assert st["srr-nba-sp"]["settlement_why"] == \
                "BOOKMAKER_TERMS_NOT_HELD:basketball/MARGIN/FULL_EVENT"
            assert st["srr-silent"]["settlement_why"].startswith(
                "VENUE_RULES_SILENT_ON:")
            assert st["srr-none"]["settlement_state"] == S.EXTERNAL
            assert st["srr-none"]["coverage_state"] == \
                "EXTERNAL_DATA_UNAVAILABLE"
            k = st["kalshi:KXNFLGAME-26OCT04-IND"]
            assert k["settlement_why"] == S.R_KALSHI_NOT_MAPPED
            assert k["coverage_state"] == "CODE_CONTROLLED_GAP"
            assert sum(sett["by_state"].values()) == cov["active"]
            assert sett["by_state"][S.EXTERNAL] >= 1
            assert any(key.startswith("POLYMARKET_US|football|nfl|WINNER")
                       for key in
                       sett["breakdown_venue_sport_league_family"])
            snap = await W.snapshot(
                c, None, {"coverage": cov, "plan": {},
                          "catalogue": {"complete": False},
                          "kalshi": KC.summary(res)},
                now=now, arming={"why": "TEST"}, fresh=set(), caps=(4, 1000))
            assert snap["settlement"]["by_state"] == sett["by_state"]
            assert "delta" in snap["settlement"]
            rv = snap["rules"]["by_venue"]
            assert rv["POLYMARKET_US"]["readable"] == 3
            assert rv["POLYMARKET_US"]["no_rules_field"] == 1
            assert rv["KALSHI"]["with_rules_primary"] == 2
            assert rv["KALSHI"]["with_rules_secondary"] == 1
            assert snap["kalshi"]["registry"]["active"] == 2
            assert any(f.startswith("EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE")
                       for f in snap["radar"]["extra_findings"])
            # PMUS universe only in the registry block Radar audits
            assert snap["universe"]["registry"]["active"] == \
                cov["active"] - 2
        finally:
            await tr.rollback()
            await c.close()
    run(go())


def test_15_canonical_opportunity_carries_settlement_evidence():
    ev = {"state": S.NOT_PROVEN, "why": "VENUE_RULES_SILENT_ON:X",
          "basis": S.BASIS_RULES_TERMS,
          "provenance": {"rules_sha256": "ab", "rules_field": "description",
                         "parser_version": R.PARSER_VERSION,
                         "matched": ["OVERTIME_INCLUDED"],
                         "verification_sources": ["NHL"], "conflicts": {}}}
    base = dict(contract={"contract_id": "m"}, entities={"e": 1},
                book={"bids": []}, probability=None,
                settlement={"compatibility": "UNKNOWN"}, execution=None,
                portfolio=None)
    a = OPP.build(**base, settlement_evidence=ev)
    assert a["settlement_evidence"]["provenance"]["rules_sha256"] == "ab"
    assert "SETTLEMENT_STATE:MAPPED_BUT_SETTLEMENT_NOT_PROVEN" in a["gaps"]
    assert a["ready_for_agent_evaluation"] is False
    # it never fills the decision's own settlement slot
    b = OPP.build(**dict(base, settlement=None), settlement_evidence=dict(
        ev, state=S.COMPATIBLE))
    assert "MISSING_SETTLEMENT" in b["gaps"]
    # without evidence the object is exactly as before
    c = OPP.build(**base)
    assert "settlement_evidence" not in c and c["gaps"] == []


@pg
def test_15b_the_opportunity_route_carries_the_provenance(monkeypatch):
    from sportsassets.api import command_market_plane as API

    async def go():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        cid = "srr-opp-%d" % int(time.time() * 1000)
        row = RULES.pmus_row({"slug": cid, "description": _desc(NHL)})
        try:
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO market_plane_registry (contract_id, venue, "
                    " sport, competition, event_id, family, period, ontology, "
                    " updated_at, settlement_state, settlement_why, "
                    " settlement_basis, settlement_evidence) VALUES ($1, "
                    " 'POLYMARKET_US', 'hockey', 'nhl', 'aec-nhl-x', "
                    " 'WINNER', 'FULL_EVENT', '{}'::jsonb, now(), $2, $3, $4,"
                    " $5::jsonb)", cid, S.NOT_PROVEN, "VENUE_RULES_SILENT_ON:X",
                    S.BASIS_RULES_TERMS, json.dumps({"rules_sha256":
                                                     row["rules_sha256"]}))
                await c.execute(
                    "INSERT INTO market_plane_rules (contract_id, venue, "
                    " rules_published, rules_field, rules_sha256, rules_text,"
                    " parse_status, evidence, parser_version, source, "
                    " observed_at) VALUES ($1,'POLYMARKET_US',true,$2,$3,$4,"
                    " $5,$6::jsonb,$7,$8,now())", cid, row["rules_field"],
                    row["rules_sha256"], row["rules_text"],
                    row["parse_status"], json.dumps(row["evidence"]),
                    row["parser_version"], row["source"])

            async def _p():
                return pool
            monkeypatch.setattr(API, "_pool", _p)
            got = await API.market_plane_opportunity(contract_id=cid)
            sev = got["settlement_evidence"]
            assert sev["state"] == S.NOT_PROVEN
            assert sev["basis"] == S.BASIS_RULES_TERMS
            pv = sev["provenance"]
            assert pv["rules_sha256"] == R.fingerprint(_desc(NHL))
            assert pv["rules_field"] == "description"
            assert pv["parser_version"] == R.PARSER_VERSION
            assert "OVERTIME_AND_SHOOTOUT_INCLUDED" in pv["matched"]
            assert pv["verification_sources"] == ["NHL"]
            assert pv["conflicts"] == {}
            assert got["ready_for_agent_evaluation"] is False
            listing = await API.market_plane(limit=5, state=None, sport=None,
                                             venue="POLYMARKET_US",
                                             settlement_state=S.NOT_PROVEN)
            assert S.NOT_PROVEN in listing["settlement_by_state"]
            assert listing["rules"]["readable"] is True
            assert "settlement_breakdown_venue_sport_league_family" in listing
        finally:
            async with pool.acquire() as c:
                await c.execute("DELETE FROM market_plane_rules WHERE "
                                "contract_id = $1", cid)
                await c.execute("DELETE FROM market_plane_registry WHERE "
                                "contract_id = $1", cid)
            await pool.close()
    run(go())


# ═════════════════════════════════════════════════════════════════════
# REAL VENUE TEXT (tests/fixtures/pmus_*listing*.json), EVERY DESCRIPTION
# ═════════════════════════════════════════════════════════════════════

def test_every_real_fixture_description_parses_as_documented():
    seen = 0
    for _f, m in _fixture_markets():
        seen += 1
        st, slug, d = m["sportsMarketType"], m["slug"], m["description"]
        e = R.polymarket_us_rule_evidence(d, sports_market_type=st)
        s, sc = e["settlement"], set(e["special_conditions"])
        assert e["status"] == R.ESTABLISHED and e["rules_sha256"], slug
        assert e["verification_sources"], slug
        assert R.STAKE_BACK not in json.dumps(e), slug       # never guessed
        assert s["postponement_window_hours"] == (
            48.0 if "two calendar days" in d else 336.0), slug
        if "-nfl-" in slug and st.endswith("_winner"):
            assert s["draw_rule"] == R.SCALAR_0_50 and R.SC_TIE_SCALAR in sc
        else:
            assert "draw_rule" not in s, slug
        if "-cfb-" in slug and st.endswith("_winner"):
            assert R.SC_TIE_MANUAL in sc                 # a named condition
        if st.startswith("hockey") and st.endswith("_winner"):
            assert s["shootout_included"] is True
        if st.startswith("hockey") and not st.endswith("_winner"):
            assert R.SC_SHOOTOUT_ONE_GOAL in sc
            assert "shootout_included" not in s
        if st.startswith("baseball"):
            assert s["extra_innings_included"] is True
            assert R.SC_SHORTENED_OFFICIAL in sc
        if st.startswith("soccer"):
            assert s["overtime_included"] is False
        if st.startswith("tennis"):
            assert {R.SC_WALKOVER, R.SC_RETIREMENT} <= sc
            assert "overtime_included" not in s
            assert s["void_rule"] == R.LAST_FAIR_PRICE
        if "_points_" in st:
            assert R.SC_MANUAL_LFMP in sc
            assert "postponement_payout" not in s and "void_rule" not in s
        elif not st.startswith("tennis"):
            assert s["postponement_payout"] == R.LAST_FAIR_PRICE
            assert s["overtime_included"] in (True, False)
    # 80 + the 24 listings of the 2026-10-08 capture of the leagues the
    # venue listed later (pmus_basketball_hockey_winner_listings_2026_10_08)
    assert seen == 104


# ═════════════════════════════════════════════════════════════════════
# PAYOFF VECTOR, KALSHI CATALOGUE, CAPTURE, PROVENANCE, PINS
# ═════════════════════════════════════════════════════════════════════

def test_scalar_050_and_stake_back_are_unequal_in_the_payoff_vector():
    base = {"overtime_included": False, "void_rule": "LAST_FAIR_PRICE",
            "postponement_window_hours": 336, "postponement_payout":
            "LAST_FAIR_PRICE"}
    v = KM.payoff_vector("MONEYLINE", {"team": "A"},
                         dict(base, draw_rule=R.SCALAR_0_50), [336])
    assert v[KM.S_DRAW] == "0.5" and v[KM.S_OT_WIN] == "0.5"
    sv = KM.settlement_compatibility(
        {"market_type": "MONEYLINE", "outcome": {"team": "A"},
         "settlement": dict(base, draw_rule=R.SCALAR_0_50)},
        {"ticker": "KX", "market_type": "MONEYLINE", "outcome": {"team": "A"},
         "settlement": dict(base, draw_rule=R.STAKE_BACK)})
    assert sv.verdict == KM.NOT_ESTABLISHED
    assert KM.S_DRAW in sv.unequal_states
    for other in ("STAKE_BACK", "LAST_FAIR_PRICE", "RESOLVES_YES",
                  "RESOLVES_NO"):
        assert KM.RULE_PAYOFF["SCALAR_0_50"] != KM.RULE_PAYOFF[other]
    same = KM.settlement_compatibility(
        {"market_type": "MONEYLINE", "outcome": {"team": "A"},
         "settlement": dict(base, draw_rule=R.SCALAR_0_50)},
        {"ticker": "KX", "market_type": "MONEYLINE", "outcome": {"team": "A"},
         "settlement": dict(base, draw_rule=R.SCALAR_0_50)})
    assert same.verdict == KM.ESTABLISHED


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.001
        return self.t


class _FakeKalshi:
    """GET-only fake of the documented endpoints: two sports series, the
    first paged over two cursors."""

    def __init__(self, *, fail_series=None, repeat=False):
        self.calls = []
        self.fail_series, self.repeat = fail_series, repeat

    def get(self, url, *, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        p = params or {}
        if url.endswith("/series"):
            assert p.get("category") == "Sports"
            return _Resp(200, {"series": [
                {"ticker": "KXNFLGAME", "title": "NFL", "tags": ["Football"],
                 "category": "Sports"},
                {"ticker": "KXATP", "title": "ATP", "tags": ["Tennis"],
                 "category": "Sports"}]})
        assert url.endswith("/markets") and p["status"] == "open"
        if p["series_ticker"] == self.fail_series:
            return _Resp(503, {})
        if p["series_ticker"] == "KXNFLGAME":
            if not p.get("cursor"):
                return _Resp(200, {"markets": [{
                    "ticker": "KXNFLGAME-26OCT04-IND",
                    "event_ticker": "KXNFLGAME-26OCT04",
                    "rules_primary": "If Indianapolis wins, resolves Yes.",
                    "rules_secondary": "In the event of a tie, all markets "
                                       "will resolve at 50c."}],
                    "cursor": "c1"})
            if self.repeat or p["cursor"] == "c1":
                return _Resp(200, {"markets": [], "cursor": "c1"
                                   if self.repeat else ""})
        return _Resp(200, {"markets": [{
            "ticker": "KXATP-X", "event_ticker": "KXATP-E",
            "rules_primary": "If a retirement occurs, resolves to a fair "
                             "market price."}], "cursor": ""})


def test_kalshi_catalogue_is_complete_only_when_every_cursor_is_exhausted():
    tx = _FakeKalshi()
    res = KC.walk(tx, sleep=lambda s: None, clock=_Clock())
    assert res["complete"] is True and res["stopped"] is None
    assert res["series_total"] == res["series_complete"] == 2
    assert res["requests"] == 4 == len(tx.calls)
    assert {m["ticker"] for m in res["markets"]} == {
        "KXNFLGAME-26OCT04-IND", "KXATP-X"}
    assert all(c[1].get("limit", 1000) == 1000 for c in tx.calls[1:])
    sm = KC.summary(res)
    assert sm["with_rules_primary"] == 2 and sm["with_rules_secondary"] == 1


def test_kalshi_catalogue_truncation_is_named_never_complete():
    res = KC.walk(_FakeKalshi(), max_requests=1, sleep=lambda s: None,
                  clock=_Clock())
    assert res["complete"] is False
    assert res["stopped"] == KC.R_TRUNCATED_BUDGET
    assert res["series_truncated"]["KXATP"] == KC.R_TRUNCATED_BUDGET
    assert res["series_unread"] == ["KXNFLGAME"]
    res = KC.walk(_FakeKalshi(repeat=True), sleep=lambda s: None,
                  clock=_Clock())
    assert not res["complete"]
    assert res["series_truncated"]["KXNFLGAME"] == KC.R_TRUNCATED_CURSOR_REPEAT
    res = KC.walk(_FakeKalshi(fail_series="KXATP"), sleep=lambda s: None,
                  clock=_Clock())
    assert not res["complete"]
    assert res["series_truncated"]["KXATP"].startswith(KC.R_TRUNCATED_HTTP)
    res = KC.walk(_FakeKalshi(), max_pages_per_series=1,
                  sleep=lambda s: None, clock=_Clock())
    assert res["series_truncated"]["KXNFLGAME"] == KC.R_TRUNCATED_PAGE_CAP


def test_kalshi_catalogue_paces_its_requests():
    slept = []
    clock = _Clock()
    KC.walk(_FakeKalshi(), pacing_s=0.0, sleep=slept.append, clock=clock)
    assert slept and all(s >= KC.MIN_GAP_S - 0.01 for s in slept)
    assert KC.MIN_GAP_S >= 0.1


def test_the_kalshi_transport_refuses_every_non_get_before_the_network():
    tx = KC.GetOnlyTransport(session=object())     # no .get: never reached
    for m in ("POST", "DELETE", "PUT", "PATCH", "post"):
        with pytest.raises(KC.NonGetRefused):
            tx.request(m, KC.BASE + "/portfolio/orders")
    assert not hasattr(KPR.RequestsGet, "post")
    d = KC.describe()
    assert d["method"] == "GET" and d["credentials"] == "NONE"
    assert d["orders"] is False and d["mutations"] is False


def test_the_kalshi_base_is_the_one_the_repo_already_documents():
    from sportsassets import kalshi_venue
    assert KPR.BASE == kalshi_venue.BASE_URLS["prod"] == KC.BASE
    assert "external-api" not in KPR.BASE


def test_the_rules_fields_are_the_live_readers():
    from sportsassets import bettor_live_read
    assert RULES.RULES_TEXT_FIELDS == bettor_live_read.RULES_TEXT_FIELDS


def test_priced_resolution_is_the_capital_gates_own_predicate():
    from sportsassets import bettor_capital_eligibility as CE
    from sportsassets import bettor_settlement_difference_policy as SDP
    good = {"policy_id": SDP.POLICY_ID, "version": SDP.VERSION, "p": 0.4}
    for pol in (good, dict(good, version="OLD"), dict(good, p=None),
                dict(good, policy_id="X")):
        want = CE.settlement_resolved({"compatibility": SDP.SETTLEMENT_PRICED,
                                       **pol})
        assert S.priced_resolved({"eligibility": {"eligible": True},
                                  "policy": pol}) is want
    assert S.priced_resolved({"eligibility": {"eligible": False},
                              "policy": good}) is False
    st = S.state_for({"contract_id": "m"},
                     valuation={"settlement_verdict": "INCOMPATIBLE"},
                     priced={"eligibility": {"eligible": True},
                             "policy": good})
    assert st["state"] == S.DIFFERENT_BUT_PRICED
    assert st["basis"] == S.BASIS_DECISION_PRICED


def test_a_never_valued_incompatible_contract_is_never_priced_here():
    c = {"contract_id": NFL, "sport": "football", "family": "WINNER",
         "period": "FULL_EVENT", "competition": "nfl"}
    row = RULES.pmus_row({"slug": NFL, "description": _desc(NFL)})
    st = S.state_for(c, rules=row, rules_looked_up=True)
    assert st["state"] == S.NOT_PROVEN
    assert st["evidence"]["difference_policy"]["eligible"] is False
    # the comparison is the exact one attest makes on the same words
    direct = ST.compare_prose(sport_family="football", market="h2h",
                              venue_prose=_desc(NFL),
                              extra_book_terms=V._legacy_book_terms(
                                  "football"))
    assert st["evidence"]["terms"]["verdict"] == direct["verdict"]
    assert st["evidence"]["terms"]["mismatched_conditions"] == \
        direct["mismatched_conditions"]


def test_a_tie_clause_blocks_a_terms_compatible_verdict(monkeypatch):
    monkeypatch.setattr(S, "terms_comparison", lambda **k: {
        "verdict": "COMPATIBLE", "book_terms_held": True,
        "per_condition": {}, "venue_self_contradictory": []})
    c = {"contract_id": "x", "sport": "football", "family": "WINNER",
         "period": "FULL_EVENT"}
    row = RULES.pmus_row({"slug": "x", "description": _desc(CFB)})
    st = S.state_for(c, rules=row, rules_looked_up=True)
    assert st["state"] == S.NOT_PROVEN
    assert st["why"].startswith(S.R_TIE_RULE_NOT_COMPARED)
    plain = RULES.pmus_row({"slug": "y", "description":
                            "Overtime is included if played."})
    st = S.state_for(c, rules=plain, rules_looked_up=True)
    assert st["state"] == S.COMPATIBLE and st["basis"] == S.BASIS_RULES_TERMS


def test_a_conflict_fails_closed_over_a_compatible_decision():
    st = S.state_for({"contract_id": "m"},
                     valuation={"settlement_verdict": "COMPATIBLE"},
                     rules={"parse_status": "CONFLICT", "evidence": {
                         "conflicts": {"overtime_included": {}}}})
    assert st["state"] == S.CONFLICT and not st["proven"]


def test_coverage_terminal_reads_the_settlement_state_only():
    c = {"contract_id": "m1", "sport": "football", "competition": "nfl",
         "event_id": "e", "family": "WINNER", "period": "FULL_EVENT",
         "ontology": {"gaps": [], "meaning": {}}}
    # a probability with no settlement refusal is NOT proof any more
    t = POP.classify(c, valuation={"has_probability": True,
                                   "settlement_verdict": None,
                                   "refusals": []}, fresh_book=True)
    assert t["state"] == "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"
    t = POP.classify(c, valuation={"has_probability": True,
                                   "settlement_verdict": "COMPATIBLE",
                                   "refusals": []}, fresh_book=True)
    assert t["state"] == "PRICEABLE"
    assert t["evidence"]["settlement_basis"] == S.BASIS_DECISION_ATTEST


def test_the_ext_pinnacle_evidence_carries_structure_and_attest_is_unchanged(
        monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as L
    text = _desc(NFL)
    monkeypatch.setattr(L, "_read_venue_rules_blocking", lambda slug: {
        "rules_text": text, "source": "pmus:/markets?slug=<slug>:rules_text",
        "rules_field": "description", "ok": True})

    class _Conn:
        async def fetchrow(self, *a, **k):
            return None

        async def fetchval(self, *a, **k):
            return None
    ve = run(L.venue_settlement_evidence(_Conn(), NFL))
    assert ve["rules_sha256"] == R.fingerprint(text)
    assert ve["structured_rules"]["settlement"]["draw_rule"] == R.SCALAR_0_50
    assert ve["structured_rules"]["rules_field"] == "description"
    bare = {k: v for k, v in ve.items()
            if k not in ("structured_rules", "rules_sha256")}
    a = V.attest(sport_family="football", venue_evidence=ve)
    b = V.attest(sport_family="football", venue_evidence=bare)
    a.pop("venue_evidence"), b.pop("venue_evidence")
    assert json.dumps(a, sort_keys=True, default=str) == \
        json.dumps(b, sort_keys=True, default=str)
    # and the persisted comparison records the provenance beside attest's
    src = (PKG / "workers" / "ext_pinnacle_loop.py").read_text()
    assert src.count("venue_rules_fingerprint") == 2
    assert src.count("venue_rules_structured") == 2


@pytest.fixture
def _rules_probe_reads_this_database(monkeypatch):
    """capture_pmus_markets first asks market_plane.rules.table_present
    whether migration 312 is applied, and that answer is a PROCESS-wide
    cache (_TABLE_STATE: "absent" is believed for 600 s). An earlier test in
    the same process whose fake pool answered "absent" (the premap sweep
    tests' fetchval -> None) made this capture return {"skipped":
    "MIGRATION_312_NOT_APPLIED"} against a database that has the table. The
    probe here starts unasked, so it is answered by THIS test's database;
    the earlier answer is restored on teardown."""
    monkeypatch.setattr(RULES, "_TABLE_STATE", {"present": None, "at": 0.0})


@pg
def test_premap_capture_writes_once_and_a_change_appends_rules_changed(
        _rules_probe_reads_this_database):
    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        RULES._SEEN.clear()
        try:
            ms = [m for _f, m in _fixture_markets()][:12]
            got = await RULES.capture_pmus_markets(c, ms)
            assert got["new"] == 12 and got["written"] == 12
            again = await RULES.capture_pmus_markets(c, ms)
            assert again["written"] == 0 and again["unchanged_cached"] == 12
            RULES._SEEN.clear()
            again = await RULES.capture_pmus_markets(c, ms)
            assert again["written"] == 0               # the db row said so
            m0 = dict(ms[0], description=ms[0]["description"] + " Extra.")
            ch = await RULES.capture_pmus_markets(c, [m0])
            assert ch["changed"] == 1 and ch["written"] == 1
            ev = await c.fetchrow(
                "SELECT payload FROM market_plane_events WHERE kind = "
                "'RULES_CHANGED' AND contract_id = $1", m0["slug"])
            p = json.loads(ev["payload"])
            assert p["previous_rules_sha256"] == R.fingerprint(
                ms[0]["description"])
            assert p["rules_sha256"] == R.fingerprint(m0["description"])
            row = await c.fetchrow("SELECT * FROM market_plane_rules WHERE "
                                   "contract_id = $1", m0["slug"])
            assert row["rules_sha256"] == p["rules_sha256"]
            assert row["label"] == "RESEARCH"
            assert row["authority"] == "MARKET_DATA_ONLY_NO_ORDER_AUTHORITY"
        finally:
            RULES._SEEN.clear()
            await tr.rollback()
            await c.close()
    run(go())


@pg
def test_10b_a_rule_change_recomputes_the_settlement_state_in_the_plane():
    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        RULES._SEEN.clear()
        try:
            await _isolate(c)
            await _seed_premap(c, "srr-chg", event=NFL,
                               sports_type="football_team_full_game_winner")
            now = time.time()
            await POP.populate(c, since=0.0, now=now, full=True)
            old = _desc(NFL)
            await RULES.upsert(c, [RULES.pmus_row({"slug": "srr-chg",
                                                   "description": old})])
            await POP.coverage_pass(c, now=now)
            r1 = await c.fetchrow(
                "SELECT settlement_why, settlement_evidence FROM "
                "market_plane_registry WHERE contract_id = 'srr-chg'")
            new = ("This market will settle to the winner of the game. "
                   "Overtime is included if played. Outcome sourced from NFL.")
            w = await RULES.upsert(c, [RULES.pmus_row({"slug": "srr-chg",
                                                       "description": new})])
            assert w["changed"] == 1
            cov = await POP.coverage_pass(c, now=now + 1)
            r2 = await c.fetchrow(
                "SELECT settlement_why, settlement_evidence FROM "
                "market_plane_registry WHERE contract_id = 'srr-chg'")
            e1, e2 = (json.loads(r1["settlement_evidence"]),
                      json.loads(r2["settlement_evidence"]))
            assert e1["rules_sha256"] == R.fingerprint(old)
            assert e2["rules_sha256"] == R.fingerprint(new)
            assert r1["settlement_why"].startswith("INCOMPATIBLE_NOT_PRICEABLE")
            assert r2["settlement_why"].startswith("VENUE_RULES_SILENT_ON")
            assert cov["settlement"]["changed"] >= 1
            assert await c.fetchval(
                "SELECT count(*) FROM market_plane_events WHERE kind = "
                "'RULES_CHANGED' AND contract_id = 'srr-chg'") == 1
        finally:
            RULES._SEEN.clear()
            await tr.rollback()
            await c.close()
    run(go())


def test_the_kalshi_step_is_supervised_and_killable(monkeypatch):
    calls = []

    def fake_walk():
        calls.append(1)
        return {"markets": [], "complete": True}

    async def go():
        t, last, rep = await W.kalshi_step(
            None, None, now=10_000.0, last=0.0,
            env={"KALSHI_CATALOGUE": "off"}, walk=fake_walk)
        assert t is None and rep is None and last == 0.0
        t, last, rep = await W.kalshi_step(
            None, None, now=10_000.0, last=0.0, env={}, walk=fake_walk)
        assert t is not None and last == 10_000.0
        await t
        assert calls == [1]
    run(go())
    assert W.KALSHI_EVERY_S == 1800.0 and W.KALSHI_ENV_FLAG == \
        "KALSHI_CATALOGUE"


# ── helpers ──────────────────────────────────────────────────────────

async def _isolate(c):
    await c.execute("UPDATE market_plane_registry SET active = false")
    await c.execute("DELETE FROM us_premap")


async def _seed_premap(c, slug, *, event, sports_type, state="PREGAME"):
    for intent, side in (("ORDER_INTENT_BUY_LONG", "a"),
                         ("ORDER_INTENT_BUY_SHORT", "b")):
        await c.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, kind, "
            " side_norm, intent, sports_type, listing_state, "
            " listing_state_source, updated_at, game_start, team_id, "
            " team_league) "
            "VALUES ($1,$2,$3,'side',$4,$5,$6,$7,'VENUE_LIVE_FLAG', now(), "
            " now() + interval '2 hours', $8, 'nfl')",
            slug, event, slug, side, intent, sports_type, state,
            11 if side == "a" else 12)
