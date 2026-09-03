"""MIRROR LIVE, PHASE P1, migration 047 (owner order 2026-09-02,
"maximum effort and certainty"). No database here: the file itself is
the contract, read as text and, where the parser is installed, as a
Postgres syntax tree.

What is pinned and why:

  * the two partial UNIQUE predicates, character for character. They
    are the reconciler's hard guarantees (one open book per market, one
    non-terminal order per book); a widened or narrowed state list
    would let a second order rest beside one whose state cannot be read
  * the state / kind / side / tif CHECK lists, so a worker cannot write
    a state no reader enumerates
  * that nothing in live_orders changes shape: no ALTER TABLE, and 045
    (the split asset claim and live_orders_status_check) byte-identical
    to its committed form, by a pinned sha256 rather than a comparison
    to HEAD that goes quiet the moment an edit is committed -- a mirror
    standing row is refused by 045's index like any other copy
    position, and nothing rebuilds it
  * that every column the P1 spec's ledger model (section 1) and
    reconciler tick (section 2) read or write on mirror_books and
    mirror_orders exists in the DDL, parsed the way
    test_mirror_shadow pins 046 against its INSERT
  * that migrate.py's sorted glob puts 047 directly after 046
"""
import hashlib
import pathlib
import re

import pytest

from sportsassets.scripts import migrate

MIG_DIR = pathlib.Path(migrate.MIGRATIONS_DIR)
SQL_047 = MIG_DIR.joinpath("047_mirror_live.sql")
SQL_045 = MIG_DIR.joinpath("045_add_legs.sql")


def _sql() -> str:
    return SQL_047.read_text()


def _flat(s: str) -> str:
    """One space between tokens, so a predicate can be matched as a
    string no matter how the file wraps it."""
    return " ".join(s.split())


def _statements(sql: str) -> list[str]:
    """The file's statements with comments stripped, each flattened."""
    body = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    return [_flat(s) for s in body.split(";") if s.strip()]


def _table_columns(sql: str, table: str) -> dict[str, str]:
    """Column name -> the rest of its definition, parsed from the CREATE
    TABLE body the way test_mirror_shadow parses 046. A line that
    declares several columns (`frozen_reason TEXT, frozen_at ...`) is
    split on the commas that separate them, never on the commas inside
    a CHECK list."""
    body = re.search(r"CREATE TABLE IF NOT EXISTS %s\s*\((.*?)\n\);" % table, sql, re.S).group(1)
    cols: dict[str, str] = {}
    pending = ""
    for ln in body.splitlines():
        t = ln.split("--", 1)[0].strip()
        if not t or t.upper().startswith(("CONSTRAINT", "UNIQUE", "PRIMARY")):
            continue
        # a CHECK that continues the previous column's definition
        if t.upper().startswith("CHECK"):
            pending += " " + t
            continue
        if pending:
            _add_defs(cols, pending)
        pending = t
    if pending:
        _add_defs(cols, pending)
    return cols


def _add_defs(cols: dict[str, str], line: str) -> None:
    depth = 0
    cur = ""
    parts = []
    for ch in line:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    for p in parts:
        p = p.strip().rstrip(",")
        if p:
            name, _, rest = p.partition(" ")
            cols[name] = rest.strip()


def _check_list(defn: str) -> list[str]:
    m = re.search(r"CHECK\s*\(\s*\w+\s+IN\s*\((.*?)\)\s*\)", defn, re.S)
    assert m, defn
    return [v.strip().strip("'") for v in m.group(1).split(",")]


# ---------------------------------------------------------------- shape

def test_047_exists_and_sorts_directly_after_046():
    assert SQL_047.exists()
    files = [p.name for p in sorted(MIG_DIR.glob("*.sql"))]
    # the ORDER is the contract (046's shadow tables before 047's live
    # ones); "047 is last" would be broken by the very next migration
    i = files.index("046_mirror_shadow.sql")
    assert files[i + 1] == "047_mirror_live.sql", files[i:i + 3]
    # migrate.py applies the sorted glob, one file per transaction; a
    # second file with the 047 prefix would race it for the version key
    assert sum(1 for f in files if f.startswith("047_")) == 1
    assert 'sorted(MIGRATIONS_DIR.glob("*.sql"))' in pathlib.Path(migrate.__file__).read_text()


