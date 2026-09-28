-- THE ACTUAL CANDIDATE CENSUS, FROM THE ACTUAL EVIDENCE RECORDS.
--
-- Owner directive, "one integrated auditable operating path" §2:
--
--   "Then rerun the actual candidate census. Report counts and candidate
--    identifiers for established compatibility, established conflict and
--    missing evidence, identifying the missing side. Do not describe all
--    464 as reclassified from a reconstructed example or predicted split.
--    Establish that classification from their actual evidence records."
--
-- The previous four-way split was computed by handing a RECONSTRUCTED
-- per-condition shape to settlement_taxonomy.classify_comparison. That
-- reconstruction was faithful to what the register recorded, and it is
-- still not the evidence. This query reads external_valuations, which is
-- where the lane persists one row per candidate with its refusal set.
--
-- READ-ONLY: a single SELECT over one table, run through research-sql
-- against the read replica. No mutating keyword appears in executable SQL.
--
-- The code-to-class mapping below is a transcription of
-- settlement_taxonomy.CLASS_OF. It is duplicated here because this query
-- runs in psql on a runner and cannot import the module; the test
-- test_the_census_sql_matches_the_python_mapping asserts the two agree, so
-- a drift fails the suite rather than silently producing a different split.
--
-- WHAT THIS CANNOT DO. external_valuations stores the refusal CODE set per
-- candidate, not the per-condition verdicts compare() produced. So the
-- code can say the venue rule was not established, and it cannot by itself
-- say WHICH of the seven conditions was silent. Where a code is
-- side-ambiguous by construction the class is reported as C4 with the
-- ambiguity named, exactly as the Python mapping does -- not promoted to
-- C3 because a reconstruction suggested it. Closing that gap needs the
-- per-condition verdicts persisted, which is a schema change and is listed
-- as such rather than papered over here.

\echo == 1 == WINDOW, BUILDS AND ROW COUNT

SELECT
    min(decided_at)                                  AS window_from,
    max(decided_at)                                  AS window_to,
    count(*)                                         AS candidate_rows,
    count(DISTINCT experiment_id)                    AS experiments,
    count(DISTINCT version)                          AS versions,
    count(DISTINCT venue)                            AS venues,
    count(*) FILTER (WHERE admissible)               AS admissible_rows
  FROM external_valuations
 WHERE decided_at >= now() - interval '30 days';

\echo == 2 == THE BUILDS AND EXPERIMENTS REPRESENTED

SELECT experiment_id, version, venue, source_class, provider, book,
       count(*)      AS rows,
       min(decided_at) AS first_seen,
       max(decided_at) AS last_seen
  FROM external_valuations
 WHERE decided_at >= now() - interval '30 days'
 GROUP BY 1,2,3,4,5,6
 ORDER BY rows DESC;

\echo == 3 == EVERY REFUSAL CODE BY FREQUENCY, WITH ITS SETTLEMENT CLASS

