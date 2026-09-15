#!/usr/bin/env python3
"""Phase X1, PMUS half: one normalized contract-equivalence record per
viable PMUS market, built from SEALED EVIDENCE ONLY.

READ ONLY. This script contacts no venue. It reads the frozen Track B-L
blocks under research/evidence/trackbl/ and writes a joinable record set.
It does not classify cross-venue equivalence -- the Kalshi half does not
exist yet, so every record carries the Kalshi slots EMPTY and
EQUIVALENCE_CLASSIFICATION = NOT_CLASSIFIED_KALSHI_SIDE_ABSENT.

SCOPE -- WHAT "VIABLE" MEANS HERE
A market enters the record set only if the sealed bytes carry BOTH
  (a) its full discovery row (event + market + marketSides), and
  (b) at least one sealed /book observation of it.
That is the pair Phase X2 will need, so it is the pair this defines.
Two cohorts qualify:
  BLOCK_4 stage-2 book probes  -- 15 markets, probed 2026-09-14T19:49Z
  BLOCK_3 capture cohort       --  6 markets, captured 2026-09-14T17:13Z
One market is in both. Each record names its own evidence.

THE HONESTY RULE -- THREE STATUSES, NEVER FOUR
Every field is an envelope {value, status, source}.
  VERIFIED        the venue serves a DEDICATED field carrying this value,
                  and the record's value is that field's value unchanged.
  QUOTED_VERBATIM the venue serves NO dedicated field for this. The value
                  is one or more sentences lifted CHARACTER-FOR-CHARACTER
                  out of a prose field named in `source`. It is an
                  extract, not a parse: nothing is normalized, summarized,
                  or turned into a machine rule.
  NOT_IDENTIFIED  neither.
There is deliberately no "INFERRED". PMUS publishes cancellation,
postponement, overtime, draw and void terms as English prose inside
market.description -- there is no rules object to read. Turning that
prose into structured rule fields would be the analyst's inference
wearing the venue's authority, and a cross-venue equivalence built on it
would be an equivalence between two summaries rather than two contracts.
So the prose travels whole: SOURCE_TEXTS carries every rules-bearing
field verbatim, and the per-rule slots carry verbatim sentences that
mention the topic, for a human (or the Kalshi-side join) to compare.

A sentence that mentions two topics appears under both. That is not a
defect: the venue wrote one sentence covering both, and splitting it
would be editing the contract.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sys
from typing import Any

RECORD_VERSION = "PX1-PMUS-1"
VENUE = "PMUS"

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.join(HERE, "evidence", "trackbl")
OUTDIR = os.path.join(HERE, "evidence", "phasex1")

BLOCK_4 = "run85_trackbl_BLOCK_4_20260914T194859Z"
BLOCK_3 = "run85_trackbl_BLOCK_3_20260914T171339Z"

VERIFIED = "VERIFIED"
QUOTED = "QUOTED_VERBATIM"
ABSENT = "NOT_IDENTIFIED"

# Which prose topics land in which rule slot. A slot is a topic, not a
# rule: the value is the venue's own sentence, never our reading of it.
RULE_TOPICS: dict[str, tuple[str, ...]] = {
    "CANCELLATION_RULES": ("cancel", "canceled", "cancelled"),
    "POSTPONEMENT_RULES": ("postpon", "delay", "reschedul", "suspend",
                           "not held within", "not rescheduled"),
    "OVERTIME_RULES": ("overtime", "extra time", "extra innings",
                       "shootout", "penalty shoot"),
    "DRAW_RULES": ("draw", "tie ", "tied", "no contest", "push"),
    "VOID_RULES": ("void", "refund", "invalid", "nullif"),
}
# Everything a rules sentence can say that none of the five slots claim:
# where the truth comes from, what a live elimination does, what the
# instrument settles to. Kept as its own slot so nothing is dropped.
OTHER_TOPICS = ("sourced from", "governing body", "settle", "settlement",
                "eliminat", "expiration date", "fair market", "resolve")


def read_jsonl_gz(path: str) -> list[dict]:
    with gzip.open(path, "rt") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def field(value: Any, status: str, source: str) -> dict:
    """One record field. A VERIFIED field must carry a real value: a
    dedicated key the venue did not send is NOT_IDENTIFIED, not an
    empty VERIFIED."""
    if status == VERIFIED and value in (None, "", [], {}):
        return {"value": None, "status": ABSENT, "source": source}
    if status == ABSENT:
        return {"value": None, "status": ABSENT, "source": source}
    return {"value": value, "status": status, "source": source}


def dedicated(obj: dict, key: str, source_prefix: str) -> dict:
    """A dedicated venue field, passed through unchanged."""
    return field(obj.get(key), VERIFIED, "%s.%s" % (source_prefix, key))


# Periods that do not end a sentence. "vs." is the one that matters:
# PMUS writes "the winner of the Canelo Alvarez vs. Christian Mbilli
# boxing match", and a naive split cuts that clause in half, leaving an
# extract that misquotes the contract and names only one fighter.
NON_TERMINAL_ABBREVIATIONS = frozenset((
    "vs", "v", "no", "nos", "st", "mr", "mrs", "ms", "dr", "prof", "jr",
    "sr", "inc", "ltd", "co", "corp", "etc", "approx", "est", "al", "fig",
    "ca", "cf", "eg", "ie", "am", "pm", "u.s", "u.k",
))


def sentences(text: str) -> list[str]:
    """Split prose into sentences without altering a single character of
    what survives. Each returned string is a contiguous slice of `text`.

    A period ends a sentence only when the word before it is not a known
    abbreviation or a bare initial, and what follows opens like a new
    sentence.
    """
    if not text:
        return []
    out, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+", text):
        head = text[start:m.start()]
        nxt = text[m.end():m.end() + 1]
        if nxt and not (nxt.isupper() or nxt.isdigit() or nxt in "\"'("):
            continue
        if head.endswith("."):
            word = re.search(r"([A-Za-z0-9.]+)\.$", head)
            tok = (word.group(1).rstrip(".").lower() if word else "")
            if tok in NON_TERMINAL_ABBREVIATIONS or (
                    len(tok) == 1 and tok.isalpha()):
                continue
        piece = head.strip()
        if piece:
            out.append(piece)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def quote_topic(sources: list[tuple[str, str]],
                needles: tuple[str, ...]) -> tuple[list[dict], bool]:
    """Every sentence, across every prose source, that mentions the topic.

    Returns (hits, matched). Each hit names the prose field it came from
    so a reader can go back to the sealed bytes."""
    hits: list[dict] = []
    for source_name, text in sources:
        for sent in sentences(text):
            low = sent.lower()
            if any(n in low for n in needles):
                hits.append({"source": source_name, "text": sent})
    return hits, bool(hits)


def team_of(side: dict) -> dict:
    return side.get("team") or {}


def side_record(side: dict, outcomes: list[str], index: int) -> dict:
    """One side of the contract, from every naming the venue supplies.

    The venue names a side FOUR ways -- marketSides[].description,
    marketSides[].team.name, outcomes[i], and the market title's prose --
    and they do not always agree about orientation. The record keeps all
    of them rather than electing one, because electing one is exactly the
    inference that would make a cross-venue join wrong in the one case
    that matters."""
    team = team_of(side)
    return {
        "SIDE_ID": field(side.get("id"), VERIFIED, "marketSides[].id"),
        "SIDE_IS_LONG": field(side.get("long"), VERIFIED,
                              "marketSides[].long"),
        "SIDE_DESCRIPTION": field(side.get("description"), VERIFIED,
                                  "marketSides[].description"),
        "SIDE_IDENTIFIER": field(side.get("identifier"), VERIFIED,
                                 "marketSides[].identifier"),
        "SIDE_TEAM_NAME": field(team.get("name"), VERIFIED,
                                "marketSides[].team.name"),
        "SIDE_TEAM_ABBREVIATION": field(team.get("abbreviation"), VERIFIED,
                                        "marketSides[].team.abbreviation"),
        "SIDE_TEAM_LEAGUE": field(team.get("league"), VERIFIED,
                                  "marketSides[].team.league"),
        "SIDE_OUTCOME_LABEL": field(
            outcomes[index] if index < len(outcomes) else None,
            VERIFIED, "market.outcomes[%d]" % index),
        "SIDE_TRADABLE": field(side.get("tradable"), VERIFIED,
                               "marketSides[].tradable"),
    }


def league_from_tags(ev: dict) -> tuple[dict | None, str]:
    """The league object PMUS hangs off an event tag, and the tag slug it
    came from. Never guessed from the market slug's prefix."""
    for tag in ev.get("tags") or []:
        lg = tag.get("league")
        if isinstance(lg, dict) and lg.get("name"):
            return lg, tag.get("slug") or ""
    return None, ""


