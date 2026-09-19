# PRODUCTION PMX KEY ROTATION — so the bridge can be retired

Owner directive 2026-09-19 23:0xZ: "Separately prepare, but do not
block X1 on, a production-key rotation plan so a new key can later be
installed directly on sportsassets-workers for continuous institutional
L2."

## Why this exists

The current production private key is unrecoverable. It lives only as
the `PMX_PRIVATE_KEY_B64` GitHub repository secret, GitHub does not
reveal secret values, and the owner no longer holds a copy. So the
credential cannot be copied onto Render, and institutional L2 reaches
BETTOR only through the GitHub evidence bridge.

The bridge works, and it is honest — but its latency regime is a CI
runner's, minutes rather than milliseconds. A persistent worker needs
its own key. That means a NEW key pair, not a recovered one.

## What rotation is, and is not

It is **not** extracting, decoding or recovering the existing secret.
Nothing in this plan reads it. A new key pair is generated, registered
with the venue, and installed where it is needed; the old one is
retired by the venue.

## The sequence

1. **Generate a new RSA key pair** in a controlled place the owner
   holds — not in this repository, not in a runner, not in a chat.
   The private half never leaves that place except into a secret store.

2. **Register the public half with the venue** and obtain its `kid`.
   This is the venue's action and it is the step that takes calendar
   time; everything else is minutes. Coordinate rather than assume: a
   participant key registration is not self-service.

3. **Install on `sportsassets-workers`** as `PMX_CLIENT_ID`,
   `PMX_KEY_ID`, `PMX_PRIVATE_KEY_B64`, `PMX_PARTICIPANT_ID`,
   `PMX_ACCOUNT` — by the owner, through Render's own secret UI or
   `render-ops env-set`, whose value is masked in the log. The value
   is never pasted into a conversation, a commit or an issue.

4. **Verify before trusting**: one `whoami` and one `/v1/orderbook`
   read from the worker, and confirm the scopes include
   `read:l2marketdata`. Until that read succeeds the worker keeps using
   the bridge.

5. **Retire the old key AT THE VENUE**, not unilaterally. The standing
   instruction is explicit: coordinate any key-pair rotation or
   revocation with the venue. Revoking a key the GitHub lane still uses
   would take the bridge down with it, so the order is: new key
   verified on the worker → bridge switched to standby → old key
   retired.

6. **Switch the latency regime.** Evidence written by the worker is
   stamped `PERSISTENT_INSTITUTIONAL_WORKER`; the schema already
   refuses to pool it with `GITHUB_BRIDGE`. Experiments spanning the
   switch must be reported by regime, never averaged across it — the
   two are different execution environments and a blended number
   describes neither.

## What does NOT change

The preprod host guard in `pmx.py` stays. The production read lane
stays read-only. A new key grants the worker MARKET DATA access for the
experimental lane; it is not authorization to submit an order, and no
order path is added by this plan.

## Until then

The bridge is the institutional L2 source. It is temporary by
intention, not by neglect, and its rows say which regime they came
from so nothing has to be re-derived when the worker takes over.
