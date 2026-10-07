# Current BETTOR integration map

This pack is not a replacement trading stack. Bind each component into existing
BETTOR evidence/readiness paths.

## Golden Market Validation
Bind against:
- `sportsassets/kalshi_mapping.py`
- `sportsassets/kalshi_catalogue.py`
- `sportsassets/venue_selection.py`
- `sportsassets/agents/adriana_arb.py`
- `sportsassets/market_plane/settlement.py`
- current Polymarket Market Plane mapping

Captured source fixtures already present in the repo:
- `backend/tests/fixtures/pmus_nfl_listing_2026_10_04.json`
- `backend/tests/fixtures/pmus_line_listings_2026_10_04.json`
- `backend/tests/fixtures/pmus_soccer_board_2026_09_25.json`

Kalshi semantic cases are frozen integration expectations, not falsely labelled
as captured production Kalshi payloads. When the Kalshi rep returns real
payloads, append them as a new source class and preserve these regression cases.

## Forward Profitability Scoreboard
Bind against:
- `bettor_paper_profitability_bind.py`
- `bettor_paper_profitability_stack.py`
- `paper_loss_attribution.py`
- `api/command_profitability.py`
- `api/command_profitability_os.py`
- `api/command_profitability_scoreboard.py`
- Adriana append-only opportunity/refusal evidence
- agent scorecards / Xavier frozen counterfactuals

Do not create a second accounting ledger. The scoreboard is a read/evidence
layer over existing truth.

## Final PM Acceptance Harness
Bind/read from:
- exact-SHA GitHub Actions gates
- Render deploy/event/metrics receipts
- `api/command_capital_readiness.py`
- `api/command_canary.py`
- `api/command_market_plane.py`
- `api/command_twin.py`
- Audrey reconciliation
- venue account truth
- production profitability readback

The PM state is intentionally external to Claude's prose:
RED / YELLOW / GREEN.
