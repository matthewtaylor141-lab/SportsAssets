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
                      through the probe funded legs close on, and write the
                      label -- side-aware; a push is "not both won" exactly as
                      a funded label counts it, a void or an unreadable price
                      is NOT a label -- with the instant it was read; re-read
                      recent labels, and version a change;
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

WHAT THE RECORD KEEPS, AND WHAT THE BINARY TARGET THROWS AWAY. An earlier
version of this paragraph said these labels are only "both won / not". That was
false about the record: `label_from` and the table keep `primary_won`,
`hedge_won` and BOTH settlement prices, so which leg won, which pushed and at
what price are all stored. It is KEY_MIDDLE's binary training target,
`middle_occurred`, that discards the distinction. What no record states is
which margin or total band occurred -- and no decision needs it: every action's
value depends on a region only through its per-leg payout pair
(`bettor_payout_states`). So the per-leg outcomes kept here, read against the
observation's stored payoff table, label the second model the lane learns --
KEY_HEDGE_GIVEN_PRIMARY, P(hedge wins | primary outcome), via
`labelled_conditional` -- and the settled fixtures give the void rate
(`void_rate`). `region_probabilities`' outside split is still not supplied by
anything, and is no longer needed on the path that prices through classes.

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
#: THE ONE LABEL DEFINITION, shared with `bettor_funded_model.LABEL_SQL`: a
#: leg won only at the payout that pays its own side in full (LONG at 1,
#: SHORT at 0). A PUSH -- a reported price strictly between -- is a leg that
#: did not win, so the pair is labelled "not both won" and flagged as a push;
#: an EXPLICIT VOID is no observation at all. (Review of 599076c: dropping
#: pushes here while funded labels counted them made an observation model
#: estimate P(both win | no push), consumed as the unconditional p_middle.)
WHY_PUSH = "A_LEG_PUSHED_SO_BOTH_SIDES_DID_NOT_WIN"
WHY_VOID = ("the venue declared a void: the outcome space never resolved, "
            "so there is nothing to label -- as for a funded leg")
#: Retained name: older history rows carry it.
WHY_PUSH_OR_VOID = WHY_VOID
#: The normalised status of a declared void.
VOID = "EXPLICIT_VOID"

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
    t0 = time.monotonic()
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
    out["fixture"] = getattr(leg, "fixture_id", None)
    tie = (tie_reader or vset.tie_is_reachable)(
        sport_family=str(row.get("sports_type") or "").split("_")[0].lower()
        or None, overtime=getattr(leg, "overtime", None))
    found = PC.discover(held_leg=leg,
                        candidate_legs=[c["leg"] for c in cands.get("legs")
                                        or []],
                        sport_permits_tie=tie.get("permits_tie"))
    out["examined"] = cands.get("examined")
    out["discovery_refusal"] = found.get("refusal")
    out["admitted"] = len(found.get("admitted") or [])
    # WHY EACH SIBLING DID NOT BECOME A SECOND LEG, counted by name: the
    # supplier's refusals (unpriced, unbuildable) and discovery's rejections.
    why: dict = {}
    for r in cands.get("refused") or []:
        k = str(r.get("refusal") or "UNNAMED")
        why[k] = why.get(k, 0) + 1
    for r in found.get("rejected") or []:
        k = str((r or {}).get("refusal") or (r or {}).get("reason")
                or "UNNAMED")
        why[k] = why.get(k, 0) + 1
    out["second_legs_refused"] = why
    if not found.get("admitted"):
        return dict(out, ok=True, refusal=R_NOTHING_ADMITTED)
    quotes = {c["candidate_id"]: c for c in cands.get("legs") or []}
    out["skipped_unpriced_second_leg"] = 0
    for adm in found["admitted"]:
        detail = quotes.get(adm["condition_id"]) or {}
        h_slug, h_side = HSUP.split_identity(adm["condition_id"])
        hleg = adm.get("leg")
        if h_side is None or getattr(hleg, "cost_cents_per_unit", None) is None:
            # COUNTED, NOT SILENT: an admitted structure whose second leg
            # names no side or carries no cost cannot be frozen as features.
            out["skipped_unpriced_second_leg"] += 1
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
            # OBSERVED WHEN THE PRICES HAD BEEN READ, not when the pass began:
            # the features are what the book showed by now.
            at=at + (time.monotonic() - t0))
        out["recorded"].append(got)
    return dict(out, ok=True, refusal=None)


# ═════════════════════════════════════════════════════════════════════
# 2 · LABELLING, FROM THE VENUE'S OWN SETTLEMENTS
# ═════════════════════════════════════════════════════════════════════

def won(side: str, price) -> bool | None:
    """Did this side win at this settlement price? None only when there is
    no price. A push (strictly between 0 and 1) did not win."""
    if price is None:
        return None
    p = float(price)
    return (p == 1.0) if side == LONG else (p == 0.0)


def is_push(price) -> bool:
    return price is not None and 0.0 < float(price) < 1.0


def settlement_from_probe(got: dict) -> dict:
    """The probe's terminal reading, in the shape `label_from` reads.

    ONLY THE READINGS A FUNDED LEG CLOSES ON COUNT. REPORTED_SETTLEMENT is the
    settlement endpoint's price CORROBORATED against the venue's own long
    side (`read_resolution`; a contradiction is UNREADABLE); EXPLICIT_VOID is
    a declared void. CONVERGED_PRICE_INFERENCE -- our inference -- and every
    other reading is not a settlement."""
    from . import bettor_live_read as LR

    reading = str((got or {}).get("terminal_reading") or "")
    rv = (got or {}).get("reader_verdict") or {}
    base = {"terminal_reading": reading or None,
            "corroboration": rv.get("corroboration"),
            "settlement_price_raw": rv.get("settlement_price_raw"),
            "settled_at": rv.get("settled_at"), "error": rv.get("error"),
            "why": (got or {}).get("why")}
    if reading == "EXPLICIT_VOID":
        return dict(base, status=VOID, settlement_price=None)
    if reading == "REPORTED_SETTLEMENT":
        return dict(base, status=LR.RESOLVED,
                    settlement_price=rv.get("settlement_price"))
    return dict(base, status=reading or LR.UNREADABLE, settlement_price=None)


def _production_settlement(slug: str) -> dict:
    """THE SAME READER, AND SO THE SAME BAR, AS A FUNDED LEG'S LABEL: the
    probe `bettor_funded_management.reconcile_settlement` closes a funded
    position on, read the way it reads it."""
    from . import bettor_venue_settlement_probe as SP
    return settlement_from_probe(SP.probe(None, slug))


def label_from(row: dict, pr: dict, hr: dict) -> dict:
    """What two settlement reads say about one observation."""
    from . import bettor_live_read as LR

    if VOID in (pr.get("status"), hr.get("status")):
        return {"status": NOT_A_LABEL, "why": WHY_VOID,
                "primary_price": pr.get("settlement_price"),
                "hedge_price": hr.get("settlement_price")}
    if pr.get("status") != LR.RESOLVED or hr.get("status") != LR.RESOLVED:
        return {"status": AWAITING,
                "why": "primary %s, hedge %s" % (pr.get("status"),
                                                 hr.get("status"))}
    # RESOLVED WITHOUT A PRICE IS NOT A PUSH. The listing branch of
    # `read_resolution` can say RESOLVED from a reported outcome field and
    # state no settlement price; the funded lane refuses to close on that
    # (no payout established), and a label is held to the same bar -- it
    # waits, rather than being recorded as a void it was never shown to be.
    if pr.get("settlement_price") is None or hr.get("settlement_price") is None:
        return {"status": AWAITING,
                "why": "RESOLVED_WITHOUT_A_SETTLEMENT_PRICE (primary %s, "
                       "hedge %s)" % (pr.get("settlement_price"),
                                      hr.get("settlement_price"))}
    pp, hp = pr.get("settlement_price"), hr.get("settlement_price")
    pw, hw = won(row["primary_side"], pp), won(row["hedge_side"], hp)
    push = is_push(pp) or is_push(hp)
    return {"status": LABELLED, "primary_won": pw, "hedge_won": hw,
            "middle": bool(pw and hw), "push": push,
            "why": WHY_PUSH if push else None,
            "primary_price": pp, "hedge_price": hp}


