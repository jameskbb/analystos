"use client";
import * as React from "react";
import Link from "next/link";
import { CheckCircle2, AlertTriangle } from "lucide-react";
import type { ArtifactRecord } from "@/lib/api/types";
import { useOptionalWorkspace } from "@/components/providers/workspace";
import { Badge } from "@/components/ui/badge";
import { CodeBlock } from "@/components/code/code-block";
import { DefinitionList } from "@/components/shell/page";
import { formatPctChange, formatShare, formatValue, humanize } from "@/lib/format";

type Obj = Record<string, unknown>;
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

export interface ContributionCalc {
  kind: "contribution";
  method: string;
  additiveValid: boolean;
  totalCurrent: number | null;
  totalBaseline: number | null;
  totalChange: number | null;
  segmentCount: number | null;
  notes: string[];
  rows: {
    segment: string;
    current: number | null;
    baseline: number | null;
    effect: number | null;
    share: number | null;
    mix: number | null;
    rate: number | null;
    pct: number | null;
  }[];
}

export interface DecompositionCalc {
  kind: "decomposition";
  method: string;
  parentCurrent: number | null;
  parentBaseline: number | null;
  parentChange: number | null;
  residual: number | null;
  residualShare: number | null;
  identityCurrent: number | null;
  identityBaseline: number | null;
  notes: string[];
  effects: { metric: string; relation: string; current: number | null; baseline: number | null; effect: number | null; share: number | null }[];
}

export type Calculation = ContributionCalc | DecompositionCalc | { kind: "python"; stdout: string; stderr: string; error: string | null } | { kind: "raw"; data: Obj } | null;

/**
 * Reads the engine `Artifact.data` (exposed by `GET /artifacts/{id}` as `data`) into a typed view:
 * contribution analyses carry per-segment effects, decompositions carry driver effects that sum to
 * the parent change (plus any residual). Anything else is shown as stored.
 */
export function readCalculation(a: Pick<ArtifactRecord, "data"> | null | undefined): Calculation {
  const d = a?.data;
  if (!d || typeof d !== "object" || !Object.keys(d).length) return null;
  const notes = Array.isArray(d.notes) ? d.notes.map(str) : [];
  if (Array.isArray(d.rows) && ("total_change" in d || "additive_valid" in d)) {
    return {
      kind: "contribution",
      method: str(d.method),
      additiveValid: d.additive_valid === true,
      totalCurrent: num(d.total_current),
      totalBaseline: num(d.total_baseline),
      totalChange: num(d.total_change),
      segmentCount: num(d.segment_count),
      notes,
      rows: (d.rows as Obj[]).map((r) => ({
        segment: str(r.segment),
        current: num(r.current),
        baseline: num(r.baseline),
        effect: num(r.effect),
        share: num(r.share_of_change),
        mix: num(r.mix_effect),
        rate: num(r.rate_effect),
        pct: num(r.pct_change),
      })),
    };
  }
  if (Array.isArray(d.effects) && "parent_change" in d) {
    return {
      kind: "decomposition",
      method: str(d.method),
      parentCurrent: num(d.parent_current),
      parentBaseline: num(d.parent_baseline),
      parentChange: num(d.parent_change),
      residual: num(d.residual),
      residualShare: num(d.residual_share),
      identityCurrent: num(d.identity_current),
      identityBaseline: num(d.identity_baseline),
      notes,
      effects: (d.effects as Obj[]).map((e) => ({
        metric: str(e.metric_id),
        relation: str(e.relation),
        current: num(e.current),
        baseline: num(e.baseline),
        effect: num(e.effect),
        share: num(e.share),
      })),
    };
  }
  if ("stdout" in d || "stderr" in d) return { kind: "python", stdout: str(d.stdout), stderr: str(d.stderr), error: d.error ? str(d.error) : null };
  return { kind: "raw", data: d as Obj };
}

const METHOD_TEXT: Record<string, string> = {
  additive: "Additive: each part's change; effects sum exactly to the total change.",
  volume_mix_rate: "Volume, mix and rate: each segment's effect = mix effect (share shift) + rate effect (value per unit).",
  ratio_mix_rate: "Ratio mix/rate: the ratio's change split into each segment's mix and rate effects over the true totals.",
  non_additive: "Not additive: segments overlap, so shares of the total are not defined; segment-level values only.",
  lmdi: "Multiplicative (LMDI log-mean Divisia): driver effects sum exactly to the parent change.",
  shapley: "Shapley: each driver's average marginal effect over all orders; effects sum to the parent change.",
  ratio_lmdi: "Ratio (LMDI): numerator and denominator effects sum exactly to the ratio's change.",
  ratio_shapley: "Ratio (Shapley): numerator and denominator effects sum to the ratio's change.",
};