def build_record(ev: dict, mkt: dict, book_evidence: list[dict]) -> dict:
    try:
        outcomes = json.loads(mkt.get("outcomes") or "[]")
    except (ValueError, TypeError):
        outcomes = []
    if not isinstance(outcomes, list):
        outcomes = []

    sides = mkt.get("marketSides") or []
    lg, lg_tag_slug = league_from_tags(ev)

    prose: list[tuple[str, str]] = []
    if mkt.get("description"):
        prose.append(("market.description", mkt["description"]))
    if mkt.get("rulesDisclaimer"):
        prose.append(("market.rulesDisclaimer", mkt["rulesDisclaimer"]))
    if ev.get("description") and ev.get("description") != mkt.get(
            "description"):
        prose.append(("event.description", ev["description"]))

    rules: dict[str, dict] = {}
    claimed: set[str] = set()
    for slot, needles in RULE_TOPICS.items():
        hits, matched = quote_topic(prose, needles)
        for h in hits:
            claimed.add(h["text"])
        rules[slot] = field(
            hits, QUOTED, "; ".join(s for s, _ in prose)) if matched else \
            field(None, ABSENT, "no dedicated field; no matching sentence")

    other_hits, _ = quote_topic(prose, OTHER_TOPICS)
    unclaimed = [h for h in other_hits if h["text"] not in claimed]
    rules["OTHER_RESOLUTION_CONDITIONS"] = (
        field(unclaimed, QUOTED, "; ".join(s for s, _ in prose))
        if unclaimed else
        field(None, ABSENT, "no dedicated field; no unclaimed sentence"))

    # settlementPriceCalculationMethod is served by /book stats, never by
    # /v1/events -- the same schema asymmetry that made BL-SELECT-1
    # activity-blind. Take it from the sealed book, and only if every
    # sealed book of this market agrees.
    spcms = sorted({b["settlement_price_calculation_method"]
                    for b in book_evidence
                    if b.get("settlement_price_calculation_method")})
    if len(spcms) == 1:
        spcm = field(spcms[0], VERIFIED,
                     "book.marketData.stats."
                     "settlementPriceCalculationMethod")
    elif len(spcms) > 1:
        spcm = field({"DISAGREEING_VALUES": spcms}, VERIFIED,
                     "book.marketData.stats."
                     "settlementPriceCalculationMethod")
        spcm["status"] = "DISAGREEMENT_ACROSS_SEALED_OBSERVATIONS"
    else:
        spcm = field(None, ABSENT,
                     "absent from every sealed book observation")

    rec: dict[str, Any] = {
        "RECORD_VERSION": RECORD_VERSION,
        "VENUE": VENUE,

        "PMUS_MARKET_ID": dedicated(mkt, "id", "market"),
        "PMUS_MARKET_SLUG": dedicated(mkt, "slug", "market"),
        "PMUS_EVENT_ID": dedicated(ev, "id", "event"),

        # PMUS serves a numeric sportId on the league object and a
        # "sports" category, but never a sport NAME. So the name is
        # NOT_IDENTIFIED and the id is what is VERIFIED.
        "SPORT": field(None, ABSENT,
                       "venue serves no sport-name field; see "
                       "SPORT_ID and TAG_SLUGS"),
        "SPORT_ID": field(lg.get("sportId") if lg else None, VERIFIED,
                          "event.tags[].league.sportId"),
        "CATEGORY": dedicated(mkt, "category", "market"),
        "TAG_SLUGS": field([t.get("slug") for t in (ev.get("tags") or [])
                            if t.get("slug")], VERIFIED,
                           "event.tags[].slug"),

        "LEAGUE": field(lg.get("name") if lg else None, VERIFIED,
                        "event.tags[].league.name (tag slug %r)"
                        % lg_tag_slug),
        "LEAGUE_ABBREVIATION": field(lg.get("abbreviation") if lg else None,
                                     VERIFIED,
                                     "event.tags[].league.abbreviation"),
        "LEAGUE_RESOLUTION_SOURCE": field(lg.get("resolution") if lg else
                                          None, VERIFIED,
                                          "event.tags[].league.resolution"),
        "LEAGUE_AUTOMATIC_RESOLUTION": field(
            lg.get("automaticResolution") if lg else None, VERIFIED,
            "event.tags[].league.automaticResolution"),

        "EVENT": field({
            "title": ev.get("title"),
            "ticker": ev.get("ticker"),
            "slug": ev.get("slug"),
            "seriesSlug": ev.get("seriesSlug"),
            "sportradarGameId": ev.get("sportradarGameId"),
        }, VERIFIED, "event.title/ticker/slug/seriesSlug/sportradarGameId"),

        "MARKET_TYPE": field({
            "sportsMarketTypeV2": mkt.get("sportsMarketTypeV2"),
            "sportsMarketType": mkt.get("sportsMarketType"),
            "marketType": mkt.get("marketType"),
            "line": mkt.get("line"),
            "spreadTotalSuffix": mkt.get("spreadTotalSuffix"),
        }, VERIFIED, "market.sportsMarketTypeV2/sportsMarketType/"
                     "marketType/line/spreadTotalSuffix"),

        # event.participants is served on every event row but was [] on
        # every market in this set, so the participants that exist are the
        # per-side team objects. Both are carried; neither is synthesized.
        "PARTICIPANTS": field(ev.get("participants") or None, VERIFIED,
                              "event.participants"),
        "PARTICIPANTS_FROM_SIDES": field(
            [team_of(s).get("name") for s in sides if team_of(s).get("name")]
            or None, VERIFIED, "marketSides[].team.name"),

        "SIDE_1_DEFINITION": (side_record(sides[0], outcomes, 0)
                              if len(sides) > 0 else
                              field(None, ABSENT, "market.marketSides[0]")),
        "SIDE_2_DEFINITION": (side_record(sides[1], outcomes, 1)
                              if len(sides) > 1 else
                              field(None, ABSENT, "market.marketSides[1]")),
        "SIDE_COUNT": field(len(sides), VERIFIED, "len(market.marketSides)"),

        "GAME_START_TIME": dedicated(mkt, "gameStartTime", "market"),
        "EVENT_START_TIME": dedicated(ev, "startTime", "event"),
        "END_TIME": dedicated(mkt, "endDate", "market"),
        "EVENT_END_TIME": dedicated(ev, "endDate", "event"),

        "RULES_DISCLAIMER": dedicated(mkt, "rulesDisclaimer", "market"),
        "RULES_DISCLAIMER_POPUP": dedicated(mkt, "rulesDisclaimerPopup",
                                            "market"),
        "SETTLEMENT_PRICE_CALCULATION_METHOD": spcm,

        "MARKET_STATUS": dedicated(mkt, "status", "market"),
        "TICK_SIZE": dedicated(mkt, "orderPriceMinTickSize", "market"),
        "MINIMUM_TRADE_QTY": dedicated(mkt, "minimumTradeQty", "market"),
        "FEE_COEFFICIENT": dedicated(mkt, "feeCoefficient", "market"),
    }
    rec.update(rules)

    # The whole prose, unedited, so the Kalshi-side join never has to
    # trust the extracts above.
    rec["SOURCE_TEXTS"] = {
        "MARKET_QUESTION": mkt.get("question"),
        "MARKET_TITLE": mkt.get("title"),
        "MARKET_DESCRIPTION": mkt.get("description"),
        "MARKET_RULES_DISCLAIMER": mkt.get("rulesDisclaimer"),
        "EVENT_TITLE": ev.get("title"),
        "EVENT_DESCRIPTION": ev.get("description"),
        "OUTCOMES": mkt.get("outcomes"),
    }
    rec["PMUS_EVIDENCE"] = book_evidence

    # The Kalshi half, deliberately empty. Shaped now so landing it later
    # is an assignment, never a change to PMUS semantics.
    rec["KALSHI"] = {k: field(None, ABSENT, "Kalshi side not built")
                     for k in ("KALSHI_TICKER", "KALSHI_SERIES",
                               "KALSHI_TITLE", "KALSHI_SUBTITLE",
                               "KALSHI_YES_DEFINITION",
                               "KALSHI_NO_DEFINITION",
                               "KALSHI_CLOSE_TIME",
                               "KALSHI_EXPIRATION_TIME",
                               "KALSHI_RULES_PRIMARY",
                               "KALSHI_RULES_SECONDARY",
                               "KALSHI_SETTLEMENT_SOURCE",
                               "KALSHI_CANCELLATION_RULES",
                               "KALSHI_POSTPONEMENT_RULES",
                               "KALSHI_OVERTIME_RULES",
                               "KALSHI_DRAW_RULES",
                               "KALSHI_VOID_RULES")}
    rec["EQUIVALENCE_CLASSIFICATION"] = "NOT_CLASSIFIED_KALSHI_SIDE_ABSENT"
    rec["EQUIVALENCE_BLOCKERS"] = ["KALSHI_SIDE_ABSENT"]
    return rec


