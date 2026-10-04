"""FROZEN DECISION SNAPSHOTS (control plane, stream E).

OWNER (section 6): "Every evaluated opportunity becomes data. Freeze the
decision-time state. At minimum persist: timestamp, sport, league, event,
market, contract, Pinnacle raw odds, de-vigged fair probability, other model
probabilities, venue bid, venue ask, spread, depth, freshness, theoretical
EV, executable EV, agent recommendations, meta-controller decision, requested
size, approved size, execution policy, order details ... Create reproducible
frozen decision snapshots."

ONE BUILDER, TWO CALLERS.

  build_snapshot(...)      PURE. From the decision's own records -- the
                           paper_decisions row, the external_valuations row it
                           names, the canonical intent (225) where one was
                           recorded, the book observation it names, its entry
                           order's creation fields, its session -- build the
                           frozen object: every owner-listed field MEASURED
                           with its value and source, or UNAVAILABLE /
                           NOT_APPLICABLE with its reason (never a zero that
                           was not measured), plus the stable ids, the code /
                           strategy / model / config versions and the sha256
                           of the whole.
  freeze_decision(...)     OFFLINE. Reads those records through the ONE
                           point-in-time accessor (pit.py): the decision's
                           record set by its id, every input by the id the
                           decision names AND only if it was recorded no later
                           than the decision record itself. Then one INSERT.
  freeze_pass(...)         OFFLINE, bounded: the next decisions without a
                           snapshot for this code, oldest first.
  shadow_record(...)       ONLINE. The single function the CONTROL_PLANE_SHADOW
                           decision_hooks slot will call with the in-memory
                           decision-time objects (the row it just inserted,
                           the valuation row, the intent, the book, the
                           order). Pure build + exactly one INSERT, bounded,
                           never raises; nothing is wired to call it yet.

The same records give the same snapshot_id and the same frozen_sha whoever
builds it, online or offline (a test pins the two equal): that is what makes
the snapshots reproducible.

WHAT "DECISION-TIME" MEANS HERE. `decided_at` is the decision instant (the
clock the decision ran on); `as_of` is the instant the decision's own record
set (decision row, canonical intent, entry order) was complete. Inputs are
read by the id the decision names and must have been recorded no later than
the decision row; mutable columns (an order's state, a valuation's joined
outcome) are never read (pit.py hides them). The canonical intent is used
only if its sha verifies and its created_at is the decision instant; the
order only if its decided_at is.

Imports: the pure canonical intent, the book-ladder reader and this
package. No paper, order, venue, execution, funded or live module.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal, ROUND_HALF_UP

from .. import bettor_book_snapshot as BS
from .. import canonical_intent as CI
from . import ids as IDS
from . import pit as PIT
from . import store as ST

SNAPSHOT_VERSION = "CP_DECISION_SNAPSHOT_V1"
MEASURED, UNAVAILABLE, NOT_APPLICABLE = "MEASURED", "UNAVAILABLE", "NOT_APPLICABLE"
SRC_OFFLINE, SRC_HOOK = "RECORDS_OFFLINE", "SHADOW_HOOK"

#: the owner's field list (section 6), in the owner's order; migration 257's
#: cp_frozen_complete CHECKs exactly these
OWNER_FIELDS = (
    "timestamp", "sport", "league", "event", "market", "contract",
    "pinnacle_raw_odds", "devigged_fair_probability",
    "other_model_probabilities", "venue_bid", "venue_ask", "spread", "depth",
    "freshness", "theoretical_ev", "executable_ev", "agent_recommendations",
    "meta_controller_decision", "requested_size", "approved_size",
    "execution_policy", "order_details")

LONG_INTENT = "ORDER_INTENT_BUY_LONG"
SHORT_INTENT = "ORDER_INTENT_BUY_SHORT"
#: top-of-book levels kept per side in the frozen depth (bounded payload)
DEPTH_LEVELS_KEPT = 10

# the columns read (never a hidden one)
DECISION_COLS = (
    "decision_id", "session_id", "account_id", "decided_at", "valuation_id",
    "us_market_slug", "holding_side", "intent", "fixture", "label", "verdict",
    "refusal", "refusals", "p_internal", "internal_model", "p_pinnacle",
    "pinnacle", "p_blended", "book_obs_id", "book", "proposed_qty",
    "limit_price", "economics", "policy_version", "policy_decision",
    "simulator_version", "strategy", "provenance")
VALUATION_COLS = (
    "id", "version", "provider", "book", "devig_method", "venue",
    "condition_id", "contract_selection", "sport_family", "market", "period",
    "line", "event_key", "raw_odds", "outcomes_priced", "expected_outcomes",
    "overround", "observed_at", "received_at", "probability", "payout_event",
    "payout_is_complement", "buy_intent", "us_market_slug", "record_purpose",
    "settlement_comparison", "decided_at")
BOOK_COLS = ("obs_id", "us_market_slug", "observed_at", "source", "bids",
             "offers", "market_state", "error", "read_basis")
INTENT_COLS = (
    "intent_id", "intent_version", "decision_id", "strategy",
    "strategy_version", "sleeve", "evidence", "opportunity_score", "derek",
    "karen", "allie", "eddie", "venue", "us_market_slug", "contract",
    "holding_side", "order_intent", "order_type", "time_in_force",
    "limit_price", "wire_price", "target_qty", "sizing_basis", "created_at",
    "content_sha")
ORDER_COLS = (
    "order_id", "idempotency_key", "account_id", "group_id", "role",
    "direction", "holding_side", "intent", "us_market_slug", "order_type",
    "time_in_force", "allow_partial", "qty", "limit_price", "wire_price",
    "decision_id", "decided_at", "eligible_at", "expires_at",
    "simulator_version", "strategy")
SESSION_COLS = ("session_id", "account_id", "started_at", "config_sha",
                "simulator_version")


# ═══════════════════════════ normal forms ═══════════════════════════════

def _j(v):
    """A jsonb value as Python (the driver hands jsonb back as text)."""
    if isinstance(v, (bytes, bytearray)):
        v = v.decode()
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _jsonish(v):
    """What a jsonb column holds after json.dumps(default=str): the online
    hook normalises its in-memory objects through this so they equal what
    the offline path reads back."""
    if v is None:
        return None
    return json.loads(json.dumps(_j(v), default=str))


def _q6(v) -> Decimal | None:
    """numeric(.., 6) as stored: Decimal at six places, half-up."""
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except Exception:                                         # noqa: BLE001
        return None
    if d != d:
        return None
    return d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _f(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _t(v) -> float | None:
    """Epoch seconds at microsecond resolution (datetime or number)."""
    return IDS.epoch(v)


def m(value, source: str, **extra) -> dict:
    return dict({"status": MEASURED, "value": value, "source": source},
                **extra)


def u(why: str, **extra) -> dict:
    return dict({"status": UNAVAILABLE, "why": str(why)}, **extra)


def na(why: str, **extra) -> dict:
    return dict({"status": NOT_APPLICABLE, "why": str(why)}, **extra)


def _status(c) -> str | None:
    if not isinstance(c, dict):
        return None
    return c.get("status") or ("MEASURED" if c.get("verdict") else None) \
        or (UNAVAILABLE if c.get("state") == "UNAVAILABLE" else None)


# ═══════════════════════════ the book at the decision ═══════════════════

def book_quote(book_row: dict | None, holding_side: str | None) -> dict:
    """Best bid / ask of the LONG book from a recorded observation, and the
    holding side's cost / exit / mid (a SHORT costs 1 - bid and exits at
    1 - ask). Pure; absences named, never zero."""
    if book_row is None:
        return {"ok": False, "why": "NO_BOOK_OBSERVATION"}
    if book_row.get("error"):
        return {"ok": False, "why": "BOOK_UNREADABLE: %s"
                % str(book_row.get("error"))[:120]}
    md = {"bids": _j(book_row.get("bids")) or [],
          "offers": _j(book_row.get("offers")) or []}
    ask = BS.acquisition_ladder(md, intent=LONG_INTENT)
    bid = BS.acquisition_ladder(md, intent=SHORT_INTENT)
    long_ask = ask.get("best_api_price") if ask.get("ok") else None
    long_bid = bid.get("best_api_price") if bid.get("ok") else None
    out = {"ok": long_ask is not None or long_bid is not None,
           "long_bid": long_bid, "long_ask": long_ask,
           "bid_displayed": bid.get("displayed_depth") if bid.get("ok")
           else None,
           "ask_displayed": ask.get("displayed_depth") if ask.get("ok")
           else None,
           "bid_levels": [{"price": x["api_price"], "qty": x["qty"]}
                          for x in (bid.get("levels") or [])
                          [:DEPTH_LEVELS_KEPT]],
           "ask_levels": [{"price": x["api_price"], "qty": x["qty"]}
                          for x in (ask.get("levels") or [])
                          [:DEPTH_LEVELS_KEPT]],
           "why_bid": None if long_bid is not None
           else "NO_BID_LEVEL_PUBLISHED",
           "why_ask": None if long_ask is not None
           else "NO_OFFER_LEVEL_PUBLISHED"}
    out["spread"] = (round(long_ask - long_bid, 6)
                     if long_ask is not None and long_bid is not None
                     else None)
    side = holding_side or "LONG"
    if side == "SHORT":
        cost = None if long_bid is None else round(1.0 - long_bid, 6)
        exit_ = None if long_ask is None else round(1.0 - long_ask, 6)
    else:
        cost, exit_ = long_ask, long_bid
    out.update(side=side, side_cost=cost, side_exit=exit_,
               side_mid=(round((cost + exit_) / 2.0, 6)
                         if cost is not None and exit_ is not None else None))
    return out


def event_start_of(valuation: dict | None) -> tuple[float | None, str]:
    """The fixture's start instant AS RECORDED ON THE VALUATION ROW at the
    decision (settlement_comparison.reference_input.discovery_start, which
    pinnapi_primary.stamp_record writes): (epoch, source) or (None, why)."""
    if not valuation:
        return None, "VALUATION_ROW_UNAVAILABLE"
    sc = _j(valuation.get("settlement_comparison")) or {}
    ref = (sc.get("reference_input") or {}) if isinstance(sc, dict) else {}
    for src, v in (("settlement_comparison.reference_input.discovery_start",
                    ref.get("discovery_start")),
                   ("settlement_comparison.commence_time",
                    sc.get("commence_time") if isinstance(sc, dict) else None)):
        if v is None:
            continue
        e = _iso_epoch(v)
        if e is not None:
            return e, src
    return None, "NO_EVENT_START_RECORDED_ON_THE_VALUATION_ROW"


def _iso_epoch(v) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if not isinstance(v, str):
        return _t(v)
    import datetime as _dt
    s = v.strip().replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp()


# ═══════════════════════════ the pure builder ═══════════════════════════

def opportunity_of(decision: dict, valuation: dict | None) -> dict:
    """THE OPPORTUNITY (fixture | slug | side | line | scope), taken exactly
    as the canonical intent takes it (cand fixture / slug / side / line /
    period): line and period from the valuation row, else from the
    decision's label (which the decision copied from the same row)."""
    label = _j(decision.get("label")) or {}
    if valuation:
        line, scope = _f(valuation.get("line")), valuation.get("period")
        basis = "external_valuations.line / period"
    else:
        line, scope = _f(label.get("line")), label.get("period")
        basis = "paper_decisions.label.line / period"
    key = IDS.opportunity_key(fixture=decision.get("fixture"),
                              us_market_slug=decision.get("us_market_slug"),
                              holding_side=decision.get("holding_side"),
                              line=line, scope=scope)
    return {"key": key, "id": IDS.opportunity_id_from_key(key),
            "basis": basis}


