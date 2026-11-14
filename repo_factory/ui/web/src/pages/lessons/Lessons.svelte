<script lang="ts">
  // Lessons: what held earlier builds, one line each, where it came from, and on or off. A lesson
  // that is on goes into the brief of every build from the next one on; off keeps it out. Nothing
  // here is deleted. The last post-mortems say what each held build taught, or why nothing.
  import { post } from "../../api/client";
  import type { Lessons } from "../../api/types.gen";
  import { load, read, stale } from "../../lib/reads.svelte";

  const PATH = "api/lessons";
  interface Lesson {
    id: string;
    text: string;
    why?: string;
    on: boolean;
    packed?: number;
    from?: { idea?: string | null; product?: string | null; round?: number | null; holds?: string[]; date?: string | null };
  }
  const got = $derived(read<Lessons>(PATH, 15000));
  const list = $derived((got.data?.lessons || []) as unknown as Lesson[]);
  let busy = $state("");
  let said = $state("");
  let error = $state("");

  /** where a lesson came from, in one line: why, the build, how often it was carried */
  function origin(x: Lesson): string {
    const f = x.from || {};
    const parts: string[] = [];
    if (x.why) parts.push(x.why);
    if (f.product) parts.push(`From ${f.product}${f.holds?.length ? ` (held: ${f.holds.join(", ")})` : ""}${f.date ? `, ${f.date}` : ""}.`);
    if (x.packed) parts.push(`In ${x.packed} brief${x.packed === 1 ? "" : "s"} so far.`);
    return parts.join(" ");
  }

  async function flip(x: Lesson): Promise<void> {
    busy = x.id;
    said = "";
    error = "";
    try {
      await post(PATH, { id: x.id, on: !x.on });
      said = `${x.id} is ${x.on ? "off" : "on"}: from the next brief.`;
      stale(PATH);
      await load(PATH);
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = "";
    }
  }
</script>

<section class="page lessons" aria-labelledby="lessons-h">
  <h1 id="lessons-h">Lessons</h1>
  <p class="muted lead">When the checks hold a build, repo-factory writes down what went wrong, in one line, and the next builds' briefs carry it. Switch off a lesson you do not want carried.</p>
  {#if got.data && !got.data.enabled}<p class="muted" id="lessons-off">Lessons are off (<code>lessons.enabled: false</code> in loop.yaml): no lesson is written or carried.</p>{/if}
  {#if got.error && !got.data}
    <p class="err" role="alert">The lessons could not be read: {got.error}</p>
  {:else if !got.data}
    <p class="muted">Reading the lessons…</p>
  {:else if got.data.error}
    <p class="err" role="alert" id="lessons-error">{got.data.error}</p>
  {:else if !list.length}
    <div class="empty" id="lessons-empty">
      <p>No lessons yet. When the checks hold a build for something its builder could have done better, the lesson appears here.</p>
    </div>
  {:else}
    <ul class="lesson-list" id="lessons">
      {#each list as x (x.id)}
        <li class={"lesson" + (x.on ? "" : " off")} id={"lesson-" + x.id}>
          <div class="lesson-main">
            <p class="lesson-text"><span class="muted lesson-id">{x.id}</span> {x.text}</p>
            <p class="muted small">{origin(x)}</p>
          </div>
          <label class="switch">
            <input type="checkbox" checked={x.on} disabled={busy === x.id} onchange={() => void flip(x)} aria-describedby={"lesson-" + x.id} />
            {x.on ? "On" : "Off"}
          </label>
        </li>
      {/each}
    </ul>
  {/if}
  {#if said}<p class="said" role="status">{said}</p>{/if}
  {#if error}<p class="err" role="alert">{error}</p>{/if}
  {#if got.data?.postmortems?.length}
    <section class="sec" aria-labelledby="pm-h">
      <h2 id="pm-h">Held builds looked at</h2>
      <ul class="pms small" id="postmortems">
        {#each got.data.postmortems as p, i (i)}
          <li><span class="muted">{p.date}</span> {p.result}</li>
        {/each}
      </ul>
    </section>
  {/if}
</section>
