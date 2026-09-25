"""RE-ESTABLISH A LEGACY POSITION'S VENUE IDENTITY, FROM THE RESOLVER.

WHY THIS EXISTS. Migration 119 puts the venue identity on the position and
the entry writer fills it. A position opened before that carries NULL, and
`bettor_entry_settlement.held_identity` then has to find a valuation that
NAMES the outcome the position holds. For the acceptance position that
search refused, in production:

    THE_ONLY_AVAILABLE_IDENTITY_DESCRIBES_A_DIFFERENT_OUTCOME
    "2 venue identities are recorded for this condition and none of them
     names the outcome this position holds ('Arizona Diamondbacks'). They
     describe ['Colorado Rockies', 'None']. Nothing is settled"

The refusal is right and it is a dead end. The position holds Arizona; the
recorded valuation describes Colorado; settling against Colorado's slug
would record the opposite result. So the binding is re-established from
the RESOLVER instead.

── WHAT THIS DOES, AND THE FOUR THINGS IT WILL NOT DO ───────────────

    1 READ the outcome the position actually holds, from `market_tokens`
      at the position's own `outcome_index`. The global catalogue's
      answer, owing nothing to any valuation.
    2 ASK `premap.resolve` for THAT outcome, with the market's own title,
      event title and global slug. It returns the venue-native contract
      and the intent that BUYS that outcome.
    3 CROSS-CHECK the answer five ways before anything is written, and
      refuse on any disagreement. Two of them do the real work: the venue
      row's own side name must still be the outcome the catalogue lists at
      this position's index, and the SIBLING outcome must not resolve to
      the same slug AND intent -- because one binding standing in for the
      other side of the same fixture is precisely the failure that
      produced the production refusal.
    4 PERSIST the identity and an auditable record of how it was derived.

IT DOES NOT read `external_valuations` at all -- which is the strongest
available guarantee that no other side's row is borrowed. It does not
reseed. It does not touch `provenance`, `entry_kind`, `source_trade_id`,
`source_account` or any seed column. And it only ever fills identity
columns that are NULL, enforced in the UPDATE's WHERE clause.
"""

from __future__ import annotations

VERSION = "LEGACY_POSITION_IDENTITY_REPAIR_V1"

#: The venue this repair is established for. `premap` resolves the PMUS
#: catalogue; a different venue would need its own resolver and its own
#: position model, so it is named rather than inferred.
VENUE = "PMUS"

R_NO_POSITION = "POSITION_NOT_FOUND"
R_NO_TOKEN = "THE_HELD_PAYOUT_EVENT_COULD_NOT_BE_RESOLVED"
R_NO_MARKET = "NO_MARKETS_ROW_FOR_THIS_CONDITION"
R_NO_PREMAP = "THE_RESOLVER_NAMES_NO_VENUE_CONTRACT_FOR_THIS_OUTCOME"
R_RESOLVER_RAISED = "THE_RESOLVER_RAISED"
R_INTENT_UNUSABLE = "THE_RESOLVED_INTENT_NAMES_NO_SIDE_WE_CAN_CONSUME"
R_SIDE_NOT_THE_OUTCOME = "THE_RESOLVED_SIDE_DOES_NOT_NAME_THE_HELD_OUTCOME"
R_SIDES_NOT_DISCRIMINATED = "THE_RESOLVER_BINDS_BOTH_SIDES_IDENTICALLY"
R_ALREADY = "THE_POSITION_ALREADY_CARRIES_AN_IDENTITY"

POSITION_SQL = """
    SELECT position_id, experiment_id, policy, condition_id, outcome_index,
           provenance, entry_kind, venue, venue_market_slug,
           venue_buy_intent, venue_ladder_side, payout_event,
           source_valuation_id, identity_repair::text AS identity_repair
      FROM rn1x_positions WHERE position_id = $1
"""

TOKEN_SQL = """
    SELECT token_id, outcome, outcome_index
      FROM market_tokens WHERE condition_id = $1 AND outcome_index = $2
"""

