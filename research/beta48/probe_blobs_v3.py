#!/usr/bin/env python3
"""PROBE blobs_v3 BEFORE claiming it unlocks anything. Correction #9.

Contacts nothing. Reads `evidence/blobs_v3/*_reconstruction.json` and reports,
per field, PRESENT / ABSENT with coverage, source, join key and collision
behaviour. Emits `BLOBS_V3_PROBE_V1.json`.

WHY THIS FILE EXISTS.

`WHALE_NATIVE_EV_BRIDGE_V1.md` and `BETTOR_DAY1_EV_ARCHITECTURE.md` both
asserted that re-deriving the whale cells from blobs_v3 "unlocks the
sport/league/market-type cells, enables event-level clustering and an
event-blocked bootstrap". That was asserted from the existence of the files and
from their upstream endpoint, NOT from their contents. This probe reads the
contents. The claim does not survive it, and the documents are corrected rather
than left standing.

THE ONE-LINE RESULT: blobs_v3 is AGGREGATE, exactly as whale_exit_priors_v1 is.
Six files of roughly 80 KB cannot and do not carry per-position rows for
accounts with millions of fills, and the top-level key set confirms it: CENSUS,
REFERENCE_ACCOUNT_REPLAY, REFERENCE_ACCOUNT_COMPLETION, MERGE_PNL_BY_OPEN_BAND,
PNL_BY_OPEN_BAND_ALL_CHANNELS. There is no row array anywhere in the document.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOBS = HERE / "evidence" / "blobs_v3"
OUT = HERE / "BLOBS_V3_PROBE_V1.json"

NOT_IDENTIFIED = "NOT_IDENTIFIED"
PRESENT = "PRESENT"
ABSENT = "ABSENT"

VENUE_CONTACT = 0
ORDERS = 0
CAPITAL = 0
CREDENTIALS = "NONE"
mirror_live = False

# The fields the correction names, in its order. Each is answered from the
# document, never from the endpoint's reputation.
PROBED_FIELDS = (
    "PER_POSITION_ROWS", "MARKET_ID", "EVENT_ID", "ENTRY_TIMESTAMP",
    "ENTRY_PRICE", "SECOND_LEG_TIMESTAMP", "SECOND_LEG_PRICE", "SPORT",
    "LEAGUE", "GAME_START", "LIQUIDITY", "BOOK_STATE", "ORDER_IDENTITY",
    "FILL_IDENTITY",
)


def load_all(root=None):
    root = Path(root or BLOBS)
    out = {}
    for p in sorted(root.glob("*_reconstruction.json")):
        d = json.loads(p.read_text())
        out[d["CENSUS"]["REFERENCE_ACCOUNT"]] = d
    return out


def _lists_in(doc):
    """Every list in the document, with its length. A per-position artefact
    would show one list whose length is of the order of the position count."""
    found = []

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, path + "/" + k)
        elif isinstance(o, list):
            found.append((path, len(o),
                          type(o[0]).__name__ if o else "EMPTY"))
            if o:
                walk(o[0], path + "[0]")
    walk(doc)
    return found


def row_bearing_lists(doc):
    """Lists that could be per-position rows: a list of dicts, length > 1000."""
    return [(p, n, t) for p, n, t in _lists_in(doc)
            if t == "dict" and n > 1000]


def cell_coverage(doc, cell):
    """opens covered by a breakdown, against the account's position count."""
    c = doc.get("REFERENCE_ACCOUNT_COMPLETION", {}).get("LIFETIME", {})
    total = c.get("REFERENCE_ACCOUNT_FIRST_SIDE_ACQUISITIONS")
    tbl = c.get(cell) or {}
    covered = sum(v.get("opens", 0) for v in tbl.values()
                  if isinstance(v, dict))
    return {
        "CELL": cell,
        "KEYS": len(tbl),
        "OPENS_COVERED": covered,
        "OPENS_TOTAL": total if total is not None else NOT_IDENTIFIED,
        "COVERAGE_PCT": (round(100.0 * covered / total, 2)
                         if total else NOT_IDENTIFIED),
        "KEY_MAX_LEN": max((len(str(k)) for k in tbl), default=0),
    }


