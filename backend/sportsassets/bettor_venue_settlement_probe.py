"""WHAT THE VENUE ACTUALLY RETURNED, preserved before it is interpreted.

WHY THIS EXISTS. `bettor_live_read.read_resolution` reported
`CLOSED_BUT_NO_REPORTED_OR_CONVERGED_OUTCOME` for the acceptance position
and for 9 of 12 calibration fixtures, with `settlement_probe UNREADABLE` and
`settlement_error NO_SETTLEMENT_PRICE_IN_RESPONSE`. Both are OUR parser's
verdicts. They are not the venue's refusal, and I described them as if they
were. The difference matters because only one of two things can be true:

  A  the payload CARRIES authoritative payout evidence and the parser does
     not consume it -- a defect of ours, repairable, and the real payload
     belongs in the regression suite; or
  B  the payload genuinely LACKS it, in which case the missing field or
     contract has a name and the supported retrieval route has a name, and
     neither is "the venue refused".

Nothing here decides which. It captures the evidence needed to decide, with
the field TYPES beside the values, because the most likely repairable defect
is a type mismatch: `_converged_winner` requires `outcomePrices` to be a
list, and a venue that returns it as a JSON-encoded STRING would produce
exactly the symptom observed while carrying the prices all along.

── WHAT IT WILL NOT DO ───────────────────────────────────────────────

It does not infer a payout from `closed`, `resolved` or `settledAt`. Those
say the market stopped trading, not who won; a position settled on that
basis is settled on a guess. The four terminal readings stay separate:

    REPORTED_SETTLEMENT        the settlement endpoint gave a price
    CONVERGED_PRICE_INFERENCE  closed, prices exactly 1 and 0 -- OURS
    EXPLICIT_VOID              the venue declared void / stakes returned
    UNREADABLE                 none of the above is present

It writes nothing, submits nothing, and returns no credential.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

VERSION = "VENUE_SETTLEMENT_PROBE_V1"

#: The endpoints this probe reads, named so a report cannot be vague about
#: which surface answered.
EP_SETTLEMENT = "/v1/markets/{slug}/settlement"
EP_LISTING = "/v1/markets?slug={slug}"

#: Terminal readings, kept apart. `CONVERGED_PRICE_INFERENCE` is OURS and is
#: labelled as ours wherever it appears.
R_REPORTED = "REPORTED_SETTLEMENT"
R_CONVERGED = "CONVERGED_PRICE_INFERENCE"
R_VOID = "EXPLICIT_VOID"
R_UNREADABLE = "UNREADABLE"
R_PENDING = "PENDING"
R_UNMATCHED = "UNMATCHED"

#: Anything whose KEY looks like a secret is replaced, whatever its value.
#: The market listing is public metadata and no field here is expected to
#: match -- the denylist is here so that a payload which GROWS one cannot
#: leak it into a build log.
SECRET_KEY = re.compile(
    r"key|token|secret|auth|cred|signature|sign|private|password|bearer"
    r"|cookie|session|api_?id|account", re.I)

#: A long string is truncated rather than dropped: rules prose runs to
#: thousands of characters and the interesting part is the head.
MAX_STR = 400
MAX_ITEMS = 24

#: The words a venue uses when it VOIDS a market. Matched only against
#: fields that describe status or resolution, never against rules prose --
#: every rules text on this venue contains the word "void" while describing
#: the conditions under which a void WOULD occur, and matching that would
#: void every market on the book.
VOID_WORDS = ("void", "cancel", "canceled", "cancelled", "no contest",
              "stakes returned", "refund")
VOID_STATUS_FIELDS = ("status", "ep3Status", "resolutionStatus",
                      "settlementStatus", "state")


def _redact(value, *, depth=0):
    """A JSON-safe, size-bounded copy, with secret-looking keys removed."""
    if depth > 6:
        return "<DEPTH_LIMIT>"
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:64]:
            ks = str(k)
            out[ks] = ("<REDACTED_BY_KEY_NAME>" if SECRET_KEY.search(ks)
                       else _redact(v, depth=depth + 1))
        return out
    if isinstance(value, (list, tuple)):
        head = [_redact(v, depth=depth + 1) for v in list(value)[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            head.append("<%d_MORE_ITEMS>" % (len(value) - MAX_ITEMS))
        return head
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else (
            value[:MAX_STR] + "<TRUNCATED_%d_CHARS>" % (len(value) - MAX_STR))
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return "<%s>" % type(value).__name__


def _shapes_of(d) -> dict:
    """Field shapes for a mapping, with secret-looking keys masked.

    `_shape` emits no values, but it does emit a string's LENGTH, and a
    shape map built straight from `.items()` would put a secret-looking key
    name into a build log beside the length of its value. Cheap to close and
    pointless to leave open.
    """
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        ks = str(k)
        out[ks] = ({"type": "<REDACTED_BY_KEY_NAME>"}
                   if SECRET_KEY.search(ks) else _shape(v))
    return out


def _shape(value):
    """The TYPE, and for a container its length and its members' types.

    This is the half of the evidence that decides between a parser defect
    and an absent field. `outcomePrices` as `list[str]` is consumable;
    `outcomePrices` as `str` is the same data behind one `json.loads`, and
    `_converged_winner` rejects it on the isinstance check before ever
    looking at a number.
    """
    t = type(value).__name__
    out = {"type": t}
    if isinstance(value, str):
        out["len"] = len(value)
        s = value.strip()
        # A JSON-encoded container inside a string is the specific defect
        # worth naming, so it is detected and REPORTED, not silently parsed.
        if s[:1] in "[{" and s[-1:] in "]}":
            out["looks_like_json_encoded"] = True
            try:
                import json as _j

                inner = _j.loads(s)
                out["decoded_type"] = type(inner).__name__
                if isinstance(inner, (list, tuple)):
                    out["decoded_len"] = len(inner)
                    out["decoded_member_types"] = sorted(
                        {type(x).__name__ for x in inner})
                    out["decoded_preview"] = _redact(inner)
            except Exception as exc:                           # noqa: BLE001
                out["decode_failed"] = type(exc).__name__
    elif isinstance(value, (list, tuple)):
        out["len"] = len(value)
        out["member_types"] = sorted({type(x).__name__ for x in value})
        if value and isinstance(value[0], dict):
            out["member_keys"] = sorted(
                {str(k) for x in value[:8] if isinstance(x, dict)
                 for k in x})
    elif isinstance(value, dict):
        out["keys"] = sorted(str(k) for k in value)
    return out


#: The fields a payout could plausibly come from. Probed BY NAME and
#: reported with their shapes, so "the field is absent" and "the field is
#: present in a shape we do not read" are never the same sentence.
PAYOUT_CANDIDATE_FIELDS = (
    "outcomePrices", "outcome_prices", "outcomes", "marketSides",
    "settlementPrice", "settlement_price", "status", "ep3Status",
    "closed", "active", "archived", "endDate", "settledAt", "resolvedAt",
    "assetPriceTerms", "line", "spreadTotalSuffix",
)


def _void_evidence(m) -> dict:
    """Whether the venue DECLARED a void, read only from status fields.

    Deliberately narrow. Every rules text on this venue says the word
    "void" while describing when a void would happen, so matching prose
    would void the whole book. Only fields that report a market's STATE are
    consulted, and the field that matched is named.
    """
    out = {"declared": False, "field": None, "value": None,
           "searched": list(VOID_STATUS_FIELDS),
           "why": ("a void is read from a STATUS field only. Rules prose "
                   "describes when a void would occur and is not evidence "
                   "that one did")}
    if not isinstance(m, dict):
        return out
    for f in VOID_STATUS_FIELDS:
        v = m.get(f)
        if v is None:
            continue
        s = str(v).strip().lower()
        if any(w in s for w in VOID_WORDS):
            out.update(declared=True, field=f, value=str(v)[:120])
            return out
    return out


def probe(client, slug: str) -> dict:
    """Capture both surfaces for one slug. Never raises."""
    from . import bettor_live_read as LR
    from . import pmus

    out = {"version": VERSION, "slug": slug,
           "retrieved_at": datetime.now(timezone.utc).isoformat(),
           "endpoints": {"settlement": EP_SETTLEMENT, "listing": EP_LISTING},
           "settlement": None, "listing": None,
           "reader_verdict": None, "terminal_reading": R_UNREADABLE,
           "authoritative_payout_present": False,
           "why": None, "parser_gap": None}

    # ── 1 · THE SETTLEMENT ENDPOINT, RAW ─────────────────────────────
    c = client if client is not None else pmus._get_client()
    st = {"endpoint": EP_SETTLEMENT, "raw": None, "field_shapes": {},
          "error": None, "top_level_keys": None}
    try:
        resp = c.markets.settlement(slug)
        st["raw"] = _redact(resp)
        if isinstance(resp, dict):
            st["top_level_keys"] = sorted(str(k) for k in resp)
            st["field_shapes"] = _shapes_of(resp)
    except Exception as exc:                                   # noqa: BLE001
        st["error"] = type(exc).__name__
        st["error_text"] = str(exc)[:300]
    out["settlement"] = st

    # ── 2 · THE MARKET LISTING, RAW ──────────────────────────────────
    li = {"endpoint": EP_LISTING, "matched": 0, "keys": None,
          "field_shapes": {}, "payout_candidates": {}, "raw_subset": None,
          "error": None, "void_evidence": None}
    m = None
    try:
        resp = LR._markets(client, pmus).list({"slug": [slug]})
        markets = list((resp or {}).get("markets") or [])
        li["matched"] = len(markets)
        if markets:
            m = markets[0]
            li["keys"] = sorted(str(k) for k in m if isinstance(k, str))
            li["field_shapes"] = _shapes_of(m)
            li["payout_candidates"] = {
                f: {"present": f in m, **({} if f not in m
                                          else {"shape": _shape(m[f]),
                                                "value": _redact(m[f])})}
                for f in PAYOUT_CANDIDATE_FIELDS}
            li["raw_subset"] = _redact(
                {k: m[k] for k in m
                 if str(k) in PAYOUT_CANDIDATE_FIELDS})
            li["void_evidence"] = _void_evidence(m)
    except Exception as exc:                                   # noqa: BLE001
        li["error"] = type(exc).__name__
        li["error_text"] = str(exc)[:300]
    out["listing"] = li

    # ── 3 · THE EXISTING READER'S OWN VERDICT, UNCHANGED ─────────────
    #
    # The probe does not replace the reader; it records what the reader
    # says beside what the payload holds, so a disagreement between them is
    # visible rather than arbitrated here.
    try:
        out["reader_verdict"] = LR.read_resolution(client, slug)
    except Exception as exc:                                   # noqa: BLE001
        out["reader_verdict"] = {"raised": type(exc).__name__}

    # ── 4 · THE TERMINAL READING, FROM NAMED EVIDENCE ONLY ───────────
    v = out["reader_verdict"] or {}
    status = str(v.get("status") or "")
    if status == LR.RESOLVED:
        out.update(terminal_reading=R_REPORTED,
                   authoritative_payout_present=True,
                   why=("the settlement endpoint returned a price. This is "
                        "the venue's own answer"))
    elif status == LR.RESOLVED_DERIVED:
        out.update(terminal_reading=R_CONVERGED,
                   authoritative_payout_present=False,
                   why=("closed with prices converged to 1 and 0. THIS IS "
                        "OUR INFERENCE from a price, not a payout the "
                        "venue reported"))
    elif (li.get("void_evidence") or {}).get("declared"):
        out.update(terminal_reading=R_VOID,
                   authoritative_payout_present=True,
                   why=("the venue declared a void in field %r"
                        % (li["void_evidence"]["field"],)))
    elif status == LR.PENDING:
        out.update(terminal_reading=R_PENDING,
                   why="the venue lists the market and reports no outcome")
    elif status == LR.UNMATCHED:
        out.update(terminal_reading=R_UNMATCHED,
                   why="the venue does not list this slug")
    else:
        out.update(terminal_reading=R_UNREADABLE,
                   why=("no reported settlement, no exact price "
                        "convergence and no declared void. `closed`, "
                        "`resolved` and `settledAt` are NOT treated as "
                        "payout evidence"))

    # ── 5 · IS THIS OUR PARSER, OR THE PAYLOAD? ──────────────────────
    #
    # The one question the whole probe exists to answer, and it is answered
    # from the SHAPES rather than from a hunch. A JSON-encoded container
    # where a list is required is a defect of ours with a named repair; an
    # absent field is not.
    out["parser_gap"] = _parser_gap(li, st, out["reader_verdict"])

    # ── 6 · THE VENUE'S OWN SETTLEMENT PROSE, VERBATIM AND UNABRIDGED ──
    out["settlement_terms"] = _terms_read(m)
    return out


#: The prose fields a settlement rule could live in, in the order
#: `bettor_live_read.read_rules_text` prefers them -- so this probe reads
#: the SAME text production reads, not a different one.
PROSE_FIELDS = ("description", "assetPriceTerms", "rules",
                "resolutionSource", "resolutionCriteria")

#: The prose is quoted in full up to this length. `MAX_STR` (400) is for
#: incidental payload strings; truncating the settlement rule at 400 is
#: exactly the mistake below, so this is separate and generous, and the
#: TRUE length is always reported beside it.
MAX_PROSE = 6000

R_NO_MARKET = "THE_LISTING_RETURNED_NO_MARKET"
R_NO_PROSE = "NO_PROSE_FIELD_ON_THE_MARKET_OBJECT"


def _terms_read(market) -> dict:
    """What the venue's own published prose STATES, condition by condition.

    ── THE DEFECT THIS CLOSES, AND IT WAS MINE ──────────────────────

    The per-condition table I reported -- two conditions stated, five
    silent -- was computed against a TEST FIXTURE whose `description` is
    183 characters. The live listing for the same slug carries 380. A
    fixture is not the venue, and a claim about what a venue does not say
    cannot be made from an abridged copy of what it does say. So the prose
    is read here from the LIVE market object, quoted in full, with its true
    length, and run through the same reader production uses.

    ── WHAT IT DOES NOT DO ──────────────────────────────────────────

    It reads the VENUE SIDE ONLY. Compatibility requires both sides and an
    admitted scope, and `bettor_settlement_terms.compare` owns that
    decision. Nothing here infers agreement from silence, and a condition
    absent from `stated` means the reader found no sentence stating a
    payout for it -- which is the absence of evidence, not a rule.
    """
    from . import bettor_settlement_terms as ST

    out = {"read": False, "field": None, "chars": None, "text": None,
           "truncated": False, "fields_present": [], "refusal": None,
           "applicable_conditions": None, "stated": {}, "contradicted": [],
           "not_stated": [], "sentences": None,
           "what_not_stated_means": (
               "the reader found no single sentence in this text stating a "
               "payout for that condition. It is the ABSENCE of a venue "
               "rule, never agreement with ours"),
           "what_this_is_not": (
               "not a compatibility verdict. Both sides and an admitted "
               "scope are required for that, and bettor_settlement_terms."
               "compare is the only place that decides it")}
    if not isinstance(market, dict):
        out["refusal"] = R_NO_MARKET
        return out
    out["fields_present"] = [f for f in PROSE_FIELDS
                             if str(market.get(f) or "").strip()]
    field = next((f for f in PROSE_FIELDS
                  if str(market.get(f) or "").strip()), None)
    if field is None:
        out["refusal"] = R_NO_PROSE
        return out
    text = str(market[field]).strip()
    out.update(read=True, field=field, chars=len(text),
               truncated=len(text) > MAX_PROSE, text=text[:MAX_PROSE])
    try:
        rd = ST.read_terms(text)
        conds = ST.applicable_conditions(sport_family="baseball",
                                        market="h2h") or ()
        stated = dict(rd.get("terms") or {})
        out.update(
            stated=stated,
            contradicted=list(rd.get("contradicted") or []),
            applicable_conditions=list(conds),
            not_stated=[c for c in conds if c not in stated],
            sentences=len(list(ST.sentences(text))),
            # THE SENTENCES THE READER SAW AND DID NOT USE. A condition can
            # read as unstated because the venue is silent OR because a
            # sentence names it without a payout the reader recognises, and
            # those are different problems with different fixes.
            unused_evidence={
                c: [e.get("sentence") for e in evs if not e.get("used")]
                for c, evs in (rd.get("evidence") or {}).items()
                if any(not e.get("used") for e in evs)})
    except Exception as exc:                                   # noqa: BLE001
        out["refusal"] = "READER_RAISED_" + type(exc).__name__
    return out


def _parser_gap(listing, settlement, reader_verdict=None) -> dict:
    """Name a consumable-but-UNCONSUMED field, or say there is none.

    UNCONSUMED IS THE WHOLE WORD. A field the reader now decodes is not a
    gap, and reporting it as one would leave a closed defect looking open
    for as long as anyone kept reading this output. So the reader's own
    verdict gates the finding: if it resolved -- reported or derived -- then
    whatever shape the field arrived in, it was consumed.
    """
    gap = {"found": False, "fields": [], "why": None,
           "what_would_change": None,
           "reader_status": (reader_verdict or {}).get("status")}
    status = str((reader_verdict or {}).get("status") or "")
    if status in ("RESOLVED", "RESOLVED_DERIVED", "PENDING", "UNMATCHED"):
        gap["why"] = (
            "the reader reached %s, so no payout-bearing field was left "
            "unconsumed. A shape the parser handles is not a gap" % status)
        return gap
    # A GAP IS A FIELD THE PARSER CANNOT CONSUME -- not merely one that
    # arrives in an awkward shape. `bettor_live_read._as_list` now decodes a
    # JSON-encoded container, so that shape is handled and is no longer a
    # finding; the shapes stay in `listing.field_shapes` as evidence either
    # way. What still counts is a present payout field that the reader's own
    # decoder cannot turn into a list at all.
    from . import bettor_live_read as _LR

    cands = (listing or {}).get("payout_candidates") or {}
    for f in ("outcomePrices", "outcome_prices", "outcomes"):
        c = cands.get(f) or {}
        if not c.get("present"):
            continue
        if _LR._as_list(c.get("value")) is None:
            sh = c.get("shape") or {}
            gap["fields"].append(
                {"field": f, "delivered_as": sh.get("type"),
                 "decoder": "bettor_live_read._as_list",
                 "decoded": None,
                 "preview": (c.get("value") if isinstance(c.get("value"),
                                                          str)
                             else None)})
    if gap["fields"]:
        gap.update(
            found=True,
            why=("the field is present and the reader's own decoder "
                 "cannot produce a list from it, so a payout-bearing "
                 "field is arriving in a shape nothing consumes"),
            what_would_change=("teaching `_as_list` this shape. Whatever "
                               "it then yields, a price vector still "
                               "produces RESOLVED_DERIVED -- a CONVERGED "
                               "PRICE INFERENCE, never a reported "
                               "settlement"))
        return gap
    st = settlement or {}
    gap["why"] = (
        "no payout-bearing field is present in a shape the parser rejects. "
        "The settlement endpoint returned %r and the listing carries "
        "`outcomes`/`outcomePrices` only as live prices. On this evidence "
        "the absence is the payload's, not the parser's"
        % (st.get("error") or (st.get("top_level_keys") or "no keys"),))
    return gap


def describe() -> dict:
    return {
        "version": VERSION,
        "reads": [EP_SETTLEMENT, EP_LISTING],
        "reuses": ["bettor_live_read.read_resolution",
                   "bettor_live_read.read_settlement",
                   "bettor_live_read._markets"],
        "writes": False, "submits_orders": False,
        "returns_credentials": False,
        "redaction": ("values whose KEY matches a secret-like name are "
                      "replaced; strings over %d chars are truncated; "
                      "lists over %d items are headed" % (MAX_STR,
                                                          MAX_ITEMS)),
        "terminal_readings": [R_REPORTED, R_CONVERGED, R_VOID, R_PENDING,
                              R_UNMATCHED, R_UNREADABLE],
        "never_infers_payout_from": ["closed", "resolved", "settledAt",
                                     "endDate", "active", "archived"],
        "void_is_read_from": list(VOID_STATUS_FIELDS),
        "why_not_prose": ("every rules text on this venue contains the "
                          "word 'void' while describing when one would "
                          "occur. Matching prose would void the book"),
    }
