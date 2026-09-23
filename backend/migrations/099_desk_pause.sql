-- PAUSE THE AFFECTED SHADOW DESK. DB-backed, fail-closed, and it
-- lands with this migration rather than waiting for an operator.
--
-- WHY NOT THE ENV FLAG. `BETTOR_DESK_LOOP` is the loop's on/off switch,
-- but changing an env var makes Render REDEPLOY the service from its
-- CONFIGURED BRANCH -- claude/session-njaewf -- which carries none of
-- the desk code. Pausing that way would roll the API back and take the
-- desk, learning and correction read routes with it, so the management
-- page could not show that the desk is paused. The pause has to be
-- data, not configuration.
--
-- WHY NOT status = 'PAUSED'. The partial unique index that guarantees
-- one ACTIVE account per desk is defined WHERE status = 'ACTIVE'.
-- Moving the account out of ACTIVE would free that index and the next
-- start would CREATE A SECOND ACCOUNT -- the one thing this must not
-- do. The account stays ACTIVE and carries a pause flag beside it.
--
-- IT IS RE-READ EVERY CYCLE, so clearing it resumes the desk without a
-- deploy, and setting it stops the desk without one either.

ALTER TABLE bettor_desk_accounts
    ADD COLUMN IF NOT EXISTS paused BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bettor_desk_accounts
    ADD COLUMN IF NOT EXISTS pause_reason TEXT;
ALTER TABLE bettor_desk_accounts
    ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ;

-- The last moment the account's state was verified against the running
-- build. The management page shows this beside the pause notice so a
-- reader knows how old the last good check is.
ALTER TABLE bettor_desk_accounts
    ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ;
ALTER TABLE bettor_desk_accounts
    ADD COLUMN IF NOT EXISTS last_verified_detail JSONB
        NOT NULL DEFAULT '{}'::jsonb;

-- ACCOUNTING UNCERTAINTY IS A PROPERTY OF THE ACCOUNT, not a note in a
-- document. The id collision ran from 16:06:51Z until the corrected
-- build, so records written in that window may have been overwritten by
-- a later process reusing the same id. The fix prevents future
-- collisions; it repairs nothing already overwritten.
ALTER TABLE bettor_desk_accounts
    ADD COLUMN IF NOT EXISTS accounting_status TEXT
        NOT NULL DEFAULT 'ACCOUNTING_OK';
ALTER TABLE bettor_desk_accounts
    ADD COLUMN IF NOT EXISTS accounting_detail JSONB
        NOT NULL DEFAULT '{}'::jsonb;

-- CONTAINMENT, applied here so it is in force the moment this build
-- boots. Only the ACTIVE account is touched; capital is not changed, no
-- record is removed and no account is created.
-- A JSON LITERAL, not jsonb_build_object. My first version built the
-- object from concatenated SQL strings and produced an ODD number of
-- arguments, which Postgres rejects -- so the migration would have
-- failed at API boot and taken the API down with it. A literal is
-- checkable before it ships.
UPDATE bettor_desk_accounts
   SET paused = TRUE,
       paused_at = COALESCE(paused_at, now()),
       pause_reason = 'ACCOUNTING_RECOVERY: identifier collision across '
                      'restarts of this account. Paused pending '
                      'verification of the corrected build.',
       accounting_status = 'ACCOUNTING_UNCERTAIN',
       accounting_detail = '{"cause": "Generated ids were a per-process counter, so two processes on this account produced identical ids. Orders upserted ON CONFLICT DO UPDATE were overwritten; decisions upserted ON CONFLICT DO NOTHING were dropped.", "window_from": "2026-09-23T16:06:51Z", "fix": "ids are now derived from the source event id", "what_the_fix_does_not_do": "it prevents future collisions and repairs nothing already overwritten", "reconstructable": "referential integrity holds: 0 orders without a decision, 0 fills without an order, 0 filled_qty disagreeing with the sum of its fills, 0 overdrawn consumption rows", "NOT_reconstructable": "whether a specific historical value was overwritten. An overwritten order carries its fills under the same key range, so they are overwritten with it and remain mutually consistent -- consistency therefore does not evidence absence of overwrite.", "performance_qualification": "SUPPRESSED"}'::jsonb
 WHERE status = 'ACTIVE';
