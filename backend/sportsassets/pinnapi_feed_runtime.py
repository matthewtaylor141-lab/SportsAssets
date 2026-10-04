"""THE PINNAPI FEED IN THE DECIDING PROCESS (C1: observe; one read-only
consumer, Xavier's held-position measure).

Started by ext_pinnacle_loop.run ONLY after it holds its writer lock
(7723901544120034), with that connection's backend pid, so the feed exists
only beside the process that decides; the owner re-checks that pid still
holds the writer lock every liveness pass and stops for good if it does not.

ARMING (fail-closed): the ingestion_state row 'pinnapi_feed' must read
exactly true, re-read every liveness pass -- absent, unreadable or anything
else disarms (authority revoked, socket closed). Disarmed, the owner takes no
lease and opens no socket; it only re-reads the row. Env PINNAPI_FEED in
{off,0,false,no} is a kill switch that keeps the module from starting at all.
(It is a kill switch, not an arm: changing a Render env var redeploys the
service, and arming must not need a deploy.)

SCOPE: ingestion_state 'pinnapi_feed_scope' = {"sport_ids": [...],
"streams": [...]}; absent -> baseball only ([6], live + prematch). The soccer
prematch firehose was 1,291 events in one snapshot (bounded capture
2026-10-01), so wider scopes are an explicit choice. R30A (P0 incident):
migration 260 makes that choice -- every sport present at BOTH Pinnacle and
the venue (SCOPE_SPORTS below) -- and `scope` no longer truncates to four.

ONE READ BY A DECISION: Xavier's measure of a HELD benchmark position
(`held_moneyline`, called from paper_xavier when the external valuation is
not fresh). It reads only this process's cache through `read` (no socket,
no network), matches the held contract with the census's own
`contract_match`, and de-vigs with bettor_pinnacle_devig.valuation. Every
refusal is named and leaves the measure stale; it never places or sizes an
order, and Derek reads nothing here. HELD PRIORITY TARGETS (pinnapi_held): the runtime installs the held watch on
this owner's cache change notification and refreshes, every
pinnapi_held.HELD_REFRESH_S, which provider events Xavier's OPEN paper and
actual positions are (`held_event_id`, the census identity). A held market's
change triggers Xavier's review; the reactive scheduler serves held events
before discovery. Same socket, same owner, same budget. Xavier's review
reads `held_moneyline` on demand within HELD_ON_DEMAND_BUDGET_S (1 s).
It also publishes a bounded heartbeat
('pinnapi_feed_last', overwritten, capped) with the owner state, the census
of the provider's current state by sport / market type / phase, and the
provider-stamp->receipt distribution.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import os
import time
import uuid
from typing import Optional

from . import pinnapi_feed as F
from . import pinnapi_owner as O
from . import pinnapi_probe as PP

log = logging.getLogger(__name__)

CONTROL_KEY = "pinnapi_feed"
SCOPE_KEY = "pinnapi_feed_scope"
HEARTBEAT_KEY = "pinnapi_feed_last"
HEARTBEAT_S = 30.0
HEARTBEAT_MAX_BYTES = 65536
ROW_READ_TIMEOUT_S = 5.0
HEARTBEAT_WRITE_TIMEOUT_S = 3.0
CENSUS_TIMEOUT_S = 10.0
DEFAULT_SCOPE = {"sport_ids": [6], "streams": ["live", "prematch"]}
#: PinnAPI's OWN sport ids, from its public documentation ("## Sport IDs --
#: Stable integer mapping. Use these in `sport_id` query params throughout
#: the API": 1 Soccer ... 12 Golf), read from https://pinnapi.com/llms-full.txt
#: by the PinnAPI probe run 37232918224 (2026-10-04T20:40Z, 56,250 bytes, sha256
#: 162705de...d394d -- the same bytes fetch-docs run 37226335697 read at
#: 18:56Z). Excerpt: tests/fixtures/pinnapi_ws_subscription_docs_2026_10_04.json.
#: "`sport_ids` use the same integer IDs as the rest of the API".
PINNAPI_SPORT_IDS = {1: "Soccer", 2: "Tennis", 3: "Basketball", 4: "Hockey",
                     5: "Football", 6: "Baseball", 7: "Rugby", 8: "MMA",
                     9: "Boxing", 10: "Other", 11: "Esports", 12: "Golf"}
ALLOWED_SPORTS = set(PINNAPI_SPORT_IDS)

# ── THE R30A SCOPE: EVERY SPORT AT BOTH PINNACLE AND THE VENUE ──────────
#
# THE DEFECT (P0 incident root cause RC1, measured 2026-10-04). Production
# subscribed sport ids [1, 6] only (migration 193), and `scope` kept at most
# FOUR ids (`[:4]`, a copy of the probe's sampling bound WS_MAX_SPORTS, not a
# provider limit). Football, hockey, basketball and tennis therefore had no
# Pinnacle WebSocket price at all: 616 venue events/24 h (77% of the
# real-sport winner universe) and 29,446 contracts were priced, if at all,
# only through the metered fallback.
#
# THE PROVIDER'S DOCUMENTED LIMITS (same capture): "One WebSocket connection
# per account"; a frame is delivered when "its sport_id matches a sport-level
# subscription"; the only subscription cap is on EVENT ids ("Event-level
# subscriptions are capped at 200 ids per stream per connection") -- there is
# no documented cap on sport-level subscriptions. We subscribe by sport, on
# the one socket the owner already holds, so the scope below is inside every
# documented limit.
#
# THE SIX. The owner named them (football, basketball, baseball, hockey,
# soccer, tennis) and both sides list each: the venue's current listing
# carries nfl/cfb, nba/wnba, mlb, nhl, soccer and atp/wta contracts
# (research-sql run 37235155757, section L1) and the PinnAPI REST probe (run
# 37232918224) listed football 64 prematch, tennis 165, basketball 62 and
# hockey 106 events beside the soccer and baseball already held.
#
# BOUNDED, NOT TRUNCATED. The scope admits exactly the sports whose cache
# load was MEASURED against the existing bounds (pinnapi_feed: "CAPACITY FOR
# THE R30A SCOPE (sports 1-6)": 45,136 markets worst case against
# MAX_MARKETS 120,000, ~1,750 records against MAX_EVENTS 4,000). Any other
# id in the control row -- a documented one whose load was never measured,
# or one the documentation does not list -- is refused BY NAME in the
# scope's own receipt (`refused`), never silently dropped, and never
# subscribed. Nothing here is a count cap.
SCOPE_SPORTS = (1, 2, 3, 4, 5, 6)
R_SCOPE_UNDOCUMENTED = "SPORT_ID_NOT_IN_PINNAPI_DOCUMENTATION"
R_SCOPE_UNMEASURED = "SPORT_CAPACITY_NOT_MEASURED_AGAINST_THE_CACHE_BOUNDS"
R_SCOPE_NOT_AN_ID = "SPORT_ID_NOT_AN_INTEGER"

_STATE: dict = {"owner": None, "task": None, "beat": None, "pool": None,
                "census": None, "runtime_id": None, "held": None,
                "scope": None, "discovery": None}
CENSUS_S = 60.0
#: PinnAPI-native discovery (pinnapi_discovery) rides the census cadence:
#: one bounded catalogue read per CENSUS_S, under its own timeout
DISCOVERY_TIMEOUT_S = 10.0
#: held_moneyline is the only decision read; no order path reads the feed
DECISION_EFFECT = "XAVIER_HELD_MEASURE_ONLY (read-only, held positions)"


def enabled() -> bool:
    """False only for an explicit kill switch; arming is the control row."""
    return (os.environ.get("PINNAPI_FEED") or "").strip().lower() not in (
        "off", "0", "false", "no")


async def _read_row(pool, key):
    # Scope is read during start_default, before the collector can continue.
    # Bound BOTH shared-pool acquisition and the query, including while the
    # control row is absent/off. Dedicated lease settings do not cover this.
    async with asyncio.timeout(ROW_READ_TIMEOUT_S):
        async with pool.acquire() as c:
            return await c.fetchval(
                "SELECT value FROM ingestion_state WHERE key = $1", key)


def _jsonish(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                       # noqa: BLE001
            return v
    return v


async def armed(pool) -> bool:
    """True only for an explicit true; anything else (absent, unreadable,
    malformed) is disarmed."""
    try:
        return _jsonish(await _read_row(pool, CONTROL_KEY)) is True
    except Exception:                                           # noqa: BLE001
        return False


def scope_of(value) -> dict:
    """The subscription a control-row value asks for, every refusal named.

    {"sport_ids": [...], "streams": [...]} plus, when anything was refused,
    "refused": {repr(id): reason}. Pure; never raises. No count cap: an id
    is admitted exactly when it is a documented PinnAPI id whose load on the
    cache was measured (SCOPE_SPORTS)."""
    v = _jsonish(value)
    if not isinstance(v, dict):
        return dict(DEFAULT_SCOPE)
    sports, refused = [], {}
    for x in (v.get("sport_ids") or []):
        if isinstance(x, bool) or not str(x).strip().isdigit():
            refused[str(x)[:16]] = R_SCOPE_NOT_AN_ID
            continue
        sid = int(str(x).strip())
        if sid not in ALLOWED_SPORTS:
            refused[str(sid)] = R_SCOPE_UNDOCUMENTED
        elif sid not in SCOPE_SPORTS:
            refused[str(sid)] = R_SCOPE_UNMEASURED
        elif sid not in sports:
            sports.append(sid)
    streams = [x for x in (v.get("streams") or []) if x in ("live",
                                                            "prematch")]
    out = {"sport_ids": sorted(sports) or DEFAULT_SCOPE["sport_ids"],
           "streams": streams or DEFAULT_SCOPE["streams"]}
    if refused:
        out["refused"] = refused
    return out


async def scope(pool) -> dict:
    try:
        v = await _read_row(pool, SCOPE_KEY)
    except Exception:                                           # noqa: BLE001
        v = None
    return scope_of(v)


def digest() -> dict:
    o = _STATE.get("owner")
    if o is None:
        return {"state": "NOT_STARTED", "enabled_env": enabled()}
    d = o.status()
    d["runtime_id"] = _STATE.get("runtime_id")
    d["enabled_env"] = enabled()
    d["coverage_census"] = _STATE.get("census")
    d["c1_decision_effect"] = DECISION_EFFECT
    d["scope"] = _STATE.get("scope")
    from . import pinnapi_discovery as PD
    d["native_discovery"] = (PD.digest(_STATE["discovery"])
                             if _STATE.get("discovery") else None)
    from . import pinnapi_held as PH
    d["held_priority_targets"] = PH.WATCH.status()
    return d


def heartbeat_view(value, *, now=None) -> dict:
    """Persisted telemetry is not a lease or a grant of price authority."""
    v = _jsonish(value)
    if not isinstance(v, dict):
        return {"status": "UNAVAILABLE", "authority_proven": False}
    now = time.time() if now is None else now
    try:
        age = now - float(v["beat_at"])
        if not math.isfinite(age):
            raise ValueError("nonfinite heartbeat time")
        current = 0 <= age <= 3 * HEARTBEAT_S
    except (TypeError, ValueError, KeyError):
        age, current = None, False
    return {"status": "RECENT_TELEMETRY" if current else "STALE_TELEMETRY",
            "age_s": age, "recorded_state": v.get("state"),
            "authority_proven": False,
            "note": "Only the live owner/lease accessor may authorize a price."}


async def _write_heartbeat(pool, payload, *, final=False):
    """A retiring instance cannot overwrite a replacement's heartbeat."""
    async with asyncio.timeout(HEARTBEAT_WRITE_TIMEOUT_S):
        async with pool.acquire() as c:
            if final:
                return await c.execute(
                    "UPDATE ingestion_state SET value=$2::jsonb WHERE key=$1 "
                    "AND value->>'runtime_id'=$3", HEARTBEAT_KEY,
                    _capped(payload), payload.get("runtime_id"))
            return await c.execute(
                "INSERT INTO ingestion_state (key,value) VALUES ($1,$2::jsonb) "
                "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                HEARTBEAT_KEY, _capped(payload))


def _capped(d: dict) -> str:
    s = json.dumps(d, default=str)
    if len(s) <= HEARTBEAT_MAX_BYTES:
        return s
    d = dict(d)
    cache = dict(d.get("cache") or {})
    cache["markets_by_sport_type_phase"] = "TRUNCATED_FOR_SIZE"
    d["cache"] = cache
    cen = dict(d.get("coverage_census") or {})
    if cen:
        cen["by_sport_family_phase_state"] = "TRUNCATED_FOR_SIZE"
        d["coverage_census"] = cen
    disc = dict(d.get("native_discovery") or {})
    if disc:
        disc["by_sport_league_state"] = "TRUNCATED_FOR_SIZE"
        disc["receipt_sample"] = "TRUNCATED_FOR_SIZE"
        d["native_discovery"] = disc
    d["heartbeat_truncated"] = True
    s = json.dumps(d, default=str)
    if len(s) <= HEARTBEAT_MAX_BYTES:
        return s
    # Truncating serialized JSON can make the Postgres jsonb write fail.
    # Keep a complete, bounded envelope if another field is too large.
    # Default ensure_ascii=True also makes this a byte-size bound.
    return json.dumps({
        "state": str(d.get("state", "UNKNOWN"))[:128],
        "enabled_env": d.get("enabled_env") is True,
        "heartbeat_truncated": True,
        "reason": "HEARTBEAT_EXCEEDED_SIZE_CAP",
        "c1_decision_effect": DECISION_EFFECT,
    })


async def _census_once(pool) -> dict:
    """The venue catalogue against the feed's current events: every contract
    one named state, reconciled to the catalogue total."""
    from . import pinnapi_census as C
    o = _STATE.get("owner")
    t0 = time.time()
    subscribed = set(o.sport_ids if o else [])
    async with pool.acquire() as c:
        rows = [dict(r) for r in await c.fetch(
            C.catalogue_sql(sport_ids=subscribed))]
        others = [(r["sports_type"], r["n"]) for r in await c.fetch(
            C.catalogue_totals_sql())]
    view = C.feed_event_view(o.cache) if o else {}
    out = C.census(rows, view, subscribed_sports=subscribed,
                   synced=bool(o and o.cache.authority.synced), now=t0,
                   others=others)
    out["computed_at"] = t0
    out["took_ms"] = round((time.time() - t0) * 1000)
    return out


async def _discovery_once(pool) -> dict:
    """PINNAPI-NATIVE DISCOVERY (R30A RC2): every subscribed fixture the
    feed holds, matched to the venue's own events with a receipt each
    (pinnapi_discovery), and every MATCHED fixture seeded in the reactive
    scheduler -- so a PinnAPI price change is evaluated whether or not the
    metered provider ever listed the competition. A fixture the metered
    cycle already seeded keeps that seed (its other books corroborate).
    Read-only; places nothing."""
    from . import pinnapi_discovery as PD
    from . import pinnapi_primary as P
    from . import pinnapi_reactive as RX
    o = _STATE.get("owner")
    t0 = time.time()
    if o is None or not o.cache.authority.synced:
        return {"skipped": "FEED_NOT_SYNCED", "computed_at": t0}
    async with pool.acquire() as c:
        rows = [dict(r) for r in await c.fetch(
            PD.venue_events_sql(o.sport_ids))]
    out = PD.discover(o.cache.events, rows, sport_ids=o.sport_ids)
    reg = collections.Counter()
    for ev in out["seeds"]:
        fam = ev["pinnapi_native"]["family"]
        if fam not in P.SPORTS:
            reg["FAMILY_NOT_PRICED_BY_THE_PRIMARY_SELECTOR"] += 1
            continue
        reg[RX.register(ev, sport_key=PD.sport_key_for(fam), family=fam,
                        received_at=t0, native=True) or "NO_SCHEDULER"] += 1
    out["registered"] = dict(reg)
    out["computed_at"] = t0
    out["took_ms"] = round((time.time() - t0) * 1000)
    return out


async def _beat_loop(pool):
    last_census = 0.0
    while True:
        o = _STATE.get("owner")
        synced = bool(o and o.cache.authority.synced)
        if not synced:
            # nothing to match against: no catalogue read while unsynced
            _STATE["census"] = {"skipped": "FEED_NOT_SYNCED"}
            last_census = 0.0
        elif time.monotonic() - last_census >= CENSUS_S:
            last_census = time.monotonic()
            try:
                async with asyncio.timeout(CENSUS_TIMEOUT_S):
                    _STATE["census"] = await _census_once(pool)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                            # noqa: BLE001
                _STATE["census"] = {"error": type(exc).__name__,
                                    "detail": str(exc)[:200]}
            try:
                async with asyncio.timeout(DISCOVERY_TIMEOUT_S):
                    _STATE["discovery"] = await _discovery_once(pool)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                            # noqa: BLE001
                _STATE["discovery"] = {"error": type(exc).__name__,
                                       "detail": str(exc)[:200]}
        try:
            await _write_heartbeat(pool, dict(digest(), beat_at=time.time()))
        except asyncio.CancelledError:
            raise
        except Exception:                                       # noqa: BLE001
            log.warning("pinnapi feed heartbeat failed", exc_info=True)
        await asyncio.sleep(HEARTBEAT_S)


async def start_default(pool, *, writer_pid: int, writer_lock_key: int,
                        lease_factory=None, connect=None) -> dict:
    """Never raises. Returns what it did and why."""
    try:
        if not enabled():
            return {"state": "KILLED_BY_ENV_PINNAPI_FEED_OFF"}
        if _STATE.get("task") is not None:
            return {"state": "ALREADY_STARTED"}
        sc = await scope(pool)
        from . import db

        async def lf():
            return await O.Lease.open(db._dsn())
        owner = O.FeedOwner(
            F.FeedCache(), sport_ids=sc["sport_ids"], streams=sc["streams"],
            lease_factory=lease_factory or lf,
            connect=connect or PP._ws_connect,
            writer_pid=writer_pid, writer_key=writer_lock_key,
            armed=lambda: armed(pool))
        loop = asyncio.get_running_loop()
        # HELD POSITIONS ARE THE PRIORITY TARGETS (pinnapi_held): the watch
        # rides this owner's cache change notification and its targets are
        # refreshed from the open positions by this runtime -- no second
        # owner, no socket, no other provider.
        from . import pinnapi_held as PH
        PH.install(owner.cache)
        PH.add_listener(PH.paper_review_listener)
        _STATE.update(owner=owner, pool=pool, runtime_id=uuid.uuid4().hex,
                      scope=sc,
                      task=loop.create_task(owner.run()),
                      beat=loop.create_task(_beat_loop(pool)),
                      held=loop.create_task(PH.refresh_loop(pool)))
        return {"state": "STARTED", "scope": sc}
    except Exception as exc:                                    # noqa: BLE001
        log.warning("pinnapi feed start failed", exc_info=True)
        return {"state": "START_FAILED", "error": type(exc).__name__}


async def shutdown_default(wait_s: float = 8.0) -> dict:
    """Stop -> (owner revokes, closes socket, releases lease) -> bounded."""
    o, t, b = _STATE.get("owner"), _STATE.get("task"), _STATE.get("beat")
    if o is None:
        return {"verdict": "NEVER_STARTED"}
    o.stop()
    hz = _STATE.get("held")
    if hz is not None:
        hz.cancel()
        try:
            await hz
        except (asyncio.CancelledError, Exception):             # noqa: BLE001
            pass
    # Stop the periodic writer before producing the terminal record.
    if b is not None:
        b.cancel()
        try:
            await b
        except asyncio.CancelledError:
            pass
        except Exception:
            log.warning("pinnapi heartbeat task ended with error", exc_info=True)
    verdict = "CLOSED"
    try:
        if t is not None:
            await asyncio.wait_for(asyncio.shield(t), wait_s)
    except asyncio.TimeoutError:
        verdict = "INCOMPLETE_CLOSE_TIMEOUT"
        t.cancel()
    except Exception:                                           # noqa: BLE001
        verdict = "CLOSED_WITH_ERROR"
    o.cache.lost(O.R_STOPPED)
    final_status = "WRITTEN_OR_SUPERSEDED"
    try:
        await _write_heartbeat(_STATE["pool"], {
            "state": "RELEASED" if verdict == "CLOSED" else "STOPPING_UNCONFIRMED",
            "runtime_id": _STATE.get("runtime_id"), "beat_at": time.time(),
            "shutdown_verdict": verdict, "authority_proven": False,
            "c1_decision_effect": DECISION_EFFECT}, final=True)
    except Exception as exc:
        final_status = "UNAVAILABLE:" + type(exc).__name__
        log.warning("pinnapi terminal heartbeat unavailable: %s", type(exc).__name__)
    _STATE.update(owner=None, task=None, beat=None, pool=None, runtime_id=None,
                  census=None, held=None, scope=None, discovery=None)
    return {"verdict": verdict, "terminal_heartbeat": final_status}


def read(event_id, key, **kw) -> dict:
    """The one accessor (read by decisions only via held_moneyline)."""
    o = _STATE.get("owner")
    if o is None:
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    return o.cache.read(event_id, key, **kw)


# ── XAVIER'S HELD POSITION: ONE CONTRACT, READ FROM THIS PROCESS'S CACHE ──
R_NO_PAYOUT_EVENT = "HELD_PAYOUT_EVENT_NOT_RECORDED"
R_BAD_COMPLEMENT = "HELD_PAYOUT_COMPLEMENT_NOT_NOT_OF_A_SELECTION"
R_CATALOGUE_UNREADABLE = "HELD_MARKET_CATALOGUE_UNREADABLE"
R_NOT_IN_CATALOGUE = "HELD_MARKET_NOT_IN_VENUE_CATALOGUE"
R_OUTCOME_UNMAPPED = "HELD_OUTCOME_NOT_ONE_FEED_DESIGNATION"
R_NOT_FULL_GAME_ML = "FEED_QUOTE_NOT_A_FULL_GAME_MONEYLINE"
R_HELD_TYPE_UNPROVED = "HELD_VENUE_TYPE_NOT_PROVED_FULL_GAME_MONEYLINE"
R_HELD_TIME_UNPROVED = "HELD_FIXTURE_TIME_NOT_PROVED"
R_HELD_LOOKUP_TIMEOUT = "HELD_MARKET_CATALOGUE_TIMEOUT"
HELD_LOOKUP_TIMEOUT_S = 2.0
#: THE ON-DEMAND READ BEFORE A REVIEW: at most this long, end to end
#: (catalogue rows + the in-process cache read), else a named refusal.
HELD_ON_DEMAND_BUDGET_S = 1.0
R_ON_DEMAND_TIMEOUT = "HELD_ON_DEMAND_READ_OVER_BUDGET"
HELD_FULL_GAME_TYPES = frozenset((
    "baseball_team_full_game_winner", "soccer_team_full_time_winner"))

#: the census's own columns, for the ONE held contract
HELD_CATALOGUE_SQL = """SELECT identifier, side_norm, event_slug, event_title,
       kind, team_name, team_id, team_league, question, signed, line,
       sports_type, extract(epoch FROM game_start)::float8 AS game_start
  FROM us_premap WHERE market_slug = $1 LIMIT 1"""


def held_event_sql() -> str:
    """Every catalogue row of the held contract's event, under the census's
    own base filter, so the held read groups the same structured team
    records the census groups (pinnapi_census.event_identity)."""
    from . import pinnapi_census as C
    return ("""SELECT event_slug, team_name, team_league, sports_type,
       extract(epoch FROM game_start)::float8 AS game_start
  FROM us_premap
 WHERE event_slug = $1 AND %s
 LIMIT 200""" % C._base_where())


def held_quote(row: dict, *, event_rows=None, payout_event,
               payout_is_complement: bool,
               at: float, max_age_s: float, sport_ids, synced: bool,
               view: dict) -> dict:
    """P(the held contract's payout event) from the feed, or a named
    refusal. `row` is the contract's catalogue row (HELD_CATALOGUE_SQL),
    `view` the census's feed_event_view of the owner's cache. Pure apart
    from `read`, which is the cache's own read with its own refusals."""
    from . import bettor_pinnacle_devig as devig
    from . import pinnapi_census as C
    # kind='side' and an empty line also occur on player/period contracts.
    # A held contract does not acquire full-game grading from a fresh feed.
    if row.get("sports_type") not in HELD_FULL_GAME_TYPES:
        return {"ok": False, "reason": R_HELD_TYPE_UNPROVED}
    try:
        start = float(row["game_start"])
        valid_times = all(math.isfinite(x) for x in
                          (start, float(at), float(max_age_s)))
        if not valid_times or float(max_age_s) < 0:
            raise ValueError("invalid time")
    except (TypeError, ValueError, KeyError, OverflowError):
        return {"ok": False, "reason": R_HELD_TIME_UNPROVED}
    pay = str(payout_event or "")
    sel = pay
    if payout_is_complement:
        if not (pay.startswith("NOT(") and pay.endswith(")") and pay[4:-1]):
            return {"ok": False, "reason": R_BAD_COMPLEMENT}
        sel = pay[4:-1]
    state, eid, sid = C.contract_match(row, event_rows or [row], view, subscribed_sports=set(
        sport_ids), synced=synced)
    if state != C.S_SUPPORTED:
        return {"ok": False, "reason": state, "sport_id": sid}
    ev = next((e for e in view.get(sid, []) if e["id"] == eid), None) or {}
    try:
        feed_start = float(ev["start"])
        if (not math.isfinite(feed_start) or
                abs(feed_start - start) > C.START_TOLERANCE_S):
            raise ValueError("unproved fixture time")
    except (TypeError, ValueError, KeyError, OverflowError):
        return {"ok": False, "reason": R_HELD_TIME_UNPROVED,
                "sport_id": sid, "feed_event_id": eid}
    des = C.designation_of(sel, ev)
    if des is None:
        return {"ok": False, "reason": R_OUTCOME_UNMAPPED, "sport_id": sid,
                "feed_event_id": eid}
    key = F.FULL_GAME_MONEYLINE_KEY
    # the record that prices the fixture now (its live-phase child in play)
    got = read(ev.get("quote_id", eid), key,
               evaluated_ms=float(at) * 1000.0, max_age_s=float(max_age_s))
    where = {"sport_id": sid, "feed_event_id": eid, "market_key": key,
             "designation": des}
    if not got.get("ok"):
        return dict(where, ok=False, reason=got.get("reason"),
                    provenance=got.get("provenance"))
    q = got["quote"]
    if q.market_type != "moneyline" or (q.period or 0) != 0 or q.alternate:
        return dict(where, ok=False, reason=R_NOT_FULL_GAME_ML,
                    provenance=got.get("provenance"))
    prov = got["provenance"]
    period = "FULL_GAME"
    val = devig.valuation(
        contract={"sport_family": C.sport_family_of(sid), "market": "h2h",
                  "selection": des, "event_key": eid, "period": period,
                  "line": None},
        quote={"book": devig.BOOK, "outcomes": q.decimal_prices(),
               "observed_at": prov.get("change_ms",
                                       prov.get("source_change_ms")) / 1000.0,
               "received_at": prov["received_ms"] / 1000.0,
               "event_key": eid, "period": period, "line": None},
        now=float(at), max_age_s=float(max_age_s))
    if val.get("probability") is None:
        return dict(where, ok=False,
                    reason=(val.get("refusals") or ["DEVIG_REFUSED"])[0],
                    why=val.get("why"), provenance=prov)
    p_sel = float(val["probability"])
    return dict(where, ok=True,
                p=(1.0 - p_sel) if payout_is_complement else p_sel,
                p_selection=p_sel, payout_event=pay,
                payout_is_complement=bool(payout_is_complement),
                provenance=dict(prov, stream=q.stream),
                devig={"version": devig.VERSION, "method": val["devig_method"],
                       "outcomes": val["expected_outcomes"],
                       "raw_odds": val["raw_odds"],
                       "devigged": val["devigged"],
                       "overround": val["overround"]})


async def held_moneyline(conn, *, us_market_slug, payout_event,
                         payout_is_complement: bool, at: float,
                         max_age_s: float) -> dict:
    """Xavier's read for ONE held contract. No owner in this process ->
    FEED_OWNERSHIP_NOT_HELD before anything else (no catalogue read)."""
    o = _STATE.get("owner")
    if o is None:
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    if not payout_event:
        return {"ok": False, "reason": R_NO_PAYOUT_EVENT}
    if not o.cache.authority.granted or not o.cache.authority.synced:
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    started = time.monotonic()
    try:
        # one bound over BOTH catalogue reads (the contract and its event)
        async with asyncio.timeout(HELD_LOOKUP_TIMEOUT_S):
            row = await conn.fetchrow(HELD_CATALOGUE_SQL, us_market_slug)
            event_rows = ([dict(r) for r in await conn.fetch(
                held_event_sql(), row["event_slug"])]
                if row is not None and row["event_slug"] else [])
    except TimeoutError:
        return {"ok": False, "reason": R_HELD_LOOKUP_TIMEOUT}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": R_CATALOGUE_UNREADABLE,
                "error": type(exc).__name__}
    if row is None:
        return {"ok": False, "reason": R_NOT_IN_CATALOGUE}
    if _STATE.get("owner") is not o:
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    from . import pinnapi_census as C
    # Do not evaluate against the clock captured before an awaited DB read.
    # Include view construction as well; source timestamps stay untouched.
    view = C.feed_event_view(o.cache)
    evaluated_at = float(at) + max(0.0, time.monotonic() - started)
    return held_quote(dict(row), event_rows=event_rows,
                      payout_event=payout_event,
                      payout_is_complement=payout_is_complement,
                      at=evaluated_at,
                      max_age_s=max_age_s, sport_ids=o.sport_ids,
                      synced=bool(o.cache.authority.synced),
                      view=view)


