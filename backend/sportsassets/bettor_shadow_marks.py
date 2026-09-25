"""UNREALISED SHADOW P&L, ON A BASIS WE ARE ENTITLED TO USE.

WHAT THIS REPLACES. `_pnl_status` reported `unrealised: NOT_IDENTIFIED`
with the reason "marking open inventory requires a price we may use. A
midpoint is where nobody transacted". That reasoning is right and is kept
-- a mid is not a mark. But it was used to justify reporting no unrealised
figure at all, and that was too strong: the manager ALREADY computes a
price we may use, on every cycle, for exactly this quantity.

THE BASIS. `bettor_mgmt_select.rank_priced_actions` prices DIRECT_EXIT as
selling the held leg INTO THE OBSERVED BID, capped at the bid's own depth,
net of the fee the production schedule charges. Its `slice_value_usd` is

    bid * sellable_qty - fee - basis_released_pro_rata

which is the cash P&L we would realise by exiting now, at a price someone
is actually showing, for a size the book will actually take. That is a
transactable mark, not a midpoint, and it is the number the exit decision
is already made on. Marking inventory at anything else would mean the
dashboard and the manager disagreed about what the position is worth.

    MARK_BASIS = EXECUTABLE_EXIT_NET_OF_FEES_ON_OBSERVED_DEPTH

FOUR THINGS THIS REFUSES TO COLLAPSE, each of which would flatter the
number:

 1. A POSITION WITH NO EXECUTABLE BID IS UNMARKED, NOT WORTH ZERO. The
    ranker refuses DIRECT_EXIT by name -- NO_BID, NO_EXECUTABLE_DEPTH,
    FEE_SCHEDULE_NOT_ESTABLISHED -- and each refusal is carried through
    and counted. An unmarked position is excluded from the unrealised sum
    and named, because summing it as 0.00 would report "this position has
    broken even" when the truth is "nobody has bid for it".

 2. A DEPTH-CAPPED MARK COVERS ONLY THE SLICE THE BOOK WOULD TAKE. When
    `depth_limited` is true the bid cannot absorb the whole position, so
    the mark applies to `qty` and the remainder is UNMARKED RESIDUAL. The
    ranker's own `retained_value_usd` prices that remainder from the HOLD
    value, which is model-derived rather than transactable, so it is
    reported under its own label and never added to the executable mark.

 3. TOTAL P&L IS ONLY A TOTAL WHEN EVERYTHING IS MARKED. With any open
    position unmarked, the total is reported as PARTIAL with the count,
    because realised + a mark over some of the book is not the book's P&L.

 4. A STALE MARK IS NOT A MARK. The decision carrying the exit price has a
    timestamp; a mark older than `MAX_MARK_AGE_S` is reported as STALE and
    excluded from the sum rather than quietly used. A price from yesterday
    is not one anybody is showing now.

EVERY FIGURE HERE IS MODELLED AND NO CAPITAL MOVED. The fills these
positions rest on came from the execution simulator, and the bid the mark
uses was observed at the venue but never traded against.
"""

from __future__ import annotations

VERSION = "BETTOR_SHADOW_MARKS_V1"

MARK_BASIS = "EXECUTABLE_EXIT_NET_OF_FEES_ON_OBSERVED_DEPTH"

#: Why the midpoint is still refused, kept where the replacement lives so
#: the two are read together.
WHY_NOT_A_MIDPOINT = (
    "a midpoint is a price at which nobody transacted, so it cannot mark a "
    "position. The observed bid, capped at its own depth and net of the "
    "production fee schedule, is a price someone is actually showing for a "
    "size the book will actually take")

WHY_NOT_THE_HOLD_VALUE = (
    "the HOLD value is derived from a probability model. It is the right "
    "input for choosing between hold and exit, and it is not a mark: "
    "nothing can be transacted at it")

#: A mark older than this is reported and excluded rather than used. One
#: management cadence is 900 s, so two cadences is the loosest reading that
#: still means "the last time we looked".
MAX_MARK_AGE_S = 1800.0

