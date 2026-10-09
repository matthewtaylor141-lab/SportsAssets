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
                "scope": None, "discovery": None, "restarts": 0,
                "last_restart": None,
                # the eviction stand-down (RC6, see EVICTION_STANDDOWN_S):
                # this runtime's record, the last record of an earlier one,
                # and the process's counts (stand-downs opened drive the
                # escalation; re-entries made at a stand-down's end)
                "standdown": None, "standdown_prior": None,
                "eviction_standdowns": 0, "eviction_reentries": 0}
CENSUS_S = 60.0
#: PinnAPI-native discovery (pinnapi_discovery) rides the census cadence:
#: one bounded catalogue read per CENSUS_S, under its own timeout
DISCOVERY_TIMEOUT_S = 10.0
#: THE PASS'S OWN CPU DEADLINE (adversarial verification, finding 1, fix
#: stage 2026-10-05). The matching pass is CPU work; it now runs in a worker
#: thread (so the WS owner, the reactive deadlines and HTTP keep the event
#: loop), and `DISCOVERY_TIMEOUT_S` -- an asyncio timeout -- can cancel the
#: AWAIT but never the thread. So the pass checks this deadline itself and
#: stops (PASS_OVER_BUDGET, nothing seeded). Measured after the fix: 0.16 s
#: for 1,300 fixtures x 400 venue events (29.67 s before), 0.62 s for
#: 2,600 x 1,500 -- the budget is ~8x the largest measured pass.
DISCOVERY_CPU_BUDGET_S = 5.0
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
    # the task's own liveness: `state` is the owner's last word, which a
    # task that ended can never update (P0 first-loss, 2026-10-06)
    d["owner_task"] = owner_task_state()
    d["last_owner_restart"] = _STATE.get("last_restart")
    # THE STAND-DOWN, on the heartbeat (RC6): only THIS runtime's record is
    # its stand-down; the last record of an earlier runtime in this process
    # (a hold change cut it short, see _retire_standdown) stays visible
    # beside it under its own name, with the process's counts
    d["eviction_standdown"] = _own_standdown()
    d["eviction_standdown_prior_runtime"] = (
        dict(_STATE["standdown_prior"]) if _STATE.get("standdown_prior")
        else None)
    d["eviction_standdowns"] = int(_STATE.get("eviction_standdowns") or 0)
    d["eviction_reentries"] = int(_STATE.get("eviction_reentries") or 0)
    d["eviction_standdown_enabled"] = standdown_enabled()
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
        if isinstance(disc.get("line_census"), dict):
            disc["line_census"] = dict(
                disc["line_census"],
                by_sport_family_state="TRUNCATED_FOR_SIZE")
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
    # THE VIEW ON THE LOOP, THE MATCHING OFF IT (R30A). The view is a copy
    # taken from the cache, which only the event loop mutates, so it is
    # taken here; `census` is pure over the rows and that copy, and matching
    # every catalogue contract against it held the loop 2.1 s
    # (`_census_once`, the API loop watchdog, research-sql run
    # 37231263685) -- so it runs in a worker thread and the loop keeps
    # serving every other task's 2-3 s budget meanwhile.
    view = C.feed_event_view(o.cache) if o else {}
    out = await asyncio.to_thread(
        C.census, rows, view, subscribed_sports=subscribed,
        synced=bool(o and o.cache.authority.synced), now=t0, others=others)
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
    # OFF THE EVENT LOOP (adversarial verification, finding 1): the pass is
    # CPU work, so it runs in a worker thread over a SNAPSHOT of the events
    # taken here, on the loop -- the thread never iterates the live cache
    # the WS owner writes -- and stops itself at its own CPU deadline.
    snapshot = {k: dict(v) for k, v in o.cache.events.items()
                if isinstance(v, dict)}
    out = await asyncio.to_thread(
        PD.discover, snapshot, rows, sport_ids=list(o.sport_ids),
        deadline=time.monotonic() + DISCOVERY_CPU_BUDGET_S)
    # REGISTRATION, back on the loop, against the CURRENT cache through ONE
    # fixture index (not one fixture-view rebuild per seed)
    reg = collections.Counter()
    index = P.fixture_index(o.cache) if out["seeds"] else None
    priced = families_with_a_priced_market()
    for ev in out["seeds"]:
        fam = ev["pinnapi_native"]["family"]
        if fam not in P.SPORTS:
            reg["FAMILY_NOT_PRICED_BY_THE_PRIMARY_SELECTOR"] += 1
            continue
        if fam not in priced:
            # no market of this family can be priced (adversarial
            # verification P2): a seed would only spend single-worker
            # evaluations and two audit rows per change, pricing nothing.
            # The receipt stays MATCHED; the seed is counted by name.
            reg[R_NOT_SEEDED_NO_PRICED_MARKET] += 1
            continue
        # keyed by the lane's own league identity of the confirmed venue
        # token (pinnapi_discovery.sport_key_for; verifier finding 1)
        key = PD.sport_key_for(
            fam, ev["pinnapi_native"].get("venue_league_tokens"))
        reg[RX.register(ev, sport_key=key, family=fam,
                        received_at=t0, native=True, index=index)
            or "NO_SCHEDULER"] += 1
    out["registered"] = dict(reg)
    # THE LINE CENSUS (bettor_market_family.census): every line contract the
    # venue lists on a matched fixture, by sport / family / precise state --
    # one more bounded catalogue read; no venue request, nothing priced
    try:
        from . import bettor_market_family as MF
        slugs = sorted(out.get("by_venue_event") or {})
        if slugs:
            async with pool.acquire() as c:
                lrows = [dict(r) for r in await c.fetch(
                    PD.line_rows_sql(), slugs, list(MF.VENUE_LINE_TYPES),
                    int(PD.RESEEN_WITHIN_S), int(PD.MAX_LINE_ROWS))]
        else:
            lrows = []
        out["line_census"] = dict(
            MF.census(lrows, out.get("by_venue_event"), o.cache,
                      now_ms=time.time() * 1000.0),
            rows_truncated=len(lrows) >= PD.MAX_LINE_ROWS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        out["line_census"] = {"error": type(exc).__name__}
    out["computed_at"] = t0
    out["took_ms"] = round((time.time() - t0) * 1000)
    # THE WATCHED LEAGUES' FULL RECEIPTS (pinnapi_discovery.watch_receipts):
    # their own row, apart from the 64 KB heartbeat. Never raises.
    if isinstance(out.get("watch"), dict):
        try:
            async with asyncio.timeout(HEARTBEAT_WRITE_TIMEOUT_S):
                async with pool.acquire() as c:
                    await c.execute(
                        "INSERT INTO ingestion_state (key, value) VALUES "
                        "($1, $2::jsonb) ON CONFLICT (key) DO UPDATE SET "
                        "value = EXCLUDED.value", DISCOVERY_WATCH_KEY,
                        json.dumps(dict(out["watch"], computed_at=t0,
                                        pass_state=out.get("pass_state")),
                                   default=str))
        except asyncio.CancelledError:
            raise
        except Exception:                                       # noqa: BLE001
            log.warning("pinnapi discovery watch write failed",
                        exc_info=True)
    return out


#: the watched leagues' discovery receipts (NCAAF funnel identity stage)
DISCOVERY_WATCH_KEY = "pinnapi_discovery_watch"


#: a MATCHED fixture of a family no market of which can be priced is not
#: seeded (counted under this name in the pass's `registered`)
R_NOT_SEEDED_NO_PRICED_MARKET = "NOT_SEEDED_NO_PRICEABLE_MARKET_FOR_THE_FAMILY"


def families_with_a_priced_market() -> set:
    """The families at least one market of which the decision path can
    price: the de-vig's supported set (money line and the line families it
    admits) and every line family whose payoff equivalence is proven
    (bettor_market_family.EQUIVALENCE). Derived, never typed: a family
    becomes seeded the moment one of its markets is proven. On the R30A
    evidence that is every subscribed family but tennis (no tennis money line
    in the de-vig set; tennis spreads and totals NOT_PROVEN)."""
    from . import bettor_market_family as MF
    from . import bettor_pinnacle_devig as devig
    return ({str(k[0]) for k in devig.SUPPORTED}
            | {str(k[0]) for k in MF.EQUIVALENCE})


#: ── AN OWNER TASK THAT ENDED BY ACCIDENT IS RESTARTED (P0 first-loss) ──
#: Production 2026-10-06 01:29:56Z (research-sql run 37411912022): the owner
#: task ended on a stray CancelledError (pinnapi_owner R_STRAY_CANCELLATION)
#: and nothing noticed -- the heartbeat kept writing its last state
#: (STARTING) for 2.5 h while every candidate read FEED_OWNERSHIP_NOT_HELD.
#: A task that ends while the owner was neither stopped nor refused (a
#: refusal -- writer lock lost, provider refusal, no key -- is a deliberate,
#: permanent stop and stays one; an eviction loop is a stand-down since RC6,
#: see EVICTION_STANDDOWN_S below) is restarted here, once per heartbeat
#: pass, after its leaked lease session (if any) is discarded so the new
#: attempt can take the lease. Authority is revoked first; the new run
#: contends, resynchronizes and is fenced exactly like the first.
R_OWNER_TASK_ENDED = "FEED_OWNER_TASK_ENDED_UNEXPECTEDLY"


#: ── AN EVICTION LOOP STANDS DOWN; IT NO LONGER ENDS THE FEED FOR GOOD (RC6) ─
#:
#: PRODUCTION (the deployed RC5 API, 2026-10-08). The owner set refused =
#: FEED_EVICTION_LOOP_SUSPECTED at 19:00:51Z after three unrequested closes
#: inside EVICTION_WINDOW_S (~18:52:40Z, 18:55:40Z, 19:00:51Z) and, being a
#: refusal, was never restarted here: five hours and more later no backend
#: held the feed lease, every 15-minute cycle carried 85-104
#: FEED_OWNERSHIP_NOT_HELD rows (SOFTWARE: "every one of those states is ours
#: to keep short", 49aaee89), and Xavier's held reads and the collector's WS
#: reference were absent. Only a process restart could bring it back.
#:
#: THE RULE IS KEPT AND BOUNDED, NOT RELAXED (ported from rc6/pipeline-reds
#: 9c0c1fe6 + c9db8ccf onto this owner). The owner still stops at
#: EVICTIONS_MAX unrequested closes inside EVICTION_WINDOW_S, revokes its
#: authority first and never fights inside a run. What changes is that an
#: EVICTION_LOOP refusal is a STAND-DOWN of a stated length, not the end of
#: the feed for the life of the process: after EVICTION_STANDDOWN_S[n]
#: (30 min, then 1 h, 2 h, and 4 h for every later one) this supervisor
#: re-enters the SAME owner once -- refusal and eviction history cleared, a
#: leaked lease session discarded, authority still revoked -- and that run
#: contends for the lease, passes the writer fence and the arm row, and
#: serves nothing until the provider's snapshots arrive on its new epoch. A
#: real second holder of the key therefore sees at most EVICTIONS_MAX
#: connections per stand-down, the stand-downs doubling. Every other refusal
#: -- the decider's writer lock lost, the provider refusing the key or the
#: plan, no key in this service -- stays permanent; a stopped owner is never
#: re-entered.
#:
#: TIMED ON THE MONOTONIC CLOCK (review of 9c0c1fe6): `*_mono` fields time
#: the stand-down, so a wall-clock step can neither end it early nor
#: stretch it; `since_at` / `until_at` / `ended_at` are wall-clock labels
#: for the heartbeat only.
#:
#: A STAND-DOWN BELONGS TO THE RUNTIME THAT OPENED IT; THE ESCALATION
#: BELONGS TO THE PROCESS (review of 9c0c1fe6). ext_pinnacle_loop runs
#: shutdown_default and then start_default in the same process every time a
#: writer hold ends. An open record left in _STATE across that change let
#: the NEXT owner's first eviction loop re-enter at once (or after the old
#: record's remainder): 2 x EVICTIONS_MAX connections back to back against
#: a possible real holder, and the new runtime's heartbeat showing the old
#: owner's stand-down. So:
#:   - every record carries the runtime_id it was opened for; another
#:     runtime's is retired (STANDDOWN_OF_ANOTHER_RUNTIME), never re-entered;
#:   - shutdown_default closes an open record (STANDDOWN_CUT_SHORT_BY_
#:     SHUTDOWN) and keeps it, labelled, as `eviction_standdown_prior_
#:     runtime` on the heartbeat (and `eviction_standdown_at_shutdown` on the
#:     terminal one);
#:   - the length is chosen by how many stand-downs THIS PROCESS has opened
#:     (`eviction_standdowns`), not by completed re-entries, so a hold change
#:     does not reset the escalation: the next hold's owner connects at once
#:     -- as a new hold's owner always has -- and its next loop stands down
#:     for the NEXT length. Only a process restart clears it, as it cleared
#:     the permanent refusal before RC6.
#:
#: KILL SWITCH: env PINNAPI_EVICTION_STANDDOWN in {off,0,false,no} restores
#: the exact pre-RC6 behaviour -- an EVICTION_LOOP refusal is permanent, no
#: record is opened, nothing is re-entered. Default on.
EVICTION_STANDDOWN_S = (1800.0, 3600.0, 7200.0, 14400.0)
STANDDOWN_ENV = "PINNAPI_EVICTION_STANDDOWN"
#: how a stand-down record ended (`ended_by`); records, not refusals
STANDDOWN_ENDED_BY_REENTRY = "REENTERED_AFTER_STANDDOWN"
STANDDOWN_CUT_SHORT_BY_SHUTDOWN = "CUT_SHORT_BY_OWNER_SHUTDOWN"
STANDDOWN_OF_ANOTHER_RUNTIME = "RETIRED_RECORD_OF_ANOTHER_RUNTIME"


def standdown_enabled() -> bool:
    """False only for an explicit kill switch (PINNAPI_EVICTION_STANDDOWN in
    off/0/false/no): then an eviction loop is a permanent refusal, exactly
    as before RC6."""
    return (os.environ.get(STANDDOWN_ENV) or "").strip().lower() not in (
        "off", "0", "false", "no")


def _standdown_s(n: int) -> float:
    return EVICTION_STANDDOWN_S[min(max(0, n), len(EVICTION_STANDDOWN_S) - 1)]


def _own_standdown() -> Optional[dict]:
    """This runtime's stand-down record (a copy, with the seconds left while
    it is open), else None."""
    sd = _STATE.get("standdown")
    if sd is None or sd.get("runtime_id") != _STATE.get("runtime_id"):
        return None
    out = dict(sd)
    if out.get("ended_mono") is None:
        try:
            out["remaining_s"] = round(max(
                0.0, float(out["until_mono"]) - time.monotonic()), 3)
        except (TypeError, ValueError, KeyError):
            out["remaining_s"] = None
    return out


def _retire_standdown(now: float, why: str) -> Optional[dict]:
    """Move the current record, whatever its runtime, to `standdown_prior`.
    An open one is closed at monotonic `now` with `why`; one that already
    ended keeps its own ending. Returns the retired record, else None."""
    sd = _STATE.get("standdown")
    if sd is None:
        return None
    sd = dict(sd)
    if sd.get("ended_mono") is None:
        sd["ended_mono"], sd["ended_at"] = now, round(time.time(), 3)
        sd["ended_by"] = why
        try:
            left = max(0.0, float(sd.get("until_mono")) - now)
        except (TypeError, ValueError):
            left = 0.0
        log.warning("pinnapi feed: stand-down of runtime %s ended early "
                    "(%s) %.0fs before its stated end", sd.get("runtime_id"),
                    why, left)
    _STATE["standdown_prior"] = sd
    _STATE["standdown"] = None
    return sd


def _eviction_standdown(o, now: float) -> Optional[str]:
    """Hold an EVICTION_LOOP refusal for its stand-down (monotonic `now`),
    then re-enter the owner once. Returns "EVICTION_STANDDOWN_ENDED" when
    it re-entered, else None. Must run on the event loop."""
    rid = _STATE.get("runtime_id")
    sd = _STATE.get("standdown")
    if sd is not None and sd.get("runtime_id") != rid:
        # another runtime's record neither holds this owner to its clock
        # nor lets it re-enter at that record's end
        _retire_standdown(now, STANDDOWN_OF_ANOTHER_RUNTIME)
        sd = None
    if sd is None or sd.get("ended_mono") is not None:
        n = int(_STATE.get("eviction_standdowns") or 0)
        secs = _standdown_s(n)
        wall = round(time.time(), 3)
        sd = {"refused": O.R_EVICTION_LOOP, "runtime_id": rid,
              "ordinal": n + 1, "seconds": secs,
              "since_mono": now, "until_mono": now + secs,
              "since_at": wall, "until_at": round(wall + secs, 3),
              "ended_mono": None, "ended_at": None, "ended_by": None}
        _STATE["standdown"] = sd
        _STATE["eviction_standdowns"] = n + 1
        o._note("EVICTION_STANDDOWN", seconds=secs, ordinal=n + 1,
                until_at=sd["until_at"])
        log.warning("pinnapi feed: eviction loop suspected; standing down "
                    "%.0fs (stand-down %d in this process) before one "
                    "re-entry", secs, n + 1)
        return None
    if now < sd["until_mono"]:
        return None
    # THE RE-ENTRY: the same owner, clean, contending from scratch. Authority
    # stays revoked until the new epoch resynchronizes.
    o.cache.lost(O.R_EVICTION_LOOP)
    lease = getattr(o, "lease", None)
    if lease is not None:
        try:
            lease.discard()
        except Exception:                                       # noqa: BLE001
            pass
        o.lease = None
    o.refused = None
    o.evictions = []
    o.state = "STARTING"
    sd["ended_mono"], sd["ended_at"] = now, round(time.time(), 3)
    sd["ended_by"] = STANDDOWN_ENDED_BY_REENTRY
    _STATE["eviction_reentries"] = int(
        _STATE.get("eviction_reentries") or 0) + 1
    o._note("EVICTION_STANDDOWN_ENDED", ordinal=sd["ordinal"],
            stood_down_s=round(now - sd["since_mono"], 3),
            reentries=_STATE["eviction_reentries"])
    log.warning("pinnapi feed: stand-down %d over after %.0fs; re-entering",
                sd["ordinal"], now - sd["since_mono"])
    _STATE["task"] = asyncio.get_running_loop().create_task(o.run())
    return "EVICTION_STANDDOWN_ENDED"


def owner_task_state() -> dict:
    t, o = _STATE.get("task"), _STATE.get("owner")
    if t is None or o is None:
        return {"state": "NOT_STARTED"}
    if not t.done():
        return {"state": "RUNNING", "restarts": _STATE.get("restarts", 0)}
    if t.cancelled():
        why = "CANCELLED"
    else:
        exc = t.exception()
        why = "RAISED:%s" % type(exc).__name__ if exc else "RETURNED"
    return {"state": "ENDED", "how": why,
            "deliberate": bool(o.stop_event.is_set() or o.refused),
            "restarts": _STATE.get("restarts", 0)}


def supervise(now: Optional[float] = None) -> Optional[str]:
    """Restart the owner task if it ended without being stopped or refused,
    and re-enter an EVICTION_LOOP refusal after its stand-down (`now`: a
    time.monotonic() reading; the current one when None). Returns how it
    had ended when it restarted it, "EVICTION_STANDDOWN_ENDED" when it
    re-entered, else None. Never raises; must run on the event loop."""
    try:
        o, t = _STATE.get("owner"), _STATE.get("task")
        if o is None or t is None or not t.done():
            return None
        if o.stop_event.is_set():
            return None
        if o.refused == O.R_EVICTION_LOOP and standdown_enabled():
            return _eviction_standdown(
                o, time.monotonic() if now is None else float(now))
        if o.refused:
            return None
        how = owner_task_state().get("how")
        o.cache.lost(R_OWNER_TASK_ENDED)
        lease = getattr(o, "lease", None)
        if lease is not None:
            try:
                lease.discard()
            except Exception:                                   # noqa: BLE001
                pass
            o.lease = None
        _STATE["restarts"] = int(_STATE.get("restarts") or 0) + 1
        _STATE["last_restart"] = {"at": time.time(), "ended": how}
        o._note("OWNER_TASK_RESTARTED", ended=how)
        log.error("pinnapi feed owner task ended (%s) without a stop or a "
                  "refusal; restarted", how)
        _STATE["task"] = asyncio.get_running_loop().create_task(o.run())
        return how
    except Exception:                                           # noqa: BLE001
        log.warning("pinnapi feed supervision failed", exc_info=True)
        return None


#: how long the decider's first cycle of a hold waits for the owner to
#: resynchronize (ext_pinnacle_loop._hold_once); bounded so a provider that
#: never answers delays one cycle by at most this, never the lane
FIRST_SYNC_WAIT_S = 60.0
#: owner states in which no sync is coming soon: return at once
_NOT_CONTENDING = ("DISARMED", "STANDBY", "WRITER_LOCK_LOST",
                   "EVICTION_LOOP_SUSPECTED", "REFUSED_BY_PROVIDER",
                   "STOPPED")


async def wait_synced(timeout_s: float = FIRST_SYNC_WAIT_S,
                      poll_s: float = 0.25) -> str:
    """Wait until this process's owner is synced, by name: SYNCED,
    NOT_STARTED, NOT_CONTENDING:<state>, OWNER_TASK_ENDED, or
    TIMEOUT_AFTER_<s>S. Reads nothing but the in-process owner."""
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        o, t = _STATE.get("owner"), _STATE.get("task")
        if o is None:
            return "NOT_STARTED"
        if o.cache.authority.synced:
            return "SYNCED"
        if o.refused or o.state in _NOT_CONTENDING:
            return "NOT_CONTENDING:%s" % (o.refused or o.state)
        if t is not None and t.done():
            return "OWNER_TASK_ENDED"
        if time.monotonic() >= deadline:
            return "TIMEOUT_AFTER_%dS" % int(timeout_s)
        await asyncio.sleep(poll_s)


async def _beat_loop(pool):
    last_census = 0.0
    while True:
        supervise()
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
        if t is not None and t.done():
            # an owner task that already ended (a stray cancellation, an
            # error): awaiting it would re-raise its CancelledError into the
            # decider's teardown (P0 first-loss, 2026-10-06)
            if t.cancelled() or t.exception() is not None:
                verdict = "CLOSED_WITH_ERROR"
        elif t is not None:
            await asyncio.wait_for(asyncio.shield(t), wait_s)
    except asyncio.TimeoutError:
        verdict = "INCOMPLETE_CLOSE_TIMEOUT"
        t.cancel()
    except asyncio.CancelledError:
        if O._task_is_being_cancelled():
            raise
        verdict = "CLOSED_WITH_ERROR"
    except Exception:                                           # noqa: BLE001
        verdict = "CLOSED_WITH_ERROR"
    # a lease the owner never retired (its task ended holding one) is
    # discarded so the next holder can take it
    _leaked = getattr(o, "lease", None)
    if _leaked is not None and (t is None or t.done()):
        try:
            _leaked.discard()
        except Exception:                                       # noqa: BLE001
            pass
        o.lease = None
    o.cache.lost(O.R_STOPPED)
    # THE STAND-DOWN DOES NOT OUTLIVE ITS RUNTIME (review of 9c0c1fe6): an
    # open one is closed here as cut short and kept, labelled, as the prior
    # runtime's; the next hold's owner opens its own (the escalation carries)
    retired = _retire_standdown(time.monotonic(),
                                STANDDOWN_CUT_SHORT_BY_SHUTDOWN)
    final_status = "WRITTEN_OR_SUPERSEDED"
    try:
        await _write_heartbeat(_STATE["pool"], {
            "state": "RELEASED" if verdict == "CLOSED" else "STOPPING_UNCONFIRMED",
            "runtime_id": _STATE.get("runtime_id"), "beat_at": time.time(),
            "shutdown_verdict": verdict, "authority_proven": False,
            "eviction_standdown_at_shutdown": retired,
            "c1_decision_effect": DECISION_EFFECT}, final=True)
    except Exception as exc:
        final_status = "UNAVAILABLE:" + type(exc).__name__
        log.warning("pinnapi terminal heartbeat unavailable: %s", type(exc).__name__)
    # `standdown` was retired above; `standdown_prior`, `eviction_standdowns`
    # and `eviction_reentries` are the process's and are kept on purpose
    _STATE.update(owner=None, task=None, beat=None, pool=None, runtime_id=None,
                  census=None, held=None, scope=None, discovery=None,
                  restarts=0, last_restart=None)
    return {"verdict": verdict, "terminal_heartbeat": final_status}


def read(event_id, key, **kw) -> dict:
    """The one accessor (read by decisions only via held_moneyline)."""
    o = _STATE.get("owner")
    if o is None:
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    return o.cache.read(event_id, key, **kw)


def read_held(event_id, key, **kw) -> dict:
    """The HELD-position accessor: the change rule, else a provider-stamped
    confirmation of the unchanged price within the same limit
    (pinnapi_feed.FeedCache.read_held)."""
    o = _STATE.get("owner")
    if o is None:
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    rh = getattr(o.cache, "read_held", None)
    return (rh or o.cache.read)(event_id, key, **kw)


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
    "baseball_team_full_game_winner", "soccer_team_full_time_winner",
    # R30A: the venue's own NFL money-line type (its listing's
    # sportsMarketType, tests/fixtures/pmus_nfl_listing_2026_10_04.json). A
    # held NFL position's measure is the period-0 two-way line, converted for
    # the venue's tie payout by the policy that reads it (paper_benchmark).
    "football_team_full_game_winner",
    # P0 coverage: the venue's NBA / NHL full-game winners (its listings'
    # sportsMarketType, tests/fixtures/
    # pmus_nba_nhl_winner_listings_2026_10_06.json). The period-0 two-way
    # line, admitted by league exactly as the cycle's (contract_match ->
    # pinnapi_census.family_of -> bettor_pinnacle_devig.SUPPORTED_BY_LEAGUE:
    # the captured leagues); every other league of the spelling refuses.
    "basketball_team_full_game_winner", "hockey_team_full_game_winner"))

