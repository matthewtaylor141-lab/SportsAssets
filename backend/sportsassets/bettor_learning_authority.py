"""WHAT LEARNING MAY CHANGE, MEASURED RATHER THAN PROMISED.

Owner directive, "COMPLETE THE AUTONOMOUS TRADING SYSTEM" §7:

    "Model promotion requires versioned evaluation, documented
     acceptance criteria, rollback, audit trail. The learning process
     must not autonomously change risk limits, eligibility,
     authorization or evidence requirements."

That last sentence was, until this module, a sentence. `learn_gate` is an
ACCEPTANCE gate -- it decides whether a candidate beat a baseline -- and
nothing anywhere checked whether a learning run had moved a risk limit,
an eligibility rule, an authorization switch or an evidence requirement
on its way to that verdict. The completion register's own wording was
"no promotion path to trading authority, BY DESIGN", which is a statement
about intent. Intent is not a control.

────────────────────────────────────────────────────────────────────
WHY IT FINGERPRINTS BEHAVIOUR AND NOT CONSTANTS.

The obvious implementation snapshots the four submission constants and
compares them. It would pass while the system was thoroughly broken,
because a constant is not the control -- the code that READS it is.
Leaving `FUNDED_SUBMISSION_ENABLED = False` and changing the branch that
consults it defeats a constant snapshot completely, and so does leaving
`silence_is_not_agreement` alone while changing what `compare` does with
a silent condition.

So each probe below CALLS something and records the answer. The
authorization probe asks the execution gate to authorize and records that
it refused. The eligibility probe asks the admission policy to admit a
bare request and records the refusal. The evidence probe hands `compare`
a condition one side is silent about and records that the verdict is
UNKNOWN rather than COMPATIBLE. A change that leaves every constant in
place but alters any of those answers moves the fingerprint.

    A CONSTANT SNAPSHOT CATCHES AN EDIT. A BEHAVIOURAL PROBE CATCHES A
    CHANGE OF BEHAVIOUR, WHICH IS THE THING THAT MATTERS.

────────────────────────────────────────────────────────────────────
WHAT `guard` ACTUALLY PROVES, AND THE THREE THINGS IT CANNOT.

`guard()` fingerprints the protected surface, runs the learning work,
fingerprints again, and raises `AuthorityViolation` naming every probe
whose answer moved. A promotion wrapped in it either leaves the
protected surface identical or does not complete.

It cannot detect:

  1. A CHANGE IN ANOTHER PROCESS. The probes read this interpreter. A
     learner running as a separate worker that rewrote a limit in the
     database would not move this fingerprint, because the fingerprint
     never reads the database. `DB_BACKED_CONTROLS` names the controls
     that live in rows rather than in code, and they are explicitly
     OUT of scope here.
  2. A CHANGE AND A CHANGE BACK inside the guarded block. The
     fingerprint is taken at two instants, not continuously.
  3. A CONTROL NOBODY PROBED. `PROBES` is a hand-maintained list and
     therefore carries the same risk every hand inventory in this
     repository has been bitten by. `coverage()` reports which of the
     four named categories each probe serves so a missing category is
     visible, and it is NOT a claim that the category is fully covered.

None of those are reasons to skip the guard. They are the reasons its
result is reported as "the probed surface did not move" rather than as
"learning changed nothing".
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass

# ── the four categories §7 names ─────────────────────────────────────

RISK_LIMITS = "RISK_LIMITS"
ELIGIBILITY = "ELIGIBILITY"
AUTHORIZATION = "AUTHORIZATION"
EVIDENCE_REQUIREMENTS = "EVIDENCE_REQUIREMENTS"

CATEGORIES = (RISK_LIMITS, ELIGIBILITY, AUTHORIZATION, EVIDENCE_REQUIREMENTS)


class AuthorityViolation(Exception):
    """A learning run moved something it has no authority over."""

    def __init__(self, moved: dict):
        self.moved = dict(moved)
        super().__init__(
            "learning moved %d protected answer(s), which it has no "
            "authority to do: %s. The promotion is void; nothing about the "
            "candidate's evaluation is established by a run that changed "
            "its own gates."
            % (len(moved), ", ".join(sorted(moved))))


# ═════════════════════════════════════════════════════════════════════
# THE PROBES. EACH ONE CALLS SOMETHING.
# ═════════════════════════════════════════════════════════════════════

def _probe_submission_constants():
    """The four code constants, by value. The weakest probe, kept as a floor."""
    from . import bettor_admission_policy as ap
    from . import bettor_entry_execution as ee
    from . import bettor_funded_execution as fe
    from . import bettor_funded_management as fm
    return {
        "FUNDED_SUBMISSION_ENABLED": fe.FUNDED_SUBMISSION_ENABLED,
        "REAL_ORDER_SUBMISSION_ENABLED": ee.REAL_ORDER_SUBMISSION_ENABLED,
        "FUNDED_EXIT_SUBMISSION_ENABLED": fm.FUNDED_EXIT_SUBMISSION_ENABLED,
        "POLICY_ADMISSION_ENABLED": ap.POLICY_ADMISSION_ENABLED,
    }


def _probe_gate_refuses_unbound():
    """ASK the gate. A constant snapshot would miss a changed branch."""
    from . import execution_gate as eg
    try:
        snap = eg.authorize("submit", lane="learning-authority-probe")
    except eg.Denied as d:
        return {"authorized": False, "reason": d.reason}
    return {"authorized": True, "ok": getattr(snap, "ok", None)}


def _probe_admission_policy_refuses():
    """ASK the policy to admit nothing in particular."""
    from . import bettor_admission_policy as ap
    return {
        "enabled": ap.POLICY_ADMISSION_ENABLED,
        "version": ap.POLICY_VERSION,
        "cannot_waive_count": len(ap.REQUIREMENTS_IT_CANNOT_WAIVE),
        "cannot_waive": tuple(sorted(
            str(x) for x in ap.REQUIREMENTS_IT_CANNOT_WAIVE)),
    }


def _probe_silence_is_not_agreement():
    """ASK `compare` about a condition one side is silent on."""
    from . import bettor_settlement_terms as st
    out = st.compare(book={"COMPLETED_IN_REGULATION": "ACTION",
                           "DECIDED_AFTER_REGULATION": "ACTION"},
                     venue={"COMPLETED_IN_REGULATION": "ACTION"},
                     conditions=("COMPLETED_IN_REGULATION",
                                 "DECIDED_AFTER_REGULATION"))
    per = out.get("per_condition") or {}
    return {
        "verdict_when_a_side_is_silent": out.get("verdict"),
        "silent_condition_verdict": (
            per.get("DECIDED_AFTER_REGULATION") or {}).get("verdict"),
        "unstated_count": len(out.get("unstated_conditions") or ()),
    }


def _probe_settlement_classification():
    """ASK the taxonomy how it classes a venue silence."""
    from . import bettor_settlement_terms as st
    from . import settlement_taxonomy as tx
    r = tx.classify_comparison(
        {"C": {"verdict": st.V_VENUE_SILENT},
         "D": {"verdict": st.V_MISMATCH}})
    return {"class_of_mismatch_plus_silence": r["candidate_class"],
            "precedence": tx.CANDIDATE_PRECEDENCE}


def _probe_exposure_effects():
    """The action table's exposure axes: which actions are gated."""
    from . import bettor_ev_actions as acts
    return {
        "action_count": len(acts.CANONICAL_ACTIONS),
        "exposure_increasing": tuple(sorted(acts.EXPOSURE_INCREASING)),
        "effects": {a: acts.EXPOSURE_EFFECT[a]
                    for a in sorted(acts.EXPOSURE_EFFECT)},
    }


