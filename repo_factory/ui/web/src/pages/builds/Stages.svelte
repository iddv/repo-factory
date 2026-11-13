<script lang="ts">
  // The six stages, in order: each with what it does, its state, its time and cost, and Open
  // the log once it has run.
  import { dur, usd } from "../../lib/format";
  import { CELL_WORD, SIX_DOES, SIX_NAME, type Cell } from "../../lib/stages";

  let { cells, logHref, open = null }: { cells: Cell[]; logHref: (k: string) => string; open?: string | null } = $props();
</script>

<ol class="stages" id="stages">
  {#each cells as c (c.k)}
    <li class={"stage s-" + c.st + (open === c.k ? " open" : "")} data-stage={c.k}>
      <div class="name"><strong>{SIX_NAME[c.k]}</strong><span class="state">{CELL_WORD[c.st]}</span></div>
      <div class="does muted">{SIX_DOES[c.k]}{#if c.why}<span class="why"> · {c.why}</span>{/if}</div>
      <div class="facts">
        <span title="Time">{c.st === "running" ? "running" : dur(c.wall_s)}</span>
        <span title="Cost">{usd(c.usd)}</span>
        {#if c.st !== "not_run"}<a href={logHref(c.k)} aria-current={open === c.k ? "true" : undefined}>Open the log</a>{:else}<span class="muted">no log yet</span>{/if}
      </div>
    </li>
  {/each}
</ol>
