"""§5's read-only diagnostic interface: absent methods, not a guarded flag.

`submission_surface.READ_ONLY_INTERFACE_REQUIREMENT` said a flag would be
weaker because "a flag is checked at one site and can be bypassed by a
new call site written in ignorance". So the tests that matter here are
the ones proving there is nothing behind a mutation name to reach, and
the one proving an allowlist rather than a denylist -- because a denylist
fails open on the sixth mutation nobody listed, which is the mistake this
repository has already shipped twice.
"""

import pytest

from sportsassets import bettor_read_only_venue as RO
from sportsassets import submission_surface as SS


# ═════════════════════════════════════════════════════════════════════
# EVERY MUTATION IS ABSENT
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", RO.KNOWN_MUTATIONS)
def test_every_known_mutation_is_absent_from_the_interface(name):
    v = RO.read_only_venue()
    assert not hasattr(v, name)
    with pytest.raises(AttributeError):
        getattr(v, name)


@pytest.mark.parametrize("name", sorted(SS.MUTATION_NAMES))
def test_every_name_the_submission_surface_calls_a_mutation_is_absent(name):
    """Cross-checked against the surface enumeration so they cannot drift."""
    v = RO.read_only_venue()
    assert not hasattr(v, name), (
        "%s is in submission_surface.MUTATION_NAMES but reachable through "
        "the read-only interface" % name)


def test_the_two_mutation_lists_agree():
    assert set(RO.KNOWN_MUTATIONS) == set(SS.MUTATION_NAMES), (
        "the read-only interface's explanation list and the submission "
        "surface's enumeration disagree; one of them is stale")


def test_the_refusal_says_the_name_is_absent_not_merely_refused():
    v = RO.read_only_venue()
    with pytest.raises(AttributeError) as e:
        v.submit_fok
    msg = str(e.value)
    assert "no code path behind this name" in msg
    assert "route around" in msg


def test_a_mutation_name_fails_before_any_argument_is_evaluated():
    """A guard inside a function would evaluate its arguments first."""
    v = RO.read_only_venue()
    evaluated = []

    def _arg():
        evaluated.append(True)
        return 1

    with pytest.raises(AttributeError):
        v.submit_fok(_arg())        # attribute lookup fails first
    assert evaluated == [], (
        "the argument was evaluated, so a callable existed to call")


def test_dir_does_not_advertise_any_mutation():
    v = RO.read_only_venue()
    listed = set(dir(v))
    assert listed.isdisjoint(set(RO.KNOWN_MUTATIONS))
    assert listed == set(RO.READS)


# ═════════════════════════════════════════════════════════════════════
# IT IS AN ALLOWLIST: A NEW NAME FAILS CLOSED
# ═════════════════════════════════════════════════════════════════════

def test_a_sixth_mutation_added_to_the_venue_module_is_not_reachable():
    """The denylist failure mode, checked directly."""
    class FakeVenue:
        def slug_ask(self, slug):
            return 0.5

        def submit_twap(self, *a, **k):        # a mutation nobody listed
            raise AssertionError("this must be unreachable")

    v = RO.read_only_venue(FakeVenue())
    assert v.slug_ask("x") == 0.5
    assert not hasattr(v, "submit_twap")
    with pytest.raises(AttributeError) as e:
        v.submit_twap
    assert "not in the read allowlist" in str(e.value)


def test_even_a_harmless_new_read_is_unreachable_until_allowlisted():
    """The cost of failing closed, stated rather than hidden."""
    class FakeVenue:
        def a_brand_new_read(self):
            return 1

    v = RO.read_only_venue(FakeVenue())
    with pytest.raises(AttributeError):
        v.a_brand_new_read


def test_the_module_states_why_a_denylist_would_not_do():
    assert "fails OPEN" in RO.WHY_AN_ALLOWLIST
    assert "twice" in RO.WHY_AN_ALLOWLIST


# ═════════════════════════════════════════════════════════════════════
# THE ALLOWLIST IS REAL: EVERY NAME IS AN ACTUAL VENUE READ
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", RO.READS)
def test_every_allowlisted_read_exists_on_the_venue_module(name):
    from sportsassets import pmus
    assert hasattr(pmus, name), (
        "%s is allowlisted but pmus does not define it, so the allowlist "
        "has drifted from the module" % name)


