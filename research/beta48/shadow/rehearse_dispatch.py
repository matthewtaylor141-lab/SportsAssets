#!/usr/bin/env python3
"""OFFLINE DISPATCH REHEARSAL. No venue traffic. No orders. mirror_live=false.

Exercises the sequence the WORKFLOW runs, through the REAL command entry
points, with mocked board responses:

    mocked board responses
      -> the actual selection command   (substantive_select._cli, mocked HTTP)
      -> the actual manifest command    (capture_manifest._cli)
      -> the actual manifest verification (capture_manifest._cli --verify)
      -> a sampling-entry sentinel      (stands in for the capture step)

Run 35333848994 reached the venue and died at selection, and the manifest
command had never been executed by anything -- it could not have run, because
it asked substantive_capture for three names that module has never defined.
This rehearsal is the thing that would have caught that offline.

    python3 rehearse_dispatch.py            # all scenarios
    python3 rehearse_dispatch.py --only happy
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# A VALID BUT DELIBERATELY NON-SORTED ROSTER. Sorted input would have hidden
# the membership/schedule defect exactly as the first test fixture did.
BOARD = [
    {"slug": "zulu-mkt-1", "id": "m-z1", "eventId": "ev-zulu",
     "gameStartTime": "2026-09-18T13:00:00Z"},
    {"slug": "alpha-mkt-1", "id": "m-a1", "eventId": "ev-alpha",
     "gameStartTime": "2026-09-18T13:10:00Z"},
    {"slug": "mike-mkt-1", "id": "m-m1", "eventId": "ev-mike",
     "gameStartTime": "2026-09-18T13:20:00Z"},
]


def _run(args, cwd, env=None):
    e = dict(os.environ)
    e.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    e.update(env or {})
    p = subprocess.run([PY] + args, cwd=cwd, env=e,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def _selection_file(d, frozen, events, markets, status="FROZEN",
                    discovery=None):
    """Write a selection the way the selection step writes it."""
    sel = {
        "EVENT_SELECTION_FROZEN": "YES" if frozen else "NO",
        "SELECTION_STATUS": status,
        "CAPTURE_MAY_START": "YES" if frozen else "NO",
        "EVENT_IDS": list(events),
        "MARKET_IDS": list(markets),
        "MARKET_SLUGS": list(markets),
    }
    # The discovery block the selection step now emits, so the manifest can
    # name the endpoint and adapter that produced this roster.
    sel.update(discovery or {
        "DISCOVERY_ENDPOINT": "/v1/events",
        "DISCOVERY_ADAPTER": "EVENTS_TO_MARKETS_V1",
        "DISCOVERY_EVENT_ROWS": 6,
        "DISCOVERY_MARKET_ROWS_EXTRACTED": 27,
        "DISCOVERY_LIST_EXHAUSTED": "YES",
        "FIRST_TERMINAL_OFFSET": 6,
    })
    p = os.path.join(d, "selection.json")
    with open(p, "w") as fh:
        json.dump(sel, fh, indent=2)
    return p, sel


def sampling_entry_sentinel(manifest_path, verify_rc):
    """Stands in for the capture step. Unreachable unless verification passed.

    The workflow enforces this with `set -e` and no `if: always()`; here the
    same condition is evaluated explicitly so the rehearsal can assert it.
    """
    if verify_rc != 0:
        return "SAMPLING_NOT_ENTERED:VERIFICATION_FAILED"
    if not os.path.exists(manifest_path):
        return "SAMPLING_NOT_ENTERED:NO_MANIFEST"
    return "SAMPLING_ENTERED"


def scenario_board_walk(name, responses, expect_status):
    """Drive the REAL board walk with mocked responses."""
    sys.path.insert(0, HERE)
    import substantive_select as S

    class Resp:
        def __init__(self, body, status=200):
            self.status_code = status
            self._b = body

        def json(self):
            return self._b

    it = iter(responses)
    last = [None]

    def get(params):
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        r = last[0]
        return Resp(r[1], r[0]) if isinstance(r, tuple) else Resp(r)

    by, rec, st = S.board_walk(get, page_limit=2, endpoint=S.MARKETS_PATH)
    cert, why = S.certify_completion(st, rec, len(by), S.MARKETS_PATH)
    ok = cert == expect_status
    print("  %-46s %-34s %s" % (name, cert, "OK" if ok else
                                "MISMATCH expected " + expect_status))
    if rec:
        print("      receipts=%d  last_shape=%s  malformed=%d  body_sha=%s"
              % (len(rec), rec[-1]["RESPONSE_SHAPE"],
                 len(rec[-1]["MALFORMED_ROWS"]),
                 str(rec[-1]["BODY_SHA256"])[:12]))
    return ok


def rehearse_discovery(workdir):
    """THE CONTINUOUS PATH. The REAL selection CLI, mocked transport only.

        retained /v1/events bodies + retained-or-synthetic book bodies
          -> substantive_select._cli               (the actual command)
             -> events_adapter                     (real)
             -> event_identity                     (real)
             -> eligibility.decision_screen        (real)
             -> ranking + freeze + serialization   (real)
          -> selection.json  <-- THE FILE
          -> capture_manifest.py       reads THAT file
          -> capture_manifest.py --verify  reads THAT file
          -> sampling-entry sentinel

    Nothing between the CLI and the manifest reconstructs, re-writes or
    overwrites the selection: the bytes the command emitted are the bytes the
    manifest consumes. Only the TRANSPORT is mocked; no eligibility or
    activity verdict is supplied by the harness.

    The clock is injected explicitly (--as-of) because the bodies are retained
    from 2026-09-14. Judging them by today's clock would present historical
    markets as current ones.
    """
    sys.path.insert(0, HERE)
    with open(os.path.join(HERE, "fixtures_events_block3.json")) as fh:
        fx = json.load(fh)
    with open(os.path.join(HERE, "fixtures_books_block4.json")) as fh:
        bx = json.load(fh)
    page0 = fx["RETAINED_PAGES"][0]["body"]
    # THE LATER OF THE TWO RETAINED CAPTURES. Serving a book captured at
    # 19:48 while claiming a 17:13 decision time would present evidence as
    # available before it existed.
    as_of = bx["AS_OF_UTC"]
    assert as_of >= fx["AS_OF_UTC"], "as-of precedes the retained event bodies"

    print("  event bodies  %s  RETAINED  (%d retained, %d embedded)"
          % (fx["PROVENANCE"]["SOURCE_RUN"],
             fx["PROVENANCE"]["EVENTS_RETAINED"],
             fx["PROVENANCE"]["EVENTS_EMBEDDED_HERE"]))
    print("  book bodies   run85_trackbl_BLOCK_4  RETAINED x%d, "
          "SYNTHETIC for the rest" % bx["RETAINED"]["COUNT"])
    print("  terminal page SYNTHETIC (%s)"
          % fx["SYNTHETIC_PAGES"]["DECLARED_TERMINAL_EMPTY_PAGE"]["PROVENANCE"])
    print("  as-of         %s  INJECTED, HISTORICAL "
          "(>= both retained captures)" % as_of)
    print("  events cap.   %s   books cap.  %s"
          % (fx["AS_OF_UTC"], bx["AS_OF_UTC"]))
    import rehearsal_transport as _T
    print("  synthetic clk %s" % _T.SYNTHETIC_CLOCK_ASSUMPTION[:96])

    sel_path = os.path.join(workdir, "selection.json")
    pages = [page0, {"events": []}]
    runner = os.path.join(workdir, "run_real_cli.py")
    with open(runner, "w") as fh:
        fh.write(
            "import json, sys\n"
            "sys.path.insert(0, %r)\n"
            "sys.path.insert(0, %r)\n"
            "import rehearsal_transport as T\n"
            "pages = json.load(open(%r))\n"
            "log, prov = [], {}\n"
            "T.install(pages, %r, log, prov)\n"
            "import substantive_select as S\n"
            "sys.argv = ['substantive_select.py', '--out', %r,\n"
            "            '--rate', '25', '--as-of', %r]\n"
            "rc = 0\n"
            "try:\n"
            "    S._cli()\n"
            "except SystemExit as e:\n"
            "    rc = e.code if isinstance(e.code, int) else 1\n"
            "json.dump({'REQUESTS': log, 'BOOK_PROVENANCE': prov},\n"
            "          open(%r, 'w'), indent=1)\n"
            "raise SystemExit(rc)\n"
            % (HERE, os.path.join(os.path.dirname(HERE), "forward"),
               os.path.join(workdir, "pages.json"), as_of, sel_path, as_of,
               os.path.join(workdir, "transport_log.json")))
    with open(os.path.join(workdir, "pages.json"), "w") as fh:
        json.dump(pages, fh)

    rc, out = _run([runner], HERE)
    print("  selection CLI rc=%d" % rc)
    for ln in out.splitlines()[-14:]:
        print("      " + ln)

    tl = json.load(open(os.path.join(workdir, "transport_log.json")))
    reqs = tl["REQUESTS"]
    prov = tl["BOOK_PROVENANCE"]
    ev_reqs = [r for r in reqs if "/v1/events" in r["url"]]
    bk_reqs = [r for r in reqs if "/book" in r["url"]]
    print("  transport     events=%d  books=%d  (all mocked; venue=0)"
          % (len(ev_reqs), len(bk_reqs)))
    print("  book source   RETAINED=%d  SYNTHETIC=%d"
          % (sum(1 for v in prov.values() if v == "RETAINED"),
             sum(1 for v in prov.values() if v != "RETAINED")))

    if not os.path.exists(sel_path):
        print("  selection file NOT WRITTEN")
        return False, None, sel_path
    sel = json.load(open(sel_path))
    print("  selection     FROZEN=%s  status=%s"
          % (sel.get("EVENT_SELECTION_FROZEN"), sel.get("SELECTION_STATUS")))
    print("  clock         %s  injected=%s"
          % (sel.get("DECISION_TIME_UTC"), sel.get("DECISION_CLOCK_INJECTED")))
    print("  discovery     endpoint=%s  events=%s  markets=%s  exhausted=%s"
          % (sel.get("DISCOVERY_ENDPOINT"), sel.get("DISCOVERY_EVENT_ROWS"),
             sel.get("DISCOVERY_MARKET_ROWS_EXTRACTED"),
             sel.get("DISCOVERY_LIST_EXHAUSTED")))
    print("  bodies kept   %d in %s/"
          % (len(sel.get("RETAINED_RESPONSE_BODIES") or ()),
             sel.get("RETAINED_RESPONSE_BODY_DIR")))
    rc_counts = (sel.get("ROW_ACCOUNTING") or {}).get("REASON_COUNTS") or {}
    for k, v in sorted(rc_counts.items(), key=lambda x: -x[1])[:6]:
        print("      %-44s %d" % (k, v))
    return rc == 0, sel, sel_path


def diagnose_book_shape():
    """Measure `two_sided` against the VENUE'S OWN retained book bodies.

    Reported, not repaired. `substantive_select.two_sided` is part of the
    frozen eligibility path and management's decision keeps that path
    unchanged, so this prints the measurement and names the incompatibility
    rather than editing the rule.
    """
    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "forward"))
    import substantive_select as S
    import eligibility as EL

    with open(os.path.join(HERE, "fixtures_books_block4.json")) as fh:
        bx = json.load(fh)
    bodies = bx["RETAINED"]["BODIES"]

    genuinely_two_sided = 0
    accepted = 0
    for slug, body in bodies.items():
        md = body.get("marketData") or {}
        if md.get("bids") and md.get("offers"):
            genuinely_two_sided += 1
        if S.two_sided(body):
            accepted += 1
    bid, ask, state = EL.book_bbo(list(bodies.values())[0])

    import throughput_v1 as TP
    import book_schema as BS
    tp_accepted = sum(1 for b in bodies.values() if TP._two_sided(b))

    print("  retained venue books                         %d" % len(bodies))
    print("  genuinely two-sided (marketData.bids+offers) %d"
          % genuinely_two_sided)
    print("  accepted by substantive_select.two_sided     %d" % accepted)
    print("  accepted by throughput_v1._two_sided         %d" % tp_accepted)
    print("  eligibility.book_bbo on the same body        bid=%s ask=%s state=%s"
          % (bid, ask, state))
    print()
    print("  both readers now route through book_schema: %s"
          % BS.NATIVE_BOOK_PATH)
    print()

    # THE REQUIREMENT DID NOT WEAKEN, AND THIS SHOWS IT ON THE SAME PATH.
    checks = [
        ("one-sided (bids only)", BS.native_book([{"px": {"value": "1"}}], [])),
        ("one-sided (offers only)", BS.native_book([], [{"px": {"value": "1"}}])),
        ("empty book", BS.native_book([], [])),
        ("no body at all", None),
        ("marketData not a mapping", {"marketData": "nope"}),
        ("a side that is not a list", {"marketData": {"bids": "x",
                                                      "offers": []}}),
        ("the OLD MOCK shape", {"bids": [1], "asks": [1]}),
    ]
    bad = [n for n, b in checks if S.two_sided(b)]
    for n, b in checks:
        print("  refused: %-26s %s" % (n, BS.reason(b)))

    ok = (accepted == genuinely_two_sided == len(bodies)
          and tp_accepted == len(bodies) and not bad)
    print()
    print("  NATIVE_BOOK_SCHEMA_CORRECTION = %s"
          % ("APPLIED_AND_VERIFIED" if ok else "INCONSISTENT"))
    if bad:
        print("  WEAKENED: these should have been refused: %s" % bad)
    return accepted, genuinely_two_sided


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    fails = []

    print("== 1. BOARD RETRIEVAL, real walk, mocked responses ==")
    full = {"markets": [{"slug": "a"}, {"slug": "b"}]}
    checks = [
        ("failed retrieval (503) blocks", [(503, {})], "HTTP_FAILURE"),
        ("malformed row retains evidence",
         [{"markets": [{"noslug": 1}]}], "SCHEMA_FAILURE"),
        ("contradictory pagination cannot certify",
         [dict(full, has_more=True), {"markets": [{"slug": "c"}],
                                      "has_more": True}],
         "PAGINATION_CONTRADICTS_END"),
        ("unestablished endpoint contract withholds",
         [full, {"markets": [{"slug": "c"}]}],
         "COMPLETION_CONTRACT_NOT_ESTABLISHED"),
    ]
    for nm, resp, exp in checks:
        if not scenario_board_walk(nm, resp, exp):
            fails.append(nm)

    print("\n== 1b. CONTINUOUS PATH, the REAL selection CLI, mocked transport ==")
    dc = tempfile.mkdtemp(prefix="rehearsal-continuous-")
    cont_ok, cont_sel, cont_sel_path = rehearse_discovery(dc)

    print("\n== 1c. THE MANIFEST CONSUMES THAT EXACT FILE ==")
    if not os.path.exists(cont_sel_path):
        print("  no selection file to consume -- chain stops here")
        print("  CONTINUOUS_CHAIN = BROKEN_AT_SELECTION")
        fails.append("continuous chain: selection produced no file")
    else:
        before = open(cont_sel_path, "rb").read()
        cman = os.path.join(dc, "capture_manifest.json")
        rcm, outm = _run(["capture_manifest.py",
                          "--orchestration-version", "3",
                          "--selection", cont_sel_path,
                          "--code-sha", "REHEARSAL_NOT_A_RELEASE",
                          "--run-id", "REHEARSAL-CONTINUOUS",
                          "--capture-seconds", "5400",
                          "--indirect-isolation-regime", "DISCLOSED_RESIDUAL",
                          "--out", cman], HERE)
        print("  manifest rc=%d" % rcm)
        for ln in outm.splitlines()[-6:]:
            print("      " + ln)
        rcv, outv = _run(["capture_manifest.py", "--verify",
                          "--manifest", cman,
                          "--selection", cont_sel_path], HERE)
        print("  verify   rc=%d" % rcv)
        print("  sampling entry  %s" % sampling_entry_sentinel(cman, rcv))
        after = open(cont_sel_path, "rb").read()
        same = before == after
        print("  selection file unmodified by the manifest step: %s" % same)
        if not same:
            fails.append("the manifest step rewrote the selection file")
        # The chain is CONTINUOUS if the same file flowed all the way through.
        # Whether it reached SAMPLING_ENTERED is a separate question, and a
        # refusal here is a real finding rather than a harness failure.
        if cont_sel and cont_sel.get("EVENT_SELECTION_FROZEN") == "YES" \
                and rcm == 0 and rcv == 0:
            print("  CONTINUOUS_CHAIN = COMPLETE_TO_SAMPLING_ENTRY")
        else:
            print("  CONTINUOUS_CHAIN = RAN_END_TO_END_AND_REFUSED")
            print("  refusal is the finding; see section 1d")

    print("\n== 1d. WHY THE ROSTER CAME OUT THE WAY IT DID ==")
    diagnose_book_shape()

    print("\n== 2. REGRESSION: the deliberately NON-SORTED roster ==")
    print("  (kept separate; it does NOT stand in for the adapted path above)")
    d = tempfile.mkdtemp(prefix="rehearsal-")
    try:
        events = ["ev-zulu", "ev-alpha", "ev-mike"]       # NOT sorted
        markets = ["zulu-mkt-1", "alpha-mkt-1", "mike-mkt-1"]
        sel_path, sel = _selection_file(d, True, events, markets)
        man = os.path.join(d, "capture_manifest.json")

        rc, out = _run(["capture_manifest.py",
                        "--orchestration-version", "3",
                        "--selection", sel_path,
                        "--code-sha", "bbce49dae0f20d9bad86bfc8f3322f28dc097859",
                        "--run-id", "REHEARSAL",
                        "--capture-seconds", "5400",
                        "--indirect-isolation-regime", "DISCLOSED_RESIDUAL",
                        "--out", man], HERE)
        print("  manifest command rc=%d" % rc)
        for ln in out.splitlines():
            print("      " + ln)
        if rc != 0:
            fails.append("manifest command")

        rc2, out2 = _run(["capture_manifest.py", "--verify",
                          "--manifest", man, "--selection", sel_path], HERE)
        print("  verify command   rc=%d" % rc2)
        for ln in out2.splitlines():
            print("      " + ln)
        if rc2 != 0:
            fails.append("verify command")

        print("  sampling entry   %s" % sampling_entry_sentinel(man, rc2))
        if sampling_entry_sentinel(man, rc2) != "SAMPLING_ENTERED":
            fails.append("sampling entry")

        # The manifest actually written, read back off disk.
        if os.path.exists(man):
            m = json.load(open(man))
            print("  --- written manifest ---")
            for k in ("MANIFEST_CREATED_AT", "SAMPLING_START_UTC",
                      "EVENT_IDS", "EVENT_ID_SCHEDULE", "MARKET_FAMILIES",
                      "SELECTION_FILE_SHA256", "INDIRECT_ISOLATION_REGIME",
                      "ORCHESTRATION_VERSION", "DISCOVERY_ENDPOINT",
                      "DISCOVERY_ADAPTER_VERSION", "DISCOVERY_EVENT_ROWS",
                      "DISCOVERY_MARKET_ROWS_EXTRACTED",
                      "DISCOVERY_LIST_EXHAUSTED", "FIRST_TERMINAL_OFFSET"):
                print("      %-24s %s" % (k, json.dumps(m.get(k))[:90]))
            rp = m.get("REQUEST_POLICY", {})
            print("      %-24s %s" % ("GLOBAL_REQUEST_INTERVAL_S",
                                      rp.get("GLOBAL_REQUEST_INTERVAL_S")))
            print("      %-24s %s" % ("PER_MARKET_REVISIT_INTERVAL_S",
                                      rp.get("PER_MARKET_REVISIT_INTERVAL_S")))

        print("\n== 3. WRITE-ONCE: a second write must refuse, nonzero, no SHA ==")
        rc3, out3 = _run(["capture_manifest.py",
                          "--orchestration-version", "3",
                          "--selection", sel_path,
                          "--code-sha", "bbce49d", "--run-id", "REHEARSAL",
                          "--out", man], HERE)
        print("  second write rc=%d" % rc3)
        for ln in out3.splitlines():
            print("      " + ln)
        if rc3 == 0:
            fails.append("write-once did not refuse")
        if "CAPTURE_MANIFEST_SHA" in out3:
            fails.append("refused write printed a SHA")

        print("\n== 4. UNFROZEN SELECTION: no manifest, no sampling ==")
        d2 = tempfile.mkdtemp(prefix="rehearsal-unfrozen-")
        try:
            sel2, _ = _selection_file(
                d2, False, [], [],
                status="BOARD_RETRIEVAL_INCOMPLETE:HTTP_FAILURE")
            man2 = os.path.join(d2, "capture_manifest.json")
            rc4, out4 = _run(["capture_manifest.py",
                              "--orchestration-version", "3",
                              "--selection", sel2, "--code-sha", "x",
                              "--run-id", "R", "--out", man2], HERE)
            print("  manifest rc=%d  %s" % (rc4, out4.splitlines()[-1]
                                            if out4 else ""))
            print("  manifest file exists: %s" % os.path.exists(man2))
            print("  sampling entry   %s"
                  % sampling_entry_sentinel(man2, 1))
            if rc4 == 0 or os.path.exists(man2):
                fails.append("unfrozen selection produced a manifest")
        finally:
            shutil.rmtree(d2, ignore_errors=True)

        print("\n== 5. MANIFEST FROM A DIFFERENT SELECTION MUST NOT VERIFY ==")
        d3 = tempfile.mkdtemp(prefix="rehearsal-other-")
        try:
            other, _ = _selection_file(d3, True, ["ev-x", "ev-y", "ev-z"],
                                       ["x-1", "y-1", "z-1"])
            rc5, out5 = _run(["capture_manifest.py", "--verify",
                              "--manifest", man, "--selection", other], HERE)
            print("  verify-against-other rc=%d" % rc5)
            print("  sampling entry   %s"
                  % sampling_entry_sentinel(man, rc5))
            if rc5 == 0:
                fails.append("manifest verified against the wrong selection")
        finally:
            shutil.rmtree(d3, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 62)
    # THE HARNESS AND THE CHAIN ARE TWO DIFFERENT VERDICTS, AND CONFLATING
    # THEM IS HOW A REFUSAL GETS REPORTED AS A PASS. Every scenario can behave
    # exactly as designed while the capture is still blocked.
    chain_ok = bool(cont_sel
                    and cont_sel.get("EVENT_SELECTION_FROZEN") == "YES")
    if fails:
        print("REHEARSAL_HARNESS = FAIL")
        for f in fails:
            print("  FAILED: %s" % f)
    else:
        print("REHEARSAL_HARNESS = PASS   (every scenario behaved as designed)")

    print("CONTINUOUS_CHAIN  = %s"
          % ("COMPLETE_TO_SAMPLING_ENTRY" if chain_ok
             else "RAN_END_TO_END_AND_REFUSED"))
    if not chain_ok:
        print("CAPTURE_READY     = NO")
        print("BLOCKED_BY        = BOOK_SHAPE_INCOMPATIBILITY "
              "(substantive_select.two_sided vs the venue's marketData shape)")
        print("                    see section 1d; NOT repaired -- the "
              "eligibility path is frozen")
    else:
        print("CAPTURE_READY     = YES")
    print("VENUE_REQUESTS = 0   ORDERS = 0   mirror_live = false")
    return 1 if (fails or not chain_ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
