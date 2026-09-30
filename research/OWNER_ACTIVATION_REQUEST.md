# Owner activation request — the inputs only the owner can give

**Dated:** 2026-09-29; corrected 2026-09-30. The concise decision form is `research/OWNER_DECISION_FORM.md`.

**What this request does not do:** answering it does not activate capital. Funded submission stays disabled in code (`FUNDED_SUBMISSION_ENABLED`, `REAL_ORDER_SUBMISSION_ENABLED`, `FUNDED_EXIT_SUBMISSION_ENABLED` are all `False`) until the final approval in §7. That approval is **not** requested here.

**What is not in here:** no engineering questions. Every item below is a choice, a value or an act that belongs to the owner. Engineering work still in progress is listed separately at the end, so it cannot be mistaken for something you are being asked for.

**Never send:** a key, a secret or a signed URL. Send only names, numbers, yes/no answers, or redacted screenshots.

---

## 1 · Account choice

**The current registry has no funded account.** Its only row, `acct_fc2d773a2afa4851`:
- is the **shadow desk book** (modelled capital, not a venue account);
- has been paused since the 2026-09-23 identifier-collision incident, with `ACCOUNTING_UNCERTAIN`;
- stays paused, and cannot be funded.

**Choose one:**

| option | what it means | what to send |
|---|---|---|
| **A** | Fund the Polymarket US account that the API key **already deployed** in the Render API service (`PMUS_KEY_ID`) belongs to | "Account A", a short label to register it under (e.g. `pmus-main`), and a **redacted screenshot** of the venue's API-key page. The screenshot should show the key's name or creation date and any permission settings the page displays. Show at most the last 4 characters of the Key ID, and never the secret. |
| **B** | Fund a **different** Polymarket US account | "Account B" and a label. A new key for that account is then provisioned by you, following §4 (it replaces the deployed key). |

**Why this is needed:**
- **The venue cannot identify the account.** Its balances response carries no account identifier (`account_identity` returns `no_identity`), so the system cannot prove which account a key signs for. Your statement becomes the identity evidence, recorded as **your attestation**.
- **Your statement is not enough to trade.** The account becomes eligible only after all four venue reconciliations pass against that key: balances, positions, open orders and executions. A failed reconciliation writes nothing.
- **How your statement is recorded.** After §4 is in place, you register the account yourself with one authenticated call. It needs the admin token and your resolution key, and the operator is taken from the server's settings.

  ```
  POST https://sportsassets-api.onrender.com/api/admin/funded-account-onboard
  headers: X-Admin-Token: <from the Render dashboard>   X-Resolution-Key: <yours>
  body: {"account_id": "<your label>", "venue": "PMUS", "confirm": "<your label>",
         "statement": "<your label> is my Polymarket US (PMUS) account and the deployed key belongs to it"}
  ```

  - The call registers the account paused and unverified, records your statement, and runs the four reconciliations.
  - It answers with the verdict of each check.
  - It never sends an order, and it refuses the shadow desk book.
- **Why a screenshot of the key page.** The venue documents a single key type whose Orders group can place, modify and cancel. It does not say whether narrower keys exist. Your screenshot settles that. Until then, the key is treated as **able to trade**, and protected accordingly.

## 2 · Limit values — five numbers in USD, each yours to choose

**Rules for every value:**
- It must be greater than 0 and no higher than the frozen maximum. Approval can only **tighten** a rail.
- `per_order_usd` cannot exceed `capital_usd`.

No values are suggested.

| field | what it actually bounds (from `bettor_entry_execution.RAIL_TYPES`) | frozen maximum | your value |
|---|---|---|---|
| `capital_usd` | Cost basis of **every** open position in the lane, at the decision instant | 3,000 | |
| `per_order_usd` (accurate name `per_market_usd`, accepted from this release) | Cost basis of open positions on **one market** (one condition id). A per-market cap, not a per-order cap | 1,000 | |
| `event_exposure_usd` | Cost basis of open positions on **one event** (one venue event slug). Two markets on the same game count as one bet on that game | 1,000 | |
| `max_exposure_usd` | **The same measured number as `capital_usd`**: every open position is counted in full. The lower of the two binds | 1,000 | |
| `daily_loss_stop_usd` (accurate name `cumulative_loss_stop_usd`) | **Not daily.** A cumulative worst-case loss ceiling with no reset. It sums three terms: realised losses on settled positions (gains do not offset them), the mark-to-market loss on marked open positions, and the full cost of unmarked open positions (the proposed position included). Once reached, new entries are refused. Only starting a new book clears realised losses. | 1,000 | |

