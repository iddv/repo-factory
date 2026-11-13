// Settings' reads and writes: the loop's settings (api/loop/rules, written one key at a time
// through api/loop/settings, which checks each value), the models, the spend caps.
import { post } from "../../api/client";
import { put, stale } from "../../lib/reads.svelte";

export const PAGES: [string, string, string][] = [
  ["claude", "Claude", "What builds sign in to Claude with"],
  ["github", "GitHub", "The account a build publishes under"],
  ["building", "Building", "The flow and the models"],
  ["publishing", "Publishing", "When it publishes, and where"],
  ["spend", "Spend", "The caps on a build and a day"],
  ["appearance", "Appearance", "Light or dark"],
];

export const RULES = "api/loop/rules";
export interface Rule {
  value: unknown;
  default: unknown;
  confirm?: string;
}
export interface Rules {
  rules: Record<string, Rule>;
}
export const ruleValue = (r: Rules | null, key: string): unknown => {
  const x = r?.rules?.[key];
  return x ? (x.value ?? x.default) : undefined;
};

/** one setting written; the answer is the rules as they are now */
export async function setRule(key: string, value: unknown): Promise<void> {
  const r = await post<Rules>("api/loop/settings", { key, value, confirm: true, origin: "ui" });
  if (r && r.rules) put(RULES, r);
  else stale(RULES);
}

export const MODELS = "api/models";
export interface Role {
  role: string;
  name: string;
  does: string;
  kind: string;
  model: string | null;
  default: string | null;
  set: boolean;
}
export interface Models {
  catalog: { id: string; name: string; note?: string }[];
  roles: Role[];
}
export async function setModel(role: string, model: string | null): Promise<void> {
  put(MODELS, await post<Models>("api/models", { role, model }));
}

export const CAPS = "api/spend/caps";
export interface Caps {
  daily_usd: number | null;
  build_max_usd: number | null;
  error?: string;
}
/** a cap as typed: a number of dollars, or "none" (or nothing) for no cap */
export function parseCap(s: string): number | null {
  const t = s.trim().replace(/^\$/, "").toLowerCase();
  if (!t || t === "none") return null;
  const v = Number(t);
  if (!Number.isFinite(v) || v <= 0) throw new Error(`"${s}" is not a number of dollars (or "none").`);
  return v;
}
export async function setCaps(daily: number | null, build: number | null): Promise<void> {
  await post("api/spend", { daily_usd: daily, build_max_usd: build });
  stale(CAPS);
}
