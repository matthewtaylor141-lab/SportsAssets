#!/usr/bin/env python3
"""RUN 81B POPULATION GATE -- the HALT checks, and the sealed loader behind them.

    python3 research/gen/run81b_population_gate.py

NO DATABASE. Opens exactly the three sealed artifacts and nothing else:
    research/snapshots/u2_events_v1.jsonl.gz
    research/snapshots/settlement_v1.jsonl
    research/snapshots/u0_timing_witness_v1.jsonl.gz

WHAT THIS FILE DOES:
  1. Verifies each artifact against its sealed UNCOMPRESSED_CANONICAL_SHA256 --
     the authoritative identity, not the .gz transport checksum.
  2. Re-derives SETTLEMENT_ANALYZABLE_STRONG from those bytes, applying the
     ladder predicate unchanged, and compares its three controls against the
     approved expectations. A mismatch is a HALT, not a note.
  3. Reports the side and source census of that population.
  4. Measures how much of U0 the sealed U2 population covers on exactly those
     conditions -- a ROW COUNT ONLY.

It is also the single definition of the population that run81b.py consumes, so
the analysis and the gate cannot drift apart into two predicates wearing one
name.

WHAT THIS FILE DOES NOT DO: any economics whatsoever.

WHY THE COVERAGE MEASUREMENT CANNOT BE VALUED. The U0 timing witness carries
trade_id, condition_id, ts and source -- deliberately no prices and no sizes,
because none of them enter the timing predicate it was sealed for. So the U0
rows outside the population can be COUNTED from sealed bytes and can never be
PRICED from them. Any per-condition acquisition pool built from these inputs is
a PARTIAL pool by construction, and this file says so rather than computing one.
"""
import gzip
import hashlib
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reachability_ladder import load_settlement, EV, ST, W_GZ  # noqa: E402

HASHES = "research/snapshots/HASHES.txt"
U0_HASHES = "research/snapshots/U0_HASHES.txt"

# The approved controls. Source: research/REACHABILITY_LADDER.md section F.
EXPECTED_EVENTS = 112543
EXPECTED_CONDITIONS = 9336
EXPECTED_NOTIONAL_2DP = Decimal("24727133.10")


