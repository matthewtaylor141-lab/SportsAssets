"""Hourly reconciliation: Path B (Data API) is the source of truth.

Sweeps recent trades per tracked wallet from the Data API and re-runs them
through the pipeline. Anything already ingested is a dedupe no-op; anything
missed (e.g. during a WS outage that outlived the poller's 100-trade window)
is ingested late and counted as `missed`.
"""

from __future__ import annotations

import json
import logging
import os
import time

import httpx

from ..config import settings
from ..db import get_pool, heartbeat
from ..ratelimit import polite_get
from .dedupe import key_fields_valid
from .pipeline import ingest_trade_result
from .poller import _sport_for_condition, parse_data_api_trade

log = logging.getLogger(__name__)

# Continuity-witness depth (rounds 17-18): the rows each page re-requests
# from the previous page's tail. See the walk-loop comment for why 3.
OVERLAP_K = 3

# Feed-ordering tolerance (round 21): the venue feed is newest-first;
# a usable row whose ts exceeds its predecessor's by more than this is
# proof the ordering premise is broken and the walk's span testimony
# is void. Same-second bundles (equal ts) and small re-indexing jitter
# pass; a genuinely misplaced row does not.
ORDER_TOL_S = 120
# A served row this far past the walk's own wallclock is feed
# corruption, never a trade (fleet r40): it inflated cov->newest and,
# tracking wallclock across two walks, forged the round-39 distinct-
# newest testimony. Generous vs real venue clock skew.
FUTURE_SKEW_S = 300

# Border-witness page size (round 21): rows a clean walk verifies PAST
# the depth cap so the cap-tail rows have ordered successors below
# them. One venue page.
BORDER_PAGE = 100

# TAKER CENSUS (to-a-tee program Phase 8, owner order 2026-09-02, "I
# want us to match everything ... mirror the whales to a tee"): nowhere
# in the system records whether HIS fill was a maker or a taker fill —
# every Data-API caller asks takerOnly=false so maker fills are
# INCLUDED but nothing marks which rows are which (timing lens §1).
# The venue-truth reading is the one the rn1_match pull used: the same
# /trades endpoint with takerOnly=true, intersected by dedupe_key with
# the rows the walk just served. It lives HERE, in the hourly walk, and
# never in the poller: data_api_max_rps is one budget shared by every
# Data-API caller (config.py:53), and one extra request per whale per
# HOUR is noise where one per poll would not be. Measurement only — no
# rule keys on the column (revision 1 D3: the per-flag IOC was killed).
TAKER_PAGE = 100
# The reading refuses under this floor (timing lens, market refutation
# F1): if takerOnly=true aggregates one taker order over N makers, the
# served sizes differ from the walk's legs, no key matches, and every
# row would silently read NULL — the census must SAY so (wrote=false)
# rather than label the walk's rows maker=false on a page it did not
# understand. And when the page CLEARS the floor with one aggregated
# row on it (the taker unit's adversarial review, the major): the
# legs of that taker order are exactly the walk rows absent from the
# page, so "absent from the page" alone would label his taker sweep
# maker=false — F1's own ladders (three fills at 0.46/0.46/0.47 inside
# one second, 68% of his dollars) are the shape at stake. The legs and
# the aggregate share the transaction, so a walk row whose tx appears
# on the taker page under a different key reads NULL, never false.
TAKER_MATCH_FLOOR = 0.9
TAKER_CENSUS_KEY = "taker_census"


def _row_ident(raw: dict) -> tuple:
    """Raw-field identity of one served row — the continuity witness
    for overlap pagination (fleet round 17). Raw values, not parsed:
    the witness must match exactly what the venue re-serves."""
    return (
        str(raw.get("transactionHash") or raw.get("txHash") or ""),
        str(raw.get("asset") or raw.get("tokenId") or ""),
        str(raw.get("side", "")), str(raw.get("size", "")),
        str(raw.get("price", "")), str(raw.get("timestamp", "")),
    )


def _tx_ident(tx_hash: str | None) -> str:
    """The transaction identity the census intersects on beside the
    dedupe key: lowercased and stripped exactly as make_dedupe_key
    folds it, so a mixed-case re-serve cannot split one tx in two."""
    return str(tx_hash or "").lower().strip()


