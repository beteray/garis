/// <reference types="vite/client" />

/**
 * Build-time flags. Only one exists, and it exists only so screenshots can be
 * taken without a running engine — see `src/dev/scenarios.ts`.
 */
interface ImportMetaEnv {
  /** "1" enables the development-only visual fixtures. Never set for a release. */
  readonly VITE_GARIS_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
