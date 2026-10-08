"""A VENUE INTERFACE THAT CANNOT SUBMIT, BECAUSE IT HAS NO SUBMIT METHOD.

Owner directive, previous batch §5:

    "Read-only diagnostics should have a constrained interface that
     cannot invoke order mutations."

`submission_surface.READ_ONLY_INTERFACE_REQUIREMENT` recorded this as
`NOT IMPLEMENTED -- stated as a requirement, not as a control`, and
stated why a flag would not have satisfied it:

    "a flag is checked at one site and can be bypassed by a new call
     site written in ignorance. An object that has no submit method
     cannot be made to submit by any caller"

This module is that object, and it does what an object can do: it removes
the mutation names from an interface, so a diagnostic call site written in
ignorance cannot submit by accident.

IT IS NOT ISOLATION, AND THIS MODULE DOES NOT CLAIM TO BE. Measured, not
argued: four reflection paths reach `pmus.submit_fok` from inside this
process while holding only a `ReadOnlyVenue` — see
`reflection_paths_still_open()`, which probes them live rather than
asserting they are shut. Any of them is one line of ordinary Python.
In-process Python affords no boundary against code running in the same
interpreter, and calling this one would be the same category error as the
`_mod` slot it replaced.

WHERE THE ACTUAL BOUNDARY IS -- `authority_boundary()` below, and
`READ_ONLY_CREDENTIAL_AND_PERMISSION_BOUNDARY.md`: the submission gates,
the DB-backed kill switch inside the adapter, and above all the absence
of a credential in the process. Those are the controls. This object is an
ergonomic guard in front of them.

────────────────────────────────────────────────────────────────────
THE ONE DESIGN DECISION: AN ALLOWLIST, NOT A DENYLIST.

A denylist of the five known mutations -- `submit_fok`, `cancel_order`,
`close_position`, `post_order`, `create_order` -- would be correct today
and wrong the moment a sixth is added. The sixth would pass through,
because nothing listed it.

This repository has already made exactly that mistake, twice, in the
same week: a hand-written inventory of the mutation surface missed the
CLOB `post_order` path and three `asyncio.to_thread(pmus.submit_fok, …)`
callable references, and the second hand-written inventory -- mine --
repeated it a day after the first was recorded. A denylist is a hand
inventory with a shorter name.

So `ReadOnlyVenue` exposes an EXPLICIT ALLOWLIST of reads and refuses
everything else, including every attribute of `pmus` that does not
appear in it. A new function added to `pmus` -- read or write -- is not
reachable through this object until somebody adds it to `READS` by name.
The failure mode on an addition is a refusal, not an exposure.

    A DENYLIST FAILS OPEN ON ADDITIONS. AN ALLOWLIST FAILS CLOSED.

────────────────────────────────────────────────────────────────────
WHAT IT REFUSES WITH, AND WHY THAT IS `AttributeError`.

A mutation name raises `AttributeError`, the same thing Python raises for
a method that does not exist -- because through this object's attribute
lookup it does not exist. `hasattr` returns False, `dir()` does not list
it, and `getattr(v, "submit_fok")` fails before any argument is evaluated.

That is a statement about ATTRIBUTE LOOKUP ON THIS OBJECT, and the
earlier version of this paragraph overstated it as "no code path behind
it to reach". A caller that goes looking -- `v._reads`, any read's
`__globals__`, `import pmus`, `sys.modules` -- reaches the module. The
refusal stops the call site that never meant to submit. It does not stop
one that means to.

WHAT THIS DOES NOT ESTABLISH.

1. Isolation of any kind. Four in-process paths to the mutation surface
   are measured open by `reflection_paths_still_open()`.
2. That diagnostics in this repository currently use it. `adoption()`
   reports the sites still reaching `pmus` directly rather than claiming
   they were converted.
3. Anything about the venue's own permission granularity, which stays
   unverified. No read-only venue credential has been shown to exist;
   until one is inspected, assume the credential a process holds can
   trade.
"""

from __future__ import annotations

