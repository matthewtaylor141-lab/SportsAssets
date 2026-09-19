# ACTIVATION CHECKLIST — what Matt must provide

Answer to: *"Tell me exactly what I need to provide to get our institutional
account live and linked to the system."*

There are two tiers. **Tier 1 costs about five minutes and unblocks everything
that is currently blocked.** Tier 2 waits on Polymarket.

---

## TIER 1 — four repository secrets (PREPROD). Unblocks the lifecycle lab.

GitHub → this repository → **Settings → Secrets and variables → Actions →
New repository secret**. Four names, exactly as spelled:

| Secret name | Value | Where it comes from |
|---|---|---|
| `PMX_CLIENT_ID` | preprod Auth0 client id | Polymarket preprod onboarding |
| `PMX_PARTICIPANT_ID` | `firms/20260902-bettortokenllc-api-participant/users/20260902-bettortokenllc-api-user` | already known from our own 2026-09-10 run |
| `PMX_KEY_ID` | the `kid` registered for our preprod public key | Polymarket preprod onboarding |
| `PMX_PRIVATE_KEY_B64` | preprod private key PEM, **base64, single line** | our own keypair; never leaves your machine except into this box |

To produce the fourth value on your machine:

```
base64 -w0 pmx-preprod-private-key.pem
```

(macOS: `base64 -i pmx-preprod-private-key.pem | tr -d '\n'`)

Paste the output straight into the secret box. **Do not paste it into this
conversation, a ticket, a PR, or email.** I never need to see any of these
four values — I only need them to exist in the store.

### How you confirm it worked, without giving me anything

Run the workflow **pmx-preprod** with `action: whoami`. That is a read-only
call. The run reports one of four verdicts by name and prints no value:

- `SECRET_MISSING` — one of the four is not set
- `SECRET_UNAVAILABLE_TO_THIS_WORKFLOW` — set, but not visible to the job
- `AUTHENTICATION_REJECTED` — reached Auth0, credential refused
- `AUTHENTICATED` — good; identity and scopes are then read back

### Why this is the whole Tier 1

Four secrets carry the lab from AUTH through IDENTITY → REFDATA/MARKET → L2
→ ORDER PREVIEW → REPORT SEARCH → ORDER STREAM. A fifth, `PMX_ACCOUNT`, is
required only from the **SUBMIT** step onward, and its value is already known
from our own logs:

```
firms/20260902-bettortokenllc-api-clearing-member/accounts/20260902-bettortokenllc-api-account
```

So it is a parameter we already hold, not a discovery. Set it at the same
time if convenient; nothing before SUBMIT reads it.

### One optional hygiene item — it blocks nothing

Runs 1–24 on 2026-09-10 printed the **preprod** client id, participant
resource name and key id into Actions logs.

**The private key was not exposed, and that is verified rather than assumed.**
Run 24's log was re-read on 2026-09-19: the key never passed through an
`env:` block or a rendered `run:` line — it was read from the event payload
on disk, masked before decoding, written 0600, and scrubbed. The staging step
printed a byte count and nothing else. No key material, bearer token or signed
assertion appears in any of those logs.

Identifiers are not key material. Rotating the preprod `kid` is therefore a
hygiene recommendation, not a prerequisite, and it is Polymarket's to perform
— we do not revoke unilaterally. Ask for it whenever convenient; nothing waits
on it.

---

## TIER 2 — production. Waits on Polymarket, not on you.

Send `PRODUCTION_ONBOARDING_REQUEST.md` (same directory) to the onboarding
contact. It asks only for things their documentation cannot answer: credential
delivery status, our production resource names, the scopes actually granted,
the fee schedule, our rate-limit tier, the gRPC hostname their docs spell four
different ways, and FIX/PrivateLink terms.

When they reply, the same credential holder installs a **separate** set of
secrets — production values never overwrite the preprod ones:

```
PMX_PROD_CLIENT_ID
PMX_PROD_PARTICIPANT_ID
PMX_PROD_KEY_ID
PMX_PROD_PRIVATE_KEY_B64
PMX_PROD_ACCOUNT
```

Production credentials unlock a **read-only** verification pass only —
authentication, identity, scopes, balances, instruments, report search,
positions. No order. Production trading stays disabled and requires separate
written approval.

---

## What you do NOT need to provide

- Any secret, in any message, to me.
- The private key to Polymarket. We generate the keypair; only the **public**
  key is submitted.
- The production REST base, Auth0 domain or token audience — their current
  documentation answers all three, and we have them.
- Any decision about FIX. We are not requesting it and it is not a
  prerequisite.
- Any change to RN1, to budgets, or to the public capture lane.
