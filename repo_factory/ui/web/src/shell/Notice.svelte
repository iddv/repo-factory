<script lang="ts">
  // The notice: a quiet band at the top of the first screen, until it is dismissed.
  import { dismissNotice, NOTICE, noticeDismissed } from "../lib/notice";

  let shown = $state(!noticeDismissed());
  function dismiss(): void {
    dismissNotice();
    shown = false;
  }
</script>

{#if shown}
  <aside class="notice" id="notice" aria-label="About this experiment">
    <p>{NOTICE}</p>
    <button type="button" class="btn sm quiet" id="notice-dismiss" onclick={dismiss}>Dismiss</button>
  </aside>
{/if}
