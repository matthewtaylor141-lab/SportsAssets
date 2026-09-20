#!/usr/bin/env python3
"""§B. IS THE PUBLIC EXECUTION TAPE ACTUALLY THERE, AND WHAT IS IN IT.

Owner directive, "GO on the narrow next step" §B:

    "Actually fetch one real tape file. I do not want 'Tape reader
    exists.' I want TAPE_FETCH_STATUS = PROVEN / FAILED and, if
    proven: file/date, retrieval timestamp, row count, schema/columns,
    earliest and latest transaction timestamp, unique symbols, whether
    BETTOR market symbols can be joined deterministically,
    duplicate-row behavior, timestamp/price/quantity precision,
    missing values, whether multiple trades can share the same
    timestamp/symbol/price, whether the tape appears cumulative or
    incremental, publication lag actually observed."

READ-ONLY, AND NARROWER THAN IT LOOKS. This drives `tape_capture.py`,
which performs the only network access: GET, one host, link-following
only, no credential, no mutating verb. Nothing here constructs a URL
from a filename convention -- a guessed 404 is indistinguishable from
"the venue does not publish this", and a guessed 200 on the wrong path
is worse.

WHAT THIS DOES NOT CLAIM. The tape carries Transaction Time, Symbol,
Last Price and Last Quantity, and explicitly no side, no aggressor and
no participant. So a tape row establishes THAT a print occurred at a
price. It does not establish which side of the book it consumed, whose
order it was, or that any particular resting order was reached. Every
field this module emits is a fact about the FILE, never about BETTOR.

PRECISION IS MEASURED, NOT ASSUMED. "Price precision" here is the
observed number of decimal places across the file, reported as a
distribution rather than a single number, because a file whose prices
are mostly 2dp and occasionally 4dp is a different object from one
that is uniformly 2dp -- and only the second can be compared to a
quote at a cent.

CUMULATIVE VS INCREMENTAL is answered structurally where it can be:
a tape of individual prints has one row per execution and no running
total column. If a running-total column appears, that is recorded and
the question is answered NOT_IDENTIFIED rather than guessed.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import tape_capture as TC

NOT_IDENTIFIED = "NOT_IDENTIFIED"

PROBE_VERSION = "beta48-tape-probe/1"

# What a tape row is, and is not. Carried onto the output so a reader
# of the JSON cannot mistake the second for the first.
A_TAPE_ROW_IS = (
    "a print occurred: this symbol, this price, this quantity, at this "
    "transaction time")
A_TAPE_ROW_IS_NOT = (
    "not which side of the book it consumed, not whose order it was, "
    "not that any particular resting order was reached, and not that "
    "BETTOR filled. TRADE_SIDE and AGGRESSOR are absent from the "
    "documented schema and cannot be recovered from it")

_DEC = re.compile(r"^\s*-?\d+(?:\.(\d+))?\s*$")


def _decimals(s):
    """Decimal places in a numeric string, or None if it is not one."""
    m = _DEC.match(str(s or ""))
    if not m:
        return None
    return len(m.group(1)) if m.group(1) else 0


def _ts_precision(s):
    """The observed timestamp shape, named rather than parsed loosely."""
    t = str(s or "").strip()
    if not t:
        return "EMPTY"
    frac = re.search(r"[.,](\d+)", t)
    if frac:
        n = len(frac.group(1))
        return ("MILLISECOND" if n == 3 else "MICROSECOND" if n == 6
                else "NANOSECOND" if n == 9 else "SUBSECOND_%dDP" % n)
    if re.search(r"\d{2}:\d{2}:\d{2}", t):
        return "SECOND"
    if re.search(r"\d{2}:\d{2}", t):
        return "MINUTE"
    if re.search(r"\d{4}-?\d{2}-?\d{2}", t):
        return "DATE_ONLY"
    return "UNRECOGNISED"


def _col(cols, *names):
    """Index of the first column whose name matches, case-insensitively."""
    low = [c.strip().strip('"').lower() for c in cols]
    for n in names:
        if n.lower() in low:
            return low.index(n.lower())
    return None


def analyse(csv_text, *, url=None, fetched_at=None, sha256=None,
            sample_symbols=60) -> dict:
    """Every §B field, measured from the file itself."""
    out = {
        "probeVersion": PROBE_VERSION,
        "url": url or NOT_IDENTIFIED,
        "RETRIEVAL_TIMESTAMP": fetched_at or NOT_IDENTIFIED,
        "sha256": sha256 or NOT_IDENTIFIED,
        "aTapeRowIs": A_TAPE_ROW_IS,
        "aTapeRowIsNot": A_TAPE_ROW_IS_NOT,
    }
    if not csv_text:
        out.update({"PARSE_STATUS": "NO_CONTENT", "TAPE_ROWS": 0})
        return out

    # The file's own name is the business date; no arithmetic on it here.
    m = re.search(r"(\d{8})-time-and-sales\.csv", url or "")
    out["TAPE_FILE_DATE"] = m.group(1) if m else NOT_IDENTIFIED
    out["fileDateIsABusinessDate"] = (
        "the venue's reporting day ends 17:00 America/New_York, so this "
        "is NOT a UTC calendar day and must never be joined as one")

    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        out.update({"PARSE_STATUS": "EMPTY", "TAPE_ROWS": 0})
        return out

    header = [c.strip().strip('"') for c in rows[0]]
    body = rows[1:]
    out["SCHEMA_COLUMNS"] = header
    out["DOCUMENTED_COLUMNS"] = list(TC.DOCUMENTED_TAPE_COLUMNS)
    out["HEADER_MATCHES_DOCUMENTATION"] = (
        "YES" if header == list(TC.DOCUMENTED_TAPE_COLUMNS) else "NO")
    out["TAPE_ROWS"] = len(body)
    out["PARSE_STATUS"] = "PARSED"

    i_ts = _col(header, "Transaction Time", "transaction_time", "time")
    i_sym = _col(header, "Symbol", "symbol")
    i_px = _col(header, "Last Price", "last_price", "price")
    i_qty = _col(header, "Last Quantity", "last_quantity", "quantity",
                 "size")
    out["columnIndex"] = {"time": i_ts, "symbol": i_sym,
                          "price": i_px, "quantity": i_qty}
    missing_cols = [n for n, i in
                    (("time", i_ts), ("symbol", i_sym),
                     ("price", i_px), ("quantity", i_qty)) if i is None]
    out["COLUMNS_MISSING"] = missing_cols
    if missing_cols:
        out["PARSE_STATUS"] = "COLUMNS_MISSING"
        return out

    def cell(r, i):
        return r[i].strip() if i < len(r) else ""

    times = [cell(r, i_ts) for r in body]
    syms = [cell(r, i_sym) for r in body]
    pxs = [cell(r, i_px) for r in body]
    qtys = [cell(r, i_qty) for r in body]

    out["MISSING_VALUES"] = {
        "time": sum(1 for v in times if not v),
        "symbol": sum(1 for v in syms if not v),
        "price": sum(1 for v in pxs if not v),
        "quantity": sum(1 for v in qtys if not v),
        "shortRows": sum(1 for r in body if len(r) < len(header)),
    }

    nonempty = [t for t in times if t]
    out["EARLIEST_TRANSACTION_TIMESTAMP"] = (min(nonempty) if nonempty
                                             else NOT_IDENTIFIED)
    out["LATEST_TRANSACTION_TIMESTAMP"] = (max(nonempty) if nonempty
                                           else NOT_IDENTIFIED)
    out["TIMESTAMP_PRECISION"] = dict(
        Counter(_ts_precision(t) for t in times).most_common())
    out["timestampOrderingIsLexical"] = (
        "min/max are lexical over the raw strings. For an ISO-8601-like "
        "fixed-width field that is also chronological; for any other "
        "shape it is not, and the precision histogram above says which")

    uniq = sorted(set(s for s in syms if s))
    out["UNIQUE_SYMBOLS"] = len(uniq)
    out["symbolSample"] = uniq[:sample_symbols]
    out["symbolLengthHistogram"] = dict(
        Counter(len(s) for s in uniq).most_common(10))

    out["PRICE_PRECISION"] = dict(
        Counter(_decimals(v) for v in pxs).most_common())
    out["QUANTITY_PRECISION"] = dict(
        Counter(_decimals(v) for v in qtys).most_common())
    out["priceNonNumeric"] = sum(1 for v in pxs if _decimals(v) is None)
    out["quantityNonNumeric"] = sum(1 for v in qtys if _decimals(v) is None)

    # DUPLICATE BEHAVIOUR. Two different questions, kept apart: identical
    # whole rows, and several prints sharing time+symbol+price. The
    # second is ordinary market behaviour and is NOT a duplicate; reading
    # it as one would silently discard real volume.
    whole = Counter(tuple(r) for r in body)
    tsp = Counter((cell(r, i_ts), cell(r, i_sym), cell(r, i_px))
                  for r in body)
    out["DUPLICATE_WHOLE_ROWS"] = sum(n - 1 for n in whole.values() if n > 1)
    out["DISTINCT_WHOLE_ROWS"] = len(whole)
    out["MULTIPLE_PRINTS_SHARING_TIME_SYMBOL_PRICE"] = (
        "YES" if any(n > 1 for n in tsp.values()) else "NO")
    out["maxPrintsAtOneTimeSymbolPrice"] = max(tsp.values()) if tsp else 0
    out["whyThatIsNotADuplicate"] = (
        "several executions can print at the same instant, symbol and "
        "price. Collapsing them as duplicates would discard real volume, "
        "so the two counts are reported separately and neither is "
        "de-duplicated here")

    # CUMULATIVE OR INCREMENTAL. Answered structurally: a per-print tape
    # has no running-total column. If quantity were cumulative within a
    # symbol it would be non-decreasing per symbol, which is testable.
    running = [c for c in header
               if re.search(r"cumulative|running|total|volume", c, re.I)]
    out["runningTotalColumns"] = running
    per_symbol_monotone = 0
    per_symbol_checked = 0
    by_sym = {}
    for s, q in zip(syms, qtys):
        by_sym.setdefault(s, []).append(_decimals(q) is not None and float(q)
                                        or None)
    for s, seq in by_sym.items():
        seq = [v for v in seq if isinstance(v, float)]
        if len(seq) < 3:
            continue
        per_symbol_checked += 1
        if all(b >= a for a, b in zip(seq, seq[1:])):
            per_symbol_monotone += 1
    out["symbolsWithQuantityNonDecreasing"] = per_symbol_monotone
    out["symbolsCheckedForMonotonicity"] = per_symbol_checked
    out["TAPE_CUMULATIVE_OR_INCREMENTAL"] = (
        "INCREMENTAL_PER_PRINT" if (not running and per_symbol_checked
                                    and per_symbol_monotone
                                    < per_symbol_checked)
        else "CUMULATIVE" if running
        else NOT_IDENTIFIED)
    out["whyCumulativeMatters"] = (
        "an incremental tape's quantities sum to volume; a cumulative "
        "one's do not, and summing it would overstate traded volume by "
        "orders of magnitude. CUMULATIVE_VOLUME is in any case NOT "
        "queue evolution")

    return out


def probe(outdir: Path, max_files: int = 1) -> dict:
    """Fetch, then measure. Returns the §B block."""
    summary = TC.capture(outdir, max_files=max_files)
    raw = outdir / "tape_raw.jsonl"
    csv_rows = []
    if raw.exists():
        for line in raw.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "CSV":
                csv_rows.append(r)

    ok = [r for r in csv_rows
          if r.get("http_status") == 200 and r.get("text")]
    block = {
        "probeVersion": PROBE_VERSION,
        "probedAtUtc": datetime.now(tz=timezone.utc).isoformat(),
        "TAPE_FETCH_STATUS": "PROVEN" if ok else "FAILED",
        "landingPages": summary.get("landing_pages"),
        "csvLinksFound": len(summary.get("csv_links_found") or []),
        "csvLinkSample": (summary.get("csv_links_found") or [])[:10],
        "PUBLIC_TIME_SALES_AVAILABLE":
            summary.get("PUBLIC_TIME_SALES_AVAILABLE"),
        "BLOCK_TRADE_DATA_PUBLIC": summary.get("BLOCK_TRADE_DATA_PUBLIC"),
        "BLOCK_TRADE_PAGE_HTTP_STATUS":
            summary.get("BLOCK_TRADE_PAGE_HTTP_STATUS"),
        "TAPE_VOLUME_IS_UPPER_BOUND_ON_CLOB_VOLUME":
            summary.get("TAPE_VOLUME_IS_UPPER_BOUND_ON_CLOB_VOLUME"),
        "attemptedFiles": [
            {"url": r.get("url"), "http_status": r.get("http_status"),
             "bytes": r.get("bytes"), "error": r.get("error")}
            for r in csv_rows],
    }
    if not ok:
        block["whyFailed"] = (
            "no CSV was fetched with HTTP 200 and a body. The landing "
            "statuses and any errors are above; an absent link and a "
            "failed fetch look identical afterwards and only one of them "
            "is honest, so both are recorded")
        (outdir / "tape_probe.json").write_text(json.dumps(block, indent=1))
        return block

    first = ok[0]
    block["file"] = analyse(first.get("text"), url=first.get("url"),
                            fetched_at=first.get("requested_at_utc"),
                            sha256=first.get("sha256"))
    # PUBLICATION LAG, OBSERVED. The gap between the file's business date
    # and the moment we successfully retrieved it. Not a promise about
    # the venue's schedule -- one observation of it.
    fd = block["file"].get("TAPE_FILE_DATE")
    if fd and fd != NOT_IDENTIFIED:
        try:
            d = datetime.strptime(fd, "%Y%m%d").replace(tzinfo=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            block["TAPE_PUBLICATION_LAG_OBSERVED_HOURS"] = round(
                (now - d).total_seconds() / 3600.0, 2)
            block["publicationLagCaveat"] = (
                "measured from the file's BUSINESS DATE at 00:00 UTC to "
                "our retrieval. The business day ends 17:00 ET, so this "
                "overstates the true publish-to-retrieve delay by up to "
                "21 hours and is an upper bound on it, not the venue's "
                "schedule")
        except ValueError:
            block["TAPE_PUBLICATION_LAG_OBSERVED_HOURS"] = NOT_IDENTIFIED
    (outdir / "tape_probe.json").write_text(json.dumps(block, indent=1))
    return block


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-files", type=int, default=1)
    a = ap.parse_args(argv)
    b = probe(Path(a.out), a.max_files)
    print(json.dumps(b, indent=1)[:200000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
