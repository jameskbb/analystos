"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutDashboard, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { dashboards, type DashboardOut } from "@/lib/api/resources/outputs";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader } from "@/components/shell/page";
import { EmptyState, InlineError, QueryState, LoadingState } from "@/components/states/states";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { formatRelative, pluralize } from "@/lib/format";

function CreateDashboardDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { id: ws, href } = useWorkspace();
  const router = useRouter();
  const qc = useQueryClient();
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const create = useMutation({
    mutationFn: () => dashboards.create(ws, { name: name.trim(), description: description.trim() || undefined }),
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: qk.dashboards(ws) });
      onOpenChange(false);
      router.push(href(`/dashboards/${d.id}?edit=1`));
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <DialogHeader>
            <DialogTitle>New dashboard</DialogTitle>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            <Field label="Name" htmlFor="dash-name">
              <Input id="dash-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Executive Sales Overview" />
            </Field>
            <Field label="Description" htmlFor="dash-desc">
              <Textarea id="dash-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
            </Field>
            <InlineError error={create.error} />
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!name.trim() || create.isPending}>
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DashboardsList() {
  const { id: ws, href, canEdit } = useWorkspace();
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const [creating, setCreating] = React.useState(params.get("new") === "1");
  const [deleting, setDeleting] = React.useState<DashboardOut | null>(null);
  const query = useQuery({ queryKey: qk.dashboards(ws), queryFn: () => dashboards.list(ws) });
  const remove = useMutation({
    mutationFn: (id: string) => dashboards.remove(ws, id),
    onSuccess: () => {
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: qk.dashboards(ws) });
      toast.success("Dashboard deleted");
    },
    onError: (e: Error) => toast.error("Delete failed", { description: e.message }),
  });

  return (
    <Page>
      <PageHeader
        title="Dashboards"
        description="Tiles bound to semantic metrics, so every KPI means the same thing everywhere."
        actions={
          canEdit ? (
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus /> New dashboard
            </Button>
          ) : null
        }
      />
      <PageBody>
        <QueryState
          query={query}
          empty={
            <EmptyState
              icon={LayoutDashboard}
              title="No dashboards yet"
              description="Build a dashboard from semantic metrics, saved queries or investigation artifacts. Or load the Summit Supply demo to explore a finished one."
              action={
                canEdit ? (
                  <Button variant="primary" onClick={() => setCreating(true)}>
                    <Plus /> Create a dashboard
                  </Button>
                ) : null
              }
              secondary={<LoadDemoButton workspaceId={ws} variant="secondary" />}
            />
          }
        >
          {(list) => (
            <div className="overflow-hidden rounded-md border border-border">
              <table className="w-full text-sm">
                <thead className="bg-bg-subtle text-left text-xs text-fg-subtle">
                  <tr>
                    <th className="px-3 py-2 font-medium">Name</th>
                    <th className="hidden px-3 py-2 font-medium md:table-cell">Tiles</th>
                    <th className="hidden px-3 py-2 font-medium md:table-cell">Version</th>
                    <th className="px-3 py-2 font-medium">Updated</th>
                    <th className="w-10 px-3 py-2">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((d) => (
                    <tr key={d.id} className="border-t border-border hover:bg-bg-subtle">
                      <td className="px-3 py-2">
                        <Link href={href(`/dashboards/${d.id}`)} className="font-medium hover:text-accent hover:underline">
                          {d.name}
                        </Link>
                        {d.description ? <div className="line-clamp-1 text-xs text-fg-subtle">{d.description}</div> : null}
                      </td>
                      <td className="hidden px-3 py-2 text-fg-muted tabular md:table-cell">
                        {pluralize(d.tile_count ?? d.tiles?.length ?? d.layout?.length ?? 0, "tile")}
                      </td>
                      <td className="hidden px-3 py-2 font-mono text-xs text-fg-subtle md:table-cell">v{d.version_no}</td>
                      <td className="px-3 py-2 text-fg-subtle">{formatRelative(d.updated_at ?? d.created_at)}</td>
                      <td className="px-2 py-2 text-right">
                        {canEdit ? (
                          <Button variant="ghost" size="icon-xs" aria-label={`Delete ${d.name}`} onClick={() => setDeleting(d)}>
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
        </QueryState>
      </PageBody>
      <CreateDashboardDialog
        open={creating}
        onOpenChange={(o) => {
          setCreating(o);
          if (!o && params.get("new")) router.replace(href("/dashboards"));
        }}
      />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.name}”?`}
        description="The dashboard and its tiles are removed. Metrics, queries and findings it used are not affected."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      />
    </Page>
  );
}

export default function DashboardsPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <DashboardsList />
    </Suspense>
  );
}
