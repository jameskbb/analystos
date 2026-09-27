"use client";
import { use } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { artifacts } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel, DefinitionList } from "@/components/shell/page";
import { LineageGraphView } from "@/components/analysis/lineage-graph";
import { FilterChips } from "@/components/analysis/filter-chips";
import { CodeBlock } from "@/components/code/code-block";
import { CalculationView } from "@/components/investigation/calculation";
import { DataGrid } from "@/components/data-grid/data-grid";
import { ArtifactChart } from "@/components/investigation/artifact-chart";
import { ValidationList } from "@/components/investigation/evidence-panel";
import { artifactResult, datasetVersions, filterChip, metricVersions, windowLabel } from "@/components/investigation/model";
import { ErrorState, LoadingState } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { formatDateTime, formatInt, humanize } from "@/lib/format";
import type { FilterContextItem, FilterSpec } from "@/lib/api/types";

export default function ArtifactLineagePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { id: ws, href } = useWorkspace();
  const art = useQuery({ queryKey: qk.artifact(ws, id), queryFn: () => artifacts.get(ws, id) });
  const lin = useQuery({ queryKey: qk.artifactLineage(ws, id), queryFn: () => artifacts.lineage(ws, id) });
  if (art.isLoading) return <LoadingState variant="block" className="h-full" label="Loading artifact" />;
  if (art.error) return <ErrorState error={art.error} onRetry={() => void art.refetch()} className="h-full" />;
  const a = art.data;
  if (!a) return null;
  const result = artifactResult(a);
  const chips: FilterContextItem[] = a.filter_context?.length
    ? a.filter_context
    : [
        ...(a.window ? [{ label: "Period", value: windowLabel(a.window), kind: "time" }] : []),
        ...((a.filters ?? []).filter((f): f is FilterSpec => "dimension" in f).map(filterChip)),
      ];
  return (
    <Page>
      <PageHeader
        breadcrumb={
          a.investigation_id ? (
            <Link href={href(`/investigate/${a.investigation_id}`)} className="hover:text-fg">
              Investigation
            </Link>
          ) : (
            "Artifact"
          )
        }
        title={a.title || `${humanize(a.kind)} ${a.id.slice(0, 8)}`}
        meta={
          <>
            <Badge>{humanize(a.kind)}</Badge>
            <span>{formatDateTime(a.created_at)}</span>
            {a.validation ? <Badge tone={a.validation.ok ? "positive" : "negative"}>{a.validation.ok ? "Validation passed" : "Validation failed"}</Badge> : null}
          </>
        }
      />
      <PageBody className="flex flex-col gap-4">
        <FilterChips items={chips} />
        <Panel title="Lineage" description="How this result traces back to source data">
          {lin.isLoading ? (
            <LoadingState variant="block" />
          ) : lin.error ? (
            <ErrorState error={lin.error} compact onRetry={() => void lin.refetch()} />
          ) : lin.data ? (
            <LineageGraphView graph={lin.data} highlightId={lin.data.root} className="p-3" />
          ) : null}
        </Panel>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="flex min-w-0 flex-col gap-4">
            {a.chart_spec || result ? (
              <Panel title="Chart">
                <div className="p-3">
                  <ArtifactChart artifact={a} filters={chips} />
                </div>
              </Panel>
            ) : null}
            {a.data && Object.keys(a.data).length ? (
              <Panel title="Calculation" description="How the numbers were computed from the input queries">
                <CalculationView artifact={a} />
              </Panel>
            ) : null}
            {a.sql ? (
              <Panel title="SQL">
                <div className="p-3">
                  <CodeBlock code={a.sql} />
                </div>
              </Panel>
            ) : null}
            {a.python ? (
              <Panel title="Python">
                <div className="p-3">
                  <CodeBlock code={a.python} language="python" />
                </div>
              </Panel>
            ) : null}
            {result ? (
              <Panel title="Result snapshot" description={`${formatInt(result.row_count)} rows${result.truncated ? " (truncated)" : ""}`}>
                <DataGrid columns={result.columns} rows={result.rows} height={320} />
              </Panel>
            ) : null}
          </div>
          <aside className="flex flex-col gap-4">
            <Panel title="Validation">
              <div className="p-3">
                <ValidationList artifacts={[a]} />
                {a.error ? <p className="mt-2 text-xs text-negative">{a.error}</p> : null}
              </div>
            </Panel>
            <Panel title="Reproducibility">
              <div className="p-3">
                <DefinitionList
                  items={[
                    ...metricVersions(a).map(([m, v]) => ({ label: humanize(m), value: v, mono: true })),
                    ...datasetVersions(a).map((d) => ({
                      label: d.table,
                      value: `${d.row_count !== null ? `${formatInt(d.row_count)} rows · ` : ""}${d.content_hash.slice(0, 12)}`,
                      mono: true,
                    })),
                    ...(a.parent_ids?.length ? [{ label: "Inputs", value: a.parent_ids.map((p) => p.slice(0, 8)).join(", "), mono: true }] : []),
                  ]}
                />
              </div>
            </Panel>
          </aside>
        </div>
      </PageBody>
    </Page>
  );
}
