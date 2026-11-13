// The shell, mounted: the nav, the notice, Builds empty, New task, a build's page and Settings,
// against a fake server (fetch stubbed; no network, no model).
import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.svelte";
import { NOTICE, NOTICE_KEY } from "./lib/notice";
import { clearReads } from "./lib/reads.svelte";
import { pending } from "./lib/tasks.svelte";

type Answer = { status?: number; body: unknown };
let routes: Record<string, (body: unknown) => Answer>;
let posts: [string, unknown][];
let app: ReturnType<typeof mount> | null = null;

function serve(): void {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url);
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    if (init?.method === "POST") posts.push([path, body]);
    const key = (init?.method === "POST" ? "POST " : "") + path.split("?")[0];
    const r = routes[key]?.(body) ?? { status: 404, body: { error: "not found" } };
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { "content-type": "application/json" } });
  }));
}
async function settle(ms = 30): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((res) => setTimeout(res, ms / 5));
    flushSync();
  }
}
async function until(f: () => boolean, ms = 3000): Promise<void> {
  const end = Date.now() + ms;
  while (!f()) {
    if (Date.now() > end) throw new Error("never true: " + f.toString());
    await settle(10);
  }
}
async function open(hash: string): Promise<void> {
  location.hash = hash;
  if (!app) app = mount(App, { target: document.body, props: { live: false } });
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  await settle();
}
const text = (): string => document.body.textContent || "";

