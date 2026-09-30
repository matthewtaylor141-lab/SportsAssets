"""A STRUCTURED PYTEST REPORT, WRITTEN FROM PYTEST'S OWN HOOKS.

WHY CONSOLE-OUTPUT REGEX HAD TO GO. The previous comparator parsed `pytest -q
-rf` console text, and five acceptance gaps were reproduced against it:

  A. an EMPTY head log compared as "NO NEW FAILURE IDENTITY", exit 0;
  B. a collection error -- where pytest itself exits unsuccessfully and runs no
     tests -- compared as exit 0;
  C. a log whose summary said 5 failed while the parser recognised 1 was
     accepted, the discrepancy printed and ignored;
  D. `test_case[case A]` and `test_case[case B]` collapsed to ONE identity,
     because the node-id regex stopped at whitespace. Comparing a run failing
     only A against one failing only B returned zero new identities;
  E. the runner ended on `echo` and `tail`, so simulated pytest exit codes 1-5
     all became runner exit 0.

Each of those turns an invalid or regressed run into a clean gate. Console text
is the wrong authority: it is a human summary that changes shape with flags --
which is exactly how captured ERROR logs became "3 new failing node ids" the
moment `--tb=short` was added.

So this plugin records the RUN ITSELF, from the hooks that own each fact, and
the comparator reads only that. Human-readable tracebacks are still written
alongside; they are for people, not for the decision.

WHAT IS RECORDED, AND WHICH HOOK OWNS IT

  collected      pytest_collection_modifyitems -- the manifest of what was
                 collected, AFTER deselection, plus what was deselected
  deselected     pytest_deselected -- a deselected acceptance test is not a
                 passed one
  collect_error  pytest_collectreport -- a failure BEFORE any test function
                 exists, so its identity is a path and has no `::`
  phases         pytest_runtest_logreport -- setup / call / teardown, EACH
                 recorded, because one test can pass its call and fail its
                 teardown and the console shows one line
  outcome        derived per node from its phases, with xfail/xpass kept
                 distinct from pass and skip
  exitstatus     pytest_sessionfinish -- pytest's OWN status, captured in the
                 process that knows it rather than inferred from text
  complete       written only in sessionfinish, so a truncated or killed run
                 has no completion record and cannot be read as a finished one

Loaded with `-p` so it never affects an ordinary developer run.

FAILURES ARE ALSO STREAMED AS THEY HAPPEN. The report above is written once, in
sessionfinish, so a run that is killed, hangs past its wall clock or loses its
machine leaves no record of which tests had already failed or why. Each failing
phase (and each collection error) is therefore appended to
`<GATE_REPORT_PATH>.failures.jsonl` the moment pytest reports it -- node id,
phase, traceback -- flushed and fsynced, one JSON object per line. That file is
evidence for people; the verdict still reads only the completed report, and a
stream without a `session_finish` line is by construction an incomplete run.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time

_OUT_ENV = "GATE_REPORT_PATH"


class GateReport:
    def __init__(self, path):
        self.path = path
        self.started_at = time.time()
        self.collected: list[str] = []
        self.deselected: list[str] = []
        self.collect_errors: list[dict] = []
        # nodeid -> {"phases": {when: outcome}, "longrepr": str|None, ...}
        self.nodes: dict[str, dict] = {}
        self.exitstatus = None
        self.complete = False
        self.interrupted = None
        self.stream_path = path + ".failures.jsonl"
        self._stream({"event": "session_start", "argv": sys.argv[1:],
                      "python": sys.version.split()[0]}, mode="w")

    # ── the failure stream ───────────────────────────────────────────
    def _stream(self, doc, *, mode="a"):
        """Append one line and force it to disk. Never raises: losing the
        side stream must not change the run it describes."""
        try:
            with open(self.stream_path, mode, encoding="utf-8") as fh:
                fh.write(json.dumps(dict(doc, at=time.time()), default=str,
                                    sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:                                       # noqa: BLE001
            pass

    # ── collection ───────────────────────────────────────────────────
    def pytest_collection_modifyitems(self, items):
        self.collected = [i.nodeid for i in items]

    def pytest_deselected(self, items):
        self.deselected.extend(i.nodeid for i in items)

    def pytest_collectreport(self, report):
        if report.failed:
            self.collect_errors.append({
                "nodeid": report.nodeid,
                "longrepr": str(report.longrepr)[:4000],
                # A COLLECTION FAILURE HAS NO `::`. It is a module that could
                # not be imported, so no test function exists to name. Any
                # comparator that requires `::` in an identity silently drops
                # these -- which is defect B.
                "kind": "COLLECT_ERROR"})
            self._stream({"event": "collect_error", "nodeid": report.nodeid,
                          "longrepr": str(report.longrepr)[:20000]})

    # ── execution ────────────────────────────────────────────────────
    def pytest_runtest_logreport(self, report):
        n = self.nodes.setdefault(
            report.nodeid, {"phases": {}, "longrepr": None, "keywords": []})
        # `report.outcome` is passed/failed/skipped; xfail and xpass are carried
        # on `wasxfail` and must not be folded into pass/skip.
        outcome = report.outcome
        if getattr(report, "wasxfail", None) is not None:
            outcome = "xpassed" if report.passed else "xfailed"
        n["phases"][report.when] = outcome
        if report.longrepr is not None and n["longrepr"] is None:
            n["longrepr"] = str(report.longrepr)[:4000]
        if outcome == "failed":
            self._stream({"event": "failed", "nodeid": report.nodeid,
                          "when": report.when,
                          "duration_s": getattr(report, "duration", None),
                          "longrepr": str(report.longrepr)[:20000]})

    # ── session ──────────────────────────────────────────────────────
    def pytest_keyboard_interrupt(self, excinfo):
        self.interrupted = "KEYBOARD_INTERRUPT"
        self._stream({"event": "interrupted", "kind": self.interrupted})

    def pytest_sessionfinish(self, session, exitstatus):
        self.exitstatus = int(exitstatus)
        self.complete = True
        self._write()
        self._stream({"event": "session_finish", "exitstatus": self.exitstatus})

    def _derive(self):
        """One outcome per node, from its phases, with the phase that decided it.

        A test that PASSES its call and FAILS its teardown is a failure, and the
        phase has to be recorded: "the assertion passed" and "the test passed"
        are different statements and only one of them is about the code.
        """
        out = {}
        for nodeid, rec in self.nodes.items():
            ph = rec["phases"]
            decided_by, outcome = None, "passed"
            for when in ("setup", "call", "teardown"):
                o = ph.get(when)
                if o in ("failed", "xfailed", "xpassed", "skipped") and \
                        decided_by is None:
                    decided_by, outcome = when, o
                    if o == "failed":
                        break
            # A failure in ANY phase wins over an earlier skip/xfail.
            for when in ("setup", "call", "teardown"):
                if ph.get(when) == "failed":
                    decided_by, outcome = when, "failed"
                    break
            out[nodeid] = {"outcome": outcome, "decided_by": decided_by,
                           "phases": ph, "longrepr": rec["longrepr"]}
        return out

    def _write(self):
        derived = self._derive()
        by = {}
        for v in derived.values():
            by[v["outcome"]] = by.get(v["outcome"], 0) + 1
        doc = {
            "schema": "GATE_REPORT_V1",
            "started_at": self.started_at,
            "finished_at": time.time(),
            # SESSION COMPLETION IS A RECORDED FACT, not an inference from the
            # presence of a summary line. A killed run never reaches here.
            "session_complete": self.complete,
            "interrupted": self.interrupted,
            "exitstatus": self.exitstatus,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "pytest": _pytest_version(),
                "cwd": os.getcwd(),
                "argv": sys.argv[1:],
                "dsn_database": _dsn_db(),
            },
            "counts": by,
            "collected_count": len(self.collected),
            "executed_count": len(derived),
            "collected": sorted(self.collected),
            "deselected": sorted(self.deselected),
            "collect_errors": self.collect_errors,
            "nodes": derived,
        }
        tmp = self.path + ".partial"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True, default=str)
        # ATOMIC RENAME. A reader must never see a half-written report and
        # treat its short node list as the run's real content.
        os.replace(tmp, self.path)


def _pytest_version():
    try:
        import pytest
        return pytest.__version__
    except Exception:                                           # noqa: BLE001
        return "unknown"


def _dsn_db():
    dsn = os.environ.get("RN1X_TEST_DSN", "")
    return dsn.rsplit("/", 1)[-1] if "/" in dsn else ""


def pytest_configure(config):
    path = os.environ.get(_OUT_ENV)
    if not path:
        return
    config.pluginmanager.register(GateReport(path), "gate-report")
