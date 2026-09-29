"""EXPOSURE ON THE ACCOUNT, ACROSS EVERY PATH THAT CAN ADD TO IT.

WHAT THIS REPLACES, AND IT WAS A CLAIM OF MINE THAT DID NOT HOLD UP.

I wrote, in the owner decision package:

    "Cross-lane account exposure is not implemented. Until it is, the pilot
     procedure below holds ONE POSITION AT A TIME, which makes the gap
     unreachable in practice rather than merely unlikely."

**That does not follow, and it is withdrawn.** One position IN THIS LANE says
nothing about what any OTHER path has already put on the same account. The venue
sees one account. If a manual desk, the legacy copier, or an open order from last
week is holding exposure there, then "this lane holds one position" is a fact
about this lane and not about the account's risk. The unique index that enforces
one live intent is **lane-local** -- it is a constraint on rows in
`bettor_funded_intents`, and a row in `live_orders` does not violate it.

SO THERE ARE EXACTLY TWO HONEST ROUTES, and this module implements the machinery
for both rather than choosing by assertion:

  ENFORCE   measure exposure across every path that shares the account, and gate
            on the total. `account_exposure()` does the measuring.

  ISOLATE   demonstrate that no other automated or manual path CAN add exposure
            under the approved scope. `isolation_evidence()` states what that
            demonstration requires, and returns NOT_DEMONSTRATED until each part
            is supplied -- because "we do not think anything else trades there"
            is not a demonstration.

WHAT COUNTS AS EXPOSURE, AND THE LIST IS DELIBERATELY WIDER THAN "POSITIONS".

Three things can commit the account and only the first is a holding:

  HELD          contracts already owned. Obvious, and the only one usually
                counted.
  WORKING       open orders. Not yet a holding and already a commitment: an
                order resting at a price can fill at any moment, and exposure
                measured without it understates by the whole working amount.
  UNRESOLVED    submissions whose outcome we do not know. The dangerous class.
                A submission that timed out may have reached the venue. Treating
                an unknown as zero is the assumption that turns a duplicate into
                a double position, so an unresolved submission is counted AT
                FULL SIZE until it is resolved.

FAIL CLOSED, AND THAT IS THE WHOLE POINT. If any path cannot be read, the total
is NOT a number -- it is `UNREADABLE`, and a caller that gates on exposure must
refuse. A partial sum over the paths that happened to answer is worse than no
answer, because it looks like an answer. The failure mode this exists to prevent
is a gate that passes because the table it should have consulted was missing.

WHAT THIS MODULE DOES NOT DO. It does not contact the venue. The venue's own
position list is the authority on what the account holds, and reading it needs a
credential this conversation does not have and must not ask for. That is named
as a REQUIRED path in `PATHS` with `source: VENUE`, and its absence makes the
total `UNREADABLE` rather than complete -- which is the correct state and is
exactly the difference between measuring exposure and assuming it.
"""

from __future__ import annotations

#: Exposure classes. Each one is a commitment; only the first is a holding.
HELD = "HELD_CONTRACTS"
WORKING = "WORKING_ORDERS"
UNRESOLVED = "UNRESOLVED_SUBMISSIONS"
CLASSES = (HELD, WORKING, UNRESOLVED)

#: A path's read outcome.
READ_OK = "READ"
READ_ABSENT = "TABLE_ABSENT"
READ_FAILED = "READ_FAILED"

#: The total's own state. There is no third option where a number is partial.
TOTAL_MEASURED = "ACCOUNT_EXPOSURE_MEASURED"
TOTAL_UNREADABLE = "ACCOUNT_EXPOSURE_UNREADABLE"

R_UNREADABLE = "R_ACCOUNT_EXPOSURE_UNREADABLE"
R_NOT_ISOLATED = "R_ACCOUNT_ISOLATION_NOT_DEMONSTRATED"

