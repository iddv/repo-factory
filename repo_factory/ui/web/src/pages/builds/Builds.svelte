<script lang="ts">
  // Builds: the list, and the selected build beside it (on a phone, one or the other). With
  // nothing built yet: one line and one button, New task.
  import { items, STATE, type State } from "../../lib/builds";
  import { read } from "../../lib/reads.svelte";
  import { href, type Route } from "../../lib/router.svelte";
  import { pendingItems } from "../../lib/tasks.svelte";
  import BuildList from "./BuildList.svelte";
  import BuildPage from "./BuildPage.svelte";
  import NewTask from "./NewTask.svelte";

  let { r }: { r: Route } = $props();

  const got = $derived(read<State>(STATE, 15000));
  const s = $derived(got.data);
  const have = $derived(new Set([...(s?.ideas || []).map((t) => t.id), ...(s?.builds || []).map((b) => b.idea || "")]));
  const rows = $derived([...pendingItems(have), ...items(s)]);
  const sel = $derived(r.id);
  // a task's address (#/builds/task:<id>, where New task lands) follows it into its build once
  // its brief is written and the build has a row of its own
  const open = $derived(
    rows.find((x) => x.key === sel) ||
      (sel?.startsWith("task:") ? rows.find((x) => x.b?.idea === sel.slice(5)) : null) ||
      null,
  );
</script>

<div class={"builds" + (sel ? " has-detail" : "")}>
  <section class="list" aria-labelledby="builds-h">
    <header class="head">
      <h1 id="builds-h">Builds</h1>
      {#if rows.length}<a class="btn primary" id="new-task" href={href("builds", "new")} aria-current={sel === "new" ? "page" : undefined}>New task</a>{/if}
    </header>
    {#if !s && got.error}
      <p class="err" role="alert">The builds could not be read: {got.error}</p>
    {:else if !s}
      <p class="muted">Reading the builds…</p>
    {:else if !rows.length}
      <div class="empty" id="builds-empty">
        <p>No builds yet: describe what you want built, and it starts with a brief.</p>
        <a class="btn primary" id="new-task" href={href("builds", "new")}>New task</a>
      </div>
    {:else}
      <BuildList {rows} {sel} />
    {/if}
  </section>
  <section class="detail" aria-label="The selected build">
    {#if sel}<a class="back" href={href("builds")}>‹ All builds</a>{/if}
    {#if sel === "new"}
      <NewTask />
    {:else if sel}
      <BuildPage key={sel} item={open} q={r.q} loaded={!!s} />
    {:else if rows.length}
      <p class="muted pick">Choose a build to see its stages.</p>
    {/if}
  </section>
</div>
