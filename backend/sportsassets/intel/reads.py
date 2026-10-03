"""READ-ONLY loaders shared by the SHADOW intelligence modules.

Every function here issues SELECTs and nothing else (pinned by
tests/test_intel_is_shadow_only.py). Every read is bounded: a lookback
window and a row LIMIT, so a cycle's cost does not grow with history.
"""
from __future__ import annotations

from . import common as C

MAX_ROWS = 20000


async def premap(conn, slugs) -> dict:
    """{market_slug: {team_league, sports_type, game_start, team_name,
    event_slug}} from the venue's own pre-map (us_premap). Absent -> absent:
    a caller treats a missing slug as UNKNOWN, never as a default."""
    slugs = sorted({s for s in slugs if s})
    if not slugs:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (market_slug) market_slug, team_league, "
        "       sports_type, team_name, event_slug, "
        "       extract(epoch FROM game_start)::float8 AS game_start "
        "  FROM us_premap WHERE market_slug = ANY($1::text[]) "
        " ORDER BY market_slug, updated_at DESC NULLS LAST", slugs)
    return {r["market_slug"]: dict(r) for r in rows}


async def latest_books(conn, slugs, *, max_age_s=None, now=None) -> dict:
    """{slug: book_view + observed_at} from the latest readable
    paper_book_observations row per slug."""
    slugs = sorted({s for s in slugs if s})
    if not slugs:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (us_market_slug) us_market_slug, bids, offers, "
        "       extract(epoch FROM observed_at)::float8 AS observed_at "
        "  FROM paper_book_observations "
        " WHERE us_market_slug = ANY($1::text[]) AND error IS NULL "
        " ORDER BY us_market_slug, observed_at DESC", slugs)
    out = {}
    for r in rows:
        if (max_age_s is not None and now is not None
                and now - float(r["observed_at"]) > max_age_s):
            continue
        v = C.book_view(r["bids"], r["offers"])
        v["observed_at"] = float(r["observed_at"])
        out[r["us_market_slug"]] = v
    return out


async def valuations_by_id(conn, ids) -> dict:
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids:
        return {}
    rows = await conn.fetch(
        "SELECT id, version, devig_method, sport_family, market, event_key, "
        "       probability, outcome_known, outcome, outcome_basis, "
        "       buy_intent, settlement_rule, us_market_slug "
        "  FROM external_valuations WHERE id = ANY($1::bigint[])", ids)
    return {int(r["id"]): dict(r) for r in rows}


async def entry_groups(conn, decision_ids) -> dict:
    """{decision_id: group_id} of each decision's ENTRY paper order."""
    ids = sorted({d for d in decision_ids if d})
    if not ids:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (decision_id) decision_id, group_id "
        "  FROM paper_orders WHERE decision_id = ANY($1::text[]) "
        "   AND role = 'ENTRY' ORDER BY decision_id, created_at", ids)
    return {r["decision_id"]: r["group_id"] for r in rows}


async def latest_settlements(conn, *, group_ids=None, account_id=None) -> dict:
    """{position_key: latest-version settlement row}."""
    if group_ids is not None:
        gids = sorted({g for g in group_ids if g})
        if not gids:
            return {}
        rows = await conn.fetch(
            "SELECT DISTINCT ON (position_key) position_key, group_id, "
            "       us_market_slug, holding_side, qty, outcome, "
            "       payout_per_contract, payout_usd, evidence_source, "
            "       extract(epoch FROM settled_at)::float8 AS settled_at "
            "  FROM paper_settlements WHERE group_id = ANY($1::text[]) "
            " ORDER BY position_key, version DESC", gids)
    else:
        rows = await conn.fetch(
            "SELECT DISTINCT ON (position_key) position_key, group_id, "
            "       us_market_slug, holding_side, qty, outcome, "
            "       payout_per_contract, payout_usd, evidence_source, "
            "       extract(epoch FROM settled_at)::float8 AS settled_at "
            "  FROM paper_settlements WHERE account_id = $1 "
            " ORDER BY position_key, version DESC LIMIT %d" % MAX_ROWS,
            account_id)
    return {r["position_key"]: dict(r) for r in rows}


def settlement_for(settlements: dict, group_id, slug, side):
    for s in settlements.values():
        if (s.get("group_id") == group_id and s.get("us_market_slug") == slug
                and str(s.get("holding_side")) == str(side)):
            return s
    return None
