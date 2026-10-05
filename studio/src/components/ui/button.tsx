import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

// Every variant sets an explicit hover border/background and a disabled style.
export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:border-border disabled:bg-muted disabled:text-muted-foreground",
  {
    variants: {
      variant: {
        default:
          "border-primary bg-primary text-primary-foreground hover:border-primary hover:bg-primary/90",
        destructive:
          "border-destructive bg-destructive text-destructive-foreground hover:border-destructive hover:bg-destructive/90",
        outline:
          "border-input bg-background text-foreground hover:border-input hover:bg-accent hover:text-accent-foreground",
        secondary:
          "border-secondary bg-secondary text-secondary-foreground hover:border-secondary hover:bg-secondary/80",
        ghost:
          "border-transparent bg-transparent text-foreground hover:border-transparent hover:bg-accent hover:text-accent-foreground",
        link: "border-transparent bg-transparent text-primary underline-offset-4 hover:border-transparent hover:bg-transparent hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 px-3 text-xs",
        lg: "h-10 px-6",
        icon: "h-9 w-9 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  /** Render the child element instead of a <button> (Radix Slot). */
  asChild?: boolean;
}

/** Token-styled button with cva variants. */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        type={asChild ? undefined : (type ?? "button")}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
