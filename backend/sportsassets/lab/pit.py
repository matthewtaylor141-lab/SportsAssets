"""THE LAB'S ONE POINT-IN-TIME ACCESSOR (anti-lookahead).

Every historical computation of the edge-decay lab (sportsassets.lab.
edge_decay, .edge_decay_reads) sees a recorded row ONLY through this module,
and only when the row was already KNOWN at the replay clock:

    known_at(row) = the LATEST of the row's own recorded stamps
                    (e.g. a book observation is known at max(observed_at,
                    recorded_at): observed_at is our receipt instant, and
                    recorded_at is the database's now() of the inserting
                    transaction, which can START before the read finishes)

    visible at clock C  <=>  known_at(row) <= C

A row missing any of its stamps is NOT visible (fail closed: an unknown
instant is never assumed to be the past). A column written AFTER its row (a
paper order's terminal state) is REVEALED only when its own stamp is <= the
clock, else it reads None and the row names the masked columns. A table
whose rows are rewritten in place (us_premap, execution_intents) is visible
only when its LAST write is <= the clock -- its earlier content is
UNAVAILABLE, never guessed.

The rule is enforced three ways: (1) a table is readable only if it is
REGISTERED here with its stamps; (2) the SQL filter `<stamp> <= clock` on
every stamp (single table, caller fragments refused if they could carry
another table's rows past the filter); (3) the POST-CHECK: every returned
row is checked again in Python and a row known after the clock raises
LookaheadViolation (defence in depth; tests prove it fires on a
deliberately future-dated row forced past the SQL filter).

The same pure filter (`visible`) serves the rows of a production
read-only extraction (research/lab_a_edge_decay_extract.sql), so a replay of
production evidence obeys the identical rule.

Interface note: the R30C replay stream builds a fuller choke point
(replay/pit.py, same rule: recorded <= replay clock). After integration this
registry's tables map one to one onto it; the lab keeps this thin accessor
so it does not depend on an unintegrated branch.

Read only: this module issues SELECTs and nothing else. It imports nothing
from any order, venue, execution or funded module.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from decimal import Decimal

APPEND_ONLY = "APPEND_ONLY_EVENT"
REWRITTEN = "ROW_REWRITTEN_IN_PLACE"
MAX_LIMIT = 20000
EPS_S = 1e-6


class LookaheadViolation(AssertionError):
    """A row, a table or a query that would let the lab see past its clock."""


@dataclass(frozen=True)
class Source:
    stamps: tuple
    kind: str
    basis: str
    #: (stamp column, columns revealed only when that stamp <= the clock)
    masked: tuple = ()


REGISTRY: dict = {
    "paper_decisions": Source(
        ("decided_at", "recorded_at"), APPEND_ONLY,
        "the decision record (append-only); known when decided AND recorded"),
    "paper_book_observations": Source(
        ("observed_at", "recorded_at"), APPEND_ONLY,
        "the venue book as the paper path read it (append-only); observed_at "
        "is our receipt instant"),
    "external_valuations": Source(
        ("received_at", "decided_at"), APPEND_ONLY,
        "the Pinnacle valuation row; decided_at is the row's own valuation "
        "instant (its insert default), received_at our receipt of the quote"),
    "paper_orders": Source(
        ("decided_at", "created_at"), APPEND_ONLY,
        "the order as created; its terminal columns only once terminal_at "
        "<= the clock (fills come from paper_fills)",
        masked=(("terminal_at", ("state", "filled_qty", "terminal_reason",
                                 "terminal_at")),)),
    "paper_fills": Source(
        ("filled_at", "recorded_at"), APPEND_ONLY, "simulated fills"),
    "paper_evaluation_attempts": Source(
        ("at",), APPEND_ONLY, "evaluation attempts (append-only)"),
    "execution_intents": Source(
        ("created_at", "updated_at"), REWRITTEN,
        "the ACTUAL lane's intent; its timeline is appended in place, so the "
        "row is visible only when its last write is <= the clock"),
    "canonical_decision_intents": Source(
        ("created_at", "recorded_at"), APPEND_ONLY,
        "the R30 canonical decision intent (append-only, migration 225)"),
    "canonical_intent_executions": Source(
        ("created_at",), APPEND_ONLY,
        "the adapters' records of a canonical intent (append-only)"),
    "us_premap": Source(
        ("updated_at",), REWRITTEN,
        "the venue catalogue row (upserted); visible only if its last write "
        "is <= the clock"),
}

_BAD = re.compile(r"(;|--|/\*|\b(select|from|join|union|with|into|returning)\b)",
                  re.IGNORECASE)
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def epoch(v):
    """A stamp as epoch seconds (float), or None."""
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    if isinstance(v, _dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def source(table: str) -> Source:
    src = REGISTRY.get(table)
    if src is None:
        raise LookaheadViolation("UNREGISTERED_TABLE:%s" % table)
    return src


def known_at(table: str, row: dict):
    """The instant `row` became known: the latest of its stamps, or None
    when any stamp is missing (never visible)."""
    src = source(table)
    vals = [epoch((row or {}).get(s)) for s in src.stamps]
    if any(v is None for v in vals):
        return None
    return max(vals)


def _mask(src: Source, row: dict, clock: float) -> dict:
    out = dict(row)
    hidden = []
    for stamp, cols in src.masked:
        st = epoch(row.get(stamp))
        if st is None or st > clock + EPS_S:
            for c in cols:
                if c in out:
                    out[c] = None
                    hidden.append(c)
    if hidden:
        out["_masked_at_clock"] = sorted(set(hidden))
    return out


def visible(table: str, rows, clock: float) -> list:
    """THE PURE FILTER: the rows of `table` known at `clock`, with columns
    written after the clock masked. Every lab computation calls this (the
    DB reader through `read`, the extraction replay directly)."""
    src = source(table)
    c = float(clock)
    out = []
    for r in rows or []:
        k = known_at(table, r)
        if k is None or k > c + EPS_S:
            continue
        out.append(_mask(src, r, c))
    return out


def post_check(table: str, rows, clock: float) -> None:
    """DEFENCE IN DEPTH: raise if any row is known after the clock (or has
    no known instant). Called on every row a query returned."""
    for r in rows or []:
        k = known_at(table, r)
        if k is None:
            raise LookaheadViolation(
                "ROW_WITHOUT_A_KNOWN_INSTANT:%s" % table)
        if k > float(clock) + EPS_S:
            raise LookaheadViolation(
                "ROW_KNOWN_AFTER_THE_CLOCK:%s known_at=%.6f clock=%.6f"
                % (table, k, float(clock)))


def _plain(v):
    if isinstance(v, _dt.datetime):
        return epoch(v)
    if isinstance(v, Decimal):
        return float(v)
    return v


def _check_fragment(src: Source, frag: str, what: str) -> None:
    if not frag:
        return
    if _BAD.search(frag):
        raise LookaheadViolation("FRAGMENT_REFUSED:%s" % what)
    for _, cols in src.masked:
        for c in cols:
            if re.search(r"\b%s\b" % re.escape(c), frag):
                raise LookaheadViolation(
                    "FILTER_ON_A_COLUMN_WRITTEN_AFTER_ITS_ROW:%s" % c)


async def read(conn, table: str, *, clock: float, columns, where: str = "TRUE",
               args=(), order: str | None = None,
               limit: int = MAX_LIMIT) -> list:
    """THE ONE DATABASE READ of the lab: SELECT `columns` FROM `table` WHERE
    (`where`) AND every stamp <= `clock`, bounded by `limit`; rows as dicts
    with stamps in epoch seconds; masked and post-checked. `where`/`order`
    are single-table fragments ($1.. refer to `args`; the clock is appended
    as the last parameter)."""
    src = source(table)
    cols = [c for c in columns]
    for c in cols:
        if not _IDENT.match(c):
            raise LookaheadViolation("COLUMN_NAME_REFUSED:%s" % c)
    for s in src.stamps:
        if s not in cols:
            cols.append(s)
    for stamp, _ in src.masked:
        if stamp not in cols:
            cols.append(stamp)
    _check_fragment(src, where, "where")
    _check_fragment(src, order or "", "order")
    n = min(int(limit), MAX_LIMIT)
    k = len(args) + 1
    clock_filter = " AND ".join(
        "%s IS NOT NULL AND %s <= to_timestamp($%d)" % (s, s, k)
        for s in src.stamps)
    sql = "SELECT %s FROM %s WHERE (%s) AND %s%s LIMIT %d" % (
        ", ".join(cols), table, where or "TRUE", clock_filter,
        (" ORDER BY " + order) if order else "", n)
    rows = [{k2: _plain(v) for k2, v in dict(r).items()}
            for r in await conn.fetch(sql, *args, float(clock))]
    post_check(table, rows, clock)
    return visible(table, rows, clock)


def describe() -> dict:
    return {"rule": "a row is visible at clock C only when the latest of its "
                    "own recorded stamps is <= C; a missing stamp is never "
                    "visible; columns written after their row are masked "
                    "until their own stamp; rewritten rows only after their "
                    "last write",
            "tables": {t: {"stamps": list(s.stamps), "kind": s.kind,
                           "basis": s.basis,
                           "masked": [{"stamp": m[0], "columns": list(m[1])}
                                      for m in s.masked]}
                       for t, s in REGISTRY.items()}}
