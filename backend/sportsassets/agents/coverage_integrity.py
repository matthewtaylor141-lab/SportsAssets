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
  settlement_supported  reach >= 5 -- MEASURED ONLY WHEN THE LEDGER RECORDS
                        ANY ROW PAST STAGE 4 in the window; a ledger holding
                        collection-stage refusals only cannot say how far an
                        event got past mapping, so the stage is NULL with
                        R_SETTLEMENT_UNMEASURED (settlement is then decided
                        per strategy at decision time: see decided/refused)
  evaluated             a sealed valuation of the event that day
                        (record_purpose ENTRY_DECISION or CALIBRATION_ONLY:
                        while book currency is not established every
                        valuation is sealed CALIBRATION_ONLY and the paper
                        strategies decide on it)

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
                     WARNING when it never has. Its FIRST LOSS (c28,
                     `first_loss`) is the nearest earlier stage that
                     MEASURED a count -- never an unmeasured NULL stage --
                     with that count, carried as stage_from /
                     previous_stage_count / detail.first_loss.
  COVERAGE_INCIDENT  (cand24) the per-league STATUS (`classify_status`):
                     provider events > 0 and an expected stage zero with no
                     later stage counting anything. Raised even below
                     `absent_min_provider`, as an ABSENT_DOWNSTREAM alert
                     carrying `coverage_status`, unless the detector already
                     raised that stage. A zero FOLLOWED by downstream flow is
                     a ledger gap (`measurement_gaps`), never an alert.
Every alert is persisted (coverage_collapse_alerts) and AUTOMATICALLY written
as an Audrey finding (paper_audrey_findings, kind COVERAGE_COLLAPSE) on the
current paper session; with no session the alert records why no finding
exists. Detection never changes a mapping, a mandate or an order: it reports.
"""
from __future__ import annotations

import asyncio
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
#: settlement_supported cannot be read from a ledger that records no row past
#: stage 4 in the window (collection-stage refusals only).
R_SETTLEMENT_UNMEASURED = ("NOT_MEASURED_BY_THE_COLLECTION_LEDGER: it records no row "
                           "past stage 4 in this window; settlement is decided per "
                           "strategy at decision time (see decided/refused)")
REFRESH_EVERY_S = 900.0
#: days re-computed each pass: today and yesterday (yesterday is finalised).
REFRESH_DAYS = 2

# THE STEP IS BOUNDED AND BACKS OFF (RC6.3b pass-stall). Production
# 2026-10-10: the step's first league statement ran 50+ s on the pooled
# connection, run_once's hard timeout cut the whole pass, and because the
# watermark was written only after run() completed the step was due again on
# the very next pass -- every pass died the same way for hours.
#: the step's own budget for run(), well under the pass's bound for steps
#: (paper_runtime: HARD_TIMEOUT_S less the record's reserve = 80 s). It is
#: cut further to the pass time the step is given (ctx["step_deadline"]).
RUN_BUDGET_S = 30.0
#: pass time kept after run() for the watermark and the Audrey finding
STEP_WRITE_MARGIN_S = 3.0
#: less than this of budget and the step does not start a run at all
MIN_RUN_BUDGET_S = 2.0
#: a snapshot is reported stale once it is older than the refresh interval
#: PLUS this grace: the step is due every REFRESH_EVERY_S but runs on a pass
#: (every ~2 min) and takes time, so a healthy snapshot is up to ~1,000 s old
#: just before its refresh. A snapshot older than that was not refreshed.
STALE_GRACE_S = 120.0

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
#: the step's own named outcomes (RC6.3b): a run cut at its budget, a run that
#: raised, snapshots not refreshed for longer than the refresh interval, and a
#: step given no pass time to run in
R_RUN_TIMED_OUT = "COVERAGE_RUN_EXCEEDED_ITS_BUDGET"
R_RUN_FAILED = "COVERAGE_RUN_FAILED"
R_SNAPSHOTS_STALE = "COVERAGE_SNAPSHOTS_STALE"
R_NO_PASS_TIME = "COVERAGE_STEP_HAD_NO_PASS_TIME"


def league_name(key: str) -> str:
    k = str(key or "")
    if k in LEAGUE_NAMES:
        return LEAGUE_NAMES[k]
    if k.startswith("UNATTRIBUTED"):
        return k
    if k.startswith("pinnapi_"):
        # PinnAPI-native discovery's row for the leagues of a family the lane
        # maps no key for (pinnapi_discovery.sport_key_for)
        return "PINNAPI_NATIVE:%s" % k[len("pinnapi_"):].upper()
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

def _ledger_stage_case() -> str:
    """THE STAGE OF A LEDGER ROW WRITTEN WITHOUT ONE (coverage census,
    2026-10-05): a SQL CASE over the row's first refusal, generated from the
    one lane-stage table (bettor_external_shadow.LEDGER_STAGE_OF), for the
    stages at or past venue identity -- the only ones the ranking below can
    change (a stage 1 or 2 ranks below `normalized` either way). Every code
    and stage is a fixed identifier, asserted before it is inlined."""
    import re
    from .. import bettor_external_shadow as ext
    pairs = sorted((c, s) for c, s in ext.LEDGER_STAGE_OF.items()
                   if str(s)[:1] in "345678")
    for c, s in pairs:
        assert re.fullmatch(r"[A-Z0-9_]+", c), c
        assert re.fullmatch(r"[3-8]_[A-Z_]+", s), s
    return ("CASE split_part(coalesce(first_refusal, ''), ':', 1) %s END"
            % " ".join("WHEN '%s' THEN '%s'" % cs for cs in pairs))


#: The row's stage: as written, else derived from its first refusal (rows
#: the venue-native identity refusal and the WS-unusable branch wrote with
#: no stage before the 2026-10-05 fix are still in the window).
LEDGER_STAGE_EXPR = "coalesce(stage, %s)" % _ledger_stage_case()

REACH_SQL = """
    CASE
      WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
      WHEN outcome = 'REFUSED' AND %(st)s ~ '^[1-8]_' THEN
           greatest(substr(%(st)s, 1, 1)::int,
                    CASE WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END)
      WHEN us_market_slug IS NOT NULL THEN 4
      ELSE 0
    END""" % {"st": LEDGER_STAGE_EXPR}

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
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)"""

#: THE VALUATION'S LEAGUE: its event key's ledger league, else (coverage
#: census, 2026-10-05) the league of the ledger rows that recorded ITS VENUE
#: CONTRACT. A venue-native valuation carries the fixture's STICKY event key
#: (migration 261: the first key recorded for the venue event -- a
#: "pinnapi:<id>" native seed, or another day's provider id), which need not
#: be any ledger row's provider_event_id in the lookback, so the valuation
#: fell to UNATTRIBUTED:<family> and its league read 0 evaluated although
#: the ledger recorded the very contract under that league.
LEAGUE_EXPR = ("coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || "
               "coalesce(ev.sport_family, 'unknown'))")
#: the join every downstream read adds beside `m`
MS_JOIN = "LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug"

EVALUATED_SQL = """
    WITH %s
    SELECT %s AS league, count(DISTINCT coalesce(ev.event_key,
                                                 ev.id::text)) AS n
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      %s
     WHERE ev.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND ev.decided_at >= to_timestamp($1) AND ev.decided_at < to_timestamp($2)
     GROUP BY 1
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR, MS_JOIN)

DECISIONS_SQL = """
    WITH %s,
    d AS (
        SELECT %s AS league, coalesce(ev.event_key, pd.us_market_slug) AS ek,
               bool_or(pd.verdict = 'ENTER') AS entered
          FROM paper_decisions pd
          JOIN external_valuations ev ON ev.id = pd.valuation_id
          LEFT JOIN m ON m.provider_event_id = ev.event_key
      %s
         WHERE pd.decided_at >= to_timestamp($1)
           AND pd.decided_at < to_timestamp($2)
         GROUP BY 1, 2)
    SELECT league, count(*) AS decided,
           count(*) FILTER (WHERE entered) AS entered,
           count(*) FILTER (WHERE NOT entered) AS refused
      FROM d GROUP BY league
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR, MS_JOIN)

ORDERS_SQL = """
    WITH %s
    SELECT %s AS league,
           count(DISTINCT coalesce(ev.event_key, po.us_market_slug)) AS n
      FROM paper_orders po
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      %s
     WHERE po.role = 'ENTRY'
       AND po.created_at >= to_timestamp($1) AND po.created_at < to_timestamp($2)
     GROUP BY 1
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR, MS_JOIN)

FILLS_SQL = """
    WITH %s
    SELECT %s AS league,
           count(DISTINCT coalesce(ev.event_key, pf.us_market_slug)) AS n
      FROM paper_fills pf
      JOIN paper_orders po ON po.order_id = pf.order_id
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      %s
     WHERE po.role = 'ENTRY'
       AND pf.filled_at >= to_timestamp($1) AND pf.filled_at < to_timestamp($2)
     GROUP BY 1
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR, MS_JOIN)

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
      %s
     WHERE ei.decided_at >= to_timestamp($1) AND ei.decided_at < to_timestamp($2)
     GROUP BY 1