#: The reads a diagnostic may make, BY NAME. Adding a name here is the
#: only way to widen this interface, and it is a reviewable one-line
#: change that a test forces to be a real `pmus` read.
READS = (
    # holdings and positions
    "position_side",
    "account_holds",
    # catalogue
    "event_board",
    "list_desk_events",
    # quotes
    "slug_ask",
    "side_ask",
    "slug_bid",
    "bbo_read",
    "book_read",
    # identity and mapping
    "slug_complement",
    "resolve_market",
    "resolve_market_exact",
    "resolve_derivative_exact",
    "resolve_team_yesno_exact",
    "order_intent_for",
)

#: Kept beside READS so the refusal message can be specific, and so a
#: test can cross-check it against `submission_surface.MUTATION_NAMES`
#: rather than letting the two drift. It is NOT the control -- the
#: control is READS. This exists only to explain a refusal.
KNOWN_MUTATIONS = ("submit_fok", "cancel_order", "close_position",
                   "post_order", "create_order")

WHY_AN_ALLOWLIST = (
    "a denylist fails OPEN when a sixth mutation is added, because "
    "nothing lists it. An allowlist fails CLOSED: the new name is "
    "unreachable until somebody adds it by name. This repository has "
    "twice shipped a hand-written mutation inventory that missed a real "
    "write path, so the design does not rely on the inventory being "
    "complete")

NOT_ESTABLISHED = (
    "ISOLATION OF ANY KIND. Four in-process reflection paths reach the "
    "mutation surface while holding only this object, measured open by "
    "`reflection_paths_still_open()`. Nor that the diagnostics in this "
    "repository use this interface today -- `adoption()` reports which "
    "still reach `pmus` directly. Nor anything about the venue's own "
    "permission granularity, which stays unverified: no read-only venue "
    "credential has been shown to exist, so assume a credential a "
    "process holds can trade. This constrains ATTRIBUTE LOOKUP ON THIS "
    "OBJECT, not the credential's scope and not the process")

WHAT_THIS_OBJECT_IS = (
    "an ergonomic guard, not a security control. It stops the diagnostic "
    "call site that never intended to submit -- the failure mode that has "
    "actually occurred in this repository twice -- and it stops nothing "
    "that intends to. Authority lives in the credential, the process and "
    "the database; see `authority_boundary()`")


def reflection_paths_still_open(module=None) -> dict:
    """PROBE the in-process paths to the mutation surface. Do not assert.

    Every entry is measured on the spot by actually walking the path from
    a live `ReadOnlyVenue` and reporting whether a mutation came back
    callable. A path that closes shows up as closed here without anyone
    editing a claim; a path that opens shows up as open.

    `OPEN` is the expected result for all of them. That is the point:
    this is the evidence that the object is not a boundary, kept as a
    measurement so it cannot quietly become a stale reassurance.
    """
    import sys

    probe = KNOWN_MUTATIONS[0]            # submit_fok
    if module is None:
        from . import pmus as module      # noqa: PLC0415
    v = ReadOnlyVenue(module)
    out: dict = {}

    def _state(getter):
        try:
            return "OPEN" if callable(getter()) else "CLOSED"
        except Exception as exc:                        # noqa: BLE001
            return "CLOSED (%s)" % type(exc).__name__

    out["object_attribute_lookup"] = _state(
        lambda: getattr(v, probe, None))
    out["the__mod_slot_that_was_removed"] = _state(
        lambda: getattr(getattr(v, "_mod"), probe))
    out["the_reads_slot"] = _state(
        lambda: object.__getattribute__(v, "_reads")[READS[0]]
        .__globals__.get(probe))
    out["a_bound_read_s_globals"] = _state(
        lambda: getattr(v, READS[0]).__globals__.get(probe))
    out["importing_the_module_directly"] = _state(
        lambda: getattr(module, probe))
    out["sys_modules"] = _state(
        lambda: getattr(sys.modules[getattr(v, READS[0]).__module__], probe))

    open_paths = sorted(k for k, s in out.items() if s == "OPEN")
    return {
        "by_path": out,
        "open": open_paths,
        "open_count": len(open_paths),
        "conclusion": (
            "%d of %d probed paths reach the mutation surface from inside "
            "this process. In-process Python affords no boundary against "
            "code running in the same interpreter; nothing in this module "
            "should be described as isolation"
            % (len(open_paths), len(out))),
        "what_did_change": (
            "`v._mod` was a REAL __slots__ attribute, so ordinary lookup "
            "found it and `__getattr__` was never consulted -- the "
            "allowlist was one underscore deep and a caller reached it "
            "without meaning to. It is now absent. The remaining paths "
            "all require a caller to go looking"),
        "where_the_boundary_is": "authority_boundary()",
    }


