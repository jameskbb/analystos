"use client";
import * as React from "react";
import { Eye, Link2, HelpCircle } from "lucide-react";
import type { StatementType } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export const STATEMENT_META: Record<
  StatementType,
  { label: string; short: string; description: string; icon: React.ComponentType<{ className?: string }> }
> = {
  observation: {
    label: "Observation",
    short: "Observed",
    description: "A measured fact computed directly from the data.",
    icon: Eye,
  },
  supported_explanation: {
    label: "Supported explanation",
    short: "Supported",
    description: "An explanation backed by executed analytical tests.",
    icon: Link2,
  },
  hypothesis: {
    label: "Hypothesis",
    short: "Hypothesis",
    description: "A possible explanation that has not been confirmed by evidence. Not a fact.",
    icon: HelpCircle,
  },
};

export function normalizeStatementType(t: string | null | undefined): StatementType {
  if (t === "supported_explanation" || t === "hypothesis" || t === "observation") return t;
  if (t === "explanation" || t === "supported") return "supported_explanation";
  return "observation";
}

const badgeTone: Record<StatementType, string> = {
  observation: "border-border-strong bg-bg-subtle text-fg-muted",
  supported_explanation: "border-transparent bg-accent-soft text-accent-soft-fg",
  hypothesis: "border-dashed border-hypothesis bg-hypothesis-soft text-hypothesis",
};

export function StatementTypeBadge({
  type,
  className,
  short = false,
}: {
  type: StatementType | string;
  className?: string;
  short?: boolean;
}) {
  const t = normalizeStatementType(type);
  const meta = STATEMENT_META[t];
  const Icon = meta.icon;
  return (
    <span
      data-statement-type={t}
      title={meta.description}
      className={cn(
        "inline-flex h-[18px] items-center gap-1 rounded-sm border px-1.5 text-2xs font-medium whitespace-nowrap",
        badgeTone[t],
        className,
      )}
    >
      <Icon className="size-3" aria-hidden />
      {short ? meta.short : meta.label}
    </span>
  );
}

const ruleTone: Record<StatementType, string> = {
  observation: "border-l-fg-faint",
  supported_explanation: "border-l-accent",
  hypothesis: "border-l-hypothesis border-dashed",
};

/**
 * A statement rendered with its type made unmistakable: a left rule, a type label and,
 * for hypotheses, italic text and a dashed rule so they can never read as facts (spec §65).
 */
export function Statement({
  type,
  children,
  className,
  showBadge = true,
  size = "md",
}: {
  type: StatementType | string;
  children: React.ReactNode;
  className?: string;
  showBadge?: boolean;
  size?: "sm" | "md" | "lg";
}) {
  const t = normalizeStatementType(type);
  return (
    <div
      data-statement-type={t}
      className={cn("border-l-2 pl-2.5", ruleTone[t], t === "hypothesis" && "border-l-2", className)}
    >
      {showBadge ? <StatementTypeBadge type={t} className="mb-1" /> : null}
      <p
        className={cn(
          "text-fg",
          size === "sm" && "text-sm",
          size === "md" && "text-sm",
          size === "lg" && "text-base leading-6 font-medium",
          t === "hypothesis" && "text-fg-muted italic",
        )}
      >
        {t === "hypothesis" ? <span className="sr-only">Hypothesis, not confirmed: </span> : null}
        {children}
      </p>
    </div>
  );
}
