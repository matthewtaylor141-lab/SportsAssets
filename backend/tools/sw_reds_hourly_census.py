"""THE SOFTWARE-REDS REPLAY INSTRUMENT (RC6.3b root-cause audit, lane H0).

Two halves, both read-only and pure apart from reading files:

  1 · THE RESEARCH STATEMENT. `hourly_census_sql(now, hours)` writes the
      SELECT-only research file (research/rc63_sw_reds_census.sql) that
      exports the first-loss census's INPUTS for an hourly series: for each
      of `hours` one-hour windows ending at `now`, `now - 1 h`, ... (each
      [now_k - 3600, now_k + 1), the window capital_readiness.feeds.
      gate_software_reds_zero reads), the production statements of
      coverage_first_loss -- ledger_events_sql(), VALUATIONS_SQL,
      DECISIONS_SQL -- with their bound parameters substituted, so what the
      research surface exports is what the gate reads. One JSON line per
      row, kinds `hour`, `ev`, `val`, `dec`.

      THE GATE'S ORDER IS PART OF ITS EVIDENCE. coverage_first_loss.
      first_loss_of_event takes an event's earliest-stage decision and,
      between two decisions at that stage, the FIRST IN RECORD ORDER -- the
      order DECISIONS_SQL returns them, ORDER BY decided_at. The statement
      does not select decided_at, so the export appends the two order
      columns (decided_at as an epoch, decision_id) to its select list: the
      one edit beyond binding its parameters, pinned by the test. The first
      version of this file did not, ordered a valuation's decisions by
      strategy instead, and so split the SOFTWARE count by code differently
      from the gate in 14 of the 24 hours (research-sql 38069842557 against
      38074718533, the same rows: the totals, by_class and UNCLASSIFIED
      were order-invariant on every row; the per-code cells were not).

  2 · THE REPLAY. `hours_from_rows` groups those lines by hour and puts each
      hour's inputs in the gate's own order (valuations by id, a valuation's
      decisions by decided_at then decision_id) -- an export without the
      order columns is refused, never replayed in some other order;
      `census_row` runs coverage_first_loss.census over one hour's inputs
      with the taxonomy of the checked-out source and derives the gate's
      evidence exactly as gate_software_reds_zero does (by_class, software,
      software_by_code, software_codes_truncated); `order_ambiguity` names,
      per row, the cells the gate's tie-break leaves to chance -- two
      decisions of one valuation at the earliest stage, different codes,
      the SAME decided_at -- which no export can reproduce, so the table
      marks them instead of presenting one resolution as the gate's;
      `hourly_table` renders the 24-row readback (SOFTWARE by code,
      UNCLASSIFIED per row). `window_inputs` reads the packet-hour fixture
      (tests/fixtures/rc63_sw_reds_104_events.json, one record per event
      with its linked valuations and decisions, captured without the order
      columns) into the same inputs; its windows carry no such tie, so the
      packet replay is exact whatever order its decisions are fed in (the
      test proves it).

Nothing here reads or moves a threshold, gate or rule; nothing writes to a
database. Usage:

    python tools/sw_reds_hourly_census.py --sql <now> [--hours 24]
    python tools/sw_reds_hourly_census.py --log <research-sql run log>
        [--out-json <path>] [--out-md <path>]
    python tools/sw_reds_hourly_census.py --fixture <path> [--window A]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve()
BACKEND = HERE.parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sportsassets import coverage_first_loss as FL  # noqa: E402
from sportsassets import refusal_taxonomy as RT  # noqa: E402
from sportsassets.capital_readiness import feeds as FEEDS  # noqa: E402

VERSION = "RC63_SW_REDS_HOURLY_CENSUS_V2"

#: the research-sql workflow's own read-only guard (research-sql.yml), kept
#: here so the file is refused locally before it is dispatched
MUTATING_KEYWORD = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|copy|"
    r"vacuum|reindex|refresh|call|do|merge|lock|set\s+role)\b", re.I)
#: the one line that parameterises the statement
PARAM_LINE = re.compile(
    r"^WITH p AS \(SELECT (?P<now>[0-9.]+)::float8 AS now, "
    r"(?P<hours>\d+)::int AS hours\),$", re.M)

#: the head of coverage_first_loss.DECISIONS_SQL's select list, pinned: the
#: export appends THE ORDER COLUMNS to it and nothing else
DECISIONS_SELECT = "SELECT valuation_id, verdict, refusal, refusals, strategy"
#: decided_at as an epoch (float8, microsecond-distinct at this magnitude)
#: and the row's key: DECISIONS_SQL orders the gate's read by decided_at;
#: decision_id makes an exact tie deterministic in the replay, and
#: `order_ambiguity` names every cell such a tie decided
DECISION_ORDER_COLUMNS = ("extract(epoch FROM decided_at)::float8 AS "
                          "decided_at_epoch, decision_id")
DECISION_ORDER = "decided_at_epoch, decision_id"


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RESEARCH STATEMENT
# ═════════════════════════════════════════════════════════════════════

def _substitute(sql: str, params: dict) -> str:
    """$n -> the expression bound to it. Every placeholder must be bound and
    every binding used (a drift in the production statement's parameters
    fails loudly instead of exporting the wrong window)."""
    found = set(re.findall(r"\$\d+", sql))
    assert found == set(params), (sorted(found), sorted(params))
    # longest placeholder first so $1 never eats the head of $10
    for k in sorted(params, key=len, reverse=True):
        sql = sql.replace(k, params[k])
    return sql


def _indent(sql: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + ln if ln.strip() else ln
                     for ln in sql.strip("\n").splitlines())


def decisions_export_sql() -> str:
    """coverage_first_loss.DECISIONS_SQL with the two order columns appended
    to its select list -- the only edit; the statement's head is pinned so
    a change to the production statement fails here instead of exporting
    something else under the same name."""
    sql = FL.DECISIONS_SQL
    head = sql.strip("\n").lstrip()
    assert head.startswith(DECISIONS_SELECT + "\n"), head.splitlines()[0]
    return sql.replace(DECISIONS_SELECT,
                       DECISIONS_SELECT + ",\n           "
                       + DECISION_ORDER_COLUMNS, 1)


def hourly_census_sql(now: float, hours: int = 24) -> str:
    """The research file: the census inputs for `hours` one-hour windows
    ending at now, now - 1 h, ... (hour k: [now - (k+1) h, now - k h + 1)).
    Pure; built from coverage_first_loss's own statements."""
    assert 1 <= int(hours) <= 7 * 24, hours
    ledger = _substitute(FL.ledger_events_sql(), {
        "$1": "h.since", "$2": "h.until", "$3": str(FL.MAX_EVENTS)})
    vals = _substitute(FL.VALUATIONS_SQL, {
        "$1": "h.since", "$2": "h.until",
        "$3": ("(SELECT coalesce(array_agg(DISTINCT e.provider_event_id::text"
               "), ARRAY[]::text[]) FROM ev e WHERE e.k = h.k)"),
        "$4": ("(SELECT coalesce(array_agg(DISTINCT s), ARRAY[]::text[]) "
               "FROM ev e, unnest(e.slugs) AS s WHERE e.k = h.k)"),
        "$5": str(FL.MAX_VALUATIONS)})
    decs = _substitute(decisions_export_sql(), {
        "$1": ("(SELECT coalesce(array_agg(v.id::bigint), ARRAY[]::bigint[]) "
               "FROM vl v WHERE v.k = h.k)"),
        "$2": "h.since", "$3": str(FL.MAX_VALUATIONS)})
    head = """\
-- READ-ONLY. RC6.3b root-cause audit, group software-reds, lane H0 (the
-- replay harness, backend/tests/test_rc63_software_reds_replay.py). THE
-- FIRST-LOSS CENSUS'S INPUTS FOR AN HOURLY SERIES: for each of `hours`
-- one-hour windows ending at `now`, `now` - 1 h, ... -- hour k is
-- [now - (k+1)*3600, now - k*3600 + 1), the window the approved-judge gate
-- software_reds_zero reads (capital_readiness.feeds.gate_software_reds_zero:
-- coverage_first_loss.read(since = now - 3600, until = now + 1)) -- the
-- production statements of coverage_first_loss (ledger_events_sql(),
-- VALUATIONS_SQL, DECISIONS_SQL) with their parameters bound, one JSON line
-- per row: kind `hour` (the window), `ev` (one furthest
-- ext_candidate_outcomes row per provider event), `val` (the linked
-- external_valuations), `dec` (the paper_decisions on them). The replay
-- (backend/tools/sw_reds_hourly_census.py --log <run log>) runs
-- coverage_first_loss.census over each hour with the taxonomy of the
-- checked-out source and renders the hourly SOFTWARE-by-code table.
--
-- THE GATE'S ORDER. coverage_first_loss.first_loss_of_event breaks a tie
-- between two same-stage decisions of one valuation by record order (the
-- first decision wins), and the gate's record order is DECISIONS_SQL's
-- ORDER BY decided_at. The statement does not select decided_at, so the
-- `dec` rows carry it (decided_at_epoch) and the row's decision_id beside
-- the statement's own columns: the one edit to the statement beyond
-- binding its parameters. The replay sorts a valuation's decisions by
-- (decided_at_epoch, decision_id) and names per hour the cells an exact
-- decided_at tie leaves to the gate's own read order.
--
-- GENERATED: backend/tools/sw_reds_hourly_census.py --sql <now> --hours <n>
-- (the test pins this file to the generator, so a change to the production
-- statements regenerates it rather than letting the export drift).
--
-- PARAMETER: `now` (epoch seconds, the instant the newest window ends at)
-- and `hours` on the first WITH line. The two fixed windows of the first
-- version of this file (now = 1791626074.8284767 and 1791626141.534181,
-- the packet's completion.json and capital_readiness.json instants, sha256
-- 5d72395ad2b0f67dc745e256fab130da19c321fb0b6438a770efeb4960c339fb) were
-- run as research-sql 38055963121 and are tests/fixtures/
-- rc63_sw_reds_104_events.json. The 24-hour series without the order
-- columns (sha256 3e3914a2304cb88238ff92c17a8e78e1b74a21345b407eb5ad8c4379
-- 521e6910) was research-sql 38069842557 and is superseded by this file.
--
-- SELECT only: no mutating keyword, no psql meta-command but \\echo.

\\echo == %(ver)s now=%(now)r hours=%(hours)d: one JSON line per row, kinds hour / ev / val / dec ==
WITH p AS (SELECT %(now)r::float8 AS now, %(hours)d::int AS hours),
h AS (
    SELECT k, p.now - k * 3600 AS now_k,
           p.now - (k + 1) * 3600 AS since,
           p.now - k * 3600 + 1 AS until
      FROM p, generate_series(0, p.hours - 1) AS k),
-- coverage_first_loss.ledger_events_sql(): $1 since, $2 until, $3 the cap
ev AS (
    SELECT h.k, e.* FROM h CROSS JOIN LATERAL (
%(ledger)s
    ) e),
-- coverage_first_loss.VALUATIONS_SQL: $1 since, $2 until, $3 the hour's
-- provider event ids, $4 the hour's venue contracts, $5 the cap
vl AS (
    SELECT h.k, v.* FROM h CROSS JOIN LATERAL (
%(vals)s
    ) v),
-- coverage_first_loss.DECISIONS_SQL with the order columns appended
-- (decided_at_epoch, decision_id): $1 the hour's valuation ids, $2 since,
-- $3 the cap
dc AS (
    SELECT h.k, d.* FROM h CROSS JOIN LATERAL (
%(decs)s
    ) d)
SELECT j FROM (
    SELECT k, -1 AS ord, '' AS o1, '' AS o2,
           json_build_object('kind', 'hour', 'k', k, 'now', now_k,
                             'since', since, 'until', until)::text AS j
      FROM h
    UNION ALL
    SELECT k, 0, sport_key, provider_event_id::text,
           json_build_object('kind', 'ev', 'k', k,
                             'row', to_jsonb(ev) - 'k')::text
      FROM ev
    UNION ALL
    SELECT k, 1, lpad(id::text, 12, '0'), '',
           json_build_object('kind', 'val', 'k', k,
                             'row', to_jsonb(vl) - 'k')::text
      FROM vl
    UNION ALL
    -- a valuation's decisions in the gate's order (decided_at, then the id)
    SELECT k, 2, lpad(valuation_id::text, 12, '0'),
           to_char(to_timestamp(decided_at_epoch) AT TIME ZONE 'UTC',
                   'YYYY-MM-DD HH24:MI:SS.US') || ' ' || decision_id,
           json_build_object('kind', 'dec', 'k', k,
                             'row', to_jsonb(dc) - 'k')::text
      FROM dc
) x ORDER BY k, ord, o1, o2, j;
""" % {"ver": VERSION, "now": float(now), "hours": int(hours),
       "ledger": _indent(ledger, 8), "vals": _indent(vals, 8),
       "decs": _indent(decs, 8)}
    return head


