"""THE MIRROR SHADOW'S FALLBACK POSITIONS SOURCE: THE FUNDED ACCOUNT'S OWN LEDGER.

READ ONLY. NO ORDER PATH. This module issues exactly one SELECT, inside a
READ ONLY transaction, through a guard that refuses any statement that is not
a single SELECT / WITH. It imports no venue adapter and no order module (a test
pins both).

WHY IT EXISTS (P1 closeout, production 2026-10-06). The shadow
(workers/mirror_shadow) mirrors the funded Polymarket US retail account. Its
primary positions source is the venue's own walk (`portfolio.positions`),
signed with the Ed25519 key in the PMUS_KEY_ID / PMUS_SECRET_KEY slot. In
production that slot holds the PMX INSTITUTIONAL Auth0 client instead
(PMX_CLIENT_ID == PMUS_KEY_ID, an RSA PEM private key; market_data_identity
PMX_NON_PMUS_SLOT_BASIS), which PMUS cannot sign with. The walk is refused by
name (mirror_shadow.R_PMUS_SECRET_NOT_ED25519) and, before this module, every
tick abandoned: positions_unreadable, abandoned, exit_leg suppressed.

WHY NOT THE PMX POSITIONS READ. The institutional API does document an
authenticated positions read (GET /v1/positions?name=<account>; pmx.py
`_Portfolio`, research/institutional/pmx_production_read.py READ_ONLY_PATHS),
and the token carries `read:positions`. It was rejected as the fallback on the
repository's own evidence, because it reads A DIFFERENT ACCOUNT:
  * research/institutional/PRODUCTION_VERIFICATION_20260919.md: the production
    PMX identity is `firms/20260820-bettortokenllc-api-participant`, "an
    account that holds no funds", positions read "200 / 0 positions ...
    created and not funded";
  * research/institutional/pmx_production_read.py (header): "production
    credentials were issued to BettorToken LLC for an account that holds no
    funds";
  * pmx.py is the PRE-PRODUCTION adapter (dummy funds; no production host);
    pmx_institutional.py's allow-list is market data only.
Nothing in the repository maps the institutional trading account to the funded
retail account. Reading it would report the funded account as FLAT -- the one
answer a positions source must never invent -- so it is named here and not used
(PMX_FALLBACK_REJECTED).

WHAT IS USED INSTEAD: the account-wide ledger the system already keeps, the
same paths `bettor_account_exposure.PATHS` names as every DB path that can put
exposure on the account, in ONE statement (one Postgres snapshot):
  * live_orders            every retail sleeve (mirror, desk, manual):
                           status filled / exiting, venue 'polymarket-us',
                           signed by the order's intent exactly as
                           mirror_shadow.ledger_net signs it;
  * bettor_funded_intents  the funded lane's ENTRY holdings still holding
                           inventory (bettor_funded_holds_inventory), venue
                           class FUNDED, demonstration books excluded,
                           residual_qty signed by intent;
  * mirror_registered_positions  the owner's registered shares outside the
                           book (migration 056), signed.
Settled rows leave the sum by the ledger's own rules (live_orders moves to
'settled'; a funded holding gets closed_at only on an evidenced exit or an
authoritative settlement).

WHAT THIS SOURCE IS NOT, STATED SO NOBODY READS IT AS MORE: it is NOT the
venue's confirmation. On a slug held only through live_orders, venue == ledger
by construction, so the shadow's venue/ledger disagreement freeze cannot fire
there (VENUE_LEDGER_CHECK). Every reading carries its source and its as-of
instant, and the shadow stamps the source on every row it writes. The live
lane (mirror_live) never reads this module: it keeps the venue walk and its
own fail-closed rule unchanged.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

VERSION = "MIRROR_POSITIONS_SOURCE_V1"

#: The two sources the shadow can read positions from, by name.
SRC_VENUE = "PMUS_VENUE_POSITIONS_WALK"
SRC_LEDGER = "FUNDED_ACCOUNT_LEDGER_DERIVED"
AUTHORITY_LEDGER = "LEDGER_DERIVED_NOT_VENUE_CONFIRMED"
VENUE_LEDGER_CHECK = "VACUOUS_ON_SLUGS_HELD_ONLY_THROUGH_LIVE_ORDERS"
PMX_FALLBACK_REJECTED = ("PMX_INSTITUTIONAL_ACCOUNT_IS_NOT_THE_FUNDED_RETAIL_"
                         "ACCOUNT")

#: The tables the one statement reads (the account-wide DB paths).
TABLES = ("live_orders", "bettor_funded_intents", "mirror_registered_positions")

# ── refusals ──────────────────────────────────────────────────────────
#: the PMUS slot's credential is not the production topology this fallback
#: was built for (the PMX RSA client): no fallback, fail closed
R_SLOT_NOT_PMX_RSA = "PMUS_SLOT_CREDENTIAL_IS_NOT_THE_PMX_RSA_CLIENT"
#: the ledger statement raised, or answered something unreadable
R_LEDGER_POSITIONS_UNREADABLE = "MIRROR_SHADOW_LEDGER_POSITIONS_UNREADABLE"
#: neither the venue walk nor the ledger fallback produced a reading
R_NO_POSITIONS_SOURCE = "MIRROR_SHADOW_NO_POSITIONS_SOURCE_READABLE"
#: the guard: a statement that is not one SELECT / WITH is refused unsent
R_NOT_A_READ = "MIRROR_POSITIONS_SOURCE_STATEMENT_IS_NOT_A_READ"


class NotARead(RuntimeError):
    """A statement the read-only guard refused before it was sent."""


# THE INTENT EXPRESSION IS live_executor.ORDER_INTENT_SQL, and it is NOT copied
# here (tests/test_realized_pnl_sign pins its single definition) and NOT
# imported (this module imports no order module): the caller, which already
# reads it for its own ledger (mirror_shadow.ledger_net), hands it in, and
# `ledger_positions_sql` refuses anything that is not that expression's shape.
_INTENT_EXPR = re.compile(r"^COALESCE\(raw #>> '\{[a-z0-9,]+\}',\s*raw #>> '\{[a-z0-9,]+\}'\)$")

_LEDGER_POSITIONS_TEMPLATE = """
WITH lo AS (
    SELECT lower(btrim(us_market_slug)) AS slug,
           CASE WHEN {intent} = 'ORDER_INTENT_BUY_SHORT'
                THEN -1 ELSE 1 END * coalesce(filled_shares, 0)::float8 AS sh,
           'live_orders'::text AS src
      FROM live_orders
     WHERE status IN ('filled', 'exiting')
       AND venue = 'polymarket-us'
       AND us_market_slug IS NOT NULL
), fu AS (
    SELECT lower(btrim(us_market_slug)) AS slug,
           CASE WHEN order_intent = 'ORDER_INTENT_BUY_SHORT'
                THEN -1 ELSE 1 END * residual_qty::float8 AS sh,
           'bettor_funded_intents'::text AS src
      FROM bettor_funded_intents
     WHERE kind = 'ENTRY'
       AND venue_class = 'FUNDED'
       AND upper(account_id) NOT LIKE '%DEMONSTRATION%'
       AND public.bettor_funded_holds_inventory(residual_qty, closed_at)
), rg AS (
    SELECT lower(btrim(us_market_slug)) AS slug, shares::float8 AS sh,
           'mirror_registered_positions'::text AS src
      FROM mirror_registered_positions
), u AS (
    SELECT * FROM lo UNION ALL SELECT * FROM fu UNION ALL SELECT * FROM rg
)
SELECT now() AS as_of,
       coalesce((SELECT json_agg(json_build_object(
                    'slug', g.slug, 'src', g.src, 'net', g.net, 'n', g.n))
                   FROM (SELECT slug, src, sum(sh) AS net, count(*) AS n
                           FROM u GROUP BY slug, src) g),
                '[]'::json)::text AS rows /* mirror-positions-ledger */
