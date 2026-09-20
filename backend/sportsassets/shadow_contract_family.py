"""CONTRACT FAMILIES, taken from the venue's own words.

Owner production check 2026-09-20: 16 bindings, EXACT 0, ELIGIBLE 0,
AMBIGUOUS 16, every one refused with "retail leg 'yes' does not name
outcome '1.5'".

WHAT THE VENUE ACTUALLY SAYS (run 35479721534, read before anything
here was written). Every one of those 16 instruments carries

    eventAttributes.eventOutcome = "EVENT_OUTCOME_DIRECTIONAL"

while the MLS first-to-score set that the outcome-name rule was built
for carries MUTUALLY_EXCLUSIVE. THE VENUE ITSELF NAMES THE FAMILY. It
did not have to be inferred from a slug, a title or a price, and this
module reads that field rather than guessing.

WHY THE OLD RULE WAS RIGHT AND STILL IS. On a mutually exclusive set
{LAF, SJE, NEITHER} the retail leg must name ONE outcome, and `yes`
names none of them -- so `yes` beside such an instrument is genuinely
ambiguous and stays refused. That rule was never wrong; it was being
applied to a family it does not describe. On a directional instrument
there is no outcome name to match: the contract IS one proposition and
`yes` means it resolves true.

THE PROOF FOR A DIRECTIONAL CONTRACT, and why it is not slug equality.
The institutional record carries `metadata.cftc_instrument_id` -- the
REGISTERED CONTRACT ID -- and for these rows it equals both the
institutional `symbol` and the retail `market_slug`. Two venues quoting
the same registered instrument id are not two venues whose names happen
to collide; they are two venues trading one contract. On top of that
this module requires the identity to COMPOSE:

    symbol == eventId + "-" + <direction token> + "-" + <strike token>

so the period-scoped event, the direction and the strike each have to
account for their own part of the key. A slug that merely looks similar
fails that, and so does an instrument whose strike or participants
belong to a different event.

THE HAZARD FOUND WHILE READING THIS, recorded rather than smoothed
over. On asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5 the venue ships two
settlement sentences that describe OPPOSITE propositions:

  instrument_rules          "...settle to Yes if Northern Illinois,
                             after applying a +13.5 point spread,
                             outscores Arizona..."
  instrument_rules_display  "...settle to Yes if Arizona outscores
                             Northern Illinois by more than 13.5..."

With a .5 line there is no push, so those two are exact complements and
one of them is wrong for this instrument. `market_title`, `question`
and `long_participant_id` all agree with the first. NOTHING HERE BINDS
DIRECTION FROM PROSE -- the identity is the registered contract id, so
the disagreement cannot flip a side -- but it is detected and recorded
on the row, because a venue field that contradicts three others is
worth knowing about before it matters somewhere that does read it.

NO PRICES, NO TITLES, NO FUZZY MATCHING. Nothing in this module reads a
book, a mid or a P&L, and a test asserts that over its code.
"""

from __future__ import annotations

import re

# ── the families, named as the venue names them ──────────────────────

BINARY_PROPOSITION = "BINARY_PROPOSITION"
MULTI_OUTCOME_SET = "MULTI_OUTCOME_SET"
FAMILY_UNKNOWN = "FAMILY_NOT_IDENTIFIED"

# The venue's own `eventOutcome` values, observed in production
# 2026-09-20. A value absent from this map is FAMILY_NOT_IDENTIFIED and
# is refused: a family we have not seen is not a family we can classify,
# and guessing one is how a three-outcome set gets traded as a binary.
EVENT_OUTCOME_FAMILY = {
    "EVENT_OUTCOME_DIRECTIONAL": BINARY_PROPOSITION,
    "EVENT_OUTCOME_MUTUALLY_EXCLUSIVE": MULTI_OUTCOME_SET,
    # The bare form the older MLS records carried.
    "MUTUALLY_EXCLUSIVE": MULTI_OUTCOME_SET,
}

# THE RETAIL VENUE'S OWN DIRECTION TOKENS. These are part of its market
# key, not an interpretation of one: `...-1h-neg-8pt5` is the retail
# venue's name for the contract, and `neg` is a character of that name.
DIRECTION_SIGN = {"pos": 1, "neg": -1, "over": 1, "under": -1}


def _text(value):
    return None if value is None else str(value).strip()


