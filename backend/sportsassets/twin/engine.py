"""THE ECONOMIC DIGITAL TWIN ENGINE. Pure: no I/O, no clock, no randomness.

WHAT IT DOES. The recorded stream (decisions, fills, settlements, books,
probabilities, fees, management actions, Karen blocks, regime states,
shadow allocations; ARCHER / SCOUT rows through their interface views) is
built into recorded POSITIONS and OPPORTUNITIES, then replayed through one
frozen alternate world at a time. Each world decision is made at an instant
t through a TimeView that only exposes records whose `at` <= t; the view
records the latest instant it served (max_input_at) and how many records it
served, and both go into the decision's trace -- the database refuses a
trace with max_input_at > decision_at.

OUTCOMES ARE FOR SCORING ONLY. A settlement payoff is never handed to a
world's decision; it enters only the P&L of the decision afterwards (the
same way the recorded P&L is scored). That is the one place a later fact is
used, and every trace names its P&L basis.

THE RECORDED BASELINE uses the intel layer's attribution identity
(intel.attribution.attribute) for each recorded position's realized P&L, so
the twin and the attribution can never disagree about what happened.

LABELS. The baseline aggregate is labelled with its basis book (PAPER or
ACTUAL), the world aggregate COUNTERFACTUAL, and the comparison is a paired
DIFFERENCE over the subjects both measured -- never a sum, and a PAPER-basis
result is never combined with an ACTUAL-basis one.

SIMPLIFICATIONS (documented in research/pos_twin_audit.md):
  * a world-only entry (a refused decision the world would take) fills its
    planned quantity (capped by the recorded depth within limit) at the
    decision's planned acquisition price and fee rate, and is held to
    settlement (no Xavier management is invented for it);
  * an immediate / Karen exit sells into the latest recorded book at or
    before the exit instant (max age frozen in the spec) at the position's
    entry fee rate, unless Xavier's thesis froze an IMMEDIATE_EXIT plan at
    entry (then that plan, computed with the real fee schedule, is used);
  * sizing scales the recorded P&L linearly (no depth-walk impact model);
  * allocator worlds scale each position's recorded return per dollar of
    entry cost by the world's notional.
"""
from __future__ import annotations

from collections import Counter

from ..intel import attribution as AT
from . import common as C

VERSION = "TWIN_ENGINE_V1"
MAX_TRACES = 100
THESIS_MAX_LAG_S = 300.0

#: the fields of a decision a world may read -- never an outcome
DECISION_KEYS = ("subject_id", "at", "verdict", "p", "p_basis", "d",
                 "d_basis", "fee_pc", "qty", "depth_qty", "strategy", "slug",
                 "side", "sport", "fixture")


# ═════════════════════════════════════════════════════════════════════
# BUILDING THE RECORDED STREAM (pure)
# ═════════════════════════════════════════════════════════════════════

def decision_fields(d: dict) -> dict:
    p, p_basis = AT.decision_probability(d)
    dp, d_basis = AT.decision_price(d)
    econ = C.jload(d.get("economics")) or {}
    acq = econ.get("acquisition") if isinstance(econ, dict) else None
    qty = C.num(d.get("proposed_qty"))
    fees = C.num((acq or {}).get("fees_usd")) if isinstance(acq, dict) else None
    fee_pc = (fees / qty) if (fees is not None and qty) else None
    depth = C.num(econ.get("depth_within_limit")) if isinstance(
        econ, dict) else None
    return {"p": p, "p_basis": p_basis, "d": dp, "d_basis": d_basis,
            "fee_pc": fee_pc, "qty": qty, "depth_qty": depth}


def _sport(val: dict | None, prem: dict | None) -> str:
    s = (val or {}).get("sport_family") or (prem or {}).get("sports_type")
    return str(s).strip().lower() if s else "unknown"


def _fixture(d: dict, prem: dict | None) -> str:
    lab = C.jload(d.get("label")) or {}
    return str((lab.get("event_key") if isinstance(lab, dict) else None)
               or (prem or {}).get("event_slug") or d.get("us_market_slug")
               or d.get("decision_id"))


def _payoff(settle, val):
    """(payoff, outcome, basis, known_at) by the attribution's rules."""
    pay, outcome, basis = AT.payoff_of(settle, val)
    if pay is None:
        return None, None, None, None
    at = (settle or {}).get("at") if basis == "PAPER_SETTLEMENT" else (
        (val or {}).get("outcome_at"))
    return pay, outcome, basis, C.num(at)


def _position(*, book, group_id, dec, fields, sport, fixture, entry, sells,
              legs, settle, val, thesis, strategy, slug, side) -> dict:
    pay, outcome, pbasis, pay_at = _payoff(settle, val)
    attr = AT.attribute(
        subject_id=group_id, book=book, p=fields["p"],
        p_basis=fields["p_basis"], d=fields["d"], d_basis=fields["d_basis"],
        entry_fills=[{k: f[k] for k in ("qty", "price", "fee_usd")}
                     for f in entry],
        sell_fills=[{k: f[k] for k in ("qty", "price", "fee_usd")}
                    for f in sells],
        hedge_legs=[{k: lg[k] for k in ("qty", "cost_usd",
                                        "payoff_per_contract")}
                    for lg in legs],
        payoff=pay, settlement_outcome=outcome, payoff_basis=pbasis)
    q = sum(C.num(f["qty"]) or 0.0 for f in entry)
    v = (sum((C.num(f["qty"]) or 0.0) * (C.num(f["price"]) or 0.0)
             for f in entry) / q) if q > 0 else None
    fees = sum(C.num(f.get("fee_usd")) or 0.0 for f in entry)
    sold = sum(C.num(s["qty"]) or 0.0 for s in sells)
    close_at = None
    if q > 0 and sold >= q - 1e-9 and sells:
        close_at = max(s["at"] for s in sells)
    elif pay_at is not None:
        close_at = pay_at
    th = None
    if thesis is not None:
        th = {"at": C.num(thesis.get("at")),
              "entry_qty": C.num(thesis.get("entry_qty")),
              "counterfactuals": C.jload(thesis.get("counterfactuals")) or {}}
    return {
        "book": book, "group_id": group_id, "subject_id": dec["decision_id"],
        "decision_id": dec["decision_id"], "decided_at": C.num(dec["at"]),
        "slug": slug, "side": side, "strategy": strategy, "sport": sport,
        "fixture": fixture,
        "first_fill_at": min(f["at"] for f in entry) if entry else None,
        "entry_at": max(f["at"] for f in entry) if entry else None,
        "p": fields["p"], "d": fields["d"],
        "q": q, "v": v, "fees": fees,
        "cost_usd": (q * v + fees) if v is not None else None,
        "entry_fills": entry, "sell_fills": sells, "hedge_legs": legs,
        "payoff": pay, "payoff_basis": pbasis, "payoff_at": pay_at,
        "settlement_outcome": outcome, "close_at": close_at,
        "realized_pnl_usd": attr.get("realized_pnl_usd"),
        "realized_unmeasured": attr["unmeasured"].get("realized_pnl_usd"),
        "management_usd": attr.get("management_usd"),
        "model_edge_pc": attr.get("model_edge_pc"),
        "slippage_pc": attr.get("slippage_pc"),
        "fee_pc": attr.get("fee_pc"),
        "thesis": th}


