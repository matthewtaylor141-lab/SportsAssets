"""THE EVIDENCE-BACKED CORRELATION GRAPH (R30C, program section 16).

THE TREATMENT THIS SITS BESIDE -- AND DOES NOT REPLACE. Every correlation
control in the system today is WORST CASE, and each is safe:

  allie_capital            CORRELATION_HAIRCUT (0.25) of profit per capital-
                           hour PER OPEN GROUP already on the fixture, the
                           FIXTURE_CAP_USD and BOOK_CAP_USD headrooms
  bettor_paper_ledger      fixture ownership under the account lock
                           (ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE),
                           THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT, the
                           per-fixture / per-market concentration caps
  bettor_funded_execution  MAX_EVENT_EXPOSURE and MAX_CORRELATED_EXPOSURE
                           ("every live intent ... under the worst-case
                           correlation assumption")

They treat every pair of exposures that share anything as if they move
together -- which also counts the two legs of a HEDGE (YES Yankees and YES
Red Sox on one game) as doubly risky when they cannot both lose. The audit:
"safe but capacity-destructive. Build event/settlement-based correlation
evidence before relaxing it."

WHAT THIS BUILDS. Nodes are the PAPER book's open positions (filled, not
settled), its working buy orders and its candidate opportunities (Derek ENTER
decisions with no order yet) -- read through position_rooms' own loaders, so
the identities are the venue's catalogue rows, not titles that look alike.
Edges come from SHARED SETTLEMENT DEPENDENCE, each with a dependence class:

  SAME_CONTRACT                   the same venue market: identical payout
                                  (same side) or complementary (opposite
                                  sides). EXACT, by the contract's definition.
  SAME_FIXTURE_SAME_WINNER_VARIABLE
                                  two winner markets of one venue event: each
                                  leg pays on a set of outcomes of ONE
                                  partition (position_rooms.resolve_identity),
                                  so the joint outcome is EXACT -- mutually
                                  exclusive, identical, nested or overlapping.
  SAME_FIXTURE_LOGICALLY_LINKED_MARKETS
                                  one game, but a leg off the winner variable
                                  (a spread, a total, an unestablished
                                  identity): linked, and the joint needs the
                                  margin / total partition these rows do not
                                  carry. UNMEASURED = WORST CASE.
  SAME_TEAM_IN_THE_WINDOW         one team in two fixtures inside
                                  TEAM_WINDOW_S. The settled records do not
                                  say which side of each contract the shared
                                  team held, so the joint cannot be read from
                                  them: UNMEASURED = WORST CASE.
  SAME_TOURNAMENT_OR_COMPETITION_SAME_DAY
                                  one competition, one date, different
                                  fixtures. MEASURED from settled outcomes
                                  where the competition has enough same-day
                                  fixture pairs (|phi| with a 95% upper bound,
                                  on disjoint pairs), else UNMEASURED = WORST
                                  CASE.

TWO PORTFOLIO VIEWS, never one number:

  WORST CASE      every node loses its whole cost at once -- the treatment
                  the caps above assume (sum of costs), plus the comonotone
                  99% tail.
  EVIDENCED CASE  the loss that is FEASIBLE given the exact edges (hedged legs
                  of one fixture cannot both lose), and a 99% tail in which
                  exactly linked legs share one outcome, UNMEASURED links stay
                  comonotone, MEASURED links use a Frechet mixture (comonotone
                  with weight w = the measured dependence bound, independent
                  otherwise), and nodes with no shared settlement dependence
                  are independent.

AUTHORITY: NONE. The worst-case caps stay authoritative; this graph is shadow
information. Relaxing any cap is a later OWNER decision, recorded as such.
Read-only: plain SELECTs, no order, venue-submit, funded or paper-writer
import.
"""
from __future__ import annotations

import math
import random
import re
import time
from typing import Any

from . import allie_capital as AC
from . import position_rooms as PR

VERSION = "EVIDENCED_CORRELATION_GRAPH_V1"
AUTHORITY = "SHADOW_INFORMATION_ONLY_WORST_CASE_CAPS_STAY_AUTHORITATIVE"

K_POSITION, K_WORKING, K_CANDIDATE = "POSITION", "WORKING_ORDER", "CANDIDATE"

R_SAME_CONTRACT = "SAME_CONTRACT"
R_SAME_EVENT_WINNER = "SAME_FIXTURE_SAME_WINNER_VARIABLE"
R_SAME_EVENT_LINKED = "SAME_FIXTURE_LOGICALLY_LINKED_MARKETS"
R_SAME_TEAM = "SAME_TEAM_IN_THE_WINDOW"
R_SAME_COMPETITION = "SAME_TOURNAMENT_OR_COMPETITION_SAME_DAY"

