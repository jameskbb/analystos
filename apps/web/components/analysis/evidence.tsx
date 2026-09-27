"use client";
import * as React from "react";
import type { EvidenceStrength } from "@/lib/api/types";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export const EVIDENCE_META: Record<EvidenceStrength, { label: string; bars: number; color: string; description: string }> = {
  strong: {
    label: "Strong evidence",
    bars: 3,
    color: "var(--evidence-strong)",
    description: "Large effect, direct calculation, corroborated by more than one test.",
  },
  moderate: {
    label: "Moderate evidence",
    bars: 2,
    color: "var(--evidence-moderate)",
    description: "Supported by a direct calculation, with a meaningful but partial effect or limited corroboration.",
  },
  weak: {
    label: "Weak evidence",
    bars: 1,
    color: "var(--evidence-weak)",
    description: "Small effect, indirect calculation or a single uncorroborated test.",
  },
  hypothesis_only: {
    label: "Hypothesis only",
    bars: 0,
    color: "var(--evidence-none)",
    description: "No executed test supports this yet.",
  },
};

export function normalizeEvidence(s: string | null | undefined): EvidenceStrength {
  if (s === "strong" || s === "moderate" || s === "weak" || s === "hypothesis_only") return s;
  if (s === "hypothesis" || s === "none") return "hypothesis_only";
  return "hypothesis_only";
}

function Bars({ n, color }: { n: number; color: string }) {
  return (
    <svg width="11" height="10" viewBox="0 0 11 10" aria-hidden className="shrink-0">
      {[0, 1, 2].map((i) => (
        <rect
          key={i}
          x={i * 4}
          y={6 - i * 3}
          width="3"
          height={4 + i * 3}
          rx="0.5"
          fill={i < n ? color : "none"}
          stroke={color}
          strokeWidth={i < n ? 0 : 0.9}
          strokeDasharray={n === 0 ? "1.2 1" : undefined}
        />
      ))}
    </svg>
  );
}

/**
 * Evidence-strength badge. Categories only, never a numeric confidence (spec §28).
 * Clicking reveals the explicit reasons.
 */
export function EvidenceBadge({
  strength,
  reasons = [],
  className,
  interactive = true,
}: {
  strength: EvidenceStrength | string;
  reasons?: string[];
  className?: string;
  interactive?: boolean;
}) {
  const s = normalizeEvidence(strength);
  const meta = EVIDENCE_META[s];
  const badge = (
    <span
      data-evidence={s}
      className={cn(
        "inline-flex h-[18px] items-center gap-1.5 rounded-sm border border-border bg-bg px-1.5 text-2xs font-medium whitespace-nowrap",
        className,
      )}
      style={{ color: meta.color }}
    >
      <Bars n={meta.bars} color={meta.color} />
      {meta.label}
    </span>
  );
  if (!interactive) return badge;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="rounded-sm focus-visible:outline-2 focus-visible:outline-ring"
          aria-label={`${meta.label}. Show reasons`}
        >
          {badge}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-80">
        <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: meta.color }}>
          <Bars n={meta.bars} color={meta.color} />
          {meta.label}
        </div>
        <p className="mt-1 text-xs text-fg-subtle">{meta.description}</p>
        <EvidenceReasons reasons={reasons} className="mt-2" />
      </PopoverContent>
    </Popover>
  );
}

export function EvidenceReasons({ reasons, className }: { reasons: string[]; className?: string }) {
  if (!reasons.length)
    return <p className={cn("text-xs text-fg-subtle italic", className)}>No reasons recorded by the engine.</p>;
  return (
    <ul className={cn("flex flex-col gap-1 text-sm", className)} aria-label="Evidence reasons">
      {reasons.map((r, i) => (
        <li key={i} className="flex gap-1.5">
          <span className="mt-2 size-1 shrink-0 rounded-full bg-fg-subtle" aria-hidden />
          <span>{r}</span>
        </li>
      ))}
    </ul>
  );
}