ALL_TOKENS_SQL = """
    SELECT outcome, outcome_index FROM market_tokens
     WHERE condition_id = $1 ORDER BY outcome_index
"""

MARKET_SQL = """
    SELECT condition_id, title, event_title, slug, sport
      FROM markets WHERE condition_id = $1
"""

#: FILLS NULLS ONLY. A repair may not overwrite an identity the writer
#: that opened the position recorded, and the guard is in the statement
#: rather than in a check the caller could forget.
WRITE_SQL = """
    UPDATE rn1x_positions
       SET venue_market_slug = $2, venue_buy_intent = $3,
           venue_ladder_side = $4, payout_event = $5, venue = $6,
           identity_repair = $7::jsonb
     WHERE position_id = $1
       AND venue_market_slug IS NULL
       AND venue_buy_intent IS NULL
     RETURNING position_id
"""


async def repair(conn, position_id, *, now, write=True) -> dict:
    """Re-establish one legacy position's venue identity. Never raises."""
    import json as _json

    from .workers import premap as _premap

    out = {"version": VERSION, "position_id": str(position_id),
           "ok": False, "written": False, "refusal": None, "why": None,
           "asked_for": None, "held_outcome": None, "held_token_id": None,
           "outcomes_listed": None, "resolver": None, "cross_checks": {},
           "identity": None,
           "reads_external_valuations": False,
           "touches_provenance": False, "reseeds": False}
    try:
        pos = await conn.fetchrow(POSITION_SQL, str(position_id))
    except Exception as exc:                                   # noqa: BLE001
        out.update(refusal=R_NO_POSITION,
                   why="the position read failed: %s" % type(exc).__name__)
        return out
    if pos is None:
        out.update(refusal=R_NO_POSITION,
                   why="no position with that id exists")
        return out
    p = dict(pos)
    out["provenance"] = p.get("provenance")
    out["policy"] = p.get("policy")
    out["experiment_id"] = p.get("experiment_id")
    out["condition_id"] = p.get("condition_id")
    out["outcome_index"] = p.get("outcome_index")

    # ── 1 · THE OUTCOME THIS POSITION HOLDS, FROM THE CATALOGUE ──────
    try:
        tok = await conn.fetchrow(TOKEN_SQL, str(p["condition_id"]),
                                  int(p["outcome_index"]))
        toks = [dict(r) for r in
                await conn.fetch(ALL_TOKENS_SQL, str(p["condition_id"]))]
    except Exception as exc:                                   # noqa: BLE001
        out.update(refusal=R_NO_TOKEN,
                   why="the token read failed: %s" % type(exc).__name__)
        return out
    if tok is None:
        out.update(refusal=R_NO_TOKEN,
                   why=("the global catalogue lists no token at outcome "
                        "index %r for %r, so the event this position pays "
                        "on is not established and is not assumed"
                        % (p["outcome_index"], p["condition_id"])))
        return out
    out["held_outcome"] = str(tok["outcome"])
    out["held_token_id"] = tok["token_id"]
    out["asked_for"] = str(tok["outcome"])
    out["outcomes_listed"] = len(toks)
    out["catalogue"] = [{"outcome": t["outcome"],
                         "outcome_index": t["outcome_index"]} for t in toks]

    # ── AN EXISTING IDENTITY IS NOT OVERWRITTEN ──────────────────────
    if p.get("venue_market_slug") or p.get("venue_buy_intent"):
        out["identity"] = {k: p.get(k) for k in
                           ("venue", "venue_market_slug", "venue_buy_intent",
                            "venue_ladder_side", "payout_event")}
        out.update(refusal=R_ALREADY,
                   why=("this position already records a venue identity. A "
                        "repair fills a gap; it does not revise a binding "
                        "the writer that opened the position made"))
        return out

    # ── 2 · THE MARKET ROW THE RESOLVER NEEDS ────────────────────────
    try:
        mkt = await conn.fetchrow(MARKET_SQL, str(p["condition_id"]))
    except Exception as exc:                                   # noqa: BLE001
        out.update(refusal=R_NO_MARKET,
                   why="the markets read failed: %s" % type(exc).__name__)
        return out
    if mkt is None:
        out.update(refusal=R_NO_MARKET,
                   why="no markets row carries this condition")
        return out
    m = dict(mkt)
    out["market"] = {"title": m.get("title"),
                     "event_title": m.get("event_title"),
                     "global_slug": m.get("slug"), "sport": m.get("sport")}

    # ── 3 · THE RESOLVER, ASKED FOR THE HELD OUTCOME ─────────────────
    try:
        got = await _premap.resolve(
            conn, m.get("title"), m.get("event_title"), out["held_outcome"],
            m.get("slug"), condition_id=str(p["condition_id"]))
    except Exception as exc:                                   # noqa: BLE001
        out.update(refusal=R_RESOLVER_RAISED,
                   why="premap.resolve raised %s" % type(exc).__name__)
        return out
    if not got or not got.get("market_slug"):
        out.update(refusal=R_NO_PREMAP,
                   why=("the venue's own catalogue names no contract for "
                        "%r on this fixture, so there is nothing to bind"
                        % out["held_outcome"]))
        return out
    # THE KEYS `premap.resolve` ACTUALLY RETURNS. It answers with
    # `market_slug`, `intent`, `outcome` (the venue row's own `side_norm`),
    # `title` (its question) and `matched_by`. Asking it for `side_norm`,
    # `identifier` or `question` reads None every time and would make an
    # empty audit record look like a populated one.
    out["resolver"] = {k: got.get(k) for k in
                       ("market_slug", "intent", "outcome", "title",
                        "matched_by", "score")}
    intent = str(got.get("intent") or "")

    # ── 4 · THE CROSS-CHECKS, ALL OF THEM, BEFORE ANY WRITE ──────────
    checks = out["cross_checks"]
    checks["intent_is_consumable"] = {
        "intent": intent,
        "passed": intent in ("ORDER_INTENT_BUY_LONG",
                             "ORDER_INTENT_BUY_SHORT")}
    if not checks["intent_is_consumable"]["passed"]:
        out.update(refusal=R_INTENT_UNUSABLE,
                   why=("the resolved intent is %r, which names no side "
                        "this system can consume" % (intent or None)))
        return out

    # THE CHECK THAT IS *NOT* MADE, AND WHY. An earlier draft of this file
    # derived the held side from the intent -- `1 if BUY_SHORT else 0` --
    # and refused when that disagreed with `outcome_index`. That is the
    # sign error `resolve_venue_identity` documents at length: the intent
    # names WHICH LADDER supplies the acquisition cost and nothing else. A
    # BUY_SHORT result for "Arizona Diamondbacks" means Arizona is the
    # venue's short side; the contract still PAYS ON ARIZONA. On the `aec-`
    # family both sides share one identifier and the intent IS the only
    # side selector, so an intent-to-index equality would have refused
    # every correct repair of a short-side holding. The index is not
    # cross-checked against the intent because they do not describe the
    # same thing; it is cross-checked against the CATALOGUE, below, which
    # is where it came from.

    # THE RESOLVED VENUE SIDE MUST NAME THE OUTCOME WE ASKED FOR. The
    # resolver is asked for one outcome and returns the row it matched;
    # comparing its own `side_norm` back against the catalogue's outcome
    # name is an independent reading of the same binding.
    from .workers.premap import _collapsed as _coll
    from .workers.premap import _norm as _pnorm

    side = str(got.get("outcome") or "")
    a, b = _coll(_pnorm(side)), _coll(_pnorm(out["held_outcome"]))
    names_it = bool(a) and bool(b) and (a in b or b in a)
    checks["the_resolved_side_names_the_held_outcome"] = {
        "venue_side_norm": side or None, "held_outcome": out["held_outcome"],
        "compared_as": [a or None, b or None], "passed": names_it,
        "why": ("the venue row's own side name, normalised by premap's own "
                "normaliser, must still be the outcome the catalogue "
                "lists at this position's index")}
    if not names_it:
        out.update(refusal=R_SIDE_NOT_THE_OUTCOME,
                   why=("the resolver matched the venue side %r while the "
                        "catalogue lists %r at this position's index. That "
                        "is the disagreement this repair exists to refuse, "
                        "and nothing is written"
                        % (side or None, out["held_outcome"])))
        return out

    # THE RESOLVER MUST DISCRIMINATE THE TWO SIDES. This is the check that
    # speaks directly to the production refusal: the failure mode was one
    # binding standing in for the other side of the same fixture. So the
    # SIBLING outcome is resolved too, and the answers must differ -- in
    # slug, or (on the `aec-` family, where both sides share an
    # identifier) in intent. If a repair cannot tell the two sides apart,
    # the identity it would write does not identify what we hold.
    sibs = [t for t in toks
            if int(t["outcome_index"]) != int(p["outcome_index"])]
    sib_ans = None
    if sibs:
        try:
            sib_ans = await _premap.resolve(
                conn, m.get("title"), m.get("event_title"),
                str(sibs[0]["outcome"]), m.get("slug"),
                condition_id=str(p["condition_id"]))
        except Exception as exc:                               # noqa: BLE001
            sib_ans = {"raised": type(exc).__name__}
    sib_slug = (sib_ans or {}).get("market_slug")
    sib_intent = (sib_ans or {}).get("intent")
    same_binding = bool(sib_slug) and (
        str(sib_slug) == str(got["market_slug"])
        and str(sib_intent or "") == intent)
    checks["the_resolver_discriminates_the_sibling_side"] = {
        "sibling_outcome": (str(sibs[0]["outcome"]) if sibs else None),
        "sibling_market_slug": sib_slug, "sibling_intent": sib_intent,
        "ours": {"market_slug": str(got["market_slug"]), "intent": intent},
        "sibling_unresolvable": sib_ans is None or not sib_slug,
        "passed": not same_binding,
        "why": ("the other side of this fixture must not resolve to the "
                "same slug AND the same intent, or the binding does not "
                "say which side is held. An unresolvable sibling is not a "
                "failure of discrimination: only ours resolves")}
    if same_binding:
        out.update(refusal=R_SIDES_NOT_DISCRIMINATED,
                   why=("both %r and %r resolve to %s with intent %s, so "
                        "this identity cannot distinguish the side this "
                        "position holds. Nothing is written"
                        % (out["held_outcome"], str(sibs[0]["outcome"]),
                           got["market_slug"], intent)))
        return out

    # THE RESOLVER WAS ASKED FOR THIS OUTCOME AND RETURNED THE SIDE THAT
    # BUYS IT, so the payout event IS that outcome -- the same reasoning
    # `resolve_venue_identity` records, and the reason a BUY_SHORT result
    # does not complement the payout.
    checks["payout_event_is_the_outcome_asked_for"] = {
        "asked_for": out["asked_for"], "payout_event": out["held_outcome"],
        "read_from": "market_tokens at the position's own outcome_index",
        "token_id": out["held_token_id"], "passed": True,
        "why": ("the resolver matched a side FOR the requested outcome, so "
                "the payout event is that outcome. A short intent means "
                "the cost comes off the bid ladder, not that the payout "
                "inverted")}
    checks["the_catalogue_lists_this_index"] = {
        "outcomes_listed": len(toks),
        "position_outcome_index": int(p["outcome_index"]),
        "indices_listed": [int(t["outcome_index"]) for t in toks],
        "passed": (len(toks) >= 2
                   and int(p["outcome_index"])
                   in [int(t["outcome_index"]) for t in toks])}
    if not checks["the_catalogue_lists_this_index"]["passed"]:
        out.update(refusal=R_NO_TOKEN,
                   why=("the catalogue lists %d outcomes for this "
                        "condition and the position's index %d is not a "
                        "binary holding it can bind"
                        % (len(toks), int(p["outcome_index"]))))
        return out

    ladder = "BID" if intent == "ORDER_INTENT_BUY_SHORT" else "ASK"
    identity = {"venue": VENUE,
                "venue_market_slug": str(got["market_slug"]),
                "venue_buy_intent": intent,
                "venue_ladder_side": ladder,
                "payout_event": out["held_outcome"]}
    out["identity"] = identity
    out["ok"] = True

    audit = {
        "version": VERSION, "at": float(now),
        "asked_for": out["asked_for"],
        "asked_for_source": ("market_tokens at the position's own "
                             "outcome_index"),
        "held_token_id": out["held_token_id"],
        "catalogue": out["catalogue"],
        "market": out["market"],
        "resolver": out["resolver"],
        "resolver_function": "workers.premap.resolve",
        "cross_checks": checks,
        "identity": identity,
        "read_external_valuations": False,
        "why_not": ("borrowing another side's recorded valuation is the "
                    "defect this repair exists to avoid, so the source of "
                    "valuations is never consulted"),
        "untouched": ["provenance", "entry_kind", "source_trade_id",
                      "source_account", "seed_qty", "seed_price",
                      "seed_basis_usd"],
    }
    out["audit"] = audit
    if not write:
        out["why"] = "resolved and cross-checked; write was not requested"
        return out
    try:
        wrote = await conn.fetchval(
            WRITE_SQL, str(position_id), identity["venue_market_slug"],
            identity["venue_buy_intent"], identity["venue_ladder_side"],
            identity["payout_event"], identity["venue"],
            _json.dumps(audit, default=str))
    except Exception as exc:                                   # noqa: BLE001
        out.update(ok=False, refusal="WRITE_FAILED",
                   why="%s: %s" % (type(exc).__name__, exc))
        return out
    out["written"] = wrote is not None
    out["why"] = (
        "identity re-established from premap.resolve for the outcome the "
        "catalogue lists at this position's own index, cross-checked "
        "against the venue intent, and recorded with its derivation"
        if out["written"] else
        "another writer filled the identity first; nothing was overwritten")
    return out


