// The page stays fresh: the live feed (GET api/stream, server-sent events) says that something
// changed, and every read on the page is asked again (at most every 2 s). A read every 15 s runs
// whatever happens, so a missed event or a feed that is down never leaves the page stale. A new
// version on the feed's host event means the server was updated: the page reloads.
import { stale } from "./reads.svelte";

export const GAP_MS = 2000;
export const SAFETY_MS = 15000;

export function startLive(url = "api/stream"): () => void {
  let es: EventSource | null = null;
  let version: string | null = null;
  let queued: ReturnType<typeof setTimeout> | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  const soon = (): void => {
    if (queued) return;
    queued = setTimeout(() => {
      queued = null;
      stale("api/");
    }, GAP_MS);
  };
  const open = (): void => {
    if (stopped || typeof EventSource === "undefined") return;
    const s = (es = new EventSource(url));
    s.addEventListener("change", soon);
    s.addEventListener("host", (e) => {
      let v: string | null = null;
      try {
        v = JSON.parse((e as MessageEvent).data).version || null;
      } catch {
        v = null;
      }
      if (version && v && v !== version) location.reload();
      if (v) version = v;
    });
    // every place on the feed is taken, or it went away: try again later (the timer reads meanwhile)
    const drop = (): void => {
      s.close();
      if (es !== s) return;
      es = null;
      retry = setTimeout(open, 30000);
    };
    s.addEventListener("busy", drop);
    s.addEventListener("error", () => {
      if (s.readyState === EventSource.CLOSED) drop();
    });
  };
  open();
  const timer = setInterval(() => stale("api/"), SAFETY_MS);
  const visible = (): void => {
    if (!document.hidden) soon();
  };
  document.addEventListener("visibilitychange", visible);
  return () => {
    stopped = true;
    es?.close();
    clearInterval(timer);
    if (queued) clearTimeout(queued);
    if (retry) clearTimeout(retry);
    document.removeEventListener("visibilitychange", visible);
  };
}
