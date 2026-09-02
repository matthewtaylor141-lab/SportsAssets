-- EVERY ROSTER DECISION, WITH THE NUMBERS THAT MADE IT.
--
-- Owner order 2026-09-01 (evening): whales enter, are promoted, and are
-- demoted by the data, not by anyone. That is only acceptable if every
-- move is auditable after the fact: which whale, from what state to
-- what, at what clip, on how many settled copies, with what interval,
-- and whether his own on-chain book was funded at the time.
--
-- One row per whale per pass, changed or not, so a quiet hour reads as
-- "the rules ran and held" rather than as silence. `changed` marks the
-- rows that moved money.
CREATE TABLE IF NOT EXISTS roster_decisions (
    id          bigserial         PRIMARY KEY,
    ts          timestamptz       NOT NULL DEFAULT now(),
    whale       text              NOT NULL,
    from_state  text              NOT NULL,
    to_state    text              NOT NULL,
    clip_usd    double precision,
    reason      text              NOT NULL,
    n           integer           NOT NULL DEFAULT 0,
    roi         double precision,
    ci_lo       double precision,
    ci_hi       double precision,
    clusters    integer,
    funded      boolean           NOT NULL DEFAULT false,
    changed     boolean           NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS roster_decisions_whale_ts_idx
    ON roster_decisions (whale, ts DESC);
CREATE INDEX IF NOT EXISTS roster_decisions_changed_idx
    ON roster_decisions (ts DESC) WHERE changed;
