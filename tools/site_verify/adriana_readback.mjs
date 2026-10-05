// ADRIANA · signed-in JSON readback (READ ONLY: two GETs with the admin token,
// exactly the header hq.mjs sends). Writes OUT/adriana_readback.json: her
// served floor seat (deployed, state, work state, monitor, heartbeat) and her
// workspace census (latest pass, venues, opportunities, refusals, blockers).
// A non-200 is recorded with its status and body head -- never a zero.
import fs from 'node:fs';
export async function adrianaReadback(host, token, out) {
  const H = { 'x-admin-token': token, accept: 'application/json' };
  const get = async (p) => { const r = await fetch(host + p, { headers: H, signal: AbortSignal.timeout(30000) }); const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) { j = null; } return { http: r.status, json: j, head: j ? null : t.slice(0, 300) }; };
  const rep = { at: new Date().toISOString() };
  try {
    const fl = await get('/api/command/floor');
    const seat = fl.json && (fl.json.agents || []).find((a) => a.agent === 'ADRIANA');
    rep.floor_http = fl.http;
    rep.seat = seat ? { deployed: seat.deployed, deploy_why: seat.deploy_why, state: seat.state, work_state: seat.work_state, work_detail: seat.work_detail,
      heartbeat: seat.heartbeat, monitor: seat.monitor, last_output: seat.last_output, authority: seat.authority && seat.authority.level } : null;
    rep.adriana_edges = fl.json ? (fl.json.edges || []).filter((e) => e.from === 'ADRIANA' || e.to === 'ADRIANA').map((e) => ({ from: e.from, to: e.to, kind: e.kind, count: e.count, at: e.at })) : null;
    const ws = await get('/api/command/adriana');
    rep.workspace_http = ws.http;
    if (ws.json) {
      const s = ws.json.sections || {};
      const sec = (k) => s[k] ? { status: s[k].status, why: s[k].why, n: Array.isArray(s[k].data) ? s[k].data.length : null } : null;
      rep.agent = ws.json.agent; rep.census = ws.json.census; rep.venues = ws.json.venues;
      rep.opportunities = sec('opportunities'); rep.refusals = sec('refusals'); rep.blockers = sec('blockers'); rep.census_passes = sec('census_passes');
      rep.top_refusals = (s.refusals && s.refusals.data || []).slice(0, 5).map((r) => ({ primary_code: r.primary_code, codes: r.codes, event_key: r.event_key, kind: r.structure_kind }));
      rep.authority = ws.json.profile && ws.json.profile.authority_declaration;
      rep.mode = ws.json.mode; rep.production_effect = ws.json.production_effect;
    } else rep.workspace_head = ws.head;
  } catch (e) { rep.error = String(e).slice(0, 200); }
  fs.writeFileSync(out + '/adriana_readback.json', JSON.stringify(rep, null, 1));
  return rep;
}
