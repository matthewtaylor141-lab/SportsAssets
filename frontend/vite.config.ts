import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Build identity (owner report 2026-08-07: "no changes on the site" —
// settle is-it-deployed questions with evidence, not vibes). Netlify
// exposes the commit as COMMIT_REF; local builds stamp "dev".
const sha = (process.env.COMMIT_REF || 'dev').slice(0, 7)

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  define: { __BUILD_SHA__: JSON.stringify(`bsha_${sha}`) },
})
