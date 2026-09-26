"""ENUMERATE WHAT THE CAPACITY PROBE WROTE, AND WHERE. An audit, not a fix.

WHY THIS EXISTS. The probe's writer phases stamped the AUTONOMOUS
STRATEGY's experiment id on every synthetic plan, so in any database the
probe ran against there are rows sitting in the book that means "what the
engine decided on current markets". Before anything is corrected, the
records have to be ENUMERATED: which database, which position ids, and what
hangs off them.

IT IS READ-ONLY. It deletes nothing and rewrites nothing. The correction is
a separate, deliberate act, and it must never be "delete everything in the
strategy experiment" -- that would take genuine rows with it.

HOW A PROBE RECORD IS IDENTIFIED. Not by its experiment (that is the
defect) but by the FINGERPRINTS the probe itself puts on a row and nothing
else does:

    condition_id   0x...c0de0000 + i        the writer phases
    condition_id   0x...cafe0000 + i        the lifecycle phase
    venue_market_slug  aec-cap-%05d-...     the writer phases
    venue_market_slug  aec-lifecycle-%05d-  the lifecycle phase
    payout_event   "Capacity Probe Side A" / "Capacity Lifecycle ..."

A row that carries one of those was written by this harness. A row that
does not is not its business, whatever experiment it is under.

Run: python3 -m tools.capacity_probe_manifest --pg <base dsn> [--json path]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

#: The strategy books a probe record must never be filed under.
STRATEGY_EXPERIMENTS = ("EXT_PINNACLE_DEVIG_V1_SHADOW",)

#: What identifies a probe record REGARDLESS of the experiment it carries.
FINGERPRINT = """
    (p.condition_id LIKE '0x%c0de0000'
     OR p.condition_id LIKE '0x%cafe0000'
     OR p.condition_id ~ '^0x0+c0de[0-9a-f]{4}$'
     OR p.condition_id ~ '^0x0+cafe[0-9a-f]{4}$'
     OR p.venue_market_slug LIKE 'aec-cap-%'
     OR p.venue_market_slug LIKE 'aec-lifecycle-%'
     OR p.payout_event LIKE 'Capacity Probe %'
     OR p.payout_event LIKE 'Capacity Lifecycle %')
"""

COUNTS_SQL = """
WITH probe AS (
  SELECT p.position_id, p.experiment_id, p.policy, p.condition_id,
         p.venue_market_slug, p.payout_event
    FROM rn1x_positions p
   WHERE %(fp)s
)
SELECT
  (SELECT count(*) FROM probe)                                AS positions,
  (SELECT count(*) FROM probe WHERE experiment_id = ANY(%(se)s))
                                                              AS in_strategy_book,
  (SELECT count(*) FROM rn1x_orders o
    WHERE o.position_id IN (SELECT position_id FROM probe))    AS orders,
  (SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o
     ON o.order_id = f.order_id
   WHERE o.position_id IN (SELECT position_id FROM probe))     AS fills,
  (SELECT count(*) FROM rn1x_decisions d
    WHERE d.position_id IN (SELECT position_id FROM probe))    AS decisions,
  (SELECT count(*) FROM rn1x_outcomes x
    WHERE x.position_id IN (SELECT position_id FROM probe))    AS outcomes,
  (SELECT coalesce(sum(f.fee_usd), 0)::float8 FROM rn1x_fills f
     JOIN rn1x_orders o ON o.order_id = f.order_id
   WHERE o.position_id IN (SELECT position_id FROM probe))     AS fees_usd,
  (SELECT coalesce(sum(f.qty), 0)::float8 FROM rn1x_fills f
     JOIN rn1x_orders o ON o.order_id = f.order_id
   WHERE o.position_id IN (SELECT position_id FROM probe))     AS filled_qty,
  (SELECT count(*) FROM external_valuations e
    WHERE e.condition_id IN (SELECT condition_id FROM probe))  AS valuations,
  (SELECT count(*) FROM rn1x_positions)                        AS all_positions
"""

IDS_SQL = """
SELECT p.position_id, p.experiment_id, p.policy, p.condition_id,
       p.venue_market_slug
  FROM rn1x_positions p
 WHERE %(fp)s
 ORDER BY p.position_id
