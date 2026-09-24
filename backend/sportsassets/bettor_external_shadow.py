"""THE EXTERNAL-VALUATION SHADOW EXPERIMENT, kept apart from everything.

It is its own experiment id, its own table and its own status tile. It
shares the entry gate, the fee schedule and the shadow order engine,
because sharing those is the point -- a valuation that only works through
a bespoke path has not been integrated. It shares NOTHING with RN1-seeded
management: different experiment, different rows, and the frozen pairing
and second-half exit policy are not consulted here at all.

WHAT IT DOES, in one line: compare an external bookmaker's de-vigged
probability against the SAME-VENUE executable ask after costs, and record
the comparison whether or not it clears.

WHAT IT REFUSES TO DO:

  * assume a resting fill. The comparison is against the ASK, crossing,
    because a resting price invents a queue position we never held. The
    execution estimate must be supplied and `p_fill` is never defaulted.
  * invent a second-half signal. This experiment only prices FULL-GAME
    markets; `period` must match explicitly and a segment contract is
    refused by PINNACLE_DEVIG_V1 rather than priced off the full game.
  * touch management's frozen exit policy. There is no exit logic here.
    An admitted entry is recorded as a shadow decision; the RN1X pairing
    and 16% trigger are a different experiment on different rows.
  * submit an order. `order_submitted` is always False and there is no
    code path that sets it True.

WHY IT IS SEPARATE FROM `bettor_fair_value`. That module reports
FV_BETTOR_INDEPENDENT = NOT_IDENTIFIED because the internal challenger was
measured WORSE than the venue price. This experiment does not revisit that
result, does not blend with it, and does not claim to supersede it. It is
a different question: not "is our model better than the market" but "does
a sharp book disagree with this venue by more than the cost of crossing".
"""

from __future__ import annotations

from . import bettor_entry_gate as gate
from . import bettor_pinnacle_devig as devig

EXPERIMENT_ID = "EXT_PINNACLE_DEVIG_V1_SHADOW"
LABEL = ("EXTERNAL BOOKMAKER VALUATION, EXPERIMENTAL SHADOW. Not a trained "
         "proprietary model, not an internally qualified settlement model, "
         "and not shown to be profitable")

#: The control row that enables it. Absence is NOT permission, matching
#: every other loop in this stack.
CONTROL_KEY = "ext_pinnacle_shadow"

#: Minimum net edge per contract, in dollars, before an entry is
#: admissible. Deliberately > 0: at exactly zero the trade is a coin flip
#: that pays the fee, and the engine's own band study says the smallest
#: apparent edges are where thin consensus lives.
MIN_NET_EDGE_PER_CONTRACT = 0.01

#: Require the anchor book plus at least this many distinct sharp books on
#: the SAME outcome. The feed module's note is the reason: "a 1c edge
#: agreed by six books is a signal; the same 1c from one book is a
#: rounding error", and its per-OUTCOME depth exists because event-level
#: depth answered the wrong question (the winner's curse audit).
MIN_OUTCOME_BOOKS = 2

R_CONTROL_OFF = "EXPERIMENT_NOT_ARMED"
R_THIN_OUTCOME = "OUTCOME_DEPTH_BELOW_FLOOR"
R_NO_CREDENTIAL = "ODDS_CREDENTIAL_NOT_PRESENT_ON_THIS_SERVICE"


def credential_present(env=None) -> dict:
    """Is the odds key readable HERE? Reported, never printed.

    The key exists: `EDGE_ODDS_API_KEY` (32 chars) is provisioned on
    edge-shadow and is a repository secret. It is NOT on
    sportsassets-api, where this decision path runs -- env-keys at
    2026-09-23T22:10:54Z lists no odds-feed key at all. Copying a
    production credential between services is refused, so provisioning it
    on the target service is an owner action and this reports the absence
    by name instead of pretending to a feed it cannot reach.
    """
    import os

    src = os.environ if env is None else env
    present = bool(str(src.get("EDGE_ODDS_API_KEY") or "").strip())
    return {
        "present": present,
        "variable": "EDGE_ODDS_API_KEY",
        "refusal": None if present else R_NO_CREDENTIAL,
        "known_locations": ["edge-shadow (Render env)",
                            "EDGE_ODDS_API_KEY (repository secret)"],
        "why": (None if present else
                ("the valuation source is wired and fail-closed: it refuses "
                 "by name rather than reaching for a feed it has no key "
                 "for. Provisioning the key on this service is an owner "
                 "action; a credential is never copied between services "
                 "from here")),
    }


