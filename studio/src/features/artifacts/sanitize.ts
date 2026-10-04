import createDOMPurify from "dompurify";

// Agent-made SVG is untrusted (plan section 5.10). It is sanitized here and then shown
// through a blob URL in an <img>, never as inline markup. An <img> never runs script or loads
// subresources, so this is the second layer; the first is that nothing unsanitized is shown.

const purify = createDOMPurify(window);

const FORBIDDEN_TAGS = ["script", "foreignObject", "set", "animate", "iframe", "embed", "object"];
/** Only inline raster data URIs on <image> may carry a scheme (matplotlib embeds PNGs so). */
const DATA_IMAGE = /^data:image\/(png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]*$/i;
const EXTERNAL_URL_FN = /url\(\s*(?!['"]?#)/i;

purify.addHook("uponSanitizeElement", (node, data) => {
  if (data.tagName !== "style") return;
  // Keep inline <style> (matplotlib uses one) unless it can reach out: @import, url(...).
  const css = node.textContent ?? "";
  if (/@import/i.test(css) || EXTERNAL_URL_FN.test(css)) node.parentNode?.removeChild(node);
});

purify.addHook("afterSanitizeAttributes", (node) => {
  for (const attr of Array.from(node.attributes)) {
    const name = attr.name.toLowerCase();
    const value = attr.value.trim();
    const isHref = name === "href" || name === "xlink:href";
    if (isHref && !value.startsWith("#")) {
      const okDataImage = node.nodeName.toLowerCase() === "image" && DATA_IMAGE.test(value);
      if (!okDataImage) node.removeAttribute(attr.name);
    } else if (EXTERNAL_URL_FN.test(value)) {
      node.removeAttribute(attr.name);
    }
  }
});

/**
 * Sanitize SVG markup: no script, foreignObject, event handlers, or external references.
 * Returns well-formed standalone SVG (namespaces declared), or null when the input holds no
 * usable `<svg>` root.
 */
export function sanitizeSvg(source: string): string | null {
  // With RETURN_DOM, DOMPurify returns the sanitized <body> element (typed as Node).
  const body = purify.sanitize(source, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: FORBIDDEN_TAGS,
    RETURN_DOM: true,
  }) as HTMLElement;
  const root = body.firstElementChild;
  if (!root || root.nodeName.toLowerCase() !== "svg") return null;
  return new XMLSerializer().serializeToString(root);
}
