// FRONTEND DEVICE HARNESS: host mode and the read credential
// (.github/frontend-preview/harness_env.js).
//
// The production device run must be able to look at the DEPLOYED site, not
// only at a build served on the runner, and every record must say which it
// was. In PRODUCTION_HOST mode the read credential is added at the browser's
// request layer: to GET/HEAD /api/command/* on https://command.bettortoken.com
// and to nothing else (not another origin, not another path, not in
// PREVIEW_BUILD mode, where serve.js adds it server side). These tests drive
// continueRead with fake routes and pin describe()'s labels.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const HARNESS = path.resolve(__dirname, '../../../.github/frontend-preview/harness_env.js');

function load(token) {
  delete require.cache[HARNESS];
  if (token === undefined) delete process.env.PROD_READ_TOKEN; else process.env.PROD_READ_TOKEN = token;
  return require(HARNESS);
}

function fakeRoute(url, method = 'GET') {
  const calls = [];
  return {
    calls,
    request: () => ({ url: () => url, method: () => method, headers: () => ({ accept: 'application/json' }) }),
    continue: (o) => { calls.push(o || null); return Promise.resolve(); },
  };
}

const PROD = 'https://command.bettortoken.com';

test('the credential goes to production /api/command/ reads only', () => {
  const H = load('read-credential-for-test');
  const r = fakeRoute(PROD + '/api/command/paper/trader-mode?x=1');
  H.continueRead(r, PROD);
  assert.equal(r.calls[0].headers['x-admin-token'], 'read-credential-for-test');
  for (const url of [PROD + '/trader', PROD + '/api/public/thing', 'https://example.org/api/command/x',
                     'https://command.bettortoken.com.evil.test/api/command/x']) {
    const o = fakeRoute(url);
    H.continueRead(o, PROD);
    assert.equal(o.calls[0], null, url);
  }
});

test('preview mode never adds it in the browser (the preview server does)', () => {
  const H = load('read-credential-for-test');
  const r = fakeRoute('http://127.0.0.1:8787/api/command/snapshot');
  H.continueRead(r, 'http://127.0.0.1:8787');
  assert.equal(r.calls[0], null);
});

test('no credential configured: nothing is added anywhere', () => {
  const H = load(undefined);
  const r = fakeRoute(PROD + '/api/command/snapshot');
  H.continueRead(r, PROD);
  assert.equal(r.calls[0], null);
});

test('every record says which site and engine it measured, and that devices are emulated', () => {
  const H = load(undefined);
  const prod = H.describe(PROD, { engine: 'chromium', engine_version: '141.0' });
  assert.equal(prod.mode, 'PRODUCTION_HOST');
  assert.match(prod.emulation, /emulated in chromium/);
  assert.match(prod.emulation, /never a physical device/);
  const prev = H.describe('http://127.0.0.1:8787', { engine: 'webkit', engine_version: '26.0' });
  assert.equal(prev.mode, 'PREVIEW_BUILD');
  assert.equal(prev.engine, 'webkit');
  assert.match(prev.credential_at, /server side/);
});

test('only chromium and webkit are accepted engines', () => {
  const H = load(undefined);
  process.env.PW_ENGINE = 'firefox';
  assert.rejects(() => H.launch({}), /PW_ENGINE/);
  delete process.env.PW_ENGINE;
});
