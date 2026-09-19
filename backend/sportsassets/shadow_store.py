"""THE APPEND-ONLY SHADOW WRITER. The minimum store required to start.

Owner directive 2026-09-19 (the approved reorder): build only the
minimum store, verify the invariants BEFORE the first row, then start
accumulating. Do not spend the turn building reporting queries that
nothing can yet report on.

WHAT THIS MODULE IS ALLOWED TO DO: insert. That is the whole surface.
There is no statement in this file that modifies or removes a row --
not one, not behind a flag, not for a correction, not for an incident.
`test_shadow_store.py` reads this file's own source text and fails if
one appears, because the database trigger and the missing code path are
two independent guarantees and the second one is the one that survives
somebody connecting with psql at 3am and being helpful.

A correction is an INSERT. A reorg is an INSERT. A score that arrives
four hours later is an INSERT into its own table. What BETTOR claimed
at T0 is never revised; it is superseded, in public, with the original
still sitting beside it.

THE PREFLIGHT IS NOT CEREMONY. store_ready() checks, against the live
catalog, that every invariant the directive names is actually enforced
by the database and not merely intended: the append-only triggers on
each ledger table, the lane column NOT NULL with NO DEFAULT, the
foreign key that makes an unfrozen policy unwritable, the partial
unique index that makes the RN1 dedupe real, and the shadow_mode /
capital_at_risk constraint. A "yes" from this function is a statement
about the schema in front of us. Nothing calls it and assumes.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from . import shadow as sh
from . import shadow_lanes as lanes
from . import shadow_bettor_sizing as szpol
from . import shadow_policy as pol
from .db import get_pool

log = logging.getLogger(__name__)

INGEST_VERSION = "rn1_shadow_ingest_v1"

NOT_IDENTIFIED = "NOT_IDENTIFIED"

OBSERVATION = "OBSERVATION"
OBSERVATION_INVALIDATED = "OBSERVATION_INVALIDATED"
OBSERVATION_CORRECTED = "OBSERVATION_CORRECTED"

# A FILL THAT ARRIVES AFTER AN INGESTION INCIDENT IS NOT A SIGHTING.
# Owner directive 2026-09-19 section 7: recovered fills "MAY NOT BE
# INSERTED AS THOUGH THEY WERE PROSPECTIVE RN1_SHADOW OBSERVATIONS ...
# They cannot generate a prospective shadow decision."
#
# The row is still kept -- it is real evidence about the venue. What it
# is not is evidence about what we knew at T0, and migration 072 makes
# the database enforce that rather than this module: shadow_decisions
# references (observation_id, prospective_kind), and prospective_kind is
# NULL on anything that is not a plain OBSERVATION.
RECOVERED_AFTER_INGESTION_INCIDENT = "RECOVERED_AFTER_INGESTION_INCIDENT"

# The kinds that may back a prospective decision. There is exactly one,
# and it is named here so a future caller has to edit this line rather
# than discover the rule by reading a CHECK constraint.
PROSPECTIVE_KINDS = (OBSERVATION,)

VENUE_NATIVE_FILL_ID = "VENUE_NATIVE_FILL_ID"
DERIVED_FILL_TUPLE = "DERIVED_FILL_TUPLE"

SOURCE_TYPES = ("CHAIN", "POLL", "S1", "RECONCILER", "BACKFILL")

SOURCE_SUPPLIED = "SOURCE_SUPPLIED"
TS_MISSING = "MISSING"
FALLBACK_SUBSTITUTED = "FALLBACK_SUBSTITUTED"


class StoreRefusal(sh.ShadowRefusal):
    """The store declined to write. The message says what was wrong with
    the row, never with the caller."""


# ── the idempotency key ──────────────────────────────────────────────


def _num(value) -> str:
    """Fixed six-decimal text, so 0.29, '0.290' and 0.2900000001 do not
    become three different fills."""
    return "%.6f" % float(value)


def observation_key(*, venue: str, source_fill_id=None, tx_hash=None,
                    asset=None, side=None, quantity=None, price=None,
                    source_ts=None) -> dict:
    """The stable idempotency key, and the basis it was built on.

    DEDUPLICATION IS SOLVED BEFORE COLLECTION, not afterwards, because a
    ledger that was collected wrong cannot be repaired by a later query:
    two rows that are really one fill and one row that is really two
    fills look identical in the store.

    TWO BASES, NEVER MIXED SILENTLY.

      VENUE_NATIVE_FILL_ID  the venue gave the fill its own identity.
                            This is the right key and it is used
                            whenever it exists.

      DERIVED_FILL_TUPLE    it did not, so the key is built from the
                            transaction, the token, the side, the SIZE,
                            the PRICE and the SOURCE TIMESTAMP.

    THE TUPLE IS NOT (MARKET, SIDE, PRICE). "Do not deduplicate
    economically distinct RN1 fills merely because they have the same
    market, side and price" -- so size and the source instant are in the
    key, and so is the transaction that carried it. Two genuinely
    distinct fills of the same size at the same price in the same
    transaction at the same instant would still collapse; that residue
    is real, it is the limit of what venue-native identifiers support,
    and it is why the basis is stored on the row instead of assumed.
    """
    if source_fill_id:
        canonical = "|".join([venue, "fill", str(source_fill_id).strip()])
        basis = VENUE_NATIVE_FILL_ID
    else:
        missing = [n for n, v in (("tx_hash", tx_hash), ("asset", asset),
                                  ("side", side), ("quantity", quantity),
                                  ("price", price))
                   if v in (None, "")]
        if missing:
            raise StoreRefusal(
                "refused: no venue-native fill id, and the derived key is "
                "missing %s -- a key that guesses is worse than no key"
                % ", ".join(missing))
        canonical = "|".join([
            venue, "tuple",
            str(tx_hash).strip().lower(),
            str(asset).strip(),
            str(side).strip().upper(),
            _num(quantity),
            _num(price),
            _ts_text(source_ts),
        ])
        basis = DERIVED_FILL_TUPLE
    return {"key": hashlib.sha256(canonical.encode()).hexdigest(),
            "basis": basis,
            "canonical": canonical}


def _ts_text(value) -> str:
    if value is None:
        return "no_source_ts"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _observation_id(key: str, record_kind: str, *, salt: str = "") -> str:
    """Deterministic for a sighting, so a retry produces the same primary
    key and the insert collapses instead of doubling."""
    raw = "|".join([key, record_kind, salt])
    return "rn1obs_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _id(prefix: str, *parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return prefix + "_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


# ── the preflight ────────────────────────────────────────────────────

REQUIRED_TRIGGERS = {
    "shadow_decisions": "shadow_decisions_immutable",
    "shadow_executions": "shadow_executions_immutable",
    "shadow_positions": "shadow_positions_immutable",
    "shadow_position_events": "shadow_position_events_immutable",
    "rn1_observations": "rn1_observations_immutable",
    "shadow_market_states": "shadow_market_states_immutable",
    "shadow_policy_versions": "shadow_policy_versions_immutable",
}

REQUIRED_CONSTRAINTS = (
    "shadow_decisions_shadow_only",
    "shadow_decisions_lane",
    "shadow_decisions_lineage",
    "shadow_decisions_rn1_no_belief",
    "shadow_decisions_rn1_observed",
    "shadow_decisions_policy_frozen",
    "rn1_obs_correction_references",
    # Section 7's prohibition, as a composite foreign key rather than a
    # convention: a decision can only descend from a row whose
    # prospective_kind is set, and that column is NULL for everything
    # except a plain OBSERVATION.
    "shadow_decisions_prospective_only",
    "rn1_obs_recovery_named",
)


async def store_ready(pool=None) -> dict:
    """Every invariant the directive names, checked against the catalog.

    Returns a verdict, not a boolean, because "not ready" is only useful
    if it says WHICH guarantee is missing.
    """
    pool = pool or await get_pool()
    problems = []

    rows = await pool.fetch(
        """
        SELECT c.relname AS tbl, t.tgname AS trg
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
         WHERE NOT t.tgisinternal
        """)
    present = {(r["tbl"], r["trg"]) for r in rows}
    for table, trigger in REQUIRED_TRIGGERS.items():
        if (table, trigger) not in present:
            problems.append(
                "%s has no append-only trigger %s" % (table, trigger))

    con = await pool.fetch(
        "SELECT conname FROM pg_constraint WHERE conname = ANY($1::text[])",
        list(REQUIRED_CONSTRAINTS))
    have = {r["conname"] for r in con}
    for name in REQUIRED_CONSTRAINTS:
        if name not in have:
            problems.append("constraint %s is absent" % name)

    lane = await pool.fetchrow(
        """
        SELECT is_nullable, column_default
          FROM information_schema.columns
         WHERE table_name = 'shadow_decisions' AND column_name = 'lane'
        """)
    if lane is None:
        problems.append("shadow_decisions has no lane column")
    else:
        if lane["is_nullable"] != "NO":
            problems.append("shadow_decisions.lane is nullable")
        # NO DEFAULT LANE. A default would let an untagged decision land
        # in whichever lane the column preferred, and attribution lost
        # at write time is not recoverable by any later query.
        if lane["column_default"] is not None:
            problems.append("shadow_decisions.lane has a default (%s)"
                            % lane["column_default"])

    idx = await pool.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname = $1",
        "rn1_observations_idem_idx")
    if not idx:
        problems.append("the RN1 idempotency index is absent")
    elif "idempotency_key" not in idx or "OBSERVATION" not in idx:
        problems.append("the RN1 idempotency index is not the expected "
                        "partial unique index: %s" % idx)

    return {
        "storeReady": not problems,
        "problems": problems,
        "shadowMode": sh.SHADOW_MODE,
        "realOrderSubmissionEnabled": sh.REAL_ORDER_SUBMISSION_ENABLED,
        "capitalAtRisk": sh.CAPITAL_AT_RISK,
        "appendOnlyWriter": append_only_writer_verdict(),
        "disclosure": sh.DISCLOSURE,
    }


def append_only_writer_verdict() -> dict:
    """What this module claims about itself, stated so the claim is
    visible in the same receipt the schema check lands in. The PROOF is
    the test that reads this file; this is the declaration it pins."""
    return {"module": __name__,
            "writesOnlyInserts": True,
            "correctionsAreAppends": True,
            "provenBy": "backend/tests/test_shadow_store.py"}


# ── the policy freeze ────────────────────────────────────────────────


async def freeze_policy(pool=None, policy: dict | None = None) -> dict:
    """Write the frozen policy row, once. Idempotent, and honest about a
    disagreement rather than papering over it.

    THREE OUTCOMES, ALL EXPLICIT:

      FROZEN          the row did not exist and now does.
      ALREADY_FROZEN  it existed and its declaration hash matches. The
                      code sha is compared too and REPORTED; a mismatch
                      there means the implementing modules' bytes moved
                      without the rules moving, which is a question for
                      a human, not a reason to stop collecting.
      REFUSED         it existed with a DIFFERENT declaration hash. The
                      rules changed under a version name that is already
                      carrying rows. Nothing is written, nothing is
                      revised; a new version is the only way forward.
    """
    pool = pool or await get_pool()
    p = policy or pol.frozen_policy()

    existing = await pool.fetchrow(
        """
        SELECT policy_version, policy_sha, policy_code_sha, frozen_at
          FROM shadow_policy_versions
         WHERE policy_version = $1
        """, p["policyVersion"])
    if existing is not None:
        if existing["policy_sha"] != p["policySha"]:
            return {
                "status": "REFUSED",
                "policyVersion": p["policyVersion"],
                "why": ("%s is already frozen with a different declaration. "
                        "Rows written under it mean what the FROZEN rules "
                        "said. Bump the version." % p["policyVersion"]),
                "frozenSha": existing["policy_sha"],
                "declaredSha": p["policySha"],
            }
        return {
            "status": "ALREADY_FROZEN",
            "policyVersion": p["policyVersion"],
            "policySha": existing["policy_sha"],
            "policyCodeSha": existing["policy_code_sha"],
            "codeShaMatches": existing["policy_code_sha"] == p["policyCodeSha"],
            "frozenAt": existing["frozen_at"],
        }

    await pool.execute(
        """
        INSERT INTO shadow_policy_versions (
            policy_version, policy_sha, policy_code_sha, lane, action_set,
            pairing_rule_version, cashout_rule_version,
            latency_policy_version, execution_reconstruction_version,
            sizing_policy_version, declaration)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11::jsonb)
        ON CONFLICT DO NOTHING
        """,
        p["policyVersion"], p["policySha"], p["policyCodeSha"], p["lane"],
        json.dumps(p["actionSet"]), p["pairingRuleVersion"],
        p["cashoutRuleVersion"], p["latencyPolicyVersion"],
        p["executionReconstructionVersion"], p["sizingPolicyVersion"],
        json.dumps(p["declaration"], default=str))

    return {"status": "FROZEN",
            "policyVersion": p["policyVersion"],
            "policySha": p["policySha"],
            "policyCodeSha": p["policyCodeSha"],
            "codeShaMatches": True}


async def freeze_sizing_policy(pool=None, policy: dict | None = None) -> dict:
    """Freeze the $1,000 standard, once, before the first eligible entry.

    THE SAME THREE OUTCOMES AS freeze_policy, for the same reason. A
    sizing rule that could be edited after results existed would let the
    denominator of every return be chosen to flatter the numerator, and
    "Do not choose starting capital retrospectively to flatter returns"
    is the same instinct one table over.

    SEPARATE FROM THE EV POLICY ON PURPOSE. BETTOR_EV_SHADOW_V1 decides
    WHETHER to act and its frozen declaration says sizing is
    NOT_APPLICABLE -- still true, because V1's action set is exactly
    [NO_TRADE]. This decides HOW MUCH once something is already
    eligible. Migration 074's header carries the full reasoning,
    including why editing the frozen EV declaration would be refused by
    the database rather than merely unwise.
    """
    pool = pool or await get_pool()
    p = policy or szpol.frozen_policy()

    if await pool.fetchval(
            "SELECT to_regclass('public.bettor_sizing_policies')") is None:
        return {"status": "STORE_NOT_READY",
                "sizingPolicyVersion": p["sizingPolicyVersion"],
                "why": "migration 074 has not been applied"}

    existing = await pool.fetchrow(
        """
        SELECT sizing_policy_version, policy_sha, standard_notional_usd,
               frozen_at
          FROM bettor_sizing_policies
         WHERE sizing_policy_version = $1
        """, p["sizingPolicyVersion"])
    if existing is not None:
        if existing["policy_sha"] != p["policySha"]:
            return {
                "status": "REFUSED",
                "sizingPolicyVersion": p["sizingPolicyVersion"],
                "why": ("%s is already frozen with a different sizing "
                        "declaration. Entries recorded under it were sized "
                        "by the FROZEN rule. Bump the version."
                        % p["sizingPolicyVersion"]),
                "frozenSha": existing["policy_sha"],
                "declaredSha": p["policySha"],
            }
        return {
            "status": "ALREADY_FROZEN",
            "sizingPolicyVersion": p["sizingPolicyVersion"],
            "policySha": existing["policy_sha"],
            "standardNotionalUsd": float(existing["standard_notional_usd"]),
            "frozenAt": existing["frozen_at"],
        }

    await pool.execute(
        """
        INSERT INTO bettor_sizing_policies (
            sizing_policy_version, cohort, lane, standard_notional_usd,
            policy_sha, declaration)
        VALUES ($1,$2,$3,$4,$5,$6::jsonb)
        ON CONFLICT DO NOTHING
        """,
        p["sizingPolicyVersion"], p["cohort"], p["lane"],
        p["standardNotionalUsd"], p["policySha"],
        json.dumps(p["declaration"], default=str))

    return {"status": "FROZEN",
            "sizingPolicyVersion": p["sizingPolicyVersion"],
            "policySha": p["policySha"],
            "standardNotionalUsd": float(p["standardNotionalUsd"])}


# ── RN1 observations ─────────────────────────────────────────────────


def observation_record(*, venue="polymarket", source_fill_id=None,
                       source_event_id=None, source_type,
                       source_reference=None, raw_evidence_reference=None,
                       rn1_source_ts=None, bettor_received_ts,
                       source_ts_status=SOURCE_SUPPLIED,
                       event_id=None, market_id=None, symbol=None,
                       outcome_leg=None, side, rn1_price, rn1_quantity,
                       tx_hash=None, asset=None,
                       ingest_version=INGEST_VERSION) -> dict:
    """Validate one sighting and build its row. Pure -- no database.

    BETTOR_RECEIVED_TS IS REQUIRED AND RN1_SOURCE_TS IS NOT. The one
    fact only we hold is when WE heard; the venue's own instant may be
    missing, and when it is, the row says MISSING rather than borrowing
    our clock and calling it the venue's. That substitution is exactly
    what run 82 cost, and it is not repeated here.
    """
    if source_type not in SOURCE_TYPES:
        raise StoreRefusal("refused: %r is not a source type" % source_type)
    if str(side).upper() not in ("BUY", "SELL"):
        raise StoreRefusal("refused: %r is not a side" % side)
    if bettor_received_ts is None:
        raise StoreRefusal(
            "refused: an observation with no BETTOR_RECEIVED_TIMESTAMP "
            "records nothing this system uniquely knows")
    if source_ts_status not in (SOURCE_SUPPLIED, TS_MISSING,
                                FALLBACK_SUBSTITUTED):
        raise StoreRefusal("refused: %r is not a timestamp status"
                           % source_ts_status)
    if rn1_source_ts is None and source_ts_status == SOURCE_SUPPLIED:
        raise StoreRefusal(
            "refused: no source timestamp, but the row claims the source "
            "supplied one")
    try:
        price = float(rn1_price)
        quantity = float(rn1_quantity)
    except (TypeError, ValueError):
        raise StoreRefusal("refused: RN1 price and quantity must be numbers")
    if quantity <= 0:
        raise StoreRefusal("refused: a fill of %r shares is not a fill"
                           % rn1_quantity)

    key = observation_key(venue=venue, source_fill_id=source_fill_id,
                          tx_hash=tx_hash, asset=asset, side=side,
                          quantity=quantity, price=price,
                          source_ts=rn1_source_ts)
    return {
        "rn1ObservationId": _observation_id(key["key"], OBSERVATION),
        "recordKind": OBSERVATION,
        "idempotencyKey": key["key"],
        "idempotencyBasis": key["basis"],
        "sourceFillId": source_fill_id,
        "sourceEventId": source_event_id,
        "sourceType": source_type,
        "sourceReference": source_reference or tx_hash,
        "rawEvidenceReference": raw_evidence_reference,
        "ingestVersion": ingest_version,
        "rn1SourceTs": rn1_source_ts,
        "bettorReceivedTs": bettor_received_ts,
        "sourceTsStatus": source_ts_status,
        "eventId": event_id,
        "marketId": market_id,
        "symbol": symbol,
        "outcomeLeg": outcome_leg,
        "side": str(side).upper(),
        "rn1Price": price,
        "rn1Quantity": quantity,
        # THE STANDING FACT ABOUT EVERY SIGHTING: it is not a trade.
        "observationIsNotATrade": True,
    }


_OBS_INSERT = """
    INSERT INTO rn1_observations (
        rn1_observation_id, record_kind, supersedes_observation_id,
        correction_reason, idempotency_key, idempotency_basis,
        source_fill_id, source_event_id, source_type, source_reference,
        raw_evidence_reference, ingest_version, rn1_source_ts,
        bettor_received_ts, observation_written_ts, source_ts_status,
        event_id, market_id, symbol, outcome_leg, side, rn1_price,
        rn1_quantity)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
            COALESCE($15, now()),$16,$17,$18,$19,$20,$21,$22,$23)
    ON CONFLICT DO NOTHING
    RETURNING rn1_observation_id
