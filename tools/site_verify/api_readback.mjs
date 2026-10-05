// SIGNED-IN API READBACK (READ ONLY: GETs with the admin token, the header
// hq.mjs sends). Writes OUT/api_readback.json: for each route its HTTP status
// and a trimmed body (arrays cut to 8 items, with their length). A non-200 is
// recorded with its status and body head -- never a zero.
import fs from 'node:fs';
const ROUTES = [
  '/api/command/release',
  '/api/command/equity/live',
  '/api/command/paper/audrey?limit=3',
  '/api/command/paper/freshness',
  '/api/command/paper/turnaround',
  '/api/command/coverage/first-loss?hours=24',
  '/api/command/archer',
  '/api/command/adriana',
  '/api/command/profitability/os',
  '/api/command/execution-calibration',
  '/api/command/paper/derek',
  '/api/command/paper/xavier',
];
function trim(v, depth = 0) {
  if (Array.isArray(v)) { const a = v.slice(0, 8).map((x) => trim(x, depth + 1)); return v.length > 8 ? { _len: v.length, _first: a } : a; }
  if (v && typeof v === 'object') { if (depth > 7) return '…'; const o = {}; for (const k of Object.keys(v)) o[k] = trim(v[k], depth + 1); return o; }
  return v;
}
export async function apiReadback(host, token, out) {
  const H = { 'x-admin-token': token, accept: 'application/json' };
  const rep = { at: new Date().toISOString(), routes: {} };
  for (const p of ROUTES) {
    try {
      const r = await fetch(host + p, { headers: H, signal: AbortSignal.timeout(45000) });
      const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) { j = null; }
      if (j && p.startsWith('/api/command/equity/live') && j.paper) {
        const m = j.paper.management;
        j = { paper: { status: j.paper.status, equity_usd: j.paper.equity_usd, cash_usd: j.paper.cash_usd, realized_pnl_usd: j.paper.realized_pnl_usd,
          unrealized_pnl_usd: j.paper.unrealized_pnl_usd, since_inception: j.paper.since_inception, open_positions: j.paper.open_positions && { count: j.paper.open_positions.count, marked: j.paper.open_positions.marked, unmarked: j.paper.open_positions.unmarked, stale_marks: j.paper.open_positions.stale_marks },
          freshness: j.paper.freshness, management: m ? Object.assign({}, m, { rows: Array.isArray(m.rows) ? { _len: m.rows.length, _first: m.rows.slice(0, 5) } : m.rows }) : null }, small: j.small && { status: j.small.status } };
      }
      rep.routes[p] = { http: r.status, body: j ? trim(j) : null, head: j ? null : t.slice(0, 300) };
    } catch (e) { rep.routes[p] = { error: String(e).slice(0, 200) }; }
  }
  fs.writeFileSync(out + '/api_readback.json', JSON.stringify(rep, null, 1));
  return rep;
}