def _intent_usable(intent: dict | None, decision: dict) -> tuple[dict | None, str | None]:
    if not intent:
        return None, ("DECISION_REFUSED_NO_CANONICAL_INTENT_IS_BUILT"
                      if decision.get("verdict") != "ENTER"
                      else "NO_CANONICAL_INTENT_RECORDED_FOR_THIS_ENTER")
    it = dict(intent)
    if it.get("decision_id") != decision.get("decision_id"):
        return None, "CANONICAL_INTENT_NAMES_ANOTHER_DECISION"
    try:
        ok = CI.verify_intent(it)
    except Exception as exc:                                   # noqa: BLE001
        return None, "CANONICAL_INTENT_UNVERIFIABLE:%s" % type(exc).__name__
    if not ok:
        return None, "CANONICAL_INTENT_SHA_DOES_NOT_VERIFY"
    ca, da = _t(it.get("created_at")), _t(decision.get("decided_at"))
    if ca is None or da is None or abs(ca - round(da, 3)) > 1e-3 + 1e-6:
        return None, "CANONICAL_INTENT_NOT_CREATED_AT_THE_DECISION_INSTANT"
    for k in ("evidence", "opportunity_score", "derek", "karen", "allie",
              "eddie", "contract", "sizing_basis"):
        it[k] = _j(it.get(k))
    return it, None


