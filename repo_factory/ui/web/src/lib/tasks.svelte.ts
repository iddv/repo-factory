// New task: the typed text (or a file's text, read in this browser) goes to api/new, and its row
// is on the list at once, saying Brief, until the next read of api/state has it.
import { post } from "../api/client";
import { STATE, type Item } from "./builds";
import { cut } from "./format";
import { load, stale } from "./reads.svelte";

interface Pending {
  n: number;
  id: string | null;
  title: string;
  at: number;
}
export const pending = $state<{ rows: Pending[] }>({ rows: [] });
let n = 0;

/** the title a task's text gives: its first line, without a heading's marks */
export const titleOf = (text: string): string => cut(text.trim().split("\n")[0]!.replace(/^#+\s*/, ""), 80);

/** the rows sent but not read back yet (for two minutes at most), as list rows */
export function pendingItems(have: Set<string>, now = Date.now() / 1000): Item[] {
  return pending.rows
    .filter((p) => !(p.id && have.has(p.id)) && now - p.at < 120)
    .map((p) => ({ key: `pending:${p.n}`, title: p.title, line: "Sent; the brief is being written", updated: p.at, b: null, task: null, word: "Brief", tone: "blue" as const }));
}

/** send a task: the text is the whole spec; returns the new task's id when the server says it */
export async function sendTask(text: string): Promise<string | null> {
  const t = text.trim();
  if (!t) throw new Error("Write what you want built, or choose a file.");
  // the row first, then the request: it is on the list the moment Build is pressed
  const p: Pending = { n: ++n, id: null, title: titleOf(t), at: Date.now() / 1000 };
  pending.rows.push(p);
  let id: string | null = null;
  try {
    const r = await post<{ id?: string | null; idea?: string | null; created?: boolean; state?: string }>("api/new", { text: t });
    id = r?.id || r?.idea || null;
    if (r?.created === false && r?.state && !["new", "queued"].includes(r.state)) {
      // the same text is already a task: say so here rather than open the old one silently
      throw new Error(`That task is already on the list (${r.state}): open it from Builds, or change the text to start a new one.`);
    }
  } catch (e) {
    pending.rows = pending.rows.filter((x) => x.n !== p.n);
    throw e;
  }
  pending.rows = pending.rows.map((x) => (x.n === p.n ? { ...x, id } : x));
  stale(STATE);
  await load(STATE);
  // read back: a row with its id is on the list now; one without an id goes with this read
  if (!id) pending.rows = pending.rows.filter((x) => x.n !== p.n);
  return id;
}

/** the text of a chosen file, read in this browser (nothing is uploaded but the text) */
export async function fileText(f: File): Promise<string> {
  if (f.size > 512 * 1024) throw new Error(`${f.name} is larger than 512 KB: a task is a page or two of text.`);
  return new Promise((ok, no) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result || ""));
    r.onerror = () => no(new Error(`${f.name} could not be read.`));
    r.readAsText(f);
  });
}
