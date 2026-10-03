"""COVERAGE INTEGRITY: THE FUNNEL PER LEAGUE PER DAY, AND COLLAPSE ALERTS.

A permanent, measured answer to "is each league still flowing from the
provider to a fill?", computed ONLY from production tables:

  ext_candidate_outcomes  every provider event the scheduled cycle saw, per
                          cycle, with the stage it stopped at (the provider,
                          normalization, venue-discovery, mapping and
                          settlement stages)
  external_valuations     ENTRY_DECISION valuations (evaluated), keyed by the
                          provider event (`event_key`)
  paper_decisions         ENTER / REFUSE on those valuations (`valuation_id`)
  paper_orders            ENTRY orders on those decisions (`decision_id`)
  paper_fills             fills of those orders (`order_id`)
  execution_intents       the actual-side intents derived from the same
                          decisions, with execmirror_orders' fills
  us_premap               the venue catalogue (a supplementary, venue-side
                          count: the catalogue holds only current listings)
  ingestion_state         the PinnAPI feed heartbeat ('pinnapi_feed_last'),
                          attached as a provider-side supplement when recent

THE UNIT IS THE PROVIDER EVENT (`provider_event_id` = `event_key`), carried
through every stage by the record links above, so stage-to-stage ratios are
comparable. The league is the provider's competition key (`sport_key`, e.g.
`americanfootball_ncaaf`); a downstream record whose event has no league
mapping is counted under `UNATTRIBUTED:<family>`, never dropped.

THE DAY IS EXPLICIT: every snapshot is computed for BOTH 'UTC' and
'America/New_York' local days (TIMEZONES); collapse alerts are raised on
ALERT_TIMEZONE (Audrey's reporting day) only, so one collapse is one alert.

STAGE REACH. Each ext_candidate_outcomes row is ranked by how far the event
got: ADMITTED / ALREADY_RECORDED = 99; a REFUSED row = its stage's ordinal
(`1_PROBABILITY` .. `8_ECONOMICS`); an unclassified refusal, a deferral or an
unclassified outcome = 0 (we do not know it passed anything). A row carrying
a venue contract (`us_market_slug`) reached at least mapping (4). Per event
per day the furthest row counts:

  provider              every event
  normalized            reach >= 3 (probability and freshness passed)
  venue_discovered      reach >= 4, or stopped at identity for a reason other
                        than "the venue does not list it" (VENUE_ABSENT)
  mapped                reach >= 4
  settlement_supported  reach >= 5
  evaluated             an ENTRY_DECISION valuation of the event that day

NEVER ZERO FOR UNMEASURED. A stage whose source table is absent or whose read
failed is NULL, with the reason in `unavailable`; a ratio over a NULL or zero
denominator is NULL with its reason.

COLLAPSE DETECTION (declared in THRESHOLDS, applied by `detect`):
  RATIO_COLLAPSE     a consecutive stage ratio below (1 - max_relative_drop)
                     x its trailing baseline (the median of the previous
                     `baseline_days` days with a denominator of at least
                     `min_denominator`; at least `min_baseline_days` such
                     days; baseline >= `min_baseline_ratio`)
  ABSENT_DOWNSTREAM  a league with at least `absent_min_provider` provider
                     events and ZERO at a presence stage (normalized ..
                     evaluated) -- named at the first stage where it vanished.
                     CRITICAL when that stage flowed in the baseline,
                     WARNING when it never has.
Every alert is persisted (coverage_collapse_alerts) and AUTOMATICALLY written
as an Audrey finding (paper_audrey_findings, kind COVERAGE_COLLAPSE) on the
current paper session; with no session the alert records why no finding
exists. Detection never changes a mapping, a mandate or an order: it reports.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import statistics
import time
from typing import Any
from zoneinfo import ZoneInfo

VERSION = "COVERAGE_INTEGRITY_V1"
TIMEZONES = ("UTC", "America/New_York")
ALERT_TIMEZONE = "America/New_York"
WATERMARK_KEY = "coverage_integrity_last"
REFRESH_EVERY_S = 900.0
#: days re-computed each pass: today and yesterday (yesterday is finalised).
REFRESH_DAYS = 2

STAGES = ("provider", "normalized", "venue_discovered", "mapped",
          "settlement_supported", "evaluated", "decided", "entered",
          "ordered", "filled")
#: Stage -> snapshot column.
COLUMN = {"provider": "provider_events", "normalized": "normalized_events",
          "venue_discovered": "venue_discovered", "mapped": "mapped_events",
          "settlement_supported": "settlement_supported",
          "evaluated": "evaluated_events", "decided": "decided_events",
          "entered": "entered_events", "ordered": "ordered_events",
          "filled": "filled_events"}
EXTRA_COLUMNS = ("refused_events", "actual_intents", "actual_submitted",
                 "actual_filled", "venue_catalogue_events")
#: The consecutive pairs whose ratio is baselined.
RATIO_PAIRS = tuple(zip(STAGES[:-1], STAGES[1:]))
#: The stages at which a provider-present league must not vanish.
PRESENCE_STAGES = ("normalized", "venue_discovered", "mapped",
                   "settlement_supported", "evaluated")

THRESHOLDS = {
    "baseline_days": 7,
    "min_baseline_days": 3,
    "min_denominator": 5,
    "max_relative_drop": 0.5,
    "min_baseline_ratio": 0.10,
    "absent_min_provider": 3,
    "declared": "2026-10-03, coverage_integrity.THRESHOLDS",
}

#: Refusals meaning "the venue does not list this fixture" -- the event was
#: not DISCOVERED at the venue, as opposed to found and not mapped.
VENUE_ABSENT = (
    "VENUE_DOES_NOT_LIST_THIS_FIXTURE", "NO_PREMAP_CONTRACT_FOR_THIS_FIXTURE",
    "NO_VENUE_NATIVE_EVENT_FOR_FIXTURE", "NO_VENUE_CONTRACT_FOR_EVENT",
    "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP")

#: Display names for provider competition keys (anything else: the key's
#: suffix upper-cased).
LEAGUE_NAMES = {"americanfootball_ncaaf": "NCAAF",
                "americanfootball_nfl": "NFL", "baseball_mlb": "MLB",
                "basketball_nba": "NBA", "basketball_wnba": "WNBA",
                "basketball_ncaab": "NCAAB", "icehockey_nhl": "NHL",
                "soccer_usa_mls": "MLS"}

#: Venue league tokens used ONLY for the supplementary venue-catalogue count,
#: beside the lane's own confirmed tokens (ext_pinnacle_loop
#: venue_league_tokens). Counting is not mapping: nothing here maps a
#: contract.
CATALOGUE_TOKENS = {"americanfootball_ncaaf": ("cfb", "ncaaf"),
                    "americanfootball_nfl": ("nfl",),
                    "baseball_mlb": ("mlb",), "basketball_nba": ("nba",),
                    "basketball_wnba": ("wnba",),
                    "basketball_ncaab": ("cbb", "ncaab"),
                    "icehockey_nhl": ("nhl",)}

R_TABLE_ABSENT = "SOURCE_TABLE_ABSENT"
R_READ_FAILED = "SOURCE_READ_FAILED"
R_DENOM_ZERO = "DENOMINATOR_ZERO"
R_DENOM_NULL = "DENOMINATOR_UNMEASURED"
R_NUM_NULL = "NUMERATOR_UNMEASURED"
R_NO_SESSION = "NO_PAPER_SESSION_CONTEXT"
R_FINDING_FAILED = "AUDREY_FINDING_WRITE_FAILED"


def league_name(key: str) -> str:
    k = str(key or "")
    if k in LEAGUE_NAMES:
        return LEAGUE_NAMES[k]
    if k.startswith("UNATTRIBUTED"):
        return k
    return (k.split("_", 1)[-1] or k).upper()


def day_window(day: _dt.date, tz: str) -> tuple:
    """(start epoch, end epoch) of the local calendar `day` in `tz`."""
    z = ZoneInfo(tz)
    start = _dt.datetime(day.year, day.month, day.day, tzinfo=z)
    end = start + _dt.timedelta(days=1)
    # DST-safe: the next local midnight, not start + 86400
    end = _dt.datetime(end.year, end.month, end.day, tzinfo=z)
    return start.timestamp(), end.timestamp()


def local_day(at: float, tz: str) -> _dt.date:
    return _dt.datetime.fromtimestamp(float(at), ZoneInfo(tz)).date()


async def _regclass(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


# ═════════════════════════════════════════════════════════════════════
# THE READS (one per source; each returns {league: {col: n}} or raises)
# ═════════════════════════════════════════════════════════════════════

REACH_SQL = """
    CASE
      WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
      WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN
           greatest(substr(stage, 1, 1)::int,
                    CASE WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END)
      WHEN us_market_slug IS NOT NULL THEN 4
      ELSE 0
    END"""

PROVIDER_SQL = """
    WITH r AS (
        SELECT sport_key, family, provider_event_id, first_refusal,
               %s AS reach
          FROM ext_candidate_outcomes
         WHERE cycle_at >= to_timestamp($1) AND cycle_at < to_timestamp($2)
           AND provider_event_id IS NOT NULL),
    e AS (
        SELECT sport_key, max(family) AS family, provider_event_id,
               max(reach) AS reach,
               bool_or(reach = 3 AND NOT (coalesce(first_refusal, '')
                                          = ANY($3::text[]))) AS found_at_3
          FROM r GROUP BY sport_key, provider_event_id)
    SELECT sport_key AS league, max(family) AS family,
           count(*) AS provider_events,
           count(*) FILTER (WHERE reach >= 3) AS normalized_events,
           count(*) FILTER (WHERE reach >= 4 OR found_at_3)
               AS venue_discovered,
           count(*) FILTER (WHERE reach >= 4) AS mapped_events,
           count(*) FILTER (WHERE reach >= 5) AS settlement_supported
      FROM e GROUP BY sport_key
