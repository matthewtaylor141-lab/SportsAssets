"""§9. THE P_FILL DATASET, AND THE SELECTION IT WOULD OTHERWISE HIDE.

Owner directive, "STOP BEFORE BUILDING §5 P_FILL LABELS" §9:

    "Once the labels are repaired, §5 should preserve: QUOTE_PRICE /
    SIDE / SIZE / QUEUE_AHEAD_AT_T0 / SPREAD / DEPTH / BOOK_IMBALANCE /
    VOLATILITY / TIME_TO_EVENT / TRADE_THROUGH_STATUS /
    TRADE_AT_PRICE_VOLUME / QUEUE_DEPLETION_EVIDENCE /
    CANCELLATION_EVIDENCE / QUEUE_PRIORITY_EVIDENCE / OUTCOME_CLASS /
    OUTCOME_IDENTIFICATION_STATUS / TIME_TO_SUPPORTED_FILL /
    ADVERSE_SELECTION_AFTER_SUPPORTED_FILL / MARKOUTS -- and the
    already-frozen FILL_SELECTION_EFFECT separately.

    Do not train only on identified rows without measuring the
    selection introduced by identification."

THE DATASET IS WRITTEN FOR A MODEL THAT DOES NOT EXIST YET, which is
why the field list is fixed before the first row rather than grown to
fit whatever the first model wanted. A schema chosen after seeing the
data is a schema chosen by the data.

────────────────────────────────────────────────────────────────────
THE THREE EVIDENCE FIELDS ARE SEPARATE BECAUSE THEY FAIL SEPARATELY.

    QUEUE_DEPLETION_EVIDENCE   how much of the queue ahead went away
    CANCELLATION_EVIDENCE      how much of that was CANCELLED, not
                               traded
    QUEUE_PRIORITY_EVIDENCE    what the venue's priority rule was

The retracted claim collapsed the first two: it read a shortfall in
traded volume as a queue that had not moved, when cancellations ahead
could have emptied it. They are three columns here so a model cannot
repeat that collapse by accident, and so a row that has the first
without the second is visibly short of a negative rather than quietly
counted as one.
────────────────────────────────────────────────────────────────────

OUTCOME_CLASS AND OUTCOME_IDENTIFICATION_STATUS ARE NOT THE SAME
COLUMN. The first is what we may assert about the quote. The second is
what kind of evidence produced it. Two rows can share
COUNTERFACTUAL_FILL_NOT_IDENTIFIED and be worth entirely different
amounts: one watched trading at its price for the whole interval
without resolving, the other saw nothing at all.

    OUTCOME_CLASS                  the label
    OUTCOME_IDENTIFICATION_STATUS  POSITIVE_SUPPORTED /
                                   NEGATIVE_SUPPORTED /
                                   INTERVAL_CENSORED / NOT_IDENTIFIED

TRAINING ON IDENTIFIED ROWS ALONE MEASURES THE WRONG THING. A quote
resolves when the tape traded through it (fast, informed markets) or
when its whole queue evolution was observable (thin, slow markets).
Neither condition is independent of whether the quote would have
filled, so the resolved subset is a SELECTED sample and a model fitted
on it estimates

    P(COUNTERFACTUAL_FILL | RESOLVABLE)   not   P(COUNTERFACTUAL_FILL)

`selection_diagnostics` measures that gap directly -- identification
rate overall and within each covariate stratum -- so the difference
between the resolved rows and the rest is a number in the record
rather than an assumption in the model. `training_contract` refuses a
fit that has not consulted it.

FILL_SELECTION_EFFECT TRAVELS SEPARATELY AND IS NOT A COLUMN. It is
the frozen weak prior on adverse selection AFTER a fill -- a different
quantity from the selection introduced by identification, measured on a
different population, with its own sha. Merging them would produce one
number that answers neither question. The row carries a pointer; the
prior is read from the registry.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER. Writing a row creates no
inventory: see §12 and `bettor_shadow_mandate.py`.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import bettor_ev_bridge as evb
from . import bettor_shadow_execution as sx

NOT_IDENTIFIED = "NOT_IDENTIFIED"

DATASET = "BETTOR_P_FILL_DATASET_V1"

# ── §9: the field list, fixed before the first row ───────────────────

FEATURE_FIELDS = (
    "QUOTE_PRICE",
    "SIDE",
    "SIZE",
    "QUEUE_AHEAD_AT_T0",
    "SPREAD",
    "DEPTH",
    "BOOK_IMBALANCE",
    "VOLATILITY",
    "TIME_TO_EVENT",
)

TAPE_FIELDS = (
    "TRADE_THROUGH_STATUS",
    "TRADE_AT_PRICE_VOLUME",
)

# Three columns, because they fail separately. See the module docstring.
QUEUE_EVIDENCE_FIELDS = (
    "QUEUE_DEPLETION_EVIDENCE",
    "CANCELLATION_EVIDENCE",
    "QUEUE_PRIORITY_EVIDENCE",
)

OUTCOME_FIELDS = (
    "OUTCOME_CLASS",
    "OUTCOME_IDENTIFICATION_STATUS",
    "TIME_TO_SUPPORTED_FILL",
    "ADVERSE_SELECTION_AFTER_SUPPORTED_FILL",
    "MARKOUTS",
)

ROW_FIELDS = (FEATURE_FIELDS + TAPE_FIELDS + QUEUE_EVIDENCE_FIELDS
              + OUTCOME_FIELDS)

WHY_THREE_QUEUE_COLUMNS = (
    "the retracted claim collapsed depletion and cancellation: it read "
    "a shortfall in traded volume as a queue that had not moved, when "
    "cancellations ahead could have emptied it. Three columns keep a "
    "row that has depletion without cancellation visibly short of a "
    "negative rather than quietly counted as one")

WHY_CLASS_AND_STATUS_ARE_TWO_COLUMNS = (
    "OUTCOME_CLASS is what we may assert; OUTCOME_IDENTIFICATION_STATUS "
    "is what kind of evidence produced it. Two rows can share "
    "COUNTERFACTUAL_FILL_NOT_IDENTIFIED and be worth entirely different "
    "amounts -- one watched trading at its price all interval without "
    "resolving, the other saw nothing at all")

# ── the frozen prior, pointed at rather than copied ──────────────────

FILL_SELECTION_EFFECT_IS_NOT_A_COLUMN = (
    "FILL_SELECTION_EFFECT is the frozen weak prior on adverse "
    "selection AFTER a fill. The selection introduced by identification "
    "is a different quantity on a different population. Merging them "
    "would produce one number that answers neither question, so the "
    "prior travels separately with its own sha")


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _s(v):
    d = _d(v)
    return str(d) if d is not None else NOT_IDENTIFIED


def row(quote: dict, outcome: dict, *, book_imbalance=None,
        volatility=None, time_to_event=None,
        time_to_supported_fill=None,
        adverse_selection_after_supported_fill=None,
        markouts=None) -> dict:
    """One training row: a sealed T0 quote joined to its labelled outcome.

    Every §9 field is present on every row. Absence is written as
    NOT_IDENTIFIED rather than omitted, because a missing key and a
    missing measurement read the same way to a model and only one of
    them is a fact about the market.
    """
    cls = outcome.get("COUNTERFACTUAL_FILL_STATUS", NOT_IDENTIFIED)
    ident = outcome.get("EVIDENCE_CLASS", NOT_IDENTIFIED)

    # §7: at-price volume is a feature, never an outcome. It is carried
    # under its own name so nothing downstream can read it as a fill.
    at_price = outcome.get("VOLUME_AT_OR_THROUGH_PRICE", NOT_IDENTIFIED)

    # Only a SUPPORTED positive may carry post-fill quantities. On every
    # other row there is no fill to be after, so they stay absent.
    positive = ident == sx.POSITIVE_SUPPORTED

    out = {
        "dataset": DATASET,
        "BOOK_SHA_AT_ARRIVAL": quote.get("BOOK_SHA_AT_ARRIVAL"),

        # ── features, all knowable at T0 ──────────────────────────────
        "QUOTE_PRICE": quote.get("PRICE", NOT_IDENTIFIED),
        "SIDE": quote.get("SIDE", NOT_IDENTIFIED),
        "SIZE": quote.get("QUANTITY", NOT_IDENTIFIED),
        "QUEUE_AHEAD_AT_T0": quote.get("QUEUE_AHEAD_AT_T0", NOT_IDENTIFIED),
        "SPREAD": quote.get("SPREAD_AT_T0", NOT_IDENTIFIED),
        "DEPTH": quote.get("DEPTH_AT_T0", NOT_IDENTIFIED),
        "BOOK_IMBALANCE": _s(book_imbalance),
        "VOLATILITY": _s(volatility),
        "TIME_TO_EVENT": _s(time_to_event),

        # ── what the tape did ────────────────────────────────────────
        "TRADE_THROUGH_STATUS": outcome.get("TRADE_THROUGH_EVIDENCE",
                                            NOT_IDENTIFIED),
        "TRADE_AT_PRICE_VOLUME": at_price,

        # ── §3: three columns, never collapsed into one ──────────────
        "QUEUE_DEPLETION_EVIDENCE": outcome.get(
            "QUEUE_DEPLETION_FROM_TRADES", NOT_IDENTIFIED),
        "CANCELLATION_EVIDENCE": outcome.get(
            "QUEUE_DEPLETION_FROM_CANCELLATIONS", NOT_IDENTIFIED),
        "QUEUE_PRIORITY_EVIDENCE": outcome.get("QUEUE_POSITION_STATUS",
                                               NOT_IDENTIFIED),
        "QUEUE_ADDITION_AHEAD": outcome.get("QUEUE_ADDITION_AHEAD",
                                            NOT_IDENTIFIED),
        "QUEUE_AHEAD_DYNAMIC_STATUS": outcome.get(
            "QUEUE_AHEAD_DYNAMIC_STATUS", NOT_IDENTIFIED),
        "HIDDEN_LIQUIDITY_STATUS": outcome.get("HIDDEN_LIQUIDITY_STATUS",
                                               NOT_IDENTIFIED),

        # ── the label, and the evidence that produced it ─────────────
        "OUTCOME_CLASS": cls,
        "OUTCOME_IDENTIFICATION_STATUS": ident,
        "TIME_TO_SUPPORTED_FILL": (_s(time_to_supported_fill) if positive
                                   else NOT_IDENTIFIED),
        "ADVERSE_SELECTION_AFTER_SUPPORTED_FILL": (
            _s(adverse_selection_after_supported_fill) if positive
            else NOT_IDENTIFIED),
        "MARKOUTS": (markouts if positive and markouts is not None
                     else NOT_IDENTIFIED),

        # ── what the row is NOT ──────────────────────────────────────
        sx.ACTUAL: NOT_IDENTIFIED,
        "neverAnActualFill": sx.NEVER_AN_ACTUAL_FILL,
        "atPriceVolumeIsNotAFill": sx.AT_PRICE_VOLUME_IS_NOT_A_FILL,
        "fillSelectionEffectIsNotAColumn":
            FILL_SELECTION_EFFECT_IS_NOT_A_COLUMN,
        "whyThreeQueueColumns": WHY_THREE_QUEUE_COLUMNS,
        "whyClassAndStatusAreTwoColumns": WHY_CLASS_AND_STATUS_ARE_TWO_COLUMNS,
    }
    # Every declared field present on every row, absence named.
    for f in ROW_FIELDS:
        out.setdefault(f, NOT_IDENTIFIED)
    return out


def counts(rows) -> dict:
    """§13's three numbers, from the evidence class rather than the label."""
    pos = neg = censored = unknown = 0
    for r in rows:
        st = r.get("OUTCOME_IDENTIFICATION_STATUS")
        if st == sx.POSITIVE_SUPPORTED:
            pos += 1
        elif st == sx.NEGATIVE_SUPPORTED:
            neg += 1
        elif st == sx.INTERVAL_CENSORED:
            censored += 1
        else:
            unknown += 1
    return {
        "P_FILL_IDENTIFIED_POSITIVES": pos,
        "P_FILL_IDENTIFIED_NEGATIVES": neg,
        "P_FILL_INTERVAL_CENSORED": censored,
        "P_FILL_NOT_IDENTIFIED": censored + unknown,
        "P_FILL_NOT_IDENTIFIED_NO_EVIDENCE": unknown,
        "rows": len(rows),
        # Censored rows are counted into NOT_IDENTIFIED because neither
        # may be asserted -- and counted separately as well, because
        # they are not worth the same to the model.
        "whyCensoredAppearsTwice": (
            "an interval-censored row is unresolved, so it belongs in "
            "P_FILL_NOT_IDENTIFIED. It is also evidence bounded by its "
            "interval, so it is reported on its own line. Neither "
            "number is a negative"),
    }