""" % (EVENT_LEAGUE_CTE, LEAGUE_EXPR, MS_JOIN)

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
        # THE LANE'S OWN LEAGUE IDENTITY WINS (cand22): what the collector
        # maps a token to is what this count attributes it to, so the
        # coverage census, this funnel and the cycle agree that `cfb` is
        # americanfootball_ncaaf ("NCAAF").
        for t in (list(L.VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY)
                  + list(L.VENUE_TOKEN_TO_PROVIDER_KEY)
                  + [t for toks in L.VENUE_LEAGUE_TOKENS_CONFIRMED.values()
                     for t in toks]):
            key = L.provider_key_for_venue_token(t)
            if key:
                out[t] = key
    except Exception:                                           # noqa: BLE001
        pass
    return out


#: THE LEAGUE READS ARE PLAN-ROBUST AND BOUNDED (RC6.3b pass-stall).
#: Production 2026-10-10: the five league statements take 0.1-0.5 s from a
#: fresh session on every coverage window -- literal values, prepared under
#: force_generic_plan and force_custom_plan, as written or with m / ms
#: MATERIALIZED -- yet pg_stat_statements shows EVALUATED_SQL 2,051 calls,
#: mean 410 ms, max 46.5 s and ACTUAL_SQL 1,800 calls, mean 901 ms, max
#: 59.4 s, and on the API's long-lived pooled connection the first of them
#: ran 50+ s every pass. The LEADING HYPOTHESIS, NOT PROVEN: a plan cached by
#: the pooled connection (asyncpg's statement cache) on old statistics. So no
#: coverage read depends on any plan the connection has cached or on any
#: setting it holds: inside its own transaction each read plans for THIS
#: call's values (plan_cache_mode = force_custom_plan) and is cut by a
#: statement timeout of its own (SET LOCAL: gone when the transaction ends).
#: A read cut by the timeout is SOURCE_READ_FAILED, NULL, never a zero; the
#: statements themselves (and so every count) are unchanged.
#: ten times the normal read (0.1-0.5 s, mean 0.9 s at worst), and well under
#: the run's budget: one bad statement cannot spend the run
READ_STATEMENT_TIMEOUT_MS = 10_000


def _supports_plan_cache_mode(conn) -> bool:
    """plan_cache_mode exists from PostgreSQL 12."""
    try:
        return int(conn.get_server_version().major) >= 12
    except Exception:                                           # noqa: BLE001
        return False


async def _bound_the_read(conn) -> None:
    """Inside the read's transaction: a custom plan, and a statement
    timeout."""
    if _supports_plan_cache_mode(conn):
        await conn.execute("SET LOCAL plan_cache_mode = force_custom_plan")
    await conn.execute("SET LOCAL statement_timeout = %d"
                       % int(READ_STATEMENT_TIMEOUT_MS))


async def _read(conn, table_deps: tuple, sql: str, *args):
    """Rows, or a reason string when a source is absent/unreadable (or not
    readable within READ_STATEMENT_TIMEOUT_MS)."""
    for t in table_deps:
        if not await _regclass(conn, t):
            return "%s:%s" % (R_TABLE_ABSENT, t)
    try:
        async with conn.transaction():
            await _bound_the_read(conn)
            return [dict(r) for r in await conn.fetch(sql, *args)]
    except Exception as exc:                                    # noqa: BLE001
        return "%s:%s" % (R_READ_FAILED, type(exc).__name__)


async def funnel_for_day(conn, day: _dt.date, tz: str, *,
                         trace: dict | None = None) -> dict:
    """{league: row} for one local day. Never raises. `trace`, when given,
    is told which source is being read (`in_flight`, "<tz> <day> <source>"),
    so a run that is cut can say which statement it was in."""
    start, end = day_window(day, tz)
    rows: dict[str, dict] = {}
    unavailable_all: dict[str, str] = {}
    sources: dict[str, Any] = {}

    def mark(source: str) -> None:
        if trace is not None:
            trace["in_flight"] = "%s %s %s" % (tz, day.isoformat(), source)

    def row(league, family=None):
        r = rows.setdefault(league, {"league": league, "sport_family": family,
                                     "_seen": set()})
        if family and not r.get("sport_family"):
            r["sport_family"] = family
        return r

    mark("provider")
    got = await _read(conn, ("ext_candidate_outcomes",), PROVIDER_SQL,
                      start, end, list(VENUE_ABSENT))
    prov_cols = ("provider_events", "normalized_events", "venue_discovered",
                 "mapped_events", "settlement_supported")
    if isinstance(got, str):
        for c in prov_cols:
            unavailable_all[c] = got
    else:
        # the ledger can measure settlement_supported only if it records
        # rows past stage 4 at all; a refusals-only ledger reads 0 for every
        # league, which is not a measurement (NEVER ZERO FOR UNMEASURED)
        ss_measured = any(int(g["settlement_supported"] or 0) > 0 for g in got)
        if got and not ss_measured:
            unavailable_all["settlement_supported"] = R_SETTLEMENT_UNMEASURED
        for g in got:
            r = row(g["league"], g.get("family"))
            for c in prov_cols:
                if c == "settlement_supported" and not ss_measured:
                    continue
                r[c] = int(g[c])
                r["_seen"].add(c)
    sources["provider"] = "ext_candidate_outcomes"

    async def single(name, deps, sql, col_map):
        mark(name)
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

    mark("catalogue")
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


def _flows_after(row: dict, stage: str) -> bool:
    """Did any LATER stage (through `decided`) count an event? Then a zero
    at `stage` is the collection ledger not recording that step, not an
    absence: the events demonstrably got past it (the 4717460 lesson,
    applied to every stage, not only settlement). Pure."""
    later = STAGES[STAGES.index(stage) + 1: STAGES.index("decided") + 1]
    return any((row.get(COLUMN[s]) or 0) > 0 for s in later)


def first_loss(row: dict, stage: str) -> dict:
    """THE FIRST LOSS behind a zero at `stage` (c28): the nearest EARLIER
    stage that MEASURED a count, i.e. where the league's events were last
    seen, and how many were lost between it and `stage`. A NULL stage is
    unmeasured (R_SETTLEMENT_UNMEASURED and the like), so it can never be
    the stage the events were last seen at -- the 2026-10-04 NCAAF alert
    named `settlement_supported` (NULL) as its stage_from with
    previous_stage_count null, when the events were last counted at
    `mapped` (5). Skipped unmeasured stages are listed. Pure."""
    i = STAGES.index(stage)
    skipped = []
    for prev in reversed(STAGES[:i]):
        n = row.get(COLUMN[prev])
        if n is None:
            skipped.append(prev)
            continue
        here = row.get(COLUMN[stage])
        return {"stage": stage, "stage_count": here, "after_stage": prev,
                "after_count": n,
                "lost": (None if here is None else max(int(n) - int(here), 0)),
                "skipped_unmeasured": skipped}
    return {"stage": stage, "stage_count": row.get(COLUMN[stage]),
            "after_stage": None, "after_count": None, "lost": None,
            "skipped_unmeasured": skipped}


def first_loss_statement(row: dict, fl: dict) -> str:
    return "FIRST LOSS %s: %s (%s) -> %s (%s)%s" % (
        league_name(row.get("league")), fl["after_stage"], fl["after_count"],
        fl["stage"], fl["stage_count"],
        ("; unmeasured between: %s" % ", ".join(fl["skipped_unmeasured"])
         if fl["skipped_unmeasured"] else ""))


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
            if n == 0 and _flows_after(today, st):
                continue          # later stages flowed: a ledger gap, not absence
            if n == 0:
                fl = first_loss(today, st)
                prev = fl["after_stage"] or STAGES[STAGES.index(st) - 1]
                flowed = stage_flowed(history, st)
                alerts.append({
                    "kind": "ABSENT_DOWNSTREAM", "league": league,
                    "stage_from": prev, "stage_to": st,
                    "ratio": 0.0, "baseline": None,
                    "severity": "CRITICAL" if flowed else "WARNING",
                    "detail": {
                        "provider_events": prov,
                        "previous_stage_count": fl["after_count"],
                        "stage_count": 0,
                        "flowed_in_baseline": flowed,
                        "first_loss": fl,
                        "statement": (
                            "%s: %d provider event(s); none reached %s; %s"
                            % (league_name(league), prov, st,
                               first_loss_statement(today, fl)))}})
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
    """Upsert every league row of one computed day, in one transaction.
    Returns rows written."""
    day = _dt.date.fromisoformat(f["day"])
    final = now >= f["window"][1]
    n = 0
    cols = [COLUMN[s] for s in STAGES] + list(EXTRA_COLUMNS)
    # ONE DAY IS WRITTEN WHOLE OR NOT AT ALL (RC6.3b): a run cut between two
    # league rows leaves none of this day's rows, and the days before it stay
    async with conn.transaction():
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
              days: int = REFRESH_DAYS, progress: dict | None = None) -> dict:
    """Compute and persist the last `days` local days in both timezones,
    then detect collapses on ALERT_TIMEZONE days. Never raises
    (CancelledError excepted).

    `progress`, when given, IS the result dict, filled as the run goes
    (`snapshots` written so far, `windows_done`, the source `in_flight`): a
    caller that cuts the run (the step's budget) still has what it persisted
    -- each day is written whole, in its own transaction, so a run cut part
    way keeps every day it had finished."""
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = progress if progress is not None else {}
    out.update({"version": VERSION, "at": at, "snapshots": 0, "alerts": [],
                "errors": {}, "windows_done": [], "in_flight": None})
    if not await _regclass(conn, "coverage_funnel_snapshots"):
        return dict(out, ran=False, why="MIGRATION_209_NOT_APPLIED")
    for tz in TIMEZONES:
        today = local_day(at, tz)
        for back in range(days - 1, -1, -1):
            day = today - _dt.timedelta(days=back)
            out["in_flight"] = "%s %s" % (tz, day.isoformat())
            try:
                f = await funnel_for_day(conn, day, tz, trace=out)
                out["snapshots"] += await persist_day(conn, f, now=at)
                out["windows_done"].append("%s %s" % (tz, day.isoformat()))
            except Exception as exc:                            # noqa: BLE001
                out["errors"]["%s:%s" % (tz, day)] = "%s: %s" % (
                    type(exc).__name__, str(exc)[:160])
                continue
            if tz != ALERT_TIMEZONE:
                continue
            live_decisions = any((r.get("decided_events") or 0) > 0
                                 for r in f["leagues"].values())
            for league, r in f["leagues"].items():
                if league.startswith("UNATTRIBUTED"):
                    continue
                try:
                    hist = await history_for(conn, tz, league, day)
                except Exception:                               # noqa: BLE001
                    hist = []
                found = detect(dict(r, league=league), hist)
                # A COVERAGE_INCIDENT IS ALWAYS AN ALERT (cand24): when the
                # collapse detector did not already raise one for that stage
                # (it needs absent_min_provider events), the status raises
                # it through the same table and the same Audrey finding.
                inc = incident_alert(dict(r, league=league), hist, found,
                                     decisions_live=live_decisions)
                if inc is not None:
                    found.append(inc)
                for a in found:
                    out["alerts"].append(await raise_alert(
                        conn, a, tz=tz, day=day.isoformat(), now=at,
                        ctx=ctx))
    out["ran"] = True
    out["in_flight"] = None
    return out


def _run_budget_s(ctx: dict) -> float:
    """The run's budget: RUN_BUDGET_S, cut to the pass time the step was
    given (`ctx["step_deadline"]`, a monotonic instant the pass sets) less the
    time kept to write the watermark. No deadline in ctx (a direct call) is
    the whole RUN_BUDGET_S."""
    budget = float(RUN_BUDGET_S)
    dl = ctx.get("step_deadline")
    if dl is not None:
        budget = min(budget, float(dl) - time.monotonic()
                     - float(STEP_WRITE_MARGIN_S))
    return budget


async def _write_watermark(conn, value: dict) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        WATERMARK_KEY, json.dumps(value, default=str))


async def _failure_finding(conn, ctx: dict, *, at: float, wm: dict) -> dict:
    """A failed or timed-out run, to Audrey: one finding per UTC day (the
    snapshots are going stale; the daily report names it). Best effort -- it
    never changes the step's result."""
    if not (ctx.get("session_id") and ctx.get("account_id")):
        return {"ok": False, "refusal": R_NO_SESSION}
    try:
        from . import paper_audrey as PA
        day = _dt.datetime.fromtimestamp(at, _dt.timezone.utc).date()
        code = R_RUN_TIMED_OUT if wm.get("timed_out") else R_RUN_FAILED
        async with conn.transaction():
            got = await PA.finding(
                conn, dict(ctx, now=at), kind=R_RUN_FAILED,
                subject=WATERMARK_KEY, severity="WARNING",
                scope=day.isoformat(),
                detail={"code": code, "timed_out": bool(wm.get("timed_out")),
                        "why": wm.get("why"), "at": at,
                        "snapshots_written": wm.get("snapshots"),
                        "in_flight": wm.get("in_flight"),
                        "budget_s": wm.get("budget_s"),
                        "refresh_every_s": REFRESH_EVERY_S,
                        "stale_after_s": REFRESH_EVERY_S + STALE_GRACE_S,
                        "consequence": "the coverage snapshots are not "
                                       "refreshed until the next attempt "
                                       "(%s s); Command reports them %s "
                                       "once older than the stale bound"
                                       % (int(REFRESH_EVERY_S),
                                          R_SNAPSHOTS_STALE),
                        "version": VERSION})
        return {"ok": True, "finding_id": got["finding_id"]}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": "%s:%s" % (R_FINDING_FAILED,
                                                   type(exc).__name__)}


