"""R30C · THE EVIDENCE-BACKED CORRELATION GRAPH (program section 16).

  * edges come from SHARED SETTLEMENT DEPENDENCE only, each with a
    dependence class: EXACT (one contract; one winner partition), MEASURED
    (same-day fixtures of one competition, from settled outcomes, with a 95%
    upper bound), UNMEASURED = WORST CASE (linked markets, the same team,
    a node with no catalogue identity, a competition without enough settled
    pairs);
  * a node with NO catalogue row is never treated as independent: it falls
    back to the recorded fixture and the fixture / teams / date its slug
    names, and is linked worst case to anything it could share settlement
    with -- a claim the graph CHECKS (unknown_is_worst_case) rather than
    asserts;
  * the WORST CASE is the caps' treatment (every node loses its whole cost;
    every node comonotone) and the EVIDENCED case is never above it; a
    MEASURED link is a Frechet mixture with ONE coin per coupling cluster,
    so the simulated joint is w x comonotone + (1 - w) x independent, not
    w^2; common random numbers make a tiny candidate a tiny marginal;
  * a hedge's two legs cannot both lose: the evidenced maximum loss is the
    worst FEASIBLE outcome, not the sum of costs;
  * the opportunities the caps REFUSE are candidates -- produced here by
    the REAL completed-game pass (decide_one -> REFUSE on a cross-strategy
    cap; decide_one -> submit_order refused on the per-fixture cap) -- and
    each shows Allie's own current treatment beside its shadow marginal;
  * the worst-case caps stay authoritative: Allie's haircut, the fixture /
    book caps and the paper ledger's refusals are unchanged, and the graph
    says a relaxation needs a later owner decision.

The DB tests' positions are written by the REAL paper ledger and simulator,
their identities by the REAL catalogue writer (workers/premap._market_rows +
_upsert, from the venue's event payload shape), their settled history by the
REAL outcome join, their candidates by the REAL paper pass. SYNTHETIC test
data in the test DB only.
"""
from __future__ import annotations

import ast
import pathlib
import time
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi import Response

from sportsassets import allie_capital as AC
from sportsassets import correlation_graph as CG
from tests import paper_harness as H
from tests.test_settlement_exception_risk import cg_on  # noqa: F401 (fixture)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
SRC = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"


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


def _games(n, *, league="mlb", p=0.5, qty=100, price=0.5, prefix="g"):
    out = []
    for i in range(n):
        oc = ["%sh%d" % (prefix, i), "%sa%d" % (prefix, i)]
        out.append(_node("%s%d" % (prefix, i), CG.K_POSITION,
                         "aec-%s-%sh%d-%sa%d-2026-10-04" % (
                             league, prefix, i, prefix, i),
                         "LONG", [oc[0]], oc, qty, price,
                         "%sev%d" % (prefix, i), oc, league=league, p=p))
    return out


def test_the_evidenced_tail_is_never_above_the_worst_case():
    nodes = _games(30)
    measured = {"mlb": {"status": CG.D_MEASURED, "abs_phi_upper_95": 0.1,
                        "phi": 0.0, "disjoint_pairs": 40}}
    unmeasured = CG.portfolio(nodes, CG.build_edges(nodes, {}))
    ev = CG.portfolio(nodes, CG.build_edges(nodes, measured))
    # 30 same-day games of one league, nothing measured: comonotone
    assert unmeasured["evidenced_case"]["tail_99_es_usd"] == pytest.approx(
        unmeasured["worst_case"]["tail_99_es_usd"])
    # measured w = 0.1 on one cluster: with probability 0.1 the whole cluster
    # is comonotone, so all 30 lose together with probability 0.05 > 1% --
    # the 99% tail of the Frechet mixture IS the comonotone loss. Never above
    # the worst case, and here equal to it (the conservative higher-order
    # assumption: only PAIRWISE dependence is measured)
    assert ev["evidenced_case"]["tail_99_es_usd"] <= \
        ev["worst_case"]["tail_99_es_usd"] + 1e-6
    assert ev["evidenced_case"]["tail_99_es_usd"] == pytest.approx(1500.0)
    # thirty games in thirty DIFFERENT competitions share no settlement
    # dependence: independent, and the tail falls well below the worst case
    spread = [dict(n, league="lg%d" % i) for i, n in enumerate(nodes)]
    ind = CG.portfolio(spread, CG.build_edges(spread, {}))
    assert CG.build_edges(spread, {}) == []
    assert ind["evidenced_case"]["tail_99_es_usd"] < \
        0.8 * ind["worst_case"]["tail_99_es_usd"]
    # and a re-read reproduces it (seeded)
    again = CG.portfolio(nodes, CG.build_edges(nodes, measured))
    assert again["evidenced_case"] == ev["evidenced_case"]


