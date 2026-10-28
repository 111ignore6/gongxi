// Pure helpers for incremental rendering decisions (no DOM access).
export function planPatch(existingIds, nextIds) {
  const existing = new Set(existingIds);
  const next = new Set(nextIds);
  const append = nextIds.filter(id => !existing.has(id));
  const remove = existingIds.filter(id => !next.has(id));
  const keptNext = nextIds.filter(id => existing.has(id));
  const keptExisting = existingIds.filter(id => next.has(id));
  const reorder = keptNext.filter((id, index) => keptExisting[index] !== id);
  return {append, remove, reorder};
}