def _opp(dec, fields, *, sport, fixture, entered, enter_verdict) -> dict:
    return {"subject_id": dec["decision_id"], "at": C.num(dec["at"]),
            "verdict": dec.get("verdict"), "strategy": dec.get("strategy"),
            "slug": dec.get("us_market_slug"),
            "side": str(dec.get("holding_side") or "LONG"), "sport": sport,
            "fixture": fixture, "recorded_entered": entered,
            "recorded_enter_verdict": enter_verdict, **fields}


def build_paper(raw: dict, *, vals: dict, prem: dict, theses: list) -> dict:
    """{opps, positions, oracle} from paper_raw(). Pure."""
    by_group: dict = {}
    for f in raw["fills"]:
        by_group.setdefault(f["group_id"], []).append(f)
    sidx = {(s["group_id"], s["us_market_slug"], str(s["holding_side"])): s
            for s in raw["settlements"]}
    cidx: dict = {}
    for s in sorted(raw["settlements"], key=lambda x: (x["at"] or 0,
                                                       x["position_key"])):
        cidx.setdefault((s["us_market_slug"], str(s["holding_side"])), s)
    thx = {t["group_id"]: t for t in theses if t["position_kind"] == "PAPER"}
    opps, positions, oracle = [], [], {}
    for d in raw["decisions"]:
        fields = decision_fields(d)
        val = vals.get(int(d["valuation_id"])) if d.get(
            "valuation_id") is not None else None
        pm = prem.get(d.get("us_market_slug"))
        sport, fixture = _sport(val, pm), _fixture(d, pm)
        g = raw["groups"].get(d["decision_id"])
        gf = by_group.get(g, []) if g else []
        entry = [f for f in gf if f["role"] == "ENTRY"
                 and f["direction"] == "BUY"]
        pos = None
        if entry:
            slug = entry[0]["us_market_slug"]
            side = str(entry[0]["holding_side"])
            sells = [f for f in gf if f["direction"] == "SELL"
                     and f["us_market_slug"] == slug
                     and str(f["holding_side"]) == side]
            legs: dict = {}
            for f in gf:
                if f["role"] == "HEDGE" and f["direction"] == "BUY":
                    k = (f["us_market_slug"], str(f["holding_side"]))
                    lg = legs.setdefault(k, {"qty": 0.0, "cost_usd": 0.0,
                                             "at": f["at"]})
                    lg["qty"] += C.num(f["qty"]) or 0.0
                    lg["cost_usd"] += (C.num(f["gross_usd"]) or 0.0) + (
                        C.num(f["fee_usd"]) or 0.0)
                    lg["at"] = max(lg["at"], f["at"])
            hedge = []
            for (hs, hside), lg in sorted(legs.items()):
                st = sidx.get((g, hs, hside))
                lg["payoff_per_contract"] = (C.num(st["payout_per_contract"])
                                             if st else None)
                hedge.append(lg)
            pos = _position(
                book="PAPER", group_id=g, dec=d, fields=fields, sport=sport,
                fixture=fixture,
                entry=[_fill(f) for f in entry],
                sells=[_fill(f) for f in sells], legs=hedge,
                settle=sidx.get((g, slug, side)), val=val,
                thesis=thx.get(g), strategy=d.get("strategy"), slug=slug,
                side=side)
            positions.append(pos)
        else:
            pay, _o, basis, pay_at = _payoff(
                cidx.get((d.get("us_market_slug"),
                          str(d.get("holding_side") or "LONG"))), val)
            oracle[d["decision_id"]] = {"payoff": pay, "basis": basis,
                                        "at": pay_at}
        opps.append(_opp(d, fields, sport=sport, fixture=fixture,
                         entered=pos is not None,
                         enter_verdict=d.get("verdict") == "ENTER"))
    return {"opps": opps, "positions": positions, "oracle": oracle}


def _fill(f) -> dict:
    return {"qty": C.num(f["qty"]), "price": C.num(f["price"]),
            "fee_usd": C.num(f.get("fee_usd")) or 0.0, "at": C.num(f["at"])}


