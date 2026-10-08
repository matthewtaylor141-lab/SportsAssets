"""THE SIGNED ACCEPTANCE RECORD (PM review of RC4, 2026-10-08).

The RC4 packet was attested in a continue-on-error step and the
attestation was never verified, so "attested" proved nothing; and a green
pm-acceptance run proves only that the evidence was COLLECTED, never that
it passes. This record separates the three questions and names every
failure:

  signature   the Sigstore attestations of evidence_packet.json and of
              SHA256SUMS VERIFIED against this repository and this
              workflow (gh attestation verify ... --signer-workflow, exit
              0 for both; the workflow passes the exit codes in)
  integrity   every SHA256SUMS line matches the bytes in acc/, and the
              manifest covers the packet and the acceptance verdict
  contents    the packet's own independent PM state (acceptance.json,
              re-run here from machine evidence) is GREEN

signed_acceptance is True only when all three hold. The verify step fails
the job when the signature or integrity fails; a RED or YELLOW contents
verdict is the truthful output and is recorded, not a job failure.

    python3 -I .github/pm-acceptance/signed_acceptance.py acc
    env: VERIFY_PACKET_RC, VERIFY_SUMS_RC (exit codes of gh attestation
         verify for the packet and for SHA256SUMS)

Writes acc/signed_acceptance.json (outside the manifest it checks).
Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys

R_SIGNATURE_NOT_VERIFIED = "ATTESTATION_NOT_VERIFIED"
R_SUMS_ABSENT = "SHA256SUMS_ABSENT"
R_SUMS_MISMATCH = "SHA256SUMS_MISMATCH"
R_SUMS_MISSING_FILE = "SHA256SUMS_LISTS_A_MISSING_FILE"
R_SUMS_DOES_NOT_COVER = "SHA256SUMS_DOES_NOT_COVER"
R_CONTENTS_NOT_GREEN = "CONTENTS_NOT_GREEN"
#: files the manifest must cover for the record to mean anything
REQUIRED = ("evidence_packet.json", "acceptance.json")


def check_sums(acc: pathlib.Path) -> list:
    """Named failures of SHA256SUMS against the bytes in acc/."""
    try:
        lines = (acc / "SHA256SUMS").read_text().splitlines()
    except OSError:
        return [R_SUMS_ABSENT]
    bad, listed = [], set()
    for ln in lines:
        h, sep, name = ln.partition("  ")
        if not sep or len(h) != 64 or not name or "/" in name or \
                name.startswith("."):
            bad.append("%s:unparsable line" % R_SUMS_MISMATCH)
            continue
        listed.add(name)
        p = acc / name
        if not p.is_file():
            bad.append("%s:%s" % (R_SUMS_MISSING_FILE, name))
        elif hashlib.sha256(p.read_bytes()).hexdigest() != h:
            bad.append("%s:%s" % (R_SUMS_MISMATCH, name))
    bad += ["%s:%s" % (R_SUMS_DOES_NOT_COVER, n) for n in REQUIRED
            if n not in listed]
    return bad


def contents_state(acc: pathlib.Path):
    """The independent PM state the packet itself carries, at its declared
    path (acceptance.independent_pm_state.value), else None."""
    try:
        pkt = json.loads((acc / "evidence_packet.json").read_text())
    except (OSError, ValueError):
        return None
    v = ((pkt.get("acceptance") or {}).get("independent_pm_state") or {})
    return v.get("value") if isinstance(v, dict) else None


def build(acc: pathlib.Path, *, verify_packet_rc, verify_sums_rc) -> dict:
    sig = []
    for what, rc in (("evidence_packet.json", verify_packet_rc),
                     ("SHA256SUMS", verify_sums_rc)):
        if str(rc) != "0":
            sig.append("%s:%s:rc=%s" % (R_SIGNATURE_NOT_VERIFIED, what, rc))
    integ = check_sums(acc)
    state = contents_state(acc)
    cont = [] if state == "GREEN" else ["%s:%s" % (R_CONTENTS_NOT_GREEN,
                                                  state)]
    pkt = acc / "evidence_packet.json"
    return {
        "signature_verified": not sig,
        "sha256sums_match": not integ,
        "contents_pm_state": state,
        "signed_acceptance": not sig and not integ and not cont,
        "packet_sha256": (hashlib.sha256(pkt.read_bytes()).hexdigest()
                          if pkt.is_file() else None),
        "reasons": sig + integ + cont,
        "meaning": "signed_acceptance requires a VERIFIED attestation, a "
                   "matching SHA256SUMS AND a GREEN independent PM state; "
                   "a completed collection run or a signature alone is "
                   "not acceptance"}


def main(argv, env=None) -> int:
    env = os.environ if env is None else env
    acc = pathlib.Path(argv[1] if len(argv) > 1 else "acc")
    rec = build(acc, verify_packet_rc=env.get("VERIFY_PACKET_RC", "unset"),
                verify_sums_rc=env.get("VERIFY_SUMS_RC", "unset"))
    (acc / "signed_acceptance.json").write_text(json.dumps(rec, indent=1))
    print(json.dumps(rec))
    # the job fails when the packet is not what was signed; RED / YELLOW
    # contents are the truthful verdict, recorded above
    return 0 if rec["signature_verified"] and rec["sha256sums_match"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