#: the census's own columns, for the ONE held contract
HELD_CATALOGUE_SQL = """SELECT identifier, side_norm, event_slug, event_title,
       kind, team_name, team_id, team_league, question, signed, line,
       sports_type, extract(epoch FROM game_start)::float8 AS game_start
  FROM us_premap WHERE market_slug = $1 LIMIT 1"""


def held_event_sql() -> str:
    """Every catalogue row of the held contract's event, under the census's
    own realism filters, so the held read groups the same structured team
    records the census groups (pinnapi_census.event_identity).

    WITHOUT THE CENSUS'S START WINDOW (closeout, production 2026-10-06): the
    census population is game_start in (now-6h, now+96h], and the held read
    used it too -- so a held NFL position on a game more than 96 h out found
    NO event rows, fell back to its own single row and refused every review
    STRUCTURED_PARTICIPANTS_NOT_TWO (16 of 127 open groups). The held
    contract's event is already identified by its slug; its start is still
    checked against the provider's (START_TOLERANCE_S) in held_quote."""
    from . import pinnapi_census as C
    return ("""SELECT event_slug, team_name, team_league, sports_type,
       extract(epoch FROM game_start)::float8 AS game_start
  FROM us_premap
 WHERE event_slug = $1 AND %s
 LIMIT 200""" % C._base_where(horizon=False))


