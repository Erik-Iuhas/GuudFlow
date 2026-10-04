import { useEffect, useState } from "react";
import { sanitizeSvg } from "./sanitize";

/** A raster image. Lazy loaded; the server re-encoded it without metadata. */
export function ImageViewer({ src, alt, className }: { src: string; alt: string; className?: string }) {
  return <img src={src} alt={alt} loading="lazy" decoding="async" className={className} />;
}

type SvgState =
  | { status: "loading" }
  | { status: "ready"; blobUrl: string }
  | { status: "error"; message: string };

/**
 * SVG, sanitized with DOMPurify and shown through a blob URL in an <img>.
 * The markup is never inserted into the page, so it cannot run script or load anything.
 */
export function SvgViewer({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [state, setState] = useState<SvgState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    let blobUrl: string | null = null;
    setState({ status: "loading" });
    fetch(src)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((text) => {
        if (cancelled) return;
        const clean = sanitizeSvg(text);
        if (clean === null) {
          setState({ status: "error", message: "This file has no usable <svg> content." });
          return;
        }
        blobUrl = URL.createObjectURL(new Blob([clean], { type: "image/svg+xml" }));
        setState({ status: "ready", blobUrl });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: "error", message: err instanceof Error ? err.message : "load failed" });
        }
      });
    return () => {
      cancelled = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [src]);

  if (state.status === "loading") {
    return <p className="m-0 text-sm text-muted-foreground">Loading SVG…</p>;
  }
  if (state.status === "error") {
    return <p role="alert" className="m-0 text-sm text-destructive">Could not show this SVG: {state.message}</p>;
  }
  return <img src={state.blobUrl} alt={alt} className={className} />;
}

/**
 * HTML in a sandboxed frame: the empty `sandbox` attribute grants nothing (no scripts, forms,
 * popups, or same-origin access). The URL is the served file, whose response carries a
 * `Content-Security-Policy: sandbox; default-src 'none'` header. No `srcdoc`.
 */
export function HtmlViewer({ src, title, className }: { src: string; title: string; className?: string }) {
  return <iframe src={src} title={title} sandbox="" referrerPolicy="no-referrer" className={className} />;
}

/** A PDF opens in the browser's viewer; the link is the fallback when embedding is off. */
export function PdfViewer({ src, title, className }: { src: string; title: string; className?: string }) {
  return (
    <div className="flex flex-col gap-2">
      <a href={src} target="_blank" rel="noopener noreferrer" className="text-sm text-primary underline">
        Open {title} in a new tab
      </a>
      <embed src={src} type="application/pdf" className={className} />
    </div>
  );
}