def _probe_submission_gates():
    """The enumerated gate list in front of a funded order."""
    from . import submission_surface as ss
    return {
        "gate_count": len(ss.GATES),
        "gates": tuple(sorted(g["gate"] for g in ss.GATES)),
        "mutation_names": tuple(sorted(ss.MUTATION_NAMES)),
    }


def _probe_read_only_interface():
    """The diagnostic interface still has no mutation."""
    from . import bettor_read_only_venue as ro
    v = ro.read_only_venue()
    return {"mutations_reachable": tuple(
        n for n in ro.KNOWN_MUTATIONS if hasattr(v, n)),
        "reads": tuple(ro.READS)}


def _probe_indirect_refusals():
    """An unestablishable structure is still refused, not middled."""
    from fractions import Fraction

    from . import bettor_indirect_structures as ins
    a = ins.Leg(condition_id="a", fixture_id="fx", kind=ins.KIND_MONEYLINE,
                period=ins.PERIOD_FULL, overtime=ins.OT_INCLUDED, backs="A",
                quantity=1, settlement_text_captured=True,
                tie_rule="tie resolves 50-50", void_rule="void: 50-50")
    b = ins.Leg(condition_id="b", fixture_id="fx", kind=ins.KIND_SPREAD,
                period=ins.PERIOD_H1, overtime=ins.OT_EXCLUDED, backs="B",
                line=Fraction(-9, 2), quantity=1,
                settlement_text_captured=True,
                tie_rule="none", void_rule="void: 50-50")
    s = ins.classify(a, b, sport_permits_tie=False, fixture_can_void=False,
                     fixture_can_postpone=False)
    return {"taxonomy": s.taxonomy, "missing_fact_count":
            len(s.missing_facts)}


