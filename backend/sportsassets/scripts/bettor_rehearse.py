"""Run the no-submit rehearsal over real captured inputs.

    python -m sportsassets.scripts.bettor_rehearse \
        ../research/beta48/rehearsal/inputs_20260920T2026Z.jsonl

Prints one line per evaluation and a summary. Exits non-zero if the
transport trip fired or a fabrication check failed -- a rehearsal that
reached for the wire, or that produced a positive decision by inventing
a missing term, is a failed rehearsal and must not read as a pass.
"""

from __future__ import annotations

import json
import sys

from .. import bettor_rehearsal as reh

FIELDS = ("OBSERVATION_ID", "INPUT_OBSERVED_AT", "INPUT_UNIVERSE_VERSION",
          "INPUT_RULE_SHA", "INPUT_READABILITY", "INPUT_LIVE_STATUS",
          "INPUT_MID", "INPUT_SPREAD", "DECISION", "BLOCKERS",
          "RISK_GATE", "RISK_GATE_DETAIL", "PROPOSED_INTENT", "PRICE",
          "SIZE", "PAIR_COMPLETION_STATUS", "EXIT_STATUS",
          "WOULD_SUBMIT_RESULT", "WOULD_NOT_SUBMIT_BECAUSE")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    rows = []
    with open(argv[1], encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    out = reh.run(rows, cohort=reh.COHORT_REAL)

    print("=" * 72)
    print("BETTOR NO-SUBMIT REHEARSAL  %s" % out["rehearsalVersion"])
    print("cohort=%s  evaluations=%d  inputs=%s"
          % (out["COHORT"], out["EVALUATIONS"], argv[1]))
    print("=" * 72)
    for i, r in enumerate(out["results"], 1):
        print("\n--- EVALUATION %d/%d ---" % (i, out["EVALUATIONS"]))
        for f in FIELDS:
            v = r.get(f)
            if isinstance(v, (list, dict)):
                v = json.dumps(v, default=str)
            print("  %-26s %s" % (f, v))
        ev = r["EV_COMPONENT_STATUS"]
        if ev:
            for action, detail in ev.items():
                print("  %-26s %s missing=%d %s"
                      % ("EV[" + action + "]", "", len(detail["missing"]),
                         ",".join(detail["missing"])))
        else:
            print("  %-26s %s" % ("EV_COMPONENT_STATUS",
                                  "no passive action applicable"))

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("  WOULD_SUBMIT            %d" % out["WOULD_SUBMIT_COUNT"])
    print("  WOULD_NOT_SUBMIT        %d" % out["WOULD_NOT_SUBMIT_COUNT"])
    print("  TRANSPORT_ARMED         %s" % ", ".join(out["TRANSPORT_ARMED"]))
    print("  SUBMISSION_REACHED_WIRE %s"
          % out["SUBMISSION_REACHED_THE_WIRE"])
    print("  IMPORT_GUARD            %s"
          % out["IMPORT_GUARD"]["IMPORT_GUARD"])
    print("  FABRICATION_CHECK       %s" % out["FABRICATION_CHECK"])
    print("  REAL_ORDER_ACTIVITY     %s"
          % out["BETTOR_EV_REAL_ORDER_ACTIVITY"])
    print("  REAL_CAPITAL_AT_RISK    %s"
          % out["BETTOR_EV_REAL_CAPITAL_AT_RISK"])
    print("=" * 72)

    bad = (out["SUBMISSION_REACHED_THE_WIRE"]
           or out["FABRICATION_CHECK"] != "CLEAN"
           or out["IMPORT_GUARD"]["IMPORT_GUARD"] != "CLEAN")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