D_EXACT = "EXACT_LOGICAL_DERIVATION"
D_MEASURED = "MEASURED_FROM_SETTLED_OUTCOMES"
D_UNMEASURED = "UNMEASURED_WORST_CASE"

#: Two fixtures of one team within this many seconds share an edge.
TEAM_WINDOW_S = 4 * 86400.0
#: Disjoint same-day fixture pairs a competition needs before its
#: dependence is MEASURED (below: UNMEASURED = worst case). Chosen, not
#: derived; stated so it can be argued with.
MIN_INDEPENDENT_PAIRS = 30
Z95 = 1.959963984540054
TAIL = 0.99
MC_SAMPLES = 4000
MC_SEED = 20261004
MAX_CANDIDATES_SCORED = 5
#: Edges LISTED in one payload (every edge is still counted and used): the
#: same-day pairs of a busy league grow with the square of the book.
MAX_EDGES_LISTED = 3000
HISTORY_LIMIT = 20000

#: The worst-case controls in force, restated by name (the paper ledger and
#: the funded rails are writer / execution modules this read-only module does
#: not import; a test pins each name to its source).
AUTHORITATIVE_CAPS = (
    {"control": "allie_capital.CORRELATION_HAIRCUT",
     "value": AC.CORRELATION_HAIRCUT,
     "treatment": ("profit per capital-hour haircut by this fraction PER OPEN "
                   "GROUP already on the fixture (capped at 100%)")},
    {"control": "allie_capital.FIXTURE_CAP_USD", "value": AC.FIXTURE_CAP_USD,
     "treatment": "open exposure on one fixture, summed at full cost"},
    {"control": "allie_capital.BOOK_CAP_USD", "value": AC.BOOK_CAP_USD,
     "treatment": "open exposure on the whole book, summed at full cost"},
    {"control": "bettor_paper_ledger.R_FIXTURE_OWNED",
     "value": "ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE",
     "treatment": ("an ENTRY refused when another strategy holds the fixture "
                   "(accounts outside the owner's main-account policy)")},
    {"control": "bettor_paper_ledger.R_SAME_CONTRACT_HELD",
     "value": "THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT",
     "treatment": "no re-entry of a held contract and side"},
    {"control": "bettor_paper_ledger.R_PER_FIXTURE",
     "value": "ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP",
     "treatment": "the session's per-fixture cap, at full cost"},
    {"control": "bettor_funded_execution MAX_CORRELATED_EXPOSURE",
     "value": "every live intent + this order + held reservations",
     "treatment": "the funded lane's worst-case correlation rail"},
)

_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _f(v, nd: int = 6):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def league_of(slug) -> str:
    parts = str(slug or "").split("-")
    return parts[1].strip().lower() if len(parts) > 1 else ""


def date_of(slug, game_start=None) -> str | None:
    m = _DATE.search(str(slug or ""))
    if m:
        return m.group(1)
    if game_start:
        try:
            import datetime as _d
            return _d.datetime.fromtimestamp(float(game_start),
                                             _d.timezone.utc).date().isoformat()
        except (TypeError, ValueError, OverflowError):
            return None
    return None


# ═════════════════════════════════════════════════════════════════════
# 1 · NODES (pure)
# ═════════════════════════════════════════════════════════════════════

def side_probability(val: dict | None, side: str) -> float | None:
    """P(the held side pays) from the latest valuation of its market: the
    stored probability is of the event the valuation's buy intent pays on
    (the lane's single complement inversion), so the other side is its
    complement."""
    if not val or val.get("probability") is None:
        return None
    p = float(val["probability"])
    want = "ORDER_INTENT_BUY_%s" % side
    return p if str(val.get("buy_intent")) == want else 1.0 - p


