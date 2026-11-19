/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DATASET_API_URL?: string;
  readonly VITE_AGENT_API_URL?: string;
  readonly VITE_CHUNKER_URL?: string;
  readonly VITE_AUTH_MODE?: string;
  readonly VITE_DEFAULT_LOCALE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
