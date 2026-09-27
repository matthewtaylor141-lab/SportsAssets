"""§7: "The learning process must not autonomously change risk limits,
eligibility, authorization or evidence requirements."

Until `bettor_learning_authority` that was a sentence. `learn_gate` is an
ACCEPTANCE gate -- it decides whether a candidate beat a baseline -- and
nothing checked whether the run had moved a gate on its way to the
verdict. The register's own wording, "no promotion path to trading
authority, BY DESIGN", is a statement about intent.

The tests that carry weight here are the ones where a simulated learner
DOES move something and the guard catches it: one per category. A guard
that only ever passes is indistinguishable from no guard.
"""

import pytest

from sportsassets import bettor_learning_authority as LA


# ═════════════════════════════════════════════════════════════════════
# THE FINGERPRINT WORKS AT ALL
# ═════════════════════════════════════════════════════════════════════

def test_every_probe_runs():
    fp = LA.fingerprint()
    broken = [k for k, v in fp.items()
              if k != "_overall" and not v["probe_ok"]]
    assert broken == [], broken


def test_the_fingerprint_is_stable_across_two_reads():
    a, b = LA.fingerprint(), LA.fingerprint()
    assert a["_overall"]["digest"] == b["_overall"]["digest"]


def test_reading_the_fingerprint_changes_nothing():
    before = LA.fingerprint()
    LA.fingerprint()
    LA.describe()
    LA.coverage()
    after = LA.fingerprint()
    assert before["_overall"]["digest"] == after["_overall"]["digest"]


def test_all_four_categories_are_reached():
    assert LA.coverage()["categories_with_no_probe"] == ()
    by_cat = LA.coverage()["by_category"]
    for c in LA.CATEGORIES:
        assert by_cat[c], c


def test_coverage_does_not_claim_completeness():
    cov = LA.coverage()
    # The key carries the negation; the value states the claim NOT made.
    assert "a claim that any category is fully covered" in cov["this_is_not"]
    assert "hand-maintained" in cov["this_is_not"]
    assert "REACHED, not which are complete" in cov["this_is_not"]


# ═════════════════════════════════════════════════════════════════════
# THE GUARD PASSES WHEN NOTHING MOVES
# ═════════════════════════════════════════════════════════════════════

def test_a_run_that_touches_nothing_passes():
    with LA.guard() as g:
        sum(range(1000))            # a "learning run" that trains nothing
    assert g.moved == {}


def test_a_run_that_only_changes_a_model_coefficient_passes():
    weights = {"w": 0.1}
    with LA.guard() as g:
        weights["w"] = 0.9          # exactly what learning MAY change
    assert g.moved == {}
    assert weights["w"] == 0.9


def test_the_result_states_what_it_does_and_does_not_establish():
    with LA.guard() as g:
        pass
    d = g.to_dict()
    assert "the PROBED surface did not move" in d["established"]
    assert "learning changed nothing" in d["not_established"]
    assert "another process" in d["not_established"]
    assert d["db_backed_controls_out_of_scope"]


# ═════════════════════════════════════════════════════════════════════
# THE GUARD CATCHES A CHANGE — ONE PER CATEGORY
# ═════════════════════════════════════════════════════════════════════

def test_a_learner_that_flips_an_authorization_constant_is_caught(
        monkeypatch):
    from sportsassets import bettor_funded_execution as fe
    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(fe, "FUNDED_SUBMISSION_ENABLED", True)
    assert "submission_constants" in e.value.moved
    assert e.value.moved["submission_constants"]["category"] == \
        LA.AUTHORIZATION


def test_a_learner_that_enables_the_admission_policy_is_caught(monkeypatch):
    from sportsassets import bettor_admission_policy as ap
    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(ap, "POLICY_ADMISSION_ENABLED", True)
    assert "admission_policy" in e.value.moved
    assert LA.ELIGIBILITY in e.value.moved["admission_policy"].values() or \
        e.value.moved["admission_policy"]["category"] == LA.ELIGIBILITY


def test_a_learner_that_shortens_the_cannot_waive_list_is_caught(monkeypatch):
    from sportsassets import bettor_admission_policy as ap
    shorter = tuple(ap.REQUIREMENTS_IT_CANNOT_WAIVE)[:-1]
    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(ap, "REQUIREMENTS_IT_CANNOT_WAIVE", shorter)
    assert "admission_policy" in e.value.moved


def test_a_learner_that_moves_an_exposure_effect_is_caught(monkeypatch):
    from sportsassets import bettor_ev_actions as acts
    relaxed = dict(acts.EXPOSURE_EFFECT)
    relaxed["COMPLETE_PAIR"] = (acts.UNCHANGED, acts.UNCHANGED)
    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(acts, "EXPOSURE_EFFECT", relaxed)
    assert "exposure_effects" in e.value.moved
    assert e.value.moved["exposure_effects"]["category"] == LA.RISK_LIMITS


def test_a_learner_that_makes_silence_agreement_is_caught(monkeypatch):
    """The evidence requirement, and the probe that CALLS rather than reads."""
    from sportsassets import bettor_settlement_terms as st

    def _permissive(**kw):
        return {"verdict": st.COMPATIBLE, "per_condition": {},
                "unstated_conditions": []}

    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(st, "compare", _permissive)
    assert "silence_is_not_agreement" in e.value.moved
    assert e.value.moved["silence_is_not_agreement"]["category"] == \
        LA.EVIDENCE_REQUIREMENTS


