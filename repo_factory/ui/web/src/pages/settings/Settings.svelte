<script lang="ts">
  // Settings: a list of its pages beside the one open (on a phone, the list above it). One
  // level: a page's own parts are sections on it.
  import { href, type Route } from "../../lib/router.svelte";
  import Appearance from "./Appearance.svelte";
  import Building from "./Building.svelte";
  import Claude from "./Claude.svelte";
  import GitHub from "./GitHub.svelte";
  import Publishing from "./Publishing.svelte";
  import Spend from "./Spend.svelte";
  import { PAGES } from "./settings";

  let { r }: { r: Route } = $props();
  const page = $derived(r.id || "claude");
  const known = $derived(PAGES.some(([k]) => k === page));
</script>

<div class="settings">
  <nav class="subnav" aria-label="Settings pages">
    <h1>Settings</h1>
    <ul>
      {#each PAGES as [k, word] (k)}
        <li><a href={href("settings", k)} aria-current={page === k ? "page" : undefined}>{word}</a></li>
      {/each}
    </ul>
  </nav>
  <section class="setpage" id={"set-" + page} aria-label={PAGES.find(([k]) => k === page)?.[1] || "Settings"}>
    {#if page === "claude"}<Claude />
    {:else if page === "github"}<GitHub />
    {:else if page === "building"}<Building />
    {:else if page === "publishing"}<Publishing />
    {:else if page === "spend"}<Spend />
    {:else if page === "appearance"}<Appearance />
    {/if}
    {#if !known}<p class="muted">There is no such settings page. <a href={href("settings")}>Settings</a>.</p>{/if}
  </section>
</div>