"""


async def record_observation(record: dict, pool=None,
                             written_ts=None) -> tuple[str, bool]:
    """Append one sighting. Returns (id, was_this_the_first_sight).

    ON CONFLICT DO NOTHING WITH NO CONFLICT TARGET, deliberately: the
    row is guarded by a primary key AND by the partial unique index on
    the idempotency key, and naming one of them as the arbiter would
    raise on the other. "Any uniqueness we declared" is the rule we
    actually want.

    THE BOOLEAN IS THE DEDUPE ANSWER AND THE ID IS NOT. That overloading
    is what made the reconciler report permanent drift in August; it is
    not repeated. A caller asking "did I just see something new?" reads
    the second element.
    """
    pool = pool or await get_pool()
    r = record
    row = await pool.fetchrow(
        _OBS_INSERT,
        r["rn1ObservationId"], r["recordKind"],
        r.get("supersedesObservationId"), r.get("correctionReason"),
        r["idempotencyKey"], r["idempotencyBasis"], r.get("sourceFillId"),
        r.get("sourceEventId"), r["sourceType"], r.get("sourceReference"),
        r.get("rawEvidenceReference"), r["ingestVersion"],
        r.get("rn1SourceTs"), r["bettorReceivedTs"], written_ts,
        r.get("sourceTsStatus", SOURCE_SUPPLIED), r.get("eventId"),
        r.get("marketId"), r.get("symbol"), r.get("outcomeLeg"),
        r["side"], r["rn1Price"], r["rn1Quantity"])
    if row is not None:
        return row["rn1_observation_id"], True
    existing = await pool.fetchval(
        """
        SELECT rn1_observation_id FROM rn1_observations
         WHERE idempotency_key = $1 AND record_kind = 'OBSERVATION'
        """, r["idempotencyKey"])
    return existing, False


def _supersede(original: dict, kind: str, reason: str,
               changes: dict | None = None) -> dict:
    if not reason:
        raise StoreRefusal(
            "refused: a %s with no reason is an erasure wearing a label"
            % kind)
    record = dict(original)
    record.update(changes or {})
    record["recordKind"] = kind
    record["supersedesObservationId"] = original["rn1ObservationId"]
    record["correctionReason"] = reason
    record["rn1ObservationId"] = _observation_id(
        original["idempotencyKey"], kind,
        salt=json.dumps(changes or {}, sort_keys=True, default=str)
        + "|" + reason)
    return record


async def invalidate_observation(original: dict, reason: str,
                                 pool=None) -> str:
    """A reorg or a withdrawal. APPENDED, referencing the original.

    The original stays exactly as written. That is not sentiment: the
    fact that BETTOR acted on a sighting the chain later withdrew is
    itself a measurement of this system, and erasing the sighting erases
    the only evidence of it.
    """
    record = _supersede(original, OBSERVATION_INVALIDATED, reason)
    obs_id, _ = await record_observation(record, pool=pool)
    return obs_id


async def correct_observation(original: dict, reason: str, changes: dict,
                              pool=None) -> str:
    """A restatement. Also appended, also referencing the original."""
    if not changes:
        raise StoreRefusal("refused: a correction that changes nothing")
    record = _supersede(original, OBSERVATION_CORRECTED, reason, changes)
    obs_id, _ = await record_observation(record, pool=pool)
    return obs_id


# ── the market state BETTOR actually had ─────────────────────────────


def market_state_record(*, captured_at, symbol, evidence_source,
                        outcome_leg=None, bid=None, ask=None,
                        available_depth=None, l2_reference=None,
                        staleness_ms=None, source_interval_s=None,
                        readable=True, why_unreadable=None) -> dict:
    """The book, with its source named and its unreadability sayable.

    AN UNREADABLE BOOK IS A STATE, NOT A ZERO. A market we could not
    read records readable=False and why; it never records a bid of 0,
    which a later reader would price against.
    """
    if not evidence_source:
        raise StoreRefusal(
            "refused: a market state with no EVIDENCE_SOURCE cannot be "
            "kept apart from another feed's")
    if not readable and not why_unreadable:
        raise StoreRefusal("refused: unreadable, with no reason given")
    mid = spread = None
    if readable and bid is not None and ask is not None:
        mid = (float(bid) + float(ask)) / 2.0
        spread = float(ask) - float(bid)
    return {
        "marketStateId": _id("mkt", symbol, evidence_source,
                             _ts_text(captured_at), outcome_leg, bid, ask),
        "capturedAt": captured_at,
        "symbol": symbol,
        "outcomeLeg": outcome_leg,
        "evidenceSource": evidence_source,
        "readable": bool(readable),
        "whyUnreadable": why_unreadable,
        "bid": bid, "ask": ask, "mid": mid, "spread": spread,
        "availableDepth": available_depth,
        "l2Reference": l2_reference,
        "stalenessMs": staleness_ms,
        "sourceIntervalS": source_interval_s,
    }


async def record_market_state(record: dict, pool=None) -> str:
    pool = pool or await get_pool()
    r = record
    await pool.execute(
        """
        INSERT INTO shadow_market_states (
            market_state_id, captured_at, symbol, outcome_leg,
            evidence_source, readable, why_unreadable, bid, ask, mid,
            spread, available_depth, l2_reference, staleness_ms,
            source_interval_s)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,
                $14,$15)
        ON CONFLICT DO NOTHING
        """,
        r["marketStateId"], r["capturedAt"], r["symbol"], r.get("outcomeLeg"),
        r["evidenceSource"], r["readable"], r.get("whyUnreadable"),
        r.get("bid"), r.get("ask"), r.get("mid"), r.get("spread"),
        _j(r.get("availableDepth")), _j(r.get("l2Reference")),
        r.get("stalenessMs"), r.get("sourceIntervalS"))
    return r["marketStateId"]


def _j(value):
    return None if value is None else json.dumps(value, default=str)


# ── the prospective decision ─────────────────────────────────────────


def rn1_decision_record(*, rn1_observation_id, market_state_id=None,
                        rn1_price=None, price_when_bettor_observed=None,
                        price_when_bettor_decided=None,
                        policy_version=None, **fields) -> dict:
    """One RN1_SHADOW decision, validated before it can be written.

    FOUR PRICES, FOUR COLUMNS, NEVER SUBSTITUTED. RN1_PRICE is what RN1
    got. It is recorded because the comparison is the point, and it is
    never used as BETTOR's executable price -- that price comes from the
    book BETTOR actually saw, at the instant BETTOR actually arrived.

    P_BETTOR AND INFORMATION_EV ARE HELD CLOSED. RN1_SHADOW is a
    mechanism benchmark. A fair value is not manufactured from RN1
    activity, here or anywhere.
    """
    if not rn1_observation_id:
        raise StoreRefusal(
            "refused: an RN1_SHADOW decision with no observation behind "
            "it is not in the lane, whatever it is tagged")
    fields.setdefault("policyVersion",
                      policy_version or pol.RN1_SHADOW_POLICY_VERSION)
    record = lanes.rn1_lane_decision(**fields)
    if record.get("pBettor") is not None:
        raise StoreRefusal(
            "refused: RN1_SHADOW carries no independent belief, and this "
            "row supplied one")
    # HELD CLOSED EXPLICITLY, not merely left out. A key that is absent
    # and a key that is None read the same from .get(), but only one of
    # them states the guarantee to a reader of the record.
    record["pBettor"] = None
    record["informationEv"] = None
    record["pBettorStatus"] = lanes.NOT_ESTABLISHED
    record["rn1ObservationId"] = rn1_observation_id
    record["marketStateId"] = market_state_id
    record["rn1Price"] = rn1_price
    record["priceWhenBettorObserved"] = price_when_bettor_observed
    record["priceWhenBettorDecided"] = price_when_bettor_decided
    return record


_DECISION_INSERT = """
    INSERT INTO shadow_decisions (
        shadow_decision_id, event_id, market_id, symbol, outcome_leg,
        sport, league, model_version, policy_version, evidence_source,
        venue_source_ts, bettor_received_ts, feature_asof_ts, decision_ts,
        market_bid, market_ask, mid, spread, available_depth,
        l2_reference, p_market, p_bettor, information_ev, execution_ev,
        total_action_ev, uncertainty, p_ev_gt_zero, break_even_fill_p,
        p_fill, proposed_action, proposed_side, proposed_price,
        proposed_quantity, reason_codes, gate_results, blockers,
        alternatives, lane, signal_source, p_bettor_status, p_fill_status,
        feature_lineage, rn1_features_used, specialist_outputs,
        action_ev_components, action_ev_status, rn1_observation_id,
        market_state_id, rn1_price, price_when_bettor_observed,
        price_when_bettor_decided, bettor_opportunity_id)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
            $18,$19::jsonb,$20::jsonb,$21,$22,$23,$24,$25,$26,$27,$28,
            $29,$30,$31,$32,$33,$34::jsonb,$35::jsonb,$36::jsonb,
            $37::jsonb,$38,$39,$40,$41,$42::jsonb,$43,$44::jsonb,
            $45::jsonb,$46,$47,$48,$49,$50,$51,$52)
    ON CONFLICT DO NOTHING
    RETURNING shadow_decision_id
