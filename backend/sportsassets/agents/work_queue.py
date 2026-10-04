"""XAVIER'S FRESH-EVIDENCE WORK QUEUE (owner R30, migration 226).

THE DEFECT. A review on stale evidence records WAITING_FOR_FRESH_EVIDENCE
(or MANAGEMENT_UNAVAILABLE_STALE_INPUT) -- correctly: no discretionary sale
is ranked on a stale probability -- and then enqueued NOTHING. The next look
at the position was the 60 s backstop, which found the same stale evidence.
Waiting was passive.

WHAT THIS DOES. When Xavier's review of a position records one of those two
non-actions, `after_review` (one call from paper_xavier.review_group, after
the review row is written) enqueues CONCRETE, DURABLE requests
(agent_work_requests + agent_work_request_events, append-only):

  PROBABILITY             a fresh probability for the held contract: the
                          existing PinnAPI held / reactive re-evaluation path
                          for the event (pinnapi_reactive.request_held_
                          reevaluation: the held queue, held-first, on the
                          scheduler's own single worker and deadline).
                          COMPLETED by a stored valuation of the group's own
                          contract decided after the request and inside the
                          freshness limit (external_valuations id), or by a
                          later review that was itself FRESH.
  VENUE_BOOK              the venue's current marks / depth: a PRIORITY book
                          read of the slug through the existing step_books
                          machinery (paper_runtime.step_books consumes
                          `priority_book_slugs` before resting orders and
                          held positions; the drain reads any left within the
                          pass's own read cap). COMPLETED by the observation
                          it produced (paper_book_observations obs_id);
                          FAILED when the read itself failed. Not enqueued
                          when the review's own book is already current.
  GAME_STATE              the fixture's current reported state, only where a
                          fixture identity exists (the entry valuation's
                          global condition id -> fixture_metadata). Acquired
                          by the same held re-evaluation (the collector lane
                          re-acquires fixture_metadata for the candidate it
                          evaluates: ext_pinnacle_loop.acquire_fixture_
                          scope). COMPLETED by a fixture row current under
                          bettor_fixture_store's own bound. Not enqueued
                          while the row is already current.
  MANAGEMENT_REASSESSMENT Xavier's re-review once evidence lands: dispatched
                          (paper_runtime.schedule_held_review, the existing
                          debounced path) after any evidence request of the
                          same batch COMPLETED; COMPLETED by the next review
                          of the position recorded after that (the review
                          id). The re-review decides on fresh evidence only:
                          the existing freshness rule (paper_xavier: EXIT /
                          REDUCE stripped on stale evidence; xavier_freshness.
                          recorded_recommendation) is untouched, so a re-
                          review that still lacks a fresh probability records
                          WAITING again -- and enqueues again, bounded below.

Every request is ENQUEUED, at most once DISPATCHED (what the acquisition path
answered, named), and then exactly once COMPLETED (with the evidence row it
produced) or FAILED (with why: expired without fresh evidence, the read
failed, the position closed).

BOUNDED, so it cannot flood the venue or PinnAPI:
  * one OPEN request per (agent, position, kind) -- the database's primary
    key on agent_work_open, not a convention;
  * every request expires (REQUEST_TTL_S) and is then FAILED, freeing the
    slot; a kind re-enqueues for a position no sooner than
    REENQUEUE_AFTER_COMPLETED_S after a completion and, after consecutive
    failures, only after a doubling backoff (FAILURE_BACKOFF_BASE_S up to
    FAILURE_BACKOFF_MAX_S);
  * at most MAX_OPEN_REQUESTS open at once;
  * per pass: at most MAX_PRIORITY_BOOKS_PER_PASS priority book reads, all
    inside the pass's existing `max_book_reads_per_pass` cap and deadline
    (no extra venue request is ever made: the reads are re-ordered, not
    added), at most MAX_DISPATCH_PER_PASS held re-evaluation requests (each
    one is queued only when the feed already holds a fresh, not-yet-
    evaluated quote; the scheduler coalesces and runs one at a time), at most
    MAX_REASSESS_PER_PASS re-review dispatches (themselves debounced).

NO AUTHORITY. It writes only the three work-queue tables. It places, cancels
and sizes nothing, changes no threshold, limit or policy, and never makes a
probability fresh: freshness stays change-driven at the source. NEVER RAISES
into its caller: a failure is returned by name.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from .. import xavier_freshness as XF

log = logging.getLogger(__name__)

VERSION = "AGENT_WORK_QUEUE_V1"
AGENT = "XAVIER"
PAPER = "PAPER"

K_PROBABILITY = "PROBABILITY"
K_VENUE_BOOK = "VENUE_BOOK"
K_GAME_STATE = "GAME_STATE"
K_REASSESS = "MANAGEMENT_REASSESSMENT"
KINDS = (K_PROBABILITY, K_VENUE_BOOK, K_GAME_STATE, K_REASSESS)
EVIDENCE_KINDS = (K_PROBABILITY, K_VENUE_BOOK, K_GAME_STATE)
#: the review outcomes that raise requests (and nothing else)
TRIGGER_RECOMMENDATIONS = XF.NON_ACTIONS

S_ENQUEUED, S_DISPATCHED = "ENQUEUED", "DISPATCHED"
S_COMPLETED, S_FAILED = "COMPLETED", "FAILED"

F_EXPIRED = "EXPIRED_WITHOUT_FRESH_EVIDENCE"
F_NO_EVIDENCE = "NO_FRESH_EVIDENCE_LANDED_BEFORE_EXPIRY"
F_BOOK_READ = "VENUE_BOOK_READ_FAILED"
F_CLOSED = "POSITION_NO_LONGER_OPEN"

R_NO_SCHEMA = "MIGRATION_226_NOT_APPLIED"
R_DEDUPED = "OPEN_REQUEST_EXISTS"
R_BACKOFF = "REENQUEUE_BACKOFF"
R_BOUND = "OPEN_REQUEST_BOUND_REACHED"
R_CURRENT = "EVIDENCE_ALREADY_CURRENT"
R_NO_FIXTURE = "NO_FIXTURE_IDENTITY"

REQUEST_TTL_S = 300.0
REENQUEUE_AFTER_COMPLETED_S = 600.0
FAILURE_BACKOFF_BASE_S = 600.0
FAILURE_BACKOFF_MAX_S = 3600.0
MAX_OPEN_REQUESTS = 400
MAX_PRIORITY_BOOKS_PER_PASS = 3
MAX_DISPATCH_PER_PASS = 20
MAX_REASSESS_PER_PASS = 10
MAX_CHECKS_PER_PASS = 60
MAX_EXPIRE_PER_PASS = 200
#: a book observed this recently, and readable, is the current mark: the
#: same 30 s as the odds source's freshness rule (read from the review's own
#: recorded limit when it carries one)
DEFAULT_CURRENT_S = 30.0


class _Deduped(Exception):
    """An open request of this (agent, position, kind) exists: roll back."""


def _now(ctx: dict | None, at=None) -> float:
    if at is not None:
        return float(at)
    c = (ctx or {}).get("clock")
    if c is not None:
        return float(c())
    if (ctx or {}).get("now") is not None:
        return float(ctx["now"])
    return time.time()


def _ep(v):
    return XF._ep(v)


def _h(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()
                          ).hexdigest()[:24]


def _limit_s(ctx: dict | None, measure: dict | None = None) -> float:
    m = measure or {}
    for v in (m.get("probability_limit_s"), m.get("pinnacle_limit_s")):
        if _ep(v):
            return float(v)
    try:
        return float(ctx["config"]["entry"]["pinnacle_max_age_s"])
    except (KeyError, TypeError, ValueError):
        return DEFAULT_CURRENT_S


def backoff_s(history: list) -> float | None:
    """HOW LONG A KIND MUST WAIT before it is enqueued again for a position,
    from its recent terminal history (newest first: [{"state", "at"}]).
    Pure. None: no wait. After a COMPLETED: REENQUEUE_AFTER_COMPLETED_S.
    After n consecutive FAILED: FAILURE_BACKOFF_BASE_S x 2^(n-1), at most
    FAILURE_BACKOFF_MAX_S."""
    if not history:
        return None
    if history[0].get("state") == S_COMPLETED:
        return REENQUEUE_AFTER_COMPLETED_S
    n = 0
    for h in history:
        if h.get("state") != S_FAILED:
            break
        n += 1
    return min(FAILURE_BACKOFF_BASE_S * (2 ** max(0, n - 1)),
               FAILURE_BACKOFF_MAX_S)


async def has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('agent_work_open') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


# ═════════════════════════════════════════════════════════════════════
# THE WRITES (each in its own savepoint: a refusal never aborts the caller)
# ═════════════════════════════════════════════════════════════════════

async def _event(conn, request_id: str, state: str, *, at: float,
                 evidence_table=None, evidence_id=None, failure=None,
                 detail: dict | None = None) -> bool:
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO agent_work_request_events (request_id, state, "
                " at, evidence_table, evidence_id, failure, detail) VALUES "
                " ($1,$2,to_timestamp($3),$4,$5,$6,$7::jsonb)",
                request_id, state, at, evidence_table,
                None if evidence_id is None else str(evidence_id), failure,
                json.dumps(detail or {}, default=str))
        return True
    except Exception as exc:                                    # noqa: BLE001
        log.info("work_queue: %s event for %s refused (%s)", state,
                 request_id, type(exc).__name__)
        return False


async def complete(conn, request_id: str, *, at: float, evidence_table: str,
                   evidence_id, detail: dict | None = None) -> bool:
    return await _event(conn, request_id, S_COMPLETED, at=at,
                        evidence_table=evidence_table,
                        evidence_id=evidence_id, detail=detail)


async def fail(conn, request_id: str, *, at: float, failure: str,
               detail: dict | None = None) -> bool:
    return await _event(conn, request_id, S_FAILED, at=at, failure=failure,
                        detail=detail)


async def dispatch(conn, request_id: str, *, at: float,
                   detail: dict | None = None) -> bool:
    return await _event(conn, request_id, S_DISPATCHED, at=at, detail=detail)


async def _history(conn, *, group_id: str, kind: str, at: float) -> list:
    rows = await conn.fetch(
        "SELECT e.state, extract(epoch FROM e.at)::float8 AS at "
        "  FROM agent_work_requests r JOIN agent_work_request_events e "
        "    ON e.request_id = r.request_id "
        " WHERE r.agent_id=$1 AND r.position_kind=$2 AND r.group_id=$3 "
        "   AND r.kind=$4 AND e.state IN ('COMPLETED','FAILED') "
        "   AND e.at > to_timestamp($5) "
        " ORDER BY e.at DESC, e.event_id DESC LIMIT 8",
        AGENT, PAPER, group_id, kind, at - 6 * FAILURE_BACKOFF_MAX_S)
    return [dict(r) for r in rows]


async def _expire_stale_slot(conn, *, group_id: str, kind: str,
                             at: float) -> bool:
    """An open request of this slot past its expiry is FAILED now (the
    drain normally does it; a held review between passes must not be
    deduplicated against a dead request). True when one was closed."""
    r = await conn.fetchrow(
        "SELECT o.request_id, (SELECT d.detail FROM agent_work_request_events"
        "        d WHERE d.request_id = o.request_id "
        "          AND d.state = 'DISPATCHED') AS dispatched "
        "  FROM agent_work_open o JOIN agent_work_requests r "
        "    ON r.request_id = o.request_id "
        " WHERE o.agent_id=$1 AND o.position_kind=$2 AND o.group_id=$3 "
        "   AND o.kind=$4 AND r.expires_at < to_timestamp($5)",
        AGENT, PAPER, group_id, kind, at)
    if r is None:
        return False
    return await fail(conn, r["request_id"], at=at, failure=(
        F_NO_EVIDENCE if kind == K_REASSESS else F_EXPIRED),
        detail={"dispatched": XF._j(r["dispatched"]),
                "closed_by": "enqueue"})


async def enqueue(conn, *, kind: str, group_id: str, reason: str, at: float,
                  batch_id: str, slug: str | None = None,
                  fixture_identity: str | None = None,
                  source_table: str | None = None,
                  source_id: str | None = None,
                  detail: dict | None = None,
                  ttl_s: float = REQUEST_TTL_S) -> dict:
    """ONE REQUEST, deduplicated by the database (one open per agent,
    position, kind), backed off, bounded (MAX_OPEN_REQUESTS open per
    account). Never raises."""
    out: dict[str, Any] = {"kind": kind, "enqueued": False}
    try:
        await _expire_stale_slot(conn, group_id=group_id, kind=kind, at=at)
        hist = await _history(conn, group_id=group_id, kind=kind, at=at)
        wait = backoff_s(hist)
        if wait is not None and at - float(hist[0]["at"]) < wait:
            return dict(out, why=R_BACKOFF, retry_after_s=round(
                wait - (at - float(hist[0]["at"])), 1))
        n_open = int(await conn.fetchval(
            "SELECT count(*) FROM agent_work_open o JOIN agent_work_requests"
            " r ON r.request_id = o.request_id WHERE o.agent_id=$1 "
            "   AND r.detail->>'account_id' IS NOT DISTINCT FROM $2",
            AGENT, (detail or {}).get("account_id")))
        if n_open >= MAX_OPEN_REQUESTS:
            return dict(out, why=R_BOUND, open=n_open)
        rid = "awr:" + _h(AGENT, PAPER, group_id, kind, at, batch_id)
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO agent_work_requests (request_id, agent_id, kind,"
                " position_kind, group_id, us_market_slug, fixture_identity,"
                " reason, source_table, source_id, batch_id, enqueued_at, "
                " expires_at, detail) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,"
                " $11,to_timestamp($12),to_timestamp($13),$14::jsonb)",
                rid, AGENT, kind, PAPER, group_id, slug, fixture_identity,
                reason, source_table, source_id, batch_id, at, at + ttl_s,
                json.dumps(dict(detail or {}, version=VERSION), default=str))
            got = await conn.fetchval(
                "INSERT INTO agent_work_open (agent_id, position_kind, "
                " group_id, kind, request_id, opened_at) VALUES "
                " ($1,$2,$3,$4,$5,to_timestamp($6)) "
                "ON CONFLICT DO NOTHING RETURNING request_id",
                AGENT, PAPER, group_id, kind, rid, at)
            if got is None:
                raise _Deduped()
            await conn.execute(
                "INSERT INTO agent_work_request_events (request_id, state, "
                " at, detail) VALUES ($1,'ENQUEUED',to_timestamp($2),"
                " $3::jsonb)", rid, at,
                json.dumps({"reason": reason, "batch_id": batch_id}))
        return dict(out, enqueued=True, request_id=rid)
    except _Deduped:
        return dict(out, why=R_DEDUPED)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, why="ENQUEUE_FAILED", error=type(exc).__name__)


async def open_requests(conn, *, group_ids=None, kinds=None,
                        limit: int = MAX_CHECKS_PER_PASS,
                        account_id: str | None = None,
                        expired_before: float | None = None) -> list:
    """Open requests (oldest first), with their dispatch state; optionally
    only one account's, or only those expired before an instant."""
    rows = await conn.fetch(
        "SELECT r.request_id, r.kind, r.group_id, r.us_market_slug, "
        "       r.fixture_identity, r.reason, r.batch_id, r.detail, "
        "       extract(epoch FROM r.enqueued_at)::float8 AS enqueued_at, "
        "       extract(epoch FROM r.expires_at)::float8 AS expires_at, "
        "       (SELECT d.detail FROM agent_work_request_events d "
        "         WHERE d.request_id = r.request_id "
        "           AND d.state = 'DISPATCHED') AS dispatched "
        "  FROM agent_work_open o JOIN agent_work_requests r "
        "    ON r.request_id = o.request_id "
        " WHERE o.agent_id = $1 "
        "   AND ($2::text[] IS NULL OR o.group_id = ANY($2::text[])) "
        "   AND ($3::text[] IS NULL OR o.kind = ANY($3::text[])) "
        "   AND ($5::text IS NULL OR r.detail->>'account_id' = $5) "
        "   AND ($6::float8 IS NULL OR r.expires_at < to_timestamp($6)) "
        " ORDER BY r.enqueued_at, r.request_id LIMIT $4",
        AGENT, None if group_ids is None else list(group_ids),
        None if kinds is None else list(kinds), int(limit), account_id,
        expired_before)
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = XF._j(d.get("detail")) or {}
        d["dispatched"] = XF._j(d.get("dispatched"))
        out.append(d)
    return out


