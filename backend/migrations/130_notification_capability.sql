-- 130: A DELIBERATELY DESIGNED, PROTECTED CAPABILITY FOR NOTIFICATION CONTROL.
--
-- WHAT WAS WRONG, AND IT WAS NOT THE ENTROPY. `user_key` is a client-generated
-- UUIDv4 -- 122 random bits, so guessing it was never the threat. It failed as a
-- credential for two structural reasons:
--
--   IT IS IN A URL PATH. `GET` and `PUT /api/prefs/{user_key}` carry it as a path
--   segment, where it lands in access logs, proxy logs, referrer headers and
--   browser history. The system that depends on its secrecy is the same system
--   that writes it down.
--
--   THE CLIENT CHOOSES IT. A value the caller picks cannot be a capability the
--   server issued, and nothing stops one client claiming another's.
--
-- SO THE IDENTIFIER AND THE CREDENTIAL ARE SEPARATED HERE. `user_key` stays as
-- the row's public IDENTIFIER -- it may appear in a path, be logged, be shared --
-- and it authorises NOTHING. Control requires a secret that:
--
--   * the SERVER generates, so entropy and uniqueness are not the client's to
--     get wrong;
--   * is returned EXACTLY ONCE, at first registration;
--   * travels in a HEADER, never a path, so it is not written to logs by
--     construction;
--   * is stored as a SHA-256 HASH, so reading this table does not yield control
--     of anything -- a database dump, a backup or a support query cannot mint
--     authority.
--
-- WHY NOT A LOGIN. There is no per-user principal in this system: the only
-- server-established identities are shared operator tokens (admin, desk, wall),
-- and none of them identifies a browser. Building accounts to protect alert
-- preferences would be the wrong size of change, so the second route the
-- instruction allows -- a deliberately designed protected capability -- is the
-- one taken, and it is designed rather than asserted.

BEGIN;

CREATE TABLE IF NOT EXISTS notification_capabilities (
    -- The PUBLIC identifier. Safe in a path, safe in a log, authorises nothing.
    user_key        text PRIMARY KEY,
    -- SHA-256 of the capability secret. The secret itself is never stored, so
    -- this table cannot be read to obtain control.
    secret_sha256   text NOT NULL,
    issued_at       timestamptz NOT NULL DEFAULT now(),
    -- Bookkeeping, so a lost or abused capability is visible rather than silent.
    last_used_at    timestamptz,
    uses            bigint NOT NULL DEFAULT 0,
    revoked_at      timestamptz,
    CONSTRAINT notification_capabilities_sha_len
        CHECK (char_length(secret_sha256) = 64)
);

COMMENT ON TABLE notification_capabilities IS
    'One capability per notification user_key. The secret is server-generated, '
    'returned once at first registration, sent in a header (never a path), and '
    'stored only as a SHA-256 hash -- so reading this table yields no control.';

COMMENT ON COLUMN notification_capabilities.user_key IS
    'The PUBLIC identifier. It may appear in a path or a log and it authorises '
    'nothing.';

COMMENT ON COLUMN notification_capabilities.secret_sha256 IS
    'SHA-256 of the capability secret. The secret is never stored.';

CREATE INDEX IF NOT EXISTS notification_capabilities_issued_idx
    ON notification_capabilities (issued_at DESC);

COMMIT;
