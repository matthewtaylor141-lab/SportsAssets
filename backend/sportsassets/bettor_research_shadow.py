"""THE UNFUNDED RESEARCH-SHADOW LANE: one waiver, named, and nothing else.

WHAT THIS IS FOR. `MODEL_TRUST_DRIFT` is the entry lane's standing blocker
on creating inventory: `PINNACLE_DEVIG_V1` says of its own default method
"validate in shadow", that validation has not been made, and
`external_source_calibration` has zero rows. The gate is therefore
NOT_EVALUABLE and it blocks. `bettor_entry_execution` says of it, correctly,
that it "does not lift by argument".

This does not lift it by argument. It records an OWNER AUTHORISATION to
collect unfunded shadow evidence while the calibration stays explicitly
unmeasured, and it applies that authorisation to exactly one gate.

    THE GATE STILL READS NOT_EVALUABLE. `state_from_evidence` is not
    touched and never returns True for MODEL_TRUST_DRIFT. The calibration
    row stays absent. Nothing here writes, implies or simulates a
    measurement, and `calibration_evidence` remains the only thing that
    could produce one.

WHY THAT DISTINCTION IS THE WHOLE POINT. A `permitted: True` written into
the gate because the lane is "only shadow" would state that an unvalidated
external valuation may size a position. It may not. What is true instead is
narrower and is what this module says: an unvalidated valuation may size a
SIMULATED position, in a lane that cannot reach capital, so that the
records it produces can become the calibration evidence the gate is waiting
for. The lane accumulates its own key, which is the argument
`bettor_entry_execution` already makes for why the blocker is not a dead
end — this just lets it happen inside the position lifecycle instead of
only beside it.

── WHAT IS WAIVED, AND WHAT IS NOT ──────────────────────────────────

WAIVED, and only this:

    MODEL_TRUST_DRIFT   because the calibration is unmeasured

EVERY OTHER RAIL AND GATE STANDS, unchanged and still blocking:

    STALE_DATA                       freshness, both clocks
    UNRESOLVED_SETTLEMENT_SEMANTICS  the condition-to-payout comparison
    OUT_OF_DISTRIBUTION              the source's declared support
    every exposure rail              per-condition, per-event, combined
    every identity check             premap contract, payout binding,
                                     outcome-index cross-check
    every execution requirement      observed depth inside break-even,
                                     p_fill, sizing policy
    the economics                    net edge must still be positive

So a settlement comparison that is UNKNOWN still refuses, a stale quote
still refuses, and a candidate with no observed depth still refuses. This
waiver cannot make an entry happen on its own; it can only stop being the
reason one did not.

── IT CANNOT REACH CAPITAL ──────────────────────────────────────────

THE FUNDED GATE IS UNTOUCHED AND UNREACHABLE FROM HERE. This module
imports nothing from `execution_gate`, `calibration`, `calibration_store`
or `calibration_execute`, references no submit path, and the positions it
permits are written by `bettor_entry_inventory`, which models fills
through the execution simulator and has no venue client. `guarded_submit`
has no caller in this lane and a test asserts that this module names none.

The funded controls remain exactly as they were: `live_trading_paused` is
engaged, `CALIBRATION_WRITES_ENABLED` is False, and no submit route exists.
Nothing here changes any of them, and enabling this mode is not a step
towards enabling them.

── ABSENCE IS NOT PERMISSION ────────────────────────────────────────

The mode is off unless a control row says otherwise, matching every other
loop in this stack. An unreadable control row is OFF, not on: a database
that cannot answer has not authorised anything.
"""

from __future__ import annotations

VERSION = "UNFUNDED_RESEARCH_SHADOW_V1"

#: The control row. Absence is NOT permission.
CONTROL_KEY = "research_shadow_uncalibrated"

#: The provenance every position created under the waiver carries, so a
#: reader can never mistake one for an entry that cleared calibration.
#: Enumerated by `rn1x_provenance_declared` (migration 122).
PROVENANCE = "UNCALIBRATED_RESEARCH_SHADOW"

