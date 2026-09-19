"""CONTRACT IDENTITY: is the institutional instrument the retail market?

Owner directive 2026-09-19 22:0xZ §2: "Do not explain this as six
minutes of price movement without evidence ... Build an identity tuple
from venue-native fields, not title similarity ... Slug equality alone
is insufficient."

WHY THIS IS A GATE AND NOT A MAPPING HELPER. An experimental trade's
P&L is reconstructed against the institutional book. If the
institutional instrument is not the same economic contract the decision
was made about, every dollar of that P&L is measured against the wrong
market -- and it would look completely normal, because the slug matched.

WHAT PRODUCTION ACTUALLY SAID (run 35472190984, 2026-09-19 22:03:10Z,
instrument astatc-mls-sje-laf-2026-09-19-sh-ftts-laf):

    priceScale          "100"
    fractionalQtyScale  "100"
    payoutValue         "100"        (eventAttributes)
    productId           astatc-mls-sje-laf-2026-09-19-sh-ftts
    eventId             astatc-mls-sje-laf-2026-09-19-sh-ftts
    metadata.event_id   mls-sje-laf-2026-09-19
    outcome_strike      "laf"
    eventOutcome        EVENT_OUTCOME_MUTUALLY_EXCLUSIVE
    market_sport_type   soccer_game_second_half_first_team_to_score
    question            "Will Los Angeles FC be the first to score a
                         goal in the second half on 2026-09-19 7:30PM ET?"

THE STRUCTURAL FINDING, which is the whole reason §2 exists. The
institutional instrument is ONE OUTCOME of a MUTUALLY EXCLUSIVE set:
`-laf` IS the "Los Angeles FC scores first in the second half" outcome,
and its siblings (`-sje`, and a none/neither outcome the rules name)
are separate instruments. Our retail collector records the SAME slug
with an `outcome_leg` of `yes` AND of `no` -- it models the market as a
YES/NO binary on that outcome.

Those are not obviously the same object. "LAF yes" in a binary and
"LAF" in a mutually-exclusive set can coincide, but they need not, and
NOTHING WE HAVE YET READ PROVES THEY DO. Until a retail-native field
establishes which institutional outcome each retail leg is, this
classifies AMBIGUOUS and §4 forbids its L2 feeding execution.

A NOTE ON THE PRICE GAP, and why it is not evidence either way. At
22:03Z the institutional book was bid 0.97 (qty 1) / offer 0.99, with
the next bid down at 0.39 for 4.07 contracts. A one-lot bid at 0.97 is
not the market; the honest reading of that book is roughly 0.39 bid
against 0.99 offer -- wide and thin. Our retail read of 0.59/0.60 sits
inside that. So the "discrepancy" may be a touch artefact rather than a
different contract, and it may not. §3's simultaneous read is what
would settle it, and it has not been run.
"""

from __future__ import annotations

import hashlib
import json

# ── the verdicts, exactly as the directive names them ────────────────

EXACT_SAME_CONTRACT = "EXACT_SAME_CONTRACT"
DIFFERENT_CONTRACT = "DIFFERENT_CONTRACT"
AMBIGUOUS = "AMBIGUOUS"
NOT_IDENTIFIED = "NOT_IDENTIFIED"
VERDICTS = (EXACT_SAME_CONTRACT, DIFFERENT_CONTRACT, AMBIGUOUS,
            NOT_IDENTIFIED)

# Only ONE of these may feed execution reconstruction (§4).
EXECUTION_ELIGIBLE = frozenset({EXACT_SAME_CONTRACT})

BINDING_VERSION = "BETTOR_IDENTITY_BINDING_V1"

# The venue-native institutional fields the tuple is built from. Names,
# not titles: a title match is a coincidence, a productId match is a
# statement by the venue.
INSTITUTIONAL_FIELDS = (
    "symbol", "productId", "eventId", "eventMetadataId", "outcomeStrike",
    "eventOutcome", "marketSportType", "settlementRule", "payoutValue",
    "expiration", "eventStartTime", "priceScale", "qtyScale",
)

RETAIL_FIELDS = (
    "marketSlug", "identifier", "eventSlug", "outcomeLeg", "intent",
    "sideNorm", "question", "kind", "line",
)


class IdentityRefusal(RuntimeError):
    """An identity claim this module will not make."""


def _text(value):
    return None if value is None else str(value).strip()


def institutional_identity(instrument: dict) -> dict:
    """The venue-native identity of one institutional instrument.

    Reads the record the venue returned. Nothing is inferred from the
    symbol's shape -- the symbol is ONE field of several, precisely
    because slug equality is what we are refusing to trust.
    """
    if not isinstance(instrument, dict):
        raise IdentityRefusal("refused: an instrument that is not a record")
    meta = instrument.get("metadata") or {}
    ev = instrument.get("eventAttributes") or {}
    return {
        "symbol": _text(instrument.get("symbol")),
        "productId": _text(instrument.get("productId")),
        "eventId": _text(ev.get("eventId")),
        "eventMetadataId": _text(meta.get("event_id")),
        "outcomeStrike": _text(meta.get("outcome_strike")),
        "eventOutcome": _text(ev.get("eventOutcome")),
        "marketSportType": _text(meta.get("market_sport_type")),
        "settlementRule": _text(meta.get("instrument_rules")),
        "payoutValue": _text(ev.get("payoutValue")),
        "expiration": _text(instrument.get("expirationDate")),
        "eventStartTime": _text(meta.get("event_start_time")),
        "priceScale": _text(instrument.get("priceScale")),
        "qtyScale": _text(instrument.get("fractionalQtyScale")),
        "question": _text(ev.get("question")),
    }


