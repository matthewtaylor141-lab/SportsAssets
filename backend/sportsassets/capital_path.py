"""WHICH CODE CAN MOVE MONEY? The import closure, computed rather than asserted.

WHY THIS EXISTS. I claimed that none of the 177 standing test failures affects
the capital path, and the only support I offered was that they are also present
in the baseline. THAT SUPPORTS SOMETHING ELSE ENTIRELY -- it establishes that my
changes did not cause them. It says nothing about whether the code they cover can
move money. A failure that predates the release is still a failure on the capital
path if it sits on the capital path.

So the claim needs a different kind of evidence, and this module produces it: the
set of modules REACHABLE BY IMPORT from the entry points that can actually place,
service or account for an order, computed by walking the real import graph. A
test file's relation to that set is then a fact about the code rather than a
judgement about the test's name.

WHAT THE CLOSURE DOES AND DOES NOT ESTABLISH, STATED BEFORE IT IS USED.

  * INSIDE the closure is decisive in the direction that matters: if a module is
    reachable from a capital entry point, a defect in it CAN reach the capital
    path, and the failure must be looked at individually. This module never
    clears anything inside the closure.
  * OUTSIDE the closure is weaker, and is reported as weaker. Static imports do
    not capture every path: a runtime `importlib` call, a module reached only
    through the database, a shared table written by one lane and read by
    another, or a subprocess would all be missed. So "outside the closure" is
    recorded as NO_STATIC_IMPORT_PATH, which is a bounded finding, and each such
    file additionally needs its subject read. The module lists its own blind
    spots in `WHAT_THIS_CANNOT_SEE` rather than leaving them implied.

IT IS ALSO NOT A CLAIM THAT THE FAILURES ARE FINE. 177 standing failures are a
debt. This module says which of them could touch money, not that the rest do not
matter.
"""

from __future__ import annotations

import ast
import os

VERSION = "CAPITAL_PATH_CLOSURE_V1"

_HERE = os.path.dirname(os.path.abspath(__file__))
PKG = "sportsassets"

#: THE ENTRY POINTS THAT CAN MOVE MONEY. Each is here for a stated reason, and
#: the list is deliberately WIDER than "the thing that calls the venue": a module
#: that decides a size, records a fee or reports a balance is on the capital path
#: because a defect in it misstates money even when no order is sent.
CAPITAL_ENTRY_POINTS = {
    "bettor_entry_execution": "the submission gate itself; holds "
                              "REAL_ORDER_SUBMISSION_ENABLED",
    "bettor_funded_execution": "drives a funded order through its lifecycle",
    "bettor_funded_management": "services, cancels and recovers open orders",
    "bettor_funded_book": "the funded book and its fee accounting",
    "bettor_funded_activation": "the authorization the submission gate reads",
    "bettor_funded_schema": "the funded tables",
    "bettor_account_exposure": "account-wide exposure, the enforcement input",
    "bettor_account_onboarding": "account eligibility and reconciliation",
    "bettor_entry_gate": "admits or refuses a candidate for entry",
    "bettor_entry_inventory": "turns a decision into held inventory",
    "bettor_entry_settlement": "settles held inventory into realised money",
    "bettor_fee_schedule": "the fee arithmetic every amount depends on",
    "bettor_admission_policy": "the disabled policy exception",
    "bettor_venue_currency": "the market-data currency verdict the gate reads",
    "bettor_capital_allocator": "decides how much capital a position may use",
}

#: STATIC IMPORTS ARE NOT EVERY PATH. Named so that "outside the closure" is read
#: with its limits attached.
WHAT_THIS_CANNOT_SEE = (
    "a module imported at runtime by name (importlib, a registry of strings)",
    "a lane reached only through the DATABASE -- one writer, another reader, no "
    "import between them. This is exactly the cross-lane exposure problem, and "
    "it is why account-wide enforcement is a separate exhibit",
    "a subprocess, a scheduled job defined outside Python, or a SQL trigger",
    "a defect in a module that is outside the closure but whose OUTPUT is "
    "copied into a capital table by hand or by a migration",
)


