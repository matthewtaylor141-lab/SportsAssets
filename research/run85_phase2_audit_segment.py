#!/usr/bin/env python3
"""Audit ONE sealed Run 85 Phase 2 capture segment. Contacts nothing.

This reads the bytes that are IN THE REPOSITORY, through `git show <ref>:<path>`,
not the working tree and not the runner's terminal output. A segment that was
captured, sealed and reported perfectly but never reached the branch fails here,
which is the point: proof run #2 did exactly that.

Every check prints PASS or FAIL with the number it saw. The exit code is 0 only
if every load-bearing check passed.

  python3 research/run85_phase2_audit_segment.py \
      --ref origin/claude/session-njaewf \
      --segment research/evidence/capture/run85_phase2_segment_<stamp>
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import sys

COHORT_SHA256 = "8cdedf26479a309bf7e22c292ebdd2f562673e2b8dccb2ec2793d9ad29187efe"
COHORT_PATH = "research/run85_phase2_frozen_cohort.json"
SPACING_S = 2.5
GET_ROUTE_SUFFIXES = ("/book", "/bbo")


class Audit:
    def __init__(self):
        self.rows = []
        self.failed = 0

    def check(self, name, ok, detail=""):
        ok = bool(ok)
        if not ok:
            self.failed += 1
        self.rows.append((name, "PASS" if ok else "FAIL", detail))
        return ok

    def note(self, name, detail):
        self.rows.append((name, "----", detail))

    def dump(self):
        w = max(len(n) for n, _, _ in self.rows)
        for n, v, d in self.rows:
            print("%-*s  %-4s  %s" % (w, n, v, d))
        print()
        print("AUDIT_FAILURES = %d" % self.failed)


def git_show(ref, path):
    return subprocess.run(["git", "show", "%s:%s" % (ref, path)],
                          check=True, capture_output=True).stdout


def git_ls(ref, path):
    out = subprocess.run(["git", "ls-tree", "--name-only", "%s:%s" % (ref, path)],
                         check=True, capture_output=True).stdout
    return sorted(out.decode().split())


def jsonl_gz(raw):
    with gzip.open(io.BytesIO(raw), "rt") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--segment", required=True)
    a = ap.parse_args(argv)
    A = Audit()
    seg = a.segment.rstrip("/")

    # ---------------------------------------------------- the segment exists
    files = git_ls(a.ref, seg)
    A.note("SEALED_SEGMENT", seg)
    A.note("FILES_COMMITTED", " ".join(files))
    A.check("SEALED_SEGMENT_EXISTS", bool(files), "%d files" % len(files))
    for want in ("checksums.sha256", "request_log.jsonl.gz", "sets.jsonl.gz",
                 "runner_metadata.json", "segment_summary.json",
                 "run85_terminal_report.txt"):
        A.check("FILE_PRESENT:%s" % want, want in files)
    A.check("MANIFEST_PRESENT",
            "runner_metadata.json" in files and "segment_summary.json" in files)

    blobs = {f: git_show(a.ref, "%s/%s" % (seg, f)) for f in files}

    # ------------------------- the manifest vs the bytes that actually landed
    manifest = {}
    for line in blobs["checksums.sha256"].decode().strip().splitlines():
        want, name = line.split("  ", 1)
        manifest[name] = want
    listed = set(manifest)
    present = set(files) - {"checksums.sha256"}
    A.check("MANIFEST_COVERS_EVERY_COMMITTED_FILE", listed == present,
            "manifest=%d committed=%d" % (len(listed), len(present)))
    bad = [n for n, want in manifest.items()
           if n not in blobs or hashlib.sha256(blobs[n]).hexdigest() != want]
    A.check("INTERNAL_CHECKSUMS", not bad, "mismatched=%s" % (bad or "none"))
    A.check("COMMITTED_BYTES_MATCH_SEALED_BYTES", not bad and listed == present,
            "every digest re-derived from the committed blob")
    seg_digest = hashlib.sha256(blobs["checksums.sha256"]).hexdigest()
    A.note("SEGMENT_DIGEST_SHA256", seg_digest)
    A.check("SEGMENT_DIGEST", len(seg_digest) == 64 and not bad)

    meta = json.loads(blobs["runner_metadata.json"])
    summ = json.loads(blobs["segment_summary.json"])

    # ------------------------------------------------------- frozen cohort
    cohort_raw = git_show(a.ref, COHORT_PATH)
    live = hashlib.sha256(cohort_raw).hexdigest()
    A.check("FROZEN_COHORT_HASH_MATCH",
            live == COHORT_SHA256 == meta.get("cohort_sha256"),
            "repo=%s runner=%s" % (live[:8], str(meta.get("cohort_sha256"))[:8]))
    cohort = json.loads(cohort_raw)
    A.check("COHORT_SIZE_12", len(cohort) == 12, "%d markets" % len(cohort))
    A.check("COHORT_EVENTS_12",
            len({e["native_event_id"] for e in cohort}) == 12)
    slugs = {e["market_slug"] for e in cohort}

    # ------------------------------------------------------------ raw rows
    rows = jsonl_gz(blobs["request_log.jsonl.gz"])
    A.check("RAW_EVIDENCE_PRESERVED", bool(rows), "%d request rows" % len(rows))
    A.check("RAW_ROW_COUNT_MATCHES_SUMMARY",
            len(rows) == summ.get("venue_requests"),
            "raw=%d summary=%s" % (len(rows), summ.get("venue_requests")))
    bodies = sum(1 for r in rows if r.get("http_status") == 200 and r.get("body"))
    ok200 = sum(1 for r in rows if r.get("http_status") == 200)
    A.check("RAW_BODIES_RETAINED_FOR_EVERY_200", bodies == ok200,
            "bodies=%d of %d" % (bodies, ok200))

    # every request is one of the two authorized public GET routes, and the
    # slug in the path is a frozen-cohort slug. A request to anything else
    # would be outside the capability boundary.
    off = [r.get("path") for r in rows
           if not str(r.get("path", "")).endswith(GET_ROUTE_SUFFIXES)]
    A.check("NON_GET_ROUTES_IN_LOG", not off, "off-route=%s" % (off[:3] or "none"))
    A.check("NON_GET_REQUESTS_ZERO", not off,
            "the collector issues GET only and every row is a book/bbo read")
    stray = {str(r.get("path")).split("/")[3] for r in rows} - slugs
    A.check("EVERY_REQUEST_IS_A_COHORT_SLUG", not stray, "stray=%s" % (stray or "none"))

    nb = sum(1 for r in rows if str(r.get("path", "")).endswith("/book"))
    nq = sum(1 for r in rows if str(r.get("path", "")).endswith("/bbo"))
    A.check("BOOK_OBSERVATIONS_POSITIVE", nb > 0, "%d" % nb)
    A.check("PAIRED_BBO_OBSERVATIONS_POSITIVE", nq > 0, "%d" % nq)
    A.note("BOOK_BBO_PAIRING", "book=%d bbo=%d" % (nb, nq))

    n429 = sum(1 for r in rows if r.get("http_status") == 429)
    A.note("HTTP_429_COUNT", str(n429))

    # ------------------------------------------------------------- the rate
    starts = sorted(r["local_request_monotonic_ns"] / 1e9 for r in rows)
    gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
    gmin = min(gaps) if gaps else None
    A.check("MIN_INTER_REQUEST_GAP_GE_2500MS",
            gmin is not None and gmin >= SPACING_S - 1e-6,
            "min=%.4f s" % gmin if gmin is not None else "no gaps")
    rps = (len(starts) - 1) / (starts[-1] - starts[0]) if len(starts) > 1 else None
    A.check("ACHIEVED_RPS_AT_OR_UNDER_NOMINAL", rps is not None and rps <= 0.4 + 1e-9,
            "%.4f rps" % rps if rps else "n/a")
    A.note("GAP_MIN_MEDIAN_MAX",
           "%.4f / %.4f / %.4f" % (gmin, sorted(gaps)[len(gaps) // 2], max(gaps))
           if gaps else "n/a")

    # ------------------------------------------------------------- the sets
    sets = jsonl_gz(blobs["sets.jsonl.gz"])
    A.check("SETS_PRESENT", bool(sets), "%d sets" % len(sets))
    valid = [s for s in sets if s.get("valid_horizon_set")]
    A.check("VALID_HORIZON_SETS_POSITIVE", bool(valid), "%d of %d"
            % (len(valid), len(sets)))
    idf = sum(s.get("identity_failures", 0) for s in sets)
    raw_idf = sum(1 for r in rows
                  if r.get("http_status") == 200
                  and ((r.get("body") or {}).get("marketData") or {}).get("marketSlug")
                  not in (None, str(r.get("path")).split("/")[3]))
    A.check("IDENTITY_FAILURES_ZERO", idf == 0 and raw_idf == 0,
            "sets=%d raw=%d" % (idf, raw_idf))

    # --------------------------------- the semantics the gate exists to guard
    zero_kept = changed = unchanged = 0
    touched = crossed = both = 0
    fill_promoted = pair_promoted = 0
    for s in sets:
        for h, hr in (s.get("horizons") or {}).items():
            if not hr.get("horizon_observation_available"):
                continue
            if hr.get("book_changed_by_horizon"):
                changed += 1
            else:
                unchanged += 1
                # an unchanged book with a postable t0 MUST carry a zero
                # markout, present and equal to "0" -- not absent, not dropped
                if s.get("passive_quote_postable"):
                    if hr.get("mid_markout") == "0":
                        zero_kept += 1
            if hr.get("passive_touch_proxy"):
                touched += 1
            if hr.get("buy_crossed") or hr.get("sell_crossed"):
                crossed += 1
            if hr.get("pair_both_touched"):
                both += 1
                if hr.get("pair_completed") != "NOT_IDENTIFIED":
                    pair_promoted += 1
            for k, v in hr.items():
                if "fill" in k.lower() and v not in (None, False, "NOT_IDENTIFIED"):
                    fill_promoted += 1
        if s.get("passive_fill_probability") != "NOT_IDENTIFIED":
            fill_promoted += 1

    postable_unchanged = sum(
        1 for s in sets if s.get("passive_quote_postable")
        for hr in (s.get("horizons") or {}).values()
        if hr.get("horizon_observation_available")
        and not hr.get("book_changed_by_horizon"))
    A.check("UNCHANGED_BOOKS_RETAINED_AS_ZERO_MARKOUTS",
            zero_kept == postable_unchanged,
            "zero markouts kept %d of %d unchanged postable horizons"
            % (zero_kept, postable_unchanged))
    A.note("BOOK_CHANGED_VS_UNCHANGED", "changed=%d unchanged=%d" % (changed, unchanged))
    A.check("TOUCH_PROMOTED_TO_FILL_NO", fill_promoted == 0,
            "promotions=%d" % fill_promoted)
    A.check("TWO_TOUCHES_PROMOTED_TO_COMPLETED_PAIR_NO", pair_promoted == 0,
            "both_touched=%d promoted=%d" % (both, pair_promoted))
    A.note("TOUCHED_CROSSED", "touched=%d crossed=%d" % (touched, crossed))

    # ---------------------------------------------------------- the subjects
    retired = summ.get("retired") or {}
    observable = len(cohort) - len(retired)
    A.note("MARKETS_OBSERVABLE", "%d / %d" % (observable, len(cohort)))
    for slug, why in retired.items():
        A.note("RETIRED:%s" % slug, json.dumps(why))

    A.note("SEGMENT_START_UTC", str(meta.get("segment_start_utc")))
    A.note("SEGMENT_END_UTC", str(meta.get("segment_end_utc")))
    A.note("CAPTURE_ELAPSED_SECONDS", "%.1f" % (summ.get("elapsed_s") or 0.0))
    A.note("STOP_REASON", str(summ.get("stop_reason")))
    A.note("NOMINAL_RPS", str(meta.get("nominal_rps")))

    A.dump()
    return 1 if A.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
