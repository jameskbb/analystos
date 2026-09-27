"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { GitBranch, Plus, Trash2 } from "lucide-react";
import { datasets, investigations } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { Investigation } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader } from "@/components/shell/page";
import { AskComposer } from "@/components/investigation/ask-composer";
import { headline } from "@/components/investigation/model";
import { StatusBadge } from "@/components/analysis/status";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { formatInt, formatRelative } from "@/lib/format";

const STATUS_FILTERS = [
  ["", "All statuses"],
  ["completed", "Completed"],
  ["awaiting_approval", "Awaiting approval"],
  ["needs_disambiguation", "Needs a choice"],
  ["running", "Running"],
  ["failed", "Failed"],
] as const;

function InvestigationRow({ inv, onDelete, canEdit }: { inv: Investigation; onDelete: () => void; canEdit: boolean }) {
  const { href } = useWorkspace();
  const answer = headline(inv);
  return (
    <tr className="group border-b border-border hover:bg-bg-subtle">
      <td className="max-w-0 py-2 pr-3 pl-4">
        <Link href={href(`/investigate/${inv.id}`)} className="block truncate font-medium hover:text-accent">
          {inv.question}
        </Link>
        {answer ? <p className="truncate text-xs text-fg-subtle">{answer}</p> : null}
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        <StatusBadge status={inv.status} />
      </td>
      <td className="hidden px-3 py-2 text-right text-xs text-fg-muted tabular md:table-cell">{formatInt(inv.run_count)}</td>
      <td className="hidden px-3 py-2 text-right text-xs text-fg-muted tabular md:table-cell">{inv.finding_count !== undefined ? formatInt(inv.finding_count) : "n/a"}</td>
      <td className="hidden px-3 py-2 text-xs whitespace-nowrap text-fg-subtle sm:table-cell">{formatRelative(inv.updated_at ?? inv.created_at)}</td>
      <td className="w-8 py-2 pr-3">
        {canEdit ? (
          <Button variant="ghost" size="icon-xs" aria-label={`Delete investigation ${inv.question}`} onClick={onDelete} className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100">
            <Trash2 />
          </Button>
        ) : null}
      </td>
    </tr>
  );
}

function InvestigationsPage() {
  const { id: ws, canEdit } = useWorkspace();
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const [status, setStatus] = React.useState("");
  const [q, setQ] = React.useState("");
  const [composer, setComposer] = React.useState(params.get("new") === "1");
  const [deleting, setDeleting] = React.useState<Investigation | null>(null);

  React.useEffect(() => {
    if (params.get("new") === "1") setComposer(true);
  }, [params]);

  const list = useQuery({ queryKey: qk.investigations(ws, { status }), queryFn: () => investigations.list(ws, { status: status || undefined, limit: 200 }) });
  const ds = useQuery({ queryKey: qk.datasets(ws), queryFn: () => datasets.list(ws) });
  const remove = useMutation({
    mutationFn: (id: string) => investigations.remove(ws, id),
    onSuccess: () => {
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: ["ws", ws, "investigations"] });
      toast.success("Investigation deleted");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const items = (list.data ?? []).filter((i) => !q.trim() || i.question.toLowerCase().includes(q.trim().toLowerCase()));
  const noData = ds.data && ds.data.length === 0;

  return (
    <Page>
      <PageHeader
        title="Investigate"
        description="Ask why a metric moved. AnalystOS builds a plan, runs real queries and assembles an evidence tree."
        actions={
          <Button
            variant="primary"
            onClick={() => {
              setComposer(true);
              router.replace("?new=1");
            }}
          >
            <Plus /> New investigation
          </Button>
        }
      />
      <PageBody className="flex flex-col gap-4">
        {noData ? (
          <EmptyState
            icon={GitBranch}
            title="No data to investigate yet"
            description="Load the Summit Supply demo (with planted stories such as the August revenue decline) or upload your own files."
            action={<LoadDemoButton workspaceId={ws} />}
          />
        ) : composer || (list.data && list.data.length === 0) ? (
          <AskComposer autoFocus className="max-w-3xl" />
        ) : null}

        {list.isLoading ? (
          <LoadingState />
        ) : list.error ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.data && list.data.length > 0 ? (
          <section className="rounded-md border border-border">
            <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
              <Input className="h-7 w-64" placeholder="Filter questions…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter investigations" />
              <NativeSelect className="h-7 w-44" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
                {STATUS_FILTERS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
              <span className="ml-auto text-xs text-fg-subtle tabular">{formatInt(items.length)} shown</span>
            </div>
            {items.length ? (
              <div className="overflow-x-auto">
                <table className="w-full table-fixed text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-2xs tracking-wider text-fg-subtle uppercase">
                      <th className="py-1.5 pr-3 pl-4 font-semibold">Question</th>
                      <th className="w-40 px-3 py-1.5 font-semibold">Status</th>
                      <th className="hidden w-16 px-3 py-1.5 text-right font-semibold md:table-cell">Runs</th>
                      <th className="hidden w-20 px-3 py-1.5 text-right font-semibold md:table-cell">Findings</th>
                      <th className="hidden w-28 px-3 py-1.5 font-semibold sm:table-cell">Updated</th>
                      <th className="w-8" />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((inv) => (
                      <InvestigationRow key={inv.id} inv={inv} onDelete={() => setDeleting(inv)} canEdit={canEdit} />
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState compact title="No investigations match" description="Clear the filters to see all investigations." />
            )}
          </section>
        ) : null}
      </PageBody>
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Delete this investigation?"
        description="Its tree, artifacts and run history are deleted. Saved findings are kept but lose their live link to the tree."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      />
    </Page>
  );
}

export default function InvestigatePage() {
  return (
    <Suspense fallback={<LoadingState variant="block" className="h-full" />}>
      <InvestigationsPage />
    </Suspense>
  );
}