def audit(rec: dict) -> list[str]:
    """Structural self-checks. A record that fails one is reported, not
    quietly fixed -- a builder that repairs its own output is a builder
    whose output cannot be audited."""
    bad: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict) and "status" in node and "value" in node:
            st, val = node["status"], node["value"]
            if st == ABSENT and val is not None:
                bad.append("%s: NOT_IDENTIFIED carries a value" % path)
            if st == VERIFIED and val in (None, "", [], {}):
                bad.append("%s: VERIFIED carries no value" % path)
            if st == QUOTED and not isinstance(val, list):
                bad.append("%s: QUOTED_VERBATIM is not a list of "
                           "extracts" % path)
            if st not in (VERIFIED, QUOTED, ABSENT,
                          "DISAGREEMENT_ACROSS_SEALED_OBSERVATIONS"):
                bad.append("%s: unknown status %r" % (path, st))
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, "%s.%s" % (path, k) if path else k)

    walk(rec, "")
    src = rec.get("SOURCE_TEXTS") or {}
    desc = src.get("MARKET_DESCRIPTION") or ""
    for slot in list(RULE_TOPICS) + ["OTHER_RESOLUTION_CONDITIONS"]:
        node = rec.get(slot) or {}
        if node.get("status") != QUOTED:
            continue
        for hit in node["value"]:
            text = hit["text"]
            pool = {"market.description": desc,
                    "market.rulesDisclaimer":
                        src.get("MARKET_RULES_DISCLAIMER") or "",
                    "event.description":
                        src.get("EVENT_DESCRIPTION") or ""}[hit["source"]]
            if text not in pool:
                bad.append("%s: extract is not a verbatim slice of %s"
                           % (slot, hit["source"]))
    return bad


