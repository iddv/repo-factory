// The builds as the page shows them: api/state's builds and the tasks not built yet, one row each,
// with the status in plain words. Pure functions; the components draw what these return.
import type { ActionOffer, BuildRow, Gate, Run } from "../api/types.gen";
import { cut } from "./format";
import { sixName } from "./stages";

/** the page's one read of everything the list shows */
export const STATE = "api/state";

/** a task in the ledger (api/state's ideas): typed on the page, or `loop add` */
export interface TaskRow {
  id: string;
  state: string | null;
  title: string;
  text?: string;
  reason?: string | null;
  build: string | null;
  created: number | null;
  updated: number | null;
}

export interface GateCheck {
  ok: boolean;
  detail?: string | null;
}
export interface GateX {
  verdict: string | null;
  reasons: unknown[];
  round: number | null;
  checks?: Record<string, GateCheck>;
  gaps?: string[];
}
export type Build = BuildRow & {
  gate: (Gate & GateX) | null;
  actions?: ActionOffer[];
  reason?: string | null;
  question?: { id: string; text?: string | null } | null;
  stopped?: { round?: number | null; resumable?: boolean } | null;
  control?: { what?: string } | null;
  precheck?: { state?: string; holds?: [string, string][] } | null;
};

export interface State {
  builds: Build[];
  ideas?: TaskRow[];
  harness?: string;
}

export type Tone = "blue" | "amber" | "green" | "red" | "grey";
export interface Said {
  word: string;
  tone: Tone;
}

/** a row of the list: a build, or a task whose brief is not written yet */
export interface Item extends Said {
  key: string;
  title: string;
  line: string;
  updated: number | null;
  b: Build | null;
  task: TaskRow | null;
}

const latest = (b: Build): Run | null => (b.builds as { latest?: Run } | null | undefined)?.latest || null;

/** published: the build has a repository (privately or in public) */
export const published = (b: Build): boolean => !!b.url || b.status === "public" || b.status === "kept";

/** a build's status, in words: what it is doing, or what it waits for */
export function buildSaid(b: Build): Said {
  const r = latest(b);
  if (b.question) return { word: "Asks you", tone: "amber" };
  if (b.control?.what) return { word: "Stopping", tone: "grey" };
  switch (b.status) {
    case "running":
      return { word: sixName(r?.stage || (b.pipeline as { stage: string; status: string }[] | undefined)?.find((s) => s.status === "running")?.stage) || "Building", tone: "blue" };
    case "queued":
      return { word: "Waiting for a slot", tone: "grey" };
    case "paused":
      return { word: "Paused", tone: "grey" };
    case "ready":
    case "awaiting_publish":
      return published(b) ? { word: "Published privately", tone: "green" } : { word: "Ready: publish?", tone: "amber" };
    case "publishing":
      return { word: "Publishing", tone: "blue" };
    case "kept":
      return { word: "Published privately", tone: "green" };
    case "public":
      return { word: "Public", tone: "green" };
    case "closed":
      return { word: "Closed", tone: "grey" };
    case "failed":
      return { word: "Failed", tone: "red" };
  }
  if (r?.outcome === "stopped") return { word: "Stopped", tone: "red" };
  if (r?.outcome === "failed") return { word: "Failed", tone: "red" };
  if (b.gate && b.gate.verdict !== "ready") return { word: "Held by the checks", tone: "red" };
  return { word: "Needs you", tone: "amber" };
}

/** a task not built yet, in words: its brief is being written, or what came of it */
export function taskSaid(t: TaskRow): Said {
  switch (t.state) {
    case "new":
    case "nominated":
    case null:
      return { word: "Brief", tone: "blue" };
    case "queued":
      return { word: "Waiting for a slot", tone: "grey" };
    case "rejected":
    case "skipped":
      return { word: "Brief: not built", tone: "grey" };
    case "error":
      return { word: "Brief failed", tone: "red" };
    case "closed":
      return { word: "Closed", tone: "grey" };
    default:
      return { word: "Brief", tone: "blue" };
  }
}

/** every row, newest first: the builds, and the tasks that have no build yet */
export function items(s: State | null): Item[] {
  if (!s) return [];
  const built = new Set(s.builds.map((b) => b.idea).filter(Boolean));
  const out: Item[] = s.builds.map((b) => ({ key: b.key, title: b.title, line: cut(b.reason || b.desc || "", 90), updated: b.updated ?? null, b, task: null, ...buildSaid(b) }));
  for (const t of s.ideas || []) {
    if (built.has(t.id) || t.build || !t.id || t.id.startsWith("card:")) continue;
    out.push({ key: "task:" + t.id, title: t.title || cut(t.text, 60), line: cut(t.reason || "", 90), updated: t.updated ?? t.created ?? null, b: null, task: t, ...taskSaid(t) });
  }
  return out.sort((a, b) => (b.updated || 0) - (a.updated || 0));
}

/** the registry's offer of kind on this build, when it is on */
export const offer = (b: Build, kind: string): ActionOffer | null => (b.actions || []).find((x) => x.kind === kind && x.enabled) || null;

/** a gate reason ("tests: 4 of 58 tests fail") without its check's key */
export function plainReason(s: unknown): string {
  const t = String(s || "").replace(/^(tests|security|completeness|documents|safe_defaults|p_hidden|delivered): /, "").replace(/^secrets scan: /, "a secret in ");
  return cut(t, 160);
}

/** each release check in plain words */
export const CHECK_WORD: Record<string, string> = {
  tests: "Its own tests pass",
  security: "The security review found nothing open",
  completeness: "It does what the brief asked",
  documents: "It has a README and its docs",
  safe_defaults: "It starts with safe defaults",
  delivered: "It delivered a working tree",
  p_hidden: "Checks it did not see pass",
};
export const checkWord = (k: string): string => CHECK_WORD[k] || k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