#: THE ENTRY'S PROVEN PROVIDER FIXTURE (closeout, production 2026-10-06):
#: the entry valuation of a held contract was written from THIS feed under
#: the discovery matcher's identity, keyed "pinnapi:<fixture id>"
#: (pinnapi_discovery.seed_event). The held read re-matched the event by
#: exact structured names only, so a fixture the entry had already proven
#: (venue "Vila Nova" vs provider "Vila Nova FC") read NO_FEED_EVENT on every
#: review. Only when that exact match finds nothing (or the venue's rows do
#: not group to two teams) and the entry's key names a fixture the cache
#: holds in the same sport, that fixture is used -- its start still checked
#: against the venue's (START_TOLERANCE_S) and the outcome still mapped to
#: ONE designation, or the read refuses exactly as before.
ENTRY_FIXTURE_PREFIX = "pinnapi:"
IDENTITY_EXACT = "EXACT_STRUCTURED_NAMES"
IDENTITY_ENTRY_FIXTURE = "ENTRY_PROVEN_PROVIDER_FIXTURE"


def entry_fixture(entry_event_key, view: dict, sid):
    """The cache's fixture the entry valuation named, or None (pure)."""
    k = str(entry_event_key or "")
    if sid is None or not k.startswith(ENTRY_FIXTURE_PREFIX):
        return None
    fid = k[len(ENTRY_FIXTURE_PREFIX):]
    if not fid:
        return None
    hits = [e for e in view.get(sid, []) if str(e.get("id")) == fid]
    return hits[0] if len(hits) == 1 else None


