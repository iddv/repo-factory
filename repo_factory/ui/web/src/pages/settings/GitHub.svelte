<script lang="ts">
  // Settings › GitHub: whether the factory can publish, and as whom: the GitHub CLI's login on
  // this machine (api/github/user: words only, never a token). Signing in is done in a terminal.
  import { getJson } from "../../api/client";
  import { put, read } from "../../lib/reads.svelte";
  import Press from "../../ui/Press.svelte";

  interface Gh {
    connected: boolean;
    login: string | null;
    line?: string;
    needs_connect?: boolean;
  }
  const USER = "api/github/user";
  const got = $derived(read<Gh>(USER, 30000));
  const x = $derived(got.data);
  const ok = $derived(!!x && x.connected && !x.needs_connect);
  async function check(): Promise<string> {
    put(USER, await getJson<Gh>(`${USER}?check=1`));
    return "Checked.";
  }
</script>

<h2>GitHub</h2>
<p class="muted">A build publishes under your GitHub account, into a private repository, and only when you press Publish.</p>
{#if got.error && !x}<p class="err" role="alert">{got.error}</p>{/if}
<div class="setrow" id="github-login">
  <div><span class={"pill t-" + (!x ? "grey" : ok ? "green" : "red")}>{!x ? "…" : ok ? "Signed in" : "Not signed in"}</span> <strong>{x ? (ok ? `as ${x.login || "your account"}` : "Publishing is not possible yet") : "Reading…"}</strong></div>
  {#if x && x.line}<p class="muted">{x.line}</p>{/if}
  {#if x && !ok}
    <p class="small">Sign in with the GitHub CLI in a terminal on this machine, then check again:</p>
    <pre class="cmd">gh auth login</pre>
  {/if}
  <div class="actions"><Press id="github-check" label="Check again" run={check} /></div>
</div>