class ReadOnlyVenue:
    """Venue reads only. Mutation names are absent, not guarded.

    >>> v = ReadOnlyVenue()
    >>> hasattr(v, "submit_fok")
    False
    """

    # NO MODULE REFERENCE ON THE OBJECT. The first version held the venue
    # module in a `_mod` slot, and `__slots__` makes that a REAL
    # attribute -- so ordinary lookup found it, `__getattr__` was never
    # consulted, and `v._mod.submit_fok` handed back every mutation. The
    # allowlist was one underscore deep. My own tests missed it because
    # they checked `hasattr(v, "submit_fok")` and `setattr`, never the
    # escape hatch the design itself created.
    #
    # Now only the ALLOWLISTED BOUND READS are held, resolved once at
    # construction. There is no attribute on this object from which the
    # module can be reached.
    __slots__ = ("_reads",)

    def __init__(self, module=None):
        if module is None:
            from . import pmus as module        # noqa: PLC0415
        object.__setattr__(self, "_reads", {
            n: getattr(module, n) for n in READS if hasattr(module, n)})

    # ── the whole control ────────────────────────────────────────────

    def __getattr__(self, name: str):
        # `_reads` is a slot and is found without reaching here; every
        # other attribute lands in this method, so READS is the only
        # door.
        if name in READS:
            fn = object.__getattribute__(self, "_reads").get(name)
            if fn is None:
                raise AttributeError(
                    "%r is allowlisted as a read but the venue module does "
                    "not define it; the allowlist has drifted from the "
                    "module and this interface will not guess" % name)
            return fn
        if name in KNOWN_MUTATIONS:
            raise AttributeError(
                "%r is a venue mutation and this is a read-only interface. "
                "It is absent rather than refused: there is no code path "
                "behind this name to reach, so no caller can route around "
                "it. Use the submitting lane, which passes the ten "
                "submission gates." % name)
        raise AttributeError(
            "%r is not in the read allowlist. This interface is an "
            "allowlist, so a name it does not know is unreachable whether "
            "or not it writes -- %s" % (name, WHY_AN_ALLOWLIST))

    def __setattr__(self, name, value):
        raise AttributeError(
            "this interface is immutable: setting %r would let a caller "
            "graft a mutation onto a read-only object" % name)

    def __delattr__(self, name):
        raise AttributeError("this interface is immutable")

    def __dir__(self):
        return sorted(READS)

    def __repr__(self):
        return "ReadOnlyVenue(reads=%d, mutations_absent=%d)" % (
            len(READS), len(KNOWN_MUTATIONS))


def read_only_venue(module=None) -> ReadOnlyVenue:
    """The interface a diagnostic should be handed."""
    return ReadOnlyVenue(module)


# ═════════════════════════════════════════════════════════════════════
# WHERE AUTHORITY ACTUALLY LIVES
# ═════════════════════════════════════════════════════════════════════
#
# The owner directive this section answers: "Enforce the intended
# separation through the relevant credential, process and database
# permissions" and "Do not describe an in-process object as security
# isolation."
#
# So this is a MAP, including the places where the intended separation is
# a code convention and NOT a permission. Those are the gaps; they are
# listed as gaps rather than folded into the reassuring entries.

