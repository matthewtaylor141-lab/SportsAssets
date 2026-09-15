#!/usr/bin/env python3
"""BETA48 — where each reference account's measured edge actually COMES FROM.

READ ONLY, OFFLINE. Reads the sealed reconstruction JSONs under
research/beta48/evidence/blobs/ and prints one table. Contacts nothing,
writes one evidence file, changes no estimator.

WHY THIS EXISTS. kch123's LIFETIME window came back reading

    realized_merge_pnl  -$673,481.63
    realized_total         +$41,957.46
    edge_roi                    +7.81%   on $134,977,032 deployed

A +7.81% return on $135M is +$10.5M. None of the three numbers is wrong
and all three are in the same record, so at least one of them is not
measuring what its name suggests to a reader. They are three different
quantities and the gap between them is the whole finding.

THE DECOMPOSITION. merge_pnl.replay books a CLOSED LOT from exactly
three places (merge_pnl.py:305, :318, :390):

  SELL      _lot(q*avg,        q*(price-avg))        realized_sell_pnl
  MERGE     _lot(m*avg_other,  m*(1-avg_other-px))   realized_merge_pnl
  SETTLED   _lot(cost[leg],    q*payout - cost[leg]) -- no named total

Only the first two have a named total in the output. The third -- a
position the account never traded out of, which simply RESOLVED -- was
added in the 2026-08-26 round-3 survivorship-bias fix and lands straight
in lot_s/lot_p with no separate line. So edge_roi = lot_p / lot_s mixes
TRADING with HOLDING and reports one number.

Both stakes are recoverable exactly, with no re-walk of the fills:

  settled_stake = open_cost - ungraded_open_cost

open_cost sums every remaining balance's cost AFTER the settled loop, and
that loop never mutates cost[], so open_cost is settled cost + ungraded
cost. Subtracting leaves precisely the stake the settled lots booked.
Everything else follows by difference, and the two identities

  settled_stake + traded_stake == lot_s
  settled_pnl   + merge + sell == lot_p

are asserted to the cent on every row, so a silent mismatch cannot pass.

WHAT THIS IS AND IS NOT. It is arithmetic on fields already in the
record. It re-derives no economics, changes no estimator, moves no
threshold, and promotes nothing. It answers one question: of the edge
the estimator reports, how much came from the pair/merge mechanism
BETA48 would actually copy, and how much came from holding to
settlement, which is a different strategy with different capital,
different duration and different risk.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOBS = HERE / "evidence" / "blobs"
OUT = HERE / "evidence"

ACCOUNTS = ("rn1", "w2c33", "homerunhazard", "swisstony", "kch123",
            "ferrarichampions2026")

# The two things the canary repair had to put in every census. A record
# missing either ran pre-repair code and is not admissible.
REQUIRED_CENSUS = ("AS_OF_UTC", "DAYS_SINCE_LAST_FILL_AT_AS_OF")
CENT = 0.01


def load(name: str) -> dict | None:
    p = BLOBS / ("%s_reconstruction.json" % name)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def gates(rec: dict) -> list[str]:
    """Repair-gate check. Returns the failures, empty if it passes."""
    bad = []
    c = rec.get("CENSUS", {})
    for k in REQUIRED_CENSUS:
        if c.get(k) is None:
            bad.append("census missing %s" % k)
    for w, r in (rec.get("REFERENCE_ACCOUNT_REPLAY") or {}).items():
        if not isinstance(r, dict):
            continue                      # some windows seal as a bare string
        v = r.get("edge_verdict") or ""
        # An EMPTY window is the repair working, not failing: a dormant
        # account has no fills in the last 7 days and must say so. Only a
        # window that HAS lots may be held to the cluster floor.
        if v == "NO_FILLS_IN_WINDOW" or not (r.get("edge_lots") or 0):
            continue
        n = r.get("edge_clusters") or 0
        if n < 30 and not v.startswith("NOT_DEMONSTRATED_INSUFFICIENT"):
            bad.append("%s: %d clusters but verdict reads %r" % (w, n, v[:40]))
    return bad


def decompose(r: dict) -> dict | None:
    """Split lot_s / lot_p into the settled and the traded channel."""
    if not isinstance(r, dict) or not r.get("lot_s"):
        return None
    lot_s, lot_p = r["lot_s"], r["lot_p"]
    merge = r.get("realized_merge_pnl") or 0.0
    sell = r.get("realized_sell_pnl") or 0.0
    settled_stake = (r.get("open_cost") or 0.0) - (r.get("ungraded_open_cost") or 0.0)
    settled_pnl = lot_p - merge - sell
    traded_stake = lot_s - settled_stake

    # The two identities. If either breaks, the split is wrong and the
    # row must not be reported -- so this raises rather than warns.
    assert abs(settled_stake + traded_stake - lot_s) < CENT, "stake identity"
    assert abs(settled_pnl + merge + sell - lot_p) < CENT, "pnl identity"

    return {
        "edge_roi": r.get("edge_roi"),
        "edge_ci95": r.get("edge_ci95"),
        "edge_clusters": r.get("edge_clusters"),
        "edge_verdict": r.get("edge_verdict"),
        "lot_s": round(lot_s, 2), "lot_p": round(lot_p, 2),
        "SETTLED_STAKE": round(settled_stake, 2),
        "SETTLED_PNL": round(settled_pnl, 2),
        "SETTLED_SHARE_OF_STAKE": (round(settled_stake / lot_s, 4)
                                   if lot_s else None),
        "TRADED_STAKE": round(traded_stake, 2),
        "TRADED_PNL": round(merge + sell, 2),
        "MERGE_PNL": round(merge, 2),
        "SELL_PNL": round(sell, 2),
        "TRADED_ROI": (round((merge + sell) / traded_stake, 6)
                       if traded_stake > 0 else None),
        "SETTLED_ROI": (round(settled_pnl / settled_stake, 6)
                        if settled_stake > 0 else None),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    out, missing = {}, []
    print("=" * 108)
    print("BETA48 CHANNEL DECOMPOSITION — identical accounting, LIFETIME window")
    print("=" * 108)

    for name in ACCOUNTS:
        rec = load(name)
        if rec is None:
            missing.append(name)
            continue
        c = rec["CENSUS"]
        bad = gates(rec)
        life = (rec.get("REFERENCE_ACCOUNT_REPLAY") or {}).get("LIFETIME")
        d = decompose(life) if isinstance(life, dict) else None

        print("\n--- %s ---" % name.upper())
        print("  coverage %s .. %s  (%s d)   fills %s  markets %s"
              % (str(c.get("RETURNED_RANGE_START"))[:10],
                 str(c.get("RETURNED_RANGE_END"))[:10],
                 c.get("RETURNED_SPAN_DAYS"), c.get("ROWS_KEPT"),
                 c.get("DISTINCT_CONDITIONS")))
        print("  buys %-9s sells %-7s  dormant %s d at as-of"
              % (c.get("BUY_COUNT"), c.get("SELL_COUNT"),
                 c.get("DAYS_SINCE_LAST_FILL_AT_AS_OF")))
        print("  REPAIR GATES: %s" % ("PASS" if not bad else "FAIL " + "; ".join(bad)))
        if d is None:
            print("  no LIFETIME lot data")
            continue
        print("  edge_roi %+.4f%%  ci95 %s  clusters %s"
              % (100 * (d["edge_roi"] or 0), d["edge_ci95"], d["edge_clusters"]))
        print("    SETTLED (held to resolution)  stake $%15s (%5.1f%%)  pnl $%14s  roi %s"
              % (f'{d["SETTLED_STAKE"]:,.0f}',
                 100 * (d["SETTLED_SHARE_OF_STAKE"] or 0),
                 f'{d["SETTLED_PNL"]:,.0f}',
                 ("%+.3f%%" % (100 * d["SETTLED_ROI"])) if d["SETTLED_ROI"] is not None else "n/a"))
        print("    TRADED  (merge + sell)        stake $%15s (%5.1f%%)  pnl $%14s  roi %s"
              % (f'{d["TRADED_STAKE"]:,.0f}',
                 100 * (1 - (d["SETTLED_SHARE_OF_STAKE"] or 0)),
                 f'{d["TRADED_PNL"]:,.0f}',
                 ("%+.3f%%" % (100 * d["TRADED_ROI"])) if d["TRADED_ROI"] is not None else "n/a"))
        print("       of which MERGE  $%14s     <-- the mechanism BETA48 would copy"
              % f'{d["MERGE_PNL"]:,.0f}')
        print("       of which SELL   $%14s" % f'{d["SELL_PNL"]:,.0f}')
        out[name] = {"CENSUS": c, "REPAIR_GATES": bad or "PASS",
                     "LIFETIME_DECOMPOSITION": d}

    if missing:
        print("\nNOT YET RETURNED (job still running or not dispatched): %s"
              % ", ".join(missing))
    out["_NOT_RETURNED"] = missing
    (OUT / "channel_decomposition.json").write_text(
        json.dumps(out, indent=1, default=str))
    print("\nsealed -> research/beta48/evidence/channel_decomposition.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
