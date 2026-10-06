"""THE REGISTRY POPULATOR AND COVERAGE EVIDENCE (integration, closeout).

The venue catalogue already exists and already proves its own completeness:
`workers/premap.refresh` walks the venue's event board under
`venue_catalogue.PageWalk` (TRUNCATED by name, never a quiet success) and
writes `us_premap` (one row per market SIDE) plus one append-only
`venue_catalogue_receipts` row per refresh. The market plane does not walk the
venue a second time: it READS that catalogue and its receipts and turns them
into the durable `market_plane_registry` (migration 312), so the API, the
workers and every restart share one list of what the venue lists.

  * EVERY listed sports market becomes a registry row (one per market, both
    sides recorded first-class: LONG = the venue's book, SHORT = its
    complement -- never a manufactured book), with its ontology, canonical
    event / team identity (the venue's own ids, never a name match) and a
    subscription priority.
  * REQUIRED markets the catalogue no longer lists (an open PAPER position, a
    recently evaluated candidate) are kept active with the reason, so no
    management responsibility is forgotten.
  * Incremental: every pass reads only catalogue rows changed since its
    watermark (a newly listed market is subscription-eligible within one
    pass); a full reconciliation runs when a new catalogue receipt lands.
  * Writes only `market_plane_registry` and `market_plane_events` (a
    CONTRACT_UPSERT event only when the contract's content changed, never per
    pass). No order, size, gate or authority.

COVERAGE. `classify` turns the evidence BETTOR already records into the five
terminal states (market_plane.coverage): ontology + canonical identity
(mapped), the latest valuation's probability (fair-value source) and its
settlement comparison (settlement proven), an EXTERNAL-class refusal from the
refusal taxonomy (external unavailable -- only with that evidence), and a
current canonical book. Nothing is inferred beyond that evidence.
"""
from __future__ import annotations

import hashlib
import json
import time

from . import ontology as O

VERSION = "MARKET_PLANE_POPULATOR_V1"
VENUE = "POLYMARKET_US"
AUTHORITY = "MARKET_DATA_ONLY_NO_ORDER_AUTHORITY"

#: a catalogue listing not re-seen for this long is no longer venue-active
#: (the full sweep and the calendar lane re-list everything well inside it)
ACTIVE_HORIZON_S = 3 * 3600.0
#: every listing the catalogue keeps except ENDED (STARTED and UNKNOWN are
#: still listed and open at the venue)
ACTIVE_LISTING_STATES = ("PREGAME", "LIVE", "NOT_LIVE", "STARTED", "UNKNOWN")
#: the evaluated-candidate window that makes a market REQUIRED
CANDIDATE_WINDOW_S = 6 * 3600.0
#: a decision valuation this recent is fair-value / settlement evidence
VALUATION_WINDOW_S = 24 * 3600.0

P_HELD = 0
P_CANDIDATE = 10
P_CORE_SOON = 20
P_OTHER_SOON = 50
P_REST = 80
CORE_METRICS = {"WINNER", "MARGIN", "POINTS", "GOALS", "RUNS"}
SOON_S = 48 * 3600.0

CATALOGUE_SQL = """
    SELECT market_slug,
           max(event_slug)                         AS event_slug,
           max(event_title)                        AS event_title,
           max(question)                           AS question,
           max(sports_type)                        AS sports_type,
           max(team_league)                        AS team_league,
           max(line)                               AS line,
           max(game_start)                         AS game_start,
           max(listing_state)                      AS listing_state,
           max(updated_at)                         AS updated_at,
           jsonb_agg(jsonb_build_object(
               'intent', intent, 'side_norm', side_norm,
               'team_id', team_id, 'team_abbr', team_abbr,
               'team_league', team_league)
             ORDER BY intent)                      AS sides
      FROM us_premap
     WHERE market_slug IS NOT NULL
       AND updated_at > to_timestamp($1)
     GROUP BY market_slug
"""

