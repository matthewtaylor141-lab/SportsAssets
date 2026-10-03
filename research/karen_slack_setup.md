# Karen on Slack: the one manual admin step

Karen (red team / challenge agent, migration 207) is wired into the existing
Slack bridge (`backend/sportsassets/slack_bridge.py`) as a fourth bot
identity. The bridge already gives every agent its own Slack app, read from
`SLACK_<AGENT>_BOT_TOKEN`, `SLACK_<AGENT>_SIGNING_SECRET` and
`SLACK_<AGENT>_APP_ID`. Everything on the code side is done; what is missing
is a Slack app for Karen, which only a workspace admin can create.

Until the step below is done Karen simply does not post: her challenges are
recorded and shown on `/karen` and `GET /api/command/karen`, and the bridge
keeps working for Derek, Xavier and Audrey (Karen is optional when the bridge
is enabled).

## The step (Slack workspace admin + whoever manages the API service's env)

Secrets go directly into Render (Dashboard → `sportsassets-api` →
Environment), never into chat, a repository or a ticket — the same as for the
other three apps (`research/completion-20261002/SLACK-INSTALL-CHECKLIST.md`).

1. **Create the Slack app from the supplied manifest.** api.slack.com/apps →
   *Create New App* → *From a manifest* → the same BettorToken workspace as
   Derek, Xavier and Audrey (`SLACK_TEAM_ID`) → paste
   `research/karen-manifest.json`. App name **Karen · BettorToken**, bot
   display name **Karen**; scopes `app_mentions:read`, `chat:write`; event
   `app_mention`; request URL
   `https://sportsassets-api.onrender.com/api/integrations/slack/karen/events`.
2. **Signing secret and App ID → Render** (API service `sportsassets-api`):
   `SLACK_KAREN_SIGNING_SECRET` (*Basic Information → App Credentials*) and
   `SLACK_KAREN_APP_ID`. Save (Render redeploys the API), then in the app →
   *Event Subscriptions* click *Retry* on the request URL until *Verified*.
3. **Install and bot token → Render.** *Install App* → *Install to
   Workspace*; copy the *Bot User OAuth Token* (`xoxb-…`) into
   `SLACK_KAREN_BOT_TOKEN`. Save.
4. **Channels.** In `#agent-workroom` and the management channel run
   `/invite @Karen`.

No other variable changes: team, channels, managers and workroom are shared.

## Never reuse another agent's app

Karen must be her **own** app. If any of her three values equals Derek's,
Xavier's or Audrey's, the bridge refuses every Karen delivery
(`IMPERSONATION_REFUSED_KAREN_TOKEN_NOT_HER_OWN`), and content of Karen's
(`source_key` `karen:...`) is never sent under another agent's token
(`IMPERSONATION_REFUSED_KAREN_CONTENT_ON_ANOTHER_TOKEN`). To check after
setting the variables: `POST /api/command/agents/slack/control` with
`{"action": "check_tokens", "actor": "<your name>"}` runs Slack's `auth.test`
per agent (it posts nothing). `karen.ok` should be `true` and
`karen.shares_bot_user_with` should be `[]`. `GET /api/command/agents/slack`
shows `karen_identity: {"configured": true, "distinct": true, "ok": true}`.

## What Karen posts and answers

- **Posts**: her new HIGH / CRITICAL challenges from the last hour, to the
  workroom, under her token only, once each (`karen:challenge:<id>`). Each
  post names the target agent, the record ids it cites, and says that the
  target answers and Karen cannot resolve her own challenge.
- **Answers a mention**: from her challenge records only (open challenges,
  precision and grounding with their numerators and denominators). No
  language model, no research assignment, and nothing in the question is
  followed as an instruction.
- She has no authority through Slack or anywhere else: no orders, no
  approvals, no activation, no promotion, no control changes. The database
  refuses her as the actor of the bridge's own on/off control.