async def _write_label(conn, row: dict, lab: dict, *, reads: dict,
                       at: float) -> bool:
    """A new label version, with its history row. False when unchanged.

    `at` is the instant the settlements were READ here, which is when the
    label became known -- not the start of the pass."""
    same = (row["label_status"] == lab["status"]
            and row.get("middle_occurred") == lab.get("middle")
            and row.get("primary_won") == (lab.get("primary_won")
                                           if lab["status"] == LABELLED
                                           else None)
            and row.get("hedge_won") == (lab.get("hedge_won")
                                         if lab["status"] == LABELLED
                                         else None)
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
            " label_version, recorded_at, label_status, label_why, "
            " primary_settlement_price, hedge_settlement_price, "
            " middle_occurred, reads) VALUES "
            "($1,$2,to_timestamp($3),$4,$5,$6,$7,$8,$9::jsonb)",
            row["observation_id"], v, float(at), lab["status"],
            lab.get("why"), lab.get("primary_price"), lab.get("hedge_price"),
            lab.get("middle") if labelled else None,
            json.dumps(reads, default=str))
    return True


def _num(v):
    return None if v is None else round(float(v), 9)


#: THE SHARE OF A PASS'S READS that may go to pairs never read before. New
#: observations arrive every hour and almost never find their fixture played
#: on the first read, so without a cap they would take every read and nothing
#: already waiting would be read again (review of 599076c).
FRESH_SHARE = 0.5
#: A pair read this recently is not due again. Fixtures take hours; a pass
#: that re-read what it read minutes ago would spend its budget on nothing.
REREAD_AFTER_S = 1800.0

_PAIR_ORDER = {
    # never read: oldest observation first
    "fresh": ("SELECT primary_slug, hedge_slug FROM bettor_pair_observations "
              " WHERE label_status=$1 GROUP BY primary_slug, hedge_slug "
              "HAVING max(last_read_at) IS NULL "
              " ORDER BY min(observed_at), primary_slug, hedge_slug LIMIT $2"),
    # read before and due again: least recently read first
    "stale": ("SELECT primary_slug, hedge_slug FROM bettor_pair_observations "
              " WHERE label_status=$1 GROUP BY primary_slug, hedge_slug "
              "HAVING max(last_read_at) IS NOT NULL "
              "   AND max(last_read_at) <= to_timestamp($3) "
              " ORDER BY max(last_read_at), primary_slug, hedge_slug LIMIT $2"),
}


def split_budget(n_fresh: int, n_stale: int, limit: int) -> tuple[int, int]:
    """How many never-read and due previously-read pairs one pass reads: the
    never-read may take the whole budget only when nothing else is due, and
    at most FRESH_SHARE of it when something is."""
    cap = max(int(limit * FRESH_SHARE), int(limit) - int(n_stale))
    take_fresh = min(int(n_fresh), cap)
    return take_fresh, min(int(n_stale), int(limit) - take_fresh)


async def label_pending(conn, *, settlement_reader=None,
                        now: float | None = None,
                        limit: int = LABEL_READS_PER_PASS,
                        recheck: int = RECHECKS_PER_PASS,
                        deadline_s: float | None = None) -> dict:
    """LABEL WHAT HAS SETTLED; RE-READ RECENT LABELS FOR CORRECTIONS.

    BY PAIR, NOT BY ROW. Every hourly observation of one (primary, hedge)
    pair is labelled by the same two settlements, so the unit of work is the
    pair: `limit` pairs awaiting a label and `recheck` recently labelled
    pairs per pass, each pair's two contracts read once, every row of it
    labelled from them.

    A correction writes a NEW label version, which changes the training
    records' hash -- so a model fit on the old label stops reproducing and is
    withdrawn by the same rule that governs funded labels.
    """
    t0 = time.monotonic()
    at0 = float(now if now is not None else time.time())

    def _now() -> float:
        return at0 + (time.monotonic() - t0)

    reader = settlement_reader or _production_settlement
    out: dict[str, Any] = {"version": VERSION, "at": at0, "labelled": 0,
                           "not_a_label": 0, "awaiting": 0, "corrected": 0,
                           "unreadable": 0, "row_errors": 0,
                           "pairs_read": 0, "stopped_for_deadline": False}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    fresh = [tuple(r) for r in await conn.fetch(
        _PAIR_ORDER["fresh"], AWAITING, int(limit))]
    stale = [tuple(r) for r in await conn.fetch(
        _PAIR_ORDER["stale"], AWAITING, int(limit), at0 - REREAD_AFTER_S)]
    nf, ns = split_budget(len(fresh), len(stale), int(limit))
    pairs = stale[:ns] + fresh[:nf]
    out["pairs_selected"] = {"never_read": nf, "read_before": ns}
    # RECENT LABELS, by the instant the FIRST label was written -- which a
    # re-read does not move -- so a label leaves the correction window when
    # it should, whatever its status.
    recent = [tuple(r) for r in await conn.fetch(
        "SELECT o.primary_slug, o.hedge_slug FROM bettor_pair_observations o "
        " WHERE o.label_status <> $1 AND (SELECT min(l.recorded_at) FROM "
        "   bettor_pair_observation_labels l "
        "   WHERE l.observation_id = o.observation_id) > to_timestamp($2) "
        " GROUP BY o.primary_slug, o.hedge_slug "
        " ORDER BY min(o.last_read_at) NULLS FIRST, o.primary_slug, "
        "          o.hedge_slug LIMIT $3",
        AWAITING, at0 - CORRECTION_WINDOW_S, int(recheck))]
    cache: dict[str, tuple[dict, float]] = {}

    async def _read(slug):
        if slug not in cache:
            try:
                got = dict(await asyncio.to_thread(reader, slug) or {})
            except Exception as exc:                        # noqa: BLE001
                got = {"status": "UNREADABLE", "error": type(exc).__name__}
            cache[slug] = (got, _now())
        return cache[slug]

    async def _rows(pair, awaiting: bool):
        return [dict(r) for r in await conn.fetch(
            "SELECT * FROM bettor_pair_observations WHERE primary_slug=$1 "
            "   AND hedge_slug=$2 AND (label_status = $3) = $4 "
            " ORDER BY observed_at", pair[0], pair[1], AWAITING, awaiting)]

    for pair, awaiting in ([(p, True) for p in pairs]
                           + [(p, False) for p in recent]):
        if deadline_s is not None and time.monotonic() - t0 > deadline_s:
            out["stopped_for_deadline"] = True
            break
        (pr, pat), (hr, hat) = await _read(pair[0]), await _read(pair[1])
        out["pairs_read"] += 1
        read_at = max(pat, hat)
        if "UNREADABLE" in (pr.get("status"), hr.get("status")):
            out["unreadable"] += 1
        reads = {"primary": {k: pr.get(k) for k in
                             ("status", "terminal_reading", "corroboration",
                              "settlement_price_raw", "settled_at", "error")},
                 "hedge": {k: hr.get(k) for k in
                           ("status", "terminal_reading", "corroboration",
                            "settlement_price_raw", "settled_at", "error")},
                 "read_at": read_at}
        for row in await _rows(pair, awaiting):
            # ONE DAMAGED ROW DOES NOT STOP THE PASS: it is counted, its
            # read instant still advances, and the rest are labelled.
            try:
                lab = label_from(row, pr, hr)
                wrote = await _write_label(conn, row, lab, at=read_at,
                                           reads=reads)
            except Exception:                               # noqa: BLE001
                out["row_errors"] += 1
                lab, wrote = {"status": AWAITING}, False
            try:
                await conn.execute(
                    "UPDATE bettor_pair_observations SET last_read_at="
                    "to_timestamp($2) WHERE observation_id=$1",
                    row["observation_id"], read_at)
            except Exception:                               # noqa: BLE001
                out["row_errors"] += 1
            if awaiting:
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
        out["pushes"].append(bool(is_push(r["primary_settlement_price"])
                                  or is_push(r["hedge_settlement_price"])))
        out["outcome_versions"].append(int(r["label_version"]))
    out["n_events"] = len({str(f) for f in out["fixtures"]})
    return dict(out, ok=True, refusal=None, n=len(out["labels"]),
                label_basis=("BOTH OBSERVED SIDES WON, from each contract's "
                             "venue settlement price (LONG at 1, SHORT at 0). "
                             "A push is 'not both won', as a funded label "
                             "counts it; a void is not a label"))


