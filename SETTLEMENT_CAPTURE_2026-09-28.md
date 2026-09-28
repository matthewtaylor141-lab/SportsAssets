# SETTLEMENT CAPTURE — the venue side, from the authoritative publication

**Directive §2:** capture the six conditions for the named sport family and market type from the authoritative publication; preserve the exact source, retrieval time, applicable scope and the evidence supporting each interpretation; feed it through the existing comparator without changing compatibility criteria.

**Result in one line:** the authoritative publication was located and read, and it **does not publish a deterministic payout per condition**. It publishes a *delegation* plus an explicit reservation of *sole and final discretion*. That is a different blocking fact from "we have not captured it", and no further reading of this document will change it.

---

## 1 · What was retrieved, and how

| | |
|---|---|
| **Retrieval route** | `.github/workflows/fetch-docs.yml` on a GitHub runner (the session container's egress to `docs.polymarket.us`, `gateway.polymarket.us` and `sportsassets-api.onrender.com` was **measured as denied** at 2026-09-28T00:0xZ — all three returned curl exit failure, code `000`) |
| **Read-only** | no secrets, no repository writes, no venue credentials, no deploy |
| **Documents read** | 1. `https://docs.polymarket.us/sitemap.xml` — 200, full URL inventory<br>2. `https://docs.polymarket.us/trader-guide/sports-schema` — 200<br>3. `https://docs.polymarket.us/llms.txt` — 200, full documentation inventory<br>4. `https://polymarketexchange.com/` — 200<br>5. **`https://polymarketexchange.com/files/legal/latest/rulebook`** — 200, `%PDF`, the Polymarket US Rulebook |
| **Retrieval times (UTC)** | sitemap 00:06:37 · sports-schema 00:07:24 · llms.txt 00:08:23 · exchange home 00:10:23 · **rulebook 00:24:09 and 00:25:09 and 00:27:06** |
| **Workflow runs** | 36360947815 · 36360997678 · 36361062996 · 36361197403 · 36362088880 · 36362151861 · 36362269540 |
| **Reproducibility** | the PDF branch prints a `sha256` of the retrieved bytes and the page count, so any citation below can be re-verified against the same artifact |

`fetch-docs.yml` was extended to read PDFs (magic-byte detection, `pdftotext -layout`, optional `grep -E` filter with context) because the rulebook is a PDF served from a path with no extension, and an HTML-only reader returned compressed stream bytes. Two inputs added against GitHub's limit of 25; file size 3,462 bytes against the 512,000-byte cap.

---

## 2 · Applicable scope

| | |
|---|---|
| **Sport family** | `baseball` |
| **Market type** | `h2h` (full-game money line) |
| **Venue instrument** | `market_sport_type = "baseball_team_full_game_winner"` — established from `docs.polymarket.us/trader-guide/sports-schema`, Section 2 ("Full-game moneyline: `<sport>_team_full_game_winner`") |
| **Conditions required by the comparator** | the seven in `bettor_settlement_terms.CONDITIONS`: `COMPLETED_IN_REGULATION`, `DECIDED_AFTER_REGULATION`, `CALLED_AND_GRADED_WITHOUT_RESUMPTION_AFTER_THE_MINIMUM`, `STOPPED_BEFORE_THE_MINIMUM`, `SUSPENDED_AND_RESUMED_WITHIN_THE_PUBLISHED_WINDOW`, `SUSPENDED_TO_RESUME_BEYOND_THE_PUBLISHED_WINDOW`, `POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED` |
| **Which side was missing** | the **VENUE** side. The bookmaker side is captured with citations (register §A2/H1) |

---

## 3 · What the authoritative publication actually says

Verbatim from the Rulebook, **Chapter 10 — CONTRACT SPECIFICATIONS**, lines as extracted:

**10.2 Contract Specifications** (line 3967–3968)
> "Each Contract will meet such specifications, and all trading in such Contract will be subject to such procedures and requirements, **as set forth in the rules governing such Contract**."

This is a **pointer, not a specification.** The per-contract rules it delegates to are not in the rulebook. I could not locate them in:
- the `docs.polymarket.us` sitemap (618 URLs) or `llms.txt` documentation inventory — which contain instrument *schema*, fees, incentives, streaming, partner onboarding and reconciliation, and **no settlement-rules or contract-specifications page**;
- the exchange site's own link set: `/notices.html`, `/regulatory.html`, `/market-integrity.html`, `/clearing/`, `/developers.html`, `/files/legal/latest/rulebook`.

**10.3 Contract Modifications** (3972–3976, 3982–3985)
> (a) "...the Company retains the discretion to modify associated Contract Specifications, **including Settlement Date and Payout Condition at any time**." (notice per CFTC Regulation 40.6)
> (b) "If any circumstance arises that would prevent a Contract's value from being determined accurately at the Expiration Date, **including but not limited to the rescheduling or cancellation of a relevant event** or delayed data from a relevant source, the Company **may at its sole discretion adjust the Expiration Date**."

**10.4 Contract Outcome Review Process** (3992–4005)
> (a) "...the Chief Executive Officer, Chief Compliance Officer, Chief Operating Officer, Head of Markets, or their designate **may at their sole discretion** undertake a review process to evaluate circumstances that may have a material impact on reliability or transparency of the underlying event related to a Contract. Following this review, **the Company may determine the final outcome of a Contract.** The Company may also reverse the final outcome of a Contract in the case of obvious error."
> (c) "The Company has **full discretion** in reviewing markets. **Determinations made by the Company are final.**"

**10.5 Contracts Concerning Natural Persons** (4009–4021) — the **only deterministic payout rule found**
> (a) If a natural person who is the primary subject dies or is incapacitated such that the qualifying outcome can no longer occur or be objectively determined, "the Exchange **will settle affected Contracts at last traded prices** prior to such an event."
> (b) If trading was materially affected, last traded prices **before the circumstances became known**, using objective evidence and, where evidence supports different times, **the earliest**.

---

## 4 · Fed through the comparator, criteria unchanged

`bettor_settlement_terms.compare` requires, per condition, a **payout on each side**. It returns `COMPATIBLE` only when every applicable condition is stated by both sides; `silence_is_not_agreement` remains `True`. Nothing about those criteria was altered.

| Condition | Venue payout, as published | Verdict |
|---|---|---|
| `COMPLETED_IN_REGULATION` | not stated as a payout in the rulebook; delegated to per-contract rules | `VENUE_STATES_NO_RULE_FOR_THIS_CONDITION` |
| `DECIDED_AFTER_REGULATION` | not stated | `VENUE_STATES_NO_RULE_FOR_THIS_CONDITION` |
| `CALLED_AND_GRADED_…` | not stated; falls under 10.4 discretionary review | `VENUE_STATES_NO_RULE_FOR_THIS_CONDITION` |
| `STOPPED_BEFORE_THE_MINIMUM` | not stated; 10.4 discretionary review | `VENUE_STATES_NO_RULE_FOR_THIS_CONDITION` |
| `SUSPENDED_AND_RESUMED_WITHIN_…` | not stated; 10.3(b) permits adjusting the Expiration Date | `VENUE_STATES_NO_RULE_FOR_THIS_CONDITION` |
| `SUSPENDED_TO_RESUME_BEYOND_…` | not stated; 10.3(b) | `VENUE_STATES_NO_RULE_FOR_THIS_CONDITION` |
| `POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED` | **10.3(b) names rescheduling/cancellation explicitly — and the published consequence is "the Company may at its sole discretion adjust the Expiration Date", plus 10.4 "may determine the final outcome". A process, not a payout.** | `VENUE_STATES_NO_RULE_FOR_THIS_CONDITION` |

Comparator verdict: **`UNKNOWN`** on all seven. Unchanged from before the capture — **but the reason is now established rather than assumed.**

---

## 5 · What this establishes, and what it does not

**Established.** The venue's authoritative publication was located and read. For the seven conditions, it publishes (i) a delegation to per-contract rules that are not published in it and that I could not locate anywhere in its own or the API documentation's link inventory, and (ii) an explicit reservation of sole and final discretion over the final outcome. The venue side is therefore **not a capture gap that more reading closes**.

**Not established — and I am not claiming any of it:**
- **Not** that the venue is incompatible. Discretion is not a conflicting payout. `C1` remains **0**.
- **Not** that the per-contract rules do not exist. 10.2 says they do. I did not find them; that is a statement about my search, and the unchecked branch is named in §6.
- **Not** that the 464 are now classified. This capture is about the **venue side of the comparison**, not about the candidates' evidence records. The actual per-candidate census is §6 below and has not run.
- **Not** that improved refusal reporting resolved anything. It did not. Zero candidates are admissible and no candidate moved.

**A finding that cuts against us, recorded because it is material:** 10.3(a) lets the Company modify **Payout Condition at any time**, and 10.4(c) makes its determinations **final**. Even a fully captured deterministic rule would be revocable on notice. Any settlement-compatibility verdict on this venue is therefore conditional on a term the counterparty may change, which is a standing risk to disclose in the capital package rather than a one-time capture task.

---

## 6 · The two remaining branches, and their owners

| # | Branch | Owner | Route | Status |
|---|---|---|---|---|
| **B1** | Locate the per-contract "rules governing such Contract" that 10.2 delegates to | Engineering, then venue relationship | not present in the sitemap, `llms.txt`, or the exchange link set. Next: `/notices.html` rule submissions and CFTC Reg 40.2/40.6 product filings, which a DCM must publish | **OPEN** |
| **B2** | Obtain written confirmation, or observe actual settlements of these conditions | **Owner / venue relationship** — this is not an engineering step | a settled postponed or called baseball game, read through the venue's own settlement endpoint | **OPEN, blocked on funded access** |
| **B3** | Run the **actual** candidate census over the real evidence records | Engineering | `research-sql.yml` → read replica. Query written (`research/settlement_census_actual.sql`), validated against the workflow's own read-only guard, **not yet dispatched** | **READY, not run** |

**The precise dependency to disclose now:** B2 cannot be completed without a funded account observing a real settlement, and B1 may not be completable from public documents at all. Until one of them lands, settlement compatibility for these edge conditions is **not documentable**, and the honest position is that this is a property of the venue's published terms, not of our evidence collection.
