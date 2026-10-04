"""THE ONE CHOKE-POINT READER OF THE HISTORICAL REPLAY (anti-hindsight).

THE RULE (specs/HISTORICAL_REPLAY.md): every row used by a replayed decision
must satisfy recorded_at <= replay_clock, unless it is a versioned policy
explicitly effective at that clock. This module is the ONLY place the replay
reads the database (tests/test_r30_replay.py pins that no other replay module
holds a query or calls the connection), and it enforces the rule four ways:

  1 THE REGISTRY. A table is readable only if it is registered here with the
    column that says WHEN ITS ROW BECAME DURABLE (paper_fills.recorded_at,
    paper_orders.created_at, paper_ledger.committed_at, ...) and its kind:
      APPEND_ONLY_EVENT         the row never changes after it is written
      VERSIONED_POLICY          a configuration effective from its stamp
                                (paper_sessions.config from started_at)
      ROW_REWRITTEN_IN_PLACE    the row is upserted (us_premap): visible only
                                if its LAST write is <= the clock; a row
                                rewritten after the clock is invisible, so
                                its earlier content is UNAVAILABLE, never
                                guessed
  2 THE FILTER. Every read is `... AND <recorded> <= to_timestamp(clock)`,
    single-table: no join, subquery or union can carry a row of another table
    past the filter (the fragments the callers pass are refused if they hold
    SELECT / FROM / JOIN / UNION / WITH / ';' / comments).
  3 THE COLUMNS THAT CHANGE LATER. A column written after its row (an
    order's terminal state, a valuation's outcome, a Karen challenge's
    response and resolution) is REVEALED only when its own stamp is <= the
    clock, else it reads None (and the row says which columns were masked);
    a column mutated with no stamp is HIDDEN (never selectable). A caller may
    not filter or order on a hidden or not-yet-revealed column -- that would
    leak the future through which rows come back.
  4 THE POST-CHECK. Every returned row's recorded stamp is checked again in
    Python: a row with recorded_at > clock raises HindsightViolation (defence
    in depth; it cannot happen through the filter, and a test proves the
    check fires when a row is forced through).

Every read is bounded (LIMIT <= MAX_LIMIT) and never beyond the run's
horizon (the run's clock_end): an outcome is read at the horizon, a decision
input at its decision clock. Each read is audited per SCOPE (e.g.
"<decision_id>:DECISION"): tables, row counts, the latest recorded stamp
used and the clock it was read at; the store persists that audit and the
database CHECKs evidence_max_recorded_at <= decision_clock (migration 237).

Timestamps come back as epoch seconds (float); Decimals as float. Read only:
this module issues SELECTs and nothing else.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

EVENT = "APPEND_ONLY_EVENT"
POLICY = "VERSIONED_POLICY_EFFECTIVE_AT"
ROW = "ROW_REWRITTEN_IN_PLACE"
MAX_LIMIT = 5000
EPS_S = 1e-6


class HindsightViolation(AssertionError):
    """A row or a query that would let the replay see past its clock."""


def _karen_state(row: dict, clock: float) -> dict:
    """A Karen challenge's state AT THE CLOCK from its own stamps (the
    `state` column is the current state and is hidden)."""
    if row.get("resolved_at") is not None:      # revealed: resolved by clock
        st = str(row.get("outcome") or "RESOLVED")
    elif row.get("responded_at") is not None:   # revealed: responded
        st = "RESPONDED"
    else:
        st = "OPEN"
    return {"state_at_clock": st}


def _order_state(row: dict, clock: float) -> dict:
    return {"state_at_clock": (row.get("state") if row.get("terminal_at")
                               is not None else "OPEN_AT_THE_CLOCK")}


@dataclass(frozen=True)
class Table:
    recorded: str
    kind: str
    basis: str
    #: (stamp column, columns revealed only when stamp <= clock)
    temporal: tuple = ()
    #: columns mutated with no stamp: never selectable
    hidden: tuple = ()
    derive: object = None

    def revealed(self) -> set:
        return {c for _, cols in self.temporal for c in cols}


REGISTRY: dict = {
    "paper_decisions": Table(
        "recorded_at", EVENT, "the decision record (append-only)"),
    "paper_book_observations": Table(
        "recorded_at", EVENT, "the venue book as read (append-only)"),
    "paper_orders": Table(
        "created_at", EVENT,
        "the order as created; its terminal state only once terminal_at <= "
        "the clock (fills come from paper_fills)",
        temporal=(("terminal_at", ("state", "filled_qty", "terminal_reason",
                                   "terminal_at", "reserved_remaining_usd")),),
        hidden=("updated_at",), derive=_order_state),
    "paper_order_events": Table(
        "recorded_at", EVENT, "the order lifecycle (append-only)"),
    "paper_fills": Table("recorded_at", EVENT, "simulated fills"),
    "paper_settlements": Table(
        "recorded_at", EVENT, "settlements (versioned, append-only)"),
    "paper_ledger": Table(
        "committed_at", EVENT, "the paper cash ledger (append-only)"),
    "paper_xavier_reviews": Table(
        "recorded_at", EVENT, "Xavier's reviews (append-only)"),
    "paper_audrey_findings": Table(
        "recorded_at", EVENT, "findings (e.g. PAPER_RISK_REFUSED_THE_ORDER)",
        hidden=("improvement_task_id",)),
    "paper_sessions": Table(
        "started_at", POLICY,
        "the paper session's configuration, effective from its start",
        temporal=(("stopped_at", ("status", "stopped_at", "stopped_why")),)),
    "external_valuations": Table(
        "decided_at", EVENT,
        "the provider observation, BETTOR receipt, mapping and valuation as "
        "written (decided_at is the insert stamp); the outcome only once "
        "outcome_at / settlement_read_at <= the clock",
        temporal=(("outcome_at", ("outcome_known", "outcome", "outcome_at",
                                  "realised_net_usd")),
                  ("settlement_read_at", ("outcome_basis", "outcome_side_map",
                                          "settlement_read",
                                          "settlement_read_at"))),
        hidden=("outcome_audit",)),
    "karen_challenges": Table(
        "created_at", EVENT,
        "Karen's challenge as raised; the response and resolution only once "
        "their stamps are <= the clock",
        temporal=(("responded_at", ("responded_at", "response_stance",
                                    "response", "responded_by",
                                    "response_evidence_refs")),
                  ("resolved_at", ("resolved_at", "outcome", "outcome_reason",
                                   "resolved_by")),
                  ("false_block_assessed_at", (
                      "false_block", "false_block_assessed_by",
                      "false_block_assessed_at", "false_block_evidence_refs")),
                  ("improvement_linked_at", (
                      "improvement_linked_at", "improvement_linked_by",
                      "improvement_finding_id", "improvement_proposal_id"))),
        hidden=("state", "updated_at", "downstream_impact"),
        derive=_karen_state),
    "eddie_execution_estimates": Table(
        "created_at", EVENT, "Eddie's recorded estimates (append-only)"),
    "canonical_decision_intents": Table(
        "recorded_at", EVENT, "R30 canonical decision intents (append-only)"),
    "canonical_management_intents": Table(
        "recorded_at", EVENT,
        "R30 canonical management intents (append-only)"),
    "xavier_entry_theses": Table(
        "recorded_at", EVENT,
        "Xavier's entry thesis (event start recorded at entry)"),
    "pos_snapshots": Table(
        "computed_at", EVENT, "profitability snapshots (append-only)"),
    "us_premap": Table(
        "updated_at", ROW,
        "the venue pre-map (upserted in place: a row rewritten after the "
        "clock is invisible)"),
    "paper_sleeve_classifications": Table(
        "classified_at", EVENT, "durable sleeve classifications"),
}

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FORBIDDEN = re.compile(r"(?i)\b(select|from|join|union|with|insert|update|"
                        r"delete|into|returning)\b|;|--|/\*")


def _epoch(v):
    if isinstance(v, _dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return v.timestamp()
    if isinstance(v, Decimal):
        return float(v)
    return v


@dataclass
class Scope:
    tables: dict = field(default_factory=dict)
    max_recorded_at: float | None = None
    max_clock: float | None = None
    masked: int = 0

    def as_dict(self) -> dict:
        return {"tables": dict(sorted(self.tables.items())),
                "max_recorded_at": self.max_recorded_at,
                "max_clock": self.max_clock, "masked_columns": self.masked}


def _visible(spec: Table, raw: dict, clock: float, cols: list,
             stamps: list):
    """The row AS SEEN AT `clock` (masked copy), or None when it was
    recorded after the clock. The raw row is never modified."""
    rec = raw["__recorded"]
    if rec is None or rec > clock + EPS_S:
        return None
    d = {k: v for k, v in raw.items() if k != "__recorded"}
    masked = []
    for stamp, revealed in spec.temporal:
        st = d.get(stamp)
        if st is None or st > clock + EPS_S:
            for c in revealed:
                if c in d:
                    if d[c] is not None:
                        masked.append(c)
                    d[c] = None
    if spec.derive is not None:
        d.update(spec.derive(d, clock))
    for s in stamps:
        if s not in cols:
            d.pop(s, None)
    d["__recorded_at"] = rec
    if masked:
        d["__masked"] = masked
    return d


class Snapshot:
    """ONE table read ONCE up to `until` (<= the run horizon) so a run does
    not re-read a large tape for every decision. `at(clock)` is the choke
    point's filter applied in memory: only rows recorded <= clock, every
    revealed column masked AT THAT CLOCK. The raw rows stay private here."""

    def __init__(self, reader, table, spec, raw, cols, stamps, until,
                 limit):
        self._reader, self._table, self._spec = reader, table, spec
        self._raw, self._cols, self._stamps = raw, cols, stamps
        self.until = until
        self.truncated = len(raw) >= limit

    def __len__(self) -> int:
        return len(self._raw)

    def at(self, clock: float, scope: str | None = None) -> list:
        clock = float(clock)
        if clock > self.until + EPS_S:
            raise HindsightViolation(
                "snapshot of %s read until %.3f cannot answer clock %.3f"
                % (self._table, self.until, clock))
        out = []
        for raw in self._raw:
            d = _visible(self._spec, raw, clock, self._cols, self._stamps)
            if d is not None:
                out.append(d)
        self._reader._audit(scope, self._table, out, clock)
        return out


class Reader:
    """THE CHOKE POINT. `rows()` / `snapshot()` are the replay's only way
    to read."""

    def __init__(self, conn, *, horizon: float):
        self.conn = conn
        self.horizon = float(horizon)
        self.scopes: dict = {}
        self.reads = 0

    def scope(self, name: str) -> dict:
        s = self.scopes.get(name)
        return Scope().as_dict() if s is None else s.as_dict()

    def _audit(self, scope, table, rows, clock) -> None:
        sc = self.scopes.setdefault(scope or "_", Scope())
        for d in rows:
            rec = d["__recorded_at"]
            sc.max_recorded_at = (rec if sc.max_recorded_at is None
                                  else max(sc.max_recorded_at, rec))
            sc.masked += len(d.get("__masked") or ())
        sc.tables[table] = sc.tables.get(table, 0) + len(rows)
        sc.max_clock = clock if sc.max_clock is None else max(sc.max_clock,
                                                              clock)

    def _check_fragment(self, spec: Table, frag: str, what: str) -> None:
        if not frag:
            return
        if _FORBIDDEN.search(frag):
            raise HindsightViolation(
                "%s fragment refused (single-table reads only): %r"
                % (what, frag[:120]))
        bad = (set(spec.hidden) | spec.revealed()) & set(_WORD.findall(frag))
        if bad:
            raise HindsightViolation(
                "%s on a column the clock may not see: %s"
                % (what, sorted(bad)))

    async def _fetch(self, table, *, clock, cols, where, args, order, limit):
        spec = REGISTRY.get(table)
        if spec is None:
            raise HindsightViolation("table %s is not registered for the "
                                     "replay" % table)
        clock = float(clock)
        if clock > self.horizon + EPS_S:
            raise HindsightViolation("clock %.3f is beyond the run horizon "
                                     "%.3f" % (clock, self.horizon))
        cols = list(cols)
        for c in cols:
            if not _IDENT.match(c):
                raise HindsightViolation("column %r is not a plain column"
                                         % c)
            if c in spec.hidden:
                raise HindsightViolation("column %s.%s is mutated in place "
                                         "with no stamp: hidden" % (table, c))
        self._check_fragment(spec, where, "filter")
        self._check_fragment(spec, order, "order")
        if not 1 <= int(limit) <= MAX_LIMIT:
            raise HindsightViolation("limit %s outside 1..%d" % (limit,
                                                                  MAX_LIMIT))
        stamps = [s for s, _ in spec.temporal]
        sel = list(dict.fromkeys(cols + stamps))
        n = len(args) + 1
        sql = ("SELECT %s, %s AS __recorded FROM %s WHERE (%s) AND %s <= "
               "to_timestamp($%d)%s LIMIT %d" % (
                   ", ".join(sel), spec.recorded, table, where or "TRUE",
                   spec.recorded, n,
                   (" ORDER BY " + order) if order else "", int(limit)))
        got = await self.conn.fetch(sql, *args, clock)
        self.reads += 1
        raw = []
        for r in got:
            d = {k: _epoch(v) for k, v in dict(r).items()}
            # THE POST-CHECK: the filter cannot return a later row; if one
            # is forced through anyway, the replay stops here
            if d.get("__recorded") is None or d["__recorded"] > clock + EPS_S:
                raise HindsightViolation(
                    "%s row recorded at %s is past the clock %.3f"
                    % (table, d.get("__recorded"), clock))
            raw.append(d)
        return spec, raw, cols, stamps, clock

    async def rows(self, table: str, *, clock: float, cols, where: str = "",
                   args=(), order: str = "", limit: int = 500,
                   scope: str | None = None) -> list:
        spec, raw, cols, stamps, clock = await self._fetch(
            table, clock=clock, cols=cols, where=where, args=args,
            order=order, limit=limit)
        out = [_visible(spec, d, clock, cols, stamps) for d in raw]
        self._audit(scope, table, out, clock)
        return out

    async def snapshot(self, table: str, *, until: float, cols,
                       where: str = "", args=(), order: str = "",
                       limit: int = MAX_LIMIT) -> Snapshot:
        spec, raw, cols, stamps, until = await self._fetch(
            table, clock=until, cols=cols, where=where, args=args,
            order=order, limit=limit)
        return Snapshot(self, table, spec, raw, cols, stamps, until, limit)

    async def has(self, table: str) -> bool:
        """Is the registered table present in this database? (catalogue
        read, not a row read)."""
        if table not in REGISTRY:
            raise HindsightViolation("table %s is not registered" % table)
        return bool(await self.conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", table))
