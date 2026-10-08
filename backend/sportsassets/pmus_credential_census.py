"""PMUS RETAIL CREDENTIAL CENSUS: EVERY CANDIDATE ENV NAME -> A SHAPE ENUM.

WHY (closeout P1, production at 08828d04, 2026-10-07). mirror_shadow refuses
the venue positions walk (PMUS_SECRET_SLOT_HOLDS_NO_ED25519_KEY) and the API's
track-record / account reads fail in the SDK signer ("The seed must be exactly
32 bytes long"). Both read ONE pair, PMUS_KEY_ID / PMUS_SECRET_KEY. The
question this module answers in production, per process, is whether a retail
Ed25519 key exists under ANY name and is merely not routed to those readers.

WHAT IT REPORTS, AND NOTHING ELSE:
  * per known pair (funded, execution mirror, edge engine, market data,
    broker, PMX): the pair's SHAPE enum and the role its names were
    provisioned for;
  * per identifier / secret: which OTHER names hold an EQUAL identifier or an
    EQUAL secret (normalised forms, market_data_identity._id_forms /
    _secret_forms) -- names only, a boolean relation;
  * every OTHER env name in the process whose value has a credential shape
    (UUID identifier, Ed25519 key material, PEM private key), or whose name
    reads as Polymarket / PMUS / retail -- name -> shape enum.

NEVER A VALUE, A LENGTH, A PREFIX OR A HASH OF A SECRET. A shape enum says
which CLASS of credential is there; it does not narrow the key.

WHAT SHAPE CANNOT PROVE: which ACCOUNT a retail-shaped key belongs to. A
retail key under another name is reported as present, with the role its name
was provisioned for; it is never treated as the funded account's key (the
mirror_positions_source PMX_FALLBACK_REJECTED rule: a positions read of
another account reports the funded account as flat).
"""
from __future__ import annotations

import os
import re

VERSION = "PMUS_CREDENTIAL_CENSUS_V1"

# ── pair shapes ────────────────────────────────────────────────────────
P_ABSENT = "ABSENT"
P_KEY_ID_ONLY = "INCOMPLETE_KEY_ID_ONLY"
P_SECRET_ONLY = "INCOMPLETE_SECRET_ONLY"
P_RETAIL = "PMUS_RETAIL_ED25519_API_KEY_SHAPE"
#: an Ed25519 key the SDK does not read as given but venue_key re-encodes
#: (hex, PKCS#8, url-safe / unpadded base64, quoted) -- the same key
P_RETAIL_REENCODE = "PMUS_RETAIL_ED25519_KEY_NEEDS_REENCODING"
P_RSA = "RSA_PEM_PRIVATE_KEY_SHAPE_NOT_A_PMUS_RETAIL_KEY"
P_OTHER = "UNRECOGNISED_SHAPE"
RETAIL_SHAPES = (P_RETAIL, P_RETAIL_REENCODE)

# ── single-name shapes ─────────────────────────────────────────────────
N_ABSENT = "ABSENT"
N_UUID = "UUID_IDENTIFIER_SHAPE"
N_ED25519 = "ED25519_KEY_MATERIAL_SHAPE"
N_PEM = "PEM_PRIVATE_KEY_SHAPE"
N_OTHER = "OTHER_SHAPE"
CREDENTIAL_NAME_SHAPES = (N_UUID, N_ED25519, N_PEM)

#: (label, key-id env, secret env, the role that name was provisioned for)
PAIRS = (
    ("PMUS_FUNDED", "PMUS_KEY_ID", "PMUS_SECRET_KEY",
     "FUNDED_RETAIL_ACCOUNT"),
    ("PMUS_EXECMIRROR", "PMUS_EXECMIRROR_KEY_ID",
     "PMUS_EXECMIRROR_SECRET_KEY",
     "EXECUTION_MIRROR_ACCOUNT_SEPARATE_FROM_FUNDED_BY_DESIGN"),
    ("EDGE_PMUS", "EDGE_PMUS_KEY_ID", "EDGE_PMUS_SECRET_KEY",
     "EDGE_ENGINE_KEY_NOT_APPROVED_FOR_REUSE"),
    ("PMUS_MD", "PMUS_MD_KEY_ID", "PMUS_MD_SECRET_KEY",
     "MARKET_DATA_ONLY_NEVER_AN_EXECUTION_KEY"),
    ("PMUS_BROKER", "PMUS_BROKER_KEY_ID", "PMUS_BROKER_SECRET_KEY",
     "BROKER_KEY_NOT_APPROVED"),
    ("PMX", "PMX_KEY_ID", "PMX_PRIVATE_KEY_B64",
     "POLYMARKET_EXCHANGE_INSTITUTIONAL_M2M_CLIENT"),
)
#: identifiers without a secret of their own, compared with every key id
IDENTIFIERS = ("PMX_CLIENT_ID", "PMX_PARTICIPANT_ID")
FUNDED = "PMUS_FUNDED"
#: the readers of the funded pair -- the routing the census is read against
FUNDED_READERS = (
    "workers: mirror_shadow.account_positions_walk / tick_positions",
    "workers: mirror_live.account_positions_walk",
    "api: track_record._fetch_raw",
    "api: pmus_account._fetch_sync / _fetch_all_positions_sync / "
    "_fetch_week_activities_sync",
    "api+workers: pmus._get_client (funded lane, reconcile, live_executor)",
)
#: names whose CLASS is fixed by their own provisioning and is never a PMUS
#: retail candidate, whatever their bytes look like (a VAPID P-256 scalar is
#: 32 bytes; a Kalshi key id is a UUID)
NOT_PMUS_PREFIXES = ("KALSHI_", "EDGE_KALSHI_", "VAPID_", "SLACK_")
_SWEEP_NAME = re.compile(r"(?i)(pmus|polymarket|poly_?us|execmirror|retail|"
                         r"ed25519)")

