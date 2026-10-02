# Slack installation — the remaining owner steps (one pass)

Secrets go directly into Render (Dashboard → `sportsassets-api` → Environment),
never into chat, a repository or a ticket. Everything else is already deployed
or will be by the release that carries it; the bridge stays OFF until step 6.

Prerequisite: the API release carrying migration 190 (`e1514ec` or later) is
live, so the three event URLs answer Slack's URL check.

1. **Create the three apps from the supplied manifests.**
   api.slack.com/apps → *Create New App* → *From a manifest* → choose the
   verified BettorToken workspace → paste
   `research/completion-20261002/derek-manifest.json`; repeat for
   `xavier-manifest.json` and `audrey-manifest.json`. (Scopes:
   `app_mentions:read`, `chat:write`; event `app_mention`; request URL
   `https://sportsassets-api.onrender.com/api/integrations/slack/<agent>/events`.)
2. **Signing secrets → Render.** Each app → *Basic Information* → *App
   Credentials*. In Render set `SLACK_DEREK_SIGNING_SECRET`,
   `SLACK_XAVIER_SIGNING_SECRET`, `SLACK_AUDREY_SIGNING_SECRET`, and the App
   IDs `SLACK_DEREK_APP_ID`, `SLACK_XAVIER_APP_ID`, `SLACK_AUDREY_APP_ID`.
   Save once (Render redeploys the API with them). Then in each app →
   *Event Subscriptions* click *Retry* on the request URL until it shows
   *Verified*.
3. **Install and bot tokens → Render.** Each app → *Install App* → *Install to
   Workspace*. Copy each *Bot User OAuth Token* (`xoxb-…`) into
   `SLACK_DEREK_BOT_TOKEN`, `SLACK_XAVIER_BOT_TOKEN`, `SLACK_AUDREY_BOT_TOKEN`.
4. **Channels.** In Slack, in `#agent-workroom` and in the management channel,
   run `/invite @Derek @Xavier @Audrey`. Copy each channel ID (channel name →
   *About* → bottom).
5. **Workspace and identity values → Render** (identifiers, not secrets, but
   set them in Render too):
   - `SLACK_TEAM_ID` — the workspace ID (`T…`).
   - `SLACK_WORKROOM_CHANNEL_ID` — `#agent-workroom` (previously recorded as
     `C0C5ZDYQT6Z`; confirm it).
   - `SLACK_MANAGEMENT_CHANNEL_ID` — the verified management channel.
   - `SLACK_ALLOWED_CHANNEL_IDS` — both IDs, comma-separated.
   - `SLACK_MANAGEMENT_USER_IDS` — the Slack member IDs (`U…`, profile → ⋯ →
     *Copy member ID*) allowed to address the agents, comma-separated.
   Save (one more redeploy).
6. **Tell me it's done.** I will then read back the configuration booleans
   (`GET /api/command/agents/slack`, values never shown), enable the bridge
   through the authenticated control with your name as the actor, and run the
   verification: one management question and follow-up per agent in a thread,
   one investigation reviewed by all three agents posted to
   `#agent-workroom`, and the initial reconciled status briefing in the
   management channel — returning permalinks, task IDs and source records.

What the bridge will do once ON:
- Answer only `@mentions` from the listed managers in the listed channels;
  thread follow-ups keep the same persona conversation.
- `@Agent assign: <question>` opens one durable research flow (that agent
  investigates, the next reviews, Audrey audits) and replies with task IDs.
- Post only *recorded* genuine reviews to `#agent-workroom`; never reply to
  bots; stages are labelled (hypothesis, recommendation, approved experiment,
  evaluated result, activated change) as recorded.
- Management channel: an initial briefing, hourly summaries, deduplicated
  alerts (service failure, reconciliation discrepancy, research blocker) and a
  daily report after 22:00 UTC, with cash, reserved, exposure, realized and
  unrealized P&L separate and training reported apart from investment.
- Financial changes stay behind the existing authenticated controls; nothing
  in Slack can place, change or approve an order or a policy.
