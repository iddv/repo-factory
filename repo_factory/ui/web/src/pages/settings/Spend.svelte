<script lang="ts">
  // Settings › Spend: the cap on one build and on one day, each a number of dollars or "none".
  // A build that reaches a cap stops, and says the cap stopped it. Maintenance has its own line:
  // what it spent this month, its monthly limit and a fix's cap.
  import { post } from "../../api/client";
  import { capWord } from "../../lib/format";
  import { put, read } from "../../lib/reads.svelte";
  import { MAINT, usd as dollars, type Maintenance } from "../projects/maintenance";
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
  const maint = $derived(read<Maintenance>(MAINT, 15000));
  const ms = $derived(maint.data?.spend);
  let month = $state("");
  let perFix = $state("");
  let mFilled = $state(false);
  let mSaid = $state("");
  let mError = $state("");
  $effect(() => {
    if (ms && !mFilled) {
      month = ms.limit == null ? "none" : String(ms.limit);
      perFix = ms.per_fix == null ? "none" : String(ms.per_fix);
      mFilled = true;
    }
  });
  async function saveMaint(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    mSaid = "";
    mError = "";
    try {
      const a = parseCap(month), b = parseCap(perFix);
      put(MAINT, await post<Maintenance>("api/maintain/settings", { monthly_usd: a, max_usd: b }));
      mSaid = `Saved: ${capWord(a)} a month for maintenance, ${capWord(b)} a fix.`;
    } catch (x) {
      mError = (x as Error).message;
    }
  }
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

<section class="sec" aria-labelledby="spend-maint-h">
  <h3 id="spend-maint-h">Maintenance</h3>
  {#if ms}
    <p id="spend-maint-month">This month: <strong>{dollars(ms.month)}</strong>{ms.limit != null ? ` of ${dollars(ms.limit)}` : " (no limit)"}; today {dollars(ms.today)}.</p>
    {#if ms.paused}<p class="err" role="alert" id="spend-maint-paused">{ms.paused}</p>{/if}
  {:else if maint.error}<p class="err" role="alert">{maint.error}</p>
  {/if}
  <form class="caps" id="spend-maint" onsubmit={saveMaint}>
    <label for="cap-month">Maintenance a month at most</label>
    <input id="cap-month" inputmode="decimal" bind:value={month} placeholder="none" />
    <label for="cap-fix">A fix at most</label>
    <input id="cap-fix" inputmode="decimal" bind:value={perFix} placeholder="none" />
    <p class="muted small">Fixes and the weekly proposals count here, and against the daily cap. At the limit maintenance starts nothing more this month, and says so.</p>
    <div class="actions"><button type="submit" class="btn primary" id="maint-caps-save" disabled={!ms}>Save</button></div>
  </form>
  {#if mSaid}<p class="said" role="status">{mSaid}</p>{/if}
  {#if mError}<p class="err" role="alert">{mError}</p>{/if}
</section>
