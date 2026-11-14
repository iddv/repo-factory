<script lang="ts">
  // A product's page: what its brief promised, its maintenance (the switch, Pause, merging its own
  // fixes), what waits for you, what its fixes did, and this week's proposals. Nothing on this page
  // reads GitHub; the poll does, every 15 minutes, and the page shows what it saw.
  import { q } from "../../api/client";
  import { ago } from "../../lib/format";
  import { read } from "../../lib/reads.svelte";
  import Press from "../../ui/Press.svelte";
  import { KIND_WORD, buildProposal, mergeOn, pause, retry, standing, switchOn, usd, type Project } from "./maintenance";
  let mergeError = $state("");
  async function flipMerge(repo: string, on: boolean): Promise<void> {
    mergeError = "";
    try {
      await mergeOn(repo, on);
    } catch (e) {
      mergeError = (e as Error).message;
    }
  }

  let { id }: { id: string } = $props();
  const when = (t: number): string => (ago(t) === "now" ? "just now" : `${ago(t)} ago`);
  const path = $derived(q("api/project", { id }));
  const got = $derived(read<Project>(path, 15000));
  const p = $derived(got.data);
  const waiting = $derived((p?.rows || []).filter((x) => x.kind !== "fixing"));
  const fixing = $derived((p?.rows || []).filter((x) => x.kind === "fixing"));
  const warnings = $derived((p?.rows || []).filter((x) => x.kind === "self_hosted"));
  const OUTCOME: Record<string, string> = {
    pr: "opened a pull request",
    failed: "stopped",
    cannot_reproduce: "could not reproduce it",
    skipped: "did nothing",
    retry: "will try again",
  };
</script>