def build_nodes(raw: dict, premap: list, vals: dict, *, now: float) -> list:
    """The graph's nodes from position_rooms' paper read. Pure."""
    by_slug, by_event = {}, {}
    for r in premap or ():
        by_slug.setdefault(str(r.get("market_slug") or "").lower(),
                           []).append(r)
        if r.get("event_slug"):
            by_event.setdefault(r["event_slug"], []).append(r)
    ident_cache: dict = {}

    def ident(slug, side):
        k = (slug, side)
        if k not in ident_cache:
            ident_cache[k] = PR.resolve_identity(slug, side, by_slug, by_event)
        return ident_cache[k]

    def base(kind, ref, group, slug, side, qty, price, cost, strategy=None):
        idn = ident(slug, side)
        val = vals.get(slug)
        p = side_probability(val, side)
        rows = by_slug.get(str(slug).lower()) or []
        gs = PR._epoch(rows[0].get("game_start")) if rows else None
        teams = sorted({str(t.get("team_name") or "").strip().lower()
                        for t in by_event.get(idn.get("event_slug"), rows)
                        if t.get("team_name")}) if (
            idn.get("event_slug") or rows) else []
        return {"node_id": "%s:%s" % (kind, ref), "kind": kind, "ref": ref,
                "group_id": group, "slug": slug, "side": side,
                "strategy": strategy, "qty": _f(qty), "price": _f(price),
                "cost_usd": _f(cost),
                "max_payout_usd": _f(qty),
                "identity_status": idn.get("status"),
                "identity_reason": idn.get("reason"),
                "event_slug": idn.get("event_slug"),
                "sports_type": idn.get("sports_type"),
                "winner_variable": idn.get("status") == "ESTABLISHED",
                "pays_on": (idn.get("pays_on") if idn.get("status") ==
                            "ESTABLISHED" else
                            ["RESOLVES_YES"] if side == "LONG"
                            else ["RESOLVES_NO"]),
                "outcomes": (idn.get("outcomes") if idn.get("status") ==
                             "ESTABLISHED" else
                             ["RESOLVES_YES", "RESOLVES_NO"]),
                "teams": teams, "league": league_of(slug),
                "game_start": gs, "date": date_of(slug, gs),
                "p": None if p is None else _f(p),
                "p_source": ("LATEST_VALUATION_%s" % (
                    val.get("valuation_id") if val else "")) if p is not None
                else "MARKET_IMPLIED_AT_THE_NODE_PRICE",
                "p_used": _f(p if p is not None else price)}
    out = []
    fills_by: dict = {}
    for f in raw.get("fills") or ():
        fills_by.setdefault((f.get("group_id"), f["slug"], f["holding_side"]),
                            []).append(f)
    sett = {(s.get("group_id"), s["slug"], s["holding_side"]): s
            for s in raw.get("settlements") or ()}
    for k, fl in fills_by.items():
        h = PR.slice_from_fills(fl, sett.get(k))
        q = float(h.get("open_qty") or 0.0)
        if q <= 1e-9:
            continue
        out.append(base(K_POSITION, "%s/%s/%s" % k, k[0], k[1], k[2], q,
                        h.get("avg_cost_incl_fees"), h.get("cost_basis_usd")))
    for o in raw.get("orders") or ():
        if o.get("direction") != "BUY" or o.get("holding_side") not in (
                "LONG", "SHORT"):
            continue
        rem = float(o.get("qty") or 0) - float(o.get("filled_qty") or 0)
        lim = _f(o.get("limit"))
        if rem <= 1e-9 or lim is None:
            continue
        if o.get("state") == PR.S_PROPOSED:
            kind = K_CANDIDATE
        elif o.get("state") in PR.STANDING_STATES or o.get("state") in (
                PR.S_SUBMITTED, PR.S_UNKNOWN):
            kind = K_WORKING
        else:
            continue
        out.append(base(kind, o.get("order_ref"), o.get("group_id"),
                        o["slug"], o["holding_side"], rem, lim, rem * lim,
                        strategy=o.get("strategy")))
    return out


# ═════════════════════════════════════════════════════════════════════
# 2 · OUTCOME GROUPS AND EDGES (pure)
# ═════════════════════════════════════════════════════════════════════

def group_key(n: dict) -> str:
    """The EXACT component a node belongs to: its venue event under the
    winner variable, else its own market (whose YES / NO is the contract's
    definition)."""
    if n["winner_variable"] and n.get("event_slug"):
        return "EVT:%s" % n["event_slug"]
    return "MKT:%s" % n["slug"]


def _atoms(nodes: list) -> list:
    """The coarsest partition of the group's outcomes that every node's
    payout is constant on: outcomes grouped by which nodes pay on them."""
    outcomes = list(nodes[0].get("outcomes") or [])
    sig: dict = {}
    for o in outcomes:
        key = tuple(sorted(n["node_id"] for n in nodes
                           if o in (n.get("pays_on") or ())))
        sig.setdefault(key, []).append(o)
    return [{"outcomes": v, "paying_nodes": list(k)} for k, v in sig.items()]