def test_047_header_is_in_the_house_style():
    head = _sql().splitlines()[0]
    assert head.startswith("-- 047: MIRROR LIVE, PHASE P1 (owner order 2026-09-02")


def test_047_creates_only_and_never_alters_live_orders():
    sql = _sql()
    up = sql.upper()
    assert "ALTER TABLE LIVE_ORDERS" not in up
    assert "ALTER TABLE" not in up
    assert "DROP " not in up
    assert "live_orders_status_check" not in sql
    assert "live_orders_one_fill_per_asset" not in sql
    assert "live_orders_one_inflight_per_asset" not in sql
    stmts = _statements(sql)
    assert len(stmts) == 11, len(stmts)
    for s in stmts:
        assert s.startswith(("CREATE TABLE IF NOT EXISTS ", "CREATE UNIQUE INDEX IF NOT EXISTS ",
                             "CREATE INDEX IF NOT EXISTS ")), s
    # the only statement that names live_orders as its target is the
    # partial index for the mirror's own standing-row reads
    on_live = [s for s in stmts if " ON live_orders " in s]
    assert on_live == ["CREATE INDEX IF NOT EXISTS live_orders_mirror_idx ON live_orders "
                       "(us_market_slug) WHERE lane = 'mirror'"], on_live
    # and the one other mention is the FK from a book to its standing row
    assert [s for s in stmts if "REFERENCES live_orders (id)" in s] == [
        s for s in stmts if s.startswith("CREATE TABLE IF NOT EXISTS mirror_books")]


def test_the_two_partial_unique_predicates_are_exact():
    stmts = _statements(_sql())
    uniq = sorted(s for s in stmts if s.startswith("CREATE UNIQUE INDEX"))
    assert uniq == [
        "CREATE UNIQUE INDEX IF NOT EXISTS mirror_books_one_open_per_market "
        "ON mirror_books (whale, us_market_slug) WHERE state <> 'closed'",
        "CREATE UNIQUE INDEX IF NOT EXISTS mirror_orders_one_open_per_book "
        "ON mirror_orders (book_id) WHERE state IN ('placing', 'open', 'unknown')",
        "CREATE UNIQUE INDEX IF NOT EXISTS mirror_orders_order_id "
        "ON mirror_orders (order_id) WHERE order_id IS NOT NULL",
    ], uniq
    # the reconciler's live scan sees every non-terminal order AND the
    # lost ones (revisited every tick until the venue agrees), so its
    # predicate is the one-open list plus 'lost' and nothing else
    live = [s for s in stmts if "mirror_orders_live_idx" in s]
    assert live == ["CREATE INDEX IF NOT EXISTS mirror_orders_live_idx ON mirror_orders (state) "
                    "WHERE state IN ('placing','open','unknown','lost')"], live
    for name, where in (("mirror_books_assets_idx", "WHERE state <> 'closed'"),
                        ("mirror_books_game_idx", "WHERE state <> 'closed'")):
        s = [x for x in stmts if name in x]
        assert len(s) == 1 and s[0].endswith(where), s