def _num(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def strike_token(value) -> str | None:
    """The venue's spelling of a strike: 13.5 -> '13pt5', 2 -> '2'.

    Both venues spell the decimal point `pt` in their keys. This turns
    a numeric strike back into that spelling so the composition check
    can be a STRING comparison against the venue's own key rather than
    a parse of it.
    """
    n = _num(value)
    if n is None:
        return None
    n = abs(n)
    if n == int(n):
        return str(int(n))
    return ("%g" % n).replace(".", "pt")


def family_of(instrument_record) -> dict:
    """Which contract family the VENUE says this instrument belongs to."""
    rec = instrument_record if isinstance(instrument_record, dict) else {}
    ev = rec.get("eventAttributes") or {}
    declared = _text(ev.get("eventOutcome"))
    family = EVENT_OUTCOME_FAMILY.get((declared or "").upper(),
                                      FAMILY_UNKNOWN)
    return {
        "family": family,
        "eventOutcome": declared,
        "why": (None if family != FAMILY_UNKNOWN else
                "the venue's eventOutcome is %r, which is not a family "
                "this module has seen; an unrecognised family is refused "
                "rather than assumed binary" % declared),
    }


def proposition(instrument_record) -> dict:
    """The venue-native description of ONE directional proposition.

    Every field here is copied from the record. Nothing is derived from
    the question text, the market title or any price.
    """
    rec = instrument_record if isinstance(instrument_record, dict) else {}
    ev = rec.get("eventAttributes") or {}
    meta = rec.get("metadata") or {}
    return {
        "symbol": _text(rec.get("symbol")),
        # THE REGISTERED CONTRACT ID. This is the identity claim that
        # matters: a CFTC instrument id is not a display name.
        "cftcInstrumentId": _text(meta.get("cftc_instrument_id")),
        "eventId": _text(ev.get("eventId")),
        "productId": _text(meta.get("product_id")
                           or meta.get("event_product_id")),
        "eventSlug": _text(meta.get("event_id")),
        "strikeValue": _text(ev.get("strikeValue")),
        "strikeUnit": _text(ev.get("strikeUnit")),
        "outcomeStrike": _text(meta.get("outcome_strike")),
        "evaluationType": _text(ev.get("evaluationType")),
        "calculationMethod": _text(ev.get("calculationMethod")),
        "payoutValue": _text(ev.get("payoutValue")),
        "outcomeType": _text(meta.get("outcome_type")),
        "marketSportType": _text(meta.get("market_sport_type")),
        "longParticipantId": _text(meta.get("long_participant_id")),
        "shortParticipantId": _text(meta.get("short_participant_id")),
        # DISPLAY NAMES, carried for the prose-conflict detector ONLY.
        # No binding decision reads them: a team name is display text
        # and `long_participant_id` is the identifier beside it.
        "longParticipantName": _text(meta.get("long_participant_name")),
        "shortParticipantName": _text(meta.get("short_participant_name")),
        "settlementRule": _text(meta.get("instrument_rules")),
        "settlementRuleDisplay": _text(meta.get("instrument_rules_display")),
        "priceScale": _text(rec.get("priceScale")),
        "qtyScale": _text(rec.get("fractionalQtyScale")),
        "state": _text(rec.get("state")),
    }


# A settlement rule for a binary proposition says what makes it settle
# YES. The words are the venue's, and their PRESENCE is what is
# checked -- never their meaning, which is prose.
_SETTLES_YES = re.compile(r"settle\s+to\s+yes\s+if", re.I)


def settlement_is_binary_yes(prop: dict) -> bool:
    rule = (prop or {}).get("settlementRule") or ""
    return bool(_SETTLES_YES.search(rule))


def settlement_prose_conflict(prop: dict) -> str | None:
    """Do the venue's two settlement sentences name opposite sides?

    DETECTED, NOT ACTED ON. The binding's identity comes from the
    registered contract id, so a disagreement between two prose fields
    cannot flip a side here. It is recorded because a venue field that
    contradicts three others is a fact about the feed, and because the
    day something else starts reading that field this row will already
    say it was wrong.

    The test is deliberately crude and only fires on the clear case:
    both sentences settle to Yes, and the participant named FIRST in
    each is a different one.
    """
    prop = prop or {}
    a = prop.get("settlementRule") or ""
    b = prop.get("settlementRuleDisplay") or ""
    long_name = prop.get("longParticipantId")
    if not a or not b or not long_name:
        return None
    if not (_SETTLES_YES.search(a) and _SETTLES_YES.search(b)):
        return None

    def first_named(text):
        tail = _SETTLES_YES.split(text, 1)
        return (tail[1] if len(tail) > 1 else "").strip().lower()[:60]

    first_a, first_b = first_named(a), first_named(b)
    if not first_a or not first_b:
        return None
    # The short participant's name leading the DISPLAY sentence while
    # the long participant leads the canonical one is the inversion
    # seen on asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5.
    short_name = (prop.get("shortParticipantName") or "").lower()
    if short_name and first_b.startswith(short_name) \
            and not first_a.startswith(short_name):
        return ("instrument_rules and instrument_rules_display name "
                "opposite propositions; identity here is the registered "
                "contract id, so no side is taken from either sentence")
    if first_a[:20] != first_b[:20]:
        return ("instrument_rules and instrument_rules_display begin "
                "differently (%r vs %r); identity here is the registered "
                "contract id, so no side is taken from either sentence"
                % (first_a[:24], first_b[:24]))
    return None


def retail_direction(market_slug) -> dict:
    """The direction and strike tokens IN THE RETAIL VENUE'S OWN KEY.

    `asc-cfb-byu-colst-2026-09-19-1h-neg-8pt5` ends in `neg-8pt5`. That
    is not an interpretation of the slug; those are two characters of
    the retail venue's name for the contract, and the institutional
    symbol is the same string.
    """
    parts = [p for p in str(market_slug or "").lower().split("-") if p]
    if len(parts) < 2:
        return {"direction": None, "sign": None, "strikeToken": None}
    token, direction = parts[-1], parts[-2]
    sign = DIRECTION_SIGN.get(direction)
    if sign is None:
        return {"direction": None, "sign": None, "strikeToken": None}
    return {"direction": direction, "sign": sign, "strikeToken": token}


def compose_check(prop: dict, market_slug) -> dict:
    """Does eventId + direction + strike REBUILD the instrument's key?

    THE CHECK THAT MAKES THIS MORE THAN SLUG EQUALITY. Each part of the
    key has to be accounted for by a field the venue published
    separately: the period-scoped event id, the direction token and the
    strike. An instrument whose strike belongs to another line, or
    whose event is another period, cannot compose.
    """
    prop = prop or {}
    got = retail_direction(market_slug)
    event_id = prop.get("eventId")
    token = strike_token(prop.get("strikeValue")
                         or prop.get("outcomeStrike"))
    if not (event_id and got["direction"] and token):
        return {"composes": False, "expected": None, "got": market_slug,
                "why": "the venue supplied no eventId, direction or strike "
                       "to compose from"}
    expected = "%s-%s-%s" % (event_id, got["direction"], token)
    return {"composes": expected == str(market_slug or "").lower(),
            "expected": expected, "got": _text(market_slug),
            "why": (None if expected == str(market_slug or "").lower()
                    else "the venue's own eventId, direction and strike "
                         "compose to %r, which is not this market's key"
                         % expected)}


def participants_match(prop: dict, event_slug) -> bool:
    """Do BOTH named participants appear in the event's own key?

    `long_participant_id` cfb-nill and `short_participant_id` cfb-arz
    against event `cfb-nill-arz-2026-09-19`. The team CODES are venue
    identifiers; the team NAMES beside them are display text and are
    not used.
    """
    slug = str(event_slug or "").lower()
    if not slug:
        return False
    for side in ("longParticipantId", "shortParticipantId"):
        pid = (prop or {}).get(side)
        if not pid:
            return False
        code = str(pid).lower().rsplit("-", 1)[-1]
        if not code or code not in slug:
            return False
    return True


def strike_agrees(prop: dict, market_slug, retail_line) -> dict:
    """Do both venues name the same SIGNED strike?

    The retail row's `line` carries the MAGNITUDE and its slug carries
    the sign; the institutional `outcome_strike` carries both. Comparing
    only the magnitudes would call -8.5 and +8.5 the same contract, and
    they are opposite sides of the same game.
    """
    got = retail_direction(market_slug)
    inst = _num((prop or {}).get("outcomeStrike"))
    declared = _num((prop or {}).get("strikeValue"))
    line = _num(retail_line)

    if inst is None or got["sign"] is None:
        return {"agrees": False,
                "why": "no signed institutional strike to compare"}
    # The venue's own two spellings of its own strike must agree first.
    if declared is not None and abs(declared) != abs(inst):
        return {"agrees": False,
                "why": "the venue's strikeValue %s and outcome_strike %s "
                       "disagree" % (declared, inst)}
    if (inst >= 0) != (got["sign"] > 0):
        return {"agrees": False,
                "why": "the retail key says %r while the institutional "
                       "strike is %s; those are opposite sides"
                       % (got["direction"], inst)}
    if line is not None and abs(line) != abs(inst):
        return {"agrees": False,
                "why": "the retail line %s and the institutional strike %s "
                       "are different lines" % (line, inst)}
    return {"agrees": True, "signedStrike": inst, "why": None}


def binary_identity(instrument_record, retail_row) -> dict:
    """PROPOSITION IDENTITY for a directional contract, or a refusal.

    Every condition is a field one of the two venues published. None of
    them is a title, a price or a similarity score, and the whole thing
    fails closed: a missing field is a refusal, not a pass.
    """
    fam = family_of(instrument_record)
    prop = proposition(instrument_record)
    rt = retail_row or {}
    slug = _text(rt.get("market_slug") or rt.get("symbol"))
    agree, why = [], []

    if fam["family"] != BINARY_PROPOSITION:
        return {"family": fam["family"], "proven": False,
                "proposition": prop, "agree": [],
                "why": [fam["why"] or
                        "the venue declares this instrument %s, so the "
                        "binary-proposition rule does not apply to it"
                        % fam["family"]]}

    # 1. ONE REGISTERED CONTRACT, quoted by both venues under its own id.
    cftc = prop.get("cftcInstrumentId")
    if not cftc:
        why.append("the instrument carries no cftc_instrument_id, so "
                   "there is no registered contract id to bind to")
    elif not (cftc == prop.get("symbol") == slug):
        why.append("cftc_instrument_id %r, symbol %r and retail slug %r "
                   "are not one registered contract"
                   % (cftc, prop.get("symbol"), slug))
    else:
        agree.append("cftc_instrument_id, institutional symbol and retail "
                     "market key are the same registered contract %r" % cftc)

    # 2. THE KEY COMPOSES from separately published parts.
    comp = compose_check(prop, slug)
    if comp["composes"]:
        agree.append("the venue's own eventId, direction and strike "
                     "compose to exactly this key (%s)" % comp["expected"])
    else:
        why.append(comp["why"] or "the instrument's key does not compose")

    # 3. BOTH VENUES NAME THE SAME SIGNED STRIKE.
    strike = strike_agrees(prop, slug, rt.get("line"))
    if strike["agrees"]:
        agree.append("retail line and institutional outcome_strike are the "
                     "same signed strike (%s)" % strike.get("signedStrike"))
    else:
        why.append(strike["why"])

    # 4. THE PARTICIPANTS ARE THIS EVENT'S.
    if participants_match(prop, rt.get("event_slug") or prop.get("eventSlug")):
        agree.append("both participant ids named by the instrument appear "
                     "in the event's own key")
    else:
        why.append("the instrument's long/short participant ids are not "
                   "both present in the event key, so the contract may "
                   "belong to another fixture")

    # 5. IT SETTLES AS A BINARY YES.
    if settlement_is_binary_yes(prop):
        agree.append("the venue's settlement rule settles this contract to "
                     "Yes on one stated condition, which is what a retail "
                     "YES leg buys")
    else:
        why.append("the venue's settlement rule does not state a binary "
                   "Yes condition, so YES has no established meaning here")

    # 6. IT CAN BE PRICED.
    for name, label in (("priceScale", "priceScale"),
                        ("qtyScale", "fractionalQtyScale"),
                        ("payoutValue", "payoutValue")):
        if not prop.get(name):
            why.append("institutional %s is absent" % label)

    conflict = settlement_prose_conflict(prop)
    return {"family": BINARY_PROPOSITION,
            "proven": not why,
            "proposition": prop,
            "agree": agree,
            "why": why,
            "settlementProseConflict": conflict,
            "composition": comp,
            "signedStrike": strike.get("signedStrike")}


def complement_symbol(prop: dict, market_slug) -> str | None:
    """The key the OPPOSITE side of this proposition would have.

    Named so the NO leg can be recorded honestly: this is the
    instrument the retail NO leg would correspond to, and until the
    institutional venue confirms it exists and is priceable the NO side
    stays blocked. "Do not manufacture a NO book as 1-YES."
    """
    got = retail_direction(market_slug)
    event_id = (prop or {}).get("eventId")
    token = strike_token((prop or {}).get("strikeValue")
                         or (prop or {}).get("outcomeStrike"))
    opposite = {"pos": "neg", "neg": "pos",
                "over": "under", "under": "over"}.get(got["direction"] or "")
    if not (event_id and token and opposite):
        return None
    return "%s-%s-%s" % (event_id, opposite, token)
