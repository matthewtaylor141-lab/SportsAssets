"""WHICH CREDENTIAL MAY READ MARKET DATA, AND PROOF IT IS NOT AN EXECUTION KEY.

The owner's topology (2026-10-03): an INSTITUTIONAL Polymarket identity used
for MARKET DATA ONLY, and a RETAIL Polymarket US key for small-live execution.
The two must never be the same identity, and neither may be the funded key.

THE CANDIDATES, AS THEY ACTUALLY EXIST (env-keys read 2026-10-03, names and
lengths only):

  PMX      Polymarket Exchange INSTITUTIONAL machine-to-machine client.
           PMX_CLIENT_ID + PMX_KEY_ID + PMX_PRIVATE_KEY_B64 (an RSA key whose
           public half is registered with the venue's Auth0 tenant) +
           PMX_PARTICIPANT_ID. Auth: a private-key JWT (RS256) client
           assertion exchanged at pmx-prod.us.auth0.com for a bearer token;
           REST at api.prod.polymarketexchange.com, gRPC market-data stream
           (`polymarket.v1.MarketDataSubscriptionAPI`) documented at
           grpc-api.prod.polymarketexchange.com:443. The market-data stream
           needs only `read:marketdata` and NO participant id (venue docs,
           /streaming-endpoints/market-data-stream). PRESENT ON
           sportsassets-workers ONLY.
  PMUS_MD  an ORDINARY retail Polymarket US API key (Ed25519, from
           polymarket.us/developer) the owner named for market data.
           ABSENT from both services.

The retail execution key (PMUS_EXECMIRROR_KEY_ID) and the funded key
(PMUS_KEY_ID) are the identities a market-data credential must never equal.

WHAT A FINGERPRINT IS HERE: sha256 of an IDENTIFIER (a key id or client id),
12 hex -- the same function `execmirror_probe.fingerprint` uses, so a
fingerprint printed by one service can be compared with one printed by the
other. Never computed over a private or secret key. Never a length, never a
prefix, never a value.

WHAT A SCOPE IS HERE: a permission the venue GRANTED, read from our own token's
`scope` claim, plus the venue's own `whoami`. A granted `write:orders` is
reported precisely so it is visible: this repository has no order path for
this credential (`pmx_institutional.ORDER_SUBMISSION_IMPLEMENTATION = NONE`),
but the credential itself is NOT venue-enforced read-only when that scope is
present, and nothing here pretends otherwise.

NO ORDER PATH. Reads only; one allow-listed identity read (`/v1/whoami`).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re

VERSION = "MARKET_DATA_IDENTITY_V1"

# ── the identities a market-data credential must never be ──────────────
RETAIL_EXEC_KEY_ID_ENV = "PMUS_EXECMIRROR_KEY_ID"
RETAIL_EXEC_SECRET_ENV = "PMUS_EXECMIRROR_SECRET_KEY"
FUNDED_KEY_ID_ENV = "PMUS_KEY_ID"
FUNDED_SECRET_ENV = "PMUS_SECRET_KEY"
#: Reported for completeness (it is a third retail key on the API service);
#: not one of the two identities the guard is defined against.
EDGE_KEY_ID_ENV = "EDGE_PMUS_KEY_ID"

# ── the candidates ─────────────────────────────────────────────────────
PMUS_MD = "PMUS_MD"
PMX = "PMX"
MD_KEY_ID_ENV = "PMUS_MD_KEY_ID"
MD_SECRET_ENV = "PMUS_MD_SECRET_KEY"
PMX_CLIENT_ID_ENV = "PMX_CLIENT_ID"
PMX_KEY_ID_ENV = "PMX_KEY_ID"
PMX_PRIVATE_KEY_ENV = "PMX_PRIVATE_KEY_B64"
PMX_PARTICIPANT_ENV = "PMX_PARTICIPANT_ID"

TYPE_PMX = "POLYMARKET_EXCHANGE_INSTITUTIONAL_M2M_CLIENT"
TYPE_PMUS = "POLYMARKET_US_RETAIL_API_KEY"

PMX_INTERFACE = {
    "interface": "Polymarket Exchange (institutional API)",
    "auth": ("Auth0 private-key JWT (RS256 client assertion) -> OAuth2 "
             "client_credentials bearer token"),
    "token_endpoint": "https://pmx-prod.us.auth0.com/oauth/token",
    "rest_base": "https://api.prod.polymarketexchange.com",
    "grpc_target": "grpc-api.prod.polymarketexchange.com:443",
    "grpc_service": "polymarket.v1.MarketDataSubscriptionAPI",
    "grpc_target_status": "DOCUMENTED_NOT_YET_CONNECTED_FROM_THIS_REPO",
    "market_data_scope": "read:marketdata (L2 depth: read:l2marketdata)",
    "participant_id_required_for_market_data": False,
    "sources": [
        "https://docs.polymarket.us/streaming-endpoints/market-data-stream",
        "https://docs.polymarket.us/streaming-endpoints/grpc-overview",
        "https://docs.polymarket.us/trader-guide/authentication",
    ],
}
PMUS_INTERFACE = {
    "interface": "Polymarket US retail gateway",
    "auth": "Ed25519 API key (X-PM-Access-Key / X-PM-Signature)",
    "market_data": "retail websocket (bettor_market_stream)",
    "sources": ["https://docs.polymarket.us/api-reference/authentication"],
}

# Refusals the guard names.
G_OK = None
G_ABSENT = "MARKET_DATA_CREDENTIAL_ABSENT"
G_IS_RETAIL_EXECUTION = "MARKET_DATA_CREDENTIAL_IS_THE_RETAIL_EXECUTION_KEY"
G_IS_FUNDED = "MARKET_DATA_CREDENTIAL_IS_THE_FUNDED_KEY"
G_UNKNOWN = "MARKET_DATA_CREDENTIAL_UNKNOWN"


def fingerprint(identifier: str) -> str | None:
    """sha256(identifier)[:12] -- identical to execmirror_probe.fingerprint."""
    return (hashlib.sha256(identifier.encode()).hexdigest()[:12]
            if identifier else None)


def _get(env, name) -> str:
    return str((env.get(name) if env is not None else None) or "").strip()


def _known(env) -> dict:
    """The execution identities present in THIS process: presence and
    fingerprint only."""
    out = {}
    for label, kid_env, sec_env in (
            ("retail_execution", RETAIL_EXEC_KEY_ID_ENV, RETAIL_EXEC_SECRET_ENV),
            ("funded", FUNDED_KEY_ID_ENV, FUNDED_SECRET_ENV),
            ("edge_shadow", EDGE_KEY_ID_ENV, None)):
        kid = _get(env, kid_env)
        out[label] = {"key_id_env": kid_env, "present": bool(kid),
                      "fingerprint": fingerprint(kid)}
    return out


def _id_forms(identifier: str) -> set:
    """The forms one identifier is compared in: as given, and folded (case,
    whitespace and dashes removed), so the same UUID with or without its
    dashes, or pasted with a stray space, is the SAME identifier. Only ever
    compared; never reported."""
    s = str(identifier or "").strip()
    if not s:
        return set()
    folded = "".join(s.split()).replace("-", "").casefold()
    return {s, folded} - {""}


def _b64_bytes(s: str):
    t = "".join(str(s or "").split())
    if len(t) < 16:
        return None
    t = t + "=" * (-len(t) % 4)
    # STRICT alphabets only: a lenient decode drops unknown characters, which
    # would make two different strings "equal".
    for cand in (t, t.replace("-", "+").replace("_", "/")):
        try:
            return base64.b64decode(cand, validate=True)
        except (binascii.Error, ValueError):
            continue
    return None


def _pem_body(text: str):
    """The base64 body of a PEM block (armour, whitespace and literal \\n
    escapes removed), or None when `text` is not PEM."""
    t = str(text or "").replace("\\n", "\n")
    if "-----BEGIN" not in t or "-----END" not in t:
        return None
    body = t.split("-----BEGIN", 1)[1].split("-----", 1)[-1]
    body = body.split("-----END", 1)[0]
    body = "".join(body.split())
    return body or None


def _secret_forms(secret: str) -> set:
    """The forms one secret is compared in: whitespace-stripped text; the
    bytes it base64-decodes to; and, when it is (or decodes to) a PEM key,
    that key's base64 body and DER bytes. So the SAME private key pasted as
    raw PEM in one variable and as base64-of-PEM in another is the same
    secret. Only ever compared; never reported, never hashed out."""
    s = str(secret or "").strip()
    if not s:
        return set()
    forms = {s, "".join(s.replace("\\n", "\n").split())}
    raw = _b64_bytes(s)
    texts = [s]
    if raw:
        forms.add(raw)
        try:
            texts.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    for t in texts:
        body = _pem_body(t)
        if body:
            forms.add(body)
            der = _b64_bytes(body)
            if der:
                forms.add(der)
    return forms - {"", b""}


def _compare(identifiers, secrets, other_kid, other_secret, other_fp_peer,
             *, same_class: bool) -> tuple:
    """(distinct: True|False|None, basis) for one candidate vs one identity.

    False on ANY match -- an identifier equal to the other key id, a secret
    equal to the other secret, or a fingerprint equal to a peer-reported
    fingerprint. Equality is decided on NORMALISED forms (`_id_forms`,
    `_secret_forms`): the same identifier with other dashes / case /
    whitespace, or the same private key in another encoding (raw PEM vs
    base64-of-PEM), is EQUAL. True when a value comparison was possible and
    nothing matched, or when the credential classes differ (a PMX Auth0
    client and a PMUS API key are issued by different systems). None when
    same-class and nothing could be compared.
    """
    ids = [i for i in identifiers if i]
    secs = [s for s in secrets if s]
    id_forms = set().union(*(_id_forms(i) for i in ids)) if ids else set()
    if other_kid and _id_forms(other_kid) & id_forms:
        return False, "IDENTIFIER_EQUAL"
    sec_forms = set().union(*(_secret_forms(s) for s in secs)) if secs \
        else set()
    if other_secret and _secret_forms(other_secret) & sec_forms:
        return False, "SECRET_EQUAL"
    fps = {fingerprint(f) for f in id_forms if isinstance(f, str)}
    if other_fp_peer and other_fp_peer in fps:
        return False, "FINGERPRINT_EQUAL_TO_PEER_REPORT"
    if other_kid or other_secret:
        return True, "COMPARED_BY_VALUE_IN_THIS_PROCESS"
    if other_fp_peer:
        return True, "COMPARED_BY_FINGERPRINT_TO_PEER_REPORT"
    if not same_class:
        return True, "DIFFERENT_CREDENTIAL_CLASS"
    return None, "EXECUTION_KEY_NOT_IN_THIS_PROCESS_AND_NO_PEER_FINGERPRINT"


def _distinctness(env, ids, secs, peer, *, same_class) -> dict:
    peer = dict(peer or {})
    r_ok, r_why = _compare(ids, secs, _get(env, RETAIL_EXEC_KEY_ID_ENV),
                           _get(env, RETAIL_EXEC_SECRET_ENV),
                           peer.get("retail_execution"), same_class=same_class)
    f_ok, f_why = _compare(ids, secs, _get(env, FUNDED_KEY_ID_ENV),
                           _get(env, FUNDED_SECRET_ENV), peer.get("funded"),
                           same_class=same_class)
    return {"distinct_from_retail_execution": r_ok,
            "distinct_from_retail_execution_basis": r_why,
            "distinct_from_funded": f_ok,
            "distinct_from_funded_basis": f_why}


def pmus_md_candidate(env=None, peer_fingerprints=None) -> dict:
    env = os.environ if env is None else env
    kid, sec = _get(env, MD_KEY_ID_ENV), _get(env, MD_SECRET_ENV)
    out = {"candidate": PMUS_MD, "credential_type": TYPE_PMUS,
           "institutional": False,
           "names": {MD_KEY_ID_ENV: bool(kid), MD_SECRET_ENV: bool(sec)},
           "present": bool(kid or sec), "complete": bool(kid and sec),
           "fingerprints": {"key_id": fingerprint(kid)},
           "interface": PMUS_INTERFACE,
           "scopes": {"verifiable": False,
                      "why": ("the retail Polymarket US API documents no "
                              "scope model or permissions endpoint for an "
                              "API key; a retail key has the account's full "
                              "trading access")}}
    if not (kid or sec):
        out.update({"distinct_from_retail_execution": None,
                    "distinct_from_retail_execution_basis": "ABSENT",
                    "distinct_from_funded": None,
                    "distinct_from_funded_basis": "ABSENT"})
        return out
    out.update(_distinctness(env, [kid], [sec], peer_fingerprints,
                             same_class=True))
    return out


def pmx_candidate(env=None, peer_fingerprints=None) -> dict:
    env = os.environ if env is None else env
    cid, kid = _get(env, PMX_CLIENT_ID_ENV), _get(env, PMX_KEY_ID_ENV)
    pk, part = _get(env, PMX_PRIVATE_KEY_ENV), _get(env, PMX_PARTICIPANT_ENV)
    names = {PMX_CLIENT_ID_ENV: bool(cid), PMX_KEY_ID_ENV: bool(kid),
             PMX_PRIVATE_KEY_ENV: bool(pk), PMX_PARTICIPANT_ENV: bool(part)}
    out = {"candidate": PMX, "credential_type": TYPE_PMX,
           "institutional": True,
           "names": names, "present": any(names.values()),
           # Market data needs the client, the key id and the private key;
           # the participant id is required by the order/account paths only.
           "complete": bool(cid and kid and pk),
           "complete_for_account_paths": all(names.values()),
           # Identifiers only. NEVER the private key.
           "fingerprints": {"client_id": fingerprint(cid),
                            "key_id": fingerprint(kid)},
           "interface": PMX_INTERFACE,
           "scopes": {"verifiable": True,
                      "how": ("the token's own `scope` claim and GET "
                              "/v1/whoami, by the read-only md_verify probe "
                              "in the process that holds the credential")}}
    if not out["present"]:
        out.update({"distinct_from_retail_execution": None,
                    "distinct_from_retail_execution_basis": "ABSENT",
                    "distinct_from_funded": None,
                    "distinct_from_funded_basis": "ABSENT"})
        return out
    out.update(_distinctness(env, [cid, kid, part], [pk], peer_fingerprints,
                             same_class=False))
    return out


CANDIDATES = {PMUS_MD: pmus_md_candidate, PMX: pmx_candidate}


def inventory(env=None, peer_fingerprints=None) -> dict:
    """EVERY candidate market-data credential in this process, with type,
    presence, fingerprints, verifiable scopes and distinctness. Names and
    fingerprints only; no value leaves the process.

    `peer_fingerprints` -- {"retail_execution": fp, "funded": fp} as printed by
    ANOTHER service's report -- lets a process that does not hold an execution
    key still prove its market-data credential is not that key."""
    env = os.environ if env is None else env
    cands = [CANDIDATES[n](env, peer_fingerprints) for n in (PMX, PMUS_MD)]
    usable = [c["candidate"] for c in cands
              if c["complete"] and guard(c["candidate"], env=env,
                                         peer_fingerprints=peer_fingerprints)
              is G_OK]
    inst = next((c["candidate"] for c in cands
                 if c["institutional"] and c["candidate"] in usable), None)
    return {
        "version": VERSION,
        "execution_identities_in_this_process": _known(env),
        "candidates": cands,
        "usable_for_market_data": usable,
        "institutional_market_data_credential": inst,
        "pmx_guard": {k: v for k, v in diagnose(
            PMX, env=env, peer_fingerprints=peer_fingerprints).items()
            if k in ("refusal", "matches", "execution_slot_shapes")},
        "verdict": ("INSTITUTIONAL_MARKET_DATA_CREDENTIAL_PRESENT" if inst
                    else "RETAIL_MARKET_DATA_KEY_ONLY" if usable
                    else "NO_MARKET_DATA_CREDENTIAL_IN_THIS_PROCESS"),
    }


def guard(candidate: str, *, env=None, peer_fingerprints=None) -> str | None:
    """None when `candidate` may be used for market data; otherwise the named
    refusal. Refuses an absent credential, and any credential whose identifier,
    secret or fingerprint equals the retail execution key or the funded key."""
    env = os.environ if env is None else env
    fn = CANDIDATES.get(candidate)
    if fn is None:
        return G_UNKNOWN
    c = fn(env, peer_fingerprints)
    if not c["complete"]:
        return G_ABSENT
    if c["distinct_from_retail_execution"] is False and not \
            _pmx_vs_non_pmus_slot(candidate, env, RETAIL_EXEC_KEY_ID_ENV,
                                  RETAIL_EXEC_SECRET_ENV):
        return G_IS_RETAIL_EXECUTION
    if c["distinct_from_funded"] is False and not \
            _pmx_vs_non_pmus_slot(candidate, env, FUNDED_KEY_ID_ENV,
                                  FUNDED_SECRET_ENV):
        return G_IS_FUNDED
    return G_OK


#: WHY A PMX COLLISION WITH A NON-PMUS SLOT IS NOT A PMUS EXECUTION IDENTITY
#: (P1 closeout, production 2026-10-06). The guard exists so market data never
#: rides a PMUS EXECUTION identity (the funded key or the retail execution
#: key). A PMUS execution identity is, by construction, a PMUS API key: a UUID
#: key id + a base64 Ed25519 secret (SHAPE_PMUS_RETAIL) -- the only credential
#: the PMUS SDK can sign with. Production's funded PMUS slot holds the PMX
#: institutional Auth0 client (same client id, the same RSA PEM key): PMUS
#: signing cannot use it (the workers log "The seed must be exactly 32 bytes
#: long" on every authenticated PMUS read), so it is NOT a working PMUS
#: execution identity, and the PMX credential is ORDERLESS in this build (pmx.py
#: is the PRE-PRODUCTION adapter with no production host; funded submission is
#: disabled -- pinned by tests). So for the PMX candidate ONLY, a collision with
#: a slot whose shape is NOT a PMUS API key is reported (diagnose / inventory:
#: PMUS_SLOT_HOLDS_A_NON_PMUS_CREDENTIAL) and does not refuse. Every collision
#: with a PMUS-shaped slot still refuses, and the PMUS market-data candidate is
#: unchanged.
PMX_NON_PMUS_SLOT_BASIS = "PMUS_SLOT_HOLDS_A_NON_PMUS_CREDENTIAL"


def _pmx_vs_non_pmus_slot(candidate: str, env, kid_env: str,
                          sec_env: str) -> bool:
    if candidate != PMX:
        return False
    # ONLY a slot POSITIVELY holding the PMX credential class (an RSA PEM
    # private key -- never a PMUS API key); an unrecognised shape still refuses
    shape = slot_shape(_get(env, kid_env), _get(env, sec_env))
    return shape == SHAPE_RSA_PEM


_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$")

SHAPE_ABSENT = "ABSENT"
SHAPE_PMUS_RETAIL = "PMUS_RETAIL_ED25519_API_KEY_SHAPE"
SHAPE_RSA_PEM = "RSA_PEM_PRIVATE_KEY_SHAPE_NOT_A_PMUS_RETAIL_KEY"
SHAPE_OTHER = "UNRECOGNISED_SHAPE"


def slot_shape(key_id: str, secret: str) -> str:
    """WHAT KIND OF CREDENTIAL an execution slot holds, by SHAPE only (an
    enum; never a value, length or prefix). A retail Polymarket US API key is
    a UUID key id + a base64 Ed25519 secret (32 or 64 bytes); a PEM private
    key (an RSA key such as the PMX client's) in that slot is not one."""
    kid, sec = str(key_id or "").strip(), str(secret or "").strip()
    if not (kid or sec):
        return SHAPE_ABSENT
    raw = _b64_bytes(sec)
    if _UUID.match(kid) and raw is not None and len(raw) in (32, 64):
        return SHAPE_PMUS_RETAIL
    texts = [sec]
    if raw:
        try:
            texts.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    if any(_pem_body(t) for t in texts):
        return SHAPE_RSA_PEM
    return SHAPE_OTHER


def diagnose(candidate: str = PMX, *, env=None, peer_fingerprints=None) -> dict:
    """THE GUARD'S ANSWER WITH ITS EVIDENCE, BY NAME ONLY: the refusal, and
    for every match the candidate's env name, the execution identity's env
    name and the basis (IDENTIFIER_EQUAL / SECRET_EQUAL /
    FINGERPRINT_EQUAL_TO_PEER_REPORT), plus the SHAPE of each execution slot
    (`slot_shape`). It decides nothing the guard does not; it says which
    variables collide so the owner can fix the provisioning. Never a value."""
    env = os.environ if env is None else env
    peer = dict(peer_fingerprints or {})
    if candidate == PMX:
        ids = [(n, _get(env, n)) for n in (PMX_CLIENT_ID_ENV, PMX_KEY_ID_ENV,
                                           PMX_PARTICIPANT_ENV)]
        secs = [(PMX_PRIVATE_KEY_ENV, _get(env, PMX_PRIVATE_KEY_ENV))]
    elif candidate == PMUS_MD:
        ids = [(MD_KEY_ID_ENV, _get(env, MD_KEY_ID_ENV))]
        secs = [(MD_SECRET_ENV, _get(env, MD_SECRET_ENV))]
    else:
        return {"candidate": candidate, "refusal": G_UNKNOWN, "matches": []}
    matches = []
    for label, kid_env, sec_env in (
            ("retail_execution", RETAIL_EXEC_KEY_ID_ENV, RETAIL_EXEC_SECRET_ENV),
            ("funded", FUNDED_KEY_ID_ENV, FUNDED_SECRET_ENV)):
        okid, osec = _get(env, kid_env), _get(env, sec_env)
        for name, v in ids:
            if v and okid and _id_forms(v) & _id_forms(okid):
                matches.append({"identity": label, "candidate_env": name,
                                "execution_env": kid_env,
                                "basis": "IDENTIFIER_EQUAL"})
            fp = peer.get(label)
            if v and fp and fp in {fingerprint(f) for f in _id_forms(v)}:
                matches.append({"identity": label, "candidate_env": name,
                                "execution_env": "%s (peer fingerprint)"
                                                 % kid_env,
                                "basis": "FINGERPRINT_EQUAL_TO_PEER_REPORT"})
        for name, v in secs:
            if v and osec and _secret_forms(v) & _secret_forms(osec):
                matches.append({"identity": label, "candidate_env": name,
                                "execution_env": sec_env,
                                "basis": "SECRET_EQUAL"})
    return {
        "version": VERSION, "candidate": candidate,
        "refusal": guard(candidate, env=env, peer_fingerprints=peer),
        "matches": matches,
        # collisions that do NOT refuse: the PMX candidate against a slot
        # holding a non-PMUS credential (PMX_NON_PMUS_SLOT_BASIS) -- named so
        # the misprovisioned slot is visible, never silently accepted
        "non_pmus_slot_collisions": sorted({
            m["identity"] for m in matches
            if _pmx_vs_non_pmus_slot(
                candidate, env,
                FUNDED_KEY_ID_ENV if m["identity"] == "funded"
                else RETAIL_EXEC_KEY_ID_ENV,
                FUNDED_SECRET_ENV if m["identity"] == "funded"
                else RETAIL_EXEC_SECRET_ENV)}),
        "non_pmus_slot_basis": PMX_NON_PMUS_SLOT_BASIS,
        "execution_slot_shapes": {
            "retail_execution": slot_shape(_get(env, RETAIL_EXEC_KEY_ID_ENV),
                                           _get(env, RETAIL_EXEC_SECRET_ENV)),
            "funded": slot_shape(_get(env, FUNDED_KEY_ID_ENV),
                                 _get(env, FUNDED_SECRET_ENV))},
        "rule": ("a market-data credential equal to the retail execution key "
                 "or the funded key (identifier, secret in any encoding, or a "
                 "peer fingerprint) is refused; a distinct one is accepted. "
                 "The fix for a match is provisioning (put the right "
                 "credential in the right variable), never a weaker rule."),
    }


def refusal_why(candidate: str = PMX, *, env=None) -> str | None:
    """One line for a refusal: the refusal and the colliding env NAMES."""
    d = diagnose(candidate, env=env)
    if d.get("refusal") is None:
        return None
    pairs = sorted({"%s==%s(%s)" % (m["candidate_env"], m["execution_env"],
                                     m["basis"]) for m in d["matches"]})
    shapes = d.get("execution_slot_shapes") or {}
    return "%s%s; funded slot shape %s" % (
        d["refusal"], (": " + ", ".join(pairs)) if pairs else "",
        shapes.get("funded"))


def guard_key_pair(key_id: str, secret: str, *, env=None) -> str | None:
    """The same rule for a raw (key id, secret) pair a caller is about to hand
    to a market-data connection. Never logs either value."""
    env = os.environ if env is None else env
    kid, sec = str(key_id or "").strip(), str(secret or "").strip()
    if not (kid and sec):
        return G_ABSENT
    kf, sf = _id_forms(kid), _secret_forms(sec)
    for refusal, kid_env, sec_env in (
            (G_IS_RETAIL_EXECUTION, RETAIL_EXEC_KEY_ID_ENV,
             RETAIL_EXEC_SECRET_ENV),
            (G_IS_FUNDED, FUNDED_KEY_ID_ENV, FUNDED_SECRET_ENV)):
        okid, osec = _get(env, kid_env), _get(env, sec_env)
        if kid == okid or (okid and kf & _id_forms(okid)):
            return refusal
        if sec == osec or (osec and sf & _secret_forms(osec)):
            return refusal
    return G_OK


# ── the read-only permissions probe (PMX) ──────────────────────────────

WHOAMI_PATH = "/v1/whoami"
_WHOAMI_PLAIN = ("firm", "firmType", "clearingMember", "name", "displayName")
_WHOAMI_FINGERPRINTED = ("user", "participant", "account", "id", "userId")


def whoami_summary(body) -> dict:
    """Resource names the venue returns about OUR identity, allow-listed. A
    user/participant/account reference is fingerprinted, not echoed."""
    if not isinstance(body, dict):
        return {"shape": type(body).__name__}
    out = {"keys": sorted(str(k) for k in body)[:40]}
    for k in _WHOAMI_PLAIN:
        if isinstance(body.get(k), (str, int)) and body.get(k) != "":
            out[k] = str(body[k])[:120]
    for k in _WHOAMI_FINGERPRINTED:
        if isinstance(body.get(k), str) and body[k]:
            out[k + "_fingerprint"] = fingerprint(body[k])
    return out


def verify_permissions(client=None, *, env=None, peer_fingerprints=None,
                       timeout=None) -> dict:
    """READ-ONLY: mint a token (no venue state changes), report its granted
    scopes, then GET /v1/whoami. Never returns the token, the assertion or the
    key. Called only in the process that holds the PMX credential."""
    from . import pmx_institutional as pmx

    env = os.environ if env is None else env
    cand = pmx_candidate(env, peer_fingerprints)
    out = {"version": VERSION, "candidate": PMX, "read_only": True,
           "fingerprints": cand["fingerprints"],
           "guard": guard(PMX, env=env, peer_fingerprints=peer_fingerprints),
           "ORDER_SUBMISSION_IMPLEMENTATION":
               pmx.ORDER_SUBMISSION_IMPLEMENTATION}
    if out["guard"] is not G_OK:
        out["state"] = out["guard"]
        return out
    client = client or pmx.Institutional(env=env)
    v = pmx.verify(client, "")
    scopes = list(v.get("TOKEN_SCOPES") or [])
    out.update({
        "auth_status": v.get("AUTH_STATUS"),
        "token_scopes": scopes,
        "read_marketdata": "read:marketdata" in scopes,
        "read_l2marketdata": "read:l2marketdata" in scopes,
        "read_instruments": "read:instruments" in scopes,
        "write_orders_granted": "write:orders" in scopes,
        # The honest consequence of a granted write scope.
        "venue_enforced_read_only": (None if not scopes
                                     else "write:orders" not in scopes),
        "token_lifetime_s": v.get("tokenLifetimeSeconds"),
    })
    if v.get("AUTH_STATUS") != pmx.A_OK:
        out["state"] = v.get("AUTH_STATUS")
        return out
    token = client.token()
    url = pmx.assert_production(pmx.REST_BASE + WHOAMI_PATH)
    try:
        r = client.session.request(
            "GET", url, headers={"Authorization": "Bearer %s" % token,
                                 "X-Request-Id": pmx.request_id()},
            timeout=timeout or pmx.TIMEOUT)
        try:
            body = r.json()
        except Exception:                                   # noqa: BLE001
            body = None
        out["whoami"] = {"status": r.status_code,
                         "identity": whoami_summary(body)
                         if r.status_code == 200 else None}
    except Exception as exc:                                # noqa: BLE001
        out["whoami"] = {"status": None, "transportError": type(exc).__name__}
    out["state"] = "VERIFIED" if out["whoami"].get("status") == 200 \
        else "SCOPES_VERIFIED_WHOAMI_UNAVAILABLE"
    return out