#: THE ONE GATE THIS MAY WAIVE. A frozenset, and the only member is the
#: calibration gate. Adding a second member is a decision somebody has to
#: make in this file, in a diff, rather than by passing a longer list.
WAIVABLE = frozenset({"MODEL_TRUST_DRIFT"})

#: Gates this must NEVER waive, asserted separately from WAIVABLE so the
#: two cannot drift into each other. If a name appears in both, `waive`
#: refuses outright rather than resolving the contradiction.
NEVER_WAIVABLE = frozenset({
    "STALE_DATA",
    "UNRESOLVED_SETTLEMENT_SEMANTICS",
    "OUT_OF_DISTRIBUTION",
})

R_NOT_AUTHORISED = "RESEARCH_SHADOW_NOT_AUTHORISED"
R_CONTROL_UNREADABLE = "RESEARCH_SHADOW_CONTROL_ROW_UNREADABLE"
R_CONTRADICTORY = "WAIVER_LIST_CONTRADICTS_ITSELF"
R_CALIBRATED = "CALIBRATION_IS_MEASURED_SO_NO_WAIVER_APPLIES"

AUTHORISED_BY = "OWNER_DIRECTIVE_2026-09-25_RESEARCH_SHADOW"

WHAT_THE_WAIVER_IS_NOT = (
    "this is not a calibration measurement, does not create one, and does "
    "not change what MODEL_TRUST_DRIFT reads. The gate stays NOT_EVALUABLE "
    "and external_source_calibration stays empty. It records that the owner "
    "authorised collecting UNFUNDED shadow evidence while that remains true")

WHY_IT_CANNOT_REACH_CAPITAL = (
    "positions permitted here are written by bettor_entry_inventory, which "
    "models fills through the execution simulator and holds no venue "
    "client. This module imports nothing from execution_gate, calibration, "
    "calibration_store or calibration_execute and names no submit path. The "
    "funded gate is unchanged: live_trading_paused engaged, "
    "CALIBRATION_WRITES_ENABLED False, no submit route in existence")


async def authorised(conn) -> dict:
    """Is the unfunded research lane authorised? Read, never assumed.

    An absent row is OFF. An unreadable row is OFF. Only an explicit
    `true` authorises, which is the same contract `ext_pinnacle_shadow`
    and `rn1x_shadow` use for their own control rows.
    """
    out = {"version": VERSION, "control_key": CONTROL_KEY,
           "authorised": False, "authorised_by": AUTHORISED_BY,
           "refusal": R_NOT_AUTHORISED,
           "what_this_is_not": WHAT_THE_WAIVER_IS_NOT,
           "why_it_cannot_reach_capital": WHY_IT_CANNOT_REACH_CAPITAL}
    try:
        row = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1", CONTROL_KEY)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, refusal=R_CONTROL_UNREADABLE,
                    error=type(exc).__name__,
                    why=("the control row could not be read, and a database "
                         "that cannot answer has not authorised anything"))
    if row is None:
        return dict(out, why=("no %s row exists. Absence is not permission"
                              % CONTROL_KEY))
    # The row is jsonb; `true` is the only authorisation. A string "true",
    # a 1, or anything else is NOT one -- the same discipline the funded
    # kill switch uses, where a non-boolean reads as paused.
    text = row if isinstance(row, str) else str(row)
    if text.strip().lower() != "true":
        return dict(out, why=("%s is %r, which is not an explicit true"
                              % (CONTROL_KEY, text[:40])))
    return dict(out, authorised=True, refusal=None,
                why=("%s is true, so the UNFUNDED research lane may create "
                     "simulated inventory while the calibration stays "
                     "explicitly unmeasured" % CONTROL_KEY))


