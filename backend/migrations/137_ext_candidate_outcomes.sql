-- ══════════════════════════════════════════════════════════════════════
-- 137 · EVERY PROVIDER EVENT, AND WHAT THE CYCLE DID WITH IT
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT WAS MISSING. A scheduled cycle refused most events before they were
-- evaluated -- no Pinnacle price, no venue contract, an ambiguous mapping, a
-- book whose currency was not established -- and those events existed only as
-- counts in `ingestion_state.ext_pinnacle_last_cycle`, a row every cycle
-- overwrites. So "33 events, 33 refusals" could be reconciled once, by
-- whoever read it within fifteen minutes, and never again; and which event was
-- refused for which reason was not recorded anywhere.
--
-- WHAT THIS HOLDS. One row per provider event per cycle, appended and never
-- updated: its identity, the venue contract it mapped to where it got that
-- far, its outcome, its first refusal, and every code the cycle attributed to
-- it in the order they fired. An event the cycle moved past without
-- attributing anything is UNCLASSIFIED -- a defect in the accounting,
-- recorded rather than hidden.
--
-- ADDITIVE AND IDEMPOTENT. A new table; nothing existing is altered. The
-- writer checks for the table and reports CANDIDATE_OUTCOMES_TABLE_ABSENT on
-- a database without it, so the code may ship before or after this.

CREATE TABLE IF NOT EXISTS ext_candidate_outcomes (
    id                 bigserial PRIMARY KEY,
    cycle_id           text        NOT NULL,
    cycle_at           timestamptz NOT NULL,
    writer             text,
    sport_key          text        NOT NULL,
    family             text,
    queue_position     integer     NOT NULL,
    provider_event_id  text,
    home               text,
    away               text,
    commence_time      text,
    global_slug        text,
    us_market_slug     text,
    stage              text,
    outcome            text        NOT NULL,
    first_refusal      text,
    codes              jsonb       NOT NULL DEFAULT '[]'::jsonb,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ext_candidate_outcome_ck CHECK (outcome IN
        ('ADMITTED', 'REFUSED', 'ALREADY_RECORDED', 'DEFERRED',
         'UNCLASSIFIED')),
    -- a REFUSED row names its refusal; no other outcome invents one
    CONSTRAINT ext_candidate_refusal_named_ck CHECK (
        (outcome = 'REFUSED') = (first_refusal IS NOT NULL)),
    CONSTRAINT ext_candidate_one_row_per_event_ck
        UNIQUE (cycle_id, sport_key, queue_position)
);

CREATE INDEX IF NOT EXISTS ext_candidate_outcomes_cycle_at_ix
    ON ext_candidate_outcomes (cycle_at);
CREATE INDEX IF NOT EXISTS ext_candidate_outcomes_event_ix
    ON ext_candidate_outcomes (provider_event_id, cycle_at);