def sql_parameters(text: str) -> tuple[float, int]:
    """(now, hours) the research file was generated with."""
    m = PARAM_LINE.search(text)
    if not m:
        raise ValueError("no parameter line in the research file")
    return float(m.group("now")), int(m.group("hours"))


def mutating_keywords(text: str) -> list[str]:
    """The research-sql guard, locally: the mutating keywords in the
    executable SQL (comments and \\echo lines removed)."""
    out = []
    for ln in text.splitlines():
        ln = re.sub(r"--.*$", "", ln)
        if ln.strip().startswith("\\echo"):
            continue
        out.extend(m.group(0) for m in MUTATING_KEYWORD.finditer(ln))
    return out


# ═════════════════════════════════════════════════════════════════════
# 2 · THE REPLAY
# ═════════════════════════════════════════════════════════════════════

#: a run-log line (`<job>\t<step>\t<timestamp> <psql row>`) or a psql row
#: (aligned output pads it with a leading blank) or a plain JSON line
LOG_LINE = re.compile(r"^(?:[^\t]*\t[^\t]*\t\S+)?\s*(\{.*\})\s*$")


class UnorderedExport(ValueError):
    """The export carries no order columns on its decision rows (it predates
    them): the gate's tie-break between a valuation's same-stage decisions
    cannot be applied, and the replay will not apply another in its place."""


