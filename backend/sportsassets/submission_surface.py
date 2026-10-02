"""WHAT CAN SEND AN ORDER, ENUMERATED. And what "unverified" actually licenses.

TWO CLAIMS OF MINE THIS CORRECTS.

──  1. "OUR TWO CODE CONSTANTS ARE THE ONLY THING BETWEEN A PROVISIONED
       CREDENTIAL AND ORDER AUTHORITY."

That was an assertion, and it is wrong in both directions at once. It OVERSTATES
the exposure -- there are more gates than two, each independently sufficient to
refuse -- and it UNDERSTATES the problem, because a credential reaches more than
one lane and neither constant guards the others.

    ABSENCE OF DOCUMENTED SCOPES MEANS PERMISSION GRANULARITY IS UNVERIFIED.

It does NOT mean every issued key carries every permission. The venue may well
issue narrow keys and simply not document them. "Unverified" licenses exactly one
action: look at the provisioning screen. It licenses no conclusion about what a
key can do, in either direction.

──  2. "SIX MODULES CAN WRITE live_orders."

True, and a WRITER INVENTORY IS NOT A TRADER CENSUS. That six modules contain a
write statement does not establish that six modules currently trade this account:
each needs an account binding, a credential, and an enabled control path, and
none of those was checked. Until they are, exposure and writer isolation are
UNKNOWN -- which is a worse-sounding answer than "six writers" and a more honest
one.

WHAT THIS MODULE DOES. It enumerates, from the source:

  * every module that READS the venue credential -- the blast radius of
    provisioning one, which is the "must not accidentally enable another lane"
    question;
  * the venue MUTATION SURFACE -- the functions that can create or cancel an
    order at all -- and every caller of each;
  * every GATE in front of them, with what clears it, so "two constants" is
    replaced by the actual list.

IT IS STILL STATIC. It reads imports and call sites; it does not observe a
running process. So it bounds the surface and does not certify the behaviour, and
`LIMITS` says so.
"""

from __future__ import annotations

import ast
import os

VERSION = "SUBMISSION_SURFACE_V1"

_HERE = os.path.dirname(os.path.abspath(__file__))

#: The settings names that ARE the venue credential.
CREDENTIAL_SETTINGS = ("pmus_key_id", "pmus_secret_key",
                       "PMUS_KEY_ID", "PMUS_SECRET_KEY")

#: THE VENUE MUTATION SURFACE -- AND IT IS NOT ONE ADAPTER.
#:
#: MY FIRST VERSION OF THIS TABLE LISTED TWO FUNCTIONS IN `pmus`, AND THAT WAS
#: WRONG IN THE REASSURING DIRECTION. `live_executor._submit_fok` does not go
#: through `pmus.submit_fok` at all: it builds its own `py_clob_client` and calls
#: `post_order` directly, which is a complete SECOND submission path, to a
#: SECOND venue (polymarket-clob). The repository had already found that on
#: 2026-09-21 by walking the AST for venue-client constructors, and recorded that
#: no hand-written inventory had ever listed it -- "including the one I wrote the
#: day before". I then wrote another hand inventory and repeated the mistake.
#:
#: So the surface is enumerated per venue, and the scanner looks for the CLOB
#: primitives too.
MUTATION_SURFACE = {
    "pmus.submit_fok": "polymarket-us: creates an order (sell=True exits one)",
    "pmus.cancel_order": "polymarket-us: cancels an outstanding order",
    "pmus.close_position": "polymarket-us: closes a held position",
    "post_order": "polymarket-CLOB: submits a built order. A SECOND venue and a "
                  "SECOND path -- it does not pass through pmus at all",
    "create_order": "polymarket-CLOB: builds the order post_order sends",
    "execmirror.Venue.place / cancel / cancel_all / close": (
        "polymarket-us, a SEPARATE ACCOUNT: the 1:1,000 execution mirror "
        "(execmirror.py). Its own credential (PMUS_EXECMIRROR_KEY_ID / "
        "_SECRET_KEY, never PMUS_KEY_ID), its own durable control "
        "(execmirror_control, off by default, emergency stop), an account "
        "fingerprint checked every cycle and a per-order notional cap. It "
        "does not pass through pmus or the funded gates by design: it trades "
        "only the mirror account, only from new paper orders after the "
        "cutover"),
}

#: The names the scanner treats as venue mutations.
MUTATION_NAMES = frozenset({"submit_fok", "cancel_order", "close_position",
                            "post_order", "create_order"})

