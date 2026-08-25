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
POSITIONS_MAX = int(os.environ.get("WHALE_EXIT_POS_MAX", "2000"))


class TruncatedPositions(RuntimeError):
    """The venue still had more positions when we stopped reading.

    Raised rather than returned because a partial book must never reach
    diff_exits: every asset past the cut is absent from `now`, which
    reads as a FULL EXIT and fires a 100% close on a position the whale
    still holds. Losing a cycle costs a delay; diffing a truncated book
    costs real sell orders."""
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
        raise TruncatedPositions(
            f"{address}: still returning full pages at {POSITIONS_MAX} "
            f"positions — refusing to diff against an incomplete book")
    return out, sibs, seen


def diff_exits(prev: dict[str, float],
               now: dict[str, float],
               resolved: set[str] | None = None) -> list[tuple[str, float]]:
    """(asset, closed_fraction) for holdings that SHRANK.

    Pure, so the rule is testable without a venue:
      * asset missing from `now` -> skipped. Could be an exit, could be
        a resolved market; resolution settles our copy on its own and
        mirroring it would sell a position that no longer exists.
      * grew -> not an exit.
      * shrank by less than MIN_SHRINK of the position -> noise.
    """
    out: list[tuple[str, float]] = []
    res = resolved or set()
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
            # We already know which markets resolved: the caller passes
            # the resolved set. Unresolved and gone is a close, at
            # 100%. Resolved and gone is still skipped, so the original
            # protection is intact rather than traded away.
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


async def _cycle(http: httpx.AsyncClient, pool) -> dict:
    from ..api.copies_record import COPY_WHALES
    from ..live_executor import execute_copy

    stats = {"whales": 0, "exits": 0, "first_snapshots": 0,
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
        try:
            now, sibs, seen = await _fetch_positions(http, r["address"])
        except TruncatedPositions as exc:
            # Forfeit this whale's cycle rather than diff a book we know
            # is incomplete. Counted, because a lane that silently skips
            # a whale every cycle looks exactly like a whale who never
            # exits.
            log.warning("whale-exit: %s", exc)
            stats["truncated_books"] = stats.get("truncated_books", 0) + 1
            continue
        except Exception as exc:  # noqa: BLE001 — one whale, not the loop
            log.warning("whale-exit positions failed for %s: %s", uname, exc)
            stats["fetch_failed"] = stats.get("fetch_failed", 0) + 1
            continue
        stats["pos_rows"] += seen
        stats["sib_rows"] += len(sibs)
        all_sibs.update(sibs)
        stats["whales"] += 1
        prev = await _load(pool, uname.lower())
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
        resolved: set[str] | None = set()
        if gone:
            try:
                rows = await pool.fetch(
                    "SELECT DISTINCT mt.token_id FROM market_tokens mt "
                    "JOIN markets m ON m.condition_id = mt.condition_id "
                    "WHERE mt.token_id = ANY($1::text[]) "
                    "  AND COALESCE(m.resolved, false) = true", gone)
                resolved = {str(r["token_id"]) for r in rows}
            except Exception:  # noqa: BLE001 — unknown, so assume all
                log.warning("whale-exit: resolution lookup failed; "
                            "treating every vanished position as "
                            "possibly resolved and skipping it")
                resolved = None
        if resolved is None:
            found = diff_exits(prev, now, set(gone))
        else:
            found = diff_exits(prev, now, resolved)
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
        to_save = dict(now)
        for asset, _frac in deferred:
            if asset in prev:
                to_save[asset] = prev[asset]
        await _save(pool, uname.lower(), to_save)
        for asset, frac in acting:
            stats["exits"] += 1
            log.warning("WHALE EXIT %s %s: closed %.0f%% (positions, not "
                        "a trade)", uname, asset, frac * 100)
            await execute_copy({"whale_username": uname, "asset": asset,
                                "side": "SELL", "closed_frac": frac})
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
