import { describe, expect, it } from "vitest";
import { prependedCount } from "./useAutoScroll";

describe("prependedCount", () => {
  it("is 0 when nothing arrived", () => {
    expect(prependedCount([3, 2, 1], [3, 2, 1])).toBe(0);
  });

  it("counts the items ahead of the old first one", () => {
    expect(prependedCount([3, 2, 1], [5, 4, 3, 2, 1])).toBe(2);
  });

  it("ignores items that expired off the end", () => {
    expect(prependedCount([3, 2, 1], [4, 3])).toBe(1);
    expect(prependedCount([3, 2, 1], [3])).toBe(0);
  });

  it("is 0 for the first load and for an emptied list", () => {
    expect(prependedCount([], [2, 1])).toBe(0);
    expect(prependedCount([2, 1], [])).toBe(0);
  });

  it("is null when the whole list was replaced", () => {
    expect(prependedCount([3, 2, 1], [9, 8])).toBeNull();
  });
});