""" % REACH_SQL

#: provider event -> league, from the same lane's records (any time in the
#: lookback), so downstream records are attributed to the league the
#: provider listed them under.
EVENT_LEAGUE_CTE = """
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC)"""

LEAGUE_EXPR = ("coalesce(m.sport_key, 'UNATTRIBUTED:' || "
               "coalesce(ev.sport_family, 'unknown'))")

EVALUATED_SQL = """
    WITH %s
    SELECT %s AS league, count(DISTINCT coalesce(ev.event_key,
                                                 ev.id::text)) AS n
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
     WHERE ev.record_purpose = 'ENTRY_DECISION'
       AND ev.decided_at >= to_timestamp($1) AND ev.decided_at < to_timestamp($2)
     GROUP BY 1
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR)

DECISIONS_SQL = """
    WITH %s,
    d AS (
        SELECT %s AS league, coalesce(ev.event_key, pd.us_market_slug) AS ek,
               bool_or(pd.verdict = 'ENTER') AS entered
          FROM paper_decisions pd
          JOIN external_valuations ev ON ev.id = pd.valuation_id
          LEFT JOIN m ON m.provider_event_id = ev.event_key
         WHERE pd.decided_at >= to_timestamp($1)
           AND pd.decided_at < to_timestamp($2)
         GROUP BY 1, 2)
    SELECT league, count(*) AS decided,
           count(*) FILTER (WHERE entered) AS entered,
           count(*) FILTER (WHERE NOT entered) AS refused
      FROM d GROUP BY league
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR)

ORDERS_SQL = """
    WITH %s
    SELECT %s AS league,
           count(DISTINCT coalesce(ev.event_key, po.us_market_slug)) AS n
      FROM paper_orders po
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
     WHERE po.role = 'ENTRY'
       AND po.created_at >= to_timestamp($1) AND po.created_at < to_timestamp($2)
     GROUP BY 1
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR)

