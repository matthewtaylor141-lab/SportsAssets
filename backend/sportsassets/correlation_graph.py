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
  bettor_entry_execution   PREDECLARED_LIMITS MAX_EVENT_EXPOSURE and
                           MAX_CORRELATED_EXPOSURE (one standard trade,
                           "every open directional position is perfectly
                           correlated with the proposed one"), measured by
                           bettor_funded_execution

They treat every pair of exposures that share anything as if they move
together -- which also counts the two legs of a HEDGE (YES Yankees and YES
Red Sox on one game) as doubly risky when they cannot both lose. The audit:
"safe but capacity-destructive. Build event/settlement-based correlation
evidence before relaxing it."

WHAT THIS BUILDS. Nodes are the PAPER book's open positions (filled, not
settled), its working buy orders and its CANDIDATE opportunities: Derek ENTER
decisions with no order yet, AND the opportunities the worst-case caps
refused -- REFUSE decisions whose only refusals are correlation /
concentration caps, and ENTERs whose order a cap refused (the capacity the
spec asks to evidence; review, R30C: before this, a cap-refused opportunity
could never become a node). Identities come from the venue's catalogue rows
(us_premap via position_rooms.resolve_identity); where a market has NO
catalogue row the node falls back to its RECORDED fixture (paper_orders /
paper_decisions .fixture, external_valuations.event_key) and to the fixture,
teams and date its venue slug names -- and an unidentified node is linked
UNMEASURED = WORST CASE to every node it could share settlement with (same
league, inside the window), never treated as independent.

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
                                  one game (the catalogue's event, the
                                  recorded fixture, or the fixture the slug
                                  names), but a leg off the winner variable
                                  (a spread, a total, an unestablished
                                  identity): linked, and the joint needs the
                                  margin / total partition these rows do not
                                  carry. UNMEASURED = WORST CASE.
  SAME_TEAM_IN_THE_WINDOW         one team in two fixtures inside
                                  TEAM_WINDOW_S. The settled records do not
                                  say which side of each contract the shared
                                  team held, so the joint cannot be read from
                                  them: UNMEASURED = WORST CASE.
  IDENTITY_NOT_ESTABLISHED_SAME_COMPETITION_IN_THE_WINDOW
                                  at least one leg has no catalogue identity
                                  (event, teams, start): whether the two
                                  share settlement cannot be ruled out.
                                  UNMEASURED = WORST CASE.
  SAME_TOURNAMENT_OR_COMPETITION_SAME_DAY
                                  one competition, one date, different
                                  fixtures, both identified. MEASURED from
                                  settled outcomes where the competition has
                                  enough same-day fixture pairs (|phi| with a
                                  95% upper bound, on disjoint pairs), else
                                  UNMEASURED = WORST CASE.

TWO PORTFOLIO VIEWS, never one number:

  WORST CASE      every node loses its whole cost at once -- the treatment
                  the caps above assume (sum of costs), plus the comonotone
                  99% tail.
  EVIDENCED CASE  the loss that is FEASIBLE given the exact edges (hedged legs
                  of one fixture cannot both lose), and a 99% tail in which
                  exactly linked legs share one outcome, UNMEASURED links stay
                  comonotone, MEASURED links use a Frechet mixture (with
                  probability w -- the measured dependence bound -- the whole
                  coupling cluster shares ONE uniform, otherwise each group
                  draws its own), and nodes with no shared settlement
                  dependence are independent. The simulation uses COMMON
                  RANDOM NUMBERS keyed per outcome group, so adding a
                  candidate does not reshuffle any other group's draws, and
                  each marginal carries its Monte Carlo standard error.

