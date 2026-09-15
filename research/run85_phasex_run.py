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
    t0 = time.monotonic()
    with httpx.Client(timeout=30.0) as http:
        events = BL.discover(http, pacer, budget, cli_pages, samples, res)
    t1 = time.monotonic()
    out = []
    for ev in events:
        for m in ev.get("markets") or []:
            if m.get("status") != "MARKET_STATUS_OPEN":
                continue
            out.append(PX1.build_record(ev, m, []))
    # Split so the next run can tell a slow VENUE walk from slow LOCAL
    # record building. Run 34971707012 could distinguish neither.
    res["DISCOVERY_SECONDS"] = round(t1 - t0, 3)
    res["RECORD_BUILD_SECONDS"] = round(time.monotonic() - t1, 3)
    res["EVENTS_WALKED"] = len(events)
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


def _quoted(rec: dict, slot: str):
    n = rec.get(slot) or {}
    if n.get("status") != K.QUOTED:
        return None
    return " ".join(h["text"] for h in n["value"])


def pmus_dimension_half(prec: dict) -> dict:
    """The PMUS-side value of every material dimension.

    Split out of dimensions() so it is computed ONCE per contract instead
    of once per candidate pair. Deterministic in `prec` alone -- it reads
    no Kalshi field and no comparison result -- so hoisting it cannot
    change any classification. Slots this venue does not publish are
    explicit None, which the gate scores UNRESOLVED.
    """
    def pv(key, sub=None):
        n = prec.get(key) or {}
        v = n.get("value")
        return (v or {}).get(sub) if (sub and isinstance(v, dict)) else v

    return {
        "SPORT": pv("SPORT"),
        "LEAGUE": pv("LEAGUE"),
        "UNDERLYING_EVENT": pv("EVENT", "title"),
        "PARTICIPANTS": pv("PARTICIPANTS_FROM_SIDES"),
        "PROPOSITION": None,      # set by the payoff pair, not a field
        "LINE": pv("MARKET_TYPE", "line"),
        "PERIOD": pv("MARKET_TYPE", "sportsMarketType"),
        "START_TIME": pv("GAME_START_TIME"),
        "CLOSE_TIME": pv("END_TIME"),
        "SETTLEMENT_SOURCE": pv("LEAGUE_RESOLUTION_SOURCE"),
        "SETTLEMENT_RULE": _quoted(prec, "OTHER_RESOLUTION_CONDITIONS"),
        "OVERTIME": _quoted(prec, "OVERTIME_RULES"),
        "EXTRA_TIME": None,
        "POSTPONEMENT": _quoted(prec, "POSTPONEMENT_RULES"),
        "CANCELLATION": _quoted(prec, "CANCELLATION_RULES"),
        "DRAW_TIE": _quoted(prec, "DRAW_RULES"),
        "VOID": _quoted(prec, "VOID_RULES"),
        "OTHER_MATERIAL_CONDITIONS": None,
    }


def kalshi_dimension_half(krec: dict) -> dict:
    """The Kalshi-side value of every material dimension. Same contract as
    pmus_dimension_half: deterministic in `krec` alone."""
    def kv(key):
        return (krec.get(key) or {}).get("value")

    return {
        "SPORT": None,
        "LEAGUE": kv("SERIES_TICKER"),
        "UNDERLYING_EVENT": kv("EVENT_TITLE"),
        "PARTICIPANTS": [x for x in (kv("YES_SUB_TITLE"),
                                     kv("NO_SUB_TITLE")) if x] or None,
        "PROPOSITION": None,
        "LINE": kv("STRIKE"),
        "PERIOD": kv("MARKET_TYPE"),
        "START_TIME": None,
        "CLOSE_TIME": kv("CLOSE_TIME"),
        "SETTLEMENT_SOURCE": kv("SETTLEMENT_SOURCES"),
        "SETTLEMENT_RULE": _quoted(krec, "OTHER_RESOLUTION_CONDITIONS"),
        "OVERTIME": _quoted(krec, "OVERTIME_RULES"),
        "EXTRA_TIME": None,
        "POSTPONEMENT": _quoted(krec, "POSTPONEMENT_RULES"),
        "CANCELLATION": _quoted(krec, "CANCELLATION_RULES"),
        "DRAW_TIE": _quoted(krec, "DRAW_RULES"),
        "VOID": _quoted(krec, "VOID_RULES"),
        "OTHER_MATERIAL_CONDITIONS": None,
    }