#: ── ONE FIXTURE IDENTITY FOR THE HELD READS AND THE HELD WATCH (RC6) ────
#:
#: The held READ learned two things in the closeout (d075e12f): held LINE
#: contracts (spreads / totals / team totals, `held_line_quote`) and the
#: ENTRY-PROVEN provider fixture (`entry_fixture`). The held WATCH -- which
#: provider fixtures are Xavier's priority targets, whose changes and
#: provider-stamped confirmations trigger an immediate review
#: (pinnapi_held.refresh -> held_event_id) -- learned neither: it refused
#: every non-moneyline contract HELD_VENUE_TYPE_NOT_PROVED_FULL_GAME_MONEYLINE
#: and matched exact names only.
#:
#: MEASURED. research-sql run 37840684877 (complete-packet reviews since
#: 2026-10-07 04:37Z): the held NFL spread asc-nfl-tb-dal-2026-10-08-pos-9pt5
#: (papergrp:bfaade40...) was read current from this cache 267 times
#: (CURRENT_BLEND_HELD_CACHE, EXACT_STRUCTURED_NAMES), yet as a non-target it
#: was reviewed only on the 60 s backstop: 2-7 complete reviews an hour on
#: 2026-10-08 13:00-18:59Z. Its provider confirmations come only when
#: Pinnacle bumps the matchup (PinnAPI forwards prematch_matchups for
#: CHANGED matchups only -- 13 single-matchup frames in 15 s for ~1,300
#: events, ws_sample run 36940200143; production confirmations
#: PREMATCH_MATCHUPS_VERSION_UNCHANGED 0 against PREMATCH_MARKETS_
#: AUTHORITATIVE_LIST 140,424, research-sql run 37841015429), so a backstop
#: review lands inside the 30 s after a bump only by chance. Four groups were
#: read current ONLY through the entry-proven fixture (75a2dd90, f8cac471,
#: c077f868, c3da878f: identity ENTRY_PROVEN_PROVIDER_FIXTURE) and so were
#: never targets either.
#:
#: THE REPAIR: `held_fixture` is the identity, used by both reads and by
#: `held_event_id`. A held line contract whose venue family is proven
#: resolves to its fixture by the line read's own event identity; the
#: entry-proven fixture applies where exact names find nothing. The watch
#: still listens on the fixture's full-game money line (a matchup bump
#: re-delivers every market of it); a review it triggers reads the HELD
#: contract's own quote under the unchanged 30 s rule -- nothing is made
#: fresh by the trigger, and nothing is read that the review would not.
def held_line_type_refusal(sports_type) -> Optional[str]:
    """None when the venue type is a line family whose payoff equivalence
    is proven (the held line read prices it), else the named refusal --
    the line read's own first two checks. Pure."""
    from . import bettor_market_family as MF
    fam = MF.venue_line_family(sports_type)
    if fam.get("refusal"):
        return R_HELD_TYPE_UNPROVED
    st = MF.family_status(fam["sport"], fam["family"])
    if not st.get("proven"):
        return st.get("refusal") or R_HELD_TYPE_UNPROVED
    return None