def test_state_checks_name_every_state_the_indexes_and_the_spec_use():
    sql = _sql()
    books = _table_columns(sql, "mirror_books")
    orders = _table_columns(sql, "mirror_orders")
    assert _check_list(books["state"]) == ["live", "frozen", "closing", "closed"]
    assert "DEFAULT 'live'" in books["state"]
    assert _check_list(orders["state"]) == [
        "placing", "open", "filled", "cancelled", "expired", "rejected", "unknown", "lost"]
    assert "DEFAULT 'placing'" in orders["state"]
    assert _check_list(orders["kind"]) == [
        "increase", "reduce", "flatten_paired", "flatten_vanished", "take", "adjust"]
    assert _check_list(orders["side"]) == ["BUY_LONG", "SELL_LONG"]
    assert _check_list(orders["tif"]) == ["GTC", "GTD", "IOC", "CLOSE"]
    # a mirror order is never a live_orders status: the worker cannot
    # be tempted to write 'submitting' or 'exiting' onto one
    for banned in ("submitting", "exiting", "merged", "settled", "cashed_out", "error"):
        assert banned not in _check_list(orders["state"])
    # every state a partial index predicates on is a state the CHECK admits
    non_terminal = {"placing", "open", "unknown"}
    assert non_terminal < set(_check_list(orders["state"]))
    assert non_terminal | {"lost"} < set(_check_list(orders["state"]))
    assert "closed" in _check_list(books["state"])
    # the intent constant is BUY_LONG in P1 (the short gate stays P2's door)
    assert "DEFAULT 'ORDER_INTENT_BUY_LONG'" in books["intent"]


# ------------------------------------------------------ 045 untouched

# sha256 of backend/migrations/045_add_legs.sql as committed. The file
# was added in cc00e71 ("Adds: his fresh buy on a leg we hold is copied
# and merged, not refused") and has not changed since; the digest is
# identical at 1f9bb0a (mirror P0, the last commit before 047) and was
# computed with
#     git show 1f9bb0a:backend/migrations/045_add_legs.sql | sha256sum
# A comparison against HEAD passes the moment an edit is committed; a
# pinned digest keeps failing until somebody re-pins it on purpose.
SHA256_045_AT_1f9bb0a = "98a608d990aef5695bd9d0d384ff4d7cad91bda8c62eb7bcfdc6b5b5e755b0bd"


def test_045_is_byte_identical_to_its_committed_form():
    got = hashlib.sha256(SQL_045.read_bytes()).hexdigest()
    assert got == SHA256_045_AT_1f9bb0a, (
        "045_add_legs.sql differs from the bytes committed in 1f9bb0a "
        "(sha256 now %s); nothing rebuilds the asset claim" % got)


def test_045_claim_indexes_and_status_check_stand_as_committed():
    """Independent of git: the two claim indexes and the status CHECK
    that 045 declared, so the mirror's standing row is refused by
    live_orders_one_fill_per_asset like any other copy position and
    'filled' / 'cashed_out' / 'cancelled' -- the only statuses a book
    ever writes -- are already admitted."""
    sql = SQL_045.read_text()
    i = sql.index("CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_fill_per_asset")
    assert _flat(sql[i:sql.index(";", i)]) == (
        "CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_fill_per_asset ON live_orders (asset) "
        "WHERE status IN ('filled', 'settled') AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog')")
    j = sql.index("CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_inflight_per_asset")
    assert _flat(sql[j:sql.index(";", j)]) == (
        "CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_inflight_per_asset ON live_orders (asset) "
        "WHERE status = 'submitting' AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog')")
    k = sql.index("ALTER TABLE live_orders ADD CONSTRAINT live_orders_status_check")
    check = _flat(sql[k:sql.index(";", k)])
    statuses = re.search(r"CHECK \(status IN \((.*?)\)\)", check).group(1)
    assert [v.strip().strip("'") for v in statuses.split(",")] == [
        "submitting", "filled", "unfilled", "rejected", "error", "settled", "cashed_out",
        "exiting", "open", "cancelled", "merged"]
    # 047 leaves that constraint alone: a book's statuses are all in it
    assert "live_orders_status_check" not in _sql()
    for st in ("filled", "cashed_out", "cancelled", "settled"):
        assert "'%s'" % st in statuses


# ------------------------------------------------------- the columns

