"use client";
import { Separator } from "react-resizable-panels";
import { cn } from "@/lib/utils";

/** 1px resize separator with a wider invisible hit area; accent while hovered, focused or dragged. */
export function ResizeHandle({ orientation = "horizontal", className }: { orientation?: "horizontal" | "vertical"; className?: string }) {
  return (
    <Separator
      className={cn(
        "relative shrink-0 bg-border outline-none data-[separator=active]:bg-accent data-[separator=focus]:bg-accent",
        orientation === "horizontal"
          ? "w-px after:absolute after:inset-y-0 after:-left-1 after:w-2 after:content-['']"
          : "h-px after:absolute after:inset-x-0 after:-top-1 after:h-2 after:content-['']",
        className,
      )}
    />
  );
}