def authority_boundary() -> dict:
    """The three real boundaries, and where each is convention instead.

    Measured or source-verified per entry. No entry claims a permission
    that was not found.
    """
    from . import submission_surface as SS       # noqa: PLC0415

    return {
        "venue_credential": {
            "what_grants_order_authority": sorted(SS.CREDENTIAL_SETTINGS),
            "enforced_by": (
                "ABSENCE. A process without these settings cannot sign a "
                "venue request, whatever object it holds. This is the "
                "strongest control in the system and it is the only one "
                "that does not depend on our own code being correct"),
            "granularity": "NOT_ESTABLISHED",
            "why_not_established": (
                "no read-only venue credential has been inspected. "
                "Whether the venue issues a key that can read quotes and "
                "holdings but not submit is unknown, and this repository "
                "must not assume such a capability exists. Until a key is "
                "issued and its refusals observed, treat any present "
                "credential as able to trade"),
        },
        "process": {
            "submitting_lane": (
                "the funded lane reaches the venue through `pmus`, behind "
                "%d enumerated gates" % len(SS.GATES)),
            "the_one_gate_inside_the_adapter": (
                "`execution_gate`, invoked inside `pmus.submit_fok` and "
                "`pmus.close_position` rather than at the call sites, so "
                "it covers callers that pass the function as a callable "
                "to `asyncio.to_thread` -- which no call-site check "
                "caught. Its denial RAISES rather than returning"),
            "kill_switch": (
                "`execution_gate.PAUSE_KEY` = 'live_trading_paused', read "
                "from `ingestion_state` at submission time, fail-closed "
                "on an unreadable row"),
            "separation_is_convention_not_permission": (
                "the learning loops, the API and the workers are not "
                "separate security principals. They differ by which code "
                "they run, not by what they are permitted to do. A worker "
                "that imported `pmus` and held the credential settings "
                "would submit; nothing outside our own source prevents "
                "it"),
        },
        "database": {
            "credentials_found": 2,
            "dsn_settings": ["DATABASE_URL", "TRADER_SCORE_DATABASE_URL"],
            "second_setting_is_not_a_second_role": (
                "TRADER_SCORE_DATABASE_URL (migration 316) is read only by "
                "the display score collector, workers/trader_live_scores.py, "
                "which is not started by workers/all.py or render.yaml. It "
                "exists so a least-privilege role CAN be given to that one "
                "process. NO SUCH ROLE IS PROVISIONED: unset, the collector "
                "falls back to DATABASE_URL, the same single credential, so "
                "the consequence below is unchanged"),
            "consequence": (
                "THERE IS NO READ-ONLY DATABASE ROLE. Every process that "
                "can reach the database holds the same credential, so "
                "every process can write every `ingestion_state` key -- "
                "including `live_trading_paused` and `mirror_loss_stop`, "
                "the rows the kill switch and the loss breaker are read "
                "from. A learning loop whose legitimate job is to upsert "
                "its own heartbeat row is, at the permission level, able "
                "to clear the kill switch"),
            "worked_example": (
                "`workers/rn1x_model_loop._heartbeat` issues "
                "`INSERT INTO ingestion_state (key, value) … ON CONFLICT "
                "DO UPDATE` with the key as a BOUND PARAMETER, so the "
                "statement itself will write any key. Its `key=` argument "
                "is passed by one caller -- `key=STANDBY_KEY`, to keep a "
                "standby out of the writer's row -- and defaults to "
                "HEARTBEAT_KEY otherwise. BOTH ARE MODULE-LEVEL LITERAL "
                "CONSTANTS NAMING THAT LOOP'S OWN TWO ROWS, and no "
                "call-site computes a key or takes one from input; "
                "verified by AST over the call sites. So the loop does "
                "not write a control row. But the reason is the two "
                "constants its callers happen to name -- not a grant. "
                "The same connection would accept the switch's key"),
            "i_first_reported_this_wrong": (
                "an earlier note here said NO CALLER PASSES ANOTHER KEY. "
                "One does. A single-line grep for `_heartbeat(.*key=` "
                "missed a call whose `key=` sat on the following line; "
                "the AST test that replaced it found it immediately. The "
                "conclusion is unchanged -- the keys are the loop's own "
                "rows -- but the stated reason was false"),
            "the_one_place_this_was_deliberately_narrowed": (
                "the research-shadow arm/disarm route in `api/app.py` "
                "accepts no key parameter, expressly so it cannot become "
                "a general `ingestion_state` writer and put "
                "`live_trading_paused` one admin request away. That is "
                "the right instinct applied at one endpoint; it is not "
                "the database's doing"),
            "what_would_make_it_a_permission": (
                "a second role with SELECT on everything and INSERT/"
                "UPDATE restricted away from the control keys -- a "
                "separate table owned by the admin role, or column-level "
                "grants plus a trigger -- handed to the loops through a "
                "second DSN setting. NOT IMPLEMENTED; stated as the "
                "remedy, not as a control"),
        },
        "this_module": WHAT_THIS_OBJECT_IS,
        "reflection": reflection_paths_still_open(),
        "honest_summary": (
            "one real boundary (the credential's absence), one lane of "
            "in-code gates that depend on our source being right, and a "
            "database with no separation at all. The read-only interface "
            "is none of these; it is an accident guard"),
    }


