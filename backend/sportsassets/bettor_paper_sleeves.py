"""THE PAPER BOOK'S ECONOMIC SLEEVES (migration 223). RESEARCH / SHADOW.

The fictional $500,000 paper account holds positions with different
economic purposes on ONE cash ledger. Each position group carries one
durable sleeve, derived from the strategy (and deciding policy version) of
its ENTRY decision -- a position never switches strategy (migration 182),
so it never switches sleeve:

  INVESTMENT    production-candidate paper: the versioned investment
                policy (PINNACLE_COMPLETED_GAME_PAPER; V2/V3 live-eligible)
                and Derek's own entry policy (DEREK_ENTRY_POLICY_V2)
  TRAINING      the bounded exploration / training strategy
                (PINNACLE_EXPLORATION_PAPER): a loss is a RESEARCH COST,
                a win is NOT production alpha
  BENCHMARK     control arms: the strict Pinnacle-only benchmark and the
                maker execution experiment (not promoted)
  UNCLASSIFIED  anything else, a group with conflicting strategies, or a
                group with no durable classification -- shown explicitly,
                NEVER silently counted as INVESTMENT

SLEEVE EQUITY METHOD: P&L-ONLY ON SHARED CASH. There is one cash ledger
and no per-sleeve funding (migration 182: "no per-strategy bankroll and no
second funding"), so a sleeve has no starting allocation to attribute.
A sleeve's equity figure is its CONTRIBUTION: realized P&L of its positions
+ unrealized P&L of its MARKED open positions (an UNMARKED position adds
nothing and is listed). The account accounting reconciles exactly:

    starting cash + sum over sleeves of (realized + marked unrealized)
        == cash + marked value + unmarked positions at cost
        == the account equity the equity wall shows

and the difference is reported (never hidden) when it is not zero.

`classify` mirrors the SQL classifier paper_sleeve_of / paper_sleeve_basis
(pinned equal by a test). The only write here is the BACKSTOP
`classify_missing` (append-only INSERT into paper_sleeve_classifications);
every read is a plain SELECT. Nothing here reaches an order, venue, sizing,
limit, threshold, gate or capital path.
"""
from __future__ import annotations

import time
from decimal import Decimal

from . import bettor_paper_ledger as L

CLASSIFIER_VERSION = "PAPER_SLEEVE_V1"
INVESTMENT, TRAINING, BENCHMARK, UNCLASSIFIED = (
    "INVESTMENT", "TRAINING", "BENCHMARK", "UNCLASSIFIED")
SLEEVES = (INVESTMENT, TRAINING, BENCHMARK, UNCLASSIFIED)

STRATEGY_SLEEVE = {
    "PINNACLE_COMPLETED_GAME_PAPER": INVESTMENT,
    "DEREK_ENTRY_POLICY_V2": INVESTMENT,
    "PINNACLE_EXPLORATION_PAPER": TRAINING,
    "PINNACLE_ONLY_PAPER_BENCHMARK": BENCHMARK,
    "PINNACLE_COMPLETED_GAME_MAKER_PAPER": BENCHMARK,
}
#: the investment policy's live-eligible versions (pinned equal to
#: execmirror.LIVE_ELIGIBLE by a test; stated here so this module imports
#: no execution module)
INVESTMENT_LIVE_ELIGIBLE = ("PINNACLE_COMPLETED_GAME_PAPER_V2",
                            "PINNACLE_COMPLETED_GAME_PAPER_V3")

R_NO_DURABLE = "NO_DURABLE_CLASSIFICATION"
R_MIXED = "GROUP_CARRIES_MORE_THAN_ONE_STRATEGY"
R_NO_SCHEMA = "MIGRATION_223_NOT_APPLIED"