def group_distribution(nodes: list) -> dict:
    """P(atom) for one exact group from its nodes' probabilities. An atom
    priced by a node paying exactly on it takes that node's p; one remaining
    atom takes the complement; more than one unpriced atom is UNAVAILABLE
    and its mass goes, conservatively, to the WORST atom."""
    atoms = _atoms(nodes)
    for a in atoms:
        a["prob"] = None
        for n in nodes:
            if sorted(n.get("pays_on") or ()) == sorted(a["outcomes"]) \
                    and n.get("p_used") is not None:
                a["prob"] = float(n["p_used"])
                a["prob_source"] = n["node_id"]
                break
    # a node paying on everything but one atom prices that atom too
    if any(a["prob"] is None for a in atoms):
        for n in nodes:
            pays = set(n.get("pays_on") or ())
            rest = [a for a in atoms if not set(a["outcomes"]) <= pays]
            if len(rest) == 1 and rest[0]["prob"] is None and \
                    n.get("p_used") is not None:
                rest[0]["prob"] = 1.0 - float(n["p_used"])
                rest[0]["prob_source"] = "COMPLEMENT_OF_%s" % n["node_id"]
    unknown = [a for a in atoms if a["prob"] is None]
    known = sum(a["prob"] for a in atoms if a["prob"] is not None)
    status = "PRICED"
    if len(unknown) == 1:
        unknown[0]["prob"] = max(0.0, 1.0 - known)
        unknown[0]["prob_source"] = "REMAINDER"
    elif len(unknown) > 1:
        status = "WORST_ATOM_TAKES_THE_UNPRICED_MASS"
        for a in unknown:
            a["prob"] = 0.0
    total = sum(a["prob"] for a in atoms)
    if total > 0 and abs(total - 1.0) > 1e-9 and status == "PRICED":
        for a in atoms:
            a["prob"] = a["prob"] / total
        status = "RENORMALISED_FROM_%s" % _f(total, 6)
    for a in atoms:
        a["pnl_usd"] = sum(
            (float(n["qty"] or 0) if n["node_id"] in a["paying_nodes"]
             else 0.0) - float(n["cost_usd"] or 0) for n in nodes)
    if len(unknown) > 1:
        worst = min(atoms, key=lambda a: a["pnl_usd"])
        worst["prob"] += max(0.0, 1.0 - known)
    return {"atoms": atoms, "status": status}


def exact_relation(a: dict, b: dict) -> dict:
    pa, pb = set(a.get("pays_on") or ()), set(b.get("pays_on") or ())
    allo = set(a.get("outcomes") or ())
    if pa == pb:
        rel = "IDENTICAL_PAYOUT"
    elif not (pa & pb):
        rel = ("COMPLEMENTARY" if (pa | pb) == allo
               else "MUTUALLY_EXCLUSIVE")
    elif pa <= pb or pb <= pa:
        rel = "NESTED"
    else:
        rel = "OVERLAPPING"
    return {"payout_relation": rel, "both_pay_on": sorted(pa & pb),
            "neither_pays_on": sorted(allo - (pa | pb))}


def phi_max(p1: float, p2: float) -> float:
    """The largest phi two Bernoulli margins admit (the comonotone joint)."""
    q1, q2 = 1.0 - p1, 1.0 - p2
    den = math.sqrt(max(p1 * q1 * p2 * q2, 0.0))
    if den <= 0:
        return 1.0
    return max(1e-9, min(1.0, (min(p1, p2) - p1 * p2) / den))


def measured_dependence(history: list) -> dict:
    """Per competition: |phi| between the LONG outcomes of two different
    fixtures settled on one date, with a 95% upper bound computed on
    DISJOINT pairs (each fixture in one pair: the repeated-fixture pairs are
    not independent). `history`: [{league, date, fixture, long_won}]."""
    by: dict = {}
    for h in history or ():
        if h.get("long_won") not in (0, 1) or not h.get("date"):
            continue
        by.setdefault(h["league"], {}).setdefault(h["date"], {}).setdefault(
            str(h["fixture"]), int(h["long_won"]))
    out = {}
    for lg, days in by.items():
        pairs = []
        for d, fx in sorted(days.items()):
            items = [v for _k, v in sorted(fx.items())]
            for i in range(0, len(items) - 1, 2):
                pairs.append((items[i], items[i + 1]))
        n = len(pairs)
        rec = {"league": lg, "disjoint_pairs": n,
               "min_pairs": MIN_INDEPENDENT_PAIRS,
               "dates": len(days),
               "fixtures": sum(len(v) for v in days.values())}
        if n < MIN_INDEPENDENT_PAIRS:
            out[lg] = dict(rec, status=D_UNMEASURED,
                           why=("%d disjoint same-day fixture pair(s); %d are "
                                "required before a dependence is measured"
                                % (n, MIN_INDEPENDENT_PAIRS)))
            continue
        a = sum(1 for x, y in pairs if x and y)
        b = sum(1 for x, y in pairs if x and not y)
        c = sum(1 for x, y in pairs if not x and y)
        d = n - a - b - c
        den = math.sqrt(max((a + b) * (c + d) * (a + c) * (b + d), 0))
        phi = 0.0 if den == 0 else (a * d - b * c) / den
        se = math.sqrt(max(1.0 - phi * phi, 0.0) / n)
        upper = min(1.0, abs(phi) + Z95 * se)
        out[lg] = dict(rec, status=D_MEASURED, phi=_f(phi, 6),
                       abs_phi_upper_95=_f(upper, 6), table=[a, b, c, d],
                       basis=("phi of the LONG outcomes of disjoint pairs of "
                              "different fixtures settled on one date; upper "
                              "bound |phi| + 1.96 x sqrt((1 - phi^2) / n)"))
    return out


