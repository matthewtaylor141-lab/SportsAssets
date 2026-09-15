#!/usr/bin/env python3
"""Generate research/rn1_latency_gate.sql from research/gen/rn1_latency_gate.core.sql.

Statement 0's code-side block is GENERATED from research/evidence_tiers.py --
its scan() (write sites), default_written() (DB defaults) and SEMANTICS
registry -- so the code half and the data half of the gate cannot drift. Editing
the SQL by hand to disagree with the registry is the drift this exists to stop.
"""
import sys

sys.path.insert(0, "research")
import evidence_tiers as ev  # noqa: E402

# The latency estimator's own fields, in the order statement 0 measures them.
LATENCY = [
    ("trades", "ts"), ("trades", "detected_at"), ("trades", "venue_seen_at"),
    ("copy_probes", "probe_at"), ("copy_probes", "fill_ts"),
    ("copy_probes", "reaction_s"), ("copy_probes", "best_ask"),
    ("copy_probes", "depth"), ("copy_probes", "book_ok"),
    ("price_path", "t_s"), ("price_path", "ask"), ("price_path", "sampled_at"),
    ("mirror_orders", "placed_at"), ("mirror_orders", "updated_at"),
    ("mirror_orders", "done_at"), ("mirror_orders", "receipt"),
    ("mirror_orders", "ask_at_send"),
    ("service_heartbeats", "beat_at"),
]

PURPOSE = {(t, f): why for t, f, why in ev.FIELDS}


def q(s):
    return "'" + s.replace("'", "''") + "'"


def main():
    writes, _reads = ev.scan()
    defaults = ev.default_written()
    missing = [k for k in LATENCY if k not in PURPOSE]
    if missing:
        sys.exit("not in the evidence_tiers registry: %r" % (missing,))
    rows = []
    for tbl, field in LATENCY:
        w = len(writes.get(field, ()))
        d = len(defaults.get(field, ()))
        stage2 = "WRITE_SITE" if w else ("DB_DEFAULT" if d else "NO_WRITE_SITE")
        stage4 = ev.SEMANTICS.get(field, ("UNVERIFIED", ""))[0]
        rows.append("  (%s, %s, %s, %s, %s)" % (
            q(tbl), q(field), q(PURPOSE[(tbl, field)]), q(stage2), q(stage4)))
    block = "  VALUES\n" + ",\n".join(rows)

    core = open("research/gen/rn1_latency_gate.core.sql").read()
    if "--%%CODEGATE%%" not in core:
        sys.exit("core79.sql has no --%%CODEGATE%% placeholder")
    out = core.replace("--%%CODEGATE%%", block)
    open("research/rn1_latency_gate.sql", "w").write(out)
    print("wrote research/rn1_latency_gate.sql, %d generated rows" % len(rows))
    for r in rows:
        print(" ", r.strip())


if __name__ == "__main__":
    main()