def build_actual(raw: dict, *, vals: dict, prem: dict, theses: list) -> dict:
    """{opps, positions, oracle} for the ACTUAL book (execution mirror).
    Fill prices are venue LONG-side wire prices converted to cost space by
    the intent's side, as in intel.attribution.load_actual."""
    sidx = {(s["group_id"], s["us_market_slug"], str(s["holding_side"])): s
            for s in raw["settlements"]}
    thx = {t["group_id"]: t for t in theses if t["position_kind"] == "ACTUAL"}
    opps, positions, seen = [], [], set()
    for it in raw["intents"]:
        g = it.get("group_id")
        if not g or g in seen:
            continue
        seen.add(g)
        side = (C.side_of_intent(it.get("order_intent"))
                if it.get("order_intent") else
                str(it.get("holding_side") or "LONG"))
        slug = it.get("us_market_slug")
        gf = [f for f in raw["fills"] if f["group_id"] == g
              and f["us_market_slug"] == slug]
        entry = [{"qty": C.num(f["qty"]),
                  "price": C.cost_space(f["price"], side),
                  "fee_usd": C.num(f["fee_usd"]) or 0.0, "at": C.num(f["at"])}
                 for f in gf if C.is_buy_intent(f["intent"])
                 and f.get("role") in ("ENTRY", None)]
        if not entry:
            continue
        sells = [{"qty": C.num(f["qty"]),
                  "price": C.cost_space(f["price"], side),
                  "fee_usd": C.num(f["fee_usd"]) or 0.0, "at": C.num(f["at"])}
                 for f in gf if not C.is_buy_intent(f["intent"])]
        dec = dict(raw["decisions"].get(it.get("decision_id")) or {})
        dec.setdefault("decision_id", it.get("decision_id") or it["intent_id"])
        dec["at"] = it["at"]
        dec.setdefault("strategy", it.get("strategy"))
        dec.setdefault("us_market_slug", slug)
        fields = decision_fields(dec)
        wire = C.cost_space(it.get("wire_price"), side)
        if wire is not None:
            fields["d"], fields["d_basis"] = (
                wire, "EXECUTION_INTENT_DECISION_WIRE")
        vid = it.get("valuation_id") or dec.get("valuation_id")
        val = vals.get(int(vid)) if vid is not None else None
        pm = prem.get(slug)
        sport, fixture = _sport(val, pm), _fixture(dec, pm)
        pos = _position(
            book="ACTUAL", group_id=g, dec=dec, fields=fields, sport=sport,
            fixture=fixture, entry=entry, sells=sells, legs=[],
            settle=sidx.get((g, slug, side)), val=val, thesis=thx.get(g),
            strategy=it.get("strategy"), slug=slug, side=side)
        positions.append(pos)
        opps.append(_opp(dec, fields, sport=sport, fixture=fixture,
                         entered=True, enter_verdict=True))
    return {"opps": opps, "positions": positions, "oracle": {}}


# ═════════════════════════════════════════════════════════════════════
# THE STREAM AND ITS TIME VIEW
# ═════════════════════════════════════════════════════════════════════

class FutureRead(AssertionError):
    """A world asked for a record after its decision instant."""


class Stream:
    """The recorded stream of one basis book, frozen for a replay."""

    def __init__(self, *, basis: str, opps: list, positions: list,
                 oracle: dict | None = None, books: list | None = None,
                 regimes: list | None = None, allocations: list | None = None,
                 karen: list | None = None, archer: list | None = None,
                 scout: list | None = None, iface_why: dict | None = None,
                 window: tuple = (0.0, 0.0)):
        self.basis = basis
        self.opps = sorted(opps, key=lambda o: (o["at"] or 0.0,
                                                o["subject_id"]))
        self.positions = sorted(positions, key=lambda p: (
            p["decided_at"] or 0.0, p["subject_id"]))
        self.pos_by_subject = {p["subject_id"]: p for p in self.positions}
        self.opp_by_subject = {o["subject_id"]: o for o in self.opps}
        self.oracle = dict(oracle or {})
        key = lambda r: (r["at"], str(r.get("obs_id") or r.get("run_id")
                                       or r.get("challenge_id")
                                       or r.get("decision_id") or ""))
        self.books = sorted(books or [], key=key)
        self.regimes = sorted(regimes or [], key=key)
        self.allocations = sorted(allocations or [], key=key)
        self.karen = sorted(karen or [], key=key)
        self.archer = None if archer is None else sorted(archer, key=key)
        self.scout = None if scout is None else sorted(scout, key=key)
        self.iface_why = dict(iface_why or {})
        self.window = (float(window[0]), float(window[1]))
        self._books_by_slug: dict = {}
        for b in self.books:
            self._books_by_slug.setdefault(b["slug"], []).append(b)
        self._alloc_by_decision: dict = {}
        for a in self.allocations:
            self._alloc_by_decision.setdefault(a.get("decision_id"),
                                               []).append(a)

    def content(self) -> dict:
        strip = lambda rs: [{k: v for k, v in r.items()} for r in rs]
        return {"basis": self.basis, "opps": strip(self.opps),
                "positions": strip(self.positions),
                "oracle": {k: self.oracle[k] for k in sorted(self.oracle)},
                "books": [{k: b[k] for k in ("obs_id", "slug", "at", "bids",
                                             "offers")} for b in self.books],
                "regimes": strip(self.regimes),
                "allocations": strip(self.allocations),
                "karen": strip(self.karen),
                "archer": None if self.archer is None else strip(self.archer),
                "scout": None if self.scout is None else strip(self.scout),
                "iface_why": self.iface_why}

    def input_sha(self) -> str:
        return C.sha(self.content())

    def view(self, t: float) -> "TimeView":
        return TimeView(self, t)


class TimeView:
    """Everything a world may know at instant t, and a record of what it
    read. A record with `at` > t is invisible; serving one raises."""

    def __init__(self, stream: Stream, t: float):
        self._s = stream
        self.t = float(t)
        self.max_input_at = None
        self.inputs_read = 0

    def _seen(self, at) -> None:
        if at is None:
            return
        at = float(at)
        if at > self.t + 1e-9:
            raise FutureRead("record at %.3f served to a decision at %.3f"
                             % (at, self.t))
        self.inputs_read += 1
        self.max_input_at = at if self.max_input_at is None else max(
            self.max_input_at, at)

    def _upto(self, recs):
        return [r for r in recs if r["at"] is not None
                and r["at"] <= self.t + 1e-9]

    def decision(self, opp: dict) -> dict:
        self._seen(opp["at"])
        return {k: opp.get(k) for k in DECISION_KEYS}

    def entry(self, pos: dict) -> dict:
        """The position's entry inventory as known at t (its entry fills)."""
        fills = self._upto(pos["entry_fills"])
        for f in fills:
            self._seen(f["at"])
        q = sum(f["qty"] or 0.0 for f in fills)
        cost = sum((f["qty"] or 0.0) * (f["price"] or 0.0) + f["fee_usd"]
                   for f in fills)
        return {"q": q, "cost_usd": cost, "fills": len(fills)}

    def sells(self, pos: dict) -> list:
        out = self._upto(pos["sell_fills"])
        for s in out:
            self._seen(s["at"])
        return out

    def latest_book(self, slug: str, max_age_s: float):
        recs = self._upto(self._s._books_by_slug.get(slug, []))
        if not recs:
            return None
        b = recs[-1]
        self._seen(b["at"])
        if self.t - b["at"] > float(max_age_s):
            return None
        return b

    def thesis(self, pos: dict):
        th = pos.get("thesis")
        if not th or th.get("at") is None or th["at"] > self.t + 1e-9:
            return None
        self._seen(th["at"])
        return th

    def regime(self):
        recs = self._upto(self._s.regimes)
        if not recs:
            return None
        self._seen(recs[-1]["at"])
        return recs[-1]["recommendation"]

    def allocation(self, decision_id: str):
        recs = self._upto(self._s._alloc_by_decision.get(decision_id, []))
        if not recs:
            return None
        self._seen(recs[-1]["at"])
        return C.num(recs[-1].get("shadow_usd"))

    def closed_by_now(self, subject_id: str) -> bool:
        """Whether the recorded position is closed at t (a fact known at t
        only if the close happened at or before t)."""
        p = self._s.pos_by_subject.get(subject_id)
        if p is None or p.get("close_at") is None or p["close_at"] > self.t:
            return False
        self._seen(p["close_at"])
        return True

    def karen_blocks(self, ids) -> list:
        ids = {i for i in ids if i}
        out = []
        for r in self._upto(self._s.karen):
            refs = {str(x.get("id")) for x in (C.jload(r.get(
                "evidence_refs")) or []) if isinstance(x, dict)}
            if r.get("target_id") in ids or refs & ids:
                self._seen(r["at"])
                out.append(r)
        return out

    def archer(self, decision_id: str):
        recs = [r for r in self._upto(self._s.archer or [])
                if r.get("decision_id") == decision_id]
        if not recs:
            return None
        self._seen(recs[-1]["at"])
        return recs[-1]

    def scout(self, decision_id: str):
        recs = [r for r in self._upto(self._s.scout or [])
                if r.get("decision_id") == decision_id]
        if not recs:
            return None
        self._seen(recs[-1]["at"])
        return recs[-1]


