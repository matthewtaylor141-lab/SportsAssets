#!/usr/bin/env python3
"""BETA48 FORWARD — PMUS public market-data collector. READ ONLY. NO CREDENTIAL.

WHY A NEW COLLECTOR AND NOT THE OLD ONE.

`research/run85_pmus_collector.py` proved the capability boundary: the PMUS
REST market-data surface answers on the PUBLIC gateway with no signature at
all, so observability never needed a trading key. That boundary is carried
here verbatim and re-proved by AST scan in test_fwd_collect.py.

What it could not do is the thing the whole retrospective programme died for:

  * `discover()` reads ONE page of 100 markets. The archaeology recorded the
    consequence as `DISCOVERY_LIST_EXHAUSTED = NO` -- a PREFIX of the board,
    not the board. A prefix reintroduces exactly the selection bias that made
    the RN1 snapshot unusable for fair value.
  * Nothing ever re-read a captured market after it settled. That single
    missing step is why 30 markets had two-sided books, 10,257 had outcomes,
    and the intersection was ZERO.

This collector fixes both, and separates two sampling jobs that must not be
confused:

  BREADTH  one pass over the WHOLE board, every eligible two-outcome event,
           no scoring, no shortlist, no activity filter. The selection rule is
           "it was on the board". This feeds fair value and the settlement
           sample.
  DEPTH    dense repeated reads of a rotating cohort, for execution questions
           -- quote changes, touches, markouts. A 2-hour breadth cadence can
           never answer a 5-second markout, and pretending otherwise is how a
           fill probability gets invented.

THROUGHPUT IS MEASURED, NEVER ASSUMED. The first runs record board size,
achieved request rate and settlement arrivals. Every calendar estimate in the
programme derives from those numbers afterwards. `PMUS_SETTLEMENT_THROUGHPUT`
stays NOT_IDENTIFIED until this measures it.

CAPABILITY BOUNDARY. GET only. gateway.polymarket.us only -- the authenticated
host is not a constant in this file and there is no branch that could build an
auth header. No credential, no secret, no wallet, no signer, no SDK, no order
or cancel endpoint, no POST/PUT/PATCH/DELETE. Rate is OUR restraint, not
evidence about what the venue permits.

NO PROFITABILITY CLAIM. This file records responses and derived observations.
It computes no expectancy, no ROI, no fill probability and no fee-adjusted
figure. Fills are decided offline, later, under frozen models, and TOUCH is
never written to a FILL field.

WHAT THE FIRST LIVE RUN CORRECTED (run 35040105217, 2026-09-16 00:29Z).

That run walked 200 pages, 20,000 market rows, and selected ZERO. Two beliefs
in the first version of this file were wrong, and both are corrected here from
the captured rows rather than from reasoning:

  1. PAGINATION DID ADVANCE. 20,000 rows carried 20,000 DISTINCT slugs, zero
     duplicates. The earlier "the offset param is ignored, so pages repeat"
     diagnosis was WRONG for this endpoint. The dedupe guard is kept because a
     non-advancing cursor is still worth detecting, but it was not the defect.

  2. THERE IS NO `eventSlug` FIELD. Zero of 20,000 rows carried one, so the
     grouping-by-event step produced an EMPTY dict and discarded the board in
     silence. That was the defect.

What the rows actually are: each row is ALREADY a binary market. It carries
exactly two `marketSides` (20,000 of 20,000), an `outcomes` pair, and BOTH
`bestBidQuote` and `bestAskQuote` on the row itself (11,364 of 20,000 had both
populated). No pairing of two rows is needed, and there is consequently NO
inter-leg time gap to control -- the two sides arrive in ONE response. That
removes the single largest measurement hazard the retrospective work fought.

Also read off those rows, as captured evidence rather than relay:
  * `feeCoefficient` = 0.06 on all 20,000 rows, at 2026-09-16 00:29Z, which is
    BEFORE the announced cutover -- live corroboration of the JUL2026 taker
    coefficient.
  * `orderPriceMinTickSize` takes THREE values: 0.001, 0.005, 0.01. The tick is
    not a cent everywhere, which matters directly to any reward score computed
    in ticks from best.
  * `DISCOVERY_LIST_EXHAUSTED = NO` at the 200-page cap: the board is at least
    20,000 open markets and its true size is still NOT_IDENTIFIED.

Usage:
    python3 fwd_collect.py board   --out DIR [--max-pages N]
    python3 fwd_collect.py breadth --out DIR --board FILE
    python3 fwd_collect.py depth   --out DIR --board FILE --seconds N
                                   [--cohort K] [--interval S]
    python3 fwd_collect.py settle  --out DIR --registry FILE
    python3 fwd_collect.py rules   --out DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

COLLECTOR_VERSION = "beta48-forward-public/1"

# The public gateway. The authenticated host is deliberately NOT a constant
# here: there is nothing in this file that could use one.
GATEWAY_BASE = "https://gateway.polymarket.us"
MARKETS_PATH = "/v1/markets"
BOOK_PATH = "/v1/markets/{slug}/book"
INCENTIVES_PATH = "/v1/incentives"

# The published rule pages, read as EVIDENCE. Both the fee coefficient and the
# reward schedule are otherwise relayed, and a relayed number is not a measured
# one. These are the only non-gateway hosts this file names, they are fetched
# GET and stored verbatim, and a failure is recorded as a row like any other.
DOC_URLS = (
    "https://docs.polymarket.us/fees",
    "https://docs.polymarket.us/incentives/liquidity",
)

# The announced taker-coefficient cutover, 23:59 ET Wed 2026-09-16 = 03:59 UTC
# Thu 2026-09-17. Stamped on every observation so the regimes are never pooled.
# Kept as a literal here rather than imported, so the collector stays free of
# any dependency on the economics modules -- this file computes NO economics.
FEE_REGIME_CUTOVER_UTC = "2026-09-17T03:59:00+00:00"

# OUR restraint. RPS_LIMIT_NOT_ESTABLISHED stays locked; this is not evidence
# about the venue's ceiling.
MAX_RPS = 2.0
MIN_SPACING_S = 1.0 / MAX_RPS
TIMEOUT_S = 10.0
PAGE_LIMIT = 100
MAX_PAGES_DEFAULT = 200          # a real terminal boundary, not a prefix
INCENTIVES_MAX_PAGES = 200       # token-paginated; walked until it exhausts


def _now():
    return (datetime.now(tz=timezone.utc).isoformat(), time.monotonic_ns())


def fee_regime(wall_utc: str) -> str:
    """Label an observation's fee regime. A LABEL, not a fee calculation.

    Two regimes must never be pooled, so every artifact this file writes
    carries the label of the wall clock that produced it. If the timestamp is
    unreadable the answer is UNKNOWN -- never a default to either side.
    """
    try:
        t = datetime.fromisoformat(str(wall_utc).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "UNKNOWN"
    return ("SEP2026" if t >= datetime.fromisoformat(FEE_REGIME_CUTOVER_UTC)
            else "JUL2026")


class Pacer:
    """Never yields faster than MIN_SPACING_S. Honours Retry-After exactly."""

    def __init__(self, spacing_s=MIN_SPACING_S):
        self.spacing = max(float(spacing_s), MIN_SPACING_S)
        self._last = None
        self.requests = 0

    def wait(self):
        if self._last is not None:
            slack = (self._last + self.spacing) - time.monotonic()
            if slack > 0:
                time.sleep(slack)
        self._last = time.monotonic()
        self.requests += 1

    def backoff(self, seconds: float):
        time.sleep(max(0.0, float(seconds)))
        self._last = time.monotonic()


def _get(http, path, params=None):
    """One public GET. A failure is recorded AS a row, never left absent."""
    wall, mono = _now()
    row = {"path": path, "params": params, "local_request_wall_utc": wall,
           "local_request_monotonic_ns": mono, "http_status": None,
           "error": None, "body": None, "response_headers": None,
           "response_bytes": None, "response_sha256": None,
           "collector_version": COLLECTOR_VERSION}
    try:
        resp = http.get(GATEWAY_BASE + path, params=params, timeout=TIMEOUT_S)
        row["http_status"] = resp.status_code
        try:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            row["response_headers"] = {
                k: v for k, v in hdrs.items()
                if ("ratelimit" in k or "rate-limit" in k or "retry" in k
                    or k in ("cache-control", "age", "date", "server",
                             "content-length", "content-type"))}
        except Exception:                                    # noqa: BLE001
            row["response_headers"] = None
        try:
            raw = resp.content
            row["response_bytes"] = len(raw)
            row["response_sha256"] = hashlib.sha256(raw).hexdigest()
        except Exception:                                    # noqa: BLE001
            pass
        if resp.status_code == 200:
            try:
                row["body"] = resp.json()
            except ValueError:
                row["error"] = "NON_JSON_BODY"
        else:
            row["error"] = "http_%d" % resp.status_code
    except httpx.HTTPError as exc:
        row["error"] = type(exc).__name__
    wall2, mono2 = _now()
    row["local_response_wall_utc"] = wall2
    row["local_response_monotonic_ns"] = mono2
    row["latency_ms"] = (mono2 - mono) / 1e6
    return row


def _retry_after(row) -> float | None:
    h = row.get("response_headers") or {}
    for k in ("retry-after", "x-ratelimit-reset"):
        if k in h:
            try:
                return float(h[k])
            except (TypeError, ValueError):
                return 60.0
    return None


def _paced_get(http, pacer, path, params=None, attempts=3):
    """GET with the pacer and an exact Retry-After honour. Never speeds up."""
    for i in range(attempts):
        pacer.wait()
        row = _get(http, path, params)
        if row.get("http_status") == 429 or row.get("http_status") == 503:
            ra = _retry_after(row)
            if i + 1 < attempts:
                pacer.backoff(ra if ra is not None else 60.0)
                continue
        return row
    return row


# ------------------------------------------------------------------ board --

def board(outdir: Path, pacer, http, max_pages=MAX_PAGES_DEFAULT) -> dict:
    """Enumerate the WHOLE active board, walked to a terminal boundary.

    `DISCOVERY_LIST_EXHAUSTED` is written as YES only when a page comes back
    short or empty. If the page cap is hit first it is written NO, and the
    dataset is then a prefix and must be treated as one.
    """
    raw, pages = [], 0
    offset = 0
    exhausted = False
    advanced = True
    by_slug: dict = {}                 # DEDUPE. See why, below.
    while pages < max_pages:
        params = {"active": "true", "closed": "false",
                  "limit": PAGE_LIMIT, "offset": offset}
        r = _paced_get(http, pacer, MARKETS_PATH, params)
        raw.append(r)
        pages += 1
        body = r.get("body") or {}
        items = body.get("markets") or body.get("data") or []
        if not isinstance(items, list) or not items:
            exhausted = True
            break
        # DEDUPE BY SLUG, AND DETECT A PAGINATION THAT DOES NOT ADVANCE.
        #
        # The first live run walked all 200 pages in 101 s and then selected
        # ZERO two-outcome events. That is the signature of a repeated page:
        # the same market arriving many times makes its event look like it has
        # 200 outcomes, so every event fails the len(ms) == 2 test and the
        # whole board is silently discarded. The repository already knows this
        # venue family ignores paging params -- reference_pull.py carries
        # "NEVER the offset param" in its header for the data-api.
        #
        # Deduping makes the grouping correct whatever the API does, and a page
        # that contributes no NEW slug ends the walk and is NAMED, so a
        # non-advancing cursor can never again be mistaken for a large board.
        fresh = 0
        for m in items:
            if not isinstance(m, dict):
                continue
            slug = m.get("slug")
            if not slug or slug in by_slug:
                continue
            by_slug[slug] = m
            fresh += 1
        if fresh == 0:
            advanced = False
            exhausted = True
            break
        if len(items) < PAGE_LIMIT:
            exhausted = True
            break
        offset += len(items)

    markets = list(by_slug.values())

    # SELECTION IS PER ROW, NOT PER EVENT.
    #
    # The first live run proved there is no `eventSlug` on these rows and no
    # grouping to do: every row IS a binary market carrying exactly two
    # marketSides and both quotes. The selection rule is therefore the weakest
    # one that can be stated -- "the row was on the board and has two sides" --
    # which is what keeps breadth a population rather than a shortlist.
    #
    # `eventSlug` is still counted, so that if the venue ever starts sending
    # it the change is visible instead of silent.
    selected, skipped = [], []
    with_event_field = 0
    for m in markets:
        slug = m.get("slug")
        if m.get("eventSlug"):
            with_event_field += 1
        sides = m.get("marketSides") or []
        if not slug:
            skipped.append({"slug": None, "reason": "NO_SLUG"})
            continue
        if len(sides) != 2:
            skipped.append({"slug": slug, "side_count": len(sides),
                            "reason": "NOT_A_TWO_SIDED_MARKET"})
            continue
        selected.append({
            "slug": slug,
            "slugs": [slug],            # kept so downstream readers stay valid
            "id": m.get("id"),
            "title": m.get("title"),
            "question": m.get("question"),
            "outcomes": m.get("outcomes"),
            "status": m.get("status"),
            "marketType": m.get("marketType"),
            "sportsMarketTypeV2": m.get("sportsMarketTypeV2"),
            "gameStartTime": m.get("gameStartTime"),
            "endDate": m.get("endDate"),
            "tags": m.get("tags"),
            # Venue-stated mechanics, captured per market rather than assumed.
            "feeCoefficient": m.get("feeCoefficient"),
            "orderPriceMinTickSize": m.get("orderPriceMinTickSize"),
            "minimumTradeQty": m.get("minimumTradeQty"),
            # The board's own quotes. Recorded as BOARD_ quotes, never merged
            # with a book read: they are a different observation at a different
            # instant and must stay distinguishable.
            "board_bestBidQuote": m.get("bestBidQuote"),
            "board_bestAskQuote": m.get("bestAskQuote"),
            "board_outcomePrices": m.get("outcomePrices"),
            "side_identifiers": [s.get("identifier") for s in sides],
            "side_descriptions": [s.get("description") for s in sides],
        })

    wall, _ = _now()
    ticks = sorted({m.get("orderPriceMinTickSize") for m in markets
                    if m.get("orderPriceMinTickSize") is not None})
    fees = sorted({m.get("feeCoefficient") for m in markets
                   if m.get("feeCoefficient") is not None})
    both_quotes = sum(1 for m in markets
                      if m.get("bestBidQuote") and m.get("bestAskQuote"))
    out = {"collector_version": COLLECTOR_VERSION,
           "captured_at_utc": wall,
           "fee_regime": fee_regime(wall),
           "pages_walked": pages,
           "DISCOVERY_LIST_EXHAUSTED": "YES" if exhausted else "NO",
           "PAGINATION_ADVANCED": "YES" if advanced else "NO",
           # PREFIX LANGUAGE, NOT POPULATION LANGUAGE.
           #
           # A walk that stopped at the page cap has seen AT LEAST this many
           # markets, not all of them. Calling 20,000 "the whole board" would
           # be the same error as the archaeology's, in a friendlier font.
           # Breadth can be unbiased WITHIN the observed prefix; it is not
           # exchange-wide complete, and the true size stays unknown.
           "OBSERVED_PREFIX_MARKETS": len(markets),
           "TRUE_ACTIVE_BOARD_SIZE": (
               len(markets) if exhausted else "NOT_IDENTIFIED"),
           "BREADTH_IS_EXCHANGE_WIDE_COMPLETE": "YES" if exhausted else "NO",
           "markets_seen": len(markets),          # retained name, same number
           "rows_with_eventSlug": with_event_field,
           "markets_two_sided": len(selected),
           "markets_skipped": len(skipped),
           "markets_with_both_board_quotes": both_quotes,
           "tick_sizes_observed": ticks,
           "fee_coefficients_observed": fees,
           "requests_used": pacer.requests,
           # Retained under the old key so any existing reader keeps working.
           "events_two_outcome": len(selected),
           "events_skipped": len(skipped),
           "selected": selected, "skipped": skipped}
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "board_raw.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in raw))
    (outdir / "board.json").write_text(json.dumps(out, indent=1))
    print("distinct markets %d | two-sided %d | both board quotes %d | "
          "pages %d | exhausted %s | pagination advanced %s | ticks %s | "
          "feeCoefficients %s | regime %s"
          % (len(markets), len(selected), both_quotes, pages,
             out["DISCOVERY_LIST_EXHAUSTED"], out["PAGINATION_ADVANCED"],
             ticks, fees, out["fee_regime"]))
    return out


# ------------------------------------------------------------------ rules --

def rules(outdir: Path, pacer, http) -> dict:
    """Capture the venue's INCENTIVE and FEE rules as evidence, not relay.

    Answers, per snapshot, the four questions a later market observation needs:
    was this market incentivized, what pool was active, what quote distance
    would score, and had the target size already been reached ahead of BETTOR.

    ESTIMATED_REWARD IS NOT ACTUAL_REWARD. This function records a PROGRAM
    DESCRIPTION. It records no reward earned by anyone, because nothing here
    has an account, and `ACTUAL_REWARD` therefore stays NOT_IDENTIFIED for
    every market until an executed, settled reward is observed elsewhere.

    Every field below is read from the response or left None. Nothing is
    defaulted to an optimistic value, and the schedule is NOT hard-coded: if
    the endpoint supplies current values they are what get stored.
    """
    wall, _ = _now()

    # TOKEN PAGINATION, walked to exhaustion.
    #
    # The first live capture took one page and recorded 100 programs beside a
    # `nextPageToken` it never followed -- the same prefix mistake that made
    # the retrospective board unusable, in a new place. It also sent
    # `active=true`, which returned byte-identical content (87,650 bytes both
    # times) and is therefore not a filter this endpoint honours; that request
    # is dropped rather than left in looking like a control.
    # THE TOKEN IS NOT A RELIABLE END MARKER. A first attempt stopped only
    # when `nextPageToken` repeated, and the live run showed why that is not
    # enough: the endpoint handed back a DIFFERENT token every time while
    # serving the SAME 100 markets, so the walk ran to the 200-page cap and
    # 267 real programme-periods were counted 200 times over as 53,400.
    #
    # Termination is therefore decided by CONTENT, not by the cursor: a page
    # that contributes no new (market, programme, period, start) ends the walk.
    # The board walk already learned this lesson; this is the same guard, and
    # not carrying it across the first time is the reason this run is void.
    rows, token, pages = [], None, 0
    exhausted = False
    advanced = True
    seen: set = set()
    while pages < INCENTIVES_MAX_PAGES:
        r = _paced_get(http, pacer, INCENTIVES_PATH,
                       {"pageToken": token} if token else None)
        rows.append(r)
        pages += 1
        body = r.get("body") or {}
        items = body.get("programs") if isinstance(body, dict) else None
        fresh = 0
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            slug = it.get("marketSlug")
            for tp in (it.get("timePeriods") or [{}]):
                tp = tp if isinstance(tp, dict) else {}
                key = (slug, tp.get("programId"), tp.get("programType"),
                       tp.get("period"), tp.get("start"))
                if key not in seen:
                    seen.add(key)
                    fresh += 1
        if fresh == 0:
            advanced = False
            exhausted = True
            break
        nxt = body.get("nextPageToken") if isinstance(body, dict) else None
        if not nxt or nxt == token:
            exhausted = True
            break
        token = nxt

    docs = []
    for url in DOC_URLS:
        w, mono = _now()
        row = {"url": url, "local_request_wall_utc": w, "http_status": None,
               "error": None, "text": None, "response_sha256": None,
               "response_bytes": None, "collector_version": COLLECTOR_VERSION}
        try:
            pacer.wait()
            resp = http.get(url, timeout=TIMEOUT_S)
            row["http_status"] = resp.status_code
            raw = resp.content
            row["response_bytes"] = len(raw)
            row["response_sha256"] = hashlib.sha256(raw).hexdigest()
            if resp.status_code == 200:
                row["text"] = resp.text
            else:
                row["error"] = "http_%d" % resp.status_code
        except httpx.HTTPError as exc:
            row["error"] = type(exc).__name__
        docs.append(row)

    # FLATTENED TO ONE RECORD PER (MARKET, TIME PERIOD).
    #
    # A market carries a LIST of `timePeriods` -- observed 1, 4 or 5 of them --
    # and every economic field lives inside a period, not on the market. The
    # first capture read the market level, found nothing there, and wrote a
    # full set of nulls beside a `raw` that held the answers. A market is
    # therefore never summarised by one of its periods: each becomes its own
    # record, and the period is named.
    #
    # `end` and `maxSpread` are NOT in this payload. They are emitted as None
    # rather than inferred from `start` plus a guessed duration.
    programs, markets_seen, emitted = [], set(), set()
    for r in rows:
        body = r.get("body")
        items = body.get("programs") if isinstance(body, dict) else None
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            slug = it.get("marketSlug")
            markets_seen.add(slug)
            periods = it.get("timePeriods") or []
            if not periods:
                periods = [{}]
            for tp in periods:
                if not isinstance(tp, dict):
                    tp = {}
                # DEDUPE ON THE WAY OUT TOO. A repeated page must not become a
                # repeated record: a distribution computed over duplicates
                # reads as a much larger sample than was ever observed.
                key = (slug, tp.get("programId"), tp.get("programType"),
                       tp.get("period"), tp.get("start"))
                if key in emitted:
                    continue
                emitted.add(key)
                programs.append({
                    "MARKET_SLUG": slug,
                    "INSTRUMENT_STATE": it.get("instrumentState"),
                    "EVENT_START_TIME": it.get("eventStartTime") or None,
                    "CATEGORY": it.get("category") or None,
                    "PROGRAM_ID": tp.get("programId"),
                    "INCENTIVE_PROGRAM_ACTIVE": tp.get("status"),
                    "PROGRAM_TYPE": tp.get("programType"),
                    "REWARD_POOL": tp.get("rewardPool"),
                    "DISCOUNT_FACTOR": tp.get("discountFactor"),
                    "TARGET_SIZE": tp.get("targetSize"),
                    "MAX_SPREAD": tp.get("maxSpread"),      # absent: stays None
                    "PROGRAM_PERIOD": tp.get("period"),
                    "PROGRAM_START": tp.get("start"),
                    # `end` was absent from every observed row. It is read
                    # anyway so that the day the venue starts sending one, it
                    # is captured rather than silently dropped -- and it is
                    # NEVER inferred from `start` plus a guessed duration.
                    "PROGRAM_END": tp.get("end"),
                    "PROGRAM_CREATED_AT": tp.get("createdAt"),
                    # Our own score needs the TOTAL qualifying score across all
                    # participants, which this endpoint does not publish.
                    "ESTIMATED_REWARD": "NOT_COMPUTED_HERE",
                    "ACTUAL_REWARD": "NOT_IDENTIFIED",
                    "TARGET_SIZE_ALREADY_MET_BY_OTHERS": "NOT_IDENTIFIED",
                    "raw_time_period": tp,
                })

    reachable = any(r.get("http_status") == 200 for r in rows)
    types = sorted({p["PROGRAM_TYPE"] for p in programs if p["PROGRAM_TYPE"]})
    out = {"collector_version": COLLECTOR_VERSION,
           "captured_at_utc": wall,
           "fee_regime": fee_regime(wall),
           "INCENTIVES_ENDPOINT_REACHABLE": "YES" if reachable else "NO",
           "INCENTIVES_LIST_EXHAUSTED": "YES" if exhausted else "NO",
           "INCENTIVES_PAGINATION_ADVANCED": "YES" if advanced else "NO",
           "incentive_pages_walked": pages,
           "incentive_http_statuses": [r.get("http_status") for r in rows],
           "incentivized_markets": len(markets_seen),
           "programs_parsed": len(programs),
           "PROGRAM_TYPES_OBSERVED": types,
           # The documentation names FOUR programs -- volume, liquidity, fill
           # and the application-only Market Maker Program. This endpoint has
           # only ever been seen returning one type. Whether it publishes the
           # others is NOT established by its silence, so the absence of a
           # type here is never read as "that programme is not running".
           "DOCUMENTED_PROGRAMS": ["VOLUME_INCENTIVE", "LIQUIDITY_INCENTIVE",
                                   "FILL_INCENTIVE", "MARKET_MAKER_PROGRAM"],
           "INCENTIVES_ENDPOINT_COVERS_ALL_PROGRAMS": "NOT_IDENTIFIED",
           "programs": programs,
           "doc_http_statuses": [d.get("http_status") for d in docs],
           "doc_sha256": {d["url"]: d.get("response_sha256") for d in docs},
           # Never asserted from this file. Both stay open until an analysis
           # step reads the captured doc text and says so explicitly.
           "THETA_TAKER_OBSERVED_IN_DOC": "NOT_PARSED_HERE",
           "NEGOTIATED_MARKET_MAKER_ECONOMICS": "NOT_IDENTIFIED",
           "TIER_VERIFIED": "NO"}

    outdir.mkdir(parents=True, exist_ok=True)
    with (outdir / "incentives_raw.jsonl").open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with (outdir / "rules_docs.jsonl").open("a") as fh:
        for d in docs:
            fh.write(json.dumps(d) + "\n")
    (outdir / "rules.json").write_text(json.dumps(out, indent=1))
    print("incentives reachable %s | pages %d | exhausted %s | advanced %s | "
          "markets %d | distinct program-periods %d | docs %s | regime %s"
          % (out["INCENTIVES_ENDPOINT_REACHABLE"], pages,
             out["INCENTIVES_LIST_EXHAUSTED"],
             out["INCENTIVES_PAGINATION_ADVANCED"], len(markets_seen),
             len(programs), out["doc_http_statuses"], out["fee_regime"]))
    return out


# ---------------------------------------------------------------- breadth --

def _slugs_of(ev):
    """Every slug an entry names. One for a binary row; two under the old
    paired shape, which is kept readable so archived boards still parse."""
    ss = ev.get("slugs") or ([ev["slug"]] if ev.get("slug") else [])
    return [s for s in ss if s]


def breadth(outdir: Path, events, pacer, http, book_reads=0) -> int:
    """One pass over EVERY market on the board. No scoring, no shortlist.

    THE BOARD IS THE BREADTH POPULATION, and that is why this is affordable.
    Every board row already carries `bestBidQuote` and `bestAskQuote`, so the
    touch for 20,000 markets arrives in the 200-page board walk -- about 100
    seconds at our 2 rps ceiling. One book read PER MARKET would instead cost
    20,000 requests, roughly 2.8 hours, which does not fit a 2-hour segment and
    would have forced a capped sample. A capped sample is exactly the selection
    bias this programme exists to avoid, so it is not taken.

    BOOK READS ARE THEREFORE OPT-IN AND DECLARED. `book_reads` is a count, not
    a filter: the first N markets in board order get depth beyond the touch,
    every market still gets its row, and `BOOK_READ` says which is which. Depth
    beyond the touch is a DEPTH question anyway, and depth() answers it on a
    cohort whose selection rule is stated.

    THE LEG GAP IS GONE, and that is a real improvement, not a simplification.
    A binary market's two sides arrive inside ONE response, so there is no
    inter-leg interval to control and no window in which the book can move
    between the legs. Run 84 established that an uncontrolled gap IS the
    result; here the gap is structurally zero and is recorded as such, with
    LEG_GAP_BASIS naming why, so no later reader mistakes a zero for an
    unmeasured field.
    """
    path = outdir / "breadth.jsonl"
    n = 0
    read_budget = int(book_reads)
    with path.open("a") as fh:
        for ev in events:
            slugs = _slugs_of(ev)
            if not slugs:
                continue
            if read_budget > 0:
                legs = [_paced_get(http, pacer, BOOK_PATH.format(slug=s))
                        for s in slugs]
                read_budget -= 1
                book_read = "YES"
            else:
                # No venue request. The row still carries the board's own
                # two-sided quote, captured at the board walk's timestamp.
                legs = [{"local_request_monotonic_ns": 0,
                         "local_request_wall_utc": _now()[0],
                         "path": None, "http_status": None,
                         "error": "NOT_REQUESTED", "body": None}]
                book_read = "NO"
            if len(legs) >= 2:
                gap_ns = abs(legs[1]["local_request_monotonic_ns"]
                             - legs[0]["local_request_monotonic_ns"])
                basis = "TWO_REQUESTS"
            else:
                gap_ns = 0
                basis = "SINGLE_RESPONSE_BOTH_SIDES"
            wall = legs[0]["local_request_wall_utc"]
            fh.write(json.dumps({
                "kind": "BREADTH",
                "slug": ev.get("slug"),
                "slugs": slugs,
                "fee_regime": fee_regime(wall),
                "question": ev.get("question"),
                "outcomes": ev.get("outcomes"),
                "gameStartTime": ev.get("gameStartTime"),
                "endDate": ev.get("endDate"),
                "sportsMarketTypeV2": ev.get("sportsMarketTypeV2"),
                "tags": ev.get("tags"),
                "feeCoefficient": ev.get("feeCoefficient"),
                "orderPriceMinTickSize": ev.get("orderPriceMinTickSize"),
                "board_bestBidQuote": ev.get("board_bestBidQuote"),
                "board_bestAskQuote": ev.get("board_bestAskQuote"),
                "leg_gap_ns": gap_ns,
                "leg_gap_s": gap_ns / 1e9,
                "LEG_GAP_BASIS": basis,
                "BOOK_READ": book_read,
                "legs": legs}) + "\n")
            n += 1
    print("breadth markets written %d | book reads %d | requests %d"
          % (n, int(book_reads) - read_budget, pacer.requests))
    return n


# ------------------------------------------------------------------ depth --

def depth(outdir: Path, events, seconds, cohort, interval_s, pacer, http) -> int:
    """Dense repeated reads of a rotating cohort, for EXECUTION questions.

    Cohort selection is by TIME TO EVENT -- the markets closest to starting --
    because that is where a maker would be quoting. This is a deliberate,
    declared selection and it is NOT the fair-value population: breadth is.
    Recorded separately for exactly that reason.
    """
    def tte(ev):
        g = ev.get("gameStartTime")
        if not g:
            return 1e18
        try:
            t = datetime.fromisoformat(str(g).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 1e18
        return abs(t - time.time())

    picked = sorted(events, key=tte)[:max(1, int(cohort))]
    path = outdir / "depth.jsonl"
    t_end = time.monotonic() + float(seconds)
    rounds = 0
    with path.open("a") as fh:
        while time.monotonic() < t_end:
            cycle_start = time.monotonic()
            for ev in picked:
                if time.monotonic() >= t_end:
                    break
                slugs = _slugs_of(ev)
                if not slugs:
                    continue
                legs = [_paced_get(http, pacer, BOOK_PATH.format(slug=s))
                        for s in slugs]
                if len(legs) >= 2:
                    gap_ns = abs(legs[1]["local_request_monotonic_ns"]
                                 - legs[0]["local_request_monotonic_ns"])
                    basis = "TWO_REQUESTS"
                else:
                    gap_ns = 0
                    basis = "SINGLE_RESPONSE_BOTH_SIDES"
                fh.write(json.dumps({
                    "kind": "DEPTH",
                    "slug": ev.get("slug"),
                    "slugs": slugs,
                    "fee_regime": fee_regime(
                        legs[0]["local_request_wall_utc"]),
                    "gameStartTime": ev.get("gameStartTime"),
                    "endDate": ev.get("endDate"),
                    "orderPriceMinTickSize": ev.get("orderPriceMinTickSize"),
                    "round": rounds,
                    "leg_gap_ns": gap_ns,
                    "leg_gap_s": gap_ns / 1e9,
                    "LEG_GAP_BASIS": basis,
                    "legs": legs}) + "\n")
            rounds += 1
            slack = (cycle_start + float(interval_s)) - time.monotonic()
            if slack > 0:
                time.sleep(slack)
    print("depth cohort %d | rounds %d | requests %d"
          % (len(picked), rounds, pacer.requests))
    return rounds


# ----------------------------------------------------------------- settle --

def settle(outdir: Path, registry_path: Path, pacer, http) -> dict:
    """Re-read captured slugs until the venue calls them resolved.

    THE STEP WHOSE ABSENCE WAS THE ENTIRE GAP. Without it a capture holds
    prices with no outcomes and an archive holds outcomes with no prices, and
    they never meet.
    """
    reg = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    pending = [s for s, v in reg.items() if not v.get("resolved")]
    now = time.time()

    def due(slug):
        ed = reg[slug].get("endDate")
        if not ed:
            return True
        try:
            return datetime.fromisoformat(
                str(ed).replace("Z", "+00:00")).timestamp() <= now
        except (TypeError, ValueError):
            return True

    todo = [s for s in pending if due(s)]
    raw, newly = [], 0
    for s in todo:
        r = _paced_get(http, pacer, MARKETS_PATH, {"slug": s})
        raw.append(r)
        body = r.get("body") or {}
        items = body.get("markets") or body.get("data") or []
        m = items[0] if isinstance(items, list) and items else None
        if not isinstance(m, dict):
            continue
        status = m.get("status")
        prices = m.get("outcomePrices") or m.get("outcome_prices")
        reg[s]["last_seen_status"] = status
        reg[s]["last_checked_utc"] = _now()[0]
        if status and "RESOLVED" in str(status).upper() and prices:
            reg[s]["resolved"] = True
            reg[s]["outcomePrices"] = prices
            reg[s]["resolved_seen_utc"] = _now()[0]
            newly += 1

    outdir.mkdir(parents=True, exist_ok=True)
    with (outdir / "settle_raw.jsonl").open("a") as fh:
        for x in raw:
            fh.write(json.dumps(x) + "\n")
    registry_path.write_text(json.dumps(reg, indent=1, sort_keys=True))
    total = sum(1 for v in reg.values() if v.get("resolved"))
    print("settle checked %d | newly resolved %d | resolved total %d of %d"
          % (len(todo), newly, total, len(reg)))
    return {"checked": len(todo), "newly_resolved": newly,
            "resolved_total": total, "registry_size": len(reg)}


# ------------------------------------------------------------- the panel --

def panel_cohort(board_events, rules_out, per_stratum=3):
    """Choose the depth panel's markets. SAMPLING FROZEN BEFORE ANY ECONOMICS.

    THE ONE RULE: selection may use only facts knowable AT DECISION TIME from
    the public programme description -- programType, rewardPool, targetSize,
    discountFactor, tick size, market period, category. It may NOT use anything
    about how the market later behaved, traded or paid. Picking markets whose
    economics turned out attractive is how a panel manufactures the result it
    was built to test, and it is the single failure this whole programme has
    been trying not to repeat.

    The stratum key is therefore made of programme facts ONLY, the markets
    inside a stratum are taken in SLUG ORDER (deterministic, and unrelated to
    any outcome), and the count per stratum is fixed in advance.

    This is a DIFFERENT DATASET from the breadth census and is never pooled
    with it: breadth is the whole observed prefix with no selection at all;
    the panel is a stratified sample chosen for depth.
    """
    by_slug = {e.get("slug"): e for e in board_events if e.get("slug")}
    strata: dict = {}
    for p in (rules_out or {}).get("programs", []):
        slug = p.get("MARKET_SLUG")
        ev = by_slug.get(slug)
        if not ev:
            continue                      # not on the observed board prefix
        key = (p.get("PROGRAM_TYPE"), p.get("REWARD_POOL"),
               p.get("TARGET_SIZE"), p.get("DISCOUNT_FACTOR"),
               p.get("PROGRAM_PERIOD"), ev.get("orderPriceMinTickSize"),
               ev.get("sportsMarketTypeV2"))
        strata.setdefault(key, []).append((slug, p, ev))

    picked, plan = [], []
    for key in sorted(strata, key=lambda k: tuple(str(x) for x in k)):
        rows = sorted(strata[key], key=lambda r: r[0])       # SLUG ORDER
        take = rows[:int(per_stratum)]
        plan.append({"stratum": [str(x) for x in key],
                     "available": len(rows), "taken": len(take)})
        for slug, p, ev in take:
            picked.append({"slug": slug, "event": ev, "program": p})
    return picked, plan


def panel(outdir: Path, board_events, rules_out, pacer, http,
          per_stratum=3, rounds=1, interval_s=5.0) -> int:
    """Read FULL DEPTH on the panel's markets, for the Target Size question.

    Writes raw book responses plus the programme facts they must be read
    against. It computes no scoring here: depth_panel.py does that offline,
    under a rule fixed before the data, so the measurement and the capture
    cannot drift into each other.
    """
    picked, plan = panel_cohort(board_events, rules_out, per_stratum)
    path = outdir / "panel.jsonl"
    n = 0
    with path.open("a") as fh:
        for rnd in range(max(1, int(rounds))):
            start = time.monotonic()
            for item in picked:
                leg = _paced_get(http, pacer,
                                 BOOK_PATH.format(slug=item["slug"]))
                fh.write(json.dumps({
                    "kind": "PANEL",
                    "round": rnd,
                    "slug": item["slug"],
                    "fee_regime": fee_regime(leg["local_request_wall_utc"]),
                    # Decision-time programme facts, carried WITH the book so
                    # the two can never be matched up wrongly later.
                    "PROGRAM_TYPE": item["program"].get("PROGRAM_TYPE"),
                    "PROGRAM_ID": item["program"].get("PROGRAM_ID"),
                    "REWARD_POOL": item["program"].get("REWARD_POOL"),
                    "TARGET_SIZE": item["program"].get("TARGET_SIZE"),
                    "DISCOUNT_FACTOR": item["program"].get("DISCOUNT_FACTOR"),
                    "PROGRAM_PERIOD": item["program"].get("PROGRAM_PERIOD"),
                    "INSTRUMENT_STATE": item["program"].get("INSTRUMENT_STATE"),
                    "tick": item["event"].get("orderPriceMinTickSize"),
                    "sportsMarketTypeV2": item["event"].get(
                        "sportsMarketTypeV2"),
                    "leg": leg}) + "\n")
                n += 1
            slack = (start + float(interval_s)) - time.monotonic()
            if rnd + 1 < int(rounds) and slack > 0:
                time.sleep(slack)

    (outdir / "panel_plan.json").write_text(json.dumps({
        "DATASET": "INCENTIVE_DEPTH_PANEL",
        "NEVER_POOLED_WITH": "BREADTH_CENSUS",
        "SAMPLING_FROZEN_BEFORE_ECONOMICS": "YES",
        "SELECTION_INPUTS": ["PROGRAM_TYPE", "REWARD_POOL", "TARGET_SIZE",
                             "DISCOUNT_FACTOR", "PROGRAM_PERIOD",
                             "orderPriceMinTickSize", "sportsMarketTypeV2"],
        "SELECTION_WITHIN_STRATUM": "SLUG_ORDER",
        "per_stratum": int(per_stratum),
        "strata": len(plan), "markets_picked": len(picked),
        "rounds": int(rounds), "plan": plan}, indent=1))
    print("panel strata %d | markets %d | rows %d"
          % (len(plan), len(picked), n))
    return n


def update_registry(registry_path: Path, events) -> int:
    """Every event seen by BREADTH enters the settlement registry."""
    reg = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    added = 0
    for ev in events:
        for s in _slugs_of(ev):
            if s not in reg:
                reg[s] = {"question": ev.get("question"),
                          "endDate": ev.get("endDate"),
                          "gameStartTime": ev.get("gameStartTime"),
                          "first_seen_utc": _now()[0],
                          "resolved": False}
                added += 1
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(reg, indent=1, sort_keys=True))
    return added


# ------------------------------------------------------------------- main --

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("board", "breadth", "depth", "settle",
                                     "rules", "panel"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--board")
    ap.add_argument("--registry")
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--cohort", type=int, default=8)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES_DEFAULT)
    ap.add_argument("--book-reads", type=int, default=0,
                    help="markets in board order that also get a book read; "
                         "0 means the board's own two-sided quote only")
    ap.add_argument("--rules", help="rules.json, for the panel's strata")
    ap.add_argument("--per-stratum", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=1)
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pacer = Pacer()
    with httpx.Client(headers={"User-Agent": COLLECTOR_VERSION}) as http:
        if a.mode == "board":
            board(out, pacer, http, a.max_pages)
            return 0
        if a.mode == "settle":
            settle(out, Path(a.registry), pacer, http)
            return 0
        if a.mode == "rules":
            rules(out, pacer, http)
            return 0
        b = json.loads(Path(a.board).read_text())
        events = b.get("selected") or []
        if a.mode == "panel":
            r = json.loads(Path(a.rules).read_text()) if a.rules else {}
            panel(out, events, r, pacer, http, a.per_stratum,
                  a.rounds, a.interval)
            return 0
        if a.mode == "breadth":
            if a.registry:
                n = update_registry(Path(a.registry), events)
                print("registry additions %d" % n)
            breadth(out, events, pacer, http, book_reads=a.book_reads)
        else:
            depth(out, events, a.seconds, a.cohort, a.interval, pacer, http)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