@pytest.mark.parametrize("name", RO.READS)
def test_every_allowlisted_read_is_reachable(name):
    v = RO.read_only_venue()
    assert callable(getattr(v, name))


def test_an_allowlisted_name_the_module_lacks_refuses_rather_than_guesses():
    class Empty:
        pass

    v = RO.read_only_venue(Empty())
    with pytest.raises(AttributeError) as e:
        v.slug_ask
    assert "allowlist has drifted" in str(e.value)


def test_no_allowlisted_read_is_also_a_mutation_name():
    assert set(RO.READS).isdisjoint(set(SS.MUTATION_NAMES))


# ═════════════════════════════════════════════════════════════════════
# THE OBJECT CANNOT BE GRAFTED ONTO
# ═════════════════════════════════════════════════════════════════════

def test_a_caller_cannot_attach_a_mutation_to_the_object():
    v = RO.read_only_venue()
    with pytest.raises(AttributeError) as e:
        v.submit_fok = lambda *a, **k: "submitted"
    assert "graft a mutation" in str(e.value)
    assert not hasattr(v, "submit_fok")


def test_a_caller_cannot_delete_the_constraint():
    v = RO.read_only_venue()
    with pytest.raises(AttributeError):
        del v._mod


def test_slots_means_there_is_no_instance_dict_to_write_through():
    v = RO.read_only_venue()
    assert not hasattr(v, "__dict__")


# ═════════════════════════════════════════════════════════════════════
# ADOPTION IS REPORTED, NOT CLAIMED
# ═════════════════════════════════════════════════════════════════════

def test_adoption_reports_which_diagnostics_still_reach_the_venue_directly():
    a = RO.adoption()
    assert set(a["by_module"]) == set(RO.DIAGNOSTIC_MODULES)
    assert "separate work" in a["status"]
    # Every module is classified; nothing is silently omitted.
    for name, state in a["by_module"].items():
        assert state in ("CONSTRAINED", "DIRECT_VENUE_MODULE",
                         "NO_VENUE_IMPORT", "MODULE_NOT_FOUND",
                         "UNREADABLE"), (name, state)


def test_adoption_does_not_claim_the_call_sites_were_converted():
    a = RO.adoption()
    assert a["converted"] == [] or set(a["converted"]) <= set(
        RO.DIAGNOSTIC_MODULES)
    assert "reads imports" in a["reads_imports_not_runtime"] or \
        "runtime import" in a["reads_imports_not_runtime"]


def test_describe_states_what_the_interface_does_not_establish():
    d = RO.describe()
    assert "adoption at each call site" in d["not_established"] or \
        "diagnostics in this repository use this interface" in \
        d["not_established"]
    assert "permission granularity" in d["not_established"]
    assert d["refuses_with"] == "AttributeError"


# ═════════════════════════════════════════════════════════════════════
# THE REQUIREMENT'S OWN STATUS IS UPDATED, AND POINTS HERE
# ═════════════════════════════════════════════════════════════════════

def test_the_submission_surface_no_longer_records_this_as_unimplemented():
    req = SS.READ_ONLY_INTERFACE_REQUIREMENT
    assert "NOT IMPLEMENTED" not in req["status"], req["status"]
    assert "bettor_read_only_venue" in req["status"]


def test_the_requirement_still_states_why_a_flag_would_be_weaker():
    """The reasoning outlives the gap it described."""
    req = SS.READ_ONLY_INTERFACE_REQUIREMENT
    assert "cannot be made to submit by any caller" in req[
        "why_a_flag_is_weaker"]