async def _batch_has_evidence(conn, batch_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM agent_work_requests r "
        "  JOIN agent_work_request_events e ON e.request_id = r.request_id "
        " WHERE r.batch_id = $1 AND r.kind <> 'MANAGEMENT_REASSESSMENT' "
        "   AND e.state = 'COMPLETED')", batch_id))


# ═════════════════════════════════════════════════════════════════════
# AFTER EACH REVIEW (the one call from paper_xavier.review_group)
# ═════════════════════════════════════════════════════════════════════

async def _fixture_identity(conn, group_id: str) -> str | None:
    """The position's fixture identity: its entry valuation's global
    condition id (the key fixture_metadata is stored under), or None."""
    try:
        cid = await conn.fetchval(
            "SELECT v.condition_id FROM paper_orders o "
            "  JOIN paper_decisions d ON d.decision_id = o.decision_id "
            "  JOIN external_valuations v ON v.id = d.valuation_id "
            " WHERE o.group_id = $1 AND o.role = 'ENTRY' "
            "   AND v.condition_id IS NOT NULL LIMIT 1", group_id)
    except Exception:                                           # noqa: BLE001
        return None
    cid = str(cid or "").strip()
    return cid or None


async def _fixture_current(conn, condition_id: str, *, at: float) -> dict:
    """bettor_fixture_store's own read and bound: is the fixture's reported
    state current? {"current", "retrieved_at", "why"}."""
    from .. import bettor_fixture_store as FS
    row = await FS.read(conn, condition_id)
    need = FS.needs_acquisition(row, now=at)
    return {"current": not need.get("acquire"),
            "retrieved_at": (row or {}).get("retrieved_at_epoch"),
            "why": need.get("why"), "read": bool((row or {}).get("read"))}


