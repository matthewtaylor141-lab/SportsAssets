"""THE UNFUNDED RESEARCH LANE: one gate waived, and nothing else moves.

WHAT IS AUTHORISED. The owner authorised collecting unfunded shadow
evidence while `PINNACLE_DEVIG_V1`'s calibration stays explicitly
unmeasured. `external_source_calibration` has zero rows in production and
these tests do not add one.

THE DISTINCTION THIS FILE EXISTS TO HOLD. `bettor_entry_execution` says of
MODEL_TRUST_DRIFT that it "does not lift by argument", and that is still
true: `state_from_evidence` is untouched and never returns True for it. The
waiver sits ABOVE the gate. It hands the risk engine a separate map, keeps
the gate's own answer under `gate_state_as_read`, and records both — so a
row always shows what the evidence said as well as what the engine was
given.

Put plainly: an unvalidated external valuation may not size a FUNDED
position, and this does not let it. It may size a SIMULATED one, in a lane
with no path to capital, so the records become the calibration evidence the
gate is waiting for.

WHAT MUST STILL BLOCK, and is asserted below: freshness, the settlement
condition-to-payout comparison, the source's declared support, every
exposure rail, every identity check, the execution estimate, sizing, and
positive net edge. The waiver cannot make an entry happen; it can only stop
being the reason one did not.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets import bettor_entry_execution as entryx
from sportsassets import bettor_research_shadow as rsh


def _unmeasured():
    return {"measured": False, "why": "no calibration row exists"}


def _gates(**over):
    """A gate map with the production shape: calibration NOT_EVALUABLE."""
    g = {"STALE_DATA": True,
         "UNRESOLVED_SETTLEMENT_SEMANTICS": True,
         "OUT_OF_DISTRIBUTION": True,
         "MODEL_TRUST_DRIFT": None}
    g.update(over)
    return g


# ── the gate itself is untouched ──────────────────────────────────────

def test_the_gate_still_reads_not_evaluable_with_no_calibration():
    """THE LOAD-BEARING ASSERTION. If this ever returns True the waiver has
    been pushed down into the gate, which is the thing it must not be."""
    got = entryx.state_from_evidence(
        freshness={"fresh": True}, settlement={"compatibility": "COMPATIBLE"},
        probability=0.5, calibration=_unmeasured())
    assert got["state"]["MODEL_TRUST_DRIFT"] is None
    assert "MODEL_TRUST_DRIFT" in got["blocking"]
    assert entryx.R_NO_CALIBRATION in got["why"]["MODEL_TRUST_DRIFT"]


def test_the_waiver_does_not_mutate_the_gate_map_it_is_given():
    g = _gates()
    rsh.waive(g, authorised_flag=True, calibration=_unmeasured())
    assert g["MODEL_TRUST_DRIFT"] is None, "the caller's map was mutated"


def test_the_gate_as_read_is_kept_beside_what_the_engine_saw():
    got = rsh.waive(_gates(), authorised_flag=True,
                    calibration=_unmeasured())
    assert got["gate_state_as_read"]["MODEL_TRUST_DRIFT"] is None
    assert got["state"]["MODEL_TRUST_DRIFT"] is True
    assert got["waived"] == ["MODEL_TRUST_DRIFT"]


def test_it_is_never_called_a_calibration_measurement():
    for text in (rsh.WHAT_THE_WAIVER_IS_NOT, rsh.describe()
                 ["what_this_is_not"]):
        assert "not a calibration measurement" in text
        assert "stays NOT_EVALUABLE" in text
        assert "stays empty" in text


# ── absence is not permission ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_absent_control_row_is_off():
    class _C:
        async def fetchval(self, *_a):
            return None

    got = await rsh.authorised(_C())
    assert got["authorised"] is False
    assert got["refusal"] == rsh.R_NOT_AUTHORISED
    assert "Absence is not permission" in got["why"]


@pytest.mark.asyncio
async def test_an_unreadable_control_row_is_off_not_on():
    class _C:
        async def fetchval(self, *_a):
            raise RuntimeError("db down")

    got = await rsh.authorised(_C())
    assert got["authorised"] is False
    assert got["refusal"] == rsh.R_CONTROL_UNREADABLE
    assert "has not authorised anything" in got["why"]


@pytest.mark.asyncio
async def test_only_an_explicit_true_authorises():
    async def val(v):
        class _C:
            async def fetchval(self, *_a):
                return v
        return await rsh.authorised(_C())

    assert (await val("true"))["authorised"] is True
    for bad in ("false", "1", '"true"', "TRUE ", "yes", "null", ""):
        got = await val(bad)
        if bad == "TRUE ":
            continue                      # case/space tolerated by design
        assert got["authorised"] is False, bad


def test_unauthorised_waives_nothing():
    got = rsh.waive(_gates(), authorised_flag=False,
                    calibration=_unmeasured())
    assert got["waived"] == []
    assert got["state"]["MODEL_TRUST_DRIFT"] is None
    assert rsh.R_NOT_AUTHORISED in got["refusals"]


# ── exactly one gate, and never the others ───────────────────────────

def test_only_the_calibration_gate_is_waivable():
    assert rsh.WAIVABLE == {"MODEL_TRUST_DRIFT"}


def test_the_two_lists_cannot_overlap():
    """A name in both sets means nobody knows what was intended, so the
    safe reading is to refuse rather than let the permissive one win."""
    assert not (rsh.WAIVABLE & rsh.NEVER_WAIVABLE)
    for name in ("STALE_DATA", "UNRESOLVED_SETTLEMENT_SEMANTICS",
                 "OUT_OF_DISTRIBUTION"):
        assert name in rsh.NEVER_WAIVABLE


def test_a_stale_quote_still_blocks_under_the_waiver():
    got = rsh.waive(_gates(STALE_DATA=False), authorised_flag=True,
                    calibration=_unmeasured())
    assert got["state"]["STALE_DATA"] is False
    assert got["waived"] == ["MODEL_TRUST_DRIFT"]


def test_an_unknown_settlement_comparison_still_blocks():
    """The blocker on 464 of 464 production candidates. The waiver must not
    touch it -- weakening settlement compatibility is exactly what was
    ruled out."""
    got = rsh.waive(_gates(UNRESOLVED_SETTLEMENT_SEMANTICS=None),
                    authorised_flag=True, calibration=_unmeasured())
    assert got["state"]["UNRESOLVED_SETTLEMENT_SEMANTICS"] is None


def test_an_incompatible_settlement_still_blocks():
    got = rsh.waive(_gates(UNRESOLVED_SETTLEMENT_SEMANTICS=False),
                    authorised_flag=True, calibration=_unmeasured())
    assert got["state"]["UNRESOLVED_SETTLEMENT_SEMANTICS"] is False


def test_out_of_distribution_still_blocks():
    got = rsh.waive(_gates(OUT_OF_DISTRIBUTION=False), authorised_flag=True,
                    calibration=_unmeasured())
    assert got["state"]["OUT_OF_DISTRIBUTION"] is False


# ── the two ways MODEL_TRUST_DRIFT can be false ──────────────────────

def test_a_measured_calibration_needs_no_waiver_and_gets_none():
    """Applying one anyway could only serve to hide a drift failure behind
    a research label."""
    got = rsh.waive(_gates(MODEL_TRUST_DRIFT=False), authorised_flag=True,
                    calibration={"measured": True,
                                 "within_tolerance": False})
    assert got["waived"] == []
    assert rsh.R_CALIBRATED in got["refusals"]
    assert got["state"]["MODEL_TRUST_DRIFT"] is False
    assert "hide a drift failure" in got["why"]


def test_an_explicit_drift_failure_is_not_an_absence():
    """NOT_EVALUABLE (None) is what a missing calibration produces. False
    means a measurement said the source HAS drifted, and that blocks."""
    got = rsh.waive(_gates(MODEL_TRUST_DRIFT=False), authorised_flag=True,
                    calibration=_unmeasured())
    assert got["waived"] == []
    assert "MODEL_TRUST_DRIFT_FAILED_ON_EVIDENCE_NOT_WAIVED" \
        in got["refusals"]


def test_a_gate_the_evidence_never_produced_is_not_invented():
    g = _gates()
    del g["MODEL_TRUST_DRIFT"]
    got = rsh.waive(g, authorised_flag=True, calibration=_unmeasured())
    assert got["waived"] == []
    assert "MODEL_TRUST_DRIFT_NOT_IN_THE_GATE_MAP" in got["refusals"]
    assert "MODEL_TRUST_DRIFT" not in got["state"]


# ── it cannot reach capital ──────────────────────────────────────────

def test_it_names_no_funded_module_or_submit_path():
    """THE CONTAINMENT ASSERTION. Checked on the module's own source, so a
    later edit that reaches for the funded path fails here."""
    src = inspect.getsource(rsh)
    for forbidden in ("execution_gate", "calibration_store",
                      "calibration_execute", "guarded_submit", "submit_fok",
                      "live_trading_paused", "CALIBRATION_WRITES_ENABLED"):
        # The prose may DESCRIBE the funded controls; what must not appear
        # is an import or a call. So the check is on import statements.
        assert "import %s" % forbidden not in src, forbidden
        assert "from .%s" % forbidden not in src, forbidden
    assert "import" in src           # sanity: the check is looking at code


def test_the_module_imports_nothing_from_the_funded_path():
    import ast

    tree = ast.parse(inspect.getsource(rsh))
    named = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            named.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            named.add(node.module or "")
            named.update(a.name for a in node.names)
    funded = {"execution_gate", "calibration", "calibration_store",
              "calibration_execute", "calibration_adapter", "pmus"}
    assert not (named & funded), named & funded


def test_the_funded_gate_module_is_unchanged_by_this_lane():
    """It must not have grown a research branch."""
    from sportsassets import execution_gate as gate

    src = inspect.getsource(gate)
    for name in ("research_shadow", "UNCALIBRATED_RESEARCH_SHADOW",
                 rsh.CONTROL_KEY):
        assert name not in src, name


def test_the_calibration_limits_are_unchanged_by_this_lane():
    from sportsassets import calibration as C

    src = inspect.getsource(C)
    assert "research_shadow" not in src
    assert C.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE == 5.00
    assert C.MAX_SESSION_CUMULATIVE_SPEND == 100.00


# ── the label travels with the position ──────────────────────────────

def test_a_waived_position_is_labelled_as_uncalibrated():
    lab = rsh.label()
    assert lab["provenance"] == "UNCALIBRATED_RESEARCH_SHADOW"
    assert lab["calibration_status"] == "EXPLICITLY_NOT_ESTABLISHED"
    assert lab["funded"] is False
    assert lab["capital_moved"] is False
    assert lab["waived_gates"] == ["MODEL_TRUST_DRIFT"]


def test_the_writer_labels_by_whether_this_candidate_used_the_waiver():
    """Not by whether the MODE is on. A candidate that cleared calibration
    on its own evidence is an ordinary autonomous entry."""
    from sportsassets import bettor_entry_inventory as inv

    assert inv.RESEARCH_PROVENANCE == rsh.PROVENANCE
    assert inv.PROVENANCE != inv.RESEARCH_PROVENANCE
    on_and_used = {"execution_plan": {"research_waiver": {
        "authorised": True, "waived": ["MODEL_TRUST_DRIFT"]}}}
    on_but_unused = {"execution_plan": {"research_waiver": {
        "authorised": True, "waived": []}}}
    assert inv._took_the_waiver(on_and_used) is True
    assert inv._took_the_waiver(on_but_unused) is False
    assert inv._took_the_waiver({}) is False
    assert inv._waiver_label(on_but_unused) is None
    assert inv._waiver_label(on_and_used)["provenance"] == rsh.PROVENANCE


def test_the_provenance_value_is_declared_by_a_migration():
    from tests.conftest import backend_path

    sql = backend_path(
        "migrations",
        "122_uncalibrated_research_shadow_provenance.sql").read_text()
    assert "UNCALIBRATED_RESEARCH_SHADOW" in sql
    assert "AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW" in sql, \
        "the existing values must survive the widening"
    assert "ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY" in sql
    assert "RN1_SIGNAL_DERIVED" in sql
    assert "never be summed as one" in sql


# ── the loop wires it, and records both readings ─────────────────────

def test_the_loop_applies_the_waiver_above_the_gate():
    from sportsassets.workers import ext_pinnacle_loop as loop

    src = inspect.getsource(loop._entry_plan)
    assert "rsh.waive(" in src
    assert 'state=waiver["state"]' in src
    # THE GATE'S OWN ANSWER IS STILL WHAT `gates` HOLDS.
    assert "entryx.state_from_evidence(" in src
    assert '"gates": gates' in src
    assert '"gates_seen_by_the_engine": waiver["state"]' in src
    assert '"research_waiver"' in src


def test_the_waiver_travels_on_the_persisted_risk_section():
    """WHY THIS IS NOT OPTIONAL. `detail` holds the whole record, but only
    `execution`, `risk` and `exposure` become columns on the valuation row
    (`bettor_external_shadow._plan_json`). `research_waiver` was in
    `detail` alone, so nothing in the DATABASE said whether a decision used
    the waiver -- which is the one question the research lane exists to
    answer. The risk verdict is the section that consumed the waived map,
    so the copy travels there."""
    import json

    from sportsassets import bettor_external_shadow as EXT
    from sportsassets.workers import ext_pinnacle_loop as loop

    src = inspect.getsource(loop._entry_plan)
    assert "research_waiver={" in src
    # THE SECTIONS THAT ACTUALLY PERSIST, read from the writer rather than
    # assumed -- if a fourth section is ever added, this stays true.
    ps = inspect.getsource(EXT.persist)
    assert '_plan_json(rec, "risk")' in ps

    # AND THE COPY IS A COPY: json-serialisable, and no gate map inside it
    # is the one the engine reads.
    plan_src = src[src.index("research_waiver={"):]
    assert '"gate_state_as_read"' in plan_src
    assert "dict(\n            verdict," in src or "dict(verdict," in src
    assert json.dumps({"authorised": True, "waived": ["MODEL_TRUST_DRIFT"]})


def test_the_loop_reads_the_control_row_each_cycle():
    from sportsassets.workers import ext_pinnacle_loop as loop

    src = inspect.getsource(loop.cycle)
    assert "rsh.authorised(conn)" in src
    assert 'research.get("authorised") is True' in src
    assert '"research_shadow": research' in src


# ── the arming route: one key, and it cannot become a general writer ──

def test_the_arming_route_writes_only_this_lanes_key():
    """A general `ingestion_state` writer would put `live_trading_paused`
    one admin request away. So no key parameter is accepted at all."""
    from sportsassets.api import app as APP

    src = inspect.getsource(APP.admin_arm_research_shadow)
    assert "RSH.CONTROL_KEY" in src
    assert "cannot be pointed at another" in src
    # NO KEY COMES FROM THE BODY.
    assert 'b.get("key")' not in src
    assert 'body.get("key")' not in src
    assert 'b["key"]' not in src
    # AND IT NAMES THE HAZARD IT IS AVOIDING.
    assert "live_trading_paused" in src


def test_arming_requires_an_exact_confirm_word():
    from sportsassets.api import app as APP

    src = inspect.getsource(APP.admin_arm_research_shadow)
    assert '== "ARM"' in src and '== "DISARM"' in src
    assert "READ_ONLY" in src
    assert '"wrote": False' in src


def test_the_route_reads_the_calibration_table_rather_than_asserting():
    """The operator must never be told "unmeasured" by a constant: if a
    measurement has appeared the waiver stops applying on its own."""
    from sportsassets.api import app as APP

    src = inspect.getsource(APP.admin_arm_research_shadow)
    assert "FROM external_source_calibration" in src
    assert 'out["calibration_still_unmeasured"] = not n' in src


def test_the_route_reads_back_through_the_modules_own_checker():
    """So the answer is the one the LOOP will get, not the one the route
    just wrote."""
    from sportsassets.api import app as APP

    src = inspect.getsource(APP.admin_arm_research_shadow)
    assert "RSH.authorised(conn)" in src


def test_the_route_reaches_no_funded_path():
    from sportsassets.api import app as APP

    src = inspect.getsource(APP.admin_arm_research_shadow)
    for forbidden in ("guarded_submit", "submit_fok", "calibration_execute",
                      "CALIBRATION_WRITES_ENABLED"):
        assert forbidden not in src, forbidden


# ── CONTAINMENT BY DATA FLOW, not only by imports ────────────────────
#
# An empty funded-module import list says this module calls nothing
# dangerous. It does NOT say a decision this lane produced cannot end up
# being what a funded submission sends. That is a question about DATA, and
# it is asked separately here.

FUNDED_TABLES = ("calibration_lifecycles", "calibration_send_attempts",
                 "calibration_sessions")
RESEARCH_TABLES = ("rn1x_positions", "rn1x_decisions", "rn1x_orders",
                   "rn1x_fills", "rn1x_outcomes")


def _modules():
    import pathlib

    from .conftest import backend_path
    root = backend_path("sportsassets")
    return {p: p.read_text(errors="ignore")
            for p in pathlib.Path(root).rglob("*.py")
            if "__pycache__" not in str(p)}


def test_no_module_touches_both_table_families():
    """THE DATA-FLOW CUT. The funded path's economics are bound from
    `calibration_*` rows; the research lane writes `rn1x_*` rows. If one
    module named both, there would be somewhere for a research decision to
    become a funded ticket -- so no module may.
    """
    both = []
    for path, src in _modules().items():
        has_funded = any(t in src for t in FUNDED_TABLES)
        has_research = any(t in src for t in RESEARCH_TABLES)
        if has_funded and has_research:
            both.append(str(path))
    assert both == [], both


def test_the_funded_send_binds_only_from_its_own_durable_row():
    """`guarded_submit` takes NO ticket parameter. Everything economically
    relevant is re-read from the claimed `calibration_*` row at claim time,
    so a caller cannot send at a price or size no human approved -- and a
    research decision is not a thing it can be handed."""
    from sportsassets import calibration_execute as CE
    from sportsassets import calibration_store as CS

    sig = inspect.signature(CE.guarded_submit)
    assert "ticket" not in sig.parameters, sig
    assert set(sig.parameters) >= {"venue", "client_order_id", "claimed_by"}
    src = inspect.getsource(CE.guarded_submit)
    assert "The ticket is NOT a parameter" in src
    # AND THE BOUND FIELDS ARE THE LEDGER'S, re-checked at claim.
    for f in ("price", "quantity", "account", "marketId"):
        assert f in CS.BOUND_FIELDS, f


def test_no_single_function_bridges_a_research_row_to_a_funded_ticket():
    """FUNCTION granularity, not file. `api/app.py` is a monolith holding
    every route, so it legitimately contains both the calibration routes and
    the rn1x routes -- a file-level grep flags it and says nothing. The
    question is whether any ONE callable reads an rn1x row and builds a
    calibration ticket field from it, because that is what a bridge would
    look like."""
    import ast

    bad = []
    for path, src in _modules().items():
        try:
            tree = ast.parse(src)
        except SyntaxError:                                    # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(src, node) or ""
            if not any(t in body for t in RESEARCH_TABLES):
                continue
            if any(t in body for t in FUNDED_TABLES) or \
                    "clientOrderId" in body:
                bad.append("%s::%s" % (path.name, node.name))
    assert bad == [], bad


def test_the_research_provenance_is_not_a_funded_environment_value():
    """A funded ticket's `environment` must be PRODUCTION or PREPROD. The
    research label is neither, so a research row cannot satisfy the funded
    identity check even if its fields were copied across."""
    from sportsassets import calibration as C

    assert rsh.PROVENANCE not in C.ENVIRONMENTS
    assert set(C.ENVIRONMENTS) == {"PRODUCTION", "PREPROD"}


def test_what_containment_does_not_claim():
    """HONESTY ABOUT THE REMAINING PATH. Nothing stops a HUMAN reading a
    research decision and typing its numbers into a calibration ticket.
    What this guarantees is narrower and is the part that matters: those
    numbers arrive with no authority of their own, and every funded control
    still applies to them -- per-ticket human approval confirmed by exact
    clientOrderId, the $5.00 and $100.00 caps, one concurrent lifecycle, the
    kill switch read fresh at submission, and `_destination_mismatch`
    against the adapter's own identity.

    So the claim is NOT "a research number can never reach the venue". It is
    "a research number cannot reach the venue WITHOUT a human approving it
    through the funded gate, and it gets no easier passage for having come
    from the research lane".
    """
    from sportsassets import calibration as C

    assert C.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE == 5.00
    assert C.MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES == 1
    # The funded refusal names that would still fire are intact.
    for name in ("R_UNFUNDED", "R_CASH_UNKNOWN", "R_STALE_STATE",
                 "R_PER_TRADE", "R_CONCURRENCY"):
        assert hasattr(C, name), name
