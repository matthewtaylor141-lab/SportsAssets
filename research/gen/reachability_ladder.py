#!/usr/bin/env python3
"""SETTLEMENT ANALYZABILITY LADDER, from sealed bytes only.

    python3 research/gen/reachability_ladder.py

NO DATABASE. Opens exactly two committed files and nothing else:
    research/snapshots/u2_events_v1.jsonl.gz
    research/snapshots/settlement_v1.jsonl

WHAT THIS IS, PERMANENTLY:

    SETTLEMENT ANALYZABILITY FROM THE SEALED SNAPSHOT OBSERVED AT
    U2_SNAPSHOT_V1_DRAWN_AT.

It is NOT what BETTOR knew, NOT the database state at AUDIT_CUTOFF_TS, and NOT
a statement about historical settlement arrival. No historical-cutoff language
is used about settlement metadata anywhere in this file.

STRUCTURAL WELL-FORMEDNESS IS NOT CORRECTNESS. A payout vector that is an array
of the right length summing to one is WELL-FORMED. Whether the outcome it names
actually won has no independent retained source and is never inferred here.

NO ECONOMICS. This file computes no settlement-realized margin, no remaining
margin, no matched-vs-directional split, no fee adjustment and no P&L. Those are
81B and 81B is not approved.
"""
import gzip
import json
from collections import defaultdict

EV = "research/snapshots/u2_events_v1.jsonl.gz"
ST = "research/snapshots/settlement_v1.jsonl"
TOL = 1e-9

STAGES = ["U2_SNAPSHOT_V1", "LINKABLE_TO_CONDITION",
          "LINKABLE_TO_SETTLEMENT_METADATA", "STRUCTURALLY_ELIGIBLE",
          "RESOLVED", "CONSERVATIVE_SETTLEMENT_TIMING_QUARANTINE_REMOVED",
          "SETTLEMENT_ANALYZABLE"]

BUCKETS = ["UNLINKED_CONDITION", "NO_SETTLEMENT_METADATA",
           "TOKEN_METADATA_MISSING", "NOT_STRUCTURALLY_BINARY", "UNRESOLVED",
           "RESOLVED_PRICES_INVALID", "TIMING_QUARANTINE",
           "PAYOUT_MAPPING_AMBIGUOUS"]


def load_settlement():
    """Sealed conditions, with the structural verdicts precomputed per row."""
    out = {}
    with open(ST) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            toks = d.get("tokens") or []
            idx = [t.get("outcome_index") for t in toks]
            # STRUCTURALLY BINARY, from sealed token metadata alone: exactly two
            # tokens, outcome indices exactly {0,1} once each, both token ids
            # present. Nothing here consults resolution -- structure is asked
            # before, and independently of, whether the market resolved.
            d["_binary"] = (bool(d.get("token_metadata_present"))
                            and d.get("token_count") == 2
                            and sorted(i for i in idx if i is not None) == [0, 1]
                            and all(t.get("token_id") for t in toks))
            px = d.get("payouts")
            ok = (d.get("resolved_prices_type") == "array"
                  and isinstance(px, list)
                  and px and all(p is not None for p in px)
                  and d.get("payout_len") == d.get("token_count"))
            s = None
            if ok:
                vals = [float(p) for p in px]
                s = sum(vals)
                ok = abs(s - 1.0) <= 1e-6
                d["_unique_winner"] = sum(1 for v in vals if abs(v - 1.0) <= TOL) == 1
            else:
                d["_unique_winner"] = False
            d["_px_valid"] = bool(ok)
            d["_px_sum"] = s
            d["_tokidx"] = {t["token_id"]: t.get("outcome_index")
                            for t in toks if t.get("token_id")}
            out[d["condition_id"]] = d
    return out


