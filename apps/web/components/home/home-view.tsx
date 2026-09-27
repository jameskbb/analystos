"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Database,
  GitBranch,
  GitMerge,
  LayoutDashboard,
  Lightbulb,
  Loader2,
  ShieldAlert,
} from "lucide-react";
import { investigations } from "@/lib/api/endpoints";
import type { HomeChange, HomeSummary } from "@/lib/api/types";
import { formatInt, formatRelative, humanize, pluralize } from "@/lib/format";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { Kpi } from "@/components/charts/kpi";
import { StatementTypeBadge } from "@/components/analysis/statement";
import { EvidenceBadge } from "@/components/analysis/evidence";
import { StatusBadge } from "@/components/analysis/status";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AskBox } from "./ask-box";
import { Onboarding } from "./onboarding";
import { useHomeSummary } from "./use-home-summary";
import { cn } from "@/lib/utils";

const DEFAULT_EXAMPLES = [
  "Why was August revenue down?",
  "Revenue was roughly flat. Why did margin decline?",
  "Which region drove the change in orders last month?",
  "What caused the forecast miss?",
];

/** Suggested questions: the biggest metric moves first, then general templates. */
export function exampleQuestions(changes: HomeChange[]): string[] {
  const moves = [...changes]
    .filter((c) => c.pct_change !== null && c.pct_change !== undefined && Math.abs(c.pct_change) >= 0.02)
    .sort((a, b) => Math.abs(b.pct_change ?? 0) - Math.abs(a.pct_change ?? 0))
    .slice(0, 2)
    .map((c) => `Why did ${c.label.toLowerCase()} ${(c.pct_change ?? 0) < 0 ? "fall" : "rise"}${c.period ? ` in ${c.period}` : ""}?`);
  return [...moves, ...DEFAULT_EXAMPLES].filter((q, i, arr) => arr.indexOf(q) === i).slice(0, 4);
}

function ChangeStrip({ changes }: { changes: HomeChange[] }) {
  const { id: ws, href } = useWorkspace();
  const router = useRouter();
  const ask = useMutation({
    mutationFn: (question: string) => investigations.create(ws, { question }),
    onSuccess: (inv) => router.push(href(`/investigate/${inv.id}`)),
  });
  const period = changes.find((c) => c.period)?.period;
  return (
    <Panel
      title="What changed"
      description={period ? `Canonical metrics, ${period} vs the prior month (last full month in the data)` : "Canonical metrics, last full month vs the prior month"}
      actions={
        <Button asChild variant="ghost" size="xs">
          <Link href={href("/metrics")}>
            All metrics <ArrowRight />
          </Link>
        </Button>
      }
    >
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6">
        {changes.slice(0, 6).map((c) => (
          <div key={c.metric_id} className="group flex flex-col justify-between gap-2 border-r border-b border-border p-3 last:border-r-0">
            <Link href={href(`/metrics/${c.metric_id}`)} className="rounded-sm hover:opacity-90">
              <Kpi
                label={c.label}
                value={c.current}
                baseline={c.baseline}
                pctChange={c.pct_change ?? null}
                format={c.format}
                comparisonLabel="vs prior"
                size="sm"
              />
            </Link>
            <button
              type="button"
              disabled={ask.isPending}
              onClick={() => ask.mutate(`Why did ${c.label} change${c.period ? ` in ${c.period}` : " last month"}?`)}
              className="flex items-center gap-1 self-start text-2xs text-fg-subtle opacity-80 hover:text-accent group-hover:opacity-100 disabled:opacity-40"
            >
              {ask.isPending && ask.variables?.includes(c.label) ? <Loader2 className="size-3 animate-spin" /> : <GitBranch className="size-3" />}
              Investigate this change
            </button>
          </div>
        ))}
      </div>
      {ask.error ? <p className="border-t border-border px-3 py-1.5 text-xs text-negative">{ask.error.message}</p> : null}
    </Panel>
  );
}

