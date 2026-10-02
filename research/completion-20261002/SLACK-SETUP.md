# Slack bridge deployment and activation

This is tested backend code, not an installed bot. No Slack credentials are included. This bridge answers explicit management @mentions in approved channels. Each agent keeps thread conversation history through the existing persona service. Agent-to-agent research is performed in the durable capability work queue; Slack publishes the genuine recorded reviews. Slack bot messages never recursively trigger other bots. Direct messages, arbitrary channel reading, trade commands, connector installation and policy activation are not implemented here.

1. Gate the backend candidate, including migration 190 and `test_slack_agent_bridge.py`, in an isolated checkout/database. If another migration 190 exists on a newer branch, renumber this new migration before gating; never edit an already-applied migration.
2. Create each app from its JSON manifest in the verified BettorToken workspace. The event route must be deployed, and that app's signing secret configured, before Slack can verify the request URL. Install the app and invite it to the approved channels. Start with one verified test/workroom channel.
3. Configure Render secrets directly, never through chat or a repository: `SLACK_DEREK_BOT_TOKEN`, `SLACK_DEREK_SIGNING_SECRET`, `SLACK_DEREK_APP_ID`, and equivalent XAVIER/AUDREY names. Configure `SLACK_TEAM_ID`, comma-separated `SLACK_ALLOWED_CHANNEL_IDS`, `SLACK_MANAGEMENT_USER_IDS`, and `SLACK_WORKROOM_CHANNEL_ID`. Do not infer management identities. Workroom previously verified: C0C5ZDYQT6Z; re-read workspace identity before configuring.
4. Authenticated GET `/api/command/agents/slack` shows configuration booleans and delivery-state counts, never token values. All three agents must be configured before enable. POST `/api/command/agents/slack/control` with the existing write credential and JSON `{"enabled":true,"actor":"Matt Taylor"}` enables the worker and records an audit row. Default is off.
5. Send one approved management @mention to each agent. Verify `SENT`, Slack timestamp, persona message ID, thread follow-up history, one response despite Slack retries, and no response to an unauthorized user or bot. Exercise disable and read it back. Then re-enable for the authorized workspace use.
6. Run `activate_research.py --apply --actor 'Matt Taylor'` from the existing authenticated environment to enable bounded research and create the owner's three durable goals. This action does not activate policy changes. A genuine completed research review can then be published by the agent's own bot identity. Automated acknowledgements are never published as reviews.

Ambiguous transport outcomes are `DELIVERY_UNKNOWN` and require manual reconciliation against the channel before any retry. Queue size 300 and research/provider request budgets remain operational safeguards; they do not limit simulated trading allocation. Failed deliveries are not silently reposted. Stored questions and responses contain management content: retain the same access/retention controls as persona conversations. Current implementation stores delivery records indefinitely; include them in the existing retention policy before broad rollout.

Official API references checked 2026-10-02:
- https://docs.slack.dev/authentication/verifying-requests-from-slack/
- https://docs.slack.dev/reference/events/app_mention/
- https://docs.slack.dev/reference/scopes/app_mentions.read/
- https://docs.slack.dev/reference/scopes/chat.write/
