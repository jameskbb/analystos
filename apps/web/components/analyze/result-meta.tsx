"use client";
import * as React from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, CircleHelp, GitFork, XCircle } from "lucide-react";
import type { AnalysisOut, Assumption } from "@/lib/api/resources/analysis";
import { useWorkspace } from "@/components/providers/workspace";
import { Badge } from "@/components/ui/badge";
import { CodeBlock } from "@/components/code/code-block";
import { FilterChips } from "@/components/analysis/filter-chips";
import { SectionLabel } from "@/components/shell/page";
import { formatDateTime, formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Badge tone per result label: exploratory results are visibly not evidence of cause. */
export function labelTone(label: string, exploratory: boolean): "warning" | "accent" | "neutral" {
  if (exploratory || label === "Exploratory") return "warning";
  if (label === "Statistical test" || label === "Model estimate") return "accent";
  return "neutral";
}

export function AssumptionList({ items, className }: { items: Assumption[]; className?: string }) {
  if (!items.length) return null;
  return (
    <ul className={cn("flex flex-col gap-1", className)} aria-label="Assumption checks">
      {items.map((a, i) => {
        const Icon = a.passed === true ? CheckCircle2 : a.passed === false ? XCircle : CircleHelp;
        const tone = a.passed === true ? "text-positive" : a.passed === false ? "text-negative" : "text-fg-subtle";
        const state = a.passed === true ? "holds" : a.passed === false ? "violated" : "not checked";
        return (
          <li key={i} className="flex items-start gap-2 text-sm">
            <Icon className={cn("mt-0.5 size-3.5 shrink-0", tone)} aria-label={state} />
            <span>
              <span className="font-medium">{a.name}</span>
              <span className="text-fg-subtle"> ({state})</span>
              {a.detail ? <span className="text-fg-muted">: {a.detail}</span> : null}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Everything that makes an analysis result trustworthy and reproducible: label, the computed
 * summary sentence, assumptions, caveats, filter context, input size, versions and the SQL.
 */
export function ResultMeta({ out, children }: { out: AnalysisOut; children?: React.ReactNode }) {
  const { href } = useWorkspace();
  const [showSql, setShowSql] = React.useState(false);
  return (
    <div className="flex flex-col gap-3" data-testid="analysis-result">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={labelTone(out.label, out.exploratory)} data-testid="analysis-label">
          {out.exploratory ? <AlertTriangle /> : null}
          {out.label}
        </Badge>
        <span className="text-sm font-semibold">{out.title}</span>
        <span className="text-xs text-fg-subtle">· {out.method}</span>
        <span className="ml-auto flex items-center gap-2 text-xs text-fg-subtle">
          {formatDateTime(out.created_at)}
          <Link href={href(`/lineage/artifact/${out.id}`)} className="inline-flex items-center gap-1 text-accent hover:underline">
            <GitFork className="size-3" /> Lineage
          </Link>
        </span>
      </div>
      <p className="text-sm" data-testid="analysis-summary">
        {out.summary}
      </p>
      {out.exploratory ? (
        <p className="rounded border border-warning/40 bg-warning-soft px-2 py-1.5 text-xs text-warning" role="note">
          Exploratory: this shows association in the data, not a cause. Test a specific hypothesis before treating it as an
          explanation.
        </p>
      ) : null}
      {children}
      {out.assumptions.length ? (
        <div>
          <SectionLabel className="mb-1">Assumptions</SectionLabel>
          <AssumptionList items={out.assumptions} />
        </div>
      ) : null}
      {out.caveats.length || out.notes.length ? (
        <div>
          <SectionLabel className="mb-1">Caveats and notes</SectionLabel>
          <ul className="list-disc pl-5 text-sm text-fg-muted">
            {[...out.caveats, ...out.notes].map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="flex flex-col gap-1">
        <SectionLabel>Filter context</SectionLabel>
        <FilterChips items={out.filter_context} />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-subtle">
        {out.input_row_count !== null ? (
          <span>
            Input: {formatInt(out.input_row_count)} rows{out.input_truncated ? " (capped; see notes)" : ""}
          </span>
        ) : null}
        {Object.entries(out.metric_versions).map(([m, v]) => (
          <span key={m} className="font-mono">
            {String(v).includes("@") ? String(v).split(":")[0] : `${m}@${String(v).slice(0, 8)}`}
          </span>
        ))}
        {out.dataset_versions.map((d) => (
          <span key={d.table} className="font-mono" title={d.content_hash}>
            {d.table} · {formatInt(d.row_count)} rows · {d.content_hash.slice(0, 8)}
          </span>
        ))}
        {out.sql ? (
          <button type="button" className="text-accent hover:underline" onClick={() => setShowSql((s) => !s)}>
            {showSql ? "Hide SQL" : "Show SQL"}
          </button>
        ) : null}
      </div>
      {showSql && out.sql ? <CodeBlock code={out.sql} maxHeight={260} title="Input query" /> : null}
    </div>
  );
}