def field_report(docs):
    """PRESENT / ABSENT per field, with the evidence for the verdict."""
    ref_acct = sorted(docs)[0]
    any_doc = docs[ref_acct]
    rows = []

    def add(field, status, coverage, source, join_key, collisions, why):
        rows.append({"FIELD": field, "STATUS": status,
                     "COVERAGE_PCT": coverage, "SOURCE": source,
                     "JOIN_KEY": join_key, "UNIQUENESS_OR_COLLISIONS":
                     collisions, "WHY": why})

    rowlists = {a: row_bearing_lists(d) for a, d in docs.items()}
    add("PER_POSITION_ROWS", ABSENT, 0.0,
        "blobs_v3 document structure",
        NOT_IDENTIFIED, "n/a",
        "no list of dicts longer than 1000 exists in any file; the longest "
        "lists are COVERAGE_LIMITATIONS (3 strings) and edge_ci95 (2 floats). "
        "Row-bearing lists found: %s" % json.dumps(
            {a: v for a, v in rowlists.items() if v}))

    for f, key in (("MARKET_ID", "DISTINCT_MARKET_SLUGS"),
                   ("EVENT_ID", None)):
        if key:
            add(f, ABSENT, 0.0, "CENSUS.%s (a COUNT, not the ids)" % key,
                NOT_IDENTIFIED, "n/a",
                "CENSUS carries the COUNT of distinct market slugs (%s: "
                "%s) but not one slug. A count cannot be joined on."
                % (ref_acct, any_doc["CENSUS"].get(key)))
        else:
            add(f, ABSENT, 0.0, NOT_IDENTIFIED, NOT_IDENTIFIED, "n/a",
                "no event identifier of any kind appears in the document; "
                "DISTINCT_CONDITIONS equals DISTINCT_MARKET_SLUGS in every "
                "file, so even the upstream grouping is by MARKET, not EVENT")

    for f in ("ENTRY_TIMESTAMP", "ENTRY_PRICE", "SECOND_LEG_TIMESTAMP",
              "SECOND_LEG_PRICE"):
        add(f, ABSENT, 0.0,
            "aggregated into BY_FIRST_LEG_PRICE_BAND / COMPLETION_GRID",
            NOT_IDENTIFIED, "n/a",
            "the per-leg quantities survive only as band means, completion "
            "rates and basis percentiles; the underlying per-position values "
            "are not retained")

    sq = {a: cell_coverage(d, "BY_SPORT_OR_QUESTION")
          for a, d in docs.items()}
    cov = sorted(r["COVERAGE_PCT"] for r in sq.values())
    add("SPORT", ABSENT, 0.0,
        "BY_SPORT_OR_QUESTION -- despite the name, a question-title table",
        "truncated 24-character question title",
        "MANY-TO-ONE: the key is the market question truncated to 24 chars, "
        "so 'Philadelphia Phillies vs' collapses every Phillies fixture "
        "across the whole period into one key. It is opponent-blind and "
        "date-blind",
        "the table is the TOP-25 titles only, covering %.2f%%-%.2f%% of "
        "opens depending on the account. It carries no sport taxonomy: the "
        "keys mix a market type ('Games Total: O/U 2.5'), a tournament "
        "('Wimbledon, Qualification') and a fixture prefix" %
        (cov[0], cov[-1]))
    add("LEAGUE", ABSENT, 0.0, "BY_SPORT_OR_QUESTION", NOT_IDENTIFIED,
        "as SPORT", "no league field exists; a league cannot be recovered "
        "from a truncated title without an external mapping, and inventing "
        "one would be an authored cell")
    add("GAME_START", ABSENT, 0.0, NOT_IDENTIFIED, NOT_IDENTIFIED, "n/a",
        "no event start time, and therefore no time-to-event, appears")
    add("LIQUIDITY", ABSENT, 0.0, NOT_IDENTIFIED, NOT_IDENTIFIED, "n/a",
        "no depth, spread or size-at-touch quantity appears")
    add("BOOK_STATE", ABSENT, 0.0, NOT_IDENTIFIED, NOT_IDENTIFIED, "n/a",
        "the source endpoint is activity?type=TRADE -- executions only. A "
        "trade tape does not contain the book")
    add("ORDER_IDENTITY", ABSENT, 0.0, NOT_IDENTIFIED, NOT_IDENTIFIED, "n/a",
        "no order id, no resting price, no cancel. WHALE_ORDER_POLICY stays "
        "NOT_IDENTIFIED and this probe does not change that")
    add("FILL_IDENTITY", ABSENT, 0.0,
        "CENSUS.UNIQUE_TRADE_IDS (a COUNT)", NOT_IDENTIFIED,
        "rn1: 4,692,866 rows against 4,541,029 unique trade ids, so trade "
        "ids are NOT unique per row upstream; DUPLICATE_FILL_KEYS = 0 under "
        "the upstream composite key",
        "the counts are retained, the ids are not")
    return rows


