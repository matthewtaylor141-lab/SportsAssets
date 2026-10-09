"""AUDREY'S INDEPENDENT RECOMPUTE OF THE SHADOW RISK ENGINE (intel/risk.py).

A SECOND, INDEPENDENTLY WRITTEN COMPUTATION PATH. The risk engine builds
positions in Python from raw fills; this path asks Postgres for the same
headline figures with aggregate SQL and shares no code with it -- not the
position builder, not the game-key function, not the open-quantity rule.
What the two share is the SPECIFICATION (intel/risk.py's docstring): open
cost basis = open qty x average acquisition cost incl. fees; game key =
first fill's label event_key, else its fixture, else the slug; an actual
contract is open while held > 0 and it has no paper settlement.

Compared per book (PAPER and ACTUAL separately, never summed):
open_positions, distinct_games (exact), gross_exposure_usd and
max_game_exposure_usd (within TOLERANCE_USD + TOLERANCE_REL x value).

DISAGREEMENT IS A FINDING. Every comparison is persisted to
intel_audrey_risk_checks; a disagreement also writes an Audrey finding
(paper_audrey_findings, kind INTEL_RISK_RECOMPUTE_DISAGREEMENT, WARNING)
under the paper account's active session -- the existing alerts mechanism.
Without an active session the check row records why no finding was filed.

SHADOW: this module reads fills and writes only those two tables. It places,
cancels and sizes nothing.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "AUDREY_INTEL_RISK_RECOMPUTE_V1"
FINDING_KIND = "INTEL_RISK_RECOMPUTE_DISAGREEMENT"
TOLERANCE_USD = 0.01
TOLERANCE_REL = 1e-6
METRICS = ("open_positions", "distinct_games", "gross_exposure_usd",
           "max_game_exposure_usd")
EXACT = ("open_positions", "distinct_games")
PAPER_ACCOUNT = "paper_acct_main"

PAPER_SQL = """
WITH grp AS (
    SELECT DISTINCT group_id FROM paper_fills
     WHERE account_id = $1 AND filled_at >= to_timestamp($2)),
f AS (
    SELECT pf.group_id, pf.us_market_slug, pf.holding_side,
           (array_agg(pf.label ORDER BY pf.filled_at))[1]   AS label,
           (array_agg(pf.fixture ORDER BY pf.filled_at))[1] AS fixture,
           sum(pf.qty) FILTER (WHERE pf.direction = 'BUY')  AS bought,
           sum(pf.gross_usd + pf.fee_usd)
               FILTER (WHERE pf.direction = 'BUY')          AS buy_cost,
           coalesce(sum(pf.qty)
               FILTER (WHERE pf.direction = 'SELL'), 0)     AS sold
      FROM paper_fills pf JOIN grp USING (group_id)
     WHERE pf.account_id = $1
     GROUP BY pf.group_id, pf.us_market_slug, pf.holding_side),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty
      FROM paper_settlements WHERE account_id = $1
     ORDER BY position_key, version DESC),
p AS (
    SELECT coalesce(f.label->>'event_key', f.fixture, f.us_market_slug)
               AS game,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty,
           f.buy_cost / nullif(f.bought, 0)      AS avg_cost
      FROM f LEFT JOIN s
        ON s.position_key = 'paperpos:' || $1 || ':' || f.group_id || ':'
                            || f.us_market_slug || ':' || f.holding_side),
o AS (SELECT * FROM p WHERE open_qty > 1e-9),
g AS (SELECT game, sum(open_qty * avg_cost) AS e FROM o GROUP BY game)
SELECT (SELECT count(*) FROM o)                          AS open_positions,
       (SELECT count(DISTINCT game) FROM o)              AS distinct_games,
       (SELECT coalesce(sum(open_qty * avg_cost), 0) FROM o)
                                                         AS gross_exposure_usd,
       (SELECT coalesce(max(e), 0) FROM g)               AS max_game_exposure_usd
"""

ACTUAL_SQL = """
WITH fills AS (
    SELECT coalesce(f.group_id, '') AS group_id, f.us_market_slug,
           CASE WHEN f.intent LIKE '%SHORT%' THEN 'SHORT' ELSE 'LONG' END
               AS side,
           f.intent LIKE '%BUY%' AS is_buy, f.qty, f.price,
           coalesce(f.fee_usd, 0) AS fee, 'POLYMARKET_US' AS venue
      FROM execmirror_fills f WHERE f.observed_at >= to_timestamp($1)
    UNION ALL
    SELECT coalesce(i.group_id, ''), i.us_market_slug,
           coalesce(i.holding, 'LONG'), k.action = 'buy', k.count, k.price,
           coalesce(k.fee_usd, 0), 'KALSHI'
      FROM kalshi_live_fills k
      LEFT JOIN kalshi_live_intents i ON i.link_id = k.link_id
     WHERE k.observed_at >= to_timestamp($1)),
p AS (
    SELECT venue, group_id, us_market_slug, side,
           sum(qty) FILTER (WHERE is_buy) AS bought,
           sum(qty * CASE WHEN side = 'SHORT' THEN 1 - price ELSE price END
               + fee) FILTER (WHERE is_buy) AS buy_cost,
           coalesce(sum(qty) FILTER (WHERE NOT is_buy), 0) AS sold
      FROM fills GROUP BY venue, group_id, us_market_slug, side),
