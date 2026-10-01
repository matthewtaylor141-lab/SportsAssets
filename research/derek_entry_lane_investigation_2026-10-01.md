# Why the Pinnacle-devig entry lane writes no ENTRY_DECISION rows (2026-10-01)

**Evidence sources.** Read-only research SQL:
- research/derek_entry_lane_investigation.sql, run 36807462544
- research/derek_entry_lane_followup.sql, run 36807676336
- research/derek_entry_lane_identity.sql, run 36807763891

Deploy history comes from render-ops `deploys` for sportsassets-api (run 36807198866). Code references are to ddd4050, the production build. Abbreviations: EPL = backend/sportsassets/workers/ext_pinnacle_loop.py; VC = bettor_venue_currency.py; SC = bettor_stream_currency.py; VNI = bettor_venue_native_identity.py.

## Headline
1. **ENTRY_DECISION rows stopped because of a deliberate, hard-coded evidentiary policy.** The cause is not a control row, a misclassification or upstream refusals. Since build ad95d69 went live (2026-09-27 19:36Z), every venue book read is refused with VENUE_BOOK_CURRENCY_NOT_ESTABLISHED. An ENTRY_DECISION row is persisted only after an admitted read.
   - While this gate stands, **no upstream repair can produce a single ENTRY_DECISION row.**
   - Every repair below raises only the CALIBRATION_ONLY (displayed-price) cohort.
2. **The 09-27 19:37Z .. 09-30 02:56Z silence has the same cause.** No writer existed for currency-refused reads until 2c97c6e went live on 09-30 at 02:52Z.
3. **Three proven defects reduce calibration evidence:**
   - venue book-read timeouts that make later candidates stale;
   - venue-native naming gaps;
   - a lost-diagnostic gap on timeouts.
4. **The 8 old unlabelled entry fixtures are not a join defect.** 7 are pre-migration-108 rows with no payout side, held on purpose. 1 is a non-binary settlement (0.485) that the code refuses to treat as a refund.

## Q1. What changed at 2026-09-27 19:36Z

### Deploys (sportsassets-api)
| live at (UTC) | commit | contains d66e89e (currency policy) | contains 2c97c6e (calibration-only writer) |
|---|---|---|---|
| 09-27 12:56 | 1d2db59 | no | no |
| **09-27 19:37** | **ad95d69** | **yes** | no |
| 09-28 03:33 / 13:04, 09-29 21:16 | f9f63d8, c3d0cfc, aca3564 | yes | no |
| **09-30 02:53** | **093168f** | yes | **yes** |
| 09-30 06:55 … 10-01 01:45 | b51378d, dff544c, e348ebf, ddd4050 | yes | yes |

### Production data
- The last ENTRY_DECISION row is id 1126 at 19:36:11Z (aec-mlb-laa-sea-2026-09-27). It was written by the outgoing 1d2db59 process, 5 s after the ad95d69 deploy began.
- L2 found **zero** valuations of any experiment between 19:37Z on 09-27 and 02:55Z on 09-30.
- The first CALIBRATION_ONLY row is id 1127 at 02:56:04Z on 09-30. Its first refusal is VENUE_BOOK_CURRENCY_NOT_ESTABLISHED.
- F4: of the 1,126 ENTRY_DECISION rows, 0 were ever admissible.

### Code path at ddd4050
- **Control row is not the cause.** `_running` (EPL:1445) reads `ingestion_state['ext_pinnacle_shadow']`, which is `true` (L0). The heartbeat state is LIVE.
- **Every read is refused.** `venue_quote` computes `currency = vc.evaluate(...)` (EPL:2830). Any verdict other than ESTABLISHED returns `ok: False, refusal: VENUE_BOOK_CURRENCY_NOT_ESTABLISHED` (EPL:2867).
- **Only M1 can establish currency.** `ESTABLISHING_MECHANISMS = (M1_LIVE_SUBSCRIPTION,)` (VC:146). M2 (a 304 revalidation) and M3 (Date minus Age) are partial and can never admit (VC:150). M2 was withdrawn on 09-27 with evidence (VC:154).
- **M1 cannot be satisfied today, for two independent reasons.**
  - (a) `P5_DOCUMENTED_TIMING: available False` (SC:431). That makes `M1_STATUS = M1_NOT_AVAILABLE_ON_THIS_FEED` (SC:522-526). `evidence_for` therefore returns `subscription: None` (SC:787-830). This is hard-coded: only a venue-published timing contract changes it.
  - (b) The decision-process subscription is off. The heartbeat's `market_subscription` reads `DISABLED_BY_CONFIGURATION` ("BETTOR_MARKET_SUBSCRIPTION is not set to on") with `M1_SUBSCRIPTION_NOT_RUNNING: 13`.