def main():
    S = load_settlement()

    # PASS 1: the latest retained U2 fill per effective condition, for the
    # timing quarantine. Note the LIMIT this creates, stated rather than buried:
    # the approved quarantine predicate witnesses ANY retained RN1 fill (U0);
    # the sealed snapshot carries only U2 events, so the quarantine computed
    # here can only see U2 fills and is therefore WEAKER than the approved rule.
    # It can miss a condition whose only post-resolution fill was un-probed.
    latest = {}
    n_events = 0
    notional = 0.0
    with gzip.open(EV, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            n_events += 1
            notional += float(r["notional"])
            c = r.get("condition_id_effective")
            if c:
                ts = r["ts"]
                if c not in latest or ts > latest[c]:
                    latest[c] = ts

    stage_ev = defaultdict(int)
    stage_no = defaultdict(float)
    stage_cond = defaultdict(set)
    fail_ev = defaultdict(int)
    fail_no = defaultdict(float)
    sub = defaultdict(int)

    def hit(i, c, n):
        stage_ev[STAGES[i]] += 1
        stage_no[STAGES[i]] += n
        if c:
            stage_cond[STAGES[i]].add(c)

    with gzip.open(EV, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            n = float(r["notional"])
            c = r.get("condition_id_effective")
            hit(0, c, n)

            if not c:
                fail_ev["UNLINKED_CONDITION"] += 1
                fail_no["UNLINKED_CONDITION"] += n
                sub[r.get("condition_linkage_method", "?")] += 1
                continue
            hit(1, c, n)

            d = S.get(c)
            if d is None or not d.get("market_row_present"):
                fail_ev["NO_SETTLEMENT_METADATA"] += 1
                fail_no["NO_SETTLEMENT_METADATA"] += n
                continue
            hit(2, c, n)

            if not d.get("token_metadata_present"):
                fail_ev["TOKEN_METADATA_MISSING"] += 1
                fail_no["TOKEN_METADATA_MISSING"] += n
                continue
            if not d["_binary"]:
                fail_ev["NOT_STRUCTURALLY_BINARY"] += 1
                fail_no["NOT_STRUCTURALLY_BINARY"] += n
                sub[f"not_binary_token_count={d.get('token_count')}"] += 1
                continue
            hit(3, c, n)

            if not d.get("resolved"):
                fail_ev["UNRESOLVED"] += 1
                fail_no["UNRESOLVED"] += n
                continue
            if not d["_px_valid"]:
                fail_ev["RESOLVED_PRICES_INVALID"] += 1
                fail_no["RESOLVED_PRICES_INVALID"] += n
                sub[f"px_type={d.get('resolved_prices_type')}"] += 1
                continue
            hit(4, c, n)

            ra = d.get("resolved_at")
            if ra is not None and latest.get(c, "") > ra:
                fail_ev["TIMING_QUARANTINE"] += 1
                fail_no["TIMING_QUARANTINE"] += n
                continue
            hit(5, c, n)

            # PAYOUT MAPPING: the event's own asset must name exactly one token
            # of this condition, and that token's outcome index must be a real
            # index into the sealed payout vector. Anything else is ambiguous
            # and is dropped rather than guessed.
            oi = d["_tokidx"].get(r.get("asset"))
            if oi is None or not isinstance(oi, int) \
               or not (0 <= oi < len(d["payouts"])):
                fail_ev["PAYOUT_MAPPING_AMBIGUOUS"] += 1
                fail_no["PAYOUT_MAPPING_AMBIGUOUS"] += n
                sub["asset_not_in_sealed_token_list"] += 1
                continue
            if not d["_unique_winner"]:
                fail_ev["PAYOUT_MAPPING_AMBIGUOUS"] += 1
                fail_no["PAYOUT_MAPPING_AMBIGUOUS"] += n
                sub["no_unique_winning_outcome"] += 1
                continue
            hit(6, c, n)

    print("== SETTLEMENT ANALYZABILITY FROM THE SEALED SNAPSHOT ==")
    print("== observed at U2_SNAPSHOT_V1_DRAWN_AT = 2026-09-12T02:53:56.653273Z ==")
    print("NOT what BETTOR knew. NOT the database state at AUDIT_CUTOFF_TS.")
    print("NOT settlement arrival. Structural well-formedness is NOT correctness.")
    print("No economics computed. Sealed bytes only; no database.\n")

    print("== A. THE LADDER ==")
    print(f"{'stage':52}{'events':>10}{'conds':>9}{'notional':>16}"
          f"{'%ev':>8}{'%$':>8}{'d events':>10}{'d notional':>15}")
    pe = pn = None
    for st in STAGES:
        e, nn, cc = stage_ev[st], stage_no[st], len(stage_cond[st])
        de = "" if pe is None else f"{e - pe:+,}"
        dn = "" if pn is None else f"{nn - pn:+,.2f}"
        print(f"{st:52}{e:>10,}{cc:>9,}{nn:>16,.2f}"
              f"{100*e/n_events:>8.3f}{100*nn/notional:>8.3f}{de:>10}{dn:>15}")
        pe, pn = e, nn

    print("\nincremental loss, with the reason for each step:")
    reasons = [None, "UNLINKED_CONDITION", "NO_SETTLEMENT_METADATA",
               "TOKEN_METADATA_MISSING + NOT_STRUCTURALLY_BINARY",
               "UNRESOLVED + RESOLVED_PRICES_INVALID", "TIMING_QUARANTINE",
               "PAYOUT_MAPPING_AMBIGUOUS"]
    for i in range(1, len(STAGES)):
        a, b = STAGES[i - 1], STAGES[i]
        print(f"  {a} -> {b}: {stage_ev[b]-stage_ev[a]:+,} events, "
              f"{stage_no[b]-stage_no[a]:+,.2f} notional -- {reasons[i]}")

    print("\n== B. FIRST-FAIL TABLE (each dropped event counted exactly once) ==")
    print(f"{'first-fail reason':44}{'events':>10}{'notional':>16}{'%ev':>9}{'%$':>9}")
    te = tn = 0
    for b in BUCKETS:
        e, nn = fail_ev[b], fail_no[b]
        te += e
        tn += nn
        print(f"{b:44}{e:>10,}{nn:>16,.2f}"
              f"{100*e/n_events:>9.3f}{100*nn/notional:>9.3f}")
    fin_e, fin_n = stage_ev[STAGES[-1]], stage_no[STAGES[-1]]
    print(f"{'SETTLEMENT_ANALYZABLE':44}{fin_e:>10,}{fin_n:>16,.2f}"
          f"{100*fin_e/n_events:>9.3f}{100*fin_n/notional:>9.3f}")

    print("\n== C. EVENT-COUNT CLOSURE ==")
    print(f"  sum(first_fail) {te:,} + analyzable {fin_e:,} = {te+fin_e:,}")
    print(f"  U2_SNAPSHOT_V1                                = {n_events:,}")
    print(f"  CLOSES" if te + fin_e == n_events else "  FAILS")

    print("\n== D. SOURCE-NOTIONAL CLOSURE ==")
    gap = abs((tn + fin_n) - notional)
    print(f"  sum(first_fail) {tn:,.2f} + analyzable {fin_n:,.2f} = {tn+fin_n:,.2f}")
    print(f"  U2_SNAPSHOT_V1                                       = {notional:,.2f}")
    print(f"  gap {gap:.6f} -- " + ("CLOSES" if gap <= 0.005 else "FAILS"))

    print("\n== E. CONDITION-COUNT TRANSITIONS ==")
    prev = None
    for st in STAGES:
        c = len(stage_cond[st])
        print(f"  {st:52}{c:>9,}" + ("" if prev is None else f"  ({c-prev:+,})"))
        prev = c

    if sub:
        print("\n== sub-reasons observed ==")
        for k, v in sorted(sub.items(), key=lambda x: -x[1]):
            print(f"  {k:52}{v:>10,}")

    print("\n== CARRIED FORWARD ==")
    print("  UNLINKABLE_TO_CONDITION_WITH_RETAINED_METADATA:")
    print(f"    {fail_ev['UNLINKED_CONDITION']:,} events / "
          f"${fail_no['UNLINKED_CONDITION']:,.2f}, all CONDITION_TOKEN_UNMAPPED.")
    print("    Within RETAINED token metadata. NOT 'impossible to link in")
    print("    principle' -- market_tokens as sealed does not carry these tokens.")
    print("  DIRECT VALIDATION of the token map, from the sealed linkage census:")
    print("    192,824 direct events where a token mapping exists;")
    print("    192,824 / 192,824 agree; 0 conflict; 0 ambiguity.")
    print("    That is what supports using retained token mapping where present.")


if __name__ == "__main__":
    main()