def held_fixture(row: dict, *, event_rows, view: dict, sport_ids,
                 synced: bool, entry_event_key=None, line: bool = False,
                 start=None) -> tuple:
    """(state, feed fixture id, sport id, identity basis) for ONE held
    contract -- THE identity of the held reads and the held watch. A
    moneyline contract: the census's contract_match. A line contract
    (`line`, `start` its finite game start): the same event identity
    without the moneyline family check. Either: the entry-proven provider
    fixture when exact names find nothing. Pure."""
    from . import pinnapi_census as C
    if not line:
        state, eid, sid = C.contract_match(
            row, event_rows or [row], view,
            subscribed_sports=set(sport_ids), synced=synced)
    else:
        sid = C.sport_id_of(row.get("sports_type"))
        if sid is None:
            return C.S_UNMAPPED_SPORT, None, None, None
        if sid not in set(sport_ids):
            return C.S_OUT_OF_SCOPE, None, sid, None
        if not synced:
            return C.S_FEED_NOT_SYNCED, None, sid, None
        same = [r for r in (event_rows or [])
                if C.sport_id_of(r.get("sports_type")) == sid
                and r.get("event_slug") == row.get("event_slug")] or [row]
        teams, leagues, starts = C.group_event(same)
        state, eid = C.event_identity(teams, leagues, starts,
                                      row.get("event_slug"), start,
                                      view.get(sid, []))
        if state == C.S_NO_FEED_EVENT:
            state, eid = C.split_name_identity(same, sid, start,
                                               view.get(sid, []))
    identity = IDENTITY_EXACT
    if state in (C.S_NO_FEED_EVENT, "STRUCTURED_PARTICIPANTS_NOT_TWO"):
        fx = entry_fixture(entry_event_key, view, sid)
        if fx is not None:
            state, eid, identity = C.S_SUPPORTED, fx["id"], \
                IDENTITY_ENTRY_FIXTURE
    return state, eid, sid, identity


