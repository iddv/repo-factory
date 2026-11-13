// The page's addresses are hash routes, read as data:
//   #/builds             the list (also #/ and an empty hash)
//   #/builds/new         New task
//   #/builds/<key>       a build beside the list; ?log=<stage> opens that stage's log
//   #/settings/<page>    a settings page (#/settings opens the first)
// Anything else is a page that does not exist, said so in words.

export type Section = "builds" | "settings" | "missing";
export interface Route {
  section: Section;
  /** builds: a build's key, or "new"; settings: the page */
  id: string | null;
  q: Record<string, string>;
}

export function parse(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  const [path = "", query = ""] = raw.split("?", 2);
  const parts = path.split("/").filter(Boolean).map((s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  });
  const q: Record<string, string> = {};
  for (const [k, v] of new URLSearchParams(query)) q[k] = v;
  const [head, id = null, ...rest] = parts;
  if (rest.length) return { section: "missing", id: null, q };
  if (!head || head === "builds") return { section: "builds", id, q };
  if (head === "settings") return { section: "settings", id, q };
  return { section: "missing", id: null, q };
}

/** an address: href("builds", key) is "#/builds/<key>" */
export function href(section: Exclude<Section, "missing">, id?: string | null, query?: Record<string, string | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query || {})) if (v) p.set(k, v);
  const s = p.toString();
  return `#/${section}${id ? "/" + encodeURIComponent(id) : ""}${s ? "?" + s : ""}`;
}

/** where the page is, kept in step with the address */
export const here = $state<{ r: Route }>({ r: parse(typeof location === "undefined" ? "" : location.hash) });

export function follow(): () => void {
  const on = (): void => {
    here.r = parse(location.hash);
  };
  on();
  addEventListener("hashchange", on);
  return () => removeEventListener("hashchange", on);
}

export function go(to: string): void {
  if (location.hash === to) return;
  location.hash = to;
  here.r = parse(to);
}