# Every column the P1 spec reads or writes on the two tables, by the
# section that names it. A column named here and missing from the DDL
# is a worker statement that would fail at runtime.
BOOK_COLUMNS_SPEC = {
    # section 1b/1c: the book open, the per-fill UPDATE and the close
    "id", "whale", "condition_id", "us_market_slug", "long_asset", "other_asset",
    "standing_row_id", "episode", "flat_reopens", "state", "last_reason",
    "ledger_net", "avg_cost", "gross_buy_usd", "gross_sell_usd", "peak_exposure_usd",
    "realized_pnl", "updated_at", "closed_at",
    # section 1e: the settle cross-check
    "settled_pnl", "own_book_pnl", "settle_disagree", "target",
    # section 2 (steps 0/G/B/P/X/E): the mode guard, the loss stop, the
    # tick's readings, the freeze, the take arm, the plan
    "game_key", "intent", "map_source", "ratio", "anchor_usd",
    "frozen_reason", "frozen_at", "frozen_ticks", "target_raw",
    "his_net", "his_long", "his_other", "snap_long", "snap_other", "drift", "his_level",
    "venue_net", "open_order_id", "take_armed_at", "last_plan", "opened_at",
}
ORDER_COLUMNS_SPEC = {
    # section 1c: the idempotency cursor written in the fill transaction
    "id", "booked_filled", "filled", "avg_px",
    # section 2 step O: the open-order walk, adoption, terminal states
    "book_id", "state", "order_id", "placed_at", "us_market_slug", "side", "price", "wire",
    "qty", "done_at", "maker", "taker_at_placement", "tif", "venue_state",
    # section 2 step G: the mirror day cap sums BUY cash
    "cash_usd", "realized",
    # section 2 step L: the 'placing' INSERT before the venue call
    "whale", "kind", "post_only", "good_till", "his_level", "pre_ids",
    "target_at_place", "ledger_at_place", "bid_at_place", "ask_at_place",
    # section 2 step X/E: the receipt persisted with the id, the named refusal
    "receipt", "reason", "updated_at",
}


def test_047_columns_cover_every_column_the_spec_reads_or_writes():
    sql = _sql()
    books = _table_columns(sql, "mirror_books")
    orders = _table_columns(sql, "mirror_orders")
    assert BOOK_COLUMNS_SPEC <= set(books), BOOK_COLUMNS_SPEC - set(books)
    assert ORDER_COLUMNS_SPEC <= set(orders), ORDER_COLUMNS_SPEC - set(orders)
    # and nothing in the DDL is unaccounted for: a column no section
    # names is a column the spec and the file disagree about
    assert set(books) == BOOK_COLUMNS_SPEC, set(books) ^ BOOK_COLUMNS_SPEC
    assert set(orders) == ORDER_COLUMNS_SPEC, set(orders) ^ ORDER_COLUMNS_SPEC


def test_047_not_null_columns_without_defaults_are_the_open_and_place_inserts():
    """The columns a worker MUST supply. The book open (section 2 step
    A) and the 'placing' INSERT (step L) name exactly these; anything
    else has a default, so a partial INSERT cannot fail on a column the
    spec never mentions."""
    sql = _sql()

    def required(cols):
        return {n for n, d in cols.items()
                if "NOT NULL" in d and "DEFAULT" not in d and "PRIMARY KEY" not in d}

    assert required(_table_columns(sql, "mirror_books")) == {
        "whale", "condition_id", "us_market_slug", "long_asset"}
    assert required(_table_columns(sql, "mirror_orders")) == {
        "book_id", "whale", "us_market_slug", "kind", "side", "tif", "price", "wire", "qty"}
    # the counters the fill transaction adds to start at zero, never NULL
    books = _table_columns(sql, "mirror_books")
    for c in ("ledger_net", "gross_buy_usd", "gross_sell_usd", "peak_exposure_usd",
              "realized_pnl", "frozen_ticks", "flat_reopens"):
        assert "NOT NULL DEFAULT 0" in books[c], (c, books[c])
    orders = _table_columns(sql, "mirror_orders")
    for c in ("filled", "booked_filled", "cash_usd", "realized"):
        assert "NOT NULL DEFAULT 0" in orders[c], (c, orders[c])
    assert "NOT NULL DEFAULT false" in orders["post_only"]
    assert "NOT NULL DEFAULT false" in orders["taker_at_placement"]


