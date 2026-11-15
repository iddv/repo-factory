// Maintenance's reads and presses: api/maintenance (every published repository, from the
// poll's cache and repo-factory's own files: never a GitHub call) and its switches. Every press
// answers with the whole picture again, which replaces what the page shows.
import { post } from "../../api/client";
import { put, stale } from "../../lib/reads.svelte";

export const MAINT = "api/maintenance";

export interface Row {
  kind: "waiting" | "setup" | "policy" | "self_hosted" | "fixing" | "fix_pr" | "needs_you" | string;
  number: number | null;
  title: string;
  why: string;
  url: string | null;
  author?: string | null;
  updated?: number | null;
}
export interface Fix {
  issue: number;
  outcome?: string;
  stage?: string | null;
  why?: string | null;
  pr?: number | null;
  url?: string | null;
  usd?: number;
  attempts?: number;
  at?: number;
}
export interface Proposal {
  id: string;
  title: string;
  why: string;
  kind: string;
  state: string;
  issue?: number | null;
  url?: string | null;
}
export interface Repo {
  repo: string;
  idea: string;
  product: string;
  one_liner: string;
  switch: { on: boolean; merge: boolean; at?: number };
  paused: string | null;
  polled: number | null;
  error: string | null;
  rows: Row[];
  counts: { waiting?: number; go_yours?: number; issues?: number; prs?: number };
  queue: { why?: string; items?: { number: number; verdict: string; why: string }[] } | null;
  release: { tag: string; url?: string } | null;
  fixes: Fix[];
  setup: { state: string; why?: string | null; pr?: number | null; url?: string | null; nothing_to_add?: boolean } | null;
  proposals: { at: number | null; state: string | null; error: string | null; items: Proposal[] };
  // why no fix starts while sessions would sign in with Claude Code's own sign-in file; null: none
  signin?: string | null;
}
export interface Spend {
  month: number;
  today: number;
  limit: number | null;
  per_fix: number | null;
  paused: string | null;
}
export interface Maintenance {
  repos: Repo[];
  spend: Spend;
  paused: boolean;
  last_poll: number | null;
  settings: Record<string, unknown>;
  // the fix sessions' allow-list, set in loop.yaml by hand (shown, never written here)
  net_hosts?: string[];
}
export interface Project extends Repo {
  brief: { one_liner: string; who: string; brief: string };
  build: string | null;
  published: string | null;
  state: string;
  month_usd: number;
}

async function press(path: string, body: Record<string, unknown>): Promise<Maintenance> {
  const m = await post<Maintenance>(path, body);
  put(MAINT, m);
  stale("api/project");
  return m;
}

export const switchOn = (repo: string, on: boolean) => press("api/maintenance/switch", { repo, on });
export const mergeOn = (repo: string, on: boolean) => press("api/maintenance/merge", { repo, on });
export const pause = (repo: string, paused: boolean) => press("api/maintenance/pause", { repo, paused });
export const retry = (repo: string, number: number) => press("api/maintenance/retry", { repo, number });
export const buildProposal = (repo: string, id: string) => press("api/maintenance/proposal", { repo, id });

/** one line: where maintenance stands on this repository */
export function standing(r: Repo): string {
  if (!r.switch.on) return "Maintenance off";
  if (r.paused) return r.paused;
  if (r.setup?.state === "running") return "On: opening the setup pull request…";
  if (r.setup?.state === "failed") return `On, but the setup stopped: ${r.setup.why || "see the log"}`;
  if (r.rows.some((x) => x.kind === "setup")) return "On; the setup pull request waits for your review";
  return r.switch.merge ? "On; merges its own bug and docs fixes when CI is green" : "On; you merge every fix";
}

export const KIND_WORD: Record<string, string> = {
  waiting: "Waiting for you",
  setup: "Setup",
  policy: "Policy file",
  self_hosted: "Warning",
  fixing: "Fixing now",
  fix_pr: "Fix to review",
  needs_you: "Needs you",
};

/** dollars, two places */
export const usd = (n: number | null | undefined): string => (n == null ? "none" : `$${n.toFixed(2)}`);