FILLS_SQL = """
    WITH %s
    SELECT %s AS league,
           count(DISTINCT coalesce(ev.event_key, pf.us_market_slug)) AS n
      FROM paper_fills pf
      JOIN paper_orders po ON po.order_id = pf.order_id
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
     WHERE po.role = 'ENTRY'
       AND pf.filled_at >= to_timestamp($1) AND pf.filled_at < to_timestamp($2)
     GROUP BY 1
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR)

ACTUAL_SQL = """
    WITH %s
    SELECT %s AS league,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               AS actual_intents,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               FILTER (WHERE ei.actual_mirror_id IS NOT NULL)
               AS actual_submitted,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               FILTER (WHERE EXISTS (
                   SELECT 1 FROM execmirror_orders xo
                    WHERE xo.execution_intent_id = ei.intent_id
                      AND coalesce(xo.cum_qty, 0) > 0)) AS actual_filled
      FROM execution_intents ei
      LEFT JOIN external_valuations ev ON ev.id = ei.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
     WHERE ei.decided_at >= to_timestamp($1) AND ei.decided_at < to_timestamp($2)
     GROUP BY 1
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR)

CATALOGUE_SQL = """
    SELECT lower(split_part(coalesce(event_slug, ''), '-', 1)) AS token,
           count(DISTINCT event_slug) AS n
      FROM us_premap
     WHERE game_start >= to_timestamp($1) AND game_start < to_timestamp($2)
       AND event_slug IS NOT NULL
     GROUP BY 1
"""