o AS (
    SELECT us_market_slug AS game, bought - sold AS open_qty,
           buy_cost / nullif(bought, 0) AS avg_cost
      FROM p
     WHERE bought - sold > 1e-9
       AND NOT EXISTS (SELECT 1 FROM paper_settlements ps
                        WHERE ps.us_market_slug = p.us_market_slug)),
g AS (SELECT game, sum(open_qty * avg_cost) AS e FROM o GROUP BY game)
SELECT (SELECT count(*) FROM o)                          AS open_positions,
       (SELECT count(DISTINCT game) FROM o)              AS distinct_games,
       (SELECT coalesce(sum(open_qty * avg_cost), 0) FROM o)
                                                         AS gross_exposure_usd,
       (SELECT coalesce(max(e), 0) FROM g)               AS max_game_exposure_usd
"""


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


async def recompute(conn, *, book: str, now: float,
                    account_id: str = None,
                    days: float = 45.0) -> dict:
    since = float(now) - float(days) * 86400.0
    if book == "PAPER":
        if account_id is None:
            from ..simulated_account_context import selected_account
            account_id = await selected_account(conn)
        r = await conn.fetchrow(PAPER_SQL, account_id, since)
    else:
        r = await conn.fetchrow(ACTUAL_SQL, since)
    return {m: _f(r[m]) for m in METRICS}


def compare(primary: dict, audrey: dict) -> list:
    """[{metric, primary_value, audrey_value, abs_diff, tolerance, agrees}]."""
    out = []
    for m in METRICS:
        a, b = _f(primary.get(m)), _f(audrey.get(m))
        tol = 0.0 if m in EXACT else (
            TOLERANCE_USD + TOLERANCE_REL * max(abs(a or 0.0), abs(b or 0.0)))
        if a is None or b is None:
            out.append({"metric": m, "primary_value": a, "audrey_value": b,
                        "abs_diff": None, "tolerance": tol, "agrees": None,
                        "why": ("PRIMARY_UNMEASURED" if a is None
                                else "AUDREY_UNMEASURED")})
            continue
        d = abs(a - b)
        out.append({"metric": m, "primary_value": a, "audrey_value": b,
                    "abs_diff": round(d, 9), "tolerance": tol,
                    "agrees": d <= tol + 1e-12})
    return out


async def _finding(conn, *, book, run_id, disagreements, now,
                   account_id) -> tuple:
    sess = await conn.fetchrow(
        "SELECT session_id FROM paper_sessions WHERE account_id = $1 "
        "   AND status = 'ACTIVE' ORDER BY started_at DESC LIMIT 1",
        account_id) if await conn.fetchval(
            "SELECT to_regclass('paper_sessions') IS NOT NULL") else None
    if sess is None:
        return None, "NO_ACTIVE_PAPER_SESSION_TO_FILE_THE_FINDING_UNDER"
    subject = "intel_risk:%s:%s" % (book, run_id)
    fid = "paperfind:" + hashlib.sha256(
        ("%s:%s:%s" % (sess["session_id"], FINDING_KIND, subject)).encode()
    ).hexdigest()[:24]
    await conn.execute(
        "INSERT INTO paper_audrey_findings (finding_id, session_id, "
        " account_id, found_at, kind, severity, subject, detail) "
        "VALUES ($1,$2,$3,to_timestamp($4),$5,'WARNING',$6,$7::jsonb) "
        "ON CONFLICT (finding_id) DO NOTHING",
        fid, sess["session_id"], account_id, float(now), FINDING_KIND,
        subject, json.dumps({
            "book": book, "run_id": run_id, "version": VERSION,
            "label": "SHADOW", "disagreements": disagreements,
            "what": ("the shadow risk engine and Audrey's independent SQL "
                     "recompute disagree beyond tolerance"),
            "authority": "NONE: a finding opens investigation only"},
            default=str))
    return fid, None


async def check(conn, *, run_id: str, book: str, primary: dict, now: float,
                account_id: str = None) -> dict:
    """Recompute, compare, persist every comparison, file a finding on a
    disagreement. Returns the comparison."""
    if account_id is None:
        from ..simulated_account_context import selected_account
        account_id = await selected_account(conn)
    audrey = await recompute(conn, book=book, now=now, account_id=account_id)
    rows = compare(primary, audrey)
    bad = [r for r in rows if r["agrees"] is False]
    fid, why = (None, None)
    if bad:
        fid, why = await _finding(conn, book=book, run_id=run_id,
                                  disagreements=bad, now=now,
                                  account_id=account_id)
    for r in rows:
        await conn.execute(
            "INSERT INTO intel_audrey_risk_checks (run_id, book, metric, "
            " computed_at, primary_value, audrey_value, abs_diff, tolerance, "
            " agrees, finding_id, detail) VALUES ($1,$2,$3,to_timestamp($4),"
            " $5,$6,$7,$8,$9,$10,$11::jsonb) "
            "ON CONFLICT (run_id, book, metric) DO NOTHING",
            run_id, book, r["metric"], float(now), r["primary_value"],
            r["audrey_value"], r["abs_diff"], r["tolerance"], r["agrees"],
            fid if r["agrees"] is False else None,
            json.dumps({"version": VERSION, "why": r.get("why"),
                        "finding_unwritten_reason": (
                            why if r["agrees"] is False else None)}))
    return {"book": book, "version": VERSION, "audrey": audrey,
            "comparisons": rows, "agrees": not bad,
            "finding_id": fid, "finding_unwritten_reason": why,
            "label": "SHADOW"}
