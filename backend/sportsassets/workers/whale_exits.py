"""Detect whale exits from POSITIONS, because they never sell.

Established 2026-08-25 after a night of looking in the wrong place:

    SELLTRUTH swisstony  the data API's own trade feed: 0 sells
    SIDES     swisstony  860,326 buys, 0 sells, across all three sources
    POSTRUTH  swisstony  62 of 75 held positions are BELOW what he bought
    POSTRUTH  ferrari    18 of 23

The owner said these accounts take profit before settlement. Our data
said they had never sold once. Both were true: they close by MERGING
complementary outcomes back to USDC, or by redeeming — neither is a
trade, so neither appears in any trade feed anywhere. The venue's own
positions payload carries `mergeable` and `oppositeAsset` fields, which
is the mechanism naming itself.

So exits are detected as a DROP IN HOLDINGS between two snapshots, and
handed to mirror_exit with the fraction we measured. That closes the
loop the owner asked for: copy both legs, proportionally.

Deliberately conservative:
  * only DECREASES matter — an increase is a new entry, which the
    normal copy path already handles.
  * a position that vanishes entirely could be an exit OR a market that
    resolved. Resolution is not an exit to mirror — the venue settles
    our copy too — so a disappearance is recorded and skipped, and only
    a SHRINK on a still-held asset is treated as an exit.
  * the first snapshot for a whale emits nothing. There is no previous
    state to diff, and inventing one would fire a full exit on every
    position the first time this ever runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from ..config import settings
from ..db import get_pool, heartbeat

log = logging.getLogger(__name__)

INTERVAL_S = float(os.environ.get("WHALE_EXIT_INTERVAL_S", "120"))
# A shrink smaller than this is noise — rounding in the venue's size
# field, or a partial that is not worth a fee to follow.
MIN_SHRINK = float(os.environ.get("WHALE_EXIT_MIN_SHRINK", "0.05"))
ENABLED = os.environ.get("WHALE_EXIT_ENABLED", "1") != "0"
# MOST EXITS PER CYCLE, PER WHALE.
#
# swisstony holds less than he bought on 62 of 75 positions. The first
# cycle that has a previous snapshot to diff against could therefore
# fire 62 real sell orders back to back — from a worker written tonight,
# on a night where several of my confident fixes turned out to do
# nothing or the opposite. A brand-new component that places real
# orders should not be able to place sixty of them before anyone sees
# the first one.
#
# The remainder is not lost: the next snapshot still shows the position
# below its recorded size, so it is picked up on the following cycle
# two minutes later. This bounds the blast radius of a bug, not the
# work.
MAX_EXITS_PER_CYCLE = int(os.environ.get("WHALE_EXIT_MAX_PER_CYCLE", "10"))

_KEY = "whale_positions:%s"
# Page size and hard ceiling for the positions read. positions_sync.py
# uses 100/2000 against the same endpoint; matched here so the two
# cannot disagree about what "his whole book" means.
POSITIONS_PAGE = int(os.environ.get("WHALE_EXIT_POS_PAGE", "100"))
# 2000 -> 6000 (2026-08-26): four of seven whale books exceeded
# 2000 and were forfeited every cycle. RAISED IN LOCKSTEP with
# positions_sync.MAX_POSITIONS_PER_WALLET -- two readers
# disagreeing about what "his whole book" means is how one of
# them ends up wrong, and a test pins them together.
POSITIONS_MAX = int(os.environ.get("WHALE_EXIT_POS_MAX", "6000"))


class EmptyPositions(RuntimeError):
    """The venue returned NO positions for a whale we know holds some.

    Sibling of TruncatedPositions and the more dangerous of the two,
    because it needs no unusual book size to happen — one transient
    empty 200 is enough. Every asset in the previous snapshot is then
    absent from `now`, diff_exits reads absent-and-unresolved as a FULL
    exit, and the cycle fires MAX_EXITS_PER_CYCLE real sell orders on
    positions the whale still holds. Measured on a 30-position book: 30
    exits detected, 10 placed.

    I guarded the truncated case and not this one. A partial book is a
    different truth; an empty book is a different truth too, and this
    is the shape that arrives without warning."""


class TruncatedPositions(RuntimeError):
    """The venue still had more positions when we stopped reading.

    A partial book must never decide a VANISH: every asset past the cut
    is absent from `now`, which reads as a FULL EXIT and fires a 100%
    close on a position the whale still holds. Losing a cycle costs a
    delay; diffing a truncated book costs real sell orders.

    But a SHRINK is different, and forfeiting it was too strong. An
    asset present in BOTH reads was observed twice and its size fell by
    a measured amount; nothing beyond the cut changes that. So the
    partial book travels on the exception and the caller acts on
    shrinks only.

    It mattered: the census read truncated_books=4 against whales=3, so
    most of the roster was skipped on every cycle, permanently, and the
    position-diff lane -- built precisely for whales who exit by
    merging -- had detected exactly zero exits.
    """

    def __init__(self, msg: str, book=None, sibs=None, seen: int = 0):
        super().__init__(msg)
        self.book = book or {}
        self.sibs = sibs or {}
        self.seen = seen


# The sibling map is merged across cycles, so it needs a ceiling.
MAX_SIBLINGS = int(os.environ.get("WHALE_EXIT_MAX_SIBLINGS", "20000"))


async def _load_siblings(pool) -> dict[str, str]:
    raw = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", _SIB_STATE_KEY)
    if not raw:
        return {}
    try:
        d = raw if isinstance(raw, dict) else json.loads(raw)
        return {str(k): str(v) for k, v in (d or {}).items() if k and v}
    except (TypeError, ValueError):
        return {}


async def _load(pool, whale: str) -> dict[str, float]:
    raw = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", _KEY % whale)
    if not raw:
        return {}
    try:
        d = raw if isinstance(raw, dict) else json.loads(raw)
        return {str(k): float(v) for k, v in (d or {}).items()}
    except (TypeError, ValueError):
        return {}


async def _save(pool, whale: str, snap: dict[str, float]) -> None:
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        _KEY % whale, json.dumps(snap))


# FAIRNESS ACROSS A BACKLOG OF REFUSED EXITS.
#
# When exits are refused they are pinned back into the snapshot so they
# are found again. With a backlog larger than MAX_EXITS_PER_CYCLE that
# is not enough on its own: the cycle acts on the first N of whatever
# order the snapshot comes back in, so the same N are retried forever
# and the rest never get a turn.
#
# The obvious fix — rely on the pinned assets landing at the end of the
# dict's insertion order — DOES NOT WORK, and would have passed a test
# while failing in production. The snapshot is stored as `jsonb`, and
# PostgreSQL's jsonb does not preserve object key order; it normalises
# keys into sorted order on write. Insertion order survives json.dumps
# and dies in the column. A stub pool that keeps the string round-trips
# it perfectly and proves nothing.
#
# So the cursor is EXPLICIT and lives in its own row: a list of assets
# most recently attempted and still pending, oldest first.
_RETRY_KEY = "whale_exit_retry:%s"
# Bounded. A whale's book is bounded, but a row that only ever grows is
# a row that eventually breaks a write.
MAX_RETRY_CURSOR = 500


def rotate_for_fairness(found: list[tuple[str, float]],
                        tried: list[str]) -> list[tuple[str, float]]:
    """Never-attempted exits first, then least-recently-attempted.

    Pure, and the whole point of the fix, so it is testable without a
    venue or a database. Stable within each group: an exit that has
    never been tried keeps its position relative to other untried ones.
    """
    rank = {a: i for i, a in enumerate(tried)}
    fresh = [x for x in found if x[0] not in rank]
    stale = sorted((x for x in found if x[0] in rank),
                   key=lambda x: rank[x[0]])
    return fresh + stale


def next_cursor(tried: list[str], attempted: list[str],
                pending: set[str]) -> list[str]:
    """The cursor after a cycle.

    An asset we attempted and SETTLED leaves the cursor entirely — it
    will not be found again. One we attempted and that is still pending
    moves to the BACK, behind everything that has been waiting longer.
    """
    done = set(attempted)
    out = [a for a in tried if a not in done]
    out += [a for a in attempted if a in pending]
    return out[-MAX_RETRY_CURSOR:]


async def _load_retry(pool, whale: str) -> list[str]:
    try:
        raw = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            _RETRY_KEY % whale)
    except Exception:  # noqa: BLE001 — a missing cursor is not an error
        return []
    if not raw:
        return []
    try:
        d = raw if isinstance(raw, list) else json.loads(raw)
        return [str(x) for x in (d or []) if x]
    except (TypeError, ValueError):
        return []


async def _save_retry(pool, whale: str, assets: list[str]) -> None:
    try:
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) "
            "VALUES ($1, $2::jsonb) ON CONFLICT (key) DO UPDATE "
            "SET value = $2::jsonb",
            _RETRY_KEY % whale, json.dumps(assets))
    except Exception:  # noqa: BLE001 — fairness is not correctness. A
        # cursor that fails to write costs us round-robin ordering, not
        # an exit; the pin in the snapshot is what preserves the exit.
        log.warning("whale-exit: retry cursor write failed for %s", whale)


# THE SIBLING TOKEN, IF THE VENUE ACTUALLY SENDS IT.
#
# classify_exit answers "is this buy really an exit?" by asking whether
# the whale holds the COMPLEMENTARY leg, and it finds that leg through
# market_tokens. The production census says it often cannot:
#
#     EXITCENSUS cls_token_unenriched: 56
#
# 56 buys in one window where the token is not in market_tokens at all,
# so the question cannot even be asked. The default when it cannot be
# asked is to treat the buy as an ENTRY — and of the buys we CAN
# classify, 79 of 122 (65%) are exits. Defaulting to entry on an
# unknown token is therefore wrong most of the time for that
# population, and when it is wrong it does not miss a copy, it DOUBLES
# one: we buy the leg he is abandoning while still holding the leg he
# just closed.
#
# This module's own docstring has claimed since it was written that the
# venue's positions payload carries `mergeable` and `oppositeAsset`. I
# wrote that line. Nothing in the codebase has ever read those fields,
# so it is an assertion, not a fact, and building a money path on it
# would be the same mistake as the SDK argument I had to retract today.
#
# So: CAPTURE AND REPORT, USE ONLY IF PRESENT. Every cycle records how
# many position rows actually carried a usable sibling. If the field
# does not exist the coverage reads zero, nothing changes anywhere, and
# the heartbeat says so. If it does exist we get a sibling map for free
# out of a call we were already making every 120 seconds and throwing
# most of away.
_SIBLING_KEYS = ("oppositeAsset", "opposite_asset", "complementAsset",
                 "oppositeTokenId", "opposite_token_id")
_SIB_STATE_KEY = "token_siblings"


def _sibling_of(p: dict) -> str:
    """The complementary token id on one position row, or ''."""
    for k in _SIBLING_KEYS:
        v = p.get(k)
        if v not in (None, "", 0):
            return str(v)
    return ""


async def _fetch_positions(http: httpx.AsyncClient,
                           address: str) -> tuple[dict[str, float],
                                                  dict[str, str], int]:
    """(sizes, sibling map, rows seen).

    The sibling map is a by-product of a call already being made. It is
    returned separately rather than folded into the sizes dict so the
    exit-diff logic is untouched by its presence or absence.
    """
    # PAGED, AND A TRUNCATED READ MUST NOT LOOK LIKE AN EXIT
    # (2026-08-25, as-designed audit).
    #
    # This asked for limit=500 and no offset. positions_sync.py pages
    # the same endpoint in 100s up to 2000, so 500 is not the ceiling —
    # it is just where this call stopped looking.
    #
    # The consequence is not a coverage gap, it is WRONG SELL ORDERS.
    # Every asset past the truncation is absent from `now`, diff_exits
    # reads absent-and-unresolved as a FULL EXIT, and we fire a 100%
    # close on positions the whale still holds — up to the per-cycle
    # cap, every cycle. swisstony carries 860k fills and a book far
    # past 500 rows.
    #
    # So: page to the same 2000 ceiling, and if the venue is still
    # handing us full pages at the end, REFUSE THE WHOLE SNAPSHOT
    # rather than diff against a book we know is incomplete. A partial
    # position list is not a smaller truth, it is a different one.
    out: dict[str, float] = {}
    sibs: dict[str, str] = {}
    seen = 0
    offset = 0
    truncated = False
    while offset < POSITIONS_MAX:
        resp = await http.get("/positions",
                              params={"user": address,
                                      "limit": POSITIONS_PAGE,
                                      "offset": offset})
        resp.raise_for_status()
        body = resp.json()
        rows = body if isinstance(body, list) else (
            body.get("data") or body.get("positions") or [])
        if not rows:
            break
        for p in rows:
            if not isinstance(p, dict):
                continue
            a = str(p.get("asset") or p.get("tokenId") or "")
            if not a:
                continue
            seen += 1
            sib = _sibling_of(p)
            if sib and sib != a:
                sibs[a] = sib
                # The relation is symmetric, and recording both
                # directions means a later buy of EITHER leg can find
                # the other.
                sibs.setdefault(sib, a)
            try:
                sz = float(p.get("size") or p.get("netPosition") or 0)
            except (TypeError, ValueError):
                continue
            if sz > 0:
                out[a] = sz
        offset += len(rows)
        if len(rows) < POSITIONS_PAGE:
            break
    else:
        truncated = True
    if truncated:
        # THE PARTIAL BOOK TRAVELS WITH THE EXCEPTION (2026-08-26).
        #
        # It was built and then discarded, and the caller forfeited the
        # whole whale. That is right for a VANISHED position -- an asset
        # past the cut is absent from `now` and reads as a full exit --
        # and far too strong for a SHRINK. An asset present in BOTH
        # reads was observed twice; its size fell by a measured amount
        # and nothing beyond the cut changes that.
        #
        # It mattered: the census read truncated_books=4 against
        # whales=3, so most of the roster was skipped every cycle,
        # permanently, and the position-diff lane -- built precisely for
        # whales who exit by merging -- had found exactly zero exits.
        raise TruncatedPositions(
            f"{address}: still returning full pages at {POSITIONS_MAX} "
            f"positions — vanished positions unusable, shrinks still "
            f"real", out, sibs, seen)
    return out, sibs, seen


def guard_empty(prev: dict[str, float], now: dict[str, float],
                address: str) -> None:
    """Refuse an EMPTY read against a non-empty prior snapshot.

    A whale does not close his entire book between two 120-second
    polls. A read that says he did is a venue hiccup, and acting on it
    fires a full close on every position he holds — up to the cycle
    cap, then again next cycle.

    Deliberately narrow: only the fully-empty case. A whale who
    genuinely goes flat will read empty on the cycle AFTER this one
    too, and by then `prev` is empty as well and nothing fires. The
    cost of the guard is one cycle of delay on a real full flatten; the
    cost of not having it is real sell orders on a transient blip."""
    if prev and not now:
        raise EmptyPositions(
            f"{address}: /positions returned NOTHING against a prior "
            f"snapshot of {len(prev)} — a whale does not close his "
            f"whole book in 120s, so this is refused rather than "
            f"mirrored as {len(prev)} full exits")


def diff_exits(prev: dict[str, float],
               now: dict[str, float],
               not_an_exit: set[str] | None = None) -> list[tuple[str, float]]:
    """(asset, closed_fraction) for holdings that SHRANK.

    Pure, so the rule is testable without a venue:
      * asset missing from `now` -> skipped. Could be an exit, could be
        a resolved market; resolution settles our copy on its own and
        mirroring it would sell a position that no longer exists.
      * grew -> not an exit.
      * shrank by less than MIN_SHRINK of the position -> noise.
    """
    out: list[tuple[str, float]] = []
    # NAMED FOR WHAT IT IS. It used to be called `resolved`, and the
    # caller passed only markets it had positively confirmed as
    # resolved — so a vanished token the caller could say NOTHING about
    # fell through to the full-exit branch. The set is the tokens a
    # disappearance must not be read as an exit on, whatever the reason,
    # and "we have no idea" is one of the reasons.
    res = not_an_exit or set()
    for asset, before in prev.items():
        if before <= 0:
            continue
        if asset not in now:
            # A VANISHED POSITION IS THE MOST COMMON EXIT, AND WE WERE
            # DROPPING EVERY ONE (2026-08-25).
            #
            # The old rule skipped disappearances because a resolved
            # market also vanishes, and mirroring a resolution would
            # sell a position the venue is about to settle for us. The
            # caution was right; treating "could be either" as "always
            # resolution" was not.
            #
            # It discarded exactly the case the whole feature exists
            # for: a FULL exit. swisstony holds below purchase on 62 of
            # 75 positions and ferrari on 18 of 23 — roughly 83% of
            # their positions get exited — and every one that reached
            # zero was invisible here. The detector could only ever
            # have fired on partial scale-outs, which these whales
            # barely do.
            #
            # The caller passes every token a disappearance must NOT be
            # read as an exit on: resolved, closed, or unknown to us.
            # Gone from a market we can see is still trading is a close,
            # at 100%.
            if asset not in res:
                out.append((asset, 1.0))
            continue
        after = now[asset]
        if after >= before:
            continue
        frac = (before - after) / before
        if frac >= MIN_SHRINK:
            out.append((asset, round(frac, 4)))
    return out


async def _confirm_gone(http: httpx.AsyncClient, pool, address: str,
                        asset: str) -> bool:
    """POSITIVE per-market confirmation that a whale no longer holds
    `asset`. Absence from a partial book is never evidence; this is.

    Fails CLOSED on everything: no condition id, an error, and --
    critically -- a response the server did not actually filter. The
    /positions `market` parameter narrows to one condition id; if the
    venue ever ignored an unknown parameter it would hand back the
    unfiltered first page, and reading the asset's absence from THAT as
    proof of exit would fire a 100% sell on a position the whale still
    holds. So a non-empty response containing any row from a DIFFERENT
    market is treated as unfiltered and refused, and an empty response
    is refused too -- a roster whale with no positions at all is not a
    plausible read, it is a failure mode.
    """
    try:
        cid = await pool.fetchval(
            "SELECT condition_id FROM market_tokens WHERE token_id = $1",
            asset)
        if not cid:
            return False
        resp = await http.get("/positions", params={
            "user": address, "market": cid, "limit": 100,
            "sizeThreshold": 0})
        if resp.status_code != 200:
            return False
        body = resp.json()
        rows = body if isinstance(body, list) else (
            body.get("data") or body.get("positions") or [])
        if not rows:
            return False
        for p in rows:
            if not isinstance(p, dict):
                return False
            row_cid = str(p.get("conditionId") or p.get("market") or "")
            if row_cid and row_cid != str(cid):
                return False        # unfiltered response -- refuse
            if str(p.get("asset") or p.get("tokenId") or "") == asset:
                try:
                    sz = float(p.get("size") or 0)
                except (TypeError, ValueError):
                    return False
                return sz <= 0
        # The venue answered FOR THIS MARKET (rows present, all from
        # this condition) and the leg is not among them: gone.
        return True
    except Exception:  # noqa: BLE001 -- unknown is not gone
        return False


async def _cycle(http: httpx.AsyncClient, pool) -> dict:
    from ..api.copies_record import COPY_WHALES
    from ..live_executor import EXIT_PENDING_REASONS, execute_copy

    # "exits" counted ATTEMPTS, not sales, because it was incremented
    # before execute_copy was called and execute_copy reported nothing
    # back. A halted sleeve therefore published the same number as a
    # working one. The four counters below are always present, never
    # conditionally added: an absent key and a zero key look identical
    # to a reader, and that has bitten this codebase before.
    stats = {"whales": 0, "exits": 0, "first_snapshots": 0,
             "exit_attempts": 0, "exits_sold": 0,
             "partial_vanished_skipped": 0,
             "exits_pending": 0, "exits_no_action": 0,
             # Coverage of the venue's sibling field, measured rather
             # than assumed. sib_rows == 0 means the field is not there
             # and the fallback below contributes nothing — which is a
             # finding, not a failure.
             "sib_rows": 0, "sib_pairs": 0, "pos_rows": 0}
    all_sibs: dict[str, str] = {}
    wanted = {w.lower() for w in COPY_WHALES}
    rows = await pool.fetch(
        "SELECT username, address FROM whales WHERE address IS NOT NULL")
    for r in rows:
        uname = r["username"] or ""
        if uname.lower() not in wanted:
            continue
        partial = False
        try:
            now, sibs, seen = await _fetch_positions(http, r["address"])
        except EmptyPositions as exc:
            log.warning("whale-exit: %s", exc)
            stats["empty_books"] = stats.get("empty_books", 0) + 1
            continue
        except TruncatedPositions as exc:
            # SHRINK-ONLY rather than forfeiting the whale outright.
            # A vanished asset may simply sit past the cut, so every
            # disappearance is skipped; a shrink was seen twice and is
            # real. See the note where this is raised.
            log.warning("whale-exit: %s", exc)
            stats["truncated_books"] = stats.get("truncated_books", 0) + 1
            now, sibs, seen = exc.book, exc.sibs, exc.seen
            if not now:
                continue
            partial = True
        except Exception as exc:  # noqa: BLE001 — one whale, not the loop
            log.warning("whale-exit positions failed for %s: %s", uname, exc)
            stats["fetch_failed"] = stats.get("fetch_failed", 0) + 1
            continue
        stats["pos_rows"] += seen
        stats["sib_rows"] += len(sibs)
        all_sibs.update(sibs)
        stats["whales"] += 1
        prev = await _load(pool, uname.lower())
        try:
            guard_empty(prev, now, r["address"])
        except EmptyPositions as exc:
            log.warning("whale-exit: %s", exc)
            stats["empty_books"] = stats.get("empty_books", 0) + 1
            continue
        if not prev:
            # No previous state: diffing against nothing would read every
            # holding as a fresh exit and fire a full close on each.
            await _save(pool, uname.lower(), now)
            stats["first_snapshots"] += 1
            continue
        # WHICH OF THE VANISHED MARKETS ACTUALLY RESOLVED.
        #
        # diff_exits now treats a disappearance as a full exit unless
        # the market resolved. That is only safe if the resolved set is
        # REAL — an empty set here would turn every settlement into a
        # sell order against a position the venue already closed.
        #
        # So the query failing is not "assume nothing resolved". It
        # falls back to the old behaviour (skip every disappearance),
        # which forfeits coverage rather than risking orders.
        gone = [a for a in prev if a not in now]
        # "NOT RESOLVED" AND "WE HAVE NEVER HEARD OF THIS TOKEN" WERE
        # THE SAME ANSWER (2026-08-26, adversarial round 4).
        #
        # The old query INNER JOINed market_tokens to markets and
        # returned only tokens with resolved=true. A vanished token that
        # is absent from market_tokens — or whose market row does not
        # exist — produced no row, and the caller read absence as
        # positive proof the market had NOT resolved, emitting a 100%
        # exit. The full-exit branch of mirror_exit calls
        # pmus.close_position: an unlimited market sell bounded only by
        # EXIT_SLIPPAGE_BIPS (300). So a position we simply had no
        # metadata for got flattened at the bid.
        #
        # The module's designed protection is that unknown-ness must
        # forfeit the cycle — that is what the except branch below is
        # for. But it only ever fired when the query RAISED, never when
        # the query simply could not see, and those are the same shape
        # coming back. This codebase has already measured that
        # population: EXITCENSUS cls_token_unenriched: 56 in one window.
        #
        # CLOSED COUNTS TOO, not just resolved. markets.resolved is fed
        # by an unordered LIMIT 500 sweep on a 300s cycle, so a
        # just-finished game is not guaranteed to be flagged yet, and in
        # that window a redemption reads as an exit and we sell a
        # near-certain $1.00 payout at the bid. `closed` is set when the
        # market stops trading, ahead of resolution, and a whale cannot
        # trade out of a closed market — so a disappearance there is a
        # redemption by construction.
        #
        # Every branch here only ever REFUSES more than before. There is
        # no input on which this sells something the old code would not.
        not_an_exit: set[str] | None = set()
        if gone:
            try:
                rows = await pool.fetch(
                    "SELECT DISTINCT mt.token_id, "
                    "       COALESCE(m.resolved, false) AS resolved, "
                    "       COALESCE(m.closed, false) AS closed "
                    "FROM market_tokens mt "
                    "JOIN markets m ON m.condition_id = mt.condition_id "
                    "WHERE mt.token_id = ANY($1::text[])", gone)
                known = {str(r["token_id"]) for r in rows}
                settled = {str(r["token_id"]) for r in rows
                           if r["resolved"] or r["closed"]}
                unknown = set(gone) - known
                if unknown:
                    log.warning(
                        "whale-exit: %s — %d vanished position(s) on "
                        "tokens we have NO market metadata for; refusing "
                        "to read them as exits (an unlimited close on a "
                        "market that may simply have settled)",
                        uname, len(unknown))
                stats["vanished_unknown"] = \
                    stats.get("vanished_unknown", 0) + len(unknown)
                stats["vanished_settled"] = \
                    stats.get("vanished_settled", 0) + len(settled)
                stats["vanished_live"] = stats.get("vanished_live", 0) + (
                    len(gone) - len(settled | unknown))
                not_an_exit = settled | unknown
            except Exception:  # noqa: BLE001 — unknown, so assume all
                log.warning("whale-exit: resolution lookup failed; "
                            "treating every vanished position as "
                            "possibly resolved and skipping it")
                not_an_exit = None
        if partial:
            # A disappearance from a PARTIAL book is not evidence -- the
            # asset may simply sit past the read cut. But it is not
            # nothing either, and skipping ALL of them made truncation a
            # permanent blind spot: the census read vanished_live:89
            # against exit_attempts:3, because the two whales with books
            # past POSITIONS_MAX (RN1 at 10k+ rows, 0x076daa87 at 7.8k,
            # per the 2026-08-26 venue ledger census) are exactly the
            # ones whose every full exit vanished into this branch. A
            # vanished-live position is a whale who has FULLY left while
            # we hold 100% of the copied notional -- the single largest
            # dollar class the exit lane exists for.
            #
            # So: for the vanished assets WE ACTUALLY HOLD (the only
            # ones that can produce dollars), and that the liveness
            # classification above did not already rule out, ask the
            # venue POSITIVELY, one market at a time. Absence from a
            # partial page is never proof; a filtered per-market read
            # that comes back without the leg is. Anything unconfirmed
            # -- errors, unfiltered responses, unknown tokens -- stays
            # skipped exactly as before.
            exclusion = set(gone)
            if not_an_exit is not None:
                live_gone = [a for a in gone if a not in not_an_exit]
                if live_gone:
                    ours = await pool.fetch(
                        "SELECT DISTINCT asset FROM live_orders "
                        "WHERE status = 'filled' "
                        "AND asset = ANY($1::text[])", live_gone)
                    held_here = {str(x["asset"]) for x in ours}
                    for a in live_gone:
                        if a not in held_here:
                            continue
                        if await _confirm_gone(http, pool,
                                               r["address"], a):
                            exclusion.discard(a)
                            stats["partial_vanished_confirmed"] = (
                                stats.get("partial_vanished_confirmed",
                                          0) + 1)
            stats["partial_vanished_skipped"] = (
                stats.get("partial_vanished_skipped", 0)
                + len([a for a in gone if a in exclusion]))
            found = diff_exits(prev, now, exclusion)
        elif not_an_exit is None:
            found = diff_exits(prev, now, set(gone))
        else:
            found = diff_exits(prev, now, not_an_exit)
        # Round-robin across a backlog, so a refused exit that keeps
        # being pinned cannot crowd out one that has never been tried.
        tried = await _load_retry(pool, uname.lower())
        found = rotate_for_fairness(found, tried)
        acting = found[:MAX_EXITS_PER_CYCLE]
        deferred = found[MAX_EXITS_PER_CYCLE:]
        if deferred:
            log.warning("whale-exit: %s has %d exits this cycle, acting on "
                        "%d — the rest are HELD BACK IN THE SNAPSHOT so "
                        "they are found again next cycle",
                        uname, len(found), MAX_EXITS_PER_CYCLE)
            stats["deferred"] = stats.get("deferred", 0) + len(deferred)
        # THE DEFERRED EXITS WERE BEING DROPPED FOREVER (2026-08-25,
        # adversarial review).
        #
        # The snapshot was saved BEFORE the diff was acted on, which is
        # right for crash safety — a crash mid-cycle must not replay the
        # same diff and fire the sells twice. But it saved `now`
        # WHOLESALE, so the next cycle's `prev` already reflected the
        # exits we had just declined to act on. The cap did not defer
        # them, it discarded them, and the log line said the opposite:
        # "the rest still read as shrunk next cycle" was false.
        #
        # It matters at exactly the moment the cap exists for. swisstony
        # held below purchase on 62 of 75 positions; a cycle like that
        # acts on 10 and silently threw away 52 real exits — positions
        # the whale had left and we would go on holding to resolution,
        # which is the divergence this whole worker exists to close.
        #
        # Both properties are available at once. Save `now`, but PIN the
        # deferred assets at their previous size, so next cycle diffs
        # pre-exit against post-exit and finds them again. Acted-on
        # exits are recorded as done and cannot re-fire; a crash after
        # the save still cannot replay them.
        # A PARTIAL READ MUST NOT BECOME THE SNAPSHOT. Saving `now`
        # wholesale after a truncated read makes every unread position
        # look VANISHED next cycle -- turning a read limit into a fleet
        # of false full exits, the exact hazard the forfeit protected
        # against. Update what we saw; keep what we could not see.
        to_save = dict(prev) if partial else {}
        to_save.update(now)
        for asset, _frac in deferred:
            if asset in prev:
                to_save[asset] = prev[asset]
        # THE SUB-MIN_SHRINK DEAD BAND (2026-08-26, adversarial
        # workflow). diff_exits drops a shrink under MIN_SHRINK (5%) as
        # noise -- right, per observation -- but this save then advanced
        # the baseline anyway, so a whale trimming 3% a cycle was
        # measured 3% against a fresh baseline every time and NEVER
        # accumulated. The mx_below_floor pending ratchet downstream
        # only engages at >= MIN_SHRINK, so the 0-5%-per-cycle band was
        # structurally invisible: he could walk out of a position in
        # twenty steps while every observation read "noise".
        #
        # PIN, do not lower: the baseline stays at the pre-trim size
        # until the CUMULATIVE fraction crosses MIN_SHRINK and enters
        # the diff, then rides the existing ratchet across
        # MIN_EXIT_FRAC. A re-add at or above the pinned size clears
        # the pin through the `after >= before` skip, so noise cannot
        # accumulate into a false exit. No floor moved.
        for asset, before in prev.items():
            after = now.get(asset)
            if after is None or before <= 0 or after >= before:
                continue
            if (before - after) / before < MIN_SHRINK:
                to_save[asset] = before
        await _save(pool, uname.lower(), to_save)
        # A REFUSED EXIT WAS ERASED FOREVER (2026-08-26, adversarial
        # round 3).
        #
        # The snapshot above is saved before acting, which is right for
        # crash safety — a crash mid-cycle must not replay the diff and
        # fire the sells twice. But it advanced the asset the moment the
        # exit was HANDED to execute_copy, and execute_copy reported
        # nothing back. So an exit the sleeve REFUSED read as an exit
        # the sleeve COMPLETED, and next cycle's prev already agreed the
        # whale was out. The position was ours to hold to resolution
        # against him, permanently, with no counter saying so.
        #
        # It is not hypothetical: the census shows mx_overspend_halt 326
        # while the breaker sat tripped on a false positive. 326 real
        # exits destroyed by a refusal that cleared with one POST.
        #
        # Same instrument as the deferred fix — PIN the asset at its
        # pre-exit size — but applied only to the reasons that mean "we
        # still hold and he is still out" (EXIT_PENDING_REASONS, an
        # allowlist owned by the module that produces them). Anything
        # else, an unrecognised reason included, advances exactly as it
        # does today: a reason nobody classified must not be able to
        # create a snapshot that re-diffs forever.
        pending: list[str] = []
        for asset, frac in acting:
            stats["exit_attempts"] += 1
            log.warning("WHALE EXIT %s %s: closed %.0f%% (positions, not "
                        "a trade)", uname, asset, frac * 100)
            reason = await execute_copy({"whale_username": uname,
                                         "asset": asset, "side": "SELL",
                                         "closed_frac": frac})
            if reason == "mx_SOLD":
                stats["exits_sold"] += 1
                stats["exits"] += 1
            elif reason in EXIT_PENDING_REASONS:
                pending.append(asset)
                stats["exits_pending"] += 1
                # Named per reason as well as totalled. "held back" is
                # only actionable if the reader can see WHAT to clear.
                key = "pend_" + str(reason)
                stats[key] = stats.get(key, 0) + 1
            else:
                stats["exits_no_action"] += 1
        if pending:
            for asset in pending:
                if asset in prev:
                    to_save[asset] = prev[asset]
            log.warning("whale-exit: %s — %d exit(s) REFUSED and held in "
                        "the snapshot for a retry next cycle", uname,
                        len(pending))
            await _save(pool, uname.lower(), to_save)
        # Advance the cursor even when nothing is pending: an asset that
        # settled this cycle has to LEAVE the cursor, or it keeps a
        # stale rank and sorts ahead of genuinely older work forever.
        attempted = [a for a, _f in acting]
        if attempted or tried:
            await _save_retry(pool, uname.lower(),
                              next_cursor(tried, attempted, set(pending)))
    # PUBLISH THE SIBLING MAP, MERGED not replaced.
    #
    # A whale's positions payload only describes the markets he is in
    # RIGHT NOW, so replacing the map each cycle would drop the sibling
    # of every position he closed — which is exactly the population
    # classify_exit asks about. Merging keeps them.
    #
    # Bounded so a long-running worker cannot grow this row without
    # limit; oldest insertions go first, which is the right order
    # because a market he has been out of for a long time is one we are
    # also out of.
    if all_sibs:
        try:
            prev = await _load_siblings(pool)
            prev.update(all_sibs)
            if len(prev) > MAX_SIBLINGS:
                prev = dict(list(prev.items())[-MAX_SIBLINGS:])
            await pool.execute(
                "INSERT INTO ingestion_state (key, value) "
                "VALUES ($1, $2::jsonb) ON CONFLICT (key) DO UPDATE "
                "SET value = $2::jsonb",
                _SIB_STATE_KEY, json.dumps(prev))
            stats["sib_pairs"] = len(prev)
        except Exception:  # noqa: BLE001 — a by-product, never the loop
            log.warning("whale-exit: sibling map write failed")
    return stats


async def main() -> None:
    if not ENABLED:
        log.warning("whale-exit detector disabled by env")
        return
    cfg = settings()
    async with httpx.AsyncClient(base_url=cfg.data_api_base,
                                 timeout=25.0) as http:
        while True:
            try:
                pool = await get_pool()
                stats = await _cycle(http, pool)
                await heartbeat("whale_exits", detail=stats)
            except Exception:  # noqa: BLE001 — never kill the loop
                log.exception("whale-exit cycle failed")
            await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
