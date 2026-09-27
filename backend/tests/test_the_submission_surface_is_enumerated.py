"""WHAT CAN SEND AN ORDER, ENUMERATED RATHER THAN ASSERTED.

THREE CLAIMS OF MINE THESE TESTS PIN, EACH WRONG IN A DIFFERENT WAY.

  1. "OUR TWO CODE CONSTANTS ARE THE ONLY THING BETWEEN A PROVISIONED CREDENTIAL
     AND ORDER AUTHORITY." Wrong in both directions at once: there are ten
     independent gates, and a credential reaches eleven modules that neither
     constant guards.

  2. "THE MUTATION SURFACE IS TWO FUNCTIONS IN ONE ADAPTER." Wrong, and wrong in
     the REASSURING direction, which is the direction that matters. My first
     scan found 2 callers. The truth is 5, across TWO venues, and three of them
     reach the venue by passing the function as a callable to
     `asyncio.to_thread` -- which a call-site scan cannot see.

     The repository had already caught this exact class of error on 2026-09-21
     and written down that no hand-written inventory had ever listed the CLOB
     path, "including the one I wrote the day before". I then wrote another hand
     inventory and repeated it. So these tests assert against a PARSE, not a
     list.

  3. "SIX MODULES WRITE live_orders" -- offered as though it described six lanes
     trading the account. It describes six modules containing a write statement.
     The isolation verdict is UNKNOWN.
"""

from __future__ import annotations

import pytest

from sportsassets import capital_path as CP
from sportsassets import submission_surface as SS


# ── 1 · THE GATES ARE ENUMERATED, AND THERE ARE MORE THAN TWO ────────

def test_there_are_more_than_two_gates_and_each_names_what_clears_it():
    assert SS.gate_count() >= 8, (
        "the claim being corrected is that TWO constants are the only "
        "protection. If this collapsed to two the correction would be wrong")
    for g in SS.GATES:
        assert g["gate"] and g["where"] and g["cleared_by"]
        assert g["kind"] in {"CODE_CONSTANT", "PROCESS_BOUND_GATE",
                             "DATABASE_ROW", "DATABASE_STATE", "MEASUREMENT",
                             "CREDENTIAL", "SCHEMA"}, g["kind"]
    # THEY ARE NOT ALL CODE CONSTANTS, which is the whole point.
    kinds = {g["kind"] for g in SS.GATES}
    assert len(kinds) >= 5, kinds


def test_the_two_constants_claim_is_recorded_as_wrong_in_BOTH_directions():
    d = SS.describe()
    why = d["and_the_two_constants_claim_was_wrong_twice"]
    assert "overstated the exposure" in why
    assert "understated the problem" in why


def test_the_gate_inside_the_adapter_is_what_covers_the_legacy_lanes():
    """The structural fact that makes the legacy callers safe at all: the gate is
    INSIDE `pmus`, not at the call sites, so it cannot be bypassed by a caller
    written in ignorance -- including the three that pass the function as a
    callable."""
    gate = next(g for g in SS.GATES if g["gate"] == "execution_gate")
    assert "INSIDE" in gate["where"]
    assert "passes the function as a callable" in gate[
        "covers_every_caller_because"] or "as a callable" in gate[
        "covers_every_caller_because"]
    assert "live_executor" in gate[
        "and_this_is_the_only_gate_that_covers_the_LEGACY_lanes"]


# ── 2 · THE MUTATION SURFACE, PARSED ─────────────────────────────────

def test_the_surface_spans_TWO_venues_not_one_adapter():
    """`live_executor._submit_fok` builds its own py_clob_client and calls
    `post_order` -- a second submission path to a second venue."""
    surface = SS.MUTATION_SURFACE
    assert "post_order" in surface
    assert "CLOB" in surface["post_order"]
    assert "does not pass through pmus" in surface["post_order"]
    venues = {v.split(":")[0] for v in surface.values()}
    assert len(venues) >= 2, venues


def test_the_CLOB_submission_path_IS_found_by_the_scanner():
    """THE REGRESSION GUARD FOR THE MISTAKE MADE TWICE IN THIS REPOSITORY."""
    callers = SS.mutation_callers()["callers"]
    assert "live_executor.py" in callers
    assert "post_order" in callers["live_executor.py"]


def test_a_function_PASSED_AS_A_CALLABLE_counts_as_reaching_the_venue():
    """`asyncio.to_thread(pmus.submit_fok, ...)` submits as surely as a direct
    call, and the first two versions of this scan missed all three such sites."""
    callers = SS.mutation_callers()["callers"]
    passed = {m for m, v in callers.items()
              if any("passed as a callable" in x for x in v)}
    assert len(passed) >= 3, passed
    assert "workers/mirror_live.py" in passed
    assert "workers/underdog.py" in passed


def test_the_adapters_themselves_are_not_counted_as_callers():
    callers = SS.mutation_callers()["callers"]
    assert "pmus.py" not in callers
    assert "pmx.py" not in callers