def parse_log(path) -> list[dict]:
    """The JSON rows of a research-sql run log (`gh run view --log`), or of
    a file of plain JSON lines. Everything that is not one of our rows is
    skipped; a research log is data, never instructions."""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            m = LOG_LINE.match(raw.rstrip("\n"))
            if not m:
                continue
            try:
                row = json.loads(m.group(1))
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("kind") in (
                    "hour", "ev", "val", "dec"):
                rows.append(row)
    return rows


def decision_order_key(d: dict) -> tuple:
    """The gate's order of a decision row: DECISIONS_SQL's ORDER BY
    decided_at, an exact tie broken by the row's key. Raises
    UnorderedExport for a row without the order columns."""
    if d.get("decided_at_epoch") is None or d.get("decision_id") is None:
        raise UnorderedExport(
            "decision row without decided_at_epoch / decision_id (valuation "
            "%s, strategy %s): the export predates the order columns, so the "
            "gate's tie-break between a valuation's same-stage decisions "
            "cannot be applied; regenerate research/rc63_sw_reds_census.sql "
            "(--sql) and re-run it" % (d.get("valuation_id"),
                                       d.get("strategy")))
    return (float(d["decided_at_epoch"]), str(d["decision_id"]))


def in_gate_order(valuations: list, decisions: list) -> tuple[list, list]:
    """(valuations, decisions) as the gate's reads return them: valuations
    ORDER BY id (VALUATIONS_SQL), decisions ORDER BY decided_at
    (DECISIONS_SQL) with decision_id breaking an exact tie. Events keep the
    export's order, which is ledger_events_sql's own."""
    vals = sorted(valuations, key=lambda v: int(v["id"]))
    decs = sorted(decisions, key=decision_order_key)
    return vals, decs