# ═════════════════════════════════════════════════════════════════════
# SCORING (outcomes enter here only)
# ═════════════════════════════════════════════════════════════════════

def _row(subject, *, kind, view, recorded_action, world_action, pnl,
         basis=None, why=None, capital=None, entry_at=None, close_at=None,
         baseline=None, baseline_why=None, baseline_capital=None,
         baseline_close_at=None, baseline_entry_at=None, opp=None) -> dict:
    ref = opp or subject
    um = {}
    if pnl is None:
        um["pnl_usd"] = why or "NOT_MEASURED"
    if baseline is None:
        um["baseline_pnl_usd"] = baseline_why or "NOT_MEASURED"
    return {"subject_id": ref["subject_id"], "decision_kind": kind,
            "decision_at": view.t, "max_input_at": view.max_input_at,
            "inputs_read": view.inputs_read,
            "recorded_action": recorded_action, "world_action": world_action,
            "pnl_usd": None if pnl is None else C.rnd(pnl),
            "pnl_basis": basis, "unmeasured": um,
            "capital_usd": None if capital is None else C.rnd(capital),
            "entry_at": entry_at, "close_at": close_at,
            "baseline_pnl_usd": None if baseline is None else C.rnd(baseline),
            "baseline_capital_usd": (None if baseline_capital is None
                                     else C.rnd(baseline_capital)),
            "baseline_close_at": baseline_close_at,
            "baseline_entry_at": baseline_entry_at,
            "sport": ref.get("sport"), "strategy": ref.get("strategy"),
            "fixture": ref.get("fixture")}


def _baseline_of(stream: Stream, opp: dict) -> tuple:
    """(pnl, why, capital, close_at, entry_at) of the RECORDED world."""
    pos = stream.pos_by_subject.get(opp["subject_id"])
    if pos is None:
        return 0.0, None, 0.0, None, None
    return (pos["realized_pnl_usd"], pos["realized_unmeasured"],
            pos["cost_usd"], pos["close_at"], pos["entry_at"])


def _recorded_action(opp: dict) -> str:
    if opp["recorded_entered"]:
        return "ENTERED"
    return "ENTER_NOT_FILLED" if opp["recorded_enter_verdict"] else "REFUSED"


def hold_pnl(pos: dict):
    if pos["q"] <= 0 or pos["v"] is None:
        return None, "NO_ENTRY_FILL"
    if pos["payoff"] is None:
        return None, "POSITION_NOT_SETTLED"
    return pos["q"] * (pos["payoff"] - pos["v"]) - pos["fees"], None


def exit_at(pos: dict, view: TimeView, *, max_book_age_s: float,
            use_thesis: bool) -> tuple:
    """(pnl, basis, why, close_at) of exiting the position at view.t: sales
    already made by t are kept; the remainder is sold into the latest book
    at or before t (or Xavier's thesis plan frozen at entry); whatever the
    book cannot absorb is held to settlement."""
    cost = pos["cost_usd"]
    if cost is None:
        return None, None, "NO_ENTRY_FILL", None
    q = pos["q"]
    done = view.sells(pos)
    sold_before = sum(s["qty"] or 0.0 for s in done)
    proceeds = sum((s["qty"] or 0.0) * (s["price"] or 0.0) - s["fee_usd"]
                   for s in done)
    rem = max(0.0, q - sold_before)
    if rem <= 1e-9:
        return proceeds - cost, "ALREADY_SOLD_BY_EXIT_INSTANT", None, view.t
    th = view.thesis(pos) if use_thesis and not done else None
    plan = ((th or {}).get("counterfactuals") or {}).get("IMMEDIATE_EXIT")
    if (th and isinstance(plan, dict) and plan.get("available")
            and th.get("entry_qty") is not None
            and abs(th["entry_qty"] - q) <= 1e-6):
        unsold = C.num(plan.get("unsold_qty")) or 0.0
        if unsold > 1e-9 and pos["payoff"] is None:
            return None, None, "UNSOLD_REMAINDER_AWAITS_SETTLEMENT", None
        tail = unsold * pos["payoff"] if unsold > 1e-9 else 0.0
        pnl = (C.num(plan.get("exit_proceeds_usd")) or 0.0) - (
            C.num(plan.get("exit_fees_usd")) or 0.0) + tail - cost
        return pnl, "XAVIER_THESIS_IMMEDIATE_EXIT_PLAN_FROZEN_AT_ENTRY", \
            None, view.t
    book = view.latest_book(pos["slug"], max_book_age_s)
    if book is None:
        return None, None, "NO_BOOK_AT_OR_BEFORE_EXIT_WITHIN_MAX_AGE", None
    walk = C.exit_walk({"bids": book["bids"], "offers": book["offers"]},
                       pos["side"], rem)
    absorbed = rem - walk["unabsorbed_qty"]
    fee_rate = (pos["fees"] / q) if q > 0 else 0.0
    if walk["unabsorbed_qty"] > 1e-9 and pos["payoff"] is None:
        return None, None, "UNABSORBED_REMAINDER_AWAITS_SETTLEMENT", None
    tail = (walk["unabsorbed_qty"] * pos["payoff"]
            if walk["unabsorbed_qty"] > 1e-9 else 0.0)
    pnl = proceeds + walk["value_usd"] - fee_rate * absorbed + tail - cost
    return pnl, "BOOK_WALK_AT_EXIT_INSTANT_ENTRY_FEE_RATE", None, view.t


