import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

export const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium leading-4",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-foreground",
        destructive: "border-transparent bg-destructive text-destructive-foreground",
        running: "border-status-running/30 bg-status-running/15 text-status-running",
        complete: "border-status-complete/30 bg-status-complete/15 text-status-complete",
        blocked: "border-status-blocked/30 bg-status-blocked/15 text-status-blocked",
        failed: "border-status-failed/30 bg-status-failed/15 text-status-failed",
        waiting: "border-status-waiting/30 bg-status-waiting/15 text-status-waiting",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

/** Small pill label. The status variants map to the `--status-*` tokens. */
export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