- **Refused reads only become calibration rows.** After a currency refusal on a read book, `_calibration_only_basis` (EPL:2538, used at :7814) runs the same evaluate/persist path, sealed CALIBRATION_ONLY (`CALIBRATION_ONLY_AFTER`, EPL:1408). No ENTRY row is ever written.
- **The code says so itself.** `book_currency_evidence` (EPL:4609) states "every venue read reaches BOOK_CURRENCY_NOT_ESTABLISHED and refuses".
- **Some reads are positively refused.** Some are now VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT (11 on 09-30, 1 on 10-01): the origin's Date minus Age places the payload outside 30 s, which is a cached response.

**Conclusion: FACT (policy), not a defect.** d66e89e deliberately made currency something that must be established, and it cannot be established until the venue publishes a timing contract (P5) **and** the market-data subscription is running. Code alone cannot turn the entry lane back on without either replacing the evidentiary standard (an owner decision) or evidence from the venue. The upstream refusals in the heartbeat decide *which* events reach the book read. They never decide whether an ENTRY row is written.

## Q2. Each refusal class: defect or fact
Sources: the latest durable cycle (L7, cycle 2937d5cb…, 55 events), the latest heartbeat (L5/L6), and `ext_candidate_outcomes` history (09-29 21:18Z onward; L3/L4).

| class | latest cycle | 09-30 rows / distinct events | verdict |
|---|---|---|---|
| NO_PINNACLE_ON_EVENT | 33 | 1,955 / 45 | **FACT**: provider coverage |
| QUOTE_STALE_ON_ARRIVAL | 8 | 448 / 25 | **mixed**: 43% provider, **57% ours (DEFECT)** |
| VENUE_BOOK_CURRENCY_NOT_ESTABLISHED | 4–5 | 705 / 26 | **FACT** (policy, Q1) |
| VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM | 5 | 441 / 8 | **DEFECT** (coverage): naming gaps |
| NO_VENUE_CONTRACT_FOR_EVENT | 5 | 725 / 26 | mostly the global-catalogue first code before the venue-native fallback; the residue is the ONE_TEAM gaps plus NO_VENUE_NATIVE_EVENT_FOR_FIXTURE (fact) |
| VENUE_BOOK_READ_FAILED | 2–5 | 147 / 24 | **DEFECT** (ours, cause partly unproven) |

### NO_PINNACLE_ON_EVENT: FACT
- `pinnacle_h2h` (EPL:1734-1748) returns None only when the provider payload has no `pinnacle` bookmaker or no `h2h` market. EPL:7406-7411 then refuses.
- The refused events are provider coverage gaps:
  - Serie B fixtures 5–8 days out (Ponte Preta–Juventude 10-07, Ceará–Criciuma 10-08, …);
  - minor Nations League ties (Malta–Gibraltar, Faroe Islands–Slovakia, Kazakhstan–Moldova, …);
  - MLB games already in play (NYY–BOS 10-01 00:15, CHC–SD 10-01 02:00) or not yet priced (CLE–CWS 10-03).
- Several events move from this code to a priced state as kick-off nears (Spain–Czech Republic, Croatia–England, Wales–Denmark, Greece–Germany all appear in both lists).
- No code defect was found.

### QUOTE_STALE_ON_ARRIVAL: mixed (provider FACT plus our DEFECT)
- Lever A (EPL:7578) refuses when `arrival − provider last_update > 30 s`, before the venue read. It splits the cause using `received_at`.
- **Durable ledger (L8):**
  - 09-30: 448 stale-on-arrival; 191 had provider lag alone over 30 s (provider); **257 were within the limit at receipt and pushed over by our processing**.
  - 10-01 so far: 15 provider, **24 ours**.
