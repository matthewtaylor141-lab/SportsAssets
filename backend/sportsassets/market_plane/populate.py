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

SETTLEMENT (settlement rule registry). `coverage_pass` also writes each
active contract's settlement state (market_plane.settlement: PROVEN
COMPATIBLE / PROVEN DIFFERENT BUT PRICED / NOT PROVEN / RULE EVIDENCE
CONFLICT / EXTERNAL SETTLEMENT DATA UNAVAILABLE) from the latest decision
valuation, the latest paper decision's priced settlement difference and the
captured current rules text (market_plane_rules), and `settlement proven`
for the coverage matrix is exactly a PROVEN state. The pass records the
delta: contracts that moved from MAPPED_BUT_SETTLEMENT_NOT_PROVEN to a
PROVEN state (and back). Decision-time attest remains the trading authority.

KALSHI. `populate_kalshi` writes the GET-only sports catalogue's markets as
registry rows (venue KALSHI, contract_id 'kalshi:'+ticker, desired_subscription
false) and their rules into market_plane_rules. Each row's ontology is
kalshi_ontology.classify's (RC6): game winners, spreads, game totals and team
totals are mapped from the series' sports tag and the contract's own
rules_primary sentence; every other contract stays a gap named by the
classifier (nothing is guessed). A mapped Kalshi contract's settlement state
is then evidence-based like any other (market_plane.settlement).

MEMORY: ONE PAGE AT A TIME (RC5, 2026-10-08). sportsassets-market-plane was
OOM-killed at 2 GiB from 06:10:01Z (~14 kills by 12:50Z). The three passes
here each held the WHOLE universe at once, measured on the plane's real run
loop at production cardinality (tools/market_plane_memory.py, synthetic rows:
146,450 active contracts, 113,431 catalogue markets, 75,169 Kalshi markets):

  * coverage_pass kept every active row, every rules row with its parsed
    evidence, a classified result per contract (evidence row + settlement
    evidence) and the matrix's own copy of all of them -- VmHWM +1,531 MB at
    boot, +1,081 MB on the next pass;
  * populate(full) kept every grouped catalogue row, the registry's whole
    (contract_id -> content_sha) map and every upsert tuple -- +444 MB;
  * populate_kalshi took the walk's full market objects (~60 fields each)
    and built every registry tuple and every rules row before writing --
    +318 MB on top of the walk's own +232 MB.

Each now reads, classifies and writes ONE PAGE (COVERAGE_PAGE / POPULATE_PAGE
/ KALSHI_PAGE rows) and keeps only counters between pages. Outputs and
writes are unchanged, row for row (tests/test_market_plane_memory_bound.py
runs the RC4 implementations beside these on the same database): the same
rows in the same order, the same accumulators in the same insertion order,
the same rules texts loaded, one write transaction per pass as before.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

from . import freshness_window as _FW
from . import ontology as O
from ..open_position_canon import CANONICAL_OPEN_POSITIONS_SQL

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

#: OPEN means bought - sold - SETTLED (open_position_canon's own rule).
#: Before, settlements were ignored: every settled position stayed "held"
#: for good (production 2026-10-07: 123 held contracts for 30 open
#: positions, 101 of them without a book for 6 h -- games long over), and the
#: priority-universe rate counted them as capital-required markets.
HELD_SQL = """
    SELECT DISTINCT p.us_market_slug AS slug FROM (""" + \
    CANONICAL_OPEN_POSITIONS_SQL + """) p WHERE p.us_market_slug IS NOT NULL
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
                    settlement_comparison->>'status',
                    settlement_comparison->>'compatibility')
                                                       AS settlement_verdict,
           settlement_comparison->>'venue_rules_fingerprint'
                                                       AS decision_rules_fingerprint,
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

#: THE PAPER RUNTIME'S BOOK READ THAT COUNTS (RC6.2, lane p-freshness): the
#: NEWEST ERROR-FREE read inside the window, with the market state it
#: stated; `paper_book_counts` then refuses one whose own state says the
#: market is not open. Before, ANY row inside the window counted (max
#: observed_at over every row): an error row -- PAPER_DISCOVERY_READ_
#: DEFERRED_DURING_VENUE_HOLD is a read that was never made -- and a read of
#: an EXPIRED market were REST_RECOVERY (research-sql 37951157946 Q3b: 42
#: error-only and 10 MARKET_STATE_EXPIRED member-snapshot credits in 24 h).
#: The held-position rule (bettor_paper_freshness.classify) and the frozen
#: window (freshness_window.PAPER_SQL / classify) already read it this way.
REST_BOOK_SQL = """
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
           observed_at, market_state
      FROM paper_book_observations
     WHERE observed_at > now() - make_interval(secs => $1)
       AND us_market_slug = ANY($2::text[])
       AND error IS NULL
     ORDER BY us_market_slug, observed_at DESC