def taker_intersection(walk_rows: dict[str, tuple[float, str]],
                       taker_rows: list[tuple[str | None, float | None, str | None]],
                       walk_dirty: bool = False,
                       now: float | None = None,
                       walk_floor: float | None = None) -> dict:
    """Pure half of the taker census: which walk rows the takerOnly
    page proves taker, which it proves maker, which it leaves UNKNOWN,
    and the match_rate that decides whether any proof is admitted.

    `walk_rows` is dedupe_key -> (venue ts, tx identity) for every row
    the walk INGESTED this run (border rows included: they are in the
    table). `taker_rows` is (dedupe_key, ts, tx identity) per served
    page row IN PAGE ORDER, all None for a row the parser or the
    validity gate refused. `walk_floor` is the walk's own testimony
    floor, cov["oldest"] (the oldest ts among the rows that testified
    for span: cap rows, never border rows); None means the caller
    cannot say what the walk verified, and then no span is written at
    all (fail closed — the fourth review).

    matched / eligible, per ROW (not per distinct key): the walk's own
    comment explains raw-identical twins — equal legs of one same-
    second taker bundle collapse to ONE trades row under the dedupe
    key, and both legs must count as matched or the floor would refuse
    the most taker-shaped page there is. Eligible rows are the page
    rows the walk could have served: ts strictly inside the walk's
    reach (a same-second bundle straddling the walk's oldest page is
    half-served, so the tie is excluded unless it matched — a match is
    proof by itself), plus every unusable row (fail closed: a row we
    cannot read counts against the rate, never for it).

    Proof of MAKER: a walk row absent from the taker page is a maker
    fill only inside the span the page actually covers — strictly
    newer than the page's oldest readable taker row (the tie second
    may be half-served). A short page is NOT read as "feed exhausted"
    below that row (the taker unit's adversarial review): the walk's
    own round-7 lesson is that a degraded index serves a truncated
    page with a 200, and a truncated takerOnly page would brand every
    older fill maker=false with the strongest possible evidence. Rows
    older than the page's reach stay NULL — measurement lost, never a
    wrong label. A page with no readable ts proves no span at all.

    Proof withheld (the review's major): a walk row that shares its
    TRANSACTION with any readable taker-page row but did not match by
    key is a leg of a taker order the page served in a different
    shape (one aggregate over N maker legs, or N legs over one
    aggregate) — one matchOrders tx is one taker order, so every leg
    of his in it is a taker leg. Absent-from-the-page is no proof of
    maker there: those rows read NULL (`ambiguous_keys`), never false,
    and never true either — the intersection is by dedupe key, and a
    tx-only match is a shape the venue has not been proven to serve.

    Span testimony VOID (the taker unit's second adversarial review,
    the major): every false above is a SPAN claim — "absent from the
    page inside the page's reach" — and the reach was a bare min()
    over the page's readable ts, the exact inference the walk itself
    was rebuilt around in round 21: sound only under an UNCHECKED
    newest-first premise. One late-indexed OLD taker row served mid-
    page (a durable feed property, per round 21) pulled the floor 30
    days below the page's real reach and branded 672 of 682 walk rows
    maker=false at match_rate 1.0 — the old row sits below the walk's
    own reach, so it is not eligible and the floor could never catch
    it. The page therefore verifies the premise the way the walk does
    (round 42's CUMULATIVE floor, not the adjacent-row check: a re-
    index ramp climbing back in tolerance-sized steps evades the
    adjacent check with unbounded inversion): a readable row whose ts
    exceeds the running minimum by more than ORDER_TOL_S proves the
    page is not newest-first-ordered and its span is void. And the
    walk's own DIRTY counter voids it too: the taker page is the same
    per-wallet index the walk just found degraded (a skipped stub, an
    inverted row, a shifted seam), and a walk row with an inverted ts
    lands inside the page's span by a timestamp the feed itself
    disowned. Under either, `false_keys` and `ambiguous_keys` are
    empty and `span_void` names the reason; `true_keys` stand —
    presence on the takerOnly page is proof by itself, the way a
    dirty walk still INGESTS and only stops testifying. Fail closed:
    a lost measurement (NULL) over a wrong sticky label.

    The page's TAIL (the taker unit's third adversarial review, the
    major): the cumulative check above fires at a SUCCESSOR, and the
    page's last row has none — a late-indexed old row served LAST
    passed it, the bare min() read it as the page's reach, and 672 of
    682 walk rows read maker=false at match_rate 1.0 again, the folded
    shape one row further down. The walk's round-21 fix has a second
    half, (b): a row testifies for span only if ordered successors are
    verified BELOW it, which is why a clean walk buys a border page
    past its cap. The census cannot buy one (one request per whale per
    walk is the budget rule), and excluding the one tail row would only
    move the shape one row up: two late-indexed old rows ordered among
    themselves at the tail floor the span exactly as one did. What the
    census HAS is the walk: a matched row is a row the clean walk
    served, and a late-indexed row inside the walk's window dirties
    the walk at its successor — so the verified chain of successors on
    the page bottoms out at the last matched row the WALK verified,
    and the rows below it — unmatched, and below the walk's reach or
    counted against the rate — are verified by nothing. The unmatched
    tail testifies for the rate and for nothing else.

    Which matched rows the walk verified (the taker unit's fourth
    adversarial review, the major): not every one. "Matched" alone was
    read as "walk-verified", but the walk's own cumulative check fires
    only at a successor, and the walk has a tail with none — the last
    row of the border page (ingested into walk_rows, never a successor
    below it, never extends oldest), or a complete walk's short final
    page. A late-indexed old row served THERE stays clean (dirty=0,
    cov->oldest untouched), and when the takerOnly page serves it too
    it matches by key and floored the span 30 days down: 582 of 682
    walk rows maker=false at match_rate 1.0, 92 of them below the
    page's own reach — the folded blast radius a third time, and every
    false of it sticky. The floor is therefore taken only from matched
    rows the walk's testimony itself covers: at or above `walk_floor`
    (cov["oldest"], the walk's (b): every cap row has the border page's
    ordered successors below it; border rows never extend oldest and
    never floor the census either) AND strictly newer than the walk's
    oldest served second — on a complete walk that second is the short
    final page's last row, which nothing verified (the walk's own
    cov->oldest trusts it, and the census does not); on a capped walk
    it is the border page's tail, which never testified anyway. A
    matched row below either line still counts for the rate and reads
    true (presence on the page is proof by itself); it floors nothing.
    The residual is the walk's own round-22 shape: a run of ordered old
    rows at the END of a complete feed, each verified only by the next
    old row, floors the census exactly as it floors the walk's coverage
    claim — no depth-capped walk of any design can see through a feed
    reordered wholesale at its end. A page with no verified match, or a
    caller that cannot name the walk's floor, proves no span at all
    (the page could never clear the floor anyway). The price is honest
    and cheap: walk rows below the deepest walk-verified taker fill
    read NULL, a lost measurement, never a false.

    Page DIRTY (the third review, its second major): a page row the
    parser or the validity gate refused — the walk's round-11 shape, a
    degraded per-wallet index serving a size-0 / hash-less stub of one
    fill — dropped its TRANSACTION with it, so the tx-share rule above
    switched off for exactly the degraded serving it exists for: a stub
    of his sweep's aggregate row counts once against the rate, 9 exact
    matches still clear 0.9, and the three legs read false. The walk
    records ANY skipped unusable row as dirty and refuses its coverage
    claim outright; the page is held to the same rule: an unreadable
    row, or a readable row dated past `now` + FUTURE_SKEW_S (the walk's
    round-40 corruption bound: no venue clock runs hours ahead, and a
    future head row has no predecessor for the ordering check to see),
    makes the whole span unwritable — `span_void` = "page_dirty" — and
    the row still counts against the rate. Void reasons in order:
    walk_dirty, then page_misordered, then page_dirty.
    """
    out = {"eligible": 0, "matched": 0, "match_rate": None,
           "true_keys": [], "false_keys": [], "ambiguous_keys": [],
           "span_void": None}
    if not walk_rows:
        return out
    if now is None:
        now = time.time()
    walk_lo = min(ts for ts, _tx in walk_rows.values())
    walk_hi = max(ts for ts, _tx in walk_rows.values())
    matched_keys: set[str] = set()
    taker_txs: set[str] = set()
    eligible = matched = 0
    readable_ts: list[float] = []
    last_match = 0                        # readable rows through the last
    #                                       match the WALK verified
    ordered = True
    page_dirty = False
    page_floor: float | None = None       # the running min, in page order
    for key, ts, tx in taker_rows:
        if key is None or ts is None or ts > now + FUTURE_SKEW_S:
            # unreadable, or future-dated corruption: counts against
            # the rate and voids the span (the third review)
            eligible += 1
            page_dirty = True
            continue
        if page_floor is not None and ts > page_floor + ORDER_TOL_S:
            ordered = False               # the newest-first premise is broken
        page_floor = ts if page_floor is None else min(page_floor, ts)
        readable_ts.append(ts)
        if tx:
            taker_txs.add(tx)
        if key in walk_rows:
            eligible += 1
            matched += 1
            matched_keys.add(key)
            if walk_floor is not None and ts >= walk_floor and ts > walk_lo:
                # only a match the walk's testimony covers floors the
                # span: a cap row (the border page verified it), never
                # a border row, never the walk's own oldest second —
                # the tail nothing verified (the fourth review)
                last_match = len(readable_ts)
        elif walk_lo < ts <= walk_hi:
            eligible += 1                 # inside the reach, unmatched
        # else: older than the walk's oldest second, or newer than its
        # newest row (filled between the walk and this page): the walk
        # never had its chance at it — neither for nor against
    out["eligible"] = eligible
    out["matched"] = matched
    out["match_rate"] = (matched / eligible) if eligible else None
    out["true_keys"] = sorted(matched_keys)
    if not readable_ts:
        return out
    if walk_dirty or not ordered or page_dirty:
        # span testimony void (see the docstring): the true keys
        # stand, nothing reads maker, and the census says why
        out["span_void"] = ("walk_dirty" if walk_dirty
                            else "page_misordered" if not ordered
                            else "page_dirty")
        return out
    if not last_match:
        return out                        # nothing walk-verified: no span
    # the floor is the oldest row the verified chain reaches (the
    # docstring's tail paragraphs): at or above the last match the
    # walk's own testimony covers
    taker_lo = min(readable_ts[:last_match])
    span = {k for k, (ts, _tx) in walk_rows.items() if ts > taker_lo}
    unmatched = span - matched_keys
    ambiguous = {k for k in unmatched if walk_rows[k][1] in taker_txs}
    out["false_keys"] = sorted(unmatched - ambiguous)
    out["ambiguous_keys"] = sorted(ambiguous)
    return out