async def _book(conn, obs_id) -> dict | None:
    if obs_id is None:
        return None
    r = await conn.fetchrow(
        "SELECT obs_id, extract(epoch FROM observed_at)::float8 AS at, "
        "       error FROM paper_book_observations WHERE obs_id = $1",
        int(obs_id))
    return None if r is None else dict(r)


async def after_review(conn, ctx: dict, *, group_id: str, pos: dict,
                       review_id: str, recommendation, measure: dict,
                       at: float | None = None) -> dict:
    """WHAT ONE REVIEW SATISFIES, AND WHAT IT STILL NEEDS. Never raises.

    1. Closes the position's open requests this review satisfies: a FRESH
       review completes PROBABILITY; the review's own book (observed after
       the request, readable) completes VENUE_BOOK; a review recorded after
       evidence of its batch landed (or on fresh evidence) completes
       MANAGEMENT_REASSESSMENT -- each with the evidence id.
    2. When the review recorded WAITING_FOR_FRESH_EVIDENCE or
       MANAGEMENT_UNAVAILABLE_STALE_INPUT: enqueues PROBABILITY, VENUE_BOOK
       (unless its book is already current), GAME_STATE (where a fixture
       identity exists and its state is not current) and, with them,
       MANAGEMENT_REASSESSMENT."""
    t = _now(ctx, at)
    out: dict[str, Any] = {"group_id": group_id, "completed": [],
                           "enqueued": [], "skipped": []}
    try:
        if not await has_schema(conn):
            return dict(out, refusal=R_NO_SCHEMA)
        m = measure or {}
        fresh = m.get("evidence_state") == XF.E_FRESH
        limit = _limit_s(ctx, m)
        book = await _book(conn, m.get("book_obs_id"))
        # ── 1 · what this review satisfies ──────────────────────────
        for r in await open_requests(conn, group_ids=[group_id]):
            done = None
            if r["kind"] == K_PROBABILITY and fresh:
                vid = m.get("valuation_id")
                done = (("external_valuations", vid) if vid is not None
                        else ("paper_xavier_reviews", review_id))
                det = {"by_review": review_id,
                       "probability_source": m.get("probability_source")
                       or m.get("source"),
                       "probability_source_at": m.get(
                           "probability_source_at"),
                       "probability_age_s": m.get("probability_age_s")}
            elif r["kind"] == K_VENUE_BOOK and book is not None \
                    and not book.get("error") \
                    and float(book["at"]) >= float(r["enqueued_at"]) - 1e-6:
                done = ("paper_book_observations", book["obs_id"])
                det = {"by_review": review_id, "observed_at": book["at"]}
            elif r["kind"] == K_REASSESS and (
                    fresh or await _batch_has_evidence(conn, r["batch_id"])):
                done = ("paper_xavier_reviews", review_id)
                det = {"evidence_state": m.get("evidence_state"),
                       "recommendation": recommendation}
            if done is not None and await complete(
                    conn, r["request_id"], at=t, evidence_table=done[0],
                    evidence_id=done[1], detail=det):
                out["completed"].append({"request_id": r["request_id"],
                                         "kind": r["kind"],
                                         "evidence": "%s:%s" % done})
        # ── 2 · what it still needs ─────────────────────────────────
        if recommendation not in TRIGGER_RECOMMENDATIONS:
            return out
        slug = pos.get("us_market_slug")
        base = {"group_id": group_id, "reason": recommendation, "at": t,
                "batch_id": review_id, "source_table": "paper_xavier_reviews",
                "source_id": review_id}
        det = {"account_id": ctx.get("account_id"),
               "holding_side": pos.get("holding_side"),
               "evidence_state": m.get("evidence_state"),
               "feed_refusal": m.get("feed_refusal")}
        evidence_open = False
        got = await enqueue(conn, kind=K_PROBABILITY, slug=slug,
                            detail=dict(det, probability_age_s=m.get(
                                "probability_age_s")), **base)
        out["enqueued" if got["enqueued"] else "skipped"].append(got)
        evidence_open |= got["enqueued"] or got.get("why") == R_DEDUPED
        book_current = (book is not None and not book.get("error")
                        and 0 <= t - float(book["at"]) <= limit)
        if book_current:
            out["skipped"].append({"kind": K_VENUE_BOOK, "why": R_CURRENT,
                                   "obs_id": book["obs_id"]})
        else:
            got = await enqueue(conn, kind=K_VENUE_BOOK, slug=slug,
                                detail=dict(det, book=book), **base)
            out["enqueued" if got["enqueued"] else "skipped"].append(got)
            evidence_open |= got["enqueued"] or got.get("why") == R_DEDUPED
        cid = await _fixture_identity(conn, group_id)
        if cid is None:
            out["skipped"].append({"kind": K_GAME_STATE, "why": R_NO_FIXTURE})
        else:
            fx = await _fixture_current(conn, cid, at=t)
            if fx["current"]:
                out["skipped"].append({"kind": K_GAME_STATE,
                                       "why": R_CURRENT,
                                       "retrieved_at": fx["retrieved_at"]})
            else:
                got = await enqueue(conn, kind=K_GAME_STATE, slug=slug,
                                    fixture_identity="fixture_metadata:%s"
                                    % cid, detail=dict(det, fixture=fx),
                                    **base)
                out["enqueued" if got["enqueued"] else "skipped"].append(got)
                evidence_open |= got["enqueued"] or got.get(
                    "why") == R_DEDUPED
        if evidence_open:
            got = await enqueue(conn, kind=K_REASSESS, slug=slug,
                                detail=det, **base)
            out["enqueued" if got["enqueued"] else "skipped"].append(got)
        return out
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, error=type(exc).__name__)