def evaluate(*, contract, quote, market_state, execution_estimate, size,
             risk, fee_fn, now, method=devig.DEFAULT_METHOD,
             outcome_books=None, armed=False,
             min_net_edge_per_contract=MIN_NET_EDGE_PER_CONTRACT,
             extra_refusals=None, payout_is_complement=False) -> dict:
    """One contract, end to end, through the REAL gate.

    Returns a record that is persisted whether or not it clears, because
    the refusals are the deliverable when nothing clears.
    """
    val = devig.valuation(contract=contract, quote=quote, now=now,
                          method=method)
    # THE EVENT THE CONTRACT ACTUALLY PAYS ON.
    #
    # The de-vig prices the SELECTION. On this venue a BUY_SHORT leg of
    # the same market pays on the COMPLEMENT of that selection, so the
    # probability to compare against its cost is 1 - p(selection).
    #
    # INVERTED EXACTLY ONCE, HERE. The caller supplies the flag and does
    # not pre-invert; the acquisition price it passes is already in cost
    # space (bettor_book_snapshot.acquisition_ladder). Inverting in both
    # places would silently restore the original outcome and look like a
    # working edge.
    #
    # The de-vig normalises over the COMPLETE outcome set and refuses a
    # partial one, so on a three-way book 1 - p(home) is exactly
    # p(away) + p(draw). It is NOT p(away): "NO home win" includes the
    # draw, and substituting the other team's price would be a different
    # event wearing the same number.
    _p_sel = val.get("probability")
    _p_pay = (None if _p_sel is None
              else (1.0 - float(_p_sel)) if payout_is_complement
              else float(_p_sel))
    rec: dict = {
        "experiment_id": EXPERIMENT_ID,
        "label": LABEL,
        "version": devig.VERSION,
        "source_class": devig.SOURCE_CLASS,
        "devig_method": method,
        "contract": dict(contract),
        "valuation": val,
        "probability": _p_pay,
        "probability_of_selection": _p_sel,
        "payout_is_complement": bool(payout_is_complement),
        "payout_event": (("NOT(%s)" % contract.get("selection"))
                         if payout_is_complement
                         else contract.get("selection")),
        "complement_note": (
            "probability is the probability of the event THIS CONTRACT "
            "PAYS ON. probability_of_selection is the de-vig's number for "
            "the selection itself. On a three-way book the complement of "
            "one outcome is the other two together, never the opposing "
            "team alone"),
        "raw_odds": val.get("raw_odds"),
        "observed_at": val.get("observed_at"),
        "received_at": val.get("received_at"),
        "age_s": val.get("age_s"),
        "mapped_outcome": val.get("mapped_outcome"),
        "order_submitted": False,
        "shadow_only": True,
        "refusals": list(val.get("refusals") or []),
    }

    # REFUSALS THE CALLER ALREADY ESTABLISHED, carried in rather than
    # short-circuited. The runtime loop can only learn some things --
    # whether the venue's settlement rule is established, for instance --
    # after it has done work this function would otherwise repeat. Handing
    # them in keeps the RECORD complete: the row still carries the odds,
    # the mapping, the venue quote, the probability and the costs, so
    # management can see what the engine was looking at when it refused,
    # instead of the row not existing at all.
    for code in (extra_refusals or []):
        if code not in rec["refusals"]:
            rec["refusals"].append(str(code))
    rec["caller_refusals"] = [str(c) for c in (extra_refusals or [])]

    if not armed:
        # Checked BEFORE anything else consumes budget or claims a
        # decision: an unarmed experiment records why and stops.
        rec["refusals"].insert(0, R_CONTROL_OFF)
        rec["decision"] = "NO_TRADE"
        rec["why"] = "the experiment's control row is not true"
        return rec

    # PER-OUTCOME depth, not per-event. The anchor alone is a rounding
    # error; the feed module's own audit is cited in MIN_OUTCOME_BOOKS.
    books = outcome_books if outcome_books is None else int(outcome_books)
    rec["outcome_books"] = books
    if _p_pay is not None and (
            books is None or books < MIN_OUTCOME_BOOKS):
        rec["refusals"].append(R_THIN_OUTCOME)

    ask = (market_state or {}).get("ask")
    fee_per = None
    if ask is not None and fee_fn is not None:
        try:
            fee_per = abs(float(fee_fn(qty=1.0, price=float(ask))))
        except Exception:                                      # noqa: BLE001
            fee_per = None
    rec["executable_price"] = None if ask is None else float(ask)
    rec["cost_per_contract"] = fee_per
    if _p_pay is not None and ask is not None \
            and fee_per is not None:
        # THE COMPARISON, stated once: the probability of the event this
        # contract PAYS ON, against the ACQUISITION price of that same
        # contract, after the cost of crossing. Both sides of this
        # subtraction describe the same payout event.
        rec["estimated_edge_per_contract"] = (
            float(_p_pay) - float(ask) - fee_per)
    else:
        rec["estimated_edge_per_contract"] = None

    admitted = gate.admit(
        action_table=None,
        model=None,
        fair_value=({"value": _p_pay,
                     "kind": devig.SOURCE_CLASS}
                    if _p_pay is not None else None),
        execution_estimate=execution_estimate,
        size=size, risk=risk, market_state=market_state, fee_fn=fee_fn,
        min_net_edge_per_contract=min_net_edge_per_contract,
        external_source=val if _p_pay is not None else None,
        external_enabled=True)
    rec["gate"] = admitted
    for code in admitted.get("refusals", []):
        if code not in rec["refusals"]:
            rec["refusals"].append(code)

    # A thin outcome is a refusal of OURS, so it must veto admission even
    # though the gate knows nothing about book depth. The same is true of
    # every refusal the caller handed in: a record that carries a refusal
    # and is still admissible would make the refusal decorative.
    rec["admissible"] = bool(admitted.get("admissible")) and \
        R_THIN_OUTCOME not in rec["refusals"] and \
        not rec["caller_refusals"]
    rec["decision"] = "BUY" if rec["admissible"] else "NO_TRADE"
    rec["proposed_size"] = size if rec["admissible"] else None
    rec["why"] = (("external probability %.4f on %s vs acquisition "
                   "%.4f less cost %.4f"
                   % (_p_pay, rec["payout_event"], float(ask), fee_per))
                  if rec["admissible"] else
                  ("; ".join(rec["refusals"]) or "no reason recorded"))
    return rec


