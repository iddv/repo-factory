<script lang="ts">
  // Projects: one row a published product, with where its maintenance stands; a product's page
  // beside nothing else (#/projects/<build>). Maintenance is off until you switch it on for one.
  import type { Route } from "../../lib/router.svelte";
  import { read } from "../../lib/reads.svelte";
  import Project from "./Project.svelte";
  import { MAINT, standing, usd, type Maintenance } from "./maintenance";

  let { r }: { r: Route } = $props();
  const got = $derived(read<Maintenance>(MAINT, 15000));
  const m = $derived(got.data);
</script>

{#if r.id}
  <Project id={r.id} />
{:else}
  <section class="page projects" aria-labelledby="projects-h">
    <h1 id="projects-h">Projects</h1>
    <p class="muted lead">Each product you published. Maintenance is off until you switch it on for one: it then fixes the issues you label <code>go</code> yourself, each as a pull request, and lists everything else here for you. It writes nothing on anyone's issue.</p>
    {#if m?.spend?.paused}<p class="err" role="alert" id="maint-spend-paused">{m.spend.paused}</p>{/if}
    {#if m?.paused}<p class="muted" id="maint-loop-paused">The loop is paused: no poll runs until you resume it.</p>{/if}
    {#if got.error && !m}
      <p class="err" role="alert">The projects could not be read: {got.error}</p>
    {:else if !m}
      <p class="muted">Reading the projects…</p>
    {:else if !m.repos.length}
      <div class="empty" id="projects-empty">
        <p>No published products yet. A build becomes a project when you publish it.</p>
        <a class="btn" href="#/builds">Go to Builds</a>
      </div>
    {:else}
      <ul class="rows" id="projects">
        {#each m.repos as p (p.repo)}
          <li class="row project">
            <a href={"#/projects/" + encodeURIComponent(p.idea)}>
              <span class="t">{p.product}</span>
              <span class={"pill " + (p.switch.on && !p.paused ? "t-green" : "muted")}>{p.switch.on ? (p.paused ? "Paused" : "On") : "Off"}</span>
              <span class="line muted">{p.repo} · {standing(p)}</span>
              <span class="age muted">{p.switch.on && p.counts.waiting ? `${p.counts.waiting} waiting for you` : ""}</span>
            </a>
          </li>
        {/each}
      </ul>
      <p class="muted small" id="maint-month">Maintenance this month: {usd(m.spend.month)}{m.spend.limit != null ? ` of ${usd(m.spend.limit)}` : " (no limit)"}. <a href="#/settings/spend">Settings › Spend</a></p>
    {/if}
  </section>
{/if}
