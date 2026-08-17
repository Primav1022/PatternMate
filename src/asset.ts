/** Prefix static files for GitHub Pages (`VITE_ASSET_BASE`) or Vite `base`. */
export function asset(path: string): string {
  if (!path) return path;
  if (/^(https?:|data:|blob:)/i.test(path)) return path;
  const trimmed = path.replace(/^\//, '');
  const remote = String(import.meta.env.VITE_ASSET_BASE || '').replace(/\/$/, '');
  if (remote) return `${remote}/${trimmed}`;
  return `${import.meta.env.BASE_URL || '/'}${trimmed}`;
}

export function coverFallbacks(url: string): string[] {
  if (!url) return [];
  const stem = url.replace(/\/(thumb\.jpg|cover\.(png|jpe?g|webp))$/i, '');
  const ordered = [`${stem}/thumb.jpg`, `${stem}/cover.png`, `${stem}/cover.jpg`];
  return [url, ...ordered.filter((item) => item !== url)];
}

export function thumbUrl(coverUrl: string): string {
  return coverUrl.replace(/\/cover\.(png|jpe?g|webp)$/i, '/thumb.jpg');
}
