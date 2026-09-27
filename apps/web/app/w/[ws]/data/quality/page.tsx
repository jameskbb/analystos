"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ListChecks, Play, Plus, ShieldCheck, Wand2 } from "lucide-react";
import { datasets, quality } from "@/lib/api/resources/data";
import { qk } from "@/lib/api/keys";
import type { QualityRun } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { RuleBuilderDialog } from "@/components/data/rule-builder";
import { RulesTable, RunDetailSheet, RunHistory, qualityKeys, useRunAllRules } from "@/components/data/quality-rules";
import { summarizeRules } from "@/components/data/dq-summary";
import { cn } from "@/lib/utils";

function Stat({ label, value, tone }: { label: string; value: number; tone?: "negative" | "positive" | "muted" }) {
  return (
    <div className="flex min-w-24 flex-col rounded border border-border px-3 py-2">
      <span className="text-xs text-fg-subtle">{label}</span>
      <span
        className={cn(
          "text-lg font-semibold tabular",
          tone === "negative" && value > 0 && "text-negative",
          tone === "positive" && "text-positive",
          tone === "muted" && "text-fg-muted",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function QualityInner() {
  const { id: ws, href, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const params = useSearchParams();
  const [datasetId, setDatasetId] = React.useState(params.get("dataset") ?? "");
  const [builder, setBuilder] = React.useState(false);
  const [openRun, setOpenRun] = React.useState<QualityRun | null>(null);
  const ds = useQuery({ queryKey: qk.datasets(ws), queryFn: () => datasets.list(ws) });
  const selected = ds.data?.find((d) => d.id === datasetId);
  const rules = useQuery({
    queryKey: qualityKeys.rules(ws, { dataset_id: datasetId || undefined }),
    queryFn: () => quality.rules(ws, datasetId ? { dataset_id: datasetId } : {}),
  });
  const runs = useQuery({
    queryKey: qualityKeys.runs(ws, { dataset_id: datasetId || undefined, limit: 50 }),
    queryFn: () => quality.runs(ws, { dataset_id: datasetId || undefined, limit: 50 }),
  });
  const runAll = useRunAllRules(datasetId || undefined);
  const suggest = useMutation({
    mutationFn: () => quality.suggest(ws, datasetId),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["ws", ws, "quality"] });
      toast.success(r.length ? `${r.length} rule${r.length === 1 ? "" : "s"} suggested from the profile` : "No new rules to suggest");
    },
    onError: (e: Error) => toast.error("Could not suggest rules", { description: e.message }),
  });

  const all = rules.data ?? [];
  const suggested = all.filter((r) => r.status === "suggested");
  const active = all.filter((r) => r.status !== "suggested");
  const totals = [...summarizeRules(all).values()].reduce(
    (a, s) => ({
      active: a.active + s.active,
      failing: a.failing + s.failing,
      passing: a.passing + s.passing,
      neverRun: a.neverRun + s.neverRun,
      suggested: a.suggested + s.suggested,
    }),
    { active: 0, failing: 0, passing: 0, neverRun: 0, suggested: 0 },
  );
  const byTable = new Map<string, typeof active>();
  active
    .slice()
    .sort((a, b) => Number(a.last_passed !== false) - Number(b.last_passed !== false))
    .forEach((r) => byTable.set(r.table_name, [...(byTable.get(r.table_name) ?? []), r]));

  return (
    <Page>
      <PageHeader
        breadcrumb={
          <>
            <Link href={href("/data")} className="hover:text-fg">Data</Link>
            <span>/</span>
            <span>Quality</span>
          </>
        }
        title="Data Quality Center"
        description="Rules detect problems and show failing rows. AnalystOS never modifies your data; suggested fixes are for you to decide."
        actions={
          canEdit ? (
            <>
              <Button onClick={() => setBuilder(true)}>
                <Plus /> New rule
              </Button>
              <Button variant="primary" onClick={() => runAll.mutate()} disabled={runAll.isPending || totals.active === 0}>
                {runAll.isPending ? <Spinner className="text-accent-fg" /> : <Play />} Run {datasetId ? "dataset" : "all"} rules
              </Button>
            </>
          ) : null
        }
      />
      <PageBody className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Dataset
            <NativeSelect value={datasetId} onChange={(e) => setDatasetId(e.target.value)} className="w-56" aria-label="Filter by dataset">
              <option value="">All datasets</option>
              {(ds.data ?? []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </NativeSelect>
          </label>
          {datasetId && canEdit ? (
            <Button onClick={() => suggest.mutate()} disabled={suggest.isPending}>
              {suggest.isPending ? <Spinner /> : <Wand2 />} Suggest rules from profile
            </Button>
          ) : null}
          <div className="ml-auto flex flex-wrap gap-2">
            <Stat label="Active rules" value={totals.active} />
            <Stat label="Failing" value={totals.failing} tone="negative" />
            <Stat label="Passing" value={totals.passing} tone="positive" />
            <Stat label="Not run" value={totals.neverRun} tone="muted" />
          </div>
        </div>

        {rules.isLoading ? (
          <LoadingState rows={6} />
        ) : rules.error ? (
          <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />
        ) : all.length === 0 ? (
          <Panel>
            <EmptyState
              icon={ShieldCheck}
              title={selected ? `No rules for ${selected.name}` : "No quality rules yet"}
              description={
                ds.data?.length
                  ? "Suggest rules from a dataset's profile (not null, unique, ranges, allowed values, foreign keys), or write your own."
                  : "Load data first: the demo includes a set of quality rules with real failures to inspect."
              }
              action={
                ds.data?.length ? (
                  canEdit ? (
                    <Button variant="primary" onClick={() => setBuilder(true)}>
                      <Plus /> New rule
                    </Button>
                  ) : undefined
                ) : (
                  <LoadDemoButton workspaceId={ws} />
                )
              }
              secondary={
                datasetId && canEdit ? (
                  <Button onClick={() => suggest.mutate()} disabled={suggest.isPending}>
                    <Wand2 /> Suggest rules
                  </Button>
                ) : undefined
              }
            />
          </Panel>
        ) : (
          <>
            {suggested.length ? (
              <Panel title={`Suggested rules (${suggested.length})`} description="Derived from profiling. Accept the ones that reflect your business rules.">
                <RulesTable rules={suggested} onOpenRun={setOpenRun} />
              </Panel>
            ) : null}
            {[...byTable.entries()].map(([table, list]) => {
              const d = ds.data?.find((x) => x.table_name === table);
              return (
                <Panel
                  key={table}
                  title={
                    d ? (
                      <Link href={href(`/data/${d.id}?tab=quality`)} className="hover:text-accent hover:underline">
                        {d.name}
                      </Link>
                    ) : (
                      table
                    )
                  }
                  description={`${list.length} rule${list.length === 1 ? "" : "s"} · ${list.filter((r) => r.last_passed === false).length} failing`}
                >
                  <RulesTable rules={list} onOpenRun={setOpenRun} showTable={false} />
                </Panel>
              );
            })}
          </>
        )}

        <Panel title="Run history" description="Most recent executions; open one to inspect failing rows.">
          {runs.isLoading ? (
            <LoadingState rows={3} className="px-3" />
          ) : runs.error ? (
            <ErrorState error={runs.error} compact onRetry={() => void runs.refetch()} />
          ) : !runs.data?.length ? (
            <EmptyState compact icon={ListChecks} title="No runs yet" description="Run rules to record results here." />
          ) : (
            <RunHistory runs={runs.data} onOpenRun={setOpenRun} />
          )}
        </Panel>
      </PageBody>
      <RuleBuilderDialog open={builder} onOpenChange={setBuilder} defaultTable={selected?.table_name} />
      <RunDetailSheet run={openRun} onOpenChange={(o) => !o && setOpenRun(null)} />
    </Page>
  );
}

export default function QualityPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <QualityInner />
    </Suspense>
  );
}
