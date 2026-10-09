// WHERE THE DEVICE HARNESS LOOKS, AND WITH WHAT (read only).
//
// shots.js and trader_accept.js run in one of two modes, named on every
// record they write so a result is never mistaken for the other:
//   PREVIEW_BUILD    a target ref's frontend built on the runner and served by
//                    serve.js on 127.0.0.1, its /api/command/* reads proxied to
//                    production with the read credential added server side;
//   PRODUCTION_HOST  the deployed site itself (https://command.bettortoken.com),
//                    whatever build Netlify is serving; the read credential is
//                    added HERE, at the browser's request layer, to GET/HEAD
//                    /api/command/* on that origin only (never to a page, a
//                    URL, another origin or a write: every non-GET is aborted).
// The engine is Playwright's Chromium unless PW_ENGINE=webkit. Phones and
// tablets are EMULATED (viewport, device scale factor, touch, isMobile) in that
// engine: a Chromium run is not Safari, and neither is a physical device.
'use strict';
const PROD = 'https://command.bettortoken.com';
const TOKEN = process.env.PROD_READ_TOKEN || '';
const ENGINES = ['chromium', 'webkit'];

function engineName() {
  const name = (process.env.PW_ENGINE || 'chromium').toLowerCase();
  if (!ENGINES.includes(name)) throw new Error('PW_ENGINE must be one of ' + ENGINES.join(', '));
  return name;
}

async function launch(pw) {
  const name = engineName();
  const opts = name === 'chromium'
    ? { executablePath: process.env.PW_CHROMIUM || undefined,
        args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] }
    : {};
  const browser = await pw[name].launch(opts);
  return { browser, engine: name, engine_version: browser.version() };
}

function mode(base) { return base === PROD ? 'PRODUCTION_HOST' : 'PREVIEW_BUILD'; }

// continue a GET/HEAD; on the production host, /api/command/* carries the read
// credential (the preview server adds it itself in PREVIEW_BUILD mode)
function continueRead(route, base) {
  const req = route.request();
  const u = new URL(req.url());
  if (TOKEN && base === PROD && u.origin === PROD && u.pathname.startsWith('/api/command/')) {
    return route.continue({ headers: Object.assign({}, req.headers(), { 'x-admin-token': TOKEN }) });
  }
  return route.continue();
}

function describe(base, launched) {
  return { mode: mode(base), engine: launched.engine, engine_version: launched.engine_version,
    emulation: 'device profiles are emulated in ' + launched.engine + ' (viewport, device scale factor, touch, isMobile); not Safari unless engine is webkit, never a physical device',
    credential_at: mode(base) === 'PRODUCTION_HOST' ? 'browser request layer, GET/HEAD /api/command/* on the production origin only' : 'preview server (serve.js), server side' };
}

module.exports = { PROD, launch, mode, continueRead, describe };