def _stratum(r):
    """A coarse, PRE-DECLARED stratification.

    Coarse on purpose: a stratification chosen after seeing which cells
    looked interesting would be the same error as a schema chosen by the
    data. Side and a three-way spread bucket, nothing cleverer.
    """
    spread = _d(r.get("SPREAD"))
    if spread is None:
        band = "SPREAD_NOT_IDENTIFIED"
    elif spread <= Decimal("0.01"):
        band = "SPREAD_TIGHT"
    elif spread <= Decimal("0.04"):
        band = "SPREAD_MID"
    else:
        band = "SPREAD_WIDE"
    return "%s|%s" % (r.get("SIDE", NOT_IDENTIFIED), band)


def selection_diagnostics(rows) -> dict:
    """MEASURE the selection identification introduces. §9's requirement.

    Identification rate overall and within each pre-declared stratum. A
    rate that varies across strata is direct evidence that the resolved
    subset is not the population, and the spread between the highest and
    lowest stratum is the size of the problem.
    """
    rows = list(rows)
    by = {}
    for r in rows:
        k = _stratum(r)
        cell = by.setdefault(k, {"rows": 0, "identified": 0})
        cell["rows"] += 1
        if r.get("OUTCOME_IDENTIFICATION_STATUS") in (
                sx.POSITIVE_SUPPORTED, sx.NEGATIVE_SUPPORTED):
            cell["identified"] += 1

    for cell in by.values():
        cell["identificationRate"] = (
            NOT_IDENTIFIED if not cell["rows"]
            else str(Decimal(cell["identified"]) / Decimal(cell["rows"])))

    ident = sum(c["identified"] for c in by.values())
    overall = (NOT_IDENTIFIED if not rows
               else str(Decimal(ident) / Decimal(len(rows))))

    numeric = [Decimal(c["identificationRate"]) for c in by.values()
               if c["identificationRate"] != NOT_IDENTIFIED and c["rows"]]
    spread = (str(max(numeric) - min(numeric)) if len(numeric) >= 2
              else NOT_IDENTIFIED)

    return {
        "rows": len(rows),
        "identified": ident,
        "IDENTIFICATION_RATE": overall,
        "byStratum": by,
        "IDENTIFICATION_RATE_SPREAD_ACROSS_STRATA": spread,
        "strataArePreDeclared": (
            "side and a three-way spread bucket, fixed before the first "
            "row. A stratification chosen after seeing which cells "
            "looked interesting would be the data choosing the test"),
        "whatASpreadMeans": (
            "identification rate varying across strata is direct "
            "evidence that the resolved subset is not the population. A "
            "model fitted on resolved rows alone then estimates "
            "P(FILL | RESOLVABLE) and carries this spread silently"),
        "FILL_SELECTION_STATUS": (
            "MEASURED_NOT_CORRECTED" if rows else NOT_IDENTIFIED),
        "notTheFrozenPrior": FILL_SELECTION_EFFECT_IS_NOT_A_COLUMN,
    }