async def step(conn, ctx: dict) -> dict:
    """THE SCHEDULED HOOK (paper pass): at most every REFRESH_EVERY_S, on
    the main paper account's session only. Never raises.

    BOUNDED AND BACKING OFF (RC6.3b): run() has its own budget
    (_run_budget_s). On a timeout or an error the step STILL advances its
    watermark with the failure recorded ({"at", "version", "timed_out",
    "why", ...}), so it is not retried on every pass but after
    REFRESH_EVERY_S; it returns a named error code (R_RUN_TIMED_OUT /
    R_RUN_FAILED) and writes one Audrey finding for the day. What the run had
    persisted before it was cut stays (a day is written whole). A step given
    no time to run (R_NO_PASS_TIME) leaves the watermark alone: nothing
    failed in coverage, it is simply due again."""
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
            backed_off = (R_RUN_TIMED_OUT if last.get("timed_out") else
                          R_RUN_FAILED if last.get("failed") else None)
            return {"ran": False, "why": "NOT_DUE", "last_at": last["at"],
                    "backed_off": backed_off,
                    "next_due_in_s": round(
                        REFRESH_EVERY_S - (at - float(last["at"])), 3)}
        budget = _run_budget_s(ctx)
        if budget < MIN_RUN_BUDGET_S:
            return {"ran": False, "why": R_NO_PASS_TIME,
                    "budget_s": round(budget, 3)}
        progress: dict[str, Any] = {}
        t_run = time.monotonic()
        in_tx = bool(getattr(conn, "is_in_transaction", lambda: False)())
        failure: dict | None = None
        bound = asyncio.timeout(budget)
        try:
            async with bound:
                res = await run(conn, now=at, ctx=ctx, progress=progress)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            took = round(time.monotonic() - t_run, 3)
            if bound.expired():
                failure = {"timed_out": True, "failed": False,
                           "why": "%s: run() was cut at its %.1fs budget "
                                  "after %.1fs%s" % (
                                      R_RUN_TIMED_OUT, budget, took,
                                      (" in %s" % progress["in_flight"])
                                      if progress.get("in_flight") else "")}
            else:
                failure = {"timed_out": False, "failed": True,
                           "why": "%s: %s: %s" % (R_RUN_FAILED,
                                                  type(exc).__name__,
                                                  str(exc)[:160])}
            failure.update(
                snapshots=int(progress.get("snapshots") or 0),
                in_flight=progress.get("in_flight"),
                windows_done=list(progress.get("windows_done") or []),
                budget_s=round(budget, 3), elapsed_s=took)
        if failure is None:
            await _write_watermark(conn, {
                "at": at, "version": VERSION,
                "alerts": len(res.get("alerts") or [])})
            return {"ran": bool(res.get("ran")),
                    "snapshots": res.get("snapshots"),
                    "alerts": len(res.get("alerts") or []),
                    "errors": res.get("errors")}
        # a cancelled run may have left a transaction of its own open
        try:
            if getattr(conn, "is_in_transaction", lambda: False)() \
                    and not in_tx:
                await conn.execute("ROLLBACK")
        except Exception:                                       # noqa: BLE001
            pass
        wm = dict(failure, at=at, version=VERSION)
        await _write_watermark(conn, wm)
        finding = await _failure_finding(conn, ctx, at=at, wm=wm)
        return {"ran": False, "timed_out": failure["timed_out"],
                "error": R_RUN_TIMED_OUT if failure["timed_out"]
                else R_RUN_FAILED,
                "why": failure["why"], "snapshots": failure["snapshots"],
                "in_flight": failure["in_flight"],
                "budget_s": failure["budget_s"],
                "next_due_in_s": REFRESH_EVERY_S,
                "audrey_finding": finding.get("finding_id"),
                "audrey_refusal": finding.get("refusal")}
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


def stale_after_s() -> float:
    """The age past which a non-final snapshot is stale: the refresh interval
    plus the grace a late pass is allowed."""
    return float(REFRESH_EVERY_S) + float(STALE_GRACE_S)