**How the values become enforced:**
1. An operator records them. This changes nothing that is enforced.
2. You approve them with `POST /api/admin/funded-limits/approve`, with `confirm` set to your `per_order_usd` value.
3. The system then computes the **effective limit digest** that your authorization in §3 must name.

## 3 · Authorization scope — confirm each line, strike any you do not accept

You will sign this yourself through `POST /api/admin/funded-owner-authorization`. The route requires the admin token **and** your resolution key from §4, and it takes the operator name from the server's settings, never from the request. Before you sign, `GET /api/admin/funded-owner-authorization` shows the exact account, venue and digest you would be binding to.

1. **Account:** the account chosen in §1.
   **Venue:** `PMUS` (Polymarket US, the funded class).
2. **Limits:** the effective digest of the five values approved in §2.
   Changing the account binding, or re-approving the limits with a different digest, **invalidates** this authorization automatically. You would then sign again.
3. **Sports and contracts:** the sports the lane values today. That is MLB (`baseball_mlb` is the only confirmed key) plus the soccer competitions whose venue catalogue and Pinnacle lines both resolve.
   Only full-game markets are in scope. A first-five-innings, half or quarter contract is never used as protection for a full-game position.
4. **Entries:** buy one side of one market. Allowed only when the Pinnacle-derived valuation clears the lane's frozen thresholds and every readiness gate passes.
   Orders are fill-or-kill with synchronous execution. Nothing is left resting on the book.
5. **Position management (Xavier):** from a position's first fill until it is reconciled, Xavier may act on it only by dispatching the **persisted winner** of its whole-position ranking. The actions it may rank are:
   - HOLD;
   - full exit;
   - partial reduction;
   - a protective hedge: netting on PMUS, or an indirect hedge in the **same game and period**.

   Exits and reductions are **not** checked against the entry rails, because they reduce exposure. This is current behaviour.
6. **Expiry:** your authorization lapses after the bound the writer enforces. The system's own authorization record expires after 24 hours, which is not changed. **State the lifetime you accept**; it must be finite.
7. **One policy choice — protective hedges when a limit is reached.**
   A hedge **adds** capital, so today it is refused by the same rails and loss stop as an entry. When that happens, the Xavier record names the gate that stopped it. Choose one:
   - **(i)** keep it that way (current behaviour); or
   - **(ii)** allow a hedge that lowers the position's worst-case loss even when an entry rail would refuse it.

   Choosing (ii) is a code change. It goes through a new gate and release, and it is not done without your written choice.

## 4 · Secure provisioning — two server settings only you should hold

**What they are for:**
- `FUNDED_RESOLUTION_KEY` is the owner-held second factor. It is needed to sign §3, and to resolve a lost-acknowledgement investigation.
- `FUNDED_RESOLUTION_OPERATOR` is the name you sign as.
- Neither is set in production today. Until both are, those routes answer 503 and fail closed.

**Steps:**
1. Generate the key **off-platform**, for example in a password manager: at least 32 random characters.
2. In the Render dashboard, open service `sportsassets-api` → **Environment**.
   - Live configuration, read on 2026-09-30 at 00:09Z through the read-only `render-ops api-branch-get`: the API's **autoDeploy is off** (branch `claude/session-njaewf`). The workers' autoDeploy is on. This corrects an earlier statement in this request.
   - A dashboard environment save may still offer to redeploy the API: choose **"Save only"**.