# ═════════════════════════════════════════════════════════════════════
# THE PRIORITY BOOK LIST (paper_runtime.step_books consumes it)
# ═════════════════════════════════════════════════════════════════════

async def priority_book_slugs(conn, *, account_id: str | None = None,
                              limit: int = MAX_PRIORITY_BOOKS_PER_PASS
                              ) -> list:
    """The slugs of this account's open VENUE_BOOK requests (any account's
    when `account_id` is None), oldest request first, at most `limit`
    distinct. Never raises ([] without the schema)."""
    try:
        if not await has_schema(conn):
            return []
        rows = await open_requests(conn, kinds=[K_VENUE_BOOK],
                                   limit=MAX_OPEN_REQUESTS)
    except Exception:                                           # noqa: BLE001
        return []
    out: list = []
    for r in rows:
        s = r.get("us_market_slug")
        if account_id is not None and \
                (r.get("detail") or {}).get("account_id") != account_id:
            continue
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


async def complete_book_reads(conn, obs: dict, *, at: float) -> dict:
    """Close the open VENUE_BOOK requests of the slugs just read: COMPLETED
    with the observation id, FAILED when the read failed. Never raises."""
    out = {"completed": 0, "failed": 0}
    try:
        if not obs or not await has_schema(conn):
            return out
        for r in await open_requests(conn, kinds=[K_VENUE_BOOK],
                                     limit=MAX_OPEN_REQUESTS):
            rec = obs.get(r.get("us_market_slug"))
            if not rec or rec.get("obs_id") is None:
                continue
            if rec.get("error"):
                if await fail(conn, r["request_id"], at=at,
                              failure=F_BOOK_READ,
                              detail={"obs_id": rec["obs_id"],
                                      "error": str(rec["error"])[:200]}):
                    out["failed"] += 1
            elif await complete(conn, r["request_id"], at=at,
                                evidence_table="paper_book_observations",
                                evidence_id=rec["obs_id"],
                                detail={"observed_at": rec.get(
                                    "observed_at")}):
                out["completed"] += 1
    except Exception as exc:                                    # noqa: BLE001
        out["error"] = type(exc).__name__
    return out


