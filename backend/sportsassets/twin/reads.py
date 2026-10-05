"""READ-ONLY, BOUNDED LOADERS OF THE RECORDED STREAM.

Every function here issues SELECTs and nothing else (pinned by
tests/test_twin_authority.py). Every read is bounded: a window and a row
LIMIT, so a cycle's cost does not grow with history. Every record carries
`at` -- the epoch instant it became knowable (a decision's decided_at, a
fill's filled_at, a settlement's settled_at, a book's observed_at, a regime
state's computed_at, a Karen challenge's challenged_at). The engine's time
view filters on it; nothing here decides anything.

THE INTERFACES of the parallel streams are read only through the
pos_iface_* views, and only when they exist with the required columns:

  pos_iface_eddie_execution        (217 ARCHER)  decision_id, at, qty,
      baseline_vwap, eddie_vwap, baseline_fee_usd, eddie_fee_usd
      [optional: baseline_fill_ratio, eddie_fill_ratio, baseline_markout_pc,
       eddie_markout_pc, spread_pc, capital_hours_saved]
  pos_iface_scout_feature_effects  (217 SCOUT)  feature_id, decision_id, at,
      p_with, p_without, status  [optional: outcome]
  pos_iface_tournament_verdicts    (218)        tournament_id, kind,
      champion, challenger, verdict, evidence_n, p_value, decided_at
"""
from __future__ import annotations

from . import common as C

MAX_DECISIONS = 5000
MAX_ROWS = 20000

IFACE = {
    "ARCHER": ("pos_iface_eddie_execution",
              ("decision_id", "at", "qty", "baseline_vwap", "eddie_vwap",
               "baseline_fee_usd", "eddie_fee_usd"),
              ("baseline_fill_ratio", "eddie_fill_ratio",
               "baseline_markout_pc", "eddie_markout_pc", "spread_pc",
               "capital_hours_saved"), "claude/pos-agents (217)"),
    "SCOUT": ("pos_iface_scout_feature_effects",
              ("feature_id", "decision_id", "at", "p_with", "p_without",
               "status"), ("outcome",), "claude/pos-agents (217)"),
    "TOURNAMENT": ("pos_iface_tournament_verdicts",
                   ("tournament_id", "kind", "champion", "challenger",
                    "verdict", "evidence_n", "p_value", "decided_at"), (),
                   "claude/pos-learn (218)"),
}


async def regclass(conn, name: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    name))


async def columns(conn, name: str) -> set:
    return {r["column_name"] for r in await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        " WHERE table_name = $1", name)}


async def iface(conn, key: str, *, since: float, until: float,
                limit: int = MAX_ROWS) -> tuple:
    """(rows, None) when the interface view exists with its required
    columns, else (None, reason). Rows carry `at` (epoch)."""
    view, req, opt, owner = IFACE[key]
    if not await regclass(conn, view):
        return None, ("INTERFACE_ABSENT:%s (provided by %s)" % (view, owner))
    cols = await columns(conn, view)
    missing = [c for c in req if c not in cols]
    if missing:
        return None, ("INTERFACE_INCOMPLETE:%s missing %s" % (
            view, ",".join(missing)))
    tcol = "decided_at" if key == "TOURNAMENT" else "at"
    sel = [c for c in req + opt if c in cols and c != tcol]
    rows = await conn.fetch(
        "SELECT %s, extract(epoch FROM %s)::float8 AS at_epoch FROM %s "
        " WHERE %s >= to_timestamp($1) AND %s <= to_timestamp($2) "
        " ORDER BY %s DESC LIMIT %d" % (
            ", ".join(sel), tcol, view, tcol, tcol, tcol, int(limit)),
        float(since), float(until))
    out = []
    for r in rows:
        d = dict(r)
        d["at"] = d.pop("at_epoch")
        out.append(d)
    return out, None


# ── THE PAPER BOOK ───────────────────────────────────────────────────

DECISIONS_SQL = """
    SELECT decision_id, extract(epoch FROM decided_at)::float8 AS at,
           verdict, refusal, valuation_id, us_market_slug, holding_side,
           p_pinnacle, p_blended, p_internal, limit_price, proposed_qty,
           economics, strategy, label, book_obs_id, account_id
      FROM paper_decisions
     WHERE decided_at >= to_timestamp($1) AND decided_at <= to_timestamp($2)
       AND ($3::text IS NULL OR account_id = $3)
     ORDER BY decided_at DESC, decision_id DESC LIMIT $4
"""