def fill_selection_effect(root=None) -> dict:
    """The frozen prior, read from the registry. Never a row column."""
    band = evb.fill_selection_band(root)
    return {
        "FILL_SELECTION_EFFECT": band,
        "isNotAColumn": FILL_SELECTION_EFFECT_IS_NOT_A_COLUMN,
        "isADifferentQuantityFrom": (
            "the selection introduced by identification, which "
            "selection_diagnostics measures on this dataset"),
    }


def training_contract(rows=None, root=None) -> dict:
    """May a P_FILL model be fitted on these rows yet, and on which?

    Fails closed. A fit is permitted only once the selection has been
    MEASURED, and censored rows are never admitted as negatives no
    matter how the caller asks.
    """
    rows = list(rows or [])
    c = counts(rows)
    sel = selection_diagnostics(rows)
    blockers = []
    if not rows:
        blockers.append("NO_ROWS")
    if c["P_FILL_IDENTIFIED_POSITIVES"] == 0:
        blockers.append("NO_IDENTIFIED_POSITIVES")
    if c["P_FILL_IDENTIFIED_NEGATIVES"] == 0:
        blockers.append("NO_IDENTIFIED_NEGATIVES")
    if sel["IDENTIFICATION_RATE"] == NOT_IDENTIFIED:
        blockers.append("SELECTION_NOT_MEASURED")
    return {
        "mayFit": not blockers,
        "blockers": blockers,
        "counts": c,
        "selection": sel,
        "labelsAdmissibleAsPositive": [sx.POSITIVE_SUPPORTED],
        "labelsAdmissibleAsNegative": [sx.NEGATIVE_SUPPORTED],
        "labelsNeverAdmissibleAsNegative": [sx.INTERVAL_CENSORED,
                                            NOT_IDENTIFIED],
        "censoredRowsAreKept": sx.INTERVAL_CENSORED_IS_NOT_A_NEGATIVE,
        "selectionMustBeModelled": sx.IDENTIFICATION_SELECTION,
        "fillSelectionEffect": fill_selection_effect(root),
    }


def describe(root=None) -> dict:
    return {
        "dataset": DATASET,
        "rowFields": list(ROW_FIELDS),
        "featureFields": list(FEATURE_FIELDS),
        "tapeFields": list(TAPE_FIELDS),
        "queueEvidenceFields": list(QUEUE_EVIDENCE_FIELDS),
        "outcomeFields": list(OUTCOME_FIELDS),
        "whyThreeQueueColumns": WHY_THREE_QUEUE_COLUMNS,
        "whyClassAndStatusAreTwoColumns": WHY_CLASS_AND_STATUS_ARE_TWO_COLUMNS,
        "labels": list(sx.LABELS),
        "evidenceClasses": list(sx.EVIDENCE_CLASSES),
        "identificationSelection": sx.IDENTIFICATION_SELECTION,
        "fillSelectionEffect": fill_selection_effect(root),
        sx.ACTUAL: NOT_IDENTIFIED,
        "createsNoInventory": (
            "a dataset row is evidence about the venue. It is not a "
            "position, and writing one creates nothing to manage. See "
            "the shadow mandate for what may create inventory"),
    }
