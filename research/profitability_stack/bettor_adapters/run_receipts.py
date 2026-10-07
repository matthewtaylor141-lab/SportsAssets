"""Build the five real-data receipts from SELECT-only research-SQL extracts.

usage: python -I run_receipts.py OUT_DIR \
         --orders LOG --orders-run ID --arb LOG --arb-run ID \
         --twin LOG --twin-run ID --decisions DATASET.jsonl.gz

Writes OUT_DIR/<module>.json (package Receipt: kind / version / payload /
sha256) plus the gzipped datasets they were computed from and a manifest of
every code, query, dataset and receipt SHA. Nothing is read from or written to
a database, a network or an order path.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import common as C                                    # noqa: E402
import attribution_adapter as ATT                     # noqa: E402
import digital_twin_adapter as TW                     # noqa: E402
import execution_truth_adapter as EX                  # noqa: E402
import governor_adapter as GOV                        # noqa: E402
import structural_arb_adapter as ARB                  # noqa: E402
from positions import build_positions                 # noqa: E402

QUERIES = {"orders": "ps_orders_v1.sql", "arb": "ps_arb_v1.sql",
           "twin": "ps_twin_v1.sql", "decisions": "apl_extract_v1.sql"}
#: byte-identical copies of the queries the research-SQL workflow ran from
#: research/ on claude/p0-closeout (each receipt records their sha256)
QDIR = HERE / "queries"


def venue_counts(text: str) -> dict:
    out = {}
    for ln in text.splitlines():
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) == 3 and parts[0] in ("KALSHI", "POLYMARKET_US") and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def declared_rows(text: str) -> int | None:
    """psql's '(N rows)' footer of the first result set."""
    import re
    m = re.search(r"^\((\d+) rows?\)\s*$", text, re.M)
    return int(m.group(1)) if m else None


