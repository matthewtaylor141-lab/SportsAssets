#!/usr/bin/env python3
"""The §B answer block, compact.

The full probe JSON carries a 60-symbol sample and per-level detail and
runs to tens of thousands of lines in a workflow log, which buries the
dozen fields the directive actually asked for. This prints those, plus
a short symbol sample for the join test, and nothing else. The full
object is still uploaded as an artifact.
"""
from __future__ import annotations

import json
import sys

FIELDS = (
    "TAPE_FETCH_STATUS", "CSV_LINK_DISCOVERY", "csvLinksFound",
    "PUBLIC_TIME_SALES_AVAILABLE", "BLOCK_TRADE_DATA_PUBLIC",
    "BLOCK_TRADE_PAGE_HTTP_STATUS",
    "TAPE_VOLUME_IS_UPPER_BOUND_ON_CLOB_VOLUME",
    "TAPE_FILE_DATE_RANGE", "TAPE_PUBLICATION_LAG_OBSERVED_HOURS",
)
FILE_FIELDS = (
    "url", "TAPE_FILE_DATE", "RETRIEVAL_TIMESTAMP", "sha256",
    "PARSE_STATUS", "TAPE_ROWS", "SCHEMA_COLUMNS",
    "HEADER_MATCHES_DOCUMENTATION",
    "EARLIEST_TRANSACTION_TIMESTAMP", "LATEST_TRANSACTION_TIMESTAMP",
    "TIMESTAMP_PRECISION", "UNIQUE_SYMBOLS", "symbolLengthHistogram",
    "PRICE_PRECISION", "QUANTITY_PRECISION", "priceNonNumeric",
    "quantityNonNumeric", "MISSING_VALUES",
    "DUPLICATE_WHOLE_ROWS", "DISTINCT_WHOLE_ROWS",
    "MULTIPLE_PRINTS_SHARING_TIME_SYMBOL_PRICE",
    "maxPrintsAtOneTimeSymbolPrice", "runningTotalColumns",
    "symbolsWithQuantityNonDecreasing", "symbolsCheckedForMonotonicity",
    "TAPE_CUMULATIVE_OR_INCREMENTAL",
)


def main(argv=None):
    path = (argv or sys.argv[1:])[0]
    d = json.load(open(path))
    out = {k: d.get(k) for k in FIELDS if k in d}
    out["perFileRows"] = d.get("perFileRows")
    f = d.get("file") or {}
    out["file"] = {k: f.get(k) for k in FILE_FIELDS if k in f}
    # A short sample for the deterministic-join test against
    # us_premap.market_slug. Capped: the file carries ~97k symbols.
    out["file"]["symbolSample"] = (f.get("symbolSample") or [])[:12]
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
