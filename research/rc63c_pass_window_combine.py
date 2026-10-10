#!/usr/bin/env python3
"""RC6.3c PROOF C COMBINER: the 60-minute pass-continuity verdict from several reads of
research/rc63c_pass_window.sql (read-only; it parses saved logs, it touches no database).

    python3 -I rc63c_pass_window_combine.py [--since 'YYYY-MM-DD HH:MM:SS'] [--min-minutes 60] LOG1 LOG2 ...

Each LOG is a saved research-sql log of one read (the `gh run view --log` text or the raw psql
output). The reads are ordered by the run header's instant. The rules are the file's header rules:
OVERLAP, UNION (window from the deploy boot, every gap <= 90 s, span >= 60 min), SLOTS (= 0),
COUNTERS (passes delta = beats seen after the previous ring's newest beat, errors delta = 0), BEATS
(ok = true), ATTEMPT (heartbeat ran = true). The window starts at the deploy boot (C8 workers_boot_at,
also carried in the C1 COUNTERS row) unless --since is given. Exit 0 = PASS, 1 = FAIL or VOID.
"""
import re
import sys
from datetime import datetime, timezone

GAP_MAX_S = 90.0
CADENCE_S = 60.0


def _content(line: str) -> str:
    if "\t" in line:
        line = line.rsplit("\t", 1)[1]
    return re.sub(r"^\S*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[.\d]*Z ?", "", line).rstrip("\n")


def _epoch(s: str) -> float:
    return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc).timestamp()


def parse_log(path: str) -> dict:
    lines = [_content(x) for x in open(path, encoding="utf-8", errors="replace")]
    out = {"path": path, "read_at": None, "beats": {}, "counters": None,
           "heartbeat": None, "boot_at": None, "boot_commit": None, "summary": {}}
    for ln in lines:
        m = re.search(r"== research/\S+ on .* at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z", ln)
        if m:
            out["read_at"] = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc).timestamp()
    # C1 table: header "kind | passes | errors | ..." then dashes, rows, "(N rows)"
    i = 0
    while i < len(lines) and not lines[i].startswith("C1 ONE SNAPSHOT"):
        i += 1
    if i >= len(lines):
        raise SystemExit("%s: no C1 section" % path)
    while i < len(lines) and not re.match(r"^\s*kind\s*\|\s*passes\s*\|", lines[i]):
        i += 1
    i += 2  # header, dashes
    while i < len(lines) and not re.match(r"^\s*\(\d+ rows?\)", lines[i]):
        parts = [p.strip() for p in lines[i].split("|")]
        i += 1
        if len(parts) < 12:
            continue
        kind, passes, errors, at_epoch, at, ok, elapsed, slowest, slowest_s, gap, flag, detail = parts[:12]
        kv = dict(re.findall(r"(\w+)=([^;]*)", detail))
        if kind == "COUNTERS":
            out["counters"] = {"passes": int(passes), "errors": int(errors),
                               "heartbeat_at": at, "read_at_txt": kv.get("read_at"),
                               "last_error_kept": kv.get("last_error_kept", "")}
            if kv.get("boot_at") and kv["boot_at"] != "?":
                out["boot_at"] = _epoch(kv["boot_at"])
                out["boot_commit"] = kv.get("boot_commit")
        elif kind == "HEARTBEAT":
            out["heartbeat"] = {"at": at, "ran": ok, "flag": flag, "elapsed_s": elapsed,
                                "trigger": kv.get("trigger"), "refusal": kv.get("refusal"),
                                "why": kv.get("why"), "in_step": kv.get("in_step")}
        elif kind == "BEAT":
            out["beats"][at_epoch] = {
                "at": float(at_epoch), "at_txt": at, "ok": ok == "true",
                "elapsed_s": float(elapsed) if elapsed else None,
                "slowest_step": slowest,
                "slowest_s": float(slowest_s) if slowest_s else None}
        elif kind.startswith("SUMMARY"):
            out["summary"][kind] = {"flag": flag, **kv}
    # C8 workers boot row (the C1 COUNTERS detail carries the same; this is the fallback)
    for j, ln in enumerate(lines):
        if re.match(r"^\s*workers_commit\s*\|\s*workers_boot_at", ln) and j + 2 < len(lines):
            parts = [p.strip() for p in lines[j + 2].split("|")]
            if len(parts) >= 2 and parts[1]:
                try:
                    out["boot_at"] = out["boot_at"] or datetime.fromisoformat(
                        parts[1].replace("Z", "+00:00")).timestamp()
                    out["boot_commit"] = out["boot_commit"] or parts[0]
                except ValueError:
                    pass
    if out["counters"] is None or not out["beats"]:
        raise SystemExit("%s: C1 has no COUNTERS row or no BEAT rows" % path)
    return out