def test_a_learner_that_makes_the_gate_authorize_is_caught(monkeypatch):
    """A changed BRANCH, not a changed constant. This is the whole point."""
    from sportsassets import execution_gate as eg

    def _permissive(operation, **kw):
        return eg.Snapshot(ok=True)

    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(eg, "authorize", _permissive)
    assert "execution_gate_refuses_unbound" in e.value.moved


def test_a_learner_that_exposes_a_mutation_on_the_diagnostic_is_caught(
        monkeypatch):
    from sportsassets import bettor_read_only_venue as ro
    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(ro, "READS", ro.READS + ("submit_fok",))
    assert "read_only_interface" in e.value.moved


def test_a_learner_that_stops_refusing_an_unestablishable_structure_is_caught(
        monkeypatch):
    from sportsassets import bettor_indirect_structures as ins

    real = ins.classify

    def _lenient(a, b, **kw):
        s = real(a, b, **kw)
        s.taxonomy = ins.MIDDLE          # call an unestablishable pair hedged
        return s

    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(ins, "classify", _lenient)
    assert "indirect_structure_refusals" in e.value.moved


def test_a_learner_that_lets_basis_into_the_ranking_is_caught(monkeypatch):
    from sportsassets import bettor_completion_policy as cp

    real = cp.rank

    def _sunk_cost_aware(pos, q, p, **kw):
        out = real(pos, q, p, **kw)
        if pos.basis_per_contract > 1.0:
            out["ranked"] = list(reversed(out["ranked"]))
            out["best"] = out["ranked"][0]["action"] if out["ranked"] else None
        return out

    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(cp, "rank", _sunk_cost_aware)
    assert "completion_ranks_on_forward_cash" in e.value.moved


# ═════════════════════════════════════════════════════════════════════
# A BROKEN PROBE IS NOT A PASS
# ═════════════════════════════════════════════════════════════════════

def test_a_probe_that_raises_moves_the_fingerprint(monkeypatch):
    """Removing a check must not look like passing it."""
    from sportsassets import bettor_admission_policy as ap

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("probe broken")

    with pytest.raises(LA.AuthorityViolation) as e:
        with LA.guard():
            monkeypatch.setattr(ap, "POLICY_VERSION", property(
                lambda self: (_ for _ in ()).throw(RuntimeError())))
            monkeypatch.delattr(ap, "REQUIREMENTS_IT_CANNOT_WAIVE")
    assert "admission_policy" in e.value.moved


def test_a_failed_probe_is_recorded_as_failed_not_omitted(monkeypatch):
    from sportsassets import bettor_admission_policy as ap
    monkeypatch.delattr(ap, "POLICY_ADMISSION_ENABLED")
    fp = LA.fingerprint()
    assert fp["admission_policy"]["probe_ok"] is False
    assert "PROBE_FAILED" in str(fp["admission_policy"]["answer"])
    # And it still contributes a digest, so it cannot vanish silently.
    assert fp["admission_policy"]["digest"]


# ═════════════════════════════════════════════════════════════════════
# THE GUARD IS NOT ESCAPABLE BY RAISING
# ═════════════════════════════════════════════════════════════════════

def test_a_run_that_raises_still_gets_fingerprinted(monkeypatch):
    from sportsassets import bettor_funded_execution as fe
    with pytest.raises(RuntimeError):
        with LA.guard() as g:
            monkeypatch.setattr(fe, "FUNDED_SUBMISSION_ENABLED", True)
            raise RuntimeError("training crashed")
    # The body's own exception propagates, and the after-fingerprint was
    # still taken, so the violation is visible on the result.
    assert g.after is not None
    assert "submission_constants" in (g.moved or {})


# ═════════════════════════════════════════════════════════════════════
# WHAT LEARNING MAY AND MAY NOT CHANGE, STATED
# ═════════════════════════════════════════════════════════════════════

def test_nomination_is_separated_from_promotion():
    note = LA.NOMINATION_IS_NOT_PROMOTION
    assert "may NOMINATE" in note
    assert "may not promote" in note
    assert "now a control rather than an" in note


def test_the_may_not_change_list_covers_all_four_categories():
    joined = " ".join(LA.MAY_NOT_CHANGE).lower()
    for word in ("risk limit", "eligibility", "authorization", "evidence"):
        assert word in joined, word


def test_the_module_explains_why_probes_call_rather_than_read():
    why = LA.describe()["why_behavioural_probes"]
    assert "a constant is not the control" in why
    assert "changing the branch that consults it" in why


def test_db_backed_controls_are_named_as_out_of_scope():
    controls = " ".join(LA.DB_BACKED_CONTROLS)
    assert "kill switch" in controls
    assert "authorization row" in controls
    assert "exposure limits" in controls


def test_the_violation_message_voids_the_promotion():
    from sportsassets import bettor_funded_execution as fe
    import pytest as _p
    with _p.MonkeyPatch.context() as m:
        with pytest.raises(LA.AuthorityViolation) as e:
            with LA.guard():
                m.setattr(fe, "FUNDED_SUBMISSION_ENABLED", True)
    msg = str(e.value)
    assert "promotion is void" in msg
    assert "changed its own gates" in msg
