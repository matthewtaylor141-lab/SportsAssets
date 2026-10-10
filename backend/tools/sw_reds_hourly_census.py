"""THE SOFTWARE-REDS REPLAY INSTRUMENT (RC6.3b root-cause audit, lane H0).

Two halves, both read-only and pure apart from reading files:

  1 · THE RESEARCH STATEMENT. `hourly_census_sql(now, hours)` writes the
      SELECT-only research file (research/rc63_sw_reds_census.sql) that
      exports the first-loss census's INPUTS for an hourly series: for each
      of `hours` one-hour windows ending at `now`, `now - 1 h`, ... (each
      [now_k - 3600, now_k + 1), the window capital_readiness.feeds.
      gate_software_reds_zero reads), the production statements of
      coverage_first_loss -- ledger_events_sql(), VALUATIONS_SQL,
      DECISIONS_SQL -- verbatim, with their bound parameters substituted,
      so what the research surface exports is exactly what the gate reads.
      One JSON line per row, kinds `hour`, `ev`, `val`, `dec`.

  2 · THE REPLAY. `hours_from_rows` groups those lines by hour,
      `census_row` runs coverage_first_loss.census over one hour's inputs
      with the taxonomy of the checked-out source and derives the gate's
      evidence exactly as gate_software_reds_zero does (by_class, software,
      software_by_code, software_codes_truncated), and `hourly_table`
      renders the 24-row readback (SOFTWARE by code, UNCLASSIFIED per row).
      `window_inputs` reads the packet-hour fixture
      (tests/fixtures/rc63_sw_reds_104_events.json, one record per event
      with its linked valuations and decisions) into the same inputs.

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

VERSION = "RC63_SW_REDS_HOURLY_CENSUS_V1"

#: the research-sql workflow's own read-only guard (research-sql.yml), kept
#: here so the file is refused locally before it is dispatched
MUTATING_KEYWORD = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|copy|"
    r"vacuum|reindex|refresh|call|do|merge|lock|set\s+role)\b", re.I)
#: the one line that parameterises the statement
PARAM_LINE = re.compile(
    r"^WITH p AS \(SELECT (?P<now>[0-9.]+)::float8 AS now, "
    r"(?P<hours>\d+)::int AS hours\),$", re.M)


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
    decs = _substitute(FL.DECISIONS_SQL, {
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
-- VALUATIONS_SQL, DECISIONS_SQL) verbatim with their parameters bound, one
-- JSON line per row: kind `hour` (the window), `ev` (one furthest
-- ext_candidate_outcomes row per provider event), `val` (the linked
-- external_valuations), `dec` (the paper_decisions on them). The replay
-- (backend/tools/sw_reds_hourly_census.py --log <run log>) runs
-- coverage_first_loss.census over each hour with the taxonomy of the
-- checked-out source and renders the hourly SOFTWARE-by-code table.
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
-- rc63_sw_reds_104_events.json.
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
-- coverage_first_loss.DECISIONS_SQL: $1 the hour's valuation ids, $2 since,
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
    SELECT k, 2, lpad(valuation_id::text, 12, '0'), coalesce(strategy, ''),
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


def hours_from_rows(rows) -> dict:
    """{k: {now, since, until, events, valuations, decisions}}."""
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
    return dict(sorted(out.items()))


def _dec_key(d: dict) -> tuple:
    return (d.get("valuation_id"), str(d.get("strategy")),
            str(d.get("verdict")), str(d.get("refusal")),
            json.dumps(d.get("refusals"), sort_keys=True))


def window_inputs(window: dict) -> tuple[list, list, list]:
    """(events, valuations, decisions) of one packet-hour fixture window
    (one record per event carrying `vals` and `decs`): valuations
    de-duplicated by id, decisions by their whole row, events stripped of
    the links. The fixture's shape is the audit's export (a valuation
    linked to two events appears under both)."""
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
    return events, list(vals.values()), list(decs.values())


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
    """One hour's census, rendered for the readback table."""
    got = FL.census(events, valuations, decisions)
    t = got["totals"]
    ev = gate_evidence(got)
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
                                got["by_competition"].items()})


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
    for row in table:
        by = {(r["stage"], r["code"]): r["events"]
              for r in _software_rows(row)}
        bc = row["by_class"]
        cells = [str(row["k"]),
                 "%s .. %s" % (_iso(row["since"]), _iso(row["now"])),
                 str(row["events"]), str(bc.get("SOFTWARE", 0)),
                 str(bc.get("ECONOMIC", 0)), str(bc.get("EXTERNAL", 0)),
                 str(bc.get("UNCLASSIFIED", 0))]
        cells += [str(by.get(sc, 0)) for sc in codes]
        lines.append("| " + " | ".join(cells) + " |")
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
    elif a.fixture:
        doc = json.loads(pathlib.Path(a.fixture).read_text())
        w = doc["windows"][a.window]
        ev, vl, dc = window_inputs(w)
        table = [census_row(ev, vl, dc, k=0, now=w["now"], since=w["since"],
                            until=w["until"])]
    else:
        ap.error("one of --sql, --log, --fixture")
        return 2
    out = {"version": VERSION, "census_version": FL.VERSION,
           "taxonomy_version": RT.VERSION, "rows": table}
    if a.out_json:
        pathlib.Path(a.out_json).write_text(json.dumps(out, indent=1) + "\n")
    md = render_markdown(table, title=a.title)
    if a.out_md:
        pathlib.Path(a.out_md).write_text(md)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
