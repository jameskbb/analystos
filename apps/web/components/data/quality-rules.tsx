"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, CheckCircle2, MoreHorizontal, Play, ShieldCheck, Trash2, XCircle, Ban, RotateCcw } from "lucide-react";
import { quality } from "@/lib/api/resources/data";
import { asList } from "@/lib/api/client";
import { isJobAccepted, waitForJob } from "@/lib/api/endpoints";
import type { QualityRule, QualityRun, QueryResult } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { DataGrid } from "@/components/data-grid/data-grid";
import { CodeBlock } from "@/components/code/code-block";
import { ErrorState, LoadingState } from "@/components/states/states";
import { SectionLabel } from "@/components/shell/page";
import { Spinner } from "@/components/ui/spinner";
import { formatDateTime, formatDuration, formatInt, formatRelative, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { describeRule, ruleKindMeta } from "./rule-params";

const SEV_TONE: Record<string, "negative" | "warning" | "neutral"> = { error: "negative", warning: "warning", info: "neutral" };

export const qualityKeys = {
  rules: (ws: string, params: Record<string, string | undefined> = {}) => ["ws", ws, "quality", "rules", params] as const,
  runs: (ws: string, params: Record<string, string | number | undefined> = {}) => ["ws", ws, "quality", "runs", params] as const,
};

export function RuleResult({ rule }: { rule: QualityRule }) {
  if (rule.status === "suggested") return <Badge tone="outline">Suggested</Badge>;
  if (rule.status === "disabled") return <Badge tone="neutral">Disabled</Badge>;
  if (rule.last_passed === true)
    return (
      <span className="inline-flex items-center gap-1 text-xs text-positive">
        <CheckCircle2 className="size-3.5" /> Passed
      </span>
    );
  if (rule.last_passed === false)
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-negative">
        <XCircle className="size-3.5" /> Failed
        {rule.last_failing_count != null ? <span className="tabular">({formatInt(rule.last_failing_count)} rows)</span> : null}
      </span>
    );
  return <span className="text-xs text-fg-subtle">Not run</span>;
}

/** Sample rows from a stored run, when the API embeds them. */
function embeddedSample(run: QualityRun): QueryResult | null {
  const s = run.sample_failing_rows as Record<string, unknown> | undefined;
  if (!s || !Array.isArray(s.rows) || !Array.isArray(s.columns)) return null;
  const columns = (s.columns as unknown[]).map((c) =>
    typeof c === "string" ? { name: c, type: "" } : { name: String((c as { name: string }).name), type: String((c as { type?: string }).type ?? "") },
  );
  return { columns, rows: s.rows as unknown[][], row_count: (s.rows as unknown[]).length, truncated: false, elapsed_ms: 0 };
}