def waive(state, *, authorised_flag, calibration=None) -> dict:
    """The gate map the risk engine should see, and the record of why.

    `state` is `state_from_evidence(...)["state"]` -- UNMODIFIED. This
    returns a COPY with the waived gate marked clear for the engine's
    purposes, plus the untouched original, so a reader can always see what
    the gate actually said.

    Returns {"state": <for the engine>, "gate_state_as_read": <original>,
             "waived": [...], "refusals": [...], ...}.
    """
    original = dict(state or {})
    out = {
        "version": VERSION,
        "gate_state_as_read": dict(original),
        "waived": [],
        "refusals": [],
        "authorised": bool(authorised_flag),
        "what_this_is_not": WHAT_THE_WAIVER_IS_NOT,
        "never_waivable": sorted(NEVER_WAIVABLE),
    }

    # A CONTRADICTORY LIST REFUSES RATHER THAN BEING RESOLVED. If somebody
    # adds a gate to both sets, the safe reading is not "the permissive one
    # wins" -- it is that nobody knows what was intended.
    overlap = WAIVABLE & NEVER_WAIVABLE
    if overlap:
        out["refusals"].append("%s: %s" % (R_CONTRADICTORY, sorted(overlap)))
        out["state"] = dict(original)
        return out

    if not authorised_flag:
        out["refusals"].append(R_NOT_AUTHORISED)
        out["state"] = dict(original)
        return out

    # A MEASURED CALIBRATION NEEDS NO WAIVER, and applying one anyway would
    # hide a real drift failure behind a research label. If the row exists
    # and says the source is OUT of tolerance, that is a genuine block and
    # this must not touch it.
    if (calibration or {}).get("measured"):
        out["refusals"].append(R_CALIBRATED)
        out["state"] = dict(original)
        out["why"] = ("a calibration measurement exists, so MODEL_TRUST_DRIFT "
                      "is answerable on its own evidence. Waiving it here "
                      "could only serve to hide a drift failure")
        return out

    state_out = dict(original)
    for name in sorted(WAIVABLE):
        if name not in state_out:
            # NOT PRESENT IS NOT WAIVED. A gate the evidence builder never
            # produced is a different problem from one it produced as
            # unknown, and inventing it here would clear a rail nobody
            # evaluated.
            out["refusals"].append("%s_NOT_IN_THE_GATE_MAP" % name)
            continue
        if state_out[name] is True:
            continue                      # already clear; nothing to waive
        if state_out[name] is False:
            # AN EXPLICIT FAILURE IS NOT AN ABSENCE. NOT_EVALUABLE (None)
            # is what the missing calibration produces; False would mean a
            # measurement said the source HAS drifted, and that blocks.
            out["refusals"].append("%s_FAILED_ON_EVIDENCE_NOT_WAIVED" % name)
            continue
        state_out[name] = True
        out["waived"].append(name)

    out["state"] = state_out
    out["why"] = (
        "MODEL_TRUST_DRIFT stays NOT_EVALUABLE as read from the evidence; "
        "the UNFUNDED research lane is authorised to proceed past it while "
        "that is true, and every other gate and rail is unchanged"
        if out["waived"] else
        "nothing was waived")
    return out


def label(position_record=None) -> dict:
    """The provenance and labels a position created under the waiver takes.

    Kept here rather than at the call site so the label and the waiver
    cannot be applied separately -- a position that took the waiver and
    does not say so is the thing that makes a dashboard lie.
    """
    return {
        "provenance": PROVENANCE,
        "research_shadow_version": VERSION,
        "authorised_by": AUTHORISED_BY,
        "calibration_status": "EXPLICITLY_NOT_ESTABLISHED",
        "funded": False,
        "capital_moved": False,
        "waived_gates": sorted(WAIVABLE),
        "what_this_is_not": WHAT_THE_WAIVER_IS_NOT,
    }


def describe() -> dict:
    """What this lane claims and refuses, for an evidence report."""
    return {
        "version": VERSION,
        "control_key": CONTROL_KEY,
        "provenance": PROVENANCE,
        "authorised_by": AUTHORISED_BY,
        "waivable": sorted(WAIVABLE),
        "never_waivable": sorted(NEVER_WAIVABLE),
        "what_this_is_not": WHAT_THE_WAIVER_IS_NOT,
        "why_it_cannot_reach_capital": WHY_IT_CANNOT_REACH_CAPITAL,
        "absence_is_not_permission": (
            "an absent or unreadable control row leaves the mode OFF"),
        "refusals": (R_NOT_AUTHORISED, R_CONTROL_UNREADABLE,
                     R_CONTRADICTORY, R_CALIBRATED),
    }
