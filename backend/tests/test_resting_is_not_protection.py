"""C28 P0: A RESTING SELL / HEDGE / ORDER IS NOT PROTECTION.

Only FILLED quantity counts as realized / matched protection. Every surface
distinguishes PROPOSED, SUBMITTED, RESTING, PARTIAL, FILLED, CANCELLED,
REJECTED, EXPIRED (and an explicit UNKNOWN that is never FILLED), mapped by
ONE shared pure function, `sportsassets.order_state_truth.order_state`.

Pinned here, against the real pure readers (no database):
  * the mapping: every raw state of every state machine, read off its own
    migration CHECK, maps to a canonical state; unknown -> UNKNOWN;
  * a resting order never appears as filled protection in any reader output
    (order_state_truth, position_rooms rooms / list rows / Xavier panel,
    execmirror_view's protection ledger, xavier_standing_view);
  * a partial fill counts only its filled part;
  * the conditional floor is labelled IF_FILLED and excluded from the
    realized floor;
  * unprotected qty = position qty - filled protection qty;
  * cancelled / rejected / expired orders contribute 0;
  * the owner's required rendering line, verbatim;
  * venue independence: KALSHI — NOT_CONNECTED from Kalshi's own control
    row, never inferred from Polymarket US;
  * the frontend never labels standing quantity as protection.
Every row below is FIXTURE data.
"""
from __future__ import annotations

import ast
import copy
import pathlib
import re

import pytest

from sportsassets import order_state_truth as OST
from sportsassets import position_rooms as P
from tests import position_room_fixtures as F

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIG = ROOT / "backend" / "migrations"


def _rooms(raw):
    return {r["group_key"]: r for r in P.build_rooms(raw)}


def _evt(raw=None):
    return _rooms(raw or F.raw_paper())["PAPER:EVT:" + F.EVENT]


def _check_list(migration: str, constraint: str) -> set:
    sql = (MIG / migration).read_text()
    m = re.search(r"%s\s+CHECK\s*\(\s*state\s+IN\s*\((.*?)\)\)" % constraint,
                  sql, re.S)
    assert m, (migration, constraint)
    return set(re.findall(r"'([A-Z_]+)'", m.group(1)))


def _funded_states() -> set:
    sql = (MIG / "125_funded_pilot_intents_and_fills.sql").read_text()
    m = re.search(r"state\s+text NOT NULL\s+CHECK \(state IN \((.*?)\)\),",
                  sql, re.S)
    assert m
    return set(re.findall(r"'([A-Z_]+)'", m.group(1)))


# ═════════════════════════════════════════════════════════════════════
# 1 · THE ONE MAPPING
# ═════════════════════════════════════════════════════════════════════

def test_the_eight_canonical_states_and_an_explicit_unknown():
    assert OST.CANONICAL_STATES == (
        "PROPOSED", "SUBMITTED", "RESTING", "PARTIAL", "FILLED",
        "CANCELLED", "REJECTED", "EXPIRED")
    assert OST.ALL_STATES == OST.CANONICAL_STATES + ("UNKNOWN",)
    assert "UNKNOWN" not in OST.FILL_BEARING_STATES
    assert set(OST.MEANING) == set(OST.ALL_STATES)


@pytest.mark.parametrize("source,states", [
    ("paper_orders", lambda: _check_list(
        "171_paper_account_and_ledger.sql", "paper_orders_state_ck")),
    ("execmirror_orders", lambda: _check_list(
        "192_execution_mirror.sql", "execmirror_orders_state_ck")),
    ("kalshi_live_intents", lambda: _check_list(
        "196_kalshi_smalllive.sql", "kalshi_intents_state_ck")),
    ("bettor_funded_intents", _funded_states),
])
def test_every_raw_state_of_every_machine_is_mapped(source, states):
    raw = states()
    assert raw, source
    assert set(OST.RAW_MAPS[source]) == raw, (source, raw)
    for r in raw:
        t = OST.order_state(r, source=source, qty=10, filled_qty=0)
        assert t["state"] in OST.ALL_STATES, (source, r)