"""


def paper_book_counts(market_state) -> bool:
    """PURE. Whether an error-free paper book read inside the bound counts
    as a current book, exactly as the frozen window classes it
    (freshness_window.external_from: X, never P): a read whose own state is
    TERMINAL or not open (freshness_window.TERMINAL_STATES / TRANSIENT_
    STATES -- bettor_paper_freshness's sets, a test pins them, plus the
    institutional enum's names) is the venue saying the market is not open,
    never a current book; any other read is (no state word included, as the
    held rule's FRESH; production's error-free reads all state one:
    research-sql 37951157946 Q1, MARKET_STATE_OPEN / INSTRUMENT_STATE_OPEN /
    MARKET_STATE_EXPIRED)."""
    st = str(market_state or "").upper()
    return not (st and (st in _FW.TERMINAL_STATES
                        or st in _FW.TRANSIENT_STATES))

#: the latest paper decision's PRICED settlement difference (the recorded
#: bettor_settlement_difference_policy.eligibility verdict and the policy's
#: own price) -- evidence for SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED only
PRICED_SQL = """
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
           pinnacle->'settlement_difference_eligibility' AS eligibility,
           pinnacle->'settlement_difference_policy'      AS policy,
           decided_at
      FROM paper_decisions
     WHERE decided_at > now() - make_interval(secs => $1)
       AND us_market_slug = ANY($2::text[])
       AND pinnacle ? 'settlement_difference_policy'
     ORDER BY us_market_slug, decided_at DESC
"""

#: the captured rules rows (no text: the text is loaded only for a contract
#: whose terms comparison is not cached yet)
RULES_META_SQL = """
    SELECT contract_id, venue, rules_published, rules_field, rules_sha256,
           parse_status, evidence, parser_version, source
      FROM market_plane_rules WHERE contract_id = ANY($1::text[])
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


#: THE VENUE'S MARKET-KIND PREFIXES (copy_sports._KINDS, pinned equal by
#: tests/test_rc6_coverage_waterfall.py): a MARKET slug carries one
#: (`aec-nfl-ind-was-2026-10-04`, `asc-...`, `tsc-...`, `astatc-...`); the
#: venue's EVENT slug does not (`nfl-ind-was-2026-10-04`).
MARKET_KIND_PREFIXES = frozenset({"atc", "aec", "asc", "tsc", "astatc", "cpc"})


def league_of(event_slug, team_league=None) -> str | None:
    """The venue's league code, read off the slug by the venue's own grammar:
    a MARKET slug's segment after its kind prefix (`aec-mlb-nyy-bos-...` ->
    mlb), an EVENT slug's FIRST segment (`nfl-ind-was-2026-10-04` -> nfl,
    `bun-2027-05-22-relegation` -> bun, `btc-range-hr-...` -> btc: the rule
    venue_catalogue.league_of and copy_sports.league_of already state), else
    the catalogue's team league.

    RC6 (lane D2). This read the SECOND segment of every slug. us_premap's
    event_slug is the venue's EVENT slug, which carries no kind prefix
    (research-sql run 37871119335, W8: every one of the top 60 first segments
    of the 74,288 active PMUS event ids is a league code -- cfb 24,024, nfl
    15,810, nhl 5,101, ... -- and the second segment is a team code or a
    futures subject), so the registry's `competition` was a TEAM code for
    every listed game (pm-acceptance 37836393458 settlement breakdown keys
    POLYMARKET_US|football|buf|TOTAL, ...|hou|MARGIN, ...), a futures
    subject for every outright ('2027' 1,347, 'wins' 621, 'deespa' 524, ...:
    10,846 outrights left SPORT_NOT_NORMALIZED because LEAGUE_SPORT was asked
    about '2027'), and 'range' / 'above' for 1,343 BTC price markets that
    NON_SPORTS_LEAGUES names for exclusion by their 'btc' code."""
    parts = [p for p in str(event_slug or "").strip().lower().split("-") if p]
    if parts:
        if parts[0] in MARKET_KIND_PREFIXES:
            if len(parts) >= 2:
                return parts[1]
        else:
            return parts[0]
    return str(team_league).lower() if team_league else None


def _jsonish(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def venue_lists_active(r: dict, *, now: float) -> bool:
    """PURE. The catalogue market is listed in an active state and was seen
    within ACTIVE_HORIZON_S: what makes its registry row active (besides
    being held or a candidate)."""
    seen = _epoch(r.get("updated_at")) or now
    return (r.get("listing_state") in ACTIVE_LISTING_STATES
            and now - seen <= ACTIVE_HORIZON_S)


def contract_row(r: dict, *, now: float, held=frozenset(),
                 candidates=frozenset()) -> dict | None:
    """PURE. One catalogue market (grouped sides) -> its registry row, or
    None for a non-sports market (named in `excluded`).

    (RC6.2, p-coverage rework) NON-SPORTS IS DECIDED BY THE ROW, NOT THE
    CODE ALONE (ontology.excluded_as_non_sports): the code must be in
    NON_SPORTS_LEAGUES AND the venue's own market type must name no sport.
    And a market that is HELD or an evaluated CANDIDATE is never excluded
    -- it is required, so it keeps its own catalogue row (event, type,
    ontology) instead of the NOT_IN_CURRENT_CATALOGUE stub the required
    pass would otherwise write for a market the catalogue does list.
    Before, RC6.1 dropped every market of a listed code before reading its
    type: all of `gtasc` (Guatemalan soccer, typed soccer money lines and
    a held PAPER position included) left the registry and the coverage
    denominator."""
    slug = r["market_slug"]
    league = league_of(r.get("event_slug"), r.get("team_league"))
    if slug not in held and slug not in candidates and \
            O.excluded_as_non_sports(league, r.get("sports_type")):
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
    listed_active = venue_lists_active(r, now=now)
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


def _upsert_tuple(c: dict, at: float) -> tuple:
    """UPSERT_SQL's arguments for one registry row (contract_row /
    kalshi_contract_row)."""
    return (c["contract_id"], c["venue"], c["sport"], c["competition"],
            c["event_id"], c["market_type"],
            json.dumps(c["ontology"], default=str), c["active"],
            c["desired_subscription"], at, c["priority"],
            c["required_reason"], c["family"], c["period"], c["event_start"],
            c["last_seen_at"], c["content_sha"], AUTHORITY)


async def required_sets(conn) -> tuple:
    held, cands, _ = await required_sets_read(conn)
    return held, cands


async def required_sets_read(conn) -> tuple:
    """(held, candidates, both_read): `both_read` False when either read
    failed -- then nothing is DEMOTED on the strength of an empty set."""
    held, cands, ok = set(), set(), True
    try:
        held = {r["slug"] for r in await conn.fetch(HELD_SQL)}
    except Exception:                                           # noqa: BLE001
        held, ok = set(), False
    try:
        cands = {r["slug"] for r in await conn.fetch(
            CANDIDATE_SQL, float(CANDIDATE_WINDOW_S))}
    except Exception:                                           # noqa: BLE001
        cands, ok = set(), False
    return held, cands, ok


#: THE REQUIRED SETS ARE RE-APPLIED EVERY PASS, not only to the catalogue
#: rows that changed: a market that became held / a candidate is promoted
#: now, and one no longer held nor evaluated leaves the priority tier now
#: (its venue-activity priority, as contract_row would compute it) -- before,
#: both waited for a full pass, and a settled position never left.
PROMOTE_SQL = """
    UPDATE market_plane_registry r SET
           priority = CASE WHEN r.contract_id = ANY($1::text[]) THEN %(held)d
                           ELSE %(cand)d END,
           required_reason = CASE WHEN r.contract_id = ANY($1::text[])
                                  THEN 'OPEN_PAPER_POSITION'
                                  ELSE 'EVALUATED_CANDIDATE' END,
           active = true, updated_at = to_timestamp($3)
     WHERE r.contract_id = ANY($2::text[])
       AND (r.priority IS DISTINCT FROM (CASE WHEN r.contract_id =
                ANY($1::text[]) THEN %(held)d ELSE %(cand)d END)
            OR NOT r.active)
""" % {"held": P_HELD, "cand": P_CANDIDATE}
DEMOTE_SQL = """
    UPDATE market_plane_registry r SET
           priority = CASE
               WHEN r.event_start IS NOT NULL
                AND (r.event_start BETWEEN to_timestamp($2)
                                       AND to_timestamp($2 + %(soon)f)
                     OR r.event_start BETWEEN to_timestamp($2 - 21600)
                                          AND to_timestamp($2))
               THEN CASE WHEN r.family = ANY($3::text[])
                          AND r.period = 'FULL_EVENT' THEN %(core)d
                         ELSE %(other)d END
               ELSE %(rest)d END,
           required_reason = 'VENUE_ACTIVE', updated_at = to_timestamp($2)
     WHERE r.priority <= %(cand)d AND NOT (r.contract_id = ANY($1::text[]))
""" % {"soon": SOON_S, "core": P_CORE_SOON, "other": P_OTHER_SOON,
       "rest": P_REST, "cand": P_CANDIDATE}


def _count(tag) -> int:
    try:
        return int(str(tag).split()[-1])
    except (ValueError, IndexError):
        return 0


async def apply_required(conn, held: set, cands: set, *, at: float,
                         both_read: bool = True) -> dict:
    req = sorted(held | cands)
    out = {"promoted": _count(await conn.execute(
        PROMOTE_SQL, sorted(held), req, float(at))), "demoted": 0}
    if both_read:
        out["demoted"] = _count(await conn.execute(
            DEMOTE_SQL, req, float(at), sorted(CORE_METRICS)))
    else:
        out["demotion_skipped"] = "A_REQUIRED_SET_READ_FAILED"
    return out


#: catalogue markets read, upserted and evented per page (one executemany)
POPULATE_PAGE = 1000

HAVE_SQL = ("SELECT contract_id, content_sha FROM market_plane_registry "
            " WHERE contract_id = ANY($1::text[])")


async def populate(conn, *, since: float, now: float | None = None,
                   full: bool = False, excluded_detail: bool = False
                   ) -> dict:
    """Upsert every catalogue market changed since `since` (all of them when
    `full`), keep REQUIRED markets active, and (on a full pass) retire
    registry rows the catalogue no longer lists and nothing requires. Returns
    counts and the new watermark.

    ONE PAGE AT A TIME (RC5): the catalogue is read through a cursor inside
    the pass's one write transaction, POPULATE_PAGE grouped markets per
    page, each page's (contract_id -> content_sha) looked up for that page
    only and written before the next is read. Before, the whole catalogue,
    the whole registry's sha map and every upsert tuple were held at once
    (+444 MB VmHWM on a full pass at production cardinality). Every page's
    markets are distinct (the catalogue is GROUP BY market_slug), so a page
    never reads a row an earlier page wrote; the writes, the events and the
    counts are those of the single-batch pass.

    `excluded_detail` (RC6, lane D2) also counts, by venue code, the
    excluded markets the venue LISTS as active (`excluded_listed_active`:
    the rows that would be active registry rows were their code a sports
    league). Off, the output is the RC5 output exactly."""
    at = float(now if now is not None else time.time())
    held, cands, both_read = await required_sets_read(conn)
    required = held | cands
    out = {"read": 0, "upserted": 0, "changed": 0, "excluded": {},
           "required_added": 0, "retired": 0, "full": bool(full)}
    if excluded_detail:
        out["excluded_listed_active"] = {}
    seen_required = set()
    watermark = float(since)
    async with conn.transaction():
        cur = await conn.cursor(CATALOGUE_SQL, 0.0 if full else float(since))
        while True:
            page = await cur.fetch(POPULATE_PAGE)
            if not page:
                break
            out["read"] += len(page)
            have = {r["contract_id"]: r["content_sha"] for r in
                    await conn.fetch(HAVE_SQL, [r["market_slug"]
                                                for r in page])}
            batch, events = [], []
            for rec in page:
                r = dict(rec)
                watermark = max(watermark,
                                _epoch(r.get("updated_at")) or watermark)
                c = contract_row(r, now=at, held=held, candidates=cands)
                if c is None:
                    lg = league_of(r.get("event_slug"), r.get("team_league"))
                    out["excluded"][lg] = out["excluded"].get(lg, 0) + 1
                    if excluded_detail and venue_lists_active(r, now=at):
                        xa = out["excluded_listed_active"]
                        xa[lg] = xa.get(lg, 0) + 1
                    continue
                if c["contract_id"] in required:
                    seen_required.add(c["contract_id"])
                changed = have.get(c["contract_id"]) != c["content_sha"]
                out["changed"] += int(changed)
                batch.append(_upsert_tuple(c, at))
                if changed:
                    events.append(("upsert:%s:%s" % (c["contract_id"],
                                                     c["content_sha"][:16]),
                                   c["contract_id"], "CONTRACT_UPSERT",
                                   json.dumps({"priority": c["priority"],
                                               "reason": c["required_reason"],
                                               "family": c["family"],
                                               "sport": c["sport"]}),
                                   AUTHORITY))
            if batch:
                await conn.executemany(UPSERT_SQL, batch)
            if events:
                await conn.executemany(EVENT_SQL, events)
            out["upserted"] += len(batch)
            del page, have, batch, events
        # REQUIRED but not (re)listed: kept active with the reason, never
        # dropped
        missing_required = sorted(required - seen_required)
        have = {r["contract_id"] for r in await conn.fetch(
            HAVE_SQL, missing_required)} if missing_required else set()
        batch = []
        for slug in missing_required:
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
        for i in range(0, len(batch), 1000):
            await conn.executemany(UPSERT_SQL, batch[i:i + 1000])
        out["upserted"] += len(batch)
        out["required_applied"] = await apply_required(
            conn, held, cands, at=at, both_read=both_read)
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
    out["watermark"] = watermark
    out["required"] = {"held": len(held), "candidates": len(cands)}
    return out


#: what a FULL pass's record says the exclusion rule is
EXCLUDED_RULE = ("ontology.excluded_as_non_sports: a venue league code of "
                 "the event slug (populate.league_of) in "
                 "ontology.NON_SPORTS_LEAGUES AND a market type naming no "
                 "sport; never a held or candidate market")


def full_pass_record(out: dict, *, at: float) -> dict:
    """PURE. THE MARKETS A FULL POPULATE PASS KEPT OUT OF THE REGISTRY, by
    name (RC6, lane D2, review finding 2).

    A market whose venue code NON_SPORTS_LEAGUES names never becomes a
    registry row, so it is in no coverage denominator (coverage.active, the
    waterfall). Only a FULL pass reads the whole catalogue; an incremental
    pass (every POPULATE_EVERY_S, since the watermark) counts only the rows
    it read, and it replaces state["populate"]. The worker keeps this
    record of the last full pass under its own key (state / snapshot
    `populate_full`), which no incremental pass overwrites:

      excluded               every catalogue market kept out, by code
      excluded_listed_active those the venue lists as active (the rows the
                             registry's active count would hold were the
                             code a sports league), by code
      at                     the full pass's time (its own clock; no
                             source-event time is restated)"""
    x = {str(k): int(v) for k, v in (out.get("excluded") or {}).items()}
    xa = {str(k): int(v) for k, v in
          (out.get("excluded_listed_active") or {}).items()}
    return {"at": float(at), "full": bool(out.get("full")),
            "read": out.get("read"), "upserted": out.get("upserted"),
            "retired": out.get("retired"),
            "rule": EXCLUDED_RULE,
            "excluded": dict(sorted(x.items())),
            "excluded_total": sum(x.values()),
            "excluded_listed_active": (dict(sorted(xa.items()))
                                       if "excluded_listed_active" in out
                                       else None),
            "excluded_listed_active_total": (sum(xa.values())
                                             if "excluded_listed_active"
                                             in out else None)}


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
             fresh_book=False, book_source=None, external_codes=(),
             settlement=None) -> dict:
    """PURE. The coverage evidence for one registry contract -> the input of
    market_plane.coverage.terminal (and its own reasons).

    SETTLEMENT PROVEN = the contract's market_plane.settlement state is
    SETTLEMENT_PROVEN_COMPATIBLE or SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED,
    from evidence only (`settlement` precomputed by coverage_pass, else
    computed here from the valuation alone). The former rule also called a
    contract proven when its valuation merely carried a probability and no
    SETTLEMENT* refusal -- silence read as agreement; that is gone."""
    from .coverage import terminal
    from . import settlement as S
    ont = _jsonish(contract.get("ontology")) or {}
    gaps = list(ont.get("gaps") or [])
    mapped = not gaps and bool(contract.get("event_id")) and bool(
        contract.get("sport"))
    v = valuation or {}
    has_p = bool(v.get("has_probability"))
    st = settlement or S.state_for(contract, valuation=valuation)
    settlement_proven = st["state"] in S.PROVEN_STATES
    ext = None
    fr = str((candidate or {}).get("first_refusal") or "")
    if fr and fr in set(external_codes) and not has_p:
        ext = "EXTERNAL_EVIDENCE:%s" % fr
    elif mapped and st["state"] == S.EXTERNAL:
        ext = "EXTERNAL_SETTLEMENT:%s" % st["why"]
    row = {"contract_id": contract.get("contract_id"),
           "sport": contract.get("sport"),
           "competition": contract.get("competition"),
           "family": contract.get("family"), "period": contract.get("period"),
           "mapped": mapped,
           "mapping_why": ("ONTOLOGY_GAPS:%s" % ",".join(gaps)) if gaps else (
               None if mapped else "EVENT_OR_SPORT_IDENTITY_MISSING"),
           "settlement_proven": settlement_proven,
           "settlement_state": st["state"],
           "settlement_basis": st["basis"],
           "settlement_why": None if settlement_proven else (
               st["why"] if st["state"] == S.NOT_PROVEN
               else "%s:%s" % (st["state"], st["why"])),
           "fair_value_source": "PINNACLE_VALUATION" if has_p else None,
           "fair_value_why": None if has_p else (
               "NO_DECISION_VALUATION_IN_WINDOW" if not v
               else "VALUATION_WITHOUT_PROBABILITY"),
           "fresh_book": bool(fresh_book),
           "book_why": None if fresh_book else "NO_CURRENT_CANONICAL_BOOK",
           "external_unavailable": ext}
    t = terminal(row)
    t.update(book_source=book_source, evidence=row, settlement=st)
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


def _in_transaction(conn) -> bool:
    try:
        return bool(conn.is_in_transaction())
    except Exception:                                           # noqa: BLE001
        return False


async def _fetch_chunked(conn, sql, ids, *args, key="slug"):
    """{key: row} for `ids`, 5,000 per read; a read that fails is dropped
    (that chunk's evidence is absent, never invented). Inside an open
    transaction each read is its own savepoint, so a failed read cannot
    abort the pass's write transaction (coverage_pass flushes each page's
    changes into one transaction while later pages are still being read)."""
    out = {}
    for i in range(0, len(ids), 5000):
        chunk = ids[i:i + 5000]
        try:
            if _in_transaction(conn):
                async with conn.transaction():
                    got = await conn.fetch(sql, *args, chunk)
            else:
                got = await conn.fetch(sql, *args, chunk)
            for r in got:
                out[r[key]] = dict(r)
        except Exception:                                       # noqa: BLE001
            pass
    return out


#: the bounded venue x sport x league x family settlement breakdown
BREAKDOWN_MAX_KEYS = 200
#: active contracts classified (and their changes written) per page
COVERAGE_PAGE = 5000
#: the coverage pass yields to the event loop every this many contracts
YIELD_EVERY_ROWS = 250

COVERAGE_KEYS_SQL = ("SELECT contract_id FROM market_plane_registry "
                     " WHERE active ORDER BY priority, contract_id")
COVERAGE_ROWS_SQL = """
    SELECT contract_id, venue, sport, competition, event_id, family,
           period, ontology, coverage_state, coverage_why, priority,
           settlement_state, settlement_why, settlement_basis,
           settlement_evidence->>'rules_sha256' AS settlement_rules_sha
      FROM market_plane_registry WHERE contract_id = ANY($1::text[])
"""
#: the rule text a terms comparison reads (Polymarket US: its one captured
#: field; Kalshi: rules_primary) and, for a mapped Kalshi contract only, its
#: rules_secondary -- where its postponement / cancellation terms are --
#: appended (RC6): the comparison reads the contract's whole rule block
RULES_TEXT_SQL = ("SELECT contract_id, rules_text FROM market_plane_rules "
                  " WHERE contract_id = ANY($1::text[])")
RULES_SECONDARY_SQL = ("SELECT contract_id, rules_secondary "
                       "  FROM market_plane_rules "
                       " WHERE contract_id = ANY($1::text[])")
#: (RC6, lane D2) the same page with the venue market type and the event
#: start, read only when the pass is asked for the line-family terms or the
#: waterfall (coverage_pass `derivative_terms` / `waterfall`)
COVERAGE_ROWS_SQL_RC6 = COVERAGE_ROWS_SQL.replace(
    "family,\n           period,",
    "family,\n           period, market_type, event_start,", 1)
assert COVERAGE_ROWS_SQL_RC6 != COVERAGE_ROWS_SQL
#: (RC6.2, p-coverage) the venue's own fixture row (migration 183) for a
#: soccer / baseball money line's event: the competition phase and game
#: format its captured book terms are scoped by. Read only with
#: `derivative_terms`; a failed read is an absent row (no scope), never an
#: invented one.
FIXTURE_SCOPE_SQL = """
    SELECT venue_fixture_key, phase, game_format, competition, source,
           retrieved_at
      FROM venue_fixture_metadata
     WHERE venue = 'PMUS' AND venue_fixture_key = ANY($1::text[])
"""
COVERAGE_WRITE_SQL = (
    "UPDATE market_plane_registry SET coverage_state = $2, "
    "       coverage_why = $3, coverage_at = to_timestamp($4) "
    " WHERE contract_id = $1")
SETTLEMENT_WRITE_SQL = (
    "UPDATE market_plane_registry SET settlement_state = $2, "
    "       settlement_why = $3, settlement_basis = $4, "
    "       settlement_evidence = $5::jsonb, "
    "       settlement_at = to_timestamp($6) "
    " WHERE contract_id = $1")


async def coverage_pass(conn, *, fresh_symbols=frozenset(), now=None,
                        rest_sla_s: float = 300.0, limit: int | None = None,
                        refreshed: dict | None = None,
                        derivative_terms: bool = False,
                        waterfall: bool = False,
                        outside_registry: dict | None = None) -> dict:
    """Classify every ACTIVE registry contract and write the changed terminal
    states AND settlement states (market_plane.settlement, evidence only).
    Returns the matrix summary (counts by state, sport, family, why), the
    settlement-state counts and breakdown, and this pass's delta: how many
    contracts moved from MAPPED_BUT_SETTLEMENT_NOT_PROVEN to a PROVEN
    settlement state (and back).

    ONE PAGE AT A TIME (RC5): the active contract ids are read once, in the
    pass's order (priority, contract_id); then COVERAGE_PAGE contracts are
    read with their evidence, classified, counted and their changes written,
    and dropped before the next page. Before, every row, every rules row and
    a full classified result per contract were held to the end of the pass
    (+1.0-1.5 GB VmHWM at production cardinality, the OOM driver). The
    counters are updated in the same row order as before, so every count,
    every insertion-ordered top list and every write is the single-pass
    result. The rules text is loaded for exactly the contracts the
    single-pass rule loaded it for: a terms comparison not cached when the
    pass began (a key this pass loaded is not treated as cached, though a
    later page finds it in the cache). The changes go into ONE transaction,
    opened at the first page that has any and committed at the end (rolled
    back if the pass raises), as before.

    THE PLANE'S OWN REST REFRESH (RC6, market_plane.active_refresh):
    `refreshed` {contract_id: receipt instant} are the members the plane
    re-read through the venue's REST book because the stream had gone quiet
    on them. Each is REST_RECOVERY exactly like a paper REST observation --
    a REST book received within `rest_sla_s` -- after the stream and after
    the paper observation (an existing source keeps the credit, so the
    refresh counts only what it adds), and the output then carries
    `rest_recovery_by_origin` per tier: PAPER_BOOK_OBSERVATION vs
    PLANE_ACTIVE_REFRESH, so the source of every REST_RECOVERY is visible.
    Absent (None), nothing is counted from it and the output is the RC5
    output exactly.

    THE LINE TERMS AND THE WATERFALL (RC6, lane D2). `derivative_terms`
    reads each page with its venue market type and passes it to
    market_plane.settlement.state_for (a never-valued full-game or period
    line contract is read against its family's captured terms, its text
    loaded once per (fingerprint, family, line, period) as the money line's
    is; a captured family withheld for want of fixture scope says so).
    `waterfall` adds
    `waterfall` (market_plane.waterfall): every active contract's target
    tier and the stage it stopped at, counters only; with it,
    `outside_registry` (the plane's last full_pass_record) is carried into
    the waterfall as the markets kept out of the registry by name, beside
    the sums and never in them. Both False: the RC5 / RC6-refresh output
    exactly.

    THE EVENT'S FIXTURE SCOPE (RC6.2, p-coverage; with `derivative_terms`
    only): for a Polymarket US soccer / baseball money line the page also
    reads the venue's own fixture row (FIXTURE_SCOPE_SQL, ('PMUS',
    'event:<event id>')) and market_plane.settlement reads the contract's
    text under that phase and format in both quote contexts. No row, no
    scope: the RC6 reading exactly."""
    from .models import TERMINAL_STATES
    from .coverage import VERSION as MATRIX_VERSION
    from . import settlement as S
    from . import waterfall as WF
    at = float(now if now is not None else time.time())
    rows_sql = (COVERAGE_ROWS_SQL_RC6 if (derivative_terms or waterfall)
                else COVERAGE_ROWS_SQL)
    wf = WF.Waterfall(now=at) if waterfall else None
    loaded_line = set()
    keys = [r["contract_id"] for r in await conn.fetch(
        COVERAGE_KEYS_SQL + (" LIMIT %d" % int(limit) if limit else ""))]
    rules_ok = True
    try:
        await conn.fetchval("SELECT 1 FROM market_plane_rules LIMIT 1")
    except Exception:                                           # noqa: BLE001
        rules_ok = False
    ext = external_codes()
    n_rows = n_changed = n_schanged = n_texts = 0
    loaded_keys = set()
    by_state = {s: 0 for s in TERMINAL_STATES}
    delta = {"to_proven": 0, "from_proven": 0, "to_conflict": 0,
             "to_external": 0}
    src_counts = {"PMX_GRPC": 0, "RETAIL_PUSH": 0, "REST_RECOVERY": 0,
                  "NONE": 0}
    # THE TWO DENOMINATORS (owner, 2026-10-06): the PRIORITY universe (open
    # positions + evaluated candidates: the capital-required markets) and
    # the ENTIRE active universe are reported apart, never blended
    tiers = {"PRIORITY": {"PMX_GRPC": 0, "REST_RECOVERY": 0, "NONE": 0,
                          "EXTERNAL_DATA_UNAVAILABLE": 0, "total": 0},
             "ALL": {"PMX_GRPC": 0, "REST_RECOVERY": 0, "NONE": 0,
                     "EXTERNAL_DATA_UNAVAILABLE": 0, "total": 0}}
    origins = None if refreshed is None else {
        t: {"PAPER_BOOK_OBSERVATION": 0, "PLANE_ACTIVE_REFRESH": 0}
        for t in ("PRIORITY", "ALL")}
    # (RC6 D1) a refreshed value is its receipt instant (a REST book), or
    # (receipt, "SNAPSHOT") for the plane's snapshot-only gRPC read: a PMX
    # gRPC book, counted PMX_GRPC and labelled apart from the stream's
    snap_by = None if refreshed is None else {
        t: {"STREAM": 0, "PLANE_SNAPSHOT_REFRESH": 0}
        for t in ("PRIORITY", "ALL")}

    def _rcv(v):
        return v[0] if isinstance(v, (tuple, list)) else v
    refreshed_ok = {} if refreshed is None else {
        k: v for k, v in refreshed.items()
        if v is not None and _rcv(v) is not None
        and 0.0 <= at - float(_rcv(v)) <= float(rest_sla_s)}
    by_sport, by_why, by_venue = {}, {}, {}
    s_by_state = {k: 0 for k in S.STATES}
    s_by_basis, s_by_why, s_by_venue, brk = {}, {}, {}, {}
    tr = None
    try:
        for p0 in range(0, len(keys), COVERAGE_PAGE):
            ids = keys[p0:p0 + COVERAGE_PAGE]
            got = {r["contract_id"]: dict(r) for r in await conn.fetch(
                rows_sql, ids)}
            rows = [got[k] for k in ids if k in got]
            del got
            slugs = [r["contract_id"] for r in rows]
            vals = await _fetch_chunked(conn, VALUATION_SQL, slugs,
                                        float(VALUATION_WINDOW_S))
            cands = await _fetch_chunked(conn, CANDIDATE_REFUSAL_SQL, slugs,
                                         float(VALUATION_WINDOW_S))
            # (RC6.2) the newest error-free paper read, unless its own state
            # says the market is not open (REST_BOOK_SQL, paper_book_counts)
            rest = {k: _epoch(v["observed_at"]) for k, v in (
                await _fetch_chunked(conn, REST_BOOK_SQL, slugs,
                                     float(rest_sla_s))).items()
                    if paper_book_counts(v.get("market_state"))}
            priced = {k: {"eligibility": _jsonish(v.get("eligibility")) or {},
                          "policy": _jsonish(v.get("policy")) or {}}
                      for k, v in (await _fetch_chunked(
                          conn, PRICED_SQL, slugs,
                          float(VALUATION_WINDOW_S))).items()}
            rules = (await _fetch_chunked(conn, RULES_META_SQL, slugs,
                                          key="contract_id")
                     ) if rules_ok else {}
            for r in rules.values():
                r["evidence"] = _jsonish(r.get("evidence")) or {}
            # (RC6.2) the fixture scope of the page's scoped money lines
            scopes = {}
            if derivative_terms:
                fkeys = sorted({S.fixture_key(r) for r in rows
                                if r.get("venue") == VENUE
                                and S.h2h_family(r) in S.SCOPED_H2H_FAMILIES
                                and S.fixture_key(r)})
                scopes = (await _fetch_chunked(
                    conn, FIXTURE_SCOPE_SQL, fkeys,
                    key="venue_fixture_key")) if fkeys else {}
            # THE TEXT, ONLY WHERE A TERMS COMPARISON IS STILL TO BE MADE: a
            # never-attested full-event winner whose (fingerprint, family,
            # league) was not in this process's comparison cache when the
            # pass began
            need = []
            for r in rows:
                s = r["contract_id"]
                rr = rules.get(s)
                v = vals.get(s) or {}
                attested = bool(v.get("settlement_verdict")) or any(
                    str(x).startswith("SETTLEMENT")
                    for x in (v.get("refusals") or []))
                lf = S.line_family(r) if derivative_terms else None
                if lf is not None:
                    # a full-game / period line contract's text, once per
                    # (text, family, line, period) -- as the money line's
                    if rr is None or attested or \
                            not rr.get("rules_published") or \
                            rr.get("venue") != VENUE:
                        continue
                    lk = S.line_key(rr.get("rules_sha256"), lf, s)
                    if lk in S._LINE_CACHE and lk not in loaded_line:
                        rr["rules_text"] = ""
                    else:
                        need.append(s)
                        loaded_line.add(lk)
                    continue
                fam = S.h2h_family(r)
                # (RC6) a MAPPED Kalshi full-event winner is compared like a
                # Polymarket US one, on its own rule block (an unmapped
                # Kalshi row has no family, so fam is None)
                if rr is None or attested or not rr.get("rules_published") \
                        or rr.get("venue") not in (VENUE, KALSHI) \
                        or fam is None:
                    continue
                tk = S.terms_key(rr.get("rules_sha256"), fam,
                                 r.get("competition"),
                                 scopes.get(S.fixture_key(r)) if (
                                     fam in S.SCOPED_H2H_FAMILIES) else None)
                if tk in S._TERMS_CACHE and tk not in loaded_keys:
                    rr["rules_text"] = ""      # cached: the text is not re-read
                else:
                    need.append(s)
                    loaded_keys.add(tk)
            texts = await _fetch_chunked(
                conn, RULES_TEXT_SQL, need, key="contract_id") if need else {}
            kneed = [s for s in need
                     if (rules.get(s) or {}).get("venue") == KALSHI]
            seconds = await _fetch_chunked(
                conn, RULES_SECONDARY_SQL, kneed,
                key="contract_id") if kneed else {}
            for s, t in texts.items():
                txt = t.get("rules_text")
                sec = (seconds.get(s) or {}).get("rules_secondary")
                if txt is not None and sec:
                    txt = "%s\n\n%s" % (txt, sec)
                rules[s]["rules_text"] = txt
            n_texts += len(texts)
            changed, schanged = [], []
            for r in rows:
                if n_rows and n_rows % YIELD_EVERY_ROWS == 0:
                    # (RC6) a terms comparison is ~1 ms: the mapped Kalshi
                    # full-event winners (4,052 of the 2026-10-09 catalogue)
                    # add up to ~1 s of comparisons to a page, so the pass
                    # hands the event loop back between rows (no output
                    # changes: nothing here reads shared state)
                    await asyncio.sleep(0)
                s = r["contract_id"]
                origin = None
                if s in fresh_symbols:
                    src, fresh = "PMX_GRPC", True
                elif s in rest:
                    src, fresh = "REST_RECOVERY", True
                    origin = "PAPER_BOOK_OBSERVATION"
                elif s in refreshed_ok:
                    v = refreshed_ok[s]
                    if isinstance(v, (tuple, list)) and len(v) > 1 and \
                            v[1] == "SNAPSHOT":
                        src, fresh = "PMX_GRPC", True
                        origin = "PLANE_SNAPSHOT_REFRESH"
                    else:
                        src, fresh = "REST_RECOVERY", True
                        origin = "PLANE_ACTIVE_REFRESH"
                else:
                    src, fresh = None, False
                src_counts[src or "NONE"] += 1
                st = S.state_for(r, valuation=vals.get(s),
                                 rules=rules.get(s), priced=priced.get(s),
                                 rules_looked_up=rules_ok,
                                 derivative_terms=derivative_terms,
                                 fixture_scope=scopes.get(S.fixture_key(r)))
                t = classify(r, valuation=vals.get(s), candidate=cands.get(s),
                             fresh_book=fresh, book_source=src,
                             external_codes=ext, settlement=st)
                t["venue"] = r.get("venue")
                if wf is not None:
                    wf.add(r, t, valued=bool(
                        (vals.get(s) or {}).get("has_probability")))
                n_rows += 1
                by_state[t["state"]] += 1
                for tier in (("PRIORITY", "ALL") if (
                        r.get("priority") is not None
                        and int(r["priority"]) <= P_CANDIDATE) else ("ALL",)):
                    tiers[tier]["total"] += 1
                    if t["state"] == "EXTERNAL_DATA_UNAVAILABLE":
                        tiers[tier]["EXTERNAL_DATA_UNAVAILABLE"] += 1
                    tiers[tier][src or "NONE"] += 1
                    if origin == "PLANE_SNAPSHOT_REFRESH":
                        snap_by[tier][origin] += 1
                    elif origins is not None and origin is not None:
                        origins[tier][origin] += 1
                    if snap_by is not None and src == "PMX_GRPC" and \
                            origin is None:
                        snap_by[tier]["STREAM"] += 1
                if (t["state"], t["why"]) != (r.get("coverage_state"),
                                             r.get("coverage_why")):
                    changed.append((s, t["state"], t["why"], at))
                sha = st["evidence"].get("rules_sha256")
                if (st["state"], st["why"], st["basis"], sha) != (
                        r.get("settlement_state"), r.get("settlement_why"),
                        r.get("settlement_basis"),
                        r.get("settlement_rules_sha")):
                    schanged.append((s, st["state"], st["why"], st["basis"],
                                     json.dumps(st["evidence"], default=str),
                                     at))
                    prior_np = (r.get("settlement_state") == S.NOT_PROVEN or (
                        r.get("settlement_state") is None
                        and r.get("coverage_state")
                        == "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"))
                    if st["proven"] and prior_np:
                        delta["to_proven"] += 1
                    if r.get("settlement_state") in S.PROVEN_STATES and \
                            not st["proven"]:
                        delta["from_proven"] += 1
                    if st["state"] == S.CONFLICT and \
                            r.get("settlement_state") != S.CONFLICT:
                        delta["to_conflict"] += 1
                    if st["state"] == S.EXTERNAL and \
                            r.get("settlement_state") != S.EXTERNAL:
                        delta["to_external"] += 1
                k = t.get("sport") or "UNKNOWN"
                by_sport.setdefault(k, {}).setdefault(t["state"], 0)
                by_sport[k][t["state"]] += 1
                vn = t.get("venue") or "UNKNOWN"
                by_venue.setdefault(vn, {}).setdefault(t["state"], 0)
                by_venue[vn][t["state"]] += 1
                w = "%s:%s" % (t["state"], t["why"])
                by_why[w] = by_why.get(w, 0) + 1
                s_by_state[st["state"]] += 1
                s_by_basis[st["basis"]] = s_by_basis.get(st["basis"], 0) + 1
                sw = "%s:%s" % (st["state"], (st["why"] or "")[:120])
                s_by_why[sw] = s_by_why.get(sw, 0) + 1
                s_by_venue.setdefault(vn, {}).setdefault(st["state"], 0)
                s_by_venue[vn][st["state"]] += 1
                bk = "%s|%s|%s|%s" % (vn, k, t.get("competition") or
                                      "UNKNOWN", t.get("family") or "UNKNOWN")
                brk.setdefault(bk, {}).setdefault(st["state"], 0)
                brk[bk][st["state"]] += 1
            n_changed += len(changed)
            n_schanged += len(schanged)
            if changed or schanged:
                if tr is None:
                    tr = conn.transaction()
                    await tr.start()
                for i in range(0, len(changed), 1000):
                    await conn.executemany(COVERAGE_WRITE_SQL,
                                           changed[i:i + 1000])
                for i in range(0, len(schanged), 1000):
                    await conn.executemany(SETTLEMENT_WRITE_SQL,
                                           schanged[i:i + 1000])
            del rows, vals, cands, rest, priced, rules, texts, changed, \
                schanged, scopes
    except BaseException:
        if tr is not None:
            try:
                await tr.rollback()
            except Exception:                                   # noqa: BLE001
                pass
        raise
    if tr is not None:
        await tr.commit()
    m = {"total": n_rows, "by_state": by_state, "silent_omissions": 0,
         "version": MATRIX_VERSION}
    if origins is not None:
        m["rest_recovery_by_origin"] = origins
        m["pmx_grpc_by_origin"] = snap_by
    if wf is not None:
        m["waterfall"] = wf.result(outside_registry=outside_registry)
    top = sorted(brk.items(), key=lambda kv: -sum(kv[1].values()))
    return dict(m, by_sport=by_sport, by_venue=by_venue,
                top_reasons=dict(sorted(by_why.items(),
                                        key=lambda kv: -kv[1])[:40]),
                source_counts=src_counts, freshness_tiers=tiers,
                changed=n_changed,
                active=n_rows, computed_at=at,
                settlement={
                    "by_state": s_by_state, "by_basis": s_by_basis,
                    "by_venue": s_by_venue,
                    "top_reasons": dict(sorted(s_by_why.items(),
                                               key=lambda kv: -kv[1])[:40]),
                    "breakdown_venue_sport_league_family": dict(
                        top[:BREAKDOWN_MAX_KEYS]),
                    "breakdown_keys_total": len(brk),
                    "breakdown_truncated": len(brk) > BREAKDOWN_MAX_KEYS,
                    "changed": n_schanged, "delta": delta,
                    "rules_table_read": rules_ok,
                    "terms_text_loaded": n_texts,
                    "authority_note": S.AUTHORITY_NOTE})


# ── Kalshi (market_plane registry rows from kalshi_catalogue) ────────

KALSHI = "KALSHI"


def kalshi_contract_row(market: dict, *, now: float) -> dict | None:
    """PURE. One Kalshi catalogue market -> its registry row; never
    subscribed on the PMUS streams (desired_subscription false).

    THE KALSHI ONTOLOGY (RC6, kalshi_ontology). Until RC6 every Kalshi row
    was the one gap KALSHI_ONTOLOGY_NOT_MAPPED (no sport, family or period:
    "no guessed mapping"; 76,467 active rows at RC5). kalshi_ontology.
    classify now reads the series' own sports tag and the contract's own
    rules_primary sentence -- anchored venue templates, a subject that is a
    participant the sentence names (or the market's code in the event
    ticker), the stated line, period and overtime clause -- and maps game
    winners, spreads, game totals and team totals; the row then carries its
    sport, family and period, and its settlement terms bound by the rule
    block's own fingerprint (kalshi_ontology.bind_terms). Everything else
    stays a gap NAMED by the classifier (player props, team-stat props,
    other game props, outrights / season contracts, non-binary payouts,
    unrecognised sentences, a ticker not under its event), never guessed.
    It reads only fields the plane's walk keeps (KALSHI_PERSIST_KEYS +
    _series), so both walks (the plane's and kalshi_market_data's) write
    the same row."""
    from .. import kalshi_ontology as KONT
    t = (market or {}).get("ticker")
    if not t:
        return None
    ser = dict(market.get("_series") or {})
    v = KONT.classify(market)
    mapped = v["status"] == KONT.MAPPED
    subj = v.get("subject") or {}
    meaning = {"venue": KALSHI, "venue_contract_id": t,
               "sport": v["sport"], "competition": ser.get("ticker"),
               "event_id": market.get("event_ticker"),
               "subject_type": subj.get("type") if mapped else None,
               "subject_id": (subj.get("ticker_code") or
                              subj.get("as_stated")) if mapped else None,
               "period": v["period"] if mapped else None,
               "metric": v["family"] if mapped else None,
               "operator": v["operator"] if mapped else None,
               "line": (float(v["line"]) if mapped and v["line"] is not None
                        else None),
               "side": None, "settlement_schema": None,
               "raw_market_type": ser.get("ticker"),
               "ontology_version": KONT.VERSION}
    # (the row is rewritten on every walk: empty fields are not stored)
    meaning = {k: x for k, x in meaning.items() if x is not None}
    ontology = {"gaps": [] if mapped else [v["refusal"]],
                "meaning": meaning,
                "kalshi": KONT.stored(v),
                "terms": KONT.bind_terms(market, v) if mapped else None,
                "venue_ids": {"ticker": t,
                              "event_ticker": market.get("event_ticker"),
                              "series_ticker": ser.get("ticker")},
                "series": {"title": ser.get("title"),
                           "tags": list(ser.get("tags") or []),
                           "category": ser.get("category")},
                "status": market.get("status"),
                "market_type": market.get("market_type"),
                "close_time": market.get("close_time"),
                "display_only": {"title": market.get("title"),
                                 "yes_sub_title": market.get("yes_sub_title")},
                "basis": ("VENUE_IDS+SERIES_SPORT_TAG+RULES_PRIMARY_TEMPLATE"
                          if mapped else "VENUE_IDS"),
                "version": O.VERSION}
    sport = v["sport"]
    family = v["family"] if mapped else None
    period = v["period"] if mapped else None
    content = {"ontology": ontology, "event_id": market.get("event_ticker"),
               "competition": ser.get("ticker"), "sport": sport,
               "family": family, "period": period}
    return {"contract_id": "kalshi:%s" % t, "venue": KALSHI, "sport": sport,
            "competition": ser.get("ticker"),
            "event_id": market.get("event_ticker"),
            "market_type": market.get("market_type"), "ontology": ontology,
            "active": True, "desired_subscription": False,
            "priority": P_REST, "required_reason": "VENUE_ACTIVE",
            "family": family, "period": period, "event_start": None,
            "last_seen_at": now, "content_sha": _sha(content)}


#: THE FIELDS THE KALSHI PERSISTERS READ, and nothing else: the registry
#: row (kalshi_contract_row), the rules row (rules.kalshi_row ->
#: settlement_rule_registry.kalshi_rule_evidence / kalshi_rules_text) and the
#: walk summary (kalshi_catalogue.summary). The plane's walk keeps only these
#: per market (kalshi_slim): a venue market object carries ~60 fields, and
#: the walk held 75,169 of them whole until they were persisted (+232 MB in
#: the walk thread, RC5 harness). tests/test_market_plane_memory_bound.py
#: proves every other field is unread (changing it changes no output).
KALSHI_PERSIST_KEYS = ("ticker", "event_ticker", "status", "market_type",
                       "close_time", "title", "yes_sub_title",
                       "rules_primary", "rules_secondary",
                       "settlement_timer_seconds", "settlement_value_dollars",
                       "settlement_ts")
#: Kalshi markets persisted per page (registry tuples and rules rows)
KALSHI_PAGE = 1000


def kalshi_slim(market: dict, series: dict) -> dict:
    """PURE. kalshi_catalogue.walk's `project`: one venue market -> the
    persisted fields and its series (the walk's own `_series`)."""
    out = {k: market[k] for k in KALSHI_PERSIST_KEYS if k in market}
    out["_series"] = series
    return out


async def populate_kalshi(conn, result: dict, *, now: float | None = None
                          ) -> dict:
    """Upsert every market a kalshi_catalogue walk returned into the
    registry (CONTRACT_UPSERT only when content changed) and its rules into
    market_plane_rules. A TRUNCATED walk writes what it read and retires
    nothing (rows not re-seen age out by ACTIVE_HORIZON_S like any
    listing). No authority.

    ONE PAGE AT A TIME (RC5): registry tuples and rules rows are built and
    written KALSHI_PAGE markets at a time (before, all 75,169 of each were
    built first: +318 MB). The registry writes stay ONE transaction and are
    compared with the registry as it was before the pass (as before); the
    rules rows go through rules.upsert a page at a time, each page its own
    transaction, so its process-local fingerprint cache only ever records
    rows that were committed. The rows written are the single-batch rows;
    only when one walk lists the same ticker twice, in different pages, does
    the second count as `unchanged_cached` rather than `written`."""
    from . import rules as RULES
    at = float(now if now is not None else time.time())
    ms = [m for m in (result or {}).get("markets") or []
          if isinstance(m, dict) and m.get("ticker")]
    have = {r["contract_id"]: r["content_sha"] for r in await conn.fetch(
        "SELECT contract_id, content_sha FROM market_plane_registry "
        " WHERE venue = $1", KALSHI)}
    upserted = changed = 0
    async with conn.transaction():
        for i in range(0, len(ms), KALSHI_PAGE):
            batch, events = [], []
            for m in ms[i:i + KALSHI_PAGE]:
                c = kalshi_contract_row(m, now=at)
                if c is None:
                    continue
                ch = have.get(c["contract_id"]) != c["content_sha"]
                batch.append(_upsert_tuple(c, at))
                if ch:
                    events.append(("upsert:%s:%s" % (c["contract_id"],
                                                     c["content_sha"][:16]),
                                   c["contract_id"], "CONTRACT_UPSERT",
                                   json.dumps({"venue": KALSHI,
                                               "series": c["competition"]}),
                                   AUTHORITY))
            if batch:
                await conn.executemany(UPSERT_SQL, batch)
            if events:
                await conn.executemany(EVENT_SQL, events)
            upserted += len(batch)
            changed += len(events)
    del have
    rr = {"offered": 0, "unchanged_cached": 0, "written": 0, "changed": 0,
          "new": 0}
    for i in range(0, len(ms), KALSHI_PAGE):
        got = await RULES.upsert(
            conn, [RULES.kalshi_row(m) for m in ms[i:i + KALSHI_PAGE]],
            now=at)
        for k in rr:
            rr[k] += int(got.get(k) or 0)
    return {"markets": len(ms), "upserted": upserted,
            "changed": changed, "rules": rr,
            "complete": bool((result or {}).get("complete")),
            "stopped": (result or {}).get("stopped")}