async def paper_raw(conn, *, since, until, account_id, limit=MAX_DECISIONS):
    decs = [dict(r) for r in await conn.fetch(
        DECISIONS_SQL, float(since), float(until), account_id, int(limit))]
    decs.reverse()
    ids = [d["decision_id"] for d in decs]
    groups = {r["decision_id"]: r["group_id"] for r in await conn.fetch(
        "SELECT DISTINCT ON (decision_id) decision_id, group_id "
        "  FROM paper_orders WHERE decision_id = ANY($1::text[]) "
        "   AND role = 'ENTRY' ORDER BY decision_id, created_at",
        ids)} if ids else {}
    gids = sorted(set(groups.values()))
    fills = [dict(r) for r in await conn.fetch(
        "SELECT fill_id, group_id, role, direction, holding_side, "
        "       us_market_slug, qty, price, fee_usd, gross_usd, "
        "       extract(epoch FROM filled_at)::float8 AS at "
        "  FROM paper_fills WHERE group_id = ANY($1::text[]) "
        " ORDER BY filled_at, fill_id", gids)] if gids else []
    slugs = sorted({d["us_market_slug"] for d in decs
                    if d.get("us_market_slug")})
    setts = [dict(r) for r in await conn.fetch(
        "SELECT DISTINCT ON (position_key) position_key, group_id, "
        "       us_market_slug, holding_side, qty, outcome, "
        "       payout_per_contract, "
        "       extract(epoch FROM settled_at)::float8 AS at "
        "  FROM paper_settlements WHERE us_market_slug = ANY($1::text[]) "
        " ORDER BY position_key, version DESC LIMIT %d" % MAX_ROWS,
        slugs)] if slugs else []
    return {"decisions": decs, "groups": groups, "fills": fills,
            "settlements": setts}


# ── THE ACTUAL BOOK (execution mirror) ───────────────────────────────

async def actual_raw(conn, *, since, until, limit=MAX_DECISIONS):
    if not await regclass(conn, "execution_intents"):
        return {"intents": [], "fills": [], "decisions": {},
                "settlements": [], "why": "EXECUTION_INTENTS_ABSENT"}
    intents = [dict(r) for r in await conn.fetch(
        "SELECT intent_id, decision_id, valuation_id, group_id, "
        "       us_market_slug, order_intent, holding_side, wire_price, "
        "       strategy, extract(epoch FROM decided_at)::float8 AS at "
        "  FROM execution_intents "
        " WHERE decided_at >= to_timestamp($1) "
        "   AND decided_at <= to_timestamp($2) "
        "   AND actual_mirror_id IS NOT NULL "
        " ORDER BY decided_at DESC LIMIT $3",
        float(since), float(until), int(limit))]
    intents.reverse()
    gids = sorted({i["group_id"] for i in intents if i.get("group_id")})
    fills = [dict(r) for r in await conn.fetch(
        "SELECT f.fill_key, f.group_id, f.us_market_slug, f.intent, f.qty, "
        "       f.price, f.fee_usd, o.role, "
        "       extract(epoch FROM f.observed_at)::float8 AS at "
        "  FROM execmirror_fills f JOIN execmirror_orders o "
        "    ON o.mirror_id = f.mirror_id "
        " WHERE f.group_id = ANY($1::text[]) "
        " ORDER BY f.observed_at, f.fill_key", gids)] if gids else []
    dids = sorted({i["decision_id"] for i in intents if i.get("decision_id")})
    decs = {r["decision_id"]: dict(r) for r in await conn.fetch(
        "SELECT decision_id, extract(epoch FROM decided_at)::float8 AS at, "
        "       verdict, valuation_id, us_market_slug, holding_side, "
        "       p_pinnacle, p_blended, p_internal, limit_price, "
        "       proposed_qty, economics, strategy, label "
        "  FROM paper_decisions WHERE decision_id = ANY($1::text[])",
        dids)} if dids else {}
    slugs = sorted({i["us_market_slug"] for i in intents
                    if i.get("us_market_slug")})
    setts = [dict(r) for r in await conn.fetch(
        "SELECT DISTINCT ON (position_key) position_key, group_id, "
        "       us_market_slug, holding_side, qty, outcome, "
        "       payout_per_contract, "
        "       extract(epoch FROM settled_at)::float8 AS at "
        "  FROM paper_settlements WHERE us_market_slug = ANY($1::text[]) "
        " ORDER BY position_key, version DESC LIMIT %d" % MAX_ROWS,
        slugs)] if slugs else []
    return {"intents": intents, "fills": fills, "decisions": decs,
            "settlements": setts, "why": None}


# ── SHARED CONTEXT ───────────────────────────────────────────────────