#: EVERY PATH THAT CAN PUT EXPOSURE ON THIS ACCOUNT.
#:
#: `required` means the total is UNREADABLE without it. A path is required when
#: it can add REAL exposure; a modelled lane cannot, and is listed with
#: `required: False` so it is visibly considered rather than silently omitted.
PATHS = (
    {
        "path": "THIS_LANE",
        "source": "DB",
        "tables": ("bettor_funded_intents", "bettor_funded_fills"),
        "required": True,
        "adds_real_exposure": True,
        "why": ("the autonomous EV lane's own intents and fills. This is the "
                "only path the lane-local unique index constrains"),
    },
    {
        "path": "LEGACY_COPIER",
        "source": "DB",
        "tables": ("live_orders",),
        "required": True,
        "adds_real_exposure": True,
        "why": ("the earlier live beta's real orders. Production holds 166,585 "
                "rows. Most are settled, and a row in a working or unknown "
                "state is live exposure this lane cannot see and its unique "
                "index does not constrain"),
    },
    {
        "path": "MANUAL_DESK",
        "source": "DB",
        "tables": ("live_orders",),
        "required": True,
        "adds_real_exposure": True,
        "why": ("migration 014 routes a 'manual' sleeve into live_orders, so a "
                "human placing a trade lands in the same table and must be "
                "counted the same way"),
    },
    {
        "path": "VENUE_HELD_POSITIONS",
        "source": "VENUE",
        "tables": (),
        "required": True,
        "adds_real_exposure": True,
        "why": ("THE AUTHORITY. What the account actually holds is what the "
                "venue says it holds, including anything put there by a path "
                "this repository does not know about. Nothing in our database "
                "can substitute for it"),
        "needs": ("a read of the venue's own position list, which needs the "
                  "account credential. Not requested here and not present"),
    },
    {
        "path": "SHADOW_LANES",
        "source": "DB",
        "tables": ("rn1x_orders", "bettor_desk_positions",
                   "shadow_positions"),
        "required": False,
        "adds_real_exposure": False,
        "why": ("MODELLED only. These rows are marked is_modelled and their "
                "writers CHECK that nothing was submitted, so they cannot "
                "commit the account. Listed so the exclusion is a decision on "
                "the record rather than an omission"),
    },
)

REQUIRED_PATHS = tuple(p["path"] for p in PATHS if p["required"])

#: WHAT AN ISOLATION DEMONSTRATION WOULD HAVE TO SHOW. Each element is a thing
#: that can be exhibited or refuted; none of them is a belief.
ISOLATION_REQUIREMENTS = (
    {
        "id": "NO_OTHER_CREDENTIAL",
        "claim": ("no credential other than the one bound to this lane can "
                  "place an order on this account"),
        "how_it_would_be_shown": ("the venue's own list of API keys for the "
                                  "account, showing exactly one, plus its "
                                  "permissions"),
        "a_lane_local_index_does_not_show_it": True,
    },
    {
        "id": "NO_MANUAL_ACCESS",
        "claim": "no human can place a trade on this account by hand",
        "how_it_would_be_shown": ("the account's web/app session state and who "
                                  "holds the login. This is an organisational "
                                  "fact, not a code fact, and it is the "
                                  "owner's to state"),
    },
    {
        "id": "NO_PRE_EXISTING_HOLDINGS",
        "claim": "the account holds nothing before the pilot starts",
        "how_it_would_be_shown": ("a venue position read returning empty, "
                                  "dated, at a known instant"),
    },
    {
        "id": "NO_OPEN_ORDERS",
        "claim": "the account has no resting orders before the pilot starts",
        "how_it_would_be_shown": "a venue open-order read returning empty",
    },
    {
        "id": "NO_UNRESOLVED_SUBMISSIONS",
        "claim": ("no submission against this account has an unknown "
                  "outcome"),
        "how_it_would_be_shown": ("our own unresolved set empty, AND the "
                                  "venue's order history showing nothing we "
                                  "cannot account for"),
    },
)

ISOLATION_DEMONSTRATED = "ACCOUNT_ISOLATION_DEMONSTRATED"
ISOLATION_NOT_DEMONSTRATED = "ACCOUNT_ISOLATION_NOT_DEMONSTRATED"