- **Latest heartbeat `odds_freshness`:** `stale_on_arrival_due_to_our_processing 7`, `provider_stale_on_arrival 1`, median provider lag 13.7 s, median our processing 22.1 s (max 32.1 s).
- **Examples, cycle 02:25Z:**
  - São Bernardo–CRB (`atc-brb-ber-crb-2026-10-02-ber`): provider lag 2.0 s, ours 31.9 s.
  - Botafogo-SP–Vila Nova: 2.0 s / 32.1 s.
  - France–Italy (`atc-unl-fra-ita-2026-10-02-fra`): 13.7 s / 22.0 s.
- **Provider examples, cycle 02:45Z:** six Serie B events at provider lag 33.5 s; CHC–SD at 651.9 s.
- **Mechanism (proven from L7).** Our processing grows by about 10 s exactly after each VENUE_BOOK_READ_FAILED.
  - Nations League, cycle 02:45Z: q3 Greece–Germany read timed out; q4 our_processing jumped from 1.7 s to 11.9 s; q7–q9 then went stale at 18.1–18.5 s on a 14.3 s provider lag.
  - Serie B, cycle 02:25Z: three timeouts (nov-goi, cui-pop, ava-csc) precede the 31.9 s our-processing figures.
- See the next subsection for the fix.

### VENUE_BOOK_READ_FAILED: DEFECT (ours; root cause of each timeout unproven)
- All 5 in the latest heartbeat's `venue_errors` are `book read failed: TimeoutError`, `stage BOOK_READ_AWAIT`, `endpoint markets.book`. Examples: `atc-unl-wal-den-2026-10-04-wal`, `atc-unl-ger-srb-2026-10-01-ger`, `atc-brb-nov-goi-2026-10-01-nov`.
- They are intermittent per slug: wal-den was read successfully in the next cycle.
- **The timeout is our own 10 s bound.** `asyncio.wait_for(asyncio.to_thread(_read_book_blocking, slug), timeout=VENUE_TIMEOUT_S)` (EPL:2663-2665; `VENUE_TIMEOUT_S = 10.0` at EPL:1018).
- **No deadline is passed to the read.** `venue_quote` does not pass a deadline to `_read_book_blocking` (it accepts `deadline_epoch_s`, EPL:1851). So the request gate treats the read as having no deadline, and it may hold the thread for up to `MAX_UNDEADLINED_WAIT_S = 20.0` (venue_request_gate.py:71) for a pacing or cooldown hold. That wait is longer than the 10 s outer bound, so a gate hold surfaces as an anonymous TimeoutError.
- **Two consequences:**
  - (i) The per-read accounting (`grt.read_state`), which would say whether the 10 s was spent waiting in our gate or on the network, is lost: the thread's result is discarded on timeout.
  - (ii) Every timeout adds 10 s to every later candidate in the fetch. This is the our-processing staleness above.
- Whether the time is venue latency or our pacer/cooldown queue (shared with the concurrently running servicing task) cannot be decided from stored data. That is itself part of the defect.
- **Proposed minimal fix.** In `venue_quote`, compute the candidate's remaining freshness budget `deadline = provider_epoch + PINNACLE_MAX_AGE_S`, then:
  - pass `deadline_epoch_s=deadline` into `_read_book_blocking`, so the gate refuses by name before dispatch (`DECISION_DEADLINE_PASSED_BEFORE_DISPATCH` / `VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE`) instead of sleeping;
  - bound the outer `wait_for` by `min(VENUE_TIMEOUT_S, deadline − now)`;
  - on TimeoutError, attach `grt.read_state(read_id)` to the diagnostic.

  No limit changes: a read that cannot finish before the 30 s rule expires could never have been admitted anyway.
- **Test:** stub `_read_book_blocking` to sleep 11 s for slug A. Assert that (a) A is refused with a named deadline refusal within the remaining budget, not after 10 s; (b) the next candidate's `our_processing_s` does not include A's 10 s; (c) the diagnostic carries the request accounting.

### VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM: DEFECT (identity coverage; refusals are correct per the module's no-alias rule)
- `match_event` (VNI:422-560) requires one-to-one containment plus a shared distinctive token for **both** teams (`same_team`, VNI:291). With zero full matches and at least one partial match, it refuses R_ONE_TEAM_ONLY (VNI:88, VNI:507).
- In every one of the 8 distinct events refused in the ledger (L9), the venue lists the fixture at the **same instant** (offset 0), in the right league, under its own spelling (I1):

