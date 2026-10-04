"""THE ONE CHOKE-POINT READER OF THE HISTORICAL REPLAY (anti-hindsight).

THE RULE (specs/HISTORICAL_REPLAY.md): every row used by a replayed decision
must satisfy recorded_at <= replay_clock, unless it is a versioned policy
explicitly effective at that clock. This module is the ONLY place the replay
reads the database (tests/test_r30_replay.py pins that no other replay module
holds a query or calls the connection), and it enforces the rule five ways:

  1 THE REGISTRY. A table is readable only if it is registered here with the
    column that says WHEN ITS ROW BECAME DURABLE (paper_fills.recorded_at,
    paper_orders.created_at, paper_ledger.committed_at, ...) and its kind:
      APPEND_ONLY_EVENT         the row never changes after it is written
      VERSIONED_POLICY          a configuration effective from its stamp
                                (paper_sessions.config from started_at)
      ROW_REWRITTEN_IN_PLACE    the row is upserted (us_premap, whose upsert
                                sets updated_at = now() on every write):
                                visible only if its LAST write is <= the
                                clock; a row rewritten after the clock is
                                invisible, so its earlier content is
                                UNAVAILABLE, never guessed
  2 THE FILTER. Every read is `... AND <recorded> <= to_timestamp(clock)`,
    single-table. The fragments a caller passes are TOKENIZED, not
    pattern-matched: only plain columns, $n parameters, numbers, the
    comparison operators, AND / OR / NOT / IS / NULL / TRUE / FALSE, the
    functions to_timestamp and ANY, and `::type` casts are accepted. A
    quote, a comment, ';', any other function, and every SQL keyword that
    can open a subquery or another table (SELECT, TABLE, VALUES, EXISTS,
    IN, LATERAL, JOIN, UNION, WITH, ...) is refused. ORDER BY accepts only
    `column [ASC|DESC]` lists (a positional `ORDER BY 2` is refused: it
    could order on a masked column and leak the future through LIMIT).
  3 THE COLUMNS THAT CHANGE LATER. A column written after its row is
    REVEALED only when its stamp is <= the clock AND, where the stamp is an
    application-supplied time rather than a write stamp, when the row's
    durable last-write stamp confirms it (paper_orders: terminal_at <= the
    clock AND updated_at -- set to now() by every UPDATE, the terminal one
    being the last -- <= the clock; red-team finding: terminal_at is the
    simulator's logical time, not the commit). Otherwise it reads None and
    the row says which columns were masked. A column mutated in place with
    no durable stamp is HIDDEN (never selectable): paper_orders.updated_at,
    queue_ahead_qty and queue_basis (the simulator rewrites the queue on
    every step, bettor_paper_simulator.py:514); karen_challenges' state and
    every response / resolution / false-block / improvement column (their
    stamps are to_timestamp(at) from the caller, karen.py:511-705 -- the
    replay rebuilds a challenge's state at the clock from the append-only
    karen_challenge_events, whose recorded_at is the database's now());
    the valuation table's outcome columns (outcome_at / settlement_read_at
    are the venue settlement time passed in, ext_pinnacle_loop.py:3577 --
    a join written hours later would otherwise be revealed at the
    settlement time; the replay reads settlements from paper_settlements).
    A caller may not filter or order on a hidden or revealed column.
  4 THE POST-CHECK. Every returned row's recorded stamp is checked again in
    Python: a row with recorded_at > clock raises HindsightViolation (defence
    in depth; it cannot happen through the filter, and a test proves the
    check fires when a row is forced through).
  5 COMPLETE OR UNAVAILABLE. A snapshot is read in pages in recorded order
    up to a hard row bound; when the bound stops it, it knows the stamp up
    to which it is complete, and asking it about a later clock raises
    SnapshotIncomplete -- the caller then reports that component
    UNAVAILABLE (e.g. TAPE_TRUNCATED), never a measured empty set. A paged
    per-decision read (Xavier's reviews) likewise reports `truncated`.

Every read is bounded and never beyond the run's horizon (the run's
clock_end): an outcome is read at the horizon, a decision input at its
decision clock. Each read is audited per SCOPE (e.g. "<decision_id>:DECISION"):
tables, row counts, the latest recorded stamp used and the clock it was read
at; the store persists that audit and the database CHECKs
evidence_max_recorded_at <= decision_clock (migration 237).

Timestamps come back as epoch seconds (float); Decimals as float. Read only:
this module issues SELECTs and nothing else.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

EVENT = "APPEND_ONLY_EVENT"
POLICY = "VERSIONED_POLICY_EFFECTIVE_AT"
ROW = "ROW_REWRITTEN_IN_PLACE"
MAX_LIMIT = 5000
#: the hard bound of one paged snapshot / paged read (rows)
MAX_SNAPSHOT_ROWS = 60000
EPS_S = 1e-6
#: the keyset overlap of the pager: a page restarts 1 ms before the last
#: stamp it read (epoch floats round to ~0.2 us) and duplicates are dropped
#: by the row's id
PAGE_SLACK_S = 1e-3
#: completeness margin: a truncated snapshot is complete strictly before
#: the last stamp it read (rows AT that stamp may be partly read)
COMPLETE_MARGIN_S = 1e-5


class HindsightViolation(AssertionError):
    """A row or a query that would let the replay see past its clock."""


class SnapshotIncomplete(Exception):
    """The bounded snapshot does not cover the asked clock (its row bound
    stopped it earlier). Not a hindsight violation: the component that
    needed it is UNAVAILABLE with this reason."""


def _order_state(row: dict, clock: float) -> dict:
    return {"state_at_clock": (row.get("state") if row.get("terminal_at")
                               is not None else "OPEN_AT_THE_CLOCK")}


@dataclass(frozen=True)
class Table:
    recorded: str
    kind: str
    basis: str
    #: (stamp column, columns revealed only when stamp <= clock[, durable
    #: confirm column that must ALSO be <= clock])
    temporal: tuple = ()
    #: columns mutated with no durable stamp: never selectable
    hidden: tuple = ()
    derive: object = None

    def revealed(self) -> set:
        return {c for t in self.temporal for c in t[1]}

    def confirms(self) -> list:
        return [t[2] for t in self.temporal if len(t) > 2 and t[2]]


_KAREN_LATE = (
    "state", "updated_at", "downstream_impact", "responded_at",
    "response_stance", "response", "responded_by", "response_evidence_refs",
    "resolved_at", "outcome", "outcome_reason", "resolved_by",
    "resolution_evidence_refs", "false_block", "false_block_assessed_by",
    "false_block_assessed_at", "false_block_evidence_refs",
    "improvement_linked_at", "improvement_linked_by",
    "improvement_finding_id", "improvement_proposal_id", "blocked")

REGISTRY: dict = {
    "paper_decisions": Table(
        "recorded_at", EVENT, "the decision record (append-only)"),
    "paper_book_observations": Table(
        "recorded_at", EVENT, "the venue book as read (append-only)"),
    "paper_orders": Table(
        "created_at", EVENT,
        "the order as created; its terminal state only once terminal_at <= "
        "the clock AND its last durable write (updated_at) <= the clock "
        "(fills come from paper_fills)",
        temporal=(("terminal_at", ("state", "filled_qty", "terminal_reason",
                                   "terminal_at", "reserved_remaining_usd"),
                   "updated_at"),),
        hidden=("updated_at", "queue_ahead_qty", "queue_basis"),
        derive=_order_state),
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
        hidden=("status", "stopped_at", "stopped_why")),
    "external_valuations": Table(
        "decided_at", EVENT,
        "the provider observation, BETTOR receipt, mapping and valuation as "
        "written (decided_at is the insert stamp); the outcome columns are "
        "hidden (their stamps are the venue settlement time, not the write)",
        hidden=("outcome_known", "outcome", "outcome_at", "realised_net_usd",
                "outcome_basis", "outcome_side_map", "settlement_read",
                "settlement_read_at", "outcome_audit")),
    "karen_challenges": Table(
        "created_at", EVENT,
        "Karen's challenge as raised; every later column is hidden (its stamp "
        "is caller-supplied) and the state at the clock is rebuilt from "
        "karen_challenge_events",
        hidden=_KAREN_LATE),
    "karen_challenge_events": Table(
        "recorded_at", EVENT,
        "Karen's challenge lifecycle (append-only; recorded_at = now())"),
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
        "the venue pre-map (upserted in place with updated_at = now(): a "
        "row rewritten after the clock is invisible)"),
    "paper_sleeve_classifications": Table(
        "classified_at", EVENT, "durable sleeve classifications"),
}

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_TOKEN = re.compile(r"\s+|\$\d+|\d+(?:\.\d+)?|::|<=|>=|<>|!=|=|<|>|\(|\)|,|"
                    r"\[|\]|[A-Za-z_][A-Za-z0-9_]*")
_KEYWORDS_OK = {"and", "or", "not", "is", "null", "true", "false"}
_FUNCS_OK = {"to_timestamp", "any"}
_TYPES_OK = {"text", "bigint", "int", "integer", "numeric", "boolean",
             "timestamptz", "float8"}
#: every keyword that could open a subquery, another table or a computed
#: expression -- refused outright (defence in depth beside the allowlist)
_DENY = {"select", "from", "join", "union", "with", "insert", "update",
         "delete", "into", "returning", "table", "values", "lateral",
         "exists", "in", "except", "intersect", "case", "when", "then",
         "else", "end", "cast", "array", "row", "over", "window", "limit",
         "offset", "order", "by", "group", "having", "distinct", "all",
         "some", "using", "on", "as", "like", "ilike", "similar", "between",
         "collate", "escape", "only", "natural", "cross", "inner", "outer",
         "left", "right", "full", "filter", "within", "do", "execute",
         "copy", "create", "drop", "alter", "grant", "call"}
_ORDER = re.compile(r"^\s*[a-z_][a-z0-9_]*(\s+(asc|desc))?"
                    r"(\s*,\s*[a-z_][a-z0-9_]*(\s+(asc|desc))?)*\s*$", re.I)


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


def _confirm_key(c: str) -> str:
    return "__confirm_" + c


def _visible(spec: Table, raw: dict, clock: float, cols: list,
             stamps: list):
    """The row AS SEEN AT `clock` (masked copy), or None when it was
    recorded after the clock. The raw row is never modified."""
    rec = raw["__recorded"]
    if rec is None or rec > clock + EPS_S:
        return None
    d = {k: v for k, v in raw.items() if k != "__recorded"
         and not k.startswith("__confirm_")}
    masked = []
    for t in spec.temporal:
        stamp, revealed = t[0], t[1]
        confirm = t[2] if len(t) > 2 else None
        st = d.get(stamp)
        ok = st is not None and st <= clock + EPS_S
        if ok and confirm:
            cv = raw.get(_confirm_key(confirm))
            ok = cv is not None and cv <= clock + EPS_S
        if not ok:
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
    """ONE table read ONCE up to `until` (<= the run horizon), in pages in
    recorded order, so a run does not re-read a large table per decision.
    `at(clock)` is the choke point's filter applied in memory: only rows
    recorded <= clock, every revealed column masked AT THAT CLOCK. When the
    row bound stopped the read, `complete_until` is the stamp up to which it
    is complete and a later clock raises SnapshotIncomplete."""

    def __init__(self, reader, table, spec, raw, cols, stamps, until,
                 truncated, complete_until):
        self._reader, self._table, self._spec = reader, table, spec
        self._raw, self._cols, self._stamps = raw, cols, stamps
        self._recs = [r["__recorded"] for r in raw]
        self.until = until
        self.truncated = truncated
        self.complete_until = complete_until
        self.table = table

    def __len__(self) -> int:
        return len(self._raw)

    def covers(self, clock: float) -> bool:
        return float(clock) <= self.complete_until + EPS_S

    def at(self, clock: float, scope: str | None = None,
           since: tuple | None = None) -> list:
        """Rows visible at `clock`; `since=(column, lo)` keeps only rows
        whose (never-masked) column is >= lo (a window pre-filter)."""
        clock = float(clock)
        if clock > self.until + EPS_S:
            raise HindsightViolation(
                "snapshot of %s read until %.3f cannot answer clock %.3f"
                % (self._table, self.until, clock))
        if not self.covers(clock):
            raise SnapshotIncomplete(
                "%s_SNAPSHOT_TRUNCATED: %d rows read (the bound), complete "
                "only up to %.3f, asked %.3f" % (self._table.upper(),
                                                 len(self._raw),
                                                 self.complete_until, clock))
        if since is not None:
            col = since[0]
            if col in self._spec.hidden or col in self._spec.revealed():
                raise HindsightViolation("window on a column the clock may "
                                         "not see: %s" % col)
        hi = bisect.bisect_right(self._recs, clock + EPS_S)
        out = []
        for raw in self._raw[:hi]:
            if since is not None:
                v = raw.get(since[0])
                if v is None or v < since[1]:
                    continue
            d = _visible(self._spec, raw, clock, self._cols, self._stamps)
            if d is not None:
                out.append(d)
        self._reader._audit(scope, self._table, out, clock)
        return out

    def at_covered(self, scope: str | None = None) -> list:
        """Rows visible at the latest clock the snapshot covers."""
        return self.at(min(self.until, self.complete_until), scope=scope)


class Reader:
    """THE CHOKE POINT. `rows()` / `snapshot()` / `paged()` are the
    replay's only way to read."""

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

    @staticmethod
    def _forbidden_cols(spec: Table) -> set:
        return set(spec.hidden) | spec.revealed() | set(spec.confirms())

    def _check_where(self, spec: Table, frag: str) -> None:
        """TOKENIZE the filter: every token must be allowed (see the module
        docstring); any other character, keyword or function is refused."""
        if not frag:
            return
        toks, pos = [], 0
        while pos < len(frag):
            m = _TOKEN.match(frag, pos)
            if m is None:
                raise HindsightViolation(
                    "filter fragment refused at %r (only columns, $n, numbers,"
                    " comparisons, AND/OR/NOT/IS/NULL, to_timestamp, ANY and "
                    "::type are accepted): %r" % (frag[pos:pos + 12],
                                                  frag[:120]))
            if not m.group(0).isspace():
                toks.append(m.group(0))
            pos = m.end()
        bad = self._forbidden_cols(spec)
        for i, t in enumerate(toks):
            if not re.match(r"[A-Za-z_]", t):
                continue
            w = t.lower()
            if w in _DENY:
                raise HindsightViolation("filter keyword refused (single-"
                                         "table reads only): %s" % t)
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            prev = toks[i - 1] if i else None
            if prev == "::":
                if w not in _TYPES_OK:
                    raise HindsightViolation("cast to %s refused" % t)
                continue
            if w in _KEYWORDS_OK:
                continue
            if nxt == "(":
                if w not in _FUNCS_OK:
                    raise HindsightViolation("function %s refused" % t)
                continue
            if w in bad:
                raise HindsightViolation(
                    "filter on a column the clock may not see: %s" % w)

    def _check_order(self, spec: Table, frag: str) -> None:
        if not frag:
            return
        if not _ORDER.match(frag):
            raise HindsightViolation(
                "order refused (only `column [ASC|DESC], ...`; no position, "
                "no expression): %r" % frag[:120])
        names = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*",
                                               frag)} - {"asc", "desc"}
        bad = (self._forbidden_cols(spec) | _DENY) & names
        if bad:
            raise HindsightViolation(
                "order on a column the clock may not see: %s" % sorted(bad))

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
                                         "with no durable stamp: hidden"
                                         % (table, c))
        self._check_where(spec, where)
        self._check_order(spec, order)
        if not 1 <= int(limit) <= MAX_LIMIT:
            raise HindsightViolation("limit %s outside 1..%d" % (limit,
                                                                  MAX_LIMIT))
        stamps = [t[0] for t in spec.temporal]
        sel = list(dict.fromkeys(cols + stamps))
        sel += ["%s AS %s" % (c, _confirm_key(c)) for c in spec.confirms()]
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

    async def _pages(self, table, *, clock, cols, where, args, id_col,
                     max_rows, each):
        """THE PAGER: reads in recorded order, MAX_LIMIT rows a page, each
        page restarting PAGE_SLACK_S before the last stamp read (duplicates
        dropped by `id_col`), until exhausted or `max_rows`. Returns
        (truncated, complete_until)."""
        spec = REGISTRY.get(table)
        if spec is None:
            raise HindsightViolation("table %s is not registered for the "
                                     "replay" % table)
        ids = (id_col,) if isinstance(id_col, str) else tuple(id_col)
        for c in ids:
            if not _IDENT.match(c) or c in self._forbidden_cols(spec):
                raise HindsightViolation("page key %r refused" % c)
        cols = list(dict.fromkeys(list(cols) + list(ids)))

        def key(r):
            return tuple(r.get(c) for c in ids)
        seen: set = set()
        lo = None
        total, last_rec = 0, None
        while True:
            w, a = (where or "TRUE"), tuple(args)
            if lo is not None:
                a = a + (lo,)
                w = "(%s) AND %s >= to_timestamp($%d)" % (w, spec.recorded,
                                                          len(a))
            spec, raw, cols_, stamps, clock_ = await self._fetch(
                table, clock=clock, cols=cols, where=w, args=a,
                order=", ".join((spec.recorded,) + ids), limit=MAX_LIMIT)
            new = [r for r in raw if key(r) not in seen]
            for r in new:
                seen.add(key(r))
            if raw:
                last_rec = raw[-1]["__recorded"]
            room = max_rows - total
            truncated = False
            if len(new) > room:
                new, truncated = new[:room], True
                last_rec = new[-1]["__recorded"] if new else last_rec
            if new:
                each(spec, new, cols_, stamps)
                total += len(new)
            if truncated or (len(raw) >= MAX_LIMIT and total >= max_rows):
                return True, (last_rec or clock) - COMPLETE_MARGIN_S
            if len(raw) < MAX_LIMIT:
                return False, float(clock)
            if not new:
                # more than MAX_LIMIT rows inside one slack window: stop,
                # complete only before it
                return True, (last_rec or clock) - COMPLETE_MARGIN_S
            lo = last_rec - PAGE_SLACK_S

    async def snapshot(self, table: str, *, until: float, cols,
                       where: str = "", args=(), id_col,
                       max_rows: int = MAX_SNAPSHOT_ROWS) -> Snapshot:
        acc: list = []
        meta: dict = {}

        def each(spec, raw, cols_, stamps):
            acc.extend(raw)
            meta.update(spec=spec, cols=cols_, stamps=stamps)
        truncated, complete = await self._pages(
            table, clock=until, cols=cols, where=where, args=args,
            id_col=id_col, max_rows=max_rows, each=each)
        spec = meta.get("spec") or REGISTRY[table]
        ids = (id_col,) if isinstance(id_col, str) else tuple(id_col)
        cols_ = meta.get("cols") or list(dict.fromkeys(list(cols) + list(ids)))
        stamps = meta.get("stamps") or [t[0] for t in spec.temporal]
        acc.sort(key=lambda r: (r["__recorded"],
                                tuple(str(r.get(c)) for c in ids)))
        return Snapshot(self, table, spec, acc, cols_, stamps, float(until),
                        truncated, min(float(until), complete))

    async def paged(self, table: str, *, clock: float, cols, where: str = "",
                    args=(), id_col, max_rows: int = MAX_SNAPSHOT_ROWS,
                    scope: str | None = None, on_page=None) -> dict:
        """A bounded paged read AT ONE CLOCK: each page's visible rows go to
        `on_page` (so a caller can compact them); returns {rows, truncated}.
        """
        n = {"rows": 0}

        def each(spec, raw, cols_, stamps):
            vis = [_visible(spec, d, float(clock), cols_, stamps)
                   for d in raw]
            vis = [d for d in vis if d is not None]
            self._audit(scope, table, vis, float(clock))
            n["rows"] += len(vis)
            if on_page is not None:
                on_page(vis)
        truncated, complete = await self._pages(
            table, clock=clock, cols=cols, where=where, args=args,
            id_col=id_col, max_rows=max_rows, each=each)
        return {"rows": n["rows"], "truncated": truncated,
                "complete_until": complete}

    async def has(self, table: str) -> bool:
        """Is the registered table present in this database? (catalogue
        read, not a row read)."""
        if table not in REGISTRY:
            raise HindsightViolation("table %s is not registered" % table)
        return bool(await self.conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", table))