#: EVERY GATE IN FRONT OF A FUNDED ORDER, and what clears each. Independent:
#: any ONE of them refusing is sufficient, so the count is not the exposure.
#:
#: This replaces "two constants". It is read against the source by
#: `test_the_submission_surface_is_enumerated_not_asserted`.
GATES = (
    {"n": 1, "gate": "FUNDED_SUBMISSION_ENABLED",
     "where": "bettor_funded_execution",
     "kind": "CODE_CONSTANT", "cleared_by": "a code change through the gate"},
    {"n": 2, "gate": "REAL_ORDER_SUBMISSION_ENABLED",
     "where": "bettor_entry_execution",
     "kind": "CODE_CONSTANT", "cleared_by": "a code change through the gate"},
    {"n": 3, "gate": "FUNDED_EXIT_SUBMISSION_ENABLED",
     "where": "bettor_funded_management",
     "kind": "CODE_CONSTANT",
     "cleared_by": "a code change, SEPARATELY from the entry switch"},
    {"n": 4, "gate": "POLICY_ADMISSION_ENABLED",
     "where": "bettor_admission_policy",
     "kind": "CODE_CONSTANT", "cleared_by": "a code change through the gate"},
    {"n": 5, "gate": "execution_gate",
     "where": "INSIDE pmus.submit_fok / pmus.close_position, and invoked "
              "explicitly in live_executor's CLOB path",
     "kind": "PROCESS_BOUND_GATE",
     "cleared_by": "a bound, unpaused gate at submission time; denial RAISES "
                   "inside the adapter rather than returning",
     "covers_every_caller_because": (
         "it is inside the adapter, not at the call sites. So all four routes "
         "through `pmus` are covered however they are invoked -- including the "
         "three that pass the function as a callable to asyncio.to_thread, which "
         "no call-site gate would have caught. The fifth route, the CLOB path in "
         "live_executor._submit_fok, does not pass through pmus at all and "
         "carries its own explicit _gate.authorize('submit_clob')"),
     "and_this_is_the_only_gate_that_covers_the_LEGACY_lanes": (
         "gates 1-4 and 6-8 guard the FUNDED lane. live_executor, mirror_live "
         "and underdog are not behind any of them")},
    {"n": 6, "gate": "a recorded submission authorization",
     "where": "bettor_funded_activation / bettor_entry_execution",
     "kind": "DATABASE_ROW",
     "cleared_by": "an authorization bound to account, venue, limits and "
                   "expiry, unrevoked"},
    {"n": 7, "gate": "an ELIGIBLE, reconciled account",
     "where": "bettor_account_onboarding",
     "kind": "DATABASE_STATE",
     "cleared_by": "four complete venue reads that reconcile"},
    {"n": 8, "gate": "a readable account-wide exposure total inside its cap",
     "where": "bettor_entry_execution.authorize_submission",
     "kind": "MEASUREMENT",
     "cleared_by": "a fresh measurement for THIS account under the cap"},
    {"n": 9, "gate": "the venue credential",
     "where": "the service environment",
     "kind": "CREDENTIAL",
     "cleared_by": "provisioning PMUS_KEY_ID / PMUS_SECRET_KEY"},
    {"n": 10, "gate": "the funded schema",
     "where": "bettor_funded_schema",
     "kind": "SCHEMA",
     "cleared_by": "applying the funded migrations to that database"},
)


def gate_count() -> int:
    return len(GATES)


#: WHAT A READ-ONLY DIAGNOSTIC MUST NOT BE ABLE TO DO.
#:
#: The user's point, and it is the right shape: a diagnostic that holds the
#: credential in order to READ balances must not be able to reach the mutation
#: surface at all. A constant that says "do not submit" is a different thing from
#: an interface that cannot submit.
READ_ONLY_INTERFACE_REQUIREMENT = {
    "requirement": (
        "a read-only diagnostic must be given an interface that EXPOSES NO "
        "MUTATION, not merely a flag telling it not to mutate"),
    "why_a_flag_is_weaker": (
        "a flag is checked at one site and can be bypassed by a new call site "
        "written in ignorance. An object that has no submit method cannot be "
        "made to submit by any caller"),
    "status": (
        "IMPLEMENTED as `bettor_read_only_venue.ReadOnlyVenue`: an object "
        "whose mutation names are ABSENT rather than guarded, so `hasattr` "
        "is False and attribute lookup fails before any argument is "
        "evaluated. It is an ALLOWLIST of reads, not a denylist of writes, "
        "so a sixth mutation added later is unreachable until somebody "
        "allowlists it by name -- the denylist failure mode this repository "
        "has already shipped twice. ADOPTION at each diagnostic call site "
        "is separate work and `bettor_read_only_venue.adoption()` reports "
        "which still import `pmus` directly rather than claiming they were "
        "converted"),
    "what_exists_today": (
        "the interface exists and refuses; the diagnostics that have not "
        "yet been handed it still call the same `pmus` module the "
        "submitting lane calls, and `adoption()` names them"),
    "still_unverified": (
        "the venue's own permission granularity. This constrains OUR "
        "interface, not the credential's scope"),
}

#: WHAT STATIC READING CANNOT SETTLE. Named so the enumeration is not read as a
#: certification.
LIMITS = (
    "this reads imports and call sites, not a running process",
    "a module that CAN write a table is not a module that currently trades an "
    "account: that needs an account binding, a credential and an enabled "
    "control path, none of which a source read establishes",
    "a runtime import by name, a subprocess, or a service outside this "
    "repository holding the same credential would all be missed",
    "the venue's actual permission granularity is unverified either way",
)


