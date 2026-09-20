-- WHAT THE TWO VENUES ACTUALLY SAY about the markets whose binding
-- came back AMBIGUOUS.
--
-- Owner, 2026-09-20: IDENTITY_BINDINGS = 16, EXACT = 0,
-- EXECUTION_ELIGIBLE = 0, AMBIGUOUS = 16. One example:
--
--   market_id                   = asc-cfb-byu-colst-2026-09-19-3q-pos-1pt5
--   institutional_instrument_id = asc-cfb-byu-colst-2026-09-19-3q-pos-1pt5
--   why  = "retail leg 'yes' does not name outcome '1.5'"
--
-- THE HYPOTHESIS TO TEST, not to assume: the resolver is applying
-- MULTI-OUTCOME outcome-name matching to a BINARY PROPOSITION. On a
-- mutually exclusive set {LAF, SJE, NEITHER} the retail leg must name
-- one outcome, and `yes` names none of them -- that rule is right and
-- stays. On a binary spread, `yes` is not supposed to equal the strike
-- text "1.5"; it means the proposition that instrument represents
-- resolves true.
--
-- THIS FILE DECIDES NOTHING. It prints the venue-native fields both
-- sides published so the classification can be built from what the
-- venues say rather than from what the slug looks like. In particular
-- it asks whether `event_outcome` separates the two families, and
-- whether the retail row's OWN `line` already names the same strike
-- the institutional record calls `outcome_strike`.

\echo '--- 1. THE BINDINGS, AND WHY EACH ONE WAS REFUSED ---'
SELECT 'binding|' || market_id
       || '|leg=' || outcome_leg
       || '|status=' || identity_status
       || '|eligible=' || execution_eligible
       || '|inst=' || COALESCE(institutional_instrument_id, 'NULL')
       || '|strike=' || COALESCE(outcome_strike, 'NULL')
       || '|eventOutcome=' || COALESCE(event_outcome, 'NULL')
       || '|payout=' || COALESCE(payout_value, 'NULL')
       || '|ps/qs=' || COALESCE(price_scale::text, 'NULL')
       || '/' || COALESCE(quantity_scale::text, 'NULL')
       || '|why=' || COALESCE(why::text, '[]')
  FROM bettor_identity_bindings
 ORDER BY market_id, outcome_leg;

\echo ''
\echo '--- 2. DOES event_outcome SEPARATE THE FAMILIES? ---'
-- If the mutually-exclusive sets carry one value here and the binary
-- propositions carry another, the venue has already told us which
-- family each instrument belongs to and nothing has to be inferred.
SELECT 'family|' || COALESCE(event_outcome, 'NULL')
       || '|bindings=' || count(*)
       || '|markets=' || count(DISTINCT market_id)
       || '|strikes=' || count(DISTINCT outcome_strike)
       || '|sample_market=' || min(market_id)
       || '|sample_strike=' || COALESCE(min(outcome_strike), 'NULL')
  FROM bettor_identity_bindings
 GROUP BY event_outcome
 ORDER BY count(*) DESC;

\echo ''
\echo '--- 3. THE RETAIL SIDE OWN FIELDS for the same markets ---'
-- us_premap is the retail venue's own row. `kind` and `line` are ITS
-- description of the contract: if `line` already equals the
-- institutional `outcome_strike`, then both venues have independently
-- published the same strike and proposition identity does not need a
-- slug to establish it.
SELECT 'retail|' || p.market_slug
       || '|side=' || COALESCE(p.side_norm, 'NULL')
       || '|kind=' || COALESCE(p.kind, 'NULL')
       || '|line=' || COALESCE(p.line::text, 'NULL')
       || '|event=' || COALESCE(p.event_slug, 'NULL')
       || '|identifier=' || COALESCE(left(p.identifier, 14), 'NULL')
  FROM us_premap p
 WHERE p.market_slug IN (SELECT DISTINCT market_id
                           FROM bettor_identity_bindings)
 ORDER BY p.market_slug, p.side_norm;

