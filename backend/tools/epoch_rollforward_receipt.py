#!/usr/bin/env python3
"""THE $500,000 MANAGEMENT EPOCH: AN EXACT DECIMAL ROLL-FORWARD RECEIPT.

    OPENING_CAPITAL + NET_CASH_FLOWS + REALIZED_PNL + UNREALIZED_PNL
        - ALL_IN_FEES = ENDING_CAPITAL

READ-ONLY, STDLIB ONLY. It reads the export of research/
rc6_identity_epoch_rollforward.sql -- a research-sql run log, a saved psql
output, or the JSON file a test writes -- and never connects to anything. It
imports three pure, stdlib-only modules of this repository
(bettor_paper_epoch, bettor_paper_ledger, bettor_book_snapshot), so the
figures come from the SAME epoch rules the API serves, not a copy of them.

WHAT IT PROVES, EACH FROM THE EXPORTED ROWS:
  1 THE APPEND-ONLY LEDGER REPLAYS: every paper_ledger row in sequence order,
    cash_after = previous cash_after + cash_delta and reserved likewise, the
    first row the one INITIAL_FUNDING of exactly $500,000, sequences strictly
    increasing, no funding after the epoch (no deposit or withdrawal kind
    exists, migration 171) -- so NET_CASH_FLOWS is 0 by the ledger, not by
    assumption.
  2 EVERY COST RECONCILES: each FILL / SALE ledger row's cash is exactly the
    fill's -(gross + fee) / (gross - fee); every fill has exactly one ledger
    row; each SETTLEMENT row's cash is its first settlement version's payout
    and each CORRECTION the difference to the version before it.
  3 THE ROLL-FORWARD BALANCES EXACTLY: bettor_paper_epoch.management_book on
    these rows (its `rollforward`), every leg a Decimal at the ledger's 6
    places, ENDING_CAPITAL taken from the ledger cash independently of the
    legs, the bridge to ledger equity itemised (the pre-management history
    and every held-outside position a line).
  4 EVERY OPEN MARK IS VERIFIED: each open position's mark is the exit price
    of an error-free book at most the ledger's 300 s old at the export
    instant; otherwise the receipt says which and is not EXACT.
  5 THE EXPORT IS WHOLE: the manifest's row counts match what was read, so a
    truncated log is refused, never read as a smaller ledger.

    python3 -I backend/tools/epoch_rollforward_receipt.py <export> [--out F]

Exit 0 only when 1, 2, 3, 4 and 5 hold (verdict EXACT, or EXACT with the
held-outside positions itemised); 1 otherwise; 2 when the export is
unreadable. A passing receipt grants no authority of any kind.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from decimal import Decimal, InvalidOperation

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sportsassets import bettor_book_snapshot as BS  # noqa: E402
from sportsassets import bettor_paper_epoch as EP  # noqa: E402
from sportsassets import bettor_paper_ledger as L  # noqa: E402

VERSION = "EPOCH_ROLLFORWARD_RECEIPT_V1"
MARK = "EPOCHRF|"
SECTIONS = ("manifest", "ledger", "fills", "settlements", "epochbooks",
            "nowbooks")
ZERO = Decimal(0)
EPS = Decimal("0.000001")
FUNDING = Decimal("500000")

R_INCOMPLETE = "THE_EXPORT_IS_INCOMPLETE"
R_UNREADABLE = "THE_EXPORT_IS_UNREADABLE"


class ExportError(ValueError):
    pass


def dec(v) -> Decimal:
    """An exact Decimal from export text. A float never gets here: the
    export carries amounts as text and json is parsed with Decimal floats."""
    if isinstance(v, bool) or v is None:
        raise ExportError("%s: not an amount" % (v,))
    if isinstance(v, float):
        raise ExportError("a float reached the receipt: %r" % (v,))
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError) as exc:
        raise ExportError("%r: %s" % (v, exc)) from exc
    if not d.is_finite():
        raise ExportError("%r: not finite" % (v,))
    return d


# ── 5 THE EXPORT ──────────────────────────────────────────────────────

def parse(text: str) -> dict:
    """{section: [rows...], 'manifest': {...}} from any text holding the
    export's marker lines (a research-sql log, psql output)."""
    chunks: dict = {s: {} for s in SECTIONS}
    for line in text.splitlines():
        i = line.find(MARK)
        if i < 0:
            continue
        rest = line[i + len(MARK):].rstrip().rstrip("+").rstrip()
        try:
            sect, chunk, payload = rest.split("|", 2)
            body = json.loads(payload, parse_float=Decimal)
            n = int(chunk)
        except ValueError as exc:
            raise ExportError("%s: %s" % (R_UNREADABLE, exc)) from exc
        if sect not in chunks:
            raise ExportError("%s: unknown section %r" % (R_UNREADABLE, sect))
        if n in chunks[sect]:
            raise ExportError("%s: section %s chunk %d twice"
                              % (R_UNREADABLE, sect, n))
        chunks[sect][n] = body
    if not chunks["manifest"]:
        raise ExportError("%s: no manifest line" % R_INCOMPLETE)
    out = {"manifest": chunks["manifest"][0]}
    for s in SECTIONS[1:]:
        got = chunks[s]
        if sorted(got) != list(range(len(got))):
            raise ExportError("%s: section %s chunks %s"
                              % (R_INCOMPLETE, s, sorted(got)))
        out[s] = [r for n in sorted(got) for r in got[n]]
    m = out["manifest"]
    for s, key in (("ledger", "ledger_rows"), ("fills", "fill_rows"),
                   ("settlements", "settlement_rows"),
                   ("epochbooks", "epoch_book_slugs"),
                   ("nowbooks", "now_book_slugs")):
        if int(m.get(key, -1)) != len(out[s]):
            raise ExportError("%s: %s has %d row(s), the manifest says %s"
                              % (R_INCOMPLETE, s, len(out[s]), m.get(key)))
    return out


