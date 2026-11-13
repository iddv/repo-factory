<script lang="ts">
  // A build that asks a question: the answer box (api/act answer).
  import { act } from "../../api/client";
  import type { Build } from "../../lib/builds";
  import { stale } from "../../lib/reads.svelte";

  let { b }: { b: Build } = $props();
  let text = $state("");
  let busy = $state(false);
  let error = $state("");

  async function send(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    if (!b.question || busy) return;
    busy = true;
    error = "";
    try {
      await act("answer", { kind: "build", id: b.key }, { qid: b.question.id, text: text.trim() });
      text = "";
      stale("api/");
    } catch (x) {
      error = (x as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<form class="askbox" id="answer" onsubmit={send}>
  <label for="answer-text">Your answer</label>
  <textarea id="answer-text" rows="3" bind:value={text}></textarea>
  {#if error}<p class="err" role="alert">{error}</p>{/if}
  <div class="actions"><button type="submit" class="btn primary" disabled={busy || !text.trim()}>{busy ? "Sending…" : "Answer"}</button></div>
</form>