async def snapshot_freshness(conn, *, now: float, tz: str = ALERT_TIMEZONE,
                             days: int = 7) -> dict:
    """HOW OLD THE SNAPSHOTS COMMAND SHOWS ARE, BY NAME (RC6.3b). The newest
    snapshot of the window `coverage_payload` returns older than the refresh
    interval (plus the grace a late pass gets) is COVERAGE_SNAPSHOTS_STALE --
    never "current". With the age comes the step's last attempt (the
    watermark: a run that timed out or failed says so and why). A final day
    is never refreshed again by design and is not judged here."""
    out: dict[str, Any] = {
        "status": "NO_SNAPSHOTS", "code": None, "newest_computed_at": None,
        "age_s": None, "refresh_every_s": REFRESH_EVERY_S,
        "stale_after_s": stale_after_s(), "last_attempt": None}
    try:
        today = local_day(now, tz)
        newest = await conn.fetchval(
            "SELECT extract(epoch FROM max(computed_at))::float8 FROM "
            " coverage_funnel_snapshots WHERE tz=$1 AND day > $2::date - $3::int"
            " AND NOT final", tz, today, int(days))
        if newest is None:
            # nothing live in the window: judge the newest of any kind
            newest = await conn.fetchval(
                "SELECT extract(epoch FROM max(computed_at))::float8 FROM "
                " coverage_funnel_snapshots WHERE tz=$1 AND day > "
                " $2::date - $3::int", tz, today, int(days))
        if newest is not None:
            age = float(now) - float(newest)
            stale = age > stale_after_s()
            out.update(newest_computed_at=float(newest),
                       age_s=round(age, 3),
                       status=R_SNAPSHOTS_STALE if stale else "CURRENT",
                       code=R_SNAPSHOTS_STALE if stale else None)
    except Exception as exc:                                    # noqa: BLE001
        out.update(status="UNAVAILABLE", why="%s: %s" % (
            type(exc).__name__, str(exc)[:160]))
    try:
        from .. import bettor_paper_ledger as L
        last = L._j(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            WATERMARK_KEY)) or {}
        if last:
            out["last_attempt"] = {k: last.get(k) for k in (
                "at", "version", "timed_out", "failed", "why", "snapshots",
                "in_flight", "budget_s", "elapsed_s", "alerts")
                if k in last}
    except Exception:                                           # noqa: BLE001
        pass
    return out


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
        # A PERSISTED ROW IS NEVER SHOWN AS CURRENT WITHOUT ITS AGE (RC6.3b):
        # past the stale bound a row the step should have refreshed says so
        # by name; a final day is not refreshed again by design
        age = (at - float(d["computed_at"])
               if d.get("computed_at") is not None else None)
        d["age_s"] = None if age is None else round(age, 3)
        d["stale"] = bool(age is not None and not d.get("final")
                          and age > stale_after_s())
        d["stale_code"] = R_SNAPSHOTS_STALE if d["stale"] else None
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
    try:
        out["league_status"] = await league_status_table(
            conn, rows=by_day.get(today.isoformat()) or [], day=today, tz=tz,
            now=at)
    except Exception as exc:                                    # noqa: BLE001
        out["league_status"] = {"status": "UNAVAILABLE", "why": "%s: %s" % (
            type(exc).__name__, str(exc)[:160])}
    try:
        out["nfl_reconciliation"] = await reconcile_league(
            conn, token="nfl", day=today, tz=tz, now=at)
    except Exception as exc:                                    # noqa: BLE001
        out["nfl_reconciliation"] = {"status": "UNAVAILABLE", "why": "%s: %s" % (
            type(exc).__name__, str(exc)[:160])}
    out["snapshot_freshness"] = await snapshot_freshness(
        conn, now=at, tz=tz, days=days)
    out.update(status="OK" if by_day else "EMPTY",
               why=None if by_day else "NO_SNAPSHOTS_AND_NO_PROVIDER_RECORDS",
               today_computed_live=live,
               days=[{"day": k, "leagues": v}
                     for k, v in sorted(by_day.items(), reverse=True)],
               alerts=alerts,
               provider_supplement=await pinnapi_supplement(conn, now=at))
    return out


# ═════════════════════════════════════════════════════════════════════
# PER-LEAGUE STATUS (cand24): EXACTLY ONE OF FIVE, WITH ITS REASON
# ═════════════════════════════════════════════════════════════════════
#
#   HEALTHY                 provider events reach decisions and at least one
#                           ENTER
#   REFUSING_BY_POLICY      reaches decisions, every one REFUSED by a named
#                           policy (NFL until R30A: SETTLEMENT_NOT_SUPPORTED
#                           everywhere; since R30A the completed-game policy
#                           prices the NFL tie from cited evidence and the
#                           strict policy still refuses settlement by name --
#                           bettor_nfl_settlement.STRICT_POLICY_MISSING; NCAAF
#                           the same since the P0 incident: the completed-game
#                           policy reads the cited college contract and the
#                           strict refusal names each payout difference --
#                           bettor_ncaaf_settlement.STRICT_CODES)
#   EXPLICITLY_UNSUPPORTED  not in the collector's scope by its declared maps
#                           (ext_pinnacle_loop), the reason named
#   COVERAGE_INCIDENT       provider events > 0 and an expected stage zero
#                           with nothing downstream of it (NULL is unmeasured,
#                           never absent; a zero followed by later flow is a
#                           ledger gap, not an incident) -- always an alert
#   UNAVAILABLE             in scope, but nothing to measure: no provider
#                           events (not requested, budget, provider does not
#                           list), the venue lists none, or a source unread
#
# Display and alerting only: no admission path reads a status.

S_HEALTHY = "HEALTHY"
S_REFUSING = "REFUSING_BY_POLICY"
S_UNSUPPORTED = "EXPLICITLY_UNSUPPORTED"
S_INCIDENT = "COVERAGE_INCIDENT"
S_UNAVAILABLE = "UNAVAILABLE"
LEAGUE_STATUSES = (S_HEALTHY, S_REFUSING, S_UNSUPPORTED, S_INCIDENT,
                   S_UNAVAILABLE)
#: The stages a provider-present, in-scope league is expected to reach.
STATUS_STAGES = ("normalized", "venue_discovered", "mapped",
                 "settlement_supported", "evaluated", "decided")
COLLECTOR_KEY = "ext_pinnacle_last_cycle"
COLLECTOR_FRESH_S = 3 * 900.0

#: Families out of scope by declaration, and why.
#: INCIDENT RELEASE (verifier finding 1): the basketball / hockey / tennis
#: notes said those families were outside the de-vig set and requested by no
#: collector. inc-pinnapi subscribes all six PinnAPI sports, PinnAPI-native
#: discovery seeds every family with a priced market (the de-vig set now
#: carries basketball spread / total and hockey spread / total / team total),
#: and tennis is matched but not seeded -- so those notes were false and
#: `lane_scope` now derives the answer for a PinnAPI family from the code
#: (`native_scope`). What remains here is out of scope whatever PinnAPI does.
FAMILY_SCOPE_NOTE = {
    "futures": "an outright/futures listing is not a fixture",
}


def native_scope() -> dict:
    """PinnAPI-native discovery's families, read from the code it runs:
    {"families": the primary selector's sports (pinnapi_primary.SPORTS),
     "seeded": those with a priced market
     (pinnapi_feed_runtime.families_with_a_priced_market)}. Empty sets when
    either cannot be read -- never a guessed scope."""
    try:
        from .. import pinnapi_primary as P
        from .. import pinnapi_feed_runtime as FR
        fams = {str(f) for f in P.SPORTS}
        return {"families": fams,
                "seeded": fams & {str(f) for f in
                                  FR.families_with_a_priced_market()}}
    except Exception:                                           # noqa: BLE001
        return {"families": set(), "seeded": set()}


def lane_scope(league: str, *, token: str | None = None,
               family: str | None = None) -> dict:
    """Is this league in the collector's declared scope? {"in_scope", "why"}.
    Read from ext_pinnacle_loop's maps -- the same identity the cycle uses --
    and, for PinnAPI families, from what PinnAPI-native discovery seeds.
    `via` names a native-discovery scope; `measured_under` names the native
    row a league's provider events are counted under when the lane maps no
    key of its own (pinnapi_discovery.sport_key_for). Pure."""
    from ..workers import ext_pinnacle_loop as L
    from .. import bettor_venue_realism as vreal
    from .. import pinnapi_discovery as PD
    keys = ({k for k, _ in L.SPORTS_CONFIRMED}
            | set(L.VENUE_TOKEN_TO_PROVIDER_KEY.values())
            | set(L.VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY.values()))
    if league in keys:
        return {"in_scope": True, "why": None}
    nat = native_scope()
    if league.startswith("pinnapi_") and \
            league[len("pinnapi_"):] in nat["seeded"]:
        # a native discovery row (a token the lane maps no key for)
        return {"in_scope": True, "why": None,
                "via": "PINNAPI_NATIVE_DISCOVERY"}
    tok = str(token or "").lower()
    fam = str(family or "").lower()
    if tok in L.VENUE_TOKENS_DELIBERATELY_EXCLUDED:
        return {"in_scope": False, "why": "DELIBERATELY_EXCLUDED: %s"
                % L.VENUE_TOKENS_DELIBERATELY_EXCLUDED[tok]}
    if tok in L.VENUE_TOKENS_WITH_A_REFUTED_MAPPING:
        return {"in_scope": False, "why": (
            "MAPPING_REFUTED_BY_THE_VENUE_FIXTURES: %s" % str(
                L.VENUE_TOKENS_WITH_A_REFUTED_MAPPING[tok].get(
                    "why_it_is_out", ""))[:200])}
    if fam and any(fam.startswith(p.rstrip("_"))
                   for p in vreal.SIMULATED_SPORTS_TYPE_PREFIXES):
        return {"in_scope": False,
                "why": "SIMULATED_COMPETITION: %s is a simulated family" % fam}
    if fam in FAMILY_SCOPE_NOTE:
        return {"in_scope": False, "why": "FAMILY_NOT_IN_COLLECTOR_SCOPE: %s"
                % FAMILY_SCOPE_NOTE[fam]}
    lane_families = {L.family_for_provider_key(k) for k in keys}
    if fam in nat["seeded"] and fam not in lane_families:
        # a family the metered lane has no key for at all (basketball,
        # hockey): native discovery seeds it, under its native key
        return {"in_scope": True, "why": None,
                "via": "PINNAPI_NATIVE_DISCOVERY",
                "measured_under": PD.sport_key_for(fam)}
    if fam in nat["families"] and fam not in nat["seeded"]:
        return {"in_scope": False, "why": (
            "FAMILY_NOT_SEEDED_NO_PRICED_MARKET: PinnAPI-native discovery "
            "matches %s fixtures but seeds none, because no %s market family "
            "is priced (pinnapi_feed_runtime.families_with_a_priced_market: "
            "the de-vig set and the proven line families)" % (fam, fam))}
    if league.startswith("UNATTRIBUTED"):
        return {"in_scope": False, "why": "UNATTRIBUTED: the record's provider "
                "event has no league in the collection ledger"}
    return {"in_scope": False, "why": (
        "VENUE_TOKEN_NOT_MAPPED: %s has no provider key in the collector's "
        "maps (ext_pinnacle_loop SPORTS_CONFIRMED / VENUE_TOKEN_TO_PROVIDER_KEY "
        "/ VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY)" % (tok or league)
        + ("; PinnAPI-native discovery reports any fixture of it it matches "
           "under %s" % PD.sport_key_for(fam) if fam in nat["seeded"]
           else ""))}


