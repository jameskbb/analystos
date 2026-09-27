"use client";
import * as React from "react";
import { use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Activity, AlertTriangle, Archive, ChevronRight, GitBranch, History, Pencil, RotateCcw, Share2, BookOpen, GitCompare, TrendingUp } from "lucide-react";
import type { Metric } from "@/lib/api/types";
import {
  dimensions as dimensionsApi,
  glossary as glossaryApi,
  metrics as metricsApi,
  semantic,
  type MetricVersionEntry,
} from "@/lib/api/resources/semantic";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { DefinitionList, Page, PageBody, PageHeader, Panel, SectionLabel } from "@/components/shell/page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { NativeSelect } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { CodeBlock } from "@/components/code/code-block";
import { ChartView } from "@/components/charts/chart-view";
import { LineageGraphView } from "@/components/analysis/lineage-graph";
import { StatusBadge } from "@/components/analysis/status";
import { EmptyState, ErrorState, InlineError, LoadingState, QueryState } from "@/components/states/states";
import { MetricFormDialog } from "@/components/semantic/metric-form-dialog";
import { SemanticNav } from "@/components/semantic/semantic-nav";
import {
  findSynonymConflicts,
  formulaRefs,
  formulaSummary,
  metricBaseEntities,
  reachableEntities,
  termCandidates,
} from "@/components/semantic/metric-utils";
import { formatDateTime, formatRelative, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

const GRAINS = ["day", "week", "month", "quarter", "year"];

function ExampleValues({ metric }: { metric: Metric }) {
  const { id: ws } = useWorkspace();
  const [dimension, setDimension] = React.useState("");
  const [grain, setGrain] = React.useState("month");
  const dims = useQuery({ queryKey: qk.dimensions(ws), queryFn: () => dimensionsApi.list(ws) });
  const ex = useQuery({
    queryKey: qk.metricPart(ws, metric.id, "examples", { dimension, grain, v: metric.version_no }),
    queryFn: () => metricsApi.examples(ws, metric.id, { dimension: dimension || undefined, grain, periods: dimension ? 25 : 18 }),
  });
  const catDims = (dims.data ?? []).filter((d) => d.type !== "time");
  return (
    <Panel
      title="Example values"
      description="Computed live from the current data with this metric version."
      actions={
        <div className="flex items-center gap-1.5">
          <NativeSelect aria-label="Break down by" value={dimension} onChange={(e) => setDimension(e.target.value)} className="h-6 w-40 text-xs">
            <option value="">Over time</option>
            {catDims.map((d) => (
              <option key={d.name} value={d.name}>
                By {d.label || d.name}
              </option>
            ))}
          </NativeSelect>
          {!dimension ? (
            <NativeSelect aria-label="Time grain" value={grain} onChange={(e) => setGrain(e.target.value)} className="h-6 w-24 text-xs">
              {GRAINS.map((g) => (
                <option key={g} value={g}>
                  {humanize(g)}
                </option>
              ))}
            </NativeSelect>
          ) : null}
        </div>
      }
    >
      <div className="p-3">
        {ex.isLoading ? (
          <LoadingState variant="block" label="Computing example values" className="h-64" />
        ) : ex.error ? (
          <ErrorState error={ex.error} compact onRetry={() => void ex.refetch()} title="Could not compute example values" />
        ) : ex.data && ex.data.result.rows.length ? (
          <>
            <ChartView
              key={`${dimension}-${grain}`}
              result={ex.data.result}
              title={`${metric.label ?? metric.id}${dimension ? ` by ${dimension}` : ` by ${grain}`}`}
              height={260}
              config={
                dimension
                  ? { type: "bar", x: ex.data.result.columns[0]?.name, y: [metric.id], format: metric.format, horizontal: true, sort: { field: metric.id, direction: "desc" } }
                  : { type: "line", x: ex.data.result.columns[0]?.name, y: [metric.id], format: metric.format }
              }
              provenance={{
                metric: metric.label ?? metric.id,
                metric_version: ex.data.version_no,
                dimensions: dimension ? [dimension] : ex.data.time_dimension ? [`${ex.data.time_dimension} (${grain})`] : [],
                filters: [],
                sql: ex.data.compiled.sql,
                calculation: formulaSummary(metric),
                sort: dimension ? `${metric.id} descending, top 25` : `${ex.data.time_dimension ?? "period"} ascending (latest 18 periods)`,
              }}
            />
            {ex.data.compiled.warnings?.length ? (
              <ul className="mt-2 flex flex-col gap-1">
                {ex.data.compiled.warnings.map((wn, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-xs text-warning">
                    <AlertTriangle className="mt-0.5 size-3 shrink-0" /> {wn}
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <EmptyState compact title="No values" description="The metric compiled but returned no rows. Check that its datasets contain data." />
        )}
      </div>
    </Panel>
  );
}

function VersionHistory({ metric }: { metric: Metric }) {
  const { id: ws, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const versions = useQuery({ queryKey: qk.metricPart(ws, metric.id, "versions"), queryFn: () => metricsApi.versions(ws, metric.id) });
  const [from, setFrom] = React.useState<number | null>(null);
  const [to, setTo] = React.useState<number | null>(null);
  const [restoring, setRestoring] = React.useState<MetricVersionEntry | null>(null);
  React.useEffect(() => {
    const vs = versions.data;
    if (vs && vs.length >= 2 && from === null) {
      const sorted = [...vs].sort((a, b) => b.version_no - a.version_no);
      setTo(sorted[0].version_no);
      setFrom(sorted[1].version_no);
    }
  }, [versions.data, from]);
  const diff = useQuery({
    queryKey: qk.metricPart(ws, metric.id, "diff", { from, to }),
    queryFn: () => metricsApi.diff(ws, metric.id, from!, to!),
    enabled: from !== null && to !== null && from !== to,
  });
  const restore = useMutation({
    mutationFn: (v: number) => metricsApi.restore(ws, metric.id, v),
    onSuccess: (m) => {
      void qc.invalidateQueries({ queryKey: qk.metrics(ws) });
      setRestoring(null);
      toast.success(`Restored as version ${m.version_no ?? m.version}`);
    },
    onError: (e: Error) => toast.error("Restore failed", { description: e.message }),
  });
  return (
    <QueryState query={versions} empty={<EmptyState compact icon={History} title="No versions recorded" />}>
      {(vs) => {
        const sorted = [...vs].sort((a, b) => b.version_no - a.version_no);
        return (
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <ol className="flex flex-col rounded-md border border-border" aria-label="Metric versions">
              {sorted.map((v) => (
                <li key={v.version_id} className="flex items-start gap-3 border-b border-border/70 px-3 py-2 last:border-b-0">
                  <span className="mt-0.5 font-mono text-xs tabular">v{v.version_no}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 text-sm">
                      {v.change_note || "No change note"}
                      {v.is_current ? <Badge tone="accent">Current</Badge> : null}
                    </div>
                    <code className="block truncate font-mono text-2xs text-fg-subtle">{formulaSummary(v.definition)}</code>
                    <div className="text-2xs text-fg-subtle" title={formatDateTime(v.created_at)}>
                      {formatRelative(v.created_at)}
                      {v.created_by ? ` · ${v.created_by}` : ""} · {v.definition_hash.slice(0, 10)}
                    </div>
                  </div>
                  {!v.is_current && canEdit ? (
                    <Button size="xs" variant="ghost" onClick={() => setRestoring(v)} aria-label={`Restore version ${v.version_no}`}>
                      <RotateCcw /> Restore
                    </Button>
                  ) : null}
                </li>
              ))}
            </ol>
            <div className="rounded-md border border-border">
              <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-sm">
                <GitCompare className="size-3.5 text-fg-subtle" />
                Compare
                <NativeSelect aria-label="From version" className="h-6 w-20 text-xs" value={from ?? ""} onChange={(e) => setFrom(Number(e.target.value))}>
                  {sorted.map((v) => (
                    <option key={v.version_no} value={v.version_no}>
                      v{v.version_no}
                    </option>
                  ))}
                </NativeSelect>
                →
                <NativeSelect aria-label="To version" className="h-6 w-20 text-xs" value={to ?? ""} onChange={(e) => setTo(Number(e.target.value))}>
                  {sorted.map((v) => (
                    <option key={v.version_no} value={v.version_no}>
                      v{v.version_no}
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <div className="p-3">
                {vs.length < 2 ? (
                  <p className="text-sm text-fg-subtle">Only one version exists. Edits create new versions you can compare here.</p>
                ) : from === to ? (
                  <p className="text-sm text-fg-subtle">Choose two different versions.</p>
                ) : diff.isLoading ? (
                  <LoadingState rows={3} />
                ) : diff.error ? (
                  <InlineError error={diff.error} />
                ) : diff.data?.changes.length ? (
                  <table className="w-full text-xs" aria-label="Version differences">
                    <thead>
                      <tr className="text-left text-fg-subtle">
                        <th className="pb-1 font-medium">Field</th>
                        <th className="pb-1 font-medium">v{from}</th>
                        <th className="pb-1 font-medium">v{to}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diff.data.changes.map((c, i) => (
                        <tr key={i} className="border-t border-border/70 align-top">
                          <td className="py-1 pr-2 font-medium">{String(c.field ?? c.path ?? "")}</td>
                          <td className="py-1 pr-2 font-mono break-all text-negative">{JSON.stringify(c.before ?? c.old ?? null)}</td>
                          <td className="py-1 font-mono break-all text-positive">{JSON.stringify(c.after ?? c.new ?? null)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="text-sm text-fg-subtle">No definition differences between these versions.</p>
                )}
              </div>
            </div>
            <ConfirmDialog
              open={!!restoring}
              onOpenChange={(o) => !o && setRestoring(null)}
              title={`Restore version ${restoring?.version_no}?`}
              description="Restoring saves the old definition as a new version. Nothing is deleted, and analyses keep the version they ran with."
              confirmLabel="Restore"
              pending={restore.isPending}
              onConfirm={() => restoring && restore.mutate(restoring.version_no)}
            />
          </div>
        );
      }}
    </QueryState>
  );
}

function MetricDetail({ id }: { id: string }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const router = useRouter();
  const qc = useQueryClient();
  const metric = useQuery({ queryKey: qk.metric(ws, id), queryFn: () => metricsApi.get(ws, id) });
  const all = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws) });
  const model = useQuery({ queryKey: qk.semantic(ws, "model"), queryFn: () => semantic.model(ws) });
  const lineage = useQuery({ queryKey: qk.metricPart(ws, id, "lineage"), queryFn: () => metricsApi.lineage(ws, id) });
  const invs = useQuery({ queryKey: qk.metricPart(ws, id, "investigations"), queryFn: () => metricsApi.investigations(ws, id) });
  const terms = useQuery({ queryKey: qk.glossary(ws), queryFn: () => glossaryApi.list(ws) });
  const [editing, setEditing] = React.useState(false);
  const [archiving, setArchiving] = React.useState(false);
  const archive = useMutation({
    mutationFn: () => metricsApi.archive(ws, id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.metrics(ws) });
      toast.success("Metric archived");
      router.push(href("/metrics"));
    },
    onError: (e: Error) => toast.error("Could not archive", { description: e.message }),
  });

  if (metric.isLoading) return <LoadingState variant="block" label="Loading metric" />;
  if (metric.error || !metric.data)
    return <ErrorState error={metric.error ?? "Metric not found"} onRetry={() => void metric.refetch()} className="h-full" />;
  const m = metric.data;

  const conflicts = findSynonymConflicts(all.data ?? []).filter((c) => c.metricIds.includes(m.id));
  const relatedTerms = (terms.data ?? []).filter(
    (t) => t.metric_id === m.id || termCandidates(t, []).includes(m.id) || (t.candidate_metric_ids ?? []).includes(m.id),
  );
  const spec = model.data?.model;
  const base = spec ? metricBaseEntities(spec, m.id) : [];
  const reachable = spec ? reachableEntities(spec, base) : [];
  const availableDims = spec ? spec.dimensions.filter((d) => reachable.includes(d.entity)) : [];
  const datasetNodes = (lineage.data?.nodes ?? []).filter((n) => n.kind === "dataset" || n.kind === "source_table" || n.kind === "table");
  const inputs = m.kind === "ratio" ? [m.numerator, m.denominator].filter(Boolean) as string[] : m.kind === "derived" ? formulaRefs(m.formula ?? "", (all.data ?? []).map((x) => x.id)) : [];
  const labelOf = (mid: string) => all.data?.find((x) => x.id === mid)?.label ?? mid;

  return (
    <Page>
      <PageHeader
        breadcrumb={
          <>
            <Link href={href("/metrics")} className="hover:text-fg">
              Metrics
            </Link>
            <ChevronRight className="size-3" />
            <span className="font-mono">{m.id}</span>
          </>
        }
        title={
          <span className="flex items-center gap-2">
            {m.label ?? m.id}
            {m.canonical ? <Badge tone="positive">Canonical</Badge> : <Badge tone="outline">Non-canonical</Badge>}
            <Badge tone="neutral" className="font-mono">
              v{m.version_no ?? m.version}
            </Badge>
            {m.archived ? <Badge tone="warning">Archived</Badge> : null}
          </span>
        }
        description={m.description || undefined}
        meta={<SemanticNav />}
        actions={
          <>
            <Button asChild>
              <Link href={href(`/investigate?new=1&q=${encodeURIComponent(`Why did ${m.label ?? m.id} change last month?`)}`)}>
                <GitBranch /> Investigate
              </Link>
            </Button>
            <Button asChild>
              <Link href={href(`/analyze?tab=forecast&metric=${encodeURIComponent(m.id)}`)}>
                <TrendingUp /> Forecast
              </Link>
            </Button>
            <Button asChild>
              <Link href={href(`/analyze?tab=anomalies&metric=${encodeURIComponent(m.id)}`)}>
                <Activity /> Anomalies
              </Link>
            </Button>
            {canEdit ? (
              <>
                <Button onClick={() => setEditing(true)}>
                  <Pencil /> Edit
                </Button>
                <Button variant="ghost" onClick={() => setArchiving(true)} aria-label="Archive metric">
                  <Archive />
                </Button>
              </>
            ) : null}
          </>
        }
      />
      <PageBody>
        <div className="flex flex-col gap-3">
          {conflicts.length ? (
            <div role="note" className="flex items-start gap-2 rounded border border-warning/30 bg-warning-soft px-3 py-2 text-sm text-warning">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <div>
                Ambiguous wording: {conflicts.map((c) => `“${c.term}” also means ${c.metricIds.filter((x) => x !== m.id).map(labelOf).join(", ")}`).join("; ")}.
                Questions using these words will ask which metric is meant rather than silently choosing this one.
              </div>
            </div>
          ) : null}
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
            <ExampleValues metric={m} />
            <Panel title="Definition">
              <div className="flex flex-col gap-3 p-3">
                <div>
                  <SectionLabel>Formula</SectionLabel>
                  <CodeBlock code={formulaSummary(m)} language="text" className="mt-1" />
                  {m.kind === "simple" && m.expr ? (
                    <div className="mt-1 text-xs text-fg-subtle">
                      Expression <code className="font-mono">{m.expr}</code> aggregated with {m.agg} at the{" "}
                      <span className="font-medium text-fg-muted">{m.entity}</span> grain.
                    </div>
                  ) : null}
                  {inputs.length ? (
                    <div className="mt-1.5 flex flex-wrap items-center gap-1 text-xs text-fg-subtle">
                      Inputs:
                      {inputs.map((x) => (
                        <Link key={x} href={href(`/metrics/${encodeURIComponent(x)}`)} className="rounded-sm border border-border px-1 text-fg hover:border-accent">
                          {labelOf(x)}
                        </Link>
                      ))}
                    </div>
                  ) : null}
                </div>
                <DefinitionList
                  items={[
                    { label: "Id", value: m.id, mono: true },
                    { label: "Kind", value: m.kind },
                    { label: "Format", value: m.format },
                    { label: "Direction", value: m.higher_is_better === false ? "Lower is better" : "Higher is better" },
                    { label: "Time dimension", value: m.default_time_dimension ?? "Entity default" },
                    { label: "Owner", value: m.owner ?? "n/a" },
                    {
                      label: "Tags",
                      value: m.tags?.length ? (
                        <span className="flex flex-wrap gap-1">
                          {m.tags.map((t) => (
                            <Badge key={t}>{t}</Badge>
                          ))}
                        </span>
                      ) : (
                        "n/a"
                      ),
                    },
                    { label: "Synonyms", value: m.synonyms?.length ? m.synonyms.join(", ") : "n/a" },
                    { label: "Version id", value: m.engine_version_id ?? m.version_id ?? "n/a", mono: true },
                    { label: "Updated", value: formatDateTime(m.updated_at) },
                  ]}
                />
                <div>
                  <SectionLabel>Dimensions available</SectionLabel>
                  {model.isLoading ? (
                    <LoadingState rows={2} />
                  ) : availableDims.length ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {availableDims.map((d) => (
                        <Badge key={d.name} tone={d.type === "time" ? "accent" : "neutral"} title={`${d.entity}.${d.expr}`}>
                          {d.label || d.name}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-1 text-xs text-fg-subtle">No dimensions reachable through approved relationships.</p>
                  )}
                  <p className="mt-1 text-2xs text-fg-subtle">
                    Only dimensions reachable through approved many-to-one relationships are offered, so breakdowns never fan out.
                  </p>
                </div>
                <div>
                  <SectionLabel>Used datasets</SectionLabel>
                  {lineage.isLoading ? (
                    <LoadingState rows={1} />
                  ) : datasetNodes.length ? (
                    <ul className="mt-1 flex flex-col gap-0.5 text-sm">
                      {datasetNodes.map((n) => (
                        <li key={n.id}>
                          {n.ref_id && n.kind === "dataset" ? (
                            <Link href={href(`/data/${n.ref_id}`)} className="text-accent hover:underline">
                              {n.label}
                            </Link>
                          ) : (
                            <span>{n.label}</span>
                          )}
                          {n.detail ? <span className="ml-1.5 font-mono text-2xs text-fg-subtle">{n.detail}</span> : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1 text-xs text-fg-subtle">No datasets recorded.</p>
                  )}
                </div>
              </div>
            </Panel>
          </div>

          <Tabs defaultValue="lineage" className="rounded-md border border-border">
            <TabsList className="px-2">
              <TabsTrigger value="lineage">
                <Share2 /> Lineage
              </TabsTrigger>
              <TabsTrigger value="history">
                <History /> History
              </TabsTrigger>
              <TabsTrigger value="investigations">
                <GitBranch /> Investigations
                {invs.data?.length ? <span className="text-2xs text-fg-subtle tabular">{invs.data.length}</span> : null}
              </TabsTrigger>
              <TabsTrigger value="glossary">
                <BookOpen /> Glossary
                {relatedTerms.length ? <span className="text-2xs text-fg-subtle tabular">{relatedTerms.length}</span> : null}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="lineage" className="p-3">
              <QueryState query={lineage} compact>
                {(g) => <LineageGraphView graph={g} highlightId={`metric:${m.id}`} />}
              </QueryState>
            </TabsContent>
            <TabsContent value="history" className="p-3">
              <VersionHistory metric={m} />
            </TabsContent>
            <TabsContent value="investigations" className="p-3">
              <QueryState
                query={invs}
                compact
                empty={
                  <EmptyState
                    compact
                    icon={GitBranch}
                    title="No investigations use this metric yet"
                    action={
                      <Button asChild variant="primary">
                        <Link href={href(`/investigate?new=1&q=${encodeURIComponent(`Why did ${m.label ?? m.id} change last month?`)}`)}>
                          Start an investigation
                        </Link>
                      </Button>
                    }
                  />
                }
              >
                {(list) => (
                  <ul className="flex flex-col">
                    {list.map((inv) => (
                      <li key={inv.id} className="flex items-center gap-3 border-b border-border/70 py-1.5 last:border-b-0">
                        <Link href={href(`/investigate/${inv.id}`)} className="min-w-0 flex-1 truncate text-sm hover:underline">
                          {inv.title}
                        </Link>
                        <StatusBadge status={inv.status} />
                        <span
                          className={cn(
                            "font-mono text-2xs",
                            inv.metric_version.version_no !== undefined && inv.metric_version.version_no !== (m.version_no ?? m.version)
                              ? "text-warning"
                              : "text-fg-subtle",
                          )}
                          title={
                            inv.metric_version.version_no !== (m.version_no ?? m.version)
                              ? "Ran with an older definition than the current version"
                              : "Ran with the current definition"
                          }
                        >
                          v{inv.metric_version.version_no ?? "?"}
                        </span>
                        <span className="text-2xs text-fg-subtle">{formatRelative(inv.created_at)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </QueryState>
            </TabsContent>
            <TabsContent value="glossary" className="p-3">
              {terms.isLoading ? (
                <LoadingState rows={2} />
              ) : relatedTerms.length ? (
                <ul className="flex flex-col gap-2">
                  {relatedTerms.map((t) => (
                    <li key={t.id} className="text-sm">
                      <Link href={href(`/metrics/glossary?q=${encodeURIComponent(t.term)}`)} className="font-medium hover:underline">
                        {t.term}
                      </Link>
                      {t.metric_id !== m.id ? <Badge tone="warning" className="ml-1.5">Ambiguous</Badge> : null}
                      <p className="text-fg-muted">{t.definition}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-fg-subtle">
                  No glossary terms reference this metric.{" "}
                  <Link href={href("/metrics/glossary")} className="text-accent hover:underline">
                    Open the glossary
                  </Link>
                </p>
              )}
            </TabsContent>
          </Tabs>
        </div>
      </PageBody>
      <MetricFormDialog open={editing} onOpenChange={setEditing} metric={m} onSaved={() => void qc.invalidateQueries({ queryKey: qk.metric(ws, id) })} />
      <ConfirmDialog
        open={archiving}
        onOpenChange={setArchiving}
        title={`Archive ${m.label ?? m.id}?`}
        description="Archived metrics disappear from pickers. Existing findings and investigations keep their recorded version and remain reproducible."
        confirmLabel="Archive"
        destructive
        pending={archive.isPending}
        onConfirm={() => archive.mutate()}
      />
    </Page>
  );
}

export default function MetricPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <MetricDetail id={decodeURIComponent(id)} />;
}
