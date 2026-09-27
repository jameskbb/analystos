"use client";
import * as React from "react";
import { CheckCircle2, XCircle, HelpCircle } from "lucide-react";
import type { ArtifactRecord, Hypothesis, Investigation } from "@/lib/api/types";
import { EvidenceBadge, EvidenceReasons } from "@/components/analysis/evidence";
import { StatementTypeBadge } from "@/components/analysis/statement";
import { SectionLabel } from "@/components/shell/page";
import { Badge } from "@/components/ui/badge";
import { formatInt } from "@/lib/format";
import { datasetVersions, metricVersions, METHOD_LABEL, type NormNode } from "./model";
import { cn } from "@/lib/utils";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-border px-3 py-2.5 last:border-b-0">
      <SectionLabel className="mb-1.5">{title}</SectionLabel>
      {children}
    </section>
  );
}

export function ValidationList({ artifacts }: { artifacts: ArtifactRecord[] }) {
  const checks = artifacts.flatMap((a) => (a.validation?.checks ?? []).map((c) => ({ ...c, artifact: a.title || a.id })));
  if (!checks.length) return <p className="text-xs text-fg-subtle">No validation checks recorded for this node.</p>;
  return (
    <ul className="flex flex-col gap-1" aria-label="Validation checks">
      {checks.map((c, i) => (
        <li key={i} className="flex items-start gap-1.5 text-xs">
          {c.passed ? (
            <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-positive" aria-label="passed" />
          ) : (
            <XCircle className="mt-0.5 size-3.5 shrink-0 text-negative" aria-label="failed" />
          )}
          <span className="min-w-0">
            <span className="font-medium">{c.name.replace(/_/g, " ")}</span>
            {c.detail ? <span className="text-fg-subtle"> · {c.detail}</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Right panel: evidence strength with reasons, methodology, validation, reproducibility, hypotheses. */
export function EvidencePanel({
  node,
  artifacts,
  investigation,
  hypotheses,
}: {
  node: NormNode | null;
  artifacts: ArtifactRecord[];
  investigation: Investigation;
  hypotheses: Hypothesis[];
}) {
  const dvs = artifacts.length ? artifacts.flatMap(datasetVersions) : datasetVersions(investigation);
  const uniqDvs = [...new Map(dvs.map((d) => [d.table, d])).values()];
  const mvs = [...new Map([...artifacts.flatMap((a) => metricVersions(a)), ...metricVersions(investigation)]).entries()];
  return (
    <div className="flex flex-col text-sm">
      {node ? (
        <Section title="Evidence">
          <EvidenceBadge strength={node.evidence_strength} reasons={node.evidence_reasons} />
          <EvidenceReasons reasons={node.evidence_reasons} className="mt-2" />
          {node.statement_type === "hypothesis" ? (
            <p className="mt-2 rounded border border-dashed border-hypothesis bg-hypothesis-soft px-2 py-1 text-xs text-hypothesis">
              This is a hypothesis. No executed test confirms it; do not report it as a fact.
            </p>
          ) : null}
        </Section>
      ) : null}
      {node?.contribution ? (
        <Section title="Methodology">
          <p className="text-xs text-fg-muted">
            {METHOD_LABEL[node.contribution.method] ?? `Method: ${node.contribution.method}`}
          </p>
          {node.contribution.mix_effect !== null && node.contribution.mix_effect !== undefined ? (
            <p className="mt-1 text-xs text-fg-subtle tabular">
              Mix effect {node.contribution.mix_effect.toFixed(2)}, rate effect {(node.contribution.rate_effect ?? 0).toFixed(2)}
            </p>
          ) : null}
          {node.notes?.length ? (
            <ul className="mt-1.5 list-disc pl-4 text-xs text-fg-subtle">
              {node.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          ) : null}
        </Section>
      ) : node?.notes?.length ? (
        <Section title="Methodology">
          <ul className="list-disc pl-4 text-xs text-fg-subtle">
            {node.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </Section>
      ) : null}
      {node ? (
        <Section title="Validation">
          <ValidationList artifacts={artifacts} />
          {artifacts.some((a) => a.warnings?.length) ? (
            <ul className="mt-1.5 flex flex-col gap-0.5 text-xs text-warning">
              {artifacts.flatMap((a) => a.warnings ?? []).map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          ) : null}
        </Section>
      ) : null}
      {investigation.orchestration?.stages?.length ? (
        <Section title="AI orchestration">
          <p className="mb-1 text-xs text-fg-subtle">
            The optional AI planner ran these stages. Every number above still comes from executed queries.
          </p>
          <ul className="flex flex-col gap-0.5 text-xs" aria-label="Orchestration stages">
            {investigation.orchestration.stages.map((st, i) => (
              <li key={i} className={cn("flex gap-1.5", st.ok ? "text-fg-muted" : "text-negative")}>
                <span className="font-mono">{st.stage}</span>
                <span className="text-fg-subtle">({st.source})</span>
                {st.detail ? <span className="truncate">{st.detail}</span> : null}
              </li>
            ))}
          </ul>
          {investigation.orchestration.rejected_outputs?.length ? (
            <p className="mt-1 text-xs text-warning">
              {investigation.orchestration.rejected_outputs.length} model output(s) were rejected by validation.
            </p>
          ) : null}
        </Section>
      ) : null}
      <Section title="Reproducibility">
        {mvs.length ? (
          <div className="mb-2 flex flex-col gap-0.5">
            {mvs.map(([m, v]) => (
              <div key={m} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate">{m}</span>
                <code className="shrink-0 font-mono text-2xs text-fg-subtle">{v}</code>
              </div>
            ))}
          </div>
        ) : null}
        {uniqDvs.length ? (
          <div className="flex flex-col gap-0.5">
            {uniqDvs.map((d) => (
              <div key={d.table} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate font-mono">{d.table}</span>
                <span className="shrink-0 text-2xs text-fg-subtle tabular">
                  {d.row_count !== null ? `${formatInt(d.row_count)} rows · ` : ""}
                  <code>{d.content_hash.slice(0, 10)}</code>
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-fg-subtle">Dataset versions are recorded when the investigation runs.</p>
        )}
        {investigation.engine_version ? (
          <p className="mt-1.5 font-mono text-2xs text-fg-faint">{investigation.engine_version}</p>
        ) : null}
      </Section>
      {hypotheses.length ? (
        <Section title="Hypotheses considered">
          <ul className="flex flex-col gap-1.5">
            {hypotheses.map((h, i) => (
              <li key={h.id ?? i} className="flex flex-col gap-0.5">
                <div className="flex items-center gap-1.5">
                  <StatementTypeBadge type="hypothesis" short />
                  {h.testable === false ? (
                    <Badge tone="outline">
                      <HelpCircle /> Not testable with this data
                    </Badge>
                  ) : h.test_step_ids?.length ? (
                    <Badge tone="neutral">{h.test_step_ids.length} test{h.test_step_ids.length > 1 ? "s" : ""}</Badge>
                  ) : null}
                </div>
                <p className={cn("text-xs text-fg-muted italic")}>{h.statement ?? h.title}</p>
                {h.reason ? <p className="text-2xs text-fg-subtle">{h.reason}</p> : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}