beforeEach(() => {
  localStorage.clear();
  clearReads();
  pending.rows = [];
  posts = [];
  routes = {
    "api/state": () => ({ body: { builds: [], ideas: [] } }),
  };
  serve();
});
afterEach(() => {
  if (app) void unmount(app);
  app = null;
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe("the shell", () => {
  it("has Builds and Settings in its nav, and nothing else", async () => {
    await open("#/builds");
    const items = [...document.querySelectorAll("nav.nav .item")].map((a) => a.textContent);
    expect(items).toEqual(["Builds", "Settings"]);
    expect(document.querySelector('nav.nav .item[aria-current="page"]')?.textContent).toBe("Builds");
    expect(text()).not.toMatch(/Projects|Ideas|Today/);
  });

  it("shows the notice once, until it is dismissed", async () => {
    await open("#/builds");
    expect(document.querySelector("#notice")?.textContent).toContain(NOTICE);
    expect(NOTICE).toBe("repo-factory is an experiment. It runs code a model wrote and can publish under your GitHub account. It costs real money or plan usage: about $10 and an hour a build on our record. Its own checks are not proof. Read what it builds.");
    (document.querySelector("#notice-dismiss") as HTMLButtonElement).click();
    flushSync();
    expect(document.querySelector("#notice")).toBeNull();
    expect(localStorage.getItem(NOTICE_KEY)).toBe("1");
    void unmount(app!);
    app = null;
    document.body.replaceChildren();
    await open("#/builds");
    expect(document.querySelector("#notice")).toBeNull();
  });

  it("says Builds is empty in one line, with one button: New task", async () => {
    await open("#/builds");
    await until(() => !!document.querySelector("#builds-empty"));
    const empty = document.querySelector("#builds-empty")!;
    expect(empty.querySelectorAll("p")).toHaveLength(1);
    const buttons = [...document.querySelectorAll("main .btn")].filter((b) => !b.closest("#notice"));
    expect(buttons.map((b) => b.textContent)).toEqual(["New task"]);
    expect((buttons[0] as HTMLAnchorElement).getAttribute("href")).toBe("#/builds/new");
  });

  it("sends a typed task with Build, and its row says Brief at once", async () => {
    let state = { builds: [] as unknown[], ideas: [] as unknown[] };
    routes["api/state"] = () => ({ body: state });
    routes["POST api/new"] = (b) => {
      state = { builds: [], ideas: [{ id: "i9", state: "new", title: (b as { text: string }).text.split("\n")[0], build: null, created: 5, updated: 5 }] };
      return { body: { ok: true, id: "i9" } };
    };
    await open("#/builds/new");
    await until(() => !!document.querySelector("#task-text"));
    const ta = document.querySelector("#task-text") as HTMLTextAreaElement;
    const build = document.querySelector("#task-build") as HTMLButtonElement;
    expect(build.disabled).toBe(true);
    ta.value = "A command-line tool that turns a folder of Markdown notes into one searchable HTML page";
    ta.dispatchEvent(new Event("input"));
    flushSync();
    expect(build.disabled).toBe(false);
    build.click();
    flushSync();
    // at once: the row is on the list before the server answers
    expect(document.querySelector("#build-rows .row [data-status]")?.textContent).toBe("Brief");
    await until(() => posts.length === 1);
    expect(posts[0]).toEqual(["api/new", { text: "A command-line tool that turns a folder of Markdown notes into one searchable HTML page" }]);
    await until(() => location.hash === "#/builds/task%3Ai9");
    await settle();
    const rows = [...document.querySelectorAll("#build-rows .row")];
    expect(rows).toHaveLength(1);
    expect(rows[0]!.textContent).toContain("Brief");
    expect(rows[0]!.textContent).toContain("A command-line tool");
    expect(document.querySelector("#stages")?.textContent).toMatch(/Brief.*running.*Plan.*Build.*Check.*Fix.*Publish/s);
  });

  it("a task's page follows it into its build once the build has a row", async () => {
    routes["api/state"] = () => ({ body: { builds: [{ key: "b1", dir: "/t/wordtally", status: "running", title: "Wordtally", idea: "i9", builds: { latest: { n: 1, stage: "build", outcome: null } }, actions: [] }], ideas: [] } });
    await open("#/builds/task:i9");
    await until(() => (document.querySelector("main")?.textContent ?? "").includes("Wordtally"));
    expect(document.querySelector("main")?.textContent).not.toContain("its brief is being written");
  });

  it("says so when the same task is already on the list, and stays on New task", async () => {
    routes["api/state"] = () => ({ body: { builds: [], ideas: [] } });
    routes["POST api/new"] = () => ({ body: { id: "i3", created: false, state: "ready" } });
    await open("#/builds/new");
    await until(() => !!document.querySelector("#task-text"));
    const ta = document.querySelector("#task-text") as HTMLTextAreaElement;
    ta.value = "a command-line tool that counts the words in text files";
    ta.dispatchEvent(new Event("input"));
    flushSync();
    (document.querySelector("#task-build") as HTMLButtonElement).click();
    await until(() => (document.querySelector("main")?.textContent ?? "").includes("already on the list"));
    expect(location.hash).toBe("#/builds/new");
    expect(document.querySelector("main")?.textContent).toContain("(ready)");
  });

  it("reads a task from a file into the box", async () => {
    await open("#/builds/new");
    await until(() => !!document.querySelector("#task-file"));
    const input = document.querySelector("#task-file") as HTMLInputElement;
    const f = new File(["# Notes to HTML\nOne page from a folder of notes."], "notes.md", { type: "text/markdown" });
    Object.defineProperty(input, "files", { value: [f] });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await until(() => (document.querySelector("#task-text") as HTMLTextAreaElement).value.startsWith("# Notes to HTML"));
    expect(text()).toContain("The text of notes.md is in the box");
  });

  it("draws a build's six stages, and Publish while it waits for the press", async () => {
    const b = { key: "notes", dir: "/home/user/.repo-factory/tasks/notes", status: "ready", title: "Notes to HTML", desc: "", updated: 9, idea: "i1", repo: null, url: null,
      gate: { verdict: "ready", reasons: [], round: 1, checks: { tests: { ok: true }, security: { ok: true } } },
      actions: [{ kind: "publish", enabled: true, title: "Publish", says: "", click: "confirm", key: null, fields: [], why: null, page: true }] };
    routes["api/state"] = () => ({ body: { builds: [b], ideas: [] } });
    const run = { n: 1, outcome: "ready", stage: null };
    routes["api/build/runs"] = () => ({ body: { key: "notes", runs: [run] } });
    routes["api/run"] = () => ({ body: { key: "notes", run, repairs: [], v5: null, of: 1, neighbours: { prev: null, next: null },
      stages: [{ stage: "scope", status: "done", wall_s: 120, usd: 0.4, reason: null, why: null, from: null, tokens: null, runs: 1, started: 1 }] } });
    routes["POST api/act"] = () => ({ body: { ok: true, say: "Publishing." } });
    await open("#/builds/notes");
    await until(() => !!document.querySelector("#publish"));
    expect(document.querySelector("#s-now")?.textContent).toContain("Ready: publish?");
    expect(document.querySelectorAll("#stages .stage")).toHaveLength(6);
    expect(document.querySelector('#stages [data-stage="plan"]')?.textContent).toMatch(/2 min.*\$0\.40.*Open the log/s);
    expect(document.querySelector('#stages [data-stage="publish"]')?.textContent).toContain("waiting for you");
    expect(document.querySelector("#checks-passed")?.textContent).toContain("Its own tests pass");
    expect(document.querySelector("#make-public")).toBeNull();
    (document.querySelector("#publish") as HTMLButtonElement).click();
    flushSync();
    expect(posts).toEqual([]); // it asks first
    (document.querySelector(".press .ask .btn.primary") as HTMLButtonElement).click();
    await until(() => posts.length === 1);
    expect(posts[0]).toEqual(["api/act", { kind: "publish", subject: { kind: "build", id: "notes" }, fields: {}, origin: "ui", confirm: true }]);
  });

  it("offers Resume on a build you stopped, and Build again on a held one", async () => {
    const offer = (kind: string) => ({ kind, enabled: true, title: kind, says: "", click: "click", key: null, fields: [], why: null, page: true });
    const stopped = { key: "notes", dir: "/home/user/.repo-factory/tasks/notes", status: "paused", title: "Notes", desc: "", updated: 9, idea: "i1", repo: null, url: null,
      gate: null, actions: [offer("resume_build"), offer("retry")] };
    routes["api/state"] = () => ({ body: { builds: [stopped], ideas: [] } });
    routes["api/build/runs"] = () => ({ body: { key: "notes", runs: [] } });
    routes["POST api/act"] = () => ({ body: { ok: true } });
    await open("#/builds/notes");
    await until(() => !!document.querySelector("#resume"));
    expect(document.querySelector("#retry")).toBeNull(); // Resume first: it keeps the work done
    (document.querySelector("#resume") as HTMLButtonElement).click();
    await until(() => posts.length === 1);
    expect(posts[0]![1]).toMatchObject({ kind: "resume_build", subject: { kind: "build", id: "notes" } });
    routes["api/state"] = () => ({ body: { builds: [{ ...stopped, status: "held", actions: [offer("retry")], gate: { verdict: "held", reasons: ["tests: 1 fails"], round: 1, checks: {} } }], ideas: [] } });
    await open("#/builds");
    await open("#/builds/notes");
    await until(() => !!document.querySelector("#retry"));
    expect(document.querySelector("#resume")).toBeNull();
  });

  it("opens each settings page", async () => {
    routes["api/claude/login"] = () => ({ body: { on: null, words: "not set", line: "", stored: false, command: "claude setup-token", hold: "Builds are held until a login is set up." } });
    routes["api/github/user"] = () => ({ body: { connected: true, login: "someone", line: "connected as someone" } });
    routes["api/loop/rules"] = () => ({ body: { rules: { "build.flow": { value: "v5", default: "v5" }, "publish.when": { value: null, default: "ask" }, "publish.approval": { value: null, default: "required" } } } });
    routes["api/models"] = () => ({ body: { catalog: [{ id: "m1", name: "Model one" }], roles: [{ role: "build", name: "Build", does: "writes the code", kind: "build", model: null, default: "m1", set: false }] } });
    routes["api/spend/caps"] = () => ({ body: { daily_usd: 50, build_max_usd: 25 } });
    const want: [string, RegExp][] = [
      ["claude", /Not set.*Builds are held/s],
      ["github", /Signed in.*as someone/s],
      ["building", /lean \(unproven\).*Model one/s],
      ["publishing", /Ask me first.*private repository/s],
      ["spend", /A build at most/],
      ["appearance", /Light.*Dark/s],
    ];
    for (const [page, re] of want) {
      await open(`#/settings/${page}`);
      await until(() => re.test(document.querySelector(".setpage")?.textContent || ""));
      expect(document.querySelector(`.subnav a[aria-current="page"]`)?.getAttribute("href")).toBe(`#/settings/${page}`);
    }
    expect((document.querySelector("#cap-build") as HTMLInputElement | null)?.value ?? "25").toBe("25");
  });

  it("switches to dark and back from Appearance", async () => {
    await open("#/settings/appearance");
    const dark = document.querySelector('#theme input[value="dark"]') as HTMLInputElement;
    dark.click();
    flushSync();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    (document.querySelector('#theme input[value="light"]') as HTMLInputElement).click();
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    (document.querySelector('#theme input[value="auto"]') as HTMLInputElement).click();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("says a page that does not exist does not", async () => {
    await open("#/ideas");
    expect(text()).toContain("No such page");
  });
});
