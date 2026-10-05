import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

// Token-styled form controls and page furniture shared by the migrated views.
// Every utility is explicit (border, background, focus ring) so the controls look
// the same wherever they are used; element defaults live in styles/globals.css.

const controlClass =
  "block w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground placeholder:text-muted-foreground focus-visible:border-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(controlClass, className)} {...props} />,
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => <textarea ref={ref} className={cn(controlClass, className)} {...props} />,
);
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => <select ref={ref} className={cn(controlClass, className)} {...props} />,
);
Select.displayName = "Select";

export const Label = React.forwardRef<HTMLLabelElement, React.LabelHTMLAttributes<HTMLLabelElement>>(
  ({ className, ...props }, ref) => (
    <label ref={ref} className={cn("mb-1 block text-sm font-medium text-muted-foreground", className)} {...props} />
  ),
);
Label.displayName = "Label";

/** Class for in-text links (the legacy `a` colour is not theme aware). */
export const linkClass =
  "text-primary underline-offset-2 hover:underline focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const alertVariants = cva("rounded-md border px-3 py-2 text-sm", {
  variants: {
    variant: {
      error: "border-status-failed/40 bg-status-failed/10 text-status-failed",
      success: "border-status-complete/40 bg-status-complete/10 text-status-complete",
      warn: "border-status-blocked/40 bg-status-blocked/10 text-foreground",
      info: "border-border bg-muted text-foreground",
    },
  },
  defaultVariants: { variant: "info" },
});

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {}

/** Inline banner. Errors use role="alert" so assistive tech announces them. */
export function Alert({ className, variant, role, ...props }: AlertProps) {
  return (
    <div
      role={role ?? (variant === "error" ? "alert" : undefined)}
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  );
}

/** Page container with the standard title. */
export function Page({
  title,
  description,
  actions,
  className,
  children,
}: {
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn("mx-auto w-full max-w-[1200px]", className)}>
      {title !== undefined && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h1 className="m-0 text-[22px] font-semibold tracking-tight">{title}</h1>
          {actions}
        </div>
      )}
      {description && <p className="mb-4 mt-0 text-sm text-muted-foreground">{description}</p>}
      {children}
    </div>
  );
}

/** Section heading used inside pages. */
export function SectionTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn("mb-2 mt-4 text-base font-semibold", className)} {...props} />;
}

/** Centered empty-state panel. */
export function EmptyState({
  title,
  children,
  className,
}: {
  title: string;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-dashed border-border bg-card px-4 py-8 text-center text-card-foreground",
        className,
      )}
    >
      <p className="m-0 text-sm font-semibold">{title}</p>
      {children && <div className="mt-1 text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}

/** Pulsing placeholder block. */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("animate-pulse rounded-md bg-muted", className)} {...props} />;
}

export function Table({ className, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <table className={cn("w-full border-collapse text-left text-sm", className)} {...props} />
    </div>
  );
}

export function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn("border-b border-border bg-muted px-3.5 py-2.5 text-left text-xs font-medium text-muted-foreground", className)}
      {...props}
    />
  );
}

export function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("border-b border-border px-3.5 py-2.5 align-middle", className)} {...props} />;
}
