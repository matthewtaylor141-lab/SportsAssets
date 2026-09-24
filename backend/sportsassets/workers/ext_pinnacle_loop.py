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
import time

from .. import bettor_external_shadow as ext
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

MARKETS_SQL = """
    SELECT condition_id, title, event_title, slug, closed, resolved
      FROM markets
     WHERE NOT closed AND NOT resolved
       AND sport = ANY($1::text[])
     LIMIT 4000
"""


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
        return {"marketData": None, "error": type(exc).__name__}
    try:
        return pmus.book_read(client, slug)
    except Exception as exc:                                    # noqa: BLE001
        return {"marketData": None, "error": type(exc).__name__}


async def venue_quote(conn, *, condition_id, outcome_index, now):
    """Contemporaneous ask and DISPLAYED depth for one venue contract.

    A quote older than MAX_VENUE_QUOTE_AGE_S is REFUSED rather than used:
    the comparison is only as fresh as its stalest side, and pairing a 3 s
    Pinnacle price against a five-minute-old venue ask manufactures an
    edge out of the gap between them.

    The depth returned is DISPLAYED depth, which is an observation and
    explicitly not a queue position -- the snapshot module says so in the
    payload itself and `P_FILL` stays NOT_IDENTIFIED regardless.
    """
    from .. import bettor_book_snapshot as bs

    slug = await conn.fetchval(
        "SELECT slug FROM markets WHERE condition_id = $1", condition_id)
    if not slug:
        return {"ok": False, "refusal": R_NO_SLUG,
                "why": "the market row carries no slug to read a book for"}
    try:
        book = await asyncio.wait_for(
            asyncio.to_thread(_read_book_blocking, slug),
            timeout=VENUE_TIMEOUT_S)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": R_VENUE_READ_FAILED,
                "why": "book read failed: %s" % type(exc).__name__,
                "exception": type(exc).__name__}
    if book.get("error"):
        return {"ok": False, "refusal": R_VENUE_READ_ERROR,
                "why": "venue read error: %s" % book["error"],
                "venue_error": str(book["error"])[:200]}

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
    venue_ts = snap.get("TRANSACT_TIME")
    age, age_basis = None, "VENUE_CLOCK_NOT_PROVIDED"
    try:
        if venue_ts not in (None, bs.NOT_IDENTIFIED):
            vt = float(venue_ts)
            vt = vt / 1000.0 if vt > 1e11 else vt      # ms or s
            age = float(now) - vt
            age_basis = "VENUE_TRANSACT_TIME"
    except (TypeError, ValueError):
        age, age_basis = None, "VENUE_CLOCK_UNPARSEABLE"
    if age is not None and age > MAX_VENUE_QUOTE_AGE_S:
        return {"ok": False, "refusal": R_VENUE_QUOTE_STALE,
                "age_s": age, "limit_s": MAX_VENUE_QUOTE_AGE_S,
                "age_basis": age_basis}

    return {"ok": True, "ask": float(ask), "depth": depth,
            "age_s": age, "age_basis": age_basis,
            "read_at": read_at, "slug": slug,
            "displayed_depth_is_not_a_queue": True,
            # The bid is deliberately reported as None. The comparison
            # crosses the ASK; a resting price would invent a queue
            # position we never held.
            "bid": None, "outcome_index": outcome_index}


# ── one cycle ───────────────────────────────────────────────────────