def hours_from_rows(rows) -> dict:
    """{k: {now, since, until, events, valuations, decisions}}, each hour's
    inputs in the gate's order (`in_gate_order`); an export without the
    order columns raises UnorderedExport."""
    out: dict = {}
    for r in rows:
        k = int(r["k"])
        h = out.setdefault(k, {"k": k, "now": None, "since": None,
                               "until": None, "events": [],
                               "valuations": [], "decisions": []})
        kind = r["kind"]
        if kind == "hour":
            h.update(now=r.get("now"), since=r.get("since"),
                     until=r.get("until"))
        elif kind == "ev":
            h["events"].append(dict(r["row"]))
        elif kind == "val":
            h["valuations"].append(dict(r["row"]))
        elif kind == "dec":
            h["decisions"].append(dict(r["row"]))
    for h in out.values():
        h["valuations"], h["decisions"] = in_gate_order(h["valuations"],
                                                        h["decisions"])
    return dict(sorted(out.items()))


def _dec_key(d: dict) -> tuple:
    return (d.get("valuation_id"), str(d.get("strategy")),
            str(d.get("verdict")), str(d.get("refusal")),
            json.dumps(d.get("refusals"), sort_keys=True))


def window_inputs(window: dict) -> tuple[list, list, list]:
    """(events, valuations, decisions) of one packet-hour fixture window
    (one record per event carrying `vals` and `decs`): valuations
    de-duplicated by id and in the gate's order (by id), decisions by their
    whole row in the fixture's order (the capture carries no decided_at:
    `order_ambiguity` treats a valuation's decisions as all tied, and the
    packet windows have no such tie), events stripped of the links. The
    fixture's shape is the audit's export (a valuation linked to two events
    appears under both)."""
    events, vals, decs, seen = [], {}, {}, set()
    for rec in window["events"]:
        ev = {k: v for k, v in rec.items() if k not in ("vals", "decs")}
        events.append(ev)
        for v in rec.get("vals") or ():
            vals.setdefault(v["id"], v)
        for d in rec.get("decs") or ():
            key = _dec_key(d)
            if key not in seen:
                seen.add(key)
                decs[key] = d
    return (events, sorted(vals.values(), key=lambda v: int(v["id"])),
            list(decs.values()))


