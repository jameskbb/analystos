"use client";
import * as React from "react";
import { use } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronRight, GitBranch, Pencil } from "lucide-react";
import { findings } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { Finding } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, Panel, DefinitionList, SectionLabel } from "@/components/shell/page";
import { Statement } from "@/components/analysis/statement";
import { EvidenceBadge, EvidenceReasons } from "@/components/analysis/evidence";
import { FilterChips } from "@/components/analysis/filter-chips";
import { ChangeValue } from "@/components/analysis/change";
import { LineageGraphView } from "@/components/analysis/lineage-graph";
import { Kpi } from "@/components/charts/kpi";
import { CodeBlock } from "@/components/code/code-block";
import { ExportMenu } from "@/components/export/export-menu";
import { ArtifactChart } from "@/components/investigation/artifact-chart";
import { ValidationList } from "@/components/investigation/evidence-panel";
import { metricVersions } from "@/components/investigation/model";
import { FindingStatusControl } from "@/components/findings/finding-status-control";
import { FindingComments } from "@/components/findings/comments";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { ErrorState, LoadingState } from "@/components/states/states";
import { formatDateTime, formatShare, humanize } from "@/lib/format";

function EditableText({ label, value, onSave, placeholder, canEdit }: {
  label: string;
  value: string;
  onSave: (v: string) => Promise<unknown>;
  placeholder: string;
  canEdit: boolean;
}) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(value);
  const [saving, setSaving] = React.useState(false);
  React.useEffect(() => setDraft(value), [value]);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <SectionLabel>{label}</SectionLabel>
        {canEdit && !editing ? (
          <Button variant="ghost" size="icon-xs" aria-label={`Edit ${label.toLowerCase()}`} onClick={() => setEditing(true)}>
            <Pencil />
          </Button>
        ) : null}
      </div>
      {editing ? (
        <form
          className="flex flex-col gap-1.5"
          onSubmit={async (e) => {
            e.preventDefault();
            setSaving(true);
            try {
              await onSave(draft.trim());
              setEditing(false);
            } finally {
              setSaving(false);
            }
          }}
        >
          <Textarea autoFocus rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label={label} />
          <div className="flex justify-end gap-1.5">
            <Button size="xs" onClick={() => { setDraft(value); setEditing(false); }}>Cancel</Button>
            <Button size="xs" variant="primary" type="submit" disabled={saving}>Save</Button>
          </div>
        </form>
      ) : value ? (
        <p className="text-sm whitespace-pre-wrap">{value}</p>
      ) : (
        <p className="text-sm text-fg-faint">{placeholder}</p>
      )}
    </div>
  );
}