def digest(exp: dict) -> str:
    return hashlib.sha256(json.dumps(
        exp, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


# ── 1 THE LEDGER REPLAY ───────────────────────────────────────────────

def replay(ledger: list, *, epoch_at: Decimal) -> dict:
    """The append-only chain, row by row. Pure."""
    problems = []
    cash = res = ZERO
    prev_seq = None
    kinds: dict = {}
    funding_rows = post_funding = 0
    for i, r in enumerate(ledger):
        seq, kind = int(r[0]), r[1]
        cd, rd, ca, ra, at = (dec(r[2]), dec(r[3]), dec(r[4]), dec(r[5]),
                              dec(r[6]))
        kinds[kind] = kinds.get(kind, 0) + 1
        if prev_seq is not None and seq <= prev_seq:
            problems.append({"seq": seq, "why": "SEQUENCE_NOT_INCREASING"})
        prev_seq = seq
        if i == 0 and (kind != "INITIAL_FUNDING" or cd != FUNDING):
            problems.append({"seq": seq,
                             "why": "FIRST_ROW_IS_NOT_THE_500000_FUNDING"})
        if kind == "INITIAL_FUNDING":
            funding_rows += 1
            if at >= epoch_at and i != 0:
                post_funding += 1
        cash += cd
        res += rd
        if cash != ca:
            problems.append({"seq": seq, "why": "CASH_AFTER_DOES_NOT_CHAIN",
                             "expected": str(cash), "recorded": str(ca)})
            cash = ca
        if res != ra:
            problems.append({"seq": seq,
                             "why": "RESERVED_AFTER_DOES_NOT_CHAIN",
                             "expected": str(res), "recorded": str(ra)})
            res = ra
    if funding_rows != 1:
        problems.append({"why": "INITIAL_FUNDING_ROWS_%d" % funding_rows})
    return {"rows": len(ledger), "kinds": kinds,
            "cash_now": str(cash), "reserved_now": str(res),
            "funding_rows": funding_rows,
            "funding_after_the_epoch": post_funding,
            "problems": problems[:50], "problem_count": len(problems),
            "ok": not problems}


# ── 2 COSTS ───────────────────────────────────────────────────────────

def costs(ledger: list, fills: list, settlements: list) -> dict:
    """Every fill's ledger cash, every settlement's and correction's. Pure."""
    by_fill: dict = {}
    for r in ledger:
        if r[1] in ("FILL", "SALE"):
            by_fill.setdefault(r[7], []).append(r)
    problems = []
    fees = ZERO
    for f in fills:
        fid, direction = f[0], f[4]
        gross, fee = dec(f[6]), dec(f[7])
        fees += fee
        rows = by_fill.get(fid, [])
        if len(rows) != 1:
            problems.append({"fill_id": fid,
                             "why": "LEDGER_ROWS_FOR_THE_FILL_%d" % len(rows)})
            continue
        want = -(gross + fee) if direction == "BUY" else gross - fee
        if dec(rows[0][2]) != want:
            problems.append({"fill_id": fid, "why": "FILL_CASH_MISMATCH",
                             "ledger": rows[0][2], "fill": str(want)})
    fill_ids = {f[0] for f in fills}
    for fid in by_fill:
        if fid not in fill_ids:
            problems.append({"fill_id": fid, "why": "LEDGER_FILL_WITHOUT_FILL"})
    vers: dict = {}
    for s in settlements:
        vers.setdefault(s[0], []).append(s)
    for v in vers.values():
        v.sort(key=lambda s: int(s[2]))
    settle_cash = {}
    for r in ledger:
        if r[1] == "SETTLEMENT":
            settle_cash.setdefault(r[8], []).append(dec(r[2]))
    corr = {}
    for r in ledger:
        if r[1] == "CORRECTION" and r[8]:
            corr.setdefault(r[8], []).append(dec(r[2]))
    for pk, v in vers.items():
        first = dec(v[0][6])
        got = settle_cash.get(pk, [])
        if sum(got, ZERO) != first:
            problems.append({"position_key": pk,
                             "why": "SETTLEMENT_CASH_IS_NOT_THE_FIRST_PAYOUT",
                             "ledger": [str(x) for x in got],
                             "payout": str(first)})
        diffs = [dec(v[i][6]) - dec(v[i - 1][6]) for i in range(1, len(v))]
        if sorted(diffs) != sorted(corr.get(pk, [])):
            problems.append({"position_key": pk,
                             "why": "CORRECTIONS_ARE_NOT_THE_VERSION_DIFFS",
                             "ledger": [str(x) for x in corr.get(pk, [])],
                             "diffs": [str(x) for x in diffs]})
    return {"fills": len(fills), "fees_all_time": str(fees),
            "settled_positions": len(vers),
            "problems": problems[:50], "problem_count": len(problems),
            "ok": not problems}


# ── 3 + 4 THE BOOK ────────────────────────────────────────────────────

def _book_dict(b: dict | None) -> dict | None:
    if not b:
        return None
    out = dict(b)
    out["observed_at"] = float(dec(b["observed_at"]))
    return out


def exit_mark(book: dict | None, side: str, *, now: Decimal,
              max_age: Decimal) -> dict:
    """The ledger's mark (bettor_paper_ledger.latest_marks): the exit price
    of the latest error-free book, STALE past the bound."""
    if not book:
        return {"price": None, "why": "NO_OBSERVED_BOOK_FOR_THIS_MARKET"}
    md = {"bids": L._j(book.get("bids")) or [],
          "offers": L._j(book.get("offers")) or []}
    ex = BS.exit_ladder(md, held_intent=("ORDER_INTENT_BUY_LONG"
                                         if side == "LONG"
                                         else "ORDER_INTENT_BUY_SHORT"))
    at = dec(book["observed_at"])
    age = now - at
    if not ex.get("ok"):
        return {"price": None, "observed_at": float(at),
                "source": "paper_book_observations:%s" % book.get("obs_id"),
                "why": ex.get("refusal") or "NO_EXIT_SIDE"}
    return {"price": float(dec(str(ex["best_exit_price"]))),
            "observed_at": float(at), "age_s": float(age),
            "stale": age > max_age,
            "source": "paper_book_observations:%s" % book.get("obs_id")}


def receipt(exp: dict) -> dict:
    m = exp["manifest"]
    acct = m["account_id"]
    E = dec(m["epoch_at"])
    now = dec(m["db_now"])
    max_age = dec(m["mark_max_age_s"])
    rep = replay(exp["ledger"], epoch_at=E)
    cst = costs(exp["ledger"], exp["fills"], exp["settlements"])

    def pk_of(group, slug, side):
        return L.position_key(account_id=acct, group_id=group, slug=slug,
                              holding_side=side)

    fills = [{"position_key": pk_of(f[1], f[2], f[3]), "direction": f[4],
              "qty": dec(f[5]), "gross_usd": dec(f[6]), "fee_usd": dec(f[7]),
              "at": float(dec(f[8])), "seq": f[9]} for f in exp["fills"]]
    sets = [{"position_key": r[8], "kind": r[1], "cash_usd": dec(r[2]),
             "at": float(dec(r[6])), "seq": int(r[0])}
            for r in exp["ledger"]
            if r[1] in ("SETTLEMENT", "CORRECTION") and r[8]]
    latest: dict = {}
    for s in exp["settlements"]:
        if s[0] not in latest or int(s[2]) > int(latest[s[0]][2]):
            latest[s[0]] = s
    sq = {pk: dec(s[3]) for pk, s in latest.items()}
    cash_e = res_e = cash_now = res_now = fund = ZERO
    for r in exp["ledger"]:
        cd, rd, at = dec(r[2]), dec(r[3]), dec(r[6])
        cash_now += cd
        res_now += rd
        if at < E or r[1] == "INITIAL_FUNDING":
            cash_e += cd
        if at < E:
            res_e += rd
        if r[1] == "INITIAL_FUNDING":
            fund += cd
    meta = {}
    for f in exp["fills"]:
        meta.setdefault(pk_of(f[1], f[2], f[3]), {
            "us_market_slug": f[2], "holding_side": f[3], "strategy": None})
        if f[10] is not None:
            cur = meta[pk_of(f[1], f[2], f[3])]["strategy"]
            meta[pk_of(f[1], f[2], f[3])]["strategy"] = max(
                [x for x in (cur, f[10]) if x is not None])
    # positions open at the epoch, as bettor_paper_epoch.read derives them
    held: dict = {}
    for x in fills:
        if Decimal(str(x["at"])) < E:
            held[x["position_key"]] = held.get(x["position_key"], ZERO) + (
                x["qty"] if x["direction"] == "BUY" else -x["qty"])
    for x in sets:
        if x["kind"] == "SETTLEMENT" and Decimal(str(x["at"])) < E:
            held[x["position_key"]] = held.get(x["position_key"], ZERO) - \
                sq.get(x["position_key"], ZERO)
    ebooks = {b["slug"]: b for b in exp["epochbooks"]}
    sets_by_pk: dict = {}
    for s in exp["settlements"]:
        sets_by_pk.setdefault(s[0], []).append({
            "settlement_id": s[1], "version": int(s[2]), "outcome": s[4],
            "payout_per_contract": dec(s[5]),
            "settled_at": float(dec(s[7])), "evidence": s[8]})
    epoch_marks = {}
    for pk, q in held.items():
        if q <= EPS or pk not in meta:
            continue
        slug, side = meta[pk]["us_market_slug"], meta[pk]["holding_side"]
        b = ebooks.get(slug) or {}
        epoch_marks[pk] = EP.classify_epoch_mark(
            epoch_at=float(E), holding_side=side,
            window_book=_book_dict(b.get("window")),
            last_book=_book_dict(b.get("last")),
            window_reads=b.get("reads"),
            first_book_after=_book_dict(b.get("after")),
            settlements=sets_by_pk.get(pk))
    nbooks = {b["slug"]: b.get("latest") for b in exp["nowbooks"]}
    now_marks = {pk: exit_mark(nbooks.get(v["us_market_slug"]),
                               v["holding_side"], now=now, max_age=max_age)
                 for pk, v in meta.items()}
    book = EP.management_book(
        epoch_at=float(E), opening_equity=EP.OPENING_EQUITY_USD, fills=fills,
        settlement_entries=sets, settled_qty=sq, epoch_marks=epoch_marks,
        now_marks=now_marks,
        ledger_epoch={"cash_usd": cash_e, "reserved_usd": res_e,
                      "funding_usd": fund},
        ledger_now={"cash_usd": cash_now, "reserved_usd": res_now},
        meta=meta, hwm_points=[], itemise=True)
    rc = book["reconciliation"]
    app_ok = (book["identity"]["holds"] and book["opening"]["identity_holds"]
              and book["ledger_reconciliation"]["reconciles"]
              and rc["exact"] and rc["fully_attributed"])
    rf = book["rollforward"]
    flows_ok = rep["funding_after_the_epoch"] == 0
    verified = (rep["ok"] and cst["ok"] and flows_ok and rf["balanced"]
                and rf["open_position_marks_verified"] and app_ok)
    contract = {
        "opening_capital": rf["cents"]["opening_capital"],
        "net_cash_flows": rf["cents"]["net_cash_flows"],
        "realized_pnl": rf["cents"]["realized_pnl"],
        "unrealized_pnl": rf["cents"]["unrealized_pnl"],
        "all_in_fees": rf["cents"]["all_in_fees"],
        "ending_capital": rf["cents"]["ending_capital"],
        "cents_rounding_residual": rf["cents"]["rounding_residual"],
        "reconciled_to_append_only_ledger": rep["ok"] and rf["balanced"],
        "open_position_marks_verified": rf["open_position_marks_verified"],
        "all_costs_reconciled": cst["ok"]}
    return {
        "version": VERSION, "account_id": acct,
        "epoch_id": EP.EPOCH_ID, "epoch_start": EP.EPOCH_START_LOCAL,
        "epoch_at": str(E), "export_at": str(now),
        "export_sha256": digest(exp),
        "verdict": (rf["verdict"] if verified else
                    "NOT_VERIFIED:" + ",".join(k for k, ok in (
                        ("LEDGER_REPLAY", rep["ok"]), ("COSTS", cst["ok"]),
                        ("NET_CASH_FLOWS", flows_ok),
                        ("BALANCE", rf["balanced"]),
                        ("OPEN_MARKS", rf["open_position_marks_verified"]),
                        ("MANAGEMENT_RECONCILIATION", app_ok)) if not ok)),
        "verified": verified,
        "contract": contract,
        "rollforward": {k: rf[k] for k in (
            "rule", "precision", "opening_capital", "net_cash_flows",
            "realized_pnl", "unrealized_pnl", "all_in_fees",
            "ending_capital", "gap", "balanced", "bridge_to_ledger",
            "open_positions", "open_position_marks_verified", "open_marks",
            "held_outside_positions", "held_outside_open", "verdict")},
        "ledger_replay": rep, "costs": cst,
        "management_book": {
            "status": "OK" if app_ok else "DOES_NOT_RECONCILE",
            "equity_usd": book["equity_usd"],
            "realized_pnl_usd": book["realized_pnl_usd"],
            "unrealized_pnl_usd": book["unrealized_pnl_usd"],
            "fees_after_epoch_usd": book["fees_after_epoch_usd"],
            "carried_positions": book["carried_positions"],
            "carried_unverified": book["carried_unverified"],
            "opened_after_epoch": book["opened_after_epoch"],
            "historical_positions": book["historical_positions"],
            "reconciliation_exact": rc["exact"],
            "fully_attributed": rc["fully_attributed"],
            "pre_management_result_usd": rc["pre_management_result_usd"],
            "unverified_positions": [
                {k: u.get(k) for k in ("position_key", "why", "market",
                                       "closed", "whole_ledger_result_usd")}
                for u in book["unverified_positions"]]},
        "positions": rf["positions"],
        "authority": "NONE: a receipt grants no trading or capital authority"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("export", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args(argv)
    try:
        exp = parse(a.export.read_text(encoding="utf-8"))
        out = receipt(exp)
    except (OSError, ExportError) as exc:
        out = {"version": VERSION, "verdict": "UNREADABLE",
               "verified": False, "why": str(exc)}
        code = 2
    else:
        code = 0 if out["verified"] else 1
    text = json.dumps(out, indent=2, sort_keys=True, default=str) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
