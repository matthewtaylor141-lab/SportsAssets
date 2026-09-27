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

This module is that object. It closes the requirement.

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
a method that does not exist -- because from this object's side it does
not exist. It is not a permission check that a caller could catch, log
and route around; there is no code path behind it to reach. `hasattr`
returns False, `dir()` does not list it, and `getattr(v, "submit_fok")`
fails before any argument is evaluated.

WHAT THIS DOES NOT ESTABLISH. That diagnostics in this repository
currently use it. Adopting it at each diagnostic call site is separate
work, and `adoption()` reports the sites still reaching `pmus` directly
rather than claiming they were converted. Nor does it say anything about
the venue's own permission granularity, which stays unverified: this
constrains OUR interface, not the credential's scope.
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
    "that the diagnostics in this repository use this interface today -- "
    "`adoption()` reports which still reach `pmus` directly -- and "
    "nothing here bears on the venue's own permission granularity, "
    "which stays unverified. This constrains OUR interface, not the "
    "credential's scope")


class ReadOnlyVenue:
    """Venue reads only. Mutation names are absent, not guarded.

    >>> v = ReadOnlyVenue()
    >>> hasattr(v, "submit_fok")
    False
    """

    __slots__ = ("_mod",)

    def __init__(self, module=None):
        if module is None:
            from . import pmus as module        # noqa: PLC0415
        object.__setattr__(self, "_mod", module)

    # ── the whole control ────────────────────────────────────────────

    def __getattr__(self, name: str):
        # `__slots__` means _mod is found without reaching here; every
        # other attribute lands in this method, so READS is the only
        # door.
        if name in READS:
            fn = getattr(object.__getattribute__(self, "_mod"), name, None)
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
        "adoption": adoption(),
    }
