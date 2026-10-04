"""CAPITAL-HOUR ECONOMICS (RESEARCH). Pure; no I/O.

WHY CAPITAL-HOURS. Two positions that each net $10 are not equally good if
one tied up $1,000 for three days and the other $200 for two hours. The
north star is net profit per unit of risk and capital-time, so every
position is measured against the capital it locked and for how long.

PER POSITION (one book: PAPER, ACTUAL, or COUNTERFACTUAL):

  capital committed     acquisition cost of every contract bought, fees
                        included (for a binary contract also its maximum
                        loss)
  capital path          the open cost basis as a step function of time:
                        + q x price + fee on a buy, - average cost x q on a
                        sale, released at settlement. Before the first fill,
                        a PAPER entry order's cash reservation counts from
                        the order's creation (capital is locked from there)
  capital-hours         the integral of the capital path over time, USD x h
  time committed        first capital lock -> release (settlement, or the
                        sale that emptied the position)
  net profit            sale proceeds (net of fees) + settlement payout -
                        acquisition cost; CLOSED positions only (an open
                        position has no realized profit: null, named)
  expected net profit   bought x p - acquisition cost, p = the entry
                        decision's probability that the held side pays
  expected release      event start + the median settlement lag (settled_at
                        - event start) measured from settlements RECORDED
                        BEFORE this position's first fill (no look-ahead),
                        >= MIN_LAG_SAMPLES samples; otherwise null, named
  expected capital-hours  capital committed x (expected release - first
                        capital lock): the entry plan, held to settlement
  REALIZED_PROFIT_PER_CAPITAL_HOUR  = net profit / capital-hours
  EXPECTED_PROFIT_PER_CAPITAL_HOUR  = expected net profit / expected
                                      capital-hours

AN OPEN POSITION'S ROW IS TIME-INVARIANT BETWEEN EVENTS: it stores the
capital-hours accrued to its last event and the cost basis still accruing;
capital-hours to date = capital_hours + open_cost_basis x hours since
last_event_at (computed by the reader).

THE COUNTERFACTUAL (book COUNTERFACTUAL, kind HOLD_TO_SETTLEMENT): for a
position that sold before settlement and whose contract's settlement is
known, the same buys held to that settlement -- its net profit and
capital-hours. It is a separate row in a separate book and is never added
to the real one.

PORTFOLIO (per book, never summed): capital locked now (open cost basis,
plus open PAPER order reservations), the expected release schedule with its
basis, capital waiting for settlement (event started, not settled), capital
overdue (past its expected release), realized and expected profit per
capital-hour, capital utilization (capital-hours in the window / (account
capital x window hours)), average and idle capital.
"""
from __future__ import annotations

from . import common as C

VERSION = "POS_ECONOMICS_V1"
EPS = 1e-9
MIN_LAG_SAMPLES = 5
WINDOW_DAYS = 30.0
HOUR = 3600.0

R_OPEN = "POSITION_OPEN_NO_REALIZED_PROFIT"
R_NO_P = "ENTRY_DECISION_CARRIES_NO_PROBABILITY"
R_NO_START = "NO_EVENT_START_RECORDED_FOR_THIS_CONTRACT"
R_NO_LAG = "FEWER_THAN_%d_SETTLEMENT_LAG_SAMPLES_BEFORE_ENTRY" % MIN_LAG_SAMPLES
R_ZERO_CH = "ZERO_CAPITAL_HOURS"
R_NO_BUY = "NO_BUY_FILL"
R_RELEASE_BEFORE = "EXPECTED_RELEASE_NOT_AFTER_CAPITAL_LOCK"


def settlement_lag(samples, *, as_of=None):
    """(median lag seconds, n) over samples [(recorded_at, lag_s)] recorded
    at or before `as_of` (all when None). None below MIN_LAG_SAMPLES."""
    lags = [lag for rec, lag in samples
            if lag is not None and lag >= 0
            and (as_of is None or (rec is not None and rec <= as_of))]
    if len(lags) < MIN_LAG_SAMPLES:
        return None, len(lags)
    return C.median(lags), len(lags)


