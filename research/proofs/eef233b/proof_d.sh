#!/bin/bash
# PROOF d: migration 181 on fresh scratch DBs (mp2 + migrate). Never prints the password.
source /tmp/claude-0/-home-user-SportsAssets/574041cf-ef73-5ae7-a98a-27ed42fe523d/scratchpad/rb3/env.sh
OLD=$S/rb3/ddd4050          # git archive of ddd4050 (scratch), the pre-181 code
UP=$WT/backend/migrations/181_derek_research_model_runs_decisive_run_per_day.sql
DOWN=$WT/backend/migrations/rollback/181_derek_research_model_runs_decisive_run_per_day.down.sql
V181=181_derek_research_model_runs_decisive_run_per_day.sql
q() { psql -h 127.0.0.1 -U postgres -d $1 -tAX -v ON_ERROR_STOP=1 -c "$2"; }
shape() {
  echo "  one_per_day constraint: $(q $1 "SELECT count(*) FROM pg_constraint WHERE conname='derek_research_model_runs_one_per_day'")"
  echo "  unique indexes on run_day: $(q $1 "SELECT string_agg(pg_get_indexdef(indexrelid), ' | ') FROM pg_index WHERE indrelid='derek_research_model_runs'::regclass AND indisunique AND indexrelid <> 'derek_research_model_runs_pkey'::regclass")"
  echo "  rows by day: $(q $1 "SELECT coalesce(string_agg(run_day||':'||n, ' '),'none') FROM (SELECT run_day, count(*) n FROM derek_research_model_runs GROUP BY 1 ORDER BY 1) d")"
}
oldmigrate() { (cd $OLD/backend && DATABASE_URL="$(dsn $1)" PYTHONDONTWRITEBYTECODE=1 nice -n 10 $PY -m sportsassets.scripts.migrate 2>&1 | grep -E "applying|complete" | sed "s/${PGPASSWORD}/***/g"); }

echo "=== d1: fresh mp2 -> migrate (rel-boot3 worktree) ==="
dropdb_ rb3_d1 2>/dev/null; mkdb rb3_d1 | grep -E "applying 1[78]|complete"
echo "  schema_migrations 170..181: $(q rb3_d1 "SELECT string_agg(version, ', ' ORDER BY version) FROM schema_migrations WHERE version >= '170'")"
shape rb3_d1
echo "--- d1: re-running migrate (expect no 'applying' line) ---"
(cd $WT/backend && DATABASE_URL="$(dsn rb3_d1)" nice -n 10 $PY -m sportsassets.scripts.migrate 2>&1 | grep -E "applying|complete|CHANGED" | sed "s/${PGPASSWORD}/***/g")
echo "--- d1: down script on a DB with no same-day pair ---"
psql -h 127.0.0.1 -U postgres -d rb3_d1 -v ON_ERROR_STOP=1 -qf $DOWN && echo "  down: OK"
shape rb3_d1
echo "--- d1: migrate after the down is a NO-OP (181 still recorded) ---"
(cd $WT/backend && DATABASE_URL="$(dsn rb3_d1)" nice -n 10 $PY -m sportsassets.scripts.migrate 2>&1 | grep -E "applying|complete" | sed "s/${PGPASSWORD}/***/g")
shape rb3_d1
echo "--- d1: the documented manual step, then migrate re-applies 181 ---"
q rb3_d1 "DELETE FROM schema_migrations WHERE version = '$V181'" >/dev/null
(cd $WT/backend && DATABASE_URL="$(dsn rb3_d1)" nice -n 10 $PY -m sportsassets.scripts.migrate 2>&1 | grep -E "applying|complete" | sed "s/${PGPASSWORD}/***/g")
shape rb3_d1

