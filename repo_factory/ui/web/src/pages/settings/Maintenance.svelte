<script lang="ts">
  // Settings › Maintenance: which published repositories it is on for (switched on each
  // product's page), how often it looks, how many fixes run at once, the weekly proposals, and
  // where a fix's session may connect. Its money is on Settings › Spend.
  import { post } from "../../api/client";
  import { put, read } from "../../lib/reads.svelte";
  import Press from "../../ui/Press.svelte";
  import { MAINT, standing, type Maintenance } from "../projects/maintenance";

  const got = $derived(read<Maintenance>(MAINT, 15000));
  const m = $derived(got.data);
  const s = $derived((m?.settings || {}) as Record<string, unknown>);
  let said = $state("");
  let error = $state("");

  async function save(key: string, value: unknown, what: string): Promise<void> {
    said = "";
    error = "";
    try {
      put(MAINT, await post<Maintenance>("api/maintain/settings", { [key]: value }));
      said = `Saved: ${what}. From the next poll.`;
    } catch (e) {
      error = (e as Error).message;
    }
  }
  const minutes = (v: unknown): string => String(Math.round(Number(v || 900) / 60));
  const hosts = (v: unknown): string => (Array.isArray(v) ? v.join(", ") : "");
  const SIGNIN_WORDS = "Fixes will start while builds sign in with Claude Code's own sign-in file: that file is copied into each fix session, where the text of an issue steers a session with tools that could read it. A login token (claude setup-token, kept in Settings › Claude) is never a file in a session.";
</script>

<h2>Maintenance</h2>
<p class="muted">Off until you switch it on for a repository, on its project's page. Then every poll it fixes the open issues you labelled <code>go</code> yourself, each as a pull request, and lists everything else under "Waiting for you". It writes nothing on anyone's issue, never pushes to the default branch, never changes a workflow file, and merges nothing unless you switch that on for the repository too.</p>
{#if got.error && !m}<p class="err" role="alert">{got.error}</p>{/if}
{#if m}
  <section class="sec" aria-labelledby="maint-repos-h">
    <h3 id="maint-repos-h">Your published repositories</h3>
    {#if !m.repos.length}
      <p class="muted" id="maint-none">Nothing published yet.</p>
    {:else}
      <ul class="pms small" id="maint-repos">
        {#each m.repos as r (r.repo)}
          <li><a href={"#/projects/" + encodeURIComponent(r.idea)}>{r.product}</a> <span class="muted">({r.repo})</span>: {standing(r)}</li>
        {/each}
      </ul>
    {/if}
  </section>
  <section class="sec" aria-labelledby="maint-how-h">
    <h3 id="maint-how-h">How it works</h3>
    <div class="setrow">
      <label for="maint-poll">Look every (minutes)</label>
      <input id="maint-poll" inputmode="numeric" value={minutes(s.poll_s)} onchange={(e) => void save("poll_s", Math.round(Number(e.currentTarget.value) * 60), `every ${e.currentTarget.value} minutes`)} />
    </div>
    <div class="setrow">
      <label for="maint-conc">Fixes at once (they share the build slots)</label>
      <input id="maint-conc" inputmode="numeric" value={String(s.concurrency ?? 1)} onchange={(e) => void save("concurrency", Number(e.currentTarget.value), `${e.currentTarget.value} at once`)} />
    </div>
    <label class="switch" id="maint-proposals">
      <input type="checkbox" checked={s.proposals !== false} onchange={(e) => void save("proposals", e.currentTarget.checked, `weekly proposals ${e.currentTarget.checked ? "on" : "off"}`)} />
      Weekly proposals (one capped call a week for each repository it is on for)
    </label>
    <fieldset class="choices" id="maint-net">
      <legend class="muted">Where a fix's session may connect</legend>
      <label><input type="radio" name="maint-net" checked={s.net !== "public"} onchange={() => void save("net", "allowlist", "the allow-list")} /> Only the allow-list below</label>
      <label><input type="radio" name="maint-net" checked={s.net === "public"} onchange={() => void save("net", "public", "anything public")} /> Anything public, as a build does</label>
    </fieldset>
    <p id="maint-hosts">The allow-list: {hosts(m.net_hosts) || "nothing"}.</p>
    <p class="muted small" id="maint-hosts-words">By default the Claude API and the Python package index only. You can add hosts in <code>loop.yaml</code> (<code>maintain.net_hosts</code>), never from this page. A fix's task is the text of an issue: adding <code>github.com</code> or <code>api.github.com</code> lets a session that brings its own token (from that text) post what it reads, your repository's source included, to GitHub.</p>
  </section>
  <section class="sec" aria-labelledby="maint-signin-h">
    <h3 id="maint-signin-h">Claude's sign-in</h3>
    <p id="maint-signin-file">{s.allow_signin_file === true ? "Fixes may run on Claude Code's own sign-in file." : "No fix runs on Claude Code's own sign-in file."}</p>
    {#if s.allow_signin_file === true}
      <Press label="Stop fixes on the sign-in file" id="maint-signin-off" run={async () => { await save("allow_signin_file", false, "no fix on Claude Code's own sign-in file"); return "Off."; }} />
    {:else}
      <Press label="Let fixes run on the sign-in file" id="maint-signin-on" confirm={SIGNIN_WORDS} yes="Let them" run={async () => { await save("allow_signin_file", true, "fixes may run on Claude Code's own sign-in file"); return "On."; }} />
    {/if}
    <p class="muted small">Off by default: while builds sign in with Claude Code's own sign-in file (copied into each session), no fix starts. Run <code>claude setup-token</code> and keep the token in Settings › Claude instead; a token is never a file in a session.</p>
  </section>
{/if}
{#if said}<p class="said" role="status">{said}</p>{/if}
{#if error}<p class="err" role="alert">{error}</p>{/if}
