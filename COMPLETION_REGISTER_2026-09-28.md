# Completion register — 2026-09-28

**The rule this register enforces, in the owner's words:** *"Do not mark a requirement complete because its blocker is now understood."*

My previous report broke that rule four times. It labelled priorities 2, 3, 4 and 6 **"Done"** on the strength of having diagnosed them. Diagnosis is not delivery. This register restates each at the state the evidence actually supports, and the states are defined so that "understood" cannot be written in the same column as "working".

## The states

| State | Means |
|---|---|
| **DELIVERED** | Implemented, tested, released, and its effect read back from production. |
| **IMPLEMENTED — PENDING RELEASE** | Code and tests exist and pass; not yet on the serving build, so no production readback. |
| **IMPLEMENTED — INACTIVE** | Code exists and is correct, and **it is not in force.** Changes nothing until switched on. |
| **DIAGNOSED** | The cause is established. **No repair has taken effect.** |
| **TESTED — NOT QUALIFIED** | Behaviour verified under controlled inputs. Production qualification unresolved. |
| **OPEN** | Dependencies documented. Implementation not started or not finished. |
| **OWNER DECISION** | Blocked on a decision only the owner can make. |

---

## The register

| # | Requirement | State | What is true, and what is not |
|---|---|---|---|
| **1** | Command Centre usable | **PENDING AUTHENTICATED VERIFICATION** | The verification step (`S4b`) is written and committed: desk sign-in, the minted token opening a gated read, a wrong password refused, and `healthz` agreeing nothing is unconfigured — all failing closed. **None of it has run.** The credential is still unconfigured in production and no one has signed in. Not usable until a run proves it. |
| **2** | Fair-value latency | **DIAGNOSED. Mitigation IMPLEMENTED — INACTIVE.** | The chain fails at one link and 296 of 399 candidates are stale by our own delay — established on all 1,126 rows. The knob (`EVENTS_PER_ODDS_FETCH`) defaults to current behaviour, so **the production delay is unchanged and none of the 296 candidates has been recovered.** A knob that changes nothing has repaired nothing. Operationally unverified. |
| **3** | Book evidence & servicing | **TESTED — NOT QUALIFIED** | Refusal behaviour is verified by 10 tests: no order sent, inventory untouched, reconciliation ungated, blocker named, three passes identical. **Production qualification of book currency remains unresolved.** A lane that refuses correctly is not a lane that can act. |
| **4** | Partial-exit accounting | **IMPLEMENTED — PENDING RELEASE AND READBACK** | `realised_on_sold` separates proceeds, allocated basis, both fee shares, result, residual and remaining basis; 14 tests pass with a database; three published figures corrected. **Not on the serving build. No production readback.** |
| **5** | Gate & release | **IN PROGRESS** | Matched runs with all three DSNs configured, on separate databases. Comparison instrument written before the results. Not complete. |
| **6** | Four required capabilities | **OPEN** | Dependencies are documented and sharpened, and the venue API surface is now enumerated rather than sampled. **No implementation exists for any of the four.** Documentation discovery is evidence, not integration. The count is still four and must not move on a documentation read. |
| **7** | Weekly report corrections | **DELIVERED** | Pipeline committed and refusing unsourced figures (verified by stripping a source and getting exit 2); attribution, subtotal labelling, coverage, Week 38 dual figures and the unresolved export failure all in the published edition. This one I do claim. |
| **8** | Funded approval package | **OWNER DECISION** | Limits proposed with reasoning. Two items need the owner: the credential's available options, and the pilot scope. **And the pilot cannot execute as scoped** — see §5 of this register's companion. |

---

## What changed from my previous report, item by item

| I said | Correct state |
|---|---|
| Priority 2 **"Done"** | **DIAGNOSED**; mitigation inactive; 296 candidates unrecovered |
| Priority 3 **"Done"** | **TESTED — NOT QUALIFIED** |
| Priority 4 **"Done"** | **IMPLEMENTED — PENDING RELEASE AND READBACK** |
| Priority 6 **"Done"** | **OPEN** |

Four requirements were reported complete on the strength of understanding them. The engineering underneath each is real and the measurements hold; **the completion claims were wrong**, and a register that lets "we know why" occupy the same cell as "it works" is the mechanism that made it easy.

## A second error in the same direction, corrected separately

I also reported book currency as impossible on this feed for reasons that had already been examined and withdrawn. That is not a completion-state error but the same failure of discipline — asserting a stronger conclusion than the evidence carried. It is reconciled in `M1_CLAIM_RECONCILED_2026-09-28.md`, and the correct narrower statement is: **our current M1 predicate does not qualify the production feed, because documented timing (P5) is unestablished.** Replacement authority *is* established.