def describe() -> dict:
    return {
        "experiment_id": EXPERIMENT_ID,
        "label": LABEL,
        "control_key": CONTROL_KEY,
        "source": devig.describe(),
        "min_net_edge_per_contract": MIN_NET_EDGE_PER_CONTRACT,
        "min_outcome_books": MIN_OUTCOME_BOOKS,
        "assumes_resting_fills": False,
        "prices_segments": False,
        "touches_frozen_exit_policy": False,
        "submits_orders": False,
        "separate_from_rn1_seeded_management": True,
        "credential": credential_present(),
    }


# ── persistence ─────────────────────────────────────────────────────

INSERT = """
    INSERT INTO external_valuations
        (experiment_id, version, source_class, provider, book, devig_method,
         venue, condition_id, us_market_slug, contract_identity_basis,
         contract_selection, sport_family, market,
         period, line, settlement_rule, event_key,
         raw_odds, outcomes_priced, expected_outcomes, overround,
         observed_at, received_at, age_s, outcome_books,
         mapped_outcome, mapping_match,
         probability, executable_price, cost_per_contract,
         estimated_edge_per_contract,
         decision, admissible, refusals, why, proposed_size)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
            $18::jsonb,$19,$20,$21,
            CASE WHEN $22::double precision IS NULL THEN NULL
                 ELSE to_timestamp($22) END,
            CASE WHEN $23::double precision IS NULL THEN NULL
                 ELSE to_timestamp($23) END,
            $24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35,$36)
    -- BARE `DO NOTHING`, deliberately. Migration 105's uniqueness is an
    -- EXPRESSION index (coalesce over the nullable key columns), and
    -- `ON CONFLICT ON CONSTRAINT` cannot name an index, while inferring
    -- it would mean repeating the whole coalesce list here and keeping
    -- two copies in step. The only unique things on this table are the
    -- serial primary key -- which this statement never supplies -- and
    -- that index, so an untargeted DO NOTHING can only mean "this
    -- observation is already recorded".
    --
    -- A skipped insert RETURNS NO ROW, so `persist` returns None and the
    -- caller must not count it as written.
    ON CONFLICT DO NOTHING
    RETURNING id
"""

JOIN_OUTCOME = """
    UPDATE external_valuations
       SET outcome_known = TRUE, outcome = $2,
           outcome_at = to_timestamp($3), realised_net_usd = $4
     WHERE id = $1 AND outcome_known = FALSE
"""