def build_edges(nodes: list, dependence: dict) -> list:
    """Every pairwise edge of shared settlement dependence. Pure."""
    edges = []
    for i in range(len(nodes)):
        a = nodes[i]
        for j in range(i + 1, len(nodes)):
            b = nodes[j]
            e = None
            if a["slug"] == b["slug"]:
                e = {"relation": R_SAME_CONTRACT, "dependence": D_EXACT,
                     **exact_relation(a, b),
                     "basis": ("one venue market: a LONG pays when it "
                               "resolves YES, a SHORT when it resolves NO")}
            elif group_key(a) == group_key(b):
                e = {"relation": R_SAME_EVENT_WINNER, "dependence": D_EXACT,
                     **exact_relation(a, b),
                     "basis": ("both legs pay on outcomes of ONE winner "
                               "partition of the venue event "
                               "(position_rooms.resolve_identity)")}
            elif a.get("event_slug") and a.get("event_slug") == \
                    b.get("event_slug"):
                e = {"relation": R_SAME_EVENT_LINKED,
                     "dependence": D_UNMEASURED,
                     "why": ("one game, a leg off the established winner "
                             "variable (%s / %s): the joint needs the margin "
                             "or total partition these rows do not carry"
                             % (a.get("identity_reason") or "winner",
                                b.get("identity_reason") or "winner"))}
            else:
                shared = sorted(set(a.get("teams") or ())
                                & set(b.get("teams") or ()))
                close = (a.get("game_start") is not None
                         and b.get("game_start") is not None
                         and abs(a["game_start"] - b["game_start"])
                         <= TEAM_WINDOW_S)
                if shared and close:
                    e = {"relation": R_SAME_TEAM, "dependence": D_UNMEASURED,
                         "shared_teams": shared,
                         "why": ("one team in two fixtures inside %d h: the "
                                 "settled records do not say which side of "
                                 "each contract the team held, so the joint "
                                 "cannot be read from them"
                                 % int(TEAM_WINDOW_S / 3600))}
                elif (a.get("league") and a["league"] == b.get("league")
                      and a.get("date") and a["date"] == b.get("date")):
                    m = dependence.get(a["league"]) or {}
                    if m.get("status") == D_MEASURED:
                        pm = phi_max(float(a["p_used"] or 0.5),
                                     float(b["p_used"] or 0.5))
                        w = min(1.0, float(m["abs_phi_upper_95"]) / pm)
                        e = {"relation": R_SAME_COMPETITION,
                             "dependence": D_MEASURED,
                             "abs_phi_upper_95": m["abs_phi_upper_95"],
                             "phi": m["phi"],
                             "disjoint_pairs": m["disjoint_pairs"],
                             "comonotone_weight": _f(w, 6),
                             "basis": ("Frechet mixture weight w = |phi| "
                                       "upper bound / the largest phi these "
                                       "margins admit (%.4f)" % pm)}
                    else:
                        e = {"relation": R_SAME_COMPETITION,
                             "dependence": D_UNMEASURED,
                             "why": m.get("why") or (
                                 "no settled same-day fixture pairs of %s"
                                 % a["league"])}
            if e is not None:
                edges.append(dict(e, a=a["node_id"], b=b["node_id"]))
    return edges


# ═════════════════════════════════════════════════════════════════════
# 3 · THE PORTFOLIO (pure)
# ═════════════════════════════════════════════════════════════════════