def _decision_stage_code(d: dict) -> tuple:
    """(chain stage, code) of one REFUSE decision, exactly as
    coverage_first_loss.first_loss_of_event derives it."""
    codes = list(d.get("refusals") or []) or \
        ([d["refusal"]] if d.get("refusal") else [])
    st, c = FL._earliest(codes, mapped=True, default="ENTER_PASS")
    if st is None:
        st, c = "ENTER_PASS", FL.R_DECISION_NAMES_NO_CODE
    st, c, _wrapped = FL._unwrap(codes, (st, c), mapped=True)
    return st, c


def order_ambiguity(events: list, valuations: list, decisions: list) -> list:
    """THE CELLS THE GATE'S TIE-BREAK LEAVES TO CHANCE. For an event whose
    first loss is a paper decision, first_loss_of_event takes the
    earliest-stage decision and, between two at that stage, the first in
    record order: valuations as VALUATIONS_SQL returns them (by id), a
    valuation's decisions as DECISIONS_SQL returns them (ORDER BY
    decided_at). Two decisions of the deciding valuation at the earliest
    stage with DIFFERENT codes and the SAME decided_at (or no decided_at at
    all: the fixture's capture) are a tie the gate resolved by whatever
    order its own read returned, which no export reproduces. One record per
    such event: {provider_event_id, stage, codes, cells: [{stage, code,
    class}]}. Links records exactly as census does. Pure."""
    by_key: dict = {}
    by_slug: dict = {}
    for v in valuations or ():
        if v.get("event_key") is not None:
            by_key.setdefault(str(v["event_key"]), []).append(v)
        if v.get("us_market_slug"):
            by_slug.setdefault(str(v["us_market_slug"]), []).append(v)
    dec_by_val: dict = {}
    for d in decisions or ():
        dec_by_val.setdefault(d.get("valuation_id"), []).append(d)
    out = []
    for ev in events or ():
        seen: dict = {}
        for v in by_key.get(str(ev.get("provider_event_id")), ()):
            seen[v["id"]] = v
        for s in list(ev.get("slugs") or []) + [ev.get("us_market_slug")]:
            for v in by_slug.get(str(s), ()) if s else ():
                seen.setdefault(v["id"], v)
        per_val = [[d for d in dec_by_val.get(v["id"], ())] for v in
                   seen.values()]
        decs = [d for ds in per_val for d in ds]
        if not decs or any(str(d.get("verdict") or "").upper() == "ENTER"
                           for d in decs):
            continue
        staged = [[(d,) + _decision_stage_code(d) for d in ds]
                  for ds in per_val]
        earliest = min(FL.CHAIN.index(st) for ds in staged for _d, st, _c
                       in ds)
        for ds in staged:
            cand = [(d, st, c) for d, st, c in ds
                    if FL.CHAIN.index(st) == earliest]
            if not cand:
                continue
            # the deciding valuation: its first candidate in the gate's
            # order wins; the candidates sharing that decided_at are tied
            # (no decided_at at all, the fixture's capture: all of them)
            times = [d.get("decided_at_epoch") for d, _st, _c in cand]
            if any(t is None for t in times):
                first, tied = None, list(cand)
            else:
                first = min(float(t) for t in times)
                tied = [(d, st, c) for d, st, c in cand
                        if float(d["decided_at_epoch"]) == first]
            codes = []
            for _d, _st, c in tied:
                k = FL.classify(c)
                if k["code"] not in codes:
                    codes.append(k["code"])
            if len(codes) > 1:
                stage = FL.CHAIN[earliest]
                out.append({
                    "provider_event_id": ev.get("provider_event_id"),
                    "stage": stage, "decided_at_epoch": first,
                    "codes": codes,
                    "cells": [{"stage": stage, "code": c,
                               "class": FL.classify(c)["class"]}
                              for c in codes]})
            break
    return out


