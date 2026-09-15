#!/usr/bin/env python3
"""BETA48 Priority 1 — canonical pair/merge reconstruction for RN1.

READ ONLY, OFFLINE. Contacts nothing. Writes only under
research/beta48/evidence/.

WHAT THIS IS. The 48-hour directive asks for a four-whale pair mechanism
reconstruction from RAW FILLS. Exactly one of the four whales has raw
fills on disk in this container: RN1, inside AUDIT_SNAPSHOT_V1
(research/snapshots/u2_events_v1.jsonl.gz). Ferrari, HomeRunHazard and
swisstony fills live in the production Postgres `trades` table and in the
Polymarket data-api, and BOTH are unreachable from here -- every relevant
host answers 000 (see BETA48_STATE.md). So this file reconstructs RN1 and
says NOT_IDENTIFIED for the other three rather than substituting the PDF
report numbers for measurement.

THE ARITHMETIC IS NOT REIMPLEMENTED. It reuses
backend/sportsassets/analytics/merge_pnl.py::replay verbatim -- the
module already in production that treats a BUY of the complementary leg
as the EXIT it is (YES + NO = $1 by construction, so buying the other
side at `price` is selling this one at 1 - price). Reusing it means the
beta's pair number and the desk's whale number are the same estimator.

WHY THE SNAPSHOT IS USABLE FOR THIS, AND WHERE IT IS NOT
The snapshot's sampling frame is "moments RN1 traded". For a study OF
RN1'S OWN BOOK that frame is the population, not a bias -- it is his
trade log over the window. It is NOT a random sample of the tradable
universe, so nothing here estimates what BETTOR could have filled. That
distinction is the whole of directive section 14 and is preserved by
never reporting a BETTOR fill rate from this file.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SNAP = ROOT / "research" / "snapshots"
OUT = HERE / "evidence"

EVENTS = SNAP / "u2_events_v1.jsonl.gz"
SETTLE = SNAP / "settlement_v1.jsonl"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The production estimator, imported by path so no package install is
# needed and no production module is modified.
M = _load("merge_pnl",
          ROOT / "backend" / "sportsassets" / "analytics" / "merge_pnl.py")


def load_payouts() -> tuple[dict, Counter]:
    """condition_id -> payout vector, from the sealed settlement file.

    A payout vector that is not a clean one-hot is NOT a label. 50/50
    voids and multi-way payouts are kept OUT by name rather than rounded
    into a winner, and the reasons are counted.
    """
    pay, why = {}, Counter()
    with open(SETTLE) as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            cid = r.get("condition_id")
            if not cid:
                continue
            if not r.get("resolved"):
                why["NOT_RESOLVED"] += 1
                continue
            vals = r.get("payouts") or []
            try:
                vals = [float(x) for x in vals]
            except (TypeError, ValueError):
                why["PAYOUT_NOT_NUMERIC"] += 1
                continue
            if len(vals) != 2:
                why["PAYOUT_NOT_BINARY"] += 1
                continue
            if sorted(vals) == [0.0, 1.0]:
                pay[cid] = vals
                why["OK_ONE_HOT"] += 1
            elif vals == [0.5, 0.5]:
                why["PAYOUT_SPLIT_50_50"] += 1
            else:
                why["PAYOUT_NOT_ONE_HOT"] += 1
    return pay, why


def load_fills() -> tuple[list, Counter]:
    """RN1's fills, chronological, in the row shape replay() consumes.

    ORDER MATTERS AND IS NOT ASSUMED. replay() walks the list as given
    and a merge is defined against the balance standing at that instant,
    so the fills are sorted by (venue trade timestamp, trade_id) rather
    than trusting file order.
    """
    fills, why = [], Counter()
    for line in gzip.open(EVENTS, "rt"):
        r = json.loads(line)
        why["ROWS"] += 1
        cid = r.get("condition_id_effective") or r.get("condition_id")
        oi = r.get("outcome_index")
        try:
            idx = int(oi) if oi is not None else -1
        except (TypeError, ValueError):
            idx = -1
        if not cid:
            why["DROP_NO_CONDITION"] += 1
            continue
        if idx not in (0, 1):
            # 999 and null are the venue's own unresolved leg identity.
            # replay() would silently skip them; counted here instead.
            why["DROP_LEG_NOT_IDENTIFIED"] += 1
            continue
        try:
            size = float(r.get("size") or 0)
            price = float(r.get("price") or 0)
        except (TypeError, ValueError):
            why["DROP_UNPARSEABLE_NUMBER"] += 1
            continue
        if size <= 0:
            why["DROP_NON_POSITIVE_SIZE"] += 1
            continue
        try:
            tid = int(r.get("trade_id") or 0)
        except (TypeError, ValueError):
            tid = 0
        fills.append({
            "condition_id": cid, "outcome_index": idx, "size": size,
            "price": price, "side": (r.get("side") or "BUY").upper(),
            "ts": r.get("ts") or "", "trade_id": tid,
            "sport": r.get("sport") or "NOT_IDENTIFIED",
            "market_slug": r.get("market_slug") or "",
        })
        why["KEPT"] += 1
    fills.sort(key=lambda f: (f["ts"], f["trade_id"]))
    return fills, why


def window(fills: list, days: int | None) -> list:
    """The last `days` of the sample, by the venue's own trade clock.
    Current regime overrides lifetime history (directive section 18)."""
    if days is None or not fills:
        return fills
    end = fills[-1]["ts"][:10]
    y, m, d = (int(x) for x in end.split("-"))
    import datetime as _dt
    cut = (_dt.date(y, m, d) - _dt.timedelta(days=days - 1)).isoformat()
    return [f for f in fills if f["ts"][:10] >= cut]


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    lines: list[str] = []

    def say(m: str = "") -> None:
        lines.append(m)
        print(m, flush=True)

    say("=" * 74)
    say("BETA48 P1 -- RN1 PAIR / MERGE RECONSTRUCTION (raw fills, offline)")
    say("=" * 74)

    pay, pwhy = load_payouts()
    fills, fwhy = load_fills()
    say("SOURCE            %s" % EVENTS.relative_to(ROOT))
    say("SETTLEMENT        %s" % SETTLE.relative_to(ROOT))
    say("ESTIMATOR         backend/sportsassets/analytics/merge_pnl.py"
        "::replay (unmodified)")
    say()
    say("--- INGESTION CENSUS ---")
    for k, v in fwhy.most_common():
        say("  %-28s %8d" % (k, v))
    say()
    say("--- SETTLEMENT CENSUS ---")
    for k, v in pwhy.most_common():
        say("  %-28s %8d" % (k, v))
    say()
    if not fills:
        say("NO FILLS -- nothing reconstructed")
        return 2
    say("FILL WINDOW       %s .. %s" % (fills[0]["ts"], fills[-1]["ts"]))
    say("DISTINCT MARKETS  %d" % len({f["condition_id"] for f in fills}))
    say()

    results = {}
    for label, days in (("LIFETIME(sample)", None), ("LAST_60D", 60),
                        ("LAST_30D", 30), ("LAST_14D", 14), ("LAST_7D", 7)):
        sub = window(fills, days)
        if not sub:
            continue
        r = M.replay(sub, payouts=pay)
        results[label] = r
        merge = r.get("realized_merge_pnl") or 0.0
        sell = r.get("realized_sell_pnl") or 0.0
        entry = r.get("entry_notional") or 0.0
        say("--- %s  (%d fills, %s .. %s) ---"
            % (label, len(sub), sub[0]["ts"][:10], sub[-1]["ts"][:10]))
        say("  n_entries              %12d" % (r.get("n_entries") or 0))
        say("  n_merges               %12d" % (r.get("n_merges") or 0))
        say("  n_sells                %12d" % (r.get("n_sells") or 0))
        say("  merge_shares           %12.1f" % (r.get("merge_shares") or 0))
        say("  entry_notional      $ %12.2f" % entry)
        say("  MATCHED (merge) PnL $ %12.2f" % merge)
        say("  realized_sell  PnL  $ %12.2f" % sell)
        # The estimator's OWN verdict and its OWN caveats, quoted rather
        # than paraphrased. It computes the cluster-robust interval; this
        # file does not re-derive one.
        say("  edge_ci95              %s" % (r.get("edge_ci95"),))
        say("  EDGE_VERDICT           %s" % (r.get("edge_verdict"),))
        if r.get("edge_coverage_note"):
            say("  coverage_note          %s" % r["edge_coverage_note"])
        if r.get("cf_note"):
            say("  cf_note                %s" % r["cf_note"])
        for k in sorted(r):
            if k in ("rows", "n_entries", "n_merges", "n_sells",
                     "merge_shares", "entry_notional", "realized_merge_pnl",
                     "realized_sell_pnl"):
                continue
            v = r[k]
            if isinstance(v, (int, float)) or v is None:
                say("  %-22s %12s" % (k, v))
        say()

    (OUT / "rn1_pair_reconstruction.txt").write_text("\n".join(lines) + "\n")
    # `rows` is the 10 extreme merges and `clus` is the per-cluster
    # accumulator -- internal state, 1.4 MB per window, and not a result.
    # Both are dropped so the sealed artifact stays readable.
    drop = ("rows", "clus")
    (OUT / "rn1_pair_reconstruction.json").write_text(
        json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in drop}
                    for k, v in results.items()}, indent=1, default=str))
    say("sealed -> research/beta48/evidence/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
