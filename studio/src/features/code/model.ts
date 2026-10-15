import type { CodeEntry } from "./api";

/** A folder or file in the tree built from the flat entry list. */
export interface TreeNode {
  name: string;
  path: string;
  entry?: CodeEntry;
  children: TreeNode[];
  /** Files under this node that changed against the base. */
  changed: number;
}

/** Folders first, then files, each by name; `changedOnly` keeps changed files and their folders. */
export function buildTree(entries: readonly CodeEntry[], changedOnly = false): TreeNode {
  const root: TreeNode = { name: "", path: "", children: [], changed: 0 };
  for (const e of entries) {
    if (changedOnly && !e.status) continue;
    const parts = e.path.split("/");
    let node = root;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const path = parts.slice(0, i + 1).join("/");
      let next = node.children.find((c) => c.path === path && !c.entry);
      if (!next) {
        next = { name: parts[i], path, children: [], changed: 0 };
        node.children.push(next);
      }
      node = next;
    }
    node.children.push({ name: parts[parts.length - 1], path: e.path, entry: e, children: [], changed: 0 });
  }
  const finish = (n: TreeNode): number => {
    n.children.sort((a, b) => (a.entry ? 1 : 0) - (b.entry ? 1 : 0) || a.name.localeCompare(b.name));
    n.changed = n.entry ? (n.entry.status ? 1 : 0) : n.children.reduce((s, c) => s + finish(c), 0);
    return n.changed;
  };
  finish(root);
  return root;
}

/** The folders that hold `path` ("a/b/c.js" → ["a", "a/b"]). */
export function parentsOf(path: string): string[] {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
}
