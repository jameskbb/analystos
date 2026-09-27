"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, RefreshCw, ShieldCheck, Activity } from "lucide-react";
import { diagnostics } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { EventLogEntry } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { StatusBadge } from "@/components/analysis/status";
import { formatDateTime, formatDuration, formatInt, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { errorRate, infoSections, summaryRows } from "./normalize";

export const EVENT_CATEGORIES = ["import", "profiling", "sql", "python", "ai", "investigation", "job", "quality", "connector", "export"] as const;

function SystemPanel() {
  const info = useQuery({ queryKey: qk.diagnostics, queryFn: diagnostics.info });
  const health = useQuery({ queryKey: ["health"], queryFn: diagnostics.health, refetchInterval: 30_000 });
  const sections = infoSections(info.data);
  return (
    <Panel
      title="System"
      actions={
        health.data ? (
          <Badge tone={health.data.status === "ok" ? "positive" : "warning"}>
            API {health.data.status}
            {health.data.version ? ` · v${health.data.version}` : ""}
          </Badge>
        ) : health.isError ? (
          <Badge tone="negative">API unreachable</Badge>
        ) : null
      }
    >
      {info.isLoading ? (
        <LoadingState rows={5} className="px-3" />
      ) : info.isError ? (
        <ErrorState compact error={info.error} onRetry={() => void info.refetch()} />
      ) : sections.length === 0 ? (
        <EmptyState compact title="No system information reported" />
      ) : (
        <div className="grid gap-x-6 gap-y-4 p-3 md:grid-cols-2 xl:grid-cols-3">
          {sections.map((s) => (
            <div key={s.title} className="min-w-0">
              <h3 className="mb-1 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">{s.title}</h3>
              <dl className="grid grid-cols-[minmax(90px,max-content)_1fr] gap-x-3 gap-y-0.5 text-xs">
                {s.rows.map((r) => (
                  <React.Fragment key={r.key}>
                    <dt className="text-fg-subtle">{r.key}</dt>
                    <dd className="min-w-0 font-mono break-all">{r.value}</dd>
                  </React.Fragment>
                ))}
              </dl>
            </div>
          ))}
        </div>
      )}
      <div className="flex items-start gap-2 border-t border-border px-3 py-2 text-xs text-fg-muted">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-fg-subtle" aria-hidden />
        <p>
          Python cells run in a separate subprocess with CPU, memory and time limits, a network socket guard, an
          isolated scratch directory, a read-only DuckDB connection and an import allowlist. This is process-level
          isolation, not a virtual machine or container; do not run untrusted code on a shared server without an
          outer sandbox. All SQL is parsed and restricted to single read-only statements before it runs.
        </p>
      </div>
    </Panel>
  );
}

function SummaryPanel() {
  const { id: ws } = useWorkspace();
  const [hours, setHours] = React.useState(24);
  const q = useQuery({ queryKey: ["ws", ws, "diagnostics-summary", hours], queryFn: () => diagnostics.summary(ws, { hours }) });
  const rows = summaryRows(q.data);
  return (
    <Panel
      title="Activity by category"
      description="Counts, error rates and durations of recorded operations"
      actions={
        <NativeSelect aria-label="Summary window" className="h-6 w-28 text-xs" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          <option value={1}>Last hour</option>
          <option value={24}>Last 24 hours</option>
          <option value={168}>Last 7 days</option>
        </NativeSelect>
      }
    >
      {q.isLoading ? (
        <LoadingState rows={3} className="px-3" />
      ) : q.isError ? (
        <ErrorState compact error={q.error} onRetry={() => void q.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState compact icon={Activity} title="No activity recorded in this window" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Activity summary</caption>
            <thead>
              <tr className="border-b border-border text-left text-2xs tracking-wide text-fg-subtle uppercase">
                <th scope="col" className="px-3 py-1.5 font-medium">Category</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Events</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Errors</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Error rate</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">p50</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">p95</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const rate = errorRate(r);
                return (
                  <tr key={r.category} className="border-b border-border last:border-b-0">
                    <td className="px-3 py-1.5">{humanize(r.category)}</td>
                    <td className="px-3 py-1.5 text-right tabular">{typeof r.count === "number" ? formatInt(r.count) : "n/a"}</td>
                    <td className="px-3 py-1.5 text-right tabular">{typeof r.errors === "number" ? formatInt(r.errors) : "n/a"}</td>
                    <td className={cn("px-3 py-1.5 text-right tabular", rate !== null && rate > 0.05 && "text-negative")}>
                      {rate === null ? "n/a" : `${(rate * 100).toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular">{formatDuration(r.p50_ms ?? null)}</td>
                    <td className="px-3 py-1.5 text-right tabular">{formatDuration(r.p95_ms ?? null)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function EventRow({ ev }: { ev: EventLogEntry }) {
  const [open, setOpen] = React.useState(false);
  const hasDetail = !!ev.detail || !!ev.error;
  return (
    <>
      <tr className={cn("border-b border-border", ev.status !== "ok" && "bg-negative-soft/40")}>
        <td className="w-6 px-1 py-1">
          {hasDetail ? (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              aria-label={open ? "Hide event details" : "Show event details"}
              className="rounded-sm p-0.5 text-fg-subtle hover:bg-bg-muted"
            >
              {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
            </button>
          ) : null}
        </td>
        <td className="px-2 py-1 whitespace-nowrap text-fg-muted tabular">{formatDateTime(ev.created_at)}</td>
        <td className="px-2 py-1">
          <Badge tone="outline">{ev.category}</Badge>
        </td>
        <td className="max-w-0 px-2 py-1">
          <span className="block truncate font-mono">{ev.name}</span>
          {ev.error && !open ? <span className="block truncate text-negative">{ev.error}</span> : null}
        </td>
        <td className="px-2 py-1">
          <StatusBadge status={ev.status === "ok" ? "ok" : ev.status === "rejected" ? "rejected" : "error"} />
        </td>
        <td className="px-2 py-1 text-right whitespace-nowrap tabular">{formatDuration(ev.duration_ms ?? null)}</td>
        <td className="hidden px-2 py-1 font-mono text-2xs text-fg-subtle lg:table-cell">{ev.request_id ?? ""}</td>
      </tr>
      {open ? (
        <tr className="border-b border-border bg-bg-subtle">
          <td />
          <td colSpan={6} className="px-2 py-2">
            {ev.error ? <p className="mb-1 text-xs whitespace-pre-wrap text-negative">{ev.error}</p> : null}
            {ev.detail ? (
              <pre className="max-h-64 overflow-auto rounded border border-border bg-code-bg p-2 font-mono text-2xs scrollbar-thin">
                {typeof ev.detail === "string" ? ev.detail : JSON.stringify(ev.detail, null, 2)}
              </pre>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

function EventLog() {
  const { id: ws } = useWorkspace();
  const [category, setCategory] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [limit, setLimit] = React.useState(200);
  const [live, setLive] = React.useState(false);
  const [text, setText] = React.useState("");
  const q = useQuery({
    queryKey: qk.events(ws, { category, status, limit }),
    queryFn: () => diagnostics.events(ws, { category: category || undefined, status: status || undefined, limit }),
    refetchInterval: live ? 5000 : false,
  });
  const needle = text.trim().toLowerCase();
  const events = (q.data ?? []).filter(
    (e) => !needle || e.name.toLowerCase().includes(needle) || (e.request_id ?? "").toLowerCase().includes(needle) || (e.error ?? "").toLowerCase().includes(needle),
  );
  return (
    <Panel
      title="Event log"
      description="Imports, profiling, SQL, Python, AI calls, investigation steps and failures, with durations"
      actions={
        <Button variant="ghost" size="icon-xs" aria-label="Refresh events" onClick={() => void q.refetch()} disabled={q.isFetching}>
          <RefreshCw className={cn(q.isFetching && "animate-spin")} />
        </Button>
      }
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <NativeSelect aria-label="Category" className="w-36" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {EVENT_CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {humanize(c)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect aria-label="Status" className="w-32" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          <option value="ok">OK</option>
          <option value="error">Error</option>
          <option value="rejected">Rejected</option>
        </NativeSelect>
        <NativeSelect aria-label="Limit" className="w-24" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          <option value={100}>100</option>
          <option value={200}>200</option>
          <option value={500}>500</option>
        </NativeSelect>
        <Input aria-label="Filter by name, request id or error" className="w-56" placeholder="Filter name / request id…" value={text} onChange={(e) => setText(e.target.value)} />
        <label className="ml-auto flex items-center gap-1.5 text-xs text-fg-muted">
          <Switch checked={live} onCheckedChange={setLive} aria-label="Auto-refresh" /> Live (5s)
        </label>
      </div>
      {q.isLoading ? (
        <LoadingState rows={6} className="px-3" />
      ) : q.isError ? (
        <ErrorState compact error={q.error} onRetry={() => void q.refetch()} />
      ) : events.length === 0 ? (
        <EmptyState compact icon={Activity} title="No matching events" description="Run a query, upload a file or start an investigation to generate events." />
      ) : (
        <div className="max-h-[480px] overflow-auto scrollbar-thin">
          <table className="w-full table-fixed text-xs">
            <caption className="sr-only">Diagnostic events</caption>
            <colgroup>
              <col className="w-7" />
              <col className="w-40" />
              <col className="w-28" />
              <col />
              <col className="w-24" />
              <col className="w-20" />
              <col className="hidden w-48 lg:table-column" />
            </colgroup>
            <thead className="sticky top-0 z-[1] bg-bg-subtle">
              <tr className="border-b border-border text-left text-2xs tracking-wide text-fg-subtle uppercase">
                <th scope="col" />
                <th scope="col" className="px-2 py-1 font-medium">Time</th>
                <th scope="col" className="px-2 py-1 font-medium">Category</th>
                <th scope="col" className="px-2 py-1 font-medium">Event</th>
                <th scope="col" className="px-2 py-1 font-medium">Status</th>
                <th scope="col" className="px-2 py-1 text-right font-medium">Duration</th>
                <th scope="col" className="hidden px-2 py-1 font-medium lg:table-cell">Request</th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev) => (
                <EventRow key={ev.id} ev={ev} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

const AUDIT_PAGE = 50;

function AuditLog() {
  const { id: ws } = useWorkspace();
  const [offset, setOffset] = React.useState(0);
  const [action, setAction] = React.useState("");
  const [applied, setApplied] = React.useState("");
  const q = useQuery({
    queryKey: qk.audit(ws, { offset, action: applied }),
    queryFn: () => diagnostics.audit(ws, { limit: AUDIT_PAGE, offset, action: applied || undefined }),
  });
  const total = q.data?.total ?? 0;
  return (
    <Panel title="Audit log" description="Who changed what, when, and from which request (owners and editors)">
      <form
        className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2"
        onSubmit={(e) => {
          e.preventDefault();
          setOffset(0);
          setApplied(action.trim());
        }}
      >
        <Input aria-label="Filter by action" className="w-56" placeholder="Action, e.g. metric.update" value={action} onChange={(e) => setAction(e.target.value)} />
        <Button type="submit" size="sm">
          Filter
        </Button>
        {q.data ? (
          <span className="ml-auto text-xs text-fg-subtle tabular">
            {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + AUDIT_PAGE, total)}`} of {formatInt(total)}
          </span>
        ) : null}
      </form>
      {q.isLoading ? (
        <LoadingState rows={5} className="px-3" />
      ) : q.isError ? (
        <ErrorState compact error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data || q.data.items.length === 0 ? (
        <EmptyState compact title="No audit entries" description={applied ? "No entries match this action." : "Changes to workspace objects are recorded here."} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <caption className="sr-only">Audit log</caption>
            <thead>
              <tr className="border-b border-border bg-bg-subtle text-left text-2xs tracking-wide text-fg-subtle uppercase">
                <th scope="col" className="px-2 py-1 font-medium">Time</th>
                <th scope="col" className="px-2 py-1 font-medium">Actor</th>
                <th scope="col" className="px-2 py-1 font-medium">Action</th>
                <th scope="col" className="px-2 py-1 font-medium">Resource</th>
                <th scope="col" className="px-2 py-1 font-medium">Detail</th>
                <th scope="col" className="hidden px-2 py-1 font-medium lg:table-cell">Request</th>
              </tr>
            </thead>
            <tbody>
              {q.data.items.map((a) => (
                <tr key={a.id} className="border-b border-border last:border-b-0 align-top">
                  <td className="px-2 py-1 whitespace-nowrap text-fg-muted tabular">{formatDateTime(a.created_at)}</td>
                  <td className="px-2 py-1">{a.actor ?? a.user_id ?? "system"}</td>
                  <td className="px-2 py-1 font-mono">{a.action}</td>
                  <td className="px-2 py-1">
                    {a.resource_type ? (
                      <span>
                        {humanize(a.resource_type)} <span className="font-mono text-2xs text-fg-subtle">{a.resource_id?.slice(0, 12)}</span>
                      </span>
                    ) : (
                      "n/a"
                    )}
                  </td>
                  <td className="max-w-[320px] px-2 py-1">
                    {a.detail && Object.keys(a.detail).length ? (
                      <code className="line-clamp-2 font-mono text-2xs break-all text-fg-muted" title={JSON.stringify(a.detail)}>
                        {JSON.stringify(a.detail)}
                      </code>
                    ) : (
                      <span className="text-fg-faint">n/a</span>
                    )}
                  </td>
                  <td className="hidden px-2 py-1 font-mono text-2xs text-fg-subtle lg:table-cell">{a.request_id ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {q.data && total > AUDIT_PAGE ? (
        <div className="flex justify-end gap-2 border-t border-border px-3 py-1.5">
          <Button size="xs" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE))}>
            Previous
          </Button>
          <Button size="xs" disabled={offset + AUDIT_PAGE >= total} onClick={() => setOffset(offset + AUDIT_PAGE)}>
            Next
          </Button>
        </div>
      ) : null}
    </Panel>
  );
}

/** Developer diagnostics (spec §57): system state, structured events and the audit trail. */
export function DiagnosticsView() {
  return (
    <Page>
      <PageHeader
        title="Diagnostics"
        description="Versions, storage, sandbox limits, AI status, and the structured event and audit logs. Secrets are redacted server-side."
      />
      <PageBody>
        <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
          <SystemPanel />
          <SummaryPanel />
          <EventLog />
          <AuditLog />
        </div>
      </PageBody>
    </Page>
  );
}