def classify_status(row: dict, *, scope: dict, collector: dict | None = None,
                    decisions_live: bool = False) -> dict:
    """ONE status for one league's day row. Pure.

    `scope` is `lane_scope`'s answer; `collector` the last cycle's selection
    ({"fresh", "requested", "rejected": {key: refusal}, "budget_dropped"})
    used only to NAME why an in-scope league has no provider events.
    `decisions_live` says the paper decision path recorded decisions for SOME
    league that day: only then is a league's zero at `decided` unexpected (a
    paper session that is off decides nothing anywhere, which is not an
    incident in any one league)."""
    out = {"status": None, "reason": None, "stage": None,
           "measurement_gaps": []}
    if not scope.get("in_scope"):
        return dict(out, status=S_UNSUPPORTED, reason=scope.get("why"))
    league = row.get("league")
    prov = row.get("provider_events")
    if prov is None:
        return dict(out, status=S_UNAVAILABLE, reason="PROVIDER_STAGE_UNMEASURED: "
                    + str((row.get("unavailable") or {}).get(
                        "provider_events") or "no ledger read"))
    if prov == 0 and scope.get("measured_under"):
        # NOT A METERED-COLLECTOR QUESTION (verifier finding 1): the lane has
        # no key for this family; native discovery counts its fixtures under
        # one native row, so this league's own row cannot carry them
        return dict(out, status=S_UNAVAILABLE, reason=(
            "MEASURED_UNDER_THE_PINNAPI_NATIVE_ROW: %s carries this family's "
            "natively discovered provider events; the lane maps no provider "
            "key to this league, so a per-league count is not measured; "
            "venue lists %s event(s)" % (
                scope["measured_under"],
                "an unmeasured number of"
                if row.get("venue_catalogue_events") is None
                else row.get("venue_catalogue_events"))))
    if prov == 0:
        c = collector or {}
        venue = row.get("venue_catalogue_events")
        # R30A: the collector's durable per-cycle receipts (migration 248),
        # when read, say what happened to THIS league in the last 24 h -- not
        # only in the one cycle the heartbeat describes.
        rec = ((c.get("receipts") or {}).get("by_competition") or {}).get(
            league) or {}
        last = rec.get("last_receipt")
        if not c.get("fresh"):
            why = "COLLECTOR_HEARTBEAT_NOT_CURRENT"
        elif league in (c.get("rejected") or {}):
            why = "PROVIDER_REFUSED: %s" % c["rejected"][league]
        elif league in (c.get("budget_dropped") or ()):
            why = ("NOT_REQUESTED_METERED_BUDGET_SPENT: the cycle's %s "
                   "metered calls went to higher-priority competitions"
                   % c.get("budget", "?")) + _receipt_note(rec)
        elif last == "SKIPPED_NO_VENUE_EVENT_IN_HORIZON":
            why = ("NOT_REQUESTED_NO_VENUE_EVENT_IN_THE_COLLECTOR_HORIZON"
                   + _receipt_note(rec))
        elif last == "FETCH_FAILED":
            why = "REQUESTED_THE_PROVIDER_CALL_FAILED" + _receipt_note(rec)
        elif league in (c.get("requested") or ()):
            why = "REQUESTED_THE_PROVIDER_RETURNED_NO_EVENTS"
        else:
            why = "NOT_A_CANDIDATE_THIS_CYCLE (not on the collector's board)"
        return dict(out, status=S_UNAVAILABLE, reason=(
            "NO_PROVIDER_EVENTS: %s; venue lists %s event(s)"
            % (why, "an unmeasured number of" if venue is None else venue)))
    if row.get("venue_catalogue_events") == 0 and \
            not (row.get("venue_discovered") or 0):
        return dict(out, status=S_UNAVAILABLE, reason=(
            "VENUE_LISTS_NO_EVENTS: the provider listed %d event(s) the venue "
            "does not list in this window" % prov))
    for st in STATUS_STAGES:
        n = row.get(COLUMN[st])
        if n is None:
            continue                                  # NULL is not absence
        if st == "decided" and not decisions_live:
            continue
        if n == 0:
            if _flows_after(row, st):
                out["measurement_gaps"].append(st)
                continue
            return dict(out, status=S_INCIDENT, stage=st, reason=(
                "%d provider event(s); none reached %s and nothing reached a "
                "later stage" % (prov, st)))
    entered = row.get("entered_events")
    decided = row.get("decided_events")
    if entered:
        return dict(out, status=S_HEALTHY, reason=(
            "%d of %s decided event(s) ENTERED" % (entered, decided)))
    if decided:
        return dict(out, status=S_REFUSING, reason=(
            "all %d decided event(s) REFUSED by a named policy" % decided))
    return dict(out, status=S_UNAVAILABLE, reason=(
        "NO_PAPER_DECISIONS_RECORDED: %s evaluated event(s); the paper "
        "decision path recorded none for any league in this window"
        % row.get("evaluated_events")))


def incident_alert(row: dict, history: list, already: list, *,
                   decisions_live: bool = False) -> dict | None:
    """The alert a COVERAGE_INCIDENT raises, unless `detect` already raised
    one for that stage. Kind ABSENT_DOWNSTREAM (present upstream, absent
    downstream -- which is what an incident is), carried with
    `coverage_status`. Pure."""
    league = row.get("league") or ""
    st = classify_status(row, scope=lane_scope(league),
                         decisions_live=decisions_live)
    if st["status"] != S_INCIDENT:
        return None
    for a in already:
        if a.get("kind") == "ABSENT_DOWNSTREAM" and \
                a.get("stage_to") == st["stage"]:
            a.setdefault("detail", {})["coverage_status"] = S_INCIDENT
            return None
    stage = st["stage"]
    fl = first_loss(row, stage)
    prev = fl["after_stage"] or STAGES[STAGES.index(stage) - 1]
    flowed = stage_flowed(history, stage)
    return {"kind": "ABSENT_DOWNSTREAM", "league": league,
            "stage_from": prev, "stage_to": stage, "ratio": 0.0,
            "baseline": None, "severity": "CRITICAL" if flowed else "WARNING",
            "detail": {"coverage_status": S_INCIDENT,
                       "provider_events": row.get("provider_events"),
                       "previous_stage_count": fl["after_count"],
                       "stage_count": 0, "flowed_in_baseline": flowed,
                       "first_loss": fl,
                       "statement": "%s: %s; %s" % (
                           league_name(league), st["reason"],
                           first_loss_statement(row, fl))}}


VENUE_TOKENS_SQL = """
    SELECT lower(split_part(coalesce(event_slug, ''), '-', 1)) AS token,
           mode() WITHIN GROUP (ORDER BY split_part(coalesce(sports_type, ''),
                                                    '_', 1)) AS family,
           count(DISTINCT event_slug) AS events
      FROM us_premap
     WHERE game_start >= to_timestamp($1) AND game_start < to_timestamp($2)
       AND event_slug IS NOT NULL
     GROUP BY 1
"""


# ── THE COLLECTOR'S COVERAGE RECEIPTS (migration 248, R30A) ─────────────
#
# THE DEFECT THESE REPLACE AS THE DESK'S SOURCE. `budget_dropped` lived on
# ONE heartbeat row that every cycle overwrote, so the desk could say what the
# LAST cycle dropped and nothing about the day: NCAAF was unfetched in 137 of
# the 153 cycles with a venue cfb event in the next 24 h (research-sql run
# 37233454453) and no record of it existed until it was reconstructed by
# hand. The scheduled cycle now appends one receipt per competition per cycle
# (requested / served / budget-dropped with its reason and promised slot /
# skipped, cycles since served) and one budget row per cycle (calls made
# against the declared budget). Read-only here; never raises.
#
# THE COLLECTOR IS THE LEASE HOLDER. Only rows written under the collector's
# single-writer lease (`writer_lease = 'HELD'`) are the collector's receipts;
# a cycle run without the lease (a test harness, a one-off run) is counted
# apart (`cycles_without_lease`) and never mixed into the desk's figures.