async def _table_exists(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                       name))
    except Exception:                                          # noqa: BLE001
        return False


async def _this_lane(conn) -> dict:
    """Held, working and unresolved from the funded lane's own tables."""
    out = {"read": READ_OK, HELD: 0.0, WORKING: 0.0, UNRESOLVED: 0.0,
           "detail": {}}
    if not await _table_exists(conn, "bettor_funded_intents"):
        return dict(out, read=READ_ABSENT)
    try:
        # HELD: entry fills minus exit fills, at cost.
        held = await conn.fetchval(
            "SELECT coalesce(sum(CASE WHEN f.direction='ENTRY' "
            "                        THEN f.qty * f.price ELSE 0 END)"
            "     - sum(CASE WHEN f.direction<>'ENTRY' "
            "                        THEN f.qty * f.price ELSE 0 END), 0)"
            "       ::float8 FROM bettor_funded_fills f")
        # WORKING: an intent that has been sent and is not terminal reserves
        # its WHOLE collateral, not its filled part -- the unfilled remainder
        # can still fill.
        working = await conn.fetchval(
            "SELECT coalesce(sum(collateral_usd),0)::float8 "
            "  FROM bettor_funded_intents "
            " WHERE state NOT IN ('FILLED','CANCELLED','REJECTED','CLOSED',"
            "                     'SETTLED','INTENT_RECORDED')")
        unres = await conn.fetchval(
            "SELECT coalesce(sum(collateral_usd),0)::float8 "
            "  FROM bettor_funded_intents WHERE state='UNRESOLVED'")
        out[HELD] = float(held or 0.0)
        out[WORKING] = float(working or 0.0)
        out[UNRESOLVED] = float(unres or 0.0)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, read=READ_FAILED, error=type(exc).__name__)
    return out


async def _live_orders(conn) -> dict:
    """The legacy copier AND the manual sleeve, which share one table.

    THE STATE SET IS READ THE CONSERVATIVE WAY. `submitting` is an UNRESOLVED
    submission, not a zero: the row exists because a request went out, and its
    outcome is unknown until it settles into filled or rejected. `error` is the
    same -- an error writing our row does not prove nothing reached the venue.
    """
    out = {"read": READ_OK, HELD: 0.0, WORKING: 0.0, UNRESOLVED: 0.0,
           "detail": {}}
    if not await _table_exists(conn, "live_orders"):
        return dict(out, read=READ_ABSENT)
    try:
        rows = await conn.fetch(
            "SELECT status, count(*) AS n, "
            "       coalesce(sum(filled_usd),0)::float8 AS filled, "
            "       coalesce(sum(requested_usd),0)::float8 AS requested "
            "  FROM live_orders GROUP BY status")
        for r in rows:
            st = str(r["status"])
            out["detail"][st] = {"rows": int(r["n"]),
                                 "filled_usd": float(r["filled"]),
                                 "requested_usd": float(r["requested"])}
            if st == "filled":
                out[HELD] += float(r["filled"])
            elif st in ("submitting", "error"):
                # AT FULL REQUESTED SIZE. An unknown outcome counted at its
                # filled amount would count a timed-out submission as zero.
                out[UNRESOLVED] += float(r["requested"])
            # 'unfilled', 'rejected' and 'settled' commit nothing further.
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, read=READ_FAILED, error=type(exc).__name__)
    return out


