"""IS THE FUNDED SCHEMA ACTUALLY PRESENT IN THIS DATABASE?

THE INCIDENT THIS EXISTS BECAUSE OF (2026-09-27). `backend/start.sh` runs the
migration runner and, on failure, logs `migrate failed -- serving anyway` and
starts the API regardless. That is the right call for availability: a service
that refuses to boot because one migration failed takes the diagnostics down
with it, and the diagnostics are how anybody finds out what failed.

It is the wrong call for EXECUTION. On 2026-09-27 migration 126 rolled back on
production's PostgreSQL 18 while every gate was green on 16, and the API served
a build whose funded code expected columns the database did not have. The funded
command-centre section reported `UndefinedColumnError: column "residual_qty"
does not exist` -- correct, and only because that section happens to catch its
own exceptions. Nothing STOPPED the funded lane: with the submission switches
flipped, an entry or an exit would have run its rails against a schema that
cannot record the result. A position held at a venue and not recorded here is
the worst state this system has.

SO AVAILABILITY AND CAPABILITY ARE SEPARATED. The API keeps serving; the funded
CAPABILITY is reported unavailable and every funded submission path refuses by
name until the schema is actually there. This module is the single place that
decides that, and it decides it by ASKING THE DATABASE -- not by reading a
marker file, an env var or the migration runner's exit code, all of which can
disagree with the catalogue that the queries actually run against.

WHAT IT CHECKS, and why each one:

  TABLES       the ledger and the discrepancy log: without them a fill's
               economics and an oversell have nowhere to go.
  COLUMNS      the ones the funded reads and writes name. A missing column is
               an exception at the worst possible moment -- mid-submission.
  FUNCTIONS    the predicates the rails and the reservation depend on. The
               reservation is the guarantee against an oversell; without
               `bettor_funded_available_to_exit` there is no reservation.

THE MIGRATION LEDGER IS REPORTED, NOT ENFORCED, and that distinction is load
bearing. `schema_migrations` records what the RUNNER believes it applied; the
objects are what the queries actually run against. The two legitimately differ --
the release gate applies the files with `psql` and records nothing, a restored
snapshot carries objects with no ledger, and on 2026-09-27 production had the
row missing AND the column missing. Blocking on the ledger would have broken
every environment that migrates by another route while adding nothing: a missing
row with every object present cannot break a query. So the ledger gap is
reported for diagnosis and the CAPABILITY turns on the catalogue.

IT IS A READ. It creates nothing, repairs nothing and never applies a migration:
a process that silently migrates on demand is how two processes race each other
through the same DDL.
"""

from __future__ import annotations

VERSION = "FUNDED_SCHEMA_READINESS_V1"

R_SCHEMA_NOT_READY = "THE_FUNDED_SCHEMA_IS_NOT_PRESENT_IN_THIS_DATABASE"
R_SCHEMA_UNREADABLE = "THE_FUNDED_SCHEMA_COULD_NOT_BE_INSPECTED"

#: The migrations the funded lane's code was written against. REPORTED, not
#: enforced -- see the header. A gap here with every object present is a
#: bookkeeping difference, not a broken database.
REQUIRED_MIGRATIONS = (
    "125_funded_pilot_intents_and_fills.sql",
    "126_funded_inventory_exits_and_fees.sql",
    "127_an_exit_commits_no_collateral.sql",
    "128_exit_reservation_and_discrepancies.sql",
)

REQUIRED_TABLES = (
    "bettor_funded_intents",
    "bettor_funded_fills",
    "bettor_funded_economics",
    "bettor_funded_discrepancies",
)

#: Column by column, because "the table exists" was true on the day this broke.
REQUIRED_COLUMNS = {
    "bettor_funded_intents": (
        "kind", "parent_intent_id", "residual_qty", "closed_at",
        "closed_reason", "settlement", "payout_event", "held_is_long",
        "client_identity", "client_identity_supported"),
    "bettor_funded_fills": (
        "direction", "expected_fee_usd", "observed_fee_usd", "fee_state",
        "fee_reconciliation", "economics_written"),
    "bettor_funded_economics": (
        "event_id", "intent_id", "kind", "amount_usd", "provisional", "basis"),
    "bettor_funded_discrepancies": (
        "discrepancy_id", "intent_id", "kind", "detail", "resolved_at"),
}

REQUIRED_FUNCTIONS = (
    "bettor_funded_order_is_outstanding",
    "bettor_funded_holds_inventory",
    "bettor_funded_position_is_open",
    "bettor_funded_available_to_exit",
)

CAPABILITY_AVAILABLE = "AVAILABLE"
CAPABILITY_BLOCKED = "BLOCKED"


