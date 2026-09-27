import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

export const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-sm border px-1.5 h-[18px] text-2xs font-medium whitespace-nowrap [&_svg]:size-3",
  {
    variants: {
      tone: {
        neutral: "border-border bg-bg-subtle text-fg-muted",
        accent: "border-transparent bg-accent-soft text-accent-soft-fg",
        positive: "border-transparent bg-positive-soft text-positive",
        negative: "border-transparent bg-negative-soft text-negative",
        warning: "border-transparent bg-warning-soft text-warning",
        outline: "border-border-strong bg-transparent text-fg-muted",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