def load_block(block: str) -> tuple[dict[str, tuple[dict, dict]], dict]:
    """Every (event, market) pair in a block's sealed discovery samples,
    keyed by market slug, plus the block's checksum manifest."""
    root = os.path.join(EVID, block)
    pairs: dict[str, tuple[dict, dict]] = {}
    for row in read_jsonl_gz(os.path.join(
            root, "discovery_body_samples.jsonl.gz")):
        for part in ("events_head", "events_tail"):
            for ev in row.get(part) or []:
                for mkt in ev.get("markets") or []:
                    if mkt.get("slug"):
                        pairs[mkt["slug"]] = (ev, mkt)
    sums: dict[str, str] = {}
    path = os.path.join(root, "checksums.sha256")
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                bits = line.split()
                if len(bits) == 2:
                    sums[bits[1].lstrip("*")] = bits[0]
    return pairs, sums


def verify_checksums(block: str, sums: dict[str, str]) -> list[str]:
    root = os.path.join(EVID, block)
    out = []
    for name, want in sorted(sums.items()):
        p = os.path.join(root, os.path.basename(name))
        if not os.path.exists(p):
            out.append("%s/%s MISSING" % (block, name))
            continue
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        got = h.hexdigest()
        out.append("%s/%s %s" % (block, name,
                                 "OK" if got == want else
                                 "MISMATCH want=%s got=%s" % (want, got)))
    return out


