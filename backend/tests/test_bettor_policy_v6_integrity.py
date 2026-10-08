"""BETTOR_EV_SHADOW_V6: the boot verdict, driven through the real worker.

PRODUCTION AT 08828d04 (2026-10-07, render log 20:49:21Z):

    policy BETTOR_EV_SHADOW_V5  policyFreeze ALREADY_FROZEN
    policyIntegrity POLICY_CODE_DRIFT  decisionWritingAllowed False
    running code sha 98aaa204379a812b != frozen 92a190a086aeafc8

That verdict was CORRECT. V5 was frozen at e8ab303 (92a190a0 on 3.12.3);
70ca3a4 then gave `decide` an entry-gate branch under the same version
name (98aaa204). Nothing else since V5 moved the boundary on any
interpreter. The remedy is a new version, never a re-pinned literal.

These tests drive `workers.shadow_bettor.run` itself up to its boot
marker, with the REAL `shadow_store.freeze_policy` over an in-memory
shadow_policy_versions that refuses UPDATE and DELETE like migration 070's
append-only trigger. No database, so nothing here can skip.
"""

from __future__ import annotations

import asyncio
import copy
import pathlib
import re
from datetime import datetime, timezone

import pytest

from sportsassets import shadow as sh
from sportsassets import shadow_bettor as bettor
from sportsassets import shadow_bettor_codesha as cs
from sportsassets import shadow_bettor_policy as bpol
from sportsassets import shadow_store as store
import sportsassets.workers.shadow_bettor as W

SRC = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
NOW = datetime(2026, 10, 7, 21, 0, 0, tzinfo=timezone.utc)


class _Booted(Exception):
    pass


class PolicyTable:
    """shadow_policy_versions, as freeze_policy sees it."""

    def __init__(self, rows=()):
        self.rows = {r["policy_version"]: dict(r) for r in rows}
        self.statements = []

    async def fetchrow(self, sql, *args):
        self.statements.append(sql)
        assert "FROM shadow_policy_versions" in sql
        row = self.rows.get(args[0])
        return dict(row) if row else None

    async def execute(self, sql, *args):
        self.statements.append(sql)
        if re.search(r"\b(UPDATE|DELETE)\b", sql, re.IGNORECASE):
            raise AssertionError("shadow_policy_versions is append-only")
        assert "INSERT INTO shadow_policy_versions" in sql
        assert "ON CONFLICT DO NOTHING" in sql
        if args[0] not in self.rows:
            self.rows[args[0]] = {"policy_version": args[0],
                                  "policy_sha": args[1],
                                  "policy_code_sha": args[2],
                                  "lane": args[3], "frozen_at": NOW}
        return "INSERT 0 1"


def _v5_row():
    """V5 exactly as production holds it."""
    return {"policy_version": "BETTOR_EV_SHADOW_V5",
            "policy_sha": bpol.V5_FROZEN_POLICY_SHA,
            "policy_code_sha": bpol.V5_FROZEN_POLICY_CODE_SHA,
            "lane": "BETTOR_EV_SHADOW", "frozen_at": NOW}


def _v6_row(**over):
    row = {"policy_version": "BETTOR_EV_SHADOW_V6",
           "policy_sha": bpol.POLICY_SHA,
           "policy_code_sha": bpol.POLICY_CODE_SHA,
           "lane": "BETTOR_EV_SHADOW", "frozen_at": NOW}
    row.update(over)
    return row


def _boot(monkeypatch, table: PolicyTable) -> dict:
    """Run the worker until it publishes its boot marker."""
    seen = {}

    async def get_pool():
        return table

    async def store_ready(pool):
        return {"storeReady": True, "problems": [], "shadowMode": True,
                "capitalAtRisk": 0, "disclosure": sh.DISCLOSURE}

    async def freeze_sizing_policy(pool, policy=None):
        return {"status": "ALREADY_FROZEN"}

    async def record_boot(pool, boot):
        seen.update(boot)
        raise _Booted()

    monkeypatch.delenv("SHADOW_BETTOR", raising=False)
    monkeypatch.setattr(W, "get_pool", get_pool)
    monkeypatch.setattr(W.store, "store_ready", store_ready)
    monkeypatch.setattr(W.store, "freeze_sizing_policy", freeze_sizing_policy)
    monkeypatch.setattr(W.ops, "record_boot", record_boot)
    with pytest.raises(_Booted):
        asyncio.run(W.run())
    return seen