def _t(e: float) -> str:
    return datetime.fromtimestamp(e, timezone.utc).strftime("%m-%d %H:%M:%S")


def combine(reads: list, since: float | None, min_minutes: float) -> int:
    fails: list[str] = []
    notes: list[str] = []
    reads = sorted(reads, key=lambda r: (r["read_at"] or 0.0))
    boots = {r["boot_at"] for r in reads if r["boot_at"]}
    if since is None:
        if not boots:
            fails.append("no deploy boot instant: C8 workers_boot_at missing in every read; pass --since")
            since = 0.0
        else:
            since = max(boots)
            if len(boots) > 1:
                fails.append("the workers process booted more than once across the reads (%s): a restart "
                             "inside the window" % ", ".join(_t(b) for b in sorted(boots)))
    commits = {r["boot_commit"] for r in reads if r["boot_commit"]}
    notes.append("reads: %d; window starts at boot %s; boot commit(s): %s"
                 % (len(reads), _t(since), ", ".join(sorted(commits)) or "?"))
    # OVERLAP and COUNTERS, pairwise
    union: dict[str, dict] = {}
    prev = None
    for r in reads:
        label = "%s (read %s)" % (r["path"].rsplit("/", 1)[-1], _t(r["read_at"]) if r["read_at"] else "?")
        union.update(r["beats"])
        hb = r["heartbeat"] or {}
        if hb.get("ran") != "true" or hb.get("flag") != "ok":
            fails.append("ATTEMPT %s: heartbeat ran=%s flag=%s refusal=%s why=%s in_step=%s"
                         % (label, hb.get("ran"), hb.get("flag"), hb.get("refusal"), hb.get("why"),
                            hb.get("in_step")))
        if prev is not None:
            shared = set(r["beats"]) & set(prev["beats"])
            if not shared:
                fails.append("OVERLAP %s: shares no beat with the previous read (%s..%s vs %s..%s): VOID, "
                             "repeat the read" % (
                                 label, _t(min(b["at"] for b in prev["beats"].values())),
                                 _t(max(b["at"] for b in prev["beats"].values())),
                                 _t(min(b["at"] for b in r["beats"].values())),
                                 _t(max(b["at"] for b in r["beats"].values()))))
            else:
                newest_prev = max(b["at"] for b in prev["beats"].values())
                newest_this = max(b["at"] for b in r["beats"].values())
                seen = sum(1 for b in union.values() if newest_prev < b["at"] <= newest_this)
                dp = r["counters"]["passes"] - prev["counters"]["passes"]
                de = r["counters"]["errors"] - prev["counters"]["errors"]
                if dp != seen:
                    fails.append("COUNTERS %s: passes +%d but %d new beats after %s (an unrecorded or an unseen "
                                 "pass)" % (label, dp, seen, _t(newest_prev)))
                if de != 0:
                    fails.append("COUNTERS %s: errors +%d since the previous read (last_error_kept: %s)"
                                 % (label, de, r["counters"]["last_error_kept"]))
                notes.append("%s: overlap %d beats, passes +%d = %d new beats, errors +%d"
                             % (label, len(shared), dp, seen, de))
        prev = r
    # UNION window
    beats = sorted(union.values(), key=lambda b: b["at"])
    pre = [b for b in beats if b["at"] < since]
    win = [b for b in beats if b["at"] >= since]
    if not win:
        fails.append("UNION: no beat at or after the boot %s" % _t(since))
        return _report(fails, notes, None)
    span_s = win[-1]["at"] - win[0]["at"]
    slots = 0
    gaps_over = 0
    for a, b in zip(win, win[1:]):
        gap = b["at"] - a["at"]
        if gap > GAP_MAX_S:
            gaps_over += 1
            why = ("previous beat ran %.1fs (coalesced a tick)" % a["elapsed_s"]
                   if a["elapsed_s"] is not None and a["elapsed_s"] > CADENCE_S
                   else "UNEXPLAINED (a pass cut as a whole / raised before its record / never scheduled)")
            fails.append("UNION gap %.1fs from %s to %s: %s" % (gap, _t(a["at"]), _t(b["at"]), why))
        slots += max(int(round(gap / CADENCE_S)) - 1, 0)
    if slots:
        fails.append("SLOTS: implied_unrecorded_60s_slots = %d (must be 0)" % slots)
    bad = [b for b in win if not b["ok"]]
    if bad:
        fails.append("BEATS: %d beat(s) with ok=false in the window: %s"
                     % (len(bad), ", ".join(_t(b["at"]) for b in bad[:12])))
    if span_s < min_minutes * 60.0:
        fails.append("UNION: the window spans %.1f min (%s..%s), less than %.0f min"
                     % (span_s / 60.0, _t(win[0]["at"]), _t(win[-1]["at"]), min_minutes))
    slow = [b for b in win if b["slowest_s"] is not None and b["slowest_s"] >= 20]
    if slow:
        fails.append("BEATS: %d beat(s) with a step over 20 s (%s)" % (
            len(slow), ", ".join("%s %s %.1fs" % (_t(b["at"]), b["slowest_step"], b["slowest_s"]) for b in slow[:6])))
    window = {"beats": len(win), "pre_deploy_beats": len(pre), "first": _t(win[0]["at"]),
              "last": _t(win[-1]["at"]), "span_min": span_s / 60.0, "gaps_over_90s": gaps_over,
              "implied_unrecorded_60s_slots": slots,
              "max_gap_s": max((b["at"] - a["at"] for a, b in zip(win, win[1:])), default=0.0),
              "max_elapsed_s": max((b["elapsed_s"] or 0.0 for b in win), default=0.0),
              "not_ok_beats": len(bad)}
    return _report(fails, notes, window)


def _report(fails: list, notes: list, window: dict | None) -> int:
    for n in notes:
        print("note:", n)
    if window:
        print("window: %(beats)d beats from %(first)s to %(last)s = %(span_min).1f min; pre-deploy beats "
              "excluded: %(pre_deploy_beats)d; max gap %(max_gap_s).1fs; gaps over 90 s: %(gaps_over_90s)d; "
              "implied unrecorded 60 s slots: %(implied_unrecorded_60s_slots)d; not-ok beats: %(not_ok_beats)d; "
              "max pass elapsed %(max_elapsed_s).2fs" % window)
    for f in fails:
        print("FAIL:", f)
    verdict = "PASS" if not fails else ("VOID" if any(f.startswith("OVERLAP") for f in fails) else "FAIL")
    print("VERDICT:", verdict)
    return 0 if verdict == "PASS" else 1


def main(argv: list) -> int:
    since = None
    min_minutes = 60.0
    paths = []
    it = iter(argv)
    for a in it:
        if a == "--since":
            since = _epoch(next(it))
        elif a == "--min-minutes":
            min_minutes = float(next(it))
        else:
            paths.append(a)
    if not paths:
        print(__doc__)
        return 2
    return combine([parse_log(p) for p in paths], since, min_minutes)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