def _order_usable(order: dict | None, decision: dict) -> tuple[dict | None, str | None]:
    if decision.get("verdict") != "ENTER":
        return None, "DECISION_REFUSED_NO_ORDER"
    if not order:
        return None, "NO_PAPER_ORDER_RECORDED_FOR_THIS_ENTER"
    if order.get("decision_id") != decision.get("decision_id"):
        return None, "ORDER_NAMES_ANOTHER_DECISION"
    oa, da = _t(order.get("decided_at")), _t(decision.get("decided_at"))
    if oa is None or da is None or IDS.clock_us(oa) != IDS.clock_us(da):
        return None, "ORDER_NOT_DECIDED_AT_THE_DECISION_INSTANT"
    return order, None


def build_snapshot(*, decision: dict, valuation: dict | None = None,
                   intent: dict | None = None, book: dict | None = None,
                   order: dict | None = None, session: dict | None = None,
                   recommendations: list | None = None,
                   meta: dict | None = None, allocation: dict | None = None,
                   execution: dict | None = None,
                   code_sha: str | None = None,
                   missing: dict | None = None) -> dict:
    """THE FROZEN DECISION-TIME STATE (pure). `missing` names why an input
    the decision references could not be read at its clock (offline)."""
    missing = dict(missing or {})
    d = dict(decision)
    label = _j(d.get("label")) or {}
    pin = _j(d.get("pinnacle")) or {}
    econ = _j(d.get("economics")) or {}
    acq = econ.get("acquisition") if isinstance(econ, dict) else None
    acq = acq if isinstance(acq, dict) else {}
    pdec = _j(d.get("policy_decision")) or {}
    bk = _j(d.get("book")) or {}
    internal = _j(d.get("internal_model")) or {}
    v = dict(valuation) if valuation else None
    side = d.get("holding_side")
    verdict = d.get("verdict")
    refusals = list(d.get("refusals") or [])
    first_refusal = d.get("refusal") or (refusals[0] if refusals else None)
    code = code_sha or IDS.code_sha()
    opp = opportunity_of(d, v)
    it, it_why = _intent_usable(intent, d)
    od, od_why = _order_usable(order, d)
    q = book_quote(book, side) if book is not None else None
    decided = _t(d.get("decided_at"))
    if decided is None:
        raise ValueError("a decision snapshot needs the decision instant")
    sleeve = CI.sleeve_of(d.get("strategy"))
    f: dict = {}

    # ── timestamp / sport / league / event / market / contract ──────────
    f["timestamp"] = m({"decided_at": IDS.decision_clock(decided),
                        "decided_at_epoch": decided},
                       "paper_decisions.decided_at")
    if v and v.get("sport_family"):
        f["sport"] = m(v["sport_family"], "external_valuations.sport_family")
    else:
        f["sport"] = u(missing.get("valuation")
                       or ("VALUATION_ROW_UNAVAILABLE" if not v
                           else "SPORT_FAMILY_NOT_RECORDED"))
    if label.get("competition"):
        f["league"] = m(label["competition"],
                        "paper_decisions.label.competition (%s)"
                        % (label.get("basis") or {}).get("competition"))
    else:
        f["league"] = u("LEAGUE_NOT_ESTABLISHED: %s" % (
            (label.get("unknown") or {}).get("competition")
            or "the decision's label names no competition"))
    start, start_src = event_start_of(v)
    ev = {"fixture": d.get("fixture"),
          "event_key": (v or {}).get("event_key") or label.get("event_key"),
          "event_title": label.get("event_title"),
          "home_team": label.get("home_team"),
          "away_team": label.get("away_team"),
          "event_date": label.get("event_date"),
          "event_start": (m(start, start_src) if start is not None
                          else u(start_src))}
    if ev["fixture"] or ev["event_key"]:
        f["event"] = m(ev, "paper_decisions.fixture / label; "
                           "external_valuations.event_key")
    else:
        f["event"] = u("NO_FIXTURE_OR_EVENT_KEY_RECORDED")
    mk = (v or {}).get("market") or label.get("market_type")
    if mk:
        f["market"] = m({"market": mk,
                         "period": (v or {}).get("period")
                         if v else label.get("period"),
                         "line": _f((v or {}).get("line")) if v
                         else _f(label.get("line"))},
                        "external_valuations.market / period / line"
                        if v else "paper_decisions.label")
    else:
        f["market"] = u("MARKET_NOT_RECORDED")
    contract = {"us_market_slug": d.get("us_market_slug"),
                "holding_side": side, "order_intent": d.get("intent"),
                "payout_event": (v or {}).get("payout_event"),
                "payout_is_complement": (v or {}).get("payout_is_complement"),
                "selection": (v or {}).get("contract_selection")
                or label.get("participant"),
                "condition_id": (v or {}).get("condition_id"),
                "venue": (v or {}).get("venue")}
    if d.get("us_market_slug") and side:
        f["contract"] = m(contract, "paper_decisions + external_valuations")
    else:
        f["contract"] = u("CONTRACT_IDENTITY_INCOMPLETE")

    # ── Pinnacle raw odds / fair probability / other models ─────────────
    if not v:
        f["pinnacle_raw_odds"] = u(missing.get("valuation")
                                   or "VALUATION_ROW_UNAVAILABLE")
    else:
        raw = _j(v.get("raw_odds"))
        sc = _j(v.get("settlement_comparison")) or {}
        ref_raw = ((sc.get("reference_input") or {}).get("raw_odds")
                   if isinstance(sc, dict) else None)
        if raw or ref_raw:
            f["pinnacle_raw_odds"] = m(
                {"raw_odds": raw or None,
                 "reference_raw_odds": ref_raw or None,
                 "provider": v.get("provider"), "book": v.get("book"),
                 "devig_method": v.get("devig_method"),
                 "overround": _f(v.get("overround")),
                 "outcomes_priced": v.get("outcomes_priced"),
                 "expected_outcomes": v.get("expected_outcomes"),
                 "observed_at": _t(v.get("observed_at")),
                 "received_at": _t(v.get("received_at"))},
                "external_valuations.raw_odds / settlement_comparison."
                "reference_input.raw_odds")
        else:
            f["pinnacle_raw_odds"] = u(
                "RAW_ODDS_NOT_RECORDED_ON_THE_VALUATION_ROW")
    p_fair = _f(d.get("p_pinnacle"))
    if p_fair is None:
        p_fair = _f(pin.get("p"))
    if p_fair is not None:
        f["devigged_fair_probability"] = m(
            {"p": p_fair, "method": pin.get("method")
             or (v or {}).get("devig_method"),
             "overround": _f(pin.get("overround")),
             "provider": pin.get("provider") or (v or {}).get("provider"),
             "source_version": pin.get("source_version")
             or (v or {}).get("version"),
             "qualified": pin.get("qualified"),
             "qualification": pin.get("qualification"),
             "probability_is": "the stored de-vigged Pinnacle probability of "
                               "the event this contract pays on"},
            "paper_decisions.p_pinnacle / pinnacle")
    else:
        f["devigged_fair_probability"] = u(
            "NO_QUALIFIED_PINNACLE_PROBABILITY: %s"
            % (pin.get("refusal") or "none recorded"))
    others = {}
    p_int, p_bl = _f(d.get("p_internal")), _f(d.get("p_blended"))
    others["internal"] = (m(p_int, "paper_decisions.p_internal")
                          if p_int is not None else
                          u(internal.get("reason") or "NO_INTERNAL_MODEL"))
    others["blended"] = (m(p_bl, "paper_decisions.p_blended")
                         if p_bl is not None else
                         u("NO_BLENDED_PROBABILITY_RECORDED"))
    if p_int is not None or p_bl is not None:
        f["other_model_probabilities"] = m(others, "paper_decisions")
    else:
        f["other_model_probabilities"] = u(
            "NO_OTHER_MODEL_PROBABILITY_RECORDED: %s"
            % (internal.get("reason") or "the strategy uses none"),
            detail=others)

    # ── the venue book: bid / ask / spread / depth ──────────────────────
    if q is None:
        why = (missing.get("book") or
               ("NO_BOOK_READ_FOR_THIS_DECISION: refused before the book "
                "read (%s)" % first_refusal if d.get("book_obs_id") is None
                else "BOOK_OBSERVATION_UNAVAILABLE"))
        for k in ("venue_bid", "venue_ask", "spread", "depth"):
            f[k] = u(why)
    elif not q["ok"]:
        for k in ("venue_bid", "venue_ask", "spread", "depth"):
            f[k] = u(q.get("why") or "BOOK_UNREADABLE")
    else:
        src = "paper_book_observations #%s" % (book or {}).get("obs_id")
        f["venue_bid"] = (m({"long_bid": q["long_bid"],
                             "side_exit": q["side_exit"]}, src)
                          if q["long_bid"] is not None else u(q["why_bid"]))
        f["venue_ask"] = (m({"long_ask": q["long_ask"],
                             "side_cost": q["side_cost"]}, src)
                          if q["long_ask"] is not None else u(q["why_ask"]))
        f["spread"] = (m({"long_spread": q["spread"]}, src)
                       if q["spread"] is not None else
                       u("ONE_SIDED_BOOK: %s" % (q["why_bid"]
                                                 or q["why_ask"])))
        f["depth"] = m({"bid_displayed": q["bid_displayed"],
                        "ask_displayed": q["ask_displayed"],
                        "bid_levels": q["bid_levels"],
                        "ask_levels": q["ask_levels"],
                        "consumed_side_displayed": _f(bk.get(
                            "displayed_depth")),
                        "depth_within_limit": _f(econ.get(
                            "depth_within_limit")),
                        "basis": "DISPLAYED depth only (not queue ahead)"},
                       src)

    # ── freshness ───────────────────────────────────────────────────────
    fr = {"probability_age_s": _f(pin.get("age_s")),
          "probability_limit_s": _f(pin.get("limit_s")),
          "probability_qualified": pin.get("qualified"),
          "probability_qualification": pin.get("qualification"),
          "probability_refusal": pin.get("refusal"),
          "valuation_to_decision_lag_s": _f(pin.get(
              "decision_lag_after_valuation_s")),
          "book_age_s": _f(bk.get("age_at_decision_s"))
          if bk.get("age_at_decision_s") is not None
          else _f(econ.get("book_age_s")),
          "book_max_age_s": _f(econ.get("book_max_age_s"))}
    if fr["probability_age_s"] is not None or fr["book_age_s"] is not None:
        f["freshness"] = m(fr, "paper_decisions.pinnacle / book / economics")
    else:
        f["freshness"] = u("NO_FRESHNESS_STAMP_RECORDED: %s"
                           % (pin.get("refusal") or pin.get("qualification")
                              or "none"), detail=fr)

    # ── theoretical EV / executable EV ──────────────────────────────────
    best_pp = _f(econ.get("best_level_edge_pp"))
    if best_pp is None:
        best_pp = _f(pdec.get("gross_edge_pp"))
    side_cost = q.get("side_cost") if q and q.get("ok") else None
    if best_pp is not None:
        per = round(best_pp / 100.0, 9)
        f["theoretical_ev"] = m(
            {"per_contract": per, "basis": "p_fair - best displayed level "
             "price, before fees and depth (the decision's own edge)",
             "edge_pp": best_pp},
            "paper_decisions.economics.best_level_edge_pp")
    elif p_fair is not None and side_cost is not None:
        f["theoretical_ev"] = m(
            {"per_contract": round(p_fair - side_cost, 9),
             "basis": "p_fair - best displayed side cost of the recorded "
                      "book, before fees and depth"},
            "derived: paper_decisions.p_pinnacle, paper_book_observations")
    else:
        f["theoretical_ev"] = u(
            "THEORETICAL_EV_NOT_COMPUTABLE: %s" % (
                "no fair probability" if p_fair is None
                else "no executable side price"))
    exec_ev = _f(acq.get("expected_net_profit_usd"))
    if exec_ev is None:
        exec_ev = _f(pdec.get("net_expected_profit_usd"))
    eddie = (it or {}).get("eddie") if it else None
    if exec_ev is not None:
        f["executable_ev"] = m(
            {"usd": exec_ev, "edge_at_vwap_pp": _f(acq.get("edge_at_vwap_pp")),
             "fees_usd": _f(acq.get("fees_usd")), "qty": _f(acq.get("qty")),
             "vwap": _f(acq.get("vwap")),
             "label": econ.get("label") or econ.get("economics_label"),
             "conditional_on": econ.get("conditional_on"),
             "eddie": eddie if isinstance(eddie, dict) else None},
            "paper_decisions.economics.acquisition (fees + depth walk "
            "within the limit)")
    else:
        f["executable_ev"] = u(
            "NOT_COMPUTED: the decision refused before its economics (%s)"
            % first_refusal if verdict != "ENTER"
            else "EXECUTABLE_EV_NOT_RECORDED_ON_THE_DECISION")

    # ── agent recommendations / meta-controller ─────────────────────────
    recs = {"derek": {"status": MEASURED, "verdict": verdict,
                      "refusal": first_refusal, "refusals": refusals,
                      "strategy": d.get("strategy"),
                      "policy_version": d.get("policy_version")}}
    for comp in ("opportunity_score", "karen", "allie", "eddie"):
        c = (it or {}).get(comp) if it else None
        if isinstance(c, dict) and c:
            recs[comp] = c
        else:
            recs[comp] = u(it_why or "COMPONENT_NOT_RECORDED_ON_THE_INTENT")
    recs["control_plane"] = (
        m(list(recommendations), "cp_agent_recommendations (stream B)")
        if recommendations else
        u("NO_CONTROL_PLANE_AGENT_RECOMMENDATIONS_RECORDED"))
    f["agent_recommendations"] = m(
        recs, "paper_decisions (Derek) + canonical_decision_intents "
              "(Karen, Allie, Eddie, Opportunity Score)")
    f["meta_controller_decision"] = (
        m(meta, "cp_meta_decisions (stream B)") if meta else
        u("NO_META_CONTROLLER_DECISION_RECORDED: the meta-controller "
          "(stream B, cp_meta_decisions) is not integrated at this stage; "
          "the canonical decision is Derek's verdict (agent_recommendations)"))

    # ── requested size / approved size ──────────────────────────────────
    req_qty = _q6(d.get("proposed_qty"))
    if req_qty is not None:
        f["requested_size"] = m(
            {"qty": req_qty, "limit_price": _q6(d.get("limit_price")),
             "budget_usd": _f(econ.get("budget_usd"))},
            "paper_decisions.proposed_qty / limit_price")
    else:
        f["requested_size"] = u("NOT_SIZED: refused before sizing (%s)"
                                % first_refusal if verdict != "ENTER"
                                else "NO_PROPOSED_QUANTITY_RECORDED")
    allie = (it or {}).get("allie") if it else None
    if verdict != "ENTER":
        f["approved_size"] = m({"qty": Decimal(0),
                                "basis": "DECISION_REFUSED_NOTHING_APPROVED"},
                               "paper_decisions.verdict")
    elif it is not None:
        f["approved_size"] = m(
            {"qty": _q6(it.get("target_qty")),
             "allie_final_allocatable_usd":
                 (allie or {}).get("final_allocatable_usd")
                 if isinstance(allie, dict) else None,
             "allie_status": (allie or {}).get("status")
             if isinstance(allie, dict) else None,
             "allocation": allocation},
            "canonical_decision_intents.target_qty")
    elif od is not None:
        f["approved_size"] = m({"qty": _q6(od.get("qty")),
                                "allocation": allocation},
                               "paper_orders.qty (no canonical intent)")
    elif req_qty is not None:
        f["approved_size"] = m({"qty": req_qty,
                                "basis": "ENTER at the proposed quantity; "
                                         "no intent or order recorded",
                                "allocation": allocation},
                               "paper_decisions.proposed_qty")
    else:
        f["approved_size"] = u("ENTER_WITHOUT_A_RECORDED_QUANTITY")

    # ── execution policy / order details ────────────────────────────────
    latency = econ.get("latency") if isinstance(econ, dict) else None
    latency = latency if isinstance(latency, dict) else {}
    if verdict != "ENTER":
        f["execution_policy"] = na("DECISION_REFUSED_NO_EXECUTION")
        f["order_details"] = na("DECISION_REFUSED_NO_ORDER")
    else:
        pol_src = it or od
        if pol_src is not None:
            f["execution_policy"] = m(
                {"order_type": pol_src.get("order_type"),
                 "time_in_force": pol_src.get("time_in_force"),
                 "allow_partial": (od or {}).get("allow_partial"),
                 "decision_to_execution_delay_s": _f(latency.get(
                     "decision_to_execution_delay_s")),
                 "fill_rule": latency.get("fill_rule"),
                 "eddie_recommendation": (eddie or {}).get("recommendation")
                 if isinstance(eddie, dict) else None,
                 "control_plane_execution": execution},
                "canonical_decision_intents" if it else "paper_orders")
        else:
            f["execution_policy"] = u(it_why or od_why)
        if od is not None:
            f["order_details"] = m(
                {"order_id": od.get("order_id"),
                 "idempotency_key": od.get("idempotency_key"),
                 "group_id": od.get("group_id"), "role": od.get("role"),
                 "direction": od.get("direction"),
                 "holding_side": od.get("holding_side"),
                 "intent": od.get("intent"),
                 "order_type": od.get("order_type"),
                 "time_in_force": od.get("time_in_force"),
                 "allow_partial": od.get("allow_partial"),
                 "qty": _q6(od.get("qty")),
                 "limit_price": _q6(od.get("limit_price")),
                 "wire_price": _q6(od.get("wire_price")),
                 "eligible_at": _t(od.get("eligible_at")),
                 "expires_at": _t(od.get("expires_at"))},
                "paper_orders (creation fields only)")
        else:
            f["order_details"] = u(od_why)

    # ── versions, inputs, the observation basis ─────────────────────────
    model_version = (v or {}).get("version") or pin.get("source_version")
    versions = {"snapshot_version": SNAPSHOT_VERSION,
                "strategy_version": d.get("policy_version"),
                "model_version": model_version,
                "internal_model": internal.get("model_id"),
                "simulator_version": d.get("simulator_version"),
                "config_sha": (session or {}).get("config_sha"),
                "intent_version": (it or {}).get("intent_version"),
                "intent_sha": (it or {}).get("content_sha"),
                "provenance_sha": (IDS.sha256(_j(d.get("provenance")))
                                   if d.get("provenance") is not None
                                   else None)}
    inputs = {"valuation_id": d.get("valuation_id"),
              "book_obs_id": d.get("book_obs_id"),
              "session_id": d.get("session_id"),
              "account_id": d.get("account_id"),
              "order_id": (od or {}).get("order_id"),
              "intent_id": (it or {}).get("intent_id"),
              "missing": missing or None}
    basis = {"holding_side": side,
             "side_cost_0": side_cost,
             "side_exit_0": q.get("side_exit") if q and q.get("ok") else None,
             "side_mid_0": q.get("side_mid") if q and q.get("ok") else None,
             "p_fair": p_fair,
             "threshold_edge": _f(econ.get("threshold_edge_probability"))
             if econ.get("threshold_edge_probability") is not None
             else _f(pdec.get("threshold_edge_probability")),
             "event_start": start,
             "order_id": (od or {}).get("order_id"),
             "group_id": (od or {}).get("group_id"),
             "order_intent": d.get("intent"),
             "limit_price": _f(d.get("limit_price"))}
    identity = {"opportunity_key": opp["key"], "opportunity_id": opp["id"],
                "opportunity_basis": opp["basis"],
                "decision_id": d.get("decision_id"),
                "intent_id": (it or {}).get("intent_id"),
                "strategy": d.get("strategy"), "sleeve": sleeve,
                "verdict": verdict, "refusals": refusals,
                "decided_at": IDS.decision_clock(decided)}
    frozen = {"snapshot_version": SNAPSHOT_VERSION, "identity": identity,
              "fields": f, "versions": versions, "inputs": inputs,
              "observation_basis": basis}
    frozen = json.loads(IDS.canonical_json(frozen))
    frozen_sha = IDS.sha256(frozen)
    sid = IDS.snapshot_id(opportunity_id=opp["id"], decided_at=decided,
                          code_sha=code)

    def col(name):
        return None if f[name]["status"] != MEASURED else name
    event_col = (d.get("fixture") or ev.get("event_key")
                 or label.get("event_title")) if col("event") else None
    return {"snapshot_id": sid, "decision_id": d.get("decision_id"),
            "opportunity_id": opp["id"], "opportunity_key": opp["key"],
            "intent_id": (it or {}).get("intent_id"),
            "decided_at": decided,
            "sport": f["sport"]["value"] if col("sport") else None,
            "league": f["league"]["value"] if col("league") else None,
            "event": (str(event_col) if event_col else None),
            "market": f["market"]["value"]["market"] if col("market")
            else None,
            "contract": contract, "strategy": d.get("strategy"),
            "strategy_version": d.get("policy_version"), "sleeve": sleeve,
            "verdict": verdict, "model_version": model_version,
            "config_sha": (session or {}).get("config_sha"),
            "code_sha": code, "frozen": frozen, "frozen_sha": frozen_sha,
            "snapshot_version": SNAPSHOT_VERSION}


