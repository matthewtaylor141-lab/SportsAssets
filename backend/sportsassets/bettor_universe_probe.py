"""Turning a LISTING into CANDIDATES, and candidates into ROWS THE RULE
CAN JUDGE.

THE DEFECT THIS EXISTS TO FIX. `_discover` passed the venue's market
LISTING straight into `bettor_universe.assess`. The listing
(`GET /v1/markets` -> `MarketDetail`) carries `id, slug, title,
outcome, description, active, closed, liquidity, volume, eventSlug,
team`. It carries NO `bestBid`, NO `bestAsk`, NO `sharesTraded` and NO
`state`. The rule needs all four, so on 2026-09-21 every one of 3,000
listed markets was excluded as ONE_SIDED_BOOK and the universe was
empty by construction.

A MISSING FIELD IN A LISTING IS NOT A ONE-SIDED MARKET. That
conflation is the whole bug, and this module exists so the rule never
sees an unenriched row again.

WHY BBO AND NOT THE LISTING'S `volume`. `MarketDetail.volume` is not
`sharesTraded` -- different unit, different meaning -- and substituting
it would silently change a frozen selection rule. The quotes and the
traded-share counter are taken from the venue's own market-data
response instead.

WHY BBO AND NOT THE WEBSOCKET. Both were inspected. The websocket's
`_MarketDataPayload` declares `marketSlug, bids, offers, state, stats,
transactTime` and `stats` declares `sharesTraded` -- but that is the
SDK's TYPE DECLARATION, and this repo holds no captured PMUS websocket
frame to check it against (the only recorded frames are from the
global CLOB, a different venue with different field names), while
`bettor_market_stream` itself still records its replacement semantics
as ASSUMED_FULL_REPLACEMENT. The BBO endpoint is the opposite case:
**30,590 verbatim HTTP 200 bodies** are archived under
`research/evidence/capture/run85_phase2_segment_*/request_log.jsonl.gz`,
and in every one of them `sharesTraded` is present and non-empty, with
`bestBid`, `bestAsk`, `state`, `bidDepth` and `askDepth` present as
keys on all 30,590. Enrichment reads the source that has been
observed, not the one that has been declared.

BBO IS NOT ASSUMED TO SUPPLY TRADED SHARES -- IT IS CHECKED. A read
whose `sharesTraded` is absent or empty yields `None`, and the rule
then excludes that market as NO_TRADED_VOLUME_FIGURE, which it already
counts apart from a market that traded nothing. The 2026-09-05 probe
recorded a CLOSED market answering `"sharesTraded": ""`, so the empty
string is a real case and is handled as missing rather than as zero.

THE PAYLOAD IS WRAPPED. Real responses are `{"marketData": {...}}`.
The SDK's `MarketBBO` TypedDict declares the fields FLAT and omits
`state` entirely, which the live payload does carry (`pmus.py` records
the same divergence). Reading the SDK's declared shape would return
nothing at all, so this module unwraps `marketData` exactly as
`pmus._bbo_quotes` and `pmus.bbo_read` already do.

`pmus.bbo_read` IS NOT REUSED AND NOT CHANGED. It returns four keys
for the mirror lane, which is frozen, and it does not carry
`sharesTraded`. This is a second reader over the same endpoint, in the
same spirit as `pmus.book_read`, and the two never share a result.

COST IS BOUNDED BY ROTATION, NOT BY LUCK. BBO is one call per market,
so enriching 3,000 candidates every refresh would be 3,000 calls. This
module probes a fixed-size batch per round and advances a deterministic
cursor over the candidates sorted by slug, so successive rounds cover
different markets and the same input always produces the same order.
COVERAGE -- how much of the candidate set has been read -- is reported
SEPARATELY from ELIGIBILITY, because "we have not looked yet" and "we
looked and it did not qualify" are different facts and only the second
one is about the market.

NO ORDER PATH. This module reads market data. It imports no order
function and holds no position state.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import deque

log = logging.getLogger(__name__)

PROBE_VERSION = "BETTOR_UNIVERSE_PROBE_V2"

# ── THE RATE LIMIT: NOT ESTABLISHED ──────────────────────────────────
#
# THE LIMIT FOR THIS ENDPOINT AND THIS CREDENTIAL CONTEXT IS NOT
# ESTABLISHED. That is the whole statement, and the weaker readings of
# it are wrong:
#
#   * "The venue publishes no limit." NOT SHOWN. What is shown is that
#     no rate-limit header appeared in the 65,980 responses WE
#     CAPTURED -- `date, content-type, cache-control, server, age` and
#     nothing else. A published limit can live in documentation, in a
#     contract, or in headers sent only once a threshold is neared. Our
#     sample cannot see any of those.
#   * "There is no 429, so there is no limit." NOT SHOWN. Zero observed
#     429s over a sample that never exceeded one request per second is
#     what you would see EITHER from no limit OR from a limit we never
#     came close to. The observation does not separate them.
#   * The 4,800 non-200s are one segment that is 100% 403 with a
#     Cloudflare `server` header and an HTML body -- consistent with a
#     WAF block of the whole host, since it took `/book` down together
#     with `/bbo`. Also not a throttle measurement.
#
# SO: UNKNOWN LIMIT, and the only defensible default is the request
# pattern that has actually been survived.
#
# WHAT HAS BEEN SURVIVED: the entire 30,590-read evidence base was
# collected STRICTLY SERIALLY. Across every segment the densest
# one-second window holds exactly ONE request, at 0.030-0.285 req/s
# sustained (median 0.145) over eight days, with 61,178 successful
# reads. That is an EXISTENCE PROOF for one pattern, not a boundary:
# it says this was tolerated, never that anything faster would be.
#
# Eight concurrent requests was not a rate limit at all -- a
# concurrency ceiling bounds how many are in flight and says nothing
# about how often a new one may start, which is what a limiter counts.
#
# CREDENTIAL CONTEXT. Those reads were taken by an UNAUTHENTICATED
# collector against the public gateway. The worker holds PMUS
# credentials. Authenticated limits are commonly different -- higher,
# lower, or separately metered -- and none of that is established
# here either. The first live stage is what begins to settle it, for
# one endpoint, at one pace.
PROBE_MAX_RPS = 0.25
PROBE_CONCURRENCY = 1

# Operator overrides, named so an override is visible in the log rather
# than buried in a service's environment. Going above the demonstrated
# envelope is allowed and WARNED ABOUT; it is not silently normal.
RPS_ENV = "BETTOR_PROBE_MAX_RPS"
CONC_ENV = "BETTOR_PROBE_CONCURRENCY"
BATCH_ENV = "BETTOR_PROBE_BATCH"

# One enrichment round. At the default pace a round is
# PROBE_BATCH / PROBE_MAX_RPS seconds of wall clock, so this is chosen
# to fit inside one discovery interval rather than to fill a universe.
PROBE_BATCH = 120

# Rounds run back to back at startup.
#
# NOT DERIVED FROM AN ASSUMED YIELD. An earlier version set this from
# the 6.35% admit rate measured over the capture so that the 100-market
# cap "would bind". That corpus is twelve distinct markets sampled
# repeatedly over eight days, not a cross-section of the venue on a
# given evening, and a historical acceptance rate is not a promise
# about tonight. The loop starts with WHATEVER THE RULE ACCEPTS and
# reports coverage beside it; the universe fills on later refreshes.
PROBE_ROUNDS_AT_START = 2

# A single read that will not answer in this long is abandoned so one
# slow market cannot hold a round open. p99 latency in the capture is
# 181 ms; the slowest single read was 14.3 s.
PROBE_TIMEOUT_S = 8.0

# 429 HANDLING. Not observed in the captures we hold, and implemented
# anyway.
#
# `Retry-After` IS NEVER SHORTENED. An earlier version capped it at
# 120 s, which meant that a server asking for an hour would be retried
# in two minutes -- ignoring the one explicit instruction a limiter
# ever gives us, in the direction that makes things worse. The cap is
# now a SUSPENSION THRESHOLD: a delay at or under it is waited out, and
# a delay above it ABANDONS THE ROUND. The venue is telling us to go
# away for longer than this round should exist, so the round ends and
# the loop's own bounded backoff decides when to come back.
PROBE_MAX_RETRIES = 3
PROBE_RETRY_BACKOFF_S = (5.0, 15.0, 45.0)
PROBE_SUSPEND_ABOVE_S = 120.0

# THE LISTING IS BOUNDED SEPARATELY FROM ENRICHMENT, because they are
# different requests against different paths with different costs. A
# listing page returns up to 500 rows; a BBO read returns one market.
# Folding them into one budget would let a listing retry storm eat an
# enrichment allowance, or the reverse.
LISTING_MAX_PAGES = 6
LISTING_MAX_RETRIES = 2
LISTING_RETRY_BACKOFF_S = (2.0, 8.0)
LISTING_TIMEOUT_S = 20.0

# ONE RESERVED UNIT PER PAGE, NOT PER INVOCATION.
#
# THE DEFECT, measured on the 2026-09-22 probe: `_list_candidates`
# reserved ONE listing unit and then ran a `while pages < max_pages`
# loop issuing up to SIX `markets.list` calls under it, with no pacing
# between them. The probe recorded `listing_attempts_reserved: 1` while
# the venue received six requests -- an undercount of up to 6x, and a
# six-request burst at the start of a run that was reported as running
# at 0.25 req/s.
#
# VERIFIED AT THE SDK, not assumed (`polymarket_us` 2026-09-22):
#   * `Markets.list` is `self._client.get("/v1/markets", query=...)` --
#     a thin wrapper. NO internal pagination.
#   * `PolymarketUS.__init__` builds `httpx.Client(timeout=timeout)`
#     with no `transport=` and no `retries=`; httpx defaults to
#     `retries=0`. NO internal retry.
#   * `follow_redirects` appears nowhere in the package, so httpx's
#     default of False applies: a 3xx is not followed, it is raised as
#     `APIStatusError`. NO hidden redirect request.
# Therefore ONE WRAPPER CALL IS EXACTLY ONE OUTBOUND HTTP REQUEST, and
# reserving per call is reserving per request. If a future SDK version
# adds retry or pagination this equality breaks, so it is asserted by
# `test_bettor_universe_probe.py::test_sdk_has_no_hidden_requests`.
LISTING_PAGE_IS_ONE_REQUEST = True

# How many consecutive throttled pages end the listing. "Honor server
# backoff and stop on repeated throttling" -- a limiter that says no
# twice in a row is not asking to be asked a third time.
LISTING_MAX_CONSECUTIVE_429 = 2

# ── REPRODUCIBLE SAMPLING ACROSS THE BOUNDED LISTING ────────────────
#
# The candidate list is sorted by slug, and the enrichment window walks
# it from an offset -- so the 2026-09-22 probe's 40 markets were the 40
# ALPHABETICALLY FIRST slugs, which delivered one market family
# (`aachc-mlb-bavg-*-leader-*`) and told us nothing about the rest.
#
# A seeded permutation spreads the window across the whole bounded
# listing instead. The seed is recorded, so the sample is reproducible
# and was fixed BEFORE any of its economic results were seen.
#
# WHAT THIS DOES NOT ESTABLISH. It improves coverage WITHIN the
# bounded listing. The listing is itself a prefix of the venue
# (`LISTING_MAX_PAGES` pages at `BETTOR_LIVE_DISCOVERY_PAGE_SIZE`), so
# nothing here supports a claim about the venue as a whole.
SAMPLE_SEED_ENV = "BETTOR_PROBE_SAMPLE_SEED"
DEFAULT_SAMPLE_SEED = "BETTOR_SAMPLE_V1"
SAMPLE_ORDER_VERSION = "BLAKE2B_KEYED_SLUG_ORDER_V1"

# How many request starts the pacer remembers, for the densest-window
# telemetry. Bounded like everything else; the flag says when it wrapped.
STARTS_RING = 4096

# How often, in completed reads, the round asks whether it should stop.
# The caller's callback does its own time-based caching, so this is a
# bound on RESPONSIVENESS, not a query rate.
PROBE_STOP_CHECK_EVERY = 5

# Why a candidate has no enriched row. NONE OF THESE IS AN EXCLUSION
# REASON: they are coverage facts, counted apart from the rule's own
# reasons so a read failure can never be read as a market judgement.
P_OK = "ENRICHED"
P_READ_FAILED = "PROBE_READ_FAILED"
P_NO_PAYLOAD = "PROBE_NO_MARKET_DATA"
P_TIMEOUT = "PROBE_TIMED_OUT"
P_RATE_LIMITED = "PROBE_RATE_LIMITED"
P_SUSPENDED = "PROBE_SUSPENDED_BY_RETRY_AFTER"
P_NOT_PROBED = "NOT_PROBED_YET"
P_STOPPED = "PROBE_STOPPED_BY_CONTROL"
# REFUSED BEFORE DISPATCH. These are the only statuses that cost the
# venue NOTHING -- they are recorded when the allowance would not cover
# the request, so the request was never sent. They are coverage facts
# like the rest: they say we did not look, never that a market is
# unsuitable.
P_NO_DISTINCT = "PROBE_NO_DISTINCT_ALLOWANCE"
P_NO_ATTEMPT = "PROBE_NO_ATTEMPT_ALLOWANCE"


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        log.warning("bettor_universe_probe: %s=%r is not a number; "
                    "using %s", name, raw, default)
        return default


def configured_pace() -> dict:
    """The pace this process will actually use, and whether it is
    inside the envelope the evidence covers."""
    rps = _env_float(RPS_ENV, PROBE_MAX_RPS)
    conc = int(_env_float(CONC_ENV, PROBE_CONCURRENCY))
    rps = max(0.001, rps)
    conc = max(1, conc)
    over = rps > 0.285 or conc > 1
    return {"max_rps": rps, "concurrency": conc,
            "demonstrated_max_rps": 0.285, "demonstrated_concurrency": 1,
            "above_demonstrated_envelope": over,
            "envelope_evidence": ("61,178 successful reads, densest "
                                  "1 s window = 1 request")}


class Pacer:
    """A hard ceiling on the rate REQUESTS ARE STARTED at.

    Not a concurrency limit. Concurrency bounds how many are in flight;
    this bounds how often a new one may begin, which is the thing a
    venue's limiter actually counts. Both are applied.

    Shared by every worker in a round, so `max_rps` is the rate of the
    ROUND and not of each task.
    """

    def __init__(self, max_rps: float, *, sleep=None, name: str = "probe"):
        self.name = name
        self.max_rps = max(0.001, max_rps)
        self.min_interval = 1.0 / self.max_rps
        self._lock = asyncio.Lock()
        self._next_at = 0.0
        self._sleep = sleep or asyncio.sleep
        self.waited_s = 0.0
        self.grants = 0

        # ── MEASURED REQUEST TELEMETRY ───────────────────────────────
        #
        # The 2026-09-22 probe reported "0.25 req/s" because that is
        # what the pacer was CONFIGURED to. That is a setting, not an
        # observation, and it was not even true of the whole process:
        # the six listing pages were issued through no pacer at all.
        # The rate at which a 429 arrived was therefore never measured.
        #
        # These are measurements. `starts` is the monotonic clock at
        # every request START (the thing a limiter counts), so the
        # densest one- and ten-second windows can be read off it; and
        # `inflight` is how many were open at once, which is the
        # separate quantity a concurrency ceiling bounds.
        self._starts: deque = deque(maxlen=STARTS_RING)
        self.inflight = 0
        self.max_inflight = 0
        self.first_start_at: float | None = None
        self.last_start_at: float | None = None
        self.penalties_s = 0.0
        self.penalties = 0

    # `acquire` STARTS a request; `release` ends it. The pair is what
    # makes `inflight` meaningful -- a caller that forgets `release`
    # inflates it, so every dispatch site uses `started()` below.
    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                self.waited_s += wait
                # THE SAME SLEEP THE ROUND USES, so a test that
                # neutralises the 429 backoff also neutralises the
                # penalty it imposes here -- otherwise asserting a
                # bounded retry costs 65 real seconds.
                await self._sleep(wait)
                now = time.monotonic()
            self._next_at = max(now, self._next_at) + self.min_interval
            self.grants += 1
            self._starts.append(now)
            if self.first_start_at is None:
                self.first_start_at = now
            self.last_start_at = now
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)

    def release(self) -> None:
        self.inflight = max(0, self.inflight - 1)

    def starts_within(self, seconds: float,
                      *, now: float | None = None) -> int:
        """How many requests THIS PROCESS started in the last
        `seconds`. Read at the moment a 429 arrives, this is the
        closest thing we have to the rate the venue saw."""
        now = time.monotonic() if now is None else now
        cut = now - seconds
        return sum(1 for t in self._starts if t >= cut)

    def densest_window(self, seconds: float) -> int:
        """The largest number of starts in any `seconds`-wide window,
        over the starts still in the ring."""
        ts = list(self._starts)
        best, j = 0, 0
        for i, t in enumerate(ts):
            while ts[j] < t - seconds:
                j += 1
            best = max(best, i - j + 1)
        return best

    def telemetry(self) -> dict:
        """What was MEASURED, kept apart from what was configured.

        `observed_mean_rps` is this process's own starts over its own
        elapsed time. It is a LOWER BOUND on the rate the venue saw:
        other workers, other processes and anything else sharing the
        credential or the egress address are invisible from here, and
        that is exactly why a 429 cannot be attributed to this number.
        """
        span = None
        if self.first_start_at is not None and self.last_start_at is not None:
            span = self.last_start_at - self.first_start_at
        return {
            "pacer": self.name,
            "configured_max_rps": self.max_rps,
            "requests_started": self.grants,
            "observed_span_s": None if span is None else round(span, 3),
            "observed_mean_rps": (
                None if not span else round(self.grants / span, 4)),
            "densest_1s_starts": self.densest_window(1.0),
            "densest_10s_starts": self.densest_window(10.0),
            "max_concurrent_inflight": self.max_inflight,
            "paced_wait_s": round(self.waited_s, 2),
            "penalties": self.penalties,
            "penalties_s": round(self.penalties_s, 2),
            "starts_ring_capacity": STARTS_RING,
            "starts_ring_full": len(self._starts) >= STARTS_RING,
            "scope": "THIS PROCESS ONLY -- not the credential, not the "
                     "egress address, not the venue",
        }

    def penalise(self, seconds: float) -> None:
        """Push every pending start back. Used on a 429, so ONE rate
        limit slows the whole round rather than only the task that
        happened to be told about it."""
        self.penalties += 1
        self.penalties_s += max(0.0, float(seconds))
        self._next_at = max(self._next_at, time.monotonic() + seconds)


def candidates_from_listing(rows) -> list:
    """Listing rows -> identity only. NO ECONOMIC JUDGEMENT HAPPENS HERE.

    The listing can tell us a market exists, what it is called and
    which outcome it is about. It cannot tell us anything the selection
    rule asks, so this function refuses to pretend otherwise: it drops
    only rows the venue itself marks closed or archived, or that carry
    no slug to read.

    `outcome` is carried through as `outcome_leg`. It is the listing's
    own label for the side ('no', 'over', a team name) and the adapter
    refuses an observation whose leg is unnamed. It is CARRIED, NOT
    FILTERED ON -- adding a leg test here would be a new selection rule
    under an old version.
    """
    out, seen = [], set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        slug = r.get("slug") or r.get("marketSlug") or r.get("market_id")
        if not slug or slug in seen:
            continue
        if r.get("closed") or r.get("archived"):
            continue
        # `active` is the listing's own openness flag. Absent means
        # unknown, and unknown is not a reason to drop a market before
        # anyone has read its book.
        if r.get("active") is False:
            continue
        seen.add(slug)
        out.append({"slug": slug,
                    "outcome_leg": (r.get("outcome") or r.get("outcomeLeg")
                                    or r.get("outcome_leg")),
                    "event_slug": r.get("eventSlug"),
                    "title": r.get("title")})
    out.sort(key=lambda c: c["slug"])
    return out


def sample_seed() -> str:
    raw = os.environ.get(SAMPLE_SEED_ENV)
    return raw.strip() if raw and raw.strip() else DEFAULT_SAMPLE_SEED


def family_of(cand: dict) -> str:
    """A COARSE NAMING HEURISTIC, not the venue's taxonomy.

    The venue publishes no family field. `eventSlug` is the closest
    thing it does publish, so it is preferred where present; otherwise
    the first three dash-separated tokens of the slug are used, which
    is what separated `aachc-mlb-bavg-...` from everything else in the
    2026-09-22 listing.

    This is reported so a reader can see whether a sample was drawn
    from one corner of the listing or spread across it. It is NOT a
    claim that these groups are the venue's own categories, and no
    selection decision is made from it.
    """
    ev = cand.get("event_slug")
    if ev:
        return "event:%s" % ev
    slug = str(cand.get("slug") or "")
    parts = [p for p in slug.split("-") if p]
    if not parts:
        return "slug:"
    return "slug:%s" % "-".join(parts[:3])


def _order_key(seed: str, slug: str) -> bytes:
    """A keyed hash, not `random.shuffle`.

    `random` is reproducible only for one PRNG implementation; a keyed
    digest of the slug is reproducible anywhere, which is what lets a
    reviewer regenerate this sample from the seed and the listing alone.
    """
    return hashlib.blake2b(slug.encode("utf-8"), digest_size=16,
                           key=seed.encode("utf-8")[:64]).digest()


def order_candidates(candidates, *, seed: str | None = None) -> dict:
    """The candidate list PERMUTED DETERMINISTICALLY by `seed`.

    Returns the permuted list plus the sampling record: the seed, the
    population it was drawn over, and the family distribution of the
    population. What the probe actually reaches is a PREFIX of the
    permuted list, so `sample_report` below describes that prefix
    against this population.
    """
    seed = seed or sample_seed()
    cands = [c for c in (candidates or []) if c.get("slug")]
    ordered = sorted(cands, key=lambda c: (_order_key(seed, c["slug"]),
                                           c["slug"]))
    fam: dict = {}
    for c in cands:
        f = family_of(c)
        fam[f] = fam.get(f, 0) + 1
    return {
        "ordered": ordered,
        "seed": seed,
        "order_version": SAMPLE_ORDER_VERSION,
        "population": len(cands),
        "population_families": len(fam),
        "population_family_counts": fam,
        "scope": "WITHIN THE BOUNDED LISTING ONLY. The listing is a "
                 "prefix of the venue; this permutation does not make "
                 "it a sample of the venue.",
    }


def sample_report(ordering: dict, slugs) -> dict:
    """What the probe actually reached, against the population it was
    drawn from. Coverage, family spread, and the fact that neither is
    a representativeness claim."""
    pop_fam = dict(ordering.get("population_family_counts") or {})
    by_slug = {c["slug"]: c for c in ordering.get("ordered") or []}
    got = [s for s in (slugs or []) if s in by_slug]
    fam: dict = {}
    for s in got:
        f = family_of(by_slug[s])
        fam[f] = fam.get(f, 0) + 1
    pop = int(ordering.get("population") or 0)
    return {
        "seed": ordering.get("seed"),
        "order_version": ordering.get("order_version"),
        "population": pop,
        "sampled": len(got),
        "coverage_of_listing": (round(len(got) / pop, 6) if pop else None),
        "population_families": len(pop_fam),
        "sampled_families": len(fam),
        "sampled_family_counts": fam,
        "largest_sampled_family_share": (
            round(max(fam.values()) / len(got), 4) if got else None),
        "establishes": "coverage within the bounded listing",
        "does_not_establish": (
            "representativeness of the venue, of any market family, or "
            "of any period other than the one observed"),
    }


def _market_data(resp):
    """`{"marketData": {...}}` -> the inner object, or None.

    Real bodies are wrapped. A response that is not wrapped, or whose
    `marketData` is not an object, is NOT an answer and is named as
    such rather than degraded into an empty market.
    """
    if not isinstance(resp, dict):
        return None
    md = resp.get("marketData")
    return md if isinstance(md, dict) else None


def row_from_bbo(slug: str, md: dict, *, outcome_leg=None) -> dict:
    """One verified BBO payload -> a row `bettor_universe.assess` reads.

    The field spellings are the ones the rule already looks up, so the
    rule itself is untouched. Prices stay as the venue's `Amount`
    objects (`{"value": "0.0100", "currency": "USD"}`) because
    `assess`'s own parser unwraps `value`; re-formatting them here
    would be a second place for a rounding decision to live.

    `sharesTraded` is passed through verbatim. An empty string is
    carried as an empty string and becomes `None` in the rule, which
    counts it as NO_TRADED_VOLUME_FIGURE rather than as zero volume.
    """
    return {
        "slug": slug,
        "market_id": slug,
        # The rule's spellings: bestBid/bestAsk and venue_state.
        "bestBid": md.get("bestBid"),
        "bestAsk": md.get("bestAsk"),
        "sharesTraded": md.get("sharesTraded"),
        "venue_state": md.get("state"),
        # Level COUNTS, not share quantities -- measured against the
        # paired book read on 29,784 of 30,590 captures. Carried for
        # sizing context; `assess` reads `bidDepth` as top_of_book_qty
        # and treats it as a bound, never as executable size.
        "bidDepth": md.get("bidDepth"),
        "askDepth": md.get("askDepth"),
        "outcome_leg": outcome_leg,
        "enriched_from": "markets.bbo",
        "probe": PROBE_VERSION,
    }


def _status_of(exc) -> int | None:
    """The HTTP status behind an SDK exception, if it carries one.

    Read by duck typing rather than by importing the SDK's error
    classes, so this module keeps working if the SDK renames them --
    and so a 429 raised as a plain `APIStatusError` is still a 429.
    """
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code if isinstance(code, int) else None


def _retry_after_s(exc) -> float | None:
    """`Retry-After` off the response, in seconds, VERBATIM.

    Never shortened. The caller decides whether to wait it out or to
    suspend; this function's job is to report what the server asked
    for, not to negotiate it down.

    Only the delta-seconds form is read. An HTTP-date is valid too and
    is NOT parsed here: getting it wrong means either hammering a
    limiter or sleeping until tomorrow, and falling back to our own
    bounded schedule is the safer of the three.
    """
    resp = getattr(exc, "response", None)
    hdrs = getattr(resp, "headers", None)
    if hdrs is None:
        return None
    try:
        raw = hdrs.get("retry-after") or hdrs.get("Retry-After")
    except Exception:                                      # noqa: BLE001
        return None
    if raw is None:
        return None
    try:
        secs = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if secs < 0:
        return None
    return secs


_RL_EVENTS_MAX = 64


def _note_429(acct: dict, pacer, slug, retry_after_s, *, where: str) -> None:
    """One HTTP 429, with what THIS PROCESS was doing at that instant.

    WHY THIS IS NOT AN ATTRIBUTION. The venue counts requests per
    credential, per address, per endpoint, or by some combination it
    has not published. This process can see only its own starts, so
    `observed_*` below is a LOWER BOUND on what the limiter counted.
    Other workers on the same service, anything else holding the same
    credential, a shared egress address, and an endpoint-specific
    ceiling are all invisible from here and all remain live
    explanations. These numbers narrow the question; they do not
    answer it.
    """
    ev = acct.setdefault("rate_limit_events", [])
    if len(ev) >= _RL_EVENTS_MAX:
        acct["rate_limit_events_dropped"] = \
            acct.get("rate_limit_events_dropped", 0) + 1
        return
    now = time.monotonic()
    first = getattr(pacer, "first_start_at", None)
    ev.append({
        "where": where,
        "slug": slug,
        "at_s_into_pacing": (None if first is None
                             else round(now - first, 3)),
        "retry_after_s": retry_after_s,
        "observed_starts_last_1s": pacer.starts_within(1.0, now=now),
        "observed_starts_last_10s": pacer.starts_within(10.0, now=now),
        "observed_starts_last_60s": pacer.starts_within(60.0, now=now),
        "observed_inflight": getattr(pacer, "inflight", None),
        "configured_max_rps": getattr(pacer, "max_rps", None),
        "attribution": "NOT ESTABLISHED -- this process's own starts "
                       "only; shared credential, shared egress address "
                       "and endpoint-specific limits are not visible "
                       "from here",
    })


def _read_one_sync(client, slug: str) -> dict:
    """One BBO call. Never raises; names what happened.

    A 429 is reported rather than retried HERE -- the retry belongs in
    the async caller, which holds the pacer that has to be slowed for
    every other task too.
    """
    fn = getattr(getattr(client, "markets", None), "bbo", None)
    if fn is None:
        return {"slug": slug, "status": P_READ_FAILED,
                "detail": "client.markets.bbo is absent"}
    try:
        resp = fn(slug)
    except Exception as exc:                               # noqa: BLE001
        code = _status_of(exc)
        if code == 429:
            return {"slug": slug, "status": P_RATE_LIMITED,
                    "detail": "HTTP 429", "http_status": 429,
                    "retry_after_s": _retry_after_s(exc)}
        return {"slug": slug, "status": P_READ_FAILED,
                "detail": type(exc).__name__, "http_status": code}
    md = _market_data(resp)
    if md is None:
        return {"slug": slug, "status": P_NO_PAYLOAD,
                "detail": "no marketData object"}
    return {"slug": slug, "status": P_OK, "marketData": md}


async def probe(client, candidates, *, offset: int = 0,
                batch: int | None = None,
                concurrency: int | None = None,
                max_rps: float | None = None,
                timeout_s: float = PROBE_TIMEOUT_S,
                remaining: int | None = None,
                max_distinct: int | None = None,
                reserve=None, pacer=None,
                should_stop=None, sleep=None) -> dict:
    """Enrich one deterministic, RATE-LIMITED slice of the candidates.

    `reserve(kind, slug)` IS THE CEILING. It is awaited before every
    request and must answer `{"ok": bool, "why": str, "new": bool}`.
    Nothing is dispatched without a grant, and `reserve=None` refuses
    everything -- an unreserved probe is the defect this exists to
    prevent, so absence is not a free pass.

    That replaces counting after the fact, which failed three ways
    (measured 2026-09-22): the charge sat below an early return so 40
    reads recorded nothing; a crash between dispatch and charge left the
    row untouched and reusable; and the cap counted SUCCESSES, so 30
    failed reads bought a further round -- 70 distinct markets against a
    ceiling of 40 in a single lifetime.

    `max_distinct` is now only a WINDOW HINT -- how many candidates it
    is worth putting in front of the reserver. It is not the bound. The
    bound is the durable row, and it is enforced one request at a time.

    `remaining` clamps the window to the part of the candidate set this
    sweep has not read yet. WITHOUT IT THE LAST ROUND WRAPS: 400
    candidates at a batch of 240 read 240 then 240 again, the second
    round crossing the end and re-reading the first 80 markets. The
    de-duplication by slug hid that in the COUNTS -- 400 distinct, as
    reported -- while the venue still received 480 requests. Those 80
    were the overlap, and they were waste.

    `should_stop` is awaited between reads so a stop does not have to
    wait out the whole batch. The caller's callback does its own
    caching, so this costs nothing per read.

    Returns the enriched rows, the next cursor, and an accounting that
    keeps ATTEMPTS, RETRIES, DISTINCT MARKETS and SUCCESSFUL
    ENRICHMENTS apart -- four numbers that an earlier version collapsed
    into one and could not then reconcile. `distinct_markets` now counts
    markets a reservation PAID FOR, not markets offered to the window.
    """
    pace = configured_pace()
    max_rps = pace["max_rps"] if max_rps is None else max_rps
    concurrency = pace["concurrency"] if concurrency is None else concurrency
    if batch is None:
        batch = int(_env_float(BATCH_ENV, PROBE_BATCH))
    batch = max(1, int(batch))
    _sleep = sleep or asyncio.sleep

    cands = list(candidates or [])
    n = len(cands)
    empty_acct = {"attempts": 0, "retries": 0, "rate_limited": 0,
                  "distinct_markets": 0, "enriched": 0,
                  "refused_distinct": 0, "refused_attempt": 0,
                  "by_status": {}}
    if n == 0:
        return {"rows": [], "next_offset": 0, "probed": 0,
                "slugs_probed": [], "stopped": False,
                "coverage": dict(empty_acct, candidates=0),
                "accounting": empty_acct, "pace": pace}

    # THE WINDOW NEVER WRAPS PAST WHAT THIS SWEEP STILL OWES, and never
    # past the probe's LIFETIME budget. `remaining` is the sweep;
    # `max_distinct` is the whole probe, restarts included, and is the
    # smaller of the two whenever a probe is bounded.
    room = n if remaining is None else max(0, min(n, int(remaining)))
    if max_distinct is not None:
        room = min(room, max(0, int(max_distinct)))
    width = min(batch, room)
    start = offset % n
    window, seen = [], set()
    for i in range(width):
        c = cands[(start + i) % n]
        if c["slug"] in seen:          # a candidate list shorter than
            continue                   # the batch cannot be read twice
        seen.add(c["slug"])
        window.append(c)

    sem = asyncio.Semaphore(max(1, concurrency))
    # THE PACER IS SHARED WHEN THE CALLER SHARES IT. `_discover` passes
    # ONE pacer through the listing and every enrichment round, so the
    # process has a single outbound rate rather than one per call site.
    # The listing used to have none at all.
    pacer = pacer if pacer is not None else Pacer(max_rps, sleep=_sleep,
                                                  name="enrichment")
    acct = {"attempts": 0, "retries": 0, "rate_limited": 0,
            # WHEN each 429 arrived and WHAT THIS PROCESS WAS DOING at
            # that moment. Recorded because "we were paced at 0.25
            # req/s" is a setting; these are observations, and they are
            # still only observations OF THIS PROCESS.
            "rate_limit_events": [],
            # DISPATCHED, not offered. `window` is what we would have
            # liked to read; this counts the markets a reservation
            # actually paid for, which is the number the ceiling is
            # written in.
            "distinct_markets": 0, "enriched": 0,
            "refused_distinct": 0, "refused_attempt": 0,
            "by_status": {}}
    stopped = {"flag": False}
    suspended = {"until": 0.0}
    done = {"n": 0}

    # ── RESERVATION, OR NO REQUEST ───────────────────────────────────
    #
    # `reserve` is injected. Passing None means NOTHING MAY BE
    # DISPATCHED: an unreserved probe is the bug this repair exists to
    # remove, so the default is refusal rather than a free pass. The
    # loop hands in a closure bound to the probe identity; the unit
    # tests hand in doubles.
    _EXHAUSTS = ("RESERVATION_EXHAUSTED",)

    async def _reserve(kind, slug):
        if reserve is None:
            return {"ok": False, "why": "NO_RESERVER",
                    "exhausted_probe": True}
        try:
            r = await reserve(kind, slug)
        except Exception as exc:                           # noqa: BLE001
            # AN UNANSWERABLE RESERVATION IS A REFUSAL. Same rule as
            # the control: we cannot show the request is funded.
            log.warning("bettor_universe_probe: reservation for %s "
                        "raised (%s); not dispatching",
                        slug, type(exc).__name__)
            return {"ok": False, "why": "RESERVATION_RAISED_%s"
                                        % type(exc).__name__,
                    "exhausted_probe": False}
        ok = bool(r) and bool(r.get("ok"))
        why = (r or {}).get("why")
        return {"ok": ok, "why": why, "new": bool((r or {}).get("new")),
                "exhausted_probe": why in _EXHAUSTS}

    async def _reserve_distinct(slug):
        r = await _reserve("distinct", slug)
        if r["ok"] and r["new"]:
            # Counted here, once, and only when a slot was actually
            # taken -- an idempotent re-grant is not a second market.
            acct["distinct_markets"] += 1
        return r

    reserve_distinct = _reserve_distinct

    async def reserve_attempt(slug):
        return await _reserve("bbo_attempt", slug)

    async def _check_stop() -> bool:
        if should_stop is None or stopped["flag"]:
            return stopped["flag"]
        done["n"] += 1
        if done["n"] % PROBE_STOP_CHECK_EVERY:
            return False
        try:
            if await should_stop():
                stopped["flag"] = True
        except Exception as exc:                           # noqa: BLE001
            # AN UNANSWERABLE STOP CHECK STOPS. Same rule as the
            # control itself: we cannot show we are still permitted.
            log.warning("bettor_universe_probe: stop check failed (%s); "
                        "abandoning the round", type(exc).__name__)
            stopped["flag"] = True
        return stopped["flag"]

    async def _one(c):
        if stopped["flag"]:
            return {"slug": c["slug"], "status": P_STOPPED}
        async with sem:
            # THE DISTINCT SLOT, BEFORE THIS MARKET'S FIRST REQUEST.
            # Idempotent in the slug, so the retries below share the one
            # slot. Refused means the ceiling is reached and this market
            # is never asked about at all.
            got = await reserve_distinct(c["slug"])
            if not got["ok"]:
                acct["refused_distinct"] += 1
                if got.get("exhausted_probe"):
                    # The allowance is gone for good, not just for this
                    # market. Stop the round rather than walking the
                    # rest of the window to collect identical refusals.
                    stopped["flag"] = True
                return {"slug": c["slug"], "status": P_NO_DISTINCT,
                        "detail": got.get("why")}
            for attempt in range(PROBE_MAX_RETRIES + 1):
                if stopped["flag"]:
                    return {"slug": c["slug"], "status": P_STOPPED}
                # AN ATTEMPT SLOT FOR EVERY ATTEMPT, RETRIES INCLUDED.
                # Reserved before the pacer, so a request that cannot be
                # paid for never even waits its turn.
                a = await reserve_attempt(c["slug"])
                if not a["ok"]:
                    acct["refused_attempt"] += 1
                    if a.get("exhausted_probe"):
                        stopped["flag"] = True
                    if attempt:
                        # Already read once; keep the earlier verdict
                        # rather than downgrading it to "not probed".
                        return {"slug": c["slug"],
                                "status": P_RATE_LIMITED,
                                "detail": "retry unfunded: %s"
                                          % a.get("why")}
                    return {"slug": c["slug"], "status": P_NO_ATTEMPT,
                            "detail": a.get("why")}
                await pacer.acquire()
                acct["attempts"] += 1
                if attempt:
                    acct["retries"] += 1
                try:
                    r = await asyncio.wait_for(
                        asyncio.to_thread(_read_one_sync, client, c["slug"]),
                        timeout=timeout_s)
                except asyncio.TimeoutError:
                    r = {"slug": c["slug"], "status": P_TIMEOUT,
                         "detail": "%.1fs" % timeout_s}
                except Exception as exc:                   # noqa: BLE001
                    r = {"slug": c["slug"], "status": P_READ_FAILED,
                         "detail": type(exc).__name__}
                finally:
                    pacer.release()
                if r["status"] != P_RATE_LIMITED:
                    await _check_stop()
                    return r
                acct["rate_limited"] += 1
                # WHAT THIS PROCESS WAS DOING WHEN THE VENUE SAID NO.
                # Measured at the moment of the refusal, so the claim
                # "we were throttled at rate X" can be checked rather
                # than read off the configuration.
                _note_429(acct, pacer, c["slug"], r.get("retry_after_s"),
                          where="enrichment")
                asked = r.get("retry_after_s")
                if asked is not None and asked > PROBE_SUSPEND_ABOVE_S:
                    # THE SERVER ASKED FOR LONGER THAN THIS ROUND
                    # SHOULD LIVE. Retrying sooner would ignore the one
                    # explicit instruction a limiter ever gives us, in
                    # the direction that makes things worse. Abandon
                    # the round; the loop's own backoff decides when to
                    # come back, and it is never sooner than this.
                    suspended["until"] = max(suspended["until"], asked)
                    stopped["flag"] = True
                    log.error("bettor_universe_probe: HTTP 429 on %s "
                              "with Retry-After %.0fs, above the %.0fs "
                              "suspension threshold; ABANDONING the "
                              "round rather than retrying sooner",
                              c["slug"], asked, PROBE_SUSPEND_ABOVE_S)
                    return {"slug": c["slug"], "status": P_SUSPENDED,
                            "detail": "Retry-After %.0fs" % asked,
                            "retry_after_s": asked}
                if attempt >= PROBE_MAX_RETRIES:
                    await _check_stop()
                    return r
                hold = asked
                if hold is None:
                    hold = PROBE_RETRY_BACKOFF_S[
                        min(attempt, len(PROBE_RETRY_BACKOFF_S) - 1)]
                # SLOW THE WHOLE ROUND, not just this task: a 429 is
                # about the round's rate, not about this market. The
                # hold is applied ONLY here -- an explicit sleep beside
                # it would make this task wait the penalty twice, once
                # on its own and again when the pacer caught up.
                pacer.penalise(hold)
                log.warning("bettor_universe_probe: HTTP 429 on %s; "
                            "holding the whole round %.0fs (attempt %d "
                            "of %d)", c["slug"], hold, attempt + 1,
                            PROBE_MAX_RETRIES)
            return {"slug": c["slug"], "status": P_RATE_LIMITED}

    results = await asyncio.gather(*[_one(c) for c in window])

    leg = {c["slug"]: c.get("outcome_leg") for c in window}
    rows = []
    for r in results:
        acct["by_status"][r["status"]] = \
            acct["by_status"].get(r["status"], 0) + 1
        if r["status"] == P_OK:
            rows.append(row_from_bbo(r["slug"], r["marketData"],
                                     outcome_leg=leg.get(r["slug"])))
    acct["enriched"] = len(rows)
    acct["paced_wait_s"] = round(pacer.waited_s, 2)
    # MEASURED, beside the configured pace rather than instead of it.
    acct["request_telemetry"] = pacer.telemetry()

    return {
        "rows": rows,
        "next_offset": (start + len(window)) % n,
        "probed": len(window),
        "slugs_probed": [c["slug"] for c in window],
        "stopped": stopped["flag"],
        # A SUSPENSION IS NOT AN ORDINARY STOP. It carries the delay
        # the SERVER asked for, so the caller can refuse to come back
        # sooner than that.
        "suspended_for_s": suspended["until"] or None,
        "accounting": acct,
        # COVERAGE, not eligibility. Nothing here says a market is
        # unsuitable; it says how much of the candidate set we have
        # actually read, and at what cost.
        "coverage": dict(acct, candidates=n, probed=len(window)),
        "pace": pace,
    }


_SUMMED = ("probed", "enriched", "attempts", "retries", "rate_limited",
           "distinct_markets", "paced_wait_s",
           "refused_distinct", "refused_attempt")


def merge_coverage(a: dict, b: dict) -> dict:
    """Coverage across rounds, so a report can say how far we got.

    `candidates` is the size of the set, not a running total, so it is
    carried rather than added. Everything else is a count of things
    that happened and is summed.
    """
    a = a or {}
    b = b or {}
    st = dict(a.get("by_status") or {})
    for k, v in (b.get("by_status") or {}).items():
        st[k] = st.get(k, 0) + v
    out = {"candidates": b.get("candidates", a.get("candidates", 0)),
           "by_status": st}
    for k in _SUMMED:
        out[k] = round(a.get(k, 0) + b.get(k, 0), 2)
    # NEITHER OF THESE IS A COUNT, so neither is summed.
    #
    # `rate_limit_events` is a list of observations and is
    # CONCATENATED, bounded. `request_telemetry` comes from a pacer
    # that `_discover` SHARES across every round, so the later reading
    # is already cumulative and the earlier one is a prefix of it --
    # summing them would double-count. A caller that does not share a
    # pacer gets the last round's telemetry only, and the
    # `requests_started` figure beside it says so.
    ev = list(a.get("rate_limit_events") or []) + \
        list(b.get("rate_limit_events") or [])
    if ev:
        out["rate_limit_events"] = ev[:_RL_EVENTS_MAX]
        if len(ev) > _RL_EVENTS_MAX:
            out["rate_limit_events_dropped"] = len(ev) - _RL_EVENTS_MAX
    tel = b.get("request_telemetry") or a.get("request_telemetry")
    if tel:
        out["request_telemetry"] = tel
    return out


def describe() -> dict:
    pace = configured_pace()
    return {
        "probe": PROBE_VERSION,
        "rate_limit": {
            "applicable_limit": "NOT_ESTABLISHED",
            "published_by_venue": "NOT_ESTABLISHED",
            "what_the_capture_shows": (
                "no rate-limit header in the 65,980 responses WE "
                "captured, and no 429. Neither establishes that no "
                "limit is published: our sample never exceeded 1 "
                "req/s, so zero 429s is what a limit we never "
                "approached would also look like"),
            "survived_pattern": ("1 request in flight, 0.030-0.285 "
                                 "req/s sustained, 61,178 successful "
                                 "reads across 8 days -- an existence "
                                 "proof for one pattern, NOT a "
                                 "boundary"),
            "enforced_max_rps": pace["max_rps"],
            "enforced_concurrency": pace["concurrency"],
            "above_demonstrated_envelope":
                pace["above_demonstrated_envelope"],
            "credential_context": ("the evidence was collected "
                                   "UNAUTHENTICATED against the public "
                                   "gateway; the worker is "
                                   "authenticated, and authenticated "
                                   "limits are NOT ESTABLISHED in "
                                   "either direction"),
            "on_429": "honour Retry-After VERBATIM up to %.0fs and "
                      "SUSPEND the round above it; else %s, "
                      "%d retries, then give up on that market"
                      % (PROBE_SUSPEND_ABOVE_S,
                         "/".join("%.0f" % s
                                  for s in PROBE_RETRY_BACKOFF_S),
                         PROBE_MAX_RETRIES),
        },
        "stop_during_acquisition": ("checked every %d completed reads"
                                    % PROBE_STOP_CHECK_EVERY),
        "listing_bounds": {"max_pages": LISTING_MAX_PAGES,
                           "max_retries": LISTING_MAX_RETRIES,
                           "backoff_s": list(LISTING_RETRY_BACKOFF_S),
                           "timeout_s": LISTING_TIMEOUT_S,
                           "counted": "separately from enrichment",
                           # ONE UNIT PER OUTBOUND REQUEST, not per
                           # invocation. The previous build reserved
                           # one for up to six.
                           "reservation_unit": "ONE_PER_OUTBOUND_REQUEST",
                           "max_outbound_requests":
                               LISTING_MAX_PAGES * (LISTING_MAX_RETRIES + 1),
                           "paced": True,
                           "stop_after_consecutive_429":
                               LISTING_MAX_CONSECUTIVE_429},
        "sdk_request_accounting": {
            "one_wrapper_call_is_one_http_request": LISTING_PAGE_IS_ONE_REQUEST,
            "verified_against": "the installed polymarket_us package",
            "checks": ["Markets.list has no loop and no pagination",
                       "httpx.Client built with no transport= and no "
                       "retries= (httpx default retries=0)",
                       "follow_redirects appears nowhere (httpx default "
                       "False; a 3xx raises rather than re-requesting)",
                       "_request contains no sleep; a 429 raises "
                       "immediately"],
            "if_this_changes": ("the listing budget silently "
                                "undercounts again; asserted by "
                                "TestTheSDKAddsNoHiddenRequests"),
        },
        "sampling": {
            "order_version": SAMPLE_ORDER_VERSION,
            "seed_env": SAMPLE_SEED_ENV,
            "seed": sample_seed(),
            "method": ("keyed BLAKE2b digest of the slug -- "
                       "reproducible from the seed and the listing "
                       "alone, unlike random.shuffle"),
            "establishes": "coverage WITHIN the bounded listing",
            "does_not_establish": ("representativeness of the venue; "
                                   "the listing is a page-bounded "
                                   "prefix and a permutation of a "
                                   "prefix is still a prefix"),
            "family_of": ("a COARSE NAMING HEURISTIC (eventSlug, else "
                          "the first three dash-separated tokens) -- "
                          "not the venue's taxonomy, and no selection "
                          "decision is made from it"),
        },
        "request_telemetry": {
            "measured": ["requests_started", "observed_mean_rps",
                         "densest_1s_starts", "densest_10s_starts",
                         "max_concurrent_inflight", "penalties_s"],
            "per_429": ["observed_starts_last_1s/_10s/_60s",
                        "observed_inflight", "retry_after_s"],
            "scope": ("THIS PROCESS ONLY. Other workers, anything else "
                      "holding the same credential, a shared egress "
                      "address and endpoint-specific ceilings are "
                      "invisible from here"),
            "attribution_of_a_429": "NOT_ESTABLISHED",
        },
        "listing_supplies": ["slug", "outcome", "active", "closed",
                             "archived", "eventSlug", "title"],
        "listing_does_not_supply": ["bestBid", "bestAsk", "sharesTraded",
                                    "state"],
        "enrichment_source": "markets.bbo(slug)['marketData']",
        "enrichment_supplies": ["bestBid", "bestAsk", "sharesTraded",
                                "state", "bidDepth", "askDepth"],
        "evidence": ("30,590 verbatim HTTP 200 bodies under "
                     "research/evidence/capture/run85_phase2_segment_*/"
                     "request_log.jsonl.gz; sharesTraded present and "
                     "non-empty in all of them"),
        "websocket_status": ("SDK-DECLARED ONLY -- no captured PMUS "
                             "frame in this repo to check it against"),
        "cost": "one call per market; bounded by PROBE_BATCH per round",
        "rotation": ("deterministic over candidates in the SEEDED "
                     "order (%s), not the slug sort -- which delivered "
                     "one market family on 2026-09-22"
                     % SAMPLE_ORDER_VERSION),
        "coverage_is_not_eligibility": True,
        "probe_failure_reasons": [P_READ_FAILED, P_NO_PAYLOAD, P_TIMEOUT,
                                  P_NOT_PROBED],
    }
