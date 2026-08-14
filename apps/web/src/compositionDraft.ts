export type SelectionMap = Record<string, string | null>;

export function hasDraftPatternChanges(draft: SelectionMap, submitted: SelectionMap): boolean {
  const keys = new Set([...Object.keys(draft), ...Object.keys(submitted)]);
  for (const key of keys) {
    if ((draft[key] ?? null) !== (submitted[key] ?? null)) return true;
  }
  return false;
}

export function submitPatternDraft(draft: SelectionMap): SelectionMap {
  return { ...draft };
}
