# Karen on Slack: the one admin action

**THE ACTION (a Slack workspace admin with access to the Render dashboard):**
create and install the Slack app from `research/karen-manifest.json` in the
BettorToken workspace, then paste its *Bot User OAuth Token*, *Signing
Secret* and *App ID* into the `sportsassets-api` service's environment as
`SLACK_KAREN_BOT_TOKEN`, `SLACK_KAREN_SIGNING_SECRET` and
`SLACK_KAREN_APP_ID`.

That is all. Everything else is already built and shared with the other
three agents (team, allowed channels, managers, workroom, the bridge's
on/off control). The manifest includes `chat:write.public`, so Karen can post
in the public `#agent-workroom` without being invited.

## Until then, Karen sends nothing

- Her challenges, peer responses and independent evaluations are recorded
  and shown on `/karen` and `GET /api/command/karen` regardless.
- The `#agent-workroom` path (`slack_bridge.publish_karen_challenges`)
  queues nothing unless her three values are present AND differ from every
  other agent's (`karen_identity().ok`). A Karen delivery that somehow exists
  without them fails `KAREN_SLACK_APP_NOT_CONFIGURED_NOTHING_SENT` and is
  not sent.
- The bridge works for Derek, Xavier and Audrey without her.

## Her own bot, never another agent's

Karen posts only as agent `karen`, under `SLACK_KAREN_BOT_TOKEN`. If any of
her three values equals Derek's, Xavier's or Audrey's, every Karen delivery
is refused (`IMPERSONATION_REFUSED_KAREN_TOKEN_NOT_HER_OWN`), and content of
hers (`source_key` `karen:...`) is never sent under another agent's token
(`IMPERSONATION_REFUSED_KAREN_CONTENT_ON_ANOTHER_TOKEN`).

Check after the action (posts nothing): `POST
/api/command/agents/slack/control` with `{"action": "check_tokens",
"actor": "<your name>"}` -- `karen.ok` true and
`karen.shares_bot_user_with` `[]`; `GET /api/command/agents/slack` shows
`karen_identity: {"configured": true, "distinct": true, "ok": true}`.

## What goes to #agent-workroom

At most three posts per bridge pass, each once (`karen:challenge:<id>`,
`karen:outcome:<id>`), from the last hour:

- a new challenge (HIGH / CRITICAL first): target agent, the record ids it
  cites, the claim, and that the target answers and Karen cannot resolve her
  own challenge;
- a recorded outcome: the target's peer response (CONCEDE / DISPUTE) and the
  independent evaluation (Audrey; Xavier for Audrey challenges) with its
  evidence ids.

A mention of Karen is answered from her challenge records only (no language
model on Slack). She has no authority through Slack or anywhere else; the
database refuses her as the actor of the bridge's own on/off control.
