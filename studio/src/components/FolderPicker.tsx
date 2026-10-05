import { useEffect, useState } from "react";
import { getToken } from "../api";
import { errorCls, fieldCls } from "../lib/field";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "./ui/dialog";

interface DirEntry {
  name: string;
  path: string;
}

interface ListResponse {
  path: string;
  parent: string | null;
  dirs: DirEntry[];
}

interface Props {
  initialPath?: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}

/** Modal folder browser hitting GET /api/fs/list. The user navigates the
 *  API host's filesystem and clicks Select on a directory to commit. */
export function FolderPicker({ initialPath, onSelect, onClose }: Props) {
  const [data, setData] = useState<ListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load(path: string) {
    setLoading(true);
    setError(null);
    try {
      const token = getToken();
      const res = await fetch(
        `/api/fs/list?path=${encodeURIComponent(path)}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} },
      );
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `${res.status}`);
      }
      setData((await res.json()) as ListResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load(initialPath || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="min-w-[480px] max-w-[640px]">
        <DialogTitle>Select folder</DialogTitle>
        <DialogDescription className="sr-only">Browse the API host's filesystem and choose a directory.</DialogDescription>

        <div className="flex items-center gap-1.5">
          <Button
            variant="outline"
            disabled={!data?.parent || loading}
            onClick={() => data?.parent && void load(data.parent)}
            title="Go up one level"
          >
            ↑ Up
          </Button>
          <input
            type="text"
            value={data?.path ?? ""}
            placeholder="(roots)"
            readOnly
            className={`${fieldCls} flex-1 font-mono text-xs`}
          />
        </div>

        {error && <div className={`${errorCls} mb-0`}>{error}</div>}

        <div className="max-h-80 overflow-y-auto rounded-md border border-border">
          {loading ? (
            <p className="m-0 p-3 text-xs text-muted-foreground">Loading…</p>
          ) : data && data.dirs.length === 0 ? (
            <p className="m-0 p-3 text-xs text-muted-foreground">No subdirectories here.</p>
          ) : (
            <ul className="m-0 list-none p-0">
              {data?.dirs.map((d) => (
                <li
                  key={d.path}
                  onClick={() => void load(d.path)}
                  className="cursor-pointer border-b border-border px-3 py-1.5 hover:bg-accent"
                >
                  📁 {d.name}
                </li>
              ))}
            </ul>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            disabled={!data?.path}
            onClick={() => {
              if (data?.path) {
                onSelect(data.path);
                onClose();
              }
            }}
          >
            Select this folder
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