#: ── THE CONDITIONAL'S LABEL RULE: every record it leaves out, by name ──
X_C_PRIMARY_OUTCOME_NOT_RECORDED = "PRIMARY_OUTCOME_NOT_RECORDED"
X_C_PRIMARY_PARTIAL = "PRIMARY_LEG_PUSHED_OR_SETTLED_BETWEEN_ZERO_AND_ONE"
X_C_HEDGE_OUTCOME_NOT_RECORDED = "HEDGE_OUTCOME_NOT_RECORDED"
X_C_CLASSES_REFUSED = "PAYOUT_CLASSES_REFUSED"
X_C_OUTCOME_NOT_IN_TABLE = "PRIMARY_OUTCOME_IS_NOT_ADMITTED_BY_THE_TABLE"
X_C_STRUCTURAL = "HEDGE_PAYOUT_IS_DETERMINED_BY_THE_TABLE_GIVEN_THIS_OUTCOME"
X_C_NOT_BINARY = "HEDGE_OUTCOME_IS_NOT_BINARY_GIVEN_THIS_OUTCOME"
X_C_HEDGE_PUSHED = "HEDGE_PUSHED_WHERE_THE_TABLE_ADMITS_ONLY_WIN_OR_LOSE"
X_C_FEATURES_UNREADABLE = "THE_FROZEN_FEATURE_VECTOR_IS_UNREADABLE"


def conditional_row(row: dict) -> dict:
    """ONE LABELLED OBSERVATION, READ FOR P(hedge wins | primary outcome).

    Pure. Returns {"include": True, "primary_outcome", "label", "features"} or
    {"include": False, "exclusion": name}. The primary outcome is WIN when the
    primary side won at its settlement price and LOSE when it lost outright; a
    push or any price between 0 and 1 on the primary is excluded, never read
    as a loss. The row is a training example only where the observation's OWN
    stored payoff table admits exactly two hedge payouts {0, 100} given that
    outcome -- the binary case the distribution needs learned. Elsewhere the
    table answers the question structurally, or the question is not binary.
    """
    from . import bettor_payout_states as PS

    pw, hw = row.get("primary_won"), row.get("hedge_won")
    if pw is None:
        return {"include": False, "exclusion": X_C_PRIMARY_OUTCOME_NOT_RECORDED}
    if is_push(row.get("primary_settlement_price")):
        return {"include": False, "exclusion": X_C_PRIMARY_PARTIAL}
    if hw is None:
        return {"include": False, "exclusion": X_C_HEDGE_OUTCOME_NOT_RECORDED}
    outcome = PS.WIN if pw else PS.LOSE
    structure = row.get("structure")
    if isinstance(structure, str):
        try:
            structure = json.loads(structure)
        except ValueError:
            structure = None
    classes = PS.payout_classes(structure or {})
    if not classes.get("ok"):
        return {"include": False,
                "exclusion": "%s:%s" % (X_C_CLASSES_REFUSED,
                                        classes.get("refusal"))}
    group = [c for c in classes["classes"]
             if not c["void"] and c["primary"]["outcome"] == outcome]
    if not group:
        return {"include": False, "exclusion": X_C_OUTCOME_NOT_IN_TABLE,
                "primary_outcome": outcome}
    if len(group) == 1:
        return {"include": False, "exclusion": X_C_STRUCTURAL,
                "primary_outcome": outcome}
    if sorted(c["hedge"]["cents"] for c in group) != [0, 100]:
        return {"include": False, "exclusion": X_C_NOT_BINARY,
                "primary_outcome": outcome}
    if is_push(row.get("hedge_settlement_price")):
        # THE TABLE SAID ONLY WIN OR LOSE WAS POSSIBLE AND THE VENUE PAID
        # SOMETHING BETWEEN. That is a disagreement between the partition and
        # the settlement, and it is counted -- not labelled as either.
        return {"include": False, "exclusion": X_C_HEDGE_PUSHED,
                "primary_outcome": outcome}
    feats = row.get("features")
    if isinstance(feats, str):
        try:
            feats = json.loads(feats)
        except ValueError:
            feats = None
    if not isinstance(feats, dict):
        return {"include": False, "exclusion": X_C_FEATURES_UNREADABLE}
    return {"include": True, "primary_outcome": outcome,
            "label": 1.0 if hw else 0.0,
            "features": dict(feats, primary_won=1.0 if pw else 0.0)}


async def labelled_conditional(conn, *, after=None, through=None,
                               outcomes_through=None, ids=None) -> dict:
    """`labelled`'s record shape, for KEY_HEDGE_GIVEN_PRIMARY.

    THE SAME WINDOWS AND THE SAME ROWS `labelled` reads (LABELLED observations
    only -- a void is still no label), so the training cutoffs, the outcome-
    availability instants, the label versions and the event-level holdouts are
    exactly KEY_MIDDLE's. What differs is the target -- did the HEDGE win --
    and which rows carry it (`conditional_row`). Every row left out is counted
    under `excluded` by name.

    The feature vector is the observation's frozen one plus `primary_won`, so
    its sha is distinct from the KEY_MIDDLE vector's.
    """
    from . import bettor_funded_model as FMD

    lab = await labelled(conn, after=after, through=through,
                         outcomes_through=outcomes_through, ids=ids)
    keys = ("decision_ids", "groups", "fixtures", "decided_at",
            "feature_shas", "outcome_available_at", "leg_outcomes", "pushes",
            "outcome_versions")
    out: dict[str, Any] = {"version": VERSION, "source": SOURCE,
                           "target": FMD.TARGET_HEDGE_GIVEN_PRIMARY,
                           "rows": [], "labels": [], "excluded": {},
                           "by_primary_outcome": {}}
    for k in keys:
        out[k] = []
    if not lab.get("ok"):
        return dict(out, ok=False, refusal=lab.get("refusal"), n=0,
                    n_events=0)
    by_id: dict = {}
    if lab["decision_ids"]:
        for r in await conn.fetch(
                "SELECT observation_id, structure, features, primary_won, "
                "       hedge_won, primary_settlement_price, "
                "       hedge_settlement_price "
                "  FROM bettor_pair_observations "
                " WHERE observation_id = ANY($1::text[])",
                [str(x) for x in lab["decision_ids"]]):
            by_id[r["observation_id"]] = dict(r)
    for i, oid in enumerate(lab["decision_ids"]):
        got = conditional_row(by_id.get(oid) or {})
        if not got["include"]:
            name = got["exclusion"]
            out["excluded"][name] = out["excluded"].get(name, 0) + 1
            continue
        o = got["primary_outcome"]
        out["by_primary_outcome"][o] = out["by_primary_outcome"].get(o, 0) + 1
        out["rows"].append(got["features"])
        out["labels"].append(got["label"])
        out["feature_shas"].append(FMD.feature_sha(got["features"]))
        for k in keys:
            if k != "feature_shas":
                out[k].append(lab[k][i])
    out["n_events"] = len({str(f) for f in out["fixtures"]})
    return dict(out, ok=True, refusal=None, n=len(out["labels"]),
                n_excluded=sum(out["excluded"].values()),
                label_basis=(
                    "THE HEDGE SIDE WON, from its venue settlement price (LONG "
                    "at 1, SHORT at 0), GIVEN the primary side won or lost "
                    "outright -- only where the observation's own payoff table "
                    "admits exactly two hedge payouts {0, 100} for that primary "
                    "outcome. A primary push, a hedge push the table does not "
                    "admit, and a void are not labels; each exclusion is "
                    "counted by name"))