| provider fixture | venue event | why it fails |
|---|---|---|
| Belgium – Turkey | unl-bel-tur-2026-10-02 (belgium, turkiye) | "turkey" ≠ "turkiye" |
| Spain – Czech Republic | unl-esp-cze-2026-10-03 (spain, czechia) | "czech republic" ≠ "czechia" |
| Benfica – Bayern Munich | uwcl-slb-fbm-2026-09-30 (sport lisboa e benfica, fc bayern munchen) | "munich" ≠ "munchen" |
| Lyon – Chelsea | uwcl-oly-che-2026-09-30 (ol lyonnes, chelsea fc) | "lyon" ≠ "lyonnes" |
| Austria Wien – Inter Milan | uwcl-fka-fim-2026-10-01 (fk austria wien, fc internazionale milano) | "inter milan" ≠ "internazionale milano" |
| Juventude – Operario PR | brb-juv-ope-2026-10-02 (ec juventude, operario ferroviario ec) | "pr" vs "ferroviario" |
| Fortaleza – Nautico PE | brb-fec-nau-2026-10-02 (fortaleza ec, clube nautico capibaribe) | "pe" vs "capibaribe" |
| Athletic Club (MG) – Sport Recife | brb-ath-rec-2026-10-01 (athletic club, sc recife) | "athletic" is generic; refused **by design** (VNI:206) |