V_FUNDED_USABLE = "FUNDED_SLOT_HOLDS_A_RETAIL_ED25519_KEY"
V_ELSEWHERE = ("FUNDED_SLOT_UNUSABLE_RETAIL_SHAPED_KEY_PRESENT_UNDER_OTHER_"
               "NAMES_ACCOUNT_NOT_PROVABLE_BY_SHAPE")
V_NONE = "FUNDED_SLOT_UNUSABLE_NO_RETAIL_ED25519_KEY_UNDER_ANY_NAME"
V_FUNDED_ABSENT = "FUNDED_SLOT_ABSENT"


def _get(env, name: str) -> str:
    return str((env.get(name) if env is not None else None) or "").strip()


def name_shape(value: str) -> str:
    """One value's credential CLASS. Order matters: an Ed25519 key in PKCS#8
    PEM is Ed25519 material, not 'a PEM'."""
    from . import market_data_identity as MDI
    from . import venue_key as VK
    v = str(value or "").strip()
    if not v:
        return N_ABSENT
    if MDI._UUID.match(v):
        return N_UUID
    if VK._decode(v) is not None:
        return N_ED25519
    texts = [v]
    raw = MDI._b64_bytes(v)
    if raw:
        try:
            texts.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    if any(MDI._pem_body(t) for t in texts):
        return N_PEM
    return N_OTHER


def pair_shape(key_id: str, secret: str) -> str:
    from . import market_data_identity as MDI
    from . import venue_key as VK
    kid, sec = str(key_id or "").strip(), str(secret or "").strip()
    if not (kid or sec):
        return P_ABSENT
    if not sec:
        return P_KEY_ID_ONLY
    if not kid:
        return P_SECRET_ONLY
    s = MDI.slot_shape(kid, sec)
    if s == MDI.SHAPE_PMUS_RETAIL:
        return P_RETAIL
    if MDI._UUID.match(kid) and VK._decode(sec) is not None:
        return P_RETAIL_REENCODE
    if s == MDI.SHAPE_RSA_PEM:
        return P_RSA
    return P_OTHER


def _equal_names(env, names, forms_fn) -> dict:
    """{name: [other names holding an EQUAL value]} -- names only."""
    forms = {n: forms_fn(_get(env, n)) for n in names}
    return {n: sorted(m for m in names
                      if m != n and forms[n] and forms[n] & forms[m])
            for n in names if forms[n]}


def census(env=None, *, service: str | None = None) -> dict:
    from . import market_data_identity as MDI
    env = os.environ if env is None else env
    known = {n for _l, k, s, _r in PAIRS for n in (k, s)} | set(IDENTIFIERS)
    id_names = [k for _l, k, _s, _r in PAIRS] + list(IDENTIFIERS)
    sec_names = [s for _l, _k, s, _r in PAIRS]
    id_eq = _equal_names(env, id_names, MDI._id_forms)
    sec_eq = _equal_names(env, sec_names, MDI._secret_forms)
    pairs = {}
    for label, kid_env, sec_env, role in PAIRS:
        shape = pair_shape(_get(env, kid_env), _get(env, sec_env))
        pairs[label] = {"key_id_env": kid_env, "secret_env": sec_env,
                        "role": role, "shape": shape,
                        "key_id_equals": id_eq.get(kid_env, []),
                        "secret_equals": sec_eq.get(sec_env, [])}
    pairs[FUNDED]["readers"] = list(FUNDED_READERS)
    others = {}
    for name in sorted(str(n) for n in env.keys()):
        if name in known or name.upper().startswith(NOT_PMUS_PREFIXES):
            continue
        shp = name_shape(_get(env, name))
        if shp in CREDENTIAL_NAME_SHAPES or (
                shp != N_ABSENT and _SWEEP_NAME.search(name)):
            others[name] = shp
    retail_under = sorted(lb for lb, p in pairs.items()
                          if p["shape"] in RETAIL_SHAPES and lb != FUNDED)
    # Ed25519-SHAPED BYTES ARE NOT AN Ed25519 KEY BY THEMSELVES: any 32
    # random bytes are a valid seed, and Render's `generateValue: true`
    # tokens (ADMIN_TOKEN on sportsassets-workers) are exactly base64 of 32
    # random bytes. Only a PMUS / Polymarket / retail-NAMED value counts
    # toward the verdict; the rest is listed, never counted.
    material = sorted(n for n, s in others.items()
                      if s == N_ED25519 and _SWEEP_NAME.search(n))
    unrelated = sorted(n for n, s in others.items()
                       if s == N_ED25519 and not _SWEEP_NAME.search(n))
    fshape = pairs[FUNDED]["shape"]
    verdict = (V_FUNDED_USABLE if fshape in RETAIL_SHAPES
               else V_FUNDED_ABSENT if fshape == P_ABSENT
               else V_ELSEWHERE if (retail_under or material) else V_NONE)
    return {"version": VERSION, "service": service,
            "values_exposed": False,
            "pairs": pairs,
            "identifiers": {n: {"shape": name_shape(_get(env, n)),
                                "equals": id_eq.get(n, [])}
                            for n in IDENTIFIERS},
            "other_candidate_names": others,
            "funded_slot_shape": fshape,
            "retail_ed25519_pairs_under_other_names": retail_under,
            "ed25519_material_under_unpaired_names": material,
            "ed25519_shaped_bytes_under_unrelated_names": unrelated,
            "account_identity": ("NOT_PROVABLE_BY_SHAPE: a retail-shaped key "
                                 "under another name is reported, never "
                                 "routed to the funded readers"),
            "verdict": verdict}