def compute_position(pos: dict, *, lag_samples=(), book=None,
                     counterfactual_kind=None, basis=None) -> dict:
    """The economics of ONE position record (see reads.py for the shape).
    Returns a dict with an `unmeasured` map and the capital `segments`
    [(t0, t1, usd)] (not persisted)."""
    out = C.Out(book=book or pos["book"], position_key=pos["position_key"],
                counterfactual_kind=counterfactual_kind,
                basis_book=(basis or {}).get("book"),
                basis_position_key=(basis or {}).get("position_key"),
                venue=pos.get("venue"), group_id=pos.get("group_id"),
                us_market_slug=pos.get("us_market_slug"),
                holding_side=pos.get("holding_side"),
                strategy=pos.get("strategy"), version=VERSION,
                label=C.LABEL,
                # the scope (migration 227): carried for the sleeve-scoped
                # metrics and forecasts; not a pos_position_economics column
                # (the sleeve is the group's durable classification, read
                # again whenever it is needed -- never a copy that drifts)
                sleeve=C.sleeve_of(pos), sleeve_basis=pos.get("sleeve_basis"),
                policy_version=pos.get("policy_version"),
                classifier_version=pos.get("classifier_version"))
    events = sorted((e for e in pos.get("events") or []
                     if C.num(e.get("t")) is not None
                     and (C.num(e.get("qty")) or 0) > 0),
                    key=lambda e: (e["t"], 0 if e["kind"] == "BUY" else 1))
    buys = [e for e in events if e["kind"] == "BUY"]
    if not buys:
        out["state"] = "OPEN"
        for k in ("net_profit_usd", "expected_net_profit_usd",
                  "capital_hours", "expected_capital_hours",
                  "realized_profit_per_capital_hour",
                  "expected_profit_per_capital_hour",
                  "capital_committed_usd"):
            out.put(k, None, R_NO_BUY)
        out["segments"] = []
        out["detail"] = {"events": len(events)}
        return out
    first_buy = buys[0]["t"]
    res = pos.get("reservation") or None
    segs = []
    start = first_buy
    if res and C.num(res.get("t")) is not None and res["t"] < first_buy \
            and (C.num(res.get("usd")) or 0) > 0:
        start = res["t"]
        segs.append((res["t"], first_buy, float(res["usd"])))
    open_q = basis_usd = bought = buy_cost = proceeds = sold = 0.0
    peak = 0.0
    prev = None
    bad_sale = False
    for e in events:
        if prev is not None and basis_usd > EPS and e["t"] > prev:
            segs.append((prev, e["t"], basis_usd))
        q = float(C.num(e["qty"]))
        px = C.num(e.get("price")) or 0.0
        fee = C.num(e.get("fee_usd")) or 0.0
        if e["kind"] == "BUY":
            open_q += q
            bought += q
            basis_usd += q * px + fee
            buy_cost += q * px + fee
        else:
            if open_q <= EPS:
                bad_sale = True
                prev = e["t"]
                continue
            take = min(q, open_q)
            avg = basis_usd / open_q
            basis_usd -= avg * take
            open_q -= take
            sold += take
            proceeds += take * px - fee
        peak = max(peak, basis_usd)
        prev = e["t"]
    last_event = prev
    payout = 0.0
    closed_at = None
    st = pos.get("settlement") or None
    release_basis_used = None
    if open_q > EPS and st and C.num(st.get("payout_per_contract")) \
            is not None and C.num(st.get("t")) is not None:
        t_set = max(float(st["t"]), last_event)
        if basis_usd > EPS and t_set > last_event:
            segs.append((last_event, t_set, basis_usd))
        payout = open_q * float(st["payout_per_contract"])
        open_q = 0.0
        basis_usd = 0.0
        closed_at = t_set
        last_event = t_set
        release_basis_used = "SETTLEMENT"
    elif open_q <= EPS:
        closed_at = last_event
        basis_usd = 0.0
        release_basis_used = "FULL_EXIT_BY_SALE"
    state = "CLOSED" if closed_at is not None else "OPEN"
    out["state"] = state
    out["opened_at"] = start
    out["first_fill_at"] = first_buy
    out["last_event_at"] = last_event
    out["released_at"] = closed_at
    out["bought_qty"] = C.rnd(bought)
    out.put("capital_committed_usd", C.rnd(buy_cost))
    out["peak_capital_usd"] = C.rnd(peak)
    out["open_cost_basis_usd"] = C.rnd(basis_usd) if state == "OPEN" else 0.0
    ch = sum((t1 - t0) / HOUR * cap for t0, t1, cap in segs)
    out.put("capital_hours", C.rnd(ch, 9))
    out["time_committed_h"] = (C.rnd((closed_at - start) / HOUR)
                               if state == "CLOSED" else None)
    if state == "CLOSED":
        net = proceeds + payout - buy_cost
        out.put("net_profit_usd", C.rnd(net))
        out.put("realized_profit_per_capital_hour",
                C.rnd(net / ch, 9) if ch > EPS else None, R_ZERO_CH)
        out["realized_edge_per_dollar"] = (C.rnd(net / buy_cost, 9)
                                           if buy_cost > EPS else None)
    else:
        out.put("net_profit_usd", None, R_OPEN)
        out.put("realized_profit_per_capital_hour", None, R_OPEN)
        out["realized_edge_per_dollar"] = None
    p = C.num(pos.get("probability"))
    out["probability"] = p
    out["probability_basis"] = pos.get("probability_basis")
    if p is None:
        out.put("expected_net_profit_usd", None, R_NO_P)
        out["predicted_edge_per_dollar"] = None
    else:
        exp_net = bought * p - buy_cost
        out.put("expected_net_profit_usd", C.rnd(exp_net))
        out["predicted_edge_per_dollar"] = (C.rnd(exp_net / buy_cost, 9)
                                            if buy_cost > EPS else None)
    # expected release: event start + the lag known at entry (no look-ahead)
    ev = C.num(pos.get("event_start_at"))
    out["event_start_at"] = ev
    lag, lag_n = settlement_lag(lag_samples, as_of=first_buy)
    exp_rel = None
    if ev is None:
        out.put("expected_capital_hours", None, R_NO_START)
        out["release_basis"] = None
    elif lag is None:
        out.put("expected_capital_hours", None, R_NO_LAG)
        out["release_basis"] = None
    else:
        exp_rel = ev + lag
        out["release_basis"] = ("EVENT_START(%s)+MEDIAN_SETTLEMENT_LAG_AT_ENTRY"
                                "(n=%d,%.0fs)" % (
                                    pos.get("event_start_basis") or "?",
                                    lag_n, lag))
        if exp_rel <= start:
            out.put("expected_capital_hours", None, R_RELEASE_BEFORE)
        else:
            out.put("expected_capital_hours",
                    C.rnd(buy_cost * (exp_rel - start) / HOUR, 9))
    out["expected_release_at"] = exp_rel
    en, ech = out.get("expected_net_profit_usd"), out.get(
        "expected_capital_hours")
    if en is None or ech is None:
        out.put("expected_profit_per_capital_hour", None,
                out["unmeasured"].get("expected_net_profit_usd")
                or out["unmeasured"].get("expected_capital_hours"))
    elif ech <= EPS:
        out.put("expected_profit_per_capital_hour", None, R_ZERO_CH)
    else:
        out.put("expected_profit_per_capital_hour", C.rnd(en / ech, 9))
    out["segments"] = segs
    out["detail"] = {
        "sold_qty": C.rnd(sold), "sale_proceeds_net_usd": C.rnd(proceeds),
        "settlement_payout_usd": C.rnd(payout),
        "release": release_basis_used,
        "settlement_basis": (st or {}).get("basis"),
        "settlement_outcome": (st or {}).get("outcome"),
        "capital_path_basis": (
            "PRE_FILL_RESERVATION+OPEN_COST_BASIS" if res and start < first_buy
            else "OPEN_COST_BASIS"),
        "capital_hours_basis": ("ACCRUED_TO_LAST_EVENT_PLUS_OPEN_COST_BASIS_"
                                "SINCE" if state == "OPEN" else "FULL_LIFE"),
        "sale_without_holding": bad_sale,
        "decision_id": pos.get("decision_id"),
        "lag_samples_at_entry": lag_n}
    return out


