"""WHAT `user_key` ACTUALLY IS, AND WHAT IT THEREFORE AUTHORISES.

I FIXED A ROUTE AND REPORTED A BOUNDARY. Those are different things, and the
difference is this module.

`/api/push/unsubscribe` deleted by `endpoint` alone, so anybody holding a push
endpoint could switch off somebody else's alerts. I added a `user_key` check and
recorded the finding closed with a residual noted. Two problems with that:

  1  A CLIENT-SUPPLIED IDENTIFIER IS NOT PROOF OF OWNERSHIP. It is proof of
     POSSESSION, which is only an authority boundary if possession is hard to
     obtain. Whether it is depends on the value's provenance and on how it
     travels -- neither of which I had established.

  2  THE BOUNDARY WAS BYPASSABLE, AND BY MY OWN CODE. `/api/push/subscribe` ran
     `ON CONFLICT (endpoint) DO UPDATE SET user_key=$1`. So a caller with an
     endpoint could POST their own key, become the owner, and then unsubscribe
     legitimately. The check I added was satisfied on the way through. Adding an
     identifier to one handler is not a boundary when another handler hands the
     identifier over.

THE THREE THINGS A CLIENT-SUPPLIED VALUE CAN BE, and they authorise differently:

  AUTHENTICATED_IDENTITY   the server established who this is, independently of
                           what the client claimed. Nothing here is this.
  PROTECTED_CAPABILITY     an unguessable secret that is only ever held by the
                           party entitled to use it, and does not leak. Bearer
                           authority, and legitimate -- IF it is actually
                           protected.
  BARE_IDENTIFIER          an unverified label. Authorises nothing.

`user_key` IS AN UNGUESSABLE VALUE THAT IS NOT KEPT SECRET, which is the awkward
middle. `crypto.randomUUID()` is 122 random bits, so guessing it is not the
threat. It leaks through its own URLs instead: `GET /api/prefs/{user_key}` and
`PUT /api/prefs/{user_key}` put it in the REQUEST PATH, where it lands in
access logs, proxy logs, referrers and browser history. A capability in a URL
path is a capability written down in several places nobody treats as secret.

    THAT IS THE FINDING. It is not "a UUID is weak". It is that this particular
    UUID is used as a path segment, so its confidentiality -- the only property
    that would make it a capability -- is not maintained by the system that
    depends on it.

AND THE PREFS ROUTES HAVE NO CHECK AT ALL. `GET /api/prefs/{user_key}` returns
that user's preferences to anyone who names the key, and `PUT` overwrites them.
So the key is simultaneously the identifier, the credential and the URL. Those
three roles cannot all be held by one value.

WHAT THIS MODULE IS. The classification, the routes that depend on it, the
bypasses checked rather than assumed, and what each repair would require. It is
read by tests so a route cannot start depending on `user_key` without appearing
here.

WHAT IS AT STAKE, STATED HONESTLY AND NOT INFLATED. No capital, no order, no
money and no position. The worst outcome is that someone's notifications are
turned off or their alert preferences are read and rewritten. That is a real
authorization defect and a small one, and it is kept on the register at its
actual size rather than dropped for being small or dressed up for being real.
"""

from __future__ import annotations

AUTHENTICATED_IDENTITY = "AUTHENTICATED_IDENTITY"
PROTECTED_CAPABILITY = "PROTECTED_CAPABILITY"
BARE_IDENTIFIER = "UNVERIFIED_CLIENT_SUPPLIED_IDENTIFIER"

#: THE VERDICT. Unguessable, and not confidential, so neither of the two things
#: that would authorise anything.
USER_KEY_CLASSIFICATION = {
    "value": "user_key",
    "origin": ("crypto.randomUUID() in frontend/src/lib/api.ts, stored in "
               "localStorage under sa_user_key"),
    "is_an_authenticated_identity": False,
    "why_not": ("no server-side step establishes who the caller is. The value "
                "is whatever the request said it was"),
    "is_a_protected_capability": False,
    "why_not_that_either": (
        "a capability has to stay confidential to be one, and this value is "
        "used as a URL PATH SEGMENT by GET and PUT /api/prefs/{user_key}. Path "
        "segments appear in access logs, proxy logs, referrer headers and "
        "browser history. The system that depends on its secrecy is the same "
        "system that writes it down"),
    "classification": BARE_IDENTIFIER,
    "entropy_is_not_the_problem": (
        "122 random bits. Guessing it is not the threat and saying 'a UUID is "
        "weak' would be the wrong finding"),
    "what_it_does_establish": (
        "POSSESSION. A caller holding the key is indistinguishable from the "
        "browser that generated it, which is useful and is not ownership"),
}

