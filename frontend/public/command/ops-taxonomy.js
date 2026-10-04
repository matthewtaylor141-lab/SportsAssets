/* BETTOR COMMAND · REFUSAL TAXONOMY · Operations desk, Release 1
 *
 * ONE frontend table: every refusal code the production backend (R29,
 * 191b299) can record -> SOFTWARE or ECONOMIC, with the pipeline stage it
 * belongs to and the backend constant the classification is derived from.
 *
 *   SOFTWARE  the system could not produce an answer: an input it needs was
 *             not established, a record was malformed, a capability (a
 *             sport, a settlement term, a model) is missing, or an external
 *             data source did not carry it. Engineering / data can clear it.
 *   ECONOMIC  the system DECIDED on the evidence it had: no edge after fees,
 *             no depth inside the break-even limit, a risk / capacity rail,
 *             a measured staleness past its limit, a stated terms conflict,
 *             or an explicit owner policy. The market or the owner clears it.
 *
 * DERIVATION, in this order of precedence:
 *   1. bettor_external_shadow.EVALUABILITY_OF (the lane's own judgement):
 *      DECIDED_ON_THE_EVIDENCE -> ECONOMIC; COULD_NOT_EVALUATE and
 *      EXTERNAL_DEPENDENCY -> SOFTWARE. Stage = bettor_external_shadow.STAGES
 *      (ext_candidate_outcomes stage ordinals 1_PROBABILITY .. 8_ECONOMICS).
 *   2. paper decision codes (derek_policy, paper_derek, paper_benchmark /
 *      CG, paper_maker, paper_explore): lost_opportunity.classify categories
 *      (MARKET_NO_EDGE -> ECONOMIC; MISSING_INPUT, DEFECT -> SOFTWARE;
 *      CONTROL -> by the control's nature, named per row) and its
 *      STAGE_ATTRIBUTION for the stage.
 *   3. bettor_paper_ledger order refusals, actual_admission (SMALL LIVE),
 *      bettor_pinnacle_devig and pinnapi_primary codes, each named per row.
 *
 * ANY CODE NOT IN THIS TABLE IS UNCLASSIFIED -- shown as its own row, never
 * folded into SOFTWARE or ECONOMIC (the lane's 9_UNCLASSIFIED_REFUSAL is
 * UNCLASSIFIED too). Codes carrying a suffix ("CODE:detail") classify by the
 * part before the first colon, as the backend does.
 *
 * Release 2 reads the backend's own taxonomy (GET /api/command/agent-funnel)
 * instead of this table. Display only: nothing here admits, refuses or
 * changes a threshold. */