"""


def ledger_positions_sql(intent_sql: str) -> str:
    """The one statement, with the ledger's own intent expression."""
    expr = " ".join(str(intent_sql or "").split())
    if not _INTENT_EXPR.match(expr):
        raise NotARead("refused (%s): the intent expression is not the "
                       "ledger's" % R_NOT_A_READ)
    return _LEDGER_POSITIONS_TEMPLATE.replace("{intent}", expr)


_READ = re.compile(r"^\s*(SELECT|WITH)\b", re.I)


def guard_read(sql: str) -> str:
    """Refuse, unsent, anything but ONE SELECT / WITH statement (the DB
    analogue of a transport that refuses every method but GET). The READ
    ONLY transaction is the second wall: the database refuses a write even
    if a statement got past this."""
    s = str(sql or "")
    body = s.strip().rstrip(";")
    if not _READ.match(body) or ";" in body:
        raise NotARead("refused (%s): not a single SELECT" % R_NOT_A_READ)
    return s


async def read_only_fetchrow(pool, sql: str, *args):
    """One guarded statement in a READ ONLY transaction on its own
    connection. The pool must hand out connections; there is no path that
    runs the statement outside a read-only transaction."""
    guard_read(sql)
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            return await conn.fetchrow(sql, *args)


# ── the credential topology (names and shapes only, never values) ─────