def catalogue_token_map() -> dict:
    """venue league token -> provider league key (counting only)."""
    out = {}
    for key, toks in CATALOGUE_TOKENS.items():
        for t in toks:
            out[t] = key
    try:
        from ..workers import ext_pinnacle_loop as L
        for t, key in L.VENUE_TOKEN_TO_PROVIDER_KEY.items():
            out.setdefault(t, key)
        for key, toks in L.VENUE_LEAGUE_TOKENS_CONFIRMED.items():
            for t in toks:
                out.setdefault(t, key)
    except Exception:                                           # noqa: BLE001
        pass
    return out


async def _read(conn, table_deps: tuple, sql: str, *args):
    """Rows, or a reason string when a source is absent/unreadable."""
    for t in table_deps:
        if not await _regclass(conn, t):
            return "%s:%s" % (R_TABLE_ABSENT, t)
    try:
        async with conn.transaction():
            return [dict(r) for r in await conn.fetch(sql, *args)]
    except Exception as exc:                                    # noqa: BLE001
        return "%s:%s" % (R_READ_FAILED, type(exc).__name__)


async def funnel_for_day(conn, day: _dt.date, tz: str) -> dict:
    """{league: row} for one local day. Never raises."""
    start, end = day_window(day, tz)
    rows: dict[str, dict] = {}
    unavailable_all: dict[str, str] = {}
    sources: dict[str, Any] = {}

    def row(league, family=None):
        r = rows.setdefault(league, {"league": league, "sport_family": family,
                                     "_seen": set()})
        if family and not r.get("sport_family"):
            r["sport_family"] = family
        return r

    got = await _read(conn, ("ext_candidate_outcomes",), PROVIDER_SQL,
                      start, end, list(VENUE_ABSENT))
    prov_cols = ("provider_events", "normalized_events", "venue_discovered",
                 "mapped_events", "settlement_supported")
    if isinstance(got, str):
        for c in prov_cols:
            unavailable_all[c] = got
    else:
        for g in got:
            r = row(g["league"], g.get("family"))
            for c in prov_cols:
                r[c] = int(g[c])
                r["_seen"].add(c)
    sources["provider"] = "ext_candidate_outcomes"

    async def single(name, deps, sql, col_map):
        res = await _read(conn, deps, sql, start, end)
        if isinstance(res, str):
            for c in col_map.values():
                unavailable_all[c] = res
            return
        for g in res:
            r = row(g["league"])
            for src, c in col_map.items():
                r[c] = int(g[src] or 0)
                r["_seen"].add(c)
        sources[name] = deps

    await single("evaluated", ("external_valuations", "ext_candidate_outcomes"),
                 EVALUATED_SQL, {"n": "evaluated_events"})
    await single("decisions", ("paper_decisions", "external_valuations",
                               "ext_candidate_outcomes"), DECISIONS_SQL,
                 {"decided": "decided_events", "entered": "entered_events",
                  "refused": "refused_events"})
    await single("orders", ("paper_orders", "paper_decisions",
                            "external_valuations", "ext_candidate_outcomes"),
                 ORDERS_SQL, {"n": "ordered_events"})
    await single("fills", ("paper_fills", "paper_orders", "paper_decisions",
                           "external_valuations", "ext_candidate_outcomes"),
                 FILLS_SQL, {"n": "filled_events"})
    await single("actual", ("execution_intents", "execmirror_orders",
                            "external_valuations", "ext_candidate_outcomes"),
                 ACTUAL_SQL, {"actual_intents": "actual_intents",
                              "actual_submitted": "actual_submitted",
                              "actual_filled": "actual_filled"})

    cat = await _read(conn, ("us_premap",), CATALOGUE_SQL, start, end)
    if isinstance(cat, str):
        unavailable_all["venue_catalogue_events"] = cat
    else:
        tmap = catalogue_token_map()
        per: dict[str, int] = {}
        for g in cat:
            key = tmap.get(g["token"])
            if key:
                per[key] = per.get(key, 0) + int(g["n"])
        # only leagues the provider side knows, or that have catalogue rows
        for key, n in per.items():
            r = row(key)
            r["venue_catalogue_events"] = n
            r["_seen"].add("venue_catalogue_events")
        sources["venue_catalogue"] = ("us_premap (current listings only: "
                                      "past days read what is still listed)")

    out = {}
    all_cols = [COLUMN[s] for s in STAGES] + list(EXTRA_COLUMNS)
    for league, r in rows.items():
        seen = r.pop("_seen")
        un = {}
        for c in all_cols:
            if c in seen:
                continue
            if c in unavailable_all:
                r[c] = None
                un[c] = unavailable_all[c]
            else:
                # the source WAS read and had no row for this league: a
                # measured zero
                r[c] = 0
        r["unavailable"] = un
        r["ratios"] = ratios(r)
        out[league] = r
    return {"day": day.isoformat(), "tz": tz, "window": [start, end],
            "leagues": out, "unavailable": unavailable_all,
            "sources": sources}


