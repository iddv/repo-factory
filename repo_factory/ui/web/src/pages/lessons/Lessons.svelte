<script lang="ts">
  // Lessons: what held earlier builds, one line each, where it came from, and on or off. A lesson
  // that is on goes into the brief of every build from the next one on; off keeps it out. A new
  // lesson waits for the person's press: it is in no brief until they switch it on. Nothing here
  // is deleted. The last post-mortems say what each held build taught, or why nothing.
  import { post } from "../../api/client";
  import type { Lessons } from "../../api/types.gen";
  import { load, read, stale } from "../../lib/reads.svelte";
  import Press from "../../ui/Press.svelte";

  const PATH = "api/lessons";
  interface Lesson {
    id: string;
    text: string;
    why?: string;
    on: boolean;
    waiting?: unknown;
    packed?: number;
    from?: { idea?: string | null; product?: string | null; round?: number | null; holds?: string[]; date?: string | null };
  }
  const got = $derived(read<Lessons>(PATH, 15000));
  const list = $derived((got.data?.lessons || []) as unknown as Lesson[]);
  let busy = $state("");
  let said = $state("");
  let error = $state("");

  /** a lesson waits for the person only when the server says exactly true (missing, null or
   *  anything else: it does not) */
  function waits(x: Lesson): boolean {
    return x.waiting === true;
  }

  /** where a lesson came from, in one line: why, the build, how often it was carried */
  function origin(x: Lesson): string {
    const f = x.from || {};
    const parts: string[] = [];
    if (x.why) parts.push(x.why);
    if (f.product) parts.push(`From ${f.product}${f.holds?.length ? ` (held: ${f.holds.join(", ")})` : ""}${f.date ? `, ${f.date}` : ""}.`);
    if (x.packed) parts.push(`In ${x.packed} brief${x.packed === 1 ? "" : "s"} so far.`);
    return parts.join(" ");
  }

  /** the press on a waiting lesson: on, so new briefs carry it, from the next one */
  async function switchOn(x: Lesson): Promise<void> {
    said = "";
    error = "";
    await post(PATH, { id: x.id, on: true });
    said = `${x.id} is on: from the next brief.`; // said under the list: the press goes away
    stale(PATH);
    await load(PATH);
  }

  async function flip(x: Lesson): Promise<void> {
    busy = x.id;
    said = "";
    error = "";
    try {
      const on = x.on !== true;
      await post(PATH, { id: x.id, on });
      said = `${x.id} is ${on ? "on" : "off"}: from the next brief.`;
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
  <p class="muted lead">When the checks hold a build, repo-factory writes down what went wrong, in one line. A new lesson waits for you: no brief carries it until you switch it on. A lesson that names a package, a host or an address, or a command-line flag is not kept. Switch off a lesson you do not want carried.</p>
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
        <li class={"lesson" + (waits(x) ? " waiting off" : x.on === true ? "" : " off")} id={"lesson-" + x.id}>
          <div class="lesson-main">
            <p class="lesson-text"><span class="muted lesson-id">{x.id}</span> {x.text}</p>
            <p class="muted small">{origin(x)}</p>
            {#if waits(x)}<p class="small lesson-waiting" id={"lesson-waiting-" + x.id}>Waiting for you: switch it on to use it in new briefs.</p>{/if}
          </div>
          {#if waits(x)}
            <Press label="Switch on" id={"lesson-on-" + x.id} primary run={() => switchOn(x)} />
          {:else}
            <label class="switch">
              <input type="checkbox" checked={x.on === true} disabled={busy === x.id} onchange={() => void flip(x)} aria-describedby={"lesson-" + x.id} />
              {x.on === true ? "On" : "Off"}
            </label>
          {/if}
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
