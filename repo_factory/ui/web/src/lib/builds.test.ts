import { describe, expect, it } from "vitest";
import { buildSaid, checkWord, items, plainReason, taskSaid, type Build, type State } from "./builds";

const build =(over: Partial<Build> = {}): Build =>
  ({
    key: "notes-to-html",
    dir: "/home/user/.repo-factory/tasks/notes-to-html",
    status: "running",
    cant_revise: null,
    state: "running",
    title: "Notes to HTML",
    desc: "One searchable page from a folder of notes",
    updated: 1000,
    live: true,
    idea: "i1",
    idea_state: "building",
    cost_usd: null,
    repo: null,
    url: null,
    gate: null,
    ...over,
  }) as Build;

describe("a build's status in words", () => {
  it("says the stage a running build is in, by the six names", () => {
    const run = { n: 1, stage: "verify", outcome: "running" };
    expect(buildSaid(build({ builds: { n: 1, latest: run } as never }))).toEqual({ word: "Check", tone: "blue" });
    expect(buildSaid(build({ builds: { n: 1, latest: { ...run, stage: "harden" } } as never })).word).toBe("Build");
  });
  it("waits at Ready: publish? until it is published, then says where", () => {
    expect(buildSaid(build({ status: "ready" })).word).toBe("Ready: publish?");
    expect(buildSaid(build({ status: "ready", url: "https://github.com/someone/notes" })).word).toBe("Published privately");
    expect(buildSaid(build({ status: "public", url: "https://github.com/someone/notes" })).word).toBe("Public");
  });
  it("says a stopped build stopped, and a held one what held it", () => {
    expect(buildSaid(build({ status: "held", builds: { n: 2, latest: { n: 2, outcome: "stopped" } } as never })).word).toBe("Stopped");
    expect(buildSaid(build({ status: "held", gate: { verdict: "held", reasons: [], round: 1 } })).word).toBe("Held by the checks");
    expect(buildSaid(build({ question: { id: "q1", text: "Which port?" } })).word).toBe("Asks you");
  });
});

describe("the list", () => {
  it("lists the tasks not built yet as Brief, beside the builds, newest first", () => {
    const s: State = {
      builds: [build({ updated: 1000 })],
      ideas: [
        { id: "i1", state: "building", title: "Notes to HTML", build: "notes-to-html", created: 900, updated: 1000 },
        { id: "i2", state: "new", title: "A timer for the terminal", build: null, created: 2000, updated: 2000 },
      ],
    };
    const rows = items(s);
    expect(rows.map((r) => [r.key, r.word])).toEqual([
      ["task:i2", "Brief"],
      ["notes-to-html", "Building"],
    ]);
  });
  it("says what came of a brief that did not become a build", () => {
    expect(taskSaid({ id: "x", state: "rejected", title: "", build: null, created: null, updated: null }).word).toBe("Brief: not built");
    expect(taskSaid({ id: "x", state: "queued", title: "", build: null, created: null, updated: null }).word).toBe("Waiting for a slot");
  });
  it("is empty without a state", () => {
    expect(items(null)).toEqual([]);
    expect(items({ builds: [] })).toEqual([]);
  });
});

describe("the checks in plain words", () => {
  it("names each check and drops the key from a reason", () => {
    expect(checkWord("tests")).toBe("Its own tests pass");
    expect(checkWord("new_thing")).toBe("New thing");
    expect(plainReason("tests: 4 of 58 tests fail")).toBe("4 of 58 tests fail");
  });
});
