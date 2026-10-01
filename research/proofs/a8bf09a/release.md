# Release of a8bf09a (API only) and enablement of the Pinnacle-only paper benchmark
- 04:15:20Z render-ops #1826 (run 36814236519) env-set PAPER_BENCHMARK (2 chars, "on") on sportsassets-api only: HTTP 200. No other service or env group changed.
- 04:16:11Z render-ops #1827 (run 36814302578) deploy-api-commit a8bf09ad0940986999f8184865784c261ffe63ab -> sportsassets-api: HTTP 201, deploy dep-dautt2t9fdbs73acd3mg; worker deploys before = after (f5d1c05 live).
- Two-model strategy: migration 182 sets PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2 off (still records decisions; an ENTER would be REFUSE STRATEGY_ENTRIES_DISABLED). Only the benchmark may open new paper entries.
- Real-money submission remains disabled; benchmark code is unreachable from funded/order paths (isolation tests in the gate).
