# BETTOR integration: EV Probability Engine V1

This is research and PAPER-SHADOW work only. It reads a SELECT-only extract of
BETTOR's immutable paper records and the settled paper order record, and it
writes receipt files. It has no database, network, credential, order or
capital path, and a test enforces this. SMALL LIVE stays SHADOW. Historical
PAPER rows are never changed. No production gate is touched.

The package in `../bettor_ev_probability_engine` is unchanged. Its 14 tests
pass, and the integration adds 14 more in `tests/`.

## Inputs

- **`queries/ev_extract_v1.sql`.** A byte-identical copy of
  `research/ev_extract_v1.sql`, which ran on `claude/p0-closeout`. It returns
  every ENTER decision, plus one REFUSE per strategy × market × side ×
  15-minute bucket. Each row carries:
  - the decision's probability and source ages;
  - the linked Pinnacle valuation (provider, book, devig method, age,
    settlement rule, payout event);
  - the venue book and the closing book;
  - the premap event, sport, family and start time;
  - the registry settlement state;
  - the settled outcome.
- **The settled paper order record.** Taken from the Profitability Stack
  receipt, with its SHA recorded.

## Probabilities, oriented to the side held

| name | what it is |
|---|---|
| `MARKET_PRIOR_VENUE` | the venue's no-vig price: the mid of the PMUS book the decision saw |
| `SHARP_PRIOR_PINNACLE` | the devigged Pinnacle probability the decision recorded |
| `BETTOR_RAW` | the probability the policy used: `economics.probability`, or `p_blended` for DEREK |
| `CALIBRATED_BETA` / `_ISOTONIC` | hierarchical calibration of `BETTOR_RAW` (see below) |
| `CALIBRATED_BETA_SHRUNK` | the calibrated value shrunk toward the venue price by a learned weight |
| `MARKET_RESIDUAL` | `logit p = logit p_venue + a + alpha * (logit p_raw - logit p_venue)`; `a` and `alpha` are shrunk by their event-clustered SEs |

**Hierarchical calibration.** The fallback order is sport × family × regime,
then sport × family, then sport, then global, then the market prior. A level
is fitted only when it has at least 40 distinct events, so repeated
evaluations of one game never make a subgroup look supported.

**Internal model.** Where DEREK recorded `p_internal`, it is benchmarked on its
own (`3b_internal_model_subset`).

## Protection against leakage and holdout tuning

1. The last 20% of events, in time order, form the untouched holdout.
2. The walk-forward runs on development events only. Each fold trains only
   on events whose outcome was settled before the fold's first test
   decision. If too few are settled, the models stay at the market prior.
3. A model is selected on the pooled walk-forward out-of-sample results. That
   selection is frozen and hashed (`frozen_spec_sha256`) before the holdout
   is read.
4. The frozen choice is refit once, on development events settled before the
   holdout begins, and evaluated once on the holdout.

A test checks that flipping every holdout outcome leaves the frozen
selection unchanged.

## Reproduce

```
python -I bettor_integration/run.py receipts/<new dir> --extract <log> --extract-run <id> \
   --orders <orders.jsonl.gz> --orders-source <text> --generated-at <iso>
```

The runner refuses an existing directory, so receipts are never overwritten.
Given the same inputs, it writes byte-identical files.