# ═════════════════════════════════════════════════════════════════════
# 3b · THE VOID RATE, FROM RECORDED OUTCOMES, EVENT-LEVEL
# ═════════════════════════════════════════════════════════════════════

#: Settled FIXTURES a void rate is stated on. Below this the rate is refused:
#: a rate from a handful of fixtures is an anecdote with a decimal point.
MIN_VOID_RATE_FIXTURES = 40
#: The two-sided 95% normal quantile the Wilson upper bound is taken at.
_Z95 = 1.959963984540054
R_VOID_RATE_TOO_FEW_FIXTURES = "TOO_FEW_SETTLED_FIXTURES_TO_STATE_A_VOID_RATE"
R_VOID_RATE_UNREADABLE = "THE_SETTLED_OUTCOMES_COULD_NOT_BE_READ"


def wilson_upper_95(k: int, n: int) -> float | None:
    """The Wilson score interval's upper 95% bound for k of n. Pure."""
    if n <= 0:
        return None
    import math as _m
    p = k / float(n)
    z2 = _Z95 * _Z95
    centre = p + z2 / (2.0 * n)
    half = _Z95 * _m.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return min(1.0, (centre + half) / (1.0 + z2 / n))


#: POINT IN TIME, FROM THE LABEL HISTORY. Each observation's label as it stood
#: at `through` is its latest history version recorded by then -- so a void
#: declared, or corrected, after `through` is not seen at `through`.
_VOID_RATE_SQL = """
    WITH pit AS (
      SELECT DISTINCT ON (l.observation_id)
             l.observation_id, l.label_status, l.label_why, l.recorded_at
        FROM bettor_pair_observation_labels l
       WHERE ($1::timestamptz IS NULL OR l.recorded_at <= $1::timestamptz)
       ORDER BY l.observation_id, l.label_version DESC)
    SELECT o.fixture,
           bool_or(pit.label_status = $2 AND pit.label_why = $4) AS any_void,
           count(*) AS observations
      FROM pit JOIN bettor_pair_observations o USING (observation_id)
     WHERE pit.label_status = $3
        OR (pit.label_status = $2 AND pit.label_why = $4)
     GROUP BY o.fixture
"""


async def void_rate(conn, *, through=None) -> dict:
    """THE SHARE OF SETTLED FIXTURES THE VENUE DECLARED VOID. Never raises.

    EVENT-LEVEL. A fixture counts once however many observations it has: it
    is settled when any of its observations was LABELLED or declared void
    (NOT_A_LABEL with WHY_VOID) by `through`, and void when any of them was
    declared void -- a single voided contract is a void the fixture's payoff
    table has to price. Repeated observations of one fixture are one example.

    NO LOOK-AHEAD. Each observation's label is read as it stood at `through`
    (the latest label version recorded by then), so a void declared or
    corrected later is not counted earlier. Below MIN_VOID_RATE_FIXTURES the
    rate is refused. The point rate prices the distribution; the Wilson upper
    95% bound is carried so a ranking can say whether its choice changes at
    the rate's upper bound.
    """
    import datetime as _d

    thr = through
    if isinstance(thr, (int, float)):
        thr = _dt(thr)
    out: dict[str, Any] = {"version": VERSION, "source": SOURCE,
                           "through": (None if thr is None else
                                       thr.timestamp() if isinstance(
                                           thr, _d.datetime) else str(thr)),
                           "min_fixtures": MIN_VOID_RATE_FIXTURES}
    try:
        if not await has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA)
        rows = await conn.fetch(_VOID_RATE_SQL, thr, NOT_A_LABEL, LABELLED,
                                WHY_VOID)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal=R_VOID_RATE_UNREADABLE,
                    error=type(exc).__name__)
    n = len(rows)
    k = sum(1 for r in rows if r["any_void"])
    out.update(n_fixtures=n, n_void_fixtures=k,
               basis=("FIXTURES whose observations settled by `through` -- "
                      "LABELLED, or NOT_A_LABEL because the venue declared a "
                      "void -- each counted once; void when any of its "
                      "observations was declared void. Read point-in-time "
                      "from the label history"))
    if n < MIN_VOID_RATE_FIXTURES:
        return dict(out, ok=False, refusal=R_VOID_RATE_TOO_FEW_FIXTURES,
                    rate=None, upper_95=None,
                    why=("%d settled fixture(s); a void rate is stated on at "
                         "least %d" % (n, MIN_VOID_RATE_FIXTURES)))
    return dict(out, ok=True, refusal=None, rate=k / float(n),
                upper_95=wilson_upper_95(k, n),
                upper_95_is="WILSON_SCORE_UPPER_BOUND_AT_95_PERCENT")


# ═════════════════════════════════════════════════════════════════════
# 4 · CANDIDATES FROM THE VENUE'S OWN CATALOGUE, NOT FROM ENTRY ADMISSION
# ═════════════════════════════════════════════════════════════════════
#
# THE DEPENDENCY THIS REMOVES. The observer used to be offered only the
# contracts the entry cycle had mapped: an event needed a Pinnacle h2h quote,
# a global-catalogue match and a resolved venue identity before a single
# pairing structure on it could be seen. On the production cycle of
# 2026-09-29 every one of 49 events was refused before identity (no venue
# contract 22, no Pinnacle price 21, ...), so the observer was offered nothing
# and recorded nothing -- the learning path starved on the ENTRY lane's
# coverage, which has nothing to do with whether two venue contracts on one
# fixture can be observed and later labelled from the venue's settlements.
#
# The venue's catalogue (`us_premap`, written by the premap sweep from the
# venue's own event listing) names every open contract. A candidate is taken
# from it by the SAME predicates the supplier applies after a paid book read
# -- realism, fixture identity, a graded variable, orientation -- applied
# FIRST, so a contract that could never become a leg costs no venue read.
# Every exclusion is counted by name.

SOURCE_ENTRY = "ENTRY_IDENTITY"
SOURCE_CATALOGUE = "VENUE_CATALOGUE"

#: The premap sweep window is now-12h .. now+96h; a fixture is offered only
#: when it has not started (with a margin: a book read at the whistle prices a
#: live market) and lies inside the full sweep's forward window.
CATALOGUE_START_MARGIN_S = 600
CATALOGUE_HORIZON_S = 96 * 3600
#: A row the sweep has not re-seen recently may be a closed market: the sweep
#: writes only open markets and never deletes a closed one before its 26-hour
#: prune, so recency is the only open-proxy the catalogue offers. Two full
#: sweeps (1800 s each) plus slack.
CATALOGUE_RESEEN_S = 3900
#: And the sweep itself must be running: a catalogue nobody refreshes is
#: refused by name rather than read as the venue's current listing.
CATALOGUE_SWEEP_FRESH_S = 3900
#: A fixture observed this recently is not offered again: repeated
#: observations of one fixture are ONE example to the event-balanced model,
#: so a fresh fixture is worth more than another look at a seen one.
OBSERVE_FIXTURE_AGAIN_AFTER_S = 6 * 3600
#: A fixture attempted and refused is not re-attempted before this, so a
#: fixture that can never admit a pair does not consume every pass.
ATTEMPT_RETRY_S = 3 * 3600
#: Rows read per catalogue pass (both sides of each contract are rows).
CATALOGUE_ROW_LIMIT = 6000
#: Families whose overtime rule is captured from venue prose
#: (`bettor_venue_settlement.OVERTIME_PROSE`). Any other family's leg is
#: refused by `build_leg` (R_OVERTIME_NOT_CAPTURED) after a paid read, so it
#: is excluded -- by name and count -- before one.
CATALOGUE_FAMILIES = ("baseball", "soccer")

R_CATALOGUE_NOT_IN_DB = "THE_VENUE_CATALOGUE_TABLE_IS_NOT_IN_THIS_DATABASE"
R_CATALOGUE_SWEEP_STALE = "THE_VENUE_CATALOGUE_SWEEP_HAS_NOT_RUN_RECENTLY"
R_CATALOGUE_READ_FAILED = "THE_VENUE_CATALOGUE_COULD_NOT_BE_READ"