function RecentInvestigations({ data }: { data: HomeSummary }) {
  const { href } = useWorkspace();
  return (
    <Panel
      title="What you were investigating"
      actions={
        <Button asChild variant="ghost" size="xs">
          <Link href={href("/investigate")}>
            All <ArrowRight />
          </Link>
        </Button>
      }
    >
      {data.recent_investigations.length === 0 ? (
        <EmptyState
          compact
          icon={GitBranch}
          title="No investigations yet"
          description="Ask a question above; the plan, queries and evidence tree are saved here."
        />
      ) : (
        <ul className="divide-y divide-border">
          {data.recent_investigations.map((inv) => (
            <li key={inv.id}>
              <Link href={href(`/investigate/${inv.id}`)} className="flex flex-col gap-0.5 px-3 py-2 hover:bg-bg-subtle">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{inv.title || inv.question}</span>
                  <StatusBadge status={inv.status} />
                </div>
                {inv.headline ? <p className="line-clamp-2 text-xs text-fg-muted">{inv.headline}</p> : null}
                <div className="text-2xs text-fg-subtle">
                  {formatRelative(inv.updated_at ?? inv.created_at)}
                  {inv.finding_count ? ` · ${pluralize(inv.finding_count, "finding")}` : ""}
                  {inv.run_count > 1 ? ` · ${inv.run_count} runs` : ""}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function RecentFindings({ data }: { data: HomeSummary }) {
  const { href } = useWorkspace();
  return (
    <Panel
      title="Recent findings"
      actions={
        <Button asChild variant="ghost" size="xs">
          <Link href={href("/findings")}>
            All <ArrowRight />
          </Link>
        </Button>
      }
    >
      {data.recent_findings.length === 0 ? (
        <EmptyState
          compact
          icon={Lightbulb}
          title="No findings saved"
          description="Save a node from an investigation as a finding to review, comment and report on it."
        />
      ) : (
        <ul className="divide-y divide-border">
          {data.recent_findings.map((f) => (
            <li key={f.id} className="flex flex-col gap-1 px-3 py-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <StatementTypeBadge type={f.statement_type} short />
                <EvidenceBadge strength={f.evidence_strength} reasons={f.evidence_reasons} />
                <StatusBadge status={f.status} />
              </div>
              <Link
                href={href(`/findings/${f.id}`)}
                className={cn("line-clamp-2 text-sm hover:underline", f.statement_type === "hypothesis" && "text-fg-muted italic")}
              >
                {f.statement}
              </Link>
              <div className="text-2xs text-fg-subtle">{formatRelative(f.updated_at ?? f.created_at)}</div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function NeedsAttention({ data }: { data: HomeSummary }) {
  const { href } = useWorkspace();
  const issueDatasets = data.datasets.filter((d) => (d.issue_count ?? 0) > 0 || d.profile_status === "failed");
  const jobs = data.running_jobs ?? [];
  const items = data.quality_failures.length + issueDatasets.length + (data.pending_relationships ?? 0) + jobs.length;
  return (
    <Panel
      title="Needs attention"
      actions={items ? <Badge tone="warning">{items}</Badge> : <Badge tone="positive">All clear</Badge>}
    >
      {items === 0 ? (
        <EmptyState
          compact
          icon={ShieldAlert}
          title="Nothing needs attention"
          description="No failing quality rules, unreviewed relationships or profiling issues."
        />
      ) : (
        <ul className="divide-y divide-border text-sm">
          {jobs.map((j) => (
            <li key={j.id} className="flex items-center gap-2 px-3 py-1.5">
              <Loader2 className="size-3.5 shrink-0 animate-spin text-accent" aria-hidden />
              <span className="min-w-0 flex-1 truncate">{j.message || humanize(j.kind)}</span>
              <span className="text-xs text-fg-subtle tabular">{Math.round((j.progress ?? 0) * 100)}%</span>
            </li>
          ))}
          {(data.pending_relationships ?? 0) > 0 ? (
            <li>
              <Link href={href("/data/relationships")} className="flex items-center gap-2 px-3 py-1.5 hover:bg-bg-subtle">
                <GitMerge className="size-3.5 shrink-0 text-warning" aria-hidden />
                <span className="flex-1">
                  {pluralize(data.pending_relationships ?? 0, "suggested relationship")} to review
                </span>
                <span className="text-2xs text-fg-subtle">Unapproved joins are never used</span>
              </Link>
            </li>
          ) : null}
          {data.quality_failures.slice(0, 6).map((q, i) => (
            <li key={q.id ?? q.rule_id ?? i}>
              <Link href={href("/data/quality")} className="flex items-center gap-2 px-3 py-1.5 hover:bg-bg-subtle">
                <AlertTriangle className={cn("size-3.5 shrink-0", q.severity === "error" ? "text-negative" : "text-warning")} aria-hidden />
                <span className="min-w-0 flex-1 truncate">
                  {q.name ?? q.rule_name ?? "Quality rule"} {q.table_name ? <span className="font-mono text-xs text-fg-subtle">{q.table_name}</span> : null}
                </span>
                {q.failing_count !== undefined && q.failing_count !== null ? (
                  <span className="text-xs text-negative tabular">{formatInt(q.failing_count)} rows</span>
                ) : null}
              </Link>
            </li>
          ))}
          {issueDatasets.slice(0, 6).map((d) => (
            <li key={d.id}>
              <Link href={href(`/data/${d.id}?tab=profile`)} className="flex items-center gap-2 px-3 py-1.5 hover:bg-bg-subtle">
                <Database className="size-3.5 shrink-0 text-warning" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{d.name}</span>
                <span className="text-xs text-fg-subtle">
                  {d.profile_status === "failed" ? "Profiling failed" : pluralize(d.issue_count ?? 0, "profile issue")}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function DataFreshness({ data }: { data: HomeSummary }) {
  const { href } = useWorkspace();
  const totalRows = data.datasets.reduce((s, d) => s + (d.row_count ?? 0), 0);
  return (
    <Panel
      title="Your data"
      description={`${pluralize(data.datasets.length, "dataset")} · ${formatInt(totalRows)} rows`}
      actions={
        <Button asChild variant="ghost" size="xs">
          <Link href={href("/data")}>
            Manage <ArrowRight />
          </Link>
        </Button>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Datasets and freshness</caption>
          <thead>
            <tr className="border-b border-border text-left text-2xs tracking-wide text-fg-subtle uppercase">
              <th scope="col" className="px-3 py-1.5 font-medium">Dataset</th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">Rows</th>
              <th scope="col" className="px-3 py-1.5 font-medium">Updated</th>
              <th scope="col" className="px-3 py-1.5 font-medium">Profile</th>
            </tr>
          </thead>
          <tbody>
            {data.datasets.slice(0, 10).map((d) => (
              <tr key={d.id} className="border-b border-border last:border-b-0 hover:bg-bg-subtle">
                <td className="max-w-0 px-3 py-1.5">
                  <Link href={href(`/data/${d.id}`)} className="block truncate hover:underline">
                    {d.name}
                  </Link>
                  <span className="block truncate font-mono text-2xs text-fg-subtle">{d.table_name}</span>
                </td>
                <td className="px-3 py-1.5 text-right tabular">{formatInt(d.row_count)}</td>
                <td className="px-3 py-1.5 whitespace-nowrap text-fg-muted">{formatRelative(d.updated_at)}</td>
                <td className="px-3 py-1.5">
                  <StatusBadge status={d.profile_status ?? "pending"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.datasets.length > 10 ? (
        <div className="border-t border-border px-3 py-1.5 text-xs text-fg-subtle">
          and {data.datasets.length - 10} more
        </div>
      ) : null}
    </Panel>
  );
}

function Dashboards({ data }: { data: HomeSummary }) {
  const { href } = useWorkspace();
  return (
    <Panel
      title="Dashboards"
      actions={
        <Button asChild variant="ghost" size="xs">
          <Link href={href("/dashboards")}>
            All <ArrowRight />
          </Link>
        </Button>
      }
    >
      {data.dashboards.length === 0 ? (
        <EmptyState
          compact
          icon={LayoutDashboard}
          title="No dashboards"
          description="Build one from saved metrics, queries and findings."
          action={
            <Button asChild size="xs">
              <Link href={href("/dashboards?new=1")}>New dashboard</Link>
            </Button>
          }
        />
      ) : (
        <ul className="divide-y divide-border">
          {data.dashboards.slice(0, 6).map((d) => (
            <li key={d.id}>
              <Link href={href(`/dashboards/${d.id}`)} className="flex items-center gap-2 px-3 py-1.5 hover:bg-bg-subtle">
                <LayoutDashboard className="size-3.5 shrink-0 text-fg-subtle" aria-hidden />
                <span className="min-w-0 flex-1 truncate text-sm">{d.name}</span>
                <span className="text-2xs text-fg-subtle">{formatRelative(d.updated_at ?? d.created_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** Home (spec §71): what data do I have, what changed, what needs attention, what was I investigating. */
export function HomeView() {
  const { id: ws, workspace, href } = useWorkspace();
  const query = useHomeSummary(ws);
  const data = query.data;
  const empty = !!data && data.datasets.length === 0;

  return (
    <Page>
      <PageHeader
        title={workspace?.name ?? "Home"}
        description={workspace?.description || "What data you have, what changed, what needs attention and what you were investigating."}
      />
      <PageBody>
        <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
          {query.isLoading ? (
            <LoadingState rows={6} label="Loading workspace summary" />
          ) : query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : data ? (
            empty ? (
              <Onboarding workspaceId={ws} href={href} />
            ) : (
              <>
                <AskBox workspaceId={ws} href={href} examples={exampleQuestions(data.changes)} />
                {data.changes.length ? <ChangeStrip changes={data.changes} /> : null}
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
                  <RecentInvestigations data={data} />
                  <RecentFindings data={data} />
                  <NeedsAttention data={data} />
                  <DataFreshness data={data} />
                  <Dashboards data={data} />
                </div>
              </>
            )
          ) : null}
        </div>
      </PageBody>
    </Page>
  );
}