async def theses(conn, group_ids) -> list:
    gids = sorted({g for g in group_ids if g})
    if not gids or not await regclass(conn, "xavier_entry_theses"):
        return []
    return [dict(r) for r in await conn.fetch(
        "SELECT thesis_id, position_kind, group_id, decision_id, "
        "       counterfactuals, entry_qty, entry_cost_usd, "
        "       extract(epoch FROM recorded_at)::float8 AS at "
        "  FROM xavier_entry_theses WHERE group_id = ANY($1::text[]) "
        " ORDER BY recorded_at", gids)]


async def valuations(conn, ids) -> dict:
    """Sport, probability and the venue-verified outcome (with the instant
    it became known) of each decision's valuation, by id. Both record
    purposes: the twin selects no candidate (classified in
    tests/test_calibration_only_records_cannot_trade.py READERS)."""
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids:
        return {}
    rows = await conn.fetch(
        "SELECT id, sport_family, probability, us_market_slug, "
        "       outcome_known, outcome, outcome_basis, "
        "       extract(epoch FROM outcome_at)::float8 AS outcome_at, "
        "       extract(epoch FROM decided_at)::float8 AS at "
        "  FROM external_valuations WHERE id = ANY($1::bigint[])", ids)
    return {int(r["id"]): dict(r) for r in rows}


async def premap(conn, slugs) -> dict:
    slugs = sorted({s for s in slugs if s})
    if not slugs or not await regclass(conn, "us_premap"):
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (market_slug) market_slug, sports_type, "
        "       team_league, event_slug, "
        "       extract(epoch FROM game_start)::float8 AS game_start "
        "  FROM us_premap WHERE market_slug = ANY($1::text[]) "
        " ORDER BY market_slug, updated_at DESC NULLS LAST", slugs)
    return {r["market_slug"]: dict(r) for r in rows}


async def regimes(conn, *, since, until) -> list:
    if not await regclass(conn, "intel_regime_states"):
        return []
    return [dict(r) for r in await conn.fetch(
        "SELECT run_id, recommendation, "
        "       extract(epoch FROM computed_at)::float8 AS at "
        "  FROM intel_regime_states WHERE computed_at >= to_timestamp($1) "
        "   AND computed_at <= to_timestamp($2) "
        " ORDER BY computed_at DESC LIMIT %d" % MAX_ROWS,
        float(since), float(until))][::-1]


async def allocations(conn, decision_ids, *, since, until) -> list:
    ids = sorted({d for d in decision_ids if d})
    if not ids or not await regclass(conn, "intel_allocations"):
        return []
    return [dict(r) for r in await conn.fetch(
        "SELECT run_id, decision_id, shadow_usd, rank, "
        "       extract(epoch FROM computed_at)::float8 AS at "
        "  FROM intel_allocations WHERE decision_id = ANY($1::text[]) "
        "   AND computed_at >= to_timestamp($2) "
        "   AND computed_at <= to_timestamp($3) "
        " ORDER BY computed_at DESC LIMIT %d" % MAX_ROWS,
        ids, float(since), float(until))][::-1]


async def karen_blocks(conn, *, since, until) -> list:
    if not await regclass(conn, "karen_challenges"):
        return []
    return [dict(r) for r in await conn.fetch(
        "SELECT challenge_id, target_agent, target_kind, target_id, "
        "       evidence_refs, state, outcome, false_block, "
        "       extract(epoch FROM challenged_at)::float8 AS at "
        "  FROM karen_challenges WHERE blocked "
        "   AND challenged_at >= to_timestamp($1) "
        "   AND challenged_at <= to_timestamp($2) "
        " ORDER BY challenged_at DESC LIMIT %d" % MAX_ROWS,
        float(since), float(until))][::-1]


# ── CROSS-SPORT TRANSFER OBSERVATIONS ────────────────────────────────

async def resolved_predictions(conn, *, since, until,
                               limit=MAX_ROWS) -> list:
    """One resolved prediction per (event, contract selection): the first
    among the most recent `limit` resolved rows of the window (later
    re-quotes of the same event are dependent). Both record purposes:
    calibration is what the rows exist for; nothing here selects a
    candidate. Bounded like intel.calibration's read: window + ORDER BY
    decided_at DESC + LIMIT."""
    rows = await conn.fetch(
        "SELECT * FROM (SELECT DISTINCT ON (event_key, contract_selection) "
        "       id, sport_family, probability, outcome, us_market_slug, at "
        "  FROM (SELECT id, event_key, contract_selection, sport_family, "
        "               probability, outcome, us_market_slug, "
        "               extract(epoch FROM decided_at)::float8 AS at "
        "          FROM external_valuations "
        "         WHERE decided_at >= to_timestamp($1) "
        "           AND decided_at <= to_timestamp($2) "
        "           AND outcome_known AND outcome IN (0, 1) "
        "           AND probability IS NOT NULL "
        "         ORDER BY decided_at DESC LIMIT $3) r "
        " ORDER BY event_key, contract_selection, at) x ORDER BY at",
        float(since), float(until), int(limit))
    return [dict(r) for r in rows]