def ratios(r: dict) -> dict:
    """{"a->b": {"ratio": x|None, "why": reason|None}}. Pure."""
    out = {}
    for a, b in RATIO_PAIRS:
        num, den = r.get(COLUMN[b]), r.get(COLUMN[a])
        key = "%s->%s" % (a, b)
        if den is None:
            out[key] = {"ratio": None, "why": R_DENOM_NULL}
        elif num is None:
            out[key] = {"ratio": None, "why": R_NUM_NULL}
        elif den == 0:
            out[key] = {"ratio": None, "why": R_DENOM_ZERO}
        else:
            out[key] = {"ratio": round(float(num) / float(den), 6),
                        "why": None, "num": num, "den": den}
    return out


# ═════════════════════════════════════════════════════════════════════
# COLLAPSE DETECTION (pure)
# ═════════════════════════════════════════════════════════════════════

def baseline(history: list, pair: str, *, th: dict = THRESHOLDS
             ) -> dict:
    """The trailing baseline of one ratio from prior days' rows (newest
    first or any order). Pure."""
    vals = []
    for h in history[: th["baseline_days"]]:
        rr = ((h or {}).get("ratios") or {}).get(pair) or {}
        den = rr.get("den")
        if rr.get("ratio") is not None and den is not None and \
                den >= th["min_denominator"]:
            vals.append(float(rr["ratio"]))
    if len(vals) < th["min_baseline_days"]:
        return {"baseline": None, "days": len(vals),
                "why": "FEWER_THAN_%d_BASELINE_DAYS" % th["min_baseline_days"]}
    return {"baseline": round(statistics.median(vals), 6), "days": len(vals),
            "why": None}


def stage_flowed(history: list, stage: str) -> bool:
    col = COLUMN[stage]
    return any(((h or {}).get(col) or 0) > 0 for h in history)


def detect(today: dict, history: list, *, th: dict = THRESHOLDS) -> list:
    """Alerts for one league's day given its prior days. Pure."""
    alerts = []
    league = today.get("league")
    prov = today.get("provider_events")
    if prov is not None and prov >= th["absent_min_provider"]:
        for st in PRESENCE_STAGES:
            n = today.get(COLUMN[st])
            if n is None:
                continue          # unmeasured is not absent
            if n == 0:
                prev = STAGES[STAGES.index(st) - 1]
                flowed = stage_flowed(history, st)
                alerts.append({
                    "kind": "ABSENT_DOWNSTREAM", "league": league,
                    "stage_from": prev, "stage_to": st,
                    "ratio": 0.0, "baseline": None,
                    "severity": "CRITICAL" if flowed else "WARNING",
                    "detail": {
                        "provider_events": prov,
                        "previous_stage_count": today.get(COLUMN[prev]),
                        "stage_count": 0,
                        "flowed_in_baseline": flowed,
                        "statement": (
                            "%s: %d provider event(s); none reached %s"
                            % (league_name(league), prov, st))}})
                break
    for a, b in RATIO_PAIRS:
        pair = "%s->%s" % (a, b)
        rr = (today.get("ratios") or {}).get(pair) or {}
        if rr.get("ratio") is None or (rr.get("den") or 0) < \
                th["min_denominator"]:
            continue
        if any(x["kind"] == "ABSENT_DOWNSTREAM" and x["stage_to"] == b
               for x in alerts):
            continue
        bl = baseline(history, pair, th=th)
        if bl["baseline"] is None or bl["baseline"] < th["min_baseline_ratio"]:
            continue
        floor = bl["baseline"] * (1.0 - th["max_relative_drop"])
        if rr["ratio"] < floor:
            alerts.append({
                "kind": "RATIO_COLLAPSE", "league": league, "stage_from": a,
                "stage_to": b, "ratio": rr["ratio"],
                "baseline": bl["baseline"], "severity": "WARNING",
                "detail": {"num": rr.get("num"), "den": rr.get("den"),
                           "floor": round(floor, 6),
                           "baseline_days": bl["days"],
                           "statement": (
                               "%s: %s->%s ratio %.3f below %.3f (baseline "
                               "%.3f over %d days)" % (
                                   league_name(league), a, b, rr["ratio"],
                                   floor, bl["baseline"], bl["days"]))}})
    return alerts