def test_the_exact_mapping():
    m = {s: {r: st for r, (st, _sub) in OST.RAW_MAPS[s].items()}
         for s in OST.RAW_MAPS}
    assert m["paper_orders"] == {
        "PENDING_SIMULATION": "SUBMITTED", "RESTING": "RESTING",
        "PARTIALLY_FILLED": "PARTIAL", "FILLED": "FILLED",
        "EXPIRED": "EXPIRED", "CANCEL_PENDING": "RESTING",
        "CANCELED": "CANCELLED", "REJECTED": "REJECTED"}
    assert m["execmirror_orders"] == m["kalshi_live_intents"] == {
        "PLANNED": "PROPOSED", "SUBMITTING": "SUBMITTED",
        "UNKNOWN": "UNKNOWN", "OPEN": "RESTING",
        "PARTIALLY_FILLED": "PARTIAL", "FILLED": "FILLED",
        "CANCEL_REQUESTED": "RESTING", "CANCELLED": "CANCELLED",
        "EXPIRED": "EXPIRED", "REJECTED": "REJECTED",
        "EXCLUDED": "REJECTED"}
    assert m["bettor_funded_intents"] == {
        "INTENT_RECORDED": "PROPOSED", "SEND_ATTEMPTED": "SUBMITTED",
        "ACKNOWLEDGED": "RESTING", "PARTIALLY_FILLED": "PARTIAL",
        "FILLED": "FILLED", "CANCELLED": "CANCELLED",
        "REJECTED": "REJECTED", "ABANDONED": "REJECTED",
        "UNRESOLVED": "UNKNOWN"}
    # a requested cancel can still fill; it says so
    t = OST.order_state("CANCEL_REQUESTED", source="execmirror_orders",
                        qty=5, filled_qty=2)
    assert (t["state"], t["sub_state"]) == ("PARTIAL", "CANCEL_PENDING")
    assert t["can_still_fill"] and t["standing_qty"] == 3.0


@pytest.mark.parametrize("raw,source", [
    ("SOMETHING_NEW", "paper_orders"), ("", "execmirror_orders"),
    (None, "kalshi_live_intents"), ("FILLED", "a_table_nobody_mapped"),
    ("FILLED", None), ("filled", "paper_orders")])
def test_unknown_states_are_explicit_unknown_never_filled(raw, source):
    t = OST.order_state(raw, source=source, qty=100, filled_qty=100)
    assert t["state"] == "UNKNOWN"
    assert t["is_fill"] is False and t["filled_qty"] == 0.0
    assert t["unknown_state_fill_excluded"] is True
    assert OST.canonical_order_state(raw, source=source) == "UNKNOWN"
    # and the position room's own mapping is the same function
    assert P.canonical_state(raw, table=source or "x") == "UNKNOWN"


def test_a_resting_row_carrying_a_fill_is_partial_never_nothing_filled():
    t = OST.order_state("RESTING", source="paper_orders", qty=10,
                        filled_qty=4)
    assert t["state"] == "PARTIAL" and t["filled_qty"] == 4.0
    assert t["standing_qty"] == 6.0