def _running(monkeypatch, digest: str) -> None:
    """Make the worker run `digest` as its code sha -- the digest of a
    REAL source edit, computed through the boundary, never a made-up one."""
    monkeypatch.setattr(bpol, "POLICY_CODE_SHA", digest)


# ── 1. the verified boot ─────────────────────────────────────────────


def test_first_v6_boot_freezes_v6_and_enables_decision_writing(monkeypatch):
    table = PolicyTable([_v5_row()])
    v5_before = copy.deepcopy(table.rows["BETTOR_EV_SHADOW_V5"])
    boot = _boot(monkeypatch, table)

    assert boot["policy"] == "BETTOR_EV_SHADOW_V6"
    assert boot["policyFreeze"] == "FROZEN"
    assert boot["policyIntegrity"] == W.INTEGRITY_OK == "VERIFIED"
    assert boot["codeShaMatches"] is True
    assert boot["decisionWritingAllowed"] is True
    assert boot["codeBoundary"] == "BETTOR_DECISION_PATH_V2"
    assert boot["policyCodeSha"] == boot["frozenPolicyCodeSha"] \
        == bpol.POLICY_CODE_SHA[:16]
    # V6 is a NEW row ...
    v6 = table.rows["BETTOR_EV_SHADOW_V6"]
    assert v6["policy_sha"] == bpol.POLICY_SHA
    assert v6["policy_code_sha"] == bpol.POLICY_CODE_SHA
    # ... and V5's row is untouched: one INSERT, no UPDATE, no DELETE.
    assert table.rows["BETTOR_EV_SHADOW_V5"] == v5_before
    inserts = [s for s in table.statements if "INSERT" in s]
    assert len(inserts) == 1


def test_a_later_v6_boot_is_already_frozen_and_verified(monkeypatch):
    table = PolicyTable([_v5_row(), _v6_row()])
    boot = _boot(monkeypatch, table)
    assert boot["policyFreeze"] == "ALREADY_FROZEN"
    assert boot["policyIntegrity"] == "VERIFIED"
    assert boot["codeShaMatches"] is True
    assert boot["decisionWritingAllowed"] is True
    assert not [s for s in table.statements if "INSERT" in s]


# ── 2. a semantic mutation after the freeze fails closed ─────────────


@pytest.mark.parametrize("module,old,new", [
    ("shadow_bettor.py", 'B_RISK_GATE = "RISK_GATE"',
     'B_RISK_GATE = "RISK_GATE_RENAMED"'),
    ("shadow_bettor.py", 'if gate["admissible"]:', "if True:"),
    ("shadow_lanes.py", 'fields["proposedAction"] = sh.NO_TRADE',
     'fields["proposedAction"] = sh.BUY'),
    ("bettor_entry_gate.py",
     'ADMISSIBLE_MODEL_STATUS = ("FROZEN", "PROMOTED", "CHAMPION")',
     'ADMISSIBLE_MODEL_STATUS = ("FROZEN", "PROMOTED", "CHAMPION", "DRAFT")'),
])
def test_a_semantic_mutation_after_v6_froze_is_policy_code_drift(
        monkeypatch, module, old, new):
    src = (SRC / module).read_text()
    assert old in src, "fixture drifted: %r not in %s" % (old, module)
    mutated = cs.semantic_code_sha(overrides={module: src.replace(old, new, 1)})
    assert mutated != bpol.POLICY_CODE_SHA

    table = PolicyTable([_v5_row(), _v6_row()])
    _running(monkeypatch, mutated)
    boot = _boot(monkeypatch, table)
    assert boot["policyFreeze"] == "ALREADY_FROZEN"
    assert boot["codeShaMatches"] is False
    assert boot["policyIntegrity"] == W.INTEGRITY_DRIFT == "POLICY_CODE_DRIFT"
    assert boot["decisionWritingAllowed"] is False
    assert mutated[:16] in boot["policyIntegrityWhy"]
    assert bpol.POLICY_CODE_SHA[:16] in boot["policyIntegrityWhy"]
    # collection is not what drift stops
    assert boot["storeReady"] is True


