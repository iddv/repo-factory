<script lang="ts">
  // The side nav: the product's name, Builds and Settings, and Help. One level only: a page's own
  // parts are on the page, never tabs inside tabs.
  import { PRODUCT } from "../lib/product";
  import type { Section } from "../lib/router.svelte";

  let { section }: { section: Section } = $props();
  const ITEMS: [Section, string, string][] = [
    ["builds", "Builds", "#/builds"],
    ["settings", "Settings", "#/settings"],
  ];
</script>

<nav class="nav" aria-label="Sections">
  <a class="brand" href="#/builds"><img src="static/brand/favicon-32.png" alt="" width="20" height="20" />{PRODUCT}</a>
  <ul>
    {#each ITEMS as [k, word, to] (k)}
      <li><a class="item" href={to} aria-current={section === k ? "page" : undefined}>{word}</a></li>
    {/each}
  </ul>
  <a class="help" href="help" target="_blank" rel="noopener">Help</a>
</nav>
