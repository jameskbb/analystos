import { Badge } from "@/components/ui/badge";
import type { DqSummary } from "./dq-summary";

export function DqBadge({ summary }: { summary: DqSummary | undefined }) {
  if (!summary || summary.active === 0)
    return <span className="text-xs text-fg-faint">{summary?.suggested ? `${summary.suggested} suggested` : "No rules"}</span>;
  if (summary.failing > 0)
    return (
      <Badge tone="negative" title={`${summary.failing} of ${summary.active} active rules failing`}>
        {summary.failing} failing
      </Badge>
    );
  if (summary.neverRun === summary.active) return <Badge tone="outline">{summary.active} not run</Badge>;
  return (
    <Badge tone="positive" title={`${summary.passing} passing, ${summary.neverRun} not run`}>
      {summary.passing}/{summary.active} passing
    </Badge>
  );
}