async def readiness(conn) -> dict:
    """What is present, what is missing, and whether funded work may run.

    Never raises: an unreadable catalogue is reported as UNREADABLE and BLOCKS,
    because "we could not tell" is not "it is fine".
    """
    out = {"version": VERSION, "capability": CAPABILITY_BLOCKED,
           "ok": False, "refusal": R_SCHEMA_NOT_READY,
           "checked": {"migrations": list(REQUIRED_MIGRATIONS),
                       "tables": list(REQUIRED_TABLES),
                       "functions": list(REQUIRED_FUNCTIONS),
                       "columns": {k: list(v)
                                   for k, v in REQUIRED_COLUMNS.items()}},
           "missing_migrations": [], "missing_tables": [],
           "missing_columns": {}, "missing_functions": [],
           "what_blocks_when_missing": [
               "bettor_funded_execution.submit_for_decision (new exposure)",
               "bettor_funded_management.submit_exit (servicing exits)",
               "bettor_funded_management.cancel_outstanding"],
           "what_stays_available": [
               "every non-funded API route and the diagnostics",
               "the command centre, with the funded section reporting BLOCKED",
               "this readiness read itself"],
           "remedy": ("apply the funded migrations to this database. The API "
                      "does not apply them on demand: two processes racing "
                      "through the same DDL is a worse failure than this one")}
    try:
        applied = {str(r["version"]) for r in await conn.fetch(
            "SELECT version FROM schema_migrations")}
    except Exception as exc:                                   # noqa: BLE001
        # No `schema_migrations` at all is a database that has never been
        # migrated. It is reported as unreadable rather than guessed at.
        return dict(out, refusal=R_SCHEMA_UNREADABLE,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]),
                    why=("the migration ledger could not be read, so nothing "
                         "about this schema is established"))
    out["missing_migrations"] = [m for m in REQUIRED_MIGRATIONS
                                 if m not in applied]
    out["migration_ledger_is_diagnostic_only"] = (
        "a version absent from schema_migrations while every object it creates "
        "is present means this database was migrated by another route -- psql, "
        "a restore, the release gate. It is reported and it does not block")
    try:
        present_tables = {str(r["table_name"]) for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            " WHERE table_schema = 'public'")}
        cols = {}
        for r in await conn.fetch(
                "SELECT table_name, column_name "
                "  FROM information_schema.columns "
                " WHERE table_schema = 'public' "
                "   AND table_name = ANY($1::text[])",
                list(REQUIRED_COLUMNS)):
            cols.setdefault(str(r["table_name"]), set()).add(
                str(r["column_name"]))
        present_functions = {str(r["proname"]) for r in await conn.fetch(
            "SELECT p.proname FROM pg_proc p "
            "  JOIN pg_namespace n ON n.oid = p.pronamespace "
            " WHERE n.nspname = 'public' "
            "   AND p.proname = ANY($1::text[])",
            list(REQUIRED_FUNCTIONS))}
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, refusal=R_SCHEMA_UNREADABLE,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]),
                    why="the catalogue could not be inspected")
    out["missing_tables"] = [t for t in REQUIRED_TABLES
                             if t not in present_tables]
    missing_cols = {}
    for table, wanted in REQUIRED_COLUMNS.items():
        have = cols.get(table, set())
        gone = [c for c in wanted if c not in have]
        if gone:
            missing_cols[table] = gone
    out["missing_columns"] = missing_cols
    out["missing_functions"] = [f for f in REQUIRED_FUNCTIONS
                                if f not in present_functions]
    # THE CAPABILITY TURNS ON THE OBJECTS, and only on the objects.
    complete = not (out["missing_tables"] or out["missing_columns"]
                    or out["missing_functions"])
    if not complete:
        return dict(out, ok=False, capability=CAPABILITY_BLOCKED,
                    refusal=R_SCHEMA_NOT_READY,
                    why=("the funded schema is incomplete in this database: "
                         "%d table(s), %d column group(s) and %d function(s) "
                         "are absent (and %d migration version(s) unrecorded). "
                         "Funded execution is blocked and the rest of the "
                         "service is unaffected"
                         % (len(out["missing_tables"]),
                            len(out["missing_columns"]),
                            len(out["missing_functions"]),
                            len(out["missing_migrations"]))))
    return dict(out, ok=True, capability=CAPABILITY_AVAILABLE, refusal=None,
                ledger_gap_without_consequence=list(
                    out["missing_migrations"]) or None,
                why=("every table, column and function the funded lane names "
                     "is present in this database"
                     + ("" if not out["missing_migrations"] else
                        ". %d migration version(s) are unrecorded in "
                        "schema_migrations, which is how another route applied "
                        "them and is not a capability problem"
                        % len(out["missing_migrations"]))))


async def require(conn) -> dict | None:
    """None when funded work may run; otherwise the refusal to return.

    The shape is a refusal a caller can return unchanged, so each funded entry
    point stays two lines: ask, and hand the answer back if it says no.
    """
    got = await readiness(conn)
    if got.get("ok"):
        return None
    return {"ok": False, "refusal": got.get("refusal") or R_SCHEMA_NOT_READY,
            "funded_capability": CAPABILITY_BLOCKED,
            "schema_readiness": got,
            "why": got.get("why"),
            "nothing_was_sent": True,
            "and_this_is_not_a_market_answer": (
                "the venue was never asked. This is our own database missing "
                "the columns the result would be recorded in, and submitting "
                "into that is how a position ends up held at a venue and "
                "absent from our book")}


def describe() -> dict:
    return {
        "version": VERSION,
        "what_this_is": ("the enforced release condition that separates API "
                         "AVAILABILITY from funded CAPABILITY"),
        "why": ("start.sh serves after a failed migration on purpose -- the "
                "diagnostics are how the failure gets found. That is not a "
                "reason to let a submission run against a schema that cannot "
                "record its result"),
        "required_migrations": list(REQUIRED_MIGRATIONS),
        "required_tables": list(REQUIRED_TABLES),
        "required_functions": list(REQUIRED_FUNCTIONS),
        "required_columns": {k: list(v) for k, v in REQUIRED_COLUMNS.items()},
        "it_never_migrates": True,
        "it_is_read_from_the_catalogue": ("not from a marker file, an env var "
                                          "or the runner's exit code, any of "
                                          "which can disagree with the "
                                          "database the queries run against"),
    }
