"""Build one immutable EV Probability Engine receipt.

usage: python -I run.py OUT_DIR --extract LOG --extract-run ID \
          --orders ORDERS.jsonl.gz --orders-source TEXT --generated-at ISO

OUT_DIR must not exist: a receipt is never overwritten (a new methodology
version or a new dataset gets a new directory). Writes dataset.jsonl.gz (the
oriented input frame), receipt.json, feedback.jsonl.gz and manifest.json with
every code, query, dataset and receipt SHA. Reads files only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dataset as D          # noqa: E402
import receipt as R          # noqa: E402

QUERY = HERE / "queries" / "ev_extract_v1.sql"


def code_sha() -> str:
    h = hashlib.sha256()
    for p in sorted(list(HERE.glob("*.py")) + list((HERE.parent / "bettor_ev_probability_engine").glob("*.py"))):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--extract", required=True)
    ap.add_argument("--extract-run", required=True)
    ap.add_argument("--orders", required=True)
    ap.add_argument("--orders-source", required=True)
    ap.add_argument("--generated-at", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        raise SystemExit("refused: %s exists -- receipts are immutable; use a new directory" % out)

    text = Path(a.extract).read_text()
    raw = D.parse_log(text)
    want = D.declared_rows(text)
    if want is not None and want != len(raw):
        raise SystemExit("extract: parsed %d rows but psql declared %d" % (len(raw), want))
    rows, dropped = D.frame_rows(raw)
    orders = D.read_jsonl_gz(a.orders)

    out.mkdir(parents=True)
    ds_sha = D.write_jsonl_gz(out / "dataset.jsonl.gz", rows)
    prov = {"code_sha256": code_sha(),
            "extract": {"research_sql_run_id": a.extract_run, "query": "ev_extract_v1.sql",
                        "query_sha256": hashlib.sha256(QUERY.read_bytes()).hexdigest(),
                        "rows_extracted": len(raw), "rows_dropped": dropped,
                        "raw_sha256": hashlib.sha256("\n".join(json.dumps(r, sort_keys=True) for r in raw).encode()).hexdigest()},
            "dataset_sha256": ds_sha,
            "orders": {"source": a.orders_source, "file_sha256": hashlib.sha256(Path(a.orders).read_bytes()).hexdigest(),
                       "rows": len(orders)},
            "generated_at": a.generated_at}
    rec = R.run(rows, orders, dataset_mod=D, fee_fn=D.taker_fee, provenance=prov)
    fb = rec.pop("_feedback_rows")
    fb_sha = D.write_jsonl_gz(out / "feedback.jsonl.gz", fb)
    rec["11_expected_vs_realized_feedback"]["file_sha256_of_jsonl"] = fb_sha
    body = json.dumps(rec, indent=1, sort_keys=True, default=str) + "\n"
    rec_sha = hashlib.sha256(body.encode()).hexdigest()
    (out / "receipt.json").write_text(body)
    man = {"version": R.VERSION, "authority": R.AUTHORITY, "receipt_sha256": rec_sha,
           "dataset_sha256": ds_sha, "feedback_sha256": fb_sha, **{k: prov[k] for k in ("code_sha256", "extract",
                                                                                         "orders", "generated_at")},
           "verdict": rec["12_capital_verdict"]["verdict"]}
    (out / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    return rec, man


if __name__ == "__main__":
    rec, man = main()
    print(json.dumps({"counts": rec["counts"], "selected": rec["5_untouched_holdout"]["selected_model"],
                      "verdict": rec["12_capital_verdict"]}, indent=1, default=str))
    print("receipt_sha256", man["receipt_sha256"])