def test_047_keys_and_references():
    sql = _sql()
    books = _table_columns(sql, "mirror_books")
    orders = _table_columns(sql, "mirror_orders")
    assert books["id"] == "BIGSERIAL PRIMARY KEY"
    assert orders["id"] == "BIGSERIAL PRIMARY KEY"
    # a book points at its standing live_orders row (nullable: the row
    # is inserted in the same transaction and back-filled)
    assert books["standing_row_id"] == "BIGINT REFERENCES live_orders (id)"
    # an order belongs to exactly one book
    assert orders["book_id"] == "BIGINT NOT NULL REFERENCES mirror_books (id)"
    # the standing row's id lives on the book, never the reverse: the
    # ledger row carries only raw.mirror.book_id, so live_orders keeps
    # its shape
    assert "mirror_books" not in SQL_045.read_text()


# --------------------------------------------------- the parse (opt-in)

def test_047_parses_as_postgres_sql_and_the_tree_agrees_with_the_text():
    """A syntax-only check through the real Postgres grammar. pglast is
    not a project dependency; where it is absent the textual pins above
    still hold and this test is skipped, never silently passed."""
    pglast = pytest.importorskip("pglast")
    from pglast.stream import RawStream

    stmts = pglast.parse_sql(_sql())
    kinds = [type(s.stmt).__name__ for s in stmts]
    assert kinds.count("CreateStmt") == 2 and kinds.count("IndexStmt") == 9 and len(kinds) == 11, kinds
    creates = {s.stmt.relation.relname: s.stmt for s in stmts if type(s.stmt).__name__ == "CreateStmt"}
    assert set(creates) == {"mirror_books", "mirror_orders"}
    assert all(c.if_not_exists for c in creates.values())
    for name, cols in (("mirror_books", BOOK_COLUMNS_SPEC), ("mirror_orders", ORDER_COLUMNS_SPEC)):
        tree_cols = {e.colname for e in creates[name].tableElts if type(e).__name__ == "ColumnDef"}
        assert tree_cols == cols, tree_cols ^ cols
    idx = {s.stmt.idxname: s.stmt for s in stmts if type(s.stmt).__name__ == "IndexStmt"}
    assert all(i.if_not_exists for i in idx.values())

    def where(name):
        return RawStream()(idx[name].whereClause) if idx[name].whereClause is not None else None

    uniq = {n for n, i in idx.items() if i.unique}
    assert uniq == {"mirror_books_one_open_per_market", "mirror_orders_one_open_per_book",
                    "mirror_orders_order_id"}
    assert [p.name for p in idx["mirror_orders_one_open_per_book"].indexParams] == ["book_id"]
    assert where("mirror_orders_one_open_per_book") == "state IN ('placing', 'open', 'unknown')"
    assert [p.name for p in idx["mirror_books_one_open_per_market"].indexParams] == ["whale", "us_market_slug"]
    assert where("mirror_books_one_open_per_market") == "state <> 'closed'"
    assert where("mirror_orders_order_id") == "order_id IS NOT NULL"
    assert idx["live_orders_mirror_idx"].relation.relname == "live_orders"
    assert [p.name for p in idx["live_orders_mirror_idx"].indexParams] == ["us_market_slug"]
    assert where("live_orders_mirror_idx") == "lane = 'mirror'"
    assert not idx["live_orders_mirror_idx"].unique
    # no statement in the tree targets live_orders other than that index
    for s in stmts:
        n = s.stmt
        if type(n).__name__ == "IndexStmt" and n.idxname != "live_orders_mirror_idx":
            assert n.relation.relname != "live_orders"


