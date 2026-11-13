<script lang="ts">
  // The shell: a side nav (Builds, Settings), the notice once, and the page the address names.
  // On a phone the nav is a bar across the top. Light and dark come from Appearance.
  import { onMount } from "svelte";
  import Nav from "./shell/Nav.svelte";
  import Notice from "./shell/Notice.svelte";
  import Builds from "./pages/builds/Builds.svelte";
  import Settings from "./pages/settings/Settings.svelte";
  import { startLive } from "./lib/live";
  import { PRODUCT } from "./lib/product";
  import { follow, here } from "./lib/router.svelte";

  let { live = true }: { live?: boolean } = $props();

  onMount(() => {
    const unfollow = follow();
    const stop = live ? startLive() : () => undefined;
    document.documentElement.dataset.ui = "ready";
    return () => {
      unfollow();
      stop();
    };
  });
  const r = $derived(here.r);
  $effect(() => {
    const name = r.section === "builds" ? "Builds" : r.section === "settings" ? "Settings" : "Not found";
    document.title = `${name} · ${PRODUCT}`;
  });
  function skip(e: MouseEvent): void {
    e.preventDefault();
    document.getElementById("main")?.focus();
  }
</script>

<a class="skip" href="#main" onclick={skip}>Skip to the page</a>
<div class="shell">
  <Nav section={r.section} />
  <main id="main" tabindex="-1">
    <Notice />
    {#if r.section === "builds"}
      <Builds {r} />
    {:else if r.section === "settings"}
      <Settings {r} />
    {:else}
      <section class="page missing">
        <h1>No such page</h1>
        <p class="muted">This address is not a page of {PRODUCT}. <a href="#/builds">Go to Builds</a>.</p>
      </section>
    {/if}
  </main>
</div>