- These events were refused 46–65 times each over 09-30..10-01.
- **Proposed minimal fix.** An explicit, reviewed alias table in VNI: (provider competition key, provider name) → venue `team_name`. Each entry cites venue-native evidence (the venue's own event slug and abbreviation, e.g. `unl-bel-tur` / `tur`). Consult it only as an additional rendering inside `same_team`.
  - Every other rule stays: both teams, the 90-minute window, a single event, the league token, realism, period. The generic-token rule stays, so Athletic Club (MG) remains refused unless its entry is accepted on review.
- **Test:** `match_event(home="Belgium", away="Turkey", …, rows=[unl-bel-tur rows])`. Assert it refuses without the alias, matches with it, and that the alias cannot produce a match in another competition's league or against a third team.

### NO_VENUE_CONTRACT_FOR_EVENT: mostly not a terminal refusal
- It is the global-catalogue code. The venue-native fallback (8074042; EPL:1299, :1322, :7451/:7492) replaces it when it finds a match; in L7, 15 of 55 events carry `mapped_by VENUE_NATIVE` with the replaced global code recorded (mostly NO_VENUE_CONTRACT_FOR_EVENT).
- It is terminal only beside the ONE_TEAM gaps above (defect) or NO_VENUE_NATIVE_EVENT_FOR_FIXTURE. The latter is a fact: the venue does not list the fixture, e.g. Greece–Germany or Wales–Denmark before listing.

### VENUE_BOOK_CURRENCY_NOT_ESTABLISHED: FACT (policy, Q1)
- Each instance is still recorded as a CALIBRATION_ONLY valuation (heartbeat `calibration_only.recorded 4`; ids 1840–1843: `aec-mlb-phi-atl-2026-10-01`, `atc-unl-wal-nor-2026-10-01-wal`, `atc-unl-gre-ger-2026-10-04-gre`, `atc-brb-lon-cri-2026-10-02-lon`).

## Q3. No valuations 09-28 00:00Z .. 09-30 02:56Z
- **Conclusion: FACT (the consequence of the Q1 policy plus deploy timing), not an outage.**
- The window actually starts at **09-27 19:37Z**; the 09-28 00:00 boundary was a day-bucket artefact (L1a: rows every hour through 19:36Z; L2: zero rows after).
- **Cause.** ad95d69 (live 19:37Z on 09-27) made every read refuse on currency. The only writer of `external_valuations` ran after an admitted read. The calibration-only writer (2c97c6e, merged 09-30 00:12Z) went live in 093168f at 02:53Z, and rows resumed at 02:56Z.
- **The loop was running throughout:**
  - three deploys in the window (f9f63d8, c3d0cfc, aca3564) all carried the policy without the writer;
  - 2c97c6e's own message says: "The calibration cohort stopped growing on 2026-09-27 … since d66e89e every read refuses … so no row was written";
  - `ext_candidate_outcomes` (deployed with aca3564) shows 9 cycles on 09-29 from 21:18Z, all REFUSED.
- **Separate gap.** The ledger has no cycles between 09-30 20:54Z and 10-01 01:30Z (the database outage), with 71 cycles on 09-30 otherwise.

## Q4. The 8 unlabelled ENTRY_DECISION fixtures
The join is `join_outcomes` (EPL:3328). It runs every cycle and takes 60 rows, ordered never-asked first (`UNJOINED_SQL`, EPL:3075). L11b shows reads every hour, and L11 shows all 19 unlabelled entry rows were asked (last asks 09-30 18:40–20:55Z). So the join did not skip them; it classified them.

- **7 fixtures from 09-24 14:23Z** (az-col, cin-atl, cws-kc, mia-chc, mil-phi, nym-tex, stl-pit; plus hou-ath and sd-lad, which have no Pinnacle price):
  - `buy_intent` and `ladder_side` are NULL (F2: 9 rows, all on 09-24, none after).
  - `venue_side_of_our_exposure` returns None, so `outcome_from_settlement` returns `VENUE_SIDE_IDENTITY_NOT_ESTABLISHED` before reading the venue (EPL:3165-3204). Their `settlement_read` and `side_map` are empty.
  - These rows predate migration 108 (6aefc45, 09-24 14:30Z): "Hold every pre-108 valuation". Their stored probability may describe NOT(selection), because the row never recorded the buy intent.
  - **FACT (deliberate hold).** Labelling them would require inventing the side.
- **aec-mlb-bal-nyy-2026-09-27** (10 rows, BUY_SHORT/BID; side map is established):
  - The venue settled at **0.485** (8 rows read `0.485`). The global catalogue lists the game closed and unresolved.
  - `outcome_from_settlement` classes a non-binary price with no declared void as `NEITHER_SIDE_PAID_AND_NO_REFUND_IS_ESTABLISHED` and writes nothing (EPL:3279).
  - **FACT**: a non-binary market, most likely a cancelled game on the last day of the regular season. It could never become a 0/1 label in any case.
- **Effect on eligibility: none.** All 8 also lack the price receipt (they predate `venue_clock` instrumentation, 09-26 17:40Z), so they would stay ineligible even if labelled.

## Ranked fixes that would raise eligible evidence without lowering any threshold
**ENTRY / EXECUTABLE cohort.** Only #1 moves it. Everything else is zero until #1.

1. **Establish book currency for the entry lane (owner + venue; not a code defect).**
   - Obtain the venue's written timing contract for market-data messages (P5). The request is drafted in research/evidence/VENUE_TIMING_SUPPORT_REQUEST_2026-09-29.md.
   - **and** run the decision-process subscription (`BETTOR_MARKET_SUBSCRIPTION=on` plus its credential), so M1 can hold markets CURRENT.
   - Both are required (SC:431, SC:787-830). The subscription alone changes the refusal name and admits nothing.
   - Expected effect: ENTRY_DECISION rows resume at the pre-09-27 rate of about 170–430 rows a day. They already carry the receipt (`freshness_evidence.venue_clock`), so the executable cohort grows again.
2. **Venue book-read timeouts: deadline-aware read plus preserved accounting** (Q2 fix above).
   - Removes about 57% of stale-on-arrival (257 of 448 on 09-30), lets more priced events reach the book read, and names the cause of each failed read.
   - Raises CALIBRATION_ONLY now, and ENTRY after #1.
3. **Reviewed venue-native alias table** (Q2 fix above).
   - Recovers 7 of the 8 recurring identity-refused fixtures (Athletic Club (MG) stays refused unless reviewed). That is about 7 fixtures per round of the competitions.
4. **Provider freshness: spend the existing re-fetch knob.**
   - `EVENTS_PER_ODDS_FETCH` (EPL:1002, default `MAX_PER_CYCLE` = 40, `odds_refetches 0`) can be lowered so later events in a sport get a fresh quote.
   - A credit decision (about 18–21 credits per extra fetch), not a threshold change. It targets the 43% of stale-on-arrival caused by provider lag being consumed in the queue.
5. **No action:** NO_PINNACLE_ON_EVENT (coverage), NO_VENUE_NATIVE_EVENT_FOR_FIXTURE (venue listing), the pre-108 rows and the 0.485 settlement (correctly held).