# --------------------------------------------- the worker's refusal

def test_the_worker_refuses_by_name_when_047_is_unapplied():
    """Workers never run migrations: a boot with 047 unapplied must
    leave the reconciler refusing 'tables_absent', never crashing. The
    worker is its own build step; until it lands this pins nothing and
    says so."""
    ml = pytest.importorskip("sportsassets.workers.mirror_live")
    src = pathlib.Path(ml.__file__).read_text()
    assert "tables_absent" in src
    assert "mirror_books" in src


# ------------------------------------------------ 049 (to-a-tee Phase 4/8)
#
# 049 is the program register's number for trades.taker (program.md
# rule 3: 048 is reserved for Phase 0b's markets.rules_text /
# resolution_kind, 049 for the Phase 4/8 columns; the taker unit's
# three reviews carried the mismatch as a minor until this rename).
# It is an ALTER: 047 stays CREATE-only (the pin above), and 049 adds
# nullable measurement columns only — trades.taker for the taker
# census (Phase 8) and the three mirror_orders columns the per-fill
# fidelity join needs (Phase 4 (vii)). migrate.py applies the sorted
# glob, so the 048 gap is harmless until Phase 0b fills it. Every 047
# pin above is unchanged; these are additive. The register (program.md
# :429) says 049 will also carry Phase 4's commission / wire-tick /
# mirror_books columns, so the pins below are "contains these four,
# and every statement is an ALTER ... ADD COLUMN IF NOT EXISTS nullable
# with no DEFAULT" — never "exactly four" (the taker unit's fourth
# review, the minor): the file may grow, its shape may not.

SQL_049 = MIG_DIR.joinpath("049_mirror_fidelity.sql")


def _sql_049() -> str:
    return SQL_049.read_text()


def test_049_exists_and_sorts_after_047_with_only_the_reserved_048_between():
    assert SQL_049.exists()
    files = [p.name for p in sorted(MIG_DIR.glob("*.sql"))]
    i = files.index("047_mirror_live.sql")
    j = files.index("049_mirror_fidelity.sql")
    assert j > i
    # whatever sits between is Phase 0b's reserved 048 and nothing else
    assert all(f.startswith("048_") for f in files[i + 1:j]), files[i:j + 1]
    assert sum(1 for f in files if f.startswith("049_")) == 1
    # and no 048 file in the tree claims trades.taker
    for f in files[i + 1:j]:
        assert "taker" not in MIG_DIR.joinpath(f).read_text()
    assert not MIG_DIR.joinpath("048_mirror_fidelity.sql").exists()


def test_049_header_is_in_the_house_style():
    head = _sql_049().splitlines()[0]
    assert head.startswith("-- 049: MIRROR FIDELITY COLUMNS (owner order 2026-09-02")


def test_049_is_alter_add_column_if_not_exists_only():
    """The ALTER, never a CREATE: 047's tables keep their shape and
    every statement is re-runnable. Every column nullable, none with
    a DEFAULT — an unwritten row reads NULL ('unknown'), never a
    guess. The count is not pinned: Phase 4 grows this file."""
    sql = _sql_049()
    # judged on the statements, comments stripped: the header PROSE
    # names what the chain decoders drop and what a NULL means
    up = " ".join(_statements(sql)).upper()
    assert "CREATE " not in up and "DROP " not in up
    assert "DEFAULT" not in up and "NOT NULL" not in up
    stmts = _statements(sql)
    assert len(stmts) >= 4, stmts
    for s in stmts:
        assert re.fullmatch(
            r"ALTER TABLE \w+ ADD COLUMN IF NOT EXISTS \w+ .+ NULL( REFERENCES \w+ \(\w+\))?",
            s), s
    # never the 047 indexes, never a bare migration number
    assert "047" not in "".join(stmts) and "048" not in "".join(stmts)