# ═════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═════════════════════════════════════════════════════════════════════

async def persist_day(conn, f: dict, *, now: float) -> int:
    """Upsert every league row of one computed day. Returns rows written."""
    day = _dt.date.fromisoformat(f["day"])
    final = now >= f["window"][1]
    n = 0
    cols = [COLUMN[s] for s in STAGES] + list(EXTRA_COLUMNS)
    for league, r in f["leagues"].items():
        vals = [r.get(c) for c in cols]
        await conn.execute(
            "INSERT INTO coverage_funnel_snapshots (tz, day, league, "
            " sport_family, %s, ratios, unavailable, sources, final, version,"
            " computed_at) VALUES ($1,$2,$3,$4,%s,$%d::jsonb,$%d::jsonb,"
            " $%d::jsonb,$%d,$%d,to_timestamp($%d)) "
            "ON CONFLICT (tz, day, league) DO UPDATE SET "
            " sport_family = EXCLUDED.sport_family, %s, "
            " ratios = EXCLUDED.ratios, unavailable = EXCLUDED.unavailable,"
            " sources = EXCLUDED.sources, final = EXCLUDED.final,"
            " version = EXCLUDED.version, computed_at = EXCLUDED.computed_at"
            " WHERE NOT coverage_funnel_snapshots.final" % (
                ", ".join(cols),
                ", ".join("$%d" % (5 + i) for i in range(len(cols))),
                5 + len(cols), 6 + len(cols), 7 + len(cols), 8 + len(cols),
                9 + len(cols), 10 + len(cols),
                ", ".join("%s = EXCLUDED.%s" % (c, c) for c in cols)),
            f["tz"], day, league, r.get("sport_family"), *vals,
            json.dumps(r.get("ratios") or {}),
            json.dumps(r.get("unavailable") or {}),
            json.dumps(f.get("sources") or {}, default=str), final, VERSION,
            float(now))
        n += 1
    return n