def held_quote(row: dict, *, event_rows=None, payout_event,
               payout_is_complement: bool,
               at: float, max_age_s: float, sport_ids, synced: bool,
               view: dict, entry_event_key=None) -> dict:
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
    state, eid, sid, identity = held_fixture(
        row, event_rows=event_rows, view=view, sport_ids=sport_ids,
        synced=synced, entry_event_key=entry_event_key)
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
    got = read_held(ev.get("quote_id", eid), key,
                    evaluated_ms=float(at) * 1000.0,
                    max_age_s=float(max_age_s))
    where = {"sport_id": sid, "feed_event_id": eid, "market_key": key,
             "designation": des, "identity_basis": identity}
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
        # THE VENUE LEAGUE TRAVELS WITH THE CONTRACT (R30A). Football is
        # admitted to the de-vig for the NFL only (SUPPORTED_BY_LEAGUE), and
        # the catalogue row is the venue's own record of which league the
        # held contract is: its team league and its venue-native slug, which
        # the de-vig requires to agree. Baseball and soccer never read it.
        contract={"sport_family": C.sport_family_of(sid), "market": "h2h",
                  "selection": des, "event_key": eid, "period": period,
                  "line": None, "league": row.get("team_league"),
                  "us_market_slug": row.get("identifier")},
        quote={"book": devig.BOOK, "outcomes": q.decimal_prices(),
               "observed_at": (prov.get("freshness_at_ms")
                               or prov.get("change_ms")
                               or prov.get("source_change_ms")) / 1000.0,
               "received_at": prov["received_ms"] / 1000.0,
               "event_key": eid, "period": period, "line": None},
        now=float(at), max_age_s=float(max_age_s))
    if val.get("probability") is None:
        return dict(where, ok=False,
                    reason=(val.get("refusals") or ["DEVIG_REFUSED"])[0],
                    why=val.get("why"), provenance=prov)
    p_sel = float(val["probability"])
    extra = {}
    if val.get("conditional_on") is not None:
        # R30A: an NFL two-way line is P(win | no tie). The conditioning
        # travels with the number; the reader that holds the contract
        # (paper_benchmark.xavier_measure) converts it to the venue
        # contract's value before comparing it with any price.
        extra["conditional_on"] = val["conditional_on"]
    return dict(where, ok=True, **extra,
                p=(1.0 - p_sel) if payout_is_complement else p_sel,
                p_selection=p_sel, payout_event=pay,
                payout_is_complement=bool(payout_is_complement),
                provenance=dict(prov, stream=q.stream),
                devig={"version": devig.VERSION, "method": val["devig_method"],
                       "outcomes": val["expected_outcomes"],
                       "raw_odds": val["raw_odds"],
                       "devigged": val["devigged"],
                       "overround": val["overround"]})