def gate_evidence(got: dict) -> dict:
    """The software_reds_zero gate's evidence from a census result, derived
    exactly as capital_readiness.feeds.gate_software_reds_zero derives it
    (the by-code rows of class SOFTWARE, capped at SOFTWARE_BY_CODE_MAX)."""
    totals = got.get("totals") or {}
    by = totals.get("by_class") or {}
    by_code = [{"stage": r.get("stage"), "code": r.get("code"),
                "events": r.get("events")}
               for r in (totals.get("by_code") or [])
               if isinstance(r, dict) and r.get("class") == RT.SOFTWARE]
    return {"software": by.get(RT.SOFTWARE), "by_class": dict(by),
            "window_s": FEEDS.FIRST_LOSS_WINDOW_S,
            "software_by_code": by_code[:FEEDS.SOFTWARE_BY_CODE_MAX],
            "software_codes_truncated":
                len(by_code) > FEEDS.SOFTWARE_BY_CODE_MAX,
            "source": "coverage_first_loss.read (1 h)"}


def census_row(events, valuations, decisions, **hour) -> dict:
    """One hour's census, rendered for the readback table, with the cells
    the gate's tie-break leaves to chance named (`order_ambiguity`,
    `order_ambiguous_events`, `order_ambiguous_cells`)."""
    got = FL.census(events, valuations, decisions)
    t = got["totals"]
    ev = gate_evidence(got)
    amb = order_ambiguity(events, valuations, decisions)
    cells = []
    for a in amb:
        for c in a["cells"]:
            if c not in cells:
                cells.append(c)
    cells.sort(key=lambda c: (FL.CHAIN.index(c["stage"]), c["code"]))
    return dict(hour, events=t["provider_events"], entered=t["entered"],
                unavailable=t["unavailable"], by_class=t["by_class"],
                software=ev["software"],
                unclassified=t["by_class"].get(RT.UNCLASSIFIED, 0),
                software_by_code=ev["software_by_code"],
                software_codes_truncated=ev["software_codes_truncated"],
                by_code=[{k: r.get(k) for k in ("stage", "code", "class",
                                                 "events", "source")}
                         for r in t["by_code"]],
                first_loss=t["first_loss"],
                valuations=len(valuations), decisions=len(decisions),
                valuations_linked=got["valuations_linked"],
                by_competition={k: v["provider_events"] for k, v in
                                got["by_competition"].items()},
                order_ambiguous_events=len(amb),
                order_ambiguous_cells=cells,
                order_ambiguity=amb)


def hourly_table(hours: dict) -> list[dict]:
    """The readback rows, hour 0 (the newest window) first."""
    return [census_row(h["events"], h["valuations"], h["decisions"],
                       k=k, now=h["now"], since=h["since"], until=h["until"])
            for k, h in sorted(hours.items())]


