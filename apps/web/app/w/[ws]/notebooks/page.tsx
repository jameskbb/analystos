"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { NotebookPen, Plus, Trash2 } from "lucide-react";
import { notebooks } from "@/lib/api/resources/workbench";
import { qk } from "@/lib/api/keys";
import type { Notebook } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { EmptyState, InlineError, QueryState } from "@/components/states/states";
import { formatRelative } from "@/lib/format";

function NotebooksList() {
  const { id: ws, href, canEdit } = useWorkspace();
  const router = useRouter();
  const search = useSearchParams();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: qk.notebooks(ws), queryFn: () => notebooks.list(ws) });
  const [creating, setCreating] = React.useState(false);
  const [title, setTitle] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [deleting, setDeleting] = React.useState<Notebook | null>(null);
  const [q, setQ] = React.useState("");

  React.useEffect(() => {
    if (search.get("new") && canEdit) {
      setCreating(true);
      router.replace(href("/notebooks"));
    }
  }, [search, canEdit, router, href]);

  const create = useMutation({
    mutationFn: () => notebooks.create(ws, { title: title.trim(), description: description.trim() }),
    onSuccess: (nb) => {
      void qc.invalidateQueries({ queryKey: qk.notebooks(ws) });
      router.push(href(`/notebooks/${nb.id}`));
    },
  });
  const del = useMutation({
    mutationFn: (id: string) => notebooks.remove(ws, id),
    onSuccess: () => {
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: qk.notebooks(ws) });
      toast.success("Notebook deleted");
    },
    onError: (e: Error) => toast.error("Delete failed", { description: e.message }),
  });

  return (
    <Page>
      <PageHeader
        title="Notebooks"
        description="SQL, Python, Markdown, chart and finding cells that re-run top to bottom, reproducibly."
        actions={
          canEdit ? (
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus /> New notebook
            </Button>
          ) : null
        }
      />
      <PageBody>
        <QueryState
          query={query}
          empty={
            <EmptyState
              icon={NotebookPen}
              title="No notebooks yet"
              description="Notebooks combine queries, sandboxed Python, charts and findings into a reproducible analysis. Investigations can also be exported to a notebook."
              action={
                canEdit ? (
                  <Button variant="primary" onClick={() => setCreating(true)}>
                    <Plus /> New notebook
                  </Button>
                ) : undefined
              }
            />
          }
        >
          {(list) => {
            const items = list.filter((n) => !q.trim() || `${n.title} ${n.description ?? ""}`.toLowerCase().includes(q.trim().toLowerCase()));
            return (
              <div className="flex flex-col gap-2">
                <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter notebooks" aria-label="Filter notebooks" className="max-w-xs" />
                <div className="overflow-hidden rounded-md border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-bg-subtle text-xs text-fg-subtle">
                      <tr>
                        <th className="px-3 py-1.5 text-left font-medium">Title</th>
                        <th className="hidden px-3 py-1.5 text-left font-medium md:table-cell">Cells</th>
                        <th className="hidden px-3 py-1.5 text-left font-medium sm:table-cell">Source</th>
                        <th className="px-3 py-1.5 text-left font-medium">Updated</th>
                        <th className="w-10" />
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((n) => (
                        <tr key={n.id} className="border-t border-border hover:bg-bg-subtle">
                          <td className="px-3 py-1.5">
                            <Link href={href(`/notebooks/${n.id}`)} className="font-medium hover:text-accent">
                              {n.title}
                            </Link>
                            {n.description ? <div className="truncate text-xs text-fg-subtle">{n.description}</div> : null}
                          </td>
                          <td className="hidden px-3 py-1.5 tabular text-fg-muted md:table-cell">{n.cell_count ?? n.cells?.length ?? "n/a"}</td>
                          <td className="hidden px-3 py-1.5 text-xs text-fg-subtle sm:table-cell">
                            {n.investigation_id ? (
                              <Link className="text-accent hover:underline" href={href(`/investigate/${n.investigation_id}`)}>
                                From investigation
                              </Link>
                            ) : (
                              "Manual"
                            )}
                          </td>
                          <td className="px-3 py-1.5 text-xs text-fg-subtle">{formatRelative(n.updated_at ?? n.created_at)}</td>
                          <td className="px-1">
                            {canEdit ? (
                              <Button size="icon-xs" variant="ghost" aria-label={`Delete ${n.title}`} onClick={() => setDeleting(n)}>
                                <Trash2 />
                              </Button>
                            ) : null}
                          </td>
                        </tr>
                      ))}
                      {!items.length ? (
                        <tr>
                          <td colSpan={5} className="px-3 py-6 text-center text-sm text-fg-subtle">
                            No notebooks match.
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          }}
        </QueryState>
      </PageBody>
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent size="sm">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (title.trim()) create.mutate();
            }}
          >
            <DialogHeader>
              <DialogTitle>New notebook</DialogTitle>
            </DialogHeader>
            <DialogBody className="flex flex-col gap-3">
              <Field label="Title" htmlFor="nb-title">
                <Input id="nb-title" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="August margin deep dive" />
              </Field>
              <Field label="Description" htmlFor="nb-desc">
                <Textarea id="nb-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
              </Field>
              <InlineError error={create.error} />
            </DialogBody>
            <DialogFooter>
              <Button variant="secondary" onClick={() => setCreating(false)}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" disabled={!title.trim() || create.isPending}>
                Create
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.title}”?`}
        description="All cells and their outputs are removed. This cannot be undone."
        confirmLabel="Delete notebook"
        destructive
        pending={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.id)}
      />
    </Page>
  );
}

export default function NotebooksPage() {
  return (
    <Suspense fallback={null}>
      <NotebooksList />
    </Suspense>
  );
}
