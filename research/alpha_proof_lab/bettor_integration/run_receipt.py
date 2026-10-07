"""Build the Alpha Proof Lab receipt from a research-sql extract log.

    python research/alpha_proof_lab/bettor_integration/run_receipt.py \
        <extract.log> <out_dir> --run-id <research-sql run id>

Reads only the log (the SELECT-only output of research/apl_extract_v1.sql);
writes <out_dir>/receipt.json and <out_dir>/dataset_manifest.json. No
database, network, order or capital access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import warnings
from pathlib import Path

# the vendored package passes penalty="l2" (deprecated in sklearn 1.8, same
# behaviour); its methodology is kept as delivered
warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import receipt as R                                              # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("out")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--generated-at", type=float, default=None)
    a = ap.parse_args(argv)
    text = Path(a.log).read_text()
    rows = R.parse_extract_log(text)
    sql = R.LAB.parents[0] / "apl_extract_v1.sql"
    src = {"extract_sql": "research/apl_extract_v1.sql",
           "extract_sql_sha256": hashlib.sha256(sql.read_bytes()).hexdigest(),
           "research_sql_run_id": a.run_id,
           "tables": ["paper_decisions", "paper_book_observations",
                      "paper_settlements", "external_valuations", "us_premap"],
           "access": "SELECT_ONLY_VIA_RESEARCH_SQL_WORKFLOW"}
    code = [p for p in R.LAB.rglob("*.py")
            if "tests" not in p.parts and "__pycache__" not in p.parts]
    rec = R.build_receipt(rows, code_sha=R.code_sha(code),
                          data_sha=R.dataset_sha(rows),
                          generated_at=a.generated_at or time.time(),
                          source=src)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rec, indent=1, sort_keys=True, default=str) + "\n"
    (out / "receipt.json").write_text(body)
    (out / "dataset_manifest.json").write_text(json.dumps({
        "rows": len(rows), "dataset_sha256": R.dataset_sha(rows),
        "receipt_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "source": src}, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"rows": len(rows), "usable": rec.get("rows_usable"),
                      "markets": rec.get("markets_usable"),
                      "verdict": rec.get("verdict")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