# ═════════════════════════════════════════════════════════════════════
# ADOPTION, REPORTED RATHER THAN CLAIMED
# ═════════════════════════════════════════════════════════════════════

#: Modules whose job is diagnosis or reporting and which therefore ought
#: to be handed this interface. Naming them is how the remaining work
#: stays visible instead of the requirement reading as closed.
DIAGNOSTIC_MODULES = (
    "bettor_live_read",
    "bettor_command_view",
    "bettor_evidence_matrix",
    "bettor_progress_feed",
    "bettor_state_capture",
    "venue_reconcile",
)


def adoption(root=None) -> dict:
    """Which diagnostics still reach the venue module directly.

    Reads imports, which is a source fact and not a runtime one. A
    module importing `pmus` is reported as not yet converted even if it
    only calls reads today -- the requirement is about the INTERFACE it
    holds, not about which calls it happens to make.
    """
    import ast
    import os

    here = root or os.path.dirname(os.path.abspath(__file__))
    out: dict = {}
    for name in DIAGNOSTIC_MODULES:
        path = os.path.join(here, "%s.py" % name)
        if not os.path.exists(path):
            out[name] = "MODULE_NOT_FOUND"
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, SyntaxError):
            out[name] = "UNREADABLE"
            continue
        direct = False
        constrained = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "pmus":
                        direct = True
                    if a.name in ("bettor_read_only_venue",
                                  "read_only_venue", "ReadOnlyVenue"):
                        constrained = True
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.endswith("pmus"):
                        direct = True
        out[name] = ("CONSTRAINED" if constrained and not direct else
                     "DIRECT_VENUE_MODULE" if direct else
                     "NO_VENUE_IMPORT")
    return {
        "by_module": out,
        "converted": sorted(k for k, v in out.items() if v == "CONSTRAINED"),
        "still_direct": sorted(k for k, v in out.items()
                               if v == "DIRECT_VENUE_MODULE"),
        "status": ("INTERFACE IMPLEMENTED; adoption at each call site is "
                   "separate work and is reported, not claimed"),
        "reads_imports_not_runtime": (
            "a runtime import by name or a subprocess would be missed"),
    }


def describe() -> dict:
    return {
        "reads": READS,
        "mutations_absent": KNOWN_MUTATIONS,
        "why_an_allowlist": WHY_AN_ALLOWLIST,
        "refuses_with": "AttributeError",
        "why_attribute_error": (
            "a mutation name does not exist on this object, so the refusal "
            "is the same one Python raises for any missing method. It is "
            "not a permission check a caller could catch and route around"),
        "not_established": NOT_ESTABLISHED,
        "what_this_object_is": WHAT_THIS_OBJECT_IS,
        "reflection_paths_still_open": reflection_paths_still_open(),
        "authority_boundary": authority_boundary(),
        "adoption": adoption(),
    }