def counterfactual_hold(pos: dict, *, lag_samples=()):
    """HOLD_TO_SETTLEMENT counterfactual of a position that sold before
    settlement, when its contract's settlement is known. None otherwise."""
    sells = [e for e in pos.get("events") or [] if e["kind"] == "SELL"]
    cs = pos.get("contract_settlement")
    if not sells or not cs or C.num(cs.get("payout_per_contract")) is None:
        return None
    cf = dict(pos)
    cf["events"] = [e for e in pos["events"] if e["kind"] == "BUY"]
    cf["settlement"] = cs
    cf["position_key"] = "cf:HOLD_TO_SETTLEMENT:" + pos["position_key"]
    return compute_position(cf, lag_samples=lag_samples,
                            book="COUNTERFACTUAL",
                            counterfactual_kind="HOLD_TO_SETTLEMENT",
                            basis={"book": pos["book"],
                                   "position_key": pos["position_key"]})


def _clip(segs, lo, hi):
    return sum(max(0.0, min(t1, hi) - max(t0, lo)) / HOUR * cap
               for t0, t1, cap in segs)


def portfolio(econs: list, *, book: str, now: float, account_capital=None,
              account_capital_basis=None, open_reservations_usd=None,
              reservations_why=None, lag_samples=(),
              window_days=WINDOW_DAYS) -> dict:
    """The capital picture of ONE book from its positions' economics."""
    out = C.Out(label=C.LABEL, authority=C.AUTHORITY, book=book,
                version=VERSION, summed_with_other_book=False)
    lo = now - window_days * 86400.0
    opened = [e for e in econs if e.get("capital_committed_usd") is not None]
    open_ = [e for e in opened if e["state"] == "OPEN"]
    closed = [e for e in opened if e["state"] == "CLOSED"]
    out["positions"] = len(opened)
    out["open_positions"] = len(open_)
    out["closed_positions"] = len(closed)
    locked_pos = sum(e.get("open_cost_basis_usd") or 0.0 for e in open_)
    out.put("capital_locked_positions_usd", C.rnd(locked_pos))
    out.put("capital_locked_reservations_usd",
            C.rnd(open_reservations_usd) if open_reservations_usd is not None
            else None, reservations_why or "NOT_MEASURED")
    out.put("capital_locked_usd", C.rnd(locked_pos + (
        open_reservations_usd or 0.0)))
    out["capital_locked_basis"] = (
        "OPEN_COST_BASIS+OPEN_ORDER_RESERVATIONS"
        if open_reservations_usd is not None
        else "OPEN_COST_BASIS_ONLY (%s)" % (reservations_why or "?"))
    # ── release schedule, with the lag known now ──────────────────────
    lag, lag_n = settlement_lag(lag_samples)
    buckets = {k: {"positions": 0, "capital_usd": 0.0} for k in (
        "OVERDUE", "WITHIN_6H", "WITHIN_24H", "WITHIN_72H", "LATER",
        "UNKNOWN")}
    upcoming = []
    waiting = overdue = 0.0
    waiting_unknown = 0
    for e in open_:
        cap = e.get("open_cost_basis_usd") or 0.0
        ev = e.get("event_start_at")
        if ev is None:
            waiting_unknown += 1
        elif ev <= now:
            waiting += cap
        rel = (ev + lag) if (ev is not None and lag is not None) else None
        if rel is None:
            k = "UNKNOWN"
        else:
            dt = rel - now
            k = ("OVERDUE" if dt < 0 else "WITHIN_6H" if dt <= 6 * HOUR
                 else "WITHIN_24H" if dt <= 24 * HOUR
                 else "WITHIN_72H" if dt <= 72 * HOUR else "LATER")
            if dt < 0:
                overdue += cap
            upcoming.append({"position_key": e["position_key"],
                             "capital_usd": C.rnd(cap),
                             "expected_release_at": rel})
        buckets[k]["positions"] += 1
        buckets[k]["capital_usd"] += cap
    for v in buckets.values():
        v["capital_usd"] = C.rnd(v["capital_usd"])
    upcoming.sort(key=lambda r: r["expected_release_at"])
    out["release_schedule"] = buckets
    out["next_releases"] = upcoming[:20]
    out["release_basis"] = (
        None if lag is None else
        "us_premap.game_start (else the thesis event start) + median "
        "settlement lag over %d recorded settlements (%.0fs)" % (lag_n, lag))
    if lag is None:
        out["unmeasured"]["release_basis"] = R_NO_LAG.replace(
            "_BEFORE_ENTRY", "")
    out.put("capital_waiting_for_settlement_usd", C.rnd(waiting))
    out["capital_waiting_basis"] = (
        "open cost basis of positions whose event has started and that are "
        "not settled (in-play and concluded-unsettled cannot be told apart "
        "in these records); %d open positions have no event start and are "
        "not counted" % waiting_unknown)
    out.put("capital_overdue_settlement_usd",
            C.rnd(overdue) if lag is not None else None,
            "NO_MEASURED_SETTLEMENT_LAG")
    # ── profit per capital-hour ───────────────────────────────────────
    win_closed = [e for e in closed if (e.get("released_at") or 0) >= lo]
    rp = [(e["net_profit_usd"], e["capital_hours"]) for e in win_closed
          if e.get("net_profit_usd") is not None
          and (e.get("capital_hours") or 0) > EPS]
    out["window_days"] = window_days
    out["window_start"] = lo
    out["realized_sample"] = len(rp)
    out.put("realized_net_profit_usd",
            C.rnd(sum(a for a, _ in rp)) if rp else None,
            "NO_POSITION_CLOSED_IN_WINDOW")
    out.put("realized_capital_hours",
            C.rnd(sum(b for _, b in rp), 6) if rp else None,
            "NO_POSITION_CLOSED_IN_WINDOW")
    out.put("REALIZED_PROFIT_PER_CAPITAL_HOUR",
            C.rnd(sum(a for a, _ in rp) / sum(b for _, b in rp), 9)
            if rp else None, "NO_POSITION_CLOSED_IN_WINDOW")
    ep = [(e["expected_net_profit_usd"], e["expected_capital_hours"])
          for e in opened if (e.get("first_fill_at") or 0) >= lo
          and e.get("expected_net_profit_usd") is not None
          and (e.get("expected_capital_hours") or 0) > EPS]
    out["expected_sample"] = len(ep)
    out.put("EXPECTED_PROFIT_PER_CAPITAL_HOUR",
            C.rnd(sum(a for a, _ in ep) / sum(b for _, b in ep), 9)
            if ep else None,
            "NO_POSITION_OPENED_IN_WINDOW_WITH_EXPECTED_PROFIT_AND_RELEASE")
    # ── utilization ───────────────────────────────────────────────────
    ch_win = 0.0
    for e in opened:
        segs = list(e.get("segments") or [])
        if e["state"] == "OPEN" and (e.get("open_cost_basis_usd") or 0) > 0 \
                and e.get("last_event_at") is not None:
            segs.append((e["last_event_at"], now, e["open_cost_basis_usd"]))
        ch_win += _clip(segs, lo, now)
    win_h = (now - lo) / HOUR
    out.put("capital_hours_in_window", C.rnd(ch_win, 6))
    out.put("average_locked_capital_usd", C.rnd(ch_win / win_h))
    cap = C.num(account_capital)
    out["account_capital_basis"] = account_capital_basis
    out.put("account_capital_usd", C.rnd(cap), "NO_ACCOUNT_CAPITAL_RECORD")
    if cap is None or cap <= 0:
        why = "NO_ACCOUNT_CAPITAL_RECORD" if cap is None else (
            "ACCOUNT_CAPITAL_NOT_POSITIVE")
        out.put("capital_utilization", None, why)
        out.put("idle_capital_usd", None, why)
    else:
        out.put("capital_utilization", C.rnd(ch_win / (cap * win_h), 9))
        out.put("idle_capital_usd", C.rnd(cap - out["capital_locked_usd"]))
    out["utilization_basis"] = (
        "capital-hours inside the window (open positions accrued to now) / "
        "(current account capital x window hours)")
    return out