PHASE_8_STATEMENTS = [
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS taker BOOLEAN NULL",
    "ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS trigger_trade_id BIGINT NULL "
    "REFERENCES trades (id)",
    "ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS his_fill_ts TIMESTAMPTZ NULL",
    "ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS first_fill_at TIMESTAMPTZ NULL",
]


def _is_subsequence(needles: list, hay: list) -> bool:
    it = iter(hay)
    return all(any(n == h for h in it) for n in needles)


def test_049_carries_the_four_phase_8_columns():
    """Contains these four, in this order, each exactly once; anything
    Phase 4 appends beside them is its own business (and the shape pin
    above still holds it to ALTER ... ADD COLUMN IF NOT EXISTS
    nullable)."""
    stmts = _statements(_sql_049())
    for s in PHASE_8_STATEMENTS:
        assert stmts.count(s) == 1, s
    assert _is_subsequence(PHASE_8_STATEMENTS, stmts)


def test_049_trigger_trade_id_is_the_trades_id_type():
    """trades.id is BIGSERIAL (001), so the FK column is BIGINT."""
    init = MIG_DIR.joinpath("001_init.sql").read_text()
    body = re.search(r"CREATE TABLE IF NOT EXISTS trades\s*\((.*?)\n\);", init, re.S).group(1)
    assert re.search(r"^\s*id\s+BIGSERIAL PRIMARY KEY", body, re.M)
    assert "trigger_trade_id BIGINT NULL REFERENCES trades (id)" in _flat(_sql_049())


def test_049_leaves_047_exactly_as_pinned():
    """Additive by construction: 047's statement list is the eleven
    CREATEs the pins above enumerate, and 049 names none of 047's
    indexes."""
    stmts_047 = _statements(_sql())
    assert len(stmts_047) == 11
    assert all(s.startswith("CREATE ") for s in stmts_047)
    for s in stmts_047:
        if s.startswith(("CREATE UNIQUE INDEX IF NOT EXISTS ", "CREATE INDEX IF NOT EXISTS ")):
            name = s.split("IF NOT EXISTS ", 1)[1].split(" ", 1)[0]
            assert name not in _sql_049(), name


def test_049_parses_as_postgres_sql_and_the_tree_agrees_with_the_text():
    pglast = pytest.importorskip("pglast")
    from pglast.enums import AlterTableType, ConstrType

    stmts = pglast.parse_sql(_sql_049())
    assert len(stmts) >= 4
    assert all(type(s.stmt).__name__ == "AlterTableStmt" for s in stmts)
    seen = []
    for s in stmts:
        n = s.stmt
        assert len(n.cmds) == 1
        cmd = n.cmds[0]
        assert cmd.subtype == AlterTableType.AT_AddColumn
        assert cmd.missing_ok is True, "IF NOT EXISTS"
        col = cmd.def_
        types = {c.contype for c in (col.constraints or [])}
        assert ConstrType.CONSTR_NULL in types
        assert ConstrType.CONSTR_NOTNULL not in types
        assert ConstrType.CONSTR_DEFAULT not in types
        seen.append((n.relation.relname, col.colname,
                     [x.sval for x in col.typeName.names][-1],
                     ConstrType.CONSTR_FOREIGN in types))
    # the four Phase 8 columns are in the tree, in order, once each;
    # the file may carry more (the register's Phase 4 columns)
    phase_8 = [
        ("trades", "taker", "bool", False),
        ("mirror_orders", "trigger_trade_id", "int8", True),
        ("mirror_orders", "his_fill_ts", "timestamptz", False),
        ("mirror_orders", "first_fill_at", "timestamptz", False),
    ]
    for col in phase_8:
        assert seen.count(col) == 1, col
    assert _is_subsequence(phase_8, seen)
    trig = seen.index(phase_8[1])
    fk = [c for c in stmts[trig].stmt.cmds[0].def_.constraints
          if c.contype == ConstrType.CONSTR_FOREIGN][0]
    assert fk.pktable.relname == "trades"
    assert [a.sval for a in fk.pk_attrs] == ["id"]
