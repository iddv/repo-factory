<script lang="ts">
  // A build's page: what it is doing now, its controls (Stop, Ask for changes, Publish, Make
  // public), its six stages with their time, cost and log, and the checks it passed and anything
  // held, in plain words. A task whose brief is being written has only its first stage.
  import type { BuildRun, BuildRuns, Run } from "../../api/types.gen";
  import { q } from "../../api/client";
  import { published, type Build, type Item } from "../../lib/builds";
  import { cut } from "../../lib/format";
  import { read } from "../../lib/reads.svelte";
  import { href } from "../../lib/router.svelte";
  import { cells, type Around } from "../../lib/stages";
  import Answer from "./Answer.svelte";
  import Checks from "./Checks.svelte";
  import Controls from "./Controls.svelte";
  import Log from "./Log.svelte";
  import Stages from "./Stages.svelte";

  let { key, item, q: query, loaded }: { key: string; item: Item | null; q: Record<string, string>; loaded: boolean } = $props();

  const b = $derived<Build | null>(item?.b || null);
  // while it builds again (a retry, a resume) the last gate is the previous attempt's: not shown
  const busy = $derived(!!b && ["running", "queued"].includes(b.status));
  const runs = $derived(b ? read<BuildRuns>(q("api/build/runs", { dir: b.dir }), 10000) : null);
  const latest = $derived<Run | null>(runs?.data?.runs?.[0] || null);
  const one = $derived(b && latest ? read<BuildRun>(q("api/run", { dir: b.dir, n: latest.n }), 10000) : null);
  const cur = $derived(one?.data && latest && one.data.run.n === latest.n ? one.data : null);
  const around = $derived<Around>(
    b
      ? {
          briefed: true,
          publish: b.status === "public" || published(b) ? "done" : b.status === "ready" || b.status === "awaiting_publish" ? "waiting" : !busy && b.gate && b.gate.verdict !== "ready" ? "held" : null,
        }
      : { briefed: false, briefing: !!item && item.word === "Brief" },
  );
  const six = $derived(cells(cur?.stages || [], around));
  const now = $derived.by(() => {
    if (!item) return "";
    if (!b) return item.word === "Brief" ? "The brief is being written: the task is read and turned into what to build. The other stages follow." : item.line || item.word;
    if (b.question) return `It asks you: ${b.question.text || "a question"}`;
    if (b.control?.what) return "Stopping: its workers are being stopped.";
    switch (item.word) {
      case "Ready: publish?":
        return "Ready: publish? It passed the checks. Nothing is on GitHub yet: Publish creates a private repository under your account.";
      case "Published privately":
        return `Published privately${b.repo ? " as " + b.repo : ""}. Read it; Make public when you are sure.`;
      case "Public":
        return `Public${b.repo ? " as " + b.repo : ""}.`;
      case "Stopped":
        return "Stopped. Its workers were killed; nothing else changed.";
      case "Waiting for a slot":
        return "Waiting for a build slot: it starts when one is free.";
      case "Held by the checks":
        return "The checks held it. Ask for changes, or read what held it below.";
    }
    if (b.status === "running") return `${item.word}: running now. Stop ends it; nothing else changes.`;
    return b.reason ? cut(b.reason, 200) : item.word;
  });
</script>

{#if !item}
  <article class="build" data-title="Builds">
    {#if !loaded}<p class="muted">Reading…</p>
    {:else if key.startsWith("task:")}<p class="muted">The task was sent; its brief is being written. It is on the list in a moment.</p>
    {:else}<p class="muted">No such build.</p>{/if}
  </article>
{:else}
  <article class="build" data-title={item.title}>
    <header>
      <h2 class="title">{item.title}</h2>
      <p class="status"><span class={"pill t-" + item.tone}>{item.word}</span>{#if b?.url}{" "}<a href={b.url} target="_blank" rel="noopener noreferrer">{b.repo || "the repository"} ↗</a>{/if}</p>
      {#if b?.desc}<p class="muted">{b.desc}</p>{/if}
    </header>
    <section class="sec" id="s-now" aria-labelledby="now-h">
      <h3 id="now-h">What now</h3>
      <p class="now" role="status">{now}</p>
      {#if b?.question}<Answer {b} />{/if}
      {#if b}<Controls {b} said={item.word} />{/if}
    </section>
    <section class="sec" id="s-stages" aria-labelledby="stages-h">
      <h3 id="stages-h">Stages{#if latest && (runs?.data?.runs?.length || 0) > 1}<span class="muted small"> · build {latest.n} of {runs?.data?.runs?.length}</span>{/if}</h3>
      {#if b && runs?.error && !runs.data}<p class="err" role="alert">{runs.error}</p>{/if}
      <Stages cells={six} logHref={(k) => href("builds", key, { log: k })} open={query.log || null} />
      {#if query.log && b && latest}
        <Log dir={b.dir} n={latest.n} cell={query.log} />
      {:else if query.log && !b}
        <p class="muted">{item.task?.text ? "The task as you wrote it:" : "Nothing logged yet."}</p>
        {#if item.task?.text}<pre class="task-text">{item.task.text}</pre>{/if}
      {/if}
    </section>
    {#if !busy && (b?.gate || b?.precheck?.holds?.length)}
      <section class="sec" id="s-checks" aria-labelledby="checks-h">
        <h3 id="checks-h">Checks</h3>
        <Checks {b} />
      </section>
    {/if}
  </article>
{/if}