def _new_entry(opp: dict, oracle: dict, qty_mult: float = 1.0) -> tuple:
    """(pnl, capital, close_at, why, basis) of a world-only entry."""
    q = opp.get("qty")
    if not q or opp.get("d") is None:
        return None, None, None, "DECISION_CARRIES_NO_PLANNED_QTY_OR_PRICE", \
            None
    q = float(q) * qty_mult
    if opp.get("depth_qty") is not None:
        q = min(q, float(opp["depth_qty"]))
    if q <= 0:
        return 0.0, 0.0, None, None, "NO_DEPTH_WITHIN_LIMIT"
    fee_pc = opp.get("fee_pc") or 0.0
    capital = q * (opp["d"] + fee_pc)
    o = oracle.get(opp["subject_id"]) or {}
    if o.get("payoff") is None:
        return None, capital, None, "OUTCOME_NOT_KNOWN", None
    return (q * (o["payoff"] - opp["d"] - fee_pc), capital, o.get("at"),
            None, "ASSUMED_FILL_AT_DECISION_PLANNED_PRICE_HELD_TO_SETTLEMENT")


# ═════════════════════════════════════════════════════════════════════
# THE WORLDS
# ═════════════════════════════════════════════════════════════════════

def _threshold_world(stream: Stream, spec: dict, *, p_of=None) -> list:
    thr = float(spec.get("min_net_edge_pc", 0.0))
    out = []
    for opp in stream.opps:
        view = stream.view(opp["at"])
        dec = view.decision(opp)
        p = dec["p"]
        p_basis = "RECORDED_DECISION_PROBABILITY"
        if p_of is not None:
            p, p_basis = p_of(view, dec)
        base, bwhy, bcap, bclose, bentry = _baseline_of(stream, opp)
        rec = _recorded_action(opp)
        bk = {"baseline": base, "baseline_why": bwhy,
              "baseline_capital": bcap, "baseline_close_at": bclose,
              "baseline_entry_at": bentry}
        if p is None or dec["d"] is None:
            out.append(_row(opp, kind="ENTRY", view=view,
                            recorded_action=rec, world_action="UNSCORABLE",
                            pnl=None,
                            why="DECISION_CARRIES_NO_PROBABILITY_OR_PRICE",
                            **bk))
            continue
        net = p - dec["d"] - (dec["fee_pc"] or 0.0)
        enter = net >= thr - 1e-12
        pos = stream.pos_by_subject.get(opp["subject_id"])
        if not enter:
            out.append(_row(opp, kind="ENTRY", view=view,
                            recorded_action=rec, world_action="REFUSE",
                            pnl=0.0, basis="NOT_ENTERED_IN_WORLD",
                            capital=0.0, **bk))
        elif pos is not None:
            out.append(_row(opp, kind="ENTRY", view=view,
                            recorded_action=rec, world_action="ENTER",
                            pnl=pos["realized_pnl_usd"],
                            why=pos["realized_unmeasured"],
                            basis="RECORDED_POSITION", capital=pos["cost_usd"],
                            entry_at=pos["entry_at"],
                            close_at=pos["close_at"], **bk))
        elif opp["recorded_enter_verdict"]:
            out.append(_row(opp, kind="ENTRY", view=view,
                            recorded_action=rec, world_action="ENTER",
                            pnl=0.0, basis="RECORDED_ENTRY_DID_NOT_FILL",
                            capital=0.0, **bk))
        else:
            pnl, cap, close, why, basis = _new_entry(opp, stream.oracle)
            out.append(_row(opp, kind="ENTRY", view=view,
                            recorded_action=rec, world_action="ENTER",
                            pnl=pnl, why=why, basis=basis, capital=cap,
                            entry_at=opp["at"], close_at=close,
                            **bk))
        out[-1]["p_basis"] = p_basis
    return out


def world_recorded(stream: Stream, spec: dict) -> list:
    out = []
    for opp in stream.opps:
        view = stream.view(opp["at"])
        view.decision(opp)
        base, bwhy, bcap, bclose, bentry = _baseline_of(stream, opp)
        rec = _recorded_action(opp)
        out.append(_row(opp, kind="ENTRY", view=view, recorded_action=rec,
                        world_action=rec, pnl=base, why=bwhy,
                        basis="RECORDED", capital=bcap, entry_at=bentry,
                        close_at=bclose, baseline=base, baseline_why=bwhy,
                        baseline_capital=bcap, baseline_close_at=bclose,
                        baseline_entry_at=bentry))
    return out


def world_derek_threshold(stream: Stream, spec: dict) -> list:
    return _threshold_world(stream, spec)


def world_sizing(stream: Stream, spec: dict) -> list:
    m = float(spec["multiplier"])
    out = []
    for pos in stream.positions:
        opp = {"subject_id": pos["subject_id"], "at": pos["decided_at"]}
        view = stream.view(pos["decided_at"])
        dec = view.decision(_opp_of(stream, pos))
        q = pos["q"]
        q2 = q * m
        capped = False
        if m > 1 and dec.get("depth_qty") is not None:
            cap = max(q, float(dec["depth_qty"]))
            capped = q2 > cap
            q2 = min(q2, cap)
        base = pos["realized_pnl_usd"]
        scale = (q2 / q) if q > 0 else None
        pnl = None if (base is None or scale is None) else base * scale
        out.append(_row(pos, kind="SIZE", view=view, recorded_action="SIZE_1X",
                        world_action="SIZE_%.2fX%s" % (
                            scale or 0.0, "_DEPTH_CAPPED" if capped else ""),
                        pnl=pnl, why=pos["realized_unmeasured"],
                        basis="LINEAR_SCALING_OF_RECORDED_PNL",
                        capital=None if (pos["cost_usd"] is None or scale
                                         is None) else pos["cost_usd"] * scale,
                        entry_at=pos["entry_at"], close_at=pos["close_at"],
                        baseline=base, baseline_why=pos["realized_unmeasured"],
                        baseline_capital=pos["cost_usd"],
                        baseline_close_at=pos["close_at"],
                        baseline_entry_at=pos["entry_at"], opp=opp | {
                            k: pos[k] for k in ("sport", "strategy",
                                                "fixture")}))
    return out