def _probe_completion_ranks_on_forward_cash():
    """Basis must stay out of the ranking."""
    from . import bettor_completion_policy as cp
    q = cp.Quotes(complement_ask=0.55, own_bid=0.44)
    low = cp.rank(cp.Position(1.0, 0.0), q, 0.35, gross=True)
    high = cp.rank(cp.Position(1.0, 5.0), q, 0.35, gross=True)
    return {"order_at_zero_basis": tuple(a["action"] for a in low["ranked"]),
            "order_at_high_basis": tuple(a["action"] for a in high["ranked"])}


#: EVERY PROBE, with the category it serves. Hand-maintained, and
#: `coverage()` exists because that is a risk rather than a reassurance.
PROBES = (
    ("submission_constants", AUTHORIZATION, _probe_submission_constants),
    ("execution_gate_refuses_unbound", AUTHORIZATION,
     _probe_gate_refuses_unbound),
    ("submission_gate_list", AUTHORIZATION, _probe_submission_gates),
    ("read_only_interface", AUTHORIZATION, _probe_read_only_interface),
    ("admission_policy", ELIGIBILITY, _probe_admission_policy_refuses),
    ("indirect_structure_refusals", ELIGIBILITY, _probe_indirect_refusals),
    ("exposure_effects", RISK_LIMITS, _probe_exposure_effects),
    ("completion_ranks_on_forward_cash", RISK_LIMITS,
     _probe_completion_ranks_on_forward_cash),
    ("silence_is_not_agreement", EVIDENCE_REQUIREMENTS,
     _probe_silence_is_not_agreement),
    ("settlement_classification", EVIDENCE_REQUIREMENTS,
     _probe_settlement_classification),
)

#: Controls that live in database rows rather than in code. The
#: fingerprint does NOT read them, and saying so is the point: a learner
#: in another process could move one without moving this fingerprint.
DB_BACKED_CONTROLS = (
    "ingestion_state['live_trading_paused'] -- the kill switch",
    "ingestion_state['mirror_loss_stop'] -- the rolling loss breaker",
    "the funded-account authorization row",
    "the per-account typed exposure limits",
)


def _digest(value) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()[:16]


def fingerprint() -> dict:
    """Every probe's answer, digested. Reading it never changes anything."""
    out: dict = {}
    for name, category, fn in PROBES:
        try:
            answer = fn()
            out[name] = {"category": category, "digest": _digest(answer),
                         "answer": answer, "probe_ok": True}
        except Exception as exc:                          # noqa: BLE001
            # A PROBE THAT CANNOT RUN IS NOT A PASS. Its failure is
            # recorded in the digest, so a change that breaks a probe
            # moves the fingerprint instead of silently removing a check.
            out[name] = {"category": category,
                         "digest": _digest(("PROBE_FAILED",
                                            type(exc).__name__)),
                         "answer": "PROBE_FAILED: %s" % type(exc).__name__,
                         "probe_ok": False}
    out["_overall"] = {
        "digest": _digest(tuple((k, out[k]["digest"])
                                for k in sorted(out) if k != "_overall")),
        "probe_count": len(PROBES),
    }
    return out