X_FAMILY_NOT_CAPTURED = "FAMILY_OVERTIME_RULE_NOT_CAPTURED"
X_NOT_A_GRADED_VARIABLE = "NOT_A_GRADED_VARIABLE"
X_NOT_REAL = "NOT_ESTABLISHED_AS_A_REAL_FIXTURE"
X_FIXTURE_IDENTITY = "FIXTURE_IDENTITY_NOT_ESTABLISHED"
X_ORIENTATION = "ORIENTATION_NOT_ESTABLISHED"
X_SIDE = "SIDE_NOT_LONG_OR_SHORT"
X_FEWER_THAN_TWO = "FEWER_THAN_TWO_GRADED_CONTRACTS_ON_THE_FIXTURE"
X_OBSERVED_RECENTLY = "FIXTURE_OBSERVED_RECENTLY"
X_ATTEMPTED_RECENTLY = "FIXTURE_ATTEMPTED_RECENTLY_AND_REFUSED"

#: ONLY ROWS THAT CAN BECOME A LEG ARE FETCHED -- a captured family and a
#: graded suffix -- because the window holds ~70,000 rows across every sport
#: and a row limit applied before that filter would be spent on table tennis.
#: Every other row is still COUNTED, by `_CATALOGUE_TALLY_SQL`, so what the
#: fetch leaves out is reported rather than silent.
_CATALOGUE_SQL = (
    "SELECT market_slug, intent, event_slug, event_title, question, "
    "       sports_type, team_abbr, side_norm, signed, line, game_start, "
    "       updated_at, extract(epoch FROM game_start) AS start_epoch "
    "  FROM us_premap "
    " WHERE game_start > to_timestamp($1) AND game_start <= to_timestamp($2) "
    "   AND updated_at > to_timestamp($3) AND event_slug IS NOT NULL "
    "   AND market_slug IS NOT NULL "
    "   AND split_part(coalesce(sports_type, ''), '_', 1) = ANY($5::text[]) "
    "   AND sports_type ~ $6 "
    " ORDER BY game_start, event_slug, market_slug, intent LIMIT $4")
_CATALOGUE_TALLY_SQL = (
    "SELECT split_part(coalesce(sports_type, ''), '_', 1) = ANY($4::text[]) "
    "         AS family_captured, "
    "       coalesce(sports_type ~ $5, false) AS graded, count(*) AS rows "
    "  FROM us_premap "
    " WHERE game_start > to_timestamp($1) AND game_start <= to_timestamp($2) "
    "   AND updated_at > to_timestamp($3) AND event_slug IS NOT NULL "
    "   AND market_slug IS NOT NULL "
    " GROUP BY 1, 2")


def graded_suffix_pattern() -> str:
    """A regular expression matching exactly the sports_types whose suffix
    `bettor_funded_hedge_supply.GRADED_SUFFIXES` names -- the supplier's own
    list, not a second copy of it."""
    import re as _re

    from . import bettor_funded_hedge_supply as HSUP

    return "(%s)$" % "|".join(_re.escape(s) for s, _ in HSUP.GRADED_SUFFIXES)

#: fixture -> (attempted_at, refusal), process-local. Lost on restart, which
#: costs one re-attempt per fixture, never a wrong observation.
_ATTEMPTED: dict = {}


def note_attempt(fixture, *, at: float, refusal) -> None:
    """Remember a REFUSED fixture so it is not re-attempted every pass. An
    attempt that recorded an observation is remembered by the table itself."""
    if fixture and refusal:
        _ATTEMPTED[str(fixture)] = (float(at), str(refusal))
        if len(_ATTEMPTED) > 4096:
            for k in sorted(_ATTEMPTED, key=lambda k: _ATTEMPTED[k][0])[:2048]:
                _ATTEMPTED.pop(k, None)


def _sweep_age_s(value, now: float):
    """Seconds since a premap sweep summary's `at` (ISO or epoch), or None
    if it cannot be read."""
    import datetime as _d

    v = value
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    at = v.get("at") if isinstance(v, dict) else None
    if at is None:
        return None
    try:
        t = float(at)
    except (TypeError, ValueError):
        try:
            dt = _d.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_d.timezone.utc)
        t = dt.timestamp()
    return round(float(now) - t, 1)


def screen_row(row: dict) -> str | None:
    """THE SUPPLIER'S OWN PREDICATES, before any venue read. None when the row
    could become a leg; otherwise the exclusion's name."""
    from . import bettor_funded_hedge_supply as HSUP
    from . import bettor_indirect_structures as IS
    from . import bettor_venue_realism as vreal

    if row.get("intent") not in (LONG, SHORT):
        return X_SIDE
    fam = str(row.get("sports_type") or "").split("_")[0].lower()
    if fam not in CATALOGUE_FAMILIES:
        return X_FAMILY_NOT_CAPTURED
    if (vreal.classify(row) or {}).get("verdict") != vreal.REAL:
        return X_NOT_REAL
    kind = HSUP.derive_kind(row)
    if kind.get("refusal"):
        return X_NOT_A_GRADED_VARIABLE
    fx = HSUP.fixture_participants(row.get("event_slug"))
    if fx.get("refusal"):
        return X_FIXTURE_IDENTITY
    if kind.get("kind") != IS.KIND_TOTAL and \
            HSUP.orientation_of(row, participants=fx).get("refusal"):
        return X_ORIENTATION
    return None


def _preference(row: dict) -> tuple:
    """Which contract of a fixture is offered as its first leg: a full-game
    winner's LONG side first, then spreads, then the rest. Discovery examines
    every sibling either way; this only fixes the order deterministically."""
    from . import bettor_funded_hedge_supply as HSUP
    from . import bettor_indirect_structures as IS

    k = HSUP.derive_kind(row)
    rank = {IS.KIND_MONEYLINE: 0, IS.KIND_THREE_WAY: 1, IS.KIND_SPREAD: 2,
            IS.KIND_TOTAL: 3}.get(k.get("kind"), 9)
    return (0 if k.get("period") == IS.PERIOD_FULL else 1, rank,
            0 if row.get("intent") == LONG else 1,
            str(row.get("market_slug") or ""))