def test_the_simulation_produces_the_measured_mixture_not_its_square():
    """Review, R30C: a mixture coin per GROUP coupled two groups only with
    probability w^2. With ONE coin per cluster, P(both lose) is
    w x P_comonotone + (1 - w) x P_independent, within Monte Carlo error."""
    w = 0.3
    dep = {"mlb": {"status": CG.D_MEASURED, "abs_phi_upper_95": w,
                   "phi": 0.0, "disjoint_pairs": 40}}
    two = _games(2, p=0.95, qty=100, price=0.95)
    edges = CG.build_edges(two, dep)
    assert edges[0]["dependence"] == CG.D_MEASURED
    assert edges[0]["comonotone_weight"] == pytest.approx(w)
    n = 200_000
    pf = CG.portfolio(two, edges, samples=n, keep_samples=True)
    both = sum(1 for x in pf["_samples"] if x > 189.999) / n
    q = 0.05
    want = w * q + (1 - w) * q * q                 # 0.01675
    square = w * w * q + (1 - w * w) * q * q       # 0.00677
    se = (want * (1 - want) / n) ** 0.5
    assert abs(both - want) < 5 * se, (both, want, square)
    assert abs(both - square) > 20 * se
    # P(both lose) >= 1%, so the 99% ES is the joint loss of both
    assert pf["evidenced_case"]["tail_99_es_usd"] == pytest.approx(190.0)
    # three groups: P(all three lose) = w q + (1 - w) q^3 (one coin for the
    # whole cluster -- a per-group coin of sqrt(w) would give w^1.5 q + ...)
    three = _games(3, p=0.95, qty=100, price=0.95)
    pf3 = CG.portfolio(three, CG.build_edges(three, dep), samples=n,
                       keep_samples=True)
    all3 = sum(1 for x in pf3["_samples"] if x > 284.999) / n
    want3 = w * q + (1 - w) * q ** 3
    se3 = (want3 * (1 - want3) / n) ** 0.5
    assert abs(all3 - want3) < 5 * se3, (all3, want3)


def test_common_random_numbers_make_a_tiny_candidate_a_tiny_marginal():
    """Review, R30C: without common random numbers a $0.0005 candidate moved
    the evidenced ES of a 40-position book by $2.44 (Monte Carlo noise).
    Each group's draws are keyed by the group, and a cluster's shared draws
    by its first COMMITTED group, so adding a candidate -- even one whose
    group key sorts FIRST -- leaves every other draw unchanged."""
    dep = {"mlb": {"status": CG.D_MEASURED, "abs_phi_upper_95": 0.2,
                   "phi": 0.05, "disjoint_pairs": 40}}
    book = _games(40, qty=1000, price=0.5)
    # mixed with eight independent competitions, so the tail is not
    # saturated by the measured cluster alone
    book += _games(8, qty=3000, price=0.5, league="solo", prefix="s")
    for i, n in enumerate(book[40:]):
        n["league"] = "solo%d" % i
    base = CG.portfolio(book, CG.build_edges(book, dep), samples=4000,
                        keep_samples=True)

    def marginal(cand):
        g = CG.graph(book + [cand], dep, samples=4000)
        # the committed book's draws are the same with or without it
        assert g["portfolio"]["held_plus_working_orders"][
            "evidenced_case"] == base["evidenced_case"]
        return g["candidates"][0]["shadow_marginal"]
    # (1) another competition: its own singleton draws only
    other = _node("c1", CG.K_CANDIDATE, "aec-xfl-aah-aaa-2026-10-04", "LONG",
                  ["aah"], ["aah", "aaa"], 1, 0.0005, "aaa-first",
                  ["aah", "aaa"], league="xfl", p=0.5)
    m1 = marginal(other)
    assert m1["status"] == "SHADOW_INFORMATION_ONLY"
    assert -1.0 <= m1["evidenced_tail_99_es_usd"] <= 0.0005 + 1e-9, m1
    assert m1["reweights_the_committed_book"] is None
    # (2) the SAME measured competition, the same margins (so the cluster's
    # weight is unchanged), a group key that sorts before every committed
    # one: the cluster keeps its committed anchor, and the marginal is the
    # candidate's own $0.0005 at most
    same = dict(other, node_id="CANDIDATE:c2", league="mlb",
                slug="aec-mlb-aah-aaa-2026-10-04")
    m2 = marginal(same)
    assert m2["reweights_the_committed_book"] is None, m2
    assert -1.0 <= m2["evidenced_tail_99_es_usd"] <= 0.0005 + 1e-9, m2
    assert abs(m2["evidenced_tail_99_es_usd"]) < 0.01, m2
    assert m2["evidenced_tail_99_es_mc_standard_error_usd"] is not None
    assert m2["evidenced_tail_99_es_mc_standard_error_usd"] < 0.05, m2
    # (3) skewed margins admit little phi: the measured bound does not
    # restrict the candidate's links, the cluster carries ONE weight, and
    # the re-weighting of the committed book is disclosed with the marginal
    skew = dict(same, node_id="CANDIDATE:c3", p=0.0005, p_used=0.0005)
    m3 = marginal(skew)
    rw = m3["reweights_the_committed_book"]
    assert rw is not None and rw["to"] == pytest.approx(1.0), m3
    assert rw["from_max"] == pytest.approx(0.2)


def _premap_rows(slug, event, teams, gs, lg="mlb"):
    """us_premap-shaped rows, as resolve_identity reads them (pure tests)."""
    out = []
    for (name, abbr), intent in zip(teams, ("ORDER_INTENT_BUY_LONG",
                                            "ORDER_INTENT_BUY_SHORT")):
        out.append({"market_slug": slug, "event_slug": event,
                    "event_title": event, "kind": "side",
                    "sports_type": "baseball_team_full_game_winner",
                    "team_abbr": abbr, "team_name": name,
                    "team_league": lg, "side_norm": name, "intent": intent,
                    "line": "", "game_start": gs})
    return out


def _raw_fills(*legs):
    fills = []
    for i, (slug, side, qty, px) in enumerate(legs):
        fills.append({"group_id": "g%d" % i, "slug": slug,
                      "holding_side": side, "direction": "BUY", "qty": qty,
                      "price": px, "fee_usd": 0.0, "at": 1.0,
                      "order_ref": "o%d" % i})
    return {"fills": fills, "orders": [], "settlements": [],
            "available": True}


