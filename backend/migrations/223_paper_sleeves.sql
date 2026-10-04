-- ══════════════════════════════════════════════════════════════════════
-- 223 · PAPER BOOK ECONOMIC TRUTH: A DURABLE SLEEVE PER PAPER POSITION
--       (RESEARCH / SHADOW, NO CAPITAL AUTHORITY)
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECT. The fictional $500,000 paper account (migration 171) holds
-- positions with different ECONOMIC PURPOSES on one cash ledger: the
-- production-candidate investment policy and the bounded training /
-- exploration strategy (migration 189) -- and, when they run, the strict
-- benchmark and the maker experiment. Every account-level figure mixed
-- them, so an exploration loss (a research cost, by design) read as an
-- investment loss and an exploration win read as production alpha.
--
-- THE FIX. Every paper position group carries ONE durable sleeve, derived
-- deterministically from the strategy (and the deciding policy version) of
-- its ENTRY decision -- a position never switches strategy (migration 182),
-- so it never switches sleeve:
--
--   INVESTMENT    PINNACLE_COMPLETED_GAME_PAPER (the versioned investment
--                 policy; V2/V3 are the live-eligible versions,
--                 execmirror.LIVE_ELIGIBLE) and DEREK_ENTRY_POLICY_V2
--                 (Derek's own entry policy)
--   TRAINING      PINNACLE_EXPLORATION_PAPER (the owner's bounded training
--                 strategy: negative expected value is a research cost)
--   BENCHMARK     PINNACLE_ONLY_PAPER_BENCHMARK (strict benchmark) and
--                 PINNACLE_COMPLETED_GAME_MAKER_PAPER (execution-style
--                 experiment, not promoted): control arms, not production
--   UNCLASSIFIED  anything else, a group whose orders carry more than one
--                 strategy, or a group with no strategy -- shown as such,
--                 NEVER silently INVESTMENT
--
-- paper_sleeve_of() is the ONE classifier (version PAPER_SLEEVE_V1); the
-- Python reader (bettor_paper_sleeves.classify) is pinned equal to it by a
-- test. A new classifier version is a NEW row per group (UNIQUE per
-- version), never an edit.
--
-- APPEND-ONLY. paper_sleeve_classifications refuses UPDATE, DELETE and
-- TRUNCATE. No ledger, order, fill, decision or settlement row is altered
-- or deleted by this migration: the backfill only READS them.
--
-- CLASSIFIED AT ENTRY. An AFTER INSERT trigger on paper_orders classifies
-- the group of every new ENTRY order in the same transaction. It can never
-- block the paper order: a classification failure is a WARNING and the
-- group then reads UNCLASSIFIED (NO_DURABLE_CLASSIFICATION) until a
-- backstop run (bettor_paper_sleeves.classify_missing) records it.
--
-- NO AUTHORITY. label = 'RESEARCH', authority = 'SHADOW_NO_AUTHORITY'
-- (CHECK). Nothing in an order, sizing, limit, threshold, gate, allowlist
-- or capital path reads this table.
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction with its schema_migrations row. Idempotent.

CREATE OR REPLACE FUNCTION paper_sleeve_of(p_strategy text,
                                           p_policy_version text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_strategy
        WHEN 'PINNACLE_COMPLETED_GAME_PAPER' THEN 'INVESTMENT'
        WHEN 'DEREK_ENTRY_POLICY_V2' THEN 'INVESTMENT'
        WHEN 'PINNACLE_EXPLORATION_PAPER' THEN 'TRAINING'
        WHEN 'PINNACLE_ONLY_PAPER_BENCHMARK' THEN 'BENCHMARK'
        WHEN 'PINNACLE_COMPLETED_GAME_MAKER_PAPER' THEN 'BENCHMARK'
        ELSE 'UNCLASSIFIED' END
$$;

CREATE OR REPLACE FUNCTION paper_sleeve_basis(p_strategy text,
                                              p_policy_version text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN p_strategy IS NULL THEN 'NO_STRATEGY_RECORDED'
        WHEN p_strategy = 'PINNACLE_COMPLETED_GAME_PAPER' THEN
            CASE WHEN p_policy_version IN ('PINNACLE_COMPLETED_GAME_PAPER_V2',
                                           'PINNACLE_COMPLETED_GAME_PAPER_V3')
                 THEN 'INVESTMENT_POLICY_LIVE_ELIGIBLE_VERSION'
                 ELSE 'INVESTMENT_POLICY_VERSION_NOT_LIVE_ELIGIBLE' END
        WHEN p_strategy = 'DEREK_ENTRY_POLICY_V2' THEN 'DEREK_ENTRY_POLICY'
        WHEN p_strategy = 'PINNACLE_EXPLORATION_PAPER' THEN
            'EXPLORATION_TRAINING_STRATEGY_RESEARCH_COST'
        WHEN p_strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK' THEN
            'STRICT_BENCHMARK_CONTROL'
        WHEN p_strategy = 'PINNACLE_COMPLETED_GAME_MAKER_PAPER' THEN
            'MAKER_EXPERIMENT_NOT_PROMOTED'
        ELSE 'UNKNOWN_STRATEGY' END
$$;

CREATE TABLE IF NOT EXISTS paper_sleeve_classifications (
    classification_id   text PRIMARY KEY,
    account_id          text        NOT NULL,
    group_id            text        NOT NULL,
    sleeve              text        NOT NULL,
    strategy            text,
    policy_version      text,
    decision_id         text,
    entry_order_id      text,
    basis               text        NOT NULL,
    classifier_version  text        NOT NULL,
    classified_by       text        NOT NULL,
    entry_at            timestamptz,
    classified_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT paper_sleeve_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT paper_sleeve_authority_ck CHECK (
        authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT paper_sleeve_sleeve_ck CHECK (sleeve IN (
        'INVESTMENT', 'TRAINING', 'BENCHMARK', 'UNCLASSIFIED')),
    CONSTRAINT paper_sleeve_by_ck CHECK (classified_by IN (
        'MIGRATION_223_BACKFILL', 'ENTRY_TRIGGER', 'BACKSTOP')),
    CONSTRAINT paper_sleeve_one_per_version UNIQUE (group_id,
                                                    classifier_version)
);
CREATE INDEX IF NOT EXISTS paper_sleeve_account_idx
    ON paper_sleeve_classifications (account_id, sleeve);

CREATE OR REPLACE FUNCTION paper_sleeve_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'paper_sleeve_classifications is append-only (%): a '
                    'new classifier version is a new row', TG_OP;
END $$;

DROP TRIGGER IF EXISTS paper_sleeve_classifications_append_only_trg
    ON paper_sleeve_classifications;
CREATE TRIGGER paper_sleeve_classifications_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_sleeve_classifications
    FOR EACH ROW EXECUTE FUNCTION paper_sleeve_is_append_only();
DROP TRIGGER IF EXISTS paper_sleeve_classifications_no_truncate_trg
    ON paper_sleeve_classifications;
CREATE TRIGGER paper_sleeve_classifications_no_truncate_trg
    BEFORE TRUNCATE ON paper_sleeve_classifications
    FOR EACH STATEMENT EXECUTE FUNCTION paper_sleeve_is_append_only();

-- THE CURRENT SLEEVE PER GROUP: the newest classifier version's row.
CREATE OR REPLACE VIEW paper_sleeve_current_v AS
SELECT DISTINCT ON (group_id) classification_id, account_id, group_id,
       sleeve, strategy, policy_version, decision_id, entry_order_id, basis,
       classifier_version, classified_by, entry_at, classified_at
  FROM paper_sleeve_classifications
 ORDER BY group_id, classified_at DESC, classification_id DESC;

-- CLASSIFIED AT ENTRY. Never blocks the paper order (a sub-transaction:
-- a failure is a WARNING, the group reads UNCLASSIFIED until backstopped).
CREATE OR REPLACE FUNCTION paper_sleeve_classify_entry()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    pv text;
BEGIN
    BEGIN
        IF NEW.decision_id IS NOT NULL THEN
            SELECT policy_version INTO pv FROM paper_decisions
             WHERE decision_id = NEW.decision_id;
        END IF;
        INSERT INTO paper_sleeve_classifications (
            classification_id, account_id, group_id, sleeve, strategy,
            policy_version, decision_id, entry_order_id, basis,
            classifier_version, classified_by, entry_at)
        VALUES ('psc:' || md5(NEW.group_id || ':PAPER_SLEEVE_V1'),
                NEW.account_id, NEW.group_id,
                paper_sleeve_of(NEW.strategy, pv), NEW.strategy, pv,
                NEW.decision_id, NEW.order_id,
                paper_sleeve_basis(NEW.strategy, pv), 'PAPER_SLEEVE_V1',
                'ENTRY_TRIGGER', NEW.created_at)
        ON CONFLICT (group_id, classifier_version) DO NOTHING;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'paper sleeve classification failed for group %: %',
            NEW.group_id, SQLERRM;
    END;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS paper_orders_sleeve_at_entry_trg ON paper_orders;
CREATE TRIGGER paper_orders_sleeve_at_entry_trg
    AFTER INSERT ON paper_orders
    FOR EACH ROW WHEN (NEW.role = 'ENTRY')
    EXECUTE FUNCTION paper_sleeve_classify_entry();

-- DETERMINISTIC BACKFILL from the existing records (read only). A group
-- whose orders carry more than one strategy is UNCLASSIFIED by name.
WITH g AS (
    SELECT group_id, count(DISTINCT strategy) AS strategies
      FROM paper_orders GROUP BY group_id),
first_order AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.account_id, o.strategy,
           o.decision_id, o.order_id, o.created_at, o.role
      FROM paper_orders o
     ORDER BY o.group_id, (o.role = 'ENTRY') DESC, o.created_at,
              o.order_id)
INSERT INTO paper_sleeve_classifications (
    classification_id, account_id, group_id, sleeve, strategy,
    policy_version, decision_id, entry_order_id, basis, classifier_version,
    classified_by, entry_at)
SELECT 'psc:' || md5(f.group_id || ':PAPER_SLEEVE_V1'), f.account_id,
       f.group_id,
       CASE WHEN g.strategies > 1 THEN 'UNCLASSIFIED'
            ELSE paper_sleeve_of(f.strategy, pd.policy_version) END,
       f.strategy, pd.policy_version, f.decision_id,
       CASE WHEN f.role = 'ENTRY' THEN f.order_id END,
       CASE WHEN g.strategies > 1 THEN 'GROUP_CARRIES_MORE_THAN_ONE_STRATEGY'
            ELSE paper_sleeve_basis(f.strategy, pd.policy_version) END,
       'PAPER_SLEEVE_V1', 'MIGRATION_223_BACKFILL', f.created_at
  FROM first_order f
  JOIN g USING (group_id)
  LEFT JOIN paper_decisions pd ON pd.decision_id = f.decision_id
ON CONFLICT (group_id, classifier_version) DO NOTHING;
