#!/usr/bin/env python3
"""Replay REAL captured observations through the real decision engine.

    python3 scripts/bettor_replay_real.py [rows.json]

With no argument it uses the committed sample of real captured rows. The
decisions are real -- the actual engine, the actual normalizer, the
actual institutional capability registry. No fills are simulated and no
performance number is produced, because a decision-only replay cannot
support one.

INSTITUTIONAL SEMANTICS, NOT THE DEMO FIXTURE. The demo venue's
`holds_both_legs_independently = SUPPORTED` exists to exercise code
paths. This runs against ('polymarket-us', 'institutional'), whose
capability is UNKNOWN in the registry, and UNKNOWN blocks.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "backend")

from sportsassets import bettor_decision_engine as de           # noqa: E402
from sportsassets import bettor_observation_adapter as oa       # noqa: E402
from sportsassets import bettor_venue_contract as vc            # noqa: E402

SAMPLE = "research/beta48/acceptance/replay_sample_rows.json"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else SAMPLE
    with open(path) as fh:
        rows = json.load(fh)

    print("=" * 74)
    print("REAL-OBSERVATION REPLAY  |  %d captured rows from %s" % (len(rows), path))
    print("=" * 74)

    caps = vc.capabilities("polymarket-us", "institutional")
    print("\nVENUE CONTRACT IN FORCE (not the demo fixture):")
    for f in ("holds_both_legs_independently", "complement_quote_source",
              "native_merge", "maker_orders", "verified_fee_schedule"):
        print("   %-34s %s" % (f, getattr(caps, f)))

    recs = [oa.normalize(r, venue="polymarket-us",
                         account_class="institutional") for r in rows]
    rep = oa.report(recs)
    print("\nNORMALIZATION")
    print("   rows %d | accepted %d | rejected %d"
          % (rep["rows"], rep["accepted"], rep["rejected"]))
    print("   complement sources: %s" % json.dumps(rep["complement_sources"]))
    print("   rejection reasons:")
    for reason, n in rep["rejection_reasons"].items():
        print("      %-44s %5d" % (reason, n))

    pairing = oa.pair_legs(recs)
    print("\nPAIRING (same market_id + two distinct outcome legs)")
    print("   genuine complement pairs formed: %d" % len(pairing["pairs"]))
    print("   contracts that could not be paired: %d" % len(pairing["unpaired"]))
    for u in pairing["unpaired"][:3]:
        print("      %s: %s" % (u["market_id"][:28], u["why"][:58]))

    print("\nDECISIONS (real engine, institutional capabilities, no fills)")
    outcomes: dict = {}
    blockers: dict = {}
    shown = 0
    for rec in recs:
        if rec.status != oa.ACCEPTED:
            continue
        d = de.decide(oa.to_book(rec), fees=de.Fees(source=rec.fee_source),
                      max_contracts=10, venue=rec.venue,
                      account_class=rec.account_class)
        outcomes[d["selected"]] = outcomes.get(d["selected"], 0) + 1
        for c in d["candidates"]:
            if c["blocker"]:
                blockers[c["blocker"]] = blockers.get(c["blocker"], 0) + 1
        if shown < 3:
            print("   %-30s -> %-9s  dq=%s"
                  % (str(rec.market_id)[:30], d["selected"], d["data_quality"]))
            print("        %s" % d["reason"][:66])
            shown += 1
    print("\n   decision counts: %s" % json.dumps(outcomes))
    print("   blockers encountered:")
    for b, n in sorted(blockers.items(), key=lambda kv: -kv[1]):
        print("      %-52s %5d" % (b, n))

    print("\n" + "=" * 74)
    print("WHAT THIS ESTABLISHES, AND WHAT IT DOES NOT")
    print("=" * 74)
    print("   Establishes: the engine consumes REAL captured observations,")
    print("   normalizes them under institutional semantics, and reaches a")
    print("   decision on every accepted row without fabricating anything.")
    print("   Does NOT establish: any edge, any fill, any profitability.")
    print("   No order was simulated and no performance number exists here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
