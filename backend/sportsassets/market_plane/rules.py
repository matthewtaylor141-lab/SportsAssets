"""THE CURRENT RULES BLOCK OF EVERY CONTRACT (migration 312 market_plane_rules).

MARKET DATA / RESEARCH ONLY -- no order, cancel, size, funding or authority.

Two writers, both of text the venue already handed us:

  * POLYMARKET US: `workers/premap` walks the venue's event board for the
    catalogue, and every market in that payload carries its own `description`
    (tests/fixtures/pmus_*listing*.json and research/beta48/shadow/
    fixtures_events_block3.json show it on events.list's inline markets). The
    sweep hands each market here AT NO EXTRA VENUE REQUEST. The field order is
    bettor_live_read.RULES_TEXT_FIELDS (pinned equal by test), so the board
    capture and the per-candidate read name the same field.
  * KALSHI: market_plane.kalshi_catalogue (GET-only) hands each market's
    `rules_primary` / `rules_secondary`.

One row per contract, written ONLY when its fingerprint changes (an
unchanged block costs one indexed read per batch, never a write). A changed
block is a new interpretation: the row is replaced (its parse with it) and a
RULES_CHANGED event names both fingerprints in market_plane_events, so the
settlement state is recomputed from the new text on the next coverage pass
and nothing certified against the old text carries forward. A listing that
carries NO rules field is recorded as such (rules_published = false): that
is the venue's own answer, read, and the only evidence that may make a
contract EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE.
"""
from __future__ import annotations

import json
import time

from .. import settlement_rule_registry as SRR

VERSION = "MARKET_PLANE_RULES_V1"
AUTHORITY = "MARKET_DATA_ONLY_NO_ORDER_AUTHORITY"
#: bettor_live_read.RULES_TEXT_FIELDS (pinned equal by test; not imported so
#: the market plane does not load the live reader and its venue client)
RULES_TEXT_FIELDS = ("description", "assetPriceTerms", "rules",
                     "resolutionSource", "resolutionCriteria")
SOURCE_PMUS_BOARD = "pmus:events.list inline market (premap sweep)"
SOURCE_KALSHI = "kalshi:GET /markets?series_ticker (kalshi_catalogue)"
#: the process-local fingerprint cache that lets an unchanged sweep skip the
#: database entirely (cleared, never trusted across a restart)
_SEEN: dict = {}
_SEEN_MAX = 400_000


def pmus_row(m: dict) -> dict | None:
    """PURE. One Polymarket US market object -> its rules row, or None
    without a slug."""
    slug = (m or {}).get("slug")
    if not slug:
        return None
    field, text = None, None
    for f in RULES_TEXT_FIELDS:
        v = m.get(f)
        if isinstance(v, str) and v.strip():
            field, text = f, v.strip()
            break
    st = m.get("sportsMarketTypeV2") or m.get("sportsMarketType")
    ev = SRR.polymarket_us_rule_evidence(text, sports_market_type=st)
    ev["rules_field"] = field
    ev["fields_present"] = [f for f in RULES_TEXT_FIELDS
                            if isinstance(m.get(f), str) and m.get(f).strip()]
    return {"contract_id": str(slug), "venue": SRR.POLYMARKET_US,
            "rules_published": text is not None, "rules_field": field,
            "rules_sha256": ev["rules_sha256"], "rules_text": text,
            "rules_secondary": None, "parse_status": ev["status"],
            "evidence": ev, "parser_version": SRR.PARSER_VERSION,
            "source": SOURCE_PMUS_BOARD}


def kalshi_contract_id(ticker) -> str:
    return "kalshi:%s" % str(ticker)