def test_a_node_without_a_catalogue_row_is_worst_case_never_independent():
    """Review, R30C: with no us_premap row, two markets of ONE game became a
    MEASURED same-competition pair and the same two teams on consecutive
    days had NO edge, while the payload claimed unknown_is_worst_case. The
    node now falls back to its recorded fixture and to what its slug names,
    and is linked worst case to anything it could share settlement with."""
    import calendar
    dep = {"mlb": {"status": CG.D_MEASURED, "abs_phi_upper_95": 0.3,
                   "phi": 0.1, "disjoint_pairs": 40}}
    gs = float(calendar.timegm((2026, 10, 4, 23, 10, 0)))
    raw = _raw_fills(
        ("aec-mlb-bos-nyy-2026-10-04", "LONG", 1000, 0.5),        # g0
        ("asc-mlb-bos-nyy-2026-10-04-1pt5", "LONG", 1000, 0.5),   # g1
        ("aec-mlb-bal-nyy-2026-09-27", "LONG", 1000, 0.95),       # g2
        ("aec-mlb-bal-nyy-2026-09-28", "LONG", 1000, 0.95),       # g3
        ("aec-mlb-sea-hou-2026-10-04", "LONG", 1000, 0.5),        # g4
        ("aec-mlb-cle-det-2026-10-04", "LONG", 1000, 0.5),        # g5
        ("aec-nfl-kc-buf-2026-10-04", "LONG", 1000, 0.5))         # g6
    # ONLY cle-det has a catalogue row
    premap = _premap_rows("aec-mlb-cle-det-2026-10-04", "mlb-cle-det-2026-10-04",
                          (("cleveland guardians", "cle"),
                           ("detroit tigers", "det")), gs)
    nodes = CG.build_nodes(raw, premap, {}, now=gs - 3600)
    by = {n["slug"]: n for n in nodes}
    assert by["aec-mlb-cle-det-2026-10-04"]["identity_complete"] is True
    assert by["aec-mlb-bos-nyy-2026-10-04"]["identity_complete"] is False
    assert by["aec-mlb-bos-nyy-2026-10-04"]["fixture_from_slug"] == \
        "mlb-bos-nyy-2026-10-04"
    edges = CG.build_edges(nodes, dep)
    e = {frozenset((x["a"], x["b"])): x for x in edges}

    def edge(s1, s2):
        return e.get(frozenset((by[s1]["node_id"], by[s2]["node_id"])))
    # one game, two markets: LINKED, worst case -- never MEASURED
    one = edge("aec-mlb-bos-nyy-2026-10-04", "asc-mlb-bos-nyy-2026-10-04-1pt5")
    assert one["relation"] == CG.R_SAME_EVENT_LINKED
    assert one["dependence"] == CG.D_UNMEASURED
    assert "VENUE_EVENT:mlb-bos-nyy-2026-10-04" in one["matched_on"]
    # a back-to-back series: the same teams the next day -- worst case
    b2b = edge("aec-mlb-bal-nyy-2026-09-27", "aec-mlb-bal-nyy-2026-09-28")
    assert b2b["relation"] == CG.R_SAME_TEAM
    assert b2b["dependence"] == CG.D_UNMEASURED
    # an unidentified node against an identified one of the same competition
    # and day: worst case, even though the competition's dependence is
    # MEASURED
    un = edge("aec-mlb-sea-hou-2026-10-04", "aec-mlb-cle-det-2026-10-04")
    assert un["relation"] == CG.R_IDENTITY_UNKNOWN
    assert un["dependence"] == CG.D_UNMEASURED
    # a different competition shares no settlement: no edge
    assert edge("aec-mlb-sea-hou-2026-10-04", "aec-nfl-kc-buf-2026-10-04") \
        is None
    # and the claim is CHECKED, and holds
    chk = CG.unknown_is_worst_case(nodes, edges)
    assert chk["holds"] is True and chk["pairs_checked"] > 0, chk
    g = CG.graph(nodes, dep, samples=2000)
    assert g["unknown_is_worst_case"] is True
    # the back-to-back pair is comonotone in the evidenced tail (V1: 1119.51
    # evidenced vs 1900 worst, i.e. independent)
    pair = [by["aec-mlb-bal-nyy-2026-09-27"], by["aec-mlb-bal-nyy-2026-09-28"]]
    pf = CG.portfolio(pair, CG.build_edges(pair, dep), samples=20000)
    assert pf["evidenced_case"]["tail_99_es_usd"] == pytest.approx(
        pf["worst_case"]["tail_99_es_usd"])


def test_a_recorded_fixture_links_markets_whose_slugs_say_nothing():
    raw = _raw_fills(("pm-condition-aaa", "LONG", 100, 0.5),
                     ("pm-condition-bbb", "LONG", 100, 0.5))
    vals = {"pm-condition-aaa": {"probability": 0.5, "event_key": "pin-77",
                                 "buy_intent": "ORDER_INTENT_BUY_LONG"},
            "pm-condition-bbb": {"probability": 0.5, "event_key": "pin-77",
                                 "buy_intent": "ORDER_INTENT_BUY_LONG"}}
    nodes = CG.build_nodes(raw, [], vals, now=0.0)
    edges = CG.build_edges(nodes, {})
    assert edges[0]["relation"] == CG.R_SAME_EVENT_LINKED
    assert edges[0]["matched_on"] == ["BOOK_EVENT:pin-77"]


def test_the_worst_case_claim_is_checked_not_asserted():
    raw = _raw_fills(("aec-mlb-bal-nyy-2026-09-27", "LONG", 100, 0.5),
                     ("aec-mlb-bal-nyy-2026-09-28", "LONG", 100, 0.5))
    nodes = CG.build_nodes(raw, [], {}, now=0.0)
    edges = CG.build_edges(nodes, {})
    assert CG.unknown_is_worst_case(nodes, edges)["holds"] is True
    # an edge set that treats the pair as independent fails the check
    got = CG.unknown_is_worst_case(nodes, [])
    assert got["holds"] is False and got["violations_total"] == 1
    measured = [dict(edges[0], dependence=CG.D_MEASURED)]
    assert CG.unknown_is_worst_case(nodes, measured)["holds"] is False


