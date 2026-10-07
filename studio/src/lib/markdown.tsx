import { Fragment, type ReactNode } from "react";

/**
 * A small markdown renderer for chat replies (plan §11.3): paragraphs, `#` headings, `-`/`*`
 * and `1.` lists (one level of nesting by indent), **bold**, *italic*, `code` and fenced code
 * blocks. It builds React elements, never HTML strings, so text cannot inject markup.
 */

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;

export function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={n++}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={n++} className="rounded bg-muted px-1 font-mono text-[0.85em]">{tok.slice(1, -1)}</code>);
    else out.push(<em key={n++}>{tok.slice(1, -1)}</em>);
    last = at + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { kind: "p"; text: string }
  | { kind: "h"; level: number; text: string }
  | { kind: "code"; text: string }
  | { kind: "list"; ordered: boolean; items: { text: string; sub: string[] }[] };

const BULLET = /^(\s*)([-*]|\d+[.)])\s+(.*)$/;

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ kind: "p", text: para.join("\n") });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {
      flush();
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith("```"); i++) body.push(lines[i]);
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flush();
      blocks.push({ kind: "h", level: heading[1].length, text: heading[2] });
      continue;
    }
    const bullet = line.match(BULLET);
    if (bullet) {
      flush();
      const ordered = /\d/.test(bullet[2]);
      const indent = bullet[1].length;
      const prev = blocks[blocks.length - 1];
      if (indent >= 2 && prev?.kind === "list" && prev.items.length) {
        prev.items[prev.items.length - 1].sub.push(bullet[3]);
      } else if (prev?.kind === "list" && prev.ordered === ordered) {
        prev.items.push({ text: bullet[3], sub: [] });
      } else {
        blocks.push({ kind: "list", ordered, items: [{ text: bullet[3], sub: [] }] });
      }
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    // A continuation line of a list item (indented, no marker) joins that item.
    const prev = blocks[blocks.length - 1];
    if (!para.length && /^\s{2,}\S/.test(line) && prev?.kind === "list") {
      const item = prev.items[prev.items.length - 1];
      item.text += ` ${line.trim()}`;
      continue;
    }
    para.push(line);
  }
  flush();
  return blocks;
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={className}>
      {parseBlocks(text).map((b, i) => {
        if (b.kind === "h") {
          return (
            <p key={i} className="mt-2 font-semibold first:mt-0">
              {renderInline(b.text)}
            </p>
          );
        }
        if (b.kind === "code") {
          return (
            <pre key={i} className="mt-2 overflow-x-auto rounded bg-muted p-2 font-mono text-xs first:mt-0">
              {b.text}
            </pre>
          );
        }
        if (b.kind === "list") {
          const List = b.ordered ? "ol" : "ul";
          return (
            <List key={i} className={`mt-2 flex flex-col gap-0.5 pl-5 first:mt-0 ${b.ordered ? "list-decimal" : "list-disc"}`}>
              {b.items.map((it, j) => (
                <li key={j}>
                  {renderInline(it.text)}
                  {it.sub.length > 0 && (
                    <ul className="list-[circle] pl-5">
                      {it.sub.map((s, k) => (
                        <li key={k}>{renderInline(s)}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </List>
          );
        }
        return (
          <p key={i} className="mt-2 whitespace-pre-wrap first:mt-0">
            {b.text.split("\n").map((l, j) => (
              <Fragment key={j}>
                {j > 0 && <br />}
                {renderInline(l)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