def _opp_of(stream: Stream, pos: dict) -> dict:
    o = stream.opp_by_subject.get(pos["subject_id"])
    if o is not None:
        return o
    return {"subject_id": pos["subject_id"], "at": pos["decided_at"],
            **{k: None for k in DECISION_KEYS if k not in (
                "subject_id", "at")}}


def _pos_row(pos, **kw):
    return _row(pos, baseline=pos["realized_pnl_usd"],
                baseline_why=pos["realized_unmeasured"],
                baseline_capital=pos["cost_usd"],
                baseline_close_at=pos["close_at"],
                baseline_entry_at=pos["entry_at"], **kw)


def world_xavier_hold(stream: Stream, spec: dict) -> list:
    out = []
    for pos in stream.positions:
        if pos["entry_at"] is None:
            continue
        view = stream.view(pos["entry_at"])
        view.entry(pos)
        pnl, why = hold_pnl(pos)
        acted = bool(pos["sell_fills"] or pos["hedge_legs"])
        out.append(_pos_row(
            pos, kind="MANAGE", view=view,
            recorded_action="MANAGED" if acted else "HELD",
            world_action="HOLD_TO_SETTLEMENT", pnl=pnl, why=why,
            basis="ENTRY_INVENTORY_HELD_TO_SETTLEMENT",
            capital=pos["cost_usd"], entry_at=pos["entry_at"],
            close_at=pos["payoff_at"]))
    return out


def world_xavier_exit(stream: Stream, spec: dict) -> list:
    out = []
    age = float(spec.get("max_book_age_s", 120.0))
    for pos in stream.positions:
        if pos["entry_at"] is None:
            continue
        t = pos["entry_at"]
        th = pos.get("thesis")
        if th and th.get("at") is not None and \
                0 <= th["at"] - t <= THESIS_MAX_LAG_S:
            t = max(t, th["at"])
        view = stream.view(t)
        view.entry(pos)
        pnl, basis, why, close = exit_at(pos, view, max_book_age_s=age,
                                         use_thesis=bool(spec.get(
                                             "prefer_thesis_plan", True)))
        acted = bool(pos["sell_fills"] or pos["hedge_legs"])
        out.append(_pos_row(
            pos, kind="MANAGE", view=view,
            recorded_action="MANAGED" if acted else "HELD",
            world_action="IMMEDIATE_EXIT", pnl=pnl, why=why, basis=basis,
            capital=pos["cost_usd"], entry_at=pos["entry_at"],
            close_at=close))
    return out


def world_allocator(stream: Stream, spec: dict) -> list:
    rule = spec["rule"]
    sleeve = float(spec.get("sleeve_usd", C.SLEEVE_NOTIONAL_USD))
    alloc: dict = {}
    out = []
    for pos in stream.positions:
        if pos["entry_at"] is None:
            continue
        opp = _opp_of(stream, pos)
        view = stream.view(pos["decided_at"])
        view.decision(opp)
        open_ = {s: n for s, n in alloc.items()
                 if not view.closed_by_now(s)}
        free = max(0.0, sleeve - sum(open_.values()))
        notional, why = None, None
        if rule == "EQUAL_WEIGHT":
            notional = min(free, sleeve / float(spec["slots"]))
        elif rule == "INDEPENDENT_FIXED_FRACTION":
            notional = sleeve * float(spec["fraction"])
        elif rule == "INTEL_SHADOW_ALLOCATOR":
            notional = view.allocation(pos["decision_id"])
            if notional is None:
                why = "NO_SHADOW_ALLOCATION_RECORDED_AT_OR_BEFORE_DECISION"
        else:
            raise ValueError("unknown allocator rule %r" % rule)
        if notional is not None:
            alloc[pos["subject_id"]] = notional
        r = None
        if pos["realized_pnl_usd"] is not None and pos["cost_usd"]:
            r = pos["realized_pnl_usd"] / pos["cost_usd"]
        pnl = None
        if notional is not None and notional <= 1e-12:
            pnl, basis = 0.0, "NO_FREE_SLEEVE_NOT_ALLOCATED"
        elif notional is not None and r is not None:
            pnl, basis = notional * r, "NOTIONAL_TIMES_RECORDED_RETURN"
        else:
            basis = None
            why = why or pos["realized_unmeasured"] or "NO_RECORDED_RETURN"
        out.append(_pos_row(
            pos, kind="ALLOCATE", view=view, recorded_action="RECORDED_SIZE",
            world_action="%s:%s" % (rule, "NONE" if notional is None
                                    else "%.2f" % notional),
            pnl=pnl, why=why, basis=basis, capital=notional,
            entry_at=pos["entry_at"], close_at=pos["close_at"]))
        out[-1]["return_per_usd"] = None if r is None else C.rnd(r)
    return out


