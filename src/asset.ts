/** Prefix static files for GitHub Pages (`VITE_ASSET_BASE`) or Vite `base`. */
export function asset(path: string): string {
  if (!path) return path;
  if (/^(https?:|data:|blob:)/i.test(path)) return path;
  const trimmed = path.replace(/^\//, '');
  const remote = String(import.meta.env.VITE_ASSET_BASE || '').replace(/\/$/, '');
  if (remote) return `${remote}/${trimmed}`;
  return `${import.meta.env.BASE_URL || '/'}${trimmed}`;
}