function Num({ v, format, signed }: { v: number | null; format: string | null; signed?: boolean }) {
  if (v === null) return <span className="text-fg-faint">n/a</span>;
  const s = formatValue(v, format, { compact: true });
  return <>{signed && v > 0 ? `+${s}` : s}</>;
}

export function CalculationView({
  artifact,
  format = null,
  artifacts = [],
  onOpenArtifact,
}: {
  artifact: ArtifactRecord;
  format?: string | null;
  /** Artifacts available in the current context, so parent queries can be opened in place. */
  artifacts?: ArtifactRecord[];
  onOpenArtifact?: (id: string) => void;
}) {
  const ws = useOptionalWorkspace();
  const calc = readCalculation(artifact);
  const parents = artifact.parent_ids ?? [];
  const parentLinks = parents.length ? (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="text-fg-subtle">Computed from {parents.length} {parents.length === 1 ? "query" : "queries"}:</span>
      {parents.map((pid) => {
        const p = artifacts.find((a) => a.id === pid || a.engine_id === pid);
        const label = p?.title || pid.slice(0, 14);
        return onOpenArtifact && p ? (
          <button key={pid} type="button" className="rounded border border-border px-1.5 py-0.5 text-accent hover:bg-bg-subtle" onClick={() => onOpenArtifact(p.id)}>
            {label}
          </button>
        ) : ws ? (
          <Link key={pid} href={ws.href(`/lineage/artifact/${pid}`)} className="rounded border border-border px-1.5 py-0.5 text-accent hover:bg-bg-subtle">
            {label}
          </Link>
        ) : (
          <span key={pid} className="font-mono">
            {label}
          </span>
        );
      })}
    </div>
  ) : null;

  if (!calc)
    return (
      <div className="flex flex-col gap-2 p-3 text-sm text-fg-subtle">
        <p>{artifact.sql ? "This artifact is a direct query; its calculation is the SQL and the result grid." : "No calculation details were stored for this artifact."}</p>
        {parentLinks}
      </div>
    );

  if (calc.kind === "contribution") {
    const sumEffects = calc.rows.reduce((s, r) => s + (r.effect ?? 0), 0);
    return (
      <div className="flex flex-col gap-3 p-3" data-testid="calculation">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="outline">{humanize(calc.method)}</Badge>
          {calc.additiveValid ? (
            <Badge tone="positive">
              <CheckCircle2 /> Shares add up exactly
            </Badge>
          ) : (
            <Badge tone="warning">
              <AlertTriangle /> Not additive: shares not valid
            </Badge>
          )}
          <span className="text-xs text-fg-subtle">{METHOD_TEXT[calc.method] ?? ""}</span>
        </div>
        <DefinitionList
          className="max-w-xl"
          items={[
            { label: "Total current", value: <Num v={calc.totalCurrent} format={format} />, mono: true },
            { label: "Total baseline", value: <Num v={calc.totalBaseline} format={format} />, mono: true },
            { label: "Total change", value: <Num v={calc.totalChange} format={format} signed />, mono: true },
            {
              label: "Sum of segment effects",
              value: (
                <>
                  <Num v={sumEffects} format={format} signed /> across {calc.rows.length} segments
                  {calc.segmentCount !== null && calc.segmentCount > calc.rows.length ? ` (of ${calc.segmentCount})` : ""}
                </>
              ),
              mono: true,
            },
          ]}
        />
        <table className="w-full text-sm" aria-label="Segment effects">
          <thead className="text-left text-xs text-fg-subtle">
            <tr>
              <th className="px-2 py-1 font-medium">Segment</th>
              <th className="px-2 py-1 text-right font-medium">Baseline</th>
              <th className="px-2 py-1 text-right font-medium">Current</th>
              <th className="px-2 py-1 text-right font-medium">Change</th>
              <th className="px-2 py-1 text-right font-medium">Effect</th>
              <th className="px-2 py-1 text-right font-medium">Share of change</th>
              <th className="px-2 py-1 text-right font-medium">Mix effect</th>
              <th className="px-2 py-1 text-right font-medium">Rate effect</th>
            </tr>
          </thead>
          <tbody>
            {calc.rows.map((r) => (
              <tr key={r.segment} className="border-t border-border tabular">
                <td className="px-2 py-1">{r.segment || "(null)"}</td>
                <td className="px-2 py-1 text-right"><Num v={r.baseline} format={format} /></td>
                <td className="px-2 py-1 text-right"><Num v={r.current} format={format} /></td>
                <td className="px-2 py-1 text-right">{r.pct === null ? "n/a" : formatPctChange(r.pct)}</td>
                <td className="px-2 py-1 text-right"><Num v={r.effect} format={format} signed /></td>
                <td className="px-2 py-1 text-right">{calc.additiveValid && r.share !== null ? formatShare(r.share) : <span className="text-fg-faint" title="Not defined for overlapping segments">n/a</span>}</td>
                <td className="px-2 py-1 text-right"><Num v={r.mix} format={format} signed /></td>
                <td className="px-2 py-1 text-right"><Num v={r.rate} format={format} signed /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {calc.notes.length ? (
          <ul className="list-disc pl-5 text-xs text-fg-muted">
            {calc.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        ) : null}
        {parentLinks}
      </div>
    );
  }

  if (calc.kind === "decomposition") {
    return (
      <div className="flex flex-col gap-3 p-3" data-testid="calculation">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="outline">{humanize(calc.method)}</Badge>
          <span className="text-xs text-fg-subtle">{METHOD_TEXT[calc.method] ?? ""}</span>
        </div>
        <DefinitionList
          className="max-w-xl"
          items={[
            { label: "Parent baseline", value: <Num v={calc.parentBaseline} format={format} />, mono: true },
            { label: "Parent current", value: <Num v={calc.parentCurrent} format={format} />, mono: true },
            { label: "Parent change", value: <Num v={calc.parentChange} format={format} signed />, mono: true },
            {
              label: "Residual",
              value: (
                <>
                  <Num v={calc.residual} format={format} signed />
                  {calc.residualShare !== null ? ` (${formatShare(calc.residualShare)})` : ""}
                </>
              ),
              mono: true,
            },
          ]}
        />
        <table className="w-full text-sm" aria-label="Driver effects">
          <thead className="text-left text-xs text-fg-subtle">
            <tr>
              <th className="px-2 py-1 font-medium">Driver</th>
              <th className="px-2 py-1 font-medium">Relation</th>
              <th className="px-2 py-1 text-right font-medium">Baseline</th>
              <th className="px-2 py-1 text-right font-medium">Current</th>
              <th className="px-2 py-1 text-right font-medium">Effect on parent</th>
              <th className="px-2 py-1 text-right font-medium">Share</th>
            </tr>
          </thead>
          <tbody>
            {calc.effects.map((e) => (
              <tr key={e.metric} className="border-t border-border tabular">
                <td className="px-2 py-1 font-mono text-xs">{e.metric}</td>
                <td className="px-2 py-1 text-fg-muted">{humanize(e.relation)}</td>
                <td className="px-2 py-1 text-right">{formatValue(e.baseline, "number", { compact: true })}</td>
                <td className="px-2 py-1 text-right">{formatValue(e.current, "number", { compact: true })}</td>
                <td className="px-2 py-1 text-right"><Num v={e.effect} format={format} signed /></td>
                <td className="px-2 py-1 text-right">{e.share === null ? "n/a" : formatShare(e.share)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {calc.notes.length ? (
          <ul className="list-disc pl-5 text-xs text-fg-muted">
            {calc.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        ) : null}
        {parentLinks}
      </div>
    );
  }

  if (calc.kind === "python")
    return (
      <div className="flex flex-col gap-2 p-3" data-testid="calculation">
        {calc.error ? <p className="text-sm text-negative">Python error: {calc.error}</p> : null}
        {calc.stdout ? <CodeBlock code={calc.stdout} language="text" title="stdout" maxHeight={240} /> : null}
        {calc.stderr ? <CodeBlock code={calc.stderr} language="text" title="stderr" maxHeight={160} /> : null}
        {parentLinks}
      </div>
    );

  return (
    <div className="flex flex-col gap-2 p-3" data-testid="calculation">
      <CodeBlock code={JSON.stringify(calc.data, null, 2)} language="json" title="Stored calculation" maxHeight={320} />
      {parentLinks}
    </div>
  );
}
