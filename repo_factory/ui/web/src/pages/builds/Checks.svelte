<script lang="ts">
  // The checks it passed, and anything held, in plain words. They are the factory's own checks:
  // useful, and not proof.
  import { checkWord, plainReason, type Build } from "../../lib/builds";

  let { b }: { b: Build } = $props();
  const checks = $derived(Object.entries(b.gate?.checks || {}));
  const passed = $derived(checks.filter(([, c]) => c.ok));
  const failed = $derived(checks.filter(([, c]) => !c.ok));
  // what held it: the failing checks' details, the gate's reasons, and the pre-push scan's holds
  const held = $derived.by(() => {
    const out: string[] = [];
    for (const [k, c] of failed) out.push(`${checkWord(k)}: no${c.detail ? " (" + plainReason(c.detail) + ")" : ""}`);
    if (!failed.length) for (const r of b.gate?.reasons || []) out.push(plainReason(r));
    for (const [k, d] of b.precheck?.holds || []) out.push(`${checkWord(k)}: ${plainReason(d)}`);
    return out;
  });
</script>

{#if passed.length}
  <h4>Passed</h4>
  <ul class="checks ok" id="checks-passed">{#each passed as [k, c] (k)}<li>{checkWord(k)}{#if c.detail}<span class="muted"> · {plainReason(c.detail)}</span>{/if}</li>{/each}</ul>
{/if}
{#if held.length}
  <h4>Held</h4>
  <ul class="checks held" id="checks-held">{#each held as h, i (i)}<li>{h}</li>{/each}</ul>
{:else if b.gate}
  <p class="muted">Nothing held.</p>
{/if}
{#if b.gate?.gaps?.length}
  <h4>Not measured</h4>
  <ul class="checks">{#each b.gate.gaps as g, i (i)}<li>{plainReason(g)}</li>{/each}</ul>
{/if}
<p class="muted small">These are the factory's own checks. They are not proof: read what it built.</p>