async def catalogue_candidates(conn, *, now: float | None = None,
                               limit: int = 24) -> dict:
    """UP TO `limit` FIRST-LEG CANDIDATES, one per fixture, from the venue's
    own catalogue. DB reads only; never raises; every exclusion is counted."""
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "source": SOURCE_CATALOGUE,
                           "at": at, "candidates": [], "excluded_rows": {},
                           "excluded_fixtures": {}, "rows_read": 0,
                           "fixtures_seen": 0, "venue_reads": 0}
    try:
        present = await conn.fetchval("SELECT to_regclass('us_premap')")
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal=R_CATALOGUE_READ_FAILED,
                    error=type(exc).__name__)
    if present is None:
        return dict(out, ok=False, refusal=R_CATALOGUE_NOT_IN_DB)
    try:
        sweeps = {r["key"]: r["value"] for r in await conn.fetch(
            "SELECT key, value FROM ingestion_state "
            " WHERE key IN ('premap_last', 'premap_last_fast')")}
    except Exception:                                           # noqa: BLE001
        sweeps = {}
    ages = {k: _sweep_age_s(v, at) for k, v in sweeps.items()}
    out["sweep_age_s"] = ages
    known = [a for a in ages.values() if a is not None]
    if not known or min(known) > CATALOGUE_SWEEP_FRESH_S:
        return dict(out, ok=False, refusal=R_CATALOGUE_SWEEP_STALE,
                    why=("the premap sweep that refreshes the catalogue last "
                         "ran %s s ago (limit %d s); an unrefreshed catalogue "
                         "is not the venue's current listing"
                         % (min(known) if known else "never",
                            CATALOGUE_SWEEP_FRESH_S)))
    pattern = graded_suffix_pattern()
    window = (at + CATALOGUE_START_MARGIN_S, at + CATALOGUE_HORIZON_S,
              at - CATALOGUE_RESEEN_S)
    try:
        rows = [dict(r) for r in await conn.fetch(
            _CATALOGUE_SQL, *window, int(CATALOGUE_ROW_LIMIT),
            list(CATALOGUE_FAMILIES), pattern)]
        # WHAT THE FETCH LEFT OUT, COUNTED: other families and ungraded
        # types in the same window, so the exclusion is a number, not a
        # silence.
        for t in await conn.fetch(_CATALOGUE_TALLY_SQL, *window,
                                  list(CATALOGUE_FAMILIES), pattern):
            if not t["family_captured"]:
                k = X_FAMILY_NOT_CAPTURED
            elif not t["graded"]:
                k = X_NOT_A_GRADED_VARIABLE
            else:
                continue
            out["excluded_rows"][k] = (out["excluded_rows"].get(k, 0)
                                       + int(t["rows"]))
        seen_fx = ({str(r["fixture"]): float(r["last"]) for r in await
                    conn.fetch(
                        "SELECT fixture, extract(epoch FROM max(observed_at)) "
                        "       AS last FROM bettor_pair_observations "
                        " WHERE observed_at > to_timestamp($1) "
                        " GROUP BY fixture",
                        at - OBSERVE_FIXTURE_AGAIN_AFTER_S)}
                   if await has_schema(conn) else {})
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal=R_CATALOGUE_READ_FAILED,
                    error=type(exc).__name__)
    out["rows_read"] = len(rows)
    out["row_limit"] = CATALOGUE_ROW_LIMIT
    out["truncated_at_row_limit"] = len(rows) >= CATALOGUE_ROW_LIMIT
    by_fx: dict[str, list] = {}
    order: list = []
    for r in rows:
        why = screen_row(r)
        if why is not None:
            out["excluded_rows"][why] = out["excluded_rows"].get(why, 0) + 1
            continue
        fx = str(r["event_slug"]).strip().lower()
        if fx not in by_fx:
            by_fx[fx] = []
            order.append(fx)
        by_fx[fx].append(r)
    out["fixtures_seen"] = len(order)

    def _xf(name):
        out["excluded_fixtures"][name] = out["excluded_fixtures"].get(
            name, 0) + 1

    fresh, again = [], []
    for fx in order:
        legs = by_fx[fx]
        if len({r["market_slug"] for r in legs}) < 2:
            _xf(X_FEWER_THAN_TWO)
            continue
        if fx in seen_fx:
            _xf(X_OBSERVED_RECENTLY)
            continue
        tried = _ATTEMPTED.get(fx)
        if tried is not None and at - tried[0] < ATTEMPT_RETRY_S:
            _xf(X_ATTEMPTED_RECENTLY)
            continue
        best = min(legs, key=_preference)
        cand = {"us_market_slug": best["market_slug"], "side": best["intent"],
                "fixture": fx, "sports_type": best["sports_type"],
                "starts_in_s": round(float(best["start_epoch"]) - at, 0),
                "graded_contracts": len({r["market_slug"] for r in legs}),
                "source": SOURCE_CATALOGUE}
        (again if tried is not None else fresh).append(cand)
    # NEVER-ATTEMPTED FIXTURES FIRST, soonest first (their labels arrive
    # soonest); fixtures whose refusal has aged out after them.
    out["candidates"] = (fresh + again)[:int(limit)]
    out["eligible_fixtures"] = len(fresh) + len(again)
    out["not_offered_for_limit"] = max(0, len(fresh) + len(again)
                                       - int(limit))
    return dict(out, ok=True, refusal=None)


# ═════════════════════════════════════════════════════════════════════
# 5 · THE VENUE READS A PASS MAKES, COUNTED AND BOUNDED
# ═════════════════════════════════════════════════════════════════════

#: Logical book reads one pass may make (each up to
#: `venue_sdk.BOOK_READ_MAX_DISPATCHES` requests). A read past it is refused
#: by name, without a request, so a fixture with many siblings cannot spend
#: the lane's share of the venue budget.
BOOK_READS_PER_PASS = 48
#: Seconds of the pass reserved for labelling: observations stop starting
#: reads this long before the deadline, so labels are always read.
LABEL_RESERVE_S = 20.0

R_READ_BUDGET = "THE_PASS_BOOK_READ_BUDGET_IS_SPENT"
R_PASS_DEADLINE = "THE_PASS_DEADLINE_FOR_OBSERVATIONS_HAS_PASSED"


def metered(fn, *args):
    """Call a blocking venue reader under its own request-gate read id, in
    THIS thread, and return (result, dispatches). Dispatches are None when
    the gate cannot say."""
    from . import venue_request_gate as grt

    rid = grt.begin_read(slug=str(args[0]) if args else None)
    grt.bind_read(rid)
    try:
        got = fn(*args)
    finally:
        grt.bind_read(None)
        st = grt.end_read(rid)
    return got, (st or {}).get("dispatched")


class PassUsage:
    """Every venue read one observation pass made, by kind, with the
    requests they dispatched where the gate counted them."""

    def __init__(self, *, book_budget: int, t0: float, obs_deadline_s: float):
        self.book_budget = int(book_budget)
        self.t0 = t0
        self.obs_deadline_s = float(obs_deadline_s)
        self.c = {"book_reads": 0, "book_dispatches": 0,
                  "book_dispatches_unknown": 0, "book_refused_for_budget": 0,
                  "book_refused_for_deadline": 0, "book_cache_hits": 0,
                  "rules_reads": 0, "rules_cache_hits": 0,
                  "rules_dispatches": 0, "rules_dispatches_unknown": 0,
                  "settlement_reads": 0, "settlement_dispatches": 0,
                  "settlement_dispatches_unknown": 0}

    def _add(self, key: str, n) -> None:
        if n is None:
            self.c[key + "_unknown"] += 1
        else:
            self.c[key] += int(n)

    def quoter(self, inner):
        async def q(slug, side):
            if time.monotonic() - self.t0 > self.obs_deadline_s:
                self.c["book_refused_for_deadline"] += 1
                return {"ok": False, "refusal": R_PASS_DEADLINE}
            if self.c["book_reads"] >= self.book_budget:
                self.c["book_refused_for_budget"] += 1
                return {"ok": False, "refusal": R_READ_BUDGET}
            got = dict(await inner(slug, side) or {})
            if got.get("book_from_pass_cache"):
                self.c["book_cache_hits"] += 1
            else:
                self.c["book_reads"] += 1
                self._add("book_dispatches", got.get("dispatches"))
            return got
        return q

    def prose_reader(self, inner):
        async def p(slug):
            got = dict(await inner(slug) or {})
            if got.get("from_cache"):
                self.c["rules_cache_hits"] += 1
            else:
                self.c["rules_reads"] += 1
                self._add("rules_dispatches", got.get("dispatches"))
            return got
        return p

    def settlement_reader(self, inner):
        def s(slug):
            got, n = metered(inner, slug)
            self.c["settlement_reads"] += 1
            self._add("settlement_dispatches", n)
            return got
        return s

    def report(self) -> dict:
        from . import venue_sdk

        per_book = int(getattr(venue_sdk, "BOOK_READ_MAX_DISPATCHES", 2))
        c = dict(self.c)
        c["requests_counted"] = (c["book_dispatches"] + c["rules_dispatches"]
                                 + c["settlement_dispatches"])
        c["reads_whose_requests_were_not_counted"] = (
            c["book_dispatches_unknown"] + c["rules_dispatches_unknown"]
            + c["settlement_dispatches_unknown"])
        c["bound"] = {
            "book_reads_per_pass": self.book_budget,
            "book_requests_per_read_at_most": per_book,
            "settlement_pairs_per_pass": LABEL_READS_PER_PASS
            + RECHECKS_PER_PASS,
            "rules_reads": "at most one per contract per hour (cached)",
            "passes": "at most one per scheduled cycle"}
        c["scope"] = ("THIS PASS ONLY, counted at the request gate per "
                      "logical read; not process totals")
        return c