def kalshi_row(market: dict) -> dict | None:
    """PURE. One Kalshi market object -> its rules row (contract_id
    'kalshi:'+ticker), or None without a ticker."""
    t = (market or {}).get("ticker")
    if not t:
        return None
    ev = SRR.kalshi_rule_evidence(market)
    p, s = SRR.kalshi_rules_text(market)
    field = "+".join(f for f, v in (("rules_primary", p),
                                    ("rules_secondary", s)) if v) or None
    return {"contract_id": kalshi_contract_id(t), "venue": SRR.KALSHI,
            "rules_published": bool(p or s), "rules_field": field,
            "rules_sha256": ev["rules_sha256"], "rules_text": p or None,
            "rules_secondary": s or None, "parse_status": ev["status"],
            "evidence": ev, "parser_version": SRR.PARSER_VERSION,
            "source": SOURCE_KALSHI}


UPSERT_SQL = """
    INSERT INTO market_plane_rules (contract_id, venue, rules_published,
        rules_field, rules_sha256, rules_text, rules_secondary, parse_status,
        evidence, parser_version, source, observed_at, label, authority)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,to_timestamp($12),
            'RESEARCH',$13)
    ON CONFLICT (contract_id) DO UPDATE SET
        venue = excluded.venue, rules_published = excluded.rules_published,
        rules_field = excluded.rules_field,
        rules_sha256 = excluded.rules_sha256,
        rules_text = excluded.rules_text,
        rules_secondary = excluded.rules_secondary,
        parse_status = excluded.parse_status, evidence = excluded.evidence,
        parser_version = excluded.parser_version, source = excluded.source,
        observed_at = excluded.observed_at
    WHERE market_plane_rules.rules_sha256 IS DISTINCT FROM excluded.rules_sha256
       OR market_plane_rules.parser_version IS DISTINCT FROM
          excluded.parser_version
       OR market_plane_rules.rules_published IS DISTINCT FROM
          excluded.rules_published
"""

EVENT_SQL = """
    INSERT INTO market_plane_events (event_key, contract_id, kind, payload,
                                     at, label, authority)
    VALUES ($1, $2, 'RULES_CHANGED', $3::jsonb, clock_timestamp(),
            'RESEARCH', $4)
    ON CONFLICT (event_key) DO NOTHING
"""


def _key(r: dict) -> tuple:
    return (r.get("rules_sha256"), r.get("parser_version"),
            bool(r.get("rules_published")))


async def upsert(conn, rows, *, now: float | None = None) -> dict:
    """Write the rows whose fingerprint (or parser version, or published
    flag) changed; append RULES_CHANGED for a contract whose previously
    captured fingerprint differs. Unchanged rows are skipped by the
    process-local cache first, then by the database's own row."""
    at = float(now if now is not None else time.time())
    rows = [r for r in rows if r]
    out = {"offered": len(rows), "unchanged_cached": 0, "written": 0,
           "changed": 0, "new": 0}
    todo = []
    for r in rows:
        if _SEEN.get(r["contract_id"]) == _key(r):
            out["unchanged_cached"] += 1
        else:
            todo.append(r)
    if not todo:
        return out
    prior = {}
    ids = [r["contract_id"] for r in todo]
    for i in range(0, len(ids), 5000):
        for p in await conn.fetch(
                "SELECT contract_id, rules_sha256, parser_version, "
                "       rules_published FROM market_plane_rules "
                " WHERE contract_id = ANY($1::text[])", ids[i:i + 5000]):
            prior[p["contract_id"]] = (p["rules_sha256"], p["parser_version"],
                                       bool(p["rules_published"]))
    batch, events = [], []
    for r in todo:
        cid = r["contract_id"]
        k = _key(r)
        was = prior.get(cid)
        if was == k:
            _SEEN[cid] = k
            continue
        batch.append((cid, r["venue"], bool(r["rules_published"]),
                      r.get("rules_field"), r.get("rules_sha256"),
                      r.get("rules_text"), r.get("rules_secondary"),
                      r["parse_status"],
                      json.dumps(r.get("evidence") or {}, default=str),
                      r["parser_version"], r["source"], at, AUTHORITY))
        if was is None:
            out["new"] += 1
        elif was[0] != k[0] or was[2] != k[2]:
            out["changed"] += 1
            events.append((
                "rules:%s:%s:%s" % (cid, was[0] or "none", k[0] or "none"),
                cid, json.dumps({
                    "previous_rules_sha256": was[0],
                    "rules_sha256": k[0],
                    "previous_published": was[2], "published": k[2],
                    "parse_status": r["parse_status"],
                    "parser_version": r["parser_version"],
                    "rules_field": r.get("rules_field"),
                    "venue": r["venue"],
                    "effect": ("the prior interpretation is invalidated; the "
                               "settlement state is recomputed from this "
                               "text")}), AUTHORITY))
    if batch:
        async with conn.transaction():
            for i in range(0, len(batch), 1000):
                await conn.executemany(UPSERT_SQL, batch[i:i + 1000])
            for i in range(0, len(events), 1000):
                await conn.executemany(EVENT_SQL, events[i:i + 1000])
        out["written"] = len(batch)
    if len(_SEEN) > _SEEN_MAX:
        _SEEN.clear()
    for r in todo:
        _SEEN[r["contract_id"]] = _key(r)
    return out