WITH win AS (
    SELECT id, experiment_id, event_key, condition_id, sport_family,
           market, period, line, settlement_rule, refusals, admissible
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
), exploded AS (
    SELECT w.id, w.event_key, w.sport_family, w.market,
           unnest(CASE WHEN w.refusals IS NULL OR array_length(w.refusals,1) IS NULL
                       THEN ARRAY['NO_REFUSAL_RECORDED']
                       ELSE w.refusals END) AS code
      FROM win w
), mapping (code, klass, missing_side) AS (
    VALUES
      -- C1: both sides stated a rule and the payouts differ. EVIDENCE.
      ('SETTLEMENT_TERMS_CONFLICT',                        'C1_ESTABLISHED_PAYOUT_CONFLICT', 'neither: both sides stated a rule'),
      ('VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE',   'C1_ESTABLISHED_PAYOUT_CONFLICT', 'neither: both sides stated a rule'),
      ('OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE',           'C1_ESTABLISHED_PAYOUT_CONFLICT', 'neither: both sides stated a rule'),
      ('VENUE_MARKET_SCOPE_IS_A_SEGMENT',                  'C1_ESTABLISHED_PAYOUT_CONFLICT', 'neither: the scope mismatch is decided'),
      -- C2: the BOOKMAKER side is not held. OUR capture gap.
      ('VOID_ABANDONMENT_BOOK_RULE_NOT_HELD',              'C2_BOOK_RULE_NOT_HELD',          'BOOKMAKER'),
      -- C3: the VENUE rule or the market scope is not established.
      ('VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED',            'C3_VENUE_RULE_OR_SCOPE_NOT_HELD','VENUE'),
      ('VENUE_MARKET_SCOPE_NOT_ESTABLISHED',               'C3_VENUE_RULE_OR_SCOPE_NOT_HELD','VENUE'),
      ('VENUE_MARKET_SCOPE_CONFLICTS_WITH_THE_IDENTIFIER', 'C3_VENUE_RULE_OR_SCOPE_NOT_HELD','VENUE'),
      ('SETTLEMENT_SCOPE_NOT_ESTABLISHED',                 'C3_VENUE_RULE_OR_SCOPE_NOT_HELD','VENUE'),
      -- C4: open, and the code does not say which side. NOT promoted.
      ('VOID_ABANDONMENT_RULE_NOT_ESTABLISHED',            'C4_OTHER_UNRESOLVED',            'SIDE_NOT_RECORDED_BY_THIS_CODE'),
      ('OVERTIME_RULE_NOT_ESTABLISHED',                    'C4_OTHER_UNRESOLVED',            'SIDE_NOT_RECORDED_BY_THIS_CODE'),
      ('DRAW_HANDLING_NOT_RECONCILED',                     'C4_OTHER_UNRESOLVED',            'SIDE_NOT_RECORDED_BY_THIS_CODE'),
      ('UNRESOLVED_SETTLEMENT_SEMANTICS',                  'C4_OTHER_UNRESOLVED',            'SIDE_NOT_RECORDED_BY_THIS_CODE')
)
SELECT e.code,
       coalesce(m.klass,
                CASE WHEN e.code ~* '(SETTLE|VOID|OVERTIME|DRAW|ABANDON|SCOPE)'
                     THEN 'C0_UNCLASSIFIED_SETTLEMENT_CODE'
                     ELSE 'NOT_A_SETTLEMENT_CODE' END) AS settlement_class,
       coalesce(m.missing_side, 'n/a')                  AS missing_side,
       count(*)                                          AS candidates_with_this_code,
       count(DISTINCT e.event_key)                       AS distinct_events
  FROM exploded e
  LEFT JOIN mapping m ON m.code = e.code
 GROUP BY 1,2,3
 ORDER BY candidates_with_this_code DESC;

\echo == 4 == PER-CANDIDATE CLASS BY PRECEDENCE, WITH COUNTS

WITH win AS (
    SELECT id, event_key, condition_id, sport_family, market, period,
           refusals, admissible
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
), flags AS (
    SELECT w.id, w.event_key, w.sport_family, w.market, w.admissible,
           bool_or(c ~ '(SETTLEMENT_TERMS_CONFLICT|CONFLICTS_WITH_BOOK_RULE|SCOPE_IS_A_SEGMENT)') AS has_c1,
           bool_or(c = 'VOID_ABANDONMENT_BOOK_RULE_NOT_HELD')                                     AS has_c2,
           bool_or(c ~ '(VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED|VENUE_MARKET_SCOPE_NOT_ESTABLISHED|VENUE_MARKET_SCOPE_CONFLICTS_WITH_THE_IDENTIFIER|SETTLEMENT_SCOPE_NOT_ESTABLISHED)') AS has_c3,
           bool_or(c ~ '(VOID_ABANDONMENT_RULE_NOT_ESTABLISHED|OVERTIME_RULE_NOT_ESTABLISHED|DRAW_HANDLING_NOT_RECONCILED|UNRESOLVED_SETTLEMENT_SEMANTICS)') AS has_c4,
           bool_or(c ~* '(SETTLE|VOID|OVERTIME|DRAW|ABANDON|SCOPE)')                              AS has_any_settlement_code,
           count(*)                                                                                AS n_refusals
      FROM win w
      LEFT JOIN LATERAL unnest(coalesce(w.refusals, ARRAY[]::text[])) AS c ON true
     GROUP BY w.id, w.event_key, w.sport_family, w.market, w.admissible
), classed AS (
    SELECT f.*,
           CASE WHEN f.has_c1                 THEN 'C1_ESTABLISHED_PAYOUT_CONFLICT'
                WHEN f.has_c2 AND f.has_c3    THEN 'C23_BOTH_SIDES_NOT_HELD'
                WHEN f.has_c3                 THEN 'C3_VENUE_RULE_OR_SCOPE_NOT_HELD'
                WHEN f.has_c2                 THEN 'C2_BOOK_RULE_NOT_HELD'
                WHEN f.has_c4                 THEN 'C4_OTHER_UNRESOLVED'
                WHEN f.has_any_settlement_code THEN 'C0_UNCLASSIFIED_SETTLEMENT_CODE'
                ELSE 'NO_SETTLEMENT_REFUSAL'
           END AS candidate_class
      FROM flags f
)
SELECT candidate_class,
       count(*)                              AS candidates,
       count(DISTINCT event_key)             AS distinct_events,
       count(*) FILTER (WHERE admissible)    AS admissible,
       round(avg(n_refusals)::numeric, 2)    AS avg_refusals_per_candidate
  FROM classed
 GROUP BY 1
 ORDER BY candidates DESC;

