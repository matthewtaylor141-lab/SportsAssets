# Migration 126: the corrected attribution, kept out of the applied file

`migrations/126_funded_inventory_exits_and_fees.sql` is byte-identical to the
version production applied on 2026-09-27 03:58 UTC (commit 714680bb; recorded
`schema_migrations.content_sha` `ae8ef15e2f2779e877d8619a6e565f8b798ebd9b9daccff66a4cded26172e8a3`,
read back 2026-10-07 through `research/rt_migration_126_sha.sql`).

Commit 527bd45c later edited a comment inside the applied file. The SQL did
not change, but the file's hash no longer matched the applied record. The
red team migration guard (MIGRATION_INTEGRITY, `APPLIED_MIGRATION_FINGERPRINT_DIFFERS`)
reported it in production at 08828d04. An applied migration is immutable
(`scripts/migrate.py`), so the file is restored to the applied bytes. The
corrected attribution that commit 527bd45c wrote is preserved here verbatim:

> Production said exactly that on 2026-09-27: the whole file rolled back, the
> API served the previous funded schema, and the funded command-centre section
> reported `column "residual_qty" does not exist`.
>
> AND THE DEFECT WAS IN THIS FILE, not in that server. An unqualified name in
> the body of a function used as an index predicate is incompatible SQL under
> PostgreSQL 17 and later -- it depends on a session setting that maintenance
> deliberately does not carry. It passed on every PostgreSQL 16 this file had
> met, which is where the version comes in: 18 EXPOSED the defect, it did not
> cause it, and "the file was byte-identical" describes how long it went
> unnoticed rather than excusing it.
