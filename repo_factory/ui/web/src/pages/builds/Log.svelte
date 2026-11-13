<script lang="ts">
  // One stage's log: the build's events (api/events) of this Build, under the engine stages that
  // light the chosen one of the six. Model calls are not in it; the full text is the build's own.
  import { q } from "../../api/client";
  import { clock } from "../../lib/format";
  import { read } from "../../lib/reads.svelte";
  import { SIX_NAME, SIX_OF, type Six } from "../../lib/stages";

  let { dir, n, cell }: { dir: string; n: number; cell: string } = $props();

  interface Ev {
    ts: number;
    type: string;
    build?: number;
    stage?: string;
    status?: string;
    [k: string]: unknown;
  }
  const got = $derived(read<{ events: Ev[] }>(q("api/events", { dir, since: 0 }), 10000));
  const SKIP = new Set(["ts", "type", "build", "task", "v"]);
  function words(e: Ev): string {
    const parts = [e.type];
    for (const [k, v] of Object.entries(e)) {
      if (SKIP.has(k) || v == null || v === "") continue;
      let s = typeof v === "object" ? JSON.stringify(v) : String(v);
      if (s.length > 240) s = s.slice(0, 239) + "…";
      parts.push(`${k}=${s}`);
    }
    return parts.join(" ");
  }
  // each event belongs to the stage that started last before it ("brief" before any)
  const lines = $derived.by(() => {
    let at = "brief";
    const out: { ts: number; t: string; bad: boolean }[] = [];
    for (const e of got.data?.events || []) {
      if (e.build != null && e.build !== n) continue;
      if (e.stage && (e.type.endsWith("_stage") || e.status === "started")) at = SIX_OF[e.stage] || at;
      if (at !== cell) continue;
      const t = words(e);
      out.push({ ts: e.ts, t, bad: /FAIL|status=failed|status=error|interrupted/.test(t) });
    }
    return out;
  });
  const name = $derived(SIX_NAME[cell as Six] || cell);
</script>

<section class="log" id="log" aria-label={`The log of ${name}`}>
  <h4>The log of {name}</h4>
  {#if got.error && !got.data}
    <p class="err" role="alert">{got.error}</p>
  {:else if !got.data}
    <p class="muted">Reading the log…</p>
  {:else if !lines.length}
    <p class="muted">{name} wrote nothing to the log in this build.</p>
  {:else}
    <div class="lines">{#each lines as l, i (i)}<div class={"ln" + (l.bad ? " bad" : "")}><span class="ts">{clock(l.ts)}</span><span>{l.t}</span></div>{/each}</div>
  {/if}
</section>
