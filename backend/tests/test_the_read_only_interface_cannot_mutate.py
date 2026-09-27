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
