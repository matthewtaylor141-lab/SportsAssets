"""The frozen incentive allowlist, and the manifest it is tied to.

WHAT THE GENERAL WORKER DOES, AND WHY IT CANNOT BE USED HERE.
`observation_subset()` picks a small deterministic sample from a
SEEDED PERMUTATION of whatever the venue listing returned. That is the
right design for a transport experiment -- it is chosen without
reference to any economic result -- and it is the wrong design for this
one, because the question here is about markets that carry a LIQUIDITY
INCENTIVE PROGRAMME, and a seeded sample of the whole venue will mostly
not. There is therefore NO FALLBACK: if the manifest does not yield
enough qualifying markets, this refuses. It never drops through to the
general universe, because a run that quietly watched ten arbitrary
markets would produce a file that looks exactly like the one we wanted.

THE MANIFEST IS CAPTURED, NOT ASSUMED. `capture()` issues the
discovery read through the eight-request ledger and writes down what
came back, verbatim, with a digest. `freeze()` then turns that into an
allowlist and FREEZES IT FOR THE RUN: no mid-run re-selection, ever. A
programme whose terms drift mid-run does not change the allowlist --
it VOIDS that market-date, which is a research-coverage outcome and is
kept apart from anything about trading.

PROGRAMME TERMS ARRIVE WITH DISCOVERY. `ListIncentivesResponse` carries
`programs[]`, and each programme carries `timePeriods[]` with
`programId`, `createdAt`, `rewardPool`, `discountFactor`, `targetSize`,
`period`, `start` and `end`. There is no per-market terms call, and
that single fact is why four requests can cover discovery, terms and
pagination together.

TEN MARKETS ARE NOT TEN EVENTS. The live culture block retrieved on
2026-09-22 shared ONE `programId` and ONE `eventStartTime`: those
markets are outcomes of a single event, drawing on one set of
parameters. `freeze()` therefore reports markets, programmes and events
as THREE SEPARATE COUNTS and refuses to let the first stand in for the
others. The coverage gate is on markets; it says nothing about
independence, and the returned verdict says so in words.

Run:  python -m pytest backend/tests/test_bettor_incentive_manifest.py
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, time as _time, timedelta, timezone

try:                                        # 3.9+
    from zoneinfo import ZoneInfo
except ImportError:                         # pragma: no cover
    ZoneInfo = None                         # type: ignore

from . import bettor_incentive_budget as bud

MANIFEST_VERSION = "BETTOR_INCENTIVE_MANIFEST_V1"

MANIFEST_ENV = "BETTOR_INCENTIVE_MANIFEST"
ET_DATE_ENV = "BETTOR_INCENTIVE_ET_DATE"

INCENTIVES_URL = "https://gateway.polymarket.us/v1/incentives"

# The coverage gate, as approved. Ten DISTINCT MARKETS at arm.
MIN_QUALIFYING_MARKETS = 10

# The approved TOTAL subscription cap for this experiment. It is a
# total, not an addition to something else: this mode subscribes to the
# allowlist and to nothing else at all.
WATCH_MAX = 12
WATCH_MAX_ENV = "BETTOR_INCENTIVE_WATCH_MAX"
# A CEILING ON THE OVERRIDE, so a configuration change cannot turn a
# bounded experiment into a collection programme.
WATCH_MAX_CEIL = 20

# The period a market-date is scored against.
PERIOD_DAILY = "daily_event"

OK = "OK"
M_ABSENT = "MANIFEST_ABSENT"
M_UNREADABLE = "MANIFEST_UNREADABLE"
M_MALFORMED = "MANIFEST_MALFORMED"
M_DATE_MISMATCH = "MANIFEST_DATE_MISMATCH"
M_INSUFFICIENT = "INSUFFICIENT_COVERAGE"
M_NO_PROGRAMS = "MANIFEST_HAS_NO_QUALIFYING_PROGRAMS"

REQUIRED_PROGRAM_KEYS = ("market_slug", "program_id", "created_at", "period",
                         "reward_pool", "discount_factor", "target_size")


# ── the ET window, expressed once ────────────────────────────────────

def et_window(et_date: str) -> dict:
    """`[midnight ET, next midnight ET)` for one calendar date.

    HALF-OPEN, AND THE ZONE DOES THE ARITHMETIC. A fixed -4/-5 offset
    is wrong twice a year, and on those two dates the window is 23 or
    25 hours -- neither is padded or trimmed to 24, and the number of
    scoring instants is DERIVED from the realised window rather than
    assumed to be 86,400.
    """
    if ZoneInfo is None:                    # pragma: no cover
        raise RuntimeError("zoneinfo is required for the ET window")
    tz = ZoneInfo("America/New_York")
    d = datetime.strptime(et_date, "%Y-%m-%d").date()
    start = datetime.combine(d, _time(0, 0, 0), tzinfo=tz)
    end = datetime.combine(d + timedelta(days=1), _time(0, 0, 0), tzinfo=tz)
    # THE SPAN IS MEASURED ON THE ABSOLUTE CLOCK, NOT THE WALL CLOCK.
    #
    # `(end - start).total_seconds()` is WRONG here and quietly so:
    # Python subtracts two aware datetimes naively when they carry the
    # SAME tzinfo object, so both DST dates come back as exactly 86,400
    # -- the one answer this function exists to avoid. Differencing the
    # POSIX timestamps forces the UTC conversion and yields the real
    # 23 h and 25 h. Found by asserting the DST dates rather than by
    # reading the code.
    span = end.timestamp() - start.timestamp()
    return {
        "et_date": et_date,
        "tz": "America/New_York",
        "start_iso": start.isoformat(),
        "end_iso": end.isoformat(),
        "start_epoch": start.timestamp(),
        "end_epoch": end.timestamp(),
        "span_s": span,
        # 23 h and 25 h are the DST dates, and they are facts about the
        # window rather than errors in it.
        "hours": round(span / 3600.0, 4),
        "scoring_instants": int(span),      # one per second, per R2
        "half_open": "[start, end) -- the next midnight belongs to the "
                     "next date",
    }


# ── capture ──────────────────────────────────────────────────────────

def capture(get, ledger: bud.RequestLedger, *, et_date: str,
            page_size: int = 100, max_pages: int = 4) -> dict:
    """Read `/v1/incentives` through the ledger and write down what came.

    `get(url, params) -> (status, body)` is injected so this is
    testable without a socket; production passes a thin urllib wrapper.

    EVERY PAGE COSTS A UNIT, AND SO DOES EVERY RETRY. The first attempt
    on a page is charged to `manifest`; a retry is charged to `retry`,
    so "how many pages did we read" and "what did failure cost" stay
    separate numbers. A refused reservation ends the capture -- it
    never dispatches anyway.

    UNAUTHENTICATED. This endpoint takes no credentials, and none are
    passed. `/v1/incentives/earnings` -- which IS authenticated -- is
    not read here and is not part of this run.
    """
    pages, raw_programs, errors = 0, [], []
    token = None
    for _ in range(max_pages):
        params = {"statuses": "active",
                  "program_type": "liquidityProgram",
                  "instrument_states": "INSTRUMENT_STATE_OPEN",
                  "page_size": page_size}
        if token:
            params["page_token"] = token
        body, err = None, None
        for attempt in range(2):            # first attempt, then one retry
            r = ledger.spend(bud.K_MANIFEST if attempt == 0 else bud.K_RETRY,
                             why="incentives page %d attempt %d"
                                 % (pages + 1, attempt + 1))
            if not r["ok"]:
                err = "%s: %s" % (r["verdict"], r.get("detail"))
                break
            try:
                status, body = get(INCENTIVES_URL, params)
            except Exception as exc:        # noqa: BLE001 -- named, not lost
                err, body = type(exc).__name__, None
                continue
            if status != 200:
                err, body = "HTTP %s" % status, None
                continue
            err = None
            break
        if body is None:
            errors.append({"page": pages + 1, "error": err})
            break
        pages += 1
        raw_programs.extend(list((body or {}).get("programs") or []))
        token = (body or {}).get("nextPageToken") or None
        if not token:
            break

    programs = []
    for p in raw_programs:
        programs.extend(_flatten(p, et_date))
    blob = json.dumps(raw_programs, sort_keys=True, default=str)
    return {
        "manifest": MANIFEST_VERSION,
        "manifest_id": hashlib.sha256(blob.encode()).hexdigest()[:16],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": INCENTIVES_URL,
        "source_params": {"statuses": "active",
                          "program_type": "liquidityProgram",
                          "instrument_states": "INSTRUMENT_STATE_OPEN",
                          "page_size": page_size},
        "authenticated": False,
        "pages_read": pages,
        "response_digest": "sha256:" + hashlib.sha256(blob.encode()).hexdigest(),
        "et_date": et_date,
        "window": et_window(et_date),
        "programs": programs,
        "programs_raw_count": len(raw_programs),
        "errors": errors,
        "request_report": ledger.report(),
    }


def _flatten(prog: dict, et_date: str) -> list:
    """One `IncentiveProgram` -> the daily-event periods we can score.

    A programme carries several `timePeriods`; only the daily one is in
    scope, and `status: active` on the PROGRAMME is not a statement
    about the INSTRUMENT -- `instrumentState` is, and it is carried
    through rather than assumed.
    """
    slug = prog.get("marketSlug")
    if not slug:
        return []
    out = []
    for tp in (prog.get("timePeriods") or []):
        if (tp.get("period") or "") != PERIOD_DAILY:
            continue
        out.append({
            "market_slug": slug,
            "program_id": tp.get("programId"),
            "created_at": tp.get("createdAt"),
            "period": tp.get("period"),
            "reward_pool": _f(tp.get("rewardPool")),
            "discount_factor": _f(tp.get("discountFactor")),
            "target_size": _f(tp.get("targetSize")),
            "start": tp.get("start"),
            "end": tp.get("end"),
            "status": tp.get("status"),
            "min_taker_notional": _f(tp.get("minTakerNotional")),
            # THE EVENT, carried so markets and events can be counted
            # apart. Several markets share one of these.
            "event_start_time": prog.get("eventStartTime"),
            "instrument_state": prog.get("instrumentState"),
            "category": prog.get("category"),
            "subcategory": prog.get("subcategory"),
            "et_date": et_date,
        })
    return out


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ── load / save ──────────────────────────────────────────────────────

def save(path: str, manifest: dict) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True, default=str)
    return path


def load(path: str | None = None) -> dict:
    """The manifest, or a NAMED refusal. Never an exception, never {}.

    An absent manifest is not an empty universe: it is a configuration
    that cannot start, and the difference has to survive into the log.
    """
    p = path or os.environ.get(MANIFEST_ENV) or ""
    if not str(p).strip():
        return {"ok": False, "why": M_ABSENT,
                "detail": "%s is unset; incentive observation mode needs a "
                          "captured manifest" % MANIFEST_ENV}
    if not os.path.exists(p):
        return {"ok": False, "why": M_ABSENT, "path": p,
                "detail": "no file at %s" % p}
    try:
        with open(p, encoding="utf-8") as fh:
            m = json.load(fh)
    except Exception as exc:                # noqa: BLE001
        return {"ok": False, "why": M_UNREADABLE, "path": p,
                "detail": type(exc).__name__}
    if not isinstance(m, dict) or m.get("manifest") != MANIFEST_VERSION:
        return {"ok": False, "why": M_MALFORMED, "path": p,
                "detail": "not a %s document" % MANIFEST_VERSION}
    if not isinstance(m.get("programs"), list):
        return {"ok": False, "why": M_MALFORMED, "path": p,
                "detail": "programs is not a list"}
    return {"ok": True, "why": OK, "path": p, "manifest": m}


# ── freeze ───────────────────────────────────────────────────────────

def freeze(manifest: dict, *, et_date: str | None = None,
           watch_max: int | None = None,
           min_markets: int = MIN_QUALIFYING_MARKETS) -> dict:
    """The allowlist, frozen. Markets, programmes and events counted apart.

    REFUSES RATHER THAN FALLS BACK. Three refusals are possible and
    each is a different fact: the manifest is for another date; it
    holds no qualifying programme at all; it holds some but fewer than
    the gate. None of them is answered by watching something else.
    """
    want_date = et_date or manifest.get("et_date")
    if want_date and manifest.get("et_date") and \
            manifest["et_date"] != want_date:
        return {"ok": False, "why": M_DATE_MISMATCH,
                "manifest_et_date": manifest.get("et_date"),
                "requested_et_date": want_date}

    cap = _watch_cap(watch_max)
    qualifying, rejected = [], []
    seen = set()
    for p in manifest.get("programs") or []:
        why = _disqualify(p)
        if why:
            rejected.append({"market_slug": p.get("market_slug"), "why": why})
            continue
        if p["market_slug"] in seen:
            # ONE ROW PER MARKET. A market with two daily periods would
            # otherwise be counted twice toward a gate about markets.
            rejected.append({"market_slug": p["market_slug"],
                             "why": "DUPLICATE_MARKET_IN_MANIFEST"})
            continue
        seen.add(p["market_slug"])
        qualifying.append(p)

    if not qualifying:
        return {"ok": False, "why": M_NO_PROGRAMS, "markets": 0,
                "rejected": rejected, "watch_max": cap,
                "min_markets": min_markets}

    # THE ORDER IS THE MANIFEST'S, which was fixed at capture BEFORE any
    # book was seen. Truncating by it is therefore not a choice made
    # with knowledge of outcomes; re-sorting here would be.
    kept = qualifying[:cap]
    dropped = [p["market_slug"] for p in qualifying[cap:]]

    markets = len(kept)
    programs = sorted({p.get("program_id") for p in kept if p.get("program_id")})
    events = sorted({p.get("event_start_time") for p in kept
                     if p.get("event_start_time")})

    if markets < min_markets:
        return {"ok": False, "why": M_INSUFFICIENT,
                "markets": markets, "min_markets": min_markets,
                "distinct_programs": len(programs),
                "distinct_events": len(events),
                "slugs": [p["market_slug"] for p in kept],
                "rejected": rejected, "watch_max": cap,
                "detail": "%d qualifying markets, %d required; this mode "
                          "does NOT fall back to the general universe"
                          % (markets, min_markets)}

    return {
        "ok": True, "why": OK,
        "frozen": True,
        "slugs": [p["market_slug"] for p in kept],
        "programs_by_slug": {p["market_slug"]: p for p in kept},
        # THREE COUNTS, AND THE FIRST DOES NOT IMPLY THE OTHERS.
        "markets": markets,
        "distinct_programs": len(programs),
        "distinct_events": len(events),
        "program_ids": programs,
        "event_start_times": events,
        "independence_note": (
            "%d markets, %d programme id(s), %d event start time(s). The "
            "coverage gate is on MARKETS. Markets sharing a programme id "
            "or an event start time are NOT independent statistical "
            "units, and any interval computed as though they were is "
            "invalid." % (markets, len(programs), len(events))),
        "watch_max": cap,
        "dropped_by_watch_cap": dropped,
        "rejected": rejected,
        "min_markets": min_markets,
        "et_date": manifest.get("et_date"),
        "manifest_id": manifest.get("manifest_id"),
        "response_digest": manifest.get("response_digest"),
        "no_fallback": "this mode has no general-universe fallback",
    }


def _disqualify(p: dict) -> str | None:
    for k in REQUIRED_PROGRAM_KEYS:
        if p.get(k) in (None, ""):
            return "MISSING_%s" % k.upper()
    if (p.get("period") or "") != PERIOD_DAILY:
        return "NOT_A_DAILY_PERIOD"
    if (p.get("instrument_state") or "") != "INSTRUMENT_STATE_OPEN":
        # `status: active` on the PROGRAMME is not the instrument being
        # open, and conflating them was a real error earlier.
        return "INSTRUMENT_NOT_OPEN"
    if not (p.get("reward_pool") or 0) > 0:
        return "NON_POSITIVE_REWARD_POOL"
    if not (p.get("target_size") or 0) > 0:
        return "NON_POSITIVE_TARGET_SIZE"
    df = p.get("discount_factor")
    if df is None or not (0.0 < df <= 1.0):
        return "DISCOUNT_FACTOR_OUT_OF_RANGE"
    return None


def _watch_cap(watch_max: int | None) -> int:
    if watch_max is not None:
        return max(0, min(int(watch_max), WATCH_MAX_CEIL))
    raw = os.environ.get(WATCH_MAX_ENV)
    if raw is None or not str(raw).strip():
        return WATCH_MAX
    try:
        return max(0, min(int(str(raw).strip()), WATCH_MAX_CEIL))
    except (TypeError, ValueError):
        return WATCH_MAX


# ── drift ────────────────────────────────────────────────────────────

DRIFT_NONE = "UNCHANGED"
DRIFT_TERMS = "PROGRAMME_TERMS_CHANGED"
DRIFT_IDENTITY = "PROGRAMME_IDENTITY_CHANGED"
DRIFT_GONE = "PROGRAMME_ABSENT_AT_RECHECK"

_TERMS = ("reward_pool", "discount_factor", "target_size")


def drift(frozen_program: dict, fresh_program: dict | None) -> dict:
    """Did this market's programme change under us during the run?

    THE ALLOWLIST DOES NOT MOVE. Drift voids a MARKET-DATE for research
    coverage; it never adds, removes or reorders a subscription. A
    re-priced programme is not the same programme, so a changed pool,
    discount factor or target size voids just as an identity change
    does -- and BOTH parameter sets are recorded, because "it changed"
    without saying from what to what is not evidence.
    """
    if fresh_program is None:
        return {"changed": True, "why": DRIFT_GONE,
                "before": _terms_of(frozen_program), "after": None}
    if (fresh_program.get("program_id") != frozen_program.get("program_id")
            or fresh_program.get("created_at")
            != frozen_program.get("created_at")):
        return {"changed": True, "why": DRIFT_IDENTITY,
                "before": _terms_of(frozen_program),
                "after": _terms_of(fresh_program)}
    for k in _TERMS:
        if fresh_program.get(k) != frozen_program.get(k):
            return {"changed": True, "why": DRIFT_TERMS, "field": k,
                    "before": _terms_of(frozen_program),
                    "after": _terms_of(fresh_program)}
    return {"changed": False, "why": DRIFT_NONE,
            "before": _terms_of(frozen_program)}


def _terms_of(p: dict) -> dict:
    return {k: p.get(k) for k in
            ("program_id", "created_at", "reward_pool", "discount_factor",
             "target_size", "event_start_time")}


def describe() -> dict:
    return {
        "manifest": MANIFEST_VERSION,
        "min_qualifying_markets": MIN_QUALIFYING_MARKETS,
        "watch_max": WATCH_MAX, "watch_max_ceiling": WATCH_MAX_CEIL,
        "fallback": "NONE -- this mode never falls back to the seeded "
                    "general universe",
        "terms_arrive_with_discovery": True,
        "authenticated": False,
        "counts_reported_separately": ["markets", "distinct_programs",
                                       "distinct_events"],
    }