def test_an_enter_not_yet_ordered_is_a_candidate_with_its_own_size():
    """position_rooms._paper's PROPOSED row (Derek ENTER, no order, no
    finding) -- a transient state in production (decide_one submits right
    after recording the ENTER) -- stays a candidate with its own size."""
    from sportsassets import position_rooms as PR
    raw = {"fills": [], "settlements": [], "available": True, "orders": [{
        "order_ref": "paper_dec_x", "decision_id": "paper_dec_x",
        "group_id": None, "direction": "BUY", "holding_side": "LONG",
        "slug": "aec-mlb-sea-hou-2026-10-04", "qty": 300.0,
        "filled_qty": 0.0, "limit": 0.55, "state": PR.S_PROPOSED,
        "strategy": "PINNACLE_COMPLETED_GAME_PAPER",
        "fixture": "condition:aec-mlb-sea-hou-2026-10-04"}]}
    n = CG.build_nodes(raw, [], {}, now=0.0)[0]
    assert n["kind"] == CG.K_CANDIDATE
    assert n["candidate_source"] == CG.C_ENTER_NOT_ORDERED
    assert (n["qty"], n["price"], n["cost_usd"]) == (300.0, 0.55, 165.0)
    assert n["fixture_recorded"] == "condition:aec-mlb-sea-hou-2026-10-04"


def test_a_cap_refused_candidate_is_sized_at_its_budget_upper_bound():
    raw = {"fills": [], "orders": [], "settlements": [], "available": True}
    vals = {"aec-mlb-sea-hou-2026-10-04": {
        "probability": 0.6, "buy_intent": "ORDER_INTENT_BUY_LONG",
        "valuation_id": 9}}
    cands = [
        {"decision_id": "d1", "slug": "aec-mlb-sea-hou-2026-10-04",
         "holding_side": "LONG", "strategy": "PINNACLE_COMPLETED_GAME_PAPER",
         "source": CG.C_CAP_REFUSED,
         "refusals": ["ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE"],
         "budget_usd": 1000.0, "proposed_qty": None, "limit_price": None},
        {"decision_id": "d2", "slug": "aec-mlb-sea-hou-2026-10-04",
         "holding_side": "SHORT", "strategy": "PINNACLE_COMPLETED_GAME_PAPER",
         "source": CG.C_ORDER_CAP_REFUSED,
         "refusals": ["ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP"],
         "budget_usd": 1000.0, "proposed_qty": 300, "limit_price": 0.41},
        {"decision_id": "d3", "slug": "aec-mlb-x-y-2026-10-04",
         "holding_side": "LONG", "source": CG.C_CAP_REFUSED,
         "refusals": ["THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT"],
         "budget_usd": None}]
    n = {x["ref"]: x for x in CG.build_nodes(raw, [], vals, now=0.0,
                                             candidates=cands)}
    # the cap refused before the book was read: the budget at the fair price
    assert n["d1"]["price"] == pytest.approx(0.6)
    assert n["d1"]["qty"] == 1666 and n["d1"]["cost_usd"] <= 1000.0
    assert n["d1"]["size_basis"].startswith(
        "UPPER_BOUND_AT_THE_STRATEGY_ORDER_BUDGET")
    # an ENTER whose order a cap refused carries its own size
    assert (n["d2"]["qty"], n["d2"]["price"]) == (300, 0.41)
    assert n["d2"]["current_refusals"] == [
        "ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP"]
    # no budget: listed, never scored on a made-up size
    assert n["d3"]["scorable"] is False
    g = CG.graph(list(n.values()), {}, samples=1000)
    by = {c["node_id"]: c for c in g["candidates"]}
    assert by["CANDIDATE:d3"]["shadow_marginal"]["status"] == "NOT_SCORED"
    assert by["CANDIDATE:d1"]["shadow_marginal"]["status"] == \
        "SHADOW_INFORMATION_ONLY"


def test_the_caps_stay_authoritative_and_unchanged():
    from sportsassets import bettor_paper_ledger as L
    assert AC.CORRELATION_HAIRCUT == 0.25
    assert AC.FIXTURE_CAP_USD == 125_000.0 and AC.BOOK_CAP_USD == 400_000.0
    caps = {c["control"]: c["value"] for c in CG.AUTHORITATIVE_CAPS}
    assert caps["allie_capital.CORRELATION_HAIRCUT"] == AC.CORRELATION_HAIRCUT
    assert caps["allie_capital.FIXTURE_CAP_USD"] == AC.FIXTURE_CAP_USD
    assert caps["allie_capital.BOOK_CAP_USD"] == AC.BOOK_CAP_USD
    assert caps["bettor_paper_ledger.R_FIXTURE_OWNED"] == L.R_FIXTURE_OWNED
    assert caps["bettor_paper_ledger.R_SAME_CONTRACT_HELD"] == \
        L.R_SAME_CONTRACT_HELD
    assert caps["bettor_paper_ledger.R_PER_FIXTURE"] == L.R_PER_FIXTURE
    treat = {"CANDIDATE:c": {"status": "RECORDED", "allie_haircut": 0.5,
                             "fixture_open_groups": 2,
                             "source": "canonical_decision_intents.allie"}}
    hedge = _hedge_book()
    g = CG.graph(hedge + [
        dict(_node("c", CG.K_CANDIDATE, S1, "LONG", [O1[0]], O1, 200, 0.5,
                   "ev1", O1, p=0.5), node_id="CANDIDATE:c")], {},
        treatments=treat)
    assert g["relaxation"]["applied"] is False
    assert all(c["status"] == "AUTHORITATIVE_UNCHANGED"
               for c in g["authoritative_caps"])
    cand = g["candidates"][0]
    # the current treatment is Allie's own record, carried -- not re-derived
    assert cand["current_treatment"]["allie_haircut"] == 0.5
    assert cand["current_treatment"]["source"] == \
        "canonical_decision_intents.allie"
    assert cand["current_treatment"]["authority"] == "AUTHORITATIVE_UNCHANGED"
    assert cand["shadow_marginal"]["status"] == "SHADOW_INFORMATION_ONLY"
    assert {e["payout_relation"] for e in cand["edges_to_the_book"]} == {
        "IDENTICAL_PAYOUT", "COMPLEMENTARY"}
    assert g["authority"] == CG.AUTHORITY
    # a pure call that read nothing says so
    bare = CG.graph(hedge + [dict(_node(
        "c", CG.K_CANDIDATE, S1, "LONG", [O1[0]], O1, 200, 0.5, "ev1", O1,
        p=0.5), node_id="CANDIDATE:c")], {})
    assert bare["candidates"][0]["current_treatment"]["status"] == "NOT_READ"


