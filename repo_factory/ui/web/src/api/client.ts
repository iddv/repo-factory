// The page's requests. Reads are GETs of relative paths (the page is served at "/"); writes are
// POSTs of JSON to the page's own server, and every press on a build is a registry action
// through api/act (the kinds are typed from the Python side in actions.gen.ts).
import type { ActionKind } from "./actions.gen";
import { CONFIRM, SUBJECTS } from "./actions.gen";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function answer<T>(r: Response): Promise<T> {
  const text = await r.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!r.ok) {
    const error = (body as { error?: unknown } | null)?.error;
    // never a JSON body as the words: the server's sentence, else the status
    const words = typeof error === "string" ? error : typeof body === "string" && body.trim() && !/^\s*[[{<]/.test(body) ? body.trim() : `HTTP ${r.status}`;
    throw new ApiError(r.status, words);
  }
  return body as T;
}

/** a path with its query: q("api/run", {dir, n}) */
export function q(path: string, query?: Record<string, string | number | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query || {})) if (v != null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `${path}?${s}` : path;
}

export async function getJson<T>(path: string): Promise<T> {
  return answer<T>(await fetch(path, { cache: "no-store" }));
}

export async function post<T = Record<string, unknown>>(path: string, body: unknown): Promise<T> {
  return answer<T>(
    await fetch(path, {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export interface Subject {
  kind: "new" | "idea" | "build" | "loop";
  id: string | null;
}

const MUST_CONFIRM = new Set<string>(CONFIRM);
const KNOWN = new Set<string>(Object.keys(SUBJECTS));

/** one registry action, pressed on the page; a kind that needs a confirm is sent with one (the
 * page asked first). `kind` is a string: a kind the server adds later is passed through, with a
 * confirm, since the page asks before every press it does not know. */
export function act(kind: ActionKind | (string & {}), subject: Subject, fields: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
  const confirm = MUST_CONFIRM.has(kind) || !KNOWN.has(kind);
  return post("api/act", { kind, subject, fields, origin: "ui", ...(confirm ? { confirm: true } : {}) });
}