#: the profit lines of a portfolio that a SLEEVE may report on its own (the
#: capital lines -- account capital, utilization, idle capital -- belong to
#: the one shared cash ledger and are never attributed to a sleeve)
SLEEVE_PROFIT_KEYS = ("positions", "open_positions", "closed_positions",
                      "capital_locked_positions_usd", "window_days",
                      "window_start", "realized_sample",
                      "realized_net_profit_usd", "realized_capital_hours",
                      "REALIZED_PROFIT_PER_CAPITAL_HOUR", "expected_sample",
                      "EXPECTED_PROFIT_PER_CAPITAL_HOUR")


def sleeve_profit(econs: list, *, book: str, sleeve: str, now: float,
                  lag_samples=(), window_days=WINDOW_DAYS) -> dict:
    """ONE sleeve's profit lines inside a book's CAPITAL picture (rows of
    other sleeves are never included; UNCLASSIFIED is never INVESTMENT)."""
    rows = [e for e in econs if e.get("book") == book
            and C.sleeve_of(e) == sleeve]
    rep = portfolio(rows, book=book, now=now, lag_samples=lag_samples,
                    window_days=window_days)
    out = {k: rep.get(k) for k in SLEEVE_PROFIT_KEYS}
    out["unmeasured"] = {k: v for k, v in (rep.get("unmeasured") or {}).items()
                         if k in SLEEVE_PROFIT_KEYS}
    out.update(C.scope_fields(rows, book=book, sleeve=sleeve))
    return out


def counterfactual_summary(econs: list) -> dict:
    """COUNTERFACTUAL book totals, reported on their own."""
    rows = [e for e in econs if e.get("book") == "COUNTERFACTUAL"]
    closed = [e for e in rows if e.get("net_profit_usd") is not None]
    out = C.Out(book="COUNTERFACTUAL", kind="HOLD_TO_SETTLEMENT",
                positions=len(rows), closed=len(closed),
                summed_with_other_book=False)
    out.put("net_profit_usd", C.rnd(sum(e["net_profit_usd"] for e in closed))
            if closed else None, "NO_COUNTERFACTUAL_POSITION")
    ch = sum(e.get("capital_hours") or 0.0 for e in closed)
    out.put("profit_per_capital_hour",
            C.rnd(out["net_profit_usd"] / ch, 9)
            if closed and ch > EPS else None, "NO_COUNTERFACTUAL_CAPITAL_HOURS")
    return out
