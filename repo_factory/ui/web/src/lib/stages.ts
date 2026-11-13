// A build's six stages, in the names a person reads: Brief, Plan, Build, Check, Fix, Publish.
// The engine's own stages (v5's scope, build, harden, claims, verify, ...) each light one of them;
// a stage of several shows what matters most: what runs, then what went wrong, then what was done.
import type { RunStage } from "../api/types.gen";

export const SIX = ["brief", "plan", "build", "check", "fix", "publish"] as const;
export type Six = (typeof SIX)[number];
export const SIX_NAME: Record<Six, string> = { brief: "Brief", plan: "Plan", build: "Build", check: "Check", fix: "Fix", publish: "Publish" };
export const SIX_DOES: Record<Six, string> = {
  brief: "The task read and written up as a brief",
  plan: "What to build, and how to know it works",
  build: "The code, its tests and its docs, written in a sandbox",
  check: "Tested as a user would, and reviewed for security",
  fix: "What the checks found, fixed and tested again",
  publish: "Packaged; it waits for your press before anything goes to GitHub",
};

/** each engine stage, by the one of the six it lights */
export const SIX_OF: Readonly<Record<string, Six>> = {
  pitch: "brief", brief: "brief", interpret: "brief",
  scope: "plan", plan: "plan",
  build: "build", harden: "build",
  claims: "check", verify: "check", oracle: "check", readiness: "check", check: "check",
  repair: "fix", reverify: "fix", fix: "fix",
  deliver: "publish", publish: "publish", gate: "publish",
};

/** the six-stage name of an engine stage ("verify" is "Check"); an unknown one as it is */
export const sixName = (stage: string | null | undefined): string => {
  if (!stage) return "";
  const k = SIX_OF[stage];
  return k ? SIX_NAME[k] : stage.charAt(0).toUpperCase() + stage.slice(1);
};

export type CellState = "done" | "running" | "failed" | "stopped" | "waiting" | "held" | "not_run";
export const CELL_WORD: Record<CellState, string> = {
  done: "done", running: "running", failed: "failed", stopped: "stopped", waiting: "waiting for you", held: "held", not_run: "not yet",
};

export interface Cell {
  k: Six;
  st: CellState;
  wall_s: number | null;
  usd: number | null;
  started: number | null;
  /** the engine stages under it, in order (their log lines are this cell's) */
  stages: string[];
  why: string | null;
}

const FIRST: CellState[] = ["running", "failed", "stopped", "held", "waiting", "done", "not_run"];
function stateOf(s: RunStage): CellState {
  if (s.reason === "carried" || s.reason === "not_needed") return "done";
  switch (s.status) {
    case "done":
    case "passed":
    case "ok":
      return "done";
    case "running":
      return "running";
    case "failed":
    case "error":
      return "failed";
    case "stopped":
      return "stopped";
    case "capped":
    case "held":
      return "held";
    default:
      return "not_run";
  }
}
const add = (a: number | null, b: number | null): number | null => (a == null ? b : b == null ? a : a + b);

/** the build's place before and after its engine stages, which the stages alone do not say */
export interface Around {
  /** a build exists: the brief is written (false: the task is still being read) */
  briefed: boolean;
  /** the brief is being written now */
  briefing?: boolean;
  /** "waiting": ready, waiting for the press; "done": published; "held": the checks held it */
  publish?: "waiting" | "done" | "held" | null;
}

/** one Build's six cells, from its stages (api/run) and where it stands */
export function cells(stages: RunStage[], around: Around): Cell[] {
  const by = new Map<Six, RunStage[]>();
  for (const s of stages) {
    const k = SIX_OF[s.stage];
    if (k) by.set(k, [...(by.get(k) || []), s]);
  }
  return SIX.map((k) => {
    const xs = by.get(k) || [];
    const sts = xs.map(stateOf);
    let st: CellState = FIRST.find((x) => sts.includes(x)) || "not_run";
    if (k === "brief" && !xs.length) st = around.briefing ? "running" : around.briefed ? "done" : "not_run";
    if (k === "publish" && around.publish && st !== "running" && st !== "failed") st = around.publish;
    return {
      k,
      st,
      wall_s: xs.reduce<number | null>((a, s) => add(a, s.status === "running" ? null : s.wall_s), null),
      usd: xs.reduce<number | null>((a, s) => add(a, s.usd), null),
      started: xs.map((s) => s.started).find((x) => x != null) ?? null,
      stages: xs.map((s) => s.stage),
      why: xs.find((s) => s.why && (s.status === "failed" || s.status === "error"))?.why ?? null,
    };
  });
}
