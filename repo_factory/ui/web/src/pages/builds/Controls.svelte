<script lang="ts">
  // A build's controls: Stop while it runs; Ask for changes once it has something to change;
  // Publish while it waits at "Ready: publish?"; Make public once it is published privately.
  // Each is a registry action (api/act) on the build, offered by the server (b.actions); a
  // press that changes something on GitHub, or ends a build, asks first.
  import { act, post } from "../../api/client";
  import { offer, published, type Build } from "../../lib/builds";
  import { stale } from "../../lib/reads.svelte";
  import Press from "../../ui/Press.svelte";

  let { b, said }: { b: Build; said: string } = $props();

  let asking = $state(false);
  let text = $state("");
  let askBusy = $state(false);
  let askSaid = $state("");
  let askError = $state("");

  const subject = $derived({ kind: "build" as const, id: b.key });
  const offered = (kind: string): boolean => !b.actions || !!offer(b, kind);
  const done = (r: Record<string, unknown> | null, words: string): string => {
    stale("api/");
    return typeof r?.say === "string" ? r.say : words;
  };

  const running = $derived(["running", "queued"].includes(b.status) && !b.control?.what);
  const stopKind = $derived(offer(b, "stop_round") ? "stop_round" : offer(b, "stop") ? "stop" : null);
  const canAsk = $derived(!running && offered("revise") && !["closed", "public"].includes(b.status));
  const canPublish = $derived(said === "Ready: publish?" && offered("publish"));
  const publicKind = $derived(offer(b, "make_public") ? "make_public" : "publish");
  const canPublic = $derived(published(b) && b.status !== "public" && (!b.actions || !!offer(b, publicKind)));

  async function stop(): Promise<string> {
    const r = stopKind ? await act(stopKind, subject) : await post("api/stop", { dir: b.dir });
    return done(r, "Stopping. The page says so once it has stopped.");
  }
  async function publish(): Promise<string> {
    return done(await act("publish", subject), "Publishing to a private repository under your account.");
  }
  // offered by the server only: Resume continues a round you stopped; Build again a held build
  const canResume = $derived(!running && !!offer(b, "resume_build"));
  const canRetry = $derived(!running && !canResume && !!offer(b, "retry") && !published(b));
  async function resume(): Promise<string> {
    return done(await act("resume_build", subject), "Queued: it continues where it stopped when a build slot is free.");
  }
  async function retry(): Promise<string> {
    return done(await act("retry", subject), "Queued: it builds again when a build slot is free.");
  }
  async function makePublic(): Promise<string> {
    return done(await act(publicKind, subject), "Making it public.");
  }
  async function ask(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    const t = text.trim();
    if (!t || askBusy) return;
    askBusy = true;
    askError = "";
    try {
      askSaid = done(await act("revise", subject, { instructions: t }), "Asked. A new build round makes the change.");
      text = "";
      asking = false;
    } catch (x) {
      askError = (x as Error).message;
    } finally {
      askBusy = false;
    }
  }
</script>

<div class="controls" id="build-controls">
  {#if canPublish}
    <Press id="publish" label="Publish" primary run={publish} confirm="Create a private repository under your GitHub account and push this build to it? Its own CI then runs there, on your account's GitHub Actions minutes." />
  {/if}
  {#if canPublic}
    <Press id="make-public" label="Make public" run={makePublic} confirm="Make the repository public? Anyone can then read it." yes="Make public" />
  {/if}
  {#if running}
    <Press id="stop" label="Stop" danger run={stop} confirm="Stop this build? Its workers are killed; nothing else changes." />
  {/if}
  {#if canResume}
    <Press id="resume" label="Resume" primary={!canPublish} run={resume} />
  {/if}
  {#if canRetry}
    <Press id="retry" label="Build again" run={retry} confirm="Build it again? It spends again, up to the build's cap." yes="Build again" />
  {/if}
  {#if canAsk}
    <button type="button" class="btn" id="ask-changes" aria-expanded={asking} onclick={() => (asking = !asking)}>Ask for changes</button>
  {/if}
  {#if askSaid}<span class="said" role="status">{askSaid}</span>{/if}
</div>
{#if asking}
  <form class="askbox" id="ask" onsubmit={ask}>
    <label for="ask-text">What should change?</label>
    <textarea id="ask-text" rows="4" bind:value={text} placeholder="The search should also match words inside code blocks"></textarea>
    {#if b.revise_left != null}<p class="muted small" id="ask-left">{b.revise_left > 0 ? "It spends from what the build's cap has left: $" + b.revise_left.toFixed(2) + "." : "The build's cap is spent: raise it in Settings › Spend first."}</p>{/if}
    {#if askError}<p class="err" role="alert">{askError}</p>{/if}
    <div class="actions">
      <button type="submit" class="btn primary" disabled={askBusy || !text.trim()}>{askBusy ? "Sending…" : "Send"}</button>
      <button type="button" class="btn quiet" onclick={() => (asking = false)}>Cancel</button>
    </div>
  </form>
{/if}
