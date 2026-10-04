import { describe, expect, it } from "vitest";
import { sanitizeSvg } from "../sanitize";
import { MALICIOUS_SVG } from "../testFixtures";

describe("sanitizeSvg", () => {
  it("strips script, event handlers, foreignObject and external references", () => {
    const out = sanitizeSvg(MALICIOUS_SVG);
    expect(out).not.toBeNull();
    const text = out as string;
    expect(text).not.toMatch(/<script/i);
    expect(text).not.toMatch(/onload|onclick|onerror/i);
    expect(text).not.toMatch(/foreignObject/i);
    expect(text).not.toMatch(/<iframe/i);
    expect(text).not.toMatch(/evil\.test/);
    expect(text).not.toMatch(/javascript:/i);
    expect(text).not.toMatch(/<set|<animate/i);
    // The harmless drawing survives.
    expect(text).toContain("<circle");
    expect(text).toContain('fill="#336699"');
  });

  it("keeps gradient references, style attributes and inline raster data URIs", () => {
    const out = sanitizeSvg(
      '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">' +
        '<defs><linearGradient id="g"><stop offset="0"/></linearGradient><circle id="c" r="2"/></defs>' +
        '<use xlink:href="#c"/><rect fill="url(#g)" style="stroke: red"/>' +
        '<image xlink:href="data:image/png;base64,AAAA"/></svg>',
    ) as string;
    expect(out).not.toContain("<use"); // DOMPurify's svg profile drops <use> altogether
    expect(out).toContain("url(#g)");
    expect(out).toContain("stroke: red");
    expect(out).toContain("data:image/png;base64,AAAA");
  });

  it("refuses a data:image/svg+xml image and an external style url", () => {
    const out = sanitizeSvg(
      '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/svg+xml;base64,AAAA"/>' +
        '<rect style="fill:url(https://evil.test/a)"/></svg>',
    ) as string;
    expect(out).not.toMatch(/svg\+xml;base64/);
    expect(out).not.toMatch(/evil\.test/);
  });

  it("drops a style element that imports or fetches", () => {
    const out = sanitizeSvg(
      '<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(https://evil.test/x.css);</style><rect/></svg>',
    ) as string;
    expect(out).not.toMatch(/evil\.test|@import/);
  });

  it("returns well-formed standalone XML", () => {
    const out = sanitizeSvg("<svg><circle r='1'/></svg>") as string;
    const doc = new DOMParser().parseFromString(out, "image/svg+xml");
    expect(doc.getElementsByTagName("parsererror")).toHaveLength(0);
    expect(doc.documentElement.namespaceURI).toBe("http://www.w3.org/2000/svg");
  });

  it("returns null when there is no svg root", () => {
    expect(sanitizeSvg("<p>hello</p>")).toBeNull();
    expect(sanitizeSvg("")).toBeNull();
  });
});