HELD_SQL = """
    SELECT DISTINCT us_market_slug AS slug FROM (
        SELECT us_market_slug,
               coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0)
             - coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS net
          FROM paper_fills GROUP BY account_id, group_id, us_market_slug,
                                    holding_side) p
     WHERE net > 1e-9 AND us_market_slug IS NOT NULL
"""

CANDIDATE_SQL = """
    SELECT DISTINCT us_market_slug AS slug FROM ext_candidate_outcomes
     WHERE cycle_at > now() - make_interval(secs => $1)
       AND us_market_slug IS NOT NULL
"""

VALUATION_SQL = """
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
           probability IS NOT NULL                   AS has_probability,
           coalesce(settlement_comparison->>'verdict',
                    settlement_comparison->>'status')  AS settlement_verdict,
           coalesce(refusals, ARRAY[]::text[])        AS refusals,
           record_purpose, decided_at
      FROM external_valuations
     WHERE decided_at > now() - make_interval(secs => $1)
       AND us_market_slug = ANY($2::text[])
     ORDER BY us_market_slug, decided_at DESC, id DESC
"""

CANDIDATE_REFUSAL_SQL = """
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
           first_refusal, stage
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - make_interval(secs => $1)
       AND us_market_slug = ANY($2::text[])
     ORDER BY us_market_slug, cycle_at DESC
"""

REST_BOOK_SQL = """
    SELECT us_market_slug AS slug, max(observed_at) AS observed_at
      FROM paper_book_observations
     WHERE observed_at > now() - make_interval(secs => $1)
       AND us_market_slug = ANY($2::text[])
     GROUP BY 1
"""

RECEIPTS_SQL = """
    SELECT DISTINCT ON (lane) lane, id, outcome, truncated, finished_at,
           receipt->'catalogue_complete' AS catalogue_complete,
           markets_seen, markets_kept
      FROM venue_catalogue_receipts
     ORDER BY lane, finished_at DESC
"""


def _sha(v) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def league_of(event_slug, team_league=None) -> str | None:
    """The venue's league code: the event slug's second token
    (`aec-mlb-nyy-bos-...` -> mlb), else the catalogue's team league."""
    parts = str(event_slug or "").split("-")
    if len(parts) >= 2 and parts[1]:
        return parts[1].lower()
    return str(team_league).lower() if team_league else None


