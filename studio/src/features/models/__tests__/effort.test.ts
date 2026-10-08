import { describe, expect, it } from "vitest";
import { formatEffortValues, parseEffortValues, toggleEffortLevel } from "../model";
import type { ModelWire } from "../api";

const base: ModelWire = { id: "m", provider: "anthropic", tier: "solid" } as ModelWire;

describe("toggleEffortLevel", () => {
  it("keeps levels in order and drops the list when empty", () => {
    const one = toggleEffortLevel(base, "max");
    const two = toggleEffortLevel(one, "low");
    expect(two.effort_levels).toEqual(["low", "max"]);
    expect(toggleEffortLevel(toggleEffortLevel(two, "low"), "max").effort_levels).toBeUndefined();
  });

  it("clears the default when its level is switched off", () => {
    const m: ModelWire = { ...base, effort_levels: ["low", "high"], default_effort: "high" };
    expect(toggleEffortLevel(m, "high").default_effort).toBeUndefined();
    expect(toggleEffortLevel(m, "low").default_effort).toBe("high");
  });
});

describe("parseEffortValues", () => {
  it("reads level=word pairs for the model's levels", () => {
    expect(parseEffortValues("low=none, high=default", ["low", "high"])).toEqual({ low: "none", high: "default" });
    expect(parseEffortValues("", ["low"])).toEqual({});
    expect(parseEffortValues("max=max", ["low"])).toBeNull();
    expect(parseEffortValues("low=", ["low"])).toBeNull();
    expect(formatEffortValues({ low: "none" })).toBe("low=none");
  });
});
