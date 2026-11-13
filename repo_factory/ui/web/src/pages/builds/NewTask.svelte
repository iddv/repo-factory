<script lang="ts">
  // New task: a text box, or a file whose text is read here and sent as the task; then Build.
  // Its first line names it. The row is on the list at once and says Brief.
  import { go, href } from "../../lib/router.svelte";
  import { fileText, sendTask } from "../../lib/tasks.svelte";

  let text = $state("");
  let from = $state("");
  let busy = $state(false);
  let error = $state("");

  async function pick(e: Event): Promise<void> {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    error = "";
    if (!f) return;
    try {
      text = await fileText(f);
      from = f.name;
    } catch (x) {
      error = (x as Error).message;
    }
  }
  async function build(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    if (busy) return;
    busy = true;
    error = "";
    try {
      const id = await sendTask(text);
      text = "";
      from = "";
      go(id ? href("builds", "task:" + id) : href("builds"));
    } catch (x) {
      error = (x as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<form class="newtask" id="new-task-form" onsubmit={build}>
  <h2>New task</h2>
  <p class="muted">Say what you want built: what it does, for whom, and how you will know it works. A sentence is enough; a page is better. The first line names it.</p>
  <label for="task-text">The task</label>
  <textarea id="task-text" rows="9" bind:value={text} placeholder="A command-line tool that turns a folder of Markdown notes into one searchable HTML page"></textarea>
  <div class="file">
    <label for="task-file">Or a file</label>
    <input id="task-file" type="file" accept=".md,.txt,text/plain,text/markdown" onchange={pick} />
    {#if from}<span class="muted small">The text of {from} is in the box: read it, change it, then Build.</span>{/if}
  </div>
  {#if error}<p class="err" role="alert">{error}</p>{/if}
  <div class="actions">
    <button type="submit" class="btn primary" id="task-build" disabled={busy || !text.trim()}>{busy ? "Sending…" : "Build"}</button>
    <span class="muted small">It costs real money or plan usage. Nothing is published until you press Publish.</span>
  </div>
</form>