# ═══════════════════════════ offline: from the records ══════════════════

async def read_record_set(conn, decision_id: str, *,
                          horizon: float) -> dict:
    """The decision's records through the point-in-time accessor. The
    decision's own record set (decision, canonical intent, entry order) by
    decision id; every input by the id the decision names, knowable no later
    than the decision record."""
    rd = PIT.Reader(conn, horizon=horizon)
    d = await rd.one("paper_decisions", clock=horizon, cols=DECISION_COLS,
                     where=[("decision_id", "=", decision_id)])
    if d is None:
        return {"ok": False, "why": "DECISION_NOT_KNOWABLE_AT_THE_HORIZON"}
    clock = d["__recorded_at"]
    missing: dict = {}
    v = b = s = None
    if d.get("valuation_id") is not None:
        v = await rd.one("external_valuations", clock=clock,
                         cols=VALUATION_COLS,
                         where=[("id", "=", int(d["valuation_id"]))])
        if v is None:
            missing["valuation"] = ("VALUATION_ROW_NOT_RECORDED_BY_THE_"
                                    "DECISION_CLOCK")
    else:
        missing["valuation"] = "THE_DECISION_NAMES_NO_VALUATION"
    if d.get("book_obs_id") is not None:
        b = await rd.one("paper_book_observations", clock=clock,
                         cols=BOOK_COLS,
                         where=[("obs_id", "=", int(d["book_obs_id"]))])
        if b is None:
            missing["book"] = "BOOK_OBSERVATION_NOT_RECORDED_BY_THE_DECISION_CLOCK"
    if d.get("session_id") is not None:
        s = await rd.one("paper_sessions", clock=clock, cols=SESSION_COLS,
                         where=[("session_id", "=", d["session_id"])])
    it = await rd.one("canonical_decision_intents", clock=horizon,
                      cols=INTENT_COLS,
                      where=[("decision_id", "=", decision_id)])
    od = await rd.one("paper_orders", clock=horizon, cols=ORDER_COLS,
                      where=[("decision_id", "=", decision_id),
                             ("role", "=", "ENTRY")],
                      order=[("created_at", "ASC"), ("order_id", "ASC")])
    record_set = [d["__recorded_at"]] + [x["__recorded_at"] for x in (it, od)
                                         if x is not None]
    evidence = [x["__recorded_at"] for x in (v, b, s) if x is not None]
    decided = _t(d["decided_at"])
    return {"ok": True, "decision": d, "valuation": v, "book": b,
            "session": s, "intent": it, "order": od, "missing": missing,
            "as_of": max([decided] + record_set),
            "evidence_max_recorded_at": max(evidence) if evidence else None,
            "audit": rd.audit.as_dict()}