"""


async def record_decision(record: dict, pool=None) -> tuple[str, bool]:
    """Append one prospective decision. NO_TRADE included, always.

    "NO_TRADE decisions are equally important and must be retained." A
    store that quietly dropped them would make the ledger a record of
    the times BETTOR acted, which is the one sample guaranteed to
    flatter it.
    """
    pool = pool or await get_pool()
    r = record
    row = await pool.fetchrow(
        _DECISION_INSERT,
        r["shadowDecisionId"], r.get("eventId"), r.get("marketId"),
        r["symbol"], r["outcomeLeg"], r.get("sport"), r.get("league"),
        r["modelVersion"], r["policyVersion"], r["evidenceSource"],
        r.get("venueSourceTs"), r.get("bettorReceivedTs"),
        r.get("featureAsofTs"), r["decisionTs"], r.get("marketBid"),
        r.get("marketAsk"), r.get("mid"), r.get("spread"),
        _j(r.get("availableDepth")), _j(r.get("l2Reference")),
        r.get("pMarket"), r.get("pBettor"), r.get("informationEv"),
        r.get("executionEv"), r.get("totalActionEv"), r.get("uncertainty"),
        r.get("pEvGtZero"), r.get("breakEvenFillP"), r.get("pFill"),
        r["proposedAction"], r.get("proposedSide"), r.get("proposedPrice"),
        r.get("proposedQuantity"), _j(r.get("reasonCodes") or []),
        _j(r.get("gateResults") or {}), _j(r.get("blockers") or []),
        _j(r.get("alternatives") or []), r["lane"], r.get("signalSource"),
        r.get("pBettorStatus"), r.get("pFillStatus"),
        _j(r.get("featureLineage")), r.get("rn1FeaturesUsed"),
        _j(r.get("specialistOutputs")), _j(r.get("actionEvComponents")),
        r.get("actionEvStatus"), r.get("rn1ObservationId"),
        r.get("marketStateId"), r.get("rn1Price"),
        r.get("priceWhenBettorObserved"), r.get("priceWhenBettorDecided"),
        # THE COLUMN MIGRATION 071's CHECK REQUIRES. Its absence here is
        # what made every BETTOR_EV_SHADOW decision fail its insert from
        # the moment 071 landed: 31 opportunities observed, zero
        # decisions written, and the worker reporting tick_failed with
        # an empty problems list. The lane that is the product was
        # silently writing nothing.
        r.get("bettorOpportunityId"))
    if row is not None:
        return row["shadow_decision_id"], True
    return r["shadowDecisionId"], False


# ── reconstructed execution ──────────────────────────────────────────


async def record_execution(*, shadow_decision_id, execution_class,
                           latency_basis, status, pool=None,
                           shadow_execution_id=None, why=None,
                           latency=None, arrival_ts=None, arrival_book=None,
                           arrival_market_state_id=None,
                           price_at_shadow_arrival=None,
                           shadow_filled_qty=None, vwap=None, slippage=None,
                           spread_cost=None, unfilled_qty=None,
                           latency_scenario_ms=None) -> str:
    """Append one reconstructed execution.

    THE CLASS IS NOT OPTIONAL AND NOT DEFAULTED. It is the single thing
    that stops a simulated number and an observed number from being
    added together, and a default would put that guarantee in the hands
    of whoever forgot to pass it.
    """
    if execution_class not in sh.EXECUTION_CLASSES:
        raise StoreRefusal("refused: %r is not an execution class"
                           % execution_class)
    if execution_class == sh.ACTUAL_FILL and shadow_filled_qty:
        raise StoreRefusal(
            "refused: ACTUAL_FILL with %r shares, while "
            "REAL_ORDER_SUBMISSION is disabled and CAPITAL_AT_RISK is 0"
            % shadow_filled_qty)
    pool = pool or await get_pool()
    lat = latency or {}
    exec_id = shadow_execution_id or _id(
        "sxe", shadow_decision_id, execution_class, latency_basis,
        latency_scenario_ms, _ts_text(arrival_ts))
    await pool.execute(
        """
        INSERT INTO shadow_executions (
            shadow_execution_id, shadow_decision_id, execution_class,
            data_latency_ms, decision_compute_ms, execution_latency_ms,
            total_to_arrival_ms, latency_basis, latency_scenario_ms,
            arrival_ts, arrival_book, shadow_filled_qty, vwap, slippage,
            spread_cost, unfilled_qty, status, why,
            price_at_shadow_arrival, arrival_market_state_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,
                $15,$16,$17,$18,$19,$20)
        ON CONFLICT DO NOTHING
        """,
        exec_id, shadow_decision_id, execution_class,
        lat.get("dataMs"), lat.get("computeMs"), lat.get("executionMs"),
        lat.get("totalMs"), latency_basis, latency_scenario_ms,
        arrival_ts, _j(arrival_book), shadow_filled_qty, vwap, slippage,
        spread_cost, unfilled_qty, status, why, price_at_shadow_arrival,
        arrival_market_state_id)
    return exec_id


# ── scores, which arrive later and ANNOTATE ──────────────────────────


async def record_score(*, shadow_decision_id, horizon,
                       source_interval_s=None, pool=None, **measures) -> dict:
    """Append a score. Its own table, keyed by the decision.

    A markout written back onto the decision row would be the system
    editing its own past claim, which is the one thing this ledger
    exists to make impossible. So scores annotate and never touch.

    OBSERVABILITY IS CHECKED PER ROW. A 30-second markout from a feed
    that ticks every minute is an interpolation wearing a timestamp; it
    is stored as unobservable WITH THE REASON, not as a number.
    """
    verdict = sh.horizon_observable(horizon, source_interval_s)
    pool = pool or await get_pool()
    if not verdict["observable"]:
        measures = {}
    await pool.execute(
        """
        INSERT INTO shadow_scores (
            shadow_decision_id, horizon, observable, why_unobservable,
            direction_correct, mid_markout, executable_markout,
            spread_relative_move, adverse_selection, slippage,
            realized_vs_expected_ev, settlement_result)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        ON CONFLICT DO NOTHING
        """,
        shadow_decision_id, horizon, verdict["observable"], verdict["why"],
        measures.get("direction_correct"), measures.get("mid_markout"),
        measures.get("executable_markout"),
        measures.get("spread_relative_move"),
        measures.get("adverse_selection"), measures.get("slippage"),
        measures.get("realized_vs_expected_ev"),
        measures.get("settlement_result"))
    return verdict


# ── positions: opened once, then annotated by events ─────────────────


async def open_position(*, shadow_position_id, originating_decision_id,
                        symbol, leg, lane, entry_time, pool=None,
                        event_id=None, market_id=None, entry_price=None,
                        entry_qty=None, entry_ev=None) -> str:
    """One row per LEG. YES and NO are different positions and are never
    netted into one number -- that defect cost real money on the live
    mirror and it is not recreated here."""
    if lane not in lanes.LANES:
        raise StoreRefusal("refused: %r is not a lane" % lane)
    pool = pool or await get_pool()
    await pool.execute(
        """
        INSERT INTO shadow_positions (
            shadow_position_id, originating_decision_id, event_id,
            market_id, symbol, leg, entry_time, entry_price, entry_qty,
            entry_ev, lane)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        ON CONFLICT DO NOTHING
        """,
        shadow_position_id, originating_decision_id, event_id, market_id,
        symbol, leg, entry_time, entry_price, entry_qty, entry_ev, lane)
    return shadow_position_id


async def record_position_event(*, shadow_position_id, kind, pool=None,
                                shadow_decision_id=None,
                                execution_class=None, at=None,
                                **measures) -> None:
    """Every state CHANGE is a new row. Nothing about a position is
    overwritten, so the whole life of a shadow position is readable in
    arrival order instead of as a final figure."""
    pool = pool or await get_pool()
    await pool.execute(
        """
        INSERT INTO shadow_position_events (
            shadow_position_id, shadow_decision_id, at, kind, current_qty,
            executable_mark, unrealized_pnl, realized_pnl, settlement_pnl,
            pair_status, exit_intention, next_decision, capital_hours,
            max_adverse_move, max_favorable_move, execution_class)
        VALUES ($1,$2,COALESCE($3, now()),$4,$5,$6,$7,$8,$9,$10,$11,$12,
                $13,$14,$15,$16)
        """,
        shadow_position_id, shadow_decision_id, at, kind,
        measures.get("current_qty"), measures.get("executable_mark"),
        measures.get("unrealized_pnl"), measures.get("realized_pnl"),
        measures.get("settlement_pnl"), measures.get("pair_status"),
        measures.get("exit_intention"), measures.get("next_decision"),
        measures.get("capital_hours"), measures.get("max_adverse_move"),
        measures.get("max_favorable_move"), execution_class)