def book_rows_block4() -> dict[str, list[dict]]:
    """One evidence row per sealed BLOCK_4 stage-2 /book probe."""
    root = os.path.join(EVID, BLOCK_4)
    out: dict[str, list[dict]] = {}
    for r in read_jsonl_gz(os.path.join(root,
                                        "book_probe_receipts.jsonl.gz")):
        md = (r.get("body") or {}).get("marketData") or {}
        st = md.get("stats") or {}
        out.setdefault(r["market_slug"], []).append({
            "cohort": "BLOCK_4_STAGE_2_BOOK_PROBE",
            "selection_rule_version": "BL-SELECT-2",
            "observed_utc": r.get("probe_wall_utc"),
            "http_status": r.get("http_status"),
            "response_sha256": r.get("response_sha256"),
            "market_state": md.get("state"),
            "settlement_price_calculation_method":
                st.get("settlementPriceCalculationMethod"),
            "evidence_path": "research/evidence/trackbl/%s/"
                             "book_probe_receipts.jsonl.gz" % BLOCK_4,
        })
    return out


def book_rows_block3() -> dict[str, list[dict]]:
    """BLOCK_3's cohort rows. BLOCK_3 sealed its capture reads in
    block_log.jsonl.gz; what this record needs from them is the fact of
    observation and the settlement method, so the cohort row -- which the
    runner derived from those same reads -- is the citation."""
    root = os.path.join(EVID, BLOCK_3)
    out: dict[str, list[dict]] = {}
    for row in json.load(open(os.path.join(root, "block_cohort.json"))):
        slug = row.get("market_slug")
        if not slug:
            continue
        out.setdefault(slug, []).append({
            "cohort": "BLOCK_3_CAPTURE_COHORT",
            "selection_rule_version": "BL-SELECT-1 (RETIRED)",
            "observed_utc": None,
            "http_status": None,
            "response_sha256": None,
            "market_state": row.get("state"),
            "settlement_price_calculation_method": None,
            "evidence_path": "research/evidence/trackbl/%s/"
                             "block_cohort.json" % BLOCK_3,
        })
    return out


