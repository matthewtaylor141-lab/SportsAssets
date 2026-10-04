# Eddie and Scout on Slack: one admin action per app

Each agent is its own Slack app with its own bot user. Nothing is sent until
the matching action below is done, and nothing in this change sends a
message.

**EDDIE -- THE ACTION (a Slack workspace admin with access to the Render
dashboard):** create and install the Slack app from
`research/eddie-manifest.json` in the BettorToken workspace, then paste its
*Bot User OAuth Token*, *Signing Secret* and *App ID* into the
`sportsassets-api` service's environment as `SLACK_EDDIE_BOT_TOKEN`,
`SLACK_EDDIE_SIGNING_SECRET` and `SLACK_EDDIE_APP_ID`.

**SCOUT -- THE ACTION (the same admin):** create and install the Slack app
from `research/scout-manifest.json` in the BettorToken workspace, then paste
its *Bot User OAuth Token*, *Signing Secret* and *App ID* into the
`sportsassets-api` service's environment as `SLACK_SCOUT_BOT_TOKEN`,
`SLACK_SCOUT_SIGNING_SECRET` and `SLACK_SCOUT_APP_ID`.

That is all. Team, allowed channels, managers, workroom and the bridge's
on/off control are shared with the other agents and already configured.

## Until then, they send nothing

- Their estimates, features, tests, scorecards and candidate reviews are
  recorded and shown on `/eddie`, `/scout`, `GET /api/command/eddie` and
  `GET /api/command/scout` regardless.
- A delivery for either agent without its three values fails
  `EDDIE_SLACK_APP_NOT_CONFIGURED_NOTHING_SENT` /
  `SCOUT_SLACK_APP_NOT_CONFIGURED_NOTHING_SENT` and is not sent.
- The bridge works for Derek, Xavier, Audrey and Karen without them; neither
  is in `REQUIRED_AGENTS`.

## Their own bots, never another agent's

Each posts only as itself (`agent` `eddie` / `scout`) under its own token.
If any of its three values equals ANY other agent's (Derek, Xavier, Audrey,
Karen, or each other), every delivery for it is refused
(`IMPERSONATION_REFUSED_EDDIE_TOKEN_NOT_ITS_OWN` /
`IMPERSONATION_REFUSED_SCOUT_TOKEN_NOT_ITS_OWN`), and content of theirs
(`source_key` `eddie:...` / `scout:...`) is never sent under another agent's
token (`IMPERSONATION_REFUSED_<AGENT>_CONTENT_ON_ANOTHER_TOKEN`).

Check after the action (posts nothing): `POST
/api/command/agents/slack/control` with `{"action": "check_tokens",
"actor": "<your name>"}` -- `eddie.ok` / `scout.ok` true and
`shares_bot_user_with` `[]`; `GET /api/command/agents/slack` shows
`dedicated_identities.eddie` / `.scout` as
`{"configured": true, "distinct": true, "ok": true}`.

## What they say

A mention is answered from the agent's own records only (no language model
on Slack): Eddie lists his latest SHADOW estimates (recommendation, net
executable edge, EV) and his edge-preservation and incremental-vs-naive
scores, null with the reason when unmeasured; Scout lists his features,
their state and forward-test progress, and his incremental Brier score.
Neither has authority through Slack or anywhere else: the database refuses
either as the actor of the bridge's on/off control.