SLEEVE_META = {
    INVESTMENT: {
        "title": "INVESTMENT SLEEVE",
        "purpose": "production-candidate paper (the investment policy)",
        "economics_label": "PRODUCTION_CANDIDATE_PAPER_ECONOMICS",
        "loss_label": "INVESTMENT LOSS", "win_label": "INVESTMENT GAIN"},
    TRAINING: {
        "title": "TRAINING / EXPLORATION SLEEVE",
        "purpose": ("bounded training: may take positions failing the "
                    "investment edge or after-fee rule to generate forward "
                    "experience"),
        "economics_label":
            "EXPLORATION_RESEARCH_COST_NOT_INVESTMENT_PERFORMANCE",
        "loss_label": "RESEARCH COST",
        "win_label": "TRAINING WIN -- NOT PRODUCTION ALPHA"},
    BENCHMARK: {
        "title": "BENCHMARK / CONTROL SLEEVE",
        "purpose": "control arms (strict benchmark, maker experiment)",
        "economics_label": "BENCHMARK_CONTROL_NOT_PRODUCTION",
        "loss_label": "CONTROL LOSS",
        "win_label": "CONTROL GAIN -- NOT PRODUCTION ALPHA"},
    UNCLASSIFIED: {
        "title": "UNCLASSIFIED",
        "purpose": ("positions whose economic purpose is not established "
                    "-- never counted as investment"),
        "economics_label": "UNCLASSIFIED_NOT_INVESTMENT",
        "loss_label": "UNCLASSIFIED LOSS",
        "win_label": "UNCLASSIFIED GAIN"},
}

EQUITY_METHOD = {
    "rule": "PNL_ONLY_ON_SHARED_CASH",
    "text": ("One $500,000 cash ledger, no per-sleeve funding: a sleeve's "
             "equity is its CONTRIBUTION -- realized P&L of its positions + "
             "unrealized P&L of its marked open positions. No starting "
             "allocation is attributed. Starting cash + every sleeve's "
             "contribution reconciles to the account equity."),
}

#: a mark price unchanged for longer than this is NO NEW MARK (the book was
#: re-read but the price did not move): the chart freezes, it never ticks
NO_NEW_MARK_AFTER_S = float(L.MARK_STALE_AFTER_S)
#: how far back (and how many observations per market) the genuine
#: mark-change search looks
MARK_HISTORY_S = 6 * 3600.0
MARK_HISTORY_ROWS = 120
MARK_CHANGE_CACHE_S = 20.0


def classify(strategy, policy_version) -> tuple:
    """(sleeve, basis) -- the Python mirror of paper_sleeve_of /
    paper_sleeve_basis (migration 223)."""
    if strategy is None:
        return UNCLASSIFIED, "NO_STRATEGY_RECORDED"
    sleeve = STRATEGY_SLEEVE.get(str(strategy), UNCLASSIFIED)
    if strategy == "PINNACLE_COMPLETED_GAME_PAPER":
        basis = ("INVESTMENT_POLICY_LIVE_ELIGIBLE_VERSION"
                 if policy_version in INVESTMENT_LIVE_ELIGIBLE
                 else "INVESTMENT_POLICY_VERSION_NOT_LIVE_ELIGIBLE")
    else:
        basis = {"DEREK_ENTRY_POLICY_V2": "DEREK_ENTRY_POLICY",
                 "PINNACLE_EXPLORATION_PAPER":
                     "EXPLORATION_TRAINING_STRATEGY_RESEARCH_COST",
                 "PINNACLE_ONLY_PAPER_BENCHMARK": "STRICT_BENCHMARK_CONTROL",
                 "PINNACLE_COMPLETED_GAME_MAKER_PAPER":
                     "MAKER_EXPERIMENT_NOT_PROMOTED"}.get(
                         str(strategy), "UNKNOWN_STRATEGY")
    return sleeve, basis


