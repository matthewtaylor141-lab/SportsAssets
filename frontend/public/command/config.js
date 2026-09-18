/* Deployment configuration. No secrets belong in this file.
 *
 * BOTH ROUTES NOW EXIST (2026-09-18). They are implemented server-side in
 * backend/sportsassets/api/app.py and built by api/command_snapshot.py from
 * the actual ledger, and netlify.toml proxies /api/command/* to the API so
 * they are same-origin -- which core.endpoint() requires and which keeps the
 * session in an HttpOnly cookie this file never sees.
 *
 * autoConnect is TRUE under the management directive of 2026-09-18 section C
 * ("connect the actual COMMAND interface to authenticated operational
 * records"). Connecting reads OUR OWN backend snapshot, never the venue: the
 * account figures behind it come from one shared 30-second process cache, so
 * N browsers are N reads of one snapshot and never N venue collectors.
 *
 * An unauthenticated visitor gets 401 and the page says so. It does NOT fall
 * back to the demo: demo stays opt-in (?demo=1) and is labelled ILLUSTRATIVE,
 * because a dashboard that answers "feed down" with fiction is worse than one
 * that answers nothing. */
window.BETTOR_COMMAND_CONFIG = Object.assign({
  snapshot: '/api/command/snapshot',
  investorSnapshot: '/api/command/investor/snapshot',
  stream: null,
  investorStream: null,
  pollMs: 15000,
  staleMs: 45000,
  autoConnect: true,
  defaultDemo: false,
  logo: 'assets/bt-logo-full.png'
}, window.BETTOR_COMMAND_CONFIG || {});
