"""R30C · THE EVIDENCE-BACKED CORRELATION GRAPH (program section 16).

  * edges come from SHARED SETTLEMENT DEPENDENCE only, each with a
    dependence class: EXACT (one contract; one winner partition), MEASURED
    (same-day fixtures of one competition, from settled outcomes, with a 95%
    upper bound), UNMEASURED = WORST CASE (linked markets, the same team,
    a competition without enough settled pairs);
  * the WORST CASE is the caps' treatment (every node loses its whole cost;
    every node comonotone) and the EVIDENCED case is never above it;
  * a hedge's two legs cannot both lose: the evidenced maximum loss is the
    worst FEASIBLE outcome, not the sum of costs;
  * the worst-case caps stay authoritative: Allie's haircut, the fixture /
    book caps and the paper ledger's refusals are unchanged, and the graph
    says a relaxation needs a later owner decision.

The DB test's positions are written by the REAL paper ledger and simulator,
its identities by us_premap rows of the venue's catalogue shape, its settled
history by the REAL outcome join. SYNTHETIC test data in the test DB only.
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi import Response

from sportsassets import allie_capital as AC
from sportsassets import correlation_graph as CG
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


def _node(nid, kind, slug, side, pays, outcomes, qty, price, ev, teams, *,
          league="mlb", date="2026-10-04", p=None, winner=True,
          gs=1_790_000_000.0):
    return {"node_id": nid, "kind": kind, "ref": nid, "group_id": "g" + nid,
            "slug": slug, "side": side, "qty": qty, "price": price,
            "cost_usd": qty * price, "max_payout_usd": qty,
            "identity_status": "ESTABLISHED" if winner else "UNGROUPED",
            "identity_reason": None if winner else "NOT_A_WINNER",
            "event_slug": ev, "winner_variable": winner,
            "pays_on": pays if winner else (
                ["RESOLVES_YES"] if side == "LONG" else ["RESOLVES_NO"]),
            "outcomes": outcomes if winner else ["RESOLVES_YES",
                                                 "RESOLVES_NO"],
            "teams": teams, "league": league, "game_start": gs, "date": date,
            "p": p, "p_used": p if p is not None else price}


O1 = ["boston red sox", "new york yankees"]
S1 = "aec-mlb-bos-nyy-2026-10-04"


def _hedge_book():
    return [
        _node("a", CG.K_POSITION, S1, "LONG", [O1[0]], O1, 1000, 0.50, "ev1",
              O1, p=0.5),
        _node("b", CG.K_POSITION, S1, "SHORT", [O1[1]], O1, 800, 0.45, "ev1",
              O1, p=0.5)]


def test_one_contract_two_sides_is_an_exact_complementary_edge():
    e = CG.build_edges(_hedge_book(), {})
    assert len(e) == 1
    assert e[0]["relation"] == CG.R_SAME_CONTRACT
    assert e[0]["dependence"] == CG.D_EXACT
    assert e[0]["payout_relation"] == "COMPLEMENTARY"


def test_a_hedge_cannot_lose_twice():
    nodes = _hedge_book()
    pf = CG.portfolio(nodes, CG.build_edges(nodes, {}))
    # the caps' treatment: both legs lose their whole cost
    assert pf["worst_case"]["sum_of_costs_usd"] == pytest.approx(860.0)
    # the feasible worst: the Yankees win, the Red Sox leg loses 500, the
    # Yankees leg pays 800 -> net -60
    assert pf["evidenced_case"]["max_feasible_loss_usd"] == pytest.approx(
        60.0)
    assert pf["evidenced_case"]["tail_99_es_usd"] <= \
        pf["worst_case"]["tail_99_es_usd"]
    g = pf["groups"][0]
    assert sorted(a["pnl_usd"] for a in g["atoms"]) == [-60.0, 140.0]


def test_one_event_two_winner_markets_share_one_partition():
    # soccer per-side contracts: LONG home and LONG away are mutually
    # exclusive (the draw pays neither)
    oc = ["arsenal", "chelsea", "DRAW"]
    nodes = [
        _node("h", CG.K_POSITION, "atc-epl-ars-che-2026-10-04-ars", "LONG",
              ["arsenal"], oc, 100, 0.40, "ev-s", oc[:2], league="epl",
              p=0.40),
        _node("w", CG.K_POSITION, "atc-epl-ars-che-2026-10-04-che", "LONG",
              ["chelsea"], oc, 100, 0.30, "ev-s", oc[:2], league="epl",
              p=0.30)]
    e = CG.build_edges(nodes, {})
    assert e[0]["relation"] == CG.R_SAME_EVENT_WINNER
    assert e[0]["payout_relation"] == "MUTUALLY_EXCLUSIVE"
    assert e[0]["neither_pays_on"] == ["DRAW"]
    pf = CG.portfolio(nodes, e)
    atoms = {tuple(a["outcomes"]): a for a in pf["groups"][0]["atoms"]}
    # the draw is priced as the remainder: 1 - 0.40 - 0.30
    assert atoms[("DRAW",)]["prob"] == pytest.approx(0.30)
    assert pf["evidenced_case"]["max_feasible_loss_usd"] == pytest.approx(
        70.0)


def test_linked_markets_and_the_same_team_are_worst_case_until_measured():
    a = _node("ml", CG.K_POSITION, S1, "LONG", [O1[0]], O1, 100, 0.5, "ev1",
              O1, p=0.5)
    sp = _node("sp", CG.K_POSITION, "aec-mlb-bos-nyy-2026-10-04-rl", "LONG",
               None, None, 100, 0.4, "ev1", O1, winner=False)
    nxt = _node("nx", CG.K_POSITION, "aec-mlb-bos-tor-2026-10-05", "LONG",
                ["boston red sox"], ["boston red sox", "toronto blue jays"],
                100, 0.5, "ev2", ["boston red sox", "toronto blue jays"],
                date="2026-10-05", gs=1_790_000_000.0 + 86400, p=0.5)
    e = {frozenset((x["a"], x["b"])): x for x in
         CG.build_edges([a, sp, nxt], {})}
    assert e[frozenset(("ml", "sp"))]["relation"] == CG.R_SAME_EVENT_LINKED
    assert e[frozenset(("ml", "sp"))]["dependence"] == CG.D_UNMEASURED
    t = e[frozenset(("ml", "nx"))]
    assert t["relation"] == CG.R_SAME_TEAM and t["dependence"] == \
        CG.D_UNMEASURED
    assert t["shared_teams"] == ["boston red sox"]


def _history(league, fixtures, dates=("2026-10-01", "2026-10-02")):
    out = []
    for i in range(fixtures):
        out.append({"league": league, "date": dates[i % len(dates)],
                    "fixture": "f%03d" % i, "long_won": (i * 7 // 3) % 2})
    return out


def test_same_day_dependence_is_measured_on_disjoint_pairs_or_worst_case():
    dep = CG.measured_dependence(_history("mlb", 64) + _history("kbo", 10))
    m = dep["mlb"]
    assert m["status"] == CG.D_MEASURED
    assert m["disjoint_pairs"] == 32
    assert 0.0 <= abs(m["phi"]) <= m["abs_phi_upper_95"] <= 1.0
    assert dep["kbo"]["status"] == CG.D_UNMEASURED
    # 5 fixtures on each of two dates: two disjoint pairs per date
    assert dep["kbo"]["disjoint_pairs"] == 4
    a = _node("x", CG.K_POSITION, "aec-mlb-sea-hou-2026-10-04", "LONG",
              ["sea"], ["sea", "hou"], 100, 0.5, "e1", ["sea", "hou"], p=0.5)
    b = _node("y", CG.K_POSITION, "aec-mlb-cle-det-2026-10-04", "LONG",
              ["cle"], ["cle", "det"], 100, 0.5, "e2", ["cle", "det"], p=0.5)
    e = CG.build_edges([a, b], dep)[0]
    assert e["relation"] == CG.R_SAME_COMPETITION
    assert e["dependence"] == CG.D_MEASURED
    # equal 0.5 margins admit phi 1, so the weight is the bound itself
    assert e["comonotone_weight"] == pytest.approx(m["abs_phi_upper_95"])
    a2, b2 = dict(a, league="kbo"), dict(b, league="kbo")
    assert CG.build_edges([a2, b2], dep)[0]["dependence"] == CG.D_UNMEASURED


def test_the_evidenced_tail_is_never_above_the_worst_case():
    nodes = []
    for i in range(30):
        oc = ["h%d" % i, "a%d" % i]
        nodes.append(_node("n%d" % i, CG.K_POSITION,
                           "aec-mlb-h%d-a%d-2026-10-04" % (i, i), "LONG",
                           [oc[0]], oc, 100, 0.5, "ev%d" % i, oc, p=0.5))
    measured = {"mlb": {"status": CG.D_MEASURED, "abs_phi_upper_95": 0.1,
                        "phi": 0.0, "disjoint_pairs": 40}}
    unmeasured = CG.portfolio(nodes, CG.build_edges(nodes, {}))
    ev = CG.portfolio(nodes, CG.build_edges(nodes, measured))
    # 30 same-day games of one league, nothing measured: comonotone
    assert unmeasured["evidenced_case"]["tail_99_es_usd"] == pytest.approx(
        unmeasured["worst_case"]["tail_99_es_usd"])
    # measured near-independence: the tail falls well below the worst case
    assert ev["evidenced_case"]["tail_99_es_usd"] < \
        0.8 * ev["worst_case"]["tail_99_es_usd"]
    # and a re-read reproduces it (seeded)
    again = CG.portfolio(nodes, CG.build_edges(nodes, measured))
    assert again["evidenced_case"] == ev["evidenced_case"]


def test_the_caps_stay_authoritative_and_unchanged():
    from sportsassets import bettor_paper_ledger as L
    assert AC.CORRELATION_HAIRCUT == 0.25
    assert AC.FIXTURE_CAP_USD == 125_000.0 and AC.BOOK_CAP_USD == 400_000.0
    caps = {c["control"]: c["value"] for c in CG.AUTHORITATIVE_CAPS}
    assert caps["allie_capital.CORRELATION_HAIRCUT"] == AC.CORRELATION_HAIRCUT
    assert caps["bettor_paper_ledger.R_FIXTURE_OWNED"] == L.R_FIXTURE_OWNED
    assert caps["bettor_paper_ledger.R_SAME_CONTRACT_HELD"] == \
        L.R_SAME_CONTRACT_HELD
    assert caps["bettor_paper_ledger.R_PER_FIXTURE"] == L.R_PER_FIXTURE
    g = CG.graph(_hedge_book() + [
        _node("c", CG.K_CANDIDATE, S1, "LONG", [O1[0]], O1, 200, 0.5, "ev1",
              O1, p=0.5)], {})
    assert g["relaxation"]["applied"] is False
    assert all(c["status"] == "AUTHORITATIVE_UNCHANGED"
               for c in g["authoritative_caps"])
    cand = g["candidates"][0]
    # the current treatment is Allie's own rule, recorded, not replaced
    assert cand["current_treatment"]["allie_haircut"] == pytest.approx(
        min(1.0, AC.CORRELATION_HAIRCUT * 2))
    assert cand["current_treatment"]["status"] == "AUTHORITATIVE_UNCHANGED"
    assert cand["shadow_marginal"]["status"] == "SHADOW_INFORMATION_ONLY"
    assert {e["payout_relation"] for e in cand["edges_to_the_book"]} == {
        "IDENTICAL_PAYOUT", "COMPLEMENTARY"}
    assert g["authority"] == CG.AUTHORITY


# ═════════════════════════════════════════════════════════════════════
# ON A REAL DATABASE
# ═════════════════════════════════════════════════════════════════════

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def _premap(conn, slug, event, lg, teams, gs):
    for side, (team, abbr) in zip(("LONG", "SHORT"), teams):
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title, "
            " market_slug, question, kind, line, side_norm, intent, "
            " team_abbr, team_name, team_safe_name, team_id, team_league, "
            " game_start, sports_type) VALUES ($1,$2,$3,$4,'TEST FIXTURE',"
            " 'side','',$5,$6,$7,$8,$8,1,$9,to_timestamp($10),"
            " 'baseball_team_full_game_winner')",
            "test-%s-%s" % (slug, side), event, "TEST %s" % event, slug,
            team, "ORDER_INTENT_BUY_%s" % side, abbr, team, lg, gs)


async def _position(conn, a, *, slug, side, qty, px, group, at):
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    o = H.order(a, key="%s-%s" % (group, side), slug=slug, holding_side=side,
                qty=qty, limit=px, group_id=group, at=at)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got["ok"], got
    book = ({"offers": [(px, qty * 2)]} if side == "LONG"
            else {"bids": [(round(1 - px, 2), qty * 2)]})
    await H.observe(conn, slug, at + 3, **book)
    r = await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 4,
                                 fee_fn=H.zero_fee)
    assert r["state"] == "FILLED", r


@pg
async def test_the_graph_of_a_real_paper_book(monkeypatch):
    from sportsassets import bettor_live_read as lr
    from sportsassets.api import command_risk_evidence as API
    from sportsassets.workers import ext_pinnacle_loop as X
    from tests.test_settlement_exception_risk import _purge, _valuation
    conn = await H.connect()
    tag = uuid.uuid4().hex[:6]
    lg = "cq%s" % tag
    prefix = "aec-%s-" % lg
    gs = time.time() + 3 * 3600
    day = time.strftime("%Y-%m-%d", time.gmtime(gs))
    s1 = "%sbos-nyy-%s" % (prefix, day)
    s2 = "%ssea-hou-%s" % (prefix, day)
    s3 = "%scle-det-%s" % (prefix, day)
    try:
        await _premap(conn, s1, "ev1-%s" % tag, lg,
                      (("boston red sox", "bos"), ("new york yankees", "nyy")),
                      gs)
        await _premap(conn, s2, "ev2-%s" % tag, lg,
                      (("seattle mariners", "sea"), ("houston astros", "hou")),
                      gs)
        await _premap(conn, s3, "ev3-%s" % tag, lg,
                      (("cleveland guardians", "cle"),
                       ("detroit tigers", "det")), gs)
        a = await H.new_account(conn, "graph")
        # THE HEDGE: YES Red Sox (LONG) 1000 @ 0.50 and YES Yankees (SHORT)
        # 800 @ 0.45 on one contract; and a Mariners position, same day
        await _position(conn, a, slug=s1, side="LONG", qty=1000, px=0.50,
                        group="paper_g_gr1_%s" % tag, at=H.T0)
        await _position(conn, a, slug=s1, side="SHORT", qty=800, px=0.45,
                        group="paper_g_gr2_%s" % tag, at=H.T0 + 20)
        await _position(conn, a, slug=s2, side="LONG", qty=500, px=0.40,
                        group="paper_g_gr3_%s" % tag, at=H.T0 + 40)
        # A CANDIDATE: Derek's ENTER with no order yet, a third game
        await conn.execute(
            "INSERT INTO paper_decisions (decision_id, session_id, "
            " account_id, decided_at, us_market_slug, holding_side, intent, "
            " verdict, internal_model, pinnacle, qualification_gaps, "
            " policy_version, simulator_version, proposed_qty, limit_price) "
            "VALUES ($1,$2,$3,now(),$4,'LONG','ORDER_INTENT_BUY_LONG',"
            " 'ENTER','{}','{}','[]','TEST','TEST',300,0.55)",
            "paper_dec_graph_%s" % tag, a["session_id"], a["account_id"], s3)
        # SETTLED HISTORY of the competition, through the real join: 64
        # fixtures over two dates -> 32 disjoint same-day pairs
        old = time.time() - 3 * 3600
        answers = {}
        for i in range(64):
            hs = "%sh%02d-a%02d-2026-09-%02d" % (prefix, i, i, 20 + i % 2)
            await _valuation(conn, slug=hs, family="baseball",
                             event_key="hev-%s-%d" % (tag, i), decided_at=old)
            v = (i * 7 // 3) % 2
            answers[hs] = {"status": lr.RESOLVED, "settlement_price": float(v),
                           "settlement_price_raw": str(v)}
        monkeypatch.setattr(X, "_read_resolution_blocking",
                            lambda s: answers.get(s, {"status": lr.PENDING}))
        got = await X.join_outcomes(conn, limit=500)
        assert got["resolved"] >= 64, got

        g = await CG.load(conn, account_id=a["account_id"], samples=2000)
        assert g["ok"], g
        kinds = sorted(n["kind"] for n in g["nodes"])
        assert kinds == [CG.K_CANDIDATE, CG.K_POSITION, CG.K_POSITION,
                         CG.K_POSITION], kinds
        by = {(n["slug"], n["side"]): n for n in g["nodes"]}
        assert by[(s1, "LONG")]["pays_on"] == ["boston red sox"]
        assert by[(s1, "SHORT")]["pays_on"] == ["new york yankees"]
        e = {frozenset((x["a"], x["b"])): x for x in g["edges"]}
        hedge = e[frozenset((by[(s1, "LONG")]["node_id"],
                             by[(s1, "SHORT")]["node_id"]))]
        assert hedge["dependence"] == CG.D_EXACT
        assert hedge["payout_relation"] == "COMPLEMENTARY"
        comp = e[frozenset((by[(s1, "LONG")]["node_id"],
                            by[(s2, "LONG")]["node_id"]))]
        assert comp["relation"] == CG.R_SAME_COMPETITION
        assert comp["dependence"] == CG.D_MEASURED
        assert g["dependence_by_competition"][lg]["disjoint_pairs"] == 32
        held = g["portfolio"]["held"]
        assert held["worst_case"]["sum_of_costs_usd"] == pytest.approx(
            500 + 360 + 200)
        # the hedge's feasible worst is -60, the Mariners leg -200
        assert held["evidenced_case"]["max_feasible_loss_usd"] == \
            pytest.approx(260.0)
        assert held["evidenced_case"]["tail_99_es_usd"] <= \
            held["worst_case"]["tail_99_es_usd"] + 1e-6
        assert g["relaxation"]["applied"] is False
        assert g["candidates"][0]["current_treatment"]["status"] == \
            "AUTHORITATIVE_UNCHANGED"

        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(API, "_pool", pool)
        from sportsassets import position_rooms as PR
        monkeypatch.setattr(PR, "PAPER_ACCOUNT_ID", a["account_id"])
        via = await API.correlation_graph(Response())
        assert via["ok"] and len(via["nodes"]) == 4
        assert via["authority"] == CG.AUTHORITY
    finally:
        await conn.execute("DELETE FROM us_premap WHERE market_slug LIKE $1",
                           prefix + "%")
        await _purge(conn, prefix)
        await conn.close()