# ---------------------------------------------------------------- hash gate
def _canonical_sha256(path):
    """sha256 over the payload lines, newline-joined, NO trailing newline.

    That is the sealed scope, quoted from the manifest: the committed file's own
    trailing newline is outside it, and so is the manifest. Reconstructing the
    scope rather than hashing the file is the point -- it is what makes the
    identity survive recompression.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    lines = []
    with opener(path, "rt") as fh:
        for line in fh:
            if line.endswith("\n"):
                line = line[:-1]
            if line:
                lines.append(line)
    h = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return h, len(lines)


def _read_kv(path):
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    return out


def verify_sealed_hashes():
    """[(artifact, expected, got, lines, ok)] against the AUTHORITATIVE identity."""
    h1, h0 = _read_kv(HASHES), _read_kv(U0_HASHES)
    checks = [
        ("u2_events_v1", EV, h1["U2_EVENT_DB_CANONICAL_SHA256"]),
        ("settlement_v1", ST, h1["SETTLEMENT_DB_CANONICAL_SHA256"]),
        ("u0_timing_witness_v1", W_GZ,
         h0["U0_WITNESS_UNCOMPRESSED_CANONICAL_SHA256"]),
    ]
    out = []
    for name, path, expected in checks:
        got, n = _canonical_sha256(path)
        out.append((name, expected, got, n, got == expected))
    return out


# ------------------------------------------------------------ sealed loader
class SealedInputs:
    """The three sealed artifacts, loaded once. No database anywhere in here."""

    def __init__(self):
        self.settlement = load_settlement()
        self.latest_u0 = {}
        self.u0_by_cond = defaultdict(int)
        self.n_witness = 0
        with gzip.open(W_GZ, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                self.n_witness += 1
                c = r.get("condition_id")
                if c:
                    # A U0 row with a NULL condition witnesses nothing about any
                    # condition. It is skipped here and still counted in the
                    # witness's own manifest.
                    self.u0_by_cond[c] += 1
                    if c not in self.latest_u0 or r["ts"] > self.latest_u0[c]:
                        self.latest_u0[c] = r["ts"]

        self.strong_quarantine = {
            c for c, d in self.settlement.items()
            if d.get("resolved_at") is not None
            and self.latest_u0.get(c, "") > d["resolved_at"]}

    def classify(self, r):
        """(FIRST-FAIL BUCKET, S) for one sealed event row.

        THE LADDER PREDICATE, APPLIED UNCHANGED AND IN ITS ORDER. The order is
        load-bearing: a bucket answers "where did this event FIRST fail", so
        reordering the tests would move events between buckets without changing
        any of them. S is the sealed terminal payout for the outcome the event
        actually bought -- payouts[outcome_index(asset)] -- and is None for
        anything that does not reach the end of the ladder.

        This is the single definition. population() below is written in terms of
        it, so the analysis cohort and the bucket census cannot drift apart.
        """
        c = r.get("condition_id_effective")
        if not c:
            return "UNLINKED_CONDITION", None
        d = self.settlement.get(c)
        if d is None or not d.get("market_row_present"):
            return "NO_SETTLEMENT_METADATA", None
        if not d.get("token_metadata_present"):
            return "TOKEN_METADATA_MISSING", None
        if not d["_binary"]:
            return "NOT_STRUCTURALLY_BINARY", None
        if not d.get("resolved"):
            return "UNRESOLVED", None
        if not d["_px_valid"]:
            return "RESOLVED_PRICES_INVALID", None
        if c in self.strong_quarantine:
            return "TIMING_QUARANTINE", None
        oi = d["_tokidx"].get(r.get("asset"))
        if oi is None or not isinstance(oi, int) \
           or not (0 <= oi < len(d["payouts"])):
            return "PAYOUT_MAPPING_AMBIGUOUS", None
        if not d["_unique_winner"]:
            return "PAYOUT_MAPPING_AMBIGUOUS", None
        return "SETTLEMENT_ANALYZABLE_STRONG", float(d["payouts"][oi])

    def events(self):
        """Yield (event_row, bucket, S) for every row of U2_SNAPSHOT_V1."""
        with gzip.open(EV, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                bucket, S = self.classify(r)
                yield r, bucket, S

    def population(self):
        """Yield (event_row, S) for every SETTLEMENT_ANALYZABLE_STRONG event."""
        for r, bucket, S in self.events():
            if bucket == "SETTLEMENT_ANALYZABLE_STRONG":
                yield r, S


def population_controls(sealed):
    """(events, conditions, exact Decimal notional, sides, sources, u2_by_cond)."""
    n = 0
    conds = set()
    # Decimal over the retained text, never float: the sealed rows carry six
    # decimal places and summing 112,543 of them in binary floating point would
    # make the control a function of addition order.
    notional = Decimal("0")
    sides, sources, u2_by_cond = defaultdict(int), defaultdict(int), defaultdict(int)
    for r, _s in sealed.population():
        n += 1
        c = r["condition_id_effective"]
        conds.add(c)
        notional += Decimal(r["notional"])
        sides[r.get("side")] += 1
        sources[r.get("source")] += 1
        u2_by_cond[c] += 1
    return n, conds, notional, sides, sources, u2_by_cond


def control_checks(n, conds, notional):
    pop_2dp = notional.quantize(Decimal("0.01"))
    return [
        ("events", n, EXPECTED_EVENTS),
        ("conditions", len(conds), EXPECTED_CONDITIONS),
        ("source notional", pop_2dp, EXPECTED_NOTIONAL_2DP),
    ]


def main():
    print("== RUN 81B POPULATION GATE ==")
    print("== SETTLEMENT_ANALYZABLE_STRONG, re-derived from sealed bytes ==")
    print("No database. No economics of any kind computed in this file.\n")

    print("== SEALED HASH GATE -- AUTHORITATIVE IDENTITY, not the .gz checksum ==")
    hashes = verify_sealed_hashes()
    for name, exp, got, nlines, ok in hashes:
        print(f"  {name:<24}{nlines:>10,} lines  "
              f"{'MATCH' if ok else '*** MISMATCH ***'}")
        if not ok:
            print(f"      expected {exp}")
            print(f"      got      {got}")
    if not all(ok for *_, ok in hashes):
        print("\n== HALT -- sealed hash failure. The bytes are not the sealed")
        print("== artifacts. Nothing downstream may be computed from them.")
        return 1
    print("  all three artifacts match their sealed canonical identity\n")

    sealed = SealedInputs()
    print(f"U0 timing witness rows read {sealed.n_witness:,}; "
          f"conditions datable {len(sealed.latest_u0):,}")
    print(f"STRONG quarantine conditions {len(sealed.strong_quarantine):,}\n")

    n, conds, notional, sides, sources, u2_by_cond = population_controls(sealed)
    checks = control_checks(n, conds, notional)
    halted = [nm for nm, got, exp in checks if got != exp]

    print("== CONTROLS ==")
    for nm, got, exp in checks:
        g = f"{got:,.2f}" if isinstance(got, Decimal) else f"{got:,}"
        e = f"{exp:,.2f}" if isinstance(exp, Decimal) else f"{exp:,}"
        flag = "MATCH" if got == exp else "*** MISMATCH ***"
        print(f"  {nm:<18}{g:>16}   expected {e:>16}   {flag}")
    print(f"  exact notional at full retained precision: {notional}")
    print("  The approved control is the 2 dp rounding of that exact sum; the")
    print("  comparison is made at 2 dp for that reason and not by truncation.")
    print()

    if halted:
        print("== HALT ==")
        print("  Controls did not reproduce: " + ", ".join(halted))
        print("  81B does not proceed. Report the drift before any further use")
        print("  of this population.")
        return 1
    print("== ALL THREE CONTROLS REPRODUCE. NO HALT. ==\n")

    print("== SIDE AND SOURCE CENSUS OF THE POPULATION ==")
    for k in sorted(sides, key=str):
        print(f"  side    {k!s:<10}{sides[k]:>12,}")
    for k in sorted(sources, key=str):
        print(f"  source  {k!s:<10}{sources[k]:>12,}")
    print()

    u0_ev = sum(sealed.u0_by_cond[c] for c in conds)
    complete = sum(1 for c in conds if u2_by_cond[c] == sealed.u0_by_cond[c])
    impossible = sum(1 for c in conds if u2_by_cond[c] > sealed.u0_by_cond[c])

    print("== U2 COVERAGE OF U0 ON THE ANALYZABLE CONDITIONS ==")
    print("   ROW COUNTS ONLY. The U0 witness carries no prices and no sizes,")
    print("   so the uncovered rows can be counted here and can never be")
    print("   valued from the locked inputs.")
    print(f"  U0 events on these conditions         {u0_ev:>12,}")
    print(f"  U2 events on the same conditions      {n:>12,}")
    print(f"  U2 share of U0 events                 {100.0 * n / u0_ev:>11.3f}%")
    print(f"  U0 events NOT in the population       {u0_ev - n:>12,}")
    print(f"  conditions with COMPLETE U2 coverage  {complete:>12,} of "
          f"{len(conds):,} ({100.0 * complete / len(conds):.2f}%)")
    print(f"  conditions WITHOUT complete coverage  {len(conds) - complete:>12,}")
    print(f"  conditions where U2 > U0 (impossible) {impossible:>12,}")
    print()
    print("FULL_RN1_MATCHED_BOOK_RECONSTRUCTION =")
    print("    NOT IDENTIFIABLE FROM CURRENT SEALED INPUTS")
    print("The 4,770-condition complete-coverage set is a COVERAGE DIAGNOSTIC")
    print("and is not a cohort. A per-condition acquisition pool built from")
    print("these inputs is a PARTIAL pool; an average cost from one is NOT")
    print("RN1's average cost. This file computes neither.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