def world_karen(stream: Stream, spec: dict) -> list:
    accept = spec["mode"] == "ACCEPTED"
    age = float(spec.get("max_book_age_s", 120.0))
    out = []
    for pos in stream.positions:
        if pos["entry_at"] is None:
            continue
        ids = {pos["decision_id"], pos["group_id"]}
        opp = _opp_of(stream, pos)
        view = stream.view(pos["decided_at"])
        view.decision(opp)
        pre = view.karen_blocks(ids)
        if pre:
            if accept:
                out.append(_pos_row(
                    pos, kind="BLOCK", view=view, recorded_action="ENTERED",
                    world_action="BLOCK_ACCEPTED_NOT_ENTERED", pnl=0.0,
                    basis="NOT_ENTERED_IN_WORLD", capital=0.0))
            else:
                out.append(_pos_row(
                    pos, kind="BLOCK", view=view, recorded_action="ENTERED",
                    world_action="BLOCK_IGNORED", pnl=pos["realized_pnl_usd"],
                    why=pos["realized_unmeasured"], basis="RECORDED_POSITION",
                    capital=pos["cost_usd"], entry_at=pos["entry_at"],
                    close_at=pos["close_at"]))
            continue
        # event-driven: each later Karen record arrives at its own instant;
        # the world acts then, on what it can see then (a position already
        # closed by that instant is a fact known at that instant)
        hit = None
        for k in stream.karen:
            if k["at"] <= pos["decided_at"]:
                continue
            v2 = stream.view(k["at"])
            if v2.closed_by_now(pos["subject_id"]):
                break
            if v2.karen_blocks(ids):
                hit, view = k, v2
                break
        if hit is None or not accept:
            out.append(_pos_row(
                pos, kind="BLOCK", view=view, recorded_action="ENTERED",
                world_action=("NO_BLOCK" if hit is None else "BLOCK_IGNORED"),
                pnl=pos["realized_pnl_usd"], why=pos["realized_unmeasured"],
                basis="RECORDED_POSITION", capital=pos["cost_usd"],
                entry_at=pos["entry_at"], close_at=pos["close_at"]))
            continue
        pnl, basis, why, close = exit_at(pos, view, max_book_age_s=age,
                                         use_thesis=False)
        out.append(_pos_row(
            pos, kind="BLOCK", view=view, recorded_action="ENTERED",
            world_action="BLOCK_ACCEPTED_EXIT", pnl=pnl, why=why,
            basis=basis, capital=pos["cost_usd"], entry_at=pos["entry_at"],
            close_at=close))
    return out


def world_archer(stream: Stream, spec: dict) -> list:
    out = []
    for pos in stream.positions:
        if pos["entry_at"] is None:
            continue
        opp = _opp_of(stream, pos)
        view = stream.view(pos["decided_at"])
        view.decision(opp)
        row = view.archer(pos["decision_id"])
        base = pos["realized_pnl_usd"]
        if row is None:
            out.append(_pos_row(
                pos, kind="EXECUTE", view=view, recorded_action="RECORDED_FILL",
                world_action="NO_ARCHER_PLAN_AT_DECISION", pnl=base,
                why=pos["realized_unmeasured"], basis="RECORDED_EXECUTION",
                capital=pos["cost_usd"], entry_at=pos["entry_at"],
                close_at=pos["close_at"]))
            continue
        ev, ef = C.num(row.get("eddie_vwap")), C.num(row.get("eddie_fee_usd"))
        if base is None or ev is None or ef is None or pos["v"] is None:
            out.append(_pos_row(
                pos, kind="EXECUTE", view=view, recorded_action="RECORDED_FILL",
                world_action="ARCHER_PLAN", pnl=None,
                why=pos["realized_unmeasured"] or "ARCHER_ROW_INCOMPLETE",
                capital=pos["cost_usd"]))
            continue
        q = pos["q"]
        pnl = base + q * (pos["v"] - ev) + (pos["fees"] - ef)
        out.append(_pos_row(
            pos, kind="EXECUTE", view=view, recorded_action="RECORDED_FILL",
            world_action="ARCHER_PLAN", pnl=pnl,
            basis="RECORDED_PNL_WITH_ARCHER_FILL_PRICE_AND_FEE",
            capital=q * ev + ef, entry_at=pos["entry_at"],
            close_at=pos["close_at"]))
    return out


def world_scout(stream: Stream, spec: dict) -> list:
    include = spec["mode"] == "INCLUDED"

    def p_of(view, dec):
        row = view.scout(dec["subject_id"])
        if row is None:
            return dec["p"], "NO_SCOUT_ROW_AT_DECISION_RECORDED_PROBABILITY"
        p = C.num(row.get("p_with" if include else "p_without"))
        return p, "SCOUT_%s" % ("WITH_FEATURE" if include
                                else "WITHOUT_FEATURE")
    return _threshold_world(stream, spec, p_of=p_of)


WORLDS = {
    "RECORDED": world_recorded,
    "DEREK_THRESHOLD": world_derek_threshold,
    "SIZING": world_sizing,
    "XAVIER_ALWAYS_HOLD": world_xavier_hold,
    "XAVIER_IMMEDIATE_EXIT": world_xavier_exit,
    "ALLOCATOR": world_allocator,
    "EDDIE_EXECUTION": world_archer,
    "SCOUT_FEATURE": world_scout,
    "KAREN_BLOCK": world_karen,
}
#: worlds that need an interface view of a parallel stream
REQUIRES = {"EDDIE_EXECUTION": "ARCHER", "SCOUT_FEATURE": "SCOUT"}


# ═════════════════════════════════════════════════════════════════════
# AGGREGATION AND THE RESULT
# ═════════════════════════════════════════════════════════════════════

