<script lang="ts">
  // One row per build: its title, its status in words, the line that says why, and its age.
  import type { Item } from "../../lib/builds";
  import { ago } from "../../lib/format";
  import { href } from "../../lib/router.svelte";

  let { rows, sel }: { rows: Item[]; sel: string | null } = $props();
</script>

<ul class="rows" id="build-rows">
  {#each rows as x (x.key)}
    <li class={"row" + (x.key === sel ? " sel" : "")} data-key={x.key}>
      <a href={href("builds", x.key)} aria-current={x.key === sel ? "page" : undefined}>
        <span class="t">{x.title}</span>
        <span class={"pill t-" + x.tone} data-status>{x.word}</span>
        {#if x.line}<span class="line muted">{x.line}</span>{/if}
        <span class="age muted">{ago(x.updated)}</span>
      </a>
    </li>
  {/each}
</ul>