COVERAGE_RECEIPTS_WINDOW_S = 86400.0
COVERAGE_RECEIPTS_SQL = """
    SELECT competition,
           count(*) AS cycles,
           count(*) FILTER (WHERE planned = 'SCHEDULED') AS requested,
           count(*) FILTER (WHERE receipt = 'FETCHED') AS served,
           count(*) FILTER (WHERE receipt = 'FETCH_FAILED') AS fetch_failed,
           count(*) FILTER (WHERE receipt IN (
               'DEFERRED_TO_SLOT', 'DEFERRED_NO_SLOT_WITHIN_ENVELOPE'))
             AS budget_dropped,
           count(*) FILTER (WHERE receipt = 'SKIPPED_NO_VENUE_EVENT_IN_HORIZON')
             AS skipped_no_venue_event,
           count(*) FILTER (WHERE receipt IN (
               'PROVIDER_DOES_NOT_LIST', 'PROVIDER_LISTS_INACTIVE',
               'PROVIDER_CATALOGUE_UNREAD')) AS provider_refused,
           (array_agg(receipt ORDER BY cycle_at DESC, id DESC))[1]
             AS last_receipt,
           (array_agg(why ORDER BY cycle_at DESC, id DESC))[1] AS last_why,
           (array_agg(cycles_since_served ORDER BY cycle_at DESC, id DESC))[1]
             AS cycles_since_served,
           (array_agg(extract(epoch FROM next_slot_at)
                      ORDER BY cycle_at DESC, id DESC))[1] AS next_slot_at,
           (array_agg(bound_cycles ORDER BY cycle_at DESC, id DESC))[1]
             AS bound_cycles,
           (array_agg(starvation_bound_cycles
                      ORDER BY cycle_at DESC, id DESC))[1]
             AS starvation_bound_cycles,
           extract(epoch FROM max(cycle_at)) AS last_cycle_at,
           extract(epoch FROM max(cycle_at) FILTER (WHERE receipt = 'FETCHED'))
             AS last_fetched_at,
           coalesce(sum(credits_charged), 0) AS credits
      FROM collector_coverage_receipts
     WHERE scope = 'COMPETITION'
       AND writer_lease = 'HELD'
       AND cycle_at > to_timestamp($1) - make_interval(secs => $2)
       AND cycle_at <= to_timestamp($1)
     GROUP BY competition
"""
COVERAGE_CYCLES_SQL = """
    SELECT count(*) FILTER (WHERE writer_lease = 'HELD') AS cycles,
           count(*) FILTER (WHERE writer_lease = 'HELD'
                              AND calls_made > calls_budget) AS over_budget,
           max(calls_made) FILTER (WHERE writer_lease = 'HELD')
             AS max_calls_made,
           max(calls_budget) FILTER (WHERE writer_lease = 'HELD')
             AS calls_budget,
           coalesce(sum(credits_spent) FILTER (WHERE writer_lease = 'HELD'),
                    0) AS credits_spent,
           extract(epoch FROM max(cycle_at) FILTER (
               WHERE writer_lease = 'HELD')) AS last_cycle_at,
           (array_agg(cycle_id ORDER BY cycle_at DESC, id DESC) FILTER (
               WHERE writer_lease = 'HELD'))[1] AS last_cycle_id,
           count(*) FILTER (WHERE writer_lease <> 'HELD')
             AS cycles_without_lease
      FROM collector_coverage_receipts
     WHERE scope = 'CYCLE'
       AND cycle_at > to_timestamp($1) - make_interval(secs => $2)
       AND cycle_at <= to_timestamp($1)
"""
COVERAGE_LAST_CYCLE_SQL = """
    SELECT competition, planned, receipt, why, cycles_since_served,
           extract(epoch FROM next_slot_at) AS next_slot_at,
           venue_events_in_horizon
      FROM collector_coverage_receipts
     WHERE scope = 'COMPETITION' AND cycle_id = $1
       AND writer_lease = 'HELD'
     ORDER BY priority_rank NULLS LAST, competition
"""
#: The receipts that are a BUDGET DROP (collector_coverage.BUDGET_DROPPED).
RECEIPT_BUDGET_DROPPED = ("DEFERRED_TO_SLOT",
                          "DEFERRED_NO_SLOT_WITHIN_ENVELOPE")


