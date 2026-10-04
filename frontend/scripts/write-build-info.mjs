#!/usr/bin/env node
/* THE FRONTEND REPORTS ITS OWN BUILD (Operations desk, Release 1).
 *
 * Runs after `vite build` (package.json "build") and writes
 * dist/command/build.json -- the file the Command executive header reads to
 * show FRONTEND <sha7>. Netlify's build environment supplies the identity:
 *
 *   COMMIT_REF  the commit being built      -> sha
 *   BRANCH      the branch being built      -> branch
 *   CONTEXT     production | deploy-preview | branch-deploy | dev -> context
 *
 * Outside Netlify (no COMMIT_REF) it falls back to `git rev-parse HEAD` and
 * labels the build LOCAL, so a local build can never pass for a deployed one.
 * When neither is available the sha is null with the reason: the header then
 * says the build identity is unavailable instead of guessing.
 *
 * Usage: node scripts/write-build-info.mjs [outDir]   (default: dist)
 * Read-only apart from the one file it writes; no network access. */
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HEX = /^[0-9a-f]{7,40}$/

function git(args) {
  try {
    return execFileSync('git', args, { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
  } catch {
    return null
  }
}

export function buildInfo(env = process.env, now = new Date()) {
  const ref = String(env.COMMIT_REF || '').trim().toLowerCase()
  if (ref) {
    return {
      schema: 'bt.frontend.build.v1',
      sha: HEX.test(ref) ? ref : null,
      sha_why: HEX.test(ref) ? null : 'COMMIT_REF is not a hex commit',
      branch: env.BRANCH || env.HEAD || null,
      context: env.CONTEXT || null,
      source: 'NETLIFY',
      built_at: now.toISOString(),
    }
  }
  const sha = (git(['rev-parse', 'HEAD']) || '').toLowerCase()
  const branch = git(['rev-parse', '--abbrev-ref', 'HEAD'])
  return {
    schema: 'bt.frontend.build.v1',
    sha: HEX.test(sha) ? sha : null,
    sha_why: HEX.test(sha) ? null : 'no COMMIT_REF and git rev-parse HEAD failed',
    branch: branch || null,
    context: 'LOCAL',
    source: 'LOCAL',
    built_at: now.toISOString(),
  }
}

export function writeBuildInfo(outDir) {
  const info = buildInfo()
  const file = join(outDir, 'command', 'build.json')
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, JSON.stringify(info, null, 2) + '\n')
  return { file, info }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const out = resolve(process.argv[2] || 'dist')
  const { file, info } = writeBuildInfo(out)
  console.log('build info -> %s (%s %s)', file, info.source, info.sha ? info.sha.slice(0, 7) : 'sha unavailable')
}
