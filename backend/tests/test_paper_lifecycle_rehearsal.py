"""THE THREE-AGENT PAPER LIFECYCLE REHEARSAL, RUN AS A PROOF.

    REHEARSAL -- SYNTHETIC -- NOT LIVE EXECUTION

`tools/paper_lifecycle_rehearsal` drives one deterministic scenario through
the paper engine's production entry points (`paper_runtime.decide_valuation`
and `paper_runtime.paper_pass`) on a LOCAL test database, in an isolated
`paper_rehearsal_*` account: entry -> partial fill -> handoff -> standing
protection -> exit -> exceptional settlement at the venue's price (and a
pending case) -> restart recovery -> Audrey's audits and the linked chain.
Every step is asserted inside the tool; this proof runs it, requires every
assertion to pass, and checks the evidence it writes.

The guards are proved without a database: a non-local DSN, a host list, the
production paper account or an account outside the rehearsal prefix are
refused by name, before anything is imported or connected.

SYNTHETIC valuations and books. No venue call, no venue order, no funded
table, never the production paper account.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from tests import paper_harness as H
from tools import paper_lifecycle_rehearsal as R

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


# ═════════════════════════════════════════════════════════════════════
# 1 · THE GUARDS (no database)
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@db.example.com:5432/sportsassets",
    "postgresql://u:p@10.0.0.5:5432/sportsassets",
    "postgresql://u:p@127.0.0.1,db.example.com:5432/x",
    "postgresql://u:p@127.0.0.1:5432/",
    "mysql://u:p@127.0.0.1:3306/x",
    "",
])
def test_a_non_local_dsn_is_refused(dsn):
    with pytest.raises(R.Refused):
        R.guard_dsn(dsn)


def test_a_local_dsn_is_accepted():
    for host in ("127.0.0.1", "localhost"):
        got = R.guard_dsn("postgresql://u:p@%s:5432/rh_local" % host)
        assert got == {"host": host, "port": 5432, "database": "rh_local"}


def test_only_a_rehearsal_account_is_ever_used():
    with pytest.raises(R.Refused):
        R.guard_account("paper_acct_main")
    with pytest.raises(R.Refused):
        R.guard_account("paper_test_x")
    acct = R.new_account_id(1_790_300_000.0)
    assert acct.startswith(R.ACCOUNT_PREFIX) and acct != "paper_acct_main"


def test_the_command_refuses_a_remote_dsn_before_doing_anything(tmp_path):
    rc = R.main(["--dsn", "postgresql://u:p@db.example.com:5432/x",
                 "--out", str(tmp_path / "out")])
    assert rc == 2
    assert not (tmp_path / "out").exists()


# ═════════════════════════════════════════════════════════════════════
# 2 · THE REHEARSAL, END TO END, ON THE LOCAL TEST DATABASE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_three_agent_lifecycle_rehearsal_passes(tmp_path):
    if (urlsplit(H.DSN).hostname or "").lower() not in R.LOCAL_HOSTS:
        pytest.skip("the rehearsal runs on a local test database only")
    # an earlier run's own synthetic valuations, if any, out of the window
    await R.purge_synthetic_valuations(H.DSN)
    try:
        ev = await R.rehearse(H.DSN, tmp_path)
    finally:
        # THE SHARED TEST DATABASE: this proof's synthetic valuations are
        # removed so no later proof's paper pass decides them
        await R.purge_synthetic_valuations(H.DSN)
    assert ev["status"] == "PASSED", (ev.get("failure"),
                                      ev.get("traceback"))
    assert ev["assertions_total"] >= 100
    assert ev["assertions_passed"] == ev["assertions_total"]
    assert ev["account_id"].startswith(R.ACCOUNT_PREFIX)

    # THE FILES, LABELLED
    saved = json.loads((tmp_path / R.EVIDENCE_FILE).read_text())
    assert saved["label"] == R.LABEL
    assert saved["status"] == "PASSED"
    md = (tmp_path / R.SUMMARY_FILE).read_text()
    assert md.startswith("# " + R.LABEL)
    assert "NOT LIVE EXECUTION" in md

    steps = {s["step"]: s for s in saved["steps"]}
    for name in ("1_entry", "2_partial_fill", "3_handoff",
                 "4_standing_protection", "5a_exit_recommendation",
                 "5b_exit_sale", "6_exceptional_settlement",
                 "7a_restart_mid_lifecycle", "7b_restart_cancel_pending",
                 "7c_restart_at_the_end", "8_audit"):
        assert name in steps, name
        assert steps[name]["assertions"], name
        assert all(a["passed"] for a in steps[name]["assertions"]), name

    # THE LOAD-BEARING FIGURES, read back from the evidence
    h = steps["3_handoff"]["evidence"]["handoffs"]
    fills = steps["2_partial_fill"]["evidence"]["A_fills"]
    assert h["A"]["confirmed_qty"] == sum(x["qty"] for x in fills) == 120.0
    s = steps["6_exceptional_settlement"]["evidence"]["B_settlement"]
    assert s["outcome"] == "SETTLED_AT_VENUE_PRICE"
    assert s["payout_per_contract"] == pytest.approx(0.43)
    assert steps["6_exceptional_settlement"]["evidence"][
        "C_position_open"]["settlement"] is None
    chains = steps["8_audit"]["evidence"]["chains"]
    for k in ("A", "B"):
        assert chains[k]["complete"] is True and chains[k]["missing"] == []
    assert all(p["mutation_attempts"] == 0 for p in saved["passes"])
