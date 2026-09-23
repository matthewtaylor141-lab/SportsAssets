"""THE ACTUAL JOURNAL ROWS, turned back into the records the page reads.

WHAT THIS IS. `render-ops sql obs-incentive-rows` runs four read-only
statements against the production database and prints their output. This
module parses that output back into the exact list of dicts
`bettor_command_center.build()` consumes, so the preview can be driven
by the run's OWN records rather than by a reconstruction from its
aggregates.

WHAT IS LOSSLESS. Every field the command centre reads:

    boot_id, kind, epoch, slug                verbatim
    non-ladder payloads                       verbatim, whole
    per-ladder bid and ask level counts       verbatim
    per-ladder ladder_class                   verbatim
    ladder timestamps                         to the millisecond

WHAT IS NOT. Two things, and neither is a figure on the page:

  * THE BOOK BODIES. No bid, ask, price or size is here. A ladder
    carries `bids` and `offers` as arrays of the right LENGTH and
    nothing else, and every one is stamped `bodies_elided: True`. The
    page asks of a frame only whether depth was persisted and how deep
    it went, and that is what survives. Any claim about what a book
    CONTAINED is unsupported by this file and always will be.

  * THE LAST FOUR DECIMAL PLACES of a ladder's timestamp. The export
    packs each ladder's instant as milliseconds since the run's first
    record, which is what keeps a day of frames small enough to read
    back at all. Non-ladder records -- every gap bound, every epoch,
    every close -- keep their full precision, so the segment and gap
    arithmetic is exact; only the per-market first-snapshot instants
    are rounded, by at most half a millisecond each.

SO IT IS NOT A FIXTURE AND IT IS NOT THE RAW TABLE. It is the real rows
with the books removed, and the page says so in those words.
"""
from __future__ import annotations

import datetime as dt
import json
import os

# class codes, as the SQL emits them
_CLASS = {0: None, 1: "INITIAL_LADDER", 2: "UPDATE", 3: "OTHER"}


class ExportError(RuntimeError):
    """The export is not in the shape this module knows how to read.

    Raised rather than returning a partial record set: a preview drawn
    from half an export would be a page of wrong numbers with no sign
    that anything was missing.
    """


def _lines(text: str) -> list:
    out = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        # GitHub stamps every log line; psql's own trailers are not data.
        if line.startswith("psql exit=") or line.startswith("Cleaning up"):
            continue
        if line.startswith("==") or line.startswith("action="):
            continue
        out.append(line)
    return out


def parse(text: str) -> dict:
    """Parse one `obs-incentive-rows` output into records and state."""
    lines = _lines(text)
    if not lines:
        raise ExportError("the export is empty")

    header = None
    metas, ladders, state = [], None, None
    for line in lines:
        if not line.startswith(("{", "[")):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, list):
            ladders = obj
            continue
        if "encoding" in obj and "boots" in obj:
            header = obj
            continue
        if "state_read_at" in obj:
            state = obj
            continue
        if "kind" in obj and "boot_id" in obj:
            metas.append(obj)

    if header is None:
        raise ExportError("no header row: this is not an obs-incentive-rows "
                          "export")
    if ladders is None:
        raise ExportError("no ladder array in the export")

    boots = header["boots"]
    slugs = header["slugs"]
    base = float(header["base_at"])

    recs = []
    for m in metas:
        recs.append({"boot_id": m["boot_id"], "at": float(m["at"]),
                     "kind": m["kind"], "epoch": m.get("epoch"),
                     "slug": m.get("slug"), "payload": m.get("payload") or {}})

    for t in ladders:
        try:
            bi, ms, epoch, si, nb, na, cls = t
        except (TypeError, ValueError) as exc:
            raise ExportError("a ladder tuple is not the seven-field "
                              "shape: %r" % (t,)) from exc
        recs.append({
            "boot_id": boots[bi],
            "at": base + ms / 1000.0,
            "kind": "LADDER",
            "epoch": epoch,
            "slug": slugs[si],
            "payload": {"bids": [1] * int(nb), "offers": [1] * int(na),
                        "levels": int(nb) + int(na),
                        "ladder_class": _CLASS.get(cls, "OTHER"),
                        # THE STAMP THAT STOPS A MISREADING. No price
                        # here is real, and nothing may be computed from
                        # one.
                        "bodies_elided": True}})

    recs.sort(key=lambda r: r["at"])

    # THE COUNTS ARE CHECKED, NOT ASSUMED. If the log was truncated, the
    # arrays are short and the page would render a smaller run as if it
    # were the whole one.
    n_lad = sum(1 for r in recs if r["kind"] == "LADDER")
    if n_lad != int(header["ladders_total"]):
        raise ExportError("the export carries %d ladders but its header "
                          "counts %d -- the log was truncated"
                          % (n_lad, header["ladders_total"]))
    if len(recs) != int(header["rows_total"]):
        raise ExportError("the export carries %d rows but its header counts "
                          "%d" % (len(recs), header["rows_total"]))

    return {"header": header, "records": recs, "state": state or {}}


def _allowlist(recs) -> list:
    """The twelve markets, from the run's own RUN_OPEN or PROGRAM_VERSION."""
    for r in recs:
        p = r.get("payload") or {}
        if r["kind"] == "RUN_OPEN" and isinstance(p.get("allowlist"), list):
            return sorted(p["allowlist"])
    for r in recs:
        p = r.get("payload") or {}
        if r["kind"] == "PROGRAM_VERSION" and isinstance(p.get("programs"),
                                                         dict):
            return sorted(p["programs"])
    return sorted({r["slug"] for r in recs if r.get("slug")})


def scenario(text: str) -> dict:
    """The preview scenario for a real export."""
    parsed = parse(text)
    recs = parsed["records"]
    st = parsed["state"]
    hdr = parsed["header"]

    control = st.get("control")
    if isinstance(control, str):
        control = control.strip().lower() == "true"
    probe = st.get("probe")
    run_row = st.get("run_row")
    read_at = float(st.get("state_read_at") or hdr["read_at"])

    return {
        "why": "ACTUAL JOURNAL RECORDS of the 2026-09-23 run, read back "
               "from production. Every boot, epoch, gap, close, slug and "
               "ladder class below is the row the collector wrote. The "
               "book BODIES are not here: each ladder carries its two "
               "level counts and nothing else, and ladder instants are "
               "rounded to the millisecond. Nothing on this page is "
               "computed from a price.",
        "real": True,
        "actual_records": True,
        "read_at": dt.datetime.fromtimestamp(
            read_at, dt.timezone.utc).isoformat(),
        "control": bool(control),
        "probe": probe,
        "run_row": run_row,
        "allowlist": _allowlist(recs),
        "now": read_at,
        "records": recs,
    }


DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))),
    "research/beta48/acceptance/journal_export_20260923T1127Z.txt")


def load(path: str = None) -> dict:
    path = path or os.environ.get("BETTOR_JOURNAL_EXPORT") or DEFAULT_PATH
    with open(path, encoding="utf-8") as fh:
        return scenario(fh.read())


if __name__ == "__main__":                                  # pragma: no cover
    import sys
    sc = load(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({"read_at": sc["read_at"], "control": sc["control"],
                      "records": len(sc["records"]),
                      "ladders": sum(1 for r in sc["records"]
                                     if r["kind"] == "LADDER"),
                      "allowlist": len(sc["allowlist"])}, indent=2))
