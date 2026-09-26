"""Worker: THE EXTERNAL-VALUATION SHADOW, ACTUALLY RUNNING IN PRODUCTION.

WHAT WAS WRONG BEFORE THIS FILE EXISTED. `bettor_external_shadow.evaluate`
was called by a test and by a research runner, and by nothing else. The
entry inputs it forwards into `bettor_entry_gate.admit` were therefore
supplied only by test fixtures. A function that accepts the new inputs in
a test is not a connected path -- the runtime caller has to supply them,
and until this loop there was no runtime caller at all.

ONE CYCLE, IN ORDER, AND IT FAILS CLOSED AT EVERY STEP:

    1. CONTROL ROW. Absent, false, malformed or unreadable all mean
       STOPPED. Absence is not permission.
    2. TABLES, against the live catalog rather than the migrations
       directory -- migration 100 sat committed at HEAD for hours while
       production had none of its tables.
    3. CREDENTIAL, by presence only. Never read into a log, a return
       value or an error message.
    4. FRESH ODDS from the provider, for the supported sports only.
    5. VENUE CONTRACT, exactly, via `bettor_venue_mapping`.
    6. CONTEMPORANEOUS VENUE QUOTE -- ask and depth, read in the same
       cycle as the odds, because comparing a fresh probability against a
       stale price is the error that makes everything look profitable.
    7. FEES from the production schedule. Never defaulted.
    8. THE REAL GATE, with the real execution and risk checks.
    9. PERSIST every evaluation, cleared or refused, exactly once.

IT PLACES NO ORDER. There is no venue write in this module's import
closure: `bettor_live_read` is a reader, and the only writer reached is
`bettor_external_shadow.persist`, whose table CHECKs order_submitted is
FALSE. That CHECK is a backstop, not the control -- the control is that no
submit function is imported here or reachable from what is.

IT DOES NOT TOUCH RN1-SEEDED MANAGEMENT. Different experiment id,
different table, different cursor. The frozen pairing policy and the 16%
second-half trigger are not consulted, imported or read.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time

from .. import bettor_entry_execution as entryx
from .. import bettor_entry_inventory as inv
from .. import bettor_research_shadow as rsh
from .. import bettor_external_shadow as ext
from .. import bettor_fixture_metadata as fmeta_mod
from .. import bettor_fixture_store as fstore
# THE SUPPORTED PARSER FOR THIS VENUE'S CLOCK, imported rather than
# rewritten. `_parse_ts` handles the bare `Z` and the venue's variable
# fractional precision (up to nanoseconds), and refuses a naive stamp.
# A second implementation here is exactly how the first one came to be
# `float()`, which refused every real value the venue sends.
from ..bettor_market_stream import _parse_ts as _stream_parse_ts
from .. import bettor_pinnacle_devig as devig
from .. import bettor_venue_mapping as vmap
from .. import bettor_venue_settlement as vset

log = logging.getLogger(__name__)

CONTROL_KEY = ext.CONTROL_KEY
ENV_FLAG = "EXT_PINNACLE_SHADOW"

#: Sports this loop asks the provider for, with the family the valuation
#: source knows. Basketball and hockey are absent BECAUSE THE BOOK DOES
#: NOT QUOTE THEM on our plan (NBA 0/41, NHL 0/33, measured run
#: 35933793563) -- asking anyway would spend credits to be refused.
SPORTS = (("soccer_epl", "soccer"),
          ("soccer_mexico_ligamx", "soccer"),
          ("baseball_mlb", "baseball"))

#: One cycle per this many seconds. The provider bills per request x
#: market x region, so the cadence IS the budget: three sports at roughly
#: 18-21 credits each is ~60 credits a cycle, ~7.2k/day at 15 minutes.
CYCLE_S = 900.0

#: Hard ceiling on contracts evaluated per cycle. A loop that can grow
#: without bound is the thing the execution-discipline rule forbids.
MAX_PER_CYCLE = 40

#: The venue read is the slow part; bound it so one hanging book cannot
#: hold the cycle open.
VENUE_TIMEOUT_S = 10.0

#: The sharp books whose agreement counts toward per-outcome depth. Taken
#: from `edge/fairvalue/feed.py`'s ANCHOR_BOOKS/SHARP_BOOKS set, which was
#: built from observed production payloads. Pinnacle is the anchor and is
#: counted too -- the floor is "the anchor plus at least one more".
SHARP_BOOKS = ("pinnacle", "betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au",
               "smarkets", "matchbook", "betanysports", "lowvig")

#: WHAT PINNACLE'S h2h ACTUALLY SETTLES ON, per sport. These differ, and
#: collapsing them into one string would be the settlement mismatch the
#: valuation source refuses by name.
PINNACLE_SETTLEMENT = {
    "soccer": "REGULATION_90_PLUS_STOPPAGE_NO_EXTRA_TIME",
    "baseball": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
}

#: family -> the labels `markets.sport` actually carries. Read out of
#: `sports._RULES`, not guessed: that table is the writer's vocabulary.
VENUE_SPORT_LABELS = {
    "soccer": ("Soccer",),
    "baseball": ("MLB",),
}

R_NO_CANDIDATE_MARKETS = "NO_OPEN_VENUE_MARKETS_IN_SUPPORTED_SPORTS"
R_NO_TABLE = "EXTERNAL_VALUATIONS_TABLE_ABSENT"
R_VENUE_RULE_UNKNOWN = "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED"
R_NO_VENUE_QUOTE = "NO_CONTEMPORANEOUS_VENUE_QUOTE"
R_VENUE_QUOTE_STALE = "VENUE_QUOTE_STALE"
R_NO_DEPTH = "VENUE_ASK_HAS_NO_DEPTH"
R_PROVIDER_ERROR = "PROVIDER_REQUEST_FAILED"
# THREE REASONS THAT WERE ONE COUNTER. The 02:08:18Z production cycle
# mapped two events exactly and then reported
# `NO_CONTEMPORANEOUS_VENUE_QUOTE 2` -- which told management the stage
# refused but not which of three unrelated things was missing: a slug on
# the market row, a book read that raised, or a venue error response.
# Depth and staleness already had their own names and did not fire, so
# these three are the whole remainder.
#: How many DISTINCT venue error messages one cycle keeps. Enough to tell
#: "every contract says the same thing" from "they say different things",
#: and few enough that a heartbeat stays a heartbeat.
MAX_VENUE_ERRORS = 5

#: THE CROSSING THIS LOOP WAS MISSING. `pmus.book_read` takes the VENUE's
#: own market slug; `markets.slug` is the global catalogue's slug for the
#: same fixture, and the venue answers NotFoundError for it -- which is
#: what every read in runs 22-24 did. The repository already crosses that
#: boundary in `workers/premap.resolve`: exact keys out of `us_premap`,
#: date agreement, the venue's own side expansion, and named refusals. It
#: is reused here rather than re-implemented, because a sixth resolver
#: with its own idea of side matching is how a wrong-side trade happens.
R_NO_PREMAP = "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"
R_INTENT_NOT_LONG = "VENUE_CONTRACT_IS_NOT_LONG_ON_THE_PRICED_OUTCOME"
R_NO_SLUG = "VENUE_MARKET_ROW_HAS_NO_SLUG"
R_VENUE_READ_FAILED = "VENUE_BOOK_READ_FAILED"
R_VENUE_READ_ERROR = "VENUE_BOOK_READ_RETURNED_ERROR"

#: A venue quote older than this is not contemporaneous with a 30 s odds
#: quote. Same order of magnitude as the feed's own freshness rule,
#: deliberately: the comparison is only as fresh as its stalest side.
MAX_VENUE_QUOTE_AGE_S = 30.0


# ── control, fail closed ────────────────────────────────────────────

async def _running(conn) -> tuple:
    if str(os.environ.get(ENV_FLAG, "")).strip().lower() in ("off", "0",
                                                             "false"):
        return False, "%s is off in the environment" % ENV_FLAG
    # `ingestion_state`, the SAME table every other loop's stop lives in.
    # A second control table would mean a second place to look during an
    # incident, and the row is the authoritative stop because it is the
    # PROMPT one -- stopping this loop must never require a deploy.
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            CONTROL_KEY)
    except Exception as exc:                                   # noqa: BLE001
        return False, "CONTROL_UNREADABLE_%s" % type(exc).__name__
    if raw is None:
        return False, "CONTROL_ROW_ABSENT"
    if raw.strip().lower() != "true":
        return False, "CONTROL_ROW_NOT_TRUE"
    return True, "RUNNING"


async def _table_ready(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('public.external_valuations') IS NOT NULL"))
    except Exception:                                          # noqa: BLE001
        return False


# ── the provider read, from THIS process ────────────────────────────

async def fetch_odds(sport_key: str, *, api_key: str, timeout=20.0) -> dict:
    """One bulk h2h request. Returns the payload plus the quota headers.

    The key is passed in rather than read here so that the only place it
    is looked up is `credential_present`, and it is never interpolated
    into a log line or an exception message.
    """
    import httpx

    url = ("https://api.the-odds-api.com/v4/sports/%s/odds/" % sport_key)
    params = {"apiKey": api_key, "regions": "eu,uk,us",
              "markets": "h2h", "oddsFormat": "decimal"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        used = r.headers.get("x-requests-used")
        remaining = r.headers.get("x-requests-remaining")
        if r.status_code != 200:
            # The body can echo the query string, so it is NOT included.
            return {"ok": False, "status": r.status_code,
                    "refusal": R_PROVIDER_ERROR,
                    "credits_used": used, "credits_remaining": remaining,
                    "events": []}
        return {"ok": True, "status": 200, "events": r.json(),
                "credits_used": used, "credits_remaining": remaining,
                "received_at": time.time()}


def _read_resolution_blocking(slug: str) -> dict:
    """THE VENUE'S OWN ANSWER about whether this market has resolved.

    A slug dated yesterday and a start time in the past establish neither
    completion nor settlement -- the fixture may have been postponed,
    suspended, or simply not written back yet. `bettor_live_read` asks the
    venue's settlement endpoint first and the listing second, and returns
    five distinct statuses so PENDING, UNREADABLE and UNMATCHED can never
    be read as resolution.

    Paced like every other venue read, and never raises.
    """
    from .. import bettor_live_read as lr
    from .. import pmus
    from ..venue_pace import pace

    pace()
    try:
        client = pmus._get_client()
    except Exception as exc:                                    # noqa: BLE001
        return {"status": lr.UNREADABLE, "error": type(exc).__name__,
                "stage": "CLIENT_CONSTRUCTION", "market_slug": slug}
    try:
        return lr.read_resolution(client, slug)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": lr.UNREADABLE, "error": type(exc).__name__,
                "stage": "RESOLUTION_READ", "market_slug": slug}


#: THE VENUE'S OWN RULES PROSE, CACHED. A contract's settlement text does
#: not change minute to minute, and re-reading it every management cycle
#: would spend a paced venue call on a static string. Keyed by slug.
_RULES_CACHE: dict = {}
RULES_CACHE_TTL_S = 3600.0


def rules_cache_reset() -> None:
    """Forget the cached prose. For tests; never called in the loop."""
    _RULES_CACHE.clear()


def _read_venue_rules_blocking(slug: str, *, now=None) -> dict:
    """THE VENUE'S PUBLISHED SETTLEMENT PROSE for one contract.

    `bettor_venue_settlement.attest` could not establish the overtime rule
    and said so with the detail "no rules text exists on either side".
    That was true of what we HELD: the venue's listing carries
    `description` and `assetPriceTerms` and nobody read them. This reads
    them, paced like every other venue call and cached for an hour, and
    never raises -- an unreadable prose is a named error, not an exception
    on the decision path.
    """
    import time as _t

    from .. import bettor_live_read as lr
    from .. import pmus
    from ..venue_pace import pace

    at = float(now if now is not None else _t.time())
    hit = _RULES_CACHE.get(slug)
    if hit is not None and (at - float(hit.get("read_at") or 0.0)
                            ) <= RULES_CACHE_TTL_S:
        return dict(hit, from_cache=True)
    try:
        pace()
        client = pmus._get_client()
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "slug": slug, "error": type(exc).__name__,
                "stage": "CLIENT_CONSTRUCTION", "read_at": at,
                "from_cache": False}
    try:
        got = lr.read_rules_text(client, slug)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "slug": slug, "error": type(exc).__name__,
                "stage": "RULES_TEXT_READ", "read_at": at,
                "from_cache": False}
    got["read_at"] = at
    got["from_cache"] = False
    # Cache the ANSWER, including a named failure: a contract that
    # publishes no prose should not be re-asked every cycle either.
    _RULES_CACHE[slug] = dict(got)
    return got


async def fetch_sport_catalogue(*, api_key: str, timeout=20.0) -> dict:
    """WHICH SPORTS THE PROVIDER OFFERS AT ALL. Costs no credits.

    `/v4/sports` is not metered, and it is the difference between "this
    sport is not in OUR set" (a decision of ours) and "the provider does
    not carry it" (a fact about them). A held exposure in an uncovered
    sport needs that distinction before anyone argues about extending the
    set: the first is a choice we can revisit, the second is not.
    """
    import httpx

    url = "https://api.the-odds-api.com/v4/sports/"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params={"apiKey": api_key, "all": "true"})
        used = r.headers.get("x-requests-used")
        remaining = r.headers.get("x-requests-remaining")
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code,
                    "refusal": R_PROVIDER_ERROR, "sports": [],
                    "credits_used": used, "credits_remaining": remaining}
        rows = r.json() or []
        return {"ok": True, "status": 200,
                "sports": [{"key": x.get("key"), "group": x.get("group"),
                            "title": x.get("title"),
                            "active": x.get("active")} for x in rows],
                "metered": False,
                "credits_used": used, "credits_remaining": remaining,
                "received_at": time.time()}


async def fetch_scores(sport_key: str, *, api_key: str, timeout=20.0) -> dict:
    """DOES THIS PROVIDER GIVE OBSERVED EVENT PROGRESS?

    The second-half loss exit is blocked on exactly one missing
    capability: a timestamped progress observation (period / quarter /
    inning / clock) that is NOT derived from a scheduled start time.
    `bettor_rn1x_policy.PROGRESS_FEED_CONNECTED` is empty by construction
    and that is what keeps the exit unavailable.

    This asks the provider's own scores endpoint what it actually returns,
    so the answer is measured rather than assumed. It reports the KEYS
    present on a live event, because the question is not "is there a
    score" but "is there a PERIOD", and those are different fields with
    different consequences: a score with no period cannot locate the
    halfway point, and substituting elapsed wall-clock time for it is
    precisely what the policy forbids.

    `daysFrom=1` is required for the endpoint to include completed and
    in-play events. It costs credits like any other request.
    """
    import httpx

    url = "https://api.the-odds-api.com/v4/sports/%s/scores/" % sport_key
    params = {"apiKey": api_key, "daysFrom": "1"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        out = {"status": r.status_code, "sport": sport_key,
               "credits_used": r.headers.get("x-requests-used"),
               "credits_remaining": r.headers.get("x-requests-remaining")}
        if r.status_code != 200:
            out["ok"] = False
            return out
        events = r.json() or []
        live = [e for e in events
                if not e.get("completed") and e.get("scores")]
        # THE KEY SETS, which is the whole point. Named, not summarised.
        keys = sorted({k for e in events for k in e})
        score_keys = sorted({k for e in events
                             for s in (e.get("scores") or []) for k in s})
        progress_fields = sorted(
            k for k in keys
            if k.lower() in ("period", "quarter", "inning", "half", "clock",
                             "time_remaining", "game_clock", "status",
                             "progress", "elapsed"))
        out.update({
            "ok": True, "events": len(events),
            "in_play_with_scores": len(live),
            "event_keys": keys, "score_keys": score_keys,
            "progress_fields_present": progress_fields,
            "carries_observed_period": bool(progress_fields),
            "verdict": (
                "a progress field is present -- inspect it before "
                "connecting" if progress_fields else
                "NO period/quarter/inning/clock field. Scores alone cannot "
                "locate the halfway point, and elapsed wall-clock time is "
                "forbidden as a substitute, so this endpoint does NOT "
                "close the second-half exit blocker"),
            "sample": (dict(live[0]) if live else
                       (dict(events[0]) if events else None)),
        })
        return out


def pinnacle_h2h(event: dict, *, received_at: float) -> dict | None:
    """Pinnacle's COMPLETE h2h outcome set for one event, or None.

    Per-OUTCOME sharp depth is counted here rather than per event, which
    is the distinction `edge/fairvalue/feed.py` made after its winner's
    curse audit: a 1c edge agreed by six books is a signal, the same 1c
    from one book is a rounding error.
    """
    books = event.get("bookmakers") or []
    pin = None
    for b in books:
        if b.get("key") == devig.BOOK:
            pin = b
            break
    if pin is None:
        return None
    mkt = None
    for m in (pin.get("markets") or []):
        if m.get("key") == "h2h":
            mkt = m
            break
    if mkt is None:
        return None

    prices, depth = {}, {}
    for o in (mkt.get("outcomes") or []):
        name = o.get("name")
        if name is None or o.get("price") in (None, 0):
            continue
        prices[str(name)] = float(o["price"])
    for b in books:
        if b.get("key") not in SHARP_BOOKS:
            continue
        for m in (b.get("markets") or []):
            if m.get("key") != "h2h":
                continue
            for o in (m.get("outcomes") or []):
                n = str(o.get("name"))
                if n in prices:
                    depth[n] = depth.get(n, 0) + 1
    return {"prices": prices, "depth": depth,
            "observed_at": mkt.get("last_update") or pin.get("last_update"),
            "received_at": received_at,
            "home": event.get("home_team"), "away": event.get("away_team"),
            "event_id": event.get("id"),
            "commence_time": event.get("commence_time")}


# ── the venue side, read in the same cycle ──────────────────────────

#: A market our table still calls open, that the venue has forgotten. Run
#: 23's three probe reads all returned NotFoundError, on slugs like
#: `mlb-chc-mia-2026-09-05-total-9pt5` -- a fixture 19 days past. `closed`
#: and `resolved` are OUR flags, written by the refresher; a row it stopped
#: updating weeks ago is stale whatever those flags say. `updated_at` is
#: the honest recency signal, so the candidate set is bounded by it.
#:
#: This NARROWS the candidate set. It does not weaken any refusal and it
#: does not raise the request budget: the same LIMIT, the same
#: MAX_PER_CYCLE, fewer reads wasted on markets that cannot answer.
MARKET_STALE_AFTER_S = 2 * 24 * 3600

MARKETS_SQL = """
    SELECT condition_id, title, event_title, slug, closed, resolved,
           sport, updated_at
      FROM markets
     WHERE NOT closed AND NOT resolved
       AND sport = ANY($1::text[])
       AND updated_at >= now() - make_interval(secs => $2::float8)
     ORDER BY updated_at DESC
     LIMIT 4000
