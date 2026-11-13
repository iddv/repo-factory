<script lang="ts">
  // Settings › Publishing: a build that passed its checks waits at "Ready: publish?" for your
  // press (publish.when: ask, the default), or becomes a private repository on its own
  // (publish.when: ready). Making it public is always a separate press on each build.
  import { read } from "../../lib/reads.svelte";
  import { RULES, ruleValue, setRule, type Rules } from "./settings";

  const rules = $derived(read<Rules>(RULES, 30000));
  const when = $derived(String(ruleValue(rules.data, "publish.when") ?? "ask"));
  const publicAlone = $derived(ruleValue(rules.data, "publish.approval") === "auto");
  let said = $state("");
  let error = $state("");
  let asking = $state(false);

  async function set(v: "ask" | "ready"): Promise<void> {
    said = "";
    error = "";
    asking = false;
    try {
      await setRule("publish.when", v);
      said = v === "ask" ? "Saved: it asks first. From the next build." : "Saved: it publishes privately when the checks pass. From the next build.";
    } catch (e) {
      error = (e as Error).message;
    }
  }
</script>

<h2>Publishing</h2>
<section class="sec">
  <h3>When</h3>
  <fieldset class="choices" id="publish-when">
    <legend class="muted">When a build passes its checks:</legend>
    <label><input type="radio" name="when" checked={when !== "ready"} onchange={() => void set("ask")} /> Ask me first: it waits at "Ready: publish?" until I press Publish</label>
    <label><input type="radio" name="when" checked={when === "ready"} onchange={() => (asking = true)} /> Publish privately on its own</label>
  </fieldset>
  {#if asking}
    <p class="ask" role="group" aria-label="Publish privately on its own">
      <span>A build that passes its checks then creates a private repository under your account without asking.</span>
      <button type="button" class="btn sm primary" onclick={() => void set("ready")}>Publish privately on its own</button>
      <button type="button" class="btn sm quiet" onclick={() => (asking = false)}>Cancel</button>
    </p>
  {/if}
</section>
<section class="sec">
  <h3>Where</h3>
  <p>A private repository under your GitHub account. Only you make it public: Make public is a separate press on each build.</p>
  {#if publicAlone}
    <p class="err" role="alert">Your settings file says <code>publish.approval: auto</code>: every published build is made public without asking. Remove that line from <code>loop.yaml</code> to make public a press again.</p>
  {/if}
</section>
{#if said}<p class="said" role="status">{said}</p>{/if}
{#if error}<p class="err" role="alert">{error}</p>{/if}
