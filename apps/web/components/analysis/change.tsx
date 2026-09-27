import * as React from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { changeTone, formatPctChange, formatSignedChange, formatShare, toNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A signed change with direction glyph. Tone reflects direction, not judgment, unless higherIsBetter is set. */
export function ChangeValue({
  pct,
  abs,
  format,
  higherIsBetter = true,
  className,
  showIcon = true,
}: {
  pct?: number | null;
  abs?: number | null;
  format?: string | null;
  higherIsBetter?: boolean;
  className?: string;
  showIcon?: boolean;
}) {
  const basis = toNumber(pct) ?? toNumber(abs);
  const tone = changeTone(basis, higherIsBetter);
  const Icon = basis === null || basis === 0 ? Minus : basis > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 font-medium tabular whitespace-nowrap",
        tone === "positive" && "text-positive",
        tone === "negative" && "text-negative",
        tone === "neutral" && "text-fg-subtle",
        className,
      )}
    >
      {showIcon ? <Icon className="size-3.5" aria-hidden /> : null}
      {pct !== undefined && pct !== null ? formatPctChange(pct) : null}
      {abs !== undefined && abs !== null ? (
        <span className={cn(pct !== undefined && pct !== null && "ml-1 font-normal opacity-80")}>
          {pct !== undefined && pct !== null ? `(${formatSignedChange(abs, format)})` : formatSignedChange(abs, format)}
        </span>
      ) : null}
    </span>
  );
}

/** Share of the parent's change as a small horizontal bar. Negative share = offsetting movement. */
export function ShareBar({ share, className }: { share: number | null | undefined; className?: string }) {
  const n = toNumber(share);
  if (n === null) return null;
  const width = Math.min(Math.abs(n), 1) * 100;
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)} title="Share of parent change">
      <span className="relative h-1.5 w-12 overflow-hidden rounded-full bg-bg-muted" aria-hidden>
        <span
          className={cn("absolute inset-y-0 left-0 rounded-full", n >= 0 ? "bg-accent" : "bg-hypothesis")}
          style={{ width: `${width}%` }}
        />
      </span>
      <span className="text-xs text-fg-muted tabular">{formatShare(n)}</span>
    </span>
  );
}
