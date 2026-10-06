import type { CSSProperties } from "react";
import { avatarFor } from "../lib/avatar";
import { cn } from "../lib/utils";

/** Round gradient avatar derived from a seed (persona id or role). */
export function AvatarDot({ seed, className, title }: { seed: string; className?: string; title?: string }) {
  const av = avatarFor(seed);
  return (
    <div
      aria-hidden={title ? undefined : true}
      title={title}
      className={cn(
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[image:var(--avatar)] text-xs font-semibold text-white",
        className,
      )}
      // The gradient is computed from the seed at runtime, so it travels as a CSS variable.
      style={{ "--avatar": av.gradient } as CSSProperties}
    >
      {av.initials}
    </div>
  );
}