# ── XAVIER'S HELD LINE POSITION: THE ENTRY'S OWN PRICING, RE-READ NOW ─────
#: THE HELD SPREAD / TOTAL / TEAM TOTAL (closeout, production 2026-10-06:
#: about 30 of 127 open PAPER groups -- NFL / NCAAF / NHL / MLB / WNBA lines
#: entered by the line-market lane -- refused every review
#: HELD_VENUE_TYPE_NOT_PROVED_FULL_GAME_MONEYLINE, because the held read knew
#: only the money line). The entry priced the line on THIS cache:
#: bettor_market_family.pinnacle_pair (same family, period 0, the IDENTICAL
#: half-point line on the IDENTICAL side, through the cache's one read path
#: and its unchanged 30 s rule) de-vigged by bettor_pinnacle_devig. The held
#: read runs exactly that again for the held contract: the line, the family
#: and the payout outcome's name come from the ENTRY VALUATION (the outcome
#: is Pinnacle's own "<team> <signed line>" / "Over <line>" spelling, so it
#: must equal exactly one of the two outcome names the pair is built from),
#: the fixture from the event identity -- or the entry's proven fixture.
R_HELD_LINE_NOT_ENTRY_LINE = "HELD_LINE_ENTRY_VALUATION_STATES_NO_LINE"
R_HELD_LINE_OUTCOME = "HELD_LINE_OUTCOME_NOT_ONE_PINNACLE_OUTCOME"