# ═════════════════════════════════════════════════════════════════════
# 6 · THE SCHEDULED PASS
# ═════════════════════════════════════════════════════════════════════

#: How many passes have run in this process: which candidates a pass
#: observes rotates with it, so a stable candidate order does not observe the
#: same few forever.
_PASSES = [0]
#: The wall-clock budget one pass may spend inside the cycle. Reads already
#: started finish; nothing new starts after it.
PASS_BUDGET_S = 60.0
#: Observation-sourced CANDIDATE models scored per pass, across every key
#: below. Scoring reads only the database; it records the evaluation and
#: approves nothing.
EVALUATIONS_PER_PASS = 2
#: The registry keys observations train: P(both win) and P(hedge wins |
#: primary outcome). Named here rather than imported at module load so this
#: module keeps its lazy import of the registry.
OBSERVATION_MODEL_KEYS = ("funded_pair_middle_region",
                          "funded_pair_hedge_given_primary")


def _candidate_list(candidates, catalogue) -> list:
    """Entry-derived and catalogue candidates, interleaved, each tagged with
    where it came from."""
    ent = []
    for c in candidates or ():
        if isinstance(c, dict):
            extra = dict(c)
            slug, side = extra.get("us_market_slug"), extra.get("side")
        else:
            extra = {}
            slug, side = c[0], c[1]
        ent.append(dict(extra, us_market_slug=slug, side=side,
                        source=extra.get("source") or SOURCE_ENTRY))
    cat = [dict(c, source=c.get("source") or SOURCE_CATALOGUE)
           for c in catalogue or ()]
    merged = []
    for i in range(max(len(ent), len(cat))):
        if i < len(ent):
            merged.append(ent[i])
        if i < len(cat):
            merged.append(cat[i])
    return merged


async def _evaluate_observation_candidates(conn, *, now: float,
                                           limit: int) -> dict:
    """SCORE OBSERVATION-SOURCED CANDIDATES WITHOUT A FUNDED ACCOUNT.

    `scheduled_learning_pass` evaluates candidates only inside funded
    servicing, which needs a bound account; with none bound, a candidate fit
    on observations was generated and never scored, so its evaluation stayed
    empty and `promote` could only refuse it as unevaluated. This scores the
    least recently scored observation-sourced candidates through the SAME
    `evaluate` (prospective and retrospective cohorts, event-balanced,
    contamination check) and records the result. It promotes nothing: the
    scores are evidence for a named approver, never an approval."""
    from . import bettor_funded_model as FMD

    out: dict[str, Any] = {"scored": [], "promoted_anything": False,
                           "model_keys": list(OBSERVATION_MODEL_KEYS)}
    try:
        if not await FMD.has_schema(conn):
            return dict(out, ok=False, refusal=FMD.R_SCHEMA_UNAVAILABLE)
        # BOTH KEYS IN ONE QUEUE, LEAST RECENTLY SCORED FIRST (never-scored
        # before all), under one bound: a backlog of one key's candidates
        # cannot starve the other's, because a scored candidate moves to the
        # back whichever key it has.
        rows = await conn.fetch(
            "SELECT model_id, model_key FROM bettor_funded_models "
            " WHERE state=$1 AND model_key = ANY($2::text[]) "
            "   AND training_provenance->>'source' = $3 "
            " ORDER BY (evaluation->>'evaluated_at')::float8 NULLS FIRST, "
            "          created_at DESC, model_id LIMIT $4",
            FMD.STATE_CANDIDATE, list(OBSERVATION_MODEL_KEYS),
            FMD.SOURCE_OBSERVATIONS, int(limit))
        waiting = {r["model_key"]: int(r["n"]) for r in await conn.fetch(
            "SELECT model_key, count(*) AS n FROM bettor_funded_models "
            " WHERE state=$1 AND model_key = ANY($2::text[]) "
            "   AND training_provenance->>'source' = $3 GROUP BY model_key",
            FMD.STATE_CANDIDATE, list(OBSERVATION_MODEL_KEYS),
            FMD.SOURCE_OBSERVATIONS)}
        out["candidates_waiting_by_key"] = {
            k: waiting.get(k, 0) for k in OBSERVATION_MODEL_KEYS}
        out["candidates_waiting"] = sum(waiting.values())
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal="OBSERVATION_CANDIDATES_UNREADABLE",
                    error=type(exc).__name__)
    for r in rows:
        try:
            ev = await FMD.evaluate(conn, model_id=r["model_id"],
                                    account_id=None, now=now)
        except Exception as exc:                                # noqa: BLE001
            ev = {"ok": False, "refusal": "EVALUATION_RAISED",
                  "error": type(exc).__name__}
        doc = ev.get("evaluation") or {}
        pros = doc.get(FMD.EVIDENCE_PROSPECTIVE) or {}
        retro = doc.get(FMD.EVIDENCE_RETROSPECTIVE) or {}
        base = ((pros.get("report") or {}).get("baseline") or {})
        out["scored"].append({
            "model_id": r["model_id"], "model_key": r["model_key"],
            "ok": ev.get("ok"),
            "refusal": ev.get("refusal"),
            "prospective_events": pros.get("n_events"),
            "prospective_log_loss": pros.get("log_loss"),
            "prospective_baseline_log_loss": base.get("log_loss"),
            "retrospective_out_of_sample_events": retro.get("n_events"),
            "retrospective_out_of_sample_log_loss": retro.get("log_loss"),
            "contamination": (doc.get("contamination") or {}).get("verdict"),
            "required_events": FMD.MIN_EVALUATION_EVENTS})
    # WITHDRAWAL FOR EVERY KEY THE OBSERVATIONS TRAIN. Retiring only removes
    # pricing authority; a correction to a training observation must reach
    # whichever key's approved model rested on it.
    out["withdraw_by_key"] = {}
    for key in OBSERVATION_MODEL_KEYS:
        try:
            w = await FMD.withdraw_invalidated(conn, model_key=key)
        except Exception as exc:                                # noqa: BLE001
            w = {"ok": False, "refusal": "WITHDRAWAL_RAISED",
                 "error": type(exc).__name__}
        out["withdraw_by_key"][key] = w
    # KEY_MIDDLE's, under its established name, for existing readers.
    out["withdraw"] = out["withdraw_by_key"][FMD.KEY_MIDDLE]
    return dict(out, ok=True, refusal=None)


def attempt_outcome(got: dict) -> str:
    """One name for what happened to an attempted candidate."""
    written = [r for r in got.get("recorded") or [] if r.get("written")]
    if written:
        return "RECORDED"
    if got.get("error"):
        return "ERROR"
    if got.get("refusal") == R_NOTHING_ADMITTED:
        return "NOTHING_ADMITTED"
    if got.get("refusal"):
        return "REFUSED"
    if got.get("recorded"):
        return "ALREADY_RECORDED_THIS_BUCKET"
    return "ADMITTED_BUT_NOTHING_RECORDABLE"


async def _has_attempt_ledger(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('bettor_pair_observation_attempts')"
        ) is not None
    except Exception:                                           # noqa: BLE001
        return False


async def _ledger_attempt(conn, *, pass_id: str, cand: dict, got: dict,
                          started: float, finished: float,
                          reads: dict) -> bool:
    """One row per attempted candidate, whatever happened. False if the
    write failed; the pass continues either way."""
    try:
        detail = {k: got.get(k) for k in (
            "quote_refusal", "held_refusal", "discovery_refusal", "examined",
            "admitted", "skipped_unpriced_second_leg", "error")}
        detail["recorded"] = [r.get("observation_id")
                              for r in got.get("recorded") or []]
        written = sum(1 for r in got.get("recorded") or [] if r.get("written"))
        await conn.execute(
            "INSERT INTO bettor_pair_observation_attempts (pass_id, "
            " attempted_at, finished_at, candidate_source, us_market_slug, "
            " side, fixture, outcome, refusal, observations_written, detail, "
            " venue_reads) VALUES ($1, to_timestamp($2), to_timestamp($3), "
            " $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb)",
            pass_id, float(started), float(finished),
            str(cand.get("source") or SOURCE_ENTRY),
            str(cand.get("us_market_slug")), str(cand.get("side")),
            cand.get("fixture") or got.get("fixture"), attempt_outcome(got),
            got.get("refusal") or got.get("error"), int(written),
            json.dumps(detail, default=str), json.dumps(reads, default=str))
        return True
    except Exception:                                           # noqa: BLE001
        return False