def _jsonish(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def contract_row(r: dict, *, now: float, held=frozenset(),
                 candidates=frozenset()) -> dict | None:
    """PURE. One catalogue market (grouped sides) -> its registry row, or
    None for a non-sports league (named in `excluded`)."""
    slug = r["market_slug"]
    league = league_of(r.get("event_slug"), r.get("team_league"))
    if league in O.NON_SPORTS_LEAGUES:
        return None
    sides = _jsonish(r.get("sides")) or []
    longs = [s for s in sides if "LONG" in str(s.get("intent") or "").upper()]
    shorts = [s for s in sides if "SHORT" in str(s.get("intent") or "").upper()]
    lg = longs[0] if longs else {}
    sh = shorts[0] if shorts else {}
    parsed = O.parse_market_type(
        venue=VENUE, contract_id=slug, sports_market_type=r.get("sports_type"),
        competition=league, event_id=r.get("event_slug"), line=r.get("line"))
    m = parsed["meaning"]
    teams = sorted({"team:%s:%s" % (s.get("team_league") or league,
                                    s.get("team_id"))
                    for s in sides if s.get("team_id") is not None})
    ontology = {
        "meaning": m, "gaps": parsed["gaps"],
        "sport_basis": parsed.get("sport_basis"),
        "metric_source": parsed.get("metric_source"),
        "question": r.get("question"), "event_title": r.get("event_title"),
        "sides": O.binary_sides(long_intent=lg.get("intent"),
                                long_side=lg.get("side_norm"),
                                short_intent=sh.get("intent"),
                                short_side=sh.get("side_norm")),
        "entities": {"event": ("event:%s" % r["event_slug"])
                     if r.get("event_slug") else None,
                     "competition": ("competition:%s" % league)
                     if league else None,
                     "teams": teams, "basis": "VENUE_IDS"},
        "listing_state": r.get("listing_state"),
        "version": O.VERSION}
    start = _epoch(r.get("game_start"))
    seen = _epoch(r.get("updated_at")) or now
    listed_active = (r.get("listing_state") in ACTIVE_LISTING_STATES
                     and now - seen <= ACTIVE_HORIZON_S)
    reason = None
    if slug in held:
        prio, reason = P_HELD, "OPEN_PAPER_POSITION"
    elif slug in candidates:
        prio, reason = P_CANDIDATE, "EVALUATED_CANDIDATE"
    elif start is not None and (0 <= start - now <= SOON_S
                                or 0 <= now - start <= 6 * 3600):
        prio = P_CORE_SOON if m.get("metric") in CORE_METRICS and \
            m.get("period") == "FULL_EVENT" else P_OTHER_SOON
        reason = "VENUE_ACTIVE"
    else:
        prio, reason = P_REST, "VENUE_ACTIVE"
    content = {"sport": m.get("sport"), "competition": league,
               "event_id": r.get("event_slug"), "market_type":
               r.get("sports_type"), "ontology": ontology,
               "family": m.get("metric"), "period": m.get("period"),
               "event_start": start}
    return {"contract_id": slug, "venue": VENUE, "sport": m.get("sport"),
            "competition": league, "event_id": r.get("event_slug"),
            "market_type": r.get("sports_type"), "ontology": ontology,
            "active": bool(listed_active or slug in held
                           or slug in candidates),
            "desired_subscription": True, "priority": prio,
            "required_reason": reason, "family": m.get("metric"),
            "period": m.get("period"), "event_start": start,
            "last_seen_at": seen, "content_sha": _sha(content)}


UPSERT_SQL = """
    INSERT INTO market_plane_registry (contract_id, venue, sport, competition,
        event_id, market_type, ontology, active, desired_subscription,
        updated_at, priority, required_reason, family, period, event_start,
        last_seen_at, content_sha, label, authority)
    VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,to_timestamp($10),$11,$12,$13,
            $14, CASE WHEN $15::float8 IS NULL THEN NULL
                      ELSE to_timestamp($15) END,
            to_timestamp($16),$17,'RESEARCH',$18)
    ON CONFLICT (contract_id) DO UPDATE SET
        venue = excluded.venue, sport = excluded.sport,
        competition = excluded.competition, event_id = excluded.event_id,
        market_type = excluded.market_type, ontology = excluded.ontology,
        active = excluded.active,
        desired_subscription = excluded.desired_subscription,
        updated_at = excluded.updated_at, priority = excluded.priority,
        required_reason = excluded.required_reason,
        family = excluded.family, period = excluded.period,
        event_start = excluded.event_start,
        last_seen_at = excluded.last_seen_at,
        content_sha = excluded.content_sha
"""

EVENT_SQL = """
    INSERT INTO market_plane_events (event_key, contract_id, kind, payload, at,
                                     label, authority)
    VALUES ($1, $2, $3, $4::jsonb, clock_timestamp(), 'RESEARCH', $5)
    ON CONFLICT (event_key) DO NOTHING
"""


async def required_sets(conn) -> tuple:
    held, cands = set(), set()
    try:
        held = {r["slug"] for r in await conn.fetch(HELD_SQL)}
    except Exception:                                           # noqa: BLE001
        held = set()
    try:
        cands = {r["slug"] for r in await conn.fetch(
            CANDIDATE_SQL, float(CANDIDATE_WINDOW_S))}
    except Exception:                                           # noqa: BLE001
        cands = set()
    return held, cands


async def populate(conn, *, since: float, now: float | None = None,
                   full: bool = False) -> dict:
    """Upsert every catalogue market changed since `since` (all of them when
    `full`), keep REQUIRED markets active, and (on a full pass) retire
    registry rows the catalogue no longer lists and nothing requires. Returns
    counts and the new watermark."""
    at = float(now if now is not None else time.time())
    held, cands = await required_sets(conn)
    rows = [dict(r) for r in await conn.fetch(
        CATALOGUE_SQL, 0.0 if full else float(since))]
    have = {r["contract_id"]: r["content_sha"] for r in await conn.fetch(
        "SELECT contract_id, content_sha FROM market_plane_registry")}
    out = {"read": len(rows), "upserted": 0, "changed": 0, "excluded": {},
           "required_added": 0, "retired": 0, "full": bool(full)}
    batch, events = [], []
    seen_slugs = set()
    watermark = float(since)
    for r in rows:
        watermark = max(watermark, _epoch(r.get("updated_at")) or watermark)
        c = contract_row(r, now=at, held=held, candidates=cands)
        if c is None:
            lg = league_of(r.get("event_slug"), r.get("team_league"))
            out["excluded"][lg] = out["excluded"].get(lg, 0) + 1
            continue
        seen_slugs.add(c["contract_id"])
        changed = have.get(c["contract_id"]) != c["content_sha"]
        out["changed"] += int(changed)
        batch.append((c["contract_id"], c["venue"], c["sport"],
                      c["competition"], c["event_id"], c["market_type"],
                      json.dumps(c["ontology"], default=str), c["active"],
                      c["desired_subscription"], at, c["priority"],
                      c["required_reason"], c["family"], c["period"],
                      c["event_start"], c["last_seen_at"], c["content_sha"],
                      AUTHORITY))
        if changed:
            events.append(("upsert:%s:%s" % (c["contract_id"],
                                             c["content_sha"][:16]),
                           c["contract_id"], "CONTRACT_UPSERT",
                           json.dumps({"priority": c["priority"],
                                       "reason": c["required_reason"],
                                       "family": c["family"],
                                       "sport": c["sport"]}), AUTHORITY))
    # REQUIRED but not (re)listed: kept active with the reason, never dropped
    missing_required = (held | cands) - seen_slugs - set(
        k for k in have if k in seen_slugs)
    for slug in sorted(missing_required):
        if slug in have and not full:
            continue
        reason = ("OPEN_PAPER_POSITION" if slug in held
                  else "EVALUATED_CANDIDATE")
        prio = P_HELD if slug in held else P_CANDIDATE
        content = {"required": reason}
        batch.append((slug, VENUE, None, None, None, None,
                      json.dumps({"gaps": ["NOT_IN_CURRENT_CATALOGUE"],
                                  "version": O.VERSION}), True, True, at,
                      prio, reason, None, None, None, at, _sha(content),
                      AUTHORITY))
        out["required_added"] += 1
    async with conn.transaction():
        for i in range(0, len(batch), 1000):
            await conn.executemany(UPSERT_SQL, batch[i:i + 1000])
        for i in range(0, len(events), 1000):
            await conn.executemany(EVENT_SQL, events[i:i + 1000])
        if full:
            # retire: not listed within the horizon and not required
            tag = await conn.execute(
                "UPDATE market_plane_registry SET active = false, "
                "       updated_at = to_timestamp($1) "
                " WHERE active AND last_seen_at < to_timestamp($2) "
                "   AND NOT (contract_id = ANY($3::text[]))",
                at, at - ACTIVE_HORIZON_S, sorted(held | cands))
            try:
                out["retired"] = int(str(tag).split()[-1])
            except (ValueError, IndexError):
                out["retired"] = 0
    out["upserted"] = len(batch)
    out["watermark"] = watermark
    out["required"] = {"held": len(held), "candidates": len(cands)}
    return out


async def catalogue_completeness(conn) -> dict:
    """The latest receipt per lane: complete only when every lane's latest
    refresh is COMPLETE, not truncated, and (when it records it) its own
    `catalogue_complete` is true. A missing table or no receipt is NOT
    complete."""
    try:
        rows = [dict(r) for r in await conn.fetch(RECEIPTS_SQL)]
    except Exception as exc:                                    # noqa: BLE001
        return {"complete": False, "why": "RECEIPTS_UNREADABLE:%s"
                % type(exc).__name__, "lanes": {}}
    lanes = {}
    ok = bool(rows)
    for r in rows:
        cc = r.get("catalogue_complete")
        if isinstance(cc, str):
            cc = cc.strip().lower() == "true"
        lane_ok = (r["outcome"] == "COMPLETE" and not r["truncated"]
                   and cc is not False)
        ok = ok and lane_ok
        lanes[r["lane"]] = {"receipt_id": r["id"], "outcome": r["outcome"],
                            "truncated": r["truncated"],
                            "catalogue_complete": cc,
                            "finished_at": _epoch(r["finished_at"]),
                            "markets_seen": r["markets_seen"],
                            "markets_kept": r["markets_kept"],
                            "complete": lane_ok}
    for need in ("full", "calendar"):
        if need not in lanes:
            ok = False
    return {"complete": ok, "lanes": lanes,
            "why": None if ok else "A_LANE_IS_NOT_PROVEN_COMPLETE",
            "basis": "venue_catalogue_receipts (premap PageWalk, migration 249)"}


# ── coverage evidence ────────────────────────────────────────────────

def classify(contract: dict, *, valuation=None, candidate=None,
             fresh_book=False, book_source=None, external_codes=()) -> dict:
    """PURE. The coverage evidence for one registry contract -> the input of
    market_plane.coverage.terminal (and its own reasons)."""
    from .coverage import terminal
    ont = _jsonish(contract.get("ontology")) or {}
    gaps = list(ont.get("gaps") or [])
    mapped = not gaps and bool(contract.get("event_id")) and bool(
        contract.get("sport"))
    v = valuation or {}
    refusals = [str(x) for x in (v.get("refusals") or [])]
    has_p = bool(v.get("has_probability"))
    verdict = str(v.get("settlement_verdict") or "")
    settle_codes = [x for x in refusals if x.startswith("SETTLEMENT")]
    settlement_proven = bool(v) and (verdict == "COMPATIBLE" or (
        has_p and not settle_codes and verdict not in ("INCOMPATIBLE",)))
    ext = None
    fr = str((candidate or {}).get("first_refusal") or "")
    if fr and fr in set(external_codes) and not has_p:
        ext = "EXTERNAL_EVIDENCE:%s" % fr
    row = {"contract_id": contract.get("contract_id"),
           "sport": contract.get("sport"),
           "competition": contract.get("competition"),
           "family": contract.get("family"), "period": contract.get("period"),
           "mapped": mapped,
           "mapping_why": ("ONTOLOGY_GAPS:%s" % ",".join(gaps)) if gaps else (
               None if mapped else "EVENT_OR_SPORT_IDENTITY_MISSING"),
           "settlement_proven": settlement_proven,
           "settlement_why": None if settlement_proven else (
               ("SETTLEMENT_REFUSED:%s" % settle_codes[0]) if settle_codes
               else ("SETTLEMENT_VERDICT:%s" % verdict) if verdict
               else "NO_SETTLEMENT_COMPARISON_RECORDED"),
           "fair_value_source": "PINNACLE_VALUATION" if has_p else None,
           "fair_value_why": None if has_p else (
               "NO_DECISION_VALUATION_IN_WINDOW" if not v
               else "VALUATION_WITHOUT_PROBABILITY"),
           "fresh_book": bool(fresh_book),
           "book_why": None if fresh_book else "NO_CURRENT_CANONICAL_BOOK",
           "external_unavailable": ext}
    t = terminal(row)
    t.update(book_source=book_source, evidence=row)
    return t


def external_codes() -> set:
    """The refusal codes the taxonomy classes EXTERNAL (evidence, not a
    guess)."""
    try:
        from .. import refusal_taxonomy_table as T
        out = set()
        for code, cls in getattr(T, "TABLE", {}).items():
            c = cls[0] if isinstance(cls, (tuple, list)) else cls
            if str(c).upper() == "EXTERNAL":
                out.add(code)
        return out
    except Exception:                                           # noqa: BLE001
        return set()


async def coverage_pass(conn, *, fresh_symbols=frozenset(), now=None,
                        rest_sla_s: float = 300.0, limit: int | None = None
                        ) -> dict:
    """Classify every ACTIVE registry contract and write the changed terminal
    states. Returns the matrix summary (counts by state, sport, family, why)."""
    from .coverage import matrix
    at = float(now if now is not None else time.time())
    rows = [dict(r) for r in await conn.fetch(
        "SELECT contract_id, sport, competition, event_id, family, period, "
        "       ontology, coverage_state, coverage_why, priority "
        "  FROM market_plane_registry WHERE active "
        " ORDER BY priority, contract_id" + (" LIMIT %d" % int(limit)
                                             if limit else ""))]
    slugs = [r["contract_id"] for r in rows]
    vals, cands, rest = {}, {}, {}
    for i in range(0, len(slugs), 5000):
        chunk = slugs[i:i + 5000]
        try:
            for v in await conn.fetch(VALUATION_SQL, float(VALUATION_WINDOW_S),
                                      chunk):
                vals[v["slug"]] = dict(v)
        except Exception:                                       # noqa: BLE001
            pass
        try:
            for c in await conn.fetch(CANDIDATE_REFUSAL_SQL,
                                      float(VALUATION_WINDOW_S), chunk):
                cands[c["slug"]] = dict(c)
        except Exception:                                       # noqa: BLE001
            pass
        try:
            for b in await conn.fetch(REST_BOOK_SQL, float(rest_sla_s), chunk):
                rest[b["slug"]] = _epoch(b["observed_at"])
        except Exception:                                       # noqa: BLE001
            pass
    ext = external_codes()
    results, changed = [], []
    src_counts = {"PMX_GRPC": 0, "RETAIL_PUSH": 0, "REST_RECOVERY": 0,
                  "NONE": 0}
    for r in rows:
        s = r["contract_id"]
        if s in fresh_symbols:
            src, fresh = "PMX_GRPC", True
        elif s in rest:
            src, fresh = "REST_RECOVERY", True
        else:
            src, fresh = None, False
        src_counts[src or "NONE"] += 1
        t = classify(r, valuation=vals.get(s), candidate=cands.get(s),
                     fresh_book=fresh, book_source=src, external_codes=ext)
        results.append(t)
        if (t["state"], t["why"]) != (r.get("coverage_state"),
                                     r.get("coverage_why")):
            changed.append((s, t["state"], t["why"], at))
    if changed:
        async with conn.transaction():
            for i in range(0, len(changed), 1000):
                await conn.executemany(
                    "UPDATE market_plane_registry SET coverage_state = $2, "
                    "       coverage_why = $3, coverage_at = to_timestamp($4) "
                    " WHERE contract_id = $1", changed[i:i + 1000])
    m = matrix([{"contract_id": t["contract_id"], **t["evidence"]}
                for t in results])
    by_sport, by_why = {}, {}
    for t in results:
        k = t.get("sport") or "UNKNOWN"
        by_sport.setdefault(k, {}).setdefault(t["state"], 0)
        by_sport[k][t["state"]] += 1
        w = "%s:%s" % (t["state"], t["why"])
        by_why[w] = by_why.get(w, 0) + 1
    m.pop("rows", None)
    return dict(m, by_sport=by_sport,
                top_reasons=dict(sorted(by_why.items(),
                                        key=lambda kv: -kv[1])[:40]),
                source_counts=src_counts, changed=len(changed),
                active=len(rows), computed_at=at)
