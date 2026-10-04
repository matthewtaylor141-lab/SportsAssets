"""P5 C12 DECISION-TIME PROOF: THE DECISION, ITS INTENT AND THE ONE STREAM BOOK.

C12 (P5_LIVE_STREAM_BOOK_V1) holds only when the decision's executable price
came from the SAME resident stream observation the rule certified. The
decision path already does that (Candidate 22: `decision_hooks.
LIVE_BOOK_STREAM` = `live_book_evidence.observe` -> `paper_benchmark.
actual_pricing` -> the intent's admission facts); this module writes the
durable record that ties them together, one per execution intent whose
decision the resident stream book was evaluated for:

  decision_id, execution_intent_id, stream symbol, connection epoch / id,
  observation id, book receipt instant and venue transact_time, book age,
  the decision's executable (wire) price, the P5 verdict and failed
  components, and whether the intent came out live-eligible.

  PRICED_FROM_STREAM         the actual lane's price came from the stream
                             observation of the exactly mapped symbol, the
                             verdict is ESTABLISHED and C12 passed;
  REFUSED_BEFORE_SUBMISSION  anything else for which a stream book was read
                             (stale, crossed, gapped, another symbol, a
                             stream price that cleared no edge): the refusal
                             is named, and the intent is never live-eligible
                             (CHECKed, migration 213).

`build` is pure and decides NOTHING: it runs after the intent exists, reads
the payload the decision path already wrote and the intent row, and returns a
record or None (no stream book was evaluated: the REST path, no mapper).
`record` writes it in a savepoint and never raises, so a missing table or a
failed write can never touch the intent, the paper sibling or the actual
lane. Imports nothing that can reach a venue.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import live_book_currency as LBC

VERSION = "P5_C12_DECISION_PROOF_V1"
TABLE = "p5_c12_decision_proof"
PRICED = "PRICED_FROM_STREAM"
REFUSED = "REFUSED_BEFORE_SUBMISSION"
STREAM_SOURCE = "INSTITUTIONAL_STREAM"
C12 = "C12_PRICED_FROM_THIS_BOOK"


def _ts(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else None
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else None
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _dec(v):
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def _ev(payload) -> dict:
    ev = (payload or {}).get("evidence")
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = None
    return ev if isinstance(ev, dict) else {}


def build(intent: dict, payload: dict) -> dict | None:
    """Pure: the C12 proof record for one intent, or None when no resident
    stream book was evaluated for its decision."""
    ev = _ev(payload)
    facts = ev.get("admission_facts") or {}
    book = facts.get("book") or {}
    bc = book.get("book_currency") if isinstance(book.get("book_currency"),
                                                 dict) else {}
    so = ev.get("stream_observation") if isinstance(
        ev.get("stream_observation"), dict) else None
    src = ev.get("actual_price_source")
    # a resident stream book was evaluated for this decision: its record
    # replaced the REST label (`stream_read`), or the actual lane was priced
    # from a stream observation. Otherwise (REST path, no mapper): no proof.
    if not (so is not None or (bc.get("rule") == LBC.RULE_ID
                               and bc.get("stream_read") is True)):
        return None
    it = dict(intent or {})
    slug = it.get("us_market_slug") or (payload or {}).get("slug")
    failed = list(bc.get("failed_components") or [])
    verdict = bc.get("verdict")
    symbol = (so or {}).get("symbol") or bc.get("symbol")
    c12_ok = bool(verdict) and C12 not in failed
    priced = (src == STREAM_SOURCE and so is not None
              and verdict == LBC.ESTABLISHED and c12_ok
              and symbol == slug)
    if priced:
        refusal = None
    elif src == STREAM_SOURCE + "_NO_ORDER":
        refusal = "STREAM_PRICING_REFUSED:%s" % ",".join(
            ev.get("actual_pricing_refusals") or ["UNSTATED"])
    elif symbol and symbol != slug:
        refusal = "STREAM_BOOK_SYMBOL_IS_NOT_THE_DECISION_CONTRACT"
    elif failed:
        refusal = "P5_%s:%s" % (verdict, failed[0])
    else:
        refusal = "P5_%s:%s" % (verdict, bc.get("reason") or "UNSTATED")
    age = (so or {}).get("age_s")
    if age is None:
        age = book.get("age_at_decision_s") if so is not None else \
            bc.get("receipt_age_s")
    wire = it.get("wire_price") if priced else None
    return {
        "version": VERSION,
        "decision_id": it.get("decision_id") or (payload or {}).get(
            "decision_id"),
        "execution_intent_id": it.get("intent_id"),
        "strategy": it.get("strategy"), "policy_version":
            it.get("policy_version"),
        "us_market_slug": slug, "order_intent": it.get("order_intent"),
        "proof_status": PRICED if priced else REFUSED, "refusal": refusal,
        "price_source": src or "REST_PAPER_BOOK",
        "stream_symbol": symbol,
        "connection_epoch": (so or {}).get("connection_epoch",
                                           bc.get("connection_epoch")),
        "connection_id": (so or {}).get("connection_id"),
        "obs_id": (so or {}).get("obs_id"),
        "book_received_at": _ts((so or {}).get("observed_at")),
        "book_venue_ts": _ts((so or {}).get("venue_ts")),
        "book_age_s": None if age is None else float(age),
        "decision_executable_price": _dec(wire),
        "limit_price": _dec(it.get("limit_price")) if priced else None,
        "p5_verdict": verdict, "failed_components": failed,
        "c12_passed": c12_ok,
        "live_eligible": bool(it.get("live_eligible")),
        "actual_state": it.get("actual_state"),
        "actual_refusal": it.get("actual_refusal"),
        "evidence": {"book_currency_reason": bc.get("reason"),
                     "stream_book_verdict": bc.get("stream_book_verdict"),
                     "subscription_state": bc.get("subscription_state"),
                     "receipt_age_s": bc.get("receipt_age_s"),
                     "venue_receipt_skew_s": bc.get("venue_receipt_skew_s"),
                     "gap_since_snapshot": bc.get("gap_since_snapshot"),
                     "market_state": bc.get("market_state"),
                     "actual_pricing_refusals":
                         ev.get("actual_pricing_refusals")},
    }


COLUMNS = ("version", "decision_id", "execution_intent_id", "strategy",
           "policy_version", "us_market_slug", "order_intent", "proof_status",
           "refusal", "price_source", "stream_symbol", "connection_epoch",
           "connection_id", "obs_id", "book_received_at", "book_venue_ts",
           "book_age_s", "decision_executable_price", "limit_price",
           "p5_verdict", "failed_components", "c12_passed", "live_eligible",
           "actual_state", "actual_refusal", "evidence")
_JSON = ("failed_components", "evidence")
INSERT_SQL = "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT (decision_id) " \
             "DO NOTHING" % (TABLE, ", ".join(COLUMNS), ", ".join(
                 "$%d%s" % (i + 1, "::jsonb" if c in _JSON else "")
                 for i, c in enumerate(COLUMNS)))


async def record(conn, intent: dict, payload: dict) -> dict:
    """Build and write the proof in a SAVEPOINT. Never raises: the intent
    and both lanes are unaffected by anything that happens here."""
    try:
        rec = build(intent, payload)
    except Exception as exc:                                  # noqa: BLE001
        return {"written": False, "why": "BUILD_FAILED:%s"
                % type(exc).__name__}
    if rec is None:
        return {"written": False, "why": "NO_STREAM_BOOK_EVALUATED"}
    try:
        async with conn.transaction():
            await conn.execute(INSERT_SQL, *[
                json.dumps(rec.get(c), default=str) if c in _JSON
                else rec.get(c) for c in COLUMNS])
    except Exception as exc:                                  # noqa: BLE001
        return {"written": False, "why": "WRITE_FAILED:%s"
                % type(exc).__name__, "proof_status": rec["proof_status"]}
    return {"written": True, "proof_status": rec["proof_status"],
            "refusal": rec["refusal"]}
