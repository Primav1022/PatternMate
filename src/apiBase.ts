export function geometryBase(): string {
  return import.meta.env.VITE_GEOMETRY_BASE_URL || '/geometry';
}

export function textBase(): string {
  return import.meta.env.VITE_TEXT_BASE_URL || import.meta.env.VITE_WORKER_BASE_URL || geometryBase();
}

export function aiBase(): string {
  return import.meta.env.VITE_AI_BASE_URL || import.meta.env.VITE_WORKER_BASE_URL || '/ai';
}