\echo == 5 == CANDIDATE IDENTIFIERS, up to 12 per class

WITH win AS (
    SELECT id, event_key, condition_id, sport_family, market, period,
           line, settlement_rule, refusals, admissible, decided_at
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
), flags AS (
    SELECT w.*,
           bool_or(c ~ '(SETTLEMENT_TERMS_CONFLICT|CONFLICTS_WITH_BOOK_RULE|SCOPE_IS_A_SEGMENT)') AS has_c1,
           bool_or(c = 'VOID_ABANDONMENT_BOOK_RULE_NOT_HELD')                                     AS has_c2,
           bool_or(c ~ '(VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED|VENUE_MARKET_SCOPE_NOT_ESTABLISHED|VENUE_MARKET_SCOPE_CONFLICTS_WITH_THE_IDENTIFIER|SETTLEMENT_SCOPE_NOT_ESTABLISHED)') AS has_c3,
           bool_or(c ~ '(VOID_ABANDONMENT_RULE_NOT_ESTABLISHED|OVERTIME_RULE_NOT_ESTABLISHED|DRAW_HANDLING_NOT_RECONCILED|UNRESOLVED_SETTLEMENT_SEMANTICS)') AS has_c4,
           bool_or(c ~* '(SETTLE|VOID|OVERTIME|DRAW|ABANDON|SCOPE)')                              AS has_any_settlement_code
      FROM win w
      LEFT JOIN LATERAL unnest(coalesce(w.refusals, ARRAY[]::text[])) AS c ON true
     GROUP BY w.id, w.event_key, w.condition_id, w.sport_family, w.market,
              w.period, w.line, w.settlement_rule, w.refusals, w.admissible,
              w.decided_at
), classed AS (
    SELECT f.*,
           CASE WHEN f.has_c1                 THEN 'C1_ESTABLISHED_PAYOUT_CONFLICT'
                WHEN f.has_c2 AND f.has_c3    THEN 'C23_BOTH_SIDES_NOT_HELD'
                WHEN f.has_c3                 THEN 'C3_VENUE_RULE_OR_SCOPE_NOT_HELD'
                WHEN f.has_c2                 THEN 'C2_BOOK_RULE_NOT_HELD'
                WHEN f.has_c4                 THEN 'C4_OTHER_UNRESOLVED'
                WHEN f.has_any_settlement_code THEN 'C0_UNCLASSIFIED_SETTLEMENT_CODE'
                ELSE 'NO_SETTLEMENT_REFUSAL'
           END AS candidate_class,
           row_number() OVER (PARTITION BY
               CASE WHEN f.has_c1                 THEN 'C1'
                    WHEN f.has_c2 AND f.has_c3    THEN 'C23'
                    WHEN f.has_c3                 THEN 'C3'
                    WHEN f.has_c2                 THEN 'C2'
                    WHEN f.has_c4                 THEN 'C4'
                    WHEN f.has_any_settlement_code THEN 'C0'
                    ELSE 'NONE' END
               ORDER BY f.decided_at DESC) AS rn
      FROM flags f
)
SELECT candidate_class, event_key, condition_id, sport_family, market,
       period, line, settlement_rule, admissible, refusals
  FROM classed
 WHERE rn <= 12
 ORDER BY candidate_class, event_key;