async def account_exposure(conn, *, account_id=None,
                           venue_positions=None, now=None) -> dict:
    """TOTAL EXPOSURE ON THE ACCOUNT, or `UNREADABLE`. Never a partial sum.

    `venue_positions` is the venue's own answer, which the caller supplies
    because this module does not hold a credential. Its shape is
    `{"held_usd": float, "working_usd": float, "unresolved_usd": float,
      "read_at_epoch_s": float}`. Omitted, the total is UNREADABLE and says
    which path is missing.
    """
    per_path = {}
    lane = await _this_lane(conn)
    per_path["THIS_LANE"] = lane
    lo = await _live_orders(conn)
    # ONE TABLE, TWO PATHS. `live_orders` carries both the legacy copier and
    # the manual sleeve, and the table does not separate them. Reporting the
    # same figures under both names would DOUBLE COUNT, so the read is
    # attributed once and the second path records why it has no separate sum.
    per_path["LEGACY_COPIER"] = lo
    per_path["MANUAL_DESK"] = {
        "read": lo["read"],
        HELD: 0.0, WORKING: 0.0, UNRESOLVED: 0.0,
        "counted_under": "LEGACY_COPIER",
        "why_zero_here": ("live_orders holds both sleeves and does not "
                          "separate them, so the total is attributed once. "
                          "This is not a claim that the manual sleeve is "
                          "empty"),
    }
    # ── THE VENUE'S FIGURES, EACH ONE STATED OR THE PATH IS UNREADABLE ──
    #
    # This used to read `float(venue_positions.get("held_usd") or 0.0)`, so a
    # caller's dict that omitted a figure -- or carried None, NaN or a string
    # -- produced a total with that path counted as ZERO. Missing exposure is
    # not zero exposure: every one of the three must be a finite,
    # non-negative number, and anything else makes the path READ_FAILED and
    # the total UNREADABLE, by name.
    def _venue_figures(vp):
        import math as _m
        got, bad = {}, []
        for key, name in (("held_usd", HELD), ("working_usd", WORKING),
                          ("unresolved_usd", UNRESOLVED)):
            v = vp.get(key)
            try:
                f = None if isinstance(v, bool) or v is None else float(v)
            except (TypeError, ValueError):
                f = None
            if f is None or not _m.isfinite(f) or f < 0:
                bad.append(key)
            else:
                got[name] = f
        return got, bad

    if isinstance(venue_positions, dict):
        figs, bad = _venue_figures(venue_positions)
        if bad:
            per_path["VENUE_HELD_POSITIONS"] = {
                "read": READ_FAILED,
                "error": "VENUE_READ_INCOMPLETE",
                "missing_or_unreadable": bad,
                "why": ("the venue read was supplied without a measurable %s, "
                        "and an unstated figure is not zero" % ", ".join(bad)),
            }
        else:
            per_path["VENUE_HELD_POSITIONS"] = dict(
                figs, read=READ_OK,
                read_at_epoch_s=venue_positions.get("read_at_epoch_s"))
    else:
        per_path["VENUE_HELD_POSITIONS"] = {
            "read": READ_FAILED,
            "error": "NO_VENUE_READ_SUPPLIED",
            "why": ("the venue is the authority on what the account holds and "
                    "reading it needs the account credential. Without it this "
                    "total cannot be complete, and an incomplete total must "
                    "not look like a number"),
        }
    per_path["SHADOW_LANES"] = {
        "read": READ_OK, HELD: 0.0, WORKING: 0.0, UNRESOLVED: 0.0,
        "why_zero": ("modelled rows cannot commit the account; their writers "
                     "CHECK that nothing was submitted"),
    }

    # AN ABSENT TABLE AND A FAILED READ ARE DIFFERENT FINDINGS, and lumping
    # them together would be wrong in the direction that refuses everything.
    #
    #   TABLE_ABSENT   the table does not exist, so it holds no rows, so this
    #                  path's exposure is KNOWABLY zero. That is a measurement,
    #                  not a guess -- a lane that was never migrated cannot have
    #                  committed the account.
    #   READ_FAILED    the table may exist and hold rows we could not read. The
    #                  exposure is UNKNOWN, and unknown must not become zero.
    #
    # The venue path is the one that matters here: it reports READ_FAILED
    # without a credential, never TABLE_ABSENT, so no missing migration can
    # quietly excuse the authority.
    unreadable = [n for n in REQUIRED_PATHS
                  if per_path[n]["read"] not in (READ_OK, READ_ABSENT)]
    absent = [n for n in REQUIRED_PATHS
              if per_path[n]["read"] == READ_ABSENT]
    import time as _time
    out = {
        "account_id": account_id,
        # WHEN THIS WAS MEASURED, ALWAYS. A total with no instant cannot be
        # shown to be current, and the submission gate refuses an undated one
        # rather than trusting it -- so the stamp is not optional metadata.
        "measured_at_epoch_s": float(now if now is not None else _time.time()),
        "paths": per_path,
        "required_paths": list(REQUIRED_PATHS),
        "unreadable_required_paths": unreadable,
        "absent_required_paths": absent,
        "an_absent_table_is_zero_a_failed_read_is_not": (
            "a table that does not exist holds no rows, so its exposure is "
            "knowably zero. A table we could not read may hold anything, and "
            "an unknown must never become a zero"),
        "classes_counted": list(CLASSES),
        "an_unresolved_submission_counts_at_full_size": True,
        "a_working_order_counts_its_whole_collateral": True,
        "lane_local_index_does_not_bound_this": (
            "the one-live-intent unique index constrains rows in "
            "bettor_funded_intents. A row in live_orders does not violate it, "
            "so it bounds THIS LANE and not the account"),
    }
    if unreadable:
        out["state"] = TOTAL_UNREADABLE
        out["TOTAL_USD"] = None
        out["BLOCKER"] = R_UNREADABLE
        out["why"] = (
            "exposure on this account cannot be measured: %s unreadable. A sum "
            "over the paths that answered would look like a total and would "
            "understate the account by whatever the missing paths hold"
            % ", ".join(unreadable))
        return out
    totals = {c: round(sum(float(per_path[n].get(c) or 0.0)
                           for n in per_path), 6) for c in CLASSES}
    out["state"] = TOTAL_MEASURED
    out["by_class"] = totals
    out["TOTAL_USD"] = round(sum(totals.values()), 6)
    out["BLOCKER"] = None
    out["why"] = ("every path that can commit this account was read and "
                  "summed, including working orders and unresolved "
                  "submissions")
    return out