def _module_imports(path: str) -> set:
    """The `sportsassets.*` modules a file imports, by static parse."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(PKG + "."):
                    out.add(a.name[len(PKG) + 1:])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # `from . import x, y` and `from sportsassets import x, y` are the
            # SAME SHAPE: each name is a submodule of the package root. The
            # second form was originally missed -- `mod == "sportsassets"` does
            # not start with `"sportsassets."` and carries level 0, so the whole
            # statement was dropped. It is the form nearly every test in this
            # repository uses, which made the first classification numbers
            # wrong in the direction of finding nothing.
            if (node.level and not mod) or mod == PKG:
                for a in node.names:
                    out.add(a.name)
                continue
            base = mod[len(PKG) + 1:] if mod.startswith(PKG + ".") else mod
            if node.level or mod.startswith(PKG + "."):
                out.add(base)
                for a in node.names:
                    out.add(base + "." + a.name if base else a.name)
    return {m for m in out if m}


def _module_path(name: str) -> str | None:
    p = os.path.join(_HERE, *name.split("."))
    for cand in (p + ".py", os.path.join(p, "__init__.py")):
        if os.path.isfile(cand):
            return cand
    return None


def closure(entry_points=None) -> dict:
    """Walk the import graph from the capital entry points. Returns the reachable
    module set with, for each, the entry point that reaches it and the depth."""
    entries = list(entry_points or CAPITAL_ENTRY_POINTS)
    reached: dict = {}
    frontier = [(e, e, 0) for e in entries]
    while frontier:
        name, root, depth = frontier.pop()
        prev = reached.get(name)
        if prev is not None and prev["depth"] <= depth:
            continue
        path = _module_path(name)
        if path is None:
            continue
        reached[name] = {"reached_from": root, "depth": depth}
        for dep in _module_imports(path):
            if dep not in reached and _module_path(dep):
                frontier.append((dep, root, depth + 1))
    return reached


def classify_test_files(files, entry_points=None,
                        tests_dir: str | None = None) -> dict:
    """For each test file, which capital-path modules does it reach?

    A file that imports NOTHING in the closure is reported as
    NO_STATIC_IMPORT_PATH -- a bounded finding, never a clearance.
    """
    reach = closure(entry_points)
    base = tests_dir or os.path.join(os.path.dirname(_HERE), "tests")
    out = {}
    for f in files:
        rel = f.split("::", 1)[0]
        path = os.path.join(os.path.dirname(base), rel) if not \
            os.path.isabs(rel) else rel
        if not os.path.isfile(path):
            path = os.path.join(base, os.path.basename(rel))
        imports = _module_imports(path) if os.path.isfile(path) else set()
        # EXACT MODULE MATCHES ONLY, and this was a real defect in the first
        # version. Matching on the first dotted segment made `workers.mirror_live`
        # count as capital-path because the bare package `workers` is reachable
        # (some worker is). That is a package being reachable, not that module,
        # and it turned 32 files into false positives -- nearly the whole answer.
        # A bare package name is likewise not a hit: `import analytics` reaches
        # whatever analytics/__init__.py imports and nothing more, which the
        # closure already walked.
        #
        # `_module_path` also filters out SYMBOLS: `from .analytics import mirror`
        # yields both `analytics.mirror` (a module) and, for
        # `from .analytics.mirror import Plan`, `analytics.mirror.Plan` (a class),
        # and only the former is a module on disk.
        hits = sorted({m for m in imports
                       if m in reach and _module_path(m) is not None})
        out[rel] = {
            "readable": os.path.isfile(path),
            "sportsassets_imports": sorted(imports),
            "capital_path_modules_reached": hits,
            "verdict": ("REACHES_THE_CAPITAL_PATH" if hits else
                        "NO_STATIC_IMPORT_PATH"),
            "and_what_that_verdict_means": (
                "a defect here CAN reach money; the failure needs reading "
                "individually" if hits else
                "no static import path was found. This is bounded evidence, "
                "not a clearance -- see WHAT_THIS_CANNOT_SEE"),
        }
    return out


# ── THE NARROW MEASURE: WHAT IS ACTUALLY IMPORTED, SYMBOL BY SYMBOL ──
#
# WHY A SECOND MEASURE IS NEEDED. The closure OVER-APPROXIMATES, and the
# 2026-09-27 run showed how much: `bettor_funded_book` imports exactly one name
# from `live_executor` --
#
#     from .live_executor import fill_cash
#
# -- a pure arithmetic function. But importing that module executes it, and it
# imports `analytics.mirror_live_rules`, `workers.mirror_shadow`,
# `api.pmus_account` and the rest of the legacy copier stack. So the whole copier
# lands in the closure on the strength of one function.
#
# A defect in `fill_cash` DOES misstate funded money. A defect in
# `mirror_live_rules` reaches funded money only if something the funded lane
# CALLS consults it. The closure cannot tell those apart, so this measure names
# the coupling surface exactly: the symbols the capital entry points actually
# import from modules that are not themselves capital entry points.

def coupling_surface(entry_points=None) -> dict:
    """The exact named symbols the capital entry points import from elsewhere.

    This is the NARROW measure and it is where a failure on a non-capital module
    becomes a capital defect. It is reported ALONGSIDE the closure, never instead
    of it: a symbol can lead somewhere this does not follow, so the closure
    remains the conservative outer bound.
    """
    entries = set(entry_points or CAPITAL_ENTRY_POINTS)
    out = {}
    for e in sorted(entries):
        path = _module_path(e)
        if path is None:
            continue
        imported = _module_imports(path)
        # A dotted entry whose own module exists is a MODULE import; one whose
        # parent exists but itself does not is a SYMBOL from that module.
        symbols, modules = [], []
        for m in sorted(imported):
            if m in entries:
                continue
            if _module_path(m) is not None:
                modules.append(m)
            elif "." in m and _module_path(m.rsplit(".", 1)[0]) is not None:
                symbols.append(m)
        out[e] = {"modules_imported_outside_the_capital_set": modules,
                  "named_symbols_imported": symbols}
    return {
        "by_entry_point": out,
        "the_whole_coupling_surface": sorted(
            {s for v in out.values() for s in v["named_symbols_imported"]}),
        "and_why_this_is_narrower_than_the_closure": (
            "importing one pure function pulls that module's whole import tree "
            "into the closure. The tree is the conservative bound; these symbols "
            "are what the capital lane actually names"),
    }


def first_ring(entry_points=None) -> set:
    """THE MIDDLE MEASURE: the capital entry points plus the modules they import
    DIRECTLY. Depth 0 and 1 of the closure, and nothing deeper.

    Why this is the useful one. The full closure is an outer bound that one pure
    function can inflate by a hundred modules. The coupling surface is an inner
    bound that names only symbols. The first ring is where a defect is at most
    one call away from money, so it is the set a failure must be checked against
    individually.
    """
    entries = set(entry_points or CAPITAL_ENTRY_POINTS)
    ring = {e for e in entries if _module_path(e)}
    cs = coupling_surface(entry_points)
    for v in cs["by_entry_point"].values():
        for m in v["modules_imported_outside_the_capital_set"]:
            if _module_path(m):
                ring.add(m)
    return ring


# ── WHO CAN REACH THE ACCOUNT? THE WRITERS, ENUMERATED ───────────────
#
# THE QUESTION THIS ANSWERS, and it is a different one from the closure above.
# "Which failures can reach money" is about our code. "Which code can commit the
# ACCOUNT" is about writers to the tables the venue's exposure is reconstructed
# from -- and those writers do not import one another. The legacy copier and the
# autonomous lane share `live_orders` and have no import edge between them, which
# is the blind spot this module's own WHAT_THIS_CANNOT_SEE names. So the account
# question is answered by grepping for WRITE STATEMENTS against the exposure
# tables rather than by walking imports.

import re as _re

#: The statements that can create or change exposure. A SELECT is not here.
_WRITE = _re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_.]*)",
    _re.IGNORECASE)


def writers_of(tables, root: str | None = None) -> dict:
    """Every module containing a write statement against one of `tables`.

    READ THIS AS A LOWER BOUND, not a census. It finds writes whose table name
    appears literally in the source. It CANNOT see a table name built by string
    formatting, a write issued from SQL or a migration, a write from outside this
    repository, or a human at a database prompt -- and the last of those is a real
    writer on a real account. `unseen_writers()` states them.
    """
    want = {t.lower() for t in tables}
    base = root or _HERE
    out: dict = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    src = fh.read()
            except OSError:
                continue
            hits = {}
            for verb, table in _WRITE.findall(src):
                t = table.lower()
                if t in want:
                    hits.setdefault(t, set()).add(verb.split()[0].upper())
            if hits:
                rel = os.path.relpath(path, base)
                out[rel] = {t: sorted(v) for t, v in sorted(hits.items())}
    return out


#: WRITERS NO STATIC SCAN CAN FIND. Stated so the enumeration is not read as
#: complete, because on a funded account an unseen writer is the whole risk.
UNSEEN_WRITERS = (
    "a human at a database prompt, or an operator using the venue's own web "
    "interface or app. Neither appears in this repository at all, and either can "
    "commit the account between our read and our order",
    "a table name built by string formatting or held in a variable",
    "a write issued by a migration, a SQL trigger, or a scheduled job defined "
    "outside Python",
    "any service outside this repository that holds the same credential",
)


def account_writers(exposure_tables=None) -> dict:
    """The writer enumeration for the account-exposure tables."""
    if exposure_tables is None:
        from . import bettor_account_exposure as AE
        exposure_tables = sorted({t for p in AE.PATHS for t in p["tables"]})
    found = writers_of(exposure_tables)
    by_table: dict = {}
    for mod, tabs in found.items():
        for t in tabs:
            by_table.setdefault(t, []).append(mod)
    return {
        "exposure_tables": list(exposure_tables),
        "modules_that_write_them": found,
        "writers_by_table": {t: sorted(v) for t, v in sorted(by_table.items())},
        "writer_count": len(found),
        "this_is_a_LOWER_BOUND": list(UNSEEN_WRITERS),
        "and_what_follows_from_that": (
            "a lane-local uniqueness constraint cannot control another writer. "
            "Either every writer goes through one enforcement boundary, or the "
            "account is isolated to one writer and that isolation is verified. "
            "Counting writers is not controlling them"),

        # ── AND A WRITER INVENTORY IS NOT A TRADER CENSUS ────────────
        #
        # THE CORRECTION. I reported "six modules write `live_orders`, and this
        # lane's index constrains none of them" as though it described six lanes
        # currently trading the account. It does not. It describes six modules
        # that CONTAIN a write statement. Each would additionally need:
        #
        #   * an account binding -- which account its rows belong to;
        #   * a credential reaching that account;
        #   * an enabled control path that actually runs it.
        #
        # None of those was checked, and a source read cannot check them. So the
        # honest verdict on isolation is UNKNOWN, which sounds worse than "six
        # writers" and is more accurate than either "six lanes trade this
        # account" or "only this lane does".
        "is_this_a_census_of_modules_that_CURRENTLY_TRADE": False,
        "what_each_writer_would_additionally_need": [
            "an account binding: which account its rows belong to",
            "a credential that reaches that account",
            "an enabled control path that actually executes it",
        ],
        "exposure_and_writer_isolation_verdict": "UNKNOWN",
        "why_UNKNOWN_and_not_a_number": (
            "the bindings and control states above are runtime facts. Until they "
            "are read against the real account, 'six writers can reach it' and "
            "'only one does' are both unsupported -- and I asserted the first"),
        "what_would_settle_it": [
            "for each writer, the account_id its rows actually carry",
            "whether its lane's control row or env flag is enabled right now",
            "whether the credential in the environment reaches that account",
        ],
    }


def report(identities, entry_points=None) -> dict:
    """THE CLASSIFICATION, on three measures kept apart.

    THE ARGUMENT I ORIGINALLY GAVE WAS THE WRONG ARGUMENT. "These failures are
    also in the baseline" establishes that the release did not cause them. It
    does not establish that they are off the capital path, and it was the only
    support offered. These three measures are the support.
    """
    ids = [str(i).strip() for i in identities if str(i).strip()]
    files = sorted({i.split("::", 1)[0] for i in ids})
    per_file = classify_test_files(files, entry_points)
    ring = first_ring(entry_points)
    cs = coupling_surface(entry_points)
    surface = set(cs["the_whole_coupling_surface"])

    for f, v in per_file.items():
        imports = set(v["sportsassets_imports"])
        v["first_ring_modules_imported"] = sorted(imports & ring)
        v["coupling_symbols_imported"] = sorted(imports & surface)
        v["first_ring"] = bool(v["first_ring_modules_imported"])

    reaching = sorted(f for f, v in per_file.items()
                      if v["verdict"] == "REACHES_THE_CAPITAL_PATH")
    ring_files = sorted(f for f, v in per_file.items() if v["first_ring"])
    sym_files = sorted(f for f, v in per_file.items()
                       if v["coupling_symbols_imported"])

    def _ids(fs):
        s = set(fs)
        return [i for i in ids if i.split("::", 1)[0] in s]

    return {
        "version": VERSION,
        "identities": len(ids),
        "files": len(files),
        "capital_entry_points": dict(CAPITAL_ENTRY_POINTS),

        # MEASURE A -- the conservative outer bound.
        "A_closure": {
            "what_it_is": "every module reachable by import from a capital "
                          "entry point, to any depth",
            "closure_size": len(closure(entry_points)),
            "files": reaching,
            "identities": len(_ids(reaching)),
            "and_it_OVER_approximates": (
                "bettor_funded_book imports one pure function from "
                "live_executor, and that import pulls the whole legacy copier "
                "tree into the closure. Inside is 'must be read individually', "
                "never 'is a capital defect'"),
        },

        # MEASURE B -- the inner bound.
        "B_coupling_surface": {
            "what_it_is": "the named symbols the capital entry points import "
                          "from modules outside the capital set",
            "symbols": sorted(surface),
            "files_importing_one": sym_files,
            "identities": len(_ids(sym_files)),
        },

        # MEASURE C -- the useful one.
        "C_first_ring": {
            "what_it_is": "the capital entry points plus the modules they "
                          "import directly -- depth 0 and 1, where a defect is "
                          "at most one call from money",
            "ring_size": len(ring),
            "ring": sorted(ring),
            "files": ring_files,
            "identities": _ids(ring_files),
            "identity_count": len(_ids(ring_files)),
        },

        "files_with_no_static_import_path": sorted(
            f for f in per_file if f not in set(reaching)),
        "per_file": per_file,
        "what_this_cannot_see": list(WHAT_THIS_CANNOT_SEE),
        "and_the_baseline_argument_is_NOT_this_argument": (
            "'also failing before my changes' establishes that the release did "
            "not cause them. It does not establish that they are off the "
            "capital path, and it was the only support I originally gave"),
    }