def test_a_DOCSTRING_mention_is_not_a_caller(tmp_path):
    """The parse exists because substring matching counted three docstrings --
    `bettor_pilot_prerequisites`, `venue_pace` and a comment -- as callers, which
    inflates a report about how wide the surface is."""
    import os

    d = tmp_path / "pkg"
    d.mkdir()
    (d / "prose.py").write_text(
        '"""This module describes pmus.submit_fok(...) and cancel_order()."""\n'
        "# and post_order( in a comment\n"
        "X = 1\n")
    (d / "real.py").write_text("import pmus\n"
                               "def go():\n"
                               "    return pmus.submit_fok('s', 1.0, 1)\n")
    saved = SS._HERE
    try:
        SS._HERE = str(d)
        callers = SS.mutation_callers()["callers"]
    finally:
        SS._HERE = saved
    assert "prose.py" not in callers
    assert callers.get("real.py") == ["submit_fok"]
    assert os.path.isdir(d)


# ── 3 · THE CREDENTIAL'S BLAST RADIUS ────────────────────────────────

def test_the_credential_reaches_many_modules_not_just_the_funded_lane():
    """"Provisioning a key must not accidentally enable another lane" is
    answered by this list, and the answer is that ONE environment credential is
    visible to every module in it."""
    cc = SS.credential_consumers()
    assert cc["count"] >= 5
    assert "live_executor.py" in cc["modules"]          # the legacy lane
    assert "bettor_funded_execution.py" in cc["modules"]  # the funded lane
    assert "the separation has to come from the gates" in (
        cc["and_what_this_means_for_provisioning"])


def test_modules_that_merely_NAME_the_credential_are_excluded_with_a_reason():
    """`bettor_evidence_store` lists the setting names so their values are never
    written to evidence. Counting a redaction list as a consumer would inflate
    the blast radius in a report about the blast radius."""
    cc = SS.credential_consumers()
    excluded = cc["excluded_as_non_consumers"]
    assert "bettor_evidence_store.py" in excluded
    assert "REDACTION" in excluded["bettor_evidence_store.py"]
    assert "config.py" in excluded
    for name in excluded:
        assert name not in cc["modules"]


# ── 4 · UNVERIFIED IS NOT A CONCLUSION ───────────────────────────────

def test_unverified_scope_granularity_licenses_only_LOOKING(monkeypatch):
    """Absence of documented scopes does not prove every issued key carries every
    permission. It licenses one action: look at the provisioning screen."""
    d = SS.describe()
    lic = d["what_unverified_scopes_license"]
    assert "provisioning screen" in lic
    assert "NOT a conclusion that a key carries every permission" in lic
    assert "not one that it carries few" in lic


def test_the_read_only_interface_is_now_a_CONTROL_and_still_not_adoption():
    """This used to assert NOT IMPLEMENTED. The interface now exists.

    `bettor_read_only_venue.ReadOnlyVenue` satisfies the requirement in the
    form the requirement itself specified -- mutation names ABSENT rather
    than guarded -- so asserting the old status would now be asserting a
    gap that has been closed. What has NOT changed is that the diagnostic
    call sites still have to be handed it, and the status says so rather
    than reading as finished.
    """
    r = SS.READ_ONLY_INTERFACE_REQUIREMENT
    assert "NOT IMPLEMENTED" not in r["status"]
    assert r["status"].startswith("IMPLEMENTED")
    assert "bettor_read_only_venue" in r["status"]
    # The reasoning that motivated it is preserved, not deleted with the gap.
    assert "a flag is checked at one site" in r["why_a_flag_is_weaker"]
    # And adoption is still open, reported rather than claimed.
    assert "ADOPTION at each diagnostic call site is separate work" in \
        r["status"]
    assert "adoption()" in r["status"]
    assert "have not" in r["what_exists_today"]


def test_the_interface_it_names_actually_refuses_the_enumerated_mutations():
    """The status is only true if the object it names behaves that way."""
    from sportsassets import bettor_read_only_venue as RO
    v = RO.read_only_venue()
    for name in sorted(SS.MUTATION_NAMES):
        assert not hasattr(v, name), name


def test_the_permission_granularity_is_still_unverified():
    """Constraining our interface says nothing about the credential's scope."""
    r = SS.READ_ONLY_INTERFACE_REQUIREMENT
    assert "permission granularity" in r["still_unverified"]
    assert "not the credential's scope" in r["still_unverified"]


def test_the_enumeration_states_what_a_source_read_cannot_settle():
    limits = " ".join(SS.LIMITS)
    assert "not a running process" in limits
    assert "is not a module that currently trades an account" in limits
    assert "unverified either way" in limits


# ── 5 · THE WRITER INVENTORY IS NOT A TRADER CENSUS ──────────────────

def test_writer_isolation_is_reported_as_UNKNOWN():
    """I reported six `live_orders` writers as though six lanes trade the
    account. Each would additionally need an account binding, a credential and
    an enabled control path -- none of which a source read establishes."""
    aw = CP.account_writers()
    assert aw["is_this_a_census_of_modules_that_CURRENTLY_TRADE"] is False
    assert aw["exposure_and_writer_isolation_verdict"] == "UNKNOWN"
    needs = " ".join(aw["what_each_writer_would_additionally_need"])
    assert "account binding" in needs
    assert "credential" in needs
    assert "control path" in needs
    # And it says what would settle it, so UNKNOWN is a state with an exit.
    assert len(aw["what_would_settle_it"]) >= 3


def test_UNKNOWN_is_not_traded_for_either_convenient_answer():
    aw = CP.account_writers()
    why = aw["why_UNKNOWN_and_not_a_number"]
    assert "both unsupported" in why
    assert "I asserted the first" in why
