import "@testing-library/jest-dom";

// jsdom lacks these browser APIs, which the Radix primitives touch.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}
// React Flow reads the viewport transform through DOMMatrixReadOnly, which jsdom lacks.
if (typeof globalThis.DOMMatrixReadOnly === "undefined") {
  class DOMMatrixReadOnlyStub {
    m22 = 1;
    constructor(transform?: string) {
      const scale = /scale\(([-\d.]+)/.exec(transform ?? "");
      if (scale) this.m22 = Number(scale[1]);
    }
  }
  (globalThis as unknown as Record<string, unknown>).DOMMatrixReadOnly = DOMMatrixReadOnlyStub;
}
// React Flow measures edge labels with SVG getBBox, which jsdom lacks.
if (typeof SVGElement !== "undefined" && !("getBBox" in SVGElement.prototype)) {
  (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () =>
    ({ x: 0, y: 0, width: 40, height: 14 }) as DOMRect;
}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
