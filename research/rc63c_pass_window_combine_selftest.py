#!/usr/bin/env python3
"""Self-test of rc63c_pass_window_combine.py on synthetic logs in the C1 output shape.
Scenarios: clean 60-min window (PASS); a whole-pass cut hidden between rings (FAIL by UNION gap + SLOTS);
reads too far apart (VOID); a passes counter that outruns the beats (FAIL by COUNTERS); an error counted
(FAIL by COUNTERS errors); a window under 60 min (FAIL by UNION span); a ran=false heartbeat (FAIL by ATTEMPT)."""
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

COMBINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rc63c_pass_window_combine.py")
T0 = datetime(2026, 10, 10, 22, 0, 0, tzinfo=timezone.utc).timestamp()   # the deploy boot


def fmt(e):
    return datetime.fromtimestamp(e, timezone.utc).strftime("%m-%d %H:%M:%S")


def log_text(read_at, beats, passes, errors, hb_ran="true", boot=T0):
    """beats: list of (at_epoch, ok, elapsed) already restricted to the ring (newest 20)."""
    ring = sorted(beats)[-20:]
    rows = []
    rows.append("COUNTERS | %d | %d | %s | %s |  |  |  |  | 12.0 |  | session=s1; mutation_attempts=0; read_at=%s; boot_at=%s; boot_commit=abc123; last_error_kept=x"
                % (passes, errors, repr(ring[-1][0]), fmt(ring[-1][0]),
                   datetime.fromtimestamp(read_at, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                   datetime.fromtimestamp(boot, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
    rows.append("HEARTBEAT |  |  | %s | %s | %s | 9.1 |  |  | 9.3 | %s | trigger=SERVICING_TASK; refusal=%s; why=-; skipped=-; error_keys=-; in_step=-; written=%s"
                % (repr(ring[-1][0]), fmt(ring[-1][0]), hb_ran, "ok" if hb_ran == "true" else "FAIL_RAN_FALSE:PAPER_PASS_RAISED_OR_TIMED_OUT",
                   "-" if hb_ran == "true" else "PAPER_PASS_RAISED_OR_TIMED_OUT", fmt(ring[-1][0] + 9)))
    prev = None
    for at, ok, el in ring:
        gap = "" if prev is None else "%.1f" % (at - prev)
        rows.append("BEAT |  |  | %s | %s | %s | %.2f | derek | 3.10 | %s |  | decisions=0; orders=0; fills=0; reviews=0; budget_exhausted=false; held_reviews=-"
                    % (repr(at), fmt(at), "true" if ok else "false", el, gap))
        prev = at
    rows.append("SUMMARY_RING |  |  |  | x..y |  |  |  |  |  | ok | beats=%d" % len(ring))
    rows.append("SUMMARY_SINCE_BOOT |  |  |  | x..y |  |  |  |  |  | ok | beats=%d" % len(ring))
    head = ["== research/rc63c_pass_window.sql on db at %sZ, statement_timeout 600000ms =="
            % datetime.fromtimestamp(read_at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "C1 ONE SNAPSHOT (one statement): ...",
            " kind | passes | errors | at_epoch | at | ok | elapsed_s | slowest_step | slowest_s | gap_s | flag | detail ",
            "------+--------+--------+----------+----+----+-----------+--------------+-----------+-------+------+--------"]
    tail = ["(%d rows)" % len(rows), "", "C8 ...", " workers_commit | workers_boot_at ", "----------------+-----------------",
            " abc123 | %s " % datetime.fromtimestamp(boot, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"), "(1 row)", "psql exit=0"]
    return "\n".join(head + [" " + r + " " for r in rows] + tail) + "\n"


def scenario(name, read_offsets_min, missing=(), extra_passes_at=None, error_at=None, hb_false_at=None, cadence=60.0):
    """Beats every `cadence` s from T0+30 s; `missing` = set of beat indexes with no beat (a whole-pass cut);
    passes = 5000 + beats recorded so far (+1 at extra_passes_at read index); errors = 7 (+1 from error_at read)."""
    logs = []
    for ri, off in enumerate(read_offsets_min):
        read_at = T0 + off * 60.0
        beats = []
        n_rec = 0
        k = 0
        while True:
            at = T0 + 30.0 + k * cadence
            if at > read_at - 5:
                break
            if k not in missing:
                beats.append((at + 0.123, True, 11.5))
                n_rec += 1
            k += 1
        passes = 5000 + n_rec + (1 if extra_passes_at is not None and ri >= extra_passes_at else 0)
        errors = 7 + (1 if error_at is not None and ri >= error_at else 0)
        logs.append(log_text(read_at, beats, passes, errors,
                             hb_ran="false" if hb_false_at == ri else "true"))
    d = tempfile.mkdtemp(prefix="c63_" + name + "_")
    paths = []
    for i, t in enumerate(logs):
        p = os.path.join(d, "read%d.log" % (i + 1))
        open(p, "w").write(t)
        paths.append(p)
    r = subprocess.run([sys.executable, "-I", COMBINE] + paths, capture_output=True, text=True)
    verdict = [ln for ln in r.stdout.splitlines() if ln.startswith("VERDICT")]
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
    return verdict[0] if verdict else "NO VERDICT\n" + r.stdout + r.stderr, fails, r.returncode


def main():
    results = []
    v, f, rc = scenario("pass", [12, 24, 36, 48, 60, 72])
    results.append(("clean 60-min window, six reads 12 min apart", v, f, rc, "PASS"))
    v, f, rc = scenario("cut", [12, 24, 36, 48, 60, 72], missing={40})      # beat 40 = T0+40.5 min, between R3 (36) and R4 (48)
    results.append(("one whole-pass cut hidden between two reads (rings overlap)", v, f, rc, "FAIL"))
    v, f, rc = scenario("void", [12, 40, 70])
    results.append(("reads 28 and 30 min apart (rings share no beat)", v, f, rc, "VOID"))
    v, f, rc = scenario("counter", [12, 24, 36, 48, 60, 72], extra_passes_at=3)
    results.append(("passes counter +1 with no beat (an unrecorded pass)", v, f, rc, "FAIL"))
    v, f, rc = scenario("errors", [12, 24, 36, 48, 60, 72], error_at=2)
    results.append(("errors +1 between two reads", v, f, rc, "FAIL"))
    v, f, rc = scenario("short", [12, 24, 36, 48])
    results.append(("four reads: window under 60 min", v, f, rc, "FAIL"))
    v, f, rc = scenario("hb", [12, 24, 36, 48, 60, 72], hb_false_at=4)
    results.append(("heartbeat ran=false at one read", v, f, rc, "FAIL"))
    v, f, rc = scenario("coalesce", [12, 24, 36, 48, 60, 72], cadence=120.0)
    results.append(("every pass 120 s apart (slots implied by every gap)", v, f, rc, "FAIL"))
    ok = True
    for name, v, f, rc, want in results:
        got = v.split(":")[-1].strip()
        good = got == want
        ok = ok and good
        print("%s  %-62s -> %s (want %s)%s" % ("ok  " if good else "BAD ", name, got, want,
                                                "" if not f else "  | " + " | ".join(x[:90] for x in f[:2])))
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