"""

BOOKS_SQL = """
SELECT experiment_id, count(*) AS positions
  FROM rn1x_positions GROUP BY 1 ORDER BY 2 DESC
"""


def _psql(dsn: str, sql: str) -> list:
    """One query, tab-separated, or an empty list when the table is absent."""
    out = subprocess.run(["psql", dsn, "-tA", "-F", "\t", "-c", sql],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [ln.split("\t") for ln in out.stdout.strip().splitlines() if ln]


def databases(base: str) -> list:
    rows = _psql(base + "/postgres",
                 "SELECT datname FROM pg_database WHERE datistemplate = "
                 "false AND datname <> 'postgres' ORDER BY 1")
    return [r[0] for r in rows]


def audit_one(dsn: str, *, with_ids: bool = True) -> dict:
    has = _psql(dsn, "SELECT to_regclass('public.rn1x_positions') IS NOT NULL")
    if not has or has[0][0] != "t":
        return {"has_ledger": False}
    se = "ARRAY[" + ",".join("'%s'" % e for e in STRATEGY_EXPERIMENTS) + "]"
    rows = _psql(dsn, COUNTS_SQL % {"fp": FINGERPRINT, "se": se})
    if not rows:
        return {"has_ledger": True, "query_failed": True}
    keys = ("positions", "in_strategy_book", "orders", "fills", "decisions",
            "outcomes", "fees_usd", "filled_qty", "valuations",
            "all_positions")
    got = dict(zip(keys, rows[0]))
    out = {"has_ledger": True}
    for k in keys:
        v = got.get(k)
        out[k] = (float(v) if k in ("fees_usd", "filled_qty")
                  else int(v or 0))
    out["books"] = {r[0]: int(r[1]) for r in _psql(dsn, BOOKS_SQL)}
    if with_ids and out["positions"]:
        ids = _psql(dsn, IDS_SQL % {"fp": FINGERPRINT})
        # THE IDS ARE THE MANIFEST, so they are kept in full -- but as plain
        # strings grouped by the book they were filed under. A per-row object
        # repeating the same four keys 2,000 times is nine times the bytes
        # and no more auditable.
        by = {}
        for r in ids:
            by.setdefault(r[1], []).append(r[0])
        out["position_ids_by_experiment"] = {k: sorted(v)
                                            for k, v in by.items()}
        out["position_id_count"] = sum(len(v) for v in by.values())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pg", default="postgresql://postgres:postgres@"
                                    "127.0.0.1:5432")
    ap.add_argument("--dsn", default="", help="audit ONE database instead")
    ap.add_argument("--json", default="")
    ap.add_argument("--no-ids", action="store_true")
    a = ap.parse_args()
    report = {"audit": "CAPACITY_PROBE_AFFECTED_RECORDS_V1",
              "read_only": True,
              "how_a_probe_record_is_identified": (
                  "by the fingerprints the harness itself writes -- the "
                  "condition id shape, the slug prefix and the payout "
                  "event -- NEVER by the experiment id, which is the "
                  "defect being audited"),
              "strategy_experiments": list(STRATEGY_EXPERIMENTS),
              "databases": {}}
    if a.dsn:
        report["databases"][a.dsn.rsplit("/", 1)[-1]] = audit_one(
            a.dsn, with_ids=not a.no_ids)
    else:
        for db in databases(a.pg):
            got = audit_one(a.pg + "/" + db, with_ids=not a.no_ids)
            if got.get("has_ledger") and (got.get("positions")
                                          or got.get("all_positions")):
                report["databases"][db] = got
    affected = {d: v for d, v in report["databases"].items()
                if v.get("positions")}
    report["databases_with_probe_records"] = sorted(affected)
    report["probe_positions_total"] = sum(v["positions"]
                                          for v in affected.values())
    report["probe_positions_in_a_strategy_book"] = sum(
        v["in_strategy_book"] for v in affected.values())
    text = json.dumps(report, indent=1, default=str)
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(text + "\n")
        print("wrote %s (%d bytes)" % (a.json, len(text)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    raise SystemExit(main())
