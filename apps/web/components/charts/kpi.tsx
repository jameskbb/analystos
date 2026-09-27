import * as React from "react";
import { ChangeValue } from "@/components/analysis/change";
import { formatValue, toNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

export function Kpi({
  label,
  value,
  format,
  baseline,
  pctChange,
  comparisonLabel = "vs prior period",
  higherIsBetter = true,
  className,
  size = "md",
}: {
  label: React.ReactNode;
  value: number | null | undefined;
  format?: string | null;
  baseline?: number | null;
  pctChange?: number | null;
  comparisonLabel?: string;
  higherIsBetter?: boolean;
  className?: string;
  size?: "sm" | "md" | "lg";
}) {
  const pct =
    pctChange ??
    (toNumber(baseline) && toNumber(value) !== null ? (toNumber(value)! - toNumber(baseline)!) / Math.abs(toNumber(baseline)!) : null);
  return (
    <div className={cn("flex min-w-0 flex-col gap-0.5", className)} data-testid="kpi">
      <div className="truncate text-xs text-fg-subtle">{label}</div>
      <div
        className={cn(
          "font-semibold tracking-tight tabular",
          size === "sm" && "text-lg",
          size === "md" && "text-xl",
          size === "lg" && "text-2xl",
        )}
      >
        {formatValue(value, format ?? undefined)}
      </div>
      {pct !== null && pct !== undefined ? (
        <div className="flex items-center gap-1 text-xs">
          <ChangeValue pct={pct} higherIsBetter={higherIsBetter} />
          <span className="text-fg-subtle">
            {comparisonLabel}
            {baseline !== null && baseline !== undefined ? ` (${formatValue(baseline, format ?? undefined, { compact: true })})` : ""}
          </span>
        </div>
      ) : null}
    </div>
  );
}
