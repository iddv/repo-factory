<script lang="ts">
  // Settings › Building: the flow a build runs (v5, the proven one, or lean, which is not proven
  // yet) and the model of each part of it. Each applies from the next build.
  import { read } from "../../lib/reads.svelte";
  import { MODELS, RULES, ruleValue, setModel, setRule, type Models, type Rules } from "./settings";

  const FLOWS: [string, string][] = [
    ["v5", "v5"],
    ["lean", "lean (unproven)"],
  ];
  const rules = $derived(read<Rules>(RULES, 30000));
  const models = $derived(read<Models>(MODELS, 30000));
  const flow = $derived(String(ruleValue(rules.data, "build.flow") ?? "v5"));
  let said = $state("");
  let error = $state("");

  async function save(what: string, f: () => Promise<void>): Promise<void> {
    said = "";
    error = "";
    try {
      await f();
      said = `Saved: ${what}. From the next build.`;
    } catch (e) {
      error = (e as Error).message;
    }
  }
</script>

<h2>Building</h2>
<section class="sec">
  <h3>The flow</h3>
  <fieldset class="choices" id="build-flow">
    <legend class="muted">How a build runs: the six stages, by one of two flows.</legend>
    {#each FLOWS as [v, word] (v)}
      <label><input type="radio" name="flow" value={v} checked={flow === v} onchange={() => void save(`the flow, ${word}`, () => setRule("build.flow", v))} /> {word}</label>
    {/each}
  </fieldset>
  <p class="muted small">v5 is the flow the record is from. Lean is shorter and cheaper, and has no record yet.</p>
</section>
<section class="sec">
  <h3>The models</h3>
  {#if models.error && !models.data}<p class="err" role="alert">{models.error}</p>
  {:else if !models.data}<p class="muted">Reading…</p>
  {:else}
    <ul class="models" id="models">
      {#each models.data.roles as m (m.role)}
        <li>
          <label for={"model-" + m.role}><strong>{m.name}</strong><span class="muted small">{m.does}</span></label>
          <select id={"model-" + m.role} value={m.set ? m.model || "" : ""} onchange={(e) => void save(`the model of ${m.name}`, () => setModel(m.role, e.currentTarget.value || null))}>
            <option value="">The default{m.default ? ` (${m.default})` : ""}</option>
            {#each models.data.catalog as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
          </select>
        </li>
      {/each}
    </ul>
  {/if}
</section>
{#if said}<p class="said" role="status">{said}</p>{/if}
{#if error}<p class="err" role="alert">{error}</p>{/if}
