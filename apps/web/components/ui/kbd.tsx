import * as React from "react";
import { cn } from "@/lib/utils";

export function Kbd({ className, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-sm border border-border-strong bg-bg-subtle px-1 font-mono text-2xs text-fg-subtle",
        className,
      )}
      {...props}
    />
  );
}