def dimensions_from_halves(ph: dict, kh: dict) -> dict:
    """Zip two precomputed halves into the (pmus, kalshi) pairs the gate
    reads. Pure rearrangement: no value is derived, defaulted or dropped."""
    return {d: (ph[d], kh[d]) for d in E.MATERIAL_DIMENSIONS}


def dimensions(prec: dict, krec: dict) -> dict:
    """The material dimensions, each read from BOTH venues' own fields.

    Whatever neither venue published stays (None, None), which the gate
    scores UNRESOLVED -- deliberately, because two silences are not
    agreement.

    Kept as the single-pair entry point and as the thing the offline gate
    compares the halved path against, byte for byte.
    """
    return dimensions_from_halves(pmus_dimension_half(prec),
                                  kalshi_dimension_half(krec))


# ------------------------------------------------- lossless candidate index
def event_key(value) -> str | None:
    """The UNDERLYING_EVENT value reduced to exactly what
    E.compare_dimension() compares two present strings by, or None when no
    such comparison applies.

    None is the conservative answer and means "this pair carries no
    elimination proof, evaluate it in full". Only a present, non-blank
    STRING on both sides licenses the proof below, because that is the
    only branch of compare_dimension() whose outcome this function can
    predict without running it.
    """
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def index_eliminates(pmus_event, kalshi_event) -> bool:
    """True only when the pair is PROVABLY EQUIVALENCE_REJECTED.

    THE PROOF, and it is a proof rather than a heuristic:
      1. compare_dimension(a, b) with both present, unequal strings
         returns MISMATCH.
      2. equivalence() sets EQUIVALENCE_REJECTED whenever the MISMATCH
         list is non-empty -- that branch is checked FIRST and nothing
         downstream can overturn it.
    So two different, present event titles settle the pair's status with
    no reference to any other dimension, either payoff proof, or any
    price. Skipping the call therefore cannot change a classification, and
    the skipped pairs are counted as REJECTED exactly as if it had run.

    It can never promote a pair: elimination yields REJECTED and only
    REJECTED, and every pair NOT eliminated is still handed to the full
    equivalence gate unchanged.
    """
    a, b = event_key(pmus_event), event_key(kalshi_event)
    return a is not None and b is not None and a != b