def held_line_quote(row: dict, *, event_rows=None, payout_event,
                    payout_is_complement: bool, at: float, max_age_s: float,
                    sport_ids, synced: bool, view: dict, cache,
                    entry_line, entry_event_key=None) -> dict:
    """P(the held line contract's payout outcome), or a named refusal. Pure
    apart from the cache's own read path."""
    from . import bettor_market_family as MF
    from . import bettor_pinnacle_devig as devig
    from . import pinnapi_census as C
    why = held_line_type_refusal(row.get("sports_type"))
    if why is not None:
        return {"ok": False, "reason": why}
    fam = MF.venue_line_family(row.get("sports_type"))
    try:
        line = float(entry_line)
        start = float(row["game_start"])
        if not all(math.isfinite(x) for x in (line, start, float(at),
                                               float(max_age_s))):
            raise ValueError
    except (TypeError, ValueError, KeyError, OverflowError):
        return {"ok": False, "reason": (
            R_HELD_LINE_NOT_ENTRY_LINE if entry_line is None
            else R_HELD_TIME_UNPROVED)}
    if not MF.half_point(line):
        return {"ok": False, "reason": MF.R_NOT_HALF_POINT}
    pay = str(payout_event or "")
    name = pay
    if payout_is_complement:
        if not (pay.startswith("NOT(") and pay.endswith(")") and pay[4:-1]):
            return {"ok": False, "reason": R_BAD_COMPLEMENT}
        name = pay[4:-1]
    state, eid, sid, identity = held_fixture(
        row, event_rows=event_rows, view=view, sport_ids=sport_ids,
        synced=synced, entry_event_key=entry_event_key, line=True,
        start=start)
    if state == C.S_UNMAPPED_SPORT:
        return {"ok": False, "reason": state}
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
    where = {"sport_id": sid, "feed_event_id": eid,
             "identity_basis": identity, "family": fam["family"],
             "line": line}
    labels = F.participants((getattr(cache, "events", None) or {})
                            .get(eid) or {})
    if set(labels) != {"home", "away"}:
        return dict(where, ok=False, reason=MF.R_NO_FIXTURE)
    # the ONE contract (designation, side) whose outcome names the entry's
    # payout outcome exactly
    hits = []
    for des in ((None,) if fam["family"] == MF.TOTAL
                else ("home", "away")):
        cand = {"family": fam["family"], "line": line, "designation": des}
        sel, oth = MF.outcome_names(cand, labels)
        if name == sel:
            hits.append((cand, sel))
        elif name == oth:
            hits.append((cand, oth))
    if len(hits) != 1:
        return dict(where, ok=False, reason=R_HELD_LINE_OUTCOME,
                    why="%d contracts name %r" % (len(hits), name))
    cand, outcome = hits[0]
    pair = MF.pinnacle_pair(cache, fixture_id=eid, contract=cand,
                            evaluated_ms=float(at) * 1000.0,
                            max_age_s=float(max_age_s), held=True)
    where.update(market_key=pair.get("key"),
                 designation=(pair.get("designations") or {}).get(outcome))
    if not pair.get("ok"):
        return dict(where, ok=False, reason=pair.get("refusal"),
                    provenance=pair.get("provenance"))
    prov = dict(pair.get("provenance") or {})
    val = devig.valuation(
        contract={"sport_family": fam["sport"], "market": fam["family"],
                  "selection": outcome, "event_key": eid,
                  "period": "FULL_GAME", "line": line,
                  "league": row.get("team_league"),
                  "us_market_slug": row.get("identifier")},
        quote={"book": devig.BOOK, "outcomes": dict(pair["outcomes"]),
               "observed_at": pair["observed_at"],
               "received_at": pair["received_at"], "event_key": eid,
               "period": "FULL_GAME", "line": pair["line"]},
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
                provenance=dict(prov, stream=pair.get("stream")),
                devig={"version": devig.VERSION,
                       "method": val["devig_method"],
                       "outcomes": val["expected_outcomes"],
                       "raw_odds": val["raw_odds"],
                       "devigged": val["devigged"],
                       "overround": val["overround"]})


async def held_moneyline(conn, *, us_market_slug, payout_event,
                         payout_is_complement: bool, at: float,
                         max_age_s: float, entry_event_key=None,
                         entry_line=None) -> dict:
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
    if row["sports_type"] not in HELD_FULL_GAME_TYPES:
        from . import bettor_market_family as MF
        if not MF.venue_line_family(row["sports_type"]).get("refusal"):
            return held_line_quote(
                dict(row), event_rows=event_rows, payout_event=payout_event,
                payout_is_complement=payout_is_complement, at=evaluated_at,
                max_age_s=max_age_s, sport_ids=o.sport_ids,
                synced=bool(o.cache.authority.synced), view=view,
                cache=o.cache, entry_line=entry_line,
                entry_event_key=entry_event_key)
    return held_quote(dict(row), event_rows=event_rows,
                      payout_event=payout_event,
                      payout_is_complement=payout_is_complement,
                      at=evaluated_at,
                      max_age_s=max_age_s, sport_ids=o.sport_ids,
                      synced=bool(o.cache.authority.synced),
                      view=view, entry_event_key=entry_event_key)


async def held_event_id(conn, us_market_slug, *, view=None,
                        entry_event_key=None) -> tuple:
    """(feed fixture id, None) for a held contract matched to ONE provider
    fixture of this process's cache under THE held reads' own identity
    (`held_fixture`: same teams, start within tolerance; a full-game
    moneyline, or a line contract whose family is proven; the entry-proven
    fixture `entry_event_key` where exact names find nothing), else (None,
    named reason). Read only; no network.

    `view` (R30A): the feed event view (pinnapi_census.feed_event_view) the
    caller already built for this pass. pinnapi_held.refresh resolves EVERY
    held slug in one pass and rebuilt the view -- a walk of up to MAX_EVENTS
    events, each one's participants parsed -- once PER SLUG, on the event
    loop: the API's loop watchdog recorded the loop held 2.0 s in exactly
    `participants <- feed_event_view <- held_event_id <- refresh`
    (research-sql run 37231263685). None builds it here, as before."""
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
        # RC6: a held LINE contract the held read prices is a target too
        line = row["sports_type"] not in HELD_FULL_GAME_TYPES
        if line:
            why = held_line_type_refusal(row["sports_type"])
            if why is not None:
                return None, why
        event_rows = ([dict(r) for r in await conn.fetch(
            held_event_sql(), row["event_slug"])] if row["event_slug"]
            else [])
    except Exception as exc:                                    # noqa: BLE001
        return None, "%s:%s" % (R_CATALOGUE_UNREADABLE, type(exc).__name__)
    start = None
    if line:
        try:
            start = float(row["game_start"])
            if not math.isfinite(start):
                raise ValueError("invalid time")
        except (TypeError, ValueError, KeyError, OverflowError):
            return None, R_HELD_TIME_UNPROVED
    if view is None:
        view = C.feed_event_view(o.cache)
    state, eid, _sid, _basis = held_fixture(
        dict(row), event_rows=event_rows or [dict(row)], view=view,
        sport_ids=o.sport_ids, synced=bool(o.cache.authority.synced),
        entry_event_key=entry_event_key, line=line, start=start)
    if state != C.S_SUPPORTED or eid is None:
        return None, state
    return eid, None