def write_gz(path: Path, rows) -> str:
    data = ("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n").encode()
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(path, "wb"), mtime=0) as fh:
        fh.write(data)
    return C.sha256_bytes(data)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    for k in ("orders", "arb", "twin"):
        ap.add_argument("--" + k, required=True)
        ap.add_argument("--%s-run" % k, required=True)
    ap.add_argument("--decisions", required=True)
    ap.add_argument("--generated-at", default=None)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    gen = a.generated_at or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    orders_txt = Path(a.orders).read_text()
    arb_txt = Path(a.arb).read_text()
    orders = C.parse_extract_log(orders_txt, "order_id")
    arb = C.parse_extract_log(arb_txt, "event_id")
    twin_txt = Path(a.twin).read_text()
    twin = C.parse_extract_log(twin_txt, "order_id")
    for nm, txt, rows in (("orders", orders_txt, orders), ("arb", arb_txt, arb), ("twin", twin_txt, twin)):
        want = declared_rows(txt)
        if want is not None and want != len(rows):
            raise SystemExit("%s: parsed %d rows but psql declared %d" % (nm, len(rows), want))
    decisions = C.read_jsonl_gz(a.decisions)

    data = {}
    for name, rows, run in (("orders", orders, a.orders_run), ("arb", arb, a.arb_run),
                            ("twin", twin, a.twin_run)):
        sha = write_gz(out / ("%s.jsonl.gz" % name), rows)
        data[name] = {"rows": len(rows), "sha256_of_jsonl": sha, "research_sql_run_id": run,
                      "query": QUERIES[name], "query_sha256": C.sha256_file(QDIR / QUERIES[name])}
    data["decisions"] = {"rows": len(decisions), "sha256_of_dataset_file": C.sha256_file(a.decisions),
                         "path": str(Path(a.decisions).resolve().relative_to(C.REPO)),
                         "query": QUERIES["decisions"],
                         "query_sha256": C.sha256_file(QDIR / QUERIES["decisions"])}
    code = C.code_sha(list(HERE.glob("*.py")) + list((C.STACK / "bettor_profit_stack").glob("*.py")))

    receipts = {}
    ex = EX.run(orders, decisions)
    receipts["execution_truth"] = C.receipt_envelope(
        "BETTOR_EXECUTION_TRUTH", ex, code=code,
        data={k: data[k] for k in ("orders", "decisions")}, generated_at=gen)

    vc = venue_counts(arb_txt)
    ar = ARB.run(arb, vc)
    receipts["structural_arb"] = C.receipt_envelope(
        "BETTOR_STRUCTURAL_ARB", ar, code=code, data={"arb": data["arb"]}, generated_at=gen)

    settle = {}
    by_gid = {}
    for r in orders:
        if r["role"] == "ENTRY" and r.get("settle_payout") is not None:
            by_gid[r["group_id"]] = {"payout": r["settle_payout"], "at": r.get("settled")}
    for o in twin:
        if o.get("role") == "ENTRY" and o["group_id"] in by_gid:
            settle[o["order_id"]] = by_gid[o["group_id"]]
    cl = [float(r["terminal_ev"]) - float(r["cancel_req"]) for r in orders
          if r.get("cancel_req") is not None and r.get("terminal_ev") is not None]
    tw = TW.run(twin, settlements=settle, cancel_latency_s=statistics.median(cl) if cl else 0.05)
    receipts["digital_twin"] = C.receipt_envelope(
        "BETTOR_DIGITAL_TWIN", tw, code=code, data={k: data[k] for k in ("twin", "orders")},
        generated_at=gen)

    positions = build_positions(orders)
    dec_by_s = {}
    for d in decisions:
        dec_by_s[d.get("strategy")] = dec_by_s.get(d.get("strategy"), 0) + 1
    exe_ok = {}
    for s, acts in ex["evaluation_actions_by_strategy"].items():
        non_refuse = sum(v for k, v in acts.items() if k != "REFUSE")
        if non_refuse == 0:
            exe_ok[s] = {"ok": False, "reason": "EVERY_EVALUATION_DECISION_REFUSED_BY_EXECUTION_TRUTH",
                         "evaluation_actions": acts}
        else:
            chk = [v for k, v in ex["realized_check_of_non_refused_paths"].items()
                   if k.startswith(s + "|") and (v.get("lower") or 0) > 0]
            exe_ok[s] = {"ok": bool(chk), "evaluation_actions": acts,
                         "reason": None if chk else "NON_REFUSED_PATHS_REALIZED_LOWER_BOUND_NOT_POSITIVE"}
    states = ar["registry_settlement_states_of_scanned_legs"]
    gv = GOV.run(positions, decision_rows_by_strategy=dec_by_s, execution_ok_by_strategy=exe_ok,
                 settlement_evidence={
                     "all_traded_contracts_proven_compatible": False,
                     "registry_states_seen_in_arb_scan": states,
                     "reason": "NO_SCANNED_REGISTRY_CONTRACT_IS_SETTLEMENT_PROVEN_COMPATIBLE"
                     if not states.get(ARB.PROVEN) else "TRADED_CONTRACTS_NOT_ALL_PROVEN"})
    receipts["profitability_governor"] = C.receipt_envelope(
        "BETTOR_PROFITABILITY_GOVERNOR", gv, code=code,
        data={k: data[k] for k in ("orders", "decisions")}, generated_at=gen)

    at = ATT.run(positions)
    receipts["pnl_attribution"] = C.receipt_envelope(
        "BETTOR_PNL_ATTRIBUTION", at, code=code, data={"orders": data["orders"]}, generated_at=gen)

    manifest = {"generated_at": gen, "authority": C.AUTHORITY, "code_sha256": code,
                "datasets": data, "receipts": {}}
    for k, r in receipts.items():
        p = out / ("%s.json" % k)
        p.write_text(json.dumps(r, indent=1, sort_keys=True, default=str) + "\n")
        manifest["receipts"][k] = {"receipt_sha256": r["sha256"], "file_sha256": C.sha256_file(p)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return receipts, manifest


if __name__ == "__main__":
    rs, man = main()
    for k, r in rs.items():
        p = r["payload"]
        print(k, r["sha256"][:16], json.dumps({x: p.get(x) for x in (
            "decision_rows", "unique_independent_events", "capital_status", "result",
            "evaluation_actions", "fill_agreement_rate", "no_lookahead_violations") if x in p})[:600])