# ═════════════════════════════════════════════════════════════════════
# THE `_mod` ESCAPE HATCH, AND THE FOUR PATHS THAT ARE STILL OPEN
# ═════════════════════════════════════════════════════════════════════
#
# The first version of this module held the venue module in a `_mod`
# slot. `__slots__` makes that a REAL attribute, so ordinary lookup found
# it, `__getattr__` was never consulted, and `v._mod.submit_fok` handed
# back every mutation. The tests above did not catch it because they
# checked `hasattr` and `setattr` -- never the escape hatch the design
# itself had created.
#
# These tests close that specific hole and then, deliberately, ASSERT
# THAT THE OBJECT IS NOT A BOUNDARY. Owner directive: "Fix _mod, but do
# not replace it with another Python wrapper and call that isolation."
# So the surviving paths are pinned as facts. If somebody later "fixes"
# one, these tests fail and force the claim to be re-measured rather than
# letting a reassurance go stale.

def test_the_mod_slot_is_gone():
    v = RO.read_only_venue()
    with pytest.raises(AttributeError):
        v._mod


def test_no_attribute_on_the_object_yields_the_venue_module():
    """The hole was not `_mod` by name; it was holding the module at all."""
    from sportsassets import pmus
    v = RO.read_only_venue()
    for name in ("_mod", "_module", "_venue", "__dict__"):
        try:
            got = getattr(v, name)
        except AttributeError:
            continue
        assert got is not pmus, (
            "%s hands back the venue module, which is the `_mod` defect "
            "under a different name" % name)


def test_the_object_does_not_claim_isolation():
    """Every self-description must say what it is not."""
    assert "not isolation" in RO.WHAT_THIS_OBJECT_IS.lower() or \
        "not a security control" in RO.WHAT_THIS_OBJECT_IS
    assert "ISOLATION OF ANY KIND" in RO.NOT_ESTABLISHED
    assert "IT IS NOT ISOLATION" in RO.__doc__


def test_the_reflection_probe_measures_rather_than_asserts():
    """It walks each path for real, so a closed path reports closed."""
    r = RO.reflection_paths_still_open()
    assert r["by_path"]["object_attribute_lookup"] == "CLOSED"
    assert r["by_path"]["the__mod_slot_that_was_removed"].startswith("CLOSED")


@pytest.mark.parametrize("path", [
    "the_reads_slot",
    "a_bound_read_s_globals",
    "importing_the_module_directly",
    "sys_modules",
])
def test_this_path_to_the_mutation_surface_is_open_and_is_recorded_as_open(
        path):
    """NOT a wish. The recorded state must match the measured state."""
    r = RO.reflection_paths_still_open()
    assert r["by_path"][path] == "OPEN", (
        "%s now reports %s. Either the path closed -- in which case "
        "re-measure and say so -- or the probe broke. Do not leave a "
        "stale reassurance in place." % (path, r["by_path"][path]))
    assert path in r["open"]


def test_the_probe_conclusion_states_the_count_it_measured():
    r = RO.reflection_paths_still_open()
    assert str(r["open_count"]) in r["conclusion"]
    assert "should be described as isolation" in r["conclusion"]


# ═════════════════════════════════════════════════════════════════════
# THE AUTHORITY BOUNDARY, INCLUDING WHERE IT IS ONLY A CONVENTION
# ═════════════════════════════════════════════════════════════════════

def test_the_boundary_names_the_credential_as_the_real_control():
    from sportsassets import submission_surface as SS
    b = RO.authority_boundary()
    c = b["venue_credential"]
    assert set(c["what_grants_order_authority"]) == set(
        SS.CREDENTIAL_SETTINGS)
    assert "ABSENCE" in c["enforced_by"]


def test_read_only_credential_capability_is_not_assumed_to_exist():
    """Standing owner constraint, not a preference."""
    c = RO.authority_boundary()["venue_credential"]
    assert c["granularity"] == "NOT_ESTABLISHED"
    assert "must not assume" in c["why_not_established"]
    assert "able to trade" in c["why_not_established"]


def test_there_is_exactly_one_database_credential_and_that_is_stated():
    """Measured: the package reads a single DSN setting."""
    import os
    import re
    root = os.path.dirname(os.path.abspath(RO.__file__))
    found = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if not f.endswith(".py"):
                continue
            with open(os.path.join(dirpath, f), "r",
                      encoding="utf-8", errors="replace") as fh:
                found |= set(re.findall(r"[A-Z_]*DATABASE_URL[A-Z_]*",
                                        fh.read()))
    db = RO.authority_boundary()["database"]
    assert found == set(db["dsn_settings"]), (found, db["dsn_settings"])
    assert db["credentials_found"] == len(found)


