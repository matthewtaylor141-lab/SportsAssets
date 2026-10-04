"""THE ONE POINT-IN-TIME ACCESSOR OF THE CONTROL PLANE (leakage guard).

THE RULE (owner, section 6): "All learning data must be event-safe and
leakage-safe. Do not train on future information accidentally exposed
through joins or mutable tables." Every OFFLINE read the control plane makes
(snapshot freezing, post-trade horizons, the paper replay's tapes) goes
through this module, and it enforces the rule five ways:

  1 THE REGISTRY. A table is readable only if it is registered here with the
    column that says WHEN ITS ROW BECAME KNOWABLE and its kind:
      APPEND_ONLY_EVENT      the row never changes after it is written (an
                             append-only trigger refuses UPDATE / DELETE);
                             knowable from its insert stamp
      VERSIONED_POLICY       a configuration effective from its stamp
                             (paper_sessions.config from started_at; the
                             config is frozen by its own trigger)
      DERIVED_AS_OF          a control-plane record whose content is a
                             function of rows recorded at or before its own
                             `as_of` column -- CHECKed by its migration
                             (cp_decision_snapshots.evidence_max_recorded_at
                             <= as_of; cp_post_trade_observations' evidence
                             recorded <= horizon_clock), so it is knowable at
                             that stamp even if it was written later
  2 THE FILTER. Every read is `... AND <recorded> <= to_timestamp(clock)` on
    ONE table. There is no free-form SQL: filters are (column, operator,
    value) triples over plain columns with bound parameters; ORDER BY is a
    list of (column, ASC|DESC). No join, no subquery, no expression, so no
    other table's (later) state can enter through the query text.
  3 THE COLUMNS THAT CHANGE LATER ARE NEVER SELECTABLE. A column rewritten
    in place with no durable stamp is HIDDEN: it cannot be selected,
    filtered or ordered on --
      paper_orders.state / filled_qty / reserved_remaining_usd /
        terminal_at / terminal_reason / updated_at / queue_ahead_qty /
        queue_basis (the simulator advances them on every step; fills come
        from the append-only paper_fills, terminal states from the
        append-only paper_order_events)
      external_valuations' outcome columns (outcome_known, outcome,
        outcome_at, realised_net_usd, outcome_basis, outcome_side_map,
        settlement_read, settlement_read_at, outcome_audit): joined by
        UPDATE hours later and stamped with the VENUE's settlement time,
        not the write (bettor_external_shadow.JOIN_OUTCOME,
        ext_pinnacle_loop.JOIN_RESOLVED_SQL) -- settlements are read from
        the append-only paper_settlements instead
      paper_sessions.status / stopped_at / stopped_why (rewritten on stop)
  4 THE POST-CHECK. Every returned row's knowable stamp (and, when asked,
    its observation instant) is checked again in Python; a row past the
    clock raises HindsightViolation (defence in depth -- the filter cannot
    return one; a test forces one through and the check fires).
  5 BOUNDED. Every read has a hard LIMIT (<= MAX_LIMIT); a paged read stops
    at its row bound and says so (`truncated`), so a caller reports the
    component UNAVAILABLE instead of treating a cut-off read as complete.

The replay harness reads its tapes ONCE (bounded, to the run's horizon) and
then applies the same rule in memory: `Tape.at(clock)` returns only rows
knowable at that clock, and a stage can never ask a tape about a clock past
its own (HindsightViolation).

This module issues SELECTs and nothing else. Timestamps in returned rows
are left as the driver gives them; `__recorded_at` (epoch seconds) is added.
RELATION TO THE R30C REPLAY READER (in flight on claude/r30c-replay,
replay/pit.py): the same rule over the same facts (the hidden columns above
are the same findings); when both land they are reconciled into ONE
registry, the other module delegating to it.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

EVENT = "APPEND_ONLY_EVENT"
POLICY = "VERSIONED_POLICY"
ASOF = "DERIVED_AS_OF"
MAX_LIMIT = 5000
#: the hard row bound of one paged read
MAX_PAGED_ROWS = 50000
EPS_S = 1e-6


class HindsightViolation(AssertionError):
    """A row, a column or a query that would let a reader see past its
    clock."""


@dataclass(frozen=True)
class Table:
    recorded: str
    kind: str
    basis: str
    hidden: tuple = ()
    #: the row's own observation instant (book observed_at, fill filled_at,
    #: settlement settled_at), checkable against the clock on request
    observed: str | None = None


_ORDER_MUTABLE = ("state", "filled_qty", "reserved_remaining_usd",
                  "terminal_at", "terminal_reason", "updated_at",
                  "queue_ahead_qty", "queue_basis")
_VALUATION_OUTCOME = ("outcome_known", "outcome", "outcome_at",
                      "realised_net_usd", "outcome_basis", "outcome_side_map",
                      "settlement_read", "settlement_read_at", "outcome_audit")

REGISTRY: dict = {
    "paper_decisions": Table(
        "recorded_at", EVENT, "the decision record (append-only trigger)",
        observed="decided_at"),
    "paper_book_observations": Table(
        "recorded_at", EVENT, "the venue book as read (append-only trigger)",
        observed="observed_at"),
    "paper_orders": Table(
        "created_at", EVENT,
        "the order AS CREATED (immutable creation fields); its mutable state "
        "is hidden -- fills come from paper_fills, terminal events from "
        "paper_order_events", hidden=_ORDER_MUTABLE),
    "paper_order_events": Table(
        "recorded_at", EVENT, "the order lifecycle (append-only trigger)",
        observed="at"),
    "paper_fills": Table(
        "recorded_at", EVENT, "simulated fills (append-only trigger)",
        observed="filled_at"),
    "paper_settlements": Table(
        "recorded_at", EVENT,
        "settlements, versioned, append-only (a correction is a new version)",
        observed="settled_at"),
    "paper_ledger": Table(
        "committed_at", EVENT,
        "the paper cash ledger (append-only; committed_at = clock_timestamp())"),
    "paper_sessions": Table(
        "started_at", POLICY,
        "the session configuration, frozen at its start (paper_session_frozen)",
        hidden=("status", "stopped_at", "stopped_why")),
    "external_valuations": Table(
        "decided_at", EVENT,
        "the provider observation, receipt, mapping and valuation as written "
        "(decided_at defaults to the insert instant); the outcome columns are "
        "HIDDEN: their stamps are the venue settlement time, not the write",
        hidden=_VALUATION_OUTCOME),
    "canonical_decision_intents": Table(
        "recorded_at", EVENT, "R30 canonical decision intents (append-only)",
        observed="created_at"),
    "canonical_intent_executions": Table(
        "created_at", EVENT, "R30 adapter records (append-only)"),
    "cp_decision_snapshots": Table(
        "as_of", ASOF,
        "a frozen snapshot is knowable at its as_of (its migration CHECKs "
        "every input recorded <= as_of)", observed="decided_at"),
    "cp_post_trade_observations": Table(
        "horizon_clock", ASOF,
        "an observation is knowable at its horizon clock (its migration "
        "CHECKs the evidence recorded and observed <= horizon_clock)",
        observed="observed_at"),
}

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_OPS = {"=", "<", "<=", ">", ">=", "<>", "IN", "IS NULL", "IS NOT NULL"}


def to_epoch(v):
    """Epoch seconds for a datetime / Decimal / number, else the value."""
    if isinstance(v, _dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return v.timestamp()
    if isinstance(v, Decimal):
        return float(v)
    return v


def spec(table: str) -> Table:
    s = REGISTRY.get(table)
    if s is None:
        raise HindsightViolation("table %s is not registered with the "
                                 "point-in-time accessor" % table)
    return s


def _check_col(s: Table, table: str, c: str, what: str) -> None:
    if not isinstance(c, str) or not _IDENT.match(c):
        raise HindsightViolation("%s %r is not a plain column" % (what, c))
    if c in s.hidden:
        raise HindsightViolation(
            "%s.%s is rewritten in place with no durable stamp: it is hidden "
            "(cannot be %s)" % (table, c, what))


@dataclass
class Audit:
    tables: dict = field(default_factory=dict)
    max_recorded_at: float | None = None
    max_clock: float | None = None

    def add(self, table: str, rows: list, clock: float) -> None:
        self.tables[table] = self.tables.get(table, 0) + len(rows)
        for r in rows:
            rec = r.get("__recorded_at")
            if rec is not None and (self.max_recorded_at is None
                                    or rec > self.max_recorded_at):
                self.max_recorded_at = rec
        self.max_clock = clock if self.max_clock is None else max(
            self.max_clock, clock)

    def as_dict(self) -> dict:
        return {"tables": dict(sorted(self.tables.items())),
                "max_recorded_at": self.max_recorded_at,
                "max_clock": self.max_clock}


class Reader:
    """THE CHOKE POINT. `rows()` / `one()` / `paged()` are the control
    plane's only way to read a source table offline. `horizon` bounds every
    clock a caller may ask about (a pass never reads past its own as-of)."""

    def __init__(self, conn, *, horizon: float):
        self.conn = conn
        self.horizon = float(horizon)
        self.audit = Audit()
        self.reads = 0

    def _sql(self, table: str, *, cols, where, order, limit: int,
             clock: float, observed_by_clock: bool):
        s = spec(table)
        clock = float(clock)
        if clock > self.horizon + EPS_S:
            raise HindsightViolation("clock %.6f is beyond the reader's "
                                     "horizon %.6f" % (clock, self.horizon))
        cols = list(dict.fromkeys(cols))
        if not cols:
            raise HindsightViolation("select at least one column")
        for c in cols:
            _check_col(s, table, c, "selected")
        if not 1 <= int(limit) <= MAX_LIMIT:
            raise HindsightViolation("limit %s outside 1..%d" % (limit,
                                                                  MAX_LIMIT))
        args, conds = [], []
        for w in where or ():
            if len(w) == 2:
                col, op, val = w[0], w[1], None
            else:
                col, op, val = w
            op = str(op).upper()
            if op not in _OPS:
                raise HindsightViolation("operator %r refused" % op)
            _check_col(s, table, col, "filtered on")
            if op in ("IS NULL", "IS NOT NULL"):
                conds.append("%s %s" % (col, op))
            elif op == "IN":
                args.append(list(val))
                conds.append("%s = ANY($%d)" % (col, len(args)))
            else:
                args.append(val)
                conds.append("%s %s $%d" % (col, op, len(args)))
        args.append(clock)
        conds.append("%s <= to_timestamp($%d)" % (s.recorded, len(args)))
        if observed_by_clock:
            if not s.observed:
                raise HindsightViolation("%s has no observation instant"
                                         % table)
            conds.append("%s <= to_timestamp($%d)" % (s.observed, len(args)))
        ords = []
        for o in order or ():
            col, d = (o, "ASC") if isinstance(o, str) else (o[0], o[1])
            _check_col(s, table, col, "ordered on")
            d = str(d).upper()
            if d not in ("ASC", "DESC"):
                raise HindsightViolation("order direction %r refused" % d)
            ords.append("%s %s" % (col, d))
        sel = cols + ["%s AS __recorded" % s.recorded]
        if observed_by_clock:
            sel.append("%s AS __observed" % s.observed)
        sql = "SELECT %s FROM %s WHERE %s%s LIMIT %d" % (
            ", ".join(sel), table, " AND ".join(conds),
            (" ORDER BY " + ", ".join(ords)) if ords else "", int(limit))
        return s, sql, args, clock

    def _post(self, table, got, clock, observed_by_clock) -> list:
        out = []
        for r in got:
            d = dict(r)
            rec = to_epoch(d.pop("__recorded", None))
            obs = to_epoch(d.pop("__observed", None))
            # THE POST-CHECK: the filter cannot return a later row; one forced
            # through stops the reader here
            if rec is None or rec > clock + EPS_S:
                raise HindsightViolation(
                    "%s row knowable at %s is past the clock %.6f"
                    % (table, rec, clock))
            if observed_by_clock and (obs is None or obs > clock + EPS_S):
                raise HindsightViolation(
                    "%s row observed at %s is past the clock %.6f"
                    % (table, obs, clock))
            d["__recorded_at"] = rec
            out.append(d)
        self.audit.add(table, out, clock)
        return out

    async def rows(self, table: str, *, clock: float, cols, where=(),
                   order=(), limit: int = 500,
                   observed_by_clock: bool = False) -> list:
        s, sql, args, clock = self._sql(
            table, cols=cols, where=where, order=order, limit=limit,
            clock=clock, observed_by_clock=observed_by_clock)
        got = await self.conn.fetch(sql, *args)
        self.reads += 1
        return self._post(table, got, clock, observed_by_clock)

    async def one(self, table: str, *, clock: float, cols, where,
                  order=(), observed_by_clock: bool = False):
        got = await self.rows(table, clock=clock, cols=cols, where=where,
                              order=order, limit=1,
                              observed_by_clock=observed_by_clock)
        return got[0] if got else None

    async def paged(self, table: str, *, clock: float, cols, where=(),
                    id_col: str, max_rows: int = MAX_PAGED_ROWS) -> dict:
        """A bounded read in knowable order (recorded stamp, then id), page
        by page: {"rows", "truncated"}. A truncated read is NOT complete and
        the caller says so."""
        s = spec(table)
        _check_col(s, table, id_col, "paged on")
        cols = list(dict.fromkeys(list(cols) + [id_col]))
        out: list = []
        seen: set = set()
        lo = None
        truncated = False
        while True:
            w = list(where or ())
            if lo is not None:
                # keyset with a 1 ms overlap (epoch floats round to ~0.2 us);
                # the overlap's duplicates are dropped by id
                w.append((s.recorded, ">=", _dt.datetime.fromtimestamp(
                    lo, tz=_dt.timezone.utc)))
            page = await self.rows(table, clock=clock, cols=cols, where=w,
                                   order=[(s.recorded, "ASC"),
                                          (id_col, "ASC")],
                                   limit=MAX_LIMIT)
            new = [r for r in page if r[id_col] not in seen]
            seen.update(r[id_col] for r in new)
            room = max_rows - len(out)
            if len(new) > room:
                out.extend(new[:room])
                truncated = True
                break
            out.extend(new)
            if len(page) < MAX_LIMIT:
                break
            if not new:
                # more than MAX_LIMIT rows inside one overlap window: stop and
                # say the read is incomplete rather than loop
                truncated = True
                break
            lo = page[-1]["__recorded_at"] - 1e-3
        return {"rows": out, "truncated": truncated}


# ═════════════════════════ in memory: the replay's tapes ═════════════════

class Tape:
    """ONE table read once (bounded) up to `until`, held in knowable order.
    `at(clock)` is the accessor's rule applied in memory: only rows knowable
    at `clock` (and, for `observed=True`, observed by it). Asking about a
    clock past `until` -- what the tape could not have seen -- or past the
    `ceiling` a stage is bound to raises HindsightViolation."""

    def __init__(self, table: str, rows: list, *, until: float,
                 truncated: bool = False, observed_key: str | None = None):
        self.table = table
        self.until = float(until)
        self.truncated = bool(truncated)
        self.observed_key = observed_key
        for r in rows:
            if r.get("__recorded_at") is None \
                    or r["__recorded_at"] > self.until + EPS_S:
                raise HindsightViolation(
                    "%s tape row knowable at %s is past the tape's horizon "
                    "%.6f" % (table, r.get("__recorded_at"), self.until))
        self._rows = sorted(rows, key=lambda r: (r["__recorded_at"],
                                                 _sort_key(r)))
        self._recs = [r["__recorded_at"] for r in self._rows]

    def __len__(self) -> int:
        return len(self._rows)

    def at(self, clock: float, *, ceiling: float | None = None,
           observed: bool = False, where=None) -> list:
        clock = float(clock)
        if ceiling is not None and clock > float(ceiling) + EPS_S:
            raise HindsightViolation(
                "a stage bound to clock %.6f asked the %s tape about %.6f"
                % (float(ceiling), self.table, clock))
        if clock > self.until + EPS_S:
            raise HindsightViolation(
                "the %s tape was read until %.6f and cannot answer %.6f"
                % (self.table, self.until, clock))
        hi = bisect.bisect_right(self._recs, clock + EPS_S)
        out = []
        for r in self._rows[:hi]:
            if observed:
                o = r.get(self.observed_key) if self.observed_key else None
                if o is None or float(o) > clock + EPS_S:
                    continue
            if where is not None and not where(r):
                continue
            out.append(r)
        return out


def _sort_key(r: dict) -> str:
    """A total, value-based tie-break for rows knowable at the same stamp
    (never the database's return order)."""
    return "|".join("%s=%s" % (k, r[k]) for k in sorted(r)
                    if not k.startswith("__"))
