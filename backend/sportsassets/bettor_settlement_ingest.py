"""The SETTLE pass the capture worker's docstring has always promised.

`workers/bettor_state.py` documents three passes -- SAMPLE, FOLLOW,
SETTLE -- and implements two. `bettor_state_capture.settlement_record()`
builds the row. `bettor_state_store.record_settlement()` writes it.
Neither has a caller anywhere in the repository. That is why
`bettor_state_settlements` holds zero rows, and it is the whole reason
the acceptance package was able to say "no outcome has matured" -- a
claim about the markets that was actually a fact about our wiring.

WHAT MEASUREMENT SAYS ABOUT THAT CLAIM. Of 1,538 captured observations,
552 are of events that had ALREADY STARTED (`time_to_event_s` negative,
`live_status` LIVE). An event starting is not an event resolving and the
capture records no end time, so how many concluded is still not
readable -- but "none has matured" was never supported.

FIVE COUNTS, REPORTED SEPARATELY. The directive is explicit and the
reason is the mistake above: one number cannot distinguish these.

  RESOLVED    the venue reports an outcome for this market
  PENDING     listed and open; no outcome yet
  UNREADABLE  the read failed, or the payload carries no field we
              recognise as an outcome
  UNMATCHED   the venue does not list this market at all
  INGESTED    a settlement row was actually WRITTEN for it

**INGESTED IS NOT RESOLVED.** A resolution we read but failed to write
is a resolution we do not have, and reporting the read count as the
stored count is how a pipeline appears to work while its table stays
empty. They are counted separately and the difference is reported.

OUTCOME TIMESTAMPS ARE PRESERVED AS THE VENUE WROTE THEM. `settled_at`
is passed through as a string, not reparsed into our own clock and not
defaulted to now. A settlement stamped with the time we happened to
read it is a settlement whose timing we have destroyed.

SEMANTICS STAY UNVERIFIED. "Did this slug settle at 1 for the side we
quoted" is a venue convention, not arithmetic.
`SETTLEMENT_SEMANTICS_STATUS` carries that separately and this module
never sets it to anything stronger than UNVERIFIED, because nothing has
verified it.

THIS MODULE WRITES ONE TABLE AND ONLY ONE. `bettor_state_settlements`,
through the existing `record_settlement`. It touches no accounting
record, no position, no order. Writing it is not deploying it.
"""

from __future__ import annotations

import logging

from . import bettor_live_read as live
from . import bettor_state_capture as sc

log = logging.getLogger(__name__)

INGEST_VERSION = "BETTOR_SETTLEMENT_INGEST_V1"

RESOLVED = live.RESOLVED
PENDING = live.PENDING
UNREADABLE = live.UNREADABLE
UNMATCHED = live.UNMATCHED
INGESTED = "INGESTED"
WRITE_FAILED = "WRITE_FAILED"


def classify(resolution: dict) -> str:
    """One venue read -> one of the four statuses. No inference."""
    status = resolution.get("status")
    return status if status in (RESOLVED, PENDING, UNREADABLE,
                                UNMATCHED) else UNREADABLE


def build_row(observation_id: str, resolution: dict) -> dict:
    """The settlement row for one observation, or None if not resolved.

    Only a RESOLVED read produces a row. PENDING, UNREADABLE and
    UNMATCHED each write NOTHING -- writing a PENDING row would fill
    the table with records that look like outcomes, and writing an
    UNREADABLE one would record our failure as the market's state.
    """
    if classify(resolution) != RESOLVED:
        return None
    return sc.settlement_record(
        observation_id,
        outcome=resolution.get("outcome"),
        # AS THE VENUE WROTE IT. Not reparsed, not defaulted to now.
        settled_at=resolution.get("settled_at"),
        semantics_status=sc.SETTLEMENT_SEMANTICS_UNVERIFIED)


async def ingest(pairs, *, reader=None, writer=None, client=None) -> dict:
    """Read each market's resolution and write the ones that resolved.

    `pairs` is an iterable of (observation_id, market_slug). `reader`
    and `writer` are injected so this is testable without a venue and
    without a database -- and so that a caller which holds neither
    cannot accidentally acquire them by importing this module.

    Returns the five counts, the per-market detail, and the reasons
    behind each UNREADABLE. Never raises: a read or a write that fails
    is counted and named, because an ingestion pass that dies halfway
    leaves a table that is neither empty nor complete.
    """
    from . import bettor_state_store as sstore

    read = reader or live.read_resolution
    write = writer or sstore.record_settlement

    counts = {RESOLVED: 0, PENDING: 0, UNREADABLE: 0, UNMATCHED: 0,
              INGESTED: 0, WRITE_FAILED: 0}
    detail: list = []
    reasons: dict = {}

    for observation_id, slug in pairs:
        try:
            res = read(client, slug)
        except Exception as exc:  # noqa: BLE001 -- named, never swallowed
            res = {"status": UNREADABLE, "error": type(exc).__name__,
                   "slug": slug}
        status = classify(res)
        counts[status] += 1
        rec = {"observation_id": observation_id, "slug": slug,
               "status": status, "outcome_field": res.get("outcome_field"),
               "settled_at": res.get("settled_at"),
               "closed": res.get("closed"), "ingested": False}
        if status == UNREADABLE:
            why = res.get("error") or "NO_RECOGNISED_OUTCOME_FIELD"
            reasons[why] = reasons.get(why, 0) + 1
            rec["why"] = why
            # The keys the payload DID carry, names only. This is what
            # corrects OUTCOME_FIELDS with one read instead of a guess.
            rec["keys_seen"] = res.get("keys_seen") or []

        row = build_row(observation_id, res)
        if row is not None:
            try:
                await write(row)
                counts[INGESTED] += 1
                rec["ingested"] = True
            except Exception as exc:  # noqa: BLE001
                counts[WRITE_FAILED] += 1
                rec["write_error"] = type(exc).__name__
        detail.append(rec)

    return {
        "ingest": INGEST_VERSION,
        "counts": counts,
        "unreadable_reasons": reasons,
        "detail": detail,
        # THE ONE COMPARISON THAT MATTERS. If these differ, resolutions
        # were read and not stored, and the table understates what we
        # know. Reporting only one of them is the defect this whole
        # module exists because of.
        "resolved_minus_ingested": counts[RESOLVED] - counts[INGESTED],
        "reconciled": counts[RESOLVED] == counts[INGESTED],
    }


def describe() -> dict:
    return {
        "ingest": INGEST_VERSION,
        "writes_tables": ["bettor_state_settlements"],
        "writes_accounting": False,
        "submits_orders": False,
        "deployed": False,
        "statuses": [RESOLVED, PENDING, UNREADABLE, UNMATCHED, INGESTED,
                     WRITE_FAILED],
        "ingested_is_not_resolved": (
            "a resolution read and not written is a resolution we do "
            "not have; the two counts are reported separately and their "
            "difference is the reconciliation"),
        "only_resolved_writes": (
            "PENDING, UNREADABLE and UNMATCHED write nothing. A PENDING "
            "row would look like an outcome; an UNREADABLE one would "
            "record our failure as the market's state"),
        "timestamps": "passed through as the venue wrote them",
        "semantics": "never stronger than UNVERIFIED",
        "why_the_table_was_empty": (
            "record_settlement() was defined and never called. Zero rows "
            "was a fact about our wiring, reported as a fact about the "
            "markets"),
    }
