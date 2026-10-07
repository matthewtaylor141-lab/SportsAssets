"""Re-derive each committed receipt from its committed dataset and require
the bytes to match (dataset sha, receipt sha). Exit 1 on any mismatch.

A receipt is reproducible only against the code that produced it: the
receipt records `11_model_card.card.code_sha`; when the current code hash
differs the receipt is reported STALE_CODE (re-derivation is skipped, not
failed), so changing the lab never silently rewrites an old receipt."""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import receipt as R                                              # noqa: E402


def main(root: str) -> int:
    bad = 0
    code = [p for p in R.LAB.rglob("*.py")
            if "tests" not in p.parts and "__pycache__" not in p.parts]
    now_code = R.code_sha(code)
    for d in sorted(Path(root).iterdir()):
        if not (d / "receipt.json").exists():
            continue
        man = json.loads((d / "dataset_manifest.json").read_text())
        rows = [json.loads(x) for x in gzip.open(d / "dataset.jsonl.gz", "rt")
                if x.strip()]
        ds = R.dataset_sha(rows)
        body = (d / "receipt.json").read_text()
        rec = json.loads(body)
        ok_ds = ds == man["dataset_sha256"] == rec["11_model_card"]["card"]["data_sha"]
        ok_rs = hashlib.sha256(body.encode()).hexdigest() == man["receipt_sha256"]
        state = "OK"
        if rec["11_model_card"]["card"]["code_sha"] != now_code:
            state = "STALE_CODE_NOT_RE_DERIVED"
        else:
            again = R.build_receipt(rows, code_sha=now_code, data_sha=ds,
                                    generated_at=rec["generated_at"],
                                    source=rec["source"])
            if json.dumps(again, indent=1, sort_keys=True, default=str) + "\n" != body:
                state = "RE_DERIVATION_DIFFERS"
        good = ok_ds and ok_rs and state != "RE_DERIVATION_DIFFERS"
        bad += not good
        print(json.dumps({"receipt": d.name, "dataset_sha_ok": ok_ds,
                          "receipt_sha_ok": ok_rs, "state": state,
                          "verdict": rec.get("verdict")}))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "receipts"))
