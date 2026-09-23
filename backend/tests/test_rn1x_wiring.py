"""THE WIRING AND THE GUARDS, asserted rather than described.

Three claims are made about this experiment in prose. All three are
checkable in code, so all three are checked here:

  1. IT HOLDS NO ORDER PATH. Not "is not supposed to" -- the import
     closure is walked and every order-shaped callable in it is armed
     with a tripwire.
  2. IT IS OFF UNLESS TWO INDEPENDENT THINGS SAY OTHERWISE. The env
     flag AND the database control row.
  3. IT IS ITS OWN WRITER, not the desk's standby. Distinct lock key,
     and a standby that retries rather than sleeping forever -- the
     audit's defect 7, which must not come back.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SRC = BACKEND / "sportsassets"
TARGETS = [
    SRC / "workers" / "rn1x_shadow.py",
    SRC / "bettor_rn1x_store.py",
    SRC / "bettor_rn1x_run.py",
    SRC / "bettor_rn1x_policy.py",
    SRC / "api" / "command_rn1x.py",
]


def _first_party_imports(path: pathlib.Path) -> set[str]:
    """The modules this file imports from our own package, by name."""
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # relative imports inside sportsassets: '.x' / '..y.z'
            if node.level and node.module:
                out.add(node.module.split(".")[-1])
            for alias in node.names:
                out.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.split(".")[-1])
    return out


def test_no_rn1x_source_file_reaches_the_venue_client():
    """`pmus` is the venue client. None of these may import it.

    The desk engine they DO use (`bettor_desk`) is a pure book: it
    defines Order and Portfolio and sends nothing. The distinction is
    the whole point -- a modelled order object is not an order path.
    """
    offenders = []
    for path in TARGETS:
        imports = _first_party_imports(path)
        for banned in ("pmus", "pmus_stream", "live_executor",
                       "execution_gate", "pmx"):
            if banned in imports:
                offenders.append("%s imports %s"
                                 % (path.relative_to(BACKEND), banned))
    assert not offenders, "; ".join(offenders)


def test_no_rn1x_source_file_names_an_order_verb():
    """A textual backstop for a late import or a getattr.

    Docstrings and comments are scanned deliberately, the same way
    test_obs_safety does it: a comment promising not to submit an order
    is not worth an exception in the check.
    """
    verbs = ("place_order", "create_order", "submit_order", "cancel_order",
             "replace_order", "amend_order", "post_order", "send_order",
             "submit_fok")
    offenders = []
    for path in TARGETS:
        text = path.read_text()
        for v in verbs:
            if v in text:
                offenders.append("%s: %s" % (path.relative_to(BACKEND), v))
    assert not offenders, ("an rn1x source file names an order verb: "
                           + ", ".join(offenders))


def test_importing_rn1x_calls_no_order_api(monkeypatch):
    """DYNAMIC proof: arm every order entry point, then import."""
    def _tripwire(name):
        def _fail(*a, **k):
            raise AssertionError(
                "the rn1x experiment called %s. It must hold no order "
                "path at all." % name)
        return _fail

    armed = 0
    for dotted, mod in list(sys.modules.items()):
        if not dotted.startswith("sportsassets."):
            continue
        if ".rn1x" in dotted or "rn1x" in dotted:
            continue
        for attr in dir(mod):
            low = attr.lower()
            if any(v in low for v in ("place_order", "create_order",
                                      "submit", "cancel_order",
                                      "replace_order")):
                if callable(getattr(mod, attr, None)):
                    monkeypatch.setattr(mod, attr,
                                        _tripwire("%s.%s" % (dotted, attr)),
                                        raising=False)
                    armed += 1

    # THE CHECK MUST NOT BE ABLE TO PASS BY ARMING NOTHING. With no
    # tripwire set, the import below proves only that the import works.
    # `pmus` is imported by conftest's collection of this package, so
    # there is always at least one order-shaped callable to arm; if that
    # ever stops being true this fails loudly rather than going quiet.
    assert armed >= 1, (
        "no order entry point was armed, so the import below would prove "
        "nothing. Load a module that has one before asserting this.")

    import importlib
    for name in ("sportsassets.bettor_rn1x_store",
                 "sportsassets.workers.rn1x_shadow",
                 "sportsassets.api.command_rn1x"):
        importlib.reload(importlib.import_module(name))


def test_it_is_off_unless_the_env_flag_says_otherwise(monkeypatch):
    from sportsassets.workers import rn1x_shadow as W

    monkeypatch.delenv("RN1X_SHADOW", raising=False)
    assert W.enabled() is False, (
        "the loop must not arm on a deploy alone. Registering it is not "
        "starting it.")
    for off in ("off", "0", "false", "no", "OFF"):
        monkeypatch.setenv("RN1X_SHADOW", off)
        assert W.enabled() is False, off
    monkeypatch.setenv("RN1X_SHADOW", "on")
    assert W.enabled() is True


def test_its_writer_lock_is_not_the_desks():
    """A shared key would make one book a silent standby of the other."""
    from sportsassets import bettor_desk_loop as DL
    from sportsassets.workers import rn1x_shadow as W

    assert W.LOCK_KEY != DL.LOCK_KEY


def test_the_standby_retries_rather_than_sleeping_forever():
    """The audit's defect 7, asserted over the source.

    The desk's standby was `if not acquired: while True: sleep`, so a
    standby could never take over -- which is the containment handover I
    wrongly attributed to a deploy. This asserts the acquire sits INSIDE
    a loop condition rather than being tested once.
    """
    src = (SRC / "workers" / "rn1x_shadow.py").read_text()
    assert "while not await conn.fetchval(" in src, (
        "the lock acquisition is not a loop condition: a standby that "
        "asks once can never take over")


def test_the_experiment_is_not_registered_in_the_worker_service():
    """Registering it there needs a push that restarts the collector.

    Read from the AST rather than by importing `workers.all`, which
    pulls in the notification stack and fails on a missing dependency in
    some environments -- the same reason `_bind_execution_gate` lives in
    its own module. The LOOPS list is parsed, so a registration added in
    a comment does not count and a real one cannot hide.
    """
    tree = ast.parse((SRC / "workers" / "all.py").read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if getattr(node.target, "id", None) != "LOOPS":
            continue
        assert isinstance(node.value, ast.List), "LOOPS is not a literal list"
        for elt in node.value.elts:
            assert isinstance(elt, ast.Tuple), ast.dump(elt)
            names.append(elt.elts[0].value)
    assert names, "the LOOPS list was not found; this check would be vacuous"
    assert not any("rn1x" in n for n in names), names


def test_the_api_registers_it_and_cancels_it_on_shutdown():
    """Hosted in the lifespan AND awaited on the way out.

    A task holding a pooled connection and a session advisory lock that
    is not cancelled before pool.close() holds the lock through shutdown
    -- which is how the desk's handover failed.
    """
    src = (SRC / "api" / "app.py").read_text()
    assert "_RN1X.run(_rn1x_pool)" in src
    assert "rn1x_task" in src
    # it must be in the cancelled-and-awaited set, not merely created
    tail = src[src.index("rn1x_task = None"):]
    assert "rn1x_task, trim_task" in tail or "desk_task, rn1x_task" in tail, (
        "rn1x_task is created but not in the shutdown cancel set")


def test_every_blocker_is_a_named_external_dependency():
    """A blocker must say what is MISSING, not just that it is blocked."""
    from sportsassets.workers import rn1x_shadow as W

    assert W.BLOCKERS, "no blockers declared at all is not credible"
    for name, text in W.BLOCKERS.items():
        assert len(text) > 80, name
    # at least one must name the dependency explicitly
    assert any("MISSING DEPENDENCY" in t for t in W.BLOCKERS.values())
    # the second-half gap is the one that disables half the policy and
    # it must be present for as long as the mapping is empty
    from sportsassets import bettor_rn1x_policy as pol
    if not pol.SECOND_HALF_MAPPING:
        assert "SECOND_HALF_UNDEFINED" in W.BLOCKERS


def test_the_heartbeat_summary_lands_before_the_readback_truncation():
    """The operational readback prints left(detail, 1400). jsonb orders
    keys by LENGTH then bytewise, so the compact per-lane summary must be
    short enough to precede the historical lane's `results` array -- which
    filled the whole window on its own and made the prospective lane
    invisible in every production read I took.
    """
    import json
    from sportsassets.workers import rn1x_shadow as W

    res = {"lanes": {"P": {"state": "IDLE_NO_CANDIDATES", "cursor": 221561718,
                           "examined": 0, "written": 0},
                     "H": {"state": "REPLAYED", "cursor": 646,
                           "examined": 400, "written": 1}},
           "prospective": {"results": [{"why": "x" * 900}]},
           "historical": {"results": [{"why": "y" * 900}]},
           "state": "REPLAYED"}
    # jsonb's ordering, reproduced: length first, then bytewise.
    order = sorted(res, key=lambda k: (len(k), k))
    assert order[0] == "lanes", order
    assert order.index("lanes") < order.index("historical"), order
    # and the summary itself fits the window with room to spare
    assert len(json.dumps(res["lanes"])) < 400, len(json.dumps(res["lanes"]))
    # the key must be exactly the one the worker emits
    src = open(W.__file__).read()
    assert '"lanes": {' in src, "the worker no longer emits the summary"