_TABLE_STATE = {"present": None, "at": 0.0}


async def table_present(conn, *, reprobe_s: float = 600.0) -> bool:
    """migration 312 applied (market_plane_rules exists)? Cached; a missing
    table is re-probed every `reprobe_s`. Unreadable is absent."""
    st = _TABLE_STATE
    now = time.time()
    if st["present"] is True or (st["present"] is False
                                 and now - st["at"] < reprobe_s):
        return bool(st["present"])
    try:
        present = bool(await conn.fetchval(
            "SELECT to_regclass('market_plane_rules') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        present = False
    st["present"], st["at"] = present, now
    return present


async def capture_pmus_markets(pool_or_conn, markets) -> dict:
    """THE PREMAP HOOK: the event board's market objects -> rules rows.
    Never raises (a capture failure never costs the sweep a row); no venue
    request is made here."""
    try:
        if not await table_present(pool_or_conn):
            return {"skipped": "MIGRATION_312_NOT_APPLIED"}
        rows = [pmus_row(m) for m in (markets or ()) if isinstance(m, dict)]
        if hasattr(pool_or_conn, "acquire"):
            async with pool_or_conn.acquire() as c:
                return await upsert(c, rows)
        return await upsert(pool_or_conn, rows)
    except Exception as exc:                                    # noqa: BLE001
        return {"error": type(exc).__name__}


RULES_COUNTS_SQL = """
    SELECT r.venue,
           count(*)                                          AS captured,
           count(*) FILTER (WHERE r.rules_published)         AS readable,
           count(*) FILTER (WHERE NOT r.rules_published)     AS no_rules_field,
           count(*) FILTER (WHERE r.parse_status = 'ESTABLISHED')
                                                             AS established,
           count(*) FILTER (WHERE r.parse_status = 'PARTIAL') AS partial,
           count(*) FILTER (WHERE r.parse_status = 'ABSENT')  AS absent,
           count(*) FILTER (WHERE r.parse_status = 'CONFLICT') AS conflict,
           count(*) FILTER (WHERE r.rules_text IS NOT NULL
                              AND r.venue = 'KALSHI')        AS with_rules_primary,
           count(*) FILTER (WHERE r.rules_secondary IS NOT NULL
                              AND r.venue = 'KALSHI')        AS with_rules_secondary
      FROM market_plane_rules r
      JOIN market_plane_registry g ON g.contract_id = r.contract_id
     WHERE g.active
     GROUP BY r.venue
"""


async def rules_counts(conn) -> dict:
    """Active contracts with captured / readable / parsed rules, by venue
    (and Kalshi's rules_primary / rules_secondary presence). A missing
    table is named, never zero."""
    try:
        rows = await conn.fetch(RULES_COUNTS_SQL)
    except Exception as exc:                                    # noqa: BLE001
        return {"readable": False, "why": "RULES_UNREADABLE:%s"
                % type(exc).__name__}
    return {"readable": True,
            "by_venue": {r["venue"]: {k: int(r[k]) for k in r.keys()
                                      if k != "venue"} for r in rows}}