def test_the_mapping_module_is_pure_and_import_free():
    src = (ROOT / "backend" / "sportsassets" / "order_state_truth.py"
           ).read_text()
    tree = ast.parse(src)
    imports = [n for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert all(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               for n in imports), "order_state_truth must import nothing"
    assert "await" not in src and "conn." not in src


def test_raw_state_lists_for_sql_come_from_the_mapping():
    assert OST.raw_states("execmirror_orders", OST.STANDING_STATES) == [
        "CANCEL_REQUESTED", "OPEN", "PARTIALLY_FILLED"]
    assert OST.raw_states("paper_orders", OST.PENDING_STATES) == [
        "PENDING_SIMULATION"]
    fb = OST.raw_states("execmirror_orders", OST.FILL_BEARING_STATES)
    assert "UNKNOWN" not in fb and "REJECTED" not in fb \
        and "EXCLUDED" not in fb and "PLANNED" not in fb


# ═════════════════════════════════════════════════════════════════════
# 2 · THE PROTECTION LEDGER (pure)
# ═════════════════════════════════════════════════════════════════════

def _o(ref, raw, qty, filled, *, role="STANDING_PROTECTION",
       direction="SELL", limit=0.51, source="paper_orders"):
    return {"order_ref": ref, "role": role, "direction": direction,
            "qty": qty, "filled_qty": filled, "limit": limit,
            "raw_state": raw, "source": source}


def test_a_resting_order_is_never_filled_protection():
    ps = OST.protection_summary(held_qty=2083, orders=[
        _o("r1", "RESTING", 2083, 0)])
    assert ps["filled_protection_qty"] == 0.0
    assert ps["standing_order_qty"] == 2083.0
    assert ps["unprotected_qty"] == 2083.0
    assert ps["realized_protection_usd"] == 0.0
    # a hedge that rests is no more protection than a sale that rests
    ps = OST.protection_summary(held_qty=1000, orders=[
        _o("h1", "OPEN", 1000, 0, role="HEDGE", direction="BUY",
           source="execmirror_orders")])
    assert ps["filled_protection_qty"] == 0.0
    assert ps["unprotected_qty"] == 1000.0


def test_partial_fill_counts_only_the_filled_part():
    # 2,083 bought; the protective sale filled 1,000 (so 1,083 still held)
    ps = OST.protection_summary(held_qty=1083, orders=[
        _o("p1", "PARTIALLY_FILLED", 2083, 1000)])
    assert ps["filled_protection_qty"] == 1000.0
    assert ps["standing_order_qty"] == 1083.0
    assert ps["position_qty"] == 2083.0
    assert ps["unprotected_qty"] == 1083.0
    # a partial hedge: only its 150 filled contracts protect
    ps = OST.protection_summary(held_qty=1000, orders=[
        _o("h", "PARTIALLY_FILLED", 400, 150, role="HEDGE", direction="BUY")])
    assert ps["filled_protection_qty"] == 150.0
    assert ps["standing_order_qty"] == 250.0
    assert ps["unprotected_qty"] == 850.0


@pytest.mark.parametrize("held,orders,position,filled", [
    (500, [_o("a", "FILLED", 500, 500)], 1000, 500),
    (700, [_o("a", "CANCELED", 400, 300)], 1000, 300),
    (1000, [_o("h", "FILLED", 600, 600, role="HEDGE", direction="BUY")],
     1000, 600),
    (1000, [], 1000, 0),
])
def test_unprotected_is_position_minus_filled_protection(held, orders,
                                                         position, filled):
    ps = OST.protection_summary(held_qty=held, orders=orders)
    assert ps["position_qty"] == position
    assert ps["filled_protection_qty"] == filled
    assert ps["unprotected_qty"] == position - filled


@pytest.mark.parametrize("raw,source", [
    ("CANCELED", "paper_orders"), ("REJECTED", "paper_orders"),
    ("EXPIRED", "paper_orders"), ("CANCELLED", "execmirror_orders"),
    ("REJECTED", "execmirror_orders"), ("EXCLUDED", "execmirror_orders"),
    ("EXPIRED", "kalshi_live_intents"), ("ABANDONED",
                                         "bettor_funded_intents")])
def test_cancelled_rejected_expired_contribute_zero(raw, source):
    ps = OST.protection_summary(held_qty=1000, orders=[
        _o("x", raw, 1000, 0, source=source)])
    assert ps["filled_protection_qty"] == 0.0
    assert ps["standing_order_qty"] == 0.0
    assert ps["pending_order_qty"] == 0.0
    assert ps["unprotected_qty"] == 1000.0
    assert ps["conditional_floor_if_filled_usd"] is None
    # a REJECTED row can never contribute, whatever filled_qty it carries
    if raw in ("REJECTED", "EXCLUDED", "ABANDONED"):
        ps = OST.protection_summary(held_qty=1000, orders=[
            _o("x", raw, 1000, 1000, source=source)])
        assert ps["filled_protection_qty"] == 0.0


def test_an_unknown_state_contributes_nothing_and_is_named():
    ps = OST.protection_summary(held_qty=1000, orders=[
        _o("u", "UNKNOWN", 1000, 1000, source="execmirror_orders")])
    assert ps["filled_protection_qty"] == 0.0
    assert ps["unknown_state_fills_excluded"] == ["u"]
    assert ps["unprotected_qty"] == 1000.0


def test_conditional_floor_is_if_filled_and_never_the_realized_floor():
    ps = OST.protection_summary(
        held_qty=2083, orders=[_o("r1", "RESTING", 2083, 0)],
        conditional_floor_if_filled_usd=32.01, realized_floor_usd=-1030.32)
    assert ps["conditional_floor_if_filled_usd"] == 32.01
    assert ps["conditional_floor_label"] == "IF_FILLED"
    assert ps["realized_floor_usd"] == -1030.32
    assert ps["realized_floor_excludes"] == "IF_FILLED"
    # nothing standing: no conditional floor at all
    ps = OST.protection_summary(
        held_qty=2083, orders=[_o("c", "CANCELED", 2083, 0)],
        conditional_floor_if_filled_usd=32.01)
    assert ps["conditional_floor_if_filled_usd"] is None


# ═════════════════════════════════════════════════════════════════════
# 3 · THE POSITION ROOM: the owner's required line, verbatim
# ═════════════════════════════════════════════════════════════════════

def _one_resting_sell_raw(state="RESTING", filled=0):
    """FIXTURE: 2,083 Yankees bought at 49c + $9.65 fees (average cost
    49.4633c incl. fees); a protective sale rests for all 2,083 at 51c."""
    raw = F.raw_paper()
    t = F.NOW - 3600
    raw["orders"] = [
        F._o("fx_entry", group=F.G_NYY, role="ENTRY", direction="BUY",
             side="SHORT", qty=2083, filled=2083, limit=0.49,
             state="FILLED", raw="FILLED", at=t, avg=0.49, fees=9.65),
        F._o("fx_prot", group=F.G_NYY, role="STANDING_PROTECTION",
             direction="SELL", side="SHORT", qty=2083, filled=filled,
             limit=0.51, state="IGNORED_BY_THE_ROOM", raw=state, at=t + 60)]
    raw["fills"] = [
        {"fill_ref": "fx_f1", "order_ref": "fx_entry", "group_id": F.G_NYY,
         "direction": "BUY", "holding_side": "SHORT", "slug": F.SLUG,
         "qty": 2083.0, "price": 0.49, "fee_usd": 9.65, "at": t + 2,
         "source": "paper_fills (SIMULATOR)"}]
    if filled:
        raw["fills"].append(
            {"fill_ref": "fx_f2", "order_ref": "fx_prot", "group_id": F.G_NYY,
             "direction": "SELL", "holding_side": "SHORT", "slug": F.SLUG,
             "qty": float(filled), "price": 0.51, "fee_usd": 0.0,
             "at": t + 90, "source": "paper_fills (SIMULATOR)"})
    return raw


def test_the_owners_required_rendering_line():
    r = _evt(_one_resting_sell_raw())
    pr = r["protection"]
    assert pr["line"] == (
        "RESTING SELL: 2,083 @ $0.51 / FILLED: 0 / CONDITIONAL FLOOR IF "
        "FILLED: $32.01 / REALIZED PROTECTION: $0.00 / UNPROTECTED QTY: "
        "2,083")
    assert pr["position_qty"] == 2083.0
    assert pr["unprotected_qty"] == 2083.0
    assert pr["standing_order_qty"] == 2083.0
    assert pr["filled_protection_qty"] == 0.0
    assert pr["conditional_floor_if_filled_usd"] == pytest.approx(32.01)
    assert pr["conditional_floor_label"] == "IF_FILLED"
    # the realized floor is the filled holding's: a Red Sox win loses it all
    assert pr["realized_floor_usd"] == pytest.approx(-1030.32)
    assert pr["realized_floor_usd"] == r["economic"]["locked_pnl_usd"]
    assert pr["current_worst_case_exposure_usd"] == pytest.approx(1030.32)
    ex = pr["current_executable_exit"]
    assert ex["available"] and ex["legs"][0]["price"] == 0.64
    # the top level bids only 1,800: the 283 beyond it are named, not priced
    assert ex["proceeds_at_top_level_usd"] == pytest.approx(1800 * 0.64)
    assert ex["legs"][0]["beyond_top_level_qty"] == 283.0
    assert ex["covers_whole_position"] is False
    # the room's state field came from the raw state, not the loader's
    o = next(o for o in r["orders"] if o["order_ref"] == "fx_prot")
    assert o["state"] == "RESTING"
    assert o["note"] == "RESTING - NOT PROTECTION UNTIL FILLED"
    assert o["counts_as_filled_qty"] == 0.0


def test_the_room_partial_counts_only_the_filled_part():
    r = _evt(_one_resting_sell_raw(state="PARTIALLY_FILLED", filled=1000))
    pr = r["protection"]
    assert pr["filled_protection_qty"] == 1000.0
    assert pr["standing_order_qty"] == 1083.0
    assert pr["position_qty"] == 2083.0
    assert pr["unprotected_qty"] == 1083.0
    assert pr["line"].startswith("PARTIAL SELL: 1,083 @ $0.51 / FILLED: "
                                 "1,000 / ")
    # realized protection: 1,000 x (51c - 49.4633c avg cost incl. fees)
    assert pr["realized_protection_usd"] == pytest.approx(
        1000 * (0.51 - (2083 * 0.49 + 9.65) / 2083), abs=1e-3)
    o = next(o for o in r["orders"] if o["order_ref"] == "fx_prot")
    assert "ONLY THE FILLED 1,000 COUNTS" in o["note"]


@pytest.mark.parametrize("raw", ["CANCELED", "EXPIRED", "REJECTED"])
def test_the_room_terminal_unfilled_orders_contribute_zero(raw):
    r = _evt(_one_resting_sell_raw(state=raw))
    pr = r["protection"]
    assert pr["filled_protection_qty"] == 0.0
    assert pr["standing_order_qty"] == 0.0
    assert pr["unprotected_qty"] == 2083.0
    assert pr["conditional_floor_if_filled_usd"] is None
    assert r["standing_orders"] == []


def test_no_reader_output_shows_resting_as_filled_protection():
    """The owner's fixture room: FILLED entry, RESTING protective sale,
    PARTIAL hedge (150 of 400), CANCELLED old sale, PROPOSED buy."""
    r = _evt()
    pr = r["protection"]
    assert pr["filled_protection_qty"] == 150.0      # the hedge's fills only
    assert pr["standing_order_qty"] == 1000.0 + 250.0
    assert pr["unprotected_qty"] == 1000.0 - 150.0
    assert pr["hedge_legs"] == ["%s|%s|LONG" % (F.G_BOS, F.SLUG)]
    # the list row carries the same ledger
    row = P.summarize(r)
    assert row["protection"]["filled_protection_qty"] == 150.0
    assert row["protection"]["standing_order_qty"] == 1250.0
    assert row["protection"]["unprotected_qty"] == 850.0
    # the Xavier panel of the Yankees group: nothing of the resting sale
    x = next(p for p in r["xavier"] if p["group_id"] == F.G_NYY)
    assert x["protection"]["filled_protection_qty"] == 0.0
    assert x["protection"]["unfilled_resting_protection_qty"] == 1000.0
    assert x["protection"]["unprotected_qty"] == 1000.0
    assert x["protection"]["conditional_floor_label"] == "IF_FILLED"
    prot = next(a for a in x["alternatives"] if a["action"] == "PROTECTION")
    assert "NOT PROTECTION UNTIL FILLED" in prot["note"]
    # the scenarios: the standing-filled column is labelled conditional
    sc = r["scenarios"]
    assert sc["conditional"]["with_standing_filled"] == "IF_FILLED"
    assert "IF_FILLED" in sc["with_standing_filled_label"]
    assert sc["current"]["locked_pnl_usd"] == pr["realized_floor_usd"]
    assert pr["conditional_floor_if_filled_usd"] != pr["realized_floor_usd"]
    # the state of every order is one of the canonical states
    assert {o["state"] for o in r["orders"]} <= set(OST.ALL_STATES)
    states = {o["order_ref"]: o["state"] for o in r["orders"]}
    assert states == {"paper_ord_fx_entry": "FILLED",
                      "paper_ord_fx_prot_old": "CANCELLED",
                      "paper_ord_fx_prot": "RESTING",
                      "paper_ord_fx_hedge": "PARTIAL",
                      "paper_dec_fx_rsox": "PROPOSED"}


def test_a_stale_loader_state_is_overridden_by_the_raw_state():
    """The room maps every order from its own raw state: a loader that
    wrote FILLED on a RESTING row cannot make it protection."""
    raw = copy.deepcopy(_one_resting_sell_raw())
    for o in raw["orders"]:
        if o["order_ref"] == "fx_prot":
            o["state"] = "FILLED"
    pr = _evt(raw)["protection"]
    assert pr["filled_protection_qty"] == 0.0
    assert pr["unprotected_qty"] == 2083.0


# ═════════════════════════════════════════════════════════════════════
# 4 · THE OTHER READERS
# ═════════════════════════════════════════════════════════════════════

def test_execmirror_view_resting_never_reduces_unprotected():
    from sportsassets import execmirror_view as V
    q = V.protection_quantities(held=1702, resting=1702, filled=1000)
    assert q["unprotected_qty"] == 1702.0      # NOT 0: resting is no cover
    assert q["position_qty"] == 2702.0
    assert q["filled_protection_qty"] == 1000.0
    q = V.protection_quantities(held=3, resting=3, filled=0, pending=0)
    assert q["unprotected_qty"] == 3.0
    q = V.protection_quantities(held=0, resting=0, filled=3)
    assert q["unprotected_qty"] == 0.0 and q["position_qty"] == 3.0
    assert "only FILLED quantity is protection" in V.PROTECTION_RULE


def test_xavier_standing_view_resting_order_counts_nothing():
    from sportsassets.agents import xavier_standing_view as XSV
    c = XSV._canonical({"book_state": "ACKNOWLEDGED", "quantity": 40.0,
                        "filled_qty": 0.0, "limit_price": 0.35})
    assert c["state"] == "RESTING" and c["counts_as_filled_qty"] == 0.0
    assert c["standing_qty"] == 40.0
    assert c["line"] == "RESTING BUY: 40 @ $0.35 / FILLED: 0"
    c = XSV._canonical({"book_state": "PARTIALLY_FILLED", "quantity": 40.0,
                        "filled_qty": 15.0, "limit_price": 0.35})
    assert c["state"] == "PARTIAL" and c["counts_as_filled_qty"] == 15.0
    c = XSV._canonical({"book_state": "UNRESOLVED", "quantity": 40.0,
                        "filled_qty": 40.0})
    assert c["state"] == "UNKNOWN" and c["counts_as_filled_qty"] == 0.0


def test_slack_progress_says_an_entry_order_is_not_a_fill():
    from sportsassets import slack_updates as SU
    from tests.test_slack_management_updates import SNAP
    s = dict(SNAP)
    act = {"decisions": 4, "approved": 2, "entries": 3, "entries_filled": 1,
           "entries_resting": 2, "other_orders": 1, "refusals": [],
           "last_entry_at": None, "unanswered": []}
    txt = SU.progress_text(s, act, None, {}, label="x",
                           with_reconciliation=False)
    assert "3 new entry orders (1 with a fill, 2 still resting or pending " \
           "with nothing filled -- an order is not a fill)" in txt


def test_persona_demonstration_floor_is_labelled_conditional():
    from sportsassets.agents import persona_facts as PF
    f = PF.demonstration_facts()
    floors = [i for i in f.items if str(i.get("field", "")).startswith(
        "floor:")]
    assert floors and all("IF_FILLED" in i["text"] for i in floors)


# ═════════════════════════════════════════════════════════════════════
# 5 · VENUE INDEPENDENCE
# ═════════════════════════════════════════════════════════════════════

def test_kalshi_not_connected_is_shown_and_never_inferred():
    pm = P.venue_connection("POLYMARKET", table_present=True,
                            row={"keyed": True, "enabled": True,
                                 "stopped": False})
    k = P.venue_connection("KALSHI", table_present=True,
                           row={"keyed": False, "enabled": False,
                                "stopped": False})
    assert pm["connected"] is True and pm["display"] == "POLYMARKET US — ENABLED"
    assert k["connected"] is False
    assert k["display"] == "KALSHI — NOT_CONNECTED"
    assert P.venue_connection("KALSHI", table_present=False, row=None)[
        "display"] == "KALSHI — NOT_CONNECTED"
    # the function takes ONE venue's own row: there is no argument through
    # which the other venue could leak in
    import inspect
    assert list(inspect.signature(P.venue_connection).parameters) == [
        "venue", "table_present", "row"]


def test_floor_counts_actual_positions_per_venue_never_summed():
    from sportsassets.api import command_floor as CF
    ac = {"by_venue": {"POLYMARKET": 2, "KALSHI": 0},
          "connected": {"POLYMARKET": True, "KALSHI": False}}
    assert CF._venue_open(ac, "POLYMARKET") == 2
    assert CF._venue_open(ac, "KALSHI") is None      # NOT_CONNECTED, not 0
    ac["connected"]["KALSHI"] = True
    assert CF._venue_open(ac, "KALSHI") == 0


# ═════════════════════════════════════════════════════════════════════
# 6 · THE FRONTEND NEVER LABELS STANDING QUANTITY AS PROTECTION
# ═════════════════════════════════════════════════════════════════════

FRONTEND = ROOT / "frontend" / "public" / "command"
FRONTEND_GLOBS = ("position*.js", "positions*.js", "xavier*.js",
                  "workspace.js", "equity-wall.js", "floor*.js", "live.js",
                  "office.js", "paper.js")
#: a string that names protection beside a standing / resting / pending
#: quantity must say it is NOT protection (or unfilled / until filled)
_PROT = re.compile(r"protect", re.I)
_STANDING = re.compile(r"standing|resting|pending|awaiting|open .*order", re.I)
_NEG = re.compile(r"\bnot\b|unfilled|until|\bno\b", re.I)
_STR = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`[^`]*`")


def frontend_violations(text: str) -> list:
    bad = []
    for m in _STR.finditer(text):
        s = m.group(0)
        if re.fullmatch(r"['\"][A-Z_]+['\"]", s):
            continue        # an enum code (a role name), not a label
        if _PROT.search(s) and _STANDING.search(s) and not _NEG.search(s):
            bad.append(s)
    # a standing / resting quantity field shown under a bare protection label
    for line in text.splitlines():
        if re.search(r"(standing|resting)[a-z_]*_qty", line) and re.search(
                r"['\"][^'\"]*\b[Pp]rotect(ion|ed)\b[^'\"]*['\"]", line) and \
                not _NEG.search(" ".join(_STR.findall(line))):
            bad.append(line.strip())
    return bad


def test_the_frontend_grep_rule_catches_a_violation():
    assert frontend_violations("fact('Standing protection', qty(p.x))")
    assert frontend_violations(
        "fact('Protection', qty(p.standing_resting_qty))")
    assert not frontend_violations(
        "fact('Standing orders (resting, NOT protection until filled)', "
        "qty(p.standing_resting_qty))")


def test_positions_and_xavier_js_never_label_standing_as_protection():
    files = sorted({p for g in FRONTEND_GLOBS for p in FRONTEND.glob(g)})
    assert files, "no frontend file found under %s" % FRONTEND
    bad = {str(p.name): frontend_violations(p.read_text()) for p in files}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, bad
