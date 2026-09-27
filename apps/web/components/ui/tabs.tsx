"use client";
import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn("flex h-9 shrink-0 items-end gap-0 overflow-x-auto border-b border-border scrollbar-thin", className)}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "-mb-px inline-flex h-9 items-center gap-1.5 border-b-2 border-transparent px-3 text-sm whitespace-nowrap text-fg-subtle hover:text-fg data-[state=active]:border-accent data-[state=active]:font-medium data-[state=active]:text-fg [&_svg]:size-3.5",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn("min-h-0 focus-visible:outline-none", className)} {...props} />;
}

/** Small segmented control for toggles like chart type or view mode. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  className,
  "aria-label": ariaLabel,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: React.ReactNode; title?: string }[];
  className?: string;
  "aria-label"?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn("inline-flex h-7 items-center rounded border border-border-strong bg-bg-subtle p-0.5", className)}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cn(
            "inline-flex h-full items-center gap-1 rounded-sm px-2 text-xs text-fg-subtle hover:text-fg [&_svg]:size-3.5",
            value === o.value && "bg-bg font-medium text-fg shadow-[0_0_0_1px_var(--border)]",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
