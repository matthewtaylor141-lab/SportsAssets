"""BETTOR's OPERATIONAL TELEMETRY. Deliberately not in shadow_bettor.

WHY THIS FILE EXISTS AS A SEPARATE MODULE. The pipeline's own health
has to read the decision ledger -- how many decisions landed, when the
last one did, which opportunities never got one. `shadow_bettor.py` may
not read `shadow_decisions` at all: its independence test refuses any
`FROM shadow_decisions` in that file outright, and the right answer to
that test failing was to move this code rather than to loosen the rule.
"Do not weaken it to improve apparent BETTOR performance" applies to a
test just as much as to a threshold.

The distinction is real and not cosmetic. shadow_bettor.py is the
DECISION path, and every input to it must be independently sourced.
Nothing here is ever an input to a decision: these functions count rows
and name failures, they are called by the worker's bookkeeping and by
COMMAND, and `decide()` cannot reach them. Keeping them apart makes
that structural instead of a comment somebody edits on a quiet
afternoon.

Owner directive 2026-09-19: "Every legitimate opportunity should either
have A DECISION or A NAMED FAILURE / BLOCKER. No silent orphan
opportunities." / "This incident should never again require noticing
that one counter is 31 while another is zero."
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import shadow_bettor as bettor
from . import shadow_lanes as lanes
from . import shadow_store as store

log = logging.getLogger(__name__)

# Owner directive 2026-09-19: "Every legitimate opportunity should
# either have A DECISION or A NAMED FAILURE / BLOCKER. No silent orphan
# opportunities." / "This incident should never again require noticing
# that one counter is 31 while another is zero."

# THE ALLOWANCE IS PART OF THE DEFINITION, and it matches the view in
# migration 073 so the worker, the API and any research query cannot
# drift into three different ideas of what an orphan is. The worker
# ticks every 60s and decides inside the same tick, so this is three
# chances before anything is called orphaned.
ORPHAN_ALLOWANCE_S = 180

FAILURE_STAGES = ("OPPORTUNITY_WRITE", "DECISION_BUILD", "DECISION_WRITE",
                  "MARKET_STATE_WRITE", "UNIVERSE_READ", "NOT_IDENTIFIED")

ANNOTATION_WRITER_INCIDENT = "DECISION_NOT_RECORDED_DUE_TO_WRITER_INCIDENT"
ANNOTATION_UNEXPLAINED = "DECISION_NOT_RECORDED_REASON_NOT_IDENTIFIED"

# The 2026-09-19 incident, named so its rows can always be excluded from
# research explicitly rather than by guesswork about dates.
WRITER_INCIDENT = "BETTOR_WRITER_2026_09_19"


async def record_failure(*, stage, error, pool=None,
                         bettor_opportunity_id=None, symbol=None) -> str:
    """Write down that a decision did not land, in the error's own words.

    THE VERBATIM TEXT IS THE POINT. The writer incident lasted an hour
    because the worker caught its exception and reported a heartbeat
    with an empty problems list: the failure existed and said nothing.
    A paraphrase here would reproduce that.
    """
    if stage not in FAILURE_STAGES:
        raise store.StoreRefusal("refused: %r is not a failure stage" % stage)
    pool = pool or await store.get_pool()
    text = "%s: %s" % (type(error).__name__, error)
    fid = store._id("bfail", stage, bettor_opportunity_id or "-",
                    symbol or "-", text[:400],
                    datetime.now(tz=timezone.utc).isoformat())
    await pool.execute(
        """
        INSERT INTO bettor_decision_failures (
            failure_id, bettor_opportunity_id, symbol, stage,
            error_class, error_text)
        VALUES ($1,$2,$3,$4,$5,$6)
        ON CONFLICT DO NOTHING
        """,
        fid, bettor_opportunity_id, symbol, stage,
        type(error).__name__, text[:4000])
    return fid


async def annotate_orphans(pool, *, limit: int = 200) -> dict:
    """Give every orphaned opportunity a NAMED reason, append-only.

    AN ANNOTATION IS NOT A DECISION. It has no action, no price, no size
    and no lane -- it is a note that a decision is missing, written now
    and stamped now. "They may NOT be presented as prospective shadow
    decisions", and the schema makes that structural: annotations live
    in their own table and nothing joins them into the decision ledger.

    THE BOUNDARY IS DERIVED, NOT GUESSED. Any orphan observed before the
    first BETTOR decision this store ever accepted belongs to the writer
    incident; any orphan after it is a NEW gap and is labelled
    REASON_NOT_IDENTIFIED rather than quietly filed under an old
    incident that has been fixed.
    """
    first = await pool.fetchval(
        "SELECT min(created_at) FROM shadow_decisions "
        "WHERE lane = $1", lanes.BETTOR_EV_SHADOW)
    rows = await pool.fetch(
        """
        SELECT o.bettor_opportunity_id, o.symbol, o.observed_at
          FROM bettor_orphan_opportunities o
         WHERE o.annotated IS FALSE
         ORDER BY o.observed_at ASC
         LIMIT $1
        """, limit)
    written = {"incident": 0, "unexplained": 0}
    for r in rows:
        incident_era = first is not None and r["observed_at"] < first
        kind = (ANNOTATION_WRITER_INCIDENT if incident_era
                else ANNOTATION_UNEXPLAINED)
        incident = WRITER_INCIDENT if incident_era else store.NOT_IDENTIFIED
        aid = store._id("bann", r["bettor_opportunity_id"], kind, incident)
        await pool.execute(
            """
            INSERT INTO bettor_opportunity_annotations (
                annotation_id, bettor_opportunity_id, annotation_kind,
                incident, detail, observed_at)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT DO NOTHING
            """,
            aid, r["bettor_opportunity_id"], kind, incident,
            ("the opportunity is a legitimate prospective observation; "
             "the decision that should have accompanied it was never "
             "written, and is not reconstructed after the fact"),
            r["observed_at"])
        written["incident" if incident_era else "unexplained"] += 1
    return written


# The columns of pipeline_health's query that come back as datetimes.
# Named individually so a new timestamp column is a visible edit here
# rather than a silent one covered by a catch-all serializer.
TIMESTAMP_FIELDS = ("last_opportunity", "last_decision", "last_failure")


async def pipeline_health(pool) -> dict:
    """What COMMAND shows, read from rows rather than inferred.

    "Do not show LIVE / HEALTHY merely because opportunity collection
    works." So DEGRADED is the answer whenever an orphan exists or a
    failure has been recorded recently, and the numbers that produced
    that verdict travel with it.
    """
    row = await pool.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM bettor_opportunities)          AS opportunities,
          (SELECT max(observed_at) FROM bettor_opportunities)  AS last_opportunity,
          (SELECT count(*) FROM shadow_decisions
            WHERE lane = $1)                                    AS decisions,
          (SELECT count(*) FROM shadow_decisions
            WHERE lane = $1 AND proposed_action = 'NO_TRADE')   AS no_trade,
          (SELECT max(created_at) FROM shadow_decisions
            WHERE lane = $1)                                    AS last_decision,
          (SELECT count(*) FROM bettor_orphan_opportunities)    AS orphans,
          (SELECT count(*) FROM bettor_decision_failures)       AS failures,
          (SELECT max(failed_at) FROM bettor_decision_failures) AS last_failure
        """, lanes.BETTOR_EV_SHADOW)
    h = dict(row)
    # ── THE DEFECT THAT COST 62 HEARTBEATS ───────────────────────────
    #
    # asyncpg returns timestamptz as datetime objects, and db.heartbeat
    # serializes its detail with json.dumps and NO default= handler. So
    # from 19:13:51Z on 2026-09-19 every beat raised
    #
    #   TypeError: Object of type datetime is not JSON serializable
    #
    # which the worker swallowed with log.debug. COMMAND read a stale
    # tick_failed row for 62 minutes while the decision loop was
    # demonstrably writing 18 of 18.
    #
    # THE CONVERSION IS EXPLICIT AND BY NAME, not json.dumps(...,
    # default=str). Owner directive 2026-09-19 20:2xZ: "Explicitly
    # convert the known datetime fields ... Unexpected unsupported types
    # should remain detectable." A blanket default= would have fixed
    # this symptom and hidden the next one -- a Decimal, a UUID, an
    # asyncpg Range -- behind a string that looks deliberate.
    for field in TIMESTAMP_FIELDS:
        value = h.get(field)
        if isinstance(value, datetime):
            h[field] = value.astimezone(timezone.utc).isoformat()
    # A RATE IS ONLY MEANINGFUL AGAINST A SETTLED DENOMINATOR. In-flight
    # opportunities are excluded, because counting them as misses would
    # make a healthy pipeline look like a failing one on every tick.
    settled = (h["opportunities"] or 0)
    h["successRate"] = (None if not settled
                        else round((h["decisions"] or 0) / settled, 4))
    h["state"] = ("DEGRADED" if (h["orphans"] or h["failures"])
                  else "LIVE" if h["decisions"]
                  else "LISTENING")
    return h


async def record_boot(pool, boot: dict) -> None:
    """Persist the boot verdict on BETTOR's OWN ingestion_state key.

    WHY A SEPARATE KEY FROM workers_boot. That one is written by
    workers/all.py and carries only {commit, at}. The first V2 deploy
    showed COMMAND's BETTOR_POLICY_INTEGRITY tile reading ABSENT for
    exactly that reason: the verdict existed in this process's log and
    inside the heartbeat detail, and nowhere the health surface looked.

    WHY NOT READ IT FROM THE HEARTBEAT. The heartbeat carries it too,
    but sourcing integrity from the telemetry plane would mean a broken
    health writer takes the integrity tile down with it -- the exact
    coupling the five-plane split exists to remove. Five components,
    five sources.

    NEVER RAISES. A marker that could stop collection would be worse
    than a marker nobody can read, but the failure is logged at error
    rather than swallowed -- the lesson of the 62 silent minutes.
    """
    try:
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) "
            "VALUES ('bettor_boot', $1::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $1::jsonb",
            json.dumps(boot))
    except Exception:                                          # noqa: BLE001
        log.error("shadow_bettor: bettor_boot marker write failed",
                  exc_info=True)
