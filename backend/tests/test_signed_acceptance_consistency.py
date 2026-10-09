"""Even verified checksums cannot make contradictory evidence acceptable.

Verification exit codes are simulated here; these tests exercise content
validation, and make no claim to verify a real Sigstore signature.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("signed_acceptance_consistency",
    ROOT / ".github/pm-acceptance/signed_acceptance.py")
SA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SA)


def evidence(tmp_path, acceptance, packet=None):
    acc = tmp_path / "acc"
    acc.mkdir()
    if packet is None:
        packet = {"acceptance": {"independent_pm_state": {
            "value": "GREEN", "source": "acceptance.json",
            "path": "independent_pm_state"}}}
    (acc / "evidence_packet.json").write_text(json.dumps(packet))
    (acc / "acceptance.json").write_text(acceptance)
    (acc / "SHA256SUMS").write_text("".join(
        hashlib.sha256((acc / name).read_bytes()).hexdigest() + "  " + name + "\n"
        for name in ("acceptance.json", "evidence_packet.json")))
    return acc


@pytest.mark.parametrize("source_state", ["RED", "YELLOW", None])
def test_a_green_packet_cannot_override_the_actual_source(tmp_path, source_state):
    acc = evidence(tmp_path, json.dumps({"independent_pm_state": source_state}))
    result = SA.build(acc, verify_packet_rc=0, verify_sums_rc=0)
    assert result["sha256sums_match"] is True
    assert result["signed_acceptance"] is False
    assert result["reasons"]
    assert SA.main(["checker", str(acc)], env={
        "VERIFY_PACKET_RC": "0", "VERIFY_SUMS_RC": "0"}) == 1


@pytest.mark.parametrize("source", ["not-json", "[]", "null", "{}",
    '{"independent_pm_state": {"value": "GREEN"}}'])
def test_checksummed_unreadable_or_wrongly_typed_source_is_not_accepted(
        tmp_path, source):
    result = SA.build(evidence(tmp_path, source), verify_packet_rc=0,
                      verify_sums_rc=0)
    assert result["sha256sums_match"] is True
    assert result["signed_acceptance"] is False
    assert result["reasons"]


@pytest.mark.parametrize("field,wrong", [("source", "other.json"),
    ("source", "../acceptance.json"), ("path", "pm_state")])
def test_a_green_value_must_name_the_actual_source_and_path(tmp_path, field, wrong):
    packet = {"acceptance": {"independent_pm_state": {
        "value": "GREEN", "source": "acceptance.json",
        "path": "independent_pm_state"}}}
    packet["acceptance"]["independent_pm_state"][field] = wrong
    result = SA.build(evidence(tmp_path,
        json.dumps({"independent_pm_state": "GREEN"}), packet),
        verify_packet_rc=0, verify_sums_rc=0)
    assert result["signed_acceptance"] is False
    assert result["reasons"]


@pytest.mark.parametrize("packet", [[], None, {"acceptance": []},
    {"acceptance": {"independent_pm_state": "GREEN"}}])
def test_malformed_packet_is_recorded_as_unproven_without_crashing(tmp_path, packet):
    # Write explicit null too; evidence() uses None for its default.
    acc = evidence(tmp_path, json.dumps({"independent_pm_state": "GREEN"}),
                   packet if packet is not None else {"acceptance": None})
    result = SA.build(acc, verify_packet_rc=0, verify_sums_rc=0)
    assert result["signed_acceptance"] is False
    assert result["reasons"]