def credential_topology(key_id: str, secret: str, pmx_client_id: str) -> dict:
    """Which credential the PMUS slot holds, by SHAPE (market_data_identity
    .slot_shape: an enum, never a value, length or prefix), and whether the
    PMX client id names the same identifier (a boolean, never the id). Pure:
    the caller supplies the configured values and nothing here keeps them."""
    from . import market_data_identity as MDI

    kid = str(key_id or "").strip()
    pmx_cid = str(pmx_client_id or "").strip()
    shape = MDI.slot_shape(kid, str(secret or "").strip())
    return {"pmus_slot_shape": shape,
            "pmus_slot_is_pmx_rsa_client": shape == MDI.SHAPE_RSA_PEM,
            "pmx_client_id_equals_pmus_key_id": bool(pmx_cid) and pmx_cid == kid,
            "pmx_positions_fallback": PMX_FALLBACK_REJECTED}


def fallback_allowed(topology: dict) -> bool:
    """The ledger fallback serves the production topology it was built for:
    the PMUS slot positively holds an RSA PEM private key (the PMX client).
    Any other unusable credential fails closed by name."""
    return bool((topology or {}).get("pmus_slot_is_pmx_rsa_client"))


# ── the reading ───────────────────────────────────────────────────────

def _iso(v: Any) -> str | None:
    try:
        return v.isoformat()
    except AttributeError:
        return None if v is None else str(v)


def positions_from_rows(as_of: Any, rows_json: Any) -> tuple[dict[str, float], dict]:
    """Pure: the statement's answer -> ({slug: signed net}, receipt).
    RAISES on anything unreadable: a row with no slug, a non-finite net, or
    an answer that is not a list -- a partial reading of the account is not
    a reading of the account."""
    rows = json.loads(rows_json) if isinstance(rows_json, str) else rows_json
    if not isinstance(rows, list):
        raise ValueError("ledger answer is not a list")
    net: dict[str, float] = {}
    by_src: dict[str, dict] = {t: {"slugs": 0, "rows": 0} for t in TABLES}
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("ledger row is not an object")
        slug = str(r.get("slug") or "").strip().lower()
        if not slug:
            raise ValueError("ledger row carries no slug")
        v = r.get("net")
        if v is None or isinstance(v, bool):
            raise ValueError("ledger row carries no net for %s" % slug)
        v = float(v)
        if not math.isfinite(v):
            raise ValueError("ledger row carries a non-finite net for %s" % slug)
        src = str(r.get("src") or "")
        if src not in by_src:
            raise ValueError("ledger row names an unknown source")
        by_src[src]["slugs"] += 1
        by_src[src]["rows"] += int(r.get("n") or 0)
        net[slug] = net.get(slug, 0.0) + v
    positions = {s: round(v, 4) for s, v in net.items() if abs(v) > 1e-9}
    receipt = {"source": SRC_LEDGER, "authority": AUTHORITY_LEDGER,
               "venue_ledger_check": VENUE_LEDGER_CHECK,
               "as_of": _iso(as_of), "tables": list(TABLES),
               "by_table": by_src, "held_slugs": len(positions),
               "version": VERSION}
    return positions, receipt


async def ledger_positions(pool, intent_sql: str) -> tuple[dict[str, float] | None, dict]:
    """({slug: signed net} or None, receipt). None -- with the refusal named
    in the receipt -- whenever the statement raises or answers something
    unreadable. Never a partial map."""
    try:
        row = await read_only_fetchrow(pool, ledger_positions_sql(intent_sql))
        if row is None:
            raise ValueError("ledger statement answered no row")
        return positions_from_rows(row["as_of"], row["rows"])
    except Exception as exc:                                    # noqa: BLE001
        return None, {"source": SRC_LEDGER,
                      "refusal": R_LEDGER_POSITIONS_UNREADABLE,
                      "error": type(exc).__name__, "version": VERSION}
