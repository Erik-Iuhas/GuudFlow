import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "./ui/dialog";

// An in-app confirm, so nothing in Studio uses the browser's own dialogs.

export interface ConfirmOptions {
  title: string;
  body?: string;
  confirmLabel?: string;
  destructive?: boolean;
}

type Confirm = (opts: ConfirmOptions) => Promise<boolean>;
const Ctx = createContext<Confirm | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);
  const confirm = useCallback<Confirm>((o) => new Promise<boolean>((resolve) => {
    resolver.current?.(false);
    resolver.current = resolve;
    setOpts(o);
  }), []);
  const close = (ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
    setOpts(null);
  };
  return (
    <Ctx.Provider value={confirm}>
      {children}
      <Dialog open={opts !== null} onOpenChange={(open) => { if (!open) close(false); }}>
        {opts && (
          <DialogContent aria-describedby={opts.body ? "confirm-body" : undefined} data-testid="confirm-dialog">
            <DialogTitle>{opts.title}</DialogTitle>
            {opts.body && <DialogDescription id="confirm-body">{opts.body}</DialogDescription>}
            <DialogFooter className="justify-start">
              <Button variant={opts.destructive ? "destructive" : "default"} onClick={() => close(true)} autoFocus>
                {opts.confirmLabel ?? "Confirm"}
              </Button>
              <Button variant="outline" onClick={() => close(false)}>Cancel</Button>
            </DialogFooter>
          </DialogContent>
        )}
      </Dialog>
    </Ctx.Provider>
  );
}

/** The in-app confirm. Outside a provider (single-component tests) it resolves true. */
export function useConfirm(): Confirm {
  return useContext(Ctx) ?? (async () => true);
}
