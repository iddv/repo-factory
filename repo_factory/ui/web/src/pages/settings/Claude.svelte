<script lang="ts">
  // Settings › Claude: what builds sign in to Claude with (api/claude/login: words only, never a
  // token). Set up keeps the token `claude setup-token` prints: a password field, read once at
  // the press and cleared then.
  import { act } from "../../api/client";
  import { read, stale } from "../../lib/reads.svelte";
  import Press from "../../ui/Press.svelte";

  interface Login {
    on: "login" | "signin" | "api_key" | null;
    words: string;
    line: string;
    stored: boolean;
    loose?: boolean;
    command: string;
    hold: string;
  }
  const LOGIN = "api/claude/login";
  const got = $derived(read<Login>(LOGIN, 15000));
  const x = $derived(got.data);
  const word = $derived(!x ? "…" : x.loose ? "Fix" : x.on === null ? "Not set" : x.on === "api_key" ? "API key" : "Your plan");
  const tone = $derived(!x ? "grey" : x.on === null || x.loose ? "red" : x.on === "api_key" ? "amber" : "green");

  let open = $state(false);
  let field = $state<HTMLInputElement>();
  async function keep(): Promise<string> {
    const token = field?.value.trim() || "";
    if (field) field.value = "";
    if (!token) throw new Error("Paste the token first.");
    const r = await act("claude_login_keep", { kind: "loop", id: null }, { token });
    open = false;
    stale(LOGIN);
    return typeof r.say === "string" ? r.say : "Kept.";
  }
  async function forget(): Promise<string> {
    const r = await act("claude_login_forget", { kind: "loop", id: null });
    stale(LOGIN);
    return typeof r.say === "string" ? r.say : "Forgotten.";
  }
</script>

<h2>Claude</h2>
<p class="muted">Builds run Claude Code. They use your Claude plan when it is set up here, else an API key, which is billed at API prices.</p>
{#if got.error && !x}<p class="err" role="alert">{got.error}</p>{/if}
<div class="setrow" id="claude-login">
  <div><span class={"pill t-" + tone}>{word}</span> <strong>{x ? x.words : "Reading…"}</strong></div>
  {#if x}<p class="muted">{x.loose ? "The kept login file can be read by other users, so it is not used: Replace it." : x.on === null ? x.hold : x.line}</p>{/if}
  <div class="actions">
    {#if !open}<button type="button" class={"btn" + (x && x.on === null ? " primary" : "")} id="claude-setup" onclick={() => (open = true)}>{x?.stored ? "Replace" : "Set up"}</button>{/if}
    {#if x?.stored}<Press id="claude-forget" label="Forget" run={forget} confirm="Forget the kept Claude login? Builds are held until it is set up again, unless another login works." />{/if}
  </div>
  {#if open}
    <div class="form" id="claude-login-form">
      <p class="small">Run this in a terminal on this machine, sign in, then paste the token it prints. It is kept in the factory's home and never shown again.</p>
      <pre class="cmd">{x?.command || "claude setup-token"}</pre>
      <label for="claude-token">The token</label>
      <input id="claude-token" type="password" autocomplete="off" spellcheck="false" bind:this={field} />
      <div class="actions">
        <Press id="claude-keep" label="Keep" primary run={keep} />
        <button type="button" class="btn quiet" onclick={() => (open = false)}>Cancel</button>
      </div>
    </div>
  {/if}
</div>