def isolation_evidence(supplied=None) -> dict:
    """IS THIS ACCOUNT DEMONSTRABLY ISOLATED? Not unless each part is shown.

    `supplied` maps a requirement id to the evidence for it. Anything absent is
    reported as absent. There is deliberately no way to pass this by asserting
    it: the default is NOT_DEMONSTRATED, and the caller cannot shorten the list.
    """
    got = supplied if isinstance(supplied, dict) else {}
    reqs = []
    for r in ISOLATION_REQUIREMENTS:
        ev = got.get(r["id"])
        reqs.append(dict(r, satisfied=bool(ev), evidence=ev))
    missing = [r["id"] for r in reqs if not r["satisfied"]]
    return {
        "verdict": (ISOLATION_DEMONSTRATED if not missing
                    else ISOLATION_NOT_DEMONSTRATED),
        "requirements": reqs,
        "missing": missing,
        "BLOCKER": None if not missing else R_NOT_ISOLATED,
        "a_one_position_pilot_does_not_demonstrate_this": (
            "holding one position in THIS lane is a fact about this lane. The "
            "venue sees one account, and another path's exposure on it is "
            "unaffected by how many positions this lane holds"),
        "what_I_withdrew": (
            "I wrote that a one-position-at-a-time pilot 'makes the gap "
            "unreachable in practice'. It does not, and the claim is "
            "withdrawn"),
        "the_two_routes": {
            "ENFORCE": "measure across every path and gate on the total",
            "ISOLATE": "demonstrate every requirement above",
        },
    }


def describe() -> dict:
    return {
        "lane_local_index_is_insufficient": (
            "the one-live-intent guarantee is a UNIQUE INDEX on "
            "bettor_funded_intents. A row in live_orders does not violate it, "
            "so it bounds THIS LANE's rows and not the account's risk"),
        "paths": [dict(p) for p in PATHS],
        "required_paths": list(REQUIRED_PATHS),
        "classes": list(CLASSES),
        "fails_closed": ("any required path unreadable makes the total "
                         "UNREADABLE, never a partial sum"),
        "isolation_requirements": [r["id"] for r in ISOLATION_REQUIREMENTS],
        "withdrawn_claim": isolation_evidence()["what_I_withdrew"],
    }