AUTHORITY: NONE. The worst-case caps stay authoritative; this graph is shadow
information. Relaxing any cap is a later OWNER decision, recorded as such.
Read-only: plain SELECTs, no order, venue-submit, funded or paper-writer
import.
"""
from __future__ import annotations

import bisect
import heapq
import math
import operator
import random
import re
import time

from . import allie_capital as AC
from . import position_rooms as PR

VERSION = "EVIDENCED_CORRELATION_GRAPH_V2"
AUTHORITY = "SHADOW_INFORMATION_ONLY_WORST_CASE_CAPS_STAY_AUTHORITATIVE"

K_POSITION, K_WORKING, K_CANDIDATE = "POSITION", "WORKING_ORDER", "CANDIDATE"

R_SAME_CONTRACT = "SAME_CONTRACT"
R_SAME_EVENT_WINNER = "SAME_FIXTURE_SAME_WINNER_VARIABLE"
R_SAME_EVENT_LINKED = "SAME_FIXTURE_LOGICALLY_LINKED_MARKETS"
R_SAME_TEAM = "SAME_TEAM_IN_THE_WINDOW"
R_IDENTITY_UNKNOWN = "IDENTITY_NOT_ESTABLISHED_SAME_COMPETITION_IN_THE_WINDOW"
R_SAME_COMPETITION = "SAME_TOURNAMENT_OR_COMPETITION_SAME_DAY"

D_EXACT = "EXACT_LOGICAL_DERIVATION"
D_MEASURED = "MEASURED_FROM_SETTLED_OUTCOMES"
D_UNMEASURED = "UNMEASURED_WORST_CASE"

# candidate sources
C_ENTER_NOT_ORDERED = "ENTER_DECISION_NOT_YET_ORDERED"
C_CAP_REFUSED = "REFUSED_BY_A_CORRELATION_OR_CONCENTRATION_CAP"
C_ORDER_CAP_REFUSED = "ENTER_WHOSE_ORDER_A_CAP_REFUSED"

#: Two fixtures of one team within this many seconds share an edge.
TEAM_WINDOW_S = 4 * 86400.0
#: A slug's date covers a whole (UTC) day: a window measured from dates
#: rather than game starts is widened by one day, never narrowed.
DATE_SLACK_S = 86400.0
#: Disjoint same-day fixture pairs a competition needs before its
#: dependence is MEASURED (below: UNMEASURED = worst case). Chosen, not
#: derived; stated so it can be argued with.
MIN_INDEPENDENT_PAIRS = 30
Z95 = 1.959963984540054
TAIL = 0.99
MC_SAMPLES = 4000
MC_MIN_SAMPLES = 1000
#: Group-draws one portfolio simulation may spend: the sample count falls
#: (never below MC_MIN_SAMPLES) as the book grows, and the payload says so.
MC_GROUP_DRAW_BUDGET = 600_000
MC_SEED = 20261004
#: Paired bootstrap resamples behind each Monte Carlo standard error.
MC_BOOTSTRAP = 20
MAX_CANDIDATES_SCORED = 8
#: Wall-clock seconds the candidate scoring may take; candidates past it are
#: listed with their current treatment, NOT_SCORED with the reason.
COMPUTE_BUDGET_S = 6.0
#: Nodes one graph carries (the read is bounded the same way): committed
#: (positions + working orders), then candidates. A truncation is disclosed
#: and marks the portfolio views incomplete.
MAX_COMMITTED_NODES = 600
MAX_CANDIDATE_NODES = 50
#: Edges LISTED in one payload (every edge is still counted and used): the
#: same-day pairs of a busy league grow with the square of the book.
MAX_EDGES_LISTED = 3000
HISTORY_LIMIT = 20000
#: How far back a cap refusal still names a candidate (chosen: a refused
#: valuation recurs about hourly per market in production -- 196 refusals on
#: 9 markets in 24 h -- so a shorter window would usually show none).
CAP_REFUSAL_WINDOW_S = 6 * 3600.0
MAX_CAP_CANDIDATES_READ = 200

#: THE CORRELATION / CONCENTRATION CAPS whose refusals name a candidate
#: (bettor_paper_ledger's R_FIXTURE_OWNED, R_SAME_STRATEGY_LIVE,
#: R_SAME_CONTRACT_HELD, R_PER_FIXTURE, R_PER_MARKET; restated -- the ledger
#: is a writer module this read-only module does not import -- and pinned to
#: the ledger by a test). Cash, per-order and group-count refusals are not
#: correlation treatments and name no candidate.
CORRELATION_CAP_REFUSALS = (
    "ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE",
    "THIS_STRATEGY_ALREADY_HAS_A_LIVE_ENTRY_ON_THIS_FIXTURE",
    "THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT",
    "ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP",
    "ABOVE_THE_PER_MARKET_CONCENTRATION_CAP",
)
#: The finding kind the paper decision writers record when the ledger
#: refuses an ENTER's order (agents/paper_derek.R_ORDER_REFUSED, which
#: paper_benchmark re-exports; pinned by a test).
ORDER_REFUSED_FINDING = "PAPER_RISK_REFUSED_THE_ORDER"

#: THE OWNER CAPITAL POLICY'S ENTRY BUDGET (bettor_paper_limits ACCOUNT_ID /
#: ENTRY_USD and effective_config; restated and pinned by a test). A cap
#: refusal is recorded before the book is read, so the decision carries no
#: size: its node is sized at the strategy's order budget, which
#: size_within_edge never exceeds (qty x (limit + max fee) <= min(target,
#: per-order cap)) -- an UPPER bound on the order the cap prevented.
OWNER_POLICY_ACCOUNT = "paper_acct_main"
OWNER_POLICY_ENTRY_USD = 1000.0

#: ALLIE'S FIXTURE INPUT, VERBATIM (canonical_components.allie_at_decision;
#: a test pins the two texts equal). The graph's "current treatment" of a
#: candidate is THIS query's answer fed through allie_capital's own haircut
#: rule -- or, where the candidate's canonical intent exists, the allie
#: component it recorded -- never a re-derivation that counts differently
#: (review, R30C: the V1 graph counted unfilled working orders and matched on
#: the catalogue event, which Allie does not).
ALLIE_FIXTURE_SQL = """SELECT count(DISTINCT o.group_id) AS n,
                      coalesce(sum(o.filled_qty * o.limit_price), 0) AS usd
                 FROM paper_orders o
                WHERE o.role = 'ENTRY' AND o.fixture = $1 AND o.filled_qty > 0
                  AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                                   WHERE s.group_id = o.group_id)"""

#: The worst-case controls in force, restated by name (the paper ledger and
#: the funded rails are writer / execution modules this read-only module does
#: not import; tests pin every entry to its source -- by import for the pure
#: and paper modules, by the source text for the execution modules).
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
    {"control": ("bettor_entry_execution.PREDECLARED_LIMITS"
                 "['MAX_CORRELATED_EXPOSURE']"),
     "value": "STANDARD x LIMIT_BASIS['MAX_CORRELATED_EXPOSURE'][0] (= 1)",
     "treatment": ("one standard trade of worst-case correlated exposure: "
                   "every open directional position is treated as perfectly "
                   "correlated with the proposed one "
                   "(WORST_CASE_CORRELATION_ASSUMPTION)")},
    {"control": ("bettor_funded_execution measured"
                 "['MAX_CORRELATED_EXPOSURE']"),
     "value": ("every live intent in the lane + this order + every HELD "
               "reservation"),
     "treatment": ("the funded lane's measurement of that rail, under the "
                   "worst-case correlation assumption")},
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


def _date_epoch(d) -> float | None:
    if not d:
        return None
    try:
        import datetime as _d
        return _d.datetime.strptime(str(d), "%Y-%m-%d").replace(
            tzinfo=_d.timezone.utc).timestamp()
    except ValueError:
        return None


def slug_identity(slug) -> dict:
    """WHAT THE VENUE SLUG ITSELF NAMES: <type>-<league>-<team>...-<date>
    [-<suffix>]. The fixture is the league, teams and date
    ('mlb-bos-nyy-2026-10-04': the venue's own event-slug convention, so it
    is comparable to us_premap.event_slug), the teams are the tokens between
    league and date. Without a date nothing but the league is read."""
    parts = [p for p in str(slug or "").strip().lower().split("-") if p != ""]
    out = {"league": parts[1] if len(parts) > 1 else "", "teams": [],
           "fixture": None, "date": None}
    for i in range(2, len(parts) - 2):
        if (len(parts[i]) == 4 and parts[i].isdigit()
                and len(parts[i + 1]) == 2 and parts[i + 1].isdigit()
                and len(parts[i + 2]) == 2 and parts[i + 2].isdigit()):
            out["date"] = "-".join(parts[i:i + 3])
            out["teams"] = parts[2:i]
            out["fixture"] = "-".join(parts[1:i + 3])
            break
    return out


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


def order_budget_usd(config: dict | None, account_id) -> float | None:
    """The strategy's per-order budget under the account's EFFECTIVE config
    (bettor_paper_limits.effective_config, restated): size_within_edge caps
    qty x (limit + max fee) at min(target_order_usd, per_order_cap_usd)."""
    cfg = config if isinstance(config, dict) else {}
    if account_id == OWNER_POLICY_ACCOUNT:
        return OWNER_POLICY_ENTRY_USD
    tgt = _f((cfg.get("entry") or {}).get("target_order_usd"))
    cap = _f((cfg.get("risk") or {}).get("per_order_cap_usd"))
    if tgt is None:
        return None
    return tgt if cap is None else min(tgt, cap)


def build_nodes(raw: dict, premap: list, vals: dict, *, now: float,
                fixtures: dict | None = None,
                candidates: list | None = None) -> list:
    """The graph's nodes from position_rooms' paper read, the recorded
    fixtures ({(group_id, slug): fixture}) and the cap-refused candidates.
    Pure."""
    fixtures = fixtures or {}
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

    def base(kind, ref, group, slug, side, qty, price, cost, strategy=None,
             fixture=None, extra=None):
        idn = ident(slug, side)
        val = vals.get(slug)
        p = side_probability(val, side)
        rows = by_slug.get(str(slug).lower()) or []
        gs = PR._epoch(rows[0].get("game_start")) if rows else None
        ev_rows = by_event.get(idn.get("event_slug"), rows) if (
            idn.get("event_slug") or rows) else []
        teams = sorted({str(t.get("team_name") or "").strip().lower()
                        for t in ev_rows if t.get("team_name")})
        abbrs = sorted({str(t.get("team_abbr") or "").strip().lower()
                        for t in ev_rows if t.get("team_abbr")})
        si = slug_identity(slug)
        book_ev = (val or {}).get("event_key")
        complete = bool(idn.get("event_slug") and teams and gs is not None)
        d = date_of(slug, gs)
        if gs is not None:
            win_at, win_basis = gs, "CATALOGUE_GAME_START"
        else:
            win_at = _date_epoch(si["date"] or d)
            win_basis = "VENUE_SLUG_DATE" if win_at is not None else None
        n = {"node_id": "%s:%s" % (kind, ref), "kind": kind, "ref": ref,
             "group_id": group, "slug": slug, "side": side,
             "strategy": strategy, "qty": _f(qty), "price": _f(price),
             "cost_usd": _f(cost),
             "max_payout_usd": _f(qty),
             "identity_status": idn.get("status"),
             "identity_reason": idn.get("reason"),
             "identity_complete": complete,
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
             "teams": teams, "team_abbrs": abbrs, "league": league_of(slug),
             "slug_teams": si["teams"], "fixture_from_slug": si["fixture"],
             # the paper writer's fixture (derek_policy.fixture_of: the
             # venue condition) and the book's event id of the latest
             # valuation -- two namespaces, kept apart
             "fixture_recorded": (None if fixture in (None, "")
                                  else str(fixture)),
             "book_event_key": (None if book_ev in (None, "")
                                else str(book_ev)),
             "game_start": gs, "date": d,
             "window_at": win_at, "window_basis": win_basis,
             "p": None if p is None else _f(p),
             "p_source": ("LATEST_VALUATION_%s" % (
                 val.get("valuation_id") if val else "")) if p is not None
             else "MARKET_IMPLIED_AT_THE_NODE_PRICE",
             "p_used": _f(p if p is not None else price)}
        if extra:
            n.update(extra)
        return n
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
                        h.get("avg_cost_incl_fees"), h.get("cost_basis_usd"),
                        fixture=fixtures.get((k[0], k[1]))))
    for o in raw.get("orders") or ():
        if o.get("direction") != "BUY" or o.get("holding_side") not in (
                "LONG", "SHORT"):
            continue
        rem = float(o.get("qty") or 0) - float(o.get("filled_qty") or 0)
        lim = _f(o.get("limit"))
        if rem <= 1e-9 or lim is None:
            continue
        extra = None
        if o.get("state") == PR.S_PROPOSED:
            kind = K_CANDIDATE
            extra = {"candidate_source": C_ENTER_NOT_ORDERED,
                     "decision_id": o.get("decision_id"),
                     "current_refusals": [],
                     "size_basis": "THE_DECISION'S_OWN_PROPOSED_QTY_AND_LIMIT"}
        elif o.get("state") in PR.STANDING_STATES or o.get("state") in (
                PR.S_SUBMITTED, PR.S_UNKNOWN):
            kind = K_WORKING
        else:
            continue
        out.append(base(kind, o.get("order_ref"), o.get("group_id"),
                        o["slug"], o["holding_side"], rem, lim, rem * lim,
                        strategy=o.get("strategy"),
                        fixture=(o.get("fixture") or fixtures.get(
                            (o.get("group_id"), o["slug"]))),
                        extra=extra))
    for c in candidates or ():
        side = c.get("holding_side")
        if side not in ("LONG", "SHORT") or not c.get("slug"):
            continue
        val = vals.get(c["slug"])
        q, lim = _f(c.get("proposed_qty")), _f(c.get("limit_price"))
        extra = {"candidate_source": c.get("source"),
                 "decision_id": c.get("decision_id"),
                 "current_refusals": list(c.get("refusals") or ()),
                 "refusals_in_window": c.get("refusals_in_window"),
                 "decided_at": c.get("decided_at"),
                 "admission_without_the_cap": c.get("admission_without_cap")}
        if q and q > 0 and lim and 0 < lim < 1:
            extra["size_basis"] = "THE_DECISION'S_OWN_PROPOSED_QTY_AND_LIMIT"
        else:
            budget = _f(c.get("budget_usd"))
            p = side_probability(val, side)
            px = p if p is not None else _f(c.get("p_pinnacle"))
            if budget is None or px is None or not (0 < px < 1):
                extra.update(size_basis="SIZE_NOT_ESTABLISHED",
                             size_unavailable_because=(
                                 "NO_ORDER_BUDGET_IN_THE_SESSION_CONFIG"
                                 if budget is None else
                                 "NO_PROBABILITY_TO_PRICE_THE_CONTRACT"))
                q, lim = None, None
            else:
                lim = px
                q = math.floor(budget / px)
                extra["size_basis"] = (
                    "UPPER_BOUND_AT_THE_STRATEGY_ORDER_BUDGET ($%.2f) AT THE "
                    "FAIR PRICE p: the cap refused before the book was read, "
                    "so the decision recorded no size; size_within_edge "
                    "never spends more than this budget" % budget)
        n = base(K_CANDIDATE, c.get("decision_id"), None, c["slug"], side,
                 q or 0.0, lim, (q or 0.0) * (lim or 0.0),
                 strategy=c.get("strategy"), fixture=c.get("fixture"),
                 extra=extra)
        if q is None:
            n["scorable"] = False
        out.append(n)
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


def _complete(n: dict) -> bool:
    if "identity_complete" in n:
        return bool(n["identity_complete"])
    return bool(n.get("event_slug") and n.get("teams")
                and n.get("game_start") is not None)


def _slug_info(n: dict) -> dict:
    if "fixture_from_slug" in n:
        return {"fixture": n.get("fixture_from_slug"),
                "teams": n.get("slug_teams") or []}
    si = slug_identity(n.get("slug"))
    return {"fixture": si["fixture"], "teams": si["teams"]}


def _window_at(n: dict):
    if "window_at" in n:
        return n.get("window_at"), n.get("window_basis")
    if n.get("game_start") is not None:
        return n["game_start"], "CATALOGUE_GAME_START"
    t = _date_epoch(n.get("date") or slug_identity(n.get("slug"))["date"])
    return t, ("VENUE_SLUG_DATE" if t is not None else None)


def _close(a: dict, b: dict):
    """Inside TEAM_WINDOW_S? True / False, or None when either leg has no
    instant at all (unknown is treated as close by the caller)."""
    ta, ba = _window_at(a)
    tb, bb = _window_at(b)
    if ta is None or tb is None:
        return None
    slack = 0.0 if (ba == bb == "CATALOGUE_GAME_START") else DATE_SLACK_S
    return abs(float(ta) - float(tb)) <= TEAM_WINDOW_S + slack


def _near(a: dict, b: dict) -> bool:
    """Could the two legs share a team or a competition's settlement in
    time? Inside the window, an unknown instant, or the same named date (a
    catalogue start and a slug date that disagree never make a pair look
    independent)."""
    return (_close(a, b) is not False
            or bool(a.get("date") and a.get("date") == b.get("date")))


def _fixture_keys(n: dict) -> set:
    ks = set()
    if n.get("event_slug"):
        ks.add("VENUE_EVENT:%s" % str(n["event_slug"]).lower())
    fs = _slug_info(n)["fixture"]
    if fs:
        ks.add("VENUE_EVENT:%s" % fs)
    if n.get("fixture_recorded"):
        ks.add("PAPER_FIXTURE:%s" % n["fixture_recorded"])
    if n.get("book_event_key"):
        ks.add("BOOK_EVENT:%s" % n["book_event_key"])
    return ks


def _team_keys(n: dict) -> set:
    lg = n.get("league") or ""
    ks = {"TEAM:%s" % t for t in (n.get("teams") or ())}
    ks |= {"ABBR:%s:%s" % (lg, t) for t in (n.get("team_abbrs") or ())}
    ks |= {"ABBR:%s:%s" % (lg, t) for t in _slug_info(n)["teams"]}
    return ks


def _leagues_compatible(a: dict, b: dict) -> bool:
    la, lb = a.get("league") or "", b.get("league") or ""
    return (not la) or (not lb) or la == lb


def edge_between(a: dict, b: dict, dependence: dict) -> dict | None:
    """The edge of shared settlement dependence between two nodes, or None
    when they share none. Pure."""
    if a["slug"] == b["slug"]:
        return {"relation": R_SAME_CONTRACT, "dependence": D_EXACT,
                **exact_relation(a, b),
                "basis": ("one venue market: a LONG pays when it resolves "
                          "YES, a SHORT when it resolves NO")}
    if group_key(a) == group_key(b):
        return {"relation": R_SAME_EVENT_WINNER, "dependence": D_EXACT,
                **exact_relation(a, b),
                "basis": ("both legs pay on outcomes of ONE winner partition "
                          "of the venue event "
                          "(position_rooms.resolve_identity)")}
    same_fx = sorted(_fixture_keys(a) & _fixture_keys(b))
    if same_fx:
        return {"relation": R_SAME_EVENT_LINKED, "dependence": D_UNMEASURED,
                "matched_on": same_fx,
                "why": ("one game (%s), a leg off the established winner "
                        "variable (%s / %s): the joint needs the margin or "
                        "total partition these rows do not carry"
                        % (", ".join(same_fx),
                           a.get("identity_reason") or "winner",
                           b.get("identity_reason") or "winner"))}
    close = _close(a, b)
    near = _near(a, b)
    shared = sorted(_team_keys(a) & _team_keys(b))
    if shared and near:
        names = sorted(k[5:] for k in shared if k.startswith("TEAM:"))
        return {"relation": R_SAME_TEAM, "dependence": D_UNMEASURED,
                "shared_teams": names or sorted(
                    k.split(":", 2)[2] for k in shared),
                "matched_on": shared,
                "why": ("one team in two fixtures inside %d h%s: the settled "
                        "records do not say which side of each contract the "
                        "team held, so the joint cannot be read from them"
                        % (int(TEAM_WINDOW_S / 3600),
                           "" if close else " (an instant is unknown: "
                           "treated as inside)"))}
    if (not (_complete(a) and _complete(b)) and _leagues_compatible(a, b)
            and near):
        who = [n["node_id"] for n in (a, b) if not _complete(n)]
        return {"relation": R_IDENTITY_UNKNOWN, "dependence": D_UNMEASURED,
                "unidentified": who,
                "why": ("%s has no catalogue identity (event, teams and game "
                        "start; %s): a shared fixture or team cannot be "
                        "ruled out inside the same competition and window, "
                        "so the link is worst case, never independence"
                        % (" and ".join(who),
                           "; ".join(sorted({str(n.get("identity_reason")
                                                 or "INCOMPLETE")
                                             for n in (a, b)
                                             if not _complete(n)}))))}
    if (a.get("league") and a["league"] == b.get("league")
            and a.get("date") and a["date"] == b.get("date")):
        m = dependence.get(a["league"]) or {}
        if m.get("status") == D_MEASURED:
            pm = phi_max(float(a["p_used"] or 0.5), float(b["p_used"] or 0.5))
            w = min(1.0, float(m["abs_phi_upper_95"]) / pm)
            return {"relation": R_SAME_COMPETITION, "dependence": D_MEASURED,
                    "abs_phi_upper_95": m["abs_phi_upper_95"],
                    "phi": m["phi"], "disjoint_pairs": m["disjoint_pairs"],
                    "comonotone_weight": _f(w, 6),
                    "basis": ("Frechet mixture weight w = |phi| upper bound / "
                              "the largest phi these margins admit (%.4f)"
                              % pm)}
        return {"relation": R_SAME_COMPETITION, "dependence": D_UNMEASURED,
                "why": m.get("why") or ("no settled same-day fixture pairs "
                                        "of %s" % a["league"])}
    return None


def build_edges(nodes: list, dependence: dict) -> list:
    """Every pairwise edge of shared settlement dependence. Pure."""
    edges = []
    for i in range(len(nodes)):
        a = nodes[i]
        for j in range(i + 1, len(nodes)):
            e = edge_between(a, nodes[j], dependence)
            if e is not None:
                edges.append(dict(e, a=a["node_id"], b=nodes[j]["node_id"]))
    return edges


def unknown_is_worst_case(nodes: list, edges: list) -> dict:
    """THE CLAIM, CHECKED -- not asserted: every pair in which a leg's
    identity is not established, that shares a competition (or whose league
    is unknown) and is inside the window (or whose instant is unknown), is
    linked EXACT or UNMEASURED (worst case), never MEASURED or absent."""
    have = {frozenset((e["a"], e["b"])): e for e in edges}
    bad, checked = [], 0
    for i in range(len(nodes)):
        a = nodes[i]
        for j in range(i + 1, len(nodes)):
            b = nodes[j]
            if _complete(a) and _complete(b):
                continue
            if not _leagues_compatible(a, b) or not _near(a, b):
                continue
            checked += 1
            e = have.get(frozenset((a["node_id"], b["node_id"])))
            if e is None or e["dependence"] == D_MEASURED:
                bad.append({"a": a["node_id"], "b": b["node_id"],
                            "edge": None if e is None else e["relation"]})
    return {"holds": not bad, "pairs_checked": checked,
            "violations": bad[:20], "violations_total": len(bad),
            "unidentified_nodes": sorted(n["node_id"] for n in nodes
                                         if not _complete(n))}


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


def _streams(cache: dict, seed, gk: str, samples: int) -> tuple:
    """COMMON RANDOM NUMBERS: each outcome group owns three uniform streams
    (its own draw, its draw as a cluster anchor, its anchor's mixture coin),
    seeded by the group's key -- so a group's draws are the same in every
    portfolio it appears in, whatever else is added. random.Random seeds a
    str through SHA-512: reproducible across processes."""
    k = ("U", seed, gk, samples)
    got = cache.get(k)
    if got is None:
        r = random.Random("%s|%s" % (seed, gk))
        rnd = r.random
        got = ([rnd() for _ in range(samples)],
               [rnd() for _ in range(samples)],
               [rnd() for _ in range(samples)])
        cache[k] = got
    return got


def _values(cache: dict, key: tuple, us: list, cs: list, ps: list) -> list:
    got = cache.get(key)
    if got is None:
        last = len(ps) - 1
        bl = bisect.bisect_left
        got = [ps[min(bl(cs, u - 1e-15), last)] for u in us]
        cache[key] = got
    return got


def _es_of(losses: list, k: int) -> float:
    return sum(heapq.nlargest(k, losses)) / k


def paired_es_standard_error(base: list, withc: list, *, key: str,
                             reps: int = MC_BOOTSTRAP) -> float | None:
    """The Monte Carlo standard error of ES(withc) - ES(base) over PAIRED
    samples (common random numbers): the spread of the difference across
    `reps` bootstrap resamples of the sample indices."""
    n = len(base)
    if n < 2 or len(withc) != n or reps < 2:
        return None
    k = max(1, int(math.ceil((1.0 - TAIL) * n)))
    r = random.Random("%s|bootstrap|%s" % (MC_SEED, key))
    rnd = r.random
    diffs = []
    for _ in range(reps):
        idx = [int(rnd() * n) for _ in range(n)]
        diffs.append(_es_of([withc[i] for i in idx], k)
                     - _es_of([base[i] for i in idx], k))
    m = sum(diffs) / reps
    return math.sqrt(sum((d - m) ** 2 for d in diffs) / (reps - 1))


def portfolio(nodes: list, edges: list, *, samples: int = MC_SAMPLES,
              seed: int = MC_SEED, _cache: dict | None = None,
              keep_samples: bool = False,
              stable_groups: set | None = None) -> dict:
    """WORST CASE and EVIDENCED CASE for one set of nodes. Pure.

    `stable_groups`: the outcome groups of the COMMITTED book. A coupling
    cluster's shared draws are keyed by its first stable member, so adding a
    candidate whose group sorts first does not re-key a whole cluster."""
    if not nodes:
        out = {"nodes": 0, "worst_case": {"sum_of_costs_usd": 0.0,
                                          "tail_99_es_usd": 0.0,
                                          "tail_99_var_usd": 0.0},
               "evidenced_case": {"max_feasible_loss_usd": 0.0,
                                  "tail_99_es_usd": 0.0,
                                  "tail_99_var_usd": 0.0,
                                  "samples": int(samples), "seed": seed},
               "expected_pnl_usd": 0.0, "groups": [], "_group_weight": {}}
        if keep_samples:
            out["_samples"] = [0.0] * int(samples)
        return out
    cache = _cache if _cache is not None else {}
    S = int(samples)
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
    for root in clusters:
        ws = [w for (x, _y), w in weight.items() if uf.find(x) == root]
        # one weight per cluster: the LARGEST of its links (a cluster that
        # holds one UNMEASURED link is comonotone throughout) -- more
        # coupling can only fatten this loss tail
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
    # and the 99% tail by simulation: common random numbers per group; ONE
    # mixture coin per cluster per sample (review, R30C: a coin per GROUP
    # coupled two groups only with probability w^2, under-weighting every
    # MEASURED link)
    cum, sig = {}, {}
    order = sorted(groups)
    for gk in order:
        # WORST FIRST, so a shared uniform aligns the groups' bad outcomes
        srt = sorted(groups[gk]["atoms"], key=lambda a: a["pnl_usd"])
        cs, ps, acc = [], [], 0.0
        for a in srt:
            acc += a["prob"]
            cs.append(acc)
            ps.append(a["pnl_usd"])
        cum[gk] = (cs, ps)
        sig[gk] = tuple(zip((round(c, 12) for c in cs),
                            (round(p, 6) for p in ps)))
    tot = [0.0] * S
    add = operator.add
    for root in sorted(clusters):
        members = sorted(clusters[root])
        if len(members) == 1:
            gk = members[0]
            own = _streams(cache, seed, gk, S)[0]
            vals = _values(cache, ("OWN", seed, S, gk, sig[gk]), own,
                           *cum[gk])
            tot = list(map(add, tot, vals))
            continue
        stable = [m for m in members if m in (stable_groups or ())]
        anchor = stable[0] if stable else members[0]
        _o, shared, coin = _streams(cache, seed, anchor, S)
        w = cw[root]
        coupled = None if w >= 1.0 else [c < w for c in coin]
        for gk in members:
            co = _values(cache, ("CO", seed, S, gk, sig[gk], anchor), shared,
                         *cum[gk])
            if coupled is None:
                vals = co
            else:
                own = _values(cache, ("OWN", seed, S, gk, sig[gk]),
                              _streams(cache, seed, gk, S)[0], *cum[gk])
                vals = [c if k else o for c, o, k in zip(co, own, coupled)]
            tot = list(map(add, tot, vals))
    losses = [-t for t in tot]
    k = max(1, int(math.ceil((1.0 - TAIL) * S)))
    top = heapq.nlargest(k, losses)
    ev_es = sum(top) / k
    ev_var = top[-1]
    out = {
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
        "_group_weight": {g: cw[uf.find(g)] for g in groups},
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
            "samples": S, "seed": seed,
            "basis": ("exact links share one outcome; UNMEASURED links stay "
                      "comonotone; MEASURED links are a Frechet mixture (ONE "
                      "coin per coupling cluster per sample: with "
                      "probability w the cluster shares one uniform); nodes "
                      "with no shared settlement dependence are independent; "
                      "common random numbers keyed per outcome group")},
        "expected_pnl_usd": _f(exp_pnl, 4),
        "pnl_is": ("completed-game settlement P&L from here (held quantity "
                   "paid $1 on its outcomes, less its cost); exceptional "
                   "settlement states are the settlement-exception table's")}
    if keep_samples:
        out["_samples"] = losses
    return out


def _strip(pf: dict) -> dict:
    return {k: v for k, v in pf.items() if not k.startswith("_")}


def graph(nodes: list, dependence: dict, *, now: float | None = None,
          samples: int = MC_SAMPLES, treatments: dict | None = None,
          budget_s: float = COMPUTE_BUDGET_S) -> dict:
    """The whole graph: nodes, edges, both portfolio views for the HELD book
    and for the book with its working orders and candidates, and each
    candidate's shadow comparison next to its CURRENT treatment (`treatments`
    {node_id: ...}, read by `read` from Allie's own records). Pure."""
    t0 = time.monotonic()
    treatments = treatments or {}
    committed_all = [n for n in nodes if n["kind"] in (K_POSITION, K_WORKING)]
    cands_all = [n for n in nodes if n["kind"] == K_CANDIDATE]
    truncation = {}
    committed = committed_all
    if len(committed_all) > MAX_COMMITTED_NODES:
        # the LARGEST exposures are kept: a truncated view understates the
        # book, and says so
        committed = sorted(committed_all, key=lambda n: -float(
            n.get("cost_usd") or 0))[:MAX_COMMITTED_NODES]
        truncation["committed"] = {"total": len(committed_all),
                                   "carried": MAX_COMMITTED_NODES}
    cands = cands_all[:MAX_CANDIDATE_NODES]
    if len(cands_all) > MAX_CANDIDATE_NODES:
        truncation["candidates"] = {"total": len(cands_all),
                                    "carried": MAX_CANDIDATE_NODES}
    nodes = committed + cands
    held = [n for n in committed if n["kind"] == K_POSITION]
    edges = build_edges(nodes, dependence)
    n_groups = len({group_key(n) for n in nodes}) or 1
    req = int(samples)
    # the sample count falls with the book (never below MC_MIN_SAMPLES, never
    # above what was asked for)
    S = req if req <= MC_MIN_SAMPLES else max(
        MC_MIN_SAMPLES, min(req, MC_GROUP_DRAW_BUDGET // n_groups))
    cache: dict = {}
    committed_ids = {x["node_id"] for x in committed}
    stable = {group_key(n) for n in committed}
    base = portfolio(committed, edges, samples=S, _cache=cache,
                     keep_samples=True, stable_groups=stable)
    scorable = [c for c in cands if c.get("scorable", True)]
    scorable_ids = {c["node_id"] for c in scorable}
    out_cands = []
    scored = 0
    for c in cands:
        links = [e for e in edges if c["node_id"] in (e["a"], e["b"])]
        cur = treatments.get(c["node_id"]) or {
            "status": "NOT_READ",
            "why": ("a pure call carries no current treatment: `read` reads "
                    "Allie's own record or query for each candidate")}
        entry = {
            "node_id": c["node_id"], "slug": c["slug"], "side": c["side"],
            "strategy": c.get("strategy"),
            "candidate_source": c.get("candidate_source"),
            "current_refusals": c.get("current_refusals") or [],
            "refusals_in_window": c.get("refusals_in_window"),
            "admission_without_the_cap": c.get("admission_without_the_cap"),
            "size_basis": c.get("size_basis"),
            "edges_to_the_book": [
                {"to": other, "relation": e["relation"],
                 "dependence": e["dependence"],
                 "payout_relation": e.get("payout_relation")}
                for e, other in ((e, e["b"] if e["a"] == c["node_id"]
                                  else e["a"]) for e in links)
                if other in committed_ids],
            "current_treatment": dict(cur, authority=(
                "AUTHORITATIVE_UNCHANGED" if cur.get("status") not in (
                    "NOT_READ", "UNAVAILABLE") else cur.get("status")))}
        if c["node_id"] not in scorable_ids:
            entry["shadow_marginal"] = {
                "status": "NOT_SCORED",
                "why": c.get("size_unavailable_because")
                or "SIZE_NOT_ESTABLISHED"}
        elif scored >= MAX_CANDIDATES_SCORED or \
                time.monotonic() - t0 > budget_s:
            entry["shadow_marginal"] = {
                "status": "NOT_SCORED",
                "why": ("COMPUTE_BUDGET: %d candidates are scored per read "
                        "within %.1f s" % (MAX_CANDIDATES_SCORED, budget_s))}
        else:
            withc = portfolio(committed + [c], edges, samples=S,
                              _cache=cache, keep_samples=True,
                              stable_groups=stable)
            scored += 1
            se = paired_es_standard_error(base["_samples"],
                                          withc["_samples"],
                                          key=c["node_id"])
            # ONE WEIGHT PER COUPLING CLUSTER: a candidate whose own links
            # need a larger weight (skewed margins admit little phi, so the
            # measured bound restricts them less) couples the committed
            # groups it joins more tightly too -- conservative, and part of
            # its marginal is that re-weighting, which is said, not hidden
            gw0, gw1 = base["_group_weight"], withc["_group_weight"]
            joined = [g for g in stable if gw1.get(g, 0.0) >
                      gw0.get(g, 0.0) + 1e-12]
            reweight = None if not joined else {
                "groups": len(joined),
                "from_max": _f(max(gw0.get(g, 0.0) for g in joined), 6),
                "to": _f(max(gw1[g] for g in joined), 6),
                "why": ("the candidate's own links need this weight and one "
                        "coupling cluster carries one weight: part of this "
                        "marginal is the committed book coupled more "
                        "tightly (conservative)")}
            entry["shadow_marginal"] = {
                "worst_case_tail_99_es_usd": _f(
                    withc["worst_case"]["tail_99_es_usd"]
                    - base["worst_case"]["tail_99_es_usd"], 4),
                "evidenced_tail_99_es_usd": _f(
                    withc["evidenced_case"]["tail_99_es_usd"]
                    - base["evidenced_case"]["tail_99_es_usd"], 4),
                "evidenced_tail_99_es_mc_standard_error_usd": _f(se, 4),
                "mc_basis": ("common random numbers keyed per outcome group "
                             "(adding the candidate leaves every other "
                             "group's draws unchanged); standard error by %d "
                             "paired bootstrap resamples of %d samples"
                             % (MC_BOOTSTRAP, S)),
                "reweights_the_committed_book": reweight,
                "worst_case_sum_of_costs_usd": _f(c["cost_usd"], 4),
                "evidenced_max_feasible_loss_usd": _f(
                    withc["evidenced_case"]["max_feasible_loss_usd"]
                    - base["evidenced_case"]["max_feasible_loss_usd"], 4),
                "status": "SHADOW_INFORMATION_ONLY"}
        out_cands.append(entry)
    counts: dict = {}
    for e in edges:
        k = "%s|%s" % (e["relation"], e["dependence"])
        counts[k] = counts.get(k, 0) + 1
    check = unknown_is_worst_case(nodes, edges)
    full = portfolio(committed + scorable, edges, samples=S, _cache=cache,
                     stable_groups=stable)
    return {
        "version": VERSION, "as_of": now, "authority": AUTHORITY,
        "nodes": nodes, "edges": edges[:MAX_EDGES_LISTED],
        "edges_total": len(edges),
        "edges_listed_truncated": len(edges) > MAX_EDGES_LISTED,
        "edge_counts": counts,
        "dependence_by_competition": dependence,
        "portfolio": {
            "held": _strip(portfolio(held, edges, samples=S, _cache=cache,
                                     stable_groups=stable)),
            "held_plus_working_orders": _strip(base),
            "held_plus_working_orders_and_candidates": _strip(full)},
        "portfolio_complete": "committed" not in truncation,
        "truncation": truncation or None,
        "monte_carlo": {
            "samples": S, "samples_requested": int(samples),
            "seed": MC_SEED, "outcome_groups": n_groups,
            "basis": ("samples = min(requested, %d group-draws / outcome "
                      "groups), never below %d"
                      % (MC_GROUP_DRAW_BUDGET, MC_MIN_SAMPLES))},
        "candidates": out_cands,
        "authoritative_caps": [dict(c, status="AUTHORITATIVE_UNCHANGED")
                               for c in AUTHORITATIVE_CAPS],
        "relaxation": {
            "applied": False,
            "requires": ("a later OWNER decision, recorded as such; the "
                         "evidenced case is shadow information and changes "
                         "no haircut, cap, refusal or size")},
        "unknown_is_worst_case": check["holds"],
        "unknown_is_worst_case_check": check,
        "compute_s": round(time.monotonic() - t0, 3)}


# ═════════════════════════════════════════════════════════════════════
# 4 · THE READ (plain SELECTs, through position_rooms' loaders)
# ═════════════════════════════════════════════════════════════════════

VALUATIONS_SQL = """
    SELECT DISTINCT ON (us_market_slug) us_market_slug, id AS valuation_id,
           probability, buy_intent, event_key,
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

#: The RECORDED fixture of each open group's market (the order writer's own
#: field), for nodes the catalogue does not identify.
FIXTURES_SQL = """
    SELECT DISTINCT ON (group_id, us_market_slug) group_id, us_market_slug,
           fixture
      FROM paper_orders
     WHERE account_id = $1 AND group_id = ANY($2::text[])
       AND fixture IS NOT NULL
     ORDER BY group_id, us_market_slug, created_at
"""

#: THE OPPORTUNITIES THE CAPS REFUSED: per contract, side and strategy, the
#: latest decision in the window that a correlation / concentration cap
#: refused -- a REFUSE whose refusals are ALL caps, or an ENTER whose order a
#: cap refused (its ORDER_REFUSED finding) -- on a market not yet settled.
CAP_CANDIDATES_SQL = """
    SELECT DISTINCT ON (d.us_market_slug, d.holding_side, d.strategy)
           d.decision_id, d.session_id, d.us_market_slug, d.holding_side,
           d.strategy, d.fixture, d.verdict, d.refusals, d.proposed_qty,
           d.limit_price, d.p_pinnacle,
           extract(epoch FROM d.decided_at)::float8 AS decided_at,
           f.detail->>'refusal' AS order_refusal,
           count(*) OVER (PARTITION BY d.us_market_slug, d.holding_side,
                                       d.strategy) AS refusals_in_window
      FROM paper_decisions d
      LEFT JOIN LATERAL (
           SELECT a.detail FROM paper_audrey_findings a
            WHERE a.subject = d.decision_id AND a.kind = $4
            ORDER BY a.found_at DESC LIMIT 1) f ON true
     WHERE d.account_id = $1 AND d.decided_at >= to_timestamp($2)
       AND d.us_market_slug IS NOT NULL
       AND d.holding_side IN ('LONG', 'SHORT')
       AND ((d.verdict = 'REFUSE' AND cardinality(d.refusals) > 0
             AND d.refusals <@ $3::text[])
         OR (d.verdict = 'ENTER' AND f.detail->>'refusal' = ANY($3::text[])))
       AND NOT EXISTS (SELECT 1 FROM external_valuations x
                        WHERE x.experiment_id = $6 AND x.outcome_known
                          AND x.us_market_slug = d.us_market_slug)
     ORDER BY d.us_market_slug, d.holding_side, d.strategy,
              d.decided_at DESC
     LIMIT $5
"""

SESSIONS_SQL = """
    SELECT session_id, config FROM paper_sessions
     WHERE session_id = ANY($1::text[])
"""

INTENT_ALLIE_SQL = """
    SELECT decision_id, allie FROM canonical_decision_intents
     WHERE decision_id = ANY($1::text[])
"""


def _json(v):
    if isinstance(v, str):
        import json as _j
        try:
            return _j.loads(v)
        except ValueError:
            return None
    return v


async def _cap_candidates(conn, account_id: str, at: float) -> list:
    from . import settlement_exception_risk as SER
    rows = [dict(r) for r in await conn.fetch(
        CAP_CANDIDATES_SQL, account_id, at - CAP_REFUSAL_WINDOW_S,
        list(CORRELATION_CAP_REFUSALS), ORDER_REFUSED_FINDING,
        MAX_CAP_CANDIDATES_READ, SER.EXPERIMENT_ID)]
    if not rows:
        return []
    cfg = {r["session_id"]: _json(r["config"]) for r in await conn.fetch(
        SESSIONS_SQL, sorted({r["session_id"] for r in rows}))}
    out = []
    for r in rows:
        enter = r["verdict"] == "ENTER"
        out.append({
            "decision_id": r["decision_id"], "slug": r["us_market_slug"],
            "holding_side": r["holding_side"], "strategy": r["strategy"],
            "fixture": r["fixture"],
            "source": C_ORDER_CAP_REFUSED if enter else C_CAP_REFUSED,
            "refusals": ([r["order_refusal"]] if enter
                         else list(r["refusals"] or ())),
            "refusals_in_window": int(r["refusals_in_window"] or 0),
            "decided_at": r["decided_at"],
            "proposed_qty": r["proposed_qty"] if enter else None,
            "limit_price": r["limit_price"] if enter else None,
            "p_pinnacle": r["p_pinnacle"],
            "budget_usd": order_budget_usd(cfg.get(r["session_id"]),
                                           account_id),
            "admission_without_cap": (
                "ADMITTED_BY_EVERY_OTHER_RULE: the decision was an ENTER; "
                "the ledger refused its order on the cap" if enter else
                "NOT_ESTABLISHED: the cap refused before the book was read, "
                "so whether the edge and EV rules would have admitted it is "
                "unknown")})
    return out


async def _allie_treatments(conn, nodes: list) -> dict:
    """Each candidate's CURRENT correlation treatment, from Allie's own
    records: the allie component its canonical intent recorded, else Allie's
    fixture query run now through allie_capital's haircut rule."""
    out: dict = {}
    cands = [n for n in nodes if n["kind"] == K_CANDIDATE][
        :MAX_CANDIDATE_NODES]
    if not cands:
        return out
    dids = sorted({n.get("decision_id") for n in cands
                   if n.get("decision_id")})
    recorded = {}
    if dids and await PR._exists(conn, "canonical_decision_intents"):
        for r in await conn.fetch(INTENT_ALLIE_SQL, dids):
            recorded[r["decision_id"]] = _json(r["allie"]) or {}
    by_fx: dict = {}
    for n in cands:
        a = recorded.get(n.get("decision_id"))
        cc = (a or {}).get("correlation_concentration") if a else None
        if isinstance(cc, dict):
            out[n["node_id"]] = {
                "status": "RECORDED",
                "source": ("canonical_decision_intents.allie"
                           ".correlation_concentration (decision %s)"
                           % n.get("decision_id")),
                "allie_haircut": cc.get("haircut"),
                "fixture_open_groups": cc.get("fixture_open_groups"),
                "fixture_open_usd": cc.get("fixture_open_usd"),
                "fixture_headroom_usd": cc.get("fixture_headroom_usd"),
                "allie_status": a.get("status")}
            continue
        fx = n.get("fixture_recorded")
        if not fx:
            out[n["node_id"]] = {
                "status": "ALLIE_QUERY_NOT_RUN_WITHOUT_A_FIXTURE",
                "source": ("canonical_components.allie_at_decision runs its "
                           "fixture query only for a decision with a fixture; "
                           "without one allocate() receives 0 open groups"),
                "allie_haircut": 0.0, "fixture_open_groups": 0,
                "fixture_open_usd": 0.0}
            continue
        if fx not in by_fx:
            r = await conn.fetchrow(ALLIE_FIXTURE_SQL, fx)
            by_fx[fx] = (int(r["n"] or 0), float(r["usd"] or 0))
        k, usd = by_fx[fx]
        out[n["node_id"]] = {
            "status": "ALLIE_QUERY_RUN_AT_THIS_READ",
            "source": ("canonical_components.allie_at_decision's fixture "
                       "query (ALLIE_FIXTURE_SQL, verbatim) on fixture %r, "
                       "through allie_capital's haircut rule min(1, %.2f x "
                       "open groups)" % (fx, AC.CORRELATION_HAIRCUT)),
            "allie_haircut": _f(min(1.0, AC.CORRELATION_HAIRCUT * k), 4),
            "fixture_open_groups": k, "fixture_open_usd": _f(usd, 2),
            "fixture_headroom_usd": _f(max(0.0, AC.FIXTURE_CAP_USD - usd), 2)}
    return out


async def read(conn, *, now: float | None = None,
               account_id: str | None = None,
               samples: int = MC_SAMPLES) -> dict:
    """Every input of the graph, read with plain SELECTs (the PAPER book:
    ACTUAL is SHADOW, SMALL LIVE holds no exposure). A failed read is a named
    refusal, never a partial graph."""
    from . import settlement_exception_risk as SER
    at = float(now if now is not None else time.time())
    if account_id is None:
        # (rc6.3 pr5-port) the durable PAPER selector, as position_rooms.load
        # (Command's position rooms) resolves it: before this the graph read
        # paper_acct_main after an activation. Unreadable -> named refusal.
        from .simulated_account_context import selected_account
        try:
            account_id = await selected_account(conn)
        except ValueError as exc:
            return {"ok": False, "refusal": str(exc)}
    acct = account_id
    raw = await PR._paper(conn, at, acct)
    if not raw.get("available"):
        return {"ok": False,
                "refusal": raw.get("why") or "PAPER_BOOK_UNAVAILABLE"}
    cands = await _cap_candidates(conn, acct, at)
    slugs = sorted({x["slug"] for x in (raw.get("fills") or [])
                    + (raw.get("orders") or []) + cands if x.get("slug")})
    premap = await PR._premap(conn, slugs)
    vals = {}
    if slugs:
        for r in await conn.fetch(VALUATIONS_SQL, slugs):
            vals[r["us_market_slug"]] = dict(r)
    fixtures = {}
    if raw.get("groups"):
        for r in await conn.fetch(FIXTURES_SQL, acct, raw["groups"]):
            fixtures[(r["group_id"], r["us_market_slug"])] = r["fixture"]
    # a PROPOSED (ENTER, not yet ordered) candidate's recorded fixture
    pdids = [o["decision_id"] for o in raw.get("orders") or ()
             if o.get("state") == PR.S_PROPOSED and o.get("decision_id")]
    if pdids:
        fx_by = {r["decision_id"]: r["fixture"] for r in await conn.fetch(
            "SELECT decision_id, fixture FROM paper_decisions "
            " WHERE decision_id = ANY($1::text[])", pdids)}
        for o in raw.get("orders") or ():
            if o.get("decision_id") in fx_by:
                o["fixture"] = fx_by[o["decision_id"]]
    hist = []
    for r in await conn.fetch(HISTORY_SQL, SER.EXPERIMENT_ID, HISTORY_LIMIT):
        hist.append({"league": league_of(r["us_market_slug"]),
                     "date": date_of(r["us_market_slug"]),
                     "fixture": r["fixture"],
                     "long_won": (None if r["long_won"] is None
                                  else int(r["long_won"]))})
    nodes = build_nodes(raw, premap, vals, now=at, fixtures=fixtures,
                        candidates=cands)
    treatments = await _allie_treatments(conn, nodes)
    return {"ok": True, "at": at, "account_id": acct, "nodes": nodes,
            "dependence": measured_dependence(hist),
            "treatments": treatments, "samples": int(samples),
            "source": {
                "paper_read": "position_rooms._paper",
                "identity": ("position_rooms.resolve_identity (us_premap); "
                             "fallback: the recorded fixture "
                             "(paper_orders / paper_decisions .fixture, "
                             "external_valuations.event_key) and the venue "
                             "slug's fixture, teams and date"),
                "probabilities": "external_valuations (latest per market)",
                "cap_refused_candidates": len(cands),
                "cap_refusal_window_s": CAP_REFUSAL_WINDOW_S,
                "current_treatment": ("canonical_decision_intents.allie, else "
                                      "Allie's own fixture query"),
                "settled_history_markets": len(hist)}}


def compute(inputs: dict) -> dict:
    """The graph from `read`'s inputs. Pure CPU: the route runs it OFF the
    event loop (asyncio.to_thread) and outside the read transaction."""
    if not inputs.get("ok"):
        return {"version": VERSION, "ok": False,
                "refusal": inputs.get("refusal") or "PAPER_BOOK_UNAVAILABLE",
                "authority": AUTHORITY}
    g = graph(inputs["nodes"], inputs["dependence"], now=inputs["at"],
              samples=inputs["samples"], treatments=inputs["treatments"])
    g.update(ok=True, refusal=None, account_id=inputs["account_id"],
             source=inputs["source"])
    return g


async def load(conn, *, now: float | None = None,
               account_id: str | None = None,
               samples: int = MC_SAMPLES) -> dict:
    """read + compute (the compute in a worker thread)."""
    import asyncio
    inputs = await read(conn, now=now, account_id=account_id, samples=samples)
    return await asyncio.to_thread(compute, inputs)


def describe() -> dict:
    return {"version": VERSION, "relations": [
        R_SAME_CONTRACT, R_SAME_EVENT_WINNER, R_SAME_EVENT_LINKED,
        R_SAME_TEAM, R_IDENTITY_UNKNOWN, R_SAME_COMPETITION],
        "dependence_classes": [D_EXACT, D_MEASURED, D_UNMEASURED],
        "candidate_sources": [C_ENTER_NOT_ORDERED, C_CAP_REFUSED,
                              C_ORDER_CAP_REFUSED],
        "team_window_s": TEAM_WINDOW_S,
        "min_independent_pairs": MIN_INDEPENDENT_PAIRS,
        "authoritative_caps": [c["control"] for c in AUTHORITATIVE_CAPS],
        "authority": AUTHORITY}
