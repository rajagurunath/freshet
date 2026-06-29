/**
 * Local persistence for user entity-review decisions.
 *
 * The NER extractor is imperfect, so we let the user curate: entities they mark
 * as "not an entity" are stored here (by `kind:name` key) and filtered out of the
 * graph. This list is the seed of the human-in-the-loop feedback loop — a later
 * iteration can POST it to the hub so future extraction skips these too.
 */

const KEY = "ctxhub.rejectedEntities";

export function entityKey(kind: string, name: string): string {
  return `${kind}:${name}`.toLowerCase();
}

export function loadRejected(): Set<string> {
  try {
    const raw = localStorage.getItem(KEY);
    return new Set<string>(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set<string>();
  }
}

export function saveRejected(set: Set<string>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify([...set]));
  } catch {
    /* ignore quota / unavailable storage */
  }
}
