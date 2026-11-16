<script lang="ts">
  // A product's page: what its brief promised, its maintenance (the switch, Pause, merging its own
  // fixes), what waits for you, what its fixes did, and this week's proposals. Nothing on this page
  // reads GitHub; the poll does (every maintain.poll_s, 15 minutes by default), and the page shows what it saw.
  import { q } from "../../api/client";
  import { ago } from "../../lib/format";
  import { read } from "../../lib/reads.svelte";
  import Press from "../../ui/Press.svelte";
  import { KIND_WORD, buildProposal, mergeOn, pause, releaseOn, retry, standing, switchOn, usd, type Project } from "./maintenance";
  // what the merge and release switches do, said before either turns on
  const MERGE_WORDS = (released: boolean): string =>
    "From the next poll, repo-factory merges one of its own fix pull requests a poll, with nobody reading it first: only a fix this install opened, whose issue is labelled bug or docs; a docs fix only when every changed file is a document; a bug fix only with its reproducing test in the diff; never a change to a manifest, a lock file, build or test settings, .github/, an agent's or editor's file, or a link; every check green. " +
    (released ? "Each merge is then released (a tag and a GitHub release, which run any workflow on tags or releases, with its secrets)." : "Merges are not released: that is its own switch, off.");
  const RELEASE_WORDS =
    "After each merge, repo-factory creates a tag and a GitHub release with your token. Any workflow of this repository that runs on tags or releases runs then, with its secrets (a package upload, if you added one), with nobody reading the change first.";

  let { id }: { id: string } = $props();
  const when = (t: number): string => (ago(t) === "now" ? "just now" : `${ago(t)} ago`);
  const path = $derived(q("api/project", { id }));
  const got = $derived(read<Project>(path, 15000));
  const p = $derived(got.data);
  const waiting = $derived((p?.rows || []).filter((x) => x.kind !== "fixing" && x.kind !== "signin" && x.kind !== "self_hosted"));
  const fixing = $derived((p?.rows || []).filter((x) => x.kind === "fixing"));
  const warnings = $derived((p?.rows || []).filter((x) => x.kind === "self_hosted"));
  const SWITCH_ON = (repo: string, signin: string | null | undefined): string =>
    `This opens one pull request on ${repo} (labels, the policy file, AGENTS.md and CI) and creates its labels. From the next poll (every 15 minutes unless you changed it in Settings › Maintenance), it fixes only issues you labelled go yourself, each as a pull request, under your monthly limit. It writes nothing on any issue. The CI it adds runs on your account's GitHub Actions minutes: four Linux jobs (the tests on two Pythons, the AGENTS.md check, a secret scan) on every pull request and push to main, and once a week.` +
    (signin ? ` But no fix starts yet: run \`claude setup-token\` first and keep the token in Settings › Claude (Claude Code's own sign-in file would be copied into each fix session).` : "");
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
      <p id="maint-state"><strong>{standing(p)}</strong>{#if p.polled}<span class="muted small">{" · last looked " + when(p.polled)}</span>{/if}</p>
      {#each warnings as w, i (i)}<p class="err" role="alert" id="maint-self-hosted">{w.title}: {w.why}</p>{/each}
      {#if p.signin}<p class="err" role="alert" id="maint-signin">No fix starts: run <code>claude setup-token</code> first and keep the token in <a href="#/settings/claude">Settings › Claude</a>. Claude Code's own sign-in file would be copied into each fix session, where the text of an issue steers a session with tools.</p>{/if}
      <div class="controls">
        {#if !p.switch.on}
          <Press
            label="Switch maintenance on"
            id="maint-on"
            primary
            confirm={SWITCH_ON(p.repo, p.signin)}
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
        <div id="maint-merge">
          <p><strong>Merge its own fixes when CI is green:</strong> {p.switch.merge ? "on" : "off"}.</p>
          <div class="controls">
            {#if p.switch.merge}
              <Press label="Stop merging" id="maint-merge-off" run={async () => { await mergeOn(p.repo, false); return "Off: nothing merges."; }} />
            {:else}
              <Press label="Merge its own fixes" id="maint-merge-on" disabled={warnings.length > 0} why={warnings.length ? "a workflow on main may run off GitHub's own runners" : null} confirm={MERGE_WORDS(p.switch.release === true)} yes="Merge them" run={async () => { await mergeOn(p.repo, true); return "On: from the next poll."; }} />
            {/if}
          </div>
          <p class="muted small">Off by default. Only a fix this install opened, whose issue says <code>bug</code> or <code>docs</code>: a docs fix changes documents only; a bug fix carries its reproducing test; no fix that changes a file that runs somewhere (manifests, lock files, build and test settings, <code>.github/</code>, agent and editor files) or a link; every check green; never a pull request anyone else pushed to; never while a workflow may run off GitHub's own runners.</p>
        </div>
        {#if p.switch.merge}
          <div id="maint-release">
            <p><strong>Release after each merge:</strong> {p.switch.release ? "on" : "off"}.</p>
            {#if p.switch.release}
              <Press label="Stop releasing" id="maint-release-off" run={async () => { await releaseOn(p.repo, false); return "Off: merges are not released."; }} />
            {:else}
              <Press label="Release after each merge" id="maint-release-on" confirm={RELEASE_WORDS} yes="Release them" run={async () => { await releaseOn(p.repo, true); return "On: the next merge is released."; }} />
            {/if}
            <p class="muted small">Off by default, whatever the policy file says.</p>
          </div>
        {/if}
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
          <p class="muted" id="waiting-empty">Nothing waits for you{p.polled ? "" : " (it has not looked yet: the first poll comes within one poll's interval, 15 minutes by default)"}.</p>
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
          <p class="muted" id="fixes-empty">No fix yet. Label one of your issues <code>go</code> on GitHub and the next poll picks it up (every 15 minutes by default; Settings › Maintenance).</p>
        {:else}
          <ul class="pms small" id="fixes">
            {#each p.fixes as f (f.issue)}
              <li>#{f.issue}: {OUTCOME[f.outcome || ""] || f.outcome}{#if f.pr}{" "}<a href={f.url || `https://github.com/${p.repo}/pull/${f.pr}`} target="_blank" rel="noopener">#{f.pr} ↗</a>{/if}{#if f.why && f.outcome !== "pr"}{` (${f.why})`}{/if}{" · " + usd(f.usd)}{#if f.at}{" · " + when(f.at)}{/if}</li>
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
