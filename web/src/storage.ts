function storage() {
  return globalThis.localStorage && typeof globalThis.localStorage.getItem === 'function'
    ? globalThis.localStorage
    : null;
}

export function loadJson<T>(key: string, fallback: T): T {
  const currentStorage = storage();
  if (!currentStorage) return fallback;
  const raw = currentStorage.getItem(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function saveJson<T>(key: string, value: T): void {
  const currentStorage = storage();
  if (!currentStorage) return;
  currentStorage.setItem(key, JSON.stringify(value));
}