function FindingDetail({ finding }: { finding: Finding }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const arts = useQuery({ queryKey: qk.findingPart(ws, finding.id, "artifacts"), queryFn: () => findings.artifacts(ws, finding.id) });
  const lineage = useQuery({ queryKey: qk.findingPart(ws, finding.id, "lineage"), queryFn: () => findings.lineage(ws, finding.id) });
  const versions = useQuery({ queryKey: qk.findingPart(ws, finding.id, "versions"), queryFn: () => findings.versions(ws, finding.id) });
  const update = useMutation({
    mutationFn: (body: Parameters<typeof findings.update>[2]) => findings.update(ws, finding.id, body),
    onSuccess: (f) => {
      qc.setQueryData(qk.finding(ws, f.id), f);
      void qc.invalidateQueries({ queryKey: qk.findingPart(ws, f.id, "versions") });
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const v = finding.values ?? {};
  const fmt = v.format ?? null;
  const charts = (arts.data ?? []).filter((a) => a.chart_spec || a.result);
  const queries = (arts.data ?? []).filter((a) => a.sql);
  const mv = metricVersions(finding);

  return (
    <Page>
      <header className="shrink-0 border-b border-border px-4 py-3 sm:px-5">
        <nav aria-label="Breadcrumb" className="mb-1 flex items-center gap-1 text-xs text-fg-subtle">
          <Link href={href("/findings")} className="hover:text-fg">Findings</Link>
          <ChevronRight className="size-3" />
          <span className="font-mono">{finding.id.slice(0, 8)}</span>
          <span>· v{finding.version_no}</span>
        </nav>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-3xl min-w-0 flex-1">
            <Statement type={finding.statement_type} size="lg">
              {finding.statement}
            </Statement>
          </div>
          <div className="flex flex-col items-end gap-2 no-print">
            <ExportMenu target={{ kind: "finding", id: finding.id }} formats={["md", "html", "pdf"]} filename={`finding-${finding.id.slice(0, 8)}`} allowPrint />
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <EvidenceBadge strength={finding.evidence_strength} reasons={finding.evidence_reasons} />
          <FindingStatusControl finding={finding} canEdit={canEdit} />
        </div>
      </header>
      <PageBody>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="flex min-w-0 flex-col gap-4">
            {v.current !== undefined && v.current !== null ? (
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-md border border-border px-4 py-3 sm:grid-cols-4" data-print-avoid-break>
                <Kpi label={finding.metric_label ?? humanize(finding.metric_id) ?? "Current"} value={v.current} format={fmt} size="sm" />
                <Kpi label="Baseline" value={v.baseline} format={fmt} size="sm" />
                <div className="flex flex-col gap-0.5">
                  <div className="text-xs text-fg-subtle">Change</div>
                  <ChangeValue pct={v.pct_change} abs={v.abs_change} format={fmt} className="text-sm" />
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-xs text-fg-subtle">Share of parent change</div>
                  <div className="text-lg font-semibold tabular">{v.share !== null && v.share !== undefined ? formatShare(v.share) : "n/a"}</div>
                </div>
              </div>
            ) : null}
            <div>
              <SectionLabel className="mb-1">Filter context</SectionLabel>
              <FilterChips items={finding.filter_context} />
            </div>
            <Panel title="Charts">
              {arts.isLoading ? (
                <LoadingState className="p-3" />
              ) : arts.error ? (
                <ErrorState error={arts.error} compact onRetry={() => void arts.refetch()} />
              ) : charts.length ? (
                <div className="grid gap-4 p-3 xl:grid-cols-2">
                  {charts.map((a) => (
                    <ArtifactChart key={a.id} artifact={a} filters={finding.filter_context} />
                  ))}
                </div>
              ) : (
                <p className="p-3 text-sm text-fg-subtle">No chart is attached to this finding.</p>
              )}
            </Panel>
            <Panel title="Queries & supporting analysis" description="Every number above comes from these executed queries.">
              {queries.length ? (
                <div className="flex flex-col gap-3 p-3">
                  {queries.map((a) => (
                    <div key={a.id} className="flex flex-col gap-1.5">
                      <div className="flex items-center gap-2 text-xs">
                        <span className="font-medium">{a.title || humanize(a.kind)}</span>
                        <Link href={href(`/lineage/artifact/${a.id}`)} className="text-accent hover:underline">Lineage</Link>
                      </div>
                      <CodeBlock code={a.sql} maxHeight={260} />
                      <ValidationList artifacts={[a]} />
                    </div>
                  ))}
                </div>
              ) : (
                <p className="p-3 text-sm text-fg-subtle">{arts.isLoading ? "Loading…" : "No queries recorded."}</p>
              )}
            </Panel>
            <Panel title="Lineage" description="Finding → chart → query → metric version → dataset → source">
              {lineage.isLoading ? (
                <LoadingState variant="block" />
              ) : lineage.error ? (
                <ErrorState error={lineage.error} compact onRetry={() => void lineage.refetch()} />
              ) : lineage.data ? (
                <LineageGraphView graph={lineage.data} className="p-3" />
              ) : null}
            </Panel>
          </div>
          <aside className="flex flex-col gap-4">
            <Panel title="Evidence">
              <div className="p-3">
                <EvidenceReasons reasons={finding.evidence_reasons} />
              </div>
            </Panel>
            <Panel title="Business context">
              <div className="flex flex-col gap-3 p-3">
                <EditableText
                  label="Business impact"
                  value={finding.business_impact ?? ""}
                  placeholder="What does this mean for the business?"
                  canEdit={canEdit}
                  onSave={(business_impact) => update.mutateAsync({ business_impact, change_note: "Edited business impact" })}
                />
                <EditableText
                  label="Notes"
                  value={finding.notes ?? ""}
                  placeholder="Analyst notes, caveats, next steps."
                  canEdit={canEdit}
                  onSave={(notes) => update.mutateAsync({ notes, change_note: "Edited notes" })}
                />
              </div>
            </Panel>
            <Panel title="Provenance">
              <div className="p-3">
                <DefinitionList
                  items={[
                    {
                      label: "Investigation",
                      value: finding.investigation_id ? (
                        <Link href={href(`/investigate/${finding.investigation_id}`)} className="inline-flex items-center gap-1 text-accent hover:underline">
                          <GitBranch className="size-3" /> {finding.investigation_title ?? "Open"}
                        </Link>
                      ) : (
                        "Created manually"
                      ),
                    },
                    { label: "Created by", value: finding.created_by_name ?? finding.created_by ?? "n/a" },
                    { label: "Created", value: formatDateTime(finding.created_at) },
                    { label: "Updated", value: formatDateTime(finding.updated_at) },
                    ...(finding.segment ? [{ label: "Segment", value: `${humanize(finding.segment.dimension)} = ${finding.segment.value ?? "(null)"}` }] : []),
                    ...mv.map(([m, ver]) => ({ label: humanize(m), value: `metric ${ver}`, mono: true })),
                  ]}
                />
              </div>
            </Panel>
            <Panel title="Comments">
              <div className="p-3">
                <FindingComments findingId={finding.id} canEdit={canEdit} />
              </div>
            </Panel>
            <Panel title="History">
              <div className="p-3">
                {versions.isLoading ? (
                  <LoadingState rows={2} />
                ) : versions.data?.length ? (
                  <ol className="flex flex-col gap-1.5 text-xs">
                    {[...versions.data].sort((a, b) => b.version_no - a.version_no).map((ver) => (
                      <li key={ver.id} className="flex gap-2">
                        <span className="w-6 shrink-0 font-mono text-fg-subtle">v{ver.version_no}</span>
                        <span className="min-w-0 flex-1">
                          {ver.change_note || (ver.snapshot.status ? `Status: ${humanize(String(ver.snapshot.status))}` : "Updated")}
                          <span className="block text-2xs text-fg-subtle">{formatDateTime(ver.created_at)}</span>
                        </span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-xs text-fg-subtle">No earlier versions.</p>
                )}
              </div>
            </Panel>
          </aside>
        </div>
      </PageBody>
    </Page>
  );
}

export default function FindingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { id: ws } = useWorkspace();
  const q = useQuery({ queryKey: qk.finding(ws, id), queryFn: () => findings.get(ws, id) });
  if (q.isLoading) return <LoadingState variant="block" className="h-full" label="Loading finding" />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} className="h-full" />;
  return q.data ? <FindingDetail finding={q.data} /> : null;
}