async def book_samples(conn, slug_sport: dict, *, since, until,
                       limit=MAX_ROWS) -> list:
    """One book observation per market-hour (consecutive reads of a market
    are dependent) for the markets BETTOR evaluated (`slug_sport`: slug ->
    sport, from the decisions' valuations), through the (slug, observed_at)
    index."""
    slugs = sorted(s for s, sp in slug_sport.items()
                   if s and sp and sp != "unknown")
    if not slugs:
        return []
    rows = await conn.fetch(
        "SELECT * FROM (SELECT DISTINCT ON (us_market_slug, "
        "                            date_trunc('hour', observed_at)) "
        "               us_market_slug, bids, offers, "
        "               extract(epoch FROM observed_at)::float8 AS at "
        "          FROM paper_book_observations "
        "         WHERE us_market_slug = ANY($1::text[]) AND error IS NULL "
        "           AND observed_at >= to_timestamp($2) "
        "           AND observed_at <= to_timestamp($3) "
        "         ORDER BY us_market_slug, date_trunc('hour', observed_at), "
        "                  observed_at) b ORDER BY at DESC LIMIT $4",
        slugs, float(since), float(until), int(limit))
    out = []
    for r in rows:
        v = C.book_view(r["bids"], r["offers"])
        out.append({"slug": r["us_market_slug"], "at": float(r["at"]),
                    "sport": slug_sport[r["us_market_slug"]],
                    "spread": v["spread"], "depth_usd": v["top5_depth_usd"],
                    "mid": v["mid"]})
    return out


async def markout_mids(conn, items, *, lo_s=60.0, hi_s=900.0) -> dict:
    """{(slug, t): mid} of the first readable book observed in
    [t + lo_s, t + hi_s] after a fill instant t. Used only to SCORE a fill
    after the fact (adverse selection), never by a decision."""
    items = sorted({(s, float(t)) for s, t in items if s and t is not None})
    if not items:
        return {}
    rows = await conn.fetch(
        "SELECT x.slug, x.t, b.bids, b.offers "
        "  FROM unnest($1::text[], $2::float8[]) AS x(slug, t) "
        "  CROSS JOIN LATERAL (SELECT bids, offers FROM "
        "       paper_book_observations WHERE us_market_slug = x.slug "
        "       AND error IS NULL "
        "       AND observed_at >= to_timestamp(x.t + $3) "
        "       AND observed_at <= to_timestamp(x.t + $4) "
        "       ORDER BY observed_at LIMIT 1) b",
        [s for s, _ in items], [t for _, t in items], float(lo_s),
        float(hi_s))
    return {(r["slug"], float(r["t"])): C.book_view(r["bids"],
                                                    r["offers"])["mid"]
            for r in rows}


async def point_books(conn, items) -> list:
    """The latest readable book AT OR BEFORE each requested (slug, instant):
    what a world deciding at that instant could have seen. The engine's
    time view filters again on `at`."""
    items = sorted({(s, float(t)) for s, t in items if s and t is not None})
    if not items:
        return []
    rows = await conn.fetch(
        "SELECT DISTINCT b.obs_id, b.us_market_slug, b.bids, b.offers, b.at "
        "  FROM unnest($1::text[], $2::float8[]) AS x(slug, t) "
        "  CROSS JOIN LATERAL (SELECT obs_id, us_market_slug, bids, offers, "
        "       extract(epoch FROM observed_at)::float8 AS at "
        "       FROM paper_book_observations WHERE us_market_slug = x.slug "
        "       AND error IS NULL AND observed_at <= to_timestamp(x.t) "
        "       ORDER BY observed_at DESC LIMIT 1) b",
        [s for s, _ in items], [t for _, t in items])
    out = []
    for r in sorted(rows, key=lambda x: (x["at"], x["obs_id"])):
        v = C.book_view(r["bids"], r["offers"])
        out.append({"obs_id": int(r["obs_id"]), "slug": r["us_market_slug"],
                    "at": float(r["at"]), "best_bid": v["best_bid"],
                    "best_offer": v["best_offer"], "spread": v["spread"],
                    "mid": v["mid"], "depth_usd": v["top5_depth_usd"],
                    "bids": v["bids"], "offers": v["offers"]})
    return out
