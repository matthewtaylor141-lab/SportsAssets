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
import logging

log = logging.getLogger(__name__)

PROBE_VERSION = "BETTOR_UNIVERSE_PROBE_V1"

# One enrichment round. 3,000 candidates is 3,000 BBO calls, so a round
# reads a slice and the next round reads the next slice.
PROBE_BATCH = 240
# Concurrent BBO reads inside a round. The SDK is synchronous, so each
# one occupies a thread; this is the ceiling on that pool.
PROBE_CONCURRENCY = 8
# Rounds run back to back at startup, to reach a decidable universe
# before the loop begins rather than after it has reported nothing.
#
# SEVEN, FROM THE MEASURED ADMIT RATE. Over the 30,590-body capture the
# frozen rule admits 6.35%, so filling the 100-market cap needs about
# 1,575 enriched markets: 1,680 at seven rounds of 240. Four rounds
# (960) would have yielded about 61 and the cap would never bind -- a
# smaller universe than the rule would have chosen, for no reason
# anyone could see in the output. The loop stops early anyway as soon
# as the rule reports a full universe, or as soon as the candidate set
# has been swept once.
PROBE_ROUNDS_AT_START = 7
# A single read that will not answer in this long is abandoned so one
# slow market cannot hold a round open.
PROBE_TIMEOUT_S = 8.0

# Why a candidate has no enriched row. NONE OF THESE IS AN EXCLUSION
# REASON: they are coverage facts, counted apart from the rule's own
# reasons so a read failure can never be read as a market judgement.
P_OK = "ENRICHED"
P_READ_FAILED = "PROBE_READ_FAILED"
P_NO_PAYLOAD = "PROBE_NO_MARKET_DATA"
P_TIMEOUT = "PROBE_TIMED_OUT"
P_NOT_PROBED = "NOT_PROBED_YET"


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


def _read_one_sync(client, slug: str) -> dict:
    """One BBO call. Never raises; names what happened."""
    fn = getattr(getattr(client, "markets", None), "bbo", None)
    if fn is None:
        return {"slug": slug, "status": P_READ_FAILED,
                "detail": "client.markets.bbo is absent"}
    try:
        resp = fn(slug)
    except Exception as exc:                               # noqa: BLE001
        return {"slug": slug, "status": P_READ_FAILED,
                "detail": type(exc).__name__}
    md = _market_data(resp)
    if md is None:
        return {"slug": slug, "status": P_NO_PAYLOAD,
                "detail": "no marketData object"}
    return {"slug": slug, "status": P_OK, "marketData": md}


async def probe(client, candidates, *, offset: int = 0,
                batch: int = PROBE_BATCH,
                concurrency: int = PROBE_CONCURRENCY,
                timeout_s: float = PROBE_TIMEOUT_S) -> dict:
    """Enrich one deterministic slice of the candidates.

    Returns the enriched rows, the next cursor, and a coverage report
    that is kept apart from anything the selection rule decides.
    """
    cands = list(candidates or [])
    n = len(cands)
    if n == 0:
        return {"rows": [], "next_offset": 0, "probed": 0,
                "coverage": {"candidates": 0, "probed": 0, "enriched": 0,
                             "by_status": {}}, "slugs_probed": []}

    start = offset % n
    window = [cands[(start + i) % n] for i in range(min(batch, n))]
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(c):
        async with sem:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(_read_one_sync, client, c["slug"]),
                    timeout=timeout_s)
            except asyncio.TimeoutError:
                return {"slug": c["slug"], "status": P_TIMEOUT,
                        "detail": "%.1fs" % timeout_s}
            except Exception as exc:                       # noqa: BLE001
                return {"slug": c["slug"], "status": P_READ_FAILED,
                        "detail": type(exc).__name__}

    results = await asyncio.gather(*[_one(c) for c in window])

    leg = {c["slug"]: c.get("outcome_leg") for c in window}
    rows, by_status = [], {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        if r["status"] == P_OK:
            rows.append(row_from_bbo(r["slug"], r["marketData"],
                                     outcome_leg=leg.get(r["slug"])))
    return {
        "rows": rows,
        "next_offset": (start + len(window)) % n,
        "probed": len(window),
        "slugs_probed": [c["slug"] for c in window],
        # COVERAGE, not eligibility. Nothing here says a market is
        # unsuitable; it says how much of the candidate set we have
        # actually read.
        "coverage": {"candidates": n, "probed": len(window),
                     "enriched": len(rows), "by_status": by_status},
    }


def merge_coverage(a: dict, b: dict) -> dict:
    """Coverage across rounds, so a report can say how far we got."""
    a = a or {}
    b = b or {}
    st = dict(a.get("by_status") or {})
    for k, v in (b.get("by_status") or {}).items():
        st[k] = st.get(k, 0) + v
    return {"candidates": b.get("candidates", a.get("candidates", 0)),
            "probed": a.get("probed", 0) + b.get("probed", 0),
            "enriched": a.get("enriched", 0) + b.get("enriched", 0),
            "by_status": st}


def describe() -> dict:
    return {
        "probe": PROBE_VERSION,
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
        "rotation": "deterministic over candidates sorted by slug",
        "coverage_is_not_eligibility": True,
        "probe_failure_reasons": [P_READ_FAILED, P_NO_PAYLOAD, P_TIMEOUT,
                                  P_NOT_PROBED],
    }
