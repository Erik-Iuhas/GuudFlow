import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Toast as UiToast,
  ToastClose,
  ToastDescription,
  ToastProvider as UiToastProvider,
  ToastViewport,
} from "./ui/toast";

/**
 * Phase G.6 — minimal toast system, rendered with the ui/toast Radix primitives.
 * Replaces inline `<div className="error">` blocks scattered through views. Useful for async API failures that
 * happen out-of-band (network blips, background polling errors).
 *
 * Inline errors are still fine for form validation — toasts are for
 * "something broke without the user doing anything just now."
 */

export type ToastKind = "info" | "success" | "warn" | "error";

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastContextValue {
  push: (message: string, kind?: ToastKind) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

let nextId = 1;
const TOAST_DURATION_MS = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, kind: ToastKind = "info") => {
    const id = nextId++;
    setToasts((prev) => [...prev, { id, kind, message }]);
  }, []);

  function dismiss(id: number) {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      <UiToastProvider duration={TOAST_DURATION_MS} swipeDirection="right">
        {children}
        {toasts.map((t) => (
          <UiToast
            key={t.id}
            variant={t.kind}
            duration={TOAST_DURATION_MS}
            onOpenChange={(open) => {
              if (!open) dismiss(t.id);
            }}
          >
            <ToastDescription>{t.message}</ToastDescription>
            <ToastClose />
          </UiToast>
        ))}
        <ToastViewport />
      </UiToastProvider>
    </ToastContext.Provider>
  );
}

/** Hook for views/components to push a toast. Throws if used outside
 *  ToastProvider — that's a programmer error, fail loudly. */
export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (ctx === null) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return ctx;
}

/** Convenience: surface async errors from a fetch promise as a toast.
 *  Useful for fire-and-forget background operations. */
export function useAsyncErrorToast() {
  const { push } = useToast();
  return useCallback(
    (where: string) => (e: unknown) => {
      const message = e instanceof Error ? e.message : String(e);
      push(`${where}: ${message}`, "error");
    },
    [push],
  );
}

/** Mount once at app root: catches unhandled promise rejections + uncaught
 *  exceptions and surfaces them as toasts so silent background failures
 *  become visible. */
export function GlobalErrorHandler() {
  const { push } = useToast();
  useEffect(() => {
    function onRejection(ev: PromiseRejectionEvent) {
      const reason =
        ev.reason instanceof Error ? ev.reason.message : String(ev.reason);
      push(`Unhandled error: ${reason}`, "error");
    }
    window.addEventListener("unhandledrejection", onRejection);
    return () => window.removeEventListener("unhandledrejection", onRejection);
  }, [push]);
  return null;
}
