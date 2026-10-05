"""THE DATA-QUALITY SENTINEL (RESEARCH). Pure; no I/O.

Predicates over the recorded rows the profitability numbers rest on. Each
check is PASS / WARN / FAIL with its numerator, denominator and threshold,
or UNAVAILABLE when its input was not read (never a pass by default). The
sentinel OBSERVES: it blocks, gates and repairs nothing; a FAIL is a reason
to distrust the numbers built on those rows, named on the page.
"""
from __future__ import annotations

from . import common as C

VERSION = "POS_OS_SENTINEL_V1"
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
ORDER = {PASS: 0, WARN: 1, FAIL: 2, C.UNAVAILABLE: 3}
OVERDUE_GRACE_S = 86400.0


def _rate_check(name, num, den, *, warn, fail, basis):
    if den is None or num is None:
        return {"check": name, "status": C.UNAVAILABLE,
                "why": C.R_INPUT_NOT_READ, "basis": basis}
    r = (num / den) if den else 0.0
    st = FAIL if (fail is not None and r > fail) else (
        WARN if (warn is not None and r > warn) else PASS)
    return {"check": name, "status": st, "numerator": int(num),
            "denominator": int(den), "rate": C.rnd(r),
            "warn_above": warn, "fail_above": fail, "basis": basis}


def conflicting_settlements(rows):
    superseded = {r.get("supersedes") for r in rows if r.get("supersedes")}
    live: dict = {}
    for r in rows:
        if r.get("settlement_id") in superseded:
            continue
        live.setdefault(r.get("position_key"), set()).add(
            (r.get("outcome"), C.num(r.get("payout_per_contract"))))
    keys = [k for k, v in live.items() if len(v) > 1]
    return len(keys), len(live), keys[:10]


def crossed_books(rows):
    from ..intel import common as IC
    crossed = errors = 0
    for b in rows:
        if b.get("error"):
            errors += 1
            continue
        try:
            v = IC.book_view(C.jload(b.get("bids")), C.jload(b.get("offers")))
        except Exception:                                       # noqa: BLE001
            errors += 1
            continue
        if v["best_bid"] is not None and v["best_offer"] is not None \
                and v["best_bid"] >= v["best_offer"]:
            crossed += 1
    return crossed, errors, len(rows)


def checks(inputs, *, now):
    ig = inputs.get("integrity")
    g = (lambda k: None if ig is None else C.num(ig.get(k)))
    out = [
        _rate_check("fills_without_recorded_book", g("fills_without_book"),
                    g("fills"), warn=0.05, fail=0.25,
                    basis="paper_fills.book_obs_id IS NULL"),
        _rate_check("fills_price_or_qty_out_of_range",
                    g("fills_out_of_range"), g("fills"), warn=None, fail=0.0,
                    basis="price <= 0 or >= 1, or qty <= 0"),
        _rate_check("fills_fee_missing_or_negative", g("fills_bad_fee"),
                    g("fills"), warn=0.0, fail=0.05,
                    basis="fee_usd IS NULL or < 0"),
        _rate_check("fills_book_evidence_older_than_60s",
                    g("fills_book_older_than_60s"), g("fills"), warn=0.05,
                    fail=0.25, basis="filled_at - book_observed_at > 60 s"),
        _rate_check("enter_decisions_without_probability",
                    g("enters_without_probability"), g("decisions"),
                    warn=None, fail=0.0,
                    basis="verdict ENTER with no p_blended / p_pinnacle / "
                          "p_internal"),
        _rate_check("decisions_without_strategy",
                    g("decisions_without_strategy"), g("decisions"),
                    warn=0.0, fail=0.05, basis="strategy IS NULL"),
        _rate_check("orders_past_expiry_not_terminal",
                    g("orders_past_expiry_not_terminal"), g("orders"),
                    warn=0.0, fail=0.05,
                    basis="terminal_at IS NULL and expires_at < now - 1 h"),
        _rate_check("orders_overfilled", g("orders_overfilled"), g("orders"),
                    warn=None, fail=0.0, basis="filled_qty > qty"),
    ]
    st = inputs.get("settlements")
    if st is None:
        out.append(_rate_check("settlements_conflicting", None, None,
                               warn=None, fail=0.0, basis=""))
    else:
        n, d, keys = conflicting_settlements(st)
        c = _rate_check("settlements_conflicting", n, d, warn=None, fail=0.0,
                        basis="a position key with more than one live "
                              "(outcome, payout) -- none superseding")
        c["examples"] = keys
        out.append(c)
    bk = inputs.get("books")
    if bk is None:
        out.append(_rate_check("books_crossed", None, None, warn=0.01,
                               fail=0.10, basis=""))
        out.append(_rate_check("books_unreadable_or_error", None, None,
                               warn=0.10, fail=0.50, basis=""))
    else:
        cr, er, n = crossed_books(bk)
        out.append(_rate_check("books_crossed", cr, n, warn=0.01, fail=0.10,
                               basis="best bid >= best offer in a recorded "
                                     "book (sample of the account's "
                                     "markets)"))
        out.append(_rate_check("books_unreadable_or_error", er, n, warn=0.10,
                               fail=0.50, basis="recorded error or "
                                                "unparsable levels"))
    pos = inputs.get("positions")
    if pos is None:
        out.append(_rate_check("positions_without_strategy", None, None,
                               warn=0.0, fail=0.05, basis=""))
        out.append(_rate_check("open_positions_overdue_24h", None, None,
                               warn=0.0, fail=0.25, basis=""))
    else:
        pp = [p for p in pos if p.get("book") == "PAPER"]
        out.append(_rate_check(
            "positions_without_strategy",
            sum(1 for p in pp if not p.get("strategy")), len(pp), warn=0.0,
            fail=0.05, basis="pos economics row with no strategy"))
        op = [p for p in pp if p.get("state") == "OPEN"]
        out.append(_rate_check(
            "open_positions_overdue_24h",
            sum(1 for p in op if C.num(p.get("expected_release_at"))
                is not None and p["expected_release_at"]
                < now - OVERDUE_GRACE_S), len(op), warn=0.0, fail=0.25,
            basis="open more than 24 h past its expected release "
                  "(settlement backlog)"))
    return out


def build(inputs, *, now):
    cs = checks(inputs, now=now)
    avail = [c for c in cs if c["status"] != C.UNAVAILABLE]
    if not avail:
        return C.unread(["integrity", "settlements", "books", "positions"],
                        inputs, audit=C.BUILT)
    worst = max((c["status"] for c in avail), key=lambda s: ORDER[s])
    judged = any(c.get("denominator") for c in avail)
    return C.section(C.OK if judged else C.EMPTY, None if judged else
                     "%s: nothing recorded to check" % C.R_NO_ROWS,
                     audit=C.BUILT, data={
        "version": VERSION, "overall": worst, "checks": cs,
        "unavailable_checks": [c["check"] for c in cs
                               if c["status"] == C.UNAVAILABLE],
        "blocks_nothing": True},
        sources=["paper_fills", "paper_decisions", "paper_orders",
                 "paper_settlements", "paper_book_observations",
                 "pos_economics_latest"])
