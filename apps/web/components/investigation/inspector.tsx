"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import type { ArtifactRecord } from "@/lib/api/types";
import { artifacts as artifactsApi } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { NativeSelect } from "@/components/ui/input";
import { CodeBlock } from "@/components/code/code-block";
import { DataGrid } from "@/components/data-grid/data-grid";
import { LineageGraphView } from "@/components/analysis/lineage-graph";
import { ErrorState, LoadingState, EmptyState } from "@/components/states/states";
import { formatDuration, formatInt } from "@/lib/format";
import { artifactResult } from "./model";
import { ValidationList } from "./evidence-panel";
import { CalculationView } from "./calculation";

export type InspectorTab = "calculation" | "sql" | "python" | "result" | "validation" | "lineage";

function ArtifactLineage({ artifactId }: { artifactId: string }) {
  const { id: ws } = useWorkspace();
  const q = useQuery({ queryKey: qk.artifactLineage(ws, artifactId), queryFn: () => artifactsApi.lineage(ws, artifactId) });
  if (q.isLoading) return <LoadingState variant="block" label="Loading lineage" />;
  if (q.error) return <ErrorState error={q.error} compact onRetry={() => void q.refetch()} />;
  return q.data ? <LineageGraphView graph={q.data} highlightId={q.data.root} className="p-2" /> : null;
}

/**
 * Bottom inspector: the calculation (method, totals, per-segment or per-driver effects), SQL, Python,
 * result grid, validation and lineage for the selected node's artifacts.
 */
export function Inspector({
  artifacts,
  tab,
  onTabChange,
  artifactId,
  onArtifactChange,
  format = null,
}: {
  /** Number format of the selected node's metric, for calculation values. */
  format?: string | null;
  artifacts: ArtifactRecord[];
  tab: InspectorTab;
  onTabChange: (t: InspectorTab) => void;
  artifactId: string | null;
  onArtifactChange: (id: string) => void;
}) {
  // Default to the first artifact with executable logic; derived artifacts (no SQL/Python) come last.
  // On the Calculation tab, default to the derived artifact that carries the computation.
  const current =
    artifacts.find((a) => a.id === artifactId || a.engine_id === artifactId) ??
    (tab === "calculation" ? artifacts.find((a) => a.data && Object.keys(a.data).length && !a.sql) : undefined) ??
    artifacts.find((a) => a.sql || a.python) ??
    artifacts[0] ??
    null;
  const result = artifactResult(current);
  return (
    <Tabs value={tab} onValueChange={(v) => onTabChange(v as InspectorTab)} className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border pr-2">
        <TabsList className="h-8 flex-1 border-b-0">
          <TabsTrigger value="calculation" className="h-8">Calculation</TabsTrigger>
          <TabsTrigger value="sql" className="h-8">SQL</TabsTrigger>
          <TabsTrigger value="python" className="h-8">Python</TabsTrigger>
          <TabsTrigger value="result" className="h-8">
            Result{result ? <span className="text-2xs text-fg-subtle tabular">{formatInt(result.row_count)}</span> : null}
          </TabsTrigger>
          <TabsTrigger value="validation" className="h-8">Validation</TabsTrigger>
          <TabsTrigger value="lineage" className="h-8">Lineage</TabsTrigger>
        </TabsList>
        {artifacts.length > 1 ? (
          <NativeSelect
            aria-label="Artifact"
            className="h-6 max-w-64 text-xs"
            value={current?.id ?? ""}
            onChange={(e) => onArtifactChange(e.target.value)}
          >
            {artifacts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title || `${a.kind} ${a.id.slice(0, 8)}`}
              </option>
            ))}
          </NativeSelect>
        ) : null}
      </div>
      {!current ? (
        <EmptyState compact title="No artifacts for this node" description="Select a node backed by an executed query to see its SQL, result and lineage." />
      ) : (
        <>
          <TabsContent value="calculation" className="min-h-0 flex-1 overflow-auto">
            <CalculationView artifact={current} format={format} artifacts={artifacts} onOpenArtifact={onArtifactChange} />
          </TabsContent>
          <TabsContent value="sql" className="min-h-0 flex-1 overflow-auto p-2">
            {current.sql ? (
              <>
                <CodeBlock code={current.sql} />
                {current.error ? <p className="mt-2 text-xs text-negative">Execution error: {current.error}</p> : null}
              </>
            ) : (
              <p className="p-2 text-sm text-fg-subtle">This artifact has no SQL (it is computed from other artifacts: {current.parent_ids?.length ?? 0} inputs).</p>
            )}
          </TabsContent>
          <TabsContent value="python" className="min-h-0 flex-1 overflow-auto p-2">
            {current.python ? <CodeBlock code={current.python} language="python" /> : <p className="p-2 text-sm text-fg-subtle">No Python was used for this artifact.</p>}
          </TabsContent>
          <TabsContent value="result" className="flex min-h-0 flex-1 flex-col">
            {result ? (
              <>
                <DataGrid columns={result.columns} rows={result.rows} className="min-h-0 flex-1" />
                <div className="flex gap-3 border-t border-border px-2 py-1 text-2xs text-fg-subtle tabular">
                  <span>{formatInt(result.row_count)} rows</span>
                  {result.truncated ? <span className="text-warning">snapshot truncated to {formatInt(result.rows.length)}</span> : null}
                  {result.elapsed_ms ? <span>{formatDuration(result.elapsed_ms)}</span> : null}
                </div>
              </>
            ) : (
              <p className="p-3 text-sm text-fg-subtle">No result snapshot stored for this artifact.</p>
            )}
          </TabsContent>
          <TabsContent value="validation" className="min-h-0 flex-1 overflow-auto p-3">
            <ValidationList artifacts={[current]} />
          </TabsContent>
          <TabsContent value="lineage" className="min-h-0 flex-1 overflow-auto">
            {tab === "lineage" ? <ArtifactLineage artifactId={current.id} /> : null}
          </TabsContent>
        </>
      )}
    </Tabs>
  );
}