class _UF:
    def __init__(self, keys):
        self.p = {k: k for k in keys}

    def find(self, k):
        while self.p[k] != k:
            self.p[k] = self.p[self.p[k]]
            k = self.p[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _tail_of(atoms: list, alpha: float) -> dict:
    """VaR and expected shortfall of ONE group's loss at `alpha`."""
    srt = sorted(atoms, key=lambda a: a["pnl_usd"])        # worst first
    mass, need, acc = 0.0, 1.0 - alpha, 0.0
    var = None
    for a in srt:
        take = min(a["prob"], max(0.0, need - mass))
        if take > 0:
            acc += take * (-a["pnl_usd"])
            mass += take
        if var is None and mass >= need - 1e-12:
            var = -a["pnl_usd"]
    if var is None and srt:
        var = -srt[-1]["pnl_usd"]
    return {"var": var or 0.0, "es": (acc / need) if need > 0 else 0.0}


def portfolio(nodes: list, edges: list, *, samples: int = MC_SAMPLES,
              seed: int = MC_SEED) -> dict:
    """WORST CASE and EVIDENCED CASE for one set of nodes. Pure."""
    if not nodes:
        return {"nodes": 0, "worst_case": {"sum_of_costs_usd": 0.0,
                                           "tail_99_es_usd": 0.0,
                                           "tail_99_var_usd": 0.0},
                "evidenced_case": {"max_feasible_loss_usd": 0.0,
                                   "tail_99_es_usd": 0.0,
                                   "tail_99_var_usd": 0.0},
                "expected_pnl_usd": 0.0, "groups": []}
    ids = {n["node_id"] for n in nodes}
    by_group: dict = {}
    for n in nodes:
        by_group.setdefault(group_key(n), []).append(n)
    groups = {}
    for gk, ns in by_group.items():
        dist = group_distribution(ns)
        groups[gk] = {"key": gk, "nodes": [n["node_id"] for n in ns],
                      "atoms": dist["atoms"], "prob_status": dist["status"]}
    gof = {n["node_id"]: group_key(n) for n in nodes}
    # COUPLING CLUSTERS over the non-exact edges between groups
    uf = _UF(list(groups))
    weight: dict = {}
    for e in edges:
        if e["a"] not in ids or e["b"] not in ids:
            continue
        ga, gb = gof[e["a"]], gof[e["b"]]
        if ga == gb:
            continue
        uf.union(ga, gb)
        # an UNMEASURED link is comonotone; so, conservatively, is an exact
        # link that crosses two outcome groups (one market whose two sides
        # resolved to different identities): its partitions are not one
        w = (float(e.get("comonotone_weight") or 0.0)
             if e["dependence"] == D_MEASURED else 1.0)
        weight[(min(ga, gb), max(ga, gb))] = max(
            weight.get((min(ga, gb), max(ga, gb)), 0.0), w)
    clusters: dict = {}
    for gk in groups:
        clusters.setdefault(uf.find(gk), []).append(gk)
    cw = {}
    for root, members in clusters.items():
        ws = [w for (x, y), w in weight.items() if uf.find(x) == root]
        cw[root] = max(ws) if ws else 0.0
    # WORST CASE, THE CAPS' TREATMENT: no credit for any link, exact or
    # measured. Every node's whole cost is summed, and the tail couples every
    # NODE comonotone (each pays qty with its own p, else loses its cost) --
    # VaR and ES are additive for comonotone losses, so it is exact.
    wc_sum = sum(float(n["cost_usd"] or 0) for n in nodes)
    wc_es = wc_var = 0.0
    for n in nodes:
        pu = min(1.0, max(0.0, float(n.get("p_used") or 0.0)))
        cost, qty = float(n["cost_usd"] or 0), float(n["qty"] or 0)
        t = _tail_of([{"prob": 1.0 - pu, "pnl_usd": -cost},
                      {"prob": pu, "pnl_usd": qty - cost}], TAIL)
        wc_es += t["es"]
        wc_var += t["var"]
    # EVIDENCED: the feasible maximum loss (the exact edges' outcomes)
    max_loss = sum(max(0.0, -min(a["pnl_usd"] for a in g["atoms"]))
                   for g in groups.values())
    exp_pnl = sum(sum(a["prob"] * a["pnl_usd"] for a in g["atoms"])
                  for g in groups.values())
    # and the 99% tail by simulation (seeded, so a re-read reproduces it)
    import bisect
    rng = random.Random(seed)
    order = sorted(groups)
    cum = {}
    for gk in order:
        # WORST FIRST, so a shared uniform aligns the groups' bad outcomes
        srt = sorted(groups[gk]["atoms"], key=lambda a: a["pnl_usd"])
        cs, ps, acc = [], [], 0.0
        for a in srt:
            acc += a["prob"]
            cs.append(acc)
            ps.append(a["pnl_usd"])
        cum[gk] = (cs, ps)

    def draw(gk, u):
        cs, ps = cum[gk]
        i = bisect.bisect_left(cs, u - 1e-15)
        return ps[min(i, len(ps) - 1)]
    losses = []
    roots = sorted(clusters)
    for _ in range(int(samples)):
        total = 0.0
        for root in roots:
            uc = rng.random()
            w = cw[root]
            for gk in sorted(clusters[root]):
                u = uc if (w >= 1.0 or rng.random() < w) else rng.random()
                total += draw(gk, u)
        losses.append(-total)
    losses.sort(reverse=True)
    k = max(1, int(math.ceil((1.0 - TAIL) * len(losses))))
    ev_es = sum(losses[:k]) / k
    ev_var = losses[k - 1]
    return {
        "nodes": len(nodes), "groups": [
            {"key": g["key"], "nodes": g["nodes"],
             "prob_status": g["prob_status"],
             "atoms": [{"outcomes": a["outcomes"], "prob": _f(a["prob"]),
                        "pnl_usd": _f(a["pnl_usd"], 4)} for a in g["atoms"]],
             "worst_feasible_loss_usd": _f(max(0.0, -min(
                 a["pnl_usd"] for a in g["atoms"])), 4),
             "sum_of_costs_usd": _f(sum(float(n["cost_usd"] or 0)
                                        for n in nodes
                                        if gof[n["node_id"]] == g["key"]), 4)}
            for g in (groups[x] for x in order)],
        "coupling_clusters": [
            {"groups": sorted(m), "comonotone_weight": _f(cw[r], 6)}
            for r, m in sorted(clusters.items()) if len(m) > 1],
        "worst_case": {
            "sum_of_costs_usd": _f(wc_sum, 4),
            "tail_99_var_usd": _f(wc_var, 4),
            "tail_99_es_usd": _f(wc_es, 4),
            "basis": ("every node loses its whole cost at once (the caps' "
                      "treatment); the tail couples every NODE comonotone, "
                      "with no credit for any link")},
        "evidenced_case": {
            "max_feasible_loss_usd": _f(max_loss, 4),
            "tail_99_var_usd": _f(ev_var, 4),
            "tail_99_es_usd": _f(ev_es, 4),
            "samples": int(samples), "seed": seed,
            "basis": ("exact links share one outcome; UNMEASURED links stay "
                      "comonotone; MEASURED links are a Frechet mixture; "
                      "nodes with no shared settlement dependence are "
                      "independent")},
        "expected_pnl_usd": _f(exp_pnl, 4),
        "pnl_is": ("completed-game settlement P&L from here (held quantity "
                   "paid $1 on its outcomes, less its cost); exceptional "
                   "settlement states are the settlement-exception table's")}


def graph(nodes: list, dependence: dict, *, now: float | None = None,
          samples: int = MC_SAMPLES) -> dict:
    """The whole graph: nodes, edges, both portfolio views for the HELD book
    and for the book with its working orders and candidates, and each
    candidate's shadow comparison. Pure."""
    edges = build_edges(nodes, dependence)
    held = [n for n in nodes if n["kind"] == K_POSITION]
    committed = [n for n in nodes if n["kind"] in (K_POSITION, K_WORKING)]
    out_cands = []
    committed_ids = {x["node_id"] for x in committed}
    base = portfolio(committed, edges, samples=samples)
    for c in [n for n in nodes if n["kind"] == K_CANDIDATE][
            :MAX_CANDIDATES_SCORED]:
        withc = portfolio(committed + [c], edges, samples=samples)
        same_fx = sorted({x["group_id"] for x in committed
                          if x.get("event_slug") and
                          x.get("event_slug") == c.get("event_slug")
                          and x.get("group_id")})
        hair = min(1.0, AC.CORRELATION_HAIRCUT * len(same_fx))
        links = [e for e in edges if c["node_id"] in (e["a"], e["b"])]
        out_cands.append({
            "node_id": c["node_id"], "slug": c["slug"], "side": c["side"],
            "edges_to_the_book": [
                {"to": other, "relation": e["relation"],
                 "dependence": e["dependence"],
                 "payout_relation": e.get("payout_relation")}
                for e, other in ((e, e["b"] if e["a"] == c["node_id"]
                                  else e["a"]) for e in links)
                if other in committed_ids],
            "current_treatment": {
                "allie_haircut": _f(hair, 4),
                "open_groups_on_the_fixture": len(same_fx),
                "fixture_cost_counted_usd": _f(sum(
                    float(x["cost_usd"] or 0) for x in committed
                    if x.get("event_slug") and
                    x.get("event_slug") == c.get("event_slug")) + float(
                        c["cost_usd"] or 0), 4),
                "status": "AUTHORITATIVE_UNCHANGED"},
            "shadow_marginal": {
                "worst_case_tail_99_es_usd": _f(
                    withc["worst_case"]["tail_99_es_usd"]
                    - base["worst_case"]["tail_99_es_usd"], 4),
                "evidenced_tail_99_es_usd": _f(
                    withc["evidenced_case"]["tail_99_es_usd"]
                    - base["evidenced_case"]["tail_99_es_usd"], 4),
                "worst_case_sum_of_costs_usd": _f(c["cost_usd"], 4),
                "evidenced_max_feasible_loss_usd": _f(
                    withc["evidenced_case"]["max_feasible_loss_usd"]
                    - base["evidenced_case"]["max_feasible_loss_usd"], 4),
                "status": "SHADOW_INFORMATION_ONLY"}})
    counts: dict = {}
    for e in edges:
        k = "%s|%s" % (e["relation"], e["dependence"])
        counts[k] = counts.get(k, 0) + 1
    return {
        "version": VERSION, "as_of": now, "authority": AUTHORITY,
        "nodes": nodes, "edges": edges[:MAX_EDGES_LISTED],
        "edges_total": len(edges),
        "edges_listed_truncated": len(edges) > MAX_EDGES_LISTED,
        "edge_counts": counts,
        "dependence_by_competition": dependence,
        "portfolio": {
            "held": portfolio(held, edges, samples=samples),
            "held_plus_working_orders": base,
            "held_plus_working_orders_and_candidates": portfolio(
                nodes, edges, samples=samples)},
        "candidates": out_cands,
        "authoritative_caps": [dict(c, status="AUTHORITATIVE_UNCHANGED")
                               for c in AUTHORITATIVE_CAPS],
        "relaxation": {
            "applied": False,
            "requires": ("a later OWNER decision, recorded as such; the "
                         "evidenced case is shadow information and changes "
                         "no haircut, cap, refusal or size")},
        "unknown_is_worst_case": True}


# ═════════════════════════════════════════════════════════════════════
# 4 · THE READ (plain SELECTs, through position_rooms' loaders)
# ═════════════════════════════════════════════════════════════════════

VALUATIONS_SQL = """
    SELECT DISTINCT ON (us_market_slug) us_market_slug, id AS valuation_id,
           probability, buy_intent,
           extract(epoch FROM decided_at)::float8 AS decided_at
      FROM external_valuations
     WHERE us_market_slug = ANY($1::text[]) AND probability IS NOT NULL
     ORDER BY us_market_slug, decided_at DESC
"""

HISTORY_SQL = """
    SELECT DISTINCT ON (us_market_slug) us_market_slug,
           coalesce(event_key, us_market_slug) AS fixture,
           CASE WHEN buy_intent = 'ORDER_INTENT_BUY_LONG' THEN outcome
                WHEN buy_intent = 'ORDER_INTENT_BUY_SHORT' THEN 1 - outcome
           END AS long_won
      FROM external_valuations
     WHERE experiment_id = $1 AND outcome_known AND outcome IN (0, 1)
       AND us_market_slug IS NOT NULL
     ORDER BY us_market_slug, id DESC
     LIMIT $2
"""


async def load(conn, *, now: float | None = None,
               account_id: str | None = None,
               samples: int = MC_SAMPLES) -> dict:
    """The graph of the PAPER book (ACTUAL is SHADOW: SMALL LIVE holds no
    exposure). Never raises a partial graph as complete: a failed read is a
    named refusal."""
    from . import settlement_exception_risk as SER
    at = float(now if now is not None else time.time())
    acct = account_id or PR.PAPER_ACCOUNT_ID
    raw = await PR._paper(conn, at, acct)
    if not raw.get("available"):
        return {"version": VERSION, "ok": False,
                "refusal": raw.get("why") or "PAPER_BOOK_UNAVAILABLE",
                "authority": AUTHORITY}
    slugs = sorted({x["slug"] for x in (raw.get("fills") or [])
                    + (raw.get("orders") or []) if x.get("slug")})
    premap = await PR._premap(conn, slugs)
    vals = {}
    if slugs:
        for r in await conn.fetch(VALUATIONS_SQL, slugs):
            vals[r["us_market_slug"]] = dict(r)
    hist = []
    for r in await conn.fetch(HISTORY_SQL, SER.EXPERIMENT_ID, HISTORY_LIMIT):
        hist.append({"league": league_of(r["us_market_slug"]),
                     "date": date_of(r["us_market_slug"]),
                     "fixture": r["fixture"],
                     "long_won": (None if r["long_won"] is None
                                  else int(r["long_won"]))})
    nodes = build_nodes(raw, premap, vals, now=at)
    dep = measured_dependence(hist)
    g = graph(nodes, dep, now=at, samples=samples)
    g.update(ok=True, refusal=None, account_id=acct,
             source={"paper_read": "position_rooms._paper",
                     "identity": "position_rooms.resolve_identity (us_premap)",
                     "probabilities": "external_valuations (latest per market)",
                     "settled_history_markets": len(hist)})
    return g


def describe() -> dict:
    return {"version": VERSION, "relations": [
        R_SAME_CONTRACT, R_SAME_EVENT_WINNER, R_SAME_EVENT_LINKED,
        R_SAME_TEAM, R_SAME_COMPETITION],
        "dependence_classes": [D_EXACT, D_MEASURED, D_UNMEASURED],
        "team_window_s": TEAM_WINDOW_S,
        "min_independent_pairs": MIN_INDEPENDENT_PAIRS,
        "authoritative_caps": [c["control"] for c in AUTHORITATIVE_CAPS],
        "authority": AUTHORITY}
