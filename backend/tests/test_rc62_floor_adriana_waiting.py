"""(RC6.2 D6f) ADRIANA'S DESK IS NOT WORKING_ON WHILE SHE READS NO BOOKS.

Production (readback 37972404139): 23 of 23 census passes in two hours were
NO_EVIDENCE (0 markets read, 0 fresh books), yet the floor raised a
WORKING_ON signal for every finished pass, and derive_state ranks a recent
signal above her recorded WAITING_FOR_EVIDENCE status. Only a pass that
actually read markets and fresh books is work; otherwise her desk shows the
agent_status the runner wrote (WAITING_FOR_EVIDENCE, with its reason).
"""
from __future__ import annotations

from sportsassets.api import command_floor as FL

NOW = 1_800_000_000.0

NO_EVIDENCE_PASS = {
    "scan_id": "adr-scan-1", "status": "NO_EVIDENCE",
    "why": "NO_RECORDED_BOOK_OF_A_SUPPORTED_FAMILY_IN_THE_LAST_900S",
    "markets_read": 0, "books_fresh": 0, "structures_considered": 0,
    "opportunities": 0, "refusals_total": 0, "finished_at": NOW - 20}
READ_PASS = dict(NO_EVIDENCE_PASS, status="OK", why=None, markets_read=40,
                 books_fresh=12, structures_considered=7)
WAITING = {"state": "WAITING_FOR_EVIDENCE",
           "activity": "NO_RECORDED_BOOK_IN_WINDOW"}


def _desk(scan):
    signals = []
    if FL.adriana_pass_read_books(scan):
        signals.append({"at": scan["finished_at"], "hint": "WORKING_ON",
                        "label": "Census", "ref": {"id": scan["scan_id"]}})
    return FL.derive_state("ADRIANA", now=NOW, deployed=True,
                           deploy_why=None, heartbeat_at=NOW - 20,
                           stale_s=900.0, status=WAITING, signals=signals)


def test_a_no_evidence_pass_is_not_work():
    assert FL.adriana_pass_read_books(NO_EVIDENCE_PASS) is False
    # a status other than NO_EVIDENCE with zero books read is not work either
    assert FL.adriana_pass_read_books(
        dict(NO_EVIDENCE_PASS, status="PARTIAL")) is False
    assert FL.adriana_pass_read_books(
        dict(READ_PASS, books_fresh=0)) is False
    assert FL.adriana_pass_read_books(None) is False


def test_a_pass_that_read_books_is_work():
    assert FL.adriana_pass_read_books(READ_PASS) is True
    s = _desk(READ_PASS)
    assert s["state"] == "WORKING_ON"


def test_her_desk_shows_waiting_for_evidence_with_the_reason():
    s = _desk(NO_EVIDENCE_PASS)
    assert s["state"] == "WAITING", s
    assert "NO_RECORDED_BOOK_IN_WINDOW" in s["detail"]
    assert s["basis"][0]["state"] == "WAITING_FOR_EVIDENCE"


def test_the_floor_gates_the_census_signal_on_books_read():
    # the build_floor Adriana branch raises the census WORKING_ON signal only
    # through the gate (never unconditionally for every finished pass)
    import inspect
    src = inspect.getsource(FL.build_floor)
    i = src.index('elif a == "ADRIANA"')
    branch = src[i:src.index("monitor = [", i)]
    assert "adriana_pass_read_books(sc)" in branch
    gate = branch.index("adriana_pass_read_books(sc)")
    assert branch.index('"WORKING_ON"', gate) > gate
    assert '"WORKING_ON"' not in branch[:gate]
