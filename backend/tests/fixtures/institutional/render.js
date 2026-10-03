/* Render the institutional page's sections from captured route payloads.
 *
 *   node render.js <fixture-set-dir> [<fixture-set-dir> ...]
 *
 * Each set dir holds <read-key>.json = {"route", "http", "body"} captured
 * from the REAL routes (see capture.py). A 2xx body becomes a successful
 * read; anything else becomes a failed read with the reason the page itself
 * derives (BettorInstitutional.httpError). The page script runs in a bare
 * VM context (no DOM, no network, no autostart) and its pure renderAll()
 * output is printed as JSON: {set: {questions, sections: {id: html}}}.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PAGE = path.resolve(__dirname, '../../../../frontend/public/command/institutional.js');

function load() {
  const ctx = {BETTOR_INSTITUTIONAL_NO_AUTOSTART: true, console};
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(PAGE, 'utf8'), ctx, {filename: 'institutional.js'});
  return ctx.BettorInstitutional;
}

function renderSet(dir) {
  const BI = load();
  const results = {};
  for (const key of Object.keys(BI.READS)) {
    const file = path.join(dir, key + '.json');
    if (!fs.existsSync(file)) { continue; }
    const fx = JSON.parse(fs.readFileSync(file, 'utf8'));
    results[key] = fx.http >= 200 && fx.http < 300
      ? {ok: true, data: fx.body}
      : {ok: false, http: fx.http, error: BI.httpError(fx.http, fx.body)};
  }
  const out = BI.renderAll(results);
  const sections = {};
  for (const s of out.sections) { sections[s.id] = s.html; }
  return {questions: out.questions, sections, reads: Object.keys(BI.READS),
          missing: Object.keys(BI.READS).filter(k => !results[k])};
}

const got = {};
for (const dir of process.argv.slice(2)) { got[path.basename(dir)] = renderSet(dir); }
process.stdout.write(JSON.stringify(got));