async def held_event_id(conn, us_market_slug) -> tuple:
    """(feed event id, None) for a held contract matched to ONE provider
    event of this process's cache under the census's own identity (same
    teams, start within tolerance, full-game moneyline family), else
    (None, named reason). Read only; no network."""
    from . import pinnapi_census as C
    o = _STATE.get("owner")
    if o is None:
        return None, F.R_NO_AUTHORITY
    if not o.cache.authority.synced:
        return None, C.S_FEED_NOT_SYNCED
    try:
        row = await conn.fetchrow(HELD_CATALOGUE_SQL, us_market_slug)
        if row is None:
            return None, R_NOT_IN_CATALOGUE
        if row["sports_type"] not in HELD_FULL_GAME_TYPES:
            return None, R_HELD_TYPE_UNPROVED
        event_rows = ([dict(r) for r in await conn.fetch(
            held_event_sql(), row["event_slug"])] if row["event_slug"]
            else [])
    except Exception as exc:                                    # noqa: BLE001
        return None, "%s:%s" % (R_CATALOGUE_UNREADABLE, type(exc).__name__)
    state, eid, _sid = C.contract_match(
        dict(row), event_rows or [dict(row)], C.feed_event_view(o.cache),
        subscribed_sports=set(o.sport_ids),
        synced=bool(o.cache.authority.synced))
    if state != C.S_SUPPORTED or eid is None:
        return None, state
    return eid, None