(function (root) {
  'use strict';
  var SOURCE_SHA = '191b299';
  var CLASSES = ['SOFTWARE', 'ECONOMIC', 'UNCLASSIFIED'];
  var STAGES = ["1_PROBABILITY", "2_FRESHNESS", "3_IDENTITY", "4_SETTLEMENT_SCOPE", "5_EXECUTION_ESTIMATE", "6_SIZING", "7_RISK", "8_ECONOMICS", "POLICY", "CONFIGURATION", "DEFECT", "9_UNCLASSIFIED_REFUSAL"];
  // code: [class, stage, basis, backend source(s) at 191b299]
  var TABLE = {
  "ADMISSION_PINNAPI_EVIDENCE_NOT_FRESH_OR_NOT_MAPPED":["SOFTWARE","1_PROBABILITY","actual_admission (SMALL LIVE admission): PinnAPI evidence not fresh or not mapped","actual_admission.R_PROBABILITY"],
  "DEVIG_METHOD_NOT_DECLARED":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig refusal: the Pinnacle probability could not be established","bettor_pinnacle_devig.R_UNKNOWN_METHOD"],
  "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED":["SOFTWARE","1_PROBABILITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:1_PROBABILITY"],
  "INTERNAL_MODEL_CANNOT_SCORE_THIS_CANDIDATE":["SOFTWARE","1_PROBABILITY","lost_opportunity.classify MISSING_INPUT; derek_policy DEP_ENGINEERING","agents.derek_policy.R_MODEL_CANNOT_SCORE;bettor_paper_ops.REFUSAL_WORDS"],
  "INTERNAL_MODEL_NOT_QUALIFIED":["SOFTWARE","1_PROBABILITY","bettor_paper_ops.REFUSAL_WORDS; MISSING_INPUT (model not qualified)","bettor_paper_ops.REFUSAL_WORDS"],
  "LINE_DOES_NOT_MATCH":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig refusal: the Pinnacle probability could not be established","bettor_pinnacle_devig.R_LINE_MISMATCH"],
  "MAPPING_AMBIGUOUS":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig refusal: the Pinnacle probability could not be established","bettor_pinnacle_devig.R_AMBIGUOUS_MAPPING"],
  "MAPPING_NOT_ESTABLISHED":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig refusal: the Pinnacle probability could not be established","bettor_pinnacle_devig.R_NO_MAPPING"],
  "MARKET_NOT_IN_SUPPORTED_SET":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig refusal: the Pinnacle probability could not be established","bettor_pinnacle_devig.R_UNSUPPORTED_MARKET"],
  "NO_APPROVED_INTERNAL_MODEL":["SOFTWARE","1_PROBABILITY","lost_opportunity.classify MISSING_INPUT; derek_policy DEP_ENGINEERING","agents.derek_policy.R_NO_MODEL;bettor_paper_ops.REFUSAL_WORDS"],
  "NO_PINNACLE_ON_EVENT":["SOFTWARE","1_PROBABILITY","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY;bettor_external_shadow.STAGES:1_PROBABILITY"],
  "NO_QUALIFIED_MODEL":["SOFTWARE","1_PROBABILITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:1_PROBABILITY"],
  "NO_QUALIFIED_PINNACLE_PROBABILITY":["SOFTWARE","1_PROBABILITY","lost_opportunity.classify CONTROL; MISSING_PROBABILITY attribution; derek_policy DEP_EVIDENCE","agents.derek_policy.R_NO_PINNACLE;bettor_paper_ops.REFUSAL_WORDS"],
  "NO_RESEARCH_MODEL_CANDIDATE_EXISTS":["SOFTWARE","1_PROBABILITY","lost_opportunity.classify MISSING_INPUT; derek_policy DEP_ENGINEERING","agents.paper_derek.R_NO_RESEARCH_MODEL;bettor_paper_ops.REFUSAL_WORDS"],
  "ODDS_NOT_A_PRICE":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig refusal: the Pinnacle probability could not be established","bettor_pinnacle_devig.R_BAD_ODDS"],
  "OUTCOME_DEPTH_BELOW_FLOOR":["SOFTWARE","1_PROBABILITY","bettor_external_shadow.STAGES 1_PROBABILITY (R_THIN_OUTCOME, the multi-book floor: probability not established)","agents.paper_benchmark.R_THIN_OUTCOME;bettor_external_shadow.R_THIN_OUTCOME;bettor_external_shadow.STAGES:1_PROBABILITY"],
  "OUTCOME_SET_INCOMPLETE":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig refusal: the Pinnacle probability could not be established","bettor_pinnacle_devig.R_INCOMPLETE_OUTCOMES"],
  "PERIOD_DOES_NOT_MATCH":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig refusal: the Pinnacle probability could not be established","bettor_pinnacle_devig.R_PERIOD_MISMATCH"],
  "PINNACLE_ABSENT_IN_OBSERVED_RESPONSES":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig: the provider does not quote it (analog NO_PINNACLE_ON_EVENT = EXTERNAL_DEPENDENCY)","bettor_pinnacle_devig.R_PINNACLE_ABSENT"],
  "PINNACLE_NOT_IN_THIS_PAYLOAD":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig: the provider does not quote it (analog NO_PINNACLE_ON_EVENT = EXTERNAL_DEPENDENCY)","bettor_pinnacle_devig.R_BOOK_MISSING"],
  "PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE":["SOFTWARE","1_PROBABILITY","paper_benchmark: the lane did not qualify the probability (MISSING_PROBABILITY)","agents.paper_benchmark.R_PROBABILITY_UNQUALIFIED;bettor_paper_ops.REFUSAL_WORDS"],
  "RESEARCH_MODEL_CANNOT_SCORE_THIS_CANDIDATE":["SOFTWARE","1_PROBABILITY","lost_opportunity.classify MISSING_INPUT; derek_policy DEP_ENGINEERING","agents.paper_derek.R_MODEL_CANNOT_SCORE;bettor_paper_ops.REFUSAL_WORDS"],
  "RESEARCH_MODEL_PROVENANCE_NOT_VERIFIED":["SOFTWARE","1_PROBABILITY","lost_opportunity.classify MISSING_INPUT; derek_policy DEP_ENGINEERING","agents.paper_derek.R_MODEL_UNVERIFIED;bettor_paper_ops.REFUSAL_WORDS"],
  "SELECTION_NOT_IN_OUTCOME_SET":["SOFTWARE","1_PROBABILITY","bettor_pinnacle_devig refusal: the Pinnacle probability could not be established","bettor_pinnacle_devig.R_SELECTION_UNMATCHED"],
  "THIN_OUTCOME_COVERAGE":["SOFTWARE","1_PROBABILITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:1_PROBABILITY"],
  "ADMISSION_BOOK_CURRENCY_NOT_LIVE_ADMISSIBLE":["SOFTWARE","2_FRESHNESS","actual_admission (SMALL LIVE admission): book currency not established (analog VENUE_BOOK_CURRENCY_NOT_ESTABLISHED = EXTERNAL_DEPENDENCY)","actual_admission.R_BOOK_CURRENCY"],
  "ONE_CLOCK_IS_NOT_MEASURED":["SOFTWARE","2_FRESHNESS","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:2_FRESHNESS"],
  "OUR_OWN_PROCESSING_DELAY_EXCEEDED_BEFORE_THE_DECISION":["ECONOMIC","2_FRESHNESS","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:2_FRESHNESS"],
  "PINNACLE_NOT_FRESH":["ECONOMIC","2_FRESHNESS","bettor_paper_ops.REFUSAL_WORDS: measured age past the limit (analog QUOTE_STALE = DECIDED_ON_THE_EVIDENCE)","bettor_paper_ops.REFUSAL_WORDS"],
  "PINNAPI_PRIMARY_CLOCK_INVALID":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PINNAPI_PRIMARY_FIXTURE_CHANGED":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PINNAPI_PRIMARY_FIXTURE_UNPROVED":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PINNAPI_PRIMARY_INCOMPLETE_OUTCOMES":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PINNAPI_PRIMARY_INPUT_CHANGED":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PINNAPI_PRIMARY_NO_EXACT_FIXTURE":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PINNAPI_PRIMARY_PHASE_UNPROVED":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PINNAPI_PRIMARY_PROVENANCE_INVALID":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","agents.paper_derek.py"],
  "PINNAPI_PRIMARY_PROVENANCE_MISSING":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","agents.paper_derek.py"],
  "PINNAPI_PRIMARY_RUNTIME_UNIDENTIFIED":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PINNAPI_PRIMARY_SPORT_UNSUPPORTED":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS: primary-source provenance not established","pinnapi_primary.py"],
  "PROBABILITY_EVIDENCE_FRESHNESS_UNKNOWN":["SOFTWARE","2_FRESHNESS","lost_opportunity.classify CONTROL; age not establishable (analog ONE_CLOCK_IS_NOT_MEASURED = COULD_NOT_EVALUATE)","agents.derek_policy.R_FRESHNESS_UNKNOWN;bettor_paper_ops.REFUSAL_WORDS"],
  "PROBABILITY_EVIDENCE_STALE":["ECONOMIC","2_FRESHNESS","lost_opportunity.classify CONTROL; measured age past the limit (analog QUOTE_STALE = DECIDED_ON_THE_EVIDENCE)","agents.derek_policy.R_STALE;bettor_paper_ops.REFUSAL_WORDS"],
  "QUOTE_HAS_NO_TIMESTAMP":["SOFTWARE","2_FRESHNESS","bettor_pinnacle_devig: age not measurable (analog ONE_CLOCK_IS_NOT_MEASURED = COULD_NOT_EVALUATE)","bettor_pinnacle_devig.R_NO_TIMESTAMP"],
  "QUOTE_STALE":["ECONOMIC","2_FRESHNESS","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:2_FRESHNESS;bettor_pinnacle_devig.R_STALE"],
  "QUOTE_STALE_ON_ARRIVAL":["ECONOMIC","2_FRESHNESS","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:2_FRESHNESS"],
  "THE_PAPER_BOOK_OBSERVATION_IS_NOT_CURRENT":["ECONOMIC","2_FRESHNESS","paper_benchmark: measured book age past its bound (analog VENUE_BOOK_STALE = DECIDED_ON_THE_EVIDENCE)","agents.paper_benchmark.R_BOOK_NOT_CURRENT;bettor_paper_ops.REFUSAL_WORDS"],
  "VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT":["ECONOMIC","2_FRESHNESS","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:2_FRESHNESS"],
  "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED":["SOFTWARE","2_FRESHNESS","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY;bettor_external_shadow.STAGES:2_FRESHNESS"],
  "VENUE_BOOK_STALE":["ECONOMIC","2_FRESHNESS","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:2_FRESHNESS"],
  "VENUE_QUOTE_STALE":["ECONOMIC","2_FRESHNESS","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE; stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:DECIDED"],
  "ADMISSION_IDENTITY_NOT_EXACT":["SOFTWARE","3_IDENTITY","actual_admission (SMALL LIVE admission): identity not exact","actual_admission.R_IDENTITY"],
  "ADMISSION_VENUE_CONTRACT_NOT_ESTABLISHED":["SOFTWARE","3_IDENTITY","actual_admission (SMALL LIVE admission): venue contract not established","actual_admission.R_CONTRACT"],
  "FIXTURE_IDENTITY_NOT_ESTABLISHED":["SOFTWARE","3_IDENTITY","lost_opportunity.classify CONTROL; identity not established (COULD_NOT_EVALUATE analog)","agents.derek_policy.R_IDENTITY;bettor_paper_ops.REFUSAL_WORDS"],
  "MARKET_OR_LINE_NOT_A_MONEYLINE_MATCH":["SOFTWARE","3_IDENTITY","paper_benchmark match: the mapped contract is not the priced moneyline (identity gap)","agents.paper_benchmark.R_MARKET;bettor_paper_ops.REFUSAL_WORDS"],
  "NOT_A_SUPPORTED_POLYMARKET_US_CONTRACT":["SOFTWARE","3_IDENTITY","lost_opportunity.classify CONTROL; IDENTITY_MAPPING attribution: unsupported venue contract","agents.paper_derek.R_NOT_PMUS;bettor_paper_ops.REFUSAL_WORDS"],
  "NO_PREMAP_CONTRACT_FOR_THIS_FIXTURE":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY;bettor_external_shadow.STAGES:3_IDENTITY"],
  "NO_VENUE_CONTRACT_FOR_EVENT":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement); stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY"],
  "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement); stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY"],
  "NO_VENUE_NATIVE_EVENT_FOR_FIXTURE":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY;bettor_external_shadow.STAGES:3_IDENTITY"],
  "PAYOUT_OUTCOME_DISAGREES_WITH_THE_VENUE_INTENT":["ECONOMIC","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:3_IDENTITY"],
  "PAYOUT_OUTCOME_INDEX_NOT_BOUND_TO_A_TOKEN":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "PAYOUT_OUTCOME_MATCH_NOT_ESTABLISHED":["SOFTWARE","3_IDENTITY","paper_benchmark match: payout outcome not established (COULD_NOT_EVALUATE analog)","agents.paper_benchmark.R_OUTCOME;bettor_paper_ops.REFUSAL_WORDS"],
  "REAL_EVENT_NOT_ESTABLISHED":["SOFTWARE","3_IDENTITY","lost_opportunity.classify CONTROL; event not established (COULD_NOT_EVALUATE analog)","agents.derek_policy.R_NOT_REAL;bettor_paper_ops.REFUSAL_WORDS"],
  "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_CONTRACT_KIND_IS_NOT_A_CONFIRMED_MONEYLINE":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_CONTRACT_PERIOD_NOT_ESTABLISHED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_DOES_NOT_LIST_THIS_FIXTURE":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_EVENT_IS_NOT_A_TWO_PARTICIPANT_MATCH":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_EVENT_TITLE_NOT_CAPTURED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_MAPPING_AMBIGUOUS":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_MARKET_SCOPE_CONFLICTS_WITH_THE_IDENTIFIER":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_MARKET_SCOPE_IS_A_SEGMENT":["ECONOMIC","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_MARKET_SCOPE_NOT_ESTABLISHED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_MARKET_TYPE_IS_NOT_A_WINNER_STRUCTURE":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_MARKET_TYPE_IS_UNSPECIFIED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_MARKET_TYPE_METADATA_NOT_RETAINED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_CANDIDATE_READ_TRUNCATED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_CATALOGUE_READ_FAILED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_COMPETITION_NOT_ESTABLISHED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_AMBIGUOUS":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_NOT_FOUND":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_CONTRACT_SIDES_NOT_ESTABLISHED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_EVENT_AMBIGUOUS":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_FAMILY_NOT_SUPPORTED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_MATCH_RAISED":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_NICKNAME_DOES_NOT_CONFIRM_THE_TEAM":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_PROVIDER_EVENT_NOT_MATCHABLE":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_NATIVE_TEAM_ASSIGNMENT_AMBIGUOUS":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "VENUE_SLUG_DOES_NOT_DECOMPOSE_INTO_EVENT_AND_SIDE":["SOFTWARE","3_IDENTITY","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:3_IDENTITY"],
  "ADMISSION_SETTLEMENT_NOT_LIVE_ADMISSIBLE":["SOFTWARE","4_SETTLEMENT_SCOPE","actual_admission (SMALL LIVE admission): settlement not admissible for live","actual_admission.R_SETTLEMENT"],
  "DRAW_HANDLING_NOT_RECONCILED":["SOFTWARE","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE; stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE"],
  "GRADING_PERIOD_NOT_FULL_GAME":["ECONOMIC","4_SETTLEMENT_SCOPE","paper_benchmark: the contract pays on part of the game (analog VENUE_MARKET_SCOPE_IS_A_SEGMENT = DECIDED_ON_THE_EVIDENCE)","agents.paper_benchmark.R_PERIOD;bettor_paper_ops.REFUSAL_WORDS"],
  "NO_COMPLETED_GAME_TERMS_FOR_THIS_SPORT":["SOFTWARE","4_SETTLEMENT_SCOPE","paper_benchmark R_FAMILY: no completed-game terms held for the sport (capability gap)","agents.paper_benchmark.R_FAMILY;bettor_paper_ops.REFUSAL_WORDS"],
  "ORDINARY_GRADING_PERIOD_MISMATCH":["ECONOMIC","4_SETTLEMENT_SCOPE","paper_benchmark: venue and book grade differently (analog SETTLEMENT_TERMS_CONFLICT = DECIDED_ON_THE_EVIDENCE)","agents.paper_benchmark.R_GP_MISMATCH;bettor_paper_ops.REFUSAL_WORDS"],
  "ORDINARY_GRADING_PERIOD_NOT_ESTABLISHED":["SOFTWARE","4_SETTLEMENT_SCOPE","paper_benchmark: grading period not established (COULD_NOT_EVALUATE analog)","agents.paper_benchmark.R_GP_UNKNOWN;bettor_paper_ops.REFUSAL_WORDS"],
  "OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE":["ECONOMIC","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE; stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:DECIDED"],
  "OVERTIME_RULE_NOT_ESTABLISHED":["SOFTWARE","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:4_SETTLEMENT_SCOPE"],
  "SETTLEMENT_NOT_SUPPORTED":["SOFTWARE","4_SETTLEMENT_SCOPE","lost_opportunity.classify CONTROL; settlement support not established for the contract (the deployed SHA's known NFL settlement-support gap); a stated payout conflict is SETTLEMENT_TERMS_CONFLICT","agents.derek_policy.R_SETTLEMENT;bettor_paper_ops.REFUSAL_WORDS"],
  "SETTLEMENT_RULE_DOES_NOT_MATCH":["ECONOMIC","4_SETTLEMENT_SCOPE","bettor_pinnacle_devig: stated rules differ (analog SETTLEMENT_TERMS_CONFLICT = DECIDED_ON_THE_EVIDENCE)","bettor_pinnacle_devig.R_SETTLEMENT_MISMATCH"],
  "SETTLEMENT_SCOPE_NOT_ESTABLISHED":["SOFTWARE","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:4_SETTLEMENT_SCOPE"],
  "SETTLEMENT_TERMS_CONFLICT":["ECONOMIC","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:4_SETTLEMENT_SCOPE"],
  "UNRESOLVED_SETTLEMENT_SEMANTICS":["SOFTWARE","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:4_SETTLEMENT_SCOPE"],
  "VENUE_RULES_TEXT_NOT_RECORDED_ON_THE_VALUATION_ROW":["SOFTWARE","4_SETTLEMENT_SCOPE","paper_benchmark: venue rules text not captured (COULD_NOT_EVALUATE analog)","agents.paper_benchmark.R_GP_TEXT_ABSENT;bettor_paper_ops.REFUSAL_WORDS"],
  "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED":["SOFTWARE","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE; stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE"],
  "VOID_ABANDONMENT_BOOK_RULE_NOT_HELD":["SOFTWARE","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE; stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE"],
  "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE":["ECONOMIC","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE; stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:DECIDED"],
  "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED":["SOFTWARE","4_SETTLEMENT_SCOPE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:4_SETTLEMENT_SCOPE"],
  "ADMISSION_DEPTH_NOT_AVAILABLE":["SOFTWARE","5_EXECUTION_ESTIMATE","actual_admission (SMALL LIVE admission): depth not established","actual_admission.R_DEPTH"],
  "ADMISSION_EXECUTABLE_PRICE_UNKNOWN":["SOFTWARE","5_EXECUTION_ESTIMATE","actual_admission (SMALL LIVE admission): executable price unknown","actual_admission.R_PRICE"],
  "ADMISSION_FEES_UNKNOWN":["SOFTWARE","5_EXECUTION_ESTIMATE","actual_admission (SMALL LIVE admission): fees unknown","actual_admission.R_FEES"],
  "ADMISSION_MARKET_NOT_TRADABLE":["ECONOMIC","5_EXECUTION_ESTIMATE","actual_admission (SMALL LIVE admission): market state not tradable (decided on the evidence)","actual_admission.R_TRADABLE"],
  "ADMISSION_SLIPPAGE_NOT_REPRESENTED":["SOFTWARE","5_EXECUTION_ESTIMATE","actual_admission (SMALL LIVE admission): slippage not represented","actual_admission.R_SLIPPAGE"],
  "BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE":["SOFTWARE","5_EXECUTION_ESTIMATE","lost_opportunity.classify CONTROL; MISSING_EXECUTABLE_BOOK attribution","agents.paper_derek.R_BOOK_DEADLINE"],
  "CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE":["SOFTWARE","5_EXECUTION_ESTIMATE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE; stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE"],
  "EXECUTION_ESTIMATE_NOT_IDENTIFIED":["SOFTWARE","5_EXECUTION_ESTIMATE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:5_EXECUTION_ESTIMATE"],
  "FEES_NOT_ESTABLISHED":["SOFTWARE","5_EXECUTION_ESTIMATE","lost_opportunity.classify CONTROL; EXECUTION_UNCERTAINTY attribution; derek_policy DEP_EVIDENCE","agents.derek_policy.R_FEES;bettor_paper_ops.REFUSAL_WORDS"],
  "LIMIT_PRICE_NOT_SUPPORTED":["SOFTWARE","5_EXECUTION_ESTIMATE","lost_opportunity.classify CONTROL; EXECUTION_UNCERTAINTY attribution: limit outside venue support","agents.derek_policy.R_LIMIT;bettor_paper_ops.REFUSAL_WORDS"],
  "NO_ASK_TO_REST_BELOW":["ECONOMIC","5_EXECUTION_ESTIMATE","paper_maker: the observed book had no ask (market state)","agents.paper_maker.R_NO_ASK;bettor_paper_ops.REFUSAL_WORDS"],
  "NO_DEPTH_AT_THE_BEST_LEVEL":["ECONOMIC","5_EXECUTION_ESTIMATE","bettor_paper_ops.REFUSAL_WORDS: no displayed quantity (LIQUIDITY)","bettor_paper_ops.REFUSAL_WORDS"],
  "NO_ESTABLISHED_EXECUTABLE_DEPTH":["ECONOMIC","5_EXECUTION_ESTIMATE","lost_opportunity.classify CONTROL; LIQUIDITY attribution (analog NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT = DECIDED_ON_THE_EVIDENCE)","agents.derek_policy.R_NO_DEPTH;bettor_paper_ops.REFUSAL_WORDS"],
  "NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT":["ECONOMIC","5_EXECUTION_ESTIMATE","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:5_EXECUTION_ESTIMATE"],
  "P_FILL_NOT_IDENTIFIED":["SOFTWARE","5_EXECUTION_ESTIMATE","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:5_EXECUTION_ESTIMATE"],
  "THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY":["SOFTWARE","5_EXECUTION_ESTIMATE","lost_opportunity.classify CONTROL; MISSING_EXECUTABLE_BOOK attribution (analog VENUE_BOOK_NOT_READ = EXTERNAL_DEPENDENCY)","agents.paper_derek.R_NO_BOOK;bettor_paper_ops.REFUSAL_WORDS"],
  "VENUE_BOOK_NOT_READ":["SOFTWARE","5_EXECUTION_ESTIMATE","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY;bettor_external_shadow.STAGES:5_EXECUTION_ESTIMATE"],
  "VENUE_BOOK_READ_FAILED":["SOFTWARE","5_EXECUTION_ESTIMATE","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement); stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY"],
  "VENUE_BOOK_READ_RETURNED_ERROR":["SOFTWARE","5_EXECUTION_ESTIMATE","bettor_external_shadow.EVALUABILITY_OF = EXTERNAL_DEPENDENCY (external data gap, not an economic judgement); stage by analogy (not in bettor_external_shadow.STAGES)","bettor_external_shadow.EVALUABILITY_OF:EXTERNAL_DEPENDENCY"],
  "NO_CAPACITY_UNDER_THE_LANE_RAILS":["ECONOMIC","6_SIZING","lost_opportunity.classify CONTROL; CAPACITY attribution (analog NO_RAIL_HEADROOM_FOR_ANY_POSITION = DECIDED_ON_THE_EVIDENCE)","agents.derek_policy.R_CAPACITY;bettor_paper_ops.REFUSAL_WORDS"],
  "NO_RAIL_HEADROOM_FOR_ANY_POSITION":["ECONOMIC","6_SIZING","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:6_SIZING"],
  "NO_SIZED_QUANTITY":["ECONOMIC","6_SIZING","lost_opportunity.classify CONTROL; LIQUIDITY attribution: no quantity at the policy edge (analog UNFILLED_NOTIONAL = DECIDED_ON_THE_EVIDENCE)","agents.derek_policy.R_NO_QTY;bettor_paper_ops.REFUSAL_WORDS"],
  "ONE_CONTRACT_EXCEEDS_THE_EXPLORATION_ENTRY_BUDGET":["ECONOMIC","6_SIZING","paper_explore: one contract exceeds the entry budget (sizing)","agents.paper_explore.R_TOO_DEAR;bettor_paper_ops.REFUSAL_WORDS"],
  "RAIL_HEADROOM_NOT_MEASURED":["SOFTWARE","6_SIZING","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:6_SIZING"],
  "SIZING_POLICY_NOT_APPLICABLE":["SOFTWARE","6_SIZING","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:6_SIZING"],
  "UNFILLED_NOTIONAL":["ECONOMIC","6_SIZING","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:6_SIZING"],
  "ABOVE_THE_MAXIMUM_CONCURRENT_GROUPS":["ECONOMIC","7_RISK","bettor_paper_ledger order refusal: concurrent-groups cap (risk/capital rail)","bettor_paper_ledger.R_MAX_GROUPS"],
  "ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP":["ECONOMIC","7_RISK","bettor_paper_ledger order refusal: per-fixture cap (risk/capital rail)","bettor_paper_ledger.R_PER_FIXTURE"],
  "ABOVE_THE_PER_MARKET_CONCENTRATION_CAP":["ECONOMIC","7_RISK","bettor_paper_ledger order refusal: per-market cap (risk/capital rail)","bettor_paper_ledger.R_PER_MARKET"],
  "ABOVE_THE_PER_ORDER_CAP":["ECONOMIC","7_RISK","bettor_paper_ledger order refusal: per-order cap (risk/capital rail)","bettor_paper_ledger.R_PER_ORDER"],
  "ACTION_EXPOSURE_EFFECT_NOT_IDENTIFIED":["SOFTWARE","7_RISK","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:7_RISK"],
  "ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE":["ECONOMIC","7_RISK","paper_benchmark / bettor_paper_ledger one-strategy-per-fixture rule (risk decision)","agents.paper_benchmark.R_CROSS_STRATEGY;bettor_paper_ledger.R_FIXTURE_OWNED;bettor_paper_ops.REFUSAL_WORDS"],
  "AN_ENTRY_MAY_NOT_SPEND_THE_HEDGE_RESERVE":["ECONOMIC","7_RISK","bettor_paper_ledger order refusal: hedge reserve protected (risk/capital rail)","bettor_paper_ledger.R_HEDGE_RESERVE"],
  "EXPLORATION_AGGREGATE_EXPOSURE_LIMIT":["ECONOMIC","7_RISK","paper_explore: aggregate exposure limit (risk limit)","agents.paper_explore.R_AGGREGATE;bettor_paper_ops.REFUSAL_WORDS"],
  "EXPLORATION_ALREADY_HOLDS_THIS_FIXTURE":["ECONOMIC","7_RISK","paper_explore: one position per fixture (risk rule)","agents.paper_explore.R_FIXTURE_TAKEN;bettor_paper_ops.REFUSAL_WORDS"],
  "EXPLORATION_REALIZED_LOSS_STOP_REACHED":["ECONOMIC","7_RISK","paper_explore: realized-loss stop reached (risk limit)","agents.paper_explore.R_LOSS_STOP;bettor_paper_ops.REFUSAL_WORDS"],
  "INSUFFICIENT_AVAILABLE_PAPER_CASH":["ECONOMIC","7_RISK","bettor_paper_ledger order refusal: paper cash exhausted (risk/capital rail)","bettor_paper_ledger.R_INSUFFICIENT"],
  "OPEN_SHADOW_BOOK_NOT_READ":["SOFTWARE","7_RISK","bettor_external_shadow.EVALUABILITY_OF = COULD_NOT_EVALUATE","bettor_external_shadow.EVALUABILITY_OF:COULD_NOT_EVALUATE;bettor_external_shadow.STAGES:7_RISK"],
  "PAPER_RISK_REFUSED_THE_ORDER":["ECONOMIC","7_RISK","lost_opportunity.classify CONTROL; RISK attribution","agents.paper_derek.R_ORDER_REFUSED;bettor_paper_ops.REFUSAL_WORDS"],
  "RISK_GATE_BLOCKED":["ECONOMIC","7_RISK","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:7_RISK"],
  "THIS_STRATEGY_ALREADY_HAS_A_LIVE_ENTRY_ON_THIS_FIXTURE":["ECONOMIC","7_RISK","bettor_paper_ledger order refusal: one live entry per fixture (risk/capital rail)","bettor_paper_ledger.R_SAME_STRATEGY_LIVE"],
  "THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT":["ECONOMIC","7_RISK","bettor_paper_ledger order refusal: one holding per contract (risk/capital rail)","bettor_paper_ledger.R_SAME_CONTRACT_HELD"],
  "ADMISSION_NET_EV_NOT_POSITIVE":["ECONOMIC","8_ECONOMICS","actual_admission (SMALL LIVE admission): net EV not positive","actual_admission.R_NET_EV"],
  "BELOW_MIN_GROSS_EDGE":["ECONOMIC","8_ECONOMICS","lost_opportunity.classify MARKET_NO_EDGE; derek_policy DEP_NONE_MARKET","agents.derek_policy.R_BELOW;bettor_paper_ops.REFUSAL_WORDS"],
  "BELOW_MIN_NET_EV":["ECONOMIC","8_ECONOMICS","lost_opportunity.classify MARKET_NO_EDGE; derek_policy DEP_NONE_MARKET","agents.derek_policy.R_BELOW_NET;bettor_paper_ops.REFUSAL_WORDS"],
  "EDGE_BELOW_5PP":["ECONOMIC","8_ECONOMICS","bettor_paper_ops.REFUSAL_WORDS (strict benchmark edge floor); MARKET_NO_EDGE","bettor_paper_ops.REFUSAL_WORDS"],
  "ESTIMATES_DISAGREE_MODEL_BELOW_MIN_GROSS_EDGE":["ECONOMIC","8_ECONOMICS","lost_opportunity.classify MARKET_NO_EDGE; derek_policy DEP_NONE_MARKET","agents.derek_policy.R_DISAGREE;bettor_paper_ops.REFUSAL_WORDS"],
  "GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT":["ECONOMIC","8_ECONOMICS","paper_benchmark V2 fee rule: no profit after fees (MARKET_NO_EDGE)","agents.paper_benchmark.R_FEES_CONSUME_EDGE;bettor_paper_ops.REFUSAL_WORDS"],
  "NET_EV_NOT_POSITIVE_AFTER_FEES":["ECONOMIC","8_ECONOMICS","lost_opportunity.classify MARKET_NO_EDGE; derek_policy DEP_NONE_MARKET","agents.derek_policy.R_NET;bettor_paper_ops.REFUSAL_WORDS"],
  "NO_ACTION_HAS_POSITIVE_NET_EDGE":["ECONOMIC","8_ECONOMICS","bettor_external_shadow.EVALUABILITY_OF = DECIDED_ON_THE_EVIDENCE","bettor_external_shadow.EVALUABILITY_OF:DECIDED;bettor_external_shadow.STAGES:8_ECONOMICS"],
  "NO_RESTING_PRICE_BELOW_THE_ASK_CLEARS_THE_THRESHOLD_AND_FEES":["ECONOMIC","8_ECONOMICS","paper_maker: no resting price clears threshold + fee (MARKET_NO_EDGE)","agents.paper_maker.R_NO_PRICE;bettor_paper_ops.REFUSAL_WORDS"],
  "A_MAKER_ENTRY_ORDER_ALREADY_RESTS_ON_THIS_FIXTURE":["ECONOMIC","POLICY","paper_maker: one resting maker bid per fixture (policy)","agents.paper_maker.R_ALREADY_RESTING;bettor_paper_ops.REFUSAL_WORDS"],
  "ENTRY_LANE_REFUSED":["ECONOMIC","POLICY","lost_opportunity.classify CONTROL; EXPLICIT_POLICY attribution: the gated entry lane refused","agents.derek_policy.R_LANE;bettor_paper_ops.REFUSAL_WORDS"],
  "FIXTURE_NOT_SELECTED_BY_THE_EXPLORATION_SAMPLE":["ECONOMIC","POLICY","paper_explore: not drawn by the recorded exploration sample (policy)","agents.paper_explore.R_NOT_SAMPLED;bettor_paper_ops.REFUSAL_WORDS"],
  "STRATEGY_ENTRIES_DISABLED":["ECONOMIC","POLICY","lost_opportunity.classify CONTROL; EXPLICIT_POLICY attribution: the owner switched the strategy's entries off (a deliberate non-trade)","agents.paper_derek.R_ENTRIES_DISABLED;bettor_paper_ops.REFUSAL_WORDS"],
  "EXPERIMENT_NOT_ARMED":["SOFTWARE","CONFIGURATION","derek_policy.classify_lane_code -> DEP_ENGINEERING_CONFIGURATION (lane control row off)","bettor_external_shadow.R_CONTROL_OFF"],
  "EXPLORATION_LIMITS_UNREADABLE":["SOFTWARE","CONFIGURATION","paper_explore: the limits could not be read (COULD_NOT_EVALUATE analog)","agents.paper_explore.R_LIMITS_UNREADABLE;bettor_paper_ops.REFUSAL_WORDS"],
  "ODDS_CREDENTIAL_NOT_PRESENT_ON_THIS_SERVICE":["SOFTWARE","CONFIGURATION","bettor_external_shadow.R_NO_CREDENTIAL: configuration, not a market judgement","bettor_external_shadow.R_NO_CREDENTIAL"],
  "PAPER_BENCHMARK_ENVIRONMENT_FLAG_IS_NOT_ON":["SOFTWARE","CONFIGURATION","paper_benchmark enablement: environment flag off (analog EXPERIMENT_NOT_ARMED = DEP_ENGINEERING_CONFIGURATION)","agents.paper_benchmark.R_ENV_OFF"],
  "THE_PINNACLE_ONLY_PAPER_BENCHMARK_CONTROL_ROW_IS_ABSENT":["SOFTWARE","CONFIGURATION","paper_benchmark enablement: control row absent (analog EXPERIMENT_NOT_ARMED)","agents.paper_benchmark.R_CONTROL_ABSENT"],
  "THE_PINNACLE_ONLY_PAPER_BENCHMARK_CONTROL_ROW_IS_OFF":["SOFTWARE","CONFIGURATION","paper_benchmark enablement: control row off (analog EXPERIMENT_NOT_ARMED)","agents.paper_benchmark.R_CONTROL_OFF"],
  "ADMISSION_DECISION_FACTS_ABSENT":["SOFTWARE","DEFECT","actual_admission (SMALL LIVE admission): decision facts absent","actual_admission.R_FACTS_ABSENT"],
  "A_SALE_NEEDS_UNCOMMITTED_HELD_INVENTORY":["SOFTWARE","DEFECT","bettor_paper_ledger order refusal: sale without held inventory (software/record defect)","bettor_paper_ledger.R_NOT_HELD"],
  "DEREK_DECISION_NOT_RECORDED":["SOFTWARE","DEFECT","lost_opportunity.classify DEFECT_CODES","agents.derek_policy.R_NOT_RECORDED"],
  "DEREK_GATE_RAISED":["SOFTWARE","DEFECT","lost_opportunity.classify DEFECT_CODES","agents.derek_policy.R_RAISED"],
  "NOT_AN_ENTRY_DECISION_RECORD":["SOFTWARE","DEFECT","derek_policy.R_NOT_ENTRY: the record is not an entry decision","agents.derek_policy.R_NOT_ENTRY"],
  "NOT_A_PAPER_IDENTIFIER":["SOFTWARE","DEFECT","bettor_paper_ledger order refusal: not a paper identifier (software/record defect)","bettor_paper_ledger.R_NOT_PAPER"],
  "THE_FILL_EXCEEDS_THE_ORDER_REMAINDER":["SOFTWARE","DEFECT","bettor_paper_ledger order refusal: overfill refused (software/record defect)","bettor_paper_ledger.R_OVERFILL"],
  "THE_ORDER_IS_MALFORMED":["SOFTWARE","DEFECT","bettor_paper_ledger order refusal: malformed order (software/record defect)","bettor_paper_ledger.R_BAD_ORDER"],
  "THE_ORDER_IS_NOT_OPEN":["SOFTWARE","DEFECT","bettor_paper_ledger order refusal: order not open (software/record defect)","bettor_paper_ledger.R_ORDER_NOT_OPEN"],
  "THE_PAPER_ACCOUNT_DOES_NOT_EXIST":["SOFTWARE","DEFECT","bettor_paper_ledger order refusal: paper account missing (software/record defect)","bettor_paper_ledger.R_NO_ACCOUNT"],
  "9_UNCLASSIFIED_REFUSAL":["UNCLASSIFIED","9_UNCLASSIFIED_REFUSAL","bettor_external_shadow.STAGE_UNCLASSIFIED: the lane itself could not classify the refusal","bettor_external_shadow.STAGE_UNCLASSIFIED"]
  };
  // prefixes the backend itself classifies by prefix
  var PREFIXES = [
    ['PINNAPI_PRIMARY_', 'SOFTWARE', '2_FRESHNESS', 'lost_opportunity.classify CONTROL_PREFIXES PINNAPI_PRIMARY_ -> FRESHNESS', 'lost_opportunity.classify.CONTROL_PREFIXES'],
    ['MODEL_RUN_RAISED', 'SOFTWARE', 'DEFECT', 'lost_opportunity.classify DEFECT_PREFIXES MODEL_RUN_RAISED', 'lost_opportunity.classify.DEFECT_PREFIXES'],
    ['EXPERIMENT_NOT_ARMED', 'SOFTWARE', 'CONFIGURATION', 'derek_policy.classify_lane_code -> DEP_ENGINEERING_CONFIGURATION', 'agents.derek_policy.classify_lane_code']
  ];
  function normalize(code) {
    return String(code == null ? '' : code).trim().split(':')[0].trim();
  }
  function row(code, t, how) {
    return {code: code, cls: t[0], stage: t[1], basis: t[2], source: t[3], matched: how};
  }
  function classify(code) {
    var c = normalize(code);
    if (!c) { return {code: '(none)', cls: 'UNCLASSIFIED', stage: 'UNKNOWN', basis: 'no refusal code recorded', source: null, matched: 'NONE'}; }
    if (Object.prototype.hasOwnProperty.call(TABLE, c)) { return row(c, TABLE[c], 'EXACT'); }
    for (var i = 0; i < PREFIXES.length; i++) {
      if (c.indexOf(PREFIXES[i][0]) === 0) { return row(c, PREFIXES[i].slice(1), 'PREFIX'); }
    }
    return {code: c, cls: 'UNCLASSIFIED', stage: 'UNKNOWN',
            basis: 'not in the R29 (' + SOURCE_SHA + ') refusal table: shown as UNCLASSIFIED, never assumed economic',
            source: null, matched: 'UNKNOWN'};
  }
  function rows() {
    return Object.keys(TABLE).map(function (k) { return row(k, TABLE[k], 'EXACT'); });
  }
  var api = {version: 'OPS_REFUSAL_TAXONOMY_R1', sourceSha: SOURCE_SHA, classes: CLASSES,
             stages: STAGES, table: TABLE, prefixes: PREFIXES, classify: classify,
             normalize: normalize, rows: rows};
  root.BTOpsTaxonomy = api;
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
})(typeof window !== 'undefined' ? window : globalThis);
