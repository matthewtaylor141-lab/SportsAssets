"""Worker: BETTOR's DECISION-ONLY prospective loop. NOT DEPLOYED.

WHAT THIS IS AND IS NOT. It reads live books through the service's own
authenticated read path, decides on each one, and persists the decision
before any outcome is known. It places no order, holds no capital and
writes exactly one table of its own.

**IT IS NOT IN `workers/all.py` AND MUST NOT BE ADDED THERE WITHOUT A
SEPARATE AUTHORIZATION.** Writing a worker is not deploying one. The
owner directive draws that line explicitly and this file sits on the
implementation side of it. The deployment request, its exact steps and
its rollback are PILOT_PROPOSAL.md section 7.

WHY DECISION-ONLY IS THE POINT. A prospective record is the only
evidence class that can support a forward-looking claim, and only if
nothing about it is retrospective. So:

  * the decision is written BEFORE any outcome exists;
  * it is keyed by `observation_id` and never overwritten -- re-deciding
    an observation after its market moved would silently turn a
    prospective record into a hindsight one;
  * freshness is computed from THREE CLOCKS at the moment of decision,
    never from a stored age.

IT WILL RECORD NO_TRADE ALMOST ALWAYS, and that is the expected result,
not a failure. On the 16 real books replayed it was NO_TRADE 16 times.
Running this establishes the RECORD-KEEPING, not an edge.

MONITORING. Every tick emits counters -- read, rejected by reason,
decided by action, blockers by name, and the freshness distribution. A
loop that reports only its trades cannot be evaluated, and this one has
no trades to report.

MEASURED CONSTRAINT, so the deployment is not costed on a guess: the
median source-to-receipt delay on the existing capture feed is 549.6 s
against a 10 s decision bound, and only 18 of 449 economically eligible
books arrive inside it. This worker reads LIVE rather than from the
capture, so it is not bound by that median -- but nothing has measured
the live path's delay either, and the first thing this loop produces is
that measurement.

Kill: BETTOR_PROSPECTIVE=off.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from .. import bettor_decision_engine as de
from .. import bettor_live_read as live
from .. import bettor_observation_adapter as oa

log = logging.getLogger(__name__)

WORKER_VERSION = "BETTOR_PROSPECTIVE_WORKER_V1"
PROSPECTIVE = "PROSPECTIVE_SHADOW"

TICK_S = 30.0
KILL_ENV = "BETTOR_PROSPECTIVE"

# A live decision may not be taken on a book older than this, measured
# from the VENUE's own source clock at the moment of decision.
FRESHNESS_BOUND_S = 10.0
# Tolerance for stamp resolution only. Transport cannot produce a
# negative delay at all, so this is not latency tolerance.
CLOCK_TOLERANCE_S = 0.5

# The published schedule for the date being decided. PUBLISHED, not
# VERIFIED_APPLIED, so the engine computes with it and still refuses to
# select on it.
FEE_DATE_ENV = "BETTOR_FEE_DATE"


def enabled() -> bool:
    return str(os.environ.get(KILL_ENV, "on")).strip().lower() != "off"


def fees(today: str | None = None) -> de.Fees:
    day = today or os.environ.get(FEE_DATE_ENV) or _now().strftime("%Y-%m-%d")
    return de.Fees.published_pmus(day)


def _now():
    return datetime.now(timezone.utc)


def _parse(x):
    """An AWARE datetime or None. A naive stamp is REFUSED, not assumed.

    Appending '+00:00' to an unlabelled stamp is not parsing, it is
    asserting the venue publishes in UTC -- and if it does not, a book
    hours old reads as fresh with nothing in the record to show it.
    """
    if x is None:
        return None
    t = str(x).strip()
    if not t or t == "NOT_IDENTIFIED":
        return None
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    if "." in t:
        head, rest = t.split(".", 1)
        digits = ""
        for ch in rest:
            if ch.isdigit():
                digits += ch
            else:
                break
        t = "%s.%s%s" % (head, digits[:6].ljust(6, "0"), rest[len(digits):])
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    return dt if dt.tzinfo and dt.tzinfo.utcoffset(dt) is not None else None


def freshness(row: dict, decided_at) -> dict:
    """Three clocks, all three required, at the MOMENT OF DECISION.

    `decided_at` is passed in and is taken per observation, not once
    per tick: a tick-start timestamp understates the age of the last
    book by however long the tick took, which is the interval the bound
    exists to catch.

    A source-to-receipt delay is NOT clock skew. A positive delay is
    the venue's stamp plus transport plus our processing. Only a
    NEGATIVE one shows the clocks disagree, because transport only runs
    forward. And we cannot decide on a book we have not received, which
    is causality in our own pipeline rather than anything about the
    venue.
    """
    s = _parse(row.get("book_source_ts"))
    r = _parse(row.get("book_received_ts"))
    d = decided_at if hasattr(decided_at, "tzinfo") else _parse(decided_at)
    if s is None:
        return {"ok": False, "reason": "NO_VENUE_SOURCE_TIMESTAMP"}
    if r is None:
        return {"ok": False, "reason": "NO_RECEIPT_TIMESTAMP"}
    if d is None:
        return {"ok": False, "reason": "NO_DECISION_TIMESTAMP"}
    age = (d - s).total_seconds()
    transport = (r - s).total_seconds()
    out = {"age_at_decision_s": round(age, 4),
           "source_to_receipt_s": round(transport, 4),
           "receipt_to_decision_s": round((d - r).total_seconds(), 4)}
    if (d - r).total_seconds() < -CLOCK_TOLERANCE_S:
        return dict(out, ok=False, reason="DECIDED_BEFORE_RECEIPT")
    if transport < -CLOCK_TOLERANCE_S:
        return dict(out, ok=False, reason="SOURCE_TIMESTAMP_AFTER_RECEIPT")
    if age < 0:
        return dict(out, ok=False, reason="SOURCE_TIMESTAMP_IN_FUTURE")
    if age > FRESHNESS_BOUND_S:
        return dict(out, ok=False, reason="STALE_AT_DECISION")
    return dict(out, ok=True, reason=None)


def decide_one(row: dict, *, fee: de.Fees, decided_at,
               max_contracts: float = 0.0) -> dict:
    """One live row -> one PROSPECTIVE_SHADOW record. Never raises.

    `max_contracts` defaults to ZERO. A worker that sized orders by
    default would be one configuration mistake away from sizing real
    ones, and nothing downstream of this needs a size to record a
    decision.
    """
    fresh = freshness(row, decided_at)
    base = {"worker": WORKER_VERSION, "evidence_class": PROSPECTIVE,
            "observation_id": row.get("observation_id") or row.get("market_id"),
            "market_id": row.get("market_id"),
            "decided_at": (decided_at.isoformat()
                           if hasattr(decided_at, "isoformat")
                           else str(decided_at)),
            "freshness": fresh}
    if not fresh["ok"]:
        return dict(base, status="REJECTED", reasons=[fresh["reason"]])

    # AGE IS WRITTEN INTO THE ROW FROM THE THREE CLOCKS, never read out
    # of it. The capture's `book_age_s` is the age AT CAPTURE.
    row = dict(row, book_age_s="%.6f" % fresh["age_at_decision_s"])
    rec = oa.normalize(row, venue="polymarket-us",
                       account_class="institutional", fee_source=fee.source)
    if rec.status != oa.ACCEPTED:
        return dict(base, status="REJECTED", reasons=list(rec.reasons))

    d = de.decide(oa.to_book(rec), fees=fee, max_contracts=max_contracts,
                  venue=rec.venue, account_class=rec.account_class)
    return dict(base, status="DECIDED", selected=d["selected"],
                size_contracts=d["size_contracts"], reason=d["reason"],
                blockers=sorted({c["blocker"] for c in d["candidates"]
                                 if c["blocker"]}),
                fee_schedule=d["fee_schedule"], decision=d)


def tick(targets, *, reader=None, client=None, fee=None,
         max_contracts: float = 0.0) -> dict:
    """One pass over `targets`. Returns records and counters.

    Each target is `(market_slug, outcome_leg)`, or a bare slug when
    the leg is not known. **A bare slug is REJECTED downstream**, with
    `NO_OUTCOME_IDENTITY`, and that is correct: one row is one OUTCOME
    LEG of one contract, and a read that cannot say which leg it is
    cannot be normalized. The reader has no way to invent it -- in this
    capture `instrument_id` equals `market_id` and carries no
    leg-level information at all -- so the caller supplies it or the
    row is refused.

    `reader` is injected, so this is testable without a venue and so a
    caller holding no client cannot acquire one by importing this.
    """
    read = reader or live.read_book
    f = fee or fees()
    records, counters = [], {}

    def bump(k, n=1):
        counters[k] = counters.get(k, 0) + n

    for target in targets:
        slug, leg = (target if isinstance(target, (tuple, list))
                     else (target, None))
        bump("read")
        try:
            row = read(client, slug, outcome_leg=leg)
        except Exception as exc:  # noqa: BLE001 -- named, never swallowed
            bump("read_failed")
            bump("read_error:%s" % type(exc).__name__)
            continue
        # PER OBSERVATION, not per tick.
        rec = decide_one(row, fee=f, decided_at=_now(),
                         max_contracts=max_contracts)
        records.append(rec)
        if rec["status"] == "REJECTED":
            bump("rejected")
            for r in rec["reasons"]:
                bump("reject:%s" % r)
        else:
            bump("decided")
            bump("action:%s" % rec["selected"])
            for b in rec["blockers"]:
                bump("blocker:%s" % b)
    return {"worker": WORKER_VERSION, "records": records,
            "counters": counters,
            "fee_schedule": f.source, "fee_status": f.status}


def describe() -> dict:
    return {
        "worker": WORKER_VERSION,
        "evidence_class": PROSPECTIVE,
        "submits_orders": False,
        "writes_accounting": False,
        "capital_at_risk": 0,
        "default_max_contracts": 0.0,
        "deployed": False,
        "in_workers_all": False,
        "deployment_requires": ("a separate authorization; see "
                                "PILOT_PROPOSAL.md section 7. Writing a "
                                "worker is not deploying one"),
        "rollback": ("remove it from the service. It writes only its own "
                     "table and touches no trading path"),
        "expected_result": ("NO_TRADE on almost every row. On the 16 real "
                            "books replayed it was NO_TRADE 16 times. This "
                            "establishes the record-keeping, not an edge"),
    }