async def cycle(conn) -> dict:
    """Never raises. Returns what it did and, mostly, why it did not."""
    started = time.time()
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
    labels = sorted({lbl for _, fam in SPORTS
                     for lbl in VENUE_SPORT_LABELS.get(fam, ())})
    markets = [dict(r) for r in await conn.fetch(MARKETS_SQL, labels)]
    if not markets:
        # SAY SO BY NAME rather than letting 44 mapping refusals imply the
        # mapper is at fault.
        tally[R_NO_CANDIDATE_MARKETS] = 1

    written = 0
    evaluated = 0
    credits = {"used": None, "remaining": None}

    for sport_key, family in SPORTS:
        if evaluated >= MAX_PER_CYCLE:
            break
        got = await fetch_odds(sport_key, api_key=api_key)
        credits["used"] = got.get("credits_used") or credits["used"]
        credits["remaining"] = (got.get("credits_remaining")
                                or credits["remaining"])
        if not got.get("ok"):
            tally[R_PROVIDER_ERROR] = tally.get(R_PROVIDER_ERROR, 0) + 1
            continue
        received_at = got["received_at"]

        for event in got["events"]:
            if evaluated >= MAX_PER_CYCLE:
                break
            quote = pinnacle_h2h(event, received_at=received_at)
            if quote is None:
                tally["NO_PINNACLE_ON_EVENT"] = \
                    tally.get("NO_PINNACLE_ON_EVENT", 0) + 1
                continue

            # ── the venue contract, exactly or not at all ───────────
            mapped = vmap.map_event(home=quote["home"], away=quote["away"],
                                    markets=markets)
            if not mapped["mapped"]:
                for code in mapped["refusals"]:
                    tally[code] = tally.get(code, 0) + 1
                continue

            now = time.time()
            vq = await venue_quote(conn, condition_id=mapped["condition_id"],
                                   outcome_index=0, now=now)
            if not vq.get("ok"):
                code = vq.get("refusal") or R_NO_VENUE_QUOTE
                tally[code] = tally.get(code, 0) + 1
                continue

            # THE SETTLEMENT RULE, AND WHY THIS USUALLY STOPS HERE.
            # Pinnacle's rule is known per sport. The VENUE contract's
            # rule is not in our data at all, so `agrees` returns None and
            # this refuses by name. An unknown is not a match, and
            # asserting one would make every edge below unfalsifiable.
            srule = vset.agrees(sport_family=family, market="h2h")
            # THE SPECIFIC UNMET RULES, not one blanket unknown. "The
            # settlement rules do not match" is four questions -- draw,
            # overtime, push, void -- and a census that collapses them
            # cannot tell management which one to go and establish.
            extra = ([] if srule["agrees"] is True
                     else (list(srule.get("unmet") or [])
                           or [srule.get("refusal") or R_VENUE_RULE_UNKNOWN]))

            contract = {
                "venue": "PMUS",
                "condition_id": mapped["condition_id"],
                "selection": quote["home"],
                "sport_family": family,
                "market": "h2h",
                "period": "FULL_GAME",
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
                       "period": "FULL_GAME",
                       "line": None,
                       "settlement_rule": srule["book_rule"]},
                market_state={"ask": vq["ask"], "depth": vq["depth"],
                              "readable": True},
                execution_estimate={"p_fill": None,
                                    "basis": "P_FILL_NOT_IDENTIFIED",
                                    "crossing": True},
                size=1.0,
                risk={"permitted": True, "reason": "shadow, no capital"},
                fee_fn=fee_fn,
                now=now,
                outcome_books=quote["depth"].get(str(quote["home"])),
                armed=True,
                extra_refusals=extra)
            evaluated += 1
            rec["venue_quote"] = vq
            rec["mapping"] = mapped
            rec["settlement"] = srule
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
            except Exception as exc:                           # noqa: BLE001
                tally["PERSIST:" + type(exc).__name__] = \
                    tally.get("PERSIST:" + type(exc).__name__, 0) + 1
                continue
            key = "ADMITTED" if rec.get("admissible") else None
            if key:
                tally[key] = tally.get(key, 0) + 1
            else:
                for code in rec.get("refusals", []):
                    tally[code] = tally.get(code, 0) + 1

    out = {"ran": True, "state": "LIVE",
           "experiment_id": ext.EXPERIMENT_ID,
           "evaluated": evaluated, "written": written,
           "refusals": tally, "credits": credits,
           "markets_considered": len(markets),
           "elapsed_s": round(time.time() - started, 2),
           "order_submitted": False}
    await _heartbeat(conn, out)
    return out


HEARTBEAT_KEY = "ext_pinnacle_last_cycle"


async def _heartbeat(conn, out: dict) -> None:
    """PERSIST THE CYCLE SUMMARY, because most refusals never reach a row.

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
            HEARTBEAT_KEY, json.dumps({
                "at": time.time(),
                "state": out.get("state"),
                "evaluated": out.get("evaluated"),
                "written": out.get("written"),
                "markets_considered": out.get("markets_considered"),
                "elapsed_s": out.get("elapsed_s"),
                "credits": out.get("credits"),
                # THE POINT OF THE WHOLE FUNCTION.
                "refusals": out.get("refusals") or {},
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
            await _heartbeat(conn, {
                "state": "STANDBY_NOT_THE_WRITER",
                "refusals": {"ANOTHER_PROCESS_HOLDS_THE_WRITER_LOCK": 1}})
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