def main() -> int:
    os.makedirs(OUTDIR, exist_ok=True)
    lines: list[str] = []

    def say(msg: str = "") -> None:
        lines.append(msg)
        print(msg)

    say("=" * 72)
    say("PHASE X1 -- PMUS HALF -- CONTRACT EQUIVALENCE RECORDS")
    say("RECORD_VERSION = %s" % RECORD_VERSION)
    say("SOURCE = SEALED EVIDENCE ONLY (no venue contact)")
    say("=" * 72)
    say()

    say("--- EVIDENCE INTEGRITY ---")
    p4, s4 = load_block(BLOCK_4)
    p3, s3 = load_block(BLOCK_3)
    ck = verify_checksums(BLOCK_4, s4) + verify_checksums(BLOCK_3, s3)
    for line in ck:
        say("  " + line)
    bad_ck = [c for c in ck if not c.endswith("OK")]
    say("  CHECKSUM_VERDICT = %s"
        % ("ALL_OK" if not bad_ck else "FAILED (%d)" % len(bad_ck)))
    say()

    b4, b3 = book_rows_block4(), book_rows_block3()
    slugs = sorted(set(b4) | set(b3))
    say("--- SCOPE ---")
    say("  BLOCK_4 stage-2 probed markets : %d" % len(b4))
    say("  BLOCK_3 capture cohort markets : %d" % len(b3))
    say("  In both cohorts                : %d" % len(set(b4) & set(b3)))
    say("  UNIQUE VIABLE PMUS CONTRACTS   : %d" % len(slugs))
    say()

    records, missing, audits = [], [], []
    for slug in slugs:
        pair = p4.get(slug) or p3.get(slug)
        if pair is None:
            missing.append(slug)
            continue
        ev, mkt = pair
        rec = build_record(ev, mkt, b4.get(slug, []) + b3.get(slug, []))
        problems = audit(rec)
        if problems:
            audits.append((slug, problems))
        records.append(rec)

    say("--- BUILD ---")
    say("  RECORDS_BUILT = %d" % len(records))
    say("  METADATA_MISSING_FROM_SEALED_BYTES = %d %s"
        % (len(missing), missing or ""))
    say("  STRUCTURAL_AUDIT = %s"
        % ("CLEAN" if not audits else "%d RECORDS FLAGGED" % len(audits)))
    for slug, problems in audits:
        for p in problems:
            say("    %s: %s" % (slug, p))
    say()

    say("--- FIELD COVERAGE ACROSS %d RECORDS ---" % len(records))
    say("  %-38s %7s %7s %7s" % ("FIELD", "VERIF", "QUOTED", "ABSENT"))
    flat_fields = [k for k, v in (records[0].items() if records else [])
                   if isinstance(v, dict) and "status" in v]
    for k in flat_fields:
        c = {VERIFIED: 0, QUOTED: 0, ABSENT: 0}
        for r in records:
            c[r[k]["status"]] = c.get(r[k]["status"], 0) + 1
        say("  %-38s %7d %7d %7d" % (k, c[VERIFIED], c[QUOTED], c[ABSENT]))
    say()

    say("--- PER-CONTRACT SUMMARY ---")
    for r in records:
        say("  %s" % r["PMUS_MARKET_SLUG"]["value"])
        say("    id=%s event=%s league=%s type=%s"
            % (r["PMUS_MARKET_ID"]["value"], r["PMUS_EVENT_ID"]["value"],
               r["LEAGUE"]["value"],
               (r["MARKET_TYPE"]["value"] or {}).get("sportsMarketTypeV2")))
        say("    start=%s end=%s spcm=%s"
            % (r["GAME_START_TIME"]["value"], r["END_TIME"]["value"],
               r["SETTLEMENT_PRICE_CALCULATION_METHOD"]["value"]))
        for slot in list(RULE_TOPICS) + ["OTHER_RESOLUTION_CONDITIONS",
                                         "RULES_DISCLAIMER"]:
            n = r[slot]
            if n["status"] == ABSENT:
                say("    %-28s NOT_IDENTIFIED" % slot)
            elif n["status"] == VERIFIED:
                say("    %-28s VERIFIED  %s" % (slot, n["value"]))
            else:
                say("    %-28s QUOTED_VERBATIM x%d" % (slot,
                                                       len(n["value"])))
                for h in n["value"]:
                    say("        [%s] %s" % (h["source"], h["text"]))
        say("    EQUIVALENCE_CLASSIFICATION = %s"
            % r["EQUIVALENCE_CLASSIFICATION"])
        say()

    out_json = os.path.join(OUTDIR, "PMUS_EQUIVALENCE_RECORDS.json")
    payload = {
        "RECORD_VERSION": RECORD_VERSION,
        "VENUE": VENUE,
        "SOURCE_BLOCKS": [BLOCK_4, BLOCK_3],
        "CHECKSUM_VERDICT": "ALL_OK" if not bad_ck else "FAILED",
        "STATUS_VOCABULARY": {
            VERIFIED: "the venue serves a dedicated field carrying this "
                      "value, unchanged",
            QUOTED: "no dedicated field; value is verbatim sentence(s) "
                    "lifted from a named prose field",
            ABSENT: "neither a dedicated field nor a matching sentence",
        },
        "EQUIVALENCE_CLASSIFICATION":
            "NOT_CLASSIFIED_KALSHI_SIDE_ABSENT",
        "RECORD_COUNT": len(records),
        "RECORDS": records,
    }
    with open(out_json, "w") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True, default=str)
    h = hashlib.sha256(open(out_json, "rb").read()).hexdigest()
    say("WROTE %s" % out_json)
    say("SHA256 %s" % h)
    say()
    say("PHASE_X1_PMUS_HALF = BUILT")
    say("PHASE_X1_KALSHI_HALF = NOT_BUILT")
    say("CROSS_VENUE_EQUIVALENCE = NOT_CLASSIFIED")

    with open(os.path.join(OUTDIR, "PMUS_EQUIVALENCE_REPORT.txt"), "w") \
            as fh:
        fh.write("\n".join(lines) + "\n")
    return 0 if not bad_ck and not audits and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