#: WHERE IT IS RELIED ON. `guard` is what the server actually checks.
DEPENDENT_ROUTES = (
    {
        "route": "POST /api/push/subscribe",
        "guard": "OWNER_MATCH_ON_CONFLICT",
        "writes": "push_subscriptions.user_key",
        "effect": "creates a subscription, or refreshes one it already owns",
        "was": ("ON CONFLICT (endpoint) DO UPDATE SET user_key=$1 -- which "
                "reassigned the owner and so defeated the unsubscribe check"),
        "now": ("the conflict clause updates only WHERE the existing user_key "
                "matches, so an endpoint's owner is immutable"),
    },
    {
        "route": "POST /api/push/unsubscribe",
        "guard": "OWNER_MATCH_REQUIRED",
        "writes": "deletes a push_subscriptions row",
        "effect": "turns off one subscription's notifications",
        "was": "DELETE WHERE endpoint=$1, with no ownership check at all",
        "now": ("DELETE WHERE endpoint=$1 AND user_key=$2, a missing key "
                "refused rather than defaulted, and a wrong key answered "
                "exactly as an already-removed row"),
    },
    {
        "route": "GET /api/prefs/{user_key}",
        "guard": "NONE",
        "writes": None,
        "effect": ("DISCLOSES one user's min_notional, muted whales and "
                   "sports to anyone who names the key"),
        "was": "no check",
        "now": "STILL no check -- open, and the key is in the URL path",
    },
    {
        "route": "PUT /api/prefs/{user_key}",
        "guard": "NONE",
        "writes": "user_prefs for the named key",
        "effect": "OVERWRITES one user's alert preferences",
        "was": "no check",
        "now": "STILL no check -- open, and the key is in the URL path",
    },
)

UNGUARDED_ROUTES = tuple(r["route"] for r in DEPENDENT_ROUTES
                         if r["guard"] == "NONE")

#: THE BYPASSES, CHECKED RATHER THAN ASSUMED ABSENT. A boundary is only as good
#: as the set of routes that can write the column it rests on.
BYPASSES = (
    {
        "id": "OWNER_REASSIGNMENT_VIA_SUBSCRIBE",
        "was_open": True,
        "closed": True,
        "how_it_worked": (
            "POST /api/push/subscribe with a known endpoint and the attacker's "
            "own user_key took ownership of the row via ON CONFLICT DO UPDATE "
            "SET user_key, after which POST /api/push/unsubscribe succeeded "
            "legitimately. Two requests, and the ownership check I had just "
            "added was satisfied on the way through"),
        "closed_by": ("the conflict clause now carries "
                      "WHERE push_subscriptions.user_key = $1"),
        "why_it_matters_beyond_this_route": (
            "it is the general shape of the mistake: a check on one handler is "
            "not a boundary while another handler can write the column the "
            "check reads"),
    },
    {
        "id": "KEY_DISCLOSED_BY_ITS_OWN_URL",
        "was_open": True,
        "closed": False,
        "how_it_works": (
            "the prefs routes carry the key as a path segment, so it is logged "
            "by every intermediary. A value that authorises anything must not "
            "travel where it is routinely recorded"),
        "what_would_close_it": (
            "move the key out of the path -- a header or a body field -- and "
            "guard the prefs routes. Both are caller-visible changes and need "
            "the frontend released with them"),
    },
    {
        "id": "NO_BINDING_BETWEEN_KEY_AND_BROWSER",
        "was_open": True,
        "closed": False,
        "how_it_works": (
            "nothing ties the key to the device that generated it, so a copied "
            "key works from anywhere. This is inherent to a bearer value and "
            "is not a bug in the routes"),
        "what_would_close_it": (
            "real per-user authentication, which is a product decision about "
            "whether this surface has accounts at all"),
    },
)

OPEN_BYPASSES = tuple(b["id"] for b in BYPASSES if not b["closed"])

#: The register's language for this finding, and its true size.
STATUS = "PARTIALLY_REPAIRED"
WHAT_IS_REPAIRED = (
    "the endpoint-only deletion, and the ownership reassignment that made the "
    "deletion fix ineffective",
)
WHAT_IS_OPEN = (
    "the two prefs routes have no authorization at all: one discloses a user's "
    "preferences, the other overwrites them",
    "the key travels as a URL path segment, so its confidentiality is not "
    "maintained by the system that depends on it",
    "there is no binding between the key and the browser that made it",
)
BLAST_RADIUS = {
    "capital": "NONE. No order, position, balance or money is reachable",
    "worst_case": ("someone's notifications switched off, or their alert "
                   "preferences read and rewritten"),
    "and_it_is_still_a_finding": (
        "an unauthorized write on another user's row is an authorization "
        "defect at whatever size. It is kept on the register at its actual "
        "size -- not dropped for being small, and not inflated for being real"),
}


def classification() -> str:
    return USER_KEY_CLASSIFICATION["classification"]


def route(name: str) -> dict | None:
    for r in DEPENDENT_ROUTES:
        if r["route"] == name:
            return r
    return None


def describe() -> dict:
    return {
        "classification": USER_KEY_CLASSIFICATION,
        "routes": [dict(r) for r in DEPENDENT_ROUTES],
        "unguarded_routes": list(UNGUARDED_ROUTES),
        "bypasses": [dict(b) for b in BYPASSES],
        "open_bypasses": list(OPEN_BYPASSES),
        "status": STATUS,
        "what_is_repaired": list(WHAT_IS_REPAIRED),
        "what_is_open": list(WHAT_IS_OPEN),
        "blast_radius": dict(BLAST_RADIUS),
        "the_correction_to_me": (
            "I added an identifier and reported a boundary. A client-supplied "
            "identifier is proof of POSSESSION, and this one was reassignable "
            "by another route of mine, so it was not even that"),
    }
