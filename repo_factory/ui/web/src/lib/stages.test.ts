import { describe, expect, it } from "vitest";
import type { RunStage } from "../api/types.gen";
import { cells, SIX, SIX_NAME, sixName } from "./stages";

const st = (stage: string, status: string, over: Partial<RunStage> = {}): RunStage => ({
  from: null, stage, status, why: null, reason: null, usd: null, wall_s: null, tokens: null, runs: 1, started: null, ...over,
});

describe("the six stages", () => {
  it("are Brief, Plan, Build, Check, Fix, Publish", () => {
    expect(SIX.map((k) => SIX_NAME[k])).toEqual(["Brief", "Plan", "Build", "Check", "Fix", "Publish"]);
    expect(sixName("scope")).toBe("Plan");
    expect(sixName("readiness")).toBe("Check");
    expect(sixName("reverify")).toBe("Fix");
  });
  it("sum each one's time and cost over the engine stages under it", () => {
    const cs = cells(
      [st("scope", "done", { wall_s: 60, usd: 0.5, started: 10 }), st("build", "done", { wall_s: 600, usd: 4 }), st("harden", "done", { wall_s: 300, usd: 1.25 }),
        st("verify", "running", { usd: 0.1 }), st("repair", "pending")],
      { briefed: true },
    );
    const by = Object.fromEntries(cs.map((c) => [c.k, c]));
    expect(by.brief!.st).toBe("done");
    expect(by.plan).toMatchObject({ st: "done", wall_s: 60, usd: 0.5, started: 10, stages: ["scope"] });
    expect(by.build).toMatchObject({ st: "done", wall_s: 900, usd: 5.25 });
    expect(by.check).toMatchObject({ st: "running", wall_s: null });
    expect(by.fix!.st).toBe("not_run");
    expect(by.publish!.st).toBe("not_run");
  });
  it("show a task being briefed, and a build waiting for the press", () => {
    expect(cells([], { briefed: false, briefing: true })[0]!.st).toBe("running");
    const done = cells([st("deliver", "done")], { briefed: true, publish: "waiting" });
    expect(done[5]!.st).toBe("waiting");
  });
  it("say a stage that failed failed, with why", () => {
    const cs = cells([st("verify", "done"), st("readiness", "failed", { why: "the server did not start" })], { briefed: true });
    expect(cs[3]).toMatchObject({ st: "failed", why: "the server did not start" });
  });
});
