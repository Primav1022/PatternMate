type GpuBases = { geometry?: string; text?: string; ai?: string; tryon?: string };

let live: GpuBases = {};

const gpuJson = String(import.meta.env.VITE_ASSET_BASE || '').replace(/\/$/, '');
if (gpuJson) {
  fetch(`${gpuJson}/gpu.json`, { cache: 'no-store' })
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => { if (data && typeof data === 'object') live = data; })
    .catch(() => {});
}

export function geometryBase(): string {
  return live.geometry || import.meta.env.VITE_GEOMETRY_BASE_URL || '/geometry';
}

export function textBase(): string {
  return live.text || import.meta.env.VITE_TEXT_BASE_URL || import.meta.env.VITE_WORKER_BASE_URL || geometryBase();
}

export function aiBase(): string {
  return live.ai || import.meta.env.VITE_AI_BASE_URL || import.meta.env.VITE_WORKER_BASE_URL || '/ai';
}

export function tryonBase(): string {
  return live.tryon || import.meta.env.VITE_TRYON_BASE_URL || '/tryon';
}