def _iso(epoch) -> str:
    import datetime as dt
    if epoch is None:
        return "?"
    return dt.datetime.fromtimestamp(float(epoch), dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _software_rows(row: dict) -> list[dict]:
    """Every SOFTWARE by-code row of the hour (the census's full list, not
    the gate's evidence list, which is capped at SOFTWARE_BY_CODE_MAX)."""
    return [r for r in row["by_code"] if r.get("class") == RT.SOFTWARE]


AMBIGUOUS_MARK = "*"
AMBIGUOUS_LEGEND = (
    "`*` the cell's count depends on which of two same-stage decisions of "
    "one valuation, written at the same decided_at with different codes, "
    "the gate's own read returned first (DECISIONS_SQL orders by decided_at "
    "alone); the replay shows the decision_id order. The row's SOFTWARE "
    "total, by_class and UNCLASSIFIED do not depend on it. The JSON names "
    "the events (`order_ambiguity`).")


def render_markdown(table: list[dict], *, title: str = "") -> str:
    codes = []
    for row in table:
        for r in _software_rows(row):
            key = (r["stage"], r["code"])
            if key not in codes:
                codes.append(key)
    codes.sort(key=lambda sc: (FL.CHAIN.index(sc[0]), sc[1]))
    heads = ["k", "window (UTC)", "events", "SOFTWARE", "ECONOMIC",
             "EXTERNAL", "UNCLASSIFIED"] + ["%s %s" % sc for sc in codes]
    lines = []
    if title:
        lines += ["# " + title, ""]
    lines.append("| " + " | ".join(heads) + " |")
    lines.append("|" + "|".join("---" for _ in heads) + "|")
    marked = False
    for row in table:
        by = {(r["stage"], r["code"]): r["events"]
              for r in _software_rows(row)}
        amb = {(c["stage"], c["code"])
               for c in row.get("order_ambiguous_cells") or ()}
        bc = row["by_class"]
        cells = [str(row["k"]),
                 "%s .. %s" % (_iso(row["since"]), _iso(row["now"])),
                 str(row["events"]), str(bc.get("SOFTWARE", 0)),
                 str(bc.get("ECONOMIC", 0)), str(bc.get("EXTERNAL", 0)),
                 str(bc.get("UNCLASSIFIED", 0))]
        for sc in codes:
            cell = str(by.get(sc, 0))
            if sc in amb:
                cell += AMBIGUOUS_MARK
                marked = True
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    if marked:
        lines += ["", AMBIGUOUS_LEGEND]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sql", type=float, metavar="NOW",
                    help="print the research statement for a series "
                         "ending at NOW (epoch seconds)")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--log", help="a research-sql run log or JSON lines")
    ap.add_argument("--fixture", help="the packet-hour fixture")
    ap.add_argument("--window", default="A")
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    ap.add_argument("--title", default="")
    a = ap.parse_args(argv)
    if a.sql is not None:
        sys.stdout.write(hourly_census_sql(a.sql, a.hours))
        return 0
    if a.log:
        hours = hours_from_rows(parse_log(a.log))
        table = hourly_table(hours)
        source = {"kind": "research-sql run log",
                  "file": pathlib.Path(a.log).name,
                  "decision_order": DECISION_ORDER}
    elif a.fixture:
        doc = json.loads(pathlib.Path(a.fixture).read_text())
        w = doc["windows"][a.window]
        ev, vl, dc = window_inputs(w)
        table = [census_row(ev, vl, dc, k=0, now=w["now"], since=w["since"],
                            until=w["until"])]
        source = {"kind": "packet-hour fixture",
                  "file": pathlib.Path(a.fixture).name, "window": a.window,
                  "decision_order": "the capture's (no decided_at; every "
                                    "valuation's decisions treated as tied)"}
    else:
        ap.error("one of --sql, --log, --fixture")
        return 2
    out = {"version": VERSION, "census_version": FL.VERSION,
           "taxonomy_version": RT.VERSION, "source": source,
           "order_ambiguous_rows": [r["k"] for r in table
                                    if r["order_ambiguous_events"]],
           "rows": table}
    if a.out_json:
        pathlib.Path(a.out_json).write_text(json.dumps(out, indent=1) + "\n")
    md = render_markdown(table, title=a.title)
    if a.out_md:
        pathlib.Path(a.out_md).write_text(md)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