def test_the_boundary_states_that_the_kill_switch_row_is_writable():
    db = RO.authority_boundary()["database"]
    assert "NO READ-ONLY DATABASE ROLE" in db["consequence"]
    from sportsassets import execution_gate as EG
    assert EG.PAUSE_KEY in db["consequence"]
    assert "mirror_loss_stop" in db["consequence"]


def test_the_heartbeat_example_rests_on_the_keys_being_the_loop_s_own():
    """The narrow claim, not 'the loop writes the kill switch'.

    A caller DOES pass `key=` -- `key=STANDBY_KEY`. What makes the loop
    safe is that both keys its callers name are module-level constants
    holding that loop's own rows. What is missing is a PERMISSION.
    """
    db = RO.authority_boundary()["database"]
    ex = db["worked_example"]
    assert "BOUND PARAMETER" in ex
    assert "MODULE-LEVEL LITERAL" in ex
    assert "not a grant" in ex


def test_the_boundary_records_that_i_first_reported_this_wrong():
    db = RO.authority_boundary()["database"]
    assert "NO CALLER PASSES ANOTHER KEY" in db["i_first_reported_this_wrong"]
    assert "AST" in db["i_first_reported_this_wrong"]


def test_every_key_passed_to_the_rn1x_heartbeat_is_one_of_its_own_rows():
    """The source fact the worked example rests on, by AST not by grep.

    A grep for `_heartbeat(.*key=` missed the one real call site because
    its `key=` was on the next line. This walks the call nodes: each
    `key=` must be a bare module-level Name, and that constant's value
    must be one of this loop's own heartbeat rows -- never a control key.
    """
    import ast
    import os
    from sportsassets import execution_gate as EG
    path = os.path.join(os.path.dirname(os.path.abspath(RO.__file__)),
                        "workers", "rn1x_model_loop.py")
    if not os.path.exists(path):
        pytest.skip("rn1x_model_loop not present")
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(
                node.value, ast.Constant) and isinstance(
                    node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = node.value.value

    control_keys = {EG.PAUSE_KEY, "mirror_loss_stop", "copy_overspend_halt",
                    "LIVE_COPY_HALT"}
    own = {consts.get("HEARTBEAT_KEY"), consts.get("STANDBY_KEY")}
    own.discard(None)
    assert own, "neither heartbeat key constant was found"

    seen = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_heartbeat"):
            continue
        for kw in node.keywords:
            if kw.arg != "key":
                continue
            seen += 1
            assert isinstance(kw.value, ast.Name), (
                "line %d computes the heartbeat key instead of naming a "
                "module constant; the boundary note assumes a literal"
                % node.lineno)
            val = consts.get(kw.value.id)
            assert val in own, (
                "line %d passes key=%s (%r), which is not one of this "
                "loop's own rows %r" % (node.lineno, kw.value.id, val, own))
            assert val not in control_keys, (
                "line %d writes a CONTROL key" % node.lineno)
    assert seen == 1, (
        "expected exactly the one known key= call site, found %d; "
        "re-verify the worked example in authority_boundary()" % seen)


def test_the_database_remedy_is_stated_as_not_implemented():
    db = RO.authority_boundary()["database"]
    assert "NOT IMPLEMENTED" in db["what_would_make_it_a_permission"]


def test_process_separation_is_called_a_convention_not_a_permission():
    p = RO.authority_boundary()["process"]
    assert "not separate security principals" in p[
        "separation_is_convention_not_permission"]
    assert "nothing outside our own source prevents" in p[
        "separation_is_convention_not_permission"]


def test_the_honest_summary_does_not_end_on_a_reassurance():
    b = RO.authority_boundary()
    s = b["honest_summary"]
    assert "no separation at all" in s
    assert "accident guard" in s


def test_describe_carries_the_boundary_and_the_open_paths():
    d = RO.describe()
    assert d["reflection_paths_still_open"]["open_count"] == 4
    assert d["authority_boundary"]["database"]["credentials_found"] == 1