def aggregate(rows: list, *, label: str, pnl_key: str = "pnl_usd",
              cap_key: str = "capital_usd", close_key: str = "close_at",
              start_key: str = "entry_at",
              sleeve_usd: float | None = None) -> dict:
    """World or baseline totals over the measured subjects. Utilization
    (allocator worlds) is capital-hours / (sleeve x the span from the first
    entry to the last close of the closed subjects) -- a span taken from
    the records, never from the clock, so the same records give the same
    figure."""
    scored = [r for r in rows if r.get(pnl_key) is not None]
    why = Counter(r["unmeasured"].get(pnl_key, "NOT_MEASURED")
                  for r in rows if r.get(pnl_key) is None)
    pnls = [r[pnl_key] for r in scored]
    entered = [r for r in scored if (r.get(cap_key) or 0.0) > 0]
    cap = sum(r[cap_key] for r in entered)
    ordered = sorted(scored, key=lambda r: (
        r.get(close_key) if r.get(close_key) is not None else float("inf"),
        r["subject_id"]))
    ch, ch_n, span = 0.0, 0, []
    for r in entered:
        close = r.get(close_key)
        start = r.get(start_key) or r.get("decision_at")
        if close is not None and start is not None and close >= start:
            ch += r[cap_key] * (close - start) / 3600.0
            ch_n += 1
            span += [start, close]
    span_h = (max(span) - min(span)) / 3600.0 if span else 0.0
    out = C.Out(label=label, subjects=len(rows), scored=len(scored),
                unscored=len(rows) - len(scored),
                unscored_reasons=dict(sorted(why.items())),
                entered=len(entered))
    if not scored:
        for k in ("total_pnl_usd", "mean_pnl_usd", "capital_usd",
                  "return_on_capital", "max_drawdown_usd", "capital_hours",
                  "pnl_per_capital_hour", "win_rate", "utilization"):
            out.put(k, None, "NO_SCORED_SUBJECT")
        out["ci"] = None
        return out
    out.put("total_pnl_usd", C.rnd(sum(pnls)))
    out.put("mean_pnl_usd", C.rnd(sum(pnls) / len(pnls)))
    out["ci"] = C.mean_ci(pnls)
    out.put("capital_usd", C.rnd(cap))
    out.put("return_on_capital",
            C.rnd(sum(r[pnl_key] for r in entered) / cap) if cap > 0
            else None, "NO_CAPITAL_DEPLOYED")
    out.put("max_drawdown_usd", C.max_drawdown([r[pnl_key] for r in ordered]))
    out.put("capital_hours", C.rnd(ch) if ch_n else None,
            "NO_CLOSED_POSITION_WITH_A_HOLDING_PERIOD")
    ech = [r for r in entered if r.get(close_key) is not None]
    out.put("pnl_per_capital_hour",
            C.rnd(sum(r[pnl_key] for r in ech) / ch) if ch > 0 else None,
            "NO_CAPITAL_HOURS")
    out.put("win_rate", C.rnd(sum(1 for r in entered if r[pnl_key] > 0)
                              / len(entered)) if entered else None,
            "NO_ENTERED_SUBJECT")
    out.put("utilization",
            C.rnd(ch / (sleeve_usd * span_h))
            if (sleeve_usd and span_h > 0 and ch_n) else None,
            "UTILIZATION_APPLIES_TO_ALLOCATOR_WORLDS_ONLY"
            if not sleeve_usd else "NO_CAPITAL_HOURS")
    by: dict = {}
    for r in scored:
        by.setdefault(r.get("sport") or "unknown", []).append(r[pnl_key])
    out["by_sport"] = {s: {"n": len(v), "total_pnl_usd": C.rnd(sum(v))}
                       for s, v in sorted(by.items())}
    fx: dict = {}
    for r in entered:
        fx[r.get("fixture")] = fx.get(r.get("fixture"), 0.0) + r[cap_key]
    out["max_fixture_capital_share"] = (C.rnd(max(fx.values()) / cap)
                                        if cap > 0 else None)
    return out


def compare(rows: list, *, basis: str) -> dict:
    pairs = [(r["pnl_usd"], r["baseline_pnl_usd"]) for r in rows
             if r["pnl_usd"] is not None and r["baseline_pnl_usd"] is not None]
    diffs = [a - b for a, b in pairs]
    out = {"label": "COUNTERFACTUAL_MINUS_%s_PAIRED_DIFFERENCE" % basis,
           "paired_n": len(pairs), "summed_across_books": False,
           "is_a_sum": False}
    if not diffs:
        out.update(total_diff_usd=None, mean_diff_usd=None, ci=None,
                   why="NO_SUBJECT_MEASURED_IN_BOTH_WORLDS")
        return out
    out.update(total_diff_usd=C.rnd(sum(diffs)),
               mean_diff_usd=C.rnd(sum(diffs) / len(diffs)),
               ci=C.diff_ci(diffs),
               improved=sum(1 for d in diffs if d > 1e-9),
               worsened=sum(1 for d in diffs if d < -1e-9), why=None)
    return out


def run_scenario(stream: Stream, scenario: dict) -> dict:
    """One frozen scenario over one stream -> the result body (without run
    identifiers) and its traces. Same stream + same spec -> same output_sha.
    """
    spec = scenario["spec"]
    world = spec["world"]
    body = {"scenario_id": scenario["scenario_id"],
            "spec_sha256": scenario["spec_sha256"],
            "basis_book": stream.basis, "engine_version": VERSION,
            "window_start": stream.window[0], "window_end": stream.window[1],
            "input_sha256": stream.input_sha()}
    need = REQUIRES.get(world)
    if need and getattr(stream, need.lower()) is None:
        body.update(status="UNAVAILABLE",
                    unavailable_reason=stream.iface_why.get(need)
                    or "INTERFACE_ABSENT:%s" % need,
                    baseline=None, world=None, comparison=None,
                    counts={}, unmeasured={})
        body["output_sha256"] = C.sha({k: body[k] for k in (
            "scenario_id", "spec_sha256", "basis_book", "input_sha256",
            "status", "unavailable_reason")})
        return {"body": body, "traces": [], "rows": []}
    rows = WORLDS[world](stream, spec)
    sleeve = (float(spec.get("sleeve_usd", C.SLEEVE_NOTIONAL_USD))
              if world == "ALLOCATOR" else None)
    world_agg = aggregate(rows, label=C.COUNTERFACTUAL, sleeve_usd=sleeve)
    base_agg = aggregate(rows, label=stream.basis, pnl_key="baseline_pnl_usd",
                         cap_key="baseline_capital_usd",
                         close_key="baseline_close_at",
                         start_key="baseline_entry_at")
    actions = Counter(r["world_action"].split(":")[0] for r in rows)
    body.update(status="OK", unavailable_reason=None, baseline=base_agg,
                world=world_agg, comparison=compare(rows, basis=stream.basis),
                counts={"subjects": len(rows),
                        "world_actions": dict(sorted(actions.items())),
                        "traces_kept": min(len(rows), MAX_TRACES),
                        "traces_truncated": max(0, len(rows) - MAX_TRACES),
                        # every decision (kept or not) went through the
                        # time view, which raises on a later record
                        "decisions_checked_no_future_read": len(rows)},
                unmeasured=dict(world_agg["unmeasured"]))
    body["output_sha256"] = C.sha({k: body[k] for k in (
        "scenario_id", "spec_sha256", "basis_book", "input_sha256", "status",
        "baseline", "world", "comparison", "counts")} | {
        "rows": [{k: r[k] for k in ("subject_id", "world_action", "pnl_usd",
                                    "decision_at", "max_input_at")}
                 for r in rows]})
    # every row feeds the scorecards; the most recent MAX_TRACES are kept
    # as stored traces (bounded storage)
    return {"body": body, "traces": rows[-MAX_TRACES:], "rows": rows}
