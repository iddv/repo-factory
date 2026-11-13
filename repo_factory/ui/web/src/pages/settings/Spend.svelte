<script lang="ts">
  // Settings › Spend: the cap on one build and on one day, each a number of dollars or "none".
  // A build that reaches a cap stops, and says the cap stopped it.
  import { capWord } from "../../lib/format";
  import { read } from "../../lib/reads.svelte";
  import { CAPS, parseCap, setCaps, type Caps } from "./settings";

  const got = $derived(read<Caps>(CAPS, 30000));
  const c = $derived(got.data);
  let build = $state("");
  let day = $state("");
  let filled = $state(false);
  let said = $state("");
  let error = $state("");
  // the fields start from what is set, once it is read
  $effect(() => {
    if (c && !filled) {
      build = c.build_max_usd == null ? "none" : String(c.build_max_usd);
      day = c.daily_usd == null ? "none" : String(c.daily_usd);
      filled = true;
    }
  });
  async function save(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    said = "";
    error = "";
    try {
      const b = parseCap(build), d = parseCap(day);
      await setCaps(d, b);
      said = `Saved: ${capWord(b)} a build, ${capWord(d)} a day. From the next build.`;
    } catch (x) {
      error = (x as Error).message;
    }
  }
</script>

<h2>Spend</h2>
<p class="muted">A build costs real money or plan usage: about $10 on our record. A build that reaches a cap stops, and says so.</p>
{#if got.error && !c}<p class="err" role="alert">{got.error}</p>{/if}
<form class="caps" id="spend-caps" onsubmit={save}>
  <label for="cap-build">A build at most</label>
  <input id="cap-build" inputmode="decimal" bind:value={build} placeholder="none" />
  <label for="cap-day">A day at most</label>
  <input id="cap-day" inputmode="decimal" bind:value={day} placeholder="none" />
  <p class="muted small">Dollars, or "none" for no cap.</p>
  <div class="actions"><button type="submit" class="btn primary" id="caps-save" disabled={!c}>Save</button></div>
</form>
{#if said}<p class="said" role="status">{said}</p>{/if}
{#if error}<p class="err" role="alert">{error}</p>{/if}