def _imports(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level and not mod:
                for a in node.names:
                    out.add(a.name)
            else:
                out.add(mod)
                for a in node.names:
                    out.add((mod + "." + a.name) if mod else a.name)
    return out


def _scan(pattern_fn) -> dict:
    """Walk the package and collect files whose source matches."""
    hits = {}
    for dirpath, dirnames, filenames in os.walk(_HERE):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    src = fh.read()
            except OSError:
                continue
            found = pattern_fn(src, p)
            if found:
                hits[os.path.relpath(p, _HERE)] = found
    return hits


#: Files that NAME the credential without reading it, each with the reason.
#: Listed rather than filtered by a heuristic, so the exclusion is a decision on
#: the record and a new one cannot slip in silently.
NOT_CONSUMERS = {
    "config.py": "the declaration itself, not a consumer",
    "bettor_evidence_store.py": "a REDACTION list -- it names the settings so "
                                "their values are never written to evidence",
    "submission_surface.py": "this module, which names them to scan for them",
}


def credential_consumers() -> dict:
    """Every module that reads the venue credential.

    THIS IS THE BLAST RADIUS OF PROVISIONING ONE. The question "must not
    accidentally enable another lane" is answered by this list, not by a promise.

    A NAME IN PROSE IS NOT A READ. The first version matched any occurrence of
    the setting name, which counted a redaction list and this module's own scan
    constants as consumers. The three known non-consumers are excluded BY NAME
    with a reason, so the exclusion is auditable.
    """
    def _m(src, _p):
        return sorted({n for n in CREDENTIAL_SETTINGS if n in src})

    hits = _scan(_m)
    for name in NOT_CONSUMERS:
        hits.pop(name, None)
    return {
        "excluded_as_non_consumers": dict(NOT_CONSUMERS),
        "modules": dict(sorted(hits.items())),
        "count": len(hits),
        "and_what_this_means_for_provisioning": (
            "one credential in the service environment is visible to EVERY "
            "module listed. Provisioning it for onboarding reads makes it "
            "available to the submitting lane in the same process, so the "
            "separation has to come from the gates and the interface -- not "
            "from which module was 'meant' to use it"),
    }


def mutation_callers() -> dict:
    """Every call site of the two functions that can change an order.

    PARSED, NOT SUBSTRING-MATCHED. The first version looked for `submit_fok(`,
    which matched three DOCSTRINGS -- `bettor_pilot_prerequisites`, `venue_pace`
    and a comment -- and reported them as callers. A module that describes the
    adapter is not a module that reaches it, and inflating this list would make
    the surface look wider than it is in a report about how wide it is.
    """
    def _name_of(node):
        return (node.attr if isinstance(node, ast.Attribute)
                else node.id if isinstance(node, ast.Name) else None)

    def _m(src, p):
        if os.path.basename(p) in ("pmus.py", "pmx.py"):
            return []                    # the adapters themselves
        try:
            tree = ast.parse(src, filename=p)
        except SyntaxError:
            return []
        got = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # (a) a direct call: `mod.submit_fok(...)` or `submit_fok(...)`.
            nm = _name_of(node.func)
            if nm in MUTATION_NAMES:
                got.add(nm)
            # (b) A REFERENCE PASSED AS AN ARGUMENT, which the first version
            # missed entirely. `asyncio.to_thread(pmus.submit_fok, slug, ...)`
            # and `functools.partial(pmus.cancel_order, ...)` submit just as
            # surely as a direct call, and `live_executor` reaches the venue
            # BOTH of those ways.
            for arg in list(node.args) + [k.value for k in node.keywords]:
                anm = _name_of(arg)
                if anm in MUTATION_NAMES:
                    got.add(anm + " (passed as a callable)")
        return sorted(got)

    hits = {k: v for k, v in _scan(_m).items() if v}
    return {
        "surface": dict(MUTATION_SURFACE),
        "callers": dict(sorted(hits.items())),
        "count": len(hits),
    }


def describe() -> dict:
    """The whole enumeration, with its corrections and its limits."""
    return {
        "version": VERSION,
        "gates": list(GATES),
        "gate_count": gate_count(),
        "and_the_two_constants_claim_was_wrong_twice": (
            "I said two code constants were the only thing between a "
            "provisioned credential and order authority. There are %d "
            "independent gates, any ONE of which refuses -- so it overstated "
            "the exposure. And a credential reaches every module below, which "
            "neither constant guards -- so it understated the problem."
            % gate_count()),
        "credential_consumers": credential_consumers(),
        "mutation_callers": mutation_callers(),
        "read_only_interface": dict(READ_ONLY_INTERFACE_REQUIREMENT),
        "what_unverified_scopes_license": (
            "looking at the provisioning screen. NOT a conclusion that a key "
            "carries every permission, and not one that it carries few"),
        "limits": list(LIMITS),
    }