echo "=== d2: a DB that already holds same-day rows ==="
echo "--- d2: mp2 -> migrate with the ddd4050 tree (through 180) ---"
dropdb_ rb3_d2 2>/dev/null; psql -h 127.0.0.1 -U postgres -d postgres -qc "CREATE DATABASE rb3_d2 TEMPLATE mp2"
oldmigrate rb3_d2 | grep -E "applying 1[78]|complete"
shape rb3_d2
echo "--- d2: the rejected 37814ad draft of 181 applied (as its runner would record it), then same-day rows ---"
psql -h 127.0.0.1 -U postgres -d rb3_d2 -v ON_ERROR_STOP=1 -qf $S/rb3/rejected_181.sql
q rb3_d2 "INSERT INTO schema_migrations (version) VALUES ('181_derek_research_model_runs_rerun_after_insufficient.sql')" >/dev/null
q rb3_d2 "INSERT INTO derek_research_model_runs (run_id, run_day, ran_at, outcome, counts) VALUES
  ('proof-d2:a', '2031-01-01', '2031-01-01T00:05Z', 'INSUFFICIENT_LABELLED_FIXTURES', '{}'),
  ('proof-d2:b', '2031-01-01', '2031-01-01T00:25Z', 'FITTED_AND_EVALUATED', '{}'),
  ('proof-d2:c', '2031-01-02', '2031-01-02T00:05Z', 'INSUFFICIENT_LABELLED_FIXTURES', '{}'),
  ('proof-d2:d', '2031-01-02', '2031-01-02T00:06Z', 'INSUFFICIENT_LABELLED_FIXTURES', '{}')" >/dev/null
shape rb3_d2
echo "--- d2: migrate (rel-boot3) applies 181 over them ---"
(cd $WT/backend && DATABASE_URL="$(dsn rb3_d2)" nice -n 10 $PY -m sportsassets.scripts.migrate 2>&1 | grep -E "applying|complete" | sed "s/${PGPASSWORD}/***/g")
shape rb3_d2
echo "--- d2: re-running migrate is a no-op ---"
(cd $WT/backend && DATABASE_URL="$(dsn rb3_d2)" nice -n 10 $PY -m sportsassets.scripts.migrate 2>&1 | grep -E "applying|complete" | sed "s/${PGPASSWORD}/***/g")
echo "--- d2: the down script REFUSES over same-day rows (expect ERROR, nothing changed) ---"
psql -h 127.0.0.1 -U postgres -d rb3_d2 -v ON_ERROR_STOP=1 -qf $DOWN 2>&1 | sed 's/^/  /'; echo "  psql exit: ${PIPESTATUS[0]}"
shape rb3_d2
echo "--- d2: append-only: the same-day rows cannot be deleted to make room ---"
q rb3_d2 "DELETE FROM derek_research_model_runs WHERE run_id='proof-d2:d'" 2>&1 | sed 's/^/  /'

echo "=== d3: CODE-ONLY ROLLBACK to ddd4050 with 181 left in place (rb3_d2) ==="
(cd $OLD/backend && DATABASE_URL="$(dsn rb3_d2)" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. $PY - <<'PY' 2>&1 | sed "s/${PGPASSWORD}/***/g"
import asyncio, os, asyncpg, datetime as dt
from sportsassets.agents import derek_research as DR
async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        for label, iso in (("day with an INSUFFICIENT + FITTED pair", "2031-01-01T12:00:00+00:00"),
                           ("day with two INSUFFICIENT rows", "2031-01-02T12:00:00+00:00")):
            t = dt.datetime.fromisoformat(iso).timestamp()
            r = await DR.daily_model_run(c, now=t)
            print("  ddd4050 daily_model_run on %s: already_ran=%s ran=%s run_id=%s" % (label, r.get("already_ran"), r.get("ran"), r.get("run_id")))
        lr = await DR.latest_model_run(c)
        print("  ddd4050 latest_model_run: run_day=%s outcome=%s" % (lr["run_day"], lr["outcome"]))
        print("  ddd4050 code reads nothing that needs UNIQUE(run_day); its first-row insert uses the run_id primary key")
    finally:
        await c.close()
asyncio.run(main())
PY
)
for d in rb3_d1 rb3_d2; do dropdb_ $d; done
echo "=== scratch DBs dropped ==="