async def persist(conn, rec: dict) -> int | None:
    """Store the record -- admissible or refused, both.

    Returns the row id. A refused record is stored with its full refusal
    list because that list is what management inspects to see WHY the
    engine did not buy.
    """
    import json

    c = rec.get("contract") or {}
    v = rec.get("valuation") or {}
    return await conn.fetchval(
        INSERT,
        rec.get("experiment_id") or EXPERIMENT_ID,
        rec.get("version") or devig.VERSION,
        rec.get("source_class") or devig.SOURCE_CLASS,
        devig.PROVIDER, devig.BOOK,
        rec.get("devig_method") or devig.DEFAULT_METHOD,
        str(c.get("venue") or ""), c.get("condition_id"),
        # THE VENUE-NATIVE IDENTITY, beside the global one and never
        # derived from it. A row that carries only the global id cannot be
        # looked up at the venue, which is the defect this fixes.
        c.get("us_market_slug"),
        (c.get("contract_identity_basis")
         or ("BOTH_PRESENT_AND_INDEPENDENTLY_SOURCED"
             if c.get("condition_id") and c.get("us_market_slug")
             else "VENUE_NATIVE_US_SLUG" if c.get("us_market_slug")
             else "GLOBAL_CONDITION_ID" if c.get("condition_id")
             else "NEITHER_IDENTITY_RECORDED")),
        str(c.get("selection") or ""), str(c.get("sport_family") or ""),
        str(c.get("market") or ""),
        (None if c.get("period") is None else str(c["period"])),
        (None if c.get("line") is None else float(c["line"])),
        (None if c.get("settlement_rule") is None
         else str(c["settlement_rule"])),
        (None if c.get("event_key") is None else str(c["event_key"])),
        json.dumps(v.get("raw_odds") or {}),
        int(v.get("outcomes_priced") or 0),
        int(v.get("expected_outcomes") or 0),
        (None if v.get("overround") is None else float(v["overround"])),
        rec.get("observed_at"), rec.get("received_at"),
        (None if rec.get("age_s") is None else float(rec["age_s"])),
        rec.get("outcome_books"),
        rec.get("mapped_outcome"), v.get("mapping_match"),
        (None if rec.get("probability") is None
         else float(rec["probability"])),
        rec.get("executable_price"), rec.get("cost_per_contract"),
        rec.get("estimated_edge_per_contract"),
        rec.get("decision") or "NO_TRADE",
        bool(rec.get("admissible")),
        list(rec.get("refusals") or []),
        str(rec.get("why") or "")[:2000],
        rec.get("proposed_size"))


REFUSAL_CENSUS = """
    SELECT unnest(refusals) AS refusal, count(*) AS n
      FROM external_valuations
     WHERE experiment_id = $1 AND decided_at >= now() - ($2 || ' hours')::interval
     GROUP BY 1 ORDER BY 2 DESC
"""

SUMMARY = """
    SELECT count(*) AS evaluated,
           count(*) FILTER (WHERE admissible) AS admissible,
           count(*) FILTER (WHERE NOT admissible) AS refused,
           count(*) FILTER (WHERE probability IS NOT NULL) AS priced,
           count(*) FILTER (WHERE outcome_known) AS settled,
           min(decided_at) AS first_at, max(decided_at) AS last_at,
           -- WHICH IDENTITY EACH ROW CARRIES. A count of valuations says
           -- nothing about whether the contracts can be looked up at the
           -- venue, and that distinction is the whole of run 24's finding.
           count(*) FILTER (WHERE us_market_slug IS NOT NULL)
               AS with_venue_native_identity,
           count(*) FILTER (WHERE condition_id IS NOT NULL)
               AS with_global_condition_id,
           count(*) FILTER (WHERE us_market_slug IS NOT NULL
                              AND condition_id IS NOT NULL)
               AS with_both
      FROM external_valuations
     WHERE experiment_id = $1
"""

#: The identity split on its own, so the census can show it per basis.
IDENTITY_CENSUS = """
    SELECT coalesce(contract_identity_basis, 'UNLABELLED') AS basis,
           count(*) AS n,
           count(*) FILTER (WHERE admissible) AS admissible,
           max(decided_at) AS last_at
      FROM external_valuations
     WHERE experiment_id = $1
     GROUP BY 1 ORDER BY 1
"""


async def census(conn, *, hours: int = 24) -> dict:
    """What the engine did and, mostly, why it did not.

    THE REFUSAL DISTRIBUTION IS THE DELIVERABLE when nothing clears, so it
    is a first-class read rather than something to be reconstructed from
    rows by hand.
    """
    rows = await conn.fetch(REFUSAL_CENSUS, EXPERIMENT_ID, str(int(hours)))
    summ = await conn.fetchrow(SUMMARY, EXPERIMENT_ID)
    ident = await conn.fetch(IDENTITY_CENSUS, EXPERIMENT_ID)
    return {
        "experiment_id": EXPERIMENT_ID,
        "window_hours": int(hours),
        "refusals": {r["refusal"]: int(r["n"]) for r in rows},
        "summary": (dict(summ) if summ is not None else {}),
        # THE TWO IDENTITIES, REPORTED APART. `us_market_slug` is what the
        # venue accepts; `condition_id` is the global catalogue's id. A row
        # carrying only the latter is a valuation of a contract no venue
        # read can reach.
        "contract_identity": [dict(r) for r in ident],
        "identity_note": (
            "VENUE_NATIVE_US_SLUG is us_premap.market_slug, which "
            "pmus.book_read accepts. GLOBAL_CONDITION_ID is the global "
            "catalogue's id, which it does not. BOTH means each was "
            "sourced independently -- never one derived from the other"),
        "credential": credential_present(),
    }
