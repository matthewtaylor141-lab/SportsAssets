/// <reference types="vite/client" />

declare const __BUILD_SHA__: string

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
