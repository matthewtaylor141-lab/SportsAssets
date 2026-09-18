/* Deployment configuration. No secrets belong in this file.
 * A server-owned read-only aggregation endpoint must implement the contract.
 * Neither route below is assumed to exist in the existing API.
 * Demo is opt-in (?demo=1), never a fallback when a feed is unavailable. */
window.BETTOR_COMMAND_CONFIG = Object.assign({
  snapshot: '/api/command/snapshot',
  investorSnapshot: '/api/command/investor/snapshot',
  stream: null,
  investorStream: null,
  pollMs: 15000,
  staleMs: 45000,
  autoConnect: false,
  defaultDemo: false,
  logo: 'assets/bt-logo-full.png'
}, window.BETTOR_COMMAND_CONFIG || {});