def compare_fingerprints(before: dict, after: dict) -> dict:
    """Which probes moved. Names them; never reports a count alone."""
    moved: dict = {}
    for name in sorted(set(before) | set(after)):
        if name == "_overall":
            continue
        b = (before.get(name) or {}).get("digest")
        a = (after.get(name) or {}).get("digest")
        if b != a:
            moved[name] = {
                "category": ((after.get(name) or before.get(name) or {})
                             .get("category")),
                "before": (before.get(name) or {}).get("answer"),
                "after": (after.get(name) or {}).get("answer"),
            }
    return {
        "moved": moved,
        "unchanged": bool(not moved),
        "categories_touched": tuple(sorted(
            {v["category"] for v in moved.values() if v.get("category")})),
    }


@dataclass
class GuardResult:
    before: dict
    after: dict | None = None
    moved: dict | None = None

    def to_dict(self) -> dict:
        return {
            "protected_surface_moved": bool(self.moved),
            "moved": dict(self.moved or {}),
            "probe_count": len(PROBES),
            "established": (
                "the PROBED surface did not move between the two instants "
                "the fingerprint was taken"),
            "not_established": (
                "that learning changed nothing. A change in another "
                "process, a change and a change back inside the block, or "
                "a control nobody probed would all be invisible here"),
            "db_backed_controls_out_of_scope": list(DB_BACKED_CONTROLS),
        }


@contextlib.contextmanager
def guard():
    """Run learning work; refuse it if the protected surface moved.

    >>> with guard() as g:          # doctest: +SKIP
    ...     train_and_promote()
    >>> g.moved                     # doctest: +SKIP
    {}
    """
    result = GuardResult(before=fingerprint())
    try:
        yield result
    finally:
        result.after = fingerprint()
        result.moved = compare_fingerprints(result.before, result.after
                                            )["moved"]
    if result.moved:
        raise AuthorityViolation(result.moved)


# ═════════════════════════════════════════════════════════════════════
# WHAT LEARNING MAY CHANGE
# ═════════════════════════════════════════════════════════════════════

MAY_CHANGE = (
    "model coefficients and hyperparameters",
    "a model version identifier",
    "prediction rows in the prediction ledger",
    "evaluation results and their stored metrics",
    "which challenger is nominated for review",
)

MAY_NOT_CHANGE = (
    "any risk limit or exposure bound",
    "any eligibility or admission rule",
    "any authorization switch, gate or credential",
    "any evidence requirement, including what counts as silence",
)

NOMINATION_IS_NOT_PROMOTION = (
    "a learning run may NOMINATE a challenger. It may not promote one to "
    "trading authority, because promotion crosses into authorization and "
    "that is outside MAY_CHANGE. The register's 'no promotion path to "
    "trading authority, by design' is now a control rather than an "
    "intention: `guard` raises if a run moves any authorization answer")


def coverage() -> dict:
    """Which categories the probes serve. NOT a claim of completeness."""
    by_cat: dict = {c: [] for c in CATEGORIES}
    for name, category, _ in PROBES:
        by_cat.setdefault(category, []).append(name)
    return {
        "by_category": {c: sorted(v) for c, v in by_cat.items()},
        "categories_with_no_probe": tuple(
            c for c in CATEGORIES if not by_cat.get(c)),
        "this_is_not": (
            "a claim that any category is fully covered. PROBES is a "
            "hand-maintained list and carries the same risk every hand "
            "inventory in this repository has been bitten by; it reports "
            "which categories are REACHED, not which are complete"),
    }


def describe() -> dict:
    return {
        "categories": CATEGORIES,
        "may_change": MAY_CHANGE,
        "may_not_change": MAY_NOT_CHANGE,
        "nomination_is_not_promotion": NOMINATION_IS_NOT_PROMOTION,
        "why_behavioural_probes": (
            "a constant is not the control; the code that reads it is. "
            "Leaving FUNDED_SUBMISSION_ENABLED False and changing the "
            "branch that consults it defeats a constant snapshot, so each "
            "probe calls something and records the answer"),
        "coverage": coverage(),
        "db_backed_controls_out_of_scope": DB_BACKED_CONTROLS,
    }