def _num_or_none(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


async def collector_receipts(conn, *, now: float,
                             window_s: float = COVERAGE_RECEIPTS_WINDOW_S
                             ) -> dict:
    """The collector's coverage receipts over the last `window_s`: per
    competition requested / served / fetch_failed / budget_dropped / skipped
    counts with the latest receipt, its reason, its promised slot and the
    cycles since it was last served; the cycles' budget rows (calls made
    against the declared budget, any cycle over it); and the latest cycle's
    rows. Never raises: an absent table is MIGRATION_248_NOT_APPLIED, a
    failed read is named -- never an empty, healthy-looking answer."""
    out: dict[str, Any] = {
        "read": False, "window_s": window_s,
        "source": "collector_coverage_receipts (migration 248)",
        "lease": "only rows written under the collector's single-writer "
                 "lease (writer_lease = 'HELD')",
        "by_competition": {}, "cycles": None, "last_cycle": None,
        "cycles_without_lease": None}
    if not await _regclass(conn, "collector_coverage_receipts"):
        out["why"] = "MIGRATION_248_NOT_APPLIED"
        return out
    try:
        async with conn.transaction():
            comp = await conn.fetch(COVERAGE_RECEIPTS_SQL, float(now),
                                    float(window_s))
            cyc = await conn.fetchrow(COVERAGE_CYCLES_SQL, float(now),
                                      float(window_s))
            last = []
            if cyc is not None and cyc["last_cycle_id"] is not None:
                last = await conn.fetch(COVERAGE_LAST_CYCLE_SQL,
                                        cyc["last_cycle_id"])
    except Exception as exc:                                    # noqa: BLE001
        out["why"] = "%s:%s" % (R_READ_FAILED, type(exc).__name__)
        return out
    for r in comp:
        out["by_competition"][str(r["competition"])] = {
            "cycles": int(r["cycles"]), "requested": int(r["requested"]),
            "served": int(r["served"]),
            "fetch_failed": int(r["fetch_failed"]),
            "budget_dropped": int(r["budget_dropped"]),
            "skipped_no_venue_event": int(r["skipped_no_venue_event"]),
            "provider_refused": int(r["provider_refused"]),
            "last_receipt": r["last_receipt"], "last_why": r["last_why"],
            "cycles_since_served": r["cycles_since_served"],
            "next_slot_at": _num_or_none(r["next_slot_at"]),
            "bound_cycles": r["bound_cycles"],
            "starvation_bound_cycles": r["starvation_bound_cycles"],
            "last_cycle_at": _num_or_none(r["last_cycle_at"]),
            "last_fetched_at": _num_or_none(r["last_fetched_at"]),
            "credits": float(r["credits"] or 0.0)}
    # cycles run WITHOUT the writer lease in the window: recorded, named,
    # and kept out of every figure above
    out["cycles_without_lease"] = (0 if cyc is None
                                   else int(cyc["cycles_without_lease"] or 0))
    if cyc is not None and int(cyc["cycles"] or 0) > 0:
        out["cycles"] = {
            "cycles": int(cyc["cycles"]),
            "over_budget": int(cyc["over_budget"] or 0),
            "max_calls_made": cyc["max_calls_made"],
            "calls_budget": cyc["calls_budget"],
            "credits_spent": float(cyc["credits_spent"] or 0.0),
            "last_cycle_at": _num_or_none(cyc["last_cycle_at"]),
            "last_cycle_id": cyc["last_cycle_id"],
            "writer_lease": "HELD",
            "cycles_without_lease": out["cycles_without_lease"]}
        out["last_cycle"] = [
            {"competition": r["competition"], "planned": r["planned"],
             "receipt": r["receipt"], "why": r["why"],
             "cycles_since_served": r["cycles_since_served"],
             "next_slot_at": _num_or_none(r["next_slot_at"]),
             "venue_events_in_horizon": r["venue_events_in_horizon"]}
            for r in last]
    out["read"] = True
    return out


def _receipt_note(rec: dict) -> str:
    """'; budget-dropped in 3 of 96 cycles in 24 h, served in 93, ...' from
    one competition's receipts summary, or '' when there is none."""
    if not rec:
        return ""
    bits = ["budget-dropped in %d of %d cycles in 24 h, served in %d"
            % (rec.get("budget_dropped") or 0, rec.get("cycles") or 0,
               rec.get("served") or 0)]
    if rec.get("cycles_since_served") is not None:
        bits.append("%s cycle(s) since served" % rec["cycles_since_served"])
    else:
        bits.append("not served in the receipts' window")
    if rec.get("next_slot_at") is not None:
        bits.append("next slot %s" % _dt.datetime.fromtimestamp(
            rec["next_slot_at"], _dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"))
    return "; " + ", ".join(bits)


async def collector_selection(conn, *, now: float) -> dict:
    """The collector's last cycle selection, for naming why a league has no
    provider events. Never raises.

    R30A: the durable coverage receipts are read beside the heartbeat. When
    their latest cycle is current, `requested` and `budget_dropped` come from
    THAT record (what the cycle actually requested and dropped, with the
    reason and the promised slot in `budget_dropped_why`); the heartbeat is
    the fallback on a database without migration 248."""
    out = {"fresh": False, "requested": [], "rejected": {},
           "budget_dropped": [], "budget": None, "at": None,
           "source": "HEARTBEAT"}
    try:
        raw = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1", COLLECTOR_KEY)
        v = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        sel = v.get("sports_selection") or {}
        at = float(v.get("at"))
        out.update(at=at, fresh=0 <= now - at <= COLLECTOR_FRESH_S,
                   requested=list(sel.get("requested") or []),
                   rejected={r.get("key"): r.get("refusal")
                             for r in sel.get("rejected") or []},
                   budget_dropped=[d.get("key")
                                   for d in sel.get("budget_dropped") or []],
                   budget=sel.get("metered_budget"))
    except Exception as exc:                                    # noqa: BLE001
        out["why"] = type(exc).__name__
    rec = await collector_receipts(conn, now=now)
    out["receipts"] = rec
    cyc = rec.get("cycles") or {}
    last_at = cyc.get("last_cycle_at")
    if rec.get("read") and last_at is not None and \
            0 <= now - last_at <= COLLECTOR_FRESH_S:
        last = rec.get("last_cycle") or []
        out.update(
            source="COVERAGE_RECEIPTS", fresh=True,
            at=max(last_at, out["at"] or 0.0),
            requested=[r["competition"] for r in last
                       if r["planned"] == "SCHEDULED"],
            budget_dropped=[r["competition"] for r in last
                            if r["receipt"] in RECEIPT_BUDGET_DROPPED],
            budget_dropped_why={
                r["competition"]: {"receipt": r["receipt"], "why": r["why"],
                                   "next_slot_at": r["next_slot_at"],
                                   "cycles_since_served":
                                       r["cycles_since_served"]}
                for r in last if r["receipt"] in RECEIPT_BUDGET_DROPPED},
            budget=cyc.get("calls_budget", out["budget"]))
    return out


#: The family a provider competition key names by its own prefix (the
#: provider's `<sport>_<league>` convention) or a native row by its suffix --
#: used only when no ledger row, venue row or lane map names one, so a
#: declared league with no record (basketball_nba on a quiet day) is still
#: scoped by its family rather than as an unmapped token.
_KEY_PREFIX_FAMILY = (("americanfootball_", "football"),
                      ("baseball_", "baseball"),
                      ("basketball_", "basketball"),
                      ("icehockey_", "hockey"), ("soccer_", "soccer"),
                      ("tennis_", "tennis"), ("pinnapi_", None))


def _family_of_key(key) -> str | None:
    k = str(key or "")
    for pre, fam in _KEY_PREFIX_FAMILY:
        if k.startswith(pre):
            return fam if fam is not None else (k[len(pre):] or None)
    return None


async def league_status_table(conn, *, rows: list, day: _dt.date, tz: str,
                              now: float) -> dict:
    """Status per league for one day: the funnel's own rows, plus every league
    the venue lists that day and every league the collector or the counting
    map declares -- so an unsupported or silent league appears, by name, and
    is never simply missing. Never raises on a source read."""
    start, end = day_window(day, tz)
    coll = await collector_selection(conn, now=now)
    tmap = catalogue_token_map()
    by_league: dict[str, dict] = {}
    for r in rows:
        by_league[r["league"]] = dict(r)
    venue = await _read(conn, ("us_premap",), VENUE_TOKENS_SQL, start, end)
    tokens_of: dict[str, list] = {}
    fam_of: dict[str, str] = {}
    if not isinstance(venue, str):
        for g in venue:
            tok = g["token"] or ""
            key = tmap.get(tok) or "venue:%s" % tok
            tokens_of.setdefault(key, []).append(tok)
            fam_of.setdefault(key, g["family"] or "")
            r = by_league.setdefault(key, {"league": key})
            if r.get("venue_catalogue_events") is None:
                r["venue_catalogue_events"] = int(g["events"])
    from ..workers import ext_pinnacle_loop as L
    declared = (set(CATALOGUE_TOKENS) | {k for k, _ in L.SPORTS_CONFIRMED}
                | set(L.VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY.values()))
    for key in declared:
        by_league.setdefault(key, {"league": key})
    ledger_read = await _regclass(conn, "ext_candidate_outcomes")
    live_decisions = any((r.get("decided_events") or 0) > 0
                         for r in by_league.values())
    out = []
    for key, r in sorted(by_league.items()):
        # a league with no funnel row was READ and had no record: a measured
        # zero -- unless the source itself is absent (then NULL)
        for c in [COLUMN[s] for s in STAGES] + list(EXTRA_COLUMNS):
            if c == "venue_catalogue_events":
                r.setdefault(c, None if isinstance(venue, str) else 0)
            else:
                r.setdefault(c, 0 if ledger_read else None)
        toks = tokens_of.get(key) or []
        fam = r.get("sport_family") or fam_of.get(key) or \
            L.family_for_provider_key(key) or _family_of_key(key)
        scope = lane_scope(key, token=(toks[0] if toks else
                                       key.split(":", 1)[-1]), family=fam)
        st = classify_status(r, scope=scope, collector=coll,
                             decisions_live=live_decisions)
        out.append({"league": key,
                    "league_name": (league_name(key) if not
                                    key.startswith("venue:") else
                                    "UNMAPPED:%s" % key.split(":", 1)[1]),
                    "sport_family": fam or None, "venue_tokens": toks,
                    "status": st["status"], "reason": st["reason"],
                    "stage": st["stage"],
                    "measurement_gaps": st["measurement_gaps"],
                    "counts": {c: r.get(c) for c in (
                        "provider_events", "normalized_events",
                        "venue_discovered", "mapped_events",
                        "settlement_supported", "evaluated_events",
                        "decided_events", "entered_events", "refused_events",
                        "venue_catalogue_events")},
                    # R30A: the collector's 24 h receipts for this league
                    # (requested / served / budget-dropped / cycles since
                    # served), None where the collector has no receipt for it
                    "collector_receipt": (
                        ((coll.get("receipts") or {}).get("by_competition")
                         or {}).get(key))})
    summary = {s: 0 for s in LEAGUE_STATUSES}
    for o in out:
        summary[o["status"]] += 1
    return {"day": day.isoformat(), "tz": tz, "statuses": out,
            "summary": summary, "status_vocabulary": list(LEAGUE_STATUSES),
            "collector": dict(
                {k: coll.get(k) for k in (
                    "fresh", "at", "requested", "budget_dropped", "budget",
                    "source", "budget_dropped_why")},
                # R30A: the durable receipts behind it -- the cycles' budget
                # rows (calls made against the declared budget, any cycle
                # over it) and per-competition 24 h counts
                receipts={k: (coll.get("receipts") or {}).get(k) for k in (
                    "read", "why", "window_s", "cycles", "by_competition",
                    "cycles_without_lease")}),
            "venue_read": None if not isinstance(venue, str) else venue}


# ═════════════════════════════════════════════════════════════════════
# ONE LEAGUE'S DAY, GAME BY GAME: EXPECTED vs OBSERVED (cand24)
# ═════════════════════════════════════════════════════════════════════

RECON_STAGES = ("EXPECTED", "PINNAPI", "NORMALIZED", "VENUE_CONTRACT",
                "EXACT_MAP", "SETTLEMENT", "PROBABILITY", "DEREK_EVALUATED",
                "VERDICT")
RECON_EXPECTED_SQL = """
    SELECT event_slug, market_slug, max(event_title) AS title,
           min(game_start) AS game_start,
           array_agg(intent ORDER BY intent) AS intents,
           array_agg(team_name ORDER BY intent) AS teams,
           array_agg(side_norm ORDER BY intent) AS nicks
      FROM us_premap
     WHERE lower(split_part(coalesce(event_slug, ''), '-', 1)) = $1
       AND sports_type = ANY($2::text[])
       AND game_start >= to_timestamp($3) AND game_start < to_timestamp($4)
     GROUP BY event_slug, market_slug
     ORDER BY min(game_start), event_slug
"""
RECON_LEDGER_SQL = """
    SELECT provider_event_id, max(home) AS home, max(away) AS away,
           max(commence_time) AS commence_time,
           max(us_market_slug) AS us_market_slug, max(%s) AS reach,
           (array_agg(first_refusal ORDER BY cycle_at DESC))[1] AS refusal,
           (array_agg(outcome ORDER BY cycle_at DESC))[1] AS outcome,
           max(cycle_at) AS last_cycle
      FROM ext_candidate_outcomes
     WHERE sport_key = ANY($1::text[]) AND provider_event_id IS NOT NULL
       AND cycle_at >= to_timestamp($2) AND cycle_at < to_timestamp($3)
     GROUP BY provider_event_id
""" % REACH_SQL
RECON_VALUATION_SQL = """
    SELECT DISTINCT ON (us_market_slug) us_market_slug, id, record_purpose,
           probability, refusals, decided_at
      FROM external_valuations
     WHERE us_market_slug = ANY($1::text[]) AND decided_at >= to_timestamp($2)
     ORDER BY us_market_slug, decided_at DESC, id DESC
"""
RECON_DECISION_SQL = """
    SELECT DISTINCT ON (us_market_slug, strategy) us_market_slug, strategy,
           verdict, refusal, decided_at
      FROM paper_decisions
     WHERE us_market_slug = ANY($1::text[]) AND decided_at >= to_timestamp($2)
     ORDER BY us_market_slug, strategy, decided_at DESC
"""
DEREK_STRATEGY = "DEREK_ENTRY_POLICY_V2"
_SETTLEMENT_CODE = ("SETTLEMENT", "_RULE_", "DRAW_HANDLING", "VOID_",
                    "OVERTIME_")
_PROBABILITY_CODE = ("MARKET_NOT_IN_SUPPORTED_SET", "NO_QUALIFIED_PINNACLE",
                     "PROBABILITY", "DEVIG", "OVERROUND")


def _fold(t) -> str:
    import re as _re
    return " ".join(_re.sub(r"[^a-z0-9]+", " ", str(t or "").lower()).split())


async def reconcile_league(conn, *, token: str, day: _dt.date, tz: str,
                           now: float, lookback_s: float = 2 * 86400.0
                           ) -> dict:
    """Every venue-listed game of `token` on the local `day`, stage by stage:
    EXPECTED -> PINNAPI (a provider event the collector saw) -> NORMALIZED ->
    VENUE_CONTRACT -> EXACT_MAP -> SETTLEMENT -> PROBABILITY ->
    DEREK_EVALUATED -> VERDICT, with the first stage that did not pass and its
    named reason, plus the MISSING list. Read-only; never raises on a read."""
    from ..workers import ext_pinnacle_loop as L
    from .. import bettor_venue_native_identity as V
    start, end = day_window(day, tz)
    key = L.provider_key_for_venue_token(token)
    fam = L.family_for_provider_key(key) if key else None
    types = list(V.FAMILY_WINNER_TYPES.get(fam or "", ()))
    out: dict[str, Any] = {"league_token": token, "provider_key": key,
                           "league_name": league_name(key) if key else None,
                           "day": day.isoformat(), "tz": tz,
                           "stages": list(RECON_STAGES)}
    if not key or not types:
        return dict(out, status="UNAVAILABLE",
                    why="the collector maps no provider competition to %r"
                    % token, games=[], missing=[])
    exp = await _read(conn, ("us_premap",), RECON_EXPECTED_SQL, token, types,
                      start, end)
    if isinstance(exp, str):
        return dict(out, status="UNAVAILABLE", why=exp, games=[], missing=[])
    # THE METERED KEY AND THE NATIVE ONE (verifier finding 1): a natively
    # discovered game is filed under the seed's key -- the lane's key for a
    # token it maps, `pinnapi_<family>` otherwise (and before the incident
    # release's fix). Each game below is still matched by its exact venue
    # slug, or by its start and both nicknames, so the wider read cannot
    # lend one league's row to another's game.
    from .. import pinnapi_discovery as PD
    led = await _read(conn, ("ext_candidate_outcomes",), RECON_LEDGER_SQL,
                      [key, PD.sport_key_for(fam)],
                      start - lookback_s, min(end, now + 1.0))
    slugs = [e["market_slug"] for e in exp]
    vals = await _read(conn, ("external_valuations",), RECON_VALUATION_SQL,
                       slugs, start - lookback_s)
    decs = await _read(conn, ("paper_decisions",), RECON_DECISION_SQL, slugs,
                       start - lookback_s)
    coll = await collector_selection(conn, now=now)
    led_rows = [] if isinstance(led, str) else led
    val_by = {} if isinstance(vals, str) else {v["us_market_slug"]: v
                                               for v in vals}
    dec_by: dict = {}
    for d in ([] if isinstance(decs, str) else decs):
        dec_by.setdefault(d["us_market_slug"], {})[d["strategy"]] = d
    games, missing = [], []
    for e in exp:
        gs = e["game_start"].timestamp() if hasattr(e["game_start"],
                                                    "timestamp") else None
        nicks = [_fold(n) for n in (e["nicks"] or []) if n]
        hit = next((r for r in led_rows
                    if r["us_market_slug"] == e["market_slug"]), None)
        if hit is None and gs is not None:
            for r in led_rows:
                ct = V._epoch(r["commence_time"])
                names = _fold("%s %s" % (r["home"], r["away"]))
                if ct is not None and abs(ct - gs) <= V.START_TOLERANCE_S \
                        and nicks and all(n in names for n in nicks):
                    hit = r
                    break
        v = val_by.get(e["market_slug"])
        dd = dec_by.get(e["market_slug"]) or {}
        dk = dd.get(DEREK_STRATEGY)
        refusals = list((v or {}).get("refusals") or [])
        st: dict[str, Any] = {"EXPECTED": True,
                              "VENUE_CONTRACT": e["market_slug"]}
        st["PINNAPI"] = ({"provider_event_id": hit["provider_event_id"]}
                         if hit else None)
        st["NORMALIZED"] = bool(hit and (hit["reach"] or 0) >= 3) or \
            v is not None
        st["EXACT_MAP"] = bool(hit and hit["us_market_slug"]
                               == e["market_slug"]) or v is not None
        sett = [c for c in refusals if any(k in c for k in _SETTLEMENT_CODE)]
        prob = [c for c in refusals if any(k in c for k in _PROBABILITY_CODE)]
        st["SETTLEMENT"] = (None if v is None else
                            ("ESTABLISHED" if not sett else sett))
        st["PROBABILITY"] = (None if v is None else
                             (v["probability"] if v["probability"] is not None
                              else (prob or ["NO_PROBABILITY"])))
        st["DEREK_EVALUATED"] = dk is not None
        st["VERDICT"] = (None if dk is None else
                         ("ENTER" if dk["verdict"] == "ENTER"
                          else "REFUSE:%s" % (dk["refusal"] or "UNNAMED")))
        if dk is not None:
            stopped, why = ("VERDICT", st["VERDICT"])
        elif v is not None:
            stopped, why = ("DEREK_EVALUATED",
                            "valuation %s recorded (%s); no Derek decision"
                            % (v["id"], v["record_purpose"]))
        elif hit is not None:
            stopped = ("NORMALIZED" if not st["NORMALIZED"] else
                       "EXACT_MAP" if not st["EXACT_MAP"] else "SETTLEMENT")
            why = "%s:%s" % (hit["outcome"], hit["refusal"] or "UNNAMED")
        else:
            if not coll.get("fresh"):
                why = "COLLECTOR_HEARTBEAT_NOT_CURRENT"
            elif key in coll.get("budget_dropped") or []:
                why = "NOT_REQUESTED_METERED_BUDGET_SPENT"
            elif key in (coll.get("rejected") or {}):
                why = "PROVIDER_REFUSED:%s" % coll["rejected"][key]
            elif key in (coll.get("requested") or []):
                why = "REQUESTED_NO_PROVIDER_EVENT_FOR_THIS_GAME"
            else:
                why = "NOT_REQUESTED_BY_THE_COLLECTOR"
            stopped = "PINNAPI"
        g = {"market_slug": e["market_slug"], "title": e["title"],
             "kickoff_utc": (_dt.datetime.fromtimestamp(gs, _dt.timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ") if gs else None),
             "kickoff_local": (_dt.datetime.fromtimestamp(gs, ZoneInfo(tz))
                               .strftime("%Y-%m-%d %H:%M") if gs else None),
             "sides": [{"intent": i, "team": t, "nickname": n}
                       for i, t, n in zip(e["intents"] or [], e["teams"] or [],
                                          e["nicks"] or [])],
             "stages": st, "stopped_at": stopped, "reason": why}
        games.append(g)
        if stopped == "PINNAPI":
            missing.append({"market_slug": e["market_slug"],
                            "title": e["title"], "reason": why})
    reached = {s: 0 for s in RECON_STAGES}
    for g in games:
        s = g["stages"]
        reached["EXPECTED"] += 1
        reached["VENUE_CONTRACT"] += 1
        reached["PINNAPI"] += bool(s["PINNAPI"])
        reached["NORMALIZED"] += bool(s["NORMALIZED"])
        reached["EXACT_MAP"] += bool(s["EXACT_MAP"])
        reached["SETTLEMENT"] += s["SETTLEMENT"] is not None
        reached["PROBABILITY"] += s["PROBABILITY"] is not None
        reached["DEREK_EVALUATED"] += bool(s["DEREK_EVALUATED"])
        reached["VERDICT"] += s["VERDICT"] is not None
    return dict(out, status="OK", expected=len(games), reached=reached,
                games=games, missing=missing,
                unread={k: v for k, v in (("ledger", led), ("valuations", vals),
                                          ("decisions", decs))
                        if isinstance(v, str)} or None)
