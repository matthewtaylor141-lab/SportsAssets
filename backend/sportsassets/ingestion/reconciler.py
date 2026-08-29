"""Hourly reconciliation: Path B (Data API) is the source of truth.

Sweeps recent trades per tracked wallet from the Data API and re-runs them
through the pipeline. Anything already ingested is a dedupe no-op; anything
missed (e.g. during a WS outage that outlived the poller's 100-trade window)
is ingested late and counted as `missed`.
"""

from __future__ import annotations

import json
import logging
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


async def reconcile_once(depth: int = 500) -> dict:
    cfg = settings()
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
            ord_prev: float | None = None
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
                        if ord_prev is not None and \
                                ts_r > ord_prev + ORDER_TOL_S:
                            # the feed served a row NEWER than its
                            # predecessor on a newest-first walk: the
                            # ordering premise behind span testimony
                            # is broken somewhere — DIRTY (r21)
                            dirty += 1
                        if ts_r > 1e9:
                            ord_prev = ts_r
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
                        mutant = await pool.fetchval(
                            "SELECT 1 FROM trades WHERE tx_hash = $1 "
                            "AND asset = $2 AND abs(extract(epoch "
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
