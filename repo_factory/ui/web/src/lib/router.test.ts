import { describe, expect, it } from "vitest";
import { href, parse } from "./router.svelte";

describe("the hash routes", () => {
  it("opens Builds by default", () => {
    expect(parse("")).toEqual({ section: "builds", id: null, q: {} });
    expect(parse("#/")).toEqual({ section: "builds", id: null, q: {} });
    expect(parse("#/builds")).toEqual({ section: "builds", id: null, q: {} });
  });
  it("reads a build, its log, New task and a settings page", () => {
    expect(parse("#/builds/notes-to-html?log=check")).toEqual({ section: "builds", id: "notes-to-html", q: { log: "check" } });
    expect(parse("#/builds/new").id).toBe("new");
    expect(parse("#/settings/spend")).toEqual({ section: "settings", id: "spend", q: {} });
    expect(parse("#/builds/task%3Aabc").id).toBe("task:abc");
  });
  it("says what is not a page", () => {
    expect(parse("#/ideas").section).toBe("missing");
    expect(parse("#/projects").section).toBe("missing");
    expect(parse("#/builds/a/b").section).toBe("missing");
  });
  it("writes the addresses it reads", () => {
    expect(href("builds")).toBe("#/builds");
    expect(href("builds", "task:abc", { log: "brief" })).toBe("#/builds/task%3Aabc?log=brief");
    expect(parse(href("builds", "task:abc", { log: "brief" }))).toEqual({ section: "builds", id: "task:abc", q: { log: "brief" } });
    expect(href("settings", "spend")).toBe("#/settings/spend");
  });
});
