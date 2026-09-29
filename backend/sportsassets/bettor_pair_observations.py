"""NON-FUNDED PAIR OBSERVATIONS: HOW THE PAIRING MODEL GETS ITS FIRST LABELS.

THE CIRCULAR DEPENDENCY, STATED. `bettor_funded_model.labelled` labels a
funded decision only when BOTH legs of its group were held and settled. Holding
the second leg requires `decide` to rank ACQUIRE_INDIRECT_HEDGE, which requires
region probabilities, which require an APPROVED model, which requires labels.
No production data can produce the first label, so the funded path alone never
learns anything.

WHAT BREAKS IT WITHOUT INVENTING ANYTHING. A pairing label -- did the held
side and the hedge side BOTH win -- is a fact about the fixture and two
contracts, read from the venue's own settlement of each. Holding them changes
the money, not the outcome. So the lane OBSERVES pairing structures it
discovers:

  observe_candidate   for a contract the entry cycle mapped, build it as a
                      hypothetical first leg at its displayed price, run the
                      production discovery (`bettor_funded_hedge_supply` +
                      `bettor_funded_pair_cycle.discover`), and for every
                      admitted structure record the model's feature vector,
                      frozen at the instant it was seen, with each price's
                      book-currency verdict;
  label_pending       later, read each contract's settlement from the venue
                      and write the label -- side-aware, a push or an
                      unreadable price is NOT a label -- with the instant it
                      was read; re-read recent labels, and version a change;
  labelled            the same record shape `bettor_funded_model.labelled`
                      returns, so the registry fits, evaluates and verifies
                      observation-sourced models through the SAME code, bar and
                      provenance rules as funded ones.

WHAT IT DOES NOT DO. It approves nothing and relaxes nothing. A model fit on
observations is a CANDIDATE: record-bound (its provenance names this source and
every observation id), scored prospectively and event-balanced on observations
made after it froze, and promoted only by a named approver through `promote`.
It sends nothing to any venue: every read is a book, catalogue, prose or
settlement read.

WHAT REMAINS SEPARATE. `region_probabilities` still needs how `1 - p_middle`
splits over the non-middle regions (`OUTSIDE_SPLIT_HAS_NO_ADMISSIBLE_SOURCE`).
These labels are "both won / not"; they do not identify which non-middle
region occurred, so they do not supply the split.

AND A STATED LIMIT. The observed population is every discovered pair; the
funded population is the pairs the lane admits. A model skilled on the first
is not thereby shown skilled on the second -- its funded predictions are scored
prospectively once funded pairs exist, like any model's.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

VERSION = "PAIR_OBSERVATIONS_V1"
SOURCE = "PAIR_OBSERVATIONS"
OBSERVATION_SOURCE_TAG = "SCHEDULED_NON_FUNDED_OBSERVATION"

AWAITING = "AWAITING_SETTLEMENT"
LABELLED = "LABELLED"
NOT_A_LABEL = "NOT_A_LABEL"

#: One observation per (fixture, first leg, second leg) per this many seconds.
#: The same pair seen every cycle is ONE fixture however often it is seen --
#: event balancing already weights it once -- and the bucket bounds the table.
BUCKET_S = 3600
#: Bounded work per cycle.
CANDIDATES_PER_PASS = 3
LABEL_READS_PER_PASS = 20
#: A label is re-read for corrections for this long after it was first written.
CORRECTION_WINDOW_S = 7 * 86400
RECHECKS_PER_PASS = 10

R_SCHEMA = "THE_OBSERVATION_SCHEMA_IS_NOT_IN_THIS_DATABASE"
R_NO_PRICE = "THE_FIRST_LEG_HAS_NO_DISPLAYED_PRICE"
R_NO_LEG = "THE_FIRST_LEG_COULD_NOT_BE_BUILT"
R_NOTHING_ADMITTED = "NO_SETTLEMENT_COMPATIBLE_SECOND_CONTRACT"
WHY_PUSH_OR_VOID = ("a settlement strictly between 0 and 1 is a push or a void, "
                    "and the venue's figure does not say which. Neither is 'both "
                    "sides won', and guessing would put a coin flip in the label")

LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"


async def has_schema(conn) -> bool:
    n = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        " WHERE table_schema='public' AND table_name = ANY($1::text[])",
        ["bettor_pair_observations", "bettor_pair_observation_labels"])
    return int(n or 0) == 2


def _dt(epoch: float):
    import datetime as _d
    return _d.datetime.fromtimestamp(float(epoch), _d.timezone.utc)


def observation_id_for(*, fixture, primary_identity, hedge_identity,
                       at: float) -> str:
    bucket = int(float(at) // BUCKET_S)
    blob = "|".join((str(fixture), str(primary_identity),
                     str(hedge_identity), str(bucket)))
    return "obs:" + hashlib.sha256(blob.encode()).hexdigest()[:24]


# ═════════════════════════════════════════════════════════════════════
# 1 · OBSERVING
# ═════════════════════════════════════════════════════════════════════

async def record(conn, *, fixture: str, admitted: dict, held_leg,
                 primary_slug: str, primary_side: str, hedge_slug: str,
                 hedge_side: str, primary_cost_cents, hedge_cost_cents,
                 overtime_included, price_basis: dict, at: float) -> dict:
    """ONE OBSERVATION, frozen. Idempotent within its bucket."""
    from . import bettor_funded_model as FMD

    structure = admitted.get("structure") or {}
    feats = FMD.features_of(structure, primary_cost_cents=primary_cost_cents,
                            hedge_cost_cents=hedge_cost_cents,
                            overtime_included=overtime_included)
    oid = observation_id_for(
        fixture=fixture,
        primary_identity=getattr(held_leg, "condition_id", primary_slug),
        hedge_identity=admitted.get("condition_id") or hedge_slug, at=at)
    status = await conn.execute(
        "INSERT INTO bettor_pair_observations (observation_id, observed_at, "
        " fixture, primary_slug, primary_side, hedge_slug, hedge_side, "
        " taxonomy, structure, primary_cost_cents, hedge_cost_cents, "
        " overtime_included, features, feature_sha, feature_schema_sha, "
        " price_basis, source) VALUES ($1, to_timestamp($2), $3, $4, $5, $6, "
        " $7, $8, $9::jsonb, $10, $11, $12, $13::jsonb, $14, $15, $16::jsonb, "
        " $17) ON CONFLICT (observation_id) DO NOTHING",
        oid, float(at), str(fixture), primary_slug, primary_side, hedge_slug,
        hedge_side, admitted.get("taxonomy"),
        json.dumps(structure, default=str), float(primary_cost_cents),
        float(hedge_cost_cents),
        None if overtime_included is None else bool(overtime_included),
        json.dumps(feats), FMD.feature_sha(feats), FMD.FEATURE_SCHEMA_SHA,
        json.dumps(price_basis or {}, default=str), OBSERVATION_SOURCE_TAG)
    return {"observation_id": oid, "written": str(status).endswith(" 1"),
            "taxonomy": admitted.get("taxonomy")}


def _basis_of(quote: dict) -> dict:
    cur = (quote or {}).get("book_currency") or {}
    return {"read_at": (quote or {}).get("read_at"),
            "book_currency_verdict": cur.get("verdict"),
            "mechanism": cur.get("mechanism"),
            "usable_for_orders": bool((quote or {}).get("usable_for_orders")),
            "price_is": "THE_DISPLAYED_ACQUISITION_PRICE_WHEN_OBSERVED"}


async def observe_candidate(conn, *, us_market_slug: str, side: str,
                            quoter, prose_reader, tie_reader=None,
                            now: float | None = None) -> dict:
    """DISCOVER AND RECORD EVERY PAIRING STRUCTURE ON ONE CANDIDATE'S FIXTURE.

    The candidate is built as a hypothetical first leg at its own displayed
    price, through the SAME supplier and discovery the funded pair pass uses.
    Nothing is held, reserved or sent.
    """
    from . import bettor_funded_hedge_supply as HSUP
    from . import bettor_funded_pair_cycle as PC
    from . import bettor_indirect_structures as IS
    from . import bettor_venue_settlement as vset

    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "us_market_slug": us_market_slug,
                           "side": side, "recorded": [], "sent_anything": False}
    q, _ = await HSUP._quote_side(quoter, us_market_slug, side)
    price = q.get("cost_per_share") or q.get("price")
    if price is None:
        return dict(out, ok=False, refusal=R_NO_PRICE,
                    quote_refusal=q.get("refusal") or q.get("error"))
    position = {"intent_id": "observation", "us_market_slug": us_market_slug,
                "order_intent": side, "limit_price": float(price),
                "filled_qty": 1, "residual_qty": 1}
    held = await HSUP.held_leg_for(conn, position=position,
                                   prose_reader=prose_reader, now=at)
    if not held.get("ok"):
        return dict(out, ok=False, refusal=R_NO_LEG,
                    held_refusal=held.get("refusal"))
    cands = await HSUP.candidate_legs_for(
        conn, held_row=dict(held.get("row") or {},
                            market_slug=held.get("us_market_slug"),
                            residual_qty=1),
        quoter=quoter, prose_reader=prose_reader, now=at)
    row = held.get("row") or {}
    leg = held["leg"]
    tie = (tie_reader or vset.tie_is_reachable)(
        sport_family=str(row.get("sports_type") or "").split("_")[0].lower()
        or None, overtime=getattr(leg, "overtime", None))
    found = PC.discover(held_leg=leg,
                        candidate_legs=[c["leg"] for c in cands.get("legs")
                                        or []],
                        sport_permits_tie=tie.get("permits_tie"))
    out["examined"] = cands.get("examined")
    out["discovery_refusal"] = found.get("refusal")
    if not found.get("admitted"):
        return dict(out, ok=True, refusal=R_NOTHING_ADMITTED)
    quotes = {c["candidate_id"]: c for c in cands.get("legs") or []}
    for adm in found["admitted"]:
        detail = quotes.get(adm["condition_id"]) or {}
        h_slug, h_side = HSUP.split_identity(adm["condition_id"])
        hleg = adm.get("leg")
        if h_side is None or getattr(hleg, "cost_cents_per_unit", None) is None:
            continue
        got = await record(
            conn, fixture=leg.fixture_id, admitted=adm, held_leg=leg,
            primary_slug=us_market_slug, primary_side=side,
            hedge_slug=h_slug, hedge_side=h_side,
            primary_cost_cents=leg.cost_cents_per_unit,
            hedge_cost_cents=hleg.cost_cents_per_unit,
            overtime_included=(getattr(leg, "overtime", None)
                               == IS.OT_INCLUDED),
            price_basis={"primary": _basis_of(q),
                         "hedge": _basis_of(detail.get("quote") or {})},
            at=at)
        out["recorded"].append(got)
    return dict(out, ok=True, refusal=None)


# ═════════════════════════════════════════════════════════════════════
# 2 · LABELLING, FROM THE VENUE'S OWN SETTLEMENTS
# ═════════════════════════════════════════════════════════════════════

def won(side: str, price) -> bool | None:
    """Did this side win at this settlement price? None when it is neither."""
    if price is None:
        return None
    p = float(price)
    if p == 1.0:
        return side == LONG
    if p == 0.0:
        return side == SHORT
    return None


def _production_settlement(slug: str) -> dict:
    from . import bettor_live_read as LR
    return LR.read_settlement(None, slug)


def label_from(row: dict, pr: dict, hr: dict) -> dict:
    """What two settlement reads say about one observation."""
    from . import bettor_live_read as LR

    if pr.get("status") != LR.RESOLVED or hr.get("status") != LR.RESOLVED:
        return {"status": AWAITING,
                "why": "primary %s, hedge %s" % (pr.get("status"),
                                                 hr.get("status"))}
    pw = won(row["primary_side"], pr.get("settlement_price"))
    hw = won(row["hedge_side"], hr.get("settlement_price"))
    if pw is None or hw is None:
        return {"status": NOT_A_LABEL, "why": WHY_PUSH_OR_VOID,
                "primary_price": pr.get("settlement_price"),
                "hedge_price": hr.get("settlement_price")}
    return {"status": LABELLED, "primary_won": pw, "hedge_won": hw,
            "middle": bool(pw and hw),
            "primary_price": pr.get("settlement_price"),
            "hedge_price": hr.get("settlement_price")}


async def _write_label(conn, row: dict, lab: dict, *, reads: dict,
                       at: float) -> bool:
    """A new label version, with its history row. False when unchanged."""
    same = (row["label_status"] == lab["status"]
            and row.get("middle_occurred") == lab.get("middle")
            and _num(row.get("primary_settlement_price"))
            == _num(lab.get("primary_price"))
            and _num(row.get("hedge_settlement_price"))
            == _num(lab.get("hedge_price")))
    if same or lab["status"] == AWAITING:
        return False
    v = int(row["label_version"]) + 1
    labelled = lab["status"] == LABELLED
    async with conn.transaction():
        await conn.execute(
            "UPDATE bettor_pair_observations SET label_status=$2, "
            " label_why=$3, primary_settlement_price=$4, "
            " hedge_settlement_price=$5, primary_won=$6, hedge_won=$7, "
            " middle_occurred=$8, outcome_available_at=$9, label_version=$10 "
            " WHERE observation_id=$1 AND label_version=$11",
            row["observation_id"], lab["status"], lab.get("why"),
            lab.get("primary_price"), lab.get("hedge_price"),
            lab.get("primary_won") if labelled else None,
            lab.get("hedge_won") if labelled else None,
            lab.get("middle") if labelled else None,
            _dt(at) if labelled else None, v, int(row["label_version"]))
        await conn.execute(
            "INSERT INTO bettor_pair_observation_labels (observation_id, "
            " label_version, label_status, label_why, primary_settlement_price,"
            " hedge_settlement_price, middle_occurred, reads) VALUES "
            "($1,$2,$3,$4,$5,$6,$7,$8::jsonb)",
            row["observation_id"], v, lab["status"], lab.get("why"),
            lab.get("primary_price"), lab.get("hedge_price"),
            lab.get("middle") if labelled else None,
            json.dumps(reads, default=str))
    return True


def _num(v):
    return None if v is None else round(float(v), 9)


async def label_pending(conn, *, settlement_reader=None,
                        now: float | None = None,
                        limit: int = LABEL_READS_PER_PASS,
                        recheck: int = RECHECKS_PER_PASS) -> dict:
    """LABEL WHAT HAS SETTLED; RE-READ RECENT LABELS FOR CORRECTIONS.

    A correction writes a NEW label version, which changes the training
    records' hash -- so a model fit on the old label stops reproducing and is
    withdrawn by the same rule that governs funded labels.
    """
    at = float(now if now is not None else time.time())
    reader = settlement_reader or _production_settlement
    out: dict[str, Any] = {"version": VERSION, "at": at, "labelled": 0,
                           "not_a_label": 0, "awaiting": 0, "corrected": 0,
                           "unreadable": 0}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    # LEAST RECENTLY READ FIRST, in both queues: a contract that never
    # settles, or a label that never changes, must not keep the rest unread.
    pending = [dict(r) for r in await conn.fetch(
        "SELECT * FROM bettor_pair_observations WHERE label_status=$1 "
        " ORDER BY last_read_at NULLS FIRST, observed_at LIMIT $2",
        AWAITING, int(limit))]
    recent = [dict(r) for r in await conn.fetch(
        "SELECT * FROM bettor_pair_observations WHERE label_status <> $1 "
        "   AND coalesce(outcome_available_at, last_read_at) "
        "       > to_timestamp($2) "
        " ORDER BY last_read_at NULLS FIRST, observed_at LIMIT $3", AWAITING,
        at - CORRECTION_WINDOW_S, int(recheck))]
    cache: dict[str, dict] = {}

    async def _read(slug):
        if slug not in cache:
            try:
                cache[slug] = dict(await asyncio.to_thread(reader, slug) or {})
            except Exception as exc:                        # noqa: BLE001
                cache[slug] = {"status": "UNREADABLE",
                               "error": type(exc).__name__}
        return cache[slug]

    for row in pending + recent:
        pr, hr = await _read(row["primary_slug"]), await _read(row["hedge_slug"])
        lab = label_from(row, pr, hr)
        if "UNREADABLE" in (pr.get("status"), hr.get("status")):
            out["unreadable"] += 1
        wrote = await _write_label(
            conn, row, lab, at=at,
            reads={"primary": {k: pr.get(k) for k in
                               ("status", "settlement_price_raw",
                                "settled_at", "error")},
                   "hedge": {k: hr.get(k) for k in
                             ("status", "settlement_price_raw",
                              "settled_at", "error")}})
        await conn.execute(
            "UPDATE bettor_pair_observations SET last_read_at=to_timestamp($2)"
            " WHERE observation_id=$1", row["observation_id"], at)
        if row["label_status"] == AWAITING:
            key = {LABELLED: "labelled", NOT_A_LABEL: "not_a_label"}.get(
                lab["status"], "awaiting")
            out[key] += 1 if (wrote or key == "awaiting") else 0
        elif wrote:
            out["corrected"] += 1
    return dict(out, ok=True)


# ═════════════════════════════════════════════════════════════════════
# 3 · THE RECORDS THE REGISTRY READS
# ═════════════════════════════════════════════════════════════════════

async def labelled(conn, *, after=None, through=None, outcomes_through=None,
                   ids=None) -> dict:
    """`bettor_funded_model.labelled`'s shape, from observations.

    WINDOWS as there: `after`/`through` bound the OBSERVATION instant and
    `outcomes_through` the instant the label was read here.
    """
    out: dict[str, Any] = {"version": VERSION, "source": SOURCE, "rows": [],
                           "labels": []}
    keys = ("decision_ids", "groups", "fixtures", "decided_at",
            "feature_shas", "outcome_available_at", "leg_outcomes", "pushes",
            "outcome_versions")
    for k in keys:
        out[k] = []
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA, n=0, n_events=0)
    sql = ("SELECT *, extract(epoch FROM observed_at) AS obs_epoch, "
           " extract(epoch FROM outcome_available_at) AS avail_epoch "
           " FROM bettor_pair_observations WHERE label_status=$1")
    args: list = [LABELLED]
    if after is not None:
        args.append(after)
        sql += " AND observed_at > $%d" % len(args)
    if through is not None:
        args.append(through)
        sql += " AND observed_at <= $%d" % len(args)
    if outcomes_through is not None:
        args.append(outcomes_through if not isinstance(
            outcomes_through, (int, float)) else _dt(outcomes_through))
        sql += " AND outcome_available_at <= $%d" % len(args)
    if ids is not None:
        args.append([str(x) for x in ids])
        sql += " AND observation_id = ANY($%d::text[])" % len(args)
    sql += " ORDER BY observed_at, observation_id"
    for r in await conn.fetch(sql, *args):
        feats = r["features"]
        feats = json.loads(feats) if isinstance(feats, str) else feats
        out["rows"].append(feats)
        out["labels"].append(1.0 if r["middle_occurred"] else 0.0)
        out["decision_ids"].append(r["observation_id"])
        out["groups"].append(r["observation_id"])
        out["fixtures"].append(r["fixture"])
        out["decided_at"].append(float(r["obs_epoch"]))
        out["feature_shas"].append(r["feature_sha"])
        out["outcome_available_at"].append(float(r["avail_epoch"]))
        out["leg_outcomes"].append([
            {"slug": r["primary_slug"], "side": r["primary_side"],
             "settlement_price": _num(r["primary_settlement_price"]),
             "won": r["primary_won"]},
            {"slug": r["hedge_slug"], "side": r["hedge_side"],
             "settlement_price": _num(r["hedge_settlement_price"]),
             "won": r["hedge_won"]}])
        out["pushes"].append(False)
        out["outcome_versions"].append(int(r["label_version"]))
    out["n_events"] = len({str(f) for f in out["fixtures"]})
    return dict(out, ok=True, refusal=None, n=len(out["labels"]),
                label_basis=("BOTH OBSERVED SIDES WON, from each contract's "
                             "venue settlement price (LONG at 1, SHORT at 0). "
                             "A push or void is not a label"))


# ═════════════════════════════════════════════════════════════════════
# 4 · THE SCHEDULED PASS
# ═════════════════════════════════════════════════════════════════════

async def observation_pass(conn, *, candidates, quoter, prose_reader,
                           settlement_reader=None, now: float | None = None,
                           per_pass: int = CANDIDATES_PER_PASS) -> dict:
    """ONE CYCLE'S OBSERVATION WORK: observe a few candidates, label what has
    settled, and offer the registry a candidate fit on observations. Bounded,
    and it never raises into the cycle."""
    from . import bettor_funded_model as FMD

    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at, "observed": [],
                           "sent_anything": False, "promoted_anything": False}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    seen = set()
    todo = []
    for slug, side in candidates or ():
        if not slug or side not in (LONG, SHORT) or (slug, side) in seen:
            continue
        seen.add((slug, side))
        todo.append((slug, side))
    out["candidates_offered"] = len(todo)
    for slug, side in todo[:int(per_pass)]:
        try:
            got = await observe_candidate(conn, us_market_slug=slug, side=side,
                                          quoter=quoter,
                                          prose_reader=prose_reader, now=at)
        except Exception as exc:                            # noqa: BLE001
            got = {"ok": False, "us_market_slug": slug, "side": side,
                   "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
        out["observed"].append({k: got.get(k) for k in
                                ("us_market_slug", "side", "ok", "refusal",
                                 "recorded", "examined", "error")})
    out["not_observed_this_pass"] = max(0, len(todo) - int(per_pass))
    try:
        out["labels"] = await label_pending(
            conn, settlement_reader=settlement_reader, now=at)
    except Exception as exc:                                # noqa: BLE001
        out["labels"] = {"ok": False, "error": type(exc).__name__}
    try:
        out["generate"] = await FMD.generate_candidate(conn, now=at,
                                                       source=SOURCE)
    except Exception as exc:                                # noqa: BLE001
        out["generate"] = {"ok": False, "error": type(exc).__name__}
    return dict(out, ok=True)