def _row_out(r) -> dict:
    d = dict(r)
    for k in ("ratios", "unavailable", "sources"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                d[k] = {}
    for k in ("computed_at", "first_computed_at"):
        if hasattr(d.get(k), "timestamp"):
            d[k] = d[k].timestamp()
    if hasattr(d.get("day"), "isoformat"):
        d["day"] = d["day"].isoformat()
    d["league_name"] = league_name(d.get("league"))
    return d


async def history_for(conn, tz: str, league: str, day: _dt.date,
                      days: int = THRESHOLDS["baseline_days"]) -> list:
    """Prior days' persisted rows for one league, newest first."""
    rows = await conn.fetch(
        "SELECT * FROM coverage_funnel_snapshots WHERE tz=$1 AND league=$2 "
        "   AND day < $3::date AND day >= $3::date - $4::int ORDER BY day DESC",
        tz, league, day, int(days))
    return [_row_out(r) for r in rows]


def alert_id_for(tz: str, day: str, a: dict) -> str:
    raw = "%s|%s|%s|%s|%s" % (tz, day, a["league"], a["kind"], a["stage_to"])
    return "covalert:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


async def raise_alert(conn, a: dict, *, tz: str, day: str, now: float,
                      ctx: dict | None) -> dict:
    """Persist one alert and route it to Audrey (paper_audrey_findings).
    Idempotent per (tz, day, league, kind, stage). Never raises."""
    aid = alert_id_for(tz, day, a)
    fid, refusal = None, None
    if ctx and ctx.get("session_id") and ctx.get("account_id"):
        try:
            from . import paper_audrey as PA
            async with conn.transaction():
                got = await PA.finding(
                    conn, dict(ctx, now=now), kind="COVERAGE_COLLAPSE",
                    subject=a["league"], severity=a["severity"],
                    scope="%s:%s:%s:%s" % (tz, day, a["kind"], a["stage_to"]),
                    detail=dict(a["detail"], alert_id=aid, tz=tz, day=day,
                                kind=a["kind"], stage_from=a["stage_from"],
                                stage_to=a["stage_to"], ratio=a["ratio"],
                                baseline=a["baseline"],
                                league_name=league_name(a["league"]),
                                thresholds=THRESHOLDS, version=VERSION))
            fid = got["finding_id"]
        except Exception as exc:                                # noqa: BLE001
            refusal = "%s:%s" % (R_FINDING_FAILED, type(exc).__name__)
    else:
        refusal = R_NO_SESSION
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO coverage_collapse_alerts (alert_id, tz, day, "
                " league, kind, stage_from, stage_to, ratio, baseline, "
                " threshold, severity, detail, audrey_finding_id, "
                " audrey_refusal, detected_at) VALUES ($1,$2,$3,$4,$5,$6,$7,"
                " $8,$9,$10::jsonb,$11,$12::jsonb,$13,$14,to_timestamp($15))"
                " ON CONFLICT (alert_id) DO UPDATE SET "
                " audrey_finding_id = coalesce("
                "   coverage_collapse_alerts.audrey_finding_id,"
                "   EXCLUDED.audrey_finding_id),"
                " audrey_refusal = CASE WHEN coalesce("
                "   coverage_collapse_alerts.audrey_finding_id,"
                "   EXCLUDED.audrey_finding_id) IS NULL THEN "
                "   EXCLUDED.audrey_refusal ELSE NULL END",
                aid, tz, _dt.date.fromisoformat(day), a["league"], a["kind"],
                a["stage_from"], a["stage_to"], a["ratio"], a["baseline"],
                json.dumps(THRESHOLDS), a["severity"],
                json.dumps(a["detail"], default=str), fid, refusal,
                float(now))
    except Exception as exc:                                    # noqa: BLE001
        return {"alert_id": aid, "ok": False,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    return {"alert_id": aid, "ok": True, "audrey_finding_id": fid,
            "audrey_refusal": refusal, "kind": a["kind"],
            "league": a["league"], "stage_to": a["stage_to"],
            "severity": a["severity"]}


async def run(conn, *, now: float | None = None, ctx: dict | None = None,
              days: int = REFRESH_DAYS) -> dict:
    """Compute and persist the last `days` local days in both timezones,
    then detect collapses on ALERT_TIMEZONE days. Never raises."""
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at, "snapshots": 0,
                           "alerts": [], "errors": {}}
    if not await _regclass(conn, "coverage_funnel_snapshots"):
        return dict(out, ran=False, why="MIGRATION_209_NOT_APPLIED")
    for tz in TIMEZONES:
        today = local_day(at, tz)
        for back in range(days - 1, -1, -1):
            day = today - _dt.timedelta(days=back)
            try:
                f = await funnel_for_day(conn, day, tz)
                out["snapshots"] += await persist_day(conn, f, now=at)
            except Exception as exc:                            # noqa: BLE001
                out["errors"]["%s:%s" % (tz, day)] = "%s: %s" % (
                    type(exc).__name__, str(exc)[:160])
                continue
            if tz != ALERT_TIMEZONE:
                continue
            for league, r in f["leagues"].items():
                if league.startswith("UNATTRIBUTED"):
                    continue
                try:
                    hist = await history_for(conn, tz, league, day)
                except Exception:                               # noqa: BLE001
                    hist = []
                for a in detect(dict(r, league=league), hist):
                    out["alerts"].append(await raise_alert(
                        conn, a, tz=tz, day=day.isoformat(), now=at,
                        ctx=ctx))
    out["ran"] = True
    return out


async def step(conn, ctx: dict) -> dict:
    """THE SCHEDULED HOOK (paper pass): at most every REFRESH_EVERY_S, on
    the main paper account's session only. Never raises."""
    from .. import bettor_paper_ledger as L
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    if ctx.get("account_id") != L.ACCOUNT_ID:
        return {"ran": False, "why": "NOT_THE_MAIN_PAPER_ACCOUNT"}
    try:
        last = L._j(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            WATERMARK_KEY)) or {}
        if last.get("at") is not None and \
                at - float(last["at"]) < REFRESH_EVERY_S:
            return {"ran": False, "why": "NOT_DUE", "last_at": last["at"]}
        res = await run(conn, now=at, ctx=ctx)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            WATERMARK_KEY, json.dumps({"at": at, "version": VERSION,
                                       "alerts": len(res.get("alerts") or [])
                                       }))
        return {"ran": bool(res.get("ran")), "snapshots": res.get("snapshots"),
                "alerts": len(res.get("alerts") or []),
                "errors": res.get("errors")}
    except Exception as exc:                                    # noqa: BLE001
        return {"ran": False, "error": "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200])}


# ═════════════════════════════════════════════════════════════════════
# THE READ (endpoint)
# ═════════════════════════════════════════════════════════════════════

async def pinnapi_supplement(conn, *, now: float) -> dict:
    """The PinnAPI feed heartbeat's own census, when recent: provider-side
    events per sport family (not per league). UNAVAILABLE otherwise."""
    try:
        from .. import pinnapi_feed_runtime as R
        raw = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", R.HEARTBEAT_KEY)
        hv = R.heartbeat_view(raw, now=now)
        if hv.get("status") != "RECENT_TELEMETRY":
            return {"status": "UNAVAILABLE",
                    "why": "PINNAPI_HEARTBEAT_%s" % hv.get("status"),
                    "age_s": hv.get("age_s")}
        v = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        cen = v.get("coverage_census") or {}
        return {"status": "OK", "age_s": hv.get("age_s"),
                "census": {k: cen.get(k) for k in (
                    "total_contracts", "states", "events_by_state",
                    "matched_events", "reconciled")
                    if k in cen} or None,
                "note": "provider-side telemetry; not a per-league count"}
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:120])}


