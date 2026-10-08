#!/usr/bin/env python3
"""ECONOMIC FUNNEL: THE CODE -> (CHAIN STAGE, CLASS) TABLE CARRIED INTO
ef_forward_funnel.sql AND ef_funnel_all.sql, GENERATED -- NEVER HAND-WRITTEN.

The stage and the SOFTWARE / ECONOMIC / EXTERNAL / UNCLASSIFIED class of a
refusal code live in the production Python (refusal_taxonomy_table,
bettor_external_shadow.EVALUABILITY_OF, coverage_first_loss.classify /
chain_stage). This runs THE RC4 PRODUCTION CODE (9b94ef5c, tree-identical to
release 7fd4574e) over the codes a research-sql run returned and prints the
SQL VALUES rows (code, rank, stage, class). A code neither table knows comes
back UNCLASSIFIED -- by name, never guessed.

Decisions are classified with mapped=True (a decision exists only for a
valuation that already named a venue contract), as the census does for
paper-decision records.

Usage (offline; nothing here reads a database):
  mkdir /tmp/rc4 && git archive 9b94ef5c backend/sportsassets | tar -x -C /tmp/rc4
  python3 research/ef_classify_codes.py /tmp/rc4/backend CODE [CODE ...]
"""
from __future__ import annotations

import sys

RANK = {"PROVIDER": 1, "NORMALIZED": 2, "MAPPED": 3, "SETTLEMENT": 4,
        "MODEL": 5, "FAIR_VALUE": 6, "BOOK": 7, "EV": 8, "ENTER_PASS": 9}


def main(backend: str, codes: list) -> None:
    sys.path.insert(0, backend)
    from sportsassets import coverage_first_loss as FL  # noqa: E402
    rows = []
    for c in sorted(set(codes)):
        k = FL.classify(c)
        st = FL.chain_stage(c, mapped=True)
        rows.append("        ('%s', %d, '%s', '%s')" % (c, RANK[st], st,
                                                         k["class"]))
    print(",\n".join(rows))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
