# R30A NFL: measured Pinnacle outcome set and venue rules text (production, read-only)

Run 37226814972, job 111508049692, workflow `research-sql.yml`, file
`research/r30a_nfl_measured_outcome_set.sql` (claude/command-center 593a857),
sha256 `8ead0851fd947377be033849c0aa46c587be3208e94924ab2813eab5b6e7b4b3`,
production read at 2026-10-04 19:03:54.38901+00 (schema head
`224_agent_identity_memory.sql`), psql exit 0, `default_transaction_read_only=on`.

## M1 · NFL valuation rows (all time)

| rows | contracts | first_at | last_at | priced_2 | priced_3 | priced_other | with_draw_key | book_pinnacle | with_probability |
|---|---|---|---|---|---|---|---|---|---|
| 15 | 15 | 2026-10-04 05:34:18Z | 2026-10-04 13:28:53Z | 15 | 0 | 0 | 0 | 15 | 0 |

## M2 · per provider

| provider | outcomes_priced | expected_outcomes | n | overround_min | overround_max |
|---|---|---|---|---|---|
| the-odds-api.com/v4 | 2 | 0 | 15 | 0.02791 | 0.03830 |

## M3 · newest rows (the book's priced set as recorded, cand22 measurement field)

| id | slug | raw_odds | refusals (first 4) |
|---|---|---|---|
| 5746 | aec-nfl-lac-sea-2026-10-04 | Seattle Seahawks 1.31 / Los Angeles Chargers 3.78 | MARKET_NOT_IN_SUPPORTED_SET, VENUE_BOOK_CURRENCY_NOT_ESTABLISHED, DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE |
| 5670 | aec-nfl-mia-min-2026-10-04 | Miami Dolphins 5.24 / Minnesota Vikings 1.18 | (same) |
| 5629 | aec-nfl-den-sf-2026-10-04 | Denver Broncos 2.27 / San Francisco 49ers 1.69 | (same) |
| 5464 | aec-nfl-det-car-2026-10-04 | Detroit Lions 1.5 / Carolina Panthers 2.73 | (same) |
| 5463 | aec-nfl-gb-tb-2026-10-04 | Green Bay Packers 1.61 / Tampa Bay Buccaneers 2.43 | (same) |
| 5332 | aec-nfl-ten-bal-2026-10-04 | Baltimore Ravens 1.14 / Tennessee Titans 6.31 | (same) |
| 5331 | aec-nfl-kc-lv-2026-10-04 | Las Vegas Raiders 2.91 / Kansas City Chiefs 1.45 | (same) |
| 5330 | aec-nfl-dal-hou-2026-10-04 | Dallas Cowboys 2.34 / Houston Texans 1.66 | (same) |
| 5329 | aec-nfl-ind-was-2026-10-04 | Indianapolis Colts 1.48 / Washington Commanders 2.8 | (same) |
| 5303 | aec-nfl-ari-nyg-2026-10-04 | New York Giants 2.17 / Arizona Cardinals 1.76 | (same) |

## M4 · venue rules text persisted on NFL rows

Ten distinct md5s (one per contract: the team names and date differ). With the
`This market will settle to the winner of the <named> NFL game scheduled for <date>.`
sentence masked, every one reads identically:

> `<WINNER-OF-NAMED-GAME-ON-DATE>. Overtime is included if played. If the game ends in a tie, the market will settle to $0.50. If the game is delayed, postponed, or suspended and not rescheduled to a date within two weeks of the originally scheduled date, the market will settle to the last fair market price. Outcome sourced from NFL.`

## M5 · venue NFL catalogue, next 8 days

| sports_type | contracts | first_start | last_start | pro_bowl_rows | preseason_rows |
|---|---|---|---|---|---|
| football_team_full_game_winner | 30 | 2026-10-04 13:30Z | 2026-10-06 00:15Z | 0 | 0 |

## What this establishes (and what it does not)

- Pinnacle's NFL money line is a COMPLETE TWO-OUTCOME set (15/15, no Draw):
  the measurement `bettor_pinnacle_devig.SUPPORTED` requires before a market
  is admitted. Its de-vigged price is P(win | no tie) (the book voids a tie
  when no draw price is offered, General Rules, captured).
- The venue's NFL text is the same on every production row: overtime
  included, tie settles to $0.50, postponement / suspension not rescheduled
  in two weeks settles at the last fair market price.
- It does NOT measure how often an NFL game is postponed, suspended or
  relocated, nor the last fair market price in that branch: those stay
  UNMEASURED exceptional-settlement risk.
