interface ImportMetaEnv {
  readonly VITE_GEOMETRY_BASE_URL?: string;
  readonly VITE_TRYON_BASE_URL?: string;
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