# ═════════════════════════════════════════════════════════════════════
# THE DRAIN (a paper-pass step, after Xavier's)
# ═════════════════════════════════════════════════════════════════════

def _request_held(slug: str) -> dict:
    """The existing PinnAPI held / reactive re-evaluation path. In-process,
    no I/O; never raises."""
    try:
        from .. import pinnapi_reactive as PR
        return PR.request_held_reevaluation(slug)
    except Exception as exc:                                    # noqa: BLE001
        return {"queued": False, "reason": "REQUEST_RAISED",
                "error": type(exc).__name__}


async def _fresh_valuation(conn, *, group_id: str, since: float, at: float,
                           limit: float) -> dict | None:
    """The newest stored valuation of the group's OWN contract (the
    review's measure identity: xavier_freshness.LATEST_VALUATION_SQL),
    decided after the request and inside the freshness limit now."""
    r = await conn.fetchrow(XF.LATEST_VALUATION_SQL, [group_id], since)
    if r is None or r["observed_at"] is None:
        return None
    age = at - _ep(r["observed_at"])
    if not 0 <= age <= limit:
        return None
    return {"id": r["id"], "observed_at": _ep(r["observed_at"]),
            "age_s": round(age, 3), "probability": r["probability"]}


async def drain(conn, ctx: dict) -> dict:
    """ONE BOUNDED PASS OVER THE OPEN REQUESTS. Never raises.

    expire -> close what landed (fresh valuation, current fixture row) ->
    dispatch what has not been (held re-evaluation; priority book reads
    inside the pass's read cap; re-review once evidence landed)."""
    out: dict[str, Any] = {"expired": 0, "completed": 0, "dispatched": 0,
                           "books": None, "closed_positions": 0}
    try:
        if not await has_schema(conn):
            return dict(out, refusal=R_NO_SCHEMA)
        t = _now(ctx)
        limit = _limit_s(ctx)
        acct = ctx.get("account_id")
        held_groups = None
        if acct:
            from .. import bettor_paper_ledger as L
            held_groups = {p["group_id"] for p in await L.positions(
                conn, acct)}
        # every account's overdue requests (a request never outlives its
        # TTL), then this account's open ones
        rows = await open_requests(conn, limit=MAX_EXPIRE_PER_PASS,
                                   expired_before=t)
        seen = {r["request_id"] for r in rows}
        rows += [r for r in await open_requests(
            conn, limit=MAX_EXPIRE_PER_PASS, account_id=acct)
            if r["request_id"] not in seen] if acct else []
        live = []
        for r in rows:
            # expiry is every account's: a request never outlives its TTL
            if t > float(r["expires_at"]):
                why = F_NO_EVIDENCE if r["kind"] == K_REASSESS else F_EXPIRED
                if await fail(conn, r["request_id"], at=t, failure=why,
                              detail={"dispatched": r.get("dispatched")}):
                    out["expired"] += 1
                continue
            racct = (r["detail"] or {}).get("account_id")
            if acct and racct and racct != acct:
                continue                  # that account's own pass serves it
            if held_groups is not None and racct == acct and \
                    r["group_id"] not in held_groups:
                if await fail(conn, r["request_id"], at=t, failure=F_CLOSED):
                    out["closed_positions"] += 1
                continue
            live.append(r)
        dispatched = 0
        held_answer: dict = {}
        for r in live[:MAX_CHECKS_PER_PASS]:
            k, slug = r["kind"], r.get("us_market_slug")
            if k == K_PROBABILITY:
                v = await _fresh_valuation(conn, group_id=r["group_id"],
                                           since=float(r["enqueued_at"]),
                                           at=t, limit=limit)
                if v is not None:
                    if await complete(conn, r["request_id"], at=t,
                                      evidence_table="external_valuations",
                                      evidence_id=v["id"], detail=v):
                        out["completed"] += 1
                    continue
                if r.get("dispatched") is None and slug and \
                        dispatched < MAX_DISPATCH_PER_PASS:
                    ans = held_answer.get(slug) or _request_held(slug)
                    held_answer[slug] = ans
                    if await dispatch(conn, r["request_id"], at=t, detail={
                            "via": "pinnapi_reactive.request_held_"
                                   "reevaluation", "answer": ans}):
                        dispatched += 1
            elif k == K_GAME_STATE:
                cid = str(r.get("fixture_identity") or "").split(":", 1)[-1]
                fx = await _fixture_current(conn, cid, at=t)
                if fx["current"] and fx["retrieved_at"] is not None:
                    if await complete(
                            conn, r["request_id"], at=t,
                            evidence_table="fixture_metadata",
                            evidence_id="%s@%s" % (cid, fx["retrieved_at"]),
                            detail=fx):
                        out["completed"] += 1
                    continue
                if r.get("dispatched") is None and slug and \
                        dispatched < MAX_DISPATCH_PER_PASS:
                    ans = held_answer.get(slug) or _request_held(slug)
                    held_answer[slug] = ans
                    if await dispatch(conn, r["request_id"], at=t, detail={
                            "via": "the held re-evaluation re-acquires "
                                   "fixture_metadata for the candidate "
                                   "(ext_pinnacle_loop.acquire_fixture_"
                                   "scope)", "answer": ans, "fixture": fx}):
                        dispatched += 1
        # ── priority book reads, inside the pass's own read cap ──────
        left = MAX_PRIORITY_BOOKS_PER_PASS - int(
            ctx.get("work_queue_books_read") or 0)
        if left > 0 and ctx.get("market_data") is not None and \
                "books_read" in ctx:
            slugs = await priority_book_slugs(conn, account_id=acct,
                                              limit=left)
            if slugs:
                from . import paper_runtime as PRT
                got = await PRT.read_books(
                    conn, ctx, slugs, basis="XAVIER_FRESH_EVIDENCE_REQUEST",
                    limit=left)
                ctx["work_queue_books_read"] = int(
                    ctx.get("work_queue_books_read") or 0) + got["read"]
                out["books"] = dict(
                    {k: got[k] for k in ("read", "errors",
                                         "skipped_budget")},
                    closed=await complete_book_reads(conn, got["obs"],
                                                     at=t))
        # ── re-review once evidence of the batch landed ─────────────
        n = 0
        for r in await open_requests(conn, kinds=[K_REASSESS],
                                     account_id=acct):
            if n >= MAX_REASSESS_PER_PASS:
                break
            if r.get("dispatched") is not None or \
                    (r["detail"] or {}).get("account_id") not in (None, acct):
                continue
            if not await _batch_has_evidence(conn, r["batch_id"]):
                continue
            sched = ctx.get("schedule_reassessment")
            if sched is None and ctx.get("schedule_review_at") is not None:
                # the live clock: the existing debounced held review
                from . import paper_runtime as PRT
                sched = PRT.schedule_held_review
            try:
                ans = (sched([r["us_market_slug"]]) if sched is not None
                       and r.get("us_market_slug") else
                       {"scheduled": False, "why": "NEXT_PASS_TRIGGER"})
            except Exception as exc:                            # noqa: BLE001
                ans = {"scheduled": False, "error": type(exc).__name__}
            if await dispatch(conn, r["request_id"], at=t, detail={
                    "via": "paper_runtime.schedule_held_review",
                    "answer": ans}):
                dispatched += 1
                n += 1
        out["dispatched"] = dispatched
        return out
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, error=type(exc).__name__,
                    detail=str(exc)[:200])
