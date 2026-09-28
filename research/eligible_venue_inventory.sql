-- READ-ONLY. IS THERE ANY ELIGIBLE VENUE INVENTORY TO SERVICE?
--
-- Owner requirement (5): "Do not ask me to choose adoption without first
-- establishing whether any eligible venue inventory exists, its
-- ownership, basis and reconciliation."
--
-- THE PROBLEM THIS ANSWERS. The proposed pilot services existing
-- inventory and opens nothing. The funded book holds 0 intents. So the
-- pilot has work only if inventory exists SOMEWHERE ELSE that could be
-- adopted -- and "somewhere else" has to be named, owned and reconciled
-- before adoption is even a proposal.
--
-- WHAT THIS CANNOT DO, STATED FIRST. The authoritative answer is the
-- venue's own `GET /v1/portfolio/get-user-positions` for our account.
-- That needs the venue credential and an egress route, and this is a
-- read-only replica query, so THIS FILE CANNOT ESTABLISH CURRENT VENUE
-- POSITIONS. What it establishes is what OUR OWN RECORDS say we opened
-- and never saw close -- which bounds the question and identifies
-- candidates, but is not a venue confirmation.
--
-- OWNERSHIP IS THE FIRST TRAP. `positions` and `api_positions` are keyed
-- by `whale_id`: they are the MIRRORED ACCOUNTS' positions, not ours.
-- Adopting one of those into our funded book would book someone else's
-- inventory as our own. They are queried here only to show they are
-- excluded, and why.

-- ─────────────────────────────────────────────────────────────────────
-- 1 · OUR OWN ORDERS THAT FILLED AND NEVER SETTLED. The best signal our
--     records hold: cash left the account and no settlement was seen.
--     Grouped by lane so ownership is attributable, never aggregated
--     into one "we hold this" figure.
-- ─────────────────────────────────────────────────────────────────────
SELECT coalesce(lane, '(null lane)')                    AS lane,
       coalesce(venue, '(null venue)')                  AS venue,
       count(*)                                         AS unsettled_orders,
       count(*) FILTER (WHERE coalesce(filled_usd, 0) > 0)
                                                        AS with_a_fill,
       round(coalesce(sum(filled_usd), 0)::numeric, 2)  AS filled_usd_total,
       min(placed_at)                                  AS oldest,
       max(placed_at)                                  AS newest
  FROM live_orders
 WHERE settled_at IS NULL
 GROUP BY 1, 2
 ORDER BY with_a_fill DESC, unsettled_orders DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 2 · AND OF THOSE, THE ONES OLD ENOUGH THAT THEIR EVENT HAS CERTAINLY
--     PASSED. An unsettled order on a fixture from weeks ago is far more
--     likely an unrecorded settlement than live inventory, and treating
--     it as adoptable inventory would be the error this section exists
--     to prevent.
-- ─────────────────────────────────────────────────────────────────────
SELECT CASE
         WHEN placed_at < now() - INTERVAL '14 days'
              THEN 'older_than_14d__ALMOST_CERTAINLY_AN_UNRECORDED_SETTLEMENT'
         WHEN placed_at < now() - INTERVAL '3 days'
              THEN 'older_than_3d__EVENT_HAS_PASSED'
         ELSE 'within_3d__COULD_PLAUSIBLY_STILL_BE_OPEN'
       END                                              AS age_class,
       count(*)                                         AS orders,
       count(*) FILTER (WHERE coalesce(filled_usd, 0) > 0)
                                                        AS with_a_fill,
       round(coalesce(sum(filled_usd), 0)::numeric, 2)  AS filled_usd
  FROM live_orders
 WHERE settled_at IS NULL
 GROUP BY 1
 ORDER BY 1;

-- ─────────────────────────────────────────────────────────────────────
-- 3 · OWNERSHIP, MADE EXPLICIT. These two tables are keyed by whale_id
--     and are NOT ours. Counted so the exclusion is visible rather than
--     silent -- a reader must be able to see that they were considered
--     and rejected on ownership, not overlooked.
-- ─────────────────────────────────────────────────────────────────────
SELECT 'positions (mirrored accounts, NOT OURS)'        AS source,
       count(*)                                         AS rows_,
       count(DISTINCT whale_id)                         AS distinct_owners,
       count(*) FILTER (WHERE NOT resolved)             AS unresolved,
       max(updated_at)                                  AS newest
  FROM positions
UNION ALL
SELECT 'api_positions (mirrored accounts, NOT OURS)'    AS source,
       count(*)                                         AS rows_,
       count(DISTINCT whale_id)                         AS distinct_owners,
       count(*) FILTER (WHERE coalesce(size, 0) <> 0)   AS unresolved,
       max(fetched_at)                                  AS newest
  FROM api_positions;

-- ─────────────────────────────────────────────────────────────────────
-- 4 · THE FUNDED BOOK, restated here so the two sit side by side. This
--     is the only ledger the pilot's servicing path reads.
-- ─────────────────────────────────────────────────────────────────────
SELECT count(*)                                      AS funded_intents,
       count(*) FILTER (WHERE kind = 'ENTRY')        AS entries,
       count(*) FILTER (WHERE closed_at IS NULL)     AS open_,
       coalesce(sum(residual_qty), 0)                AS residual_contracts,
       count(DISTINCT account_id)                    AS accounts
  FROM bettor_funded_intents;

-- ─────────────────────────────────────────────────────────────────────
-- 5 · THE DESK ACCOUNTS THAT EXIST AT ALL, with their pause and
--     accounting state. A pilot needs a NAMED account; this is the list
--     it would have to name one from.
-- ─────────────────────────────────────────────────────────────────────
SELECT account_id, desk_id, status, paused, accounting_status,
       opening_balance, left(coalesce(note, ''), 60) AS note
  FROM bettor_desk_accounts
 ORDER BY account_id;

-- ─────────────────────────────────────────────────────────────────────
-- 6 · RECONCILIATION STATE. An adopted position must reconcile, so any
--     standing discrepancy is a precondition rather than a footnote.
-- ─────────────────────────────────────────────────────────────────────
SELECT kind,
       count(*)                                     AS rows_,
       count(*) FILTER (WHERE resolved_at IS NULL)  AS unresolved,
       max(at)                                      AS newest
  FROM bettor_funded_discrepancies
 GROUP BY kind
 ORDER BY unresolved DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 7 · AND WHAT THE VENUE'S OWN LEDGER LAST SAID ABOUT OPEN COST. The
--     venue-truth methodology holds unresolved markets "open at cost";
--     if any per-day row carries cost with nothing settled, that is a
--     pointer to inventory the venue still shows.
-- ─────────────────────────────────────────────────────────────────────
SELECT day, venue, settled, wins, losses,
       round(cost::numeric, 2)     AS cost,
       round(realized::numeric, 2) AS realized,
       updated_at
  FROM venue_truth_days
 WHERE day >= '2026-09-14'
 ORDER BY day DESC, venue;
