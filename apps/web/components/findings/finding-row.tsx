"use client";
import * as React from "react";
import Link from "next/link";
import { GitBranch, MessageSquare } from "lucide-react";
import type { Finding } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { StatementTypeBadge, normalizeStatementType } from "@/components/analysis/statement";
import { EvidenceBadge } from "@/components/analysis/evidence";
import { StatusBadge } from "@/components/analysis/status";
import { FilterChips } from "@/components/analysis/filter-chips";
import { ChangeValue } from "@/components/analysis/change";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

const RULE: Record<string, string> = {
  observation: "border-l-fg-faint",
  supported_explanation: "border-l-accent",
  hypothesis: "border-l-hypothesis border-dashed",
};

export function FindingRow({ finding }: { finding: Finding }) {
  const { href } = useWorkspace();
  const t = normalizeStatementType(finding.statement_type);
  const v = finding.values ?? {};
  return (
    <li className={cn("flex flex-col gap-1.5 border-b border-border py-2.5 pr-3 pl-3 last:border-b-0", finding.status === "rejected" && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-1.5">
        <StatementTypeBadge type={t} />
        <EvidenceBadge strength={finding.evidence_strength} reasons={finding.evidence_reasons} />
        <StatusBadge status={finding.status} />
        {v.pct_change !== undefined && v.pct_change !== null ? <ChangeValue pct={v.pct_change} abs={v.abs_change} format={v.format} className="text-xs" /> : null}
        <span className="ml-auto text-2xs text-fg-subtle">
          {finding.created_by_name ? `${finding.created_by_name} · ` : ""}
          {formatRelative(finding.updated_at ?? finding.created_at)}
        </span>
      </div>
      <div className={cn("border-l-2 pl-2.5", RULE[t])}>
        <Link
          href={href(`/findings/${finding.id}`)}
          className={cn("text-sm font-medium hover:text-accent", t === "hypothesis" && "font-normal text-fg-muted italic", finding.status === "rejected" && "line-through")}
        >
          {finding.statement}
        </Link>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <FilterChips items={finding.filter_context} size="xs" />
        {finding.investigation_id ? (
          <Link href={href(`/investigate/${finding.investigation_id}`)} className="inline-flex items-center gap-1 text-2xs text-fg-subtle hover:text-accent">
            <GitBranch className="size-3" /> {finding.investigation_title ?? "Investigation"}
          </Link>
        ) : null}
        {finding.comment_count ? (
          <span className="inline-flex items-center gap-1 text-2xs text-fg-subtle">
            <MessageSquare className="size-3" /> {finding.comment_count}
          </span>
        ) : null}
      </div>
    </li>
  );
}
