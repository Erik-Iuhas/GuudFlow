import { describe, expect, it } from "vitest";
import { parseWorkingContext } from "../model";

describe("parseWorkingContext", () => {
  it("reads thousands of tokens, blank is the default, out of range is invalid", () => {
    expect(parseWorkingContext("", 1_000_000)).toBeUndefined();
    expect(parseWorkingContext("200", 1_000_000)).toBe(200_000);
    expect(parseWorkingContext("300", 200_000)).toBeNull();
    expect(parseWorkingContext("0", 200_000)).toBeNull();
    expect(parseWorkingContext("1.5", 200_000)).toBeNull();
  });
});
