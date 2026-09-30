# Provisioning the owner's resolution credentials

**Names only.** This document names two settings and says how the owner puts them in place. It
contains no value, and no value should ever be sent back in reply to it: not the key, not its length,
not a hash, not a screenshot that shows it.

| setting | what it is | secret? |
|---|---|---|
| `FUNDED_RESOLUTION_KEY` | the owner-held second factor (`X-Resolution-Key`) | **yes** |
| `FUNDED_RESOLUTION_OPERATOR` | the name that key authenticates; written into every audit row | no (a name) |

## What they guard

Both are read by `require_resolution_key` in `backend/sportsassets/api/app.py`. With either unset the
routes below answer **503 `FUNDED_RESOLUTION_KEY_NOT_CONFIGURED`** and do nothing:

- `POST /api/admin/funded-owner-authorization` — the owner signs the funded scope (account, venue,
  effective-limit digest).
- `POST /api/admin/funded-owner-authorization/revoke` — the owner withdraws it.
- `POST /api/admin/funded-investigations/{intent_id}/resolve` — the owner closes a lost acknowledgement.

Each of these also needs the admin token. The admin token is held by verification workflows, so on its
own it proves possession of a service credential, not the owner's decision; the resolution key is the
factor only the owner holds. The operator recorded is always `FUNDED_RESOLUTION_OPERATOR` from the
server's settings, never a name typed into a request.

Before signing, `GET /api/admin/funded-owner-authorization` (admin token only) shows `to_sign`: the
exact account, venue and approved effective-limit digest the signature would bind, plus
`owner_key_configured` (a boolean — never the value).

`backend/sportsassets/config.py` lists both names in `OWNER_CREDENTIAL_CONSEQUENCES`, reported by the
credential posture beside — not inside — `CREDENTIAL_CONSEQUENCES`. They are kept out of
`all_configured` deliberately: that flag is published by `/healthz` and the verification workflow expects
it to read `true`; an owner-only credential that is not yet provisioned is the correct state for a
service with capital off, not a misconfiguration.

## The steps (owner)

1. **Generate the key off-platform** — in a password manager or with a local generator you trust. At
   least 32 random characters. Do not generate it in a GitHub workflow, a chat, a ticket or a shared
   document.
2. **Check what saving will deploy, before you save anything.** The environment screen of a Render
   service can trigger a deploy of the **latest commit on the branch the service tracks**, which may not
   be the reviewed commit.
   - `render.yaml` declares **no `autoDeploy`** for `sportsassets-api`, so the service follows Render's
     default (auto-deploy on). Do not assume it is off.
   - Read the live setting without revealing anything: Render dashboard → `sportsassets-api` →
     **Settings → Build & Deploy → Auto-Deploy**, or ask engineering to run `render-ops` action
     `api-branch-get`, which prints only the tracked branch and `autoDeploy`.
   - If the environment screen offers **"Save only"** (save without deploying), use it.
   - If it offers only "Save and deploy" / "Save, rebuild, and deploy", do **not** save yet: first set
     **Auto-Deploy to "No"** in Settings, or confirm with engineering that the tracked branch's head is
     exactly the reviewed commit. Saving otherwise may release an unreviewed commit.
3. **Enter both values in the Render dashboard only**: `sportsassets-api` → **Environment** → add
   `FUNDED_RESOLUTION_KEY` (the generated value) and `FUNDED_RESOLUTION_OPERATOR` (the name you will sign
   as). Only the API service needs them; do not add them to `sportsassets-workers` or any other service.
4. **Reply "resolution credentials in place".** Nothing else. If a value is ever pasted into a
   conversation, treat it as compromised and rotate it (below).

## The restart (engineering)

`settings()` is `lru_cache`d (`config.py`), so a running process keeps the old (empty) values until it
restarts. The restart must be **the reviewed SHA through the established API-only route**:

- GitHub Actions → `render-ops` → action **`deploy-api-commit`**, `confirm=DO`, `arg` = the reviewed
  **40-hex** commit SHA. This deploys only `sportsassets-api`, by commit id, and cannot reach a worker.
- Not `restart` or `deploy` (those rebuild whatever the branch points at), and not a dashboard deploy.

## Verification that reveals no value

1. `GET /api/admin/funded-investigations` with the admin token → **`resolution_key_configured: true`**.
   (`GET /api/admin/funded-owner-authorization` → `owner_key_configured: true` says the same.)
2. `POST /api/admin/funded-owner-authorization/revoke` with the admin token and a **deliberately wrong**
   `X-Resolution-Key` → **401 `RESOLUTION_KEY_REQUIRED`** means the key is configured; **503** means it is
   not (or the operator name is missing). A wrong key is rejected by the dependency before the handler
   runs, so this writes nothing.
3. Neither check prints, measures or hashes the value.

## Why `render-ops env-set` must NOT be used for the key

`env-set` takes `arg=KEY=VALUE` as a `workflow_dispatch` input, and:

- the value stays in the run's stored **event payload**, readable by anyone with access to the run, and
  whoever triggers it has typed it into GitHub;
- it prints the value's **length** (twice), and `env-keys` prints every value's length;
- the value is passed on curl's **argv** (`-d`);
- if `arg` has **no `=`**, nothing is masked and the argument is **echoed in cleartext** before the
  refusal;
- Render then redeploys the **tracked branch**, not the reviewed SHA.

Masking hides a value in logs only. `env-set` is acceptable for `FUNDED_RESOLUTION_OPERATOR`, which is a
name, but setting both in the dashboard in one step (above) is simpler. A lasting GitHub secret for the
key would contradict its owner-only purpose and is not used.

## Residual risk (cannot be removed by provisioning)

`RENDER_API_KEY`, held as a GitHub secret for the operations workflows, can **read environment values**
through the Render API. Anyone who can put a workflow on the default branch can therefore read
`FUNDED_RESOLUTION_KEY`, however it was entered. The key raises the bar from "holds the admin token" to
"holds the owner's key or can merge a workflow to the default branch"; it does not remove the second
path. Branch protection on the default branch is what bounds it.

## Rotation

Repeat steps 1–4 with a new value, then the restart. The old value stops working when the
`deploy-api-commit` restart completes (settings are read once per process). Rotation does not affect a
recorded owner authorization — records carry which factors were verified, never the key — so an
authorization signed under the old key stays valid until it expires, is revoked, or its scope changes.
If the key was exposed, **revoke the active owner authorization** as well (`POST
/api/admin/funded-owner-authorization/revoke`) and sign again under the new key.