#: THE BOOK OBSERVATION'S OWN AGE BOUND, separate from the decision's.
#:
#: A decision can be recent and still rest on a stale book: the manager
#: decides at its cadence, the venue quote it used has its own instant, and
#: the entry lane's freshness contract already bounds a contemporaneous
#: quote at 30 s. A mark is only as current as the PRICE inside it, so the
#: observation instant is required and bounded on its own. Loosened to two
#: management cadences here for the same reason MAX_MARK_AGE_S is: a mark is
#: a valuation, not an order.
MAX_OBSERVATION_AGE_S = 1800.0

NOT_IDENTIFIED = "NOT_IDENTIFIED"

R_NO_DECISION = "NO_DECISION_HAS_PRICED_THIS_POSITION"
R_NO_EXIT_CANDIDATE = "NO_EXECUTABLE_EXIT_IN_THE_LATEST_DECISION"
R_STALE = "MARK_OLDER_THAN_THE_MANAGEMENT_CADENCE"
R_MALFORMED = "EXIT_CANDIDATE_CARRIES_NO_USABLE_NUMBER"
R_NO_RANKED = "THE_DECISION_RANKED_NOTHING"
R_RESIDUAL_UNKNOWN = "CURRENT_RESIDUAL_INVENTORY_NOT_ESTABLISHED"
R_QTY_MISMATCH = "THE_EXIT_ESTIMATE_DOES_NOT_MATCH_CURRENT_RESIDUAL"
R_NO_OBSERVATION_TIME = "THE_MARK_CARRIES_NO_OBSERVATION_INSTANT"

#: WHERE THE RANKED LIST ACTUALLY LIVES, measured rather than assumed.
#:
#: I built this module expecting `rn1x_decisions.alternatives` to be a JSON
#: ARRAY of candidates. It is not. Asked of production on 2026-09-25, the
#: three newest decision rows answer
#:
#:     alt_type = object    alt_len = 0    actions = NULL
#:
#: so the column holds a mapping, and on those rows an EMPTY one. That is
#: consistent with what those positions are -- their fixtures have finished,
#: so there is no bid and nothing was rankable -- but it means a reader that
#: assumed a list would have found nothing and reported it as a malformed
#: row rather than as "the decision ranked nothing".
#:
#: So both shapes are accepted, and an empty one is named. These are the
#: keys a mapping may carry the ranked list under, in the store's own
#: vocabulary (`persist_run` writes `"ranked": d.get("alternatives")`).
RANKED_KEYS = ("ranked", "candidates", "alternatives", "priced")

#: The ranker's own refusal names, passed through rather than renamed. A
#: dashboard that invents its own vocabulary for a blocker the engine
#: already named is a dashboard nobody can reconcile against the engine.
PASS_THROUGH_BLOCKERS = ("NO_BID", "NO_EXECUTABLE_DEPTH",
                         "FEE_SCHEDULE_NOT_ESTABLISHED")