\echo ''
\echo '--- 4. DO RETAIL line AND INSTITUTIONAL outcome_strike AGREE? ---'
-- THE QUESTION THAT DECIDES THE WHOLE THING. Two venues independently
-- publishing the same strike for the same event is proposition
-- identity stated by both of them. Two venues where only the SLUG
-- matches is a coincidence of naming, and §1 refuses to trade on it.
SELECT 'strike_agreement|' || b.market_id
       || '|leg=' || b.outcome_leg
       || '|inst_strike=' || COALESCE(b.outcome_strike, 'NULL')
       || '|retail_line=' || COALESCE(p.line::text, 'ABSENT')
       || '|retail_kind=' || COALESCE(p.kind, 'ABSENT')
       || '|agree=' || CASE
              WHEN p.line IS NULL OR b.outcome_strike IS NULL THEN 'UNKNOWN'
              WHEN b.outcome_strike ~ '^-?[0-9]+(\.[0-9]+)?$'
                   AND p.line::numeric = b.outcome_strike::numeric
                  THEN 'YES'
              ELSE 'NO' END
  FROM bettor_identity_bindings b
  LEFT JOIN us_premap p
         ON p.market_slug = b.market_id
        AND lower(p.side_norm) = b.outcome_leg
 ORDER BY b.market_id, b.outcome_leg;

\echo ''
\echo '--- 5. THE SLUG GRAMMAR of the focus set, counted ---'
-- The prefix is the retail venue's own contract-family marker in this
-- codebase (aec- moneyline, asc- spread, tsc- total, astatc- prop).
-- Printed to see WHICH families the experimental focus set is actually
-- made of, rather than reasoning from the one example in hand.
SELECT 'grammar|' || split_part(market_id, '-', 1)
       || '|markets=' || count(DISTINCT market_id)
       || '|eligible=' || count(*) FILTER (WHERE execution_eligible)
       || '|statuses=' || string_agg(DISTINCT identity_status, ',')
  FROM bettor_identity_bindings
 GROUP BY split_part(market_id, '-', 1)
 ORDER BY count(DISTINCT market_id) DESC;

\echo ''
\echo '--- 6. IS THERE A FULL INSTRUMENT RECORD ANYWHERE? ---'
-- Settlement rule and period live in the record`s metadata, which the
-- binding table does not store. If the direct worker has begun writing
-- instrument_record into evidence, the remaining venue-native fields
-- are readable and the classification can use them.
SELECT 'record|' || instrument_id
       || '|regime=' || COALESCE(latency_regime, 'NULL')
       || '|has_record=' || (instrument_record IS NOT NULL)
       || '|keys=' || COALESCE(
              (SELECT string_agg(k, ',' ORDER BY k)
                 FROM jsonb_object_keys(instrument_record) AS k), 'NONE')
  FROM bettor_l2_evidence
 WHERE instrument_id IN (SELECT DISTINCT market_id
                           FROM bettor_identity_bindings)
 ORDER BY received_timestamp DESC
 LIMIT 12;

\echo ''
\echo '--- 7. THE METADATA BLOCK, verbatim, for one spread instrument ---'
-- Verbatim because the FIELD NAMES are the thing being established.
-- Adapting to guessed names has cost this lane a whole run before.
SELECT 'meta|' || instrument_id || '|' || jsonb_pretty(
           COALESCE(instrument_record -> 'metadata', '{}'::jsonb))
  FROM bettor_l2_evidence
 WHERE instrument_record IS NOT NULL
   AND instrument_id LIKE 'asc-%'
 ORDER BY received_timestamp DESC
 LIMIT 1;

\echo ''
\echo '--- 8. AND THE eventAttributes BLOCK, verbatim ---'
SELECT 'eventattrs|' || instrument_id || '|' || jsonb_pretty(
           COALESCE(instrument_record -> 'eventAttributes', '{}'::jsonb))
  FROM bettor_l2_evidence
 WHERE instrument_record IS NOT NULL
 ORDER BY received_timestamp DESC
 LIMIT 2;

\echo ''
\echo '--- 9. THE DECISIONS THESE BINDINGS BLOCKED ---'
SELECT 'blocked|' || action
       || '|' || execution_status
       || '|' || identity_binding_status
       || '|n=' || count(*)
       || '|markets=' || count(DISTINCT market_id)
  FROM bettor_experimental_decisions
 WHERE decision_timestamp > now() - interval '24 hours'
 GROUP BY action, execution_status, identity_binding_status
 ORDER BY count(*) DESC;
