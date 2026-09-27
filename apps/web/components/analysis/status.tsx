import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

const TONES: Record<string, "neutral" | "accent" | "positive" | "negative" | "warning" | "outline"> = {
  draft: "neutral",
  proposed: "outline",
  confirmed: "positive",
  rejected: "negative",
  needs_review: "warning",
  in_review: "warning",
  published: "positive",
  completed: "positive",
  succeeded: "positive",
  ok: "positive",
  passed: "positive",
  approved: "positive",
  active: "accent",
  running: "accent",
  queued: "neutral",
  planned: "accent",
  awaiting_approval: "warning",
  interpreted: "neutral",
  failed: "negative",
  error: "negative",
  suggested: "outline",
  disabled: "neutral",
  ready: "positive",
  pending: "neutral",
  untested: "neutral",
  connected: "positive",
};

const LABELS: Record<string, string> = {
  needs_review: "Needs review",
  awaiting_approval: "Awaiting approval",
  in_review: "In review",
};

export function StatusBadge({ status, className }: { status: string | null | undefined; className?: string }) {
  const s = status ?? "unknown";
  return (
    <Badge tone={TONES[s] ?? "neutral"} className={cn(className)} data-status={s}>
      {s === "running" ? <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden /> : null}
      {LABELS[s] ?? humanize(s)}
    </Badge>
  );
}