# ── 3. a comment-only / format-only edit does not ────────────────────


@pytest.mark.parametrize("module,old,new", [
    ("shadow_bettor.py", "def decide(",
     "# an explanatory comment that changes no behaviour\n\n\ndef decide("),
    ("bettor_entry_gate.py", "def admit(", "# prose only\ndef admit("),
    ("shadow_bettor.py", 'B_RISK_GATE = "RISK_GATE"',
     "B_RISK_GATE = ('RISK_GATE')"),
])
def test_a_comment_or_format_edit_after_v6_froze_stays_verified(
        monkeypatch, module, old, new):
    src = (SRC / module).read_text()
    assert old in src
    digest = cs.semantic_code_sha(overrides={module: src.replace(old, new, 1)})
    assert digest == bpol.POLICY_CODE_SHA
    table = PolicyTable([_v5_row(), _v6_row()])
    _running(monkeypatch, digest)
    boot = _boot(monkeypatch, table)
    assert boot["policyIntegrity"] == "VERIFIED"
    assert boot["decisionWritingAllowed"] is True


# ── 4. the incident itself, reproduced ───────────────────────────────


def test_the_08828d04_verdict_reproduces_under_v5(monkeypatch):
    """A build still labelled V5 but running 70ca3a4's decide."""
    v5_policy = dict(bpol.frozen_policy(),
                     policyVersion="BETTOR_EV_SHADOW_V5",
                     policySha=bpol.V5_FROZEN_POLICY_SHA,
                     policyCodeSha=bpol.V5_DRIFTED_POLICY_CODE_SHA)
    monkeypatch.setattr(bpol, "frozen_policy", lambda: v5_policy)
    monkeypatch.setattr(bpol, "BETTOR_POLICY_VERSION", "BETTOR_EV_SHADOW_V5")
    _running(monkeypatch, bpol.V5_DRIFTED_POLICY_CODE_SHA)
    boot = _boot(monkeypatch, PolicyTable([_v5_row()]))
    assert boot["policyFreeze"] == "ALREADY_FROZEN"
    assert boot["policyIntegrity"] == "POLICY_CODE_DRIFT"
    assert boot["decisionWritingAllowed"] is False
    assert boot["policyIntegrityWhy"].startswith(
        "running code sha 98aaa204379a812b does not match the frozen "
        "92a190a086aeafc8 for BETTOR_EV_SHADOW_V5")


def test_a_changed_declaration_under_frozen_v6_is_refused(monkeypatch):
    table = PolicyTable([_v5_row(), _v6_row(policy_sha="0" * 64)])
    boot = _boot(monkeypatch, table)
    assert boot["policyIntegrity"] == "POLICY_DECLARATION_REFUSED"
    assert boot["decisionWritingAllowed"] is False
    assert boot["storeReady"] is False
    assert not [s for s in table.statements if "INSERT" in s]


# ── 5. V5 is preserved; V6 differs from it only where it says it does ─


def test_v6_is_v5_plus_its_named_deltas_and_nothing_else():
    """Undo exactly the deltas V6 names and the result hashes to V5's
    frozen declaration. Any other change -- safety, belief, action set,
    universe, blocker vocabulary -- would make this fail."""
    d = copy.deepcopy(bpol.DECLARATION)
    assert d["policyVersion"] == "BETTOR_EV_SHADOW_V6"
    assert d["supersedes"] == "BETTOR_EV_SHADOW_V5"
    d["policyVersion"] = "BETTOR_EV_SHADOW_V5"
    d["supersedes"] = "BETTOR_EV_SHADOW_V4"
    d["consideredActionSet"] = bpol.CONSIDERED_ACTION_SET_V5
    d["codeBoundary"] = bpol.V5_FROZEN_CODE_BOUNDARY
    del d["entryGate"], d["supersedesBecause"]
    assert bpol.policy_sha(d) == bpol.V5_FROZEN_POLICY_SHA