3. Add `FUNDED_RESOLUTION_KEY` = your generated value and `FUNDED_RESOLUTION_OPERATOR` = your name as you will sign.
4. Reply **"resolution credentials in place"**. Nothing else is needed.
   - The settings are cached at process start, so I then restart the API by deploying the reviewed commit through the established route: `render-ops` → `deploy-api-commit`, from ref `claude/session-njaewf`, `confirm=DO`, with a 40-hex SHA.
   - I then verify without revealing anything: `GET /api/admin/funded-investigations` must report `resolution_key_configured: true`, and a deliberately wrong `X-Resolution-Key` must get **401**, not 503.

**Do not use the `render-ops env-set` action for the key.** It prints the value's length. It keeps the value in the run's stored event payload. A malformed argument is echoed in cleartext before it is refused. It is fine only for the operator name.

**If you chose option B in §1:** provision that account's `PMUS_KEY_ID` and `PMUS_SECRET_KEY` the same way, in the same dashboard screen, then give the same reply.

**Residual risk to know about:** anyone who can put a workflow on the default branch can read environment values through the Render API key, however they were entered.

**Rotation:** repeat the steps with a new value. The old value stops working when the restart completes.

## 5 · Send the venue one question about book timing (P5)

**What is blocked and why:** book currency (P5, documented timing) is the one precondition that no engineering work, stream connection, experiment or approval can clear. Only the venue's documented answer can.

**What to do:**
- The ready-to-send text, with the exact questions and which answers would and would not count, is in `research/evidence/VENUE_TIMING_SUPPORT_REQUEST_2026-09-29.md`.
- Post it through the venue's published channel: the Discord linked from its docs footer, https://discord.gg/cA6Skf5wCk.
- Paste the reply, or a redacted screenshot of it, back here.

## 6 · One more policy choice — the public trade export

`/api/venue-export` and `/api/venue-export-raw` are **public** by your order of 2026-08-14 ("every trade on the account, served plainly"). They publish every trade of whichever account the deployed key signs for. Once that account holds funded positions, the export reveals them live. Choose one:
- **(a)** keep them public; or
- **(b)** put them behind the admin token before funding. I would update the three workflows that read them.

---

## 7 · The final approval — not requested now

When §1–§6 are answered **and** every evidence gate below holds, I will ask one yes/no question, naming:
- the exact release SHA;
- the account;
- the limit digest;
- the scope.

The question is whether to set the three submission switches to `True` in that gated release. Until you answer yes, no funded order can be sent.

## What is *not* an owner input — reconciliation, implementation and measurement I must finish

**External evidence (neither owner authorization nor engineering can clear these):**
- **P5, book-timing contract.** Cleared only by the venue's documented answer: an accepted answer to question 1 (what `transactTime` denotes) and to question 2 or 3 (every change is delivered, or a stated delay bound), as tabled in the P5 request. It must then also be matched by live stream evidence in the process that decides (P2–P4).
- **Calibration of the Pinnacle source** (`bettor_source_calibration`, unchanged since it was written):
  - The acceptance needs 300 SCORED independent fixtures.
  - The chronologically earliest third of resolved fixtures is used only to fit the baseline and is never scored, so 450 resolved fixtures are needed in total (150 fit + 300 scored).
  - Production has 53 resolved, about 36 of them scored.
  - All four conditions must hold together: at least 300 scored fixtures; Brier at or below the ceiling; beating the held-out baseline on paired squared error with a 95% interval excluding zero; mean calibration error ≤ 0.05.
  - The row must also come from the current evaluator and be at most 14 days old.
- **Pairing model (indirect hedges only).** Approval needs:
  - at least 40 labelled training fixtures;
  - then at least 40 prospective fixtures observed after the model was frozen;
  - a better event-balanced log loss than the baseline, by 0.01 or more;
  - promotion by a named approver.

  The 20-fixture cohort check is only an extra refusal on top; it does not make a model valid. The void rate needs at least 40 settled fixtures. There are zero production observations today; the observer collects them without trading.

**Reconciliation:** the account you choose must pass the four venue reconciliations. There must be no unresolved lost acknowledgement, and no settlement the venue contradicts.

**Implementation, then release:**
- merge the remaining workstreams;
- wire the common valuation (one measure for HOLD, EXIT, REDUCE and ACQUIRE) into the ranking;
- the scheduled-path demonstrations;
- the frozen gate;
- the API-only release with submissions disabled;
- the production readback.
