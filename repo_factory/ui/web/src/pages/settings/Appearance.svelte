<script lang="ts">
  // Settings › Appearance: light, dark, or your device's; kept in this browser only.
  import { setTheme, themeNow, themeWords, type Theme } from "../../lib/theme";

  let t = $state<Theme>(themeNow());
  const CHOICES: [Theme, string][] = [
    ["light", "Light"],
    ["dark", "Dark"],
    ["auto", "Your device's"],
  ];
  function pick(v: Theme): void {
    t = v;
    setTheme(v);
  }
</script>

<h2>Appearance</h2>
<fieldset class="choices" id="theme">
  <legend class="muted">Kept in this browser.</legend>
  {#each CHOICES as [v, word] (v)}
    <label><input type="radio" name="theme" value={v} checked={t === v} onchange={() => pick(v)} /> {word}</label>
  {/each}
</fieldset>
<p class="muted small" role="status">Now: {themeWords(t)}.</p>
