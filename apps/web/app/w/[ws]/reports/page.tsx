"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CalendarRange, FileText, Plus, Trash2 } from "lucide-react";
import { reports, type ReportOut } from "@/lib/api/resources/outputs";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader } from "@/components/shell/page";
import { EmptyState, LoadingState, QueryState } from "@/components/states/states";
import { StatusBadge } from "@/components/analysis/status";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/tabs";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { CreateReportDialog } from "@/components/outputs/create-report-dialog";
import { monthLabel } from "@/components/outputs/model";
import { formatRelative, humanize } from "@/lib/format";

type KindFilter = "all" | "business_review" | "investigation" | "custom";

function ReportsList() {
  const { id: ws, href, canEdit } = useWorkspace();
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const [creating, setCreating] = React.useState(params.get("new") === "1");
  const [kind, setKind] = React.useState<KindFilter>("all");
  const [deleting, setDeleting] = React.useState<ReportOut | null>(null);
  const query = useQuery({ queryKey: qk.reports(ws), queryFn: () => reports.list(ws) });
  const remove = useMutation({
    mutationFn: (id: string) => reports.remove(ws, id),
    onSuccess: () => {
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: qk.reports(ws) });
      toast.success("Report deleted");
    },
    onError: (e: Error) => toast.error("Delete failed", { description: e.message }),
  });
  return (
    <Page>
      <PageHeader
        title="Reports"
        description="Narrative, findings, KPIs and charts with methodology and sources. Reviewed before publishing."
        actions={
          canEdit ? (
            <>
              <Button asChild variant="secondary">
                <Link href={href("/reports/review")}>
                  <CalendarRange /> Business review
                </Link>
              </Button>
              <Button variant="primary" onClick={() => setCreating(true)}>
                <Plus /> New report
              </Button>
            </>
          ) : null
        }
      />
      <PageBody>
        <QueryState
          query={query}
          empty={
            <EmptyState
              icon={FileText}
              title="No reports yet"
              description="Turn an investigation into a memo, build an executive summary from confirmed findings, or run a monthly business review."
              action={
                canEdit ? (
                  <Button variant="primary" onClick={() => setCreating(true)}>
                    <Plus /> Create a report
                  </Button>
                ) : null
              }
              secondary={<LoadDemoButton workspaceId={ws} variant="secondary" />}
            />
          }
        >
          {(list) => {
            const rows = kind === "all" ? list : list.filter((r) => r.kind === kind);
            return (
              <div className="flex flex-col gap-3">
                <Segmented<KindFilter>
                  aria-label="Filter by type"
                  value={kind}
                  onChange={setKind}
                  options={[
                    { value: "all", label: `All (${list.length})` },
                    { value: "business_review", label: "Business reviews" },
                    { value: "investigation", label: "Investigation memos" },
                    { value: "custom", label: "Custom" },
                  ]}
                />
                {rows.length === 0 ? (
                  <EmptyState compact title="No reports of this type" />
                ) : (
                  <div className="overflow-hidden rounded-md border border-border">
                    <table className="w-full text-sm">
                      <thead className="bg-bg-subtle text-left text-xs text-fg-subtle">
                        <tr>
                          <th className="px-3 py-2 font-medium">Title</th>
                          <th className="hidden px-3 py-2 font-medium md:table-cell">Type</th>
                          <th className="px-3 py-2 font-medium">Status</th>
                          <th className="hidden px-3 py-2 font-medium sm:table-cell">Version</th>
                          <th className="px-3 py-2 font-medium">Updated</th>
                          <th className="w-10 px-3 py-2">
                            <span className="sr-only">Actions</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((r) => (
                          <tr key={r.id} className="border-t border-border hover:bg-bg-subtle">
                            <td className="px-3 py-2">
                              <Link href={href(`/reports/${r.id}`)} className="font-medium hover:text-accent hover:underline">
                                {r.title}
                              </Link>
                              {r.period ? <div className="text-xs text-fg-subtle">{monthLabel(r.period)}</div> : null}
                            </td>
                            <td className="hidden px-3 py-2 text-fg-muted md:table-cell">{humanize(r.kind)}</td>
                            <td className="px-3 py-2">
                              <StatusBadge status={r.status} />
                            </td>
                            <td className="hidden px-3 py-2 font-mono text-xs text-fg-subtle sm:table-cell">v{r.version_no}</td>
                            <td className="px-3 py-2 text-fg-subtle">{formatRelative(r.updated_at ?? r.created_at)}</td>
                            <td className="px-2 py-2 text-right">
                              {canEdit ? (
                                <Button variant="ghost" size="icon-xs" aria-label={`Delete ${r.title}`} onClick={() => setDeleting(r)}>
                                  <Trash2 />
                                </Button>
                              ) : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          }}
        </QueryState>
      </PageBody>
      <CreateReportDialog
        open={creating}
        onOpenChange={(o) => {
          setCreating(o);
          if (!o && params.get("new")) router.replace(href("/reports"));
        }}
      />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.title}”?`}
        description="The report is removed. Findings and investigations it references are not affected."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      />
    </Page>
  );
}

export default function ReportsPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <ReportsList />
    </Suspense>
  );
}
