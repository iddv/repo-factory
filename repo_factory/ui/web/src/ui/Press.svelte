<script lang="ts">
  // A button that does one thing on the server. With `confirm`, the first press asks in a line
  // under it (what will happen, and Yes or Cancel); the answer or the error is said beside it.
  let {
    label,
    run,
    confirm = null,
    yes = null,
    primary = false,
    danger = false,
    id = undefined,
    disabled = false,
    why = null,
  }: {
    label: string;
    run: () => Promise<string | void>;
    confirm?: string | null;
    yes?: string | null;
    primary?: boolean;
    danger?: boolean;
    id?: string;
    disabled?: boolean;
    why?: string | null;
  } = $props();

  let asking = $state(false);
  let busy = $state(false);
  let said = $state("");
  let error = $state("");

  async function go(): Promise<void> {
    asking = false;
    busy = true;
    said = "";
    error = "";
    try {
      said = (await run()) || "";
    } catch (e) {
      error = (e as Error).message || "It did not work.";
    } finally {
      busy = false;
    }
  }
  function press(): void {
    if (confirm) asking = true;
    else void go();
  }
</script>

<span class="press">
  <button type="button" class={"btn" + (primary ? " primary" : "") + (danger ? " danger" : "")} {id} disabled={disabled || busy} title={disabled ? why : null} aria-expanded={confirm ? asking : undefined} onclick={press}>{busy ? label + "…" : label}</button>
  {#if asking}
    <span class="ask" role="group" aria-label={label}>
      <span>{confirm}</span>
      <button type="button" class={"btn sm" + (danger ? " danger" : " primary")} onclick={() => void go()}>{yes || label}</button>
      <button type="button" class="btn sm quiet" onclick={() => (asking = false)}>Cancel</button>
    </span>
  {/if}
  {#if said}<span class="said" role="status">{said}</span>{/if}
  {#if error}<span class="err" role="alert">{error}</span>{/if}
</span>