<section class="page project" aria-labelledby="project-h">
  <a class="back" href="#/projects">← Projects</a>
  {#if got.error && !p}
    <h1 id="project-h">Project</h1>
    <p class="err" role="alert">{got.error}</p>
  {:else if !p}
    <h1 id="project-h">Project</h1>
    <p class="muted">Reading…</p>
  {:else}
    <h1 id="project-h">{p.product}</h1>
    <p class="muted">{p.brief.one_liner} · <a href={"https://github.com/" + p.repo} target="_blank" rel="noopener">{p.repo} ↗</a>{#if p.build} · <a href={"#/builds/" + encodeURIComponent(p.build)}>its build</a>{/if}</p>

    <section class="sec" aria-labelledby="promise-h">
      <h2 id="promise-h">What its brief promised</h2>
      {#if p.brief.who}<p><strong>For:</strong> {p.brief.who}</p>{/if}
      <p id="project-brief">{p.brief.brief || "The build kept no brief."}</p>
    </section>

    <section class="sec" aria-labelledby="maint-h" id="maintenance">
      <h2 id="maint-h">Maintenance</h2>
      <p id="maint-state"><strong>{standing(p)}</strong>{#if p.polled} <span class="muted small">· last looked {when(p.polled)}</span>{/if}</p>
      {#each warnings as w, i (i)}<p class="err" role="alert" id="maint-self-hosted">{w.title}: {w.why}</p>{/each}
      <div class="controls">
        {#if !p.switch.on}
          <Press
            label="Switch maintenance on"
            id="maint-on"
            primary
            confirm={`This opens one pull request on ${p.repo} (labels, the policy file, AGENTS.md and CI) and creates its labels. Then, every 15 minutes, it fixes only issues you label go yourself, each as a pull request, under your monthly limit. It writes nothing on any issue.`}
            yes="Switch on"
            run={async () => {
              await switchOn(p.repo, true);
              return "On: the setup pull request opens in the background.";
            }}
          />
        {:else}
          <Press label="Switch maintenance off" id="maint-off" confirm="The next poll will not read this repository at all. A fix already running stops before its next step." yes="Switch off" run={async () => { await switchOn(p.repo, false); return "Off."; }} />
          {#if p.paused}
            <Press label="Resume" id="maint-resume" run={async () => { await pause(p.repo, false); return "Resumed: from the next poll."; }} />
          {:else}
            <Press label="Pause" id="maint-pause" run={async () => { await pause(p.repo, true); return "Paused: the next poll skips it."; }} />
          {/if}
        {/if}
      </div>
      {#if p.switch.on}
        <label class="switch" id="maint-merge">
          <input
            type="checkbox"
            checked={p.switch.merge}
            disabled={warnings.length > 0}
            onchange={(e) => void flipMerge(p.repo, e.currentTarget.checked)}
          />
          Merge its own fixes when CI is green
        </label>
        {#if mergeError}<p class="err" role="alert">{mergeError}</p>{/if}
        <p class="muted small">Off by default. Bugs and docs only, every check green, never a change under <code>.github/</code>, never a pull request anyone else pushed to, never while a workflow names a self-hosted runner; then a patch release, when the policy file allows one.</p>
      {/if}
      {#if p.setup?.state === "failed"}<p class="err" role="alert" id="maint-setup-failed">The setup stopped: {p.setup.why}</p>{/if}
      {#if p.setup?.url}<p class="small">Setup pull request: <a href={p.setup.url} target="_blank" rel="noopener">#{p.setup.pr} ↗</a></p>{/if}
      {#if p.error}<p class="err small">{p.error}</p>{/if}
      <p class="muted small" id="maint-spent">Spent on this product this month: {usd(p.month_usd)}.</p>
    </section>

    {#if p.switch.on}
      <section class="sec" aria-labelledby="waiting-h">
        <h2 id="waiting-h">Waiting for you</h2>
        {#if !waiting.length}
          <p class="muted" id="waiting-empty">Nothing waits for you{p.polled ? "" : " (it has not looked yet: the first poll comes within 15 minutes)"}.</p>
        {:else}
          <ul class="rows" id="waiting">
            {#each waiting as x, i (i)}
              <li class="row wait">
                <a href={x.url || "#/projects/" + p.idea} target={x.url ? "_blank" : undefined} rel="noopener">
                  <span class="t">{x.number ? `#${x.number} ` : ""}{x.title}</span>
                  <span class="pill muted">{KIND_WORD[x.kind] || x.kind}</span>
                  <span class="line muted">{x.why}</span>
                </a>
                {#if x.kind === "needs_you" && x.number}
                  <Press label="Retry" run={async () => { await retry(p.repo, x.number as number); return "It may try again at the next poll."; }} />
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
        {#if fixing.length}<p class="small" id="fixing">Fixing now: {fixing.map((x) => `#${x.number}`).join(", ")}</p>{/if}
      </section>

      <section class="sec" aria-labelledby="fixes-h">
        <h2 id="fixes-h">What its fixes did</h2>
        {#if !p.fixes.length}
          <p class="muted" id="fixes-empty">No fix yet. Label one of your issues <code>go</code> on GitHub and it is picked up within 15 minutes.</p>
        {:else}
          <ul class="pms small" id="fixes">
            {#each p.fixes as f (f.issue)}
              <li>#{f.issue}: {OUTCOME[f.outcome || ""] || f.outcome}{#if f.pr} <a href={f.url || `https://github.com/${p.repo}/pull/${f.pr}`} target="_blank" rel="noopener">#{f.pr} ↗</a>{/if}{#if f.why && f.outcome !== "pr"} ({f.why}){/if} · {usd(f.usd)}{#if f.at} · {when(f.at)}{/if}</li>
            {/each}
          </ul>
        {/if}
      </section>

      <section class="sec" aria-labelledby="proposals-h">
        <h2 id="proposals-h">This week's proposals</h2>
        <p class="muted small">Once a week it reads the README and the open issues' titles and suggests a few improvements. Nothing is built from one until you press it: it then becomes an issue labelled <code>go</code> by you, and the next poll fixes it as a pull request.</p>
        {#if !p.proposals.items.length}
          <p class="muted" id="proposals-empty">{p.proposals.error || "No proposals yet."}</p>
        {:else}
          <ul class="lesson-list" id="proposals">
            {#each p.proposals.items as x (x.id)}
              <li class="lesson">
                <div class="lesson-main">
                  <p class="lesson-text"><span class="muted lesson-id">{x.kind}</span> {x.title}</p>
                  <p class="muted small">{x.why}</p>
                </div>
                {#if x.state === "built"}
                  <a class="small" href={x.url || `https://github.com/${p.repo}/issues/${x.issue}`} target="_blank" rel="noopener">issue #{x.issue} ↗</a>
                {:else}
                  <Press label="Build this proposal" confirm={`This opens an issue on ${p.repo} with this text, labelled go by you; the next poll fixes it as a pull request.`} yes="Open the issue" run={async () => { await buildProposal(p.repo, x.id); return "Opened."; }} />
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </section>
    {/if}
  {/if}
</section>
