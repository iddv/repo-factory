<script lang="ts">
  // Settings › Maintenance: which published repositories it is on for (switched on each
  // product's page), how often it looks, how many fixes run at once, the weekly proposals, and
  // where a fix's session may connect. Its money is on Settings › Spend.
  import { post } from "../../api/client";
  import { put, read } from "../../lib/reads.svelte";
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
      <label><input type="radio" name="maint-net" checked={s.net !== "public"} onchange={() => void save("net", "allowlist", "the allow-list")} /> Only the allow-list (Claude, the Python package index, GitHub's public pages)</label>
      <label><input type="radio" name="maint-net" checked={s.net === "public"} onchange={() => void save("net", "public", "anything public")} /> Anything public, as a build does</label>
    </fieldset>
  </section>
{/if}
{#if said}<p class="said" role="status">{said}</p>{/if}
{#if error}<p class="err" role="alert">{error}</p>{/if}