async def _taker_census(http, pool, whale: dict,
                        walk_rows: dict[str, tuple[float, str]],
                        walk_failed: bool, walk_dirty: bool = True,
                        now: float | None = None,
                        walk_floor: float | None = None) -> dict:
    """I/O half of the taker census for one whale: ONE takerOnly=true
    page, the intersection above, the two column writes when the floor
    is cleared, and the census row in ingestion_state every time —
    wrote=false with a reason on any refusal (HTTP error, timeout,
    non-list body, no eligible rows, under the floor, a failed walk).
    `walk_dirty` is the walk's own dirty counter read as a bool and it
    defaults to True (fail closed: a caller that cannot say the walk
    was clean gets no false labels); `now` is the walk's own wallclock,
    the bound a future-dated page row is judged against (the third
    review), defaulting to the clock; `walk_floor` is the walk's
    testimony floor, cov["oldest"], and it defaults to None (fail
    closed again: a caller that cannot say what the walk verified gets
    no false labels either — the fourth review). Nothing here can raise into the
    walk: the census is measurement, and a measurement that fails must
    never turn a clean walk into a failed:<addr> entry or change its
    heartbeat."""
    census = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "whale": whale["address"], "taker_page_rows": 0,
              "matched": 0, "match_rate": None, "wrote": False,
              "labeled_true": 0, "labeled_false": 0, "ambiguous": 0,
              "span_void": None, "reason": None}
    try:
        if walk_failed:
            census["reason"] = "walk_failed"
        elif not walk_rows:
            census["reason"] = "no_walk_rows"
        else:
            try:
                resp = await polite_get(
                    http, "/trades",
                    params={
                        "user": whale["address"],
                        "limit": TAKER_PAGE,
                        "offset": 0,
                        "takerOnly": "true",
                    },
                )
                resp.raise_for_status()
                page = resp.json()
            except Exception as exc:  # noqa: BLE001 — 4xx, timeout, bad JSON
                page = None
                census["reason"] = "http:%s" % type(exc).__name__
            if page is not None and not isinstance(page, list):
                page = None
                census["reason"] = "non_list"
            if page is not None:
                census["taker_page_rows"] = len(page)
                taker_rows: list[tuple[str | None, float | None, str | None]] = []
                for raw in page:
                    key: str | None = None
                    ts: float | None = None
                    tx: str | None = None
                    try:
                        if isinstance(raw, dict):
                            ev = parse_data_api_trade(
                                raw, whale["id"], whale["username"])
                            if key_fields_valid(ev):
                                key = ev.dedupe_key
                                ts = float(ev.ts_epoch)
                                tx = _tx_ident(ev.tx_hash)
                    except Exception:  # noqa: BLE001 — one junk row
                        key = ts = tx = None
                    taker_rows.append((key, ts, tx))
                res = taker_intersection(walk_rows, taker_rows,
                                         walk_dirty=walk_dirty, now=now,
                                         walk_floor=walk_floor)
                census["matched"] = res["matched"]
                census["match_rate"] = res["match_rate"]
                census["ambiguous"] = len(res["ambiguous_keys"])
                census["span_void"] = res["span_void"]
                if res["match_rate"] is None:
                    census["reason"] = "no_eligible_rows"
                elif res["match_rate"] < TAKER_MATCH_FLOOR:
                    census["reason"] = "under_floor"
                else:
                    if res["true_keys"]:
                        await pool.execute(
                            "UPDATE trades SET taker = true "
                            "WHERE dedupe_key = ANY($1::text[])",
                            res["true_keys"])
                    if res["false_keys"]:
                        # a false is the weaker proof (absence from a
                        # page) and never overwrites a true (presence
                        # on one): only rows still unknown take it
                        await pool.execute(
                            "UPDATE trades SET taker = false "
                            "WHERE dedupe_key = ANY($1::text[]) "
                            "AND taker IS NULL",
                            res["false_keys"])
                    census["labeled_true"] = len(res["true_keys"])
                    census["labeled_false"] = len(res["false_keys"])
                    census["wrote"] = True
    except Exception as exc:  # noqa: BLE001 — a write failed mid-way
        census["wrote"] = False
        census["reason"] = "db:%s" % type(exc).__name__
    try:
        body = json.dumps(census)
        # the spec-literal key carries the LAST whale walked; the
        # per-whale sibling is what a reader picks one whale out of
        for key in (TAKER_CENSUS_KEY, TAKER_CENSUS_KEY + ":" + whale["address"]):
            await pool.execute(
                "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                key, body)
    except Exception as exc:  # noqa: BLE001 — the census row itself
        log.warning("taker census: could not record %s: %s",
                    whale["address"][:10], exc)
    return census


TAKER_CENSUS_ENV = "RECONCILE_TAKER_CENSUS"


def _taker_census_switch(cfg) -> bool:
    """The census switch read fail-closed: the settings field when
    config carries it, else the env var that field will read, and only
    an explicit yes turns it on."""
    field = getattr(cfg, "reconcile_taker_census", None)
    if field is not None:
        return bool(field)
    return os.environ.get(TAKER_CENSUS_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


async def reconcile_once(depth: int = 500,
                         taker_census: bool | None = None) -> dict:
    cfg = settings()
    # FAIL-CLOSED SWITCH: the census is OFF unless the caller names it,
    # config carries `reconcile_taker_census`, or — config.py being
    # outside this unit's files (the three reviews' standing minor:
    # the integrating phase adds the field next to
    # reconcile_interval_seconds) — the deployment sets the env var
    # the field will read, RECONCILE_TAKER_CENSUS, to an explicit yes.
    # The settings attribute wins when it exists (it IS that env var,
    # parsed by config); anything but an explicit yes is off, so every
    # existing caller's walk is byte-identical: same requests, same
    # statements, same result and heartbeat.
    if taker_census is None:
        taker_census = _taker_census_switch(cfg)
    walk_now = time.time()          # future-row corruption bound (r40)
    pool = await get_pool()
    run_id = await pool.fetchval(
        "INSERT INTO reconciliation_runs DEFAULT VALUES RETURNING id"
    )
    whales = await pool.fetch("SELECT id, address, username FROM whales WHERE active AND NOT banned")
    missed = 0
    per_wallet: dict = {}
    async with httpx.AsyncClient(base_url=cfg.data_api_base, timeout=20) as http:
        for whale in whales:
            wallet_missed = 0
            offset = 0
            # COVERAGE EVIDENCE for the S1 corroboration sweep (fleet
            # round 6): a run that fetched only the newest `depth` rows
            # of a busy wallet never had its chance at an older fill,
            # and no later run reaches deeper — treating it as covering
            # falsely tripped the emitter on a venue-visible fill. The
            # sweep now requires either complete=true (feed exhausted
            # inside the depth) or oldest (the oldest venue ts this run
            # actually reached) at or before the fill's own time.
            complete = False
            oldest_ts: float | None = None
            newest_ts: float | None = None    # upper bound (r38)
            newest_row: tuple | None = None   # its identity (r41)
            # FEED ORDERING (fleet round 21, major): oldest was a bare
            # min() over usable rows — an inference sound only under
            # an UNCHECKED newest-first premise. One genuinely valid
            # late-indexed OLD row inside the walked window (a durable
            # feed property, served identically to every hourly walk,
            # so the round-20 two-run rule could not decorrelate it)
            # set oldest below an S1 fill that sat BELOW the depth
            # cap: false span coverage, permanent false STICKY. The
            # walk now verifies the premise it relies on: (a) a usable
            # row whose ts exceeds its predecessor's by more than
            # ORDER_TOL_S proves the feed is not newest-first-ordered
            # — the walk is DIRTY (span testimony void; ingest still
            # runs); and (b) a row only testifies for span if ordered
            # successors are verified BELOW it — a clean walk that
            # reaches the depth cap fetches ONE border-witness page
            # past the cap whose rows are ordering-checked and
            # ingested but never extend oldest. An interior misordered
            # row is caught by (a) at its successor; a misordered run
            # at the exact cap tail is caught by (a)-via-(b) when the
            # genuine feed resumes newer inside the border page. The
            # residual (round-22 fleet, executed): >= BORDER_PAGE -
            # OVERLAP_K consecutively misordered rows spanning the cap
            # boundary escape with dirty=0 — a wholesale-reordered
            # feed no depth-capped walk of any design can see through.
            # HONEST direction there: if the reorder is DURABLE, both
            # hourly walks cover identically and the failure is a
            # false STICKY on a correct emission — over-alarm and
            # disarm, NOT defer. Money fails closed (never a wrong or
            # double emission); the cost is a spurious operator alarm
            # under a non-physical feed pathology.
            ord_floor: float | None = None
            # DIRTY WALK (fleet round 11, major): a row skipped as
            # unusable below neither ingests nor testifies — correct —
            # but when the skipped row was the S1 fill's OWN row (a
            # degraded per-wallet index serving a size-0 / hash-less
            # stub of exactly that fill), the healthy neighbors still
            # handed the run complete=true and a spanning oldest, and
            # the sweep branded a correct, venue-visible emission
            # "never shown by the feed" — a permanent false STICKY
            # trip. A walk that skipped ANY unusable row is recorded
            # dirty and the sweep refuses its coverage claim outright:
            # the wallet's rows DEFER until a clean run covers them,
            # the same safe direction every degradation shape takes.
            dirty = 0
            # OVERLAP PAGINATION (fleet round 17, major): each page is
            # a FRESH query, and two same-timestamp rows straddling a
            # page boundary under an unstable tiebreak could swap —
            # the walk then re-served the tie-mate and NEVER saw the
            # other row, while every individual page was valid: dirty
            # stayed 0, complete=true, spanning oldest — a clean
            # coverage claim over a walk that skipped the S1 fill.
            # Every page after the first now re-requests the previous
            # page's last OVERLAP_K rows (offset advances by len-K);
            # if that witness run does not come back verbatim and in
            # order, the feed shifted between queries and the walk is
            # DIRTY — it may have dropped a row it can no longer prove
            # it saw. K is 3, not 1 (fleet round 18, major): a RAW-
            # IDENTICAL twin — equal legs of one same-second taker
            # bundle — could impersonate a single-row witness after a
            # shift by exactly the twin distance, silently resuming
            # the walk below the skipped fill. Masking a 3-row witness
            # needs three consecutive rows EACH with an ident-twin at
            # the identical distance; and a witness row that has a
            # twin visible in its own page makes the boundary
            # ambiguous, which also dirties. Round 20 PROVED the
            # residual (invisible aligned triple twins + an exact-
            # distance shift) can still yield a clean claim from ONE
            # walk — content identity cannot prove positional
            # continuity against raw-identical rows — so the sweep no
            # longer trusts any single walk: an uncorroborated verdict
            # requires TWO distinct clean covering runs (SQL_RECON_
            # SINCE counts to 2), and the masking coincidence would
            # have to recur across walks with decorrelated geometry.
            witness: list[tuple] = []
            # the rows this walk INGESTED (dedupe_key -> (venue ts, tx
            # identity)), the set the taker census intersects; collected
            # only under the switch so the OFF path is the walk as it was
            walk_rows: dict[str, tuple[float, str]] = {}
            try:
                # a dirty walk skips the border page — its coverage
                # claim is already refused, so the extra fetch buys
                # nothing (r21)
                while offset < (depth if dirty else depth + BORDER_PAGE):
                    # rows past the depth cap are the border witness:
                    # ordering-checked and ingested, never span (r21)
                    testify = offset < depth
                    resp = await polite_get(
                        http, "/trades",
                        params={
                            "user": whale["address"],
                            "limit": 100,
                            "offset": offset,
                            "takerOnly": "false",
                        },
                    )
                    resp.raise_for_status()
                    page = resp.json()
                    if not isinstance(page, list):
                        # a non-list body is a venue error shape — the
                        # walk cannot continue this run, but it DEFERS
                        # (dirty, no coverage claim) instead of
                        # aborting the wallet (round 19)
                        dirty += 1
                        break
                    batch = [r for r in page if isinstance(r, dict)]
                    if len(batch) != len(page):
                        # round 19 (major): a JSON null in the batch
                        # reached the witness build BEFORE the per-row
                        # containment and its AttributeError killed the
                        # wallet's ENTIRE walk — the round-14/15
                        # one-row-costs-one-row promise, reintroduced
                        # one line earlier by the witness machinery.
                        # Non-dict elements are unusable rows: counted
                        # dirty, excluded from idents, ingest and
                        # witness alike; the healthy rows still walk.
                        dirty += len(page) - len(batch)
                    if not batch:
                        # An empty FIRST page is not "feed exhausted"
                        # (fleet round 7): a degraded venue serving
                        # 200-[] would brand a correct emission
                        # uncorroborated with the strongest possible
                        # evidence. Only an end reached AFTER real
                        # rows proves the feed was walked; an empty
                        # start defers (no cov claim of completeness).
                        if witness:
                            # the overlap rows we re-requested VANISHED
                            # between queries — shift evidence (r17)
                            dirty += 1
                        complete = offset > 0
                        break
                    idents = [_row_ident(r) for r in batch]
                    rows = batch
                    if witness:
                        k = len(witness)
                        if idents[:k] == witness:
                            rows = batch[k:]
                            if not rows:
                                # only the witness came back — the
                                # feed is exhausted at the boundary
                                complete = True
                                break
                        else:
                            # boundary mismatch: the feed reordered
                            # between page queries — a row may have
                            # slipped through the seam. Process what
                            # we got (dedupe absorbs any repeats) but
                            # the walk can never claim clean coverage.
                            dirty += 1
                    for raw in rows:
                        # per-row containment (round 14): a hostile
                        # field can make the PARSE itself raise
                        # (Infinity timestamp -> int() overflow) — one
                        # junk row must cost one row, never the walk
                        try:
                            ev = parse_data_api_trade(
                                raw, whale["id"], whale["username"])
                            usable = key_fields_valid(ev)
                        except Exception:  # noqa: BLE001
                            usable = False
                        if not usable:
                            # a row unusable for ingest must not
                            # testify for coverage either (fleet round
                            # 9, major: a degraded-index stub with
                            # ts=1 set oldest=1.0 and faked full-
                            # history span coverage — a universal
                            # waiver that false-tripped STICKY on a
                            # correct emission). Round 12 (major x2):
                            # a MANGLED-TIMESTAMP stub (tx and size
                            # intact, ts absent/zero/sentinel) passed
                            # this filter, ingested as a key-divergent
                            # 1970-dated row that could never stamp
                            # the real fill's venue_seen_at, and left
                            # dirty at 0 — the ts sentinel floor is
                            # part of validity, and a served copy of
                            # the fill that cannot corroborate it must
                            # dirty the walk like any other stub.
                            # Round 13 (major): the dedupe key is
                            # (tx, asset, side, size, price, ts) — a
                            # stub missing ANY key field (price -> 0.0,
                            # asset -> "", side -> "") ingests as a
                            # key-divergent junk row that can never
                            # stamp venue_seen_at, so EVERY key field
                            # is validity, not just tx/size/ts.
                            dirty += 1
                            continue
                        ts_r = float(ev.ts_epoch or 0.0)
                        if ts_r > walk_now + FUTURE_SKEW_S:
                            # fleet r40 (major): a FUTURE-dated row is
                            # feed corruption, full stop — no venue
                            # clock runs hours ahead. As the HEAD row
                            # it had no predecessor, so the ordering
                            # check below could never fire, it kept
                            # dirty=0, and its bare-max ts inflated
                            # cov->newest above every fill; tracking
                            # wallclock across two walks it even
                            # produced two DISTINCT newest values and
                            # defeated the round-39 rule. A dirty
                            # walk can never testify, whatever its
                            # head claims.
                            dirty += 1
                            continue
                        if ord_floor is not None and \
                                ts_r > ord_floor + ORDER_TOL_S:
                            # the feed served a row NEWER than its
                            # predecessor on a newest-first walk: the
                            # ordering premise behind span testimony
                            # is broken somewhere — DIRTY (r21)
                            dirty += 1
                        if ts_r > 1e9:
                            # CUMULATIVE floor, not the previous row
                            # (round 42, executed): the adjacent-only
                            # check let an ascending re-index ramp
                            # climb back to trajectory in
                            # <= ORDER_TOL_S steps, accumulating
                            # unbounded inversion at dirty=0 and
                            # sinking cov->oldest 7500s below the
                            # walk's real reach. Under a genuine
                            # newest-first feed ts is non-increasing,
                            # so ts_r never exceeds the running floor
                            # and this can never fire falsely.
                            ord_floor = (ts_r if ord_floor is None
                                         else min(ord_floor, ts_r))
                        if (testify and ev.ts_epoch and ev.ts_epoch > 1e9
                                and (oldest_ts is None
                                     or ev.ts_epoch < oldest_ts)):
                            # the 1e9 floor (2001) rejects sentinel
                            # timestamps a degraded venue might emit
                            oldest_ts = float(ev.ts_epoch)
                        if (testify and ev.ts_epoch and ev.ts_epoch > 1e9
                                and (newest_ts is None
                                     or ev.ts_epoch > newest_ts)):
                            # fleet round 38 (major): coverage claimed
                            # only a LOWER bound — a frozen index's
                            # walk spanned far below a fill it could
                            # never have served, counted as clean
                            # coverage twice (byte-identical geometry
                            # defeats the round-20 decorrelation), and
                            # the sweep STICKY-tripped a correct
                            # emission. The walk now testifies to the
                            # NEWEST row it served; SQL_RECON_SINCE
                            # requires the fill to sit INSIDE the span
                            newest_ts = float(ev.ts_epoch)
                            newest_row = (ev.tx_hash, ev.asset,
                                          float(ev.ts_epoch))
                        # per-row containment around the INGEST (fleet
                        # round 15): a gate-passing row can still fail
                        # inside ingest (constraint, overflow, datetime
                        # range) — the uncontained raise aborted the
                        # walk as failed:<addr> every run and lost the
                        # healthy fills behind it. A row that cannot
                        # land cannot corroborate either: it counts
                        # into dirty like any other unusable serving,
                        # so the walk keeps going and still never
                        # claims clean coverage.
                        try:
                            sport = await _sport_for_condition(ev.condition_id)
                            if sport:
                                ev.sport = sport
                            # WAS IT NEW? `is not None` stopped meaning
                            # that when ingest_trade switched to ON
                            # CONFLICT DO UPDATE — it returns the id for
                            # duplicates too, so this counted the ENTIRE
                            # 500-row-per-wallet re-sweep as missed
                            # fills and reported permanent drift on
                            # every run.
                            _tid, was_new = await ingest_trade_result(ev)
                        except Exception:  # noqa: BLE001
                            dirty += 1
                            continue
                        if was_new:
                            wallet_missed += 1
                        if taker_census:
                            walk_rows[ev.dedupe_key] = (ts_r, _tx_ident(ev.tx_hash))
                    witness = idents[-OVERLAP_K:]
                    if len(page) < 100:
                        # completeness is judged on the page AS SERVED
                        # (r19: dropped non-dict elements must not
                        # fake a short page — dirty already defers)
                        complete = True
                        break
                    if any(idents.count(w) > 1 for w in witness):
                        # a witness row has an ident-twin visible in
                        # its own page: the boundary about to be
                        # crossed is ambiguous — a twin could
                        # impersonate it after a shift (round 18).
                        # The walk continues but can never claim
                        # clean coverage past this seam. (Only
                        # boundaries actually crossed matter — a twin
                        # in the FINAL page's tail is harmless.)
                        dirty += 1
                    # advance by len-K over the page AS SERVED: the
                    # next page re-requests the witness run first
                    # (rounds 17-19)
                    offset += max(1, len(page) - len(witness))
                # HEAD-MUTANT CHECK (fleet round 41, major): the
                # round-40 future bound only catches a phantom head
                # MORE than FUTURE_SKEW_S ahead — a present-dated
                # phantom tracking each walk's own wallclock forged
                # two DISTINCT newest values over a frozen feed and
                # false-STICKY-tripped a correct emission. But a
                # wallclock-tracking phantom re-serves the SAME
                # (tx, asset) with a MUTATING timestamp, and our own
                # trades table remembers its earlier ingests: the
                # newest-testimony row already recorded at a ts more
                # than ORDER_TOL_S away is feed corruption — dirty,
                # never testimony. (A feed fabricating fresh tx
                # hashes every walk is indistinguishable from real
                # activity to ANY observer; negative certification is
                # void against an adversarial feed — the design round
                # owns that boundary honestly.)
                if newest_row is not None:
                    try:
                        # r42 (F6, executed): lower() both sides —
                        # SQL_PROBE and make_dedupe_key lowercase, a
                        # mixed-case re-serve bypassed the r41 fix —
                        # and compare against source='poll' rows only:
                        # chain/s1 rows carry BLOCK timestamps whose
                        # honest 120-300s venue skew fired the probe
                        # on every clean walk and silenced the alarm.
                        mutant = await pool.fetchval(
                            "SELECT 1 FROM trades "
                            "WHERE lower(tx_hash) = lower($1) "
                            "AND asset = $2 AND source = 'poll' "
                            "AND abs(extract(epoch "
                            "from ts)::float8 - $3) > $4 LIMIT 1",
                            newest_row[0], newest_row[1],
                            newest_row[2], float(ORDER_TOL_S))
                    except Exception:  # noqa: BLE001 — probe only
                        mutant = None
                    if mutant:
                        dirty += 1
                per_wallet["cov:" + whale["address"]] = {
                    "complete": complete, "oldest": oldest_ts,
                    "newest": newest_ts, "dirty": dirty}
            except Exception as exc:  # noqa: BLE001 — one wallet must
                # never abort the whole run: this sweep is the sole
                # backstop for the S1 corroboration stamp, and a single
                # wallet's HTTP error was doubling the backstop gap for
                # every wallet AFTER it in the roster (fleet round 4).
                # Whatever this wallet ingested before failing stands.
                # No cov: key on failure — a partial sweep proves
                # nothing about depth (round 6).
                log.warning("reconcile: wallet %s failed, continuing: %s",
                            whale["address"][:10], exc)
                per_wallet["failed:" + whale["address"]] = 1
            if taker_census:
                # after the walk, outside its try: the census reads
                # the rows the walk ingested and writes only its own
                # column and its own state key — never per_wallet,
                # never the run row, never the heartbeat
                # the walk's own dirty counter voids the census's span
                # testimony (the second review's major); a missing cov
                # row reads dirty — fail closed, no false labels; and
                # the walk's testimony floor (oldest) is the only line
                # below which a matched row cannot floor the census
                # (the fourth review's major: the walk's own tail is
                # verified by nothing)
                cov = per_wallet.get("cov:" + whale["address"], {})
                await _taker_census(
                    http, pool, whale, walk_rows,
                    ("failed:" + whale["address"]) in per_wallet,
                    walk_dirty=cov.get("dirty", 1) > 0, now=walk_now,
                    walk_floor=cov.get("oldest"))
            per_wallet[whale["address"]] = wallet_missed
            missed += wallet_missed

    await pool.execute(
        "UPDATE reconciliation_runs SET finished_at=now(), missed=$2, details=$3::jsonb WHERE id=$1",
        run_id,
        missed,
        json.dumps({"per_wallet": per_wallet}),
    )
    # round 27 (minor): status derived solely from `missed` let a
    # TOTAL-LOSS run (every wallet's walk failed; nothing ingested,
    # nothing covered, nothing stamped) heartbeat 'ok' — the backstop
    # carrier 100% dead, byte-identical on the ops row to a flawless
    # run, the same silent-success arithmetic rounds 23-25 killed in
    # the other three carriers. Failure now speaks in status like
    # every sibling: all wallets failed -> 'error'; partial failures
    # ride the detail so the dashboard can see degradation.
    failed_n = sum(1 for k in per_wallet if str(k).startswith("failed:"))
    if whales and failed_n == len(whales):
        status = "error"
    else:
        status = "ok" if missed == 0 else "drift"
    await heartbeat("reconciler", status,
                    {"missed": missed, "failed": failed_n})
    if missed:
        log.warning("reconciliation ingested %s missed fills: %s", missed, per_wallet)
    return {"run_id": run_id, "missed": missed, "per_wallet": per_wallet}