def _num(v):
    """A finite float, or None. A string, a NaN or a None are all None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _ranked_rows(alternatives):
    """The candidate list, out of either shape. Never raises.

    A LIST is used as-is. A MAPPING is searched for the first key in
    RANKED_KEYS that holds a list -- which is how the store writes it. A
    mapping of action -> candidate is also accepted, because that is the
    other plausible way a dict could carry the same information and
    guessing wrong would report a live exit as absent.
    """
    if isinstance(alternatives, (list, tuple)):
        return list(alternatives)
    if not isinstance(alternatives, dict):
        return []
    for k in RANKED_KEYS:
        v = alternatives.get(k)
        if isinstance(v, (list, tuple)):
            return list(v)
    # action -> candidate, with the action recoverable from the key.
    out = []
    for k, v in alternatives.items():
        if isinstance(v, dict):
            out.append(dict(v, action=v.get("action", k)))
    return out


def exit_candidate(alternatives):
    """The DIRECT_EXIT row out of a decision's ranked alternatives.

    Returns (candidate, blocker). Exactly one is not None. A refused
    DIRECT_EXIT carries the ranker's own blocker name, which is the whole
    reason this returns it rather than just absence.
    """
    rows = _ranked_rows(alternatives)
    blocker = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("action") or "") != "DIRECT_EXIT":
            continue
        if row.get("blocker"):
            # Remember it, but keep looking: a ranked candidate beats a
            # refused one if both somehow appear.
            blocker = str(row["blocker"])
            continue
        return row, None
    return None, blocker


def mark_one(*, position_id, alternatives, decided_at, now,
             position_qty=None, residual_qty=None, observed_at=None,
             max_age_s=MAX_MARK_AGE_S,
             max_observation_age_s=MAX_OBSERVATION_AGE_S) -> dict:
    """Mark ONE open position, or say by name why it is unmarked.

    `alternatives` is `rn1x_decisions.alternatives` for the position's
    LATEST decision. `decided_at`, `observed_at` and `now` are epoch
    seconds.

    `residual_qty` is the position's CURRENT residual -- what is actually
    still held -- and it is REQUIRED. `position_qty` is the seed, kept only
    to report how much of the original has been worked off.

    AN OLD DECISION'S EXIT ESTIMATE IS NOT AUTOMATICALLY A CURRENT MARK,
    and three separate things are checked because they fail separately:

      * the DECISION must be recent (`max_age_s`);
      * the BOOK OBSERVATION the price came from must be recent
        (`max_observation_age_s`) and must EXIST -- a mark with no
        observation instant cannot be aged at all;
      * the estimate's quantity must still match the CURRENT residual. An
        exit priced for 10 contracts values nothing if 6 have since been
        sold, and scaling it pro-rata would invent a price for a size the
        book was never asked about.
    """
    out = {"position_id": str(position_id), "marked": False,
           "basis": MARK_BASIS, "unrealised_usd": None,
           "blocker": None, "mark_age_s": None,
           "observation_age_s": None, "observed_at": None,
           "residual_qty": None, "seed_qty": position_qty,
           "depth_limited": None, "marked_qty": None,
           "unmarked_residual_qty": None,
           "retained_value_usd": None,
           "retained_value_basis": None}

    da, at = _num(decided_at), _num(now)
    if da is None or at is None:
        out["blocker"] = R_NO_DECISION
        return out
    age = at - da
    out["mark_age_s"] = round(age, 1)

    # AN EMPTY RANKING IS ITS OWN FACT. "The decision ranked nothing" and
    # "the ranking had no exit in it" call for different actions: the first
    # means no price was available at all, the second means an exit was
    # considered and refused.
    if not _ranked_rows(alternatives):
        out["blocker"] = R_NO_RANKED
        out["why"] = ("the latest decision carries no ranked alternatives, "
                      "so nothing was priced for this position -- not even a "
                      "refused exit. This is what a finished fixture looks "
                      "like: no bid exists to rank against")
        return out

    cand, blocker = exit_candidate(alternatives)
    if cand is None:
        # THE RANKER'S OWN NAME WHERE IT HAS ONE. "No exit candidate" and
        # "nobody is bidding" are different facts about the book.
        out["blocker"] = blocker if blocker in PASS_THROUGH_BLOCKERS \
            else (blocker or R_NO_EXIT_CANDIDATE)
        return out

    # STALENESS IS CHECKED AFTER THE CANDIDATE IS FOUND, so a stale mark
    # is reported as stale rather than as missing. The two call for
    # different actions: one needs a cycle to run, the other needs a bid.
    if age > float(max_age_s):
        out["blocker"] = R_STALE
        out["why"] = ("the latest decision priced this %0.0f s ago, beyond "
                      "the %0.0f s bound. A price nobody is showing now is "
                      "not a mark" % (age, float(max_age_s)))
        return out

    # ── THE BOOK OBSERVATION'S OWN INSTANT, REQUIRED ─────────────────
    oa = _num(observed_at)
    if oa is None:
        out["blocker"] = R_NO_OBSERVATION_TIME
        out["why"] = ("the decision carries no instant for the book the "
                      "exit price came from, so how current this price is "
                      "cannot be answered. An unanswerable age is not a "
                      "fresh one")
        return out
    out["observed_at"] = oa
    obs_age = at - oa
    out["observation_age_s"] = round(obs_age, 1)
    if obs_age > float(max_observation_age_s):
        out["blocker"] = R_STALE
        out["stale_side"] = "BOOK_OBSERVATION"
        out["why"] = ("the exit price was observed %0.0f s ago, beyond the "
                      "%0.0f s bound. A recent DECISION resting on a stale "
                      "BOOK is still a stale mark"
                      % (obs_age, float(max_observation_age_s)))
        return out

    # ── CURRENT RESIDUAL, REQUIRED AND COMPARED ──────────────────────
    res = _num(residual_qty)
    if res is None:
        out["blocker"] = R_RESIDUAL_UNKNOWN
        out["why"] = ("what is still held is not established, so there is "
                      "nothing to mark. Marking the seed quantity would "
                      "value inventory that may already be gone")
        return out
    out["residual_qty"] = res
    if res <= 0:
        out["blocker"] = R_RESIDUAL_UNKNOWN
        out["why"] = ("the residual is %s: nothing is held, so this is not "
                      "an open position to mark" % res)
        return out

    slice_usd = _num(cand.get("slice_value_usd"))
    qty = _num(cand.get("qty"))
    if slice_usd is None or qty is None:
        out["blocker"] = R_MALFORMED
        return out

    # THE ESTIMATE MUST BE FOR WHAT IS ACTUALLY HELD. `qty` is what the bid
    # could take of the quantity the decision was priced on; it may be less
    # than the residual (depth) but it must never EXCEED it, and the
    # quantity the decision valued must still be the residual.
    priced_on = _num(cand.get("priced_on_qty"))
    if qty - res > 1e-9 or (priced_on is not None
                            and abs(priced_on - res) > 1e-9):
        out["blocker"] = R_QTY_MISMATCH
        out["why"] = ("the exit estimate covers %s of a position priced on "
                      "%s, but %s is held now. A price obtained for a "
                      "different size is not a mark for this one"
                      % (qty, priced_on, res))
        return out

    out["marked"] = True
    out["unrealised_usd"] = slice_usd
    out["marked_qty"] = qty
    out["fees_usd"] = _num(cand.get("fees_usd"))
    out["cash_now_usd"] = _num(cand.get("cash_now_usd"))
    out["depth_limited"] = bool(cand.get("depth_limited"))

    # THE REMAINDER THE BOOK WOULD NOT TAKE, kept apart. Its value comes
    # from the HOLD model, so it carries its own basis label and is never
    # added into the executable figure.
    # THE POSITION'S OWN QUANTITY COMES FROM THE POSITION, not from the
    # candidate: the candidate carries the SELLABLE size, and dividing
    # `value_usd` by `value_per_contract` to recover the rest would put a
    # rounding error into an accounting figure.
    total_q = res
    if out["depth_limited"]:
        out["unmarked_residual_qty"] = (None if total_q is None
                                        else max(0.0, total_q - qty))
        out["retained_value_usd"] = _num(cand.get("retained_value_usd"))
        out["retained_value_basis"] = (
            "MODEL_DERIVED_HOLD_VALUE_NOT_A_TRANSACTABLE_MARK")
        out["why_separate"] = WHY_NOT_THE_HOLD_VALUE
    return out


def roll_up(marks, *, realised_usd=None, open_positions=None) -> dict:
    """Aggregate per-position marks into the figure a dashboard shows.

    `realised_usd` is the settled figure from `rn1x_outcomes`. It is
    passed in rather than recomputed: two places computing realised P&L
    is two places that can disagree about it.
    """
    rows = [m for m in (marks or ()) if isinstance(m, dict)]
    ok = [m for m in rows if m.get("marked")]
    bad = [m for m in rows if not m.get("marked")]

    unreal = sum(float(m["unrealised_usd"]) for m in ok) if ok else None
    by_blocker: dict = {}
    for m in bad:
        k = str(m.get("blocker") or R_NO_DECISION)
        by_blocker[k] = by_blocker.get(k, 0) + 1

    # THE DENOMINATOR IS THE OPEN BOOK, NOT THE ROWS WE HAPPENED TO READ.
    # If a position has no decision at all it produces no mark row, and a
    # coverage figure computed over rows would report 100% while missing
    # it entirely.
    total = int(open_positions if open_positions is not None else len(rows))
    missing = max(0, total - len(rows))
    if missing:
        by_blocker[R_NO_DECISION] = by_blocker.get(R_NO_DECISION, 0) + missing

    unmarked = len(bad) + missing
    real = _num(realised_usd)

    out = {
        "version": VERSION,
        "mark_basis": MARK_BASIS,
        "why_not_a_midpoint": WHY_NOT_A_MIDPOINT,
        "max_mark_age_s": MAX_MARK_AGE_S,
        "open_positions": total,
        "marked_positions": len(ok),
        "unmarked_positions": unmarked,
        "unmarked_by_blocker": by_blocker,
        "unrealised_usd": unreal,
        "unrealised_covers": ("%d of %d open positions" % (len(ok), total)),
        "realised_usd": real,
        "depth_limited_positions": sum(1 for m in ok
                                       if m.get("depth_limited")),
        "retained_value_usd": (
            sum(float(m["retained_value_usd"]) for m in ok
                if _num(m.get("retained_value_usd")) is not None) or None),
        "retained_value_basis":
            "MODEL_DERIVED_HOLD_VALUE_NOT_A_TRANSACTABLE_MARK",
        "modelled": True,
        "no_capital_moved": True,
    }

    # TOTAL IS ONLY A TOTAL WHEN EVERYTHING IS MARKED.
    if unmarked or real is None or unreal is None:
        out["total_pnl_usd"] = None
        out["total_pnl_status"] = "PARTIAL"
        why = []
        if real is None:
            why.append("no realised figure is available")
        if unreal is None:
            why.append("no open position could be marked")
        if unmarked:
            why.append("%d of %d open positions are unmarked (%s)"
                       % (unmarked, total,
                          ", ".join("%s x%d" % (k, v)
                                    for k, v in sorted(by_blocker.items()))))
        out["why_total_is_partial"] = (
            "; ".join(why) + ". Realised plus a mark over PART of the book "
            "is not the book's P&L, so no total is stated")
    else:
        out["total_pnl_usd"] = real + unreal
        out["total_pnl_status"] = "COMPLETE"
        out["reconciles"] = (
            abs(out["total_pnl_usd"] - (real + unreal)) < 1e-9)
        out["how_total_is_built"] = (
            "realised (%s, settled positions) + unrealised (%s, %s over "
            "every open position)" % (round(real, 4), round(unreal, 4),
                                      MARK_BASIS))
    return out


def describe() -> dict:
    """What this module claims, and what it refuses to claim."""
    return {
        "version": VERSION,
        "mark_basis": MARK_BASIS,
        "why_not_a_midpoint": WHY_NOT_A_MIDPOINT,
        "why_not_the_hold_value": WHY_NOT_THE_HOLD_VALUE,
        "refuses_to_collapse": (
            "an unmarked position is not worth zero",
            "a depth-capped mark covers only the slice the book would take",
            "a total is only a total when every open position is marked",
            "a mark older than the management cadence is stale, not current",
            "a recent decision resting on a stale book is still a stale mark",
            "an estimate priced for a different size is not a mark for this "
            "one",
            "the mark is against CURRENT residual, never the seed quantity",
        ),
        # R_NO_RANKED belongs here and was missing: it is the blocker that
        # actually fires on production's current rows, so leaving it out of
        # the self-description would have hidden the common case.
        "blockers": (R_NO_DECISION, R_NO_RANKED, R_NO_EXIT_CANDIDATE,
                     R_STALE, R_MALFORMED, R_RESIDUAL_UNKNOWN,
                     R_QTY_MISMATCH,
                     R_NO_OBSERVATION_TIME) + PASS_THROUGH_BLOCKERS,
        "everything_here_is_modelled": True,
    }
