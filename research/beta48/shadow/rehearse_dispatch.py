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


def rehearse_discovery():
    """The ADAPTED path, on retained /v1/events bodies. No venue traffic.

        retained event listing
          -> child-market extraction
          -> the unchanged eligibility and ranking rules
          -> the unchanged freeze

    Evaluated at the FIXTURE'S OWN AS-OF. These events were eligible on
    2026-09-14 and are not eligible today; using the current clock would
    present historical markets as current ones.
    """
    sys.path.insert(0, HERE)
    import substantive_select as S
    import events_adapter as EA
    import event_identity as EI

    with open(os.path.join(HERE, "fixtures_events_block3.json")) as fh:
        fx = json.load(fh)
    page0 = fx["RETAINED_PAGES"][0]["body"]
    as_of = fx["AS_OF_UTC"]

    print("  fixtures      %s (%d events retained, %d embedded)"
          % (fx["PROVENANCE"]["SOURCE_RUN"],
             fx["PROVENANCE"]["EVENTS_RETAINED"],
             fx["PROVENANCE"]["EVENTS_EMBEDDED_HERE"]))
    print("  as-of         %s  (HISTORICAL: %s)"
          % (as_of, fx["AS_OF_IS_HISTORICAL"]))

    class Resp:
        def __init__(self, body, status=200):
            self.status_code = status
            self._b = body

        def json(self):
            return self._b

    seq = [page0, {"events": []}]          # page, then the terminal page

    def get(params):
        i = int(params["offset"]) // int(params["limit"])
        return Resp(seq[i] if i < len(seq) else {"events": []})

    by, idx, rec, st, blk = S.discovery_walk(get, page_limit=6,
                                             endpoint=S.EVENTS_PATH)
    cert, why = S.certify_completion(st, rec, len(idx), S.EVENTS_PATH)
    print("  walk          pages=%d  status=%s  certified=%s"
          % (len(rec), st, cert))
    print("  counts        EVENT_ROWS=%d  MARKET_ROWS=%d  (never compared)"
          % (blk["EVENT_ROWS"], blk["MARKET_ROWS_EXTRACTED"]))
    print("  dropped       %d  conflicting=%d"
          % (blk["DROPPED_COUNT"], blk["CONFLICTING_DUPLICATE_COUNT"]))

    lv = {}
    for m in by.values():
        _, l = EI.event_identity(m)
        lv[l] = lv.get(l, 0) + 1
    print("  identity      " + "  ".join(
        "%s=%d" % (k.split("_")[1], v) for k, v in sorted(lv.items())))

    books = {s: {"bids": [{"price": "0.4"}], "asks": [{"price": "0.6"}]}
             for s in by}
    act = {s: {"ACTIVE_AT_DECISION": True} for s in by}
    sel = S.freeze(list(by.values()), books, as_of, activity_of=act)
    sel.update(S.discovery_retrieval_block(by, idx, rec, st, blk,
                                           endpoint=S.EVENTS_PATH))
    sel["SELECTION_STATUS"] = S.selection_status(
        cert, sel.get("EVENT_SELECTION_FROZEN") == "YES")

    print("  freeze        FROZEN=%s  events=%d  markets=%d  status=%s"
          % (sel["EVENT_SELECTION_FROZEN"], len(sel["EVENT_IDS"]),
             len(sel["MARKET_IDS"]), sel["SELECTION_STATUS"]))

    parents = {e["slug"] for e in page0["events"]}
    parent_leak = set(sel["MARKET_SLUGS"]) & parents
    print("  parent leak   %s" % (sorted(parent_leak) or "none"))

    ok = (cert == S.BOARD_VERIFIED_END
          and sel["EVENT_SELECTION_FROZEN"] == "YES"
          and not parent_leak
          and blk["CONFLICTING_DUPLICATE_COUNT"] == 0
          and blk["MARKET_ROWS_EXTRACTED"] > blk["EVENT_ROWS"])
    print("  -> %s" % ("OK" if ok else "FAILED"))
    return ok, sel


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

    print("\n== 1b. DISCOVERY, real /v1/events walk on RETAINED bodies ==")
    ok, disc_sel = rehearse_discovery()
    if not ok:
        fails.append("events discovery")

    print("\n== 2. COMMAND SEQUENCE, real CLIs, non-sorted roster ==")
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
    if fails:
        print("REHEARSAL_RESULT = FAIL")
        for f in fails:
            print("  FAILED: %s" % f)
        return 1
    print("REHEARSAL_RESULT = PASS")
    print("VENUE_REQUESTS = 0   ORDERS = 0   mirror_live = false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