async def coverage_payload(conn, *, tz: str = ALERT_TIMEZONE, days: int = 7,
                           now: float | None = None) -> dict:
    """Persisted snapshots (newest day first) + alerts; today computed
    read-only when nothing is persisted for it yet."""
    at = float(now if now is not None else time.time())
    if tz not in TIMEZONES:
        tz = ALERT_TIMEZONE
    days = max(1, min(int(days or 7), 60))
    out: dict[str, Any] = {"version": VERSION, "as_of": at, "tz": tz,
                           "timezones": list(TIMEZONES),
                           "alert_timezone": ALERT_TIMEZONE,
                           "stages": list(STAGES), "unit": "provider events",
                           "thresholds": THRESHOLDS,
                           "definitions": __doc__.split("STAGE REACH.")[1]
                           .split("NEVER ZERO")[0].strip()}
    if not await _regclass(conn, "coverage_funnel_snapshots"):
        out.update(status="UNAVAILABLE", why="MIGRATION_209_NOT_APPLIED",
                   days=[], alerts=[])
        return out
    today = local_day(at, tz)
    rows = await conn.fetch(
        "SELECT * FROM coverage_funnel_snapshots WHERE tz=$1 "
        "   AND day > $2::date - $3::int ORDER BY day DESC, league", tz, today,
        days)
    by_day: dict[str, list] = {}
    for r in rows:
        d = _row_out(r)
        by_day.setdefault(d["day"], []).append(d)
    live = False
    if today.isoformat() not in by_day:
        f = await funnel_for_day(conn, today, tz)
        by_day[today.isoformat()] = [
            dict(r, day=today.isoformat(), league_name=league_name(k),
                 persisted=False)
            for k, r in sorted(f["leagues"].items())]
        live = True
    alerts = []
    if await _regclass(conn, "coverage_collapse_alerts"):
        for r in await conn.fetch(
                "SELECT * FROM coverage_collapse_alerts WHERE day > $1::date - $2::int"
                " ORDER BY detected_at DESC LIMIT 200", today, days):
            d = dict(r)
            for k in ("threshold", "detail"):
                if isinstance(d.get(k), str):
                    d[k] = json.loads(d[k])
            for k in ("detected_at", "recorded_at"):
                if hasattr(d.get(k), "timestamp"):
                    d[k] = d[k].timestamp()
            d["day"] = d["day"].isoformat()
            d["league_name"] = league_name(d["league"])
            alerts.append(d)
    out.update(status="OK" if by_day else "EMPTY",
               why=None if by_day else "NO_SNAPSHOTS_AND_NO_PROVIDER_RECORDS",
               today_computed_live=live,
               days=[{"day": k, "leagues": v}
                     for k, v in sorted(by_day.items(), reverse=True)],
               alerts=alerts,
               provider_supplement=await pinnapi_supplement(conn, now=at))
    return out