async def freeze_decision(conn, decision_id: str, *,
                          horizon: float | None = None,
                          code_sha: str | None = None,
                          write: bool = True) -> dict:
    """FREEZE ONE DECISION from its records (offline). Idempotent: the same
    code over the same records gives the same snapshot_id and frozen_sha,
    and the second INSERT is a no-op."""
    hz = float(horizon if horizon is not None else time.time())
    rs = await read_record_set(conn, decision_id, horizon=hz)
    if not rs["ok"]:
        return rs
    snap = build_snapshot(decision=rs["decision"], valuation=rs["valuation"],
                          intent=rs["intent"], book=rs["book"],
                          order=rs["order"], session=rs["session"],
                          code_sha=code_sha, missing=rs["missing"])
    snap.update(as_of=rs["as_of"],
                evidence_max_recorded_at=rs["evidence_max_recorded_at"],
                source=SRC_OFFLINE)
    out = {"ok": True, "snapshot": snap, "audit": rs["audit"]}
    if write:
        out["inserted"] = await ST.insert_snapshot(conn, snap)
    return out


async def freeze_pass(conn, *, now: float | None = None, since: float,
                      limit: int = 200, code_sha: str | None = None) -> dict:
    """THE OFFLINE FREEZER (bounded): decisions knowable by `now`, decided
    at or after `since`, without a snapshot for this code, oldest first."""
    hz = float(now if now is not None else time.time())
    code = code_sha or IDS.code_sha()
    limit = max(1, min(int(limit), 1000))
    rd = PIT.Reader(conn, horizon=hz)
    import datetime as _dt
    cand = await rd.rows(
        "paper_decisions", clock=hz, cols=("decision_id", "decided_at"),
        where=[("decided_at", ">=", _dt.datetime.fromtimestamp(
            float(since), tz=_dt.timezone.utc))],
        order=[("decided_at", "ASC"), ("decision_id", "ASC")],
        limit=min(PIT.MAX_LIMIT, limit * 4))
    done = await ST.frozen_decisions(conn, [c["decision_id"] for c in cand],
                                     code_sha=code)
    out = {"candidates": len(cand), "frozen": 0, "already": 0, "failed": {},
           "code_sha": code}
    for c in cand:
        if out["frozen"] >= limit:
            break
        if c["decision_id"] in done:
            out["already"] += 1
            continue
        try:
            got = await freeze_decision(conn, c["decision_id"], horizon=hz,
                                        code_sha=code)
        except Exception as exc:                               # noqa: BLE001
            k = type(exc).__name__
            out["failed"][k] = out["failed"].get(k, 0) + 1
            continue
        if got.get("ok") and got.get("inserted"):
            out["frozen"] += 1
        elif got.get("ok"):
            out["already"] += 1
        else:
            k = got.get("why") or "UNKNOWN"
            out["failed"][k] = out["failed"].get(k, 0) + 1
    return out