def classify_pairs(precs: list, krecords: list, say=None) -> tuple:
    """Run the frozen equivalence gate over the PMUS x Kalshi product and
    return (verified_pairs, counts, census).

    IDENTICAL IN RESULT to the all-by-all loop it replaces; the offline
    gate proves that against a reference implementation copied from the
    frozen original. Two things make it tractable:

      1. Deterministic per-contract parsing is hoisted -- each contract's
         payoff proposition and dimension half is computed ONCE.
      2. Pairs that index_eliminates() proves are EQUIVALENCE_REJECTED are
         counted as rejected without calling the gate.

    Neither touches the gate itself. E.equivalence remains the only thing
    that assigns a status to any pair it is asked about, and the eliminated
    pairs get the status the gate would have given them.
    """
    say = say or (lambda _m="": None)
    pp_all = [pmus_side(p) for p in precs]
    ph_all = [pmus_dimension_half(p) for p in precs]
    kp_all = [kalshi_side(k) for k in krecords]
    kh_all = [kalshi_dimension_half(k) for k in krecords]
    n_p, n_k = len(precs), len(krecords)

    by_event: dict = {}
    for j, kh in enumerate(kh_all):
        by_event.setdefault(event_key(kh["UNDERLYING_EVENT"]), []).append(j)
    unkeyed_k = by_event.get(None, [])

    # A per-dimension census over every pair actually evaluated. This is
    # what makes a GATE D result diagnostic instead of merely negative: it
    # names WHICH dimension refused, rather than reporting that none
    # passed.
    census = {d: {E.MATCH: 0, E.MISMATCH: 0, E.UNRESOLVED: 0}
              for d in E.MATERIAL_DIMENSIONS}
    blockers: dict = {}

    verified: list = []
    n_verified = n_rejected = n_unknown = 0
    eliminated = evaluations = candidates = 0
    t0 = time.monotonic()
    for i, prec in enumerate(precs):
        pk = event_key(ph_all[i]["UNDERLYING_EVENT"])
        cand = range(n_k) if pk is None else (by_event.get(pk, []) + unkeyed_k)
        n_cand = n_k if pk is None else len(cand)
        candidates += n_cand
        # The skipped pairs are not discarded: index_eliminates() proves
        # each is EQUIVALENCE_REJECTED, so they are counted as rejected and
        # POTENTIAL_MATCHES / EQUIVALENCE_REJECTED stay exactly what
        # all-by-all produces.
        eliminated += n_k - n_cand
        n_rejected += n_k - n_cand

        pp = pp_all[i]
        for j in cand:
            evaluations += 1
            gate = E.equivalence(pp, kp_all[j],
                                 dimensions_from_halves(ph_all[i], kh_all[j]))
            for d, r in gate["DIMENSION_RESULTS"].items():
                census[d][r] += 1
            status = gate["EQUIVALENCE_STATUS"]
            if status == E.EQUIVALENCE_VERIFIED:
                n_verified += 1
                verified.append({"PMUS": prec, "KALSHI": krecords[j],
                                 "GATE": gate})
            elif status == E.EQUIVALENCE_REJECTED:
                n_rejected += 1
            elif gate["DIMENSION_RESULTS"].get("PARTICIPANTS") == E.MATCH:
                # The frozen filter keeps a NOT_IDENTIFIED pair only when
                # the participants match, and drops the rest uncounted.
                n_unknown += 1
                key = ",".join(gate["UNRESOLVED_DIMENSIONS"])
                blockers[key] = blockers.get(key, 0) + 1
        if n_p >= 200 and (i + 1) % max(1, n_p // 20) == 0:
            say("  [progress] pmus %d/%d  evaluated=%d  eliminated=%d  "
                "verified=%d  (%.1f s)"
                % (i + 1, n_p, evaluations, eliminated, n_verified,
                   time.monotonic() - t0))

    counts = {
        "PMUS_CONTRACTS_DISCOVERED": n_p,
        "KALSHI_CONTRACTS_DISCOVERED": n_k,
        "PAIRS_ALL_BY_ALL": n_p * n_k,
        "CANDIDATE_PAIRS_AFTER_INDEX": candidates,
        "PAIRS_ELIMINATED_BY_INDEX": eliminated,
        "EQUIVALENCE_EVALUATIONS": evaluations,
        "POTENTIAL_MATCHES": n_verified + n_rejected + n_unknown,
        "EQUIVALENCE_VERIFIED": n_verified,
        "EQUIVALENCE_REJECTED": n_rejected,
        "EQUIVALENCE_NOT_IDENTIFIED": n_unknown,
    }
    top = sorted(blockers.items(), key=lambda kv: -kv[1])[:20]
    return verified, counts, {"DIMENSION_CENSUS": census,
                              "NOT_IDENTIFIED_BLOCKERS": top}


# ------------------------------------------------------- evidence seal --
REQUIRED_OUTPUT = (
    "HOST_USED", "PMUS_REACHABILITY", "KALSHI_REACHABILITY",
    "READONLY_AUDIT", "PMUS_CONTRACTS_DISCOVERED",
    "KALSHI_CONTRACTS_DISCOVERED", "CANDIDATE_PAIRS_AFTER_INDEX",
    "EQUIVALENCE_EVALUATIONS", "EQUIVALENCE_VERIFIED",
    "EQUIVALENCE_REJECTED", "EQUIVALENCE_NOT_IDENTIFIED",
    "EXECUTABLE_PAIRS_TESTED", "BEST_GROSS_EDGE", "BEST_NET_EDGE",
    "MAX_EXECUTABLE_SIZE_AT_POSITIVE_NET", "TOTAL_POSITIVE_NET_CAPACITY",
    "OBSERVATION_TIMING_GAP", "GATE",
)

# A field this run did not reach is NOT_REACHED. It is never 0 and never a
# negative result: run 34971707012 died mid-measurement and its economic
# fields had to be reported this way, which is exactly why the vocabulary
# exists as a constant rather than a convention.
NOT_REACHED = "NOT_REACHED"


def _read_context(out: Path) -> dict:
    """The workflow's own step receipts, echoed into the result so the
    JSON is self-contained. Read-only; absence is recorded, never
    invented."""
    ctx = {"HOST_USED": NOT_REACHED, "PMUS_REACHABILITY": NOT_REACHED,
           "KALSHI_REACHABILITY": NOT_REACHED, "READONLY_AUDIT": NOT_REACHED}
    reach = out / "reachability.txt"
    if reach.exists():
        for ln in reach.read_text().splitlines():
            if "=" not in ln:
                continue
            k, v = (x.strip() for x in ln.split("=", 1))
            if k == "HOST_USED":
                ctx["HOST_USED"] = v
            elif k == "gateway.polymarket.us":
                ctx["PMUS_REACHABILITY"] = v
            elif k == "api.elections.kalshi.com":
                ctx["KALSHI_REACHABILITY"] = v
    audit = out / "readonly_audit.txt"
    if audit.exists():
        txt = audit.read_text()
        ctx["READONLY_AUDIT"] = ("PASS" if "AUDIT = PASS" in txt
                                 else "NOT_PASS")
    return ctx


def seal(out: Path, res: dict, lines: list, stages: dict,
         krecords: list, cli, verified: list) -> None:
    """Write every artifact on EVERY exit path.

    The Kalshi contract records and the request receipts used to be
    written only on the X2 path, so a GATE D run -- the most likely
    outcome and the one whose inputs most need auditing -- threw away the
    contracts it had just read. Sealing is not a result, so it is not
    gated on one.
    """
    res.setdefault("GATE", None)
    res["STAGE_SECONDS"] = stages
    res.update(_read_context(out))
    for k in REQUIRED_OUTPUT:
        res.setdefault(k, NOT_REACHED)

    lines.append("")
    lines.append("--- REQUIRED OUTPUT ---")
    for k in REQUIRED_OUTPUT:
        line = "  %-36s %s" % (k, res.get(k))
        lines.append(line)
        print(line, flush=True)

    (out / "phasex_result.json").write_text(
        json.dumps(res, indent=1, default=str))
    (out / "phasex_report.txt").write_text("\n".join(lines) + "\n")
    if krecords:
        (out / "phasex_kalshi_records.json").write_text(
            json.dumps(krecords, indent=1, default=str))
    if cli is not None:
        (out / "phasex_receipts.json").write_text(
            json.dumps(cli.receipts, indent=1, default=str))
    # Every VERIFIED pair in full: both original contract records, both
    # normalized payoff propositions with their proof chains, and the
    # dimension-by-dimension gate result.
    (out / "phasex_verified_pairs.json").write_text(
        json.dumps(verified, indent=1, default=str))


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

    # EVERY LINE FLUSHED. Run 34971707012 was killed by the job timeout
    # with 58 minutes of progress still sitting in Python's block buffer,
    # so the sealed log ended at the last line Track B-L's own logger had
    # flushed and said nothing about where the time went. A measurement
    # that cannot be diagnosed when it dies is a measurement that has to be
    # run twice.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    t_start = time.monotonic()
    stages: dict[str, float] = {}

    def say(msg: str = "") -> None:
        lines.append(msg)
        print(msg, flush=True)

    def stage(name: str, t0: float) -> float:
        now = time.monotonic()
        stages[name] = round(now - t0, 3)
        say("  [stage] %-28s %8.1f s  (elapsed %.1f s)"
            % (name, now - t0, now - t_start))
        return now

    res: dict = {"PHASE": "X", "GATE": None}
    say("=" * 70)
    say("PHASE X -- CROSS-VENUE (PMUS <-> KALSHI) -- READ ONLY")
    say("=" * 70)

    if not PMUS_RECORDS.exists():
        say("PMUS side absent: %s" % PMUS_RECORDS)
        say("GATE = %s" % GATE_E)
        return 2
    sealed = json.loads(PMUS_RECORDS.read_text())
    t0 = time.monotonic()
    if a.pmus == "live":
        try:
            pages, dres = [], {}
            precs = pmus_universe(pages, [], dres)
            say("PMUS_SPORTS_MARKETS = %d (live board)" % len(precs))
            say("  discovery %.1f s over %d events; record build %.1f s"
                % (dres.get("DISCOVERY_SECONDS", -1),
                   dres.get("EVENTS_WALKED", -1),
                   dres.get("RECORD_BUILD_SECONDS", -1)))
            res["PMUS_DISCOVERY"] = {
                k: dres.get(k) for k in ("DISCOVERY_SECONDS",
                                         "RECORD_BUILD_SECONDS",
                                         "EVENTS_WALKED")}
            t0 = stage("pmus_discovery_and_records", t0)
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
            seal(out, res, lines, stages, [], None, [])
            return 2
    else:
        precs = sealed["RECORDS"]
        say("PMUS_SPORTS_MARKETS = %d (sealed %s, reference only)"
            % (sealed["RECORD_COUNT"], sealed["RECORD_VERSION"]))
        t0 = stage("pmus_sealed_load", t0)

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
        say("  [progress] kalshi series %-14s cumulative markets=%d"
            % (series, len(krecords)))

    say("KALSHI_SPORTS_MARKETS = %d" % len(krecords))
    t0 = stage("kalshi_discovery", t0)
    if failures:
        say("KALSHI_READ_FAILURES = %s" % json.dumps(failures))
    if not krecords:
        # Every requested observation failed. That is an ACCESS fact, and
        # it must never be reported as "no opportunities were found".
        say("no Kalshi contract was retrieved; nothing was measured")
        say("GATE = %s" % GATE_E)
        res.update({"GATE": GATE_E, "KALSHI_CONTRACTS_DISCOVERED": 0,
                    "PMUS_CONTRACTS_DISCOVERED": len(precs),
                    "READ_FAILURES": failures})
        seal(out, res, lines, stages, [], cli, [])
        return 2

    # ---- X1 continued: match, then gate BEFORE any price is looked at --
    #
    # DETERMINISTIC PER-CONTRACT PARSING IS HOISTED. pmus_side(),
    # kalshi_side() and each dimension half depend on ONE record and
    # nothing else -- not on the other venue, not on a comparison result --
    # so computing each once instead of once per pair is arithmetic, not
    # judgement. Run 34971707012 re-parsed every Kalshi contract's rules
    # prose once per PMUS contract.
    verified, counts, census = classify_pairs(precs, krecords, say)
    for k in ("PMUS_CONTRACTS_DISCOVERED", "KALSHI_CONTRACTS_DISCOVERED",
              "PAIRS_ALL_BY_ALL", "CANDIDATE_PAIRS_AFTER_INDEX",
              "PAIRS_ELIMINATED_BY_INDEX", "EQUIVALENCE_EVALUATIONS",
              "POTENTIAL_MATCHES", "EQUIVALENCE_VERIFIED",
              "EQUIVALENCE_REJECTED", "EQUIVALENCE_NOT_IDENTIFIED"):
        say("%-28s = %d" % (k, counts[k]))
    t0 = stage("equivalence", t0)

    # WHICH DIMENSION REFUSED. Printed on every run, because "nothing
    # passed" is only actionable once you know what stopped it.
    say()
    say("--- DIMENSION CENSUS (over evaluated pairs) ---")
    say("  %-28s %10s %10s %11s" % ("DIMENSION", "MATCH", "MISMATCH",
                                    "UNRESOLVED"))
    for d in E.MATERIAL_DIMENSIONS:
        c = census["DIMENSION_CENSUS"][d]
        say("  %-28s %10d %10d %11d"
            % (d, c[E.MATCH], c[E.MISMATCH], c[E.UNRESOLVED]))
    res.update(census)

    if not verified:
        say("no pair reached the equivalence gate; no economics were run")
        say("GATE = %s" % GATE_D)
        res.update(counts)
        res.update({"GATE": GATE_D, "READ_FAILURES": failures})
        seal(out, res, lines, stages, krecords, cli, verified)
        return 1

    # ---- X2/X3/X4 ------------------------------------------------------
    say()
    say("--- X2: books, then X3 both directions, then X4 economics ---")
    say("PMUS books are NOT fetched here: this container has no PMUS "
        "capture in flight and a stale PMUS book beside a fresh Kalshi "
        "one is not a simultaneous observation. Supply a contemporaneous "
        "PMUS capture to complete X2.")
    rows = []
    for n, p in enumerate(verified):
        tk = (p["KALSHI"]["TICKER"] or {}).get("value")
        r = cli.orderbook(tk)
        if not r["ok"]:
            failures.append({"ticker": tk, "outcome": r["outcome"]})
            continue
        bk = K.kalshi_book_record(tk, r["body"] or {},
                                  r["local_receipt_wall"],
                                  r["local_receipt_monotonic"])
        rows.append({"PAIR": p, "KALSHI_BOOK": bk})
        p["KALSHI_BOOK"] = bk
        if (n + 1) % 25 == 0:
            say("  [progress] kalshi books %d/%d  (%.1f s)"
                % (n + 1, len(verified), time.monotonic() - t0))

    say("VERIFIED_PAIRS_WITH_FRESH_BOOKS = %d" % len(rows))
    say("POSITIVE_GROSS_PAIR_COUNT = NOT_MEASURED (PMUS leg absent)")
    say("POSITIVE_NET_PAIR_COUNT   = %s" % E.NOT_IDENTIFIED_FEES)
    say("GATE = %s" % GATE_E)
    stage("x2_kalshi_books", t0)
    res.update(counts)
    res.update({"GATE": GATE_E,
                "VERIFIED_PAIRS_WITH_FRESH_BOOKS": len(rows),
                # X2 is NOT complete: this driver fetches the Kalshi leg
                # only, so no economics are computed and these are not
                # results. See the X2 note printed above.
                "EXECUTABLE_PAIRS_TESTED": 0,
                "BEST_GROSS_EDGE": "NOT_MEASURED_PMUS_LEG_ABSENT",
                "BEST_NET_EDGE": E.NOT_IDENTIFIED_FEES,
                "MAX_EXECUTABLE_SIZE_AT_POSITIVE_NET":
                    "NOT_MEASURED_PMUS_LEG_ABSENT",
                "TOTAL_POSITIVE_NET_CAPACITY":
                    "NOT_MEASURED_PMUS_LEG_ABSENT",
                "OBSERVATION_TIMING_GAP": "NOT_MEASURED_PMUS_LEG_ABSENT",
                "BLOCKED_BY": ["PMUS_CONTEMPORANEOUS_CAPTURE",
                               "KALSHI_FEE_SCHEDULE_UNVERIFIED"],
                "READ_FAILURES": failures})
    seal(out, res, lines, stages, krecords, cli, verified)
    return 1


if __name__ == "__main__":
    sys.exit(main())