def _ep(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _r2(v):
    return None if v is None else round(float(v) + 0.0, 2)


async def schema(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('paper_sleeve_classifications') IS NOT NULL"))


async def classifications(conn, account_id: str) -> dict:
    """{group_id: the current durable classification row}."""
    rows = await conn.fetch(
        "SELECT group_id, sleeve, strategy, policy_version, decision_id, "
        "       basis, classifier_version, classified_by, entry_at, "
        "       classified_at "
        "  FROM paper_sleeve_current_v WHERE account_id = $1", account_id)
    return {r["group_id"]: {
        "sleeve": r["sleeve"], "strategy": r["strategy"],
        "policy_version": r["policy_version"],
        "decision_id": r["decision_id"], "basis": r["basis"],
        "classifier_version": r["classifier_version"],
        "classified_by": r["classified_by"],
        "entry_at": _ep(r["entry_at"]),
        "classified_at": _ep(r["classified_at"])} for r in rows}


BACKSTOP_SQL = """
    WITH g AS (
        SELECT group_id, count(DISTINCT strategy) AS strategies
          FROM paper_orders WHERE account_id = $1 GROUP BY group_id),
    first_order AS (
        SELECT DISTINCT ON (o.group_id) o.group_id, o.account_id,
               o.strategy, o.decision_id, o.order_id, o.created_at, o.role
          FROM paper_orders o
         WHERE o.account_id = $1
           AND NOT EXISTS (SELECT 1 FROM paper_sleeve_classifications c
                            WHERE c.group_id = o.group_id
                              AND c.classifier_version = $2)
         ORDER BY o.group_id, (o.role = 'ENTRY') DESC, o.created_at,
                  o.order_id)
    INSERT INTO paper_sleeve_classifications (
        classification_id, account_id, group_id, sleeve, strategy,
        policy_version, decision_id, entry_order_id, basis,
        classifier_version, classified_by, entry_at)
    SELECT 'psc:' || md5(f.group_id || ':' || $2), f.account_id, f.group_id,
           CASE WHEN g.strategies > 1 THEN 'UNCLASSIFIED'
                ELSE paper_sleeve_of(f.strategy, pd.policy_version) END,
           f.strategy, pd.policy_version, f.decision_id,
           CASE WHEN f.role = 'ENTRY' THEN f.order_id END,
           CASE WHEN g.strategies > 1
                THEN 'GROUP_CARRIES_MORE_THAN_ONE_STRATEGY'
                ELSE paper_sleeve_basis(f.strategy, pd.policy_version) END,
           $2, 'BACKSTOP', f.created_at
      FROM first_order f JOIN g USING (group_id)
      LEFT JOIN paper_decisions pd ON pd.decision_id = f.decision_id
    ON CONFLICT (group_id, classifier_version) DO NOTHING
"""


async def classify_missing(conn, account_id: str = None) -> int:
    """THE BACKSTOP: record the deterministic classification of any group
    the entry trigger did not (append-only; idempotent). Returns the number
    of rows written."""
    account_id = account_id or await L.selected_account(conn)
    if not await schema(conn):
        return 0
    got = await conn.execute(BACKSTOP_SQL, account_id, CLASSIFIER_VERSION)
    try:
        return int(str(got).split()[-1])
    except (ValueError, IndexError):                         # pragma: no cover
        return 0


# ═════════════════════════════════════════════════════════════════════
# GENUINE MARK CHANGES (not book re-reads)
# ═════════════════════════════════════════════════════════════════════

def mark_change_of(obs: list, holding_side: str) -> dict:
    """`obs`: [(observed_at, market_data)] NEWEST FIRST. The mark is the
    ledger's own (bettor_book_snapshot.exit_ladder best exit price, as
    bettor_paper_ledger.latest_marks computes it). Returns
    {price, changed_at, unchanged_since_at_least, observations}: changed_at
    is the first observation carrying the CURRENT price after a different
    one; when no different price is in the window, changed_at is None and
    unchanged_since_at_least is the oldest observation looked at."""
    from . import bettor_book_snapshot as BS
    intent = ("ORDER_INTENT_BUY_LONG" if holding_side == "LONG"
              else "ORDER_INTENT_BUY_SHORT")
    prices = []
    for at, md in obs:
        ex = BS.exit_ladder(md, held_intent=intent)
        prices.append((at, ex.get("best_exit_price") if ex.get("ok")
                       else None))
    if not prices:
        return {"price": None, "changed_at": None,
                "unchanged_since_at_least": None, "observations": 0}
    cur = prices[0][1]
    run_start = prices[0][0]
    for at, p in prices[1:]:
        if p != cur:
            return {"price": cur, "changed_at": run_start,
                    "unchanged_since_at_least": None,
                    "observations": len(prices)}
        run_start = at
    return {"price": cur, "changed_at": None,
            "unchanged_since_at_least": run_start,
            "observations": len(prices)}


_MC_CACHE: dict = {"at": 0.0, "key": None, "val": None}


async def mark_changes(conn, open_positions: list, *, now: float) -> dict:
    """{(slug, side): mark_change_of(...)} for the open positions, from a
    bounded window of recorded observations. Cached briefly per process."""
    keys = sorted({(p.get("us_market_slug"), p.get("holding_side"))
                   for p in open_positions if p.get("us_market_slug")})
    if not keys:
        return {}
    ck = tuple(keys)
    if (_MC_CACHE["key"] == ck and _MC_CACHE["val"] is not None
            and abs(now - _MC_CACHE["at"]) < MARK_CHANGE_CACHE_S):
        return _MC_CACHE["val"]
    slugs = sorted({k[0] for k in keys})
    rows = await conn.fetch(
        "SELECT s.slug, o.observed_at, o.bids, o.offers "
        "  FROM unnest($1::text[]) AS s(slug) "
        "  CROSS JOIN LATERAL ("
        "    SELECT observed_at, bids, offers FROM paper_book_observations "
        "     WHERE us_market_slug = s.slug AND error IS NULL "
        "       AND observed_at >= to_timestamp($2) "
        "     ORDER BY observed_at DESC LIMIT $3) o "
        " ORDER BY s.slug, o.observed_at DESC",
        slugs, float(now) - MARK_HISTORY_S, MARK_HISTORY_ROWS)
    by: dict = {}
    for r in rows:
        md = {"bids": L._j(r["bids"]) or [], "offers": L._j(r["offers"]) or []}
        by.setdefault(r["slug"], []).append((_ep(r["observed_at"]), md))
    out = {k: mark_change_of(by.get(k[0], []), k[1]) for k in keys}
    _MC_CACHE.update(at=now, key=ck, val=out)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE SPLIT (pure)
# ═════════════════════════════════════════════════════════════════════

def _sleeve_of_group(gid, classes: dict, strategy=None) -> tuple:
    c = classes.get(gid)
    if c is None:
        return UNCLASSIFIED, R_NO_DURABLE, None
    return c["sleeve"], c["basis"], c


def _empty(sleeve: str) -> dict:
    return {"sleeve": sleeve, **SLEEVE_META[sleeve],
            "groups": set(), "positions_open": 0, "positions_closed": 0,
            "realized": Decimal(0), "unreal": Decimal(0),
            "cost_basis": Decimal(0), "marked_value": Decimal(0),
            "unmarked_cost": Decimal(0), "unmarked": 0, "stale": 0,
            "fees": Decimal(0), "realized_wins": Decimal(0),
            "realized_losses": Decimal(0), "obs_ats": [],
            "genuine": [], "unchanged_since": [], "strategies": {},
            "bases": {}}


def split(bal: dict, all_positions: list, classes: dict, *, now: float,
          changes: dict | None = None,
          stale_mark_after_s: float = L.MARK_STALE_AFTER_S) -> dict:
    """bettor_paper_ledger.balances output (its open positions carry the
    marks) + every position (open AND closed, bettor_paper_ledger.positions
    include_closed=True) + the durable classifications -> per-sleeve
    economics and the exact account reconciliation. Pure."""
    D = L.D
    changes = changes or {}
    acc = {s: _empty(s) for s in SLEEVES}
    marks_by_key = {p["position_key"]: p for p in bal.get("open_positions")
                    or []}
    for p in all_positions:
        s, basis, c = _sleeve_of_group(p.get("group_id"), classes)
        a = acc[s]
        a["groups"].add(p.get("group_id"))
        st = (c or {}).get("strategy") or p.get("strategy")
        a["strategies"][st] = a["strategies"].get(st, 0) + 1
        a["bases"][basis] = a["bases"].get(basis, 0) + 1
        rz = D(p.get("realized_pnl_usd") or 0)
        a["realized"] += rz
        if rz > 0:
            a["realized_wins"] += rz
        elif rz < 0:
            a["realized_losses"] += rz
        a["fees"] += D(p.get("buy_fees_usd") or 0) + D(
            p.get("sale_fees_usd") or 0)
        if (p.get("open_qty") or 0) > 1e-9:
            a["positions_open"] += 1
            v = marks_by_key.get(p["position_key"]) or {}
            m = v.get("mark") or {}
            cb = D(p.get("cost_basis_usd") or 0)
            a["cost_basis"] += cb
            if m.get("price") is None:
                a["unmarked"] += 1
                a["unmarked_cost"] += cb
            else:
                a["marked_value"] += D(v.get("marked_value_usd") or 0)
                a["unreal"] += D(v.get("unrealized_pnl_usd") or 0)
                at = _ep(m.get("observed_at"))
                if at is not None:
                    a["obs_ats"].append(at)
                    if m.get("stale") or now - at > stale_mark_after_s:
                        a["stale"] += 1
                ch = changes.get((p.get("us_market_slug"),
                                  p.get("holding_side"))) or {}
                if ch.get("changed_at") is not None:
                    a["genuine"].append(ch["changed_at"])
                elif ch.get("unchanged_since_at_least") is not None:
                    a["unchanged_since"].append(
                        ch["unchanged_since_at_least"])
        else:
            a["positions_closed"] += 1
    sleeves = {}
    contrib_sum = Decimal(0)
    for s in SLEEVES:
        a = acc[s]
        net = a["realized"] + a["unreal"]
        contrib_sum += net
        newest = max(a["obs_ats"]) if a["obs_ats"] else None
        oldest = min(a["obs_ats"]) if a["obs_ats"] else None
        genuine = max(a["genuine"]) if a["genuine"] else None
        if not a["positions_open"]:
            mstate = "NO_OPEN_POSITIONS"
        elif a["unmarked"] == a["positions_open"]:
            mstate = "UNMARKED"
        elif a["stale"]:
            mstate = "STALE"
        elif genuine is None or now - genuine > NO_NEW_MARK_AFTER_S:
            mstate = "NO_NEW_MARK"
        else:
            mstate = "FRESH"
        meta = SLEEVE_META[s]
        realized = a["realized"]
        sleeves[s] = {
            "sleeve": s, "title": meta["title"], "purpose": meta["purpose"],
            "economics_label": meta["economics_label"],
            "groups": len(a["groups"]),
            "positions_open": a["positions_open"],
            "positions_closed": a["positions_closed"],
            "realized_pnl_usd": _r2(realized),
            "realized_label": (meta["loss_label"] if realized < 0 else
                               meta["win_label"] if realized > 0 else None),
            "realized_wins_usd": _r2(a["realized_wins"]),
            "realized_losses_usd": _r2(a["realized_losses"]),
            "unrealized_pnl_usd": _r2(a["unreal"]),
            "unrealized_basis": ("marked open positions only (%d of %d); "
                                 "UNMARKED positions add nothing"
                                 % (a["positions_open"] - a["unmarked"],
                                    a["positions_open"])),
            "net_pnl_usd": _r2(net),
            "equity_contribution_usd": _r2(net),
            "equity_method": EQUITY_METHOD["rule"],
            "fees_paid_usd": _r2(a["fees"]),
            "exposure": {"cost_basis_usd": _r2(a["cost_basis"]),
                         "marked_value_usd": _r2(a["marked_value"]),
                         "unmarked_cost_basis_usd": _r2(a["unmarked_cost"]),
                         "unmarked_positions": a["unmarked"]},
            "marks": {
                "state": mstate,
                "newest_observed_at": newest, "oldest_observed_at": oldest,
                "newest_age_s": (None if newest is None
                                 else round(now - newest, 1)),
                "stale_marks": a["stale"],
                "last_genuine_mark_update_at": genuine,
                "last_genuine_mark_update_age_s": (
                    None if genuine is None else round(now - genuine, 1)),
                "unchanged_since_at_least": (min(a["unchanged_since"])
                                             if a["unchanged_since"]
                                             and genuine is None else None),
                "genuine_rule": ("the mark PRICE changed between recorded "
                                 "observations (a book re-read at the same "
                                 "price is not a mark update); window %dh"
                                 % int(MARK_HISTORY_S // 3600))},
            "strategies": {k: v for k, v in a["strategies"].items()},
            "classification_bases": dict(a["bases"]),
        }
    cash = D(bal.get("cash_usd") or 0)
    start = D(bal.get("starting_cash_usd") or L.STARTING_CASH_USD)
    acct_mv = sum((acc[s]["marked_value"] for s in SLEEVES), Decimal(0))
    acct_uc = sum((acc[s]["unmarked_cost"] for s in SLEEVES), Decimal(0))
    account_equity = cash + acct_mv + acct_uc
    accounted = start + contrib_sum
    diff = accounted - account_equity
    return {
        "classifier_version": CLASSIFIER_VERSION,
        "equity_method": EQUITY_METHOD,
        "sleeves": sleeves,
        "accounting": {
            "title": "COMBINED ACCOUNTING TOTAL",
            "starting_cash_usd": _r2(start),
            "sleeve_contributions_usd": _r2(contrib_sum),
            "accounted_equity_usd": _r2(accounted),
            "account_equity_usd": _r2(account_equity),
            "difference_usd": _r2(diff),
            "reconciled": abs(diff) < Decimal("0.005"),
            "rule": ("starting cash + sum of every sleeve's contribution "
                     "(INVESTMENT + TRAINING + BENCHMARK + UNCLASSIFIED) == "
                     "cash + marked value + unmarked positions at cost")},
        "unclassified_positions": (sleeves[UNCLASSIFIED]["positions_open"]
                                   + sleeves[UNCLASSIFIED][
                                       "positions_closed"]),
    }


async def sleeve_book(conn, account_id: str = None, *,
                      now: float | None = None, bal: dict | None = None
                      ) -> dict:
    """The read: balances (or the caller's), every position, the durable
    classifications and the genuine mark changes -> split(). Plain
    SELECTs only."""
    account_id = account_id or await L.selected_account(conn)
    now = float(time.time() if now is None else now)
    if not await schema(conn):
        return {"status": "UNAVAILABLE", "why": R_NO_SCHEMA}
    if bal is None:
        bal = await L.balances(conn, account_id, now=now)
    if not bal or not bal.get("ok"):
        return {"status": "UNAVAILABLE",
                "why": (bal or {}).get("refusal") or "PAPER_NOT_READ"}
    allp = await L.positions(conn, account_id, include_closed=True)
    classes = await classifications(conn, account_id)
    changes = await mark_changes(conn, bal.get("open_positions") or [],
                                 now=now)
    out = split(bal, allp, classes, now=now, changes=changes)
    genuine = [c["changed_at"] for c in changes.values()
               if c.get("changed_at") is not None]
    out.update(status="OK", why=None, as_of=now,
               last_genuine_mark_update_at=max(genuine) if genuine else None,
               source=("paper_sleeve_classifications (migration 223) + "
                       "paper_fills / paper_settlements (positions) + "
                       "paper_book_observations (marks and genuine mark "
                       "changes)"))
    return out