async def observation_pass(conn, *, candidates, quoter, prose_reader,
                           settlement_reader=None, now: float | None = None,
                           per_pass: int = CANDIDATES_PER_PASS,
                           budget_s: float = PASS_BUDGET_S,
                           catalogue=None,
                           book_budget: int = BOOK_READS_PER_PASS,
                           evaluations: int = EVALUATIONS_PER_PASS) -> dict:
    """ONE CYCLE'S OBSERVATION WORK: observe a few candidates, label what has
    settled, offer the registry a candidate fit on observations, and score
    the observation-sourced candidates. Bounded in candidates, book reads,
    pairs and wall-clock time; it never raises into the cycle.

    EVERY ATTEMPT IS ACCOUNTED FOR. Each attempted candidate comes back with
    its outcome and the exact refusal at every depth (the first leg's quote,
    its build, each sibling's, discovery's), and -- when migration 142 is
    present -- is appended to `bettor_pair_observation_attempts`. Each
    candidate offered and not attempted is counted by the reason. Every venue
    read the pass made is counted by kind, with the requests the gate saw."""
    from . import bettor_funded_model as FMD

    t0 = time.monotonic()
    at = float(now if now is not None else time.time())

    def _now() -> float:
        return at + (time.monotonic() - t0)

    obs_deadline = max(0.0, float(budget_s) - min(LABEL_RESERVE_S,
                                                  float(budget_s) / 3.0))
    usage = PassUsage(book_budget=book_budget, t0=t0,
                      obs_deadline_s=obs_deadline)
    pass_id = "opass:" + hashlib.sha256(
        ("%r|%d" % (at, _PASSES[0])).encode()).hexdigest()[:16]
    out: dict[str, Any] = {"version": VERSION, "at": at, "pass_id": pass_id,
                           "observed": [], "sent_anything": False,
                           "promoted_anything": False, "budget_s": budget_s,
                           "observation_deadline_s": obs_deadline,
                           "stopped_for_deadline": False}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    seen = set()
    todo = []
    for c in _candidate_list(candidates, catalogue):
        key = (c.get("us_market_slug"), c.get("side"))
        if not key[0] or key[1] not in (LONG, SHORT) or key in seen:
            continue
        seen.add(key)
        todo.append(c)
    out["candidates_offered"] = len(todo)
    out["candidates_offered_by_source"] = {}
    for c in todo:
        s = c["source"]
        out["candidates_offered_by_source"][s] = \
            out["candidates_offered_by_source"].get(s, 0) + 1
    if todo:
        k = (_PASSES[0] * max(1, int(per_pass))) % len(todo)
        todo = todo[k:] + todo[:k]
    _PASSES[0] += 1
    ledger = await _has_attempt_ledger(conn)
    out["attempt_ledger"] = {"present": ledger, "written": 0, "failed": 0}
    q_metered = usage.quoter(quoter) if quoter is not None else None
    p_metered = (usage.prose_reader(prose_reader)
                 if prose_reader is not None else None)
    outcomes: dict = {}
    refusals: dict = {}
    done = 0
    for cand in todo[:int(per_pass)]:
        if time.monotonic() - t0 > obs_deadline:
            out["stopped_for_deadline"] = True
            break
        slug, side = cand["us_market_slug"], cand["side"]
        before = dict(usage.c)
        started = _now()
        try:
            got = await observe_candidate(conn, us_market_slug=slug, side=side,
                                          quoter=q_metered,
                                          prose_reader=p_metered,
                                          now=started)
        except Exception as exc:                            # noqa: BLE001
            got = {"ok": False, "us_market_slug": slug, "side": side,
                   "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
        finished = _now()
        done += 1
        reads = {k: usage.c[k] - before[k] for k in usage.c
                 if usage.c[k] != before[k]}
        budget_limited = bool(reads.get("book_refused_for_budget")
                              or reads.get("book_refused_for_deadline"))
        outcome = attempt_outcome(got)
        refusal = got.get("refusal") or got.get("error")
        deeper = (got.get("quote_refusal") or got.get("held_refusal")
                  or got.get("discovery_refusal"))
        name = "%s%s" % (refusal, (" <- %s" % deeper) if deeper else "") \
            if refusal else None
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if name:
            refusals[name] = refusals.get(name, 0) + 1
        entry = {k: got.get(k) for k in
                 ("us_market_slug", "side", "ok", "refusal", "quote_refusal",
                  "held_refusal", "discovery_refusal", "examined",
                  "admitted", "second_legs_refused",
                  "skipped_unpriced_second_leg", "recorded", "error")}
        entry.update(source=cand["source"],
                     fixture=cand.get("fixture") or got.get("fixture"),
                     outcome=outcome, budget_limited=budget_limited,
                     elapsed_s=round(finished - started, 3), reads=reads)
        out["observed"].append(entry)
        if outcome not in ("RECORDED", "ALREADY_RECORDED_THIS_BUCKET") \
                and not budget_limited:
            note_attempt(entry["fixture"], at=finished, refusal=name
                         or outcome)
        if ledger:
            ok = await _ledger_attempt(conn, pass_id=pass_id, cand=cand,
                                       got=dict(got, fixture=entry["fixture"]),
                                       started=started, finished=finished,
                                       reads=reads)
            out["attempt_ledger"]["written" if ok else "failed"] += 1
    out["attempted"] = done
    out["outcomes"] = outcomes
    out["refusals"] = refusals
    out["observations_written"] = sum(
        1 for e in out["observed"] for r in e.get("recorded") or []
        if r.get("written"))
    out["not_observed_this_pass"] = max(0, len(todo) - done)
    cap = min(len(todo), max(0, int(per_pass)))
    out["not_attempted"] = {"LIMIT_PER_PASS": len(todo) - cap,
                            "PASS_DEADLINE": cap - done}
    reader = settlement_reader or _production_settlement
    try:
        out["labels"] = await label_pending(
            conn, settlement_reader=usage.settlement_reader(reader),
            now=_now(),
            deadline_s=max(0.0, budget_s - (time.monotonic() - t0)))
    except Exception as exc:                                # noqa: BLE001
        out["labels"] = {"ok": False, "error": type(exc).__name__}
    # THE FIT IS DECLARED THROUGH AN INSTANT AFTER THE LABELS WERE READ, so
    # every outcome it trains on was known here by its declared window.
    try:
        out["generate"] = await FMD.generate_candidate(conn, now=_now(),
                                                       source=SOURCE)
    except Exception as exc:                                # noqa: BLE001
        out["generate"] = {"ok": False, "refusal": "GENERATION_RAISED",
                           "error": type(exc).__name__}
    # AND THE CONDITIONAL THE PAYOUT-STATE DISTRIBUTION NEEDS, through the
    # same governed generator: same windows, same event bar, same refit rule,
    # a CANDIDATE and nothing more.
    try:
        out["generate_conditional"] = await FMD.generate_candidate(
            conn, now=_now(), source=SOURCE,
            model_key=FMD.KEY_HEDGE_GIVEN_PRIMARY)
    except Exception as exc:                                # noqa: BLE001
        out["generate_conditional"] = {
            "ok": False, "refusal": "GENERATION_RAISED",
            "model_key": FMD.KEY_HEDGE_GIVEN_PRIMARY,
            "error": type(exc).__name__}
    # AND SCORED, WITHOUT A FUNDED ACCOUNT. Evidence, not approval.
    out["evaluate"] = await _evaluate_observation_candidates(
        conn, now=_now(), limit=int(evaluations))
    out["venue_usage"] = usage.report()
    out["elapsed_s"] = round(time.monotonic() - t0, 3)
    return dict(out, ok=True)
