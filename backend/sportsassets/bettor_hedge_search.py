"""HOW THE HEDGE SEARCH ENDED: THE SUPPLIER'S OWN ACCOUNT. Pure.

This is decision EVIDENCE, not presentation. The funded servicing pass
computes it from the sibling search's own report (`candidate_legs_read`:
limit, truncation, quote refusals for budget or deadline, catalogue rows
read) and passes it to the policy gate and onto Xavier's record, whether or
not the ladder-rendering module (`agents.xavier_ladder`) loads or works.

When the account itself cannot be computed, completeness is UNKNOWN
(`complete` None, stop reason COMPLETENESS_UNKNOWN) -- never COMPLETE. A
policy that requires a complete comparison treats anything but
`complete is True` as incomplete.
"""
from __future__ import annotations

from typing import Any

STOP_COMPLETE = "COMPLETE"
STOP_BUDGET = "READ_BUDGET_EXHAUSTED"
STOP_LIMIT = "LIMIT_REACHED"
STOP_DEADLINE = "DEADLINE"
STOP_NOT_RUN = "SEARCH_NOT_RUN"
STOP_NOT_REPORTED = "NOT_REPORTED_BY_THE_SUPPLIER"


def _quote_stop(r: dict) -> str | None:
    """A candidate whose QUOTE was refused for want of time or read budget
    was not examined -- it is unexamined, not excluded."""
    if str(r.get("stage") or "") != "QUOTE":
        return None
    q = str(r.get("quote_refusal") or "")
    if "DEADLINE" in q:
        return STOP_DEADLINE
    if "BUDGET" in q:
        return STOP_BUDGET
    return None


def search_account(facts: dict | None) -> dict:
    """HOW THE HEDGE SEARCH ENDED, from the supplier's own report carried on
    the pair inputs (`candidate_legs_read`). Pure.

    discovered = the fixture's (slug, side) sibling pairs in the catalogue;
    examined   = the siblings the search attempted within its read budget,
                 less any whose quote was refused for budget or deadline;
    excluded   = attempted and refused, by (stage, refusal) -- with reasons;
    unexamined = discovered - examined, with the reason the search ended:
                 COMPLETE / READ_BUDGET_EXHAUSTED (the per-pass quote budget,
                 `candidate_legs_for`'s cap) / LIMIT_REACHED (the catalogue
                 read's own limit) / DEADLINE / SEARCH_NOT_RUN /
                 NOT_REPORTED_BY_THE_SUPPLIER.
    A limited search is described as best AMONG THE EXAMINED, never as the
    best available across the market."""
    f = dict(facts or {})
    clr = dict(f.get("candidate_legs_read") or {})
    held = dict(f.get("held_leg_read") or {})
    so = dict(clr.get("search_order") or {})
    elig = clr.get("eligibility")
    refused = list(clr.get("refused") or [])
    out: dict[str, Any] = {
        "supplier_limit": clr.get("limit"),
        "supplier_truncated_at_limit": clr.get("truncated_at_limit"),
        "truncation_note": clr.get("truncation_note"),
        "fixture_candidate_pairs": clr.get("fixture_candidate_pairs"),
        "catalogue_rows_read": so.get("catalogue_rows_read"),
        "eligibility": ({k: (elig or {}).get(k) for k in (
            "rows_fetched", "excluded_before_reads", "eligible_total",
            "examined", "deferred", "exhausted_skipped", "cap")}
            if isinstance(elig, dict) else None),
        "eligibility_why": (None if isinstance(elig, dict) else
                            "the funded path applies no pre-read eligibility "
                            "screen; every sibling in the search order is "
                            "quoted up to the cap")}
    if (held and held.get("ok") is False) or clr.get("refusal"):
        held = dict(held, refusal=held.get("refusal") or clr.get("refusal"))
        return dict(out, complete=False, stop_reason=STOP_NOT_RUN,
                    discovered=None, examined=0, excluded={},
                    unexamined=None,
                    why=held.get("refusal") or "THE_HELD_LEG_WAS_NOT_BUILT",
                    comparison_scope=(
                        "NO_HEDGE_SEARCH: %s -- nothing is concluded about "
                        "pairs that were never looked for"
                        % (held.get("refusal") or "held leg not built")))
    if clr.get("truncated_at_limit") is None:
        return dict(out, complete=None, stop_reason=STOP_NOT_REPORTED,
                    discovered=None, examined=clr.get("examined"),
                    excluded={}, unexamined=None,
                    comparison_scope=(
                        "BEST_AMONG_EXAMINED (%s examined; whether the search "
                        "was complete is not reported)" % clr.get("examined")))
    stops = [x for x in (_quote_stop(r) for r in refused) if x]
    excluded: dict[str, int] = {}
    for r in refused:
        if _quote_stop(r):
            continue
        k = "%s:%s" % (r.get("stage") or "?", r.get("refusal") or "UNNAMED")
        excluded[k] = excluded.get(k, 0) + 1
    for k, n in dict((elig or {}).get("excluded_before_reads") or {}).items():
        key = "SCREEN_BEFORE_READ:%s" % k
        if key not in excluded:
            excluded[key] = int(n)
    attempted = int(clr.get("examined") or 0)
    examined = attempted - len(stops)
    rows_read = so.get("catalogue_rows_read")
    discovered = clr.get("fixture_candidate_pairs")
    if discovered is None:
        discovered = rows_read
    discovered = int(discovered or 0)
    pre_screened = sum(int(v) for v in dict(
        (elig or {}).get("excluded_before_reads") or {}).values())
    unexamined = max(0, discovered - attempted - pre_screened) + len(stops)
    if STOP_DEADLINE in stops:
        stop = STOP_DEADLINE
    elif STOP_BUDGET in stops or clr.get("truncated_at_limit"):
        stop = STOP_BUDGET
    elif rows_read is not None and discovered > int(rows_read):
        stop = STOP_LIMIT
    elif unexamined > 0:
        stop = STOP_BUDGET
    else:
        stop = STOP_COMPLETE
    complete = stop == STOP_COMPLETE
    return dict(out, complete=complete, stop_reason=stop,
                discovered=discovered, attempted=attempted,
                examined=examined, excluded=excluded,
                excluded_total=sum(excluded.values()),
                unexamined=unexamined,
                comparison_scope=(
                    "COMPLETE: every sibling examined (%d of %d)"
                    % (examined, discovered) if complete else
                    "BEST_AMONG_EXAMINED (%d of %d; %d unexamined: %s)"
                    % (examined, discovered, unexamined, stop)),
                is_not=("a claim that the chosen pair is the best available "
                        "across the market" if not complete else None))


STOP_UNKNOWN = "COMPLETENESS_UNKNOWN"


def unknown(why: str, *, error: str | None = None) -> dict:
    """The account when it could not be established: UNKNOWN, never
    COMPLETE, and it says why."""
    return {"complete": None, "stop_reason": STOP_UNKNOWN,
            "discovered": None, "examined": None, "excluded": {},
            "unexamined": None, "why": why, "error": error,
            "comparison_scope": (
                "COMPLETENESS_UNKNOWN (%s): the hedge search's coverage is "
                "not established, so no pair is described as best available"
                % why)}


def account_or_unknown(facts: dict | None) -> dict:
    """`search_account`, or the UNKNOWN account naming the exception."""
    try:
        return search_account(facts)
    except Exception as exc:                                    # noqa: BLE001
        return unknown("THE_SEARCH_ACCOUNT_RAISED", error=type(exc).__name__)
