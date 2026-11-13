// Small words for times and money.

/** "under 1 min", "12 min", "1 h 5 min"; "–" without a duration */
export function dur(s: number | null | undefined): string {
  if (!s) return "–";
  const m = Math.round(s / 60);
  if (m < 1) return "under 1 min";
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

/** money with two places: "$1.20"; none is "–" */
export const usd = (v: number | null | undefined): string => (v == null ? "–" : "$" + Number(v).toFixed(2));

/** a cap: "$25.00", or "none" */
export const capWord = (v: number | null | undefined): string => (v == null ? "none" : usd(v));

/** a row's age from a time in seconds: "now", "12 min", "3 h", "4 d" */
export function ago(ts: number | null | undefined, now: number = Date.now()): string {
  if (!ts) return "";
  const m = Math.round((now / 1000 - ts) / 60);
  return m < 1 ? "now" : m < 60 ? `${m} min` : m < 1440 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} d`;
}

/** a clock time from seconds: "14:05" */
export const clock = (ts: number): string => new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

/** the first line of a text, cut to n characters */
export function cut(s: string | null | undefined, n: number): string {
  const t = String(s || "").split("\n")[0] || "";
  return t.length > n ? t.slice(0, n - 1).trimEnd() + "…" : t;
}