def retail_identity(row: dict) -> dict:
    """The venue-native identity of one retail market row (us_premap)."""
    if not isinstance(row, dict):
        raise IdentityRefusal("refused: a retail row that is not a record")
    return {
        "marketSlug": _text(row.get("market_slug") or row.get("symbol")),
        # The token/asset id: the retail venue's OWN key for the thing
        # traded, and the only retail field that is an identifier
        # rather than a description.
        "identifier": _text(row.get("identifier")),
        "eventSlug": _text(row.get("event_slug")),
        "outcomeLeg": _text(row.get("outcome_leg") or row.get("side_norm")),
        "intent": _text(row.get("intent")),
        "sideNorm": _text(row.get("side_norm")),
        "question": _text(row.get("question")),
        "kind": _text(row.get("kind")),
        "line": _text(row.get("line")),
    }


def binding_sha(institutional: dict, retail: dict, verdict: str) -> str:
    """A hash over BOTH identities and the verdict.

    It travels on every experimental decision so that a later reader can
    tell which binding a trade was executed under. If the binding is
    ever re-derived differently, the stored sha stops matching and the
    trade is visibly from the old belief rather than silently re-read
    under the new one.
    """
    body = {"bindingVersion": BINDING_VERSION, "verdict": verdict,
            "institutional": institutional, "retail": retail}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"),
                     default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def classify(institutional: dict, retail: dict) -> dict:
    """The gate. Returns a verdict and the reasons behind it.

    THE DEFAULT IS NOT "SAME". Every path that cannot positively
    establish sameness lands on AMBIGUOUS or NOT_IDENTIFIED, and §4
    admits neither to execution. A binding that fails open is not a
    binding.
    """
    if not institutional or not retail:
        verdict, why = NOT_IDENTIFIED, ["one side of the pair is absent"]
        return _result(institutional, retail, verdict, why, [])

    agree, disagree = [], []

    inst_symbol = institutional.get("symbol")
    retail_slug = retail.get("marketSlug")
    if inst_symbol and retail_slug and inst_symbol == retail_slug:
        agree.append("symbol == marketSlug")
    elif inst_symbol and retail_slug:
        disagree.append("symbol %r != marketSlug %r"
                        % (inst_symbol, retail_slug))

    # THE CHECK THAT ACTUALLY MATTERS, and the one slug equality hides.
    # A mutually-exclusive OUTCOME instrument and a YES/NO binary LEG
    # are different objects until something says otherwise. The retail
    # leg must be bound to a named institutional outcome; `yes`/`no` is
    # not a name of one.
    outcome = (institutional.get("outcomeStrike") or "").lower()
    leg = (retail.get("outcomeLeg") or "").lower()
    exclusive = (institutional.get("eventOutcome") or "").upper().endswith(
        "MUTUALLY_EXCLUSIVE")
    if not outcome:
        disagree.append("the institutional instrument names no outcome")
    elif leg == outcome:
        agree.append("retail leg names the institutional outcome %r"
                     % outcome)
    elif exclusive and leg in ("yes", "no", "over", "under"):
        disagree.append(
            "institutional %r is one outcome of a MUTUALLY EXCLUSIVE set "
            "while the retail row is a %r leg of a binary; the mapping "
            "between them is not established by either venue's fields"
            % (outcome, leg))
    elif leg:
        disagree.append("retail leg %r does not name outcome %r"
                        % (leg, outcome))
    else:
        disagree.append("the retail row names no leg")

    for name in ("priceScale", "qtyScale", "payoutValue"):
        if not institutional.get(name):
            disagree.append("institutional %s is absent" % name)

    if disagree:
        # A disagreement about IDENTITY is DIFFERENT_CONTRACT only when
        # a venue-native key positively contradicts; an unestablished
        # mapping is AMBIGUOUS, which is refused just the same but says
        # something true about why.
        contradicted = any(d.startswith("symbol ") for d in disagree)
        verdict = DIFFERENT_CONTRACT if contradicted else AMBIGUOUS
    elif agree:
        verdict = EXACT_SAME_CONTRACT
    else:
        verdict = NOT_IDENTIFIED

    return _result(institutional, retail, verdict, disagree, agree)


def _result(institutional, retail, verdict, why, agree) -> dict:
    return {
        "bindingVersion": BINDING_VERSION,
        "verdict": verdict,
        "executionEligible": verdict in EXECUTION_ELIGIBLE,
        "agree": agree,
        "why": why,
        "institutional": institutional,
        "retail": retail,
        "identityBindingSha": binding_sha(institutional or {},
                                          retail or {}, verdict),
    }


def assert_execution_eligible(binding: dict) -> dict:
    """§4: only an exact binding may feed execution reconstruction."""
    if not isinstance(binding, dict) or not binding.get("executionEligible"):
        raise IdentityRefusal(
            "refused: identity is %s, not %s. No fuzzy mapping, no title "
            "mapping, and no slug-only mapping where the slug is not "
            "proven unique to the economic contract."
            % ((binding or {}).get("verdict", NOT_IDENTIFIED),
               EXACT_SAME_CONTRACT))
    return binding