def test_v5s_recorded_numbers_are_constants_and_never_recomputed():
    assert bpol.V5_FROZEN_POLICY_SHA.startswith("1887fe6ee4624e31")
    assert bpol.V5_FROZEN_POLICY_CODE_SHA.startswith("92a190a086aeafc8")
    assert bpol.V5_DRIFTED_POLICY_CODE_SHA.startswith("98aaa204379a812b")
    assert bpol.POLICY_SHA != bpol.V5_FROZEN_POLICY_SHA
    assert bpol.POLICY_CODE_SHA != bpol.V5_FROZEN_POLICY_CODE_SHA


def test_v6_decisions_never_collide_with_v5_rows():
    """The decision id carries the version, so a V6 decision about an
    opportunity V5 already decided is a new row, never a rewrite."""
    op = _op()
    d6 = bettor.decide(op, _book())
    v5_id = bettor._id("bdec", op["bettorOpportunityId"],
                       "BETTOR_EV_SHADOW_V5")
    assert d6["policyVersion"] == "BETTOR_EV_SHADOW_V6"
    assert d6["shadowDecisionId"] != v5_id


# ── 6. no authority moved ────────────────────────────────────────────


def test_v6_expands_no_authority():
    assert bpol.ACTION_SET == [sh.NO_TRADE]
    assert bpol.DECLARATION["decisionSemantics"]["actionSet"] == [sh.NO_TRADE]
    assert bpol.DECLARATION["decisionSemantics"]["currentEligibleAction"] \
        == sh.NO_TRADE
    s = bpol.DECLARATION["safety"]
    assert s["shadowMode"] is True
    assert s["realOrderSubmissionEnabled"] is False
    assert s["capitalAtRisk"] == 0
    assert s["mirrorLive"] is False
    assert s["orderPathPresent"] is False
    assert bpol.SIZING_POLICY_VERSION == bpol.NOT_APPLICABLE
    gate = bpol.DECLARATION["entryGate"]
    assert gate["productionInputsSupplied"] is False
    assert gate["productionOutcome"] == sh.NO_TRADE
    assert gate["admittedRecordIsShadowOnly"] is True


def test_the_production_path_supplies_no_entry_inputs():
    """The declaration's productionInputsSupplied=False, checked in the
    CODE (the parsed tree, so prose cannot satisfy or break it): the key
    is used in exactly one place in the package, and that place reads it."""
    import ast
    import warnings
    uses = []
    for path in SRC.rglob("*.py"):
        with warnings.catch_warnings():
            # other modules' regex literals; not this test's subject
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "entryInputs":
                uses.append(path.relative_to(SRC).as_posix())
    assert uses == ["shadow_bettor.py"], uses
    assert 'opportunity.get("entryInputs")' in (SRC / "shadow_bettor.py").read_text()


def test_a_production_shaped_decision_is_still_no_trade():
    d = bettor.decide(_op(), _book())
    assert d["proposedAction"] == sh.NO_TRADE
    assert d["policyVersion"] == "BETTOR_EV_SHADOW_V6"
    assert d.get("orderSubmitted") in (None, False)


# ── fixtures, as test_shadow_bettor builds them ──────────────────────


def _book(**over):
    fields = dict(captured_at=NOW, symbol="aec-nfl-x",
                  evidence_source="PMUS_BBO", bid=0.48, ask=0.52)
    fields.update(over)
    return store.market_state_record(**fields)


def _op(**over):
    fields = dict(symbol="aec-nfl-x", observed_at=NOW, outcome_leg="HOME",
                  evidence_source="PMUS_BBO", market_state=_book(),
                  cadence_s=300)
    fields.update(over)
    return bettor.opportunity_record(**fields)
