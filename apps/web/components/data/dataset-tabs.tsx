"use client";
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronLeft, ChevronRight, GitMerge, History, Play, Plus, ShieldCheck, Sigma, Wand2 } from "lucide-react";
import { datasets, quality, relationships, type DatasetProfile, type DatasetVersionRecord } from "@/lib/api/resources/data";
import { metrics as metricsApi } from "@/lib/api/endpoints";
import type { Dataset, LineageGraph, QualityRun, QualityRuleKind } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Panel } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { NativeSelect } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { DataGrid } from "@/components/data-grid/data-grid";
import { CodeBlock } from "@/components/code/code-block";
import { LineageGraphView } from "@/components/analysis/lineage-graph";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { formatDateTime, formatInt, formatRelative, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RelationshipList } from "./relationships";
import { AddRelationshipDialog } from "./add-relationship-dialog";
import { RuleBuilderDialog } from "./rule-builder";
import { RulesTable, RunDetailSheet, RunHistory, useRunAllRules } from "./quality-rules";

/* ------------------------------------------------------------------ schema */

export function SchemaTab({
  dataset,
  profile,
  highlight,
}: {
  dataset: Dataset;
  profile: DatasetProfile | undefined;
  highlight: string | null;
}) {
  const { canEdit } = useWorkspace();
  const [rule, setRule] = React.useState<{ column: string; kind: QualityRuleKind } | null>(null);
  const rowRef = React.useRef<HTMLTableRowElement>(null);
  React.useEffect(() => {
    rowRef.current?.scrollIntoView({ block: "center" });
  }, [highlight]);
  const stats = new Map(profile?.columns.map((c) => [c.name, c]) ?? []);
  return (
    <Panel title={`${dataset.columns.length} columns`} description={`Stored as ${dataset.table_name} in the workspace DuckDB store`}>
      <div className="overflow-x-auto scrollbar-thin">
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-fg-subtle">
              <th className="w-10 px-3 py-1.5 text-right font-medium">#</th>
              <th className="px-3 py-1.5 font-medium">Column</th>
              <th className="px-3 py-1.5 font-medium">Type</th>
              <th className="px-3 py-1.5 font-medium">Nullable</th>
              <th className="px-3 py-1.5 font-medium">Semantic role</th>
              <th className="px-3 py-1.5 text-right font-medium">Null %</th>
              <th className="px-3 py-1.5 text-right font-medium">Distinct</th>
              <th className="px-3 py-1.5" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {dataset.columns.map((c, i) => {
              const s = stats.get(c.name);
              const hl = highlight === c.name;
              const nullPct = s ? (s.null_pct > 1 ? s.null_pct : s.null_pct * 100) : null;
              return (
                <tr
                  key={c.name}
                  ref={hl ? rowRef : undefined}
                  className={cn("border-b border-border last:border-b-0 hover:bg-bg-subtle", hl && "bg-accent-soft hover:bg-accent-soft")}
                  aria-current={hl ? "true" : undefined}
                >
                  <td className="px-3 py-1.5 text-right text-xs text-fg-faint tabular">{i + 1}</td>
                  <td className="px-3 py-1.5 font-mono text-xs font-medium">{c.name}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-fg-muted">{c.type}</td>
                  <td className="px-3 py-1.5 text-xs">{c.nullable === false ? "No" : "Yes"}</td>
                  <td className="px-3 py-1.5">
                    <div className="flex flex-wrap gap-1">
                      {(s?.semantic_roles ?? []).map((r) => (
                        <Badge key={r} tone="accent">
                          {humanize(r)}
                        </Badge>
                      ))}
                      {!s?.semantic_roles?.length ? <span className="text-xs text-fg-faint">n/a</span> : null}
                    </div>
                  </td>
                  <td className={cn("px-3 py-1.5 text-right text-xs tabular", nullPct && nullPct > 0 ? "text-warning" : "text-fg-subtle")}>
                    {nullPct === null ? "n/a" : `${nullPct.toFixed(nullPct < 10 ? 1 : 0)}%`}
                  </td>
                  <td className="px-3 py-1.5 text-right text-xs tabular">{s ? formatInt(s.distinct_count) : "n/a"}</td>
                  <td className="px-3 py-1.5 text-right">
                    {canEdit ? (
                      <Button size="xs" variant="ghost" onClick={() => setRule({ column: c.name, kind: "not_null" })} aria-label={`Add quality rule on ${c.name}`}>
                        <ShieldCheck /> Rule
                      </Button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <RuleBuilderDialog
        open={!!rule}
        onOpenChange={(o) => !o && setRule(null)}
        defaultTable={dataset.table_name}
        defaultColumn={rule?.column}
        defaultKind={rule?.kind}
      />
    </Panel>
  );
}

/* ------------------------------------------------------------------ preview */

const PAGE = 100;

export function PreviewTab({ dataset }: { dataset: Dataset }) {
  const { id: ws } = useWorkspace();
  const [offset, setOffset] = React.useState(0);
  const [orderBy, setOrderBy] = React.useState("");
  const [direction, setDirection] = React.useState<"asc" | "desc">("asc");
  const q = useQuery({
    queryKey: ["ws", ws, "datasets", dataset.id, "preview", { offset, orderBy, direction }],
    queryFn: () => datasets.preview(ws, dataset.id, { limit: PAGE, offset, order_by: orderBy || undefined, direction }),
    placeholderData: (prev) => prev,
  });
  const last = Math.min(offset + PAGE, dataset.row_count);
  return (
    <Panel
      title="Preview"
      description="Read-only rows from the workspace store; sort and page on the server."
      actions={
        <div className="flex items-center gap-1">
          <NativeSelect aria-label="Sort by column" className="h-6 w-40 text-xs" value={orderBy} onChange={(e) => { setOrderBy(e.target.value); setOffset(0); }}>
            <option value="">Storage order</option>
            {dataset.columns.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </NativeSelect>
          <NativeSelect aria-label="Sort direction" className="h-6 w-20 text-xs" value={direction} onChange={(e) => setDirection(e.target.value as "asc" | "desc")} disabled={!orderBy}>
            <option value="asc">Asc</option>
            <option value="desc">Desc</option>
          </NativeSelect>
          <span className="px-1 text-xs text-fg-subtle tabular">
            {dataset.row_count ? `${formatInt(offset + 1)}–${formatInt(last)} of ${formatInt(dataset.row_count)}` : ""}
          </span>
          <Button size="icon-xs" variant="ghost" aria-label="Previous page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            <ChevronLeft />
          </Button>
          <Button size="icon-xs" variant="ghost" aria-label="Next page" disabled={last >= dataset.row_count} onClick={() => setOffset(offset + PAGE)}>
            <ChevronRight />
          </Button>
        </div>
      }
    >
      {q.isLoading ? (
        <LoadingState rows={8} className="px-3" />
      ) : q.error ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : q.data ? (
        <div className={cn(q.isFetching && "opacity-70")}>
          <DataGrid columns={q.data.columns} rows={q.data.rows} height={520} emptyMessage="This dataset has no rows." />
        </div>
      ) : null}
    </Panel>
  );
}

/* ------------------------------------------------------------------ freshness / versions */

function columnDiff(prev: DatasetVersionRecord | undefined, cur: DatasetVersionRecord) {
  if (!prev) return { added: [], removed: [], retyped: [] as string[] };
  const p = new Map(prev.columns.map((c) => [c.name, c.type]));
  const c = new Map(cur.columns.map((x) => [x.name, x.type]));
  return {
    added: [...c.keys()].filter((k) => !p.has(k)),
    removed: [...p.keys()].filter((k) => !c.has(k)),
    retyped: [...c.entries()].filter(([k, t]) => p.has(k) && p.get(k) !== t).map(([k]) => k),
  };
}

export function FreshnessTab({ dataset }: { dataset: Dataset }) {
  const { id: ws } = useWorkspace();
  const q = useQuery({ queryKey: ["ws", ws, "datasets", dataset.id, "versions"], queryFn: () => datasets.versions(ws, dataset.id) });
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <span>
          <span className="text-fg-subtle">Last updated</span> {formatDateTime(dataset.updated_at ?? dataset.created_at)} ({formatRelative(dataset.updated_at ?? dataset.created_at)})
        </span>
        <span>
          <span className="text-fg-subtle">Profiled</span> {dataset.profiled_at ? formatDateTime(dataset.profiled_at) : "not yet"}
        </span>
        <span>
          <span className="text-fg-subtle">Created</span> {formatDateTime(dataset.created_at)}
        </span>
      </div>
      <Panel title="Version history" description="Each ingest records a content hash and row count; analyses cite the version they used.">
        {q.isLoading ? (
          <LoadingState rows={3} className="px-3" />
        ) : q.error ? (
          <ErrorState error={q.error} compact onRetry={() => void q.refetch()} />
        ) : !q.data?.length ? (
          <EmptyState compact icon={History} title="No versions recorded" description="Versions are captured at each ingest or snapshot." />
        ) : (
          <ol className="relative" aria-label="Dataset versions">
            {q.data.map((v, i) => {
              const prev = q.data[i + 1];
              const delta = prev ? v.row_count - prev.row_count : null;
              const diff = columnDiff(prev, v);
              const current = v.id === dataset.current_version_id;
              return (
                <li key={v.id} className="flex gap-3 border-b border-border px-3 py-2.5 last:border-b-0">
                  <div className="flex flex-col items-center pt-1">
                    <span className={cn("size-2 rounded-full", current ? "bg-accent" : "bg-border-strong")} aria-hidden />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium">Version {v.version_no}</span>
                      {current ? <Badge tone="accent">Current</Badge> : null}
                      <span className="text-xs text-fg-subtle" title={formatDateTime(v.captured_at)}>
                        {formatDateTime(v.captured_at)}
                      </span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-fg-muted">
                      <span className="tabular">
                        {formatInt(v.row_count)} rows
                        {delta !== null && delta !== 0 ? (
                          <span className={delta > 0 ? "text-positive" : "text-negative"}> ({delta > 0 ? "+" : "−"}{formatInt(Math.abs(delta))})</span>
                        ) : null}
                      </span>
                      <span>{v.columns.length} columns</span>
                      <span className="font-mono" title={v.content_hash}>
                        hash {v.content_hash.slice(0, 16)}
                      </span>
                      {prev && prev.content_hash === v.content_hash ? <span className="text-fg-subtle">content unchanged</span> : null}
                    </div>
                    {diff.added.length || diff.removed.length || diff.retyped.length ? (
                      <div className="mt-1 flex flex-wrap gap-1 text-2xs">
                        {diff.added.map((c) => <Badge key={`a${c}`} tone="positive">+ {c}</Badge>)}
                        {diff.removed.map((c) => <Badge key={`r${c}`} tone="negative">− {c}</Badge>)}
                        {diff.retyped.map((c) => <Badge key={`t${c}`} tone="warning">type changed: {c}</Badge>)}
                      </div>
                    ) : null}
                    {Object.keys(v.ingest_options ?? {}).length ? (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-xs text-fg-subtle hover:text-fg">Ingest options</summary>
                        <CodeBlock code={JSON.stringify(v.ingest_options, null, 2)} language="json" className="mt-1" maxHeight={180} />
                      </details>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </Panel>
    </div>
  );
}

/* ------------------------------------------------------------------ lineage */

/** API lineage node ids look like "metric:revenue"; derive navigation refs when absent. */
export function withRefs(graph: LineageGraph): LineageGraph {
  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      if (n.ref_id) return n;
      const [prefix, ...rest] = n.id.split(":");
      const ref = rest.join(":");
      if (!ref) return n;
      if (prefix === "metric" || prefix === "dataset") return { ...n, ref_id: ref };
      return n;
    }),
  };
}

export function LineageTab({ dataset }: { dataset: Dataset }) {
  const { id: ws, href } = useWorkspace();
  const q = useQuery({ queryKey: ["ws", ws, "datasets", dataset.id, "usage"], queryFn: () => datasets.usage(ws, dataset.id) });
  if (q.isLoading) return <LoadingState rows={4} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const u = q.data!;
  const groups: [string, string[], ((s: string) => string) | null][] = [
    ["Entities", u.entities, null],
    ["Dimensions", u.dimensions, null],
    ["Metrics", u.metrics, (m) => href(`/metrics/${encodeURIComponent(m)}`)],
    ["Quality rules", u.quality_rules, () => href(`/data/quality?dataset=${dataset.id}`)],
    ["Relationships", u.relationships, () => href("/data/relationships")],
    ["Saved queries", u.saved_queries, null],
  ];
  return (
    <div className="flex flex-col gap-4">
      <Panel title="Lineage" description="Source → dataset → semantic entities → metrics">
        <LineageGraphView graph={withRefs(u.lineage)} className="p-3" highlightId={`dataset:${dataset.id}`} />
      </Panel>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {groups.map(([label, items, link]) => (
          <Panel key={label} title={`${label} (${items.length})`}>
            {items.length ? (
              <ul className="flex flex-col px-3 py-2 text-sm">
                {items.map((it) => (
                  <li key={it} className="truncate py-0.5 font-mono text-xs">
                    {link ? (
                      <Link href={link(it)} className="hover:text-accent hover:underline">
                        {it}
                      </Link>
                    ) : (
                      it
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-3 py-2 text-xs text-fg-subtle">None</p>
            )}
          </Panel>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ metrics using this dataset */

export function MetricsTab({ dataset }: { dataset: Dataset }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const ids = useQuery({ queryKey: ["ws", ws, "datasets", dataset.id, "metrics"], queryFn: () => datasets.metrics(ws, dataset.id) });
  const all = useQuery({ queryKey: ["ws", ws, "metrics"], queryFn: () => metricsApi.list(ws), enabled: !!ids.data?.length });
  if (ids.isLoading) return <LoadingState rows={4} />;
  if (ids.error) return <ErrorState error={ids.error} onRetry={() => void ids.refetch()} />;
  if (!ids.data?.length)
    return (
      <Panel>
        <EmptyState
          icon={Sigma}
          title="No metrics use this dataset yet"
          description="Define a metric on this table (for example SUM of an amount column) so investigations can use it."
          action={canEdit ? <Button asChild variant="primary"><Link href={href("/metrics?new=1")}><Plus /> Define a metric</Link></Button> : undefined}
        />
      </Panel>
    );
  const byName = new Map((all.data ?? []).map((m) => [m.name, m]));
  const byId = new Map((all.data ?? []).map((m) => [m.id, m]));
  return (
    <Panel title={`${ids.data.length} metrics computed from ${dataset.name}`} description="Directly, or through ratio and derived metrics.">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-fg-subtle">
            <th className="px-3 py-1.5 font-medium">Metric</th>
            <th className="px-3 py-1.5 font-medium">Kind</th>
            <th className="px-3 py-1.5 font-medium">Definition</th>
            <th className="px-3 py-1.5 font-medium">Format</th>
            <th className="px-3 py-1.5 text-right font-medium">Version</th>
          </tr>
        </thead>
        <tbody>
          {ids.data.map((mid) => {
            const m = byName.get(mid) ?? byId.get(mid);
            return (
              <tr key={mid} className="border-b border-border last:border-b-0 hover:bg-bg-subtle">
                <td className="px-3 py-1.5">
                  <Link href={href(`/metrics/${encodeURIComponent(m?.id ?? mid)}`)} className="font-medium hover:text-accent hover:underline">
                    {m?.label ?? mid}
                  </Link>
                  <div className="font-mono text-2xs text-fg-subtle">{mid}</div>
                </td>
                <td className="px-3 py-1.5 text-xs">{m ? humanize(m.kind) : "n/a"}</td>
                <td className="max-w-md px-3 py-1.5 font-mono text-xs text-fg-muted">
                  <span className="line-clamp-2">
                    {m ? m.formula ?? (m.agg ? `${m.agg.toUpperCase()}(${m.expr ?? ""})` : m.expr ?? (m.numerator ? `${m.numerator} / ${m.denominator}` : "")) : ""}
                  </span>
                </td>
                <td className="px-3 py-1.5 text-xs">{m ? humanize(m.format) : "n/a"}</td>
                <td className="px-3 py-1.5 text-right font-mono text-xs">{m ? `v${m.version}` : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

/* ------------------------------------------------------------------ relationships */

export function RelationshipsTab({ dataset }: { dataset: Dataset }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const [adding, setAdding] = React.useState(false);
  const q = useQuery({
    queryKey: ["ws", ws, "relationships", { table: dataset.table_name }],
    queryFn: () => relationships.list(ws, { table: dataset.table_name }),
  });
  return (
    <Panel
      title="Relationships"
      description="Only approved relationships are used in joins."
      actions={
        <>
          <Button size="xs" asChild variant="ghost">
            <Link href={href("/data/relationships")}>
              <GitMerge /> All relationships
            </Link>
          </Button>
          {canEdit ? (
            <Button size="xs" onClick={() => setAdding(true)}>
              <Plus /> Add
            </Button>
          ) : null}
        </>
      }
    >
      {q.isLoading ? (
        <LoadingState rows={3} className="px-3" />
      ) : q.error ? (
        <ErrorState error={q.error} compact onRetry={() => void q.refetch()} />
      ) : (
        <RelationshipList
          items={q.data ?? []}
          empty={
            <EmptyState
              compact
              icon={GitMerge}
              title="No relationships involve this table"
              description="Run discovery on the Relationships page or declare one manually."
            />
          }
        />
      )}
      <AddRelationshipDialog open={adding} onOpenChange={setAdding} defaultFromTable={dataset.table_name} />
    </Panel>
  );
}

/* ------------------------------------------------------------------ quality */

export function QualityTab({ dataset }: { dataset: Dataset }) {
  const { id: ws, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const [builder, setBuilder] = React.useState(false);
  const [openRun, setOpenRun] = React.useState<QualityRun | null>(null);
  const rules = useQuery({
    queryKey: ["ws", ws, "quality", "rules", { dataset_id: dataset.id }],
    queryFn: () => quality.rules(ws, { dataset_id: dataset.id }),
  });
  const runs = useQuery({
    queryKey: ["ws", ws, "quality", "runs", { dataset_id: dataset.id, limit: 20 }],
    queryFn: () => quality.runs(ws, { dataset_id: dataset.id, limit: 20 }),
  });
  const runAll = useRunAllRules(dataset.id);
  const suggest = useMutation({
    mutationFn: () => quality.suggest(ws, dataset.id),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["ws", ws, "quality"] });
      toast.success(r.length ? `${r.length} rules suggested` : "No new rules to suggest");
    },
    onError: (e: Error) => toast.error("Could not suggest rules", { description: e.message }),
  });
  const list = rules.data ?? [];
  const active = list.filter((r) => r.status !== "suggested");
  const suggested = list.filter((r) => r.status === "suggested");
  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="Quality rules"
        description="Rules detect problems; AnalystOS never modifies your data."
        actions={
          canEdit ? (
            <>
              <Button size="xs" onClick={() => suggest.mutate()} disabled={suggest.isPending}>
                {suggest.isPending ? <Spinner /> : <Wand2 />} Suggest
              </Button>
              <Button size="xs" onClick={() => setBuilder(true)}>
                <Plus /> New rule
              </Button>
              <Button size="xs" variant="primary" onClick={() => runAll.mutate()} disabled={runAll.isPending || !active.length}>
                {runAll.isPending ? <Spinner className="text-accent-fg" /> : <Play />} Run all
              </Button>
            </>
          ) : null
        }
      >
        {rules.isLoading ? (
          <LoadingState rows={3} className="px-3" />
        ) : rules.error ? (
          <ErrorState error={rules.error} compact onRetry={() => void rules.refetch()} />
        ) : !list.length ? (
          <EmptyState
            compact
            icon={ShieldCheck}
            title="No rules for this dataset"
            description="Suggest rules from the profile or write your own."
            action={canEdit ? <Button size="xs" variant="primary" onClick={() => suggest.mutate()}><Wand2 /> Suggest rules</Button> : undefined}
          />
        ) : (
          <>
            {suggested.length ? (
              <div className="border-b border-border">
                <div className="px-3 pt-2 text-xs font-medium text-fg-muted">Suggested ({suggested.length})</div>
                <RulesTable rules={suggested} onOpenRun={setOpenRun} showTable={false} />
              </div>
            ) : null}
            {active.length ? <RulesTable rules={active} onOpenRun={setOpenRun} showTable={false} /> : null}
          </>
        )}
      </Panel>
      <Panel title="Recent runs">
        {runs.isLoading ? (
          <LoadingState rows={2} className="px-3" />
        ) : runs.error ? (
          <ErrorState error={runs.error} compact onRetry={() => void runs.refetch()} />
        ) : !runs.data?.length ? (
          <p className="px-3 py-3 text-sm text-fg-subtle">No runs yet.</p>
        ) : (
          <RunHistory runs={runs.data} onOpenRun={setOpenRun} />
        )}
      </Panel>
      <RuleBuilderDialog open={builder} onOpenChange={setBuilder} defaultTable={dataset.table_name} />
      <RunDetailSheet run={openRun} onOpenChange={(o) => !o && setOpenRun(null)} />
    </div>
  );
}

