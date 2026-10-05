import { vi } from "vitest";

/**
 * jsdom does no layout, so a windowed list sees a 0px viewport and renders nothing.
 * Give every element a fixed box. Call from `beforeEach` and `vi.restoreAllMocks()` after.
 */
export function mockElementSize(width = 800, height = 600): void {
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(height);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(width);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(height);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height, toJSON: () => ({}),
  });
}

/** Make `(min-width: 1024px)` match (desktop layout) or not (strip + drawer). */
export function mockViewport(desktop: boolean): void {
  window.matchMedia = ((query: string) => ({
    matches: desktop && query.includes("min-width"),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
