# eef233b evidence-integrity review (independent, read-only), 2026-10-01 ~02:45Z
Verdict: ACCEPTABLE. No blocking, high or medium defects.
- Threshold and promotion unchanged: MIN_TRAIN_EVENTS 40 (bettor_funded_model.py:257), CANDIDATE_REFIT_MIN_NEW_EVENTS 10, promote's own >=40 check; the retry fits only when a cohort reaches 40 (derek_research.py:1188), cutoff through=at, evaluation cohort declared before fit (:906 before :923).
- No cherry-picking: append-only trigger present after 181; at most one decisive row per day; already_ran on a decided day; back-dated retry refused.
- Unreadable labels recorded as LABELS_UNREADABLE, never empty or sufficient; a retry whose read fails writes nothing.
- Paper path cannot fit or promote (FIT_DEFERRED_TO_SCHEDULED_STEP; try-lock).
- Tests genuine: 9 passed on eef233b; all 9 fail on ddd4050; the repro fails for the right reason.
- 37814ad not an ancestor (merge-base rc=1).
LOW 1: a run whose own insert fails rolls back its fit and attempt row, so the day stays open and the next call refits; nothing durable records the discarded fit.
LOW 2: one concurrency assertion counts waiting advisory locks cluster-wide (test :603-606); the other assertions prove the serialization on their own.
Earlier lenses: schema and gate-order ACCEPTABLE (LOW: the test re-runs 181 on the shared table); concurrency and paper path ACCEPTABLE (LOW: the scheduled lock has no lock_timeout).
