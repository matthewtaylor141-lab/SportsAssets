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

Ten distinct md5s (one per contract: the team names and date differ). This
query grouped the UNMASKED text with `LIMIT 10`, so it showed 10 of the 15
rows only; the five others were not observed here (see W1b below, which
counts every row). With the
`This market will settle to the winner of the <named> NFL game scheduled for <date>.`
sentence masked, every one of the ten shown reads identically:

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
- The venue's NFL text is the same on every production row (15 of 15, W1b
  below): overtime included, tie settles to $0.50, postponement /
  suspension not rescheduled in two weeks settles at the last fair market
  price.
- It does NOT measure how often an NFL game is postponed, suspended or
  relocated, nor the last fair market price in that branch: those stay
  UNMEASURED exceptional-settlement risk.

---

# R30A review read (production, read-only)

## W · every row's wording, the catalogue shape, the game-day phase

Run 37231923822, job 111523310648, `research/r30a_nfl_wording_and_catalogue_shape.sql`
(claude/command-center 9277e5d), sha256
`c4ceae792bd2493991ce841fd10e290b49e6c7abcaee28e47748807097c8aabb`, read at
2026-10-04 20:23:54Z (schema head `224_agent_identity_memory.sql`), psql exit 0.

- W1a: 15 NFL valuation rows, 15 contracts, 15 with a venue rules text, 0 without.
- W1b (grouped by the MASKED wording, no limit): ONE wording, 15 rows, 15
  contracts, masked md5 `2edf6be4acd35ae8ed3cc2fce9b2e3a0`:
  > `<WINNER-OF-NAMED-GAME-ON-DATE>. Overtime is included if played. If the game ends in a tie, the market will settle to $0.50. If the game is delayed, postponed, or suspended and not rescheduled to a date within two weeks of the originally scheduled date, the market will settle to the last fair market price. Outcome sourced from NFL.`
- W2 (us_premap, current NFL rows): `football_team_full_game_winner`,
  team_league `nfl`, identifier prefix `aec-nfl`, identifier = market_slug,
  kind `side`, **line NOT blank on all 28 rows / 14 contracts**.
- W3: 28 rows, all 28 on an America/New_York game day inside the cited 2026
  regular season (2026-09-09 .. 2027-01-10; first 2026-10-04, last
  2026-10-05); 0 before, 0 after. The slug's date equals the ET game day on
  28 of 28 rows and the UTC day on only 24 (the Sunday- and Monday-night
  games).

## R · the NFL catalogue rows as persisted

Run 37232171531, job 111524036039, `research/r30a_nfl_catalogue_rows.sql`
(claude/command-center ca5632a), sha256
`620c4a82c870997dc897f7254bfb8758b4a6bc52ab2ab27124b60b9cc86a9b1c`, read at
2026-10-04 20:28:26Z. The 28 rows are held verbatim in
`backend/tests/fixtures/pmus_nfl_catalogue_rows_2026_10_04.json`. Every
row's `line` is the question's clock minutes (`... at 5:00 PM UTC?` ->
`00`; `8:05 PM` -> `05`; `12:20 AM` -> `20`), `signed` is empty.

## What the review read changes

- The census admits the NFL money line only after PROVING that line is the
  clock artifact (market_clock_artifact's side-aware proof, the one baseball
  and soccer use). Without it, every production NFL catalogue row would have
  read WINNER_WITH_UNEXPECTED_LINE and stayed unsupported for the held read
  even after the type was admitted; the first pass's tests used a
  catalogue row with no line and could not see it.
- The "same wording on every row" claim is now measured on 15 of 15 rows.
- The phase window holds every current NFL contract (W3), so the
  regular-season requirement refuses none of today's slate.