/** Failing-row inspection for one run (spec §11). Suggested fixes are text only; nothing is ever applied. */
export function RunDetailSheet({ run, onOpenChange }: { run: QualityRun | null; onOpenChange: (o: boolean) => void }) {
  const { id: ws } = useWorkspace();
  const sample = run ? embeddedSample(run) : null;
  const rows = useQuery({
    queryKey: ["ws", ws, "quality", "run", run?.id, "failing-rows"],
    queryFn: () => quality.failingRows(ws, run!.id, { limit: 500 }),
    enabled: !!run && !run.passed && !sample,
    retry: false,
  });
  const result = sample ?? rows.data ?? null;
  return (
    <Sheet open={!!run} onOpenChange={onOpenChange}>
      <SheetContent width="w-[760px]">
        <SheetHeader>
          <SheetTitle>{run?.rule_name ?? "Rule run"}</SheetTitle>
          <SheetDescription>
            {run?.table_name ? `${run.table_name} · ` : ""}
            {run ? formatDateTime(run.created_at) : ""}
            {run?.duration_ms != null ? ` · ${formatDuration(run.duration_ms)}` : ""}
          </SheetDescription>
        </SheetHeader>
        {run ? (
          <SheetBody className="flex flex-col gap-4">
            <div
              className={cn(
                "flex items-center gap-2 rounded border px-3 py-2 text-sm",
                run.error || run.status === "error"
                  ? "border-warning/30 bg-warning-soft text-warning"
                  : run.passed
                    ? "border-positive/30 bg-positive-soft text-positive"
                    : "border-negative/30 bg-negative-soft text-negative",
              )}
            >
              {run.passed ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}
              {run.error
                ? `Rule could not run: ${run.error}`
                : run.passed
                  ? "All rows pass."
                  : `${formatInt(run.failing_count)} failing row${run.failing_count === 1 ? "" : "s"}${run.total_count ? ` of ${formatInt(run.total_count)} (${((run.failing_count / run.total_count) * 100).toFixed(2)}%)` : ""}.`}
            </div>
            {!run.passed && !run.error ? (
              <div>
                <SectionLabel className="mb-1">Failing rows (sample)</SectionLabel>
                {result ? (
                  <DataGrid columns={result.columns} rows={result.rows} height={280} className="rounded border border-border" ariaLabel="Failing rows" />
                ) : rows.isLoading ? (
                  <LoadingState rows={4} />
                ) : rows.error ? (
                  <ErrorState error={rows.error} compact onRetry={() => void rows.refetch()} />
                ) : null}
              </div>
            ) : null}
            <div>
              <SectionLabel className="mb-1">Check SQL (read-only)</SectionLabel>
              <CodeBlock code={run.sql} maxHeight={200} />
            </div>
            {run.suggested_fix ? (
              <div>
                <SectionLabel className="mb-1">Suggested fix (for you to decide)</SectionLabel>
                <p className="rounded border border-border bg-bg-subtle px-3 py-2 text-sm whitespace-pre-wrap">{run.suggested_fix}</p>
              </div>
            ) : null}
            <p className="flex items-center gap-1.5 text-xs text-fg-subtle">
              <ShieldCheck className="size-3.5" /> AnalystOS never modifies your data. Fix issues at the source, then re-ingest.
            </p>
          </SheetBody>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

/** Rules table with run / accept / disable / delete actions. */
export function RulesTable({
  rules,
  onOpenRun,
  showTable = true,
}: {
  rules: QualityRule[];
  onOpenRun: (run: QualityRun) => void;
  showTable?: boolean;
}) {
  const { id: ws, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["ws", ws, "quality"] });
  const [running, setRunning] = React.useState<string | null>(null);
  const runRule = useMutation({
    mutationFn: (id: string) => {
      setRunning(id);
      return quality.runRule(ws, id);
    },
    onSuccess: (run) => {
      invalidate();
      if (run.error || run.status === "error") toast.error(`${run.rule_name ?? "Rule"} could not run`, { description: run.error ?? undefined });
      else if (!run.passed) onOpenRun(run);
      else toast.success(`${run.rule_name ?? "Rule"} passed`);
    },
    onError: (e: Error) => toast.error("Rule run failed", { description: e.message }),
    onSettled: () => setRunning(null),
  });
  const accept = useMutation({
    mutationFn: (id: string) => quality.acceptSuggestion(ws, id),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error("Could not accept", { description: e.message }),
  });
  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => quality.updateRule(ws, id, { status }),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error("Update failed", { description: e.message }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => quality.deleteRule(ws, id),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error("Delete failed", { description: e.message }),
  });
  return (
    <div className="overflow-x-auto scrollbar-thin">
      <table className="w-full min-w-[720px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-fg-subtle">
            <th className="px-3 py-1.5 font-medium">Rule</th>
            {showTable ? <th className="px-3 py-1.5 font-medium">Table</th> : null}
            <th className="px-3 py-1.5 font-medium">Check</th>
            <th className="px-3 py-1.5 font-medium">Severity</th>
            <th className="px-3 py-1.5 font-medium">Last result</th>
            <th className="px-3 py-1.5 font-medium">Last run</th>
            <th className="px-3 py-1.5" aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id} className={cn("border-b border-border last:border-b-0 hover:bg-bg-subtle", r.status === "disabled" && "opacity-60")}>
              <td className="px-3 py-1.5">
                <div className="font-medium">{r.name}</div>
                {r.description ? <div className="max-w-xs truncate text-xs text-fg-subtle">{r.description}</div> : null}
              </td>
              {showTable ? <td className="px-3 py-1.5 font-mono text-xs">{r.table_name}</td> : null}
              <td className="px-3 py-1.5">
                <div className="text-xs">{ruleKindMeta(r.kind)?.label ?? humanize(r.kind)}</div>
                <div className="max-w-[260px] truncate font-mono text-2xs text-fg-subtle" title={describeRule(r.kind, r.column, r.params)}>
                  {describeRule(r.kind, r.column, r.params)}
                </div>
              </td>
              <td className="px-3 py-1.5">
                <Badge tone={SEV_TONE[r.severity] ?? "neutral"}>{humanize(r.severity)}</Badge>
              </td>
              <td className="px-3 py-1.5">
                <RuleResult rule={r} />
              </td>
              <td className="px-3 py-1.5 text-xs text-fg-subtle" title={formatDateTime(r.last_run_at)}>
                {r.last_run_at ? formatRelative(r.last_run_at) : "n/a"}
              </td>
              <td className="px-3 py-1.5">
                <div className="flex items-center justify-end gap-1">
                  {r.status === "suggested" && canEdit ? (
                    <Button size="xs" variant="primary" onClick={() => accept.mutate(r.id)} disabled={accept.isPending}>
                      <Check /> Accept
                    </Button>
                  ) : null}
                  {r.status !== "suggested" && canEdit ? (
                    <Button size="xs" onClick={() => runRule.mutate(r.id)} disabled={running !== null || r.status === "disabled"} aria-label={`Run ${r.name}`}>
                      {running === r.id ? <Spinner /> : <Play />} Run
                    </Button>
                  ) : null}
                  {canEdit ? (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon-xs" aria-label={`More actions for ${r.name}`}>
                          <MoreHorizontal />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {r.status === "active" ? (
                          <DropdownMenuItem onSelect={() => update.mutate({ id: r.id, status: "disabled" })}>
                            <Ban /> Disable
                          </DropdownMenuItem>
                        ) : r.status === "disabled" ? (
                          <DropdownMenuItem onSelect={() => update.mutate({ id: r.id, status: "active" })}>
                            <RotateCcw /> Enable
                          </DropdownMenuItem>
                        ) : null}
                        <DropdownMenuItem destructive onSelect={() => remove.mutate(r.id)}>
                          <Trash2 /> {r.status === "suggested" ? "Dismiss suggestion" : "Delete rule"}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Runs every active rule (optionally for one dataset). Handles both synchronous and job responses. */
export function useRunAllRules(datasetId?: string) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<QualityRun[]> => {
      const res = await quality.runAll(ws, datasetId ? { dataset_id: datasetId } : {});
      if (isJobAccepted(res)) {
        await waitForJob(res);
        return [];
      }
      return asList(res);
    },
    onSuccess: (runs) => {
      void qc.invalidateQueries({ queryKey: ["ws", ws, "quality"] });
      void qc.invalidateQueries({ queryKey: ["ws", ws, "datasets"] });
      const errored = runs.filter((r) => r.error || r.status === "error").length;
      const failed = runs.filter((r) => !r.passed).length - errored;
      toast.success(
        runs.length ? `Ran ${runs.length} rules: ${failed} failing${errored ? `, ${errored} could not run` : ""}` : "Rules ran",
      );
    },
    onError: (e: Error) => toast.error("Could not run rules", { description: e.message }),
  });
}

/** Execution history table. */
export function RunHistory({ runs, onOpenRun }: { runs: QualityRun[]; onOpenRun: (r: QualityRun) => void }) {
  return (
    <div className="overflow-x-auto scrollbar-thin">
      <table className="w-full min-w-[620px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-fg-subtle">
            <th className="px-3 py-1.5 font-medium">When</th>
            <th className="px-3 py-1.5 font-medium">Rule</th>
            <th className="px-3 py-1.5 font-medium">Result</th>
            <th className="px-3 py-1.5 text-right font-medium">Failing rows</th>
            <th className="px-3 py-1.5 text-right font-medium">Duration</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-b border-border last:border-b-0 hover:bg-bg-subtle">
              <td className="px-3 py-1.5 text-xs text-fg-subtle" title={formatDateTime(r.created_at)}>
                {formatRelative(r.created_at)}
              </td>
              <td className="px-3 py-1.5">
                <button type="button" className="text-left hover:text-accent hover:underline" onClick={() => onOpenRun(r)}>
                  {r.rule_name ?? r.rule_id}
                </button>
                {r.table_name ? <span className="ml-1.5 font-mono text-2xs text-fg-subtle">{r.table_name}</span> : null}
              </td>
              <td className="px-3 py-1.5">
                {r.error || r.status === "error" ? (
                  <Badge tone="warning">Could not run</Badge>
                ) : r.passed ? (
                  <Badge tone="positive">Passed</Badge>
                ) : (
                  <Badge tone="negative">Failed</Badge>
                )}
              </td>
              <td className="px-3 py-1.5 text-right tabular">{formatInt(r.failing_count)}</td>
              <td className="px-3 py-1.5 text-right text-xs text-fg-subtle tabular">{formatDuration(r.duration_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