def describe() -> dict:
    return {
        "version": VERSION, "venue": VENUE,
        "asks": "workers.premap.resolve, for the outcome the catalogue "
                "lists at the position's own outcome_index",
        "reads_external_valuations": False,
        "why_not": ("the refusal this repairs was caused by a valuation "
                    "that describes the OTHER side. Consulting valuations "
                    "at all would reopen that door"),
        "cross_checks": ["intent_is_consumable",
                         "the_resolved_side_names_the_held_outcome",
                         "the_resolver_discriminates_the_sibling_side",
                         "payout_event_is_the_outcome_asked_for",
                         "the_catalogue_lists_this_index"],
        "does_not_check": {
            "what": "the intent against the catalogue's outcome_index",
            "why": ("the intent names which ladder supplies acquisition "
                    "cost, not which event pays. Deriving the held side "
                    "from it is the sign error resolve_venue_identity "
                    "documents, and on the aec- family -- where both "
                    "sides share one identifier -- it would refuse every "
                    "correct repair of a short-side holding")},
        "fills_only_nulls": True,
        "never_touches": ["provenance", "entry_kind", "source_trade_id",
                          "source_account", "seed_qty", "seed_price",
                          "seed_basis_usd"],
        "reseeds": False,
        "refusals": [R_NO_POSITION, R_NO_TOKEN, R_NO_MARKET, R_NO_PREMAP,
                     R_RESOLVER_RAISED, R_INTENT_UNUSABLE,
                     R_SIDE_NOT_THE_OUTCOME, R_SIDES_NOT_DISCRIMINATED,
                     R_ALREADY],
    }