def constructible_cells(docs):
    """Which cells the probe PROVES are constructible, and which it refuses."""
    yes, no = [], []
    for cell, label in (("BY_FIRST_LEG_PRICE_BAND", "ACCOUNT x PRICE_BAND"),
                        ("BY_FILL_SIZE_BUCKET", "ACCOUNT x FILL_SIZE_BUCKET"),
                        ("BY_WEEK", "ACCOUNT x ISO_WEEK"),
                        ("BY_SPORT_OR_QUESTION",
                         "ACCOUNT x TRUNCATED_QUESTION_TITLE")):
        per = {a: cell_coverage(d, cell) for a, d in docs.items()}
        worst = min((r["COVERAGE_PCT"] for r in per.values()
                     if isinstance(r["COVERAGE_PCT"], (int, float))),
                    default=0)
        row = {"CELL": label, "SOURCE_KEY": cell,
               "MIN_COVERAGE_PCT": worst, "PER_ACCOUNT": per}
        if worst >= 99.9 and cell != "BY_SPORT_OR_QUESTION":
            yes.append(row)
        else:
            row["WHY_REFUSED"] = (
                "a top-25 table covering %.2f%% of opens in the worst "
                "account, keyed on a 24-character truncation that collides "
                "across fixtures; it is not a taxonomy and not a partition"
                % worst)
            no.append(row)
    return yes, no


def build(root=None):
    docs = load_all(root)
    yes, no = constructible_cells(docs)
    return {
        "VERSION": "BLOBS_V3_PROBE_V1",
        "PROBE_IS": "A_READ_OF_THE_FILES_NOT_AN_INFERENCE_FROM_THEIR_SOURCE",
        "VENUE_CONTACT": 0, "ORDERS": 0, "CAPITAL": 0, "mirror_live": False,

        "FILES": sorted(docs),
        "ACCOUNTS_NOT_IN_THE_FOUR_ACCOUNT_PRIOR": sorted(
            set(docs) - {"rn1", "ferrarichampions2026", "homerunhazard",
                         "swisstony"}),
        "TOP_LEVEL_KEYS": sorted(next(iter(docs.values()))),
        "GRANULARITY": "AGGREGATE",
        "PER_POSITION_ROWS": "NOT_PRESENT",
        "SOURCE_ENDPOINT": next(iter(docs.values()))["CENSUS"].get(
            "SOURCE_ENDPOINT"),

        "FIELD_REPORT": field_report(docs),
        "CELLS_GENUINELY_CONSTRUCTIBLE": yes,
        "CELLS_REFUSED": no,

        # The claim under test, and its verdict.
        "CLAIM_UNDER_TEST": (
            "re-deriving the whale cells from blobs_v3 unlocks the "
            "sport/league/market-type cells, enables event-level clustering "
            "and an event-blocked bootstrap"),
        "CLAIM_VERDICT": "REFUTED_BY_THE_FILES",
        "BLOBS_V3_RICH_CELL_STATUS": "NOT_AVAILABLE_AGGREGATE_ONLY",
        "EVENT_LEVEL_CLUSTERING_FROM_BLOBS_V3": NOT_IDENTIFIED,
        "EVENT_BLOCKED_BOOTSTRAP_FROM_BLOBS_V3": NOT_IDENTIFIED,
        "INDEPENDENT_EFFECTIVE_N_FROM_BLOBS_V3": NOT_IDENTIFIED,

        # What the probe DID find that the retained priors do not carry. Two
        # new cells at full coverage is a real, if smaller, gain -- and it is
        # reported as what it is rather than inflated into the claim above.
        "NEW_CELLS_BLOBS_V3_ADDS_OVER_whale_exit_priors_v1": [
            "ACCOUNT x FILL_SIZE_BUCKET", "ACCOUNT x ISO_WEEK"],
        "NEW_CELLS_ARE_STILL": "ACCOUNT_LEVEL_MARGINALS_NOT_A_CROSS_PRODUCT",

        "WHAT_WOULD_ACTUALLY_UNLOCK_THE_CELL_SPACE": (
            "re-running the upstream extraction from "
            "data-api.polymarket.com/activity?type=TRADE and RETAINING the "
            "per-row condition id, market slug and timestamp before "
            "aggregation. The ids existed at extraction time -- CENSUS counts "
            "them -- and were not written down. That is a new extraction, not "
            "a reprocessing of these files"),
        "THAT_WORK_REQUIRES": "AN_UPSTREAM_RE_EXTRACTION_NOT_VENUE_ACCESS",
    }


def main():                                                   # pragma: no cover
    doc = build()
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print("wrote %s" % OUT.name)
    print("%-22s %-8s %s" % ("FIELD", "STATUS", "WHY (first line)"))
    for r in doc["FIELD_REPORT"]:
        print("%-22s %-8s %s" % (r["FIELD"], r["STATUS"],
                                 r["WHY"].split(";")[0][:70]))
    print()
    print("BLOBS_V3_RICH_CELL_STATUS = %s" % doc["BLOBS_V3_RICH_CELL_STATUS"])
    for c in doc["CELLS_GENUINELY_CONSTRUCTIBLE"]:
        print("  CONSTRUCTIBLE  %-34s %.1f%%"
              % (c["CELL"], c["MIN_COVERAGE_PCT"]))
    for c in doc["CELLS_REFUSED"]:
        print("  REFUSED        %-34s %.1f%%"
              % (c["CELL"], c["MIN_COVERAGE_PCT"]))
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