# ═══════════════════════════ online: the shadow hook ════════════════════

_JSONB_DECISION = ("label", "internal_model", "pinnacle", "book", "economics",
                   "policy_decision", "provenance")
_JSONB_VALUATION = ("raw_odds", "settlement_comparison")
_JSONB_INTENT = ("evidence", "opportunity_score", "derek", "karen", "allie",
                 "eddie", "contract", "sizing_basis")


def _online_form(row: dict | None, jsonb: tuple) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for k in jsonb:
        if k in out:
            out[k] = _jsonish(out[k])
    return out


async def shadow_record(conn, *, decision: dict, valuation: dict | None = None,
                        intent: dict | None = None, book: dict | None = None,
                        order: dict | None = None, session: dict | None = None,
                        recommendations: list | None = None,
                        meta: dict | None = None,
                        allocation: dict | None = None,
                        execution: dict | None = None) -> dict:
    """THE CONTROL_PLANE_SHADOW HOOK (online). Called by the executing
    process with the decision-time objects it already holds: `decision` is
    the paper_decisions row as inserted (same keys), `valuation` the
    external_valuations row the candidate came from, `intent` the canonical
    intent (or None), `book` {obs_id, observed_at, bids, offers, error},
    `order` the entry order's creation fields, `session` {session_id,
    config_sha}. Optional control-plane outputs (stream B/C/D) ride along.

    PURE, BOUNDED, NO I/O BEYOND ONE INSERT (cp_decision_snapshots, ON
    CONFLICT DO NOTHING). Never raises: a SHADOW recorder must never change
    or stop the decision; a failure is returned, not thrown. SHADOW: nothing
    it writes is read by any decision path."""
    try:
        d = _online_form(decision, _JSONB_DECISION)
        snap = build_snapshot(
            decision=d, valuation=_online_form(valuation, _JSONB_VALUATION),
            intent=_online_form(intent, _JSONB_INTENT),
            book=_online_form(book, ("bids", "offers")),
            order=order, session=session,
            recommendations=_jsonish(recommendations),
            meta=_jsonish(meta), allocation=_jsonish(allocation),
            execution=_jsonish(execution))
        snap.update(as_of=snap["decided_at"], evidence_max_recorded_at=None,
                    source=SRC_HOOK)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "why": "SNAPSHOT_BUILD_FAILED:%s"
                % type(exc).__name__}
    try:
        inserted = await ST.insert_snapshot(conn, snap)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "why": "SNAPSHOT_INSERT_FAILED:%s"
                % type(exc).__name__, "snapshot_id": snap["snapshot_id"]}
    return {"ok": True, "inserted": inserted,
            "snapshot_id": snap["snapshot_id"],
            "opportunity_id": snap["opportunity_id"],
            "frozen_sha": snap["frozen_sha"]}