\echo == 6 == THE INDEPENDENT ADMISSION BLOCKERS, ALONGSIDE SETTLEMENT

-- §2: "Keep market-data currency, calibration and other independent
-- admission blockers visible alongside settlement." A candidate whose
-- settlement evidence were completed would still have to clear these, so
-- reporting settlement alone would overstate how close admission is.

WITH win AS (
    SELECT id, refusals, admissible, age_s, overround,
           outcomes_priced, expected_outcomes
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
), exploded AS (
    SELECT w.id, w.admissible,
           unnest(coalesce(w.refusals, ARRAY['NO_REFUSAL_RECORDED']::text[])) AS code
      FROM win w
)
SELECT CASE
         WHEN code ~* '(SETTLE|VOID|OVERTIME|DRAW|ABANDON|SCOPE)' THEN 'SETTLEMENT'
         WHEN code ~* '(STALE|AGE|STREAM|STAMP|STATE|FRESH|CURRENC)' THEN 'MARKET_DATA_CURRENCY'
         WHEN code ~* '(CALIB|SOURCE|DEVIG|PROVIDER|BOOK_COUNT|OVERROUND)' THEN 'SOURCE_CALIBRATION'
         WHEN code ~* '(ACCOUNT|AUTHORI|CREDENTIAL|PAUSED|ELIGIB)' THEN 'ACCOUNT_OR_AUTHORIZATION'
         WHEN code ~* '(IDENT|MAP|ALIAS|SLUG|CONTRACT)' THEN 'INSTRUMENT_IDENTITY'
         WHEN code ~* '(EDGE|EV|EDGE_GATE|NET)' THEN 'ECONOMICS'
         ELSE 'OTHER'
       END                            AS blocker_family,
       count(DISTINCT id)             AS candidates_blocked,
       count(*)                       AS code_occurrences
  FROM exploded
 GROUP BY 1
 ORDER BY candidates_blocked DESC;

\echo == 7 == HOW MANY CANDIDATES CLEAR EVERY FAMILY EXCEPT SETTLEMENT

WITH win AS (
    SELECT id, refusals, admissible
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
), fam AS (
    SELECT w.id, w.admissible,
           bool_or(c ~* '(SETTLE|VOID|OVERTIME|DRAW|ABANDON|SCOPE)')       AS settlement,
           bool_or(c ~* '(STALE|AGE|STREAM|STAMP|STATE|FRESH|CURRENC)')    AS currency,
           bool_or(c ~* '(CALIB|SOURCE|DEVIG|PROVIDER|BOOK_COUNT|OVERROUND)') AS calibration,
           bool_or(c ~* '(ACCOUNT|AUTHORI|CREDENTIAL|PAUSED|ELIGIB)')      AS account,
           bool_or(c ~* '(IDENT|MAP|ALIAS|SLUG|CONTRACT)')                 AS identity,
           bool_or(c ~* '(EDGE|EV|EDGE_GATE|NET)')                         AS economics,
           count(*)                                                         AS n
      FROM win w
      LEFT JOIN LATERAL unnest(coalesce(w.refusals, ARRAY[]::text[])) AS c ON true
     GROUP BY w.id, w.admissible
)
SELECT count(*)                                                     AS candidates,
       count(*) FILTER (WHERE n = 0)                                AS zero_refusals,
       count(*) FILTER (WHERE settlement)                           AS blocked_on_settlement,
       count(*) FILTER (WHERE settlement AND NOT currency
                          AND NOT calibration AND NOT account
                          AND NOT identity AND NOT economics)       AS settlement_is_the_only_family,
       count(*) FILTER (WHERE NOT settlement)                       AS settlement_clear,
       count(*) FILTER (WHERE NOT settlement AND NOT currency
                          AND NOT calibration AND NOT account
                          AND NOT identity AND NOT economics)       AS every_family_clear,
       count(*) FILTER (WHERE admissible)                           AS admissible
  FROM fam;
