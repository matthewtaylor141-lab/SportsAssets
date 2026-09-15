#!/usr/bin/env python3
"""Phase X driver: X1 -> X2 -> X3 -> X4 -> FAST DECISION GATE.

Runs the whole chain the moment egress exists, with no further engineering
cycle. Until then it exits on GATE E and says which host it could not
reach -- it never routes around the policy and never fabricates a leg.

  python3 research/run85_phasex_run.py --series KXNFLGAME KXMLBGAME ...

PMUS side comes from the sealed PX1-PMUS-1 records (already built); the
Kalshi side is fetched read-only. Nothing here can place an order: the
client it uses has no write path (see run85_phasex_kalshi.ReadOnlyGuard).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


K = _load("px_kalshi", "run85_phasex_kalshi.py")
E = _load("px_econ", "run85_phasex_economics.py")
# The PMUS discovery + record builders already proven in Track B-L and
# Phase X1. Reused rather than rewritten: a second discovery path would be
# a second thing to be wrong about the venue's pagination.
BL = _load("px_bl", "run85_trackbl_block.py")
PX1 = _load("px1", "run85_phasex1_pmus_equivalence.py")

PMUS_RECORDS = HERE / "evidence" / "phasex1" / "PMUS_EQUIVALENCE_RECORDS.json"
OUTDIR = HERE / "evidence" / "phasex"
SIZES = (100, 500, 1000, 5000, 10000)

GATE_A = "A -- EXECUTABLE EDGE OBSERVED"
GATE_B = "B -- SMALL / CONDITIONAL EDGE"
GATE_C = "C -- NO EXECUTABLE EDGE OBSERVED"
GATE_D = "D -- EQUIVALENCE BOTTLENECK"
GATE_E = "E -- DATA / ACCESS BOTTLENECK"


def httpx_transport():
    """The real transport, built only when asked. Import is deferred so
    this module loads (and its tests run) with no network library."""
    import httpx

    client = httpx.Client(timeout=20.0, follow_redirects=False)

    def transport(method, url, params, timeout):
        return client.request(method, url, params=params, timeout=timeout)

    return transport


# --------------------------------------------------------------- X1 ----
def pmus_universe(cli_pages: list, samples: list, res: dict) -> list[dict]:
    """The CURRENT PMUS sports universe, built with Track B-L's own
    discovery walk and Phase X1's own record builder.

    The 20 sealed PX1-PMUS-1 contracts stay as reference evidence; the
    opportunity scan must run against what is quotable now, or it measures
    a board that has closed.
    """
    import httpx

    budget = BL.Budget(BL.MAX_VENUE_REQUESTS)
    pacer = BL.B.AdaptivePacer(base=BL.SPACING_S)
    with httpx.Client(timeout=30.0) as http:
        events = BL.discover(http, pacer, budget, cli_pages, samples, res)
    out = []
    for ev in events:
        for m in ev.get("markets") or []:
            if m.get("status") != "MARKET_STATUS_OPEN":
                continue
            out.append(PX1.build_record(ev, m, []))
    return out


def pmus_side(rec: dict) -> dict:
    """PAYS_1_IF for the PMUS long side of a PX1-PMUS-1 record."""
    s1 = rec.get("SIDE_1_DEFINITION") or {}
    s2 = rec.get("SIDE_2_DEFINITION") or {}

    def label(side):
        for key in ("SIDE_TEAM_NAME", "SIDE_DESCRIPTION",
                    "SIDE_OUTCOME_LABEL"):
            node = side.get(key) or {}
            if node.get("value"):
                return str(node["value"])
        return None

    def bindings(side):
        """Every authoritative field that declares what the LONG side IS.
        Several are collected on purpose: when they disagree, the proof
        chain must see the contradiction rather than one elected winner."""
        out = []
        for key, src in (("SIDE_TEAM_NAME", "marketSides[].team.name"),
                         ("SIDE_DESCRIPTION", "marketSides[].description")):
            node = side.get(key) or {}
            if node.get("value"):
                out.append((src, str(node["value"])))
        return out

    prose = []
    txt = (rec.get("SOURCE_TEXTS") or {}).get("MARKET_DESCRIPTION")
    if txt:
        prose.append(("market.description", txt))
    dis = (rec.get("SOURCE_TEXTS") or {}).get("MARKET_RULES_DISCLAIMER")
    if dis:
        prose.append(("market.rulesDisclaimer", dis))
    mt = (rec.get("MARKET_TYPE") or {}).get("value") or {}
    return E.pays_1_if(
        venue="PMUS",
        contract_id=(rec.get("PMUS_MARKET_SLUG") or {}).get("value"),
        side_label=label(s1), sibling_label=label(s2), rules_prose=prose,
        subject=label(s1), line=mt.get("line"),
        period=mt.get("sportsMarketType"),
        settlement_source=(rec.get("LEAGUE_RESOLUTION_SOURCE") or {})
        .get("value"),
        event=((rec.get("EVENT") or {}).get("value") or {}).get("title"),
        side_binding_fields=bindings(s1))


def kalshi_side(krec: dict) -> dict:
    prose = []
    for key, name in (("RULES_PRIMARY", "market.rules_primary"),
                      ("RULES_SECONDARY", "market.rules_secondary")):
        node = krec.get(key) or {}
        if node.get("value"):
            prose.append((name, node["value"]))
    return E.pays_1_if(
        venue="KALSHI", contract_id=(krec.get("TICKER") or {}).get("value"),
        side_label=(krec.get("YES_SUB_TITLE") or {}).get("value"),
        sibling_label=(krec.get("NO_SUB_TITLE") or {}).get("value"),
        rules_prose=prose,
        subject=(krec.get("YES_SUB_TITLE") or {}).get("value"),
        line=(krec.get("STRIKE") or {}).get("value"),
        period=(krec.get("MARKET_TYPE") or {}).get("value"),
        settlement_source=(krec.get("SETTLEMENT_SOURCES") or {}).get("value"),
        event=((krec.get("EVENT_TITLE") or {}).get("value")
               or (krec.get("EVENT_TICKER") or {}).get("value")),
        side_binding_fields=[
            (src, (krec.get(k) or {}).get("value"))
            for k, src in (("YES_SUB_TITLE", "market.yes_sub_title"),)
            if (krec.get(k) or {}).get("value")])


def dimensions(prec: dict, krec: dict) -> dict:
    """The material dimensions, each read from BOTH venues' own fields.

    Whatever neither venue published stays (None, None), which the gate
    scores UNRESOLVED -- deliberately, because two silences are not
    agreement.
    """
    def pv(key, sub=None):
        n = prec.get(key) or {}
        v = n.get("value")
        return (v or {}).get(sub) if (sub and isinstance(v, dict)) else v

    def kv(key):
        return (krec.get(key) or {}).get("value")

    def quoted(rec, slot):
        n = rec.get(slot) or {}
        if n.get("status") != K.QUOTED:
            return None
        return " ".join(h["text"] for h in n["value"])

    return {
        "SPORT": (pv("SPORT"), None),
        "LEAGUE": (pv("LEAGUE"), kv("SERIES_TICKER")),
        "UNDERLYING_EVENT": (pv("EVENT", "title"), kv("EVENT_TITLE")),
        "PARTICIPANTS": (pv("PARTICIPANTS_FROM_SIDES"),
                         [x for x in (kv("YES_SUB_TITLE"),
                                      kv("NO_SUB_TITLE")) if x] or None),
        "PROPOSITION": (None, None),   # set by the payoff pair, not a field
        "LINE": (pv("MARKET_TYPE", "line"), kv("STRIKE")),
        "PERIOD": (pv("MARKET_TYPE", "sportsMarketType"), kv("MARKET_TYPE")),
        "START_TIME": (pv("GAME_START_TIME"), None),
        "CLOSE_TIME": (pv("END_TIME"), kv("CLOSE_TIME")),
        "SETTLEMENT_SOURCE": (pv("LEAGUE_RESOLUTION_SOURCE"),
                              kv("SETTLEMENT_SOURCES")),
        "SETTLEMENT_RULE": (quoted(prec, "OTHER_RESOLUTION_CONDITIONS"),
                            quoted(krec, "OTHER_RESOLUTION_CONDITIONS")),
        "OVERTIME": (quoted(prec, "OVERTIME_RULES"),
                     quoted(krec, "OVERTIME_RULES")),
        "EXTRA_TIME": (None, None),
        "POSTPONEMENT": (quoted(prec, "POSTPONEMENT_RULES"),
                         quoted(krec, "POSTPONEMENT_RULES")),
        "CANCELLATION": (quoted(prec, "CANCELLATION_RULES"),
                         quoted(krec, "CANCELLATION_RULES")),
        "DRAW_TIE": (quoted(prec, "DRAW_RULES"), quoted(krec, "DRAW_RULES")),
        "VOID": (quoted(prec, "VOID_RULES"), quoted(krec, "VOID_RULES")),
        "OTHER_MATERIAL_CONDITIONS": (None, None),
    }


# ------------------------------------------------------------- driver --
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="+", required=True)
    ap.add_argument("--out", default=str(OUTDIR))
    ap.add_argument("--pmus", choices=("live", "sealed"), default="live",
                    help="live: walk the current PMUS board (the scan); "
                         "sealed: the 20 PX1-PMUS-1 reference contracts")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def say(msg: str = "") -> None:
        lines.append(msg)
        print(msg)

    res: dict = {"PHASE": "X", "GATE": None}
    say("=" * 70)
    say("PHASE X -- CROSS-VENUE (PMUS <-> KALSHI) -- READ ONLY")
    say("=" * 70)

    if not PMUS_RECORDS.exists():
        say("PMUS side absent: %s" % PMUS_RECORDS)
        say("GATE = %s" % GATE_E)
        return 2
    sealed = json.loads(PMUS_RECORDS.read_text())
    if a.pmus == "live":
        try:
            pages, dres = [], {}
            precs = pmus_universe(pages, [], dres)
            say("PMUS_SPORTS_MARKETS = %d (live board)" % len(precs))
            # AN UNREACHABLE BOARD IS NOT AN EMPTY BOARD. B-L's discover()
            # treats a failed page as a terminal boundary, which is right
            # for a walk that ends and wrong for a walk that never began:
            # zero markets would otherwise read downstream as "nothing to
            # arbitrage" when it means "we could not look".
            first = pages[0] if pages else {}
            if not precs and first.get("http_status") != 200:
                raise RuntimeError(
                    "PMUS discovery returned no page: first page status=%s "
                    "error=%s" % (first.get("http_status"),
                                  first.get("error")))
        except Exception as exc:                           # noqa: BLE001
            say("PMUS board unreachable: %s: %s"
                % (type(exc).__name__, exc))
            say("a stale PMUS leg is not a contemporaneous observation")
            say("GATE = %s" % GATE_E)
            res.update({"GATE": GATE_E,
                        "BLOCKED_BY": ["PMUS_BOARD_UNREACHABLE"]})
            (out / "phasex_result.json").write_text(
                json.dumps(res, indent=1, default=str))
            (out / "phasex_report.txt").write_text("\n".join(lines) + "\n")
            return 2
    else:
        precs = sealed["RECORDS"]
        say("PMUS_SPORTS_MARKETS = %d (sealed %s, reference only)"
            % (sealed["RECORD_COUNT"], sealed["RECORD_VERSION"]))

    # ---- X1: Kalshi universe + full rules -----------------------------
    try:
        transport = httpx_transport()
    except Exception as exc:                               # noqa: BLE001
        say("no transport available: %s" % exc)
        say("GATE = %s" % GATE_E)
        return 2

    cli = K.KalshiResearchClient(transport)
    krecords: list[dict] = []
    failures: list[dict] = []
    for series in a.series:
        cursor = ""
        for _page in range(10):
            r = cli.events(series, cursor)
            if not r["ok"]:
                failures.append({"series": series, "outcome": r["outcome"],
                                 "attempts": len(r["attempts"])})
                break
            body = r["body"] or {}
            for ev in body.get("events") or []:
                for m in ev.get("markets") or []:
                    krecords.append(K.kalshi_contract_record(
                        m, ev, {"series": series,
                                "receipt_wall": r["local_receipt_wall"]}))
            cursor = body.get("cursor") or ""
            if not cursor:
                break

    say("KALSHI_SPORTS_MARKETS = %d" % len(krecords))
    if failures:
        say("KALSHI_READ_FAILURES = %s" % json.dumps(failures))
    if not krecords:
        # Every requested observation failed. That is an ACCESS fact, and
        # it must never be reported as "no opportunities were found".
        say("no Kalshi contract was retrieved; nothing was measured")
        say("GATE = %s" % GATE_E)
        res.update({"GATE": GATE_E, "KALSHI_SPORTS_MARKETS": 0,
                    "READ_FAILURES": failures})
        (out / "phasex_result.json").write_text(
            json.dumps(res, indent=1, default=str))
        (out / "phasex_report.txt").write_text("\n".join(lines) + "\n")
        return 2

    # ---- X1 continued: match, then gate BEFORE any price is looked at --
    pairs = []
    for prec in precs:
        pp = pmus_side(prec)
        for krec in krecords:
            kp = kalshi_side(krec)
            gate = E.equivalence(pp, kp, dimensions(prec, krec))
            if gate["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_NOT_IDENTIFIED \
                    and not gate["DIMENSION_RESULTS"].get("PARTICIPANTS") \
                    == E.MATCH:
                continue              # not even a candidate pairing
            pairs.append({"PMUS": prec, "KALSHI": krec, "GATE": gate})

    verified = [p for p in pairs
                if p["GATE"]["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_VERIFIED]
    rejected = [p for p in pairs
                if p["GATE"]["EQUIVALENCE_STATUS"] == E.EQUIVALENCE_REJECTED]
    unknown = [p for p in pairs if p not in verified and p not in rejected]
    say("POTENTIAL_MATCHES            = %d" % len(pairs))
    say("EQUIVALENCE_VERIFIED         = %d" % len(verified))
    say("EQUIVALENCE_REJECTED         = %d" % len(rejected))
    say("EQUIVALENCE_NOT_IDENTIFIED   = %d" % len(unknown))

    if not verified:
        say("no pair reached the equivalence gate; no economics were run")
        say("GATE = %s" % GATE_D)
        res.update({"GATE": GATE_D, "POTENTIAL_MATCHES": len(pairs),
                    "EQUIVALENCE_VERIFIED": 0,
                    "EQUIVALENCE_REJECTED": len(rejected),
                    "EQUIVALENCE_NOT_IDENTIFIED": len(unknown)})
        (out / "phasex_result.json").write_text(
            json.dumps(res, indent=1, default=str))
        (out / "phasex_report.txt").write_text("\n".join(lines) + "\n")
        return 1

    # ---- X2/X3/X4 ------------------------------------------------------
    say()
    say("--- X2: books, then X3 both directions, then X4 economics ---")
    say("PMUS books are NOT fetched here: this container has no PMUS "
        "capture in flight and a stale PMUS book beside a fresh Kalshi "
        "one is not a simultaneous observation. Supply a contemporaneous "
        "PMUS capture to complete X2.")
    rows = []
    for p in verified:
        tk = (p["KALSHI"]["TICKER"] or {}).get("value")
        r = cli.orderbook(tk)
        if not r["ok"]:
            failures.append({"ticker": tk, "outcome": r["outcome"]})
            continue
        bk = K.kalshi_book_record(tk, r["body"] or {},
                                  r["local_receipt_wall"],
                                  r["local_receipt_monotonic"])
        rows.append({"PAIR": p, "KALSHI_BOOK": bk})

    say("VERIFIED_PAIRS_WITH_FRESH_BOOKS = %d" % len(rows))
    say("POSITIVE_GROSS_PAIR_COUNT = NOT_MEASURED (PMUS leg absent)")
    say("POSITIVE_NET_PAIR_COUNT   = %s" % E.NOT_IDENTIFIED_FEES)
    say("GATE = %s" % GATE_E)
    res.update({"GATE": GATE_E,
                "POTENTIAL_MATCHES": len(pairs),
                "EQUIVALENCE_VERIFIED": len(verified),
                "EQUIVALENCE_REJECTED": len(rejected),
                "EQUIVALENCE_NOT_IDENTIFIED": len(unknown),
                "VERIFIED_PAIRS_WITH_FRESH_BOOKS": len(rows),
                "BLOCKED_BY": ["PMUS_CONTEMPORANEOUS_CAPTURE",
                               "KALSHI_FEE_SCHEDULE_UNVERIFIED"],
                "READ_FAILURES": failures})
    (out / "phasex_result.json").write_text(
        json.dumps(res, indent=1, default=str))
    (out / "phasex_kalshi_records.json").write_text(
        json.dumps(krecords, indent=1, default=str))
    (out / "phasex_receipts.json").write_text(
        json.dumps(cli.receipts, indent=1, default=str))
    (out / "phasex_report.txt").write_text("\n".join(lines) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
