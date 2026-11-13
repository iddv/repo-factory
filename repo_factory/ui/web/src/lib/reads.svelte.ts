// What the page has read, by path: reactive, read again once older than the caller asks (maxAge)
// or when a change says so (stale). A read in flight is never asked twice; an answer that fails
// keeps what was read before, with the error beside it.
import { SvelteMap } from "svelte/reactivity";
import { getJson } from "../api/client";

export interface Read<T> {
  data: T | null;
  error: string | null;
  at: number;
  loading: boolean;
}
const READS = new SvelteMap<string, Read<unknown>>();
const EMPTY: Read<never> = { data: null, error: null, at: 0, loading: true };
let fetcher: (path: string) => Promise<unknown> = getJson;

/** tests read through their own fetcher */
export function setFetcher(f: (path: string) => Promise<unknown>): void {
  fetcher = f;
}

/** the cached answer for path, asking again when it is older than maxAge (or never read) */
export function read<T>(path: string, maxAge = 30000): Read<T> {
  const c = READS.get(path) as Read<T> | undefined;
  if (!c || (!c.loading && Date.now() - c.at > maxAge)) queueMicrotask(() => void load(path).catch(() => undefined));
  return c || EMPTY;
}

const FLYING = new Set<string>();

export async function load(path: string): Promise<void> {
  if (FLYING.has(path)) return;
  FLYING.add(path);
  const c = READS.get(path);
  READS.set(path, { ...(c || { data: null, error: null, at: 0 }), loading: true });
  try {
    READS.set(path, { data: await fetcher(path), error: null, at: Date.now(), loading: false });
  } catch (e) {
    READS.set(path, { data: c?.data ?? null, error: (e as Error).message, at: Date.now(), loading: false });
  } finally {
    FLYING.delete(path);
  }
}

/** a write's answer replaces what the page shows */
export function put<T>(path: string, data: T): void {
  READS.set(path, { data, error: null, at: Date.now(), loading: false });
}

/** every read under prefix is asked again on its next use, showing what it had meanwhile */
export function stale(prefix = ""): void {
  for (const [k, c] of [...READS.entries()]) if (k.startsWith(prefix) && !c.loading) READS.set(k, { ...c, at: 0 });
}

/** forget what is cached (tests) */
export function clearReads(): void {
  READS.clear();
  FLYING.clear();
}