"""


#: Anything token-shaped is redacted before a venue message is stored. A
#: diagnostic is worth nothing if it cannot be shown, and an exception
#: string from an HTTP client can carry a signed URL.
_TOKENISH = re.compile(r"[A-Za-z0-9_\-]{20,}")
_SECRETISH = re.compile(r"(?i)(key|secret|token|signature|sig|auth)"
                        r"\s*[=:]\s*\S+")


def _sanitize(text: str, *, limit: int = 240) -> str:
    """Strip query strings, secrets and token-shaped runs from a message."""
    s = str(text or "")
    s = re.sub(r"\?[^\s'\"]+", "?<query-removed>", s)
    s = _SECRETISH.sub(r"\1=<redacted>", s)
    s = _TOKENISH.sub("<redacted>", s)
    return s[:limit]


def _venue_diagnostic(slug, exc, *, stage, code=None, feed=None) -> dict:
    """What a person needs to act on a venue refusal.

    The contract identifier, the stage, the venue's own code, and an HTTP
    status when the client exposes one -- `type(exc).__name__` alone was
    the reason `VENUE_BOOK_READ_RETURNED_ERROR 2` could not be acted on.
    Everything free-text goes through `_sanitize` first.
    """
    out = {"slug": slug, "stage": stage, "endpoint": "markets.book",
           "code": code, "feed": feed, "status": None, "detail": None,
           "exception": type(exc).__name__ if exc is not None else None}
    if exc is not None:
        for attr in ("status", "status_code", "code"):
            val = getattr(exc, attr, None)
            if isinstance(val, int):
                out["status"] = val
                break
        else:
            resp = getattr(exc, "response", None)
            val = getattr(resp, "status_code", None)
            if isinstance(val, int):
                out["status"] = val
        out["detail"] = _sanitize(exc)
    return out


def _read_book_blocking(slug: str) -> dict:
    """One PACED public book read, off the event loop. Never raises.

    `pmus.book_read` rather than `bbo_read`, and that choice is load
    bearing: `bbo_read` returns four keys and discards the ladders, which
    is why `shadow_market_states.available_depth` was NULL on every row
    BETTOR ever wrote and INSUFFICIENT_DEPTH fired on half its decisions.
    This comparison needs depth, so it reads the whole `marketData` and
    puts it through `bettor_book_snapshot`, which exists for exactly this.

    `venue_pace.pace` is called because this loop shares the venue budget
    with the collector, which must not be starved by it.
    """
    from .. import pmus
    from ..venue_pace import pace

    pace()
    try:
        client = pmus._get_client()
    except Exception as exc:                                    # noqa: BLE001
        return {"marketData": None, "error": type(exc).__name__,
                "diagnostic": _venue_diagnostic(
                    slug, exc, stage="CLIENT_CONSTRUCTION")}
    try:
        out = pmus.book_read(client, slug)
    except Exception as exc:                                    # noqa: BLE001
        return {"marketData": None, "error": type(exc).__name__,
                "diagnostic": _venue_diagnostic(
                    slug, exc, stage="BOOK_READ")}
    if out.get("error"):
        # book_read never raises: it NAMES the failure. Carry the name plus
        # the request context, because "the venue returned an error" is not
        # actionable and "this slug, this feed, this code" is.
        out["diagnostic"] = _venue_diagnostic(
            slug, None, stage="BOOK_READ", code=out.get("error"),
            feed=out.get("feed"))
    return out


#: Fields a live-progress observation would have to arrive in. Matched
#: case-insensitively against the KEYS of a payload, never against values:
#: the question is whether the field exists at all.
PROGRESS_FIELD_NAMES = (
    "period", "periods", "half", "halves", "quarter", "inning", "innings",
    "clock", "gameclock", "timeremaining", "elapsed", "minute", "phase",
    "gamestate", "livestate", "eventstate", "status", "state",
)


def _progress_like_keys(obj, prefix="", out=None, depth=0):
    """Every key in a payload whose NAME could carry event progress."""
    out = [] if out is None else out
    if depth > 3:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = "%s.%s" % (prefix, k) if prefix else str(k)
            if str(k).lower().replace("_", "") in PROGRESS_FIELD_NAMES:
                out.append({"path": path,
                            "value_type": type(v).__name__,
                            # The VALUE is reported only as a type and a
                            # short repr: a status string is evidence, and
                            # a full payload dump in a diagnostic is not.
                            "sample": _sanitize(repr(v), limit=60)})
            _progress_like_keys(v, path, out, depth + 1)
    elif isinstance(obj, list) and obj:
        _progress_like_keys(obj[0], "%s[0]" % prefix, out, depth + 1)
    return out


def venue_event_progress_probe(limit=1) -> dict:
    """Does the VENUE's own event payload carry event progress?

    "No code reads a period field" is an inspection. This is the
    measurement: it fetches one page of the venue's events and reports
    which of its KEYS could carry progress. The distinction the exit
    blocker turns on -- "our integrations lack the field" versus "no
    accessible source exists" -- can only be settled on the payload.
    """
    from .. import pmus
    from ..venue_pace import pace

    out = {"source": "venue", "endpoint": "events.list",
           "reader": "pmus._get_client().events.list",
           "events_seen": 0, "top_level_keys": [],
           "progress_like_keys": [], "carries_observed_period": False}
    try:
        pace()
        client = pmus._get_client()
        resp = client.events.list({"limit": int(limit)}) or {}
    except Exception as exc:                                   # noqa: BLE001
        out.update(error=type(exc).__name__,
                   detail=_sanitize(exc, limit=160),
                   verdict="the venue event list could not be read, so this "
                           "says nothing either way")
        return out
    evs = resp.get("events") or []
    out["events_seen"] = len(evs)
    if not evs:
        out["verdict"] = "the venue returned no events; nothing to inspect"
        return out
    ev = evs[0] if isinstance(evs[0], dict) else {}
    out["top_level_keys"] = sorted(str(k) for k in ev.keys())
    hits = _progress_like_keys(ev)
    out["progress_like_keys"] = hits[:12]
    # A `status`/`state` key is not a period. Only a period/half/quarter/
    # inning/clock field can locate the halfway point, so the verdict is
    # narrow on purpose.
    period_names = ("period", "periods", "half", "halves", "quarter",
                    "inning", "innings", "clock", "gameclock",
                    "timeremaining", "elapsed", "minute")
    out["carries_observed_period"] = any(
        h["path"].split(".")[-1].lower().replace("_", "") in period_names
        for h in hits)
    out["verdict"] = (
        "the venue's event payload DOES carry a period-like field -- "
        "%s -- and the exit blocker should be reconsidered against it"
        % ", ".join(h["path"] for h in hits)
        if out["carries_observed_period"] else
        ("the venue's event payload carries no period, half, quarter, "
         "inning or clock field. Combined with the odds provider's scores "
         "endpoint (measured three times, progress_fields []), NEITHER "
         "integration we hold carries the observation -- which is a "
         "different statement from 'no accessible source exists'"))
    return out


async def resolve_venue_identity(conn, *, market_row, priced_outcome):
    """The GLOBAL fixture -> the VENUE's own contract, or a named refusal.

    Reuses `workers/premap.resolve`, which is the lane the copy path
    already trusts: deterministic keys out of `us_premap`, game agreement
    on the date (including the venue's calendar-adjacent dating, which a
    naive date equality gets wrong -- the C9 board has the bookmaker's
    2026-09-10 against the venue's 2026-09-09 for one fixture), and the
    venue's own side expansion so a side cannot be inferred.

    TWO THINGS ARE CHECKED, NOT ONE. The slug, and the INTENT. `resolve`
    returns the intent for buying that contract: LONG means it pays on the
    outcome we priced, SHORT means it pays on the complement. Comparing
    p(home win) against the ask on a SHORT contract is a sign error that
    would look like a large edge, so a non-LONG intent refuses by name.
    """
    from . import premap as _premap

    out = {"ok": False, "us_market_slug": None, "intent": None,
           "refusal": None, "resolver": "workers.premap.resolve",
           "global_slug": market_row.get("slug"),
           "condition_id": market_row.get("condition_id"),
           "priced_outcome": priced_outcome}
    try:
        got = await _premap.resolve(
            conn, market_row.get("title"), market_row.get("event_title"),
            priced_outcome, market_row.get("slug"),
            condition_id=market_row.get("condition_id"))
    except Exception as exc:                                   # noqa: BLE001
        out["refusal"] = R_NO_PREMAP
        out["why"] = "the resolver raised %s" % type(exc).__name__
        return out
    if not got or not got.get("market_slug"):
        out["refusal"] = R_NO_PREMAP
        out["why"] = ("the venue's own catalogue carries no contract for "
                      "this fixture and outcome. `markets.slug` is the "
                      "GLOBAL id and the venue does not accept it")
        return out
    out["us_market_slug"] = str(got["market_slug"])
    intent = str(got.get("intent") or "")
    out["intent"] = got.get("intent")
    # BOTH INTENTS ARE VALID RESOLVED EXPOSURES.
    #
    # This used to refuse anything that was not BUY_LONG, and the refusal
    # was CORRECT while `venue_quote` could only read the offer ladder:
    # pricing p(outcome) against the long book's ask on a short leg is a
    # sign error. Run 28 refused eleven of forty-one candidates there.
    #
    # The resolver was never wrong. On the `aec-` family both sides carry
    # the same identifier and the side is the INTENT, so BUY_SHORT is the
    # resolver correctly saying "the exposure to this outcome is the short
    # leg of that market". Now that the reader consumes the ladder the
    # intent names, the refusal narrows to intents nothing can price.
    if intent not in ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"):
        out["refusal"] = R_INTENT_NOT_LONG
        out["why"] = ("the resolved contract's buy intent is %r, which "
                      "names no side this reader can consume"
                      % (got.get("intent"),))
        return out
    # ── THREE SEPARATE RELATIONSHIPS, NOT ONE INFERENCE ──────────────
    #
    # THE DEFECT THIS REPLACES, and it was mine. This wrapper did:
    #
    #     short = intent == "ORDER_INTENT_BUY_SHORT"
    #     pays_on_priced_outcome = not short
    #     payout_event = "NOT(%s)" % priced_outcome if short else ...
    #
    # which reads the payout event off the INTENT. That is wrong, and it
    # fabricates a positive edge. `premap.resolve` is asked for a specific
    # OUTCOME and returns the row matching it together with the intent
    # that BUYS it. A BUY_SHORT result for "Chicago Cubs" means Cubs is
    # the venue's SHORT side; the contract still PAYS ON CUBS. It does not
    # become NOT(Cubs).
    #
    # With p(Cubs)=0.30 and an acquisition cost of 0.40 the true edge is
    # -0.10. The old inference complemented a probability that already
    # described the selected exposure and produced +0.30.
    #
    # So the three relationships are now stated apart:
    #
    #   1 INTENT -> WHICH LADDER pays for it (short consumes the bids at
    #     1 - bid). That is all the intent decides.
    #   2 REQUESTED OUTCOME + MATCHED VENUE SIDE + settlement terms ->
    #     WHICH EVENT PAYS. The resolver matched a side FOR the requested
    #     outcome, so the payout event is that outcome.
    #   3 The probability is complemented ONLY when its source event is
    #     demonstrably the complement of the payout event.
    #
    # The resolver's own evidence is PRESERVED rather than discarded and
    # reconstructed: side_norm, identifier, matched_by and question travel
    # onto the row, so a reader can audit which venue side was chosen.
    # THE KEY NAMES THE RESOLVER ACTUALLY RETURNS.
    #
    # THE DEFECT THIS FIXES, and it was silent. This block read
    # `side_norm`, `identifier` and `question` -- none of which
    # `premap.resolve` puts in its result. It returns the side under
    # `outcome`, the venue identifier under `market_slug`, and the
    # question under `title`. So every candidate row in production carried
    #
    #     matched_side_norm  null
    #     matched_identifier null
    #     matched_question   null
    #
    # while the comment above promised the resolver's evidence was
    # "PRESERVED rather than discarded and reconstructed". It was
    # discarded. Nothing downstream read the nulls, so nothing broke and
    # nothing complained -- which is exactly why payout identity could not
    # be audited from the row it was supposed to be auditable from.
    out["matched_side_norm"] = got.get("outcome")
    out["matched_identifier"] = got.get("market_slug")
    out["matched_by"] = got.get("matched_by")
    out["matched_question"] = got.get("title")
    out["matched_league_alias"] = got.get("league_alias")
    out["resolver_asked_for"] = str(priced_outcome)

    # ── WHICH PERIOD THE VENUE CONTRACT PAYS ON, ESTABLISHED ─────────
    #
    # Before this, the lane set `contract["period"] = "FULL_GAME"` as a
    # literal and passed the same literal to the valuation, so both sides
    # of the comparison agreed on a period neither had read. The venue's
    # board carries `...-2026-09-25-i6-draw` (inning six) inside the
    # money-line family, so the exposure is real.
    # THE CATALOGUE'S OWN FIELDS, read for this contract. `premap.resolve`
    # does not return the kind or the sibling count, so they are read here
    # from the same table the resolver matched in -- one bounded query, no
    # second resolver.
    pmeta = await period_metadata(conn, out["us_market_slug"])
    period = vmap.period_of_venue_slug(
        out["us_market_slug"],
        # The catalogue's `side_norm` is preferred over the resolver's
        # returned outcome: the decomposition has to be checked against
        # the venue's own spelling, not ours.
        side=pmeta.get("side_norm") or got.get("outcome"),
        event_slug=pmeta.get("event_slug"),
        kind=pmeta.get("kind"),
        sibling_markets=pmeta.get("sibling_markets"))
    period["catalogue"] = pmeta
    out["period_evidence"] = period
    if period.get("period") != vmap.FULL_MATCH:
        # THE SPECIFIC REASON, not one blanket unknown. An unsupported kind,
        # a slug that will not decompose, and a field of entrants have three
        # different remedies and the census has to be able to tell them
        # apart.
        out["refusal"] = ((period.get("refusals") or [None])[0]
                          or vmap.R_PERIOD_UNKNOWN)
        out["why"] = period.get("why")
        out["period_evidence"] = period
        return out
    out["period"] = "FULL_GAME"
    out["period_basis"] = period["basis"]
    out["ladder_side"] = ("BID" if intent == "ORDER_INTENT_BUY_SHORT"
                          else "ASK")
    out["intent_selects"] = (
        "the ladder that supplies acquisition cost, and nothing else. It "
        "does NOT name the payout event")

    # THE PAYOUT EVENT. The resolver was asked for `priced_outcome` and
    # returned the side that buys it, so that outcome is what pays.
    out["payout_event"] = str(priced_outcome)
    out["payout_event_basis"] = (
        "RESOLVER_MATCHED_A_SIDE_FOR_THE_REQUESTED_OUTCOME")
    # THE PROBABILITY'S SOURCE EVENT is the same outcome the de-vig
    # prices, so no complement is involved and none is applied. The flag
    # stays in the vocabulary -- a genuinely complementary payout is a
    # real case -- but it is only ever set when the two events are
    # demonstrably complementary, which this path never asserts from the
    # intent.
    out["probability_event"] = str(priced_outcome)
    out["payout_is_complement"] = False
    out["complement_note"] = (
        "payout_is_complement is false here because the resolver matched "
        "a side FOR the requested outcome: the probability's event and "
        "the payout event are the SAME event. A short intent means the "
        "cost comes off the bid ladder, not that the payout inverted. "
        "Where a complement IS involved, remember it is every other "
        "outcome together -- on a three-way book NOT(home) covers away "
        "AND draw, and is not p(the other team)")
    out["ok"] = True
    return out


#: The venue's own catalogue, asked whether it lists a SEPARATE draw
#: contract for this event. Queried by `event_slug` rather than parsed out
#: of the slug's grammar: the grammar is premap's to own, and a data
#: question with a data answer cannot be wrong about a naming convention.
DRAW_SIBLING_SQL = """
    SELECT p2.market_slug
      FROM us_premap p1
      JOIN us_premap p2 ON p2.event_slug = p1.event_slug
     WHERE p1.market_slug = $1
       AND p2.market_slug <> p1.market_slug
       AND lower(p2.market_slug) LIKE '%-draw'
     LIMIT 1
"""

EVENT_ROW_SQL = """
    SELECT event_slug, team_league, sports_type, game_start, kind, side_norm
      FROM us_premap WHERE market_slug = $1 LIMIT 1
"""

#: THE STRUCTURED METADATA THE PERIOD CHECK NEEDS, in one query: the
#: catalogue's own kind, event and side for this contract, plus HOW MANY
#: contracts it publishes for that event. The count is what separates a
#: two-participant match (2 or 3) from a trophy or a conference winner
#: (one per entrant) -- by counting, never by reading a name.
PERIOD_METADATA_SQL = """
    SELECT p1.kind, p1.event_slug, p1.side_norm,
           (SELECT count(DISTINCT p2.market_slug)
              FROM us_premap p2
             WHERE p2.event_slug = p1.event_slug) AS sibling_markets
      FROM us_premap p1
     WHERE p1.market_slug = $1
     LIMIT 1
"""


async def period_metadata(conn, us_market_slug: str) -> dict:
    """The catalogue's own kind, event, side and sibling count, or empties.

    A read that FAILS leaves every field None, and the period check then
    refuses by name. Returning partial defaults would be the silent
    fallback this lane exists to avoid.
    """
    out = {"source": "us_premap", "read": False, "kind": None,
           "event_slug": None, "side_norm": None, "sibling_markets": None}
    try:
        row = await conn.fetchrow(PERIOD_METADATA_SQL, us_market_slug)
    except Exception as exc:                                   # noqa: BLE001
        out["error"] = type(exc).__name__
        return out
    if row is None:
        out["why"] = ("the venue's catalogue has no row for this market "
                      "slug, so none of its structured fields exist")
        return out
    out.update(read=True, kind=row["kind"], event_slug=row["event_slug"],
               side_norm=row["side_norm"],
               sibling_markets=row["sibling_markets"])
    return out


async def venue_settlement_evidence(conn, us_market_slug: str) -> dict:
    """What the VENUE's catalogue shows about this event's settlement.

    One fact does real work here: whether the venue lists a separate DRAW
    contract. If it does, its "will A beat B" binary cannot be paying on a
    draw, because the draw is a different contract -- which is the same
    treatment the bookmaker's 3-outcome h2h gives it. That is an
    attestation from two independent catalogues, not an assumption.
    """
    out = {"source": "us_premap", "market_slug": us_market_slug,
           "draw_contract_present": False, "draw_slug": None,
           "event_slug": None, "team_league": None, "sports_type": None,
           "game_start": None, "kind": None, "side_norm": None,
           "readable": False}
    try:
        row = await conn.fetchrow(EVENT_ROW_SQL, us_market_slug)
        if row is not None:
            out.update(readable=True, event_slug=row["event_slug"],
                       team_league=row["team_league"],
                       sports_type=row["sports_type"],
                       kind=row["kind"], side_norm=row["side_norm"],
                       game_start=(row["game_start"].isoformat()
                                   if row["game_start"] is not None else None))
        draw = await conn.fetchval(DRAW_SIBLING_SQL, us_market_slug)
        if draw:
            out.update(draw_contract_present=True, draw_slug=str(draw))
    except Exception as exc:                                   # noqa: BLE001
        out["error"] = type(exc).__name__

    # THE VENUE'S OWN PUBLISHED PROSE, FETCHED RATHER THAN ASSUMED ABSENT.
    #
    # `attest` compares the bookmaker's captured terms against
    # `rules_text`, condition by condition. This function never supplied
    # it, so every entry candidate's settlement comparison was UNKNOWN
    # for want of a read -- which is not the same as the venue publishing
    # nothing, and `bettor_live_read.read_rules_text` exists precisely
    # because that distinction was got wrong once already.
    #
    # One listing read per candidate, paced and bounded by MAX_PER_CYCLE,
    # and it never raises: a failed read leaves the prose absent and the
    # comparison UNKNOWN, which is the same conservative answer as before
    # -- it just now says which of the two happened.
    # THE READER THAT ALREADY EXISTED. I had added a second, uncached one
    # beside it -- a parallel implementation of the same venue call that
    # would spend a paced request per candidate per cycle on a string that
    # does not change. `_read_venue_rules_blocking` caches for an hour,
    # keyed by slug, and reports a named failure rather than raising.
    rules = await asyncio.to_thread(_read_venue_rules_blocking,
                                    us_market_slug)
    out["rules_text"] = rules.get("rules_text")
    out["rules_source"] = rules.get("source")
    out["rules_read"] = rules
    return out


async def venue_quote(conn, *, us_slug, intent, now, size=None):
    """Contemporaneous ACQUISITION ladder for one venue contract.

    `intent` IS THE SIDE, AND IT IS REQUIRED. This used to take an
    `outcome_index` that it accepted and never read: it returned BEST_ASK
    for the market however the caller asked. On the `aec-` family both
    sides carry the SAME identifier -- equal to the slug -- and the side
    is carried only by the order intent, so half of all candidates were
    being priced off the wrong book. Run 28 refused eleven of forty-one
    candidates for exactly that reason, correctly. A parameter that looks
    like it selects a side and does not is how that hid, so it is gone
    rather than kept and ignored.

    LONG consumes the OFFER ladder at its published price. SHORT consumes
    the BID ladder at (1 - bid), which is the conversion `pmus.slug_bid`
    settled against five live markets exact to the cent. The per-share
    arithmetic lives in `bettor_book_snapshot.cost_per_share`, extracted
    rather than imported from the execution module on purpose: this loop
    is guarded by a test asserting no order-submission path is even
    nameable from it, and a shadow loop should not reach a submit
    function transitively for one line of arithmetic. A test pins the two
    definitions equal so they cannot drift.

    A quote older than MAX_VENUE_QUOTE_AGE_S is REFUSED rather than used:
    the comparison is only as fresh as its stalest side, and pairing a 3 s
    Pinnacle price against a five-minute-old venue ask manufactures an
    edge out of the gap between them.

    The depth returned is DISPLAYED depth, which is an observation and
    explicitly not a queue position -- the snapshot module says so in the
    payload itself and `P_FILL` stays NOT_IDENTIFIED regardless.
    """
    from .. import bettor_book_snapshot as bs

    # THE VENUE'S OWN SLUG, SUPPLIED BY THE CALLER. This used to read
    # `markets.slug` -- the GLOBAL catalogue's id -- and hand it to a US
    # endpoint, which is why every read answered NotFoundError. The
    # crossing now happens once, in `resolve_venue_identity`, through the
    # resolver the copy lane uses.
    slug = str(us_slug or "")
    if not slug:
        return {"ok": False, "refusal": R_NO_SLUG,
                "why": ("no venue-native slug was supplied. A global "
                        "condition id is not a US market slug and must "
                        "never be passed as one")}
    try:
        book = await asyncio.wait_for(
            asyncio.to_thread(_read_book_blocking, slug),
            timeout=VENUE_TIMEOUT_S)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": R_VENUE_READ_FAILED,
                "why": "book read failed: %s" % type(exc).__name__,
                "exception": type(exc).__name__,
                "diagnostic": _venue_diagnostic(slug, exc,
                                                stage="BOOK_READ_AWAIT")}
    if book.get("error"):
        diag = book.get("diagnostic") or {}
        return {"ok": False, "refusal": R_VENUE_READ_ERROR,
                "why": "venue read error: %s" % book["error"],
                "venue_error": _sanitize(book["error"], limit=80),
                "diagnostic": diag}

    read_at = time.time()
    snap = bs.snapshot(book.get("marketData"), symbol=slug,
                       captured_at=read_at)
    ask = snap.get("BEST_ASK")
    if ask in (None, bs.NOT_IDENTIFIED):
        return {"ok": False, "refusal": R_NO_DEPTH,
                "why": "the book has no ask side",
                "parse_status": snap.get("PARSE_STATUS")}
    depth_raw = (snap.get("DISPLAYED_DEPTH_AT_T0") or {}).get("ask")
    try:
        depth = float(depth_raw)
    except (TypeError, ValueError):
        depth = 0.0
    if depth <= 0:
        return {"ok": False, "refusal": R_NO_DEPTH,
                "why": "the ask side shows no displayed quantity"}

    # THE VENUE'S OWN CLOCK, when it gives one. `transactTime` is the
    # venue stating when this book was true; our read time is only when we
    # asked. Falling back to our read time would make every quote look
    # fresh by construction, so the fallback is NAMED.
    # THE DEFECT THIS FIXES, measured in production on build 51f20e7.
    #
    # `float(venue_ts)` was the whole parse. The venue does not send a
    # number: `marketData.transactTime` is an ISO-8601 UTC string with a
    # bare `Z` and VARIABLE fractional precision, up to nanoseconds --
    # "2026-09-21T18:20:25.743291447Z". `float()` raises on it, so every
    # book read recorded VENUE_CLOCK_UNPARSEABLE, `venue_age_s` stayed
    # unmeasured, and `_entry_freshness` returned `fresh: null`. That is
    # UNKNOWN, and UNKNOWN blocks -- so nine positive-edge candidates were
    # refused for a clock we never read rather than a book that was stale.
    #
    # `datetime.fromisoformat` in 3.11 ALSO rejects nine fractional
    # digits, which is why the repo already has a parser for this exact
    # shape: `bettor_market_stream._parse_ts` strips the Z, truncates the
    # fraction to six digits and refuses a naive stamp. That is the
    # supported parser for this venue's clock and it is reused here rather
    # than reimplemented -- the second implementation is how the first one
    # came to be `float()`.
    #
    # THE RAW VALUE TRAVELS WITH THE VERDICT. Its type and its repr are
    # carried out so a future mismatch names itself instead of collapsing
    # into "unparseable" again.
    venue_ts = snap.get("TRANSACT_TIME")
    age, age_basis, vt = None, "VENUE_CLOCK_NOT_PROVIDED", None
    clock = {"field_path": "marketData.transactTime",
             "raw": (None if venue_ts is None else str(venue_ts)[:64]),
             "raw_type": type(venue_ts).__name__,
             "parser": "bettor_market_stream._parse_ts",
             "parser_accepts": ("ISO_8601_UTC_WITH_A_BARE_Z_AND_UP_TO_"
                                "NANOSECOND_FRACTIONAL_SECONDS")}
    if venue_ts not in (None, bs.NOT_IDENTIFIED):
        dt = _stream_parse_ts(venue_ts)
        if dt is None:
            age, age_basis, vt = None, "VENUE_CLOCK_UNPARSEABLE", None
            clock["why"] = ("the venue sent a value the supported parser "
                            "refuses. It is NOT absent, and it is NOT an "
                            "observed stale age: the age is UNMEASURED")
        else:
            vt = dt.timestamp()
            age = float(now) - vt
            age_basis = "VENUE_TRANSACT_TIME"
    else:
        clock["why"] = ("the venue supplied no transactTime, so our read "
                        "clock is the only one -- and using it would make "
                        "every book fresh by construction")
    clock["parsed_epoch_s"] = vt
    clock["age_at_read_s"] = (None if age is None else round(age, 3))
    clock["basis"] = age_basis
    if age is not None and age > MAX_VENUE_QUOTE_AGE_S:
        return {"ok": False, "refusal": R_VENUE_QUOTE_STALE,
                "age_s": age, "limit_s": MAX_VENUE_QUOTE_AGE_S,
                "age_basis": age_basis, "venue_clock": clock}

    # THE SIDE THAT ACTUALLY PAYS ON OUR OUTCOME, in cost space.
    lad = bs.acquisition_ladder(book.get("marketData"), intent=intent)
    if not lad.get("ok"):
        return {"ok": False,
                "refusal": lad.get("refusal") or R_NO_DEPTH,
                "why": ("the ladder this intent must consume is not "
                        "readable: %s" % (lad.get("book_was")
                                          or lad.get("parse_status"))),
                "acquisition": lad, "slug": slug}
    sized = (bs.fill_across_levels(lad, float(size))
             if size else None)

    return {"ok": True,
            # `ask` is kept for every existing reader and is now
            # explicitly the YES-denominated API price of the best level
            # ON THE SIDE THIS INTENT CONSUMES.
            "ask": lad["best_api_price"],
            "acquisition_price": lad["best_acquisition_price"],
            "api_price": lad["best_api_price"],
            "price_spaces": bs.PRICE_SPACES,
            "intent": intent,
            "side_consumed": lad["side_consumed"],
            "pays_on": lad["pays_on"],
            "depth": lad["displayed_depth"],
            "levels_published": lad.get("levels_published"),
            "levels_read": lad.get("levels_read"),
            "sized": sized,
            # THE WHOLE LADDER, so a caller can walk it rather than
            # guess from the best level. The entry lane's marketable
            # execution estimate needs every level's price and quantity;
            # returning only `depth` forced the loop to treat the book as
            # one price with a number beside it.
            "acquisition_ladder": lad,
            "age_s": age, "age_basis": age_basis,
            # THE CLOCK'S OWN EVIDENCE: field path, raw value, its type,
            # which parser was applied and what that parser accepts.
            "venue_clock": clock,
            # THE VENUE'S OWN INSTANT, CARRIED OUT. `age_s` is the age at
            # READ time, and a decision taken after two more network reads
            # is not that fresh. Returning the timestamp lets the freshness
            # gate re-age the book against the DECISION instant instead of
            # inheriting a number measured earlier.
            "venue_ts": vt,
            "read_at": read_at, "slug": slug,
            "displayed_depth_is_not_a_queue": True,
            # The bid is deliberately reported as None. The comparison
            # crosses the ladder this intent must cross; a resting price
            # would invent a queue position we never held.
            "bid": None}


# ── THE OUTCOME JOIN, WHICH IS WHAT MAKES CALIBRATION POSSIBLE ───────
#
# `external_valuations` records what the source SAID. Nothing was reading
# back what then HAPPENED, so `outcome_known` was false on every row ever
# written and the calibration measurement had an empty input by
# construction. `JOIN_OUTCOME` has existed since migration 103 and had no
# caller.
#
# ── TWO CONVERSIONS, AND THEY ARE NOT THE SAME CONVERSION ───────────
#
# THE DEFECT THIS REPLACES, AND IT WAS MINE. This join mapped the venue's
# settlement price to our outcome through `payout_is_complement`. That
# flag answers a question about the PROBABILITY: is the de-vig's source
# event the complement of the event the contract pays on? On this lane it
# is always FALSE, because `resolve_venue_identity` asks the resolver for
# a specific outcome and the resolver returns the side that BUYS it -- so
# the probability and the payout describe the same event, INCLUDING when
# that side is the venue's SHORT side.
#
# The settlement price is about the venue's OWN LONG (YES) side. A
# fixture whose YES settles at 1, held SHORT, pays 0. With the flag FALSE
# the old code recorded 1. That is not an approximation, it is the exact
# opposite answer, on every short leg.
#
# So the two conversions are now stated apart and applied once each:
#
#   PROBABILITY CONVERSION  payout_is_complement, applied inside
#                           `bettor_external_shadow.evaluate` at decision
#                           time. By the time a row is written, the
#                           `probability` column already describes the
#                           payout event. This join must not touch it.
#   SETTLEMENT CONVERSION   buy_intent / ladder_side, applied HERE. It
#                           says whether the exposure we hold is the
#                           venue's LONG side (pays the settlement price)
#                           or its SHORT side (pays one minus it).
#
# `buy_intent` and `ladder_side` are the VERIFIED venue-side identity:
# both are written by `resolve_venue_identity` from the resolver's own
# answer, and they agree by construction (BUY_SHORT <-> BID). The mapper
# below requires them to agree and refuses rather than guessing when they
# do not, because a disagreement means the identity is not established.
#
# ── FOUR OUTCOMES OF A READ, KEPT APART ─────────────────────────────
#
# A single `None` return once meant all four of these, and the caller
# recorded all four as VOID -- permanently, since a joined row is never
# re-read:
#
#   CONFIRMED_VOID   the venue DECLARED a void or refund in a field this
#                    reader has identified. A price of neither 0 nor 1 is
#                    NOT this: it establishes only that neither side was
#                    paid in full, which is equally consistent with a
#                    partial settlement, a scaled payout and a unit
#                    convention we have misread. That case is
#                    NEITHER_SIDE_PAID_AND_NO_REFUND_IS_ESTABLISHED and
#                    nothing is written for it.
#   NAMED_WINNER     a label, e.g. "Houston Astros". `float()` raises,
#                    which is how a resolved fixture became a void.
#   INFERRED         RESOLVED_DERIVED: closed with prices converged. The
#                    venue reported no winner; this is our inference from
#                    a price and is not settlement evidence.
#   UNPARSEABLE      a value present that is neither.
#
# VOID IS NEVER INFERRED FROM A NONBINARY VALUE. "Stakes returned" is a
# claim about what the venue DID, and a number that is not 0 or 1 is
# consistent with a void, with a partially-settled market, with a scaled
# payout, with a different unit convention and with a payload we have
# misread. A void therefore requires the venue to DECLARE one, in a field
# `_declared_void` has identified; as of 2026-09-25 the PMUS settlement
# payload carries no such field, so in practice every nonbinary price is
# an unresolved accounting state. Anything that is not a settled 0/1 or a
# declared void is reported by its own name and left unjoined.

# `outcome_basis IS NULL` is the queue condition, and it is the SAME
# condition the calibration scope uses. A row leaves this queue exactly
# when a basis is recorded for it -- a settled 0/1 or a confirmed void --
# and rows reopened by migration 118's audit re-enter it, because the
# audit cleared their basis along with their outcome.
#
# ORDERED BY WHEN THE VENUE WAS LAST ASKED, never-asked first. A fixture
# the venue reports only as a named winner is never written, so ordering
# by `decided_at` alone would let a handful of unresolvable rows occupy
# the whole per-run budget forever and starve every later fixture.
UNJOINED_SQL = """
    SELECT id, us_market_slug, buy_intent, ladder_side,
           payout_is_complement
      FROM external_valuations
     WHERE experiment_id = $1
       AND outcome_known = FALSE
       AND outcome_basis IS NULL
       AND us_market_slug IS NOT NULL
       AND decided_at < now() - interval '2 hours'
     ORDER BY settlement_read_at ASC NULLS FIRST, decided_at ASC
     LIMIT $2
"""

#: Bounded per run: each row costs one paced venue read, and the join
#: shares the venue budget with the collector and the entry loop.
MAX_JOINS_PER_RUN = 60

#: A settlement price this far from 0 or 1 is not a side paying out.
_SETTLED_EPS = 1e-6

#: How a stored outcome was established. NULL in the column means "not
#: established by this mapper", which is what keeps a row out of scope.
B_SETTLEMENT_PRICE = "VENUE_SETTLEMENT_PRICE"
B_REPORTED_OUTCOME = "VENUE_REPORTED_OUTCOME"
B_CONFIRMED_VOID = "CONFIRMED_VOID"

#: Classes that are NOT an outcome, each its own answer.
C_NAMED_WINNER = "VENUE_NAMED_A_WINNER_NOT_A_PRICE"
C_INFERRED = "INFERRED_FROM_CONVERGED_PRICES_NOT_VENUE_SETTLEMENT"
C_UNPARSEABLE = "SETTLEMENT_VALUE_UNPARSEABLE"
C_SIDE_UNKNOWN = "VENUE_SIDE_IDENTITY_NOT_ESTABLISHED"
#: A parseable price that paid neither side in full, with NO declared void
#: or refund. Not an outcome, and NOT a refund: see the long note in
#: `outcome_from_settlement`.
C_NEITHER_SIDE_PAID = "NEITHER_SIDE_PAID_AND_NO_REFUND_IS_ESTABLISHED"

#: Fields a venue response would have to carry for a refund to be
#: ESTABLISHED rather than inferred. Named rather than guessed at, so that
#: when the venue does publish one the change is a one-line addition here
#: and not a re-derivation somewhere downstream. As of 2026-09-25 the PMUS
#: settlement payload carries none of them, which is why every nonbinary
#: price is currently an unresolved accounting state.
VOID_FLAG_FIELDS = ("void", "voided", "isVoid", "is_void",
                    "refunded", "isRefunded", "is_refunded",
                    "stakeReturned", "stake_returned", "cancelled",
                    "canceled", "isCancelled")
#: Values of a `status`-like field that DECLARE a void. Matched exactly
#: after casefolding; a substring match would read "not_voided" as a void.
VOID_STATUS_FIELDS = ("settlementStatus", "settlement_status", "status",
                      "resolution", "resolutionStatus")
VOID_STATUS_VALUES = ("void", "voided", "refunded", "cancelled",
                      "canceled", "no_action", "noaction", "push")


def _declared_void(resolution) -> dict:
    """Did the venue DECLARE a void or refund, in a field we identified?

    Returns the evidence, never a bare boolean, because "no void was
    declared" and "we could not tell" must not read the same. A missing
    field is not a false: it is an absent statement, and that is exactly
    why the caller leaves the position unresolved rather than settling it.
    """
    out = {"declared": False, "field": None, "value": None,
           "fields_present": [],
           "basis": "VENUE_MUST_STATE_A_VOID_IT_IS_NEVER_INFERRED"}
    r = resolution or {}
    if not isinstance(r, dict):
        return out
    for f in VOID_FLAG_FIELDS:
        if f in r and r[f] is not None:
            out["fields_present"].append(f)
            if r[f] is True or str(r[f]).strip().casefold() in (
                    "true", "1", "yes"):
                out.update(declared=True, field=f, value=str(r[f]))
                return out
    for f in VOID_STATUS_FIELDS:
        v = r.get(f)
        if v is None:
            continue
        out["fields_present"].append(f)
        if str(v).strip().casefold() in VOID_STATUS_VALUES:
            out.update(declared=True, field=f, value=str(v))
            return out
    return out

#: The two venue sides, and which settlement value each of them pays.
SIDE_LONG = "VENUE_LONG_PAYS_THE_SETTLEMENT_PRICE"
SIDE_SHORT = "VENUE_SHORT_PAYS_ONE_MINUS_THE_SETTLEMENT_PRICE"


def venue_side_of_our_exposure(*, buy_intent, ladder_side):
    """Which side of the venue's market the held exposure IS.

    Returns `SIDE_LONG`, `SIDE_SHORT`, or None when the two independent
    statements of the identity do not agree -- which is a refusal, not a
    tie to be broken. Both fields are written together by
    `resolve_venue_identity` from one resolver answer, so disagreement
    means a row was written by something else or the writer changed.
    """
    intent = str(buy_intent or "")
    side = str(ladder_side or "").upper()
    if intent == "ORDER_INTENT_BUY_LONG" and side in ("ASK", ""):
        return SIDE_LONG
    if intent == "ORDER_INTENT_BUY_SHORT" and side in ("BID", ""):
        return SIDE_SHORT
    return None


def outcome_from_settlement(resolution, *, buy_intent, ladder_side):
    """The held exposure's 0/1 truth, or the exact reason there is none.

    `resolution` is a `bettor_live_read.read_resolution` result. Returns
    a dict -- never a bare value -- because the CALLER has to be able to
    tell a void from a label from an unreadable payload, and a single
    `None` cannot.

        {"outcome": 0|1|None, "basis": str|None, "side_map": str|None,
         "settlement_read": str|None, "class": str}
    """
    out = {"outcome": None, "basis": None, "side_map": None,
           "settlement_read": None, "class": None,
           "status": str((resolution or {}).get("status") or "")}
    side = venue_side_of_our_exposure(buy_intent=buy_intent,
                                     ladder_side=ladder_side)
    out["side_map"] = side
    if side is None:
        out["class"] = C_SIDE_UNKNOWN
        return out

    from .. import bettor_live_read as lr

    st = out["status"]
    # AN INFERENCE IS NOT A SETTLEMENT. RESOLVED_DERIVED means the market
    # closed and its prices converged; the venue named nothing. Scoring a
    # probability against our own inference would make the calibration
    # partly a measurement of our inference.
    if st == getattr(lr, "RESOLVED_DERIVED", "RESOLVED_DERIVED"):
        out["class"] = C_INFERRED
        out["settlement_read"] = (
            None if resolution.get("outcome") is None
            else str(resolution["outcome"]))
        return out
    if st != lr.RESOLVED:
        out["class"] = "NOT_RESOLVED:%s" % (st or "UNKNOWN")
        return out

    # A RESOLVED READ CARRIES EITHER A PRICE OR A NAME. The settlement
    # endpoint gives a price; the listing's reported-outcome field, if it
    # ever appears, gives whatever the venue put there.
    raw = resolution.get("settlement_price")
    if raw is None:
        raw = resolution.get("outcome")
    # THE VENUE'S OWN STRING, UNPARSED, when it gave one. `settlement_price`
    # is our float reading of it; recording the reading instead of the
    # value would lose the unit convention the raw string carries and make
    # a later recheck check our arithmetic rather than the venue's answer.
    shown = resolution.get("settlement_price_raw")
    out["settlement_read"] = (str(shown) if shown is not None
                              else (None if raw is None else str(raw)))
    try:
        sp = float(raw)
    except (TypeError, ValueError):
        # A NAME IS NOT A NUMBER, and it is not a void either. Mapping a
        # label to a side needs the venue's own outcome labels beside the
        # side we hold, which this reader does not carry -- so it is
        # reported and left for a reader that does.
        out["class"] = (C_NAMED_WINNER
                        if isinstance(raw, str) and raw.strip()
                        else C_UNPARSEABLE)
        return out

    if abs(sp - 1.0) <= _SETTLED_EPS:
        yes = 1
    elif abs(sp) <= _SETTLED_EPS:
        yes = 0
    else:
        # ── A NONBINARY PRICE IS NOT A REFUND ────────────────────────
        #
        # THE DEFECT THIS CORRECTS, AND IT WAS MINE, TWICE OVER. The
        # previous version read any parseable value other than 0 or 1 as
        # CONFIRMED_VOID, and the settlement consumer then assumed the
        # original stake came back, called the result authoritative and
        # closed the inventory. A 0.5 response with no void or refund
        # evidence anywhere produced a manufactured cash entitlement.
        #
        # 0.5 is consistent with a void, with a partially settled market,
        # with a unit convention we have misread, with a scaled payout,
        # and with a contract that does not settle in {0, 1} at all. The
        # only thing it establishes is that NEITHER SIDE was paid in
        # full, and "neither side paid in full" is not "stakes returned".
        # Calling the fee treatment conservative did not establish the
        # cash entitlement either; a conservative guess is still a guess.
        #
        # VERIFIED REFUND SEMANTICS ARE REQUIRED. Until the venue states
        # a void or a refund in a field this reader has actually
        # identified, the answer is an explicit UNRESOLVED accounting
        # state: nothing is written, no exposure is released, and the
        # position stays open and stays in the queue.
        got_void = _declared_void(resolution)
        if got_void.get("declared"):
            out["class"] = B_CONFIRMED_VOID
            out["basis"] = B_CONFIRMED_VOID
            out["void_evidence"] = got_void
            return out
        out["class"] = C_NEITHER_SIDE_PAID
        out["void_evidence"] = got_void
        out["why"] = (
            "the venue settled at %s, which paid neither side in full. "
            "That is consistent with a void, with a partial settlement, "
            "with a scaled payout and with a unit convention we have "
            "misread, and it establishes none of them. No refund is "
            "inferred and no exposure is released" % out["settlement_read"])
        return out

    out["outcome"] = (1 - yes) if side == SIDE_SHORT else yes
    out["basis"] = (B_SETTLEMENT_PRICE
                    if resolution.get("settlement_price") is not None
                    else B_REPORTED_OUTCOME)
    out["class"] = out["basis"]
    return out


#: A settled 0/1, with the provenance that makes it scorable.
JOIN_RESOLVED_SQL = """
    UPDATE external_valuations
       SET outcome_known = TRUE, outcome = $2,
           outcome_at = to_timestamp($3),
           outcome_basis = $4, outcome_side_map = $5,
           settlement_read = $6, settlement_read_at = to_timestamp($3)
     WHERE id = $1 AND outcome_known = FALSE
"""

#: A CONFIRMED void. `outcome_known` stays FALSE -- migration 103 is right
#: that a known outcome must be 0 or 1 -- and the BASIS is what takes the
#: row out of the unjoined queue and tells the evaluator to class it VOID.
JOIN_VOID_SQL = """
    UPDATE external_valuations
       SET outcome_basis = $2, outcome_side_map = $3,
           settlement_read = $4, settlement_read_at = to_timestamp($5)
     WHERE id = $1 AND outcome_known = FALSE AND outcome_basis IS NULL
"""

#: A READ THAT ESTABLISHED NOTHING. It records that the venue was asked
#: and what came back, and deliberately leaves `outcome_basis` NULL so the
#: row is neither scorable nor forgotten.
JOIN_ATTEMPT_SQL = """
    UPDATE external_valuations
       SET outcome_side_map = $2, settlement_read = $3,
           settlement_read_at = to_timestamp($4)
     WHERE id = $1 AND outcome_known = FALSE AND outcome_basis IS NULL
"""


async def join_outcomes(conn, *, limit=MAX_JOINS_PER_RUN) -> dict:
    """Read back what happened, for valuations that do not know yet.

    Never raises. Reports every class separately -- resolved, void, still
    pending, unreadable, unmatched, named-winner, inferred -- because a
    calibration that could not say how much of its record it failed to
    resolve would be reporting a sample it had not characterised, and
    because three of those classes were previously all recorded as VOID.

    ONLY TWO CLASSES ARE WRITTEN: a settled 0/1 and a confirmed void.
    Everything else is LEFT UNJOINED on purpose -- a named winner and an
    inferred resolution are readable later by a reader that carries the
    venue's outcome labels, and permanently stamping them now is exactly
    the mistake migration 118 had to reopen rows to undo.
    """
    from .. import bettor_live_read as lr

    out = {"ran": True, "examined": 0, "resolved": 0, "void": 0,
           "pending": 0, "unreadable": 0, "unmatched": 0, "errors": 0,
           "named_winner": 0, "inferred": 0, "side_unknown": 0,
           "unparseable": 0, "neither_side_paid": 0,
           "limit": int(limit), "by_status": {}, "by_class": {}}
    try:
        rows = await conn.fetch(UNJOINED_SQL, ext.EXPERIMENT_ID, int(limit))
    except Exception as exc:                                   # noqa: BLE001
        return {"ran": False, "error": "%s" % type(exc).__name__,
                "why": "the unjoined-valuation read failed"}
    out["examined"] = len(rows)
    for r in rows:
        try:
            res = await asyncio.to_thread(_read_resolution_blocking,
                                          r["us_market_slug"])
        except Exception as exc:                               # noqa: BLE001
            out["errors"] += 1
            out["by_status"]["EXC:" + type(exc).__name__] = \
                out["by_status"].get("EXC:" + type(exc).__name__, 0) + 1
            continue
        st = str(res.get("status") or "")
        out["by_status"][st] = out["by_status"].get(st, 0) + 1

        got = outcome_from_settlement(res, buy_intent=r["buy_intent"],
                                      ladder_side=r["ladder_side"])
        cls = str(got.get("class") or "UNCLASSIFIED")
        out["by_class"][cls] = out["by_class"].get(cls, 0) + 1
        at = time.time()

        if got["outcome"] is not None:
            try:
                await conn.execute(JOIN_RESOLVED_SQL, r["id"],
                                   int(got["outcome"]), at, got["basis"],
                                   got["side_map"], got["settlement_read"])
                out["resolved"] += 1
            except Exception:                                  # noqa: BLE001
                out["errors"] += 1
            continue
        if cls == B_CONFIRMED_VOID:
            try:
                await conn.execute(JOIN_VOID_SQL, r["id"], B_CONFIRMED_VOID,
                                   got["side_map"], got["settlement_read"],
                                   at)
                out["void"] += 1
            except Exception:                                  # noqa: BLE001
                out["errors"] += 1
            continue

        # NO BASIS IS WRITTEN FOR THE REST -- so they stay in scope for a
        # later read and out of scope for calibration -- but the ATTEMPT is
        # stamped, which is what stops a handful of unresolvable fixtures
        # from consuming the whole per-run budget every run.
        try:
            await conn.execute(JOIN_ATTEMPT_SQL, r["id"], got["side_map"],
                               got["settlement_read"], at)
        except Exception:                                      # noqa: BLE001
            out["errors"] += 1
        if cls == C_NEITHER_SIDE_PAID:
            out["neither_side_paid"] += 1
        elif cls == C_NAMED_WINNER:
            out["named_winner"] += 1
        elif cls == C_INFERRED:
            out["inferred"] += 1
        elif cls == C_SIDE_UNKNOWN:
            out["side_unknown"] += 1
        elif cls == C_UNPARSEABLE:
            out["unparseable"] += 1
        elif st == lr.PENDING:
            out["pending"] += 1
        elif st == lr.UNMATCHED:
            out["unmatched"] += 1
        else:
            out["unreadable"] += 1
    return out


# ── THE ENTRY LANE'S EXECUTION, SIZING AND RISK ─────────────────────
#
# Everything below composes existing engines and computes no economics of
# its own. The arithmetic lives in `bettor_entry_execution`; these are the
# adapters that fetch what it needs from this loop's own data.

FIXTURE_META_SQL = """
    SELECT phase, phase_uncovered, game_format, scheduled_innings,
           play_has_begun, event_state_raw, abstract_state, start_evidence,
           terminal_hint, game_pk, home_team, away_team, source, source_url,
           reader_version,
           official_date::text AS official_date,
           extract(epoch FROM actual_start_at)::float8 AS actual_start_at,
           to_char(retrieved_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
               AS retrieved_at
      FROM fixture_metadata WHERE condition_id = $1
"""


async def fixture_metadata_for(conn, condition_id) -> dict:
    """The persisted authoritative fixture row, or why there is none.

    THROUGH THE SHARED STORE, not a second copy of the query. The manager
    and the admin acquisition route read and write the same row through
    `bettor_fixture_store`; a private SELECT here is how the two lanes
    would drift apart.

    A FAILED READ IS NOT AN ABSENT ROW, and the store reports them
    separately: an absent row is an acquisition task, a failed read is a
    database fault.
    """
    return await fstore.read(conn, condition_id)


#: One HTTP GET per official date per cycle, at most this many dates. The
#: league's schedule endpoint is asked for a DATE, and a cycle's candidates
#: cluster on one or two of them.
FIXTURE_MAX_DATES_PER_CYCLE = 3
FIXTURE_FETCH_TIMEOUT_S = 15.0


def _official_date_candidates(commence_iso) -> list:
    """The official dates a fixture at this instant could belong to.

    A 01:40Z first pitch is a US evening game on the PREVIOUS calendar
    date, and `officialDate` is what the league calls it. So the UTC date
    and the day before are both tried, in that order, and a match is
    required to be unique within the date that produced it -- never
    stitched across the two.
    """
    at = fmeta_mod._epoch(commence_iso)
    if at is None:
        return []
    import datetime as _dt

    d = _dt.datetime.fromtimestamp(float(at), _dt.timezone.utc).date()
    return [d.isoformat(), (d - _dt.timedelta(days=1)).isoformat()]


def _fetch_schedule_blocking(date_str):
    """The league's own schedule for one date. Blocking; called in a thread.

    NO CREDENTIAL, no write, one GET. The failure is returned, not raised,
    because an unreachable league host must leave the scope UNESTABLISHED
    rather than end a cycle.
    """
    import json as _json
    import urllib.request as _ur

    url = fmeta_mod.SOURCE_URL % date_str
    try:
        req = _ur.Request(url, headers={"Accept": "application/json"})
        with _ur.urlopen(req,                               # noqa: S310
                         timeout=FIXTURE_FETCH_TIMEOUT_S) as r:
            return {"ok": True, "url": url,
                    "payload": _json.loads(r.read().decode("utf-8"))}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "url": url,
                "error": "%s: %s" % (type(exc).__name__, exc)}


async def acquire_fixture_scope(conn, *, condition_id, home, away,
                                commence_iso, now, cache, fetcher=None):
    """The scope evidence for ONE candidate's fixture, acquired if needed.

    ── THE DEFECT THIS CLOSES ───────────────────────────────────────

    `fixture_metadata` was written by an admin route invoked by hand for
    one acceptance position. Ordinary candidates had no row, so the quote
    context stayed unproven, `book_terms()` withheld the bookmaker side and
    every settlement verdict read UNKNOWN. Measured 2026-09-25: 15 of 15
    candidate rows carried `ctx - phase - fmt -` with `rules_read true`.

    ── WHAT IT DOES ─────────────────────────────────────────────────

    Reads the row; if absent, stale past `bettor_fixture_store`'s bound, or
    carrying no reported event state, fetches the league's schedule for the
    fixture's own official date, matches the game INDEPENDENTLY on the two
    team names the ODDS PROVIDER gave for this event plus that date, and
    persists the parsed evidence with its source, URL, binding and
    retrieval time. Then it RE-READS the row, so what the lane uses is what
    is persisted rather than what was computed in memory.

    ── WHAT IT WILL NOT DO ──────────────────────────────────────────

    It never infers a context from a scheduled start: the context comes
    from `fmeta_mod.context_for`, which refuses unless the league reported
    a state. It never selects a fixture without both team names and a date.
    It never widens the scope guard. Every failure is a named refusal on
    the returned row, and the caller's gate reads that refusal.
    """
    row = await fstore.read(conn, condition_id)
    need = fstore.needs_acquisition(row, now=now)
    acq = {"attempted": False, "decided": need}
    if not need.get("acquire"):
        return dict(row, acquisition=acq)
    if not (str(home or "").strip() and str(away or "").strip()):
        acq["refusal"] = "FIXTURE_BINDING_INCOMPLETE"
        acq["why"] = ("the odds provider gave no pair of team names for "
                      "this event, and a fixture is matched on the two "
                      "names and the official date")
        return dict(row, acquisition=acq)
    dates = _official_date_candidates(commence_iso)
    if not dates:
        acq["refusal"] = "COMMENCE_TIME_NOT_READABLE"
        acq["why"] = ("the event's commence time could not be read, so no "
                      "official date could be derived to ask for")
        return dict(row, acquisition=acq)

    acq["attempted"] = True
    acq["dates_tried"] = []
    fetch = fetcher or _fetch_schedule_blocking
    for date_str in dates:
        if date_str not in cache:
            if len([k for k in cache if cache[k] is not None]) >= \
                    FIXTURE_MAX_DATES_PER_CYCLE:
                acq["refusal"] = "FIXTURE_FETCH_BUDGET_SPENT"
                acq["why"] = ("this cycle has already fetched %d schedule "
                              "dates, which is the bound"
                              % FIXTURE_MAX_DATES_PER_CYCLE)
                return dict(row, acquisition=acq)
            got = await asyncio.to_thread(fetch, date_str)
            cache[date_str] = got if got.get("ok") else None
            if not got.get("ok"):
                acq.setdefault("fetch_errors", []).append(
                    {"date": date_str, "error": got.get("error"),
                     "url": got.get("url")})
        got = cache.get(date_str)
        if got is None:
            continue
        parsed = fmeta_mod.parse_games(got["payload"])
        if not parsed.get("ok"):
            acq.setdefault("parse_refusals", []).append(
                {"date": date_str, "refusal": parsed.get("refusal")})
            continue
        m = fmeta_mod.match_fixture(parsed["games"], home=home, away=away,
                                   official_date=date_str)
        acq["dates_tried"].append({"date": date_str,
                                   "games": parsed.get("total"),
                                   "matched": bool(m.get("ok")),
                                   "refusal": m.get("refusal")})
        if not m.get("ok"):
            continue
        # THE RETRIEVAL TIME IS OURS, STAMPED AT THE READ, because the row
        # is evidence and an undated observation is not evidence.
        import datetime as _dt

        at = _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        ev = fmeta_mod.evidence_from(m["game"], retrieved_at=at,
                                     source_url=got["url"],
                                     condition_id=str(condition_id))
        wrote = await fstore.upsert(conn, ev, raw_game=m["game"])
        acq["write"] = wrote
        acq["evidence_refusals"] = list(ev.get("refusals") or [])
        fresh = await fstore.read(conn, condition_id)
        return dict(fresh, acquisition=acq)
    acq["refusal"] = acq.get("refusal") or fmeta_mod.R_NO_MATCH
    acq["why"] = acq.get("why") or (
        "no schedule date returned exactly one game for %r vs %r, so the "
        "fixture is not bound and the scope stays unestablished"
        % (away, home))
    return dict(row, acquisition=acq)


def _risk_action(intent) -> str:
    """The EV action vocabulary's name for acquiring this leg.

    Not the ladder side: `BID`/`ASK` names a side of someone else's book,
    and the risk engine asks what WE are doing. Both are marketable --
    this lane crosses -- so both are TAKE_*, never MAKE_*.
    """
    return ("TAKE_NO" if str(intent) == "ORDER_INTENT_BUY_SHORT"
            else "TAKE_YES")


def _quote_epoch(quote):
    """The provider's `last_update` as epoch seconds, or None.

    THE PROVIDER STATES ISO-8601. `quote["observed_at"]` is that string;
    the de-vig parses it internally, so callers that read the field raw
    got a string. `float()` on it made the freshness gate report "one of
    the two clocks is not measured" for EVERY candidate -- a wrong refusal
    that looked like a venue problem -- and made the quote-context rule
    raise. Parsed once, here, through the odds source's own reader.
    """
    at = (quote or {}).get("observed_at")
    if at is None:
        return None
    if isinstance(at, (int, float)):
        return float(at)
    try:
        return float(devig._epoch(at))
    except (TypeError, ValueError):
        return None


TOKENS_SQL = """
    SELECT token_id, outcome, outcome_index
      FROM market_tokens WHERE condition_id = $1
     ORDER BY outcome_index
"""

R_OUTCOME_NOT_BOUND = "PAYOUT_OUTCOME_INDEX_NOT_BOUND_TO_A_TOKEN"
R_OUTCOME_DISAGREES = "PAYOUT_OUTCOME_DISAGREES_WITH_THE_VENUE_INTENT"


async def bind_payout_outcome(conn, *, condition_id, payout_event, intent):
    """Which GLOBAL token and index the held payout event actually is.

    ── WHY THIS IS NOT DERIVED FROM THE INTENT ──────────────────────
    It was: `1 if intent == BUY_SHORT else 0`. That treats the US venue's
    order intent as though it determined the GLOBAL catalogue's outcome
    ordering, and those are two different systems. `market_tokens` lists
    the outcomes with their indices as the global market defines them,
    and nothing guarantees index 0 is the side a BUY_LONG acquires --
    the ordering is the catalogue's, not the venue's.

    Under the old rule a market whose token order happened to be reversed
    would file the position under the opposite leg: the one-position index
    would collide with the other side, the settlement payout would be read
    off the wrong outcome, and every number downstream would be about a
    contract we do not hold.

    So the binding is READ, by matching the payout event's name against
    the token outcomes, and then CROSS-CHECKED against the intent: the two
    are expected to agree, and a disagreement is reported rather than
    resolved in favour of either. An unbindable outcome refuses the entry
    -- there is no default.
    """
    try:
        rows = await conn.fetch(TOKENS_SQL, str(condition_id))
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": R_OUTCOME_NOT_BOUND,
                "error": type(exc).__name__,
                "why": ("the token read failed, so which outcome this "
                        "contract pays on is unknown")}
    toks = [dict(r) for r in rows]
    if not toks:
        return {"ok": False, "refusal": R_OUTCOME_NOT_BOUND, "tokens": [],
                "why": ("the global catalogue lists no tokens for this "
                        "condition, so the payout outcome cannot be bound "
                        "to an index. It is NOT assumed to be 0")}

    want = _norm_outcome(payout_event)
    hits = [t for t in toks if _norm_outcome(t["outcome"]) == want]
    if len(hits) != 1:
        return {"ok": False, "refusal": R_OUTCOME_NOT_BOUND,
                "tokens": [t["outcome"] for t in toks],
                "payout_event": payout_event,
                "matches": len(hits),
                "why": ("the payout event matches %d of %d listed outcomes. "
                        "One and only one is a binding" % (len(hits),
                                                           len(toks)))}
    tok = hits[0]
    idx = int(tok["outcome_index"])
    # THE CROSS-CHECK. The intent implies a side under the venue's own
    # two-leg convention; if that disagrees with the catalogue's index the
    # two systems are not describing the same leg and the entry stops.
    implied = 1 if str(intent) == "ORDER_INTENT_BUY_SHORT" else 0
    out = {"ok": True, "outcome_index": idx, "token_id": tok["token_id"],
           "outcome": tok["outcome"], "outcomes_listed": len(toks),
           "intent": intent, "intent_implied_index": implied,
           "basis": "MATCHED_THE_PAYOUT_EVENT_AGAINST_market_tokens",
           "cross_check": ("AGREES" if idx == implied else "DISAGREES")}
    if idx != implied and len(toks) == 2:
        # ON A BINARY MARKET the two conventions should coincide. When they
        # do not, one of them is wrong about which leg is held, and picking
        # either would be a guess about where the money is.
        out.update(ok=False, refusal=R_OUTCOME_DISAGREES,
                   why=("the catalogue puts %r at index %d while the venue "
                        "intent %s implies index %d. On a two-outcome "
                        "market these must agree; they do not, so which "
                        "leg is held is unestablished"
                        % (tok["outcome"], idx, intent, implied)))
    return out


def _norm_outcome(name) -> str:
    """Compare outcome names without punctuation or case.

    Deliberately NOT the team-name normaliser: that one strips words like
    "city" and "united" as noise, which is right for matching a fixture
    and wrong here, where "Manchester City" and "Manchester United" are
    two outcomes of two different markets and must never collapse.
    """
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


#: The odds source's own hard rule, restated where the gate can see it.
#: `bettor_pinnacle_devig` refuses a quote older than this; the entry
#: lane's freshness gate must not be laxer than the valuation's.
PINNACLE_MAX_AGE_S = 30.0


def _entry_freshness(quote, vq, now) -> dict:
    """Both clocks, and the STALEST one governs.

    An edge computed from a 3-second Pinnacle price and a five-minute-old
    venue ask is an artefact of the gap between them. Either age being
    unmeasurable leaves the verdict UNKNOWN, which blocks -- in
    particular when the venue supplies no `transactTime`, because then
    our read time is the only clock and using it would make every quote
    look fresh by construction.
    """
    p_age = None
    at = _quote_epoch(quote)
    if at is not None:
        p_age = float(now) - at
    # BOTH CLOCKS RE-AGED AT THE DECISION INSTANT. `vq["age_s"]` is the
    # age the book had when it was READ, and the decision that follows is
    # taken after the rules read and the fixture-metadata read as well --
    # so inheriting that number would claim a freshness the decision never
    # had. The venue's own transact time is carried out of the read for
    # exactly this, and when it did not provide one there is nothing to
    # re-age from, which stays unmeasured rather than falling back to our
    # read clock.
    vt = vq.get("venue_ts")
    if vt is not None:
        v_age = float(now) - float(vt)
        v_basis = "VENUE_TRANSACT_TIME_REAGED_AT_THE_DECISION"
    else:
        v_age = None
        v_basis = vq.get("age_basis") or "VENUE_CLOCK_NOT_PROVIDED"
    # THE PROVIDER'S OWN STALENESS AND OUR PROCESSING DELAY, SEPARATED.
    #
    # `pinnacle_age_s` is the number the 30-second rule governs and it is
    # UNCHANGED. But it is a sum: how old the price already was when the
    # provider gave it to us, plus how long we then took to decide. A
    # refusal reading 58 s says nothing about which, and the remedy differs
    # -- a slow provider is a coverage fact, our own delay is ours to fix.
    # Both are computed from clocks already on the record: the provider's
    # `last_update` and the receipt stamp taken at the fetch.
    recv = (quote or {}).get("received_at")
    prov_lag = (None if (at is None or recv is None)
                else round(float(recv) - at, 3))
    our_delay = (None if recv is None else round(float(now) - float(recv), 3))
    out = {"pinnacle_age_s": (None if p_age is None else round(p_age, 3)),
           "pinnacle_limit_s": PINNACLE_MAX_AGE_S,
           "pinnacle_provider_lag_s": prov_lag,
           "pinnacle_our_processing_s": our_delay,
           "age_decomposition": ("pinnacle_age_s = provider_lag + "
                                 "our_processing, both from clocks on the "
                                 "record. The 30 s rule governs the SUM "
                                 "and is unchanged"),
           "venue_age_s": (None if v_age is None else round(float(v_age), 3)),
           "venue_age_at_read_s": vq.get("age_s"),
           "venue_limit_s": MAX_VENUE_QUOTE_AGE_S,
           "venue_age_basis": v_basis,
           "venue_clock": vq.get("venue_clock"),
           "both_reaged_at_the_decision": True,
           "stalest_governs": True}
    if p_age is None or v_age is None:
        out["fresh"] = None
        out["why"] = ("one of the two clocks is not measured (%s), so "
                      "whether this pair is contemporaneous is unknown"
                      % ("pinnacle" if p_age is None
                         else vq.get("age_basis") or "venue"))
        return out
    ok = p_age <= PINNACLE_MAX_AGE_S and float(v_age) <= MAX_VENUE_QUOTE_AGE_S
    out["fresh"] = bool(ok)
    out["why"] = ("pinnacle %.2fs/%.0fs and venue %.2fs/%.0fs"
                  % (p_age, PINNACLE_MAX_AGE_S, float(v_age),
                     MAX_VENUE_QUOTE_AGE_S))
    return out


CALIBRATION_SQL = """
    SELECT source_version, sample_size, metric, score, tolerance,
           within_tolerance, measured_by,
           to_char(measured_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS measured_at,
           to_char(window_start, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
               AS window_start,
           to_char(window_end, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS window_end
      FROM external_source_calibration
     WHERE source_version = $1
     ORDER BY measured_at DESC LIMIT 1
"""


async def source_calibration(conn, source_version) -> dict:
    """The latest calibration MEASUREMENT for the external source.

    `measured` is False when there is no row, when the table does not
    exist, or when the read fails -- and in every one of those cases the
    MODEL_TRUST_DRIFT gate stays NOT_EVALUABLE and blocks inventory. The
    three cases are distinguished in `why` because they call for different
    actions, but none of them is a pass.

    THE GATE READS A ROW, NOT A CONSTANT, and that is the point: the
    blocker is cleared by producing evidence about the source, not by
    editing a boolean in a worker.
    """
    try:
        row = await conn.fetchrow(CALIBRATION_SQL, str(source_version))
    except Exception as exc:                                   # noqa: BLE001
        return {"measured": False, "error": type(exc).__name__,
                "source_version": source_version,
                "why": ("the calibration read failed, which is not "
                        "evidence that the source is calibrated")}
    if row is None:
        return {"measured": False, "error": None,
                "source_version": source_version,
                "why": ("no calibration has been measured for %s. Its own "
                        "module says to validate it in shadow, and that "
                        "measurement has not been made" % source_version)}
    d = dict(row)
    d["measured"] = True
    d["why"] = ("%s = %.6f against a tolerance of %.6f over %d resolved "
                "observations (%s to %s), measured by %s"
                % (d["metric"], float(d["score"]), float(d["tolerance"]),
                   int(d["sample_size"]), d["window_start"], d["window_end"],
                   d["measured_by"]))
    return d


OPEN_BOOK_SQL = """
    SELECT p.condition_id,
           p.seed_basis_usd::float8 AS cost_usd,
           p.seed_qty::float8       AS qty,
           extract(epoch FROM p.decision_ts)::float8 AS opened_at,
           o.net_usd::float8        AS realized_net_usd
      FROM rn1x_positions p
      LEFT JOIN rn1x_outcomes o ON o.position_id = p.position_id
     WHERE p.experiment_id = $1
"""


async def open_shadow_book(conn, experiment_id) -> list | None:
    """The lane's own open inventory, or None when it could not be read.

    None IS NOT AN EMPTY BOOK. An unread book leaves every rail
    NOT_EVALUABLE and blocks the entry; an empty book is a measurement
    that happens to be zero. Collapsing the two would permit a trade
    because the database was down.
    """
    try:
        rows = await conn.fetch(OPEN_BOOK_SQL, str(experiment_id))
    except Exception:                                          # noqa: BLE001
        return None
    return [dict(r) for r in rows]


def _settlement_compatibility(srule) -> dict:
    """The per-condition comparison verdict, from where `attest` puts it.

    The condition-to-payout comparison lives under the `void` rule --
    that is the rule whose prose the captured terms speak to -- and the
    engine's verdict is COMPATIBLE, INCOMPATIBLE or UNKNOWN. Anything
    else, including a rule dict that predates the comparison, reads as
    NOT_ESTABLISHED, which leaves the gate NOT_EVALUABLE rather than
    quietly clear.
    """
    void = ((srule or {}).get("rules") or {}).get("void") or {}
    cmp_ = void.get("terms_comparison") or {}
    return {"compatibility": cmp_.get("verdict"),
            "mismatched_conditions": cmp_.get("mismatched_conditions"),
            "applicable_conditions": cmp_.get("applicable_conditions"),
            "unstated_conditions": cmp_.get("unstated_conditions"),
            "compared_on": void.get("compared_on")}


def _entry_plan(*, ladder, fee_fn, observation_age_s, action, condition_id,
                event_key, open_book, settlement, freshness, calibration,
                now, research_authorised=False):
    """A callable `bettor_external_shadow.evaluate` invokes once.

    It receives the fair value that function computed -- so there is
    exactly one valuation and exactly one complement inversion -- and
    returns the size, the marketable execution estimate, the price of the
    quantity actually claimed, and the risk verdict.
    """
    def plan(*, fair_value, contract):
        # ── WHAT THE RAILS STILL ALLOW, MEASURED BEFORE SIZING ───────
        #
        # Production run 71 showed every positive-edge candidate failing
        # all five exposure rails at once, because a STANDARD-dollar
        # budget reserved at the break-even limit always exceeds a
        # STANDARD-dollar rail by exactly the edge. Sizing now asks the
        # rails what is left FIRST and spends at most that. No limit
        # moves; the standard trade remains the ceiling.
        head = entryx.headroom_from_rows(
            open_book, condition_id=condition_id, event_key=event_key,
            now=now)
        est = entryx.estimate(ladder=ladder, fair_value=fair_value,
                              fee_fn=fee_fn,
                              observation_age_s=observation_age_s,
                              headroom=head)
        detail = {"execution": est, "rail_headroom": head}
        refusals = list(est.get("refusals") or [])
        if not est.get("ok"):
            # NO SIZE MEANS NO RISK QUESTION. Evaluating rails against a
            # position that was never sized would produce a verdict about
            # nothing, and a `permitted: True` from it would be the
            # placeholder returning by another door.
            detail["risk"] = {"evaluated": False,
                              "why": ("no execution estimate, so there is "
                                      "no proposed position to measure "
                                      "exposure for")}
            return {"execution_estimate": None, "size": None,
                    "ask": None, "refusals": refusals,
                    "risk": {"permitted": False,
                             "reason": "NO_SIZED_POSITION_TO_ASSESS"},
                    "detail": detail}

        # THE RESERVATION IS DELIBERATELY THE WORST CASE. Nothing fills
        # above the submitted limit, so sizing the exposure at the limit
        # reserves the most the order could possibly consume -- which is
        # the conservative thing to do against a rail. It is NOT the
        # expected acquisition cost, and the two must not be swapped:
        # reserving at the modelled walk would under-reserve, and
        # comparing edge at the limit destroys the edge (see below).
        cost = float(est["size"]) * float(est["worst_case_cost_per_contract"])
        exposure = entryx.exposure_from_rows(
            open_book, condition_id=condition_id, event_key=event_key,
            proposed_cost_usd=cost, proposed_qty=est["size"], now=now,
            proposed_cost_basis="SIZE_TIMES_WORST_CASE_COST_PER_CONTRACT")
        gates = entryx.state_from_evidence(
            freshness=freshness,
            settlement=_settlement_compatibility(settlement),
            probability=fair_value,
            # THE MEASUREMENT AS READ, not an assertion made here.
            # Production finds no row -- PINNACLE_DEVIG_V1's own note on
            # its default method is "validate in shadow" and that
            # validation has not been done -- so the gate stays
            # NOT_EVALUABLE.
            calibration=calibration)

        # ── THE UNFUNDED RESEARCH WAIVER, APPLIED ABOVE THE GATE ─────
        #
        # `state_from_evidence` IS NOT TOUCHED and MODEL_TRUST_DRIFT still
        # reads None in `gates["state"]`. The waiver returns a SEPARATE map
        # for the risk engine, keeps the original under
        # `gate_state_as_read`, and both are recorded -- so the row always
        # shows what the gate said as well as what the engine was given.
        #
        # Unauthorised, it waives nothing and the engine sees the gate map
        # unchanged, which is the production default.
        waiver = rsh.waive(gates["state"],
                           authorised_flag=research_authorised,
                           calibration=calibration)
        verdict = entryx.verdict(action, observed=exposure["observed"],
                                 state=waiver["state"])
        # THE WAIVER, CARRIED ON THE PERSISTED ROW. `detail` holds the
        # complete record, but only `execution`, `risk` and `exposure` are
        # stored as columns on the valuation -- so a later reader of the
        # database could not tell whether a decision used the waiver, which
        # is precisely the question the research lane has to answer. The
        # risk verdict is the section that CONSUMED the waived map, so the
        # record travels with it. It is a copy and an input to nothing.
        verdict = dict(
            verdict,
            research_waiver={
                "version": waiver.get("version"),
                "authorised": waiver.get("authorised"),
                "waived": list(waiver.get("waived") or []),
                "refusals": list(waiver.get("refusals") or []),
                "gate_state_as_read": waiver.get("gate_state_as_read"),
            },
            # ── THE FRESHNESS EVIDENCE, FOR THE SAME REASON ──────────
            #
            # STALE_DATA is a gate this verdict CONSUMED, and production
            # reported it failing on candidates whose Pinnacle quote was
            # 16 and 22 seconds old against a 30-second rule -- which
            # looks like a contradiction and is not one. `_entry_freshness`
            # returns `fresh: None` whenever EITHER clock is unmeasured,
            # and the venue supplies no `transactTime` on some reads, so
            # the verdict is UNKNOWN rather than STALE. Those two have
            # completely different remedies: one is our latency, the other
            # is a field the venue did not send.
            #
            # None of that was on the row, so the question could only be
            # answered by re-deriving it. It is on the row now: both ages,
            # both limits, the venue's clock basis, and the provider-lag /
            # our-processing split of the Pinnacle age.
            freshness_evidence={
                "fresh": (freshness or {}).get("fresh"),
                "why": (freshness or {}).get("why"),
                "pinnacle_age_s": (freshness or {}).get("pinnacle_age_s"),
                "pinnacle_limit_s": (freshness or {}).get("pinnacle_limit_s"),
                "pinnacle_provider_lag_s": (
                    (freshness or {}).get("pinnacle_provider_lag_s")),
                "pinnacle_our_processing_s": (
                    (freshness or {}).get("pinnacle_our_processing_s")),
                "venue_age_s": (freshness or {}).get("venue_age_s"),
                "venue_limit_s": (freshness or {}).get("venue_limit_s"),
                "venue_age_basis": (freshness or {}).get("venue_age_basis"),
                # THE RAW CLOCK VALUE AND ITS TYPE, on the row. A refusal
                # that says only "unparseable" cannot be acted on; one that
                # carries the field path, the value and the parser can.
                "venue_clock": (freshness or {}).get("venue_clock"),
                "venue_age_at_read_s": (
                    (freshness or {}).get("venue_age_at_read_s")),
                "unknown_is_not_stale": (
                    "fresh=null means a clock was not measured, so whether "
                    "the pair is contemporaneous is UNKNOWN. fresh=false "
                    "means both were measured and one was too old"),
            },
            # WHAT THE RAILS LEFT, AND WHICH ONE BOUND THE SIZE. Without
            # this a reader cannot tell a rail that refused the candidate
            # from a rail that merely sized it.
            rail_headroom=(est.get("rail_headroom") or {}).get("headroom"),
            qty_cap=est.get("qty_cap"),
            notional_reduced_by_rails=est.get("notional_reduced_by_rails"),
            policy_notional_usd=est.get("policy_notional_usd"))
        detail["research_waiver"] = waiver
        detail.update({"exposure": exposure, "gates": gates,
                       "gates_seen_by_the_engine": waiver["state"],
                       "risk": verdict, "proposed_cost_usd": round(cost, 6),
                       "proposed_cost_basis":
                           "SIZE_TIMES_WORST_CASE_COST_PER_CONTRACT",
                       "risk_action": action})
        refusals.extend(exposure.get("refusals") or [])
        # ── THE THREE PRICES, EACH TO ITS OWN CONSUMER ───────────────
        # `acquisition_cost_per_contract` is what the quantity is
        # modelled to actually cost across the levels walked: that is
        # the number the economic comparison must use, because the edge
        # is (probability - what we pay - fees).
        #
        # `submitted_limit` is the break-even price the belief implies.
        # It is the ORDER's price, and it is by construction equal to
        # fair_value - fee, so an edge computed against it is zero minus
        # rounding, always. Handing it to the gate as the "ask" is the
        # defect that produced 159 NO_ACTION_HAS_POSITIVE_NET_EDGE
        # refusals in run 48: arithmetic, not a market fact.
        #
        # `worst_case_cost_per_contract` is the reservation price above.
        return {"execution_estimate": est,
                "size": est["size"],
                "ask": est["acquisition_cost_per_contract"],
                "ask_basis": est["acquisition_cost_is"],
                "fee_per_contract": est["fee_per_contract_realised"],
                "submitted_limit": est["submitted_limit"],
                "worst_case_cost_per_contract":
                    est["worst_case_cost_per_contract"],
                "risk": verdict,
                "refusals": refusals,
                "detail": detail}
    return plan


# ── one cycle ───────────────────────────────────────────────────────

async def cycle(conn) -> dict:
    """Never raises. Returns what it did and, mostly, why it did not."""
    started = time.time()
    # ONE SCHEDULE FETCH PER OFFICIAL DATE PER CYCLE. The candidates in a
    # cycle cluster on one or two dates; asking per candidate would be the
    # same answer many times over.
    fixture_cache: dict = {}
    running, why = await _running(conn)
    if not running:
        return {"ran": False, "state": "STOPPED", "why": why}
    if not await _table_ready(conn):
        return {"ran": False, "state": "BLOCKED", "why": R_NO_TABLE}

    cred = ext.credential_present()
    if not cred["present"]:
        return {"ran": False, "state": "BLOCKED",
                "why": cred["refusal"], "credential": cred}
    api_key = os.environ["EDGE_ODDS_API_KEY"]

    from .. import bettor_fee_schedule as FEES
    from decimal import Decimal

    def fee_fn(qty, price, maker=False):
        return float(FEES.LATEST.fill_fee(
            Decimal(str(round(float(qty), 6))),
            Decimal(str(round(float(price), 6))), maker=bool(maker)))

    # TWO SPORT VOCABULARIES EXIST AND THEY DO NOT OVERLAP.
    #
    # `markets.sport` is written by `workers/metadata_refresher` via
    # `sports.classify`, whose labels are capitalised leagues:
    # 'Soccer', 'MLB', 'NBA', 'NHL', 'Tennis', ... . The family names this
    # loop and `bettor_pinnacle_devig` use ('soccer', 'baseball') come from
    # `bettor_sport_mapping`, which reads the venue's own market-type
    # prefixes. Filtering the markets table on the SECOND vocabulary
    # matched nothing at all.
    #
    # Measured, not deduced: the first armed cycle reported
    # `markets 0` with `NO_VENUE_CONTRACT_FOR_EVENT 44` -- every event
    # refused for want of a venue contract when the truth was that the
    # candidate query returned an empty set. A mapping refusal and an empty
    # candidate list are different facts, and only one of them is about
    # the mapping.
    tally: dict = {}
    # THE VENUE'S OWN ERROR TEXT, bounded. A counter says how often the
    # venue refused; only the message says whether that is an entitlement,
    # a closed market or a rate limit -- and those need different actions
    # from different people.
    venue_errors: list = []
    seen_venue_errors: set = set()
    labels = sorted({lbl for _, fam in SPORTS
                     for lbl in VENUE_SPORT_LABELS.get(fam, ())})
    markets = [dict(r) for r in await conn.fetch(MARKETS_SQL, labels, MARKET_STALE_AFTER_S)]
    # ── THE OBSERVED UNIVERSE, AND WHERE IT NARROWS ──────────────────
    #
    # `markets_considered` was ONE number over BOTH supported sports, and
    # the refusal tally was one dictionary over all of them. Neither could
    # answer the question that was actually asked: does the OTHER supported
    # sport contribute any candidate at all, and if not, at which step does
    # it stop? So the universe is counted per venue label and the early
    # gates are counted per provider sport. Reporting only -- no gate reads
    # any of this.
    universe: dict = {}
    for _m in markets:
        _lbl = str(_m.get("sport") or "UNLABELLED")
        universe[_lbl] = universe.get(_lbl, 0) + 1
    funnel: dict = {}
    if not markets:
        # SAY SO BY NAME rather than letting 44 mapping refusals imply the
        # mapper is at fault.
        tally[R_NO_CANDIDATE_MARKETS] = 1

    written = 0
    evaluated = 0
    credits = {"used": None, "remaining": None}

    # THE OPEN BOOK, READ ONCE PER CYCLE. The risk rails are measured
    # against it plus the position being proposed, so it has to be read
    # before any candidate is evaluated. None means the read FAILED, and
    # None blocks every entry this cycle rather than reading as a flat
    # book -- a database outage must not look like available headroom.
    open_book = await open_shadow_book(conn, ext.EXPERIMENT_ID)
    if open_book is None:
        tally[entryx.R_BOOK_NOT_READ] = 1
    #: What this cycle actually created, reported per entry so a reader
    #: never has to infer inventory from a refusal count.
    entries: list = []
    # THE CALIBRATION MEASUREMENT, read once per cycle. Absent, the
    # MODEL_TRUST_DRIFT gate blocks every entry -- which is the current
    # production state and is reported rather than worked around.
    calibration = await source_calibration(conn, devig.VERSION)
    # THE UNFUNDED RESEARCH LANE'S AUTHORISATION, read once per cycle from
    # its own control row. Absent or unreadable is OFF. It waives exactly
    # MODEL_TRUST_DRIFT and nothing else; see bettor_research_shadow.
    research = await rsh.authorised(conn)

    for sport_key, family in SPORTS:
        if evaluated >= MAX_PER_CYCLE:
            break
        step = funnel.setdefault(sport_key, {
            "family": family,
            "venue_labels": list(VENUE_SPORT_LABELS.get(family, ())),
            "venue_markets_open_and_fresh": sum(
                universe.get(lbl, 0)
                for lbl in VENUE_SPORT_LABELS.get(family, ())),
            "provider_events": 0, "with_pinnacle_h2h": 0,
            "mapped_to_a_venue_contract": 0, "evaluated": 0, "written": 0,
            "refusals": {}})

        def _step_refuse(code, _s=step):
            _s["refusals"][code] = _s["refusals"].get(code, 0) + 1

        got = await fetch_odds(sport_key, api_key=api_key)
        credits["used"] = got.get("credits_used") or credits["used"]
        credits["remaining"] = (got.get("credits_remaining")
                                or credits["remaining"])
        if not got.get("ok"):
            tally[R_PROVIDER_ERROR] = tally.get(R_PROVIDER_ERROR, 0) + 1
            _step_refuse(R_PROVIDER_ERROR)
            continue
        received_at = got["received_at"]
        step["provider_events"] = len(got["events"] or [])

        for event in got["events"]:
            if evaluated >= MAX_PER_CYCLE:
                break
            quote = pinnacle_h2h(event, received_at=received_at)
            if quote is None:
                tally["NO_PINNACLE_ON_EVENT"] = \
                    tally.get("NO_PINNACLE_ON_EVENT", 0) + 1
                _step_refuse("NO_PINNACLE_ON_EVENT")
                continue
            step["with_pinnacle_h2h"] += 1

            # ── the venue contract, exactly or not at all ───────────
            mapped = vmap.map_event(home=quote["home"], away=quote["away"],
                                    markets=markets)
            if not mapped["mapped"]:
                for code in mapped["refusals"]:
                    tally[code] = tally.get(code, 0) + 1
                    _step_refuse(code)
                continue
            step["mapped_to_a_venue_contract"] += 1

            # ── the venue's OWN contract, before any venue read ─────
            # Refusing here costs nothing: a fixture the venue's catalogue
            # does not carry cannot answer a book read, and asking anyway
            # spends a read the protected collector shares.
            ident = await resolve_venue_identity(
                conn, market_row=mapped["market_row"],
                priced_outcome=quote["home"])
            if not ident["ok"]:
                code = ident["refusal"] or R_NO_PREMAP
                tally[code] = tally.get(code, 0) + 1
                key = (ident.get("global_slug"), code, ident.get("intent"))
                if (key not in seen_venue_errors
                        and len(venue_errors) < MAX_VENUE_ERRORS):
                    seen_venue_errors.add(key)
                    venue_errors.append({
                        "stage": "IDENTITY_CROSSING",
                        "resolver": ident["resolver"],
                        "global_slug": ident.get("global_slug"),
                        "condition_id": ident.get("condition_id"),
                        "priced_outcome": ident.get("priced_outcome"),
                        "intent": ident.get("intent"),
                        "refusal": code,
                        "why": _sanitize(ident.get("why") or "", limit=200)})
                continue

            # THE READ CLOCK, WHICH IS NOT THE DECISION CLOCK. This
            # instant ages the book at the moment it was read. The
            # DECISION instant is taken after the venue read, the rules
            # read and the fixture-metadata read have all completed, and
            # both inputs are re-aged against THAT -- because a decision
            # stamped before several network reads claims a freshness it
            # did not have, which is the defect already repaired once in
            # the management lane.
            read_at = time.time()
            # THE INTENT IS THE SIDE. Passing it is what makes this read
            # the ladder the contract actually trades on.
            vq = await venue_quote(conn, us_slug=ident["us_market_slug"],
                                   intent=ident["intent"], now=read_at)
            if not vq.get("ok"):
                code = vq.get("refusal") or R_NO_VENUE_QUOTE
                tally[code] = tally.get(code, 0) + 1
                # THE VENUE'S OWN WORDS, kept. Run 22 named this refusal
                # `VENUE_BOOK_READ_RETURNED_ERROR 2` -- which is the right
                # counter and still not an answer: whether that is an
                # entitlement, a closed market or a rate limit decides
                # whether anyone can act on it, and the message says which.
                # Bounded, deduplicated, and slug-tagged so it identifies a
                # contract without becoming a log dump.
                diag = dict(vq.get("diagnostic") or {})
                diag["condition_id"] = mapped["condition_id"]
                diag["us_market_slug"] = ident["us_market_slug"]
                diag["refusal"] = code
                diag.setdefault("why", _sanitize(vq.get("why") or "",
                                                 limit=120))
                key = (diag.get("slug"), diag.get("code"),
                       diag.get("status"), diag.get("exception"))
                if (key not in seen_venue_errors
                        and len(venue_errors) < MAX_VENUE_ERRORS):
                    seen_venue_errors.add(key)
                    venue_errors.append(diag)
                continue

            # THE SETTLEMENT RULE, AND WHY THIS USUALLY STOPS HERE.
            # Pinnacle's rule is known per sport. The VENUE contract's
            # rule is not in our data at all, so `agrees` returns None and
            # this refuses by name. An unknown is not a match, and
            # asserting one would make every edge below unfalsifiable.
            # THE RULES, ATTESTED PER FIXTURE FROM EACH SIDE'S CATALOGUE.
            # `agrees()` answers from the module-level ATTESTED table,
            # which is empty and stays empty; `attest()` asks what THIS
            # event's evidence shows and says which class established each
            # rule. Only ATTESTING_CLASSES count -- an inference is
            # recorded and does not unblock.
            vevid = await venue_settlement_evidence(
                conn, ident["us_market_slug"])
            # THE SAME AUTHORITATIVE EVIDENCE MANAGEMENT ALREADY USES.
            #
            # The entry lane used to attest settlement with no context,
            # no scope and no event state, which means the comparison ran
            # against whichever rule set happened to be keyed by
            # ("baseball", "h2h") alone. Management does not do that: it
            # reads the persisted `fixture_metadata` row, takes the
            # competition phase and the game format from it, and derives
            # the quote's PRE_GAME/LIVE context from the ACTUAL reported
            # event state rather than a catalogue start time.
            #
            # Entry now reads the same row, through the same parser, for
            # the same reasons -- Pinnacle grades a called game one way
            # before the first pitch and the opposite way in play, and an
            # entry priced under the wrong one of those is priced against
            # a contract that does not exist. The provenance travels with
            # it: source, URL, fixture binding and retrieval time.
            # ACQUIRED IF ABSENT, not merely read. The row used to be
            # written only by a hand-invoked admin route, so ordinary
            # candidates had none and the comparison could never see our
            # own side. The lane now acquires the same evidence, through
            # the same parser, for the candidate it is evaluating.
            fmeta = await acquire_fixture_scope(
                conn, condition_id=mapped["condition_id"],
                home=quote.get("home"), away=quote.get("away"),
                commence_iso=quote.get("commence_time"),
                now=time.time(), cache=fixture_cache)
            ctx_ev = None
            if fmeta.get("read") and fmeta.get("play_has_begun") is not None:
                ctx_ev = fmeta_mod.context_for(
                    {"play_has_begun": fmeta.get("play_has_begun"),
                     "actual_start_at": fmeta.get("actual_start_at"),
                     "retrieved_at": fmeta.get("retrieved_at")},
                    observed_at=_quote_epoch(quote))
            srule = vset.attest(
                sport_family=family, market="h2h",
                venue_evidence=vevid,
                book_evidence={"outcome_names": list(quote["prices"].keys()),
                               "source": "theoddsapi:h2h:%s" % devig.BOOK},
                observed_at=_quote_epoch(quote),
                book_context=(ctx_ev or {}).get("context"),
                phase=fmeta.get("phase"),
                game_format=fmeta.get("game_format"))
            srule["book_rule"] = vset.BOOK_SETTLEMENT.get(family)
            srule["fixture_metadata"] = fmeta
            srule["quote_context_evidence"] = ctx_ev

            # ── THE DECISION INSTANT, TAKEN HERE ────────────────────
            # After the venue book read, the venue rules read and the
            # fixture-metadata read. Both clocks are re-aged against it
            # below, so a decision cannot be stamped fresher than the work
            # that produced it. `decision_lag_s` is the gap those reads
            # actually took, reported rather than absorbed.
            now = time.time()
            decision_lag_s = round(now - read_at, 3)
            # THE SPECIFIC UNMET RULES, not one blanket unknown. "The
            # settlement rules do not match" is four questions -- draw,
            # overtime, push, void -- and a census that collapses them
            # cannot tell management which one to go and establish.
            extra = ([] if srule.get("overall_established")
                     else (list(srule.get("unmet") or [])
                           or [srule.get("refusal") or R_VENUE_RULE_UNKNOWN]))

            contract = {
                "venue": "PMUS",
                # BOTH IDENTITIES, EACH FROM ITS OWN SOURCE. The global id
                # comes from the `markets` row the fixture was found in;
                # the venue-native slug comes from the venue's own
                # catalogue via premap.resolve. Neither is derived from
                # the other, which is why the basis can say BOTH.
                "condition_id": mapped["condition_id"],
                "us_market_slug": ident["us_market_slug"],
                "contract_identity_basis":
                    "BOTH_PRESENT_AND_INDEPENDENTLY_SOURCED",
                "identity_resolver": ident["resolver"],
                "buy_intent": ident["intent"],
                "selection": quote["home"],
                # WHAT THIS CONTRACT PAYS ON, carried onto the row so a
                # reader never has to infer it from the intent.
                # THE IDENTITY EVIDENCE, PRESERVED. Requested outcome,
                # the venue side actually matched, the intent, the ladder
                # it selects and the event that pays -- all on the row, so
                # payout identity is never reconstructed from the intent.
                "payout_event": ident["payout_event"],
                "payout_event_basis": ident["payout_event_basis"],
                "probability_event": ident["probability_event"],
                "resolver_asked_for": ident["resolver_asked_for"],
                "matched_side_norm": ident.get("matched_side_norm"),
                "matched_identifier": ident.get("matched_identifier"),
                "matched_by": ident.get("matched_by"),
                "ladder_side": ident["ladder_side"],
                "sport_family": family,
                "market": "h2h",
                # ESTABLISHED, NOT ASSERTED. `resolve_venue_identity`
                # refuses unless the venue slug demonstrably names the
                # whole dated fixture, so reaching here means FULL_GAME
                # was read off the venue's own identifier rather than
                # typed in. The evidence travels with it.
                "period": ident["period"],
                "period_basis": ident["period_basis"],
                "period_evidence": ident["period_evidence"],
                "line": None,
                "settlement_rule": srule["book_rule"],
                "event_key": quote["event_id"],
            }
            rec = ext.evaluate(
                contract=contract,
                quote={# THE BOOK IS DECLARED, and the source checks it.
                       # Omitting it made the valuation refuse every event
                       # with PINNACLE_NOT_IN_THIS_PAYLOAD -- correctly,
                       # since an undeclared book is exactly the silent
                       # substitution the refusal exists to catch.
                       "book": devig.BOOK,
                       # `outcomes`, which is the key the source reads.
                       # Passing `prices` produced "0 of 3 outcomes priced"
                       # -- the refusal was right and the caller was wrong.
                       "outcomes": quote["prices"],
                       "observed_at": quote["observed_at"],
                       "received_at": quote["received_at"],
                       "event_key": quote["event_id"],
                       # THE SAME ESTABLISHED PERIOD ON BOTH SIDES. When
                       # this was a literal on both, the comparison could
                       # not fail: two assertions of FULL_GAME always
                       # agree. Now the contract's period comes from the
                       # venue slug and the quote is declared to be the
                       # feed's full-match h2h, so a mismatch is visible.
                       "period": ident["period"],
                       "period_basis": "THE_FEED_H2H_MARKET_IS_FULL_MATCH",
                       "line": None,
                       "settlement_rule": srule["book_rule"]},
                # THE ACQUISITION PRICE, NOT THE API PRICE. For a LONG
                # these are the same number; for a SHORT the API price is
                # YES-denominated and the cost is its complement, and
                # passing the wrong one here is the same
                # mis-denomination the execution lane's wire-price
                # conversion exists to prevent (that module is
                # deliberately not nameable from this loop).
                market_state={"ask": vq["acquisition_price"],
                              "api_price": vq["api_price"],
                              "side_consumed": vq["side_consumed"],
                              "depth": vq["depth"],
                              "readable": True},
                # THE THREE PLACEHOLDERS ARE GONE. What stood here was
                #
                #     execution_estimate p_fill None  -> refused, honestly
                #     size               1.0          -> chosen by nobody
                #     risk               permitted    -> the risk layer
                #                                       told its answer
                #
                # and the second and third are the ones that mattered: a
                # BUY admitted on them would have been sized by a literal
                # and cleared by an assertion. All three now come from
                # `bettor_entry_execution`, which composes the frozen
                # sizing policy, the marketable-fill reconstructor and the
                # risk engine with this lane's predeclared limits. The
                # plan runs INSIDE evaluate, after the one valuation, so
                # the limit, the size and the price all describe the same
                # payout event as the probability.
                execution_plan=_entry_plan(
                    ladder=vq.get("acquisition_ladder"),
                    fee_fn=fee_fn,
                    observation_age_s=vq.get("age_s"),
                    action=_risk_action(ident["intent"]),
                    condition_id=mapped["condition_id"],
                    event_key=quote["event_id"],
                    open_book=open_book,
                    settlement=srule,
                    freshness=_entry_freshness(quote, vq, now),
                    calibration=calibration,
                    research_authorised=research.get("authorised") is True,
                    now=now),
                # Still passed, and still what the gate sees if no plan
                # is built: a missing estimate refuses by name.
                execution_estimate={"p_fill": None,
                                    "basis": "P_FILL_NOT_IDENTIFIED",
                                    "crossing": True},
                size=None,
                risk={"permitted": False,
                      "reason": "NO_EXECUTION_PLAN_WAS_BUILT"},
                fee_fn=fee_fn,
                now=now,
                outcome_books=quote["depth"].get(str(quote["home"])),
                armed=True,
                # INVERTED ONCE, INSIDE evaluate. The loop does not
                # pre-invert the probability; it states which event the
                # contract pays on and lets the one place that owns the
                # comparison do the arithmetic.
                payout_is_complement=bool(ident["payout_is_complement"]),
                extra_refusals=extra)
            evaluated += 1
            step["evaluated"] += 1
            rec["venue_quote"] = vq
            rec["mapping"] = mapped
            rec["settlement"] = srule
            # THE COMPARISON AND WHAT SCOPED IT, on the row. A verdict
            # without the fixture evidence behind it cannot be rechecked:
            # the same prose reads differently under a different phase or
            # game format, and the provenance says which one applied.
            rec["settlement_comparison"] = dict(
                _settlement_compatibility(srule),
                quote_context=(ctx_ev or {}).get("context"),
                quote_context_why=(ctx_ev or {}).get("why"),
                scope_phase=fmeta.get("phase"),
                scope_game_format=fmeta.get("game_format"),
                fixture_source=fmeta.get("source"),
                fixture_source_url=fmeta.get("source_url"),
                fixture_retrieved_at=fmeta.get("retrieved_at"),
                fixture_game_pk=fmeta.get("game_pk"),
                fixture_read=fmeta.get("read"),
                # WHETHER THE LANE ACQUIRED IT THIS CYCLE, AND WHY NOT.
                # An absent scope used to be indistinguishable from an
                # unattempted one on the row.
                fixture_acquisition=fmeta.get("acquisition"),
                fixture_event_state=fmeta.get("event_state_raw"),
                fixture_play_has_begun=fmeta.get("play_has_begun"),
                venue_rules_read=bool((vevid or {}).get("rules_text")),
                venue_rules_source=(vevid or {}).get("rules_source"))
            try:
                row_id = await ext.persist(conn, rec)
                if row_id is None:
                    # ALREADY RECORDED. The provider's quote has not moved
                    # since a previous cycle, so this is the same
                    # observation, not a new one. Counting it again is the
                    # duplicate accounting migration 105 exists to stop.
                    tally["DUPLICATE_OBSERVATION_SKIPPED"] = \
                        tally.get("DUPLICATE_OBSERVATION_SKIPPED", 0) + 1
                    continue
                written += 1
                step["written"] += 1
            except Exception as exc:                           # noqa: BLE001
                tally["PERSIST:" + type(exc).__name__] = \
                    tally.get("PERSIST:" + type(exc).__name__, 0) + 1
                continue
            key = "ADMITTED" if rec.get("admissible") else None
            if key:
                tally[key] = tally.get(key, 0) + 1
                # AN ADMITTED DECISION BECOMES INVENTORY. Writing the
                # valuation row alone is a decision record, not an entry:
                # nothing would hold a position, owe a fee, carry
                # residual quantity or have to reconcile. The rows land
                # in the same four tables the managed position uses, so
                # the existing management cycle picks this up as an
                # ordinary open position on its next pass.
                try:
                    # THE OUTCOME INDEX IS READ AND CROSS-CHECKED, never
                    # derived from the venue intent. An unbindable or
                    # disagreeing outcome refuses the inventory write: the
                    # valuation row stands, but nothing is held under an
                    # index nobody established.
                    bound = await bind_payout_outcome(
                        conn, condition_id=mapped["condition_id"],
                        payout_event=rec.get("payout_event"),
                        intent=ident["intent"])
                    rec["payout_binding"] = bound
                    if not bound.get("ok"):
                        code = bound.get("refusal") or R_OUTCOME_NOT_BOUND
                        tally[code] = tally.get(code, 0) + 1
                        entries.append({"written": False,
                                        "refusals": [code],
                                        "condition_id":
                                            mapped["condition_id"],
                                        "payout_binding": bound})
                        continue
                    # THE IDENTITY GOES ON THE POSITION, not left to be
                    # rediscovered later by matching on condition alone.
                    # `buy_intent` and `ladder_side` live on the CONTRACT
                    # (that is where `persist` reads them from), so they
                    # are lifted onto the record the writer sees, and the
                    # valuation row id is the link back to the decision
                    # that admitted this. See migration 119.
                    rec["valuation_row_id"] = row_id
                    rec["buy_intent"] = contract.get("buy_intent")
                    rec["ladder_side"] = contract.get("ladder_side")
                    rec["us_market_slug"] = contract.get("us_market_slug")
                    plan = inv.plan_entry(
                        rec, now=now,
                        outcome_index=bound["outcome_index"],
                        fee_fn=fee_fn)
                    wrote = await inv.persist_entry(conn, plan)
                    rec["inventory"] = wrote
                    entries.append({
                        "position_id": wrote.get("position_id"),
                        "decision_id": wrote.get("decision_id"),
                        "order_id": wrote.get("order_id"),
                        "written": wrote.get("written"),
                        "refusals": wrote.get("refusals"),
                        "accounting": wrote.get("accounting")})
                    if wrote.get("written"):
                        tally["ENTRY_INVENTORY_WRITTEN"] = \
                            tally.get("ENTRY_INVENTORY_WRITTEN", 0) + 1
                        # ── THE BOOK IS RESERVED AGAINST, IMMEDIATELY ──
                        #
                        # THE DEFECT THIS CLOSES. `open_book` is read once
                        # per cycle, so every candidate after the first
                        # measured its exposure against a book that did
                        # not contain the positions this cycle had just
                        # created. Two $600 entries, each admissible alone
                        # against an empty book, both cleared a $1,000
                        # combined-exposure rail -- and the rail is the
                        # one thing standing between a capped pilot and an
                        # uncapped one.
                        #
                        # A RESERVATION, NOT A RE-READ. The row was
                        # written inside a transaction that has committed,
                        # so appending it here is the same fact the next
                        # read would return, without a second round trip
                        # per candidate. It carries the COST BASIS
                        # (including fees) and the filled quantity, which
                        # are exactly what the rails are measured in.
                        acct = wrote.get("accounting") or {}
                        if open_book is not None:
                            open_book.append({
                                "condition_id": mapped["condition_id"],
                                "event_key": quote["event_id"],
                                "cost_usd": acct.get("cost_basis_usd"),
                                "qty": acct.get("filled_qty"),
                                "opened_at": now,
                                "reserved_in_this_cycle": True})
                            tally["EXPOSURE_RESERVED"] = \
                                tally.get("EXPOSURE_RESERVED", 0) + 1
                    else:
                        for code in wrote.get("refusals") or []:
                            tally[code] = tally.get(code, 0) + 1
                except Exception as exc:                       # noqa: BLE001
                    # THE VALUATION ROW IS ALREADY DOWN. An inventory
                    # write that fails must not erase the decision that
                    # preceded it, so this is counted and named rather
                    # than raised.
                    tally["ENTRY_INVENTORY:" + type(exc).__name__] = \
                        tally.get("ENTRY_INVENTORY:"
                                  + type(exc).__name__, 0) + 1
            else:
                for code in rec.get("refusals", []):
                    tally[code] = tally.get(code, 0) + 1

    # THE OUTCOME JOIN RUNS EVERY CYCLE, bounded. Collection has to
    # progress on its own: a calibration that waits for someone to
    # remember to run a backfill is a calibration that never happens.
    joined = await join_outcomes(conn)

    out = {"ran": True, "state": "LIVE",
           "experiment_id": ext.EXPERIMENT_ID,
           "outcome_join": joined,
           "evaluated": evaluated, "written": written,
           "refusals": tally, "credits": credits,
           "venue_errors": venue_errors,
           "markets_considered": len(markets),
           # WHAT WAS AVAILABLE, AND WHERE EACH SPORT STOPPED. See the
           # comment at `universe` above: one aggregate count could not say
           # whether a supported sport contributed anything.
           "venue_universe_by_label": universe,
           "funnel_by_provider_sport": funnel,
           "entries": entries,
           "source_calibration": calibration,
           "research_shadow": research,
           "open_book_rows": (None if open_book is None else len(open_book)),
           "elapsed_s": round(time.time() - started, 2),
           "order_submitted": False}
    await _heartbeat(conn, out)
    return out


HEARTBEAT_KEY = "ext_pinnacle_last_cycle"

#: THE STANDBY'S OWN KEY. A process that holds no lock writes no
#: valuations, so it has no cycle to report -- but it must still be
#: visible, because "a second process is up and waiting" is worth knowing.
#: It gets its own row. Sharing HEARTBEAT_KEY made the standby's empty
#: cycle the thing every read returned.
STANDBY_KEY = "ext_pinnacle_last_cycle_standby"


def _code_identity() -> dict:
    """WHAT CODE THE ACTIVE WRITER IS RUNNING, from the writer itself.

    A deploy id says what the service was asked to run. This says what the
    process that wrote this heartbeat is actually executing: a digest of
    this module's own source, plus the build marker when the platform sets
    one. Verifying a fix by reading the deploy id assumes the restart
    happened and the import succeeded; this does not.
    """
    import hashlib
    import inspect
    import os
    import sys

    try:
        src = inspect.getsource(sys.modules[__name__]).encode()
        digest = hashlib.sha256(src).hexdigest()[:12]
    except Exception:                                          # noqa: BLE001
        digest = None
    return {"module": __name__, "source_sha256_12": digest,
            "build": (os.getenv("RENDER_GIT_COMMIT")
                      or os.getenv("GIT_COMMIT") or None),
            "pid": os.getpid()}


async def _heartbeat(conn, out: dict, *, key: str = None) -> None:
    """PERSIST THE CYCLE SUMMARY, because most refusals never reach a row.

    `key` EXISTS SO A STANDBY CANNOT ERASE THE WRITER'S CYCLE. Run 26 read
    `markets 0, evaluated 0, venue_errors []` and
    `ANOTHER_PROCESS_HOLDS_THE_WRITER_LOCK 1` from this row -- not because
    the writer refused everything, but because the STANDBY in the API
    process rewrote this key every 60 s with an empty cycle while the real
    writer (a different pid) cycled more slowly. The measurement was of the
    process that does nothing. Standbys now write STANDBY_KEY and the
    writer's row is left alone.

    MOST of this loop's refusal counters fire BEFORE anything is
    evaluated -- no candidate markets at all, no Pinnacle on the event, no
    venue contract, an ambiguous mapping, a closed market, a segment
    contract, no slug on the market row, a failed venue read, a venue
    error, no ask depth, a stale venue quote. (A count was written here
    instead and went stale the moment one counter was split into three;
    the list is the claim, not the number.) Those candidates never reach
    `ext.evaluate`, so they never
    produce an `external_valuations` row, so the refusal census cannot see
    them. The first live run showed exactly that: `evaluated 0` with an
    empty refusal list, which reads as "nothing happened" when in fact
    every candidate was refused for a nameable reason.

    "If no candidate qualifies, report the actual refusal counts" cannot be
    satisfied by a table that only holds the candidates that got far
    enough to be scored. So the whole tally goes here, into the same
    `ingestion_state` table the other loops heartbeat into, and the
    command-centre tile reads it.
    """
    import json

    try:
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            key or HEARTBEAT_KEY, json.dumps({
                "at": time.time(),
                "writer": _code_identity(),
                "state": out.get("state"),
                "evaluated": out.get("evaluated"),
                "written": out.get("written"),
                "markets_considered": out.get("markets_considered"),
                "elapsed_s": out.get("elapsed_s"),
                "credits": out.get("credits"),
                # THE POINT OF THE WHOLE FUNCTION.
                "refusals": out.get("refusals") or {},
                # Three sports and two labels, so this stays a heartbeat.
                "venue_universe_by_label":
                    out.get("venue_universe_by_label") or {},
                "funnel_by_provider_sport":
                    out.get("funnel_by_provider_sport") or {},
                # and, for the refusals that have a message, the message.
                "venue_errors": out.get("venue_errors") or [],
            }, default=str))
    except Exception:                                          # noqa: BLE001
        # A heartbeat that cannot be written must not take the cycle down.
        log.warning("ext_pinnacle: heartbeat failed", exc_info=True)


#: How long to wait before looking at the control row again while the loop
#: is NOT running. A stopped cycle spends no provider credits and no venue
#: requests -- it reads one column -- so the full cadence would only mean
#: that arming the experiment took up to CYCLE_S to be noticed, and that a
#: STOP issued during a sleep would look like it had not worked.
IDLE_POLL_S = 60.0


#: ONE WRITER, AND ITS OWN KEY. This loop writes `external_valuations`
#: and its own heartbeat. Two instances -- two API instances, or an
#: overlapping deploy -- would each spend provider credits and each write
#: the same observation, and the one-per-observation index would turn the
#: second one's work into silent DUPLICATE_OBSERVATION_SKIPPED rows rather
#: than into an error anyone would see. The key is this loop's alone: a
#: key shared with the shadow loop would make one of them a standby of the
#: other, which is a different bug wearing the same lock.
#: rn1x_shadow holds ...032 and rn1x_learn_loop ...033.
LOCK_KEY = 7723901544120034


async def run(get_pool) -> None:
    """The long-running task. Armed from the API's startup.

    THE CADENCE DEPENDS ON WHETHER IT RAN. A cycle that actually valued
    anything waits CYCLE_S, because that interval IS the provider budget.
    A cycle that was stopped or blocked waits IDLE_POLL_S, because it
    consumed nothing and the control row is the thing it is waiting for.

    THE WRITER LOCK IS SESSION-SCOPED, so it is held on ONE connection for
    the loop's whole life. Acquiring it per cycle and returning that
    connection to the pool would release it, which is a lock that reads as
    present and enforces nothing. The contention is re-asked every
    IDLE_POLL_S so a standby can actually take over -- a standby that
    never asks again cannot.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        while not await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", LOCK_KEY):
            log.info("ext_pinnacle STANDBY: another process holds the "
                     "writer lock; writing nothing, retrying in %ss",
                     IDLE_POLL_S)
            # ITS OWN KEY. This process writes no valuations, so it has no
            # cycle; writing HEARTBEAT_KEY here erased the real writer's.
            await _heartbeat(conn, {
                "state": "STANDBY_NOT_THE_WRITER",
                "refusals": {"ANOTHER_PROCESS_HOLDS_THE_WRITER_LOCK": 1}},
                key=STANDBY_KEY)
            await asyncio.sleep(IDLE_POLL_S)
        log.info("ext_pinnacle: writer lock held (key %s)", LOCK_KEY)
        while True:
            delay = IDLE_POLL_S
            try:
                out = await cycle(conn)
                log.info("ext_pinnacle: %s", out)
                if out.get("ran"):
                    delay = CYCLE_S
            except asyncio.CancelledError:
                raise
            except Exception:                                  # noqa: BLE001
                log.warning("ext_pinnacle: cycle failed", exc_info=True)
            await asyncio.sleep(delay)