def _sql_in(path: pathlib.Path, func: str, needle: str) -> str:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name == func:
            for c in ast.walk(node):
                if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                        and needle in c.value:
                    return c.value
    raise AssertionError("no SQL with %r in %s.%s" % (needle, path, func))


def _attrs_in(path: pathlib.Path, func: str) -> set:
    """Every attribute / name a function's body mentions."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name == func:
            return ({c.attr for c in ast.walk(node)
                     if isinstance(c, ast.Attribute)}
                    | {c.id for c in ast.walk(node)
                       if isinstance(c, ast.Name)})
    raise AssertionError("no function %s in %s" % (func, path))


def test_the_restated_constants_are_their_sources_own():
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_limits as LIMITS
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_derek as PD
    # the correlation / concentration caps whose refusals name candidates
    assert set(CG.CORRELATION_CAP_REFUSALS) == {
        L.R_FIXTURE_OWNED, L.R_SAME_STRATEGY_LIVE, L.R_SAME_CONTRACT_HELD,
        L.R_PER_FIXTURE, L.R_PER_MARKET}
    assert PB.R_CROSS_STRATEGY in CG.CORRELATION_CAP_REFUSALS
    # cash, per-order and group-count refusals are not correlation caps
    for r in (L.R_INSUFFICIENT, L.R_PER_ORDER, L.R_MAX_GROUPS):
        assert r not in CG.CORRELATION_CAP_REFUSALS
    assert CG.ORDER_REFUSED_FINDING == PD.R_ORDER_REFUSED == PB.R_ORDER_REFUSED
    # the owner policy's budget, and the effective budget for any account
    assert CG.OWNER_POLICY_ACCOUNT == LIMITS.ACCOUNT_ID
    assert CG.OWNER_POLICY_ENTRY_USD == LIMITS.ENTRY_USD
    for cfg, acct in (({"entry": {"target_order_usd": 5000.0},
                        "risk": {"per_order_cap_usd": 250.0}}, "paper_x"),
                      ({"entry": {"target_order_usd": 800.0},
                        "risk": {"per_order_cap_usd": None}}, "paper_y"),
                      ({"entry": {"target_order_usd": 5000.0},
                        "risk": {"per_order_cap_usd": 250.0}},
                       LIMITS.ACCOUNT_ID)):
        eff = LIMITS.effective_config(cfg, acct)
        cap = eff["risk"].get("per_order_cap_usd")
        want = (eff["entry"]["target_order_usd"] if cap is None
                else min(eff["entry"]["target_order_usd"], cap))
        assert CG.order_budget_usd(cfg, acct) == want, (cfg, acct)
    # ALLIE'S FIXTURE QUERY, THE SAME STATEMENT (RC6.3 allie-exposure: both
    # execute open_position_canon's canonical-open-quantity statement; the
    # graph's constant IS that object, and allie_at_decision executes it by
    # name with no inline SQL of its own to drift from it). RC6.3c
    # allie-exposure scale: at the decision the fixture and the book come
    # out of ONE statement (OPEN_EXPOSURE_BOOK_AND_FIXTURE_SQL) built on the
    # very rows statement the graph's fixture query is built on, so the two
    # still count the same positions the same way; without a fixture the
    # book statement alone.
    from sportsassets import open_position_canon as OPC
    assert CG.ALLIE_FIXTURE_SQL is OPC.OPEN_EXPOSURE_FIXTURE_SQL
    assert OPC.OPEN_EXPOSURE_ROWS_SQL in CG.ALLIE_FIXTURE_SQL
    assert OPC.OPEN_EXPOSURE_ROWS_SQL in OPC.OPEN_EXPOSURE_BOOK_AND_FIXTURE_SQL
    assert "OPEN_EXPOSURE_BOOK_AND_FIXTURE_SQL" in _attrs_in(
        SRC / "canonical_components.py", "allie_at_decision")
    assert "OPEN_EXPOSURE_BOOK_SQL" in _attrs_in(
        SRC / "canonical_components.py", "allie_at_decision")
    for needle in ("paper_settlements", "paper_orders", "NOT EXISTS"):
        try:
            _sql_in(SRC / "canonical_components.py", "allie_at_decision",
                    needle)
        except AssertionError:
            continue
        raise AssertionError("allie_at_decision carries inline SQL with %r: "
                             "her exposure inputs are open_position_canon's"
                             % needle)
    # THE FUNDED CORRELATED-EXPOSURE RAIL, pinned to its source text (an
    # execution module this read-only module and this test do not import)
    ee = (SRC / "bettor_entry_execution.py").read_text()
    assert '"MAX_CORRELATED_EXPOSURE": STANDARD * LIMIT_BASIS[' in ee
    assert '"MAX_CORRELATED_EXPOSURE": (1, "one standard trade of worst-case' \
        in ee
    assert "WORST_CASE_CORRELATION_ASSUMPTION = (" in ee
    fe = (SRC / "bettor_funded_execution.py").read_text()
    assert '"MAX_CORRELATED_EXPOSURE": (' in fe
    assert "under the worst-case correlation assumption" in fe
    controls = {c["control"] for c in CG.AUTHORITATIVE_CAPS}
    assert ("bettor_entry_execution.PREDECLARED_LIMITS"
            "['MAX_CORRELATED_EXPOSURE']") in controls
    assert "bettor_funded_execution measured['MAX_CORRELATED_EXPOSURE']" \
        in controls


def test_the_graph_is_bounded_and_says_so(monkeypatch):
    monkeypatch.setattr(CG, "MAX_COMMITTED_NODES", 10)
    monkeypatch.setattr(CG, "MAX_CANDIDATE_NODES", 2)
    book = _games(15, league="x")
    cands = [dict(n, kind=CG.K_CANDIDATE, node_id="CANDIDATE:%d" % i)
             for i, n in enumerate(_games(4, league="y", prefix="c"))]
    g = CG.graph(book + cands, {}, samples=50_000)
    assert g["portfolio_complete"] is False
    assert g["truncation"]["committed"] == {"total": 15, "carried": 10}
    assert g["truncation"]["candidates"] == {"total": 4, "carried": 2}
    # the sample count falls with the book and is disclosed
    assert g["monte_carlo"]["samples"] == min(
        50_000, CG.MC_GROUP_DRAW_BUDGET // g["monte_carlo"]["outcome_groups"])
    assert g["monte_carlo"]["samples_requested"] == 50_000


# ═════════════════════════════════════════════════════════════════════
# ON A REAL DATABASE
# ═════════════════════════════════════════════════════════════════════

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def _catalogue(conn, *, lg, slug, gs, long_team, short_team):
    """The venue's event payload (the SDK shape the catalogue sweep reads),
    written by the REAL catalogue writer: workers/premap._market_rows ->
    _upsert, exactly as the sweep writes it."""
    from sportsassets.workers import premap as pm
    await pm._ensure_table(conn)
    ev_slug = slug.split("-", 1)[1]          # the venue's event-slug form
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(gs))

    def side(t, is_long):
        abbr, name, safe, tid = t
        return {"identifier": slug, "description": name.split()[-1],
                "long": is_long,
                "team": {"abbreviation": abbr, "name": name,
                         "safeName": safe, "league": "mlb", "id": tid}}
    ev = {"slug": ev_slug,
          "title": "%s vs. %s" % (long_team[1], short_team[1]),
          "markets": [{"slug": slug,
                       "question": "%s vs. %s" % (long_team[1],
                                                  short_team[1]),
                       "sportsMarketType": "baseball_team_full_game_winner",
                       "gameStartTime": iso, "closed": False,
                       "marketSides": [side(long_team, True),
                                       side(short_team, False)]}]}
    keys = pm.event_keys_for(ev["title"], ev["slug"])
    rows = [r for m in ev["markets"] for r in pm._market_rows(ev, m)]
    keys = sorted(set(keys) | pm.venue_kick_keys(rows))
    for r in rows:
        await pm._upsert(conn, r, pm.keys_for_row(keys, r))
    return ev_slug


async def _position(conn, a, *, slug, side, qty, px, group, at):
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    o = H.order(a, key="%s-%s" % (group, side), slug=slug, holding_side=side,
                qty=qty, limit=px, group_id=group, at=at,
                fixture="condition:%s" % slug)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got["ok"], got
    book = ({"offers": [(px, qty * 2)]} if side == "LONG"
            else {"bids": [(round(1 - px, 2), qty * 2)]})
    await H.observe(conn, slug, at + 3, **book)
    r = await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 4,
                                 fee_fn=H.zero_fee)
    assert r["state"] == "FILLED", r
    # the held book re-read two-sided after the fill: an exit-side mark, so
    # the position is freshly manageable (migration 270's allocation rail
    # refuses a strategy's next ENTRY while its held positions are not)
    await H.observe(conn, slug, at + 5, bids=[(round(px - 0.02, 2), qty * 2)],
                    offers=[(px, qty * 2)])


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
    nxt = time.strftime("%Y-%m-%d", time.gmtime(gs + 86400))
    s1 = "%sbos-nyy-%s" % (prefix, day)
    s2 = "%ssea-hou-%s" % (prefix, day)
    # NO catalogue rows: a spread on the Red Sox game, and the same two
    # teams the next day
    s4 = "asc-%s-bos-nyy-%s-1pt5" % (lg, day)
    s5 = "%sbos-nyy-%s" % (prefix, nxt)
    try:
        ev1 = await _catalogue(conn, lg=lg, slug=s1, gs=gs,
                               long_team=("bos", "Boston Red Sox", "Boston",
                                          111),
                               short_team=("nyy", "New York Yankees",
                                           "New York", 147))
        await _catalogue(conn, lg=lg, slug=s2, gs=gs,
                         long_team=("sea", "Seattle Mariners", "Seattle", 136),
                         short_team=("hou", "Houston Astros", "Houston", 117))
        a = await H.new_account(conn, "graph")
        # THE HEDGE: YES Red Sox (LONG) 1000 @ 0.50 and YES Yankees (SHORT)
        # 800 @ 0.45 on one contract; a Mariners position, same day; the
        # uncatalogued spread and next-day legs
        await _position(conn, a, slug=s1, side="LONG", qty=1000, px=0.50,
                        group="paper_g_gr1_%s" % tag, at=H.T0)
        await _position(conn, a, slug=s1, side="SHORT", qty=800, px=0.45,
                        group="paper_g_gr2_%s" % tag, at=H.T0 + 20)
        await _position(conn, a, slug=s2, side="LONG", qty=500, px=0.40,
                        group="paper_g_gr3_%s" % tag, at=H.T0 + 40)
        await _position(conn, a, slug=s4, side="LONG", qty=200, px=0.40,
                        group="paper_g_gr4_%s" % tag, at=H.T0 + 60)
        await _position(conn, a, slug=s5, side="LONG", qty=100, px=0.50,
                        group="paper_g_gr5_%s" % tag, at=H.T0 + 80)
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
        assert sorted(n["kind"] for n in g["nodes"]) == [CG.K_POSITION] * 5
        by = {(n["slug"], n["side"]): n for n in g["nodes"]}
        # identities from the REAL catalogue writer's rows
        assert by[(s1, "LONG")]["pays_on"] == ["boston red sox"]
        assert by[(s1, "SHORT")]["pays_on"] == ["new york yankees"]
        assert by[(s1, "LONG")]["event_slug"] == ev1
        assert by[(s4, "LONG")]["identity_complete"] is False
        assert by[(s4, "LONG")]["fixture_recorded"] == "condition:%s" % s4
        e = {frozenset((x["a"], x["b"])): x for x in g["edges"]}

        def edge(x, y):
            return e[frozenset((by[x]["node_id"], by[y]["node_id"]))]
        hedge = edge((s1, "LONG"), (s1, "SHORT"))
        assert hedge["dependence"] == CG.D_EXACT
        assert hedge["payout_relation"] == "COMPLEMENTARY"
        comp = edge((s1, "LONG"), (s2, "LONG"))
        assert comp["relation"] == CG.R_SAME_COMPETITION
        assert comp["dependence"] == CG.D_MEASURED
        assert g["dependence_by_competition"][lg]["disjoint_pairs"] == 32
        # the uncatalogued spread is linked to its game, worst case: its
        # slug names the catalogue's own event
        sp = edge((s1, "LONG"), (s4, "LONG"))
        assert sp["relation"] == CG.R_SAME_EVENT_LINKED
        assert sp["dependence"] == CG.D_UNMEASURED
        assert "VENUE_EVENT:%s" % ev1 in sp["matched_on"]
        # the next day's uncatalogued game of the same teams: worst case
        tm = edge((s1, "LONG"), (s5, "LONG"))
        assert tm["relation"] == CG.R_SAME_TEAM
        assert tm["dependence"] == CG.D_UNMEASURED
        # an unidentified leg against the Mariners game: worst case
        assert edge((s2, "LONG"), (s4, "LONG"))["dependence"] == \
            CG.D_UNMEASURED
        assert g["unknown_is_worst_case"] is True
        assert g["unknown_is_worst_case_check"]["pairs_checked"] >= 4
        held = g["portfolio"]["held"]
        assert held["worst_case"]["sum_of_costs_usd"] == pytest.approx(
            500 + 360 + 200 + 80 + 50)
        # the hedge's feasible worst is -60, the Mariners leg -200, the
        # spread -80, the next-day leg -50
        assert held["evidenced_case"]["max_feasible_loss_usd"] == \
            pytest.approx(390.0)
        assert held["evidenced_case"]["tail_99_es_usd"] <= \
            held["worst_case"]["tail_99_es_usd"] + 1e-6
        assert g["relaxation"]["applied"] is False
        assert g["portfolio_complete"] is True

        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(API, "_pool", pool)
        from sportsassets import position_rooms as PR
        monkeypatch.setattr(PR, "PAPER_ACCOUNT_ID", a["account_id"])
        via = await API.correlation_graph(Response())
        assert via["ok"] and len(via["nodes"]) == 5
        assert via["authority"] == CG.AUTHORITY
    finally:
        await conn.execute("DELETE FROM us_premap WHERE market_slug LIKE $1",
                           prefix + "%")
        await _purge(conn, prefix)
        await conn.close()


async def _paper_pass(conn, acct, client, t, now):
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_derek as PD
    from sportsassets.agents import paper_runtime as PRT
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()

    async def nosleep(_):
        return None
    t.t = max(t.t, now)
    got = await PRT.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, fee_fn=H.flat_fee(0.01),
                               sleep=nosleep)
    assert got["ran"] and not got["errors"], got["errors"]


@pg
async def test_the_opportunities_the_caps_refuse_are_candidates(cg_on):  # noqa: F811
    """Review, R30C: only ENTER decisions with neither an order nor a
    finding were candidates, so the capacity the worst-case caps destroy
    (THIS_STRATEGY_ALREADY_HOLDS 196 rows / 24 h in production) could never
    appear. Both cap paths are driven through the REAL completed-game pass:

      A  another strategy already holds the contract -> decide_one records a
         REFUSE whose only refusal is the cross-strategy cap;
      B  the session's per-fixture cap is $10 -> decide_one ENTERs, and
         submit_order refuses the order (ORDER_REFUSED finding).
    """
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    from sportsassets import live_parity as LP
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_derek as PD
    from tests import paper_live_fixture as PL
    conn = await H.connect()
    tag = uuid.uuid4().hex[:6]
    try:
        await PL.purge_everything(conn)
        now = time.time() + 5.0
        ctl = await LP.control(conn)
        if ctl.get("halted"):
            await LP.clear_halt(conn, actor="test harness (human operator)",
                                reason="isolate this test")
        if await conn.fetchval("SELECT count(*) FROM execmirror_control") \
                == 0:
            await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        slug = v["slug"]
        t = PL.Transport(now)
        t.set(slug, offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        client = PL.client(t)

        # ── A: Derek's strategy already holds this contract (real ledger) ──
        acct_a = await PL.new_account(conn, "cga%s" % tag, now=now)
        o = H.order(acct_a, key="held", slug=slug, qty=100, limit=0.50,
                    group_id="paper_g_held_%s" % tag, at=now - 60,
                    fixture="condition:%s" % slug)
        o["strategy"] = "DEREK_ENTRY_POLICY_V2"
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=now - 60)
        assert got["ok"], got
        await H.observe(conn, slug, now - 57, offers=[(0.50, 500)])
        r = await SIM.simulate_order(conn, got["order"]["order_id"],
                                     now=now - 56, fee_fn=H.zero_fee)
        assert r["state"] == "FILLED", r
        await _paper_pass(conn, acct_a, client, t, now)
        da = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 "
            " AND valuation_id=$2 AND strategy=$3", acct_a["session_id"],
            v["valuation_id"], PB.CG_STRATEGY)
        assert da["verdict"] == "REFUSE", (da["refusal"], da["refusals"])
        assert list(da["refusals"]) == [PB.R_CROSS_STRATEGY]

        # ── B: a $10 per-fixture cap; the decision ENTERs, the order is
        # refused by the ledger ──
        acct_b = await PL.new_account(
            conn, "cgb%s" % tag, now=now,
            cfg=PL.config(risk={"per_fixture_cap_usd": 10.0}))
        await _paper_pass(conn, acct_b, client, t, now + 1)
        db = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 "
            " AND valuation_id=$2 AND strategy=$3", acct_b["session_id"],
            v["valuation_id"], PB.CG_STRATEGY)
        assert db["verdict"] == "ENTER", (db["refusal"], db["refusals"])
        fnd = await conn.fetchrow(
            "SELECT detail FROM paper_audrey_findings WHERE subject=$1 "
            " AND kind=$2", db["decision_id"], PD.R_ORDER_REFUSED)
        assert H.j(fnd["detail"])["refusal"] == L.R_PER_FIXTURE
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE decision_id=$1",
            db["decision_id"]) == 0

        # ── THE GRAPH OF A: the refused opportunity is a candidate ──
        ga = await CG.load(conn, account_id=acct_a["account_id"],
                           samples=2000)
        assert ga["ok"], ga
        ca = [c for c in ga["candidates"]
              if c["candidate_source"] == CG.C_CAP_REFUSED]
        assert len(ca) == 1, ga["candidates"]
        ca = ca[0]
        assert ca["current_refusals"] == [PB.R_CROSS_STRATEGY]
        assert ca["refusals_in_window"] >= 1
        assert ca["admission_without_the_cap"].startswith("NOT_ESTABLISHED")
        assert ca["size_basis"].startswith(
            "UPPER_BOUND_AT_THE_STRATEGY_ORDER_BUDGET")
        node = next(n for n in ga["nodes"] if n["node_id"] == ca["node_id"])
        budget = CG.order_budget_usd(acct_a["config"], acct_a["account_id"])
        assert budget and 0 < node["cost_usd"] <= budget
        # its link to the book: the SAME contract Derek's strategy holds
        assert [x["payout_relation"] for x in ca["edges_to_the_book"]] == [
            "IDENTICAL_PAYOUT"]
        # Allie's OWN fixture query, through her haircut rule: one filled,
        # unsettled ENTRY group on the decision's fixture
        cur = ca["current_treatment"]
        assert cur["status"] == "ALLIE_QUERY_RUN_AT_THIS_READ", cur
        assert cur["fixture_open_groups"] == 1
        assert cur["allie_haircut"] == pytest.approx(AC.CORRELATION_HAIRCUT)
        assert cur["authority"] == "AUTHORITATIVE_UNCHANGED"
        sm = ca["shadow_marginal"]
        assert sm["status"] == "SHADOW_INFORMATION_ONLY"
        # an identical payout is exact: the evidenced marginal IS the whole
        # added cost (both legs lose together), as in the worst case
        assert sm["evidenced_tail_99_es_usd"] == pytest.approx(
            node["cost_usd"], abs=1e-3)
        assert sm["worst_case_tail_99_es_usd"] == pytest.approx(
            node["cost_usd"], abs=1e-3)

        # ── THE GRAPH OF B: the ENTER whose order the cap refused ──
        gb = await CG.load(conn, account_id=acct_b["account_id"],
                           samples=2000)
        cb = [c for c in gb["candidates"]
              if c["candidate_source"] == CG.C_ORDER_CAP_REFUSED]
        assert len(cb) == 1, gb["candidates"]
        cb = cb[0]
        assert cb["current_refusals"] == [L.R_PER_FIXTURE]
        assert cb["admission_without_the_cap"].startswith(
            "ADMITTED_BY_EVERY_OTHER_RULE")
        assert cb["size_basis"] == "THE_DECISION'S_OWN_PROPOSED_QTY_AND_LIMIT"
        nb = next(n for n in gb["nodes"] if n["node_id"] == cb["node_id"])
        assert nb["qty"] == pytest.approx(float(db["proposed_qty"]))
        assert nb["price"] == pytest.approx(float(db["limit_price"]))
        # the ENTER's canonical intent recorded Allie's component: carried
        it = await conn.fetchrow(
            "SELECT allie FROM canonical_decision_intents WHERE "
            " decision_id=$1", db["decision_id"])
        assert it is not None
        cc = H.j(it["allie"])["correlation_concentration"]
        cur_b = cb["current_treatment"]
        assert cur_b["status"] == "RECORDED", cur_b
        assert cur_b["allie_haircut"] == cc["haircut"]
        assert cur_b["fixture_open_groups"] == cc["fixture_open_groups"]
        assert cb["shadow_marginal"]["status"] == "SHADOW_INFORMATION_ONLY"
        # nothing was relaxed
        assert ga["relaxation"]["applied"] is False
        assert gb["relaxation"]["applied"] is False
    finally:
        await PL.purge_everything(conn)
        await conn.close()
